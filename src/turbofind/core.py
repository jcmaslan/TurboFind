import os
import re
import json
import hashlib
import urllib.request
import urllib.error
from contextlib import contextmanager
from usearch.index import Index

try:
    import fcntl
    _USE_FCNTL = True
except ImportError:
    _USE_FCNTL = False
    import msvcrt

TURBOFIND_DIR = ".turbofind"
INDEXES_DIR = "indexes"
INDEX_FILENAME = "index.usearch"
METADATA_FILENAME = "meta.json"
GRAPH_FILENAME = "graph.json"
LOCK_FILENAME = "lock"
DEFAULT_INDEX = "code-intent"
ROOT_MARKERS = ["repo_map.txt", ".turbofind", ".turbofind.toml", ".git"]

def find_project_root(start_path=None):
    """Walk up from start_path looking for project root markers.
    Checks for .git/, .turbofind/, .turbofind.toml, and repo_map.txt in priority order.
    Falls back to cwd if no marker is found."""
    current = os.path.abspath(start_path or os.getcwd())
    while True:
        for marker in ROOT_MARKERS:
            candidate = os.path.join(current, marker)
            if os.path.exists(candidate):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(os.getcwd())
        current = parent

def get_ollama_host():
    return os.environ.get("OLLAMA_HOST", "localhost:11434")

def check_ollama():
    """Returns True if Ollama is reachable, raises exception otherwise."""
    host = get_ollama_host()
    url = f"http://{host}/api/version"
    try:
        response = urllib.request.urlopen(url, timeout=2)
        if response.status == 200:
            return True
    except (urllib.error.URLError, ConnectionError):
        raise RuntimeError(f"Ollama is not reachable at {host} -- run `ollama serve` first.")
    raise RuntimeError(f"Ollama is not reachable at {host} -- run `ollama serve` first.")

def _index_dir(project_root, index_name):
    """Return the directory path for a named index, creating it if needed."""
    if not re.match(r'^[A-Za-z0-9_.\-]+$', index_name) or index_name in (".", ".."):
        raise ValueError(f"Invalid index name '{index_name}': must contain only letters, digits, underscores, dots, or hyphens (and not '.' or '..')")
    d = os.path.join(project_root, TURBOFIND_DIR, INDEXES_DIR, index_name)
    os.makedirs(d, exist_ok=True)
    return d

@contextmanager
def index_lock(project_root):
    """Acquire an exclusive file lock for the duration of an index read-modify-write cycle.
    Uses fcntl on macOS/Linux and msvcrt on Windows."""
    lock_dir = os.path.join(project_root, TURBOFIND_DIR)
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, LOCK_FILENAME)
    lock_fd = open(lock_path, "w")
    try:
        if _USE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        else:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if _USE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        else:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        lock_fd.close()

def load_index(project_root=None, index_name=DEFAULT_INDEX, ndim=768):
    """Loads usearch index and metadata for a named index. Returns (index, metadata_dict).
    Caller MUST hold index_lock() during the full read-modify-write cycle."""
    root = project_root or find_project_root()
    d = _index_dir(root, index_name)
    index_path = os.path.join(d, INDEX_FILENAME)
    meta_path = os.path.join(d, METADATA_FILENAME)

    index = Index(ndim=ndim, metric="cos", dtype="i8")
    if os.path.exists(index_path):
        index.load(index_path)

    metadata = {}
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            metadata = json.load(f)
            metadata = {int(k): v for k, v in metadata.items()}

    return index, metadata

def save_index(index, metadata, project_root=None, index_name=DEFAULT_INDEX):
    """Saves usearch index and metadata atomically for a named index.
    Caller MUST hold index_lock()."""
    root = project_root or find_project_root()
    d = _index_dir(root, index_name)
    index.save(os.path.join(d, INDEX_FILENAME))

    meta_path = os.path.join(d, METADATA_FILENAME)
    tmp_path = meta_path + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    os.replace(tmp_path, meta_path)

def load_graph(project_root=None):
    """Loads the global topology graph as {"nodes": [...], "edges": [...]}."""
    root = project_root or find_project_root()
    d = os.path.join(root, TURBOFIND_DIR)
    graph_path = os.path.join(d, GRAPH_FILENAME)
    if os.path.exists(graph_path):
        with open(graph_path, 'r') as f:
            return json.load(f)
    return {"nodes": [], "edges": []}

def save_graph(graph_dict, project_root=None):
    """Saves the global topology graph atomically."""
    root = project_root or find_project_root()
    d = os.path.join(root, TURBOFIND_DIR)
    os.makedirs(d, exist_ok=True)
    graph_path = os.path.join(d, GRAPH_FILENAME)
    tmp_path = graph_path + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(graph_dict, f, indent=2)
    os.replace(tmp_path, graph_path)

def graph_to_xml(graph_dict):
    """Serialize a topology graph dict to compact XML for prompt injection."""
    from xml.sax.saxutils import quoteattr
    lines = ["<repository_topology>"]
    for node in graph_dict.get("nodes", []):
        lines.append(
            f"  <node id={quoteattr(node['id'])} file={quoteattr(node['file'])} "
            f"type={quoteattr(node['type'])} line={quoteattr(str(node['line']))} />"
        )
    for edge in graph_dict.get("edges", []):
        edge_type = edge.get("type", "calls")
        lines.append(
            f"  <edge from={quoteattr(edge['from'])} to={quoteattr(edge['to'])} "
            f"type={quoteattr(edge_type)} />"
        )
    lines.append("</repository_topology>")
    return "\n".join(lines)


_ADJ_CACHE = {}  # (root, weights_key) -> {"mtime": ..., "adj": ...}
_EDGE_WEIGHTS = {"imports": 1.0, "extends": 0.8, "calls": 0.5}


def load_file_adjacency(project_root=None, edge_weights=None):
    """Return {file: {neighbor_file: max_edge_weight}}, cached by graph.json mtime
    and effective edge_weights. Collapses node-level edges to file-file pairs,
    keeping the max weight across edge types; symmetrizes; skips self-loops.
    Returns {} if graph is absent.
    """
    root = project_root or find_project_root()
    graph_path = os.path.join(root, TURBOFIND_DIR, GRAPH_FILENAME)
    if not os.path.exists(graph_path):
        return {}

    weights = edge_weights or _EDGE_WEIGHTS
    weights_key = tuple(sorted(weights.items()))
    cache_key = (root, weights_key)
    mtime = os.path.getmtime(graph_path)
    entry = _ADJ_CACHE.get(cache_key)
    if entry and entry["mtime"] == mtime:
        return entry["adj"]

    with open(graph_path, 'r') as f:
        graph = json.load(f)

    node_to_file = {n["id"]: n["file"] for n in graph.get("nodes", [])}
    adj = {}
    for edge in graph.get("edges", []):
        fa = node_to_file.get(edge["from"])
        fb = node_to_file.get(edge["to"])
        if not fa or not fb or fa == fb:
            continue
        w = weights.get(edge.get("type", "calls"), 0.0)
        if w <= 0:
            continue
        if adj.setdefault(fa, {}).get(fb, 0.0) < w:
            adj[fa][fb] = w
        if adj.setdefault(fb, {}).get(fa, 0.0) < w:
            adj[fb][fa] = w

    _ADJ_CACHE[cache_key] = {"mtime": mtime, "adj": adj}
    return adj


def index_graph(graph_dict):
    """Pre-index a graph dict for O(local_degree) per-file subgraph slicing.
    Returns (nodes_by_id, file_to_local_ids, node_to_incident_edges)."""
    nodes_by_id = {n["id"]: n for n in graph_dict.get("nodes", [])}
    file_to_local_ids = {}
    for n in graph_dict.get("nodes", []):
        file_to_local_ids.setdefault(n.get("file"), set()).add(n["id"])
    node_to_edges = {}
    for edge in graph_dict.get("edges", []):
        node_to_edges.setdefault(edge["from"], []).append(edge)
        if edge["to"] != edge["from"]:
            node_to_edges.setdefault(edge["to"], []).append(edge)
    return nodes_by_id, file_to_local_ids, node_to_edges


def build_file_subgraph(graph_dict, file_path, index=None):
    """Return a {'nodes': [...], 'edges': [...]} slice centered on file_path:
    all nodes belonging to the file, every edge touching one of those nodes,
    and the opposite-endpoint nodes. Accepts an optional pre-built index from
    index_graph() to avoid O(n) scans per call.
    """
    if index is None:
        index = index_graph(graph_dict)
    nodes_by_id, file_to_local_ids, node_to_edges = index

    local_ids = file_to_local_ids.get(file_path, set())
    if not local_ids:
        return {"nodes": [], "edges": []}

    kept_edges = []
    seen = set()
    included_ids = set(local_ids)
    for nid in local_ids:
        for edge in node_to_edges.get(nid, []):
            key = (edge["from"], edge["to"], edge.get("type"))
            if key in seen:
                continue
            seen.add(key)
            kept_edges.append(edge)
            included_ids.add(edge["from"])
            included_ids.add(edge["to"])

    nodes = [nodes_by_id[nid] for nid in included_ids if nid in nodes_by_id]
    return {"nodes": nodes, "edges": kept_edges}


def load_graph_as_nx(project_root=None):
    """Load graph.json and reconstruct a NetworkX MultiDiGraph for querying."""
    import networkx as nx
    graph_dict = load_graph(project_root)
    G = nx.MultiDiGraph()
    for node in graph_dict.get("nodes", []):
        G.add_node(node["id"], file=node["file"], type=node["type"], line=node["line"])
    for edge in graph_dict.get("edges", []):
        G.add_edge(edge["from"], edge["to"], type=edge.get("type", "calls"))
    return G

def embed_text(text, prefix=""):
    """Calls Ollama locally to embed text with nomic-embed-text."""
    host = get_ollama_host()
    url = f"http://{host}/api/embeddings"
    payload = {
        "model": "nomic-embed-text",
        "prompt": f"{prefix}{text}"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'))
    req.add_header('Content-Type', 'application/json')
    try:
        response = urllib.request.urlopen(req)
        data = json.loads(response.read())
        return data["embedding"]
    except Exception as e:
        raise RuntimeError(f"Failed to generate embedding via Ollama: {e}")

def file_sha1(filepath):
    """Compute SHA1 hex digest of a file's contents."""
    h = hashlib.sha1()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def text_sha1(text):
    """Compute SHA1 hex digest of a string."""
    return hashlib.sha1(text.encode('utf-8')).hexdigest()

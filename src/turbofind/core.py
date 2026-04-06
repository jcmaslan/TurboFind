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
    if not re.match(r'^[A-Za-z0-9_.\-]+$', index_name):
        raise ValueError(f"Invalid index name '{index_name}': must contain only letters, digits, underscores, dots, or hyphens")
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
    """Loads the global AST graph."""
    root = project_root or find_project_root()
    d = os.path.join(root, TURBOFIND_DIR)
    graph_path = os.path.join(d, GRAPH_FILENAME)
    if os.path.exists(graph_path):
        with open(graph_path, 'r') as f:
            return json.load(f)
    return {}

def save_graph(graph_dict, project_root=None):
    """Saves the global AST graph atomically."""
    root = project_root or find_project_root()
    d = os.path.join(root, TURBOFIND_DIR)
    os.makedirs(d, exist_ok=True)
    graph_path = os.path.join(d, GRAPH_FILENAME)
    tmp_path = graph_path + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(graph_dict, f, indent=2)
    os.replace(tmp_path, graph_path)

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

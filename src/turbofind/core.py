import os
import json
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

INDEX_FILENAME = ".turbofind.usearch"
METADATA_FILENAME = ".turbofind.meta.json"
LOCK_FILENAME = ".turbofind.lock"
ROOT_MARKERS = ["repo_map.txt", ".turbofind.toml", ".git"]

def find_project_root(start_path=None):
    """Walk up from start_path looking for project root markers.
    Checks for .git/, .turbofind.toml, and repo_map.txt in priority order.
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
        raise RuntimeError(f"Ollama is not reachable at {host} — run `ollama serve` first.")
    raise RuntimeError(f"Ollama is not reachable at {host} — run `ollama serve` first.")

@contextmanager
def index_lock(project_root):
    """Acquire an exclusive file lock for the duration of an index read-modify-write cycle.
    Uses fcntl on macOS/Linux and msvcrt on Windows."""
    lock_path = os.path.join(project_root, LOCK_FILENAME)
    lock_fd = open(lock_path, "w")
    try:
        if _USE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        else:
            # Windows: lock the first byte of the file
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if _USE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        else:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        lock_fd.close()

def load_index(project_root=None, ndim=768):
    """Loads usearch index and metadata. Returns (index, metadata_dict).
    Caller MUST hold index_lock() during the full read-modify-write cycle."""
    root = project_root or find_project_root()
    index_path = os.path.join(root, INDEX_FILENAME)
    meta_path = os.path.join(root, METADATA_FILENAME)

    index = Index(ndim=ndim, metric="cos", dtype="i8")
    if os.path.exists(index_path):
        index.load(index_path)
        
    metadata = {}
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            metadata = json.load(f)
            metadata = {int(k): v for k, v in metadata.items()}
            
    return index, metadata

def save_index(index, metadata, project_root=None):
    """Saves usearch index and metadata atomically.
    Caller MUST hold index_lock()."""
    root = project_root or find_project_root()
    index.save(os.path.join(root, INDEX_FILENAME))

    # Write metadata to a temp file then rename for atomic replacement
    meta_path = os.path.join(root, METADATA_FILENAME)
    tmp_path = meta_path + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    os.replace(tmp_path, meta_path)

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


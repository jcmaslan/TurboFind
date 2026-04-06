"""Configuration loading for TurboFind guardrails.

Priority: CLI flags > .turbofind.toml > hardcoded defaults.
"""
import os
import json
import tomli
import pathspec

# ── Hardcoded defaults ──
DEFAULT_MAX_FILE_SIZE = 51200   # 50 KB
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_FILES = 100
DEFAULT_COST_LIMIT = 5.00
DEFAULT_MAX_DEPTH = 4
DEFAULT_GRAPH_MAX_TOKENS = 128000

# Files that are git-tracked but should not be indexed
DEFAULT_EXTRA_EXCLUDES = ["*.lock", "*.min.js", "*.min.css", "*.map"]

# Source file extensions to index when given a directory
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".js", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".swift", ".kt", ".scala",
    ".sh", ".bash",
}

# Approximate token costs (per million tokens) for cost estimation
# Based on Claude Haiku 4.5 pricing
TOKEN_COSTS = {
    "input_per_m": 1.00,     # $/M input tokens
    "output_per_m": 5.00,    # $/M output tokens
}
TOKENS_PER_LINE = 8  # rough average for source code


def load_config(project_root):
    """Load .turbofind.toml if present, merged with defaults."""
    config = {
        "per_file": {
            "max_size_bytes": DEFAULT_MAX_FILE_SIZE,
            "max_lines": DEFAULT_MAX_LINES,
            "max_depth": DEFAULT_MAX_DEPTH,
        },
        "per_batch": {
            "max_files": DEFAULT_MAX_FILES,
            "cost_limit": DEFAULT_COST_LIMIT,
        },
        "exclude": {
            "patterns": list(DEFAULT_EXTRA_EXCLUDES),
        },
        "graph": {
            "max_tokens": DEFAULT_GRAPH_MAX_TOKENS,
        }
    }

    toml_path = os.path.join(project_root, ".turbofind.toml")
    if os.path.exists(toml_path):
        with open(toml_path, "rb") as f:
            user_config = tomli.load(f)
        # Merge user overrides
        for section in ["per_file", "per_batch", "exclude", "graph"]:
            if section in user_config:
                config[section].update(user_config[section])

    return config


def load_exclusion_spec(project_root, extra_patterns=None):
    """Build a pathspec matcher from .gitignore + TurboFind excludes."""
    patterns = []

    gitignore_path = os.path.join(project_root, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as f:
            patterns.extend(f.read().splitlines())

    patterns.extend(extra_patterns or DEFAULT_EXTRA_EXCLUDES)

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def check_file_limits(filepath, config):
    """Check if a file exceeds per-file limits. Returns (ok, reason)."""
    max_size = config["per_file"]["max_size_bytes"]
    max_lines = config["per_file"]["max_lines"]

    file_size = os.path.getsize(filepath)
    if file_size > max_size:
        return False, f"exceeds max file size ({file_size:,} bytes > {max_size:,} bytes)"

    with open(filepath, "r", errors="replace") as f:
        line_count = sum(1 for _ in f)
    if line_count > max_lines:
        return False, f"exceeds max line count ({line_count:,} lines > {max_lines:,} lines)"

    return True, None


# Approximate per-file timing (milliseconds)
CLAUDE_LATENCY_MS = 1500       # avg Claude API round-trip for synthesis
OLLAMA_EMBED_LATENCY_MS = 150  # avg Ollama embedding call per chunk
CHUNK_SIZE = 100               # lines per chunk (must match upsert.py)


def compute_max_depth(graph, budget, default_depth):
    """Return the AST max_depth to use for the next file given the current graph size.

    Estimates token count from the serialized graph (rough heuristic: 1 token per 4 chars).
    Returns default_depth while under budget, 0 once the budget is reached.

    NOTE: In a future TTT-capable model, the depth reduction strategy would be
    dynamically determined by the model based on its context capacity and the
    structural importance of each file.
    """
    estimated_tokens = len(json.dumps(graph)) // 4
    if estimated_tokens >= budget:
        return 0
    return default_depth


def estimate_file(filepath):
    """Estimate Claude API cost and elapsed time for synthesizing one file.
    Returns (cost_usd, time_ms)."""
    with open(filepath, "r", errors="replace") as f:
        line_count = sum(1 for _ in f)

    input_tokens = (line_count * TOKENS_PER_LINE) + 2800
    output_tokens = 600

    input_cost = (input_tokens / 1_000_000) * TOKEN_COSTS["input_per_m"]
    output_cost = (output_tokens / 1_000_000) * TOKEN_COSTS["output_per_m"]
    cost = input_cost + output_cost

    num_chunks = max(1, (line_count + CHUNK_SIZE - 1) // CHUNK_SIZE)
    time_ms = CLAUDE_LATENCY_MS + (num_chunks * OLLAMA_EMBED_LATENCY_MS)

    return cost, time_ms

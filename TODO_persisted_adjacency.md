# TODO: Persist File Adjacency to Disk for Fast tf-search Startup

**Status:** deferred — not a priority right now.

## Why this exists

`tf-search` runs graph-expanded retrieval against a file→neighbors adjacency projected from `graph.json` (`load_file_adjacency` in `src/turbofind/core.py`). The projection is memoized in a module-level `_ADJ_CACHE`, but every CLI invocation is a fresh Python process — the cache starts empty. Every `tf-search` call re-parses the full `graph.json` (~12 MB on a real repo with ~40k nodes / ~11k edges) and rebuilds the same adjacency.

Cost: ~200–500 ms of fixed overhead per query. Acceptable today; worth fixing if startup latency becomes visible.

## Approach

Write the collapsed adjacency to `.turbofind/adjacency.json` at `tf-upsert` time. `tf-search` reads that small file instead of parsing the full graph.

### Step 1 — Persist adjacency in `core.py`

```python
ADJACENCY_FILENAME = "adjacency.json"

def save_file_adjacency(adj, project_root=None):
    """Write file-level adjacency as {file: {neighbor: weight}} atomically."""
```

Update `load_file_adjacency` to:
1. Read `.turbofind/adjacency.json` first.
2. Validate with stored `{mtime, weights_key}` against `graph.json`'s current mtime + effective weights.
3. If stale or missing, project from `graph.json`, save, and return.

File format: `{"mtime": ..., "weights_key": [...], "adj": {...}}`.

Factor the edge-walking body of the current projection into `_project_file_adjacency(graph_dict, edge_weights)` so writer and lazy fallback share logic. Keep the in-process `_ADJ_CACHE` as a second-layer optimization for daemon/batch callers.

### Step 2 — Write adjacency from `tf-upsert`

Three call sites in `src/turbofind/upsert.py` currently call `save_graph`:
- `--graph-only` mode (after building topology)
- `--remove` mode
- `--prune` mode

After each `save_graph(graph, ...)`, also build and save the adjacency:
```python
adj = _project_file_adjacency(graph, edge_weights=_EDGE_WEIGHTS)
save_file_adjacency(adj, project_root=project_root)
```

The live-run parent doesn't need changes — it already delegates graph writing to the `--graph-only` subprocess.

### Step 3 — tf-search wiring

No changes needed. `load_file_adjacency` is the single entry point; the internal implementation changes but the contract stays the same.

### Step 4 — Weight-override edge case

If a user overrides `search.graph.edge_weights` in `.turbofind.toml`, the stored adjacency (written with default weights) won't match. Fall through to in-process projection when `weights_key` differs from stored — power-user path pays the old cost. Comment as a known fast-path limitation. Don't bother with multi-profile caches until someone actually uses them.

## Files touched

| File | Change |
|---|---|
| `src/turbofind/core.py` | Factor `_project_file_adjacency`; add `save_file_adjacency` + `ADJACENCY_FILENAME`; make `load_file_adjacency` disk-first. |
| `src/turbofind/upsert.py` | Save adjacency alongside `save_graph` in `--graph-only`, `--remove`, `--prune`. |

Untouched: `search.py`, `verify.py`, `graph.json` schema, prompts, config.

## Verification

1. `tf-upsert . --graph-only` in `demo_repo/` → `.turbofind/adjacency.json` exists, much smaller than `graph.json`.
2. `time tf-search "authentication and authorization" --json > /dev/null` — startup improvement (mostly visible on large repos).
3. `touch .turbofind/graph.json` → next `tf-search` re-projects and overwrites `adjacency.json`.
4. `tf-upsert --remove <file>` → that file's edges gone from `adjacency.json`.
5. Custom `edge_weights` in `.turbofind.toml` → tf-search still works (fall through to in-process projection), no stale disk cache contamination.
6. Delete `graph.json` → "graph not found" warning, seed-only results (unchanged).

## Out of scope

- Per-weight-profile disk caches.
- Daemonization / long-lived process refactoring.
- Node-level on-disk index for `tf-verify` — different access pattern, full load is fine there.

---

# Related deferred perf items

## TODO: Persist a file → representative-meta index during `tf-upsert`

**Status:** deferred.

`_graph_expand()` in `src/turbofind/search.py` walks every `metadata.values()` entry on every search to build a `file → best-meta` lookup (prefer lowest `start_line`). For a large index (tens of thousands of chunks) this is an O(N_chunks) scan per query on top of the adjacency load.

Fix: during `tf-upsert`, compute the representative chunk id per file and store it in `meta.json` (e.g., a sibling `file_to_primary.json` or a top-level key in `meta.json`). `tf-search` reads the prebuilt map and only needs to hit neighbor entries directly.

Pair this with the persisted-adjacency work above — both are query-time O(N) scans that disappear once we pay for them once at upsert time.

## TODO: Avoid double `resolve_paths` in live `tf-upsert` runs

**Status:** deferred.

Live `tf-upsert` currently calls `resolve_paths` in the parent (for Phase 2's loop) and again in the `--graph-only` subprocess. On a large repo with many files/globs that's a redundant filesystem walk.

Fix options:
1. Write the parent's resolved list to a temp file, pass `--paths-file <path>` to the subprocess. Subprocess skips its own walk.
2. Hand the subprocess the already-resolved absolute paths directly (Copilot's suggestion) — simple but risks ARG_MAX on very large repos.

Option 1 is the cleaner long-term fix; option 2 is a 1-line change that works for typical repos (< a few thousand files). Pick option 1 when this becomes a real bottleneck.

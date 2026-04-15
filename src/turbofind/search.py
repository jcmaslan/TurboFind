import sys
import json
import argparse
from .core import check_ollama, load_index, embed_text, file_sha1, find_project_root, load_file_adjacency, DEFAULT_INDEX
from .config import load_config
import numpy as np

def _check_stale(meta, project_root):
    """Check if a metadata entry's source has changed since indexing."""
    stored_hash = meta.get("content_sha1")
    if not stored_hash:
        return False  # no hash stored, can't determine staleness

    kind = meta.get("kind", "file")
    if kind == "file":
        import os
        fpath = os.path.join(project_root, meta.get("file_path", ""))
        if os.path.exists(fpath):
            return file_sha1(fpath) != stored_hash
        return True  # file deleted = stale

    # For non-file kinds, check referenced files
    ref_files = meta.get("referenced_files", {})
    if ref_files:
        import os
        for fpath, old_hash in ref_files.items():
            abs_path = os.path.join(project_root, fpath) if not os.path.isabs(fpath) else fpath
            if os.path.exists(abs_path):
                if file_sha1(abs_path) != old_hash:
                    return True
            elif old_hash != "not_found":
                return True  # file was there before, now gone
    return False

def main():
    parser = argparse.ArgumentParser(description="TurboFind Semantic Search")
    parser.add_argument("query", help="Semantic intent to search for")
    parser.add_argument("--index", default=DEFAULT_INDEX, help=f"Named index to search (default: {DEFAULT_INDEX})")
    parser.add_argument("--top-k", type=int, default=40, help="Max results to evaluate (elbow cutoff trims dynamically)")
    parser.add_argument("--floor", type=float, default=0.55, help="Absolute floor score below which results are discarded")
    parser.add_argument("--visual", action="store_true", help="Show colored score bars (for human use, not agent consumption)")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output results as JSON (for agent consumption)")
    parser.add_argument("--no-graph", action="store_true", help="Disable graph-expanded retrieval (seeds-only)")
    parser.add_argument("--graph-weight", type=float, default=None, help="RRF weight on graph-expansion rank (default from config, 1.0)")
    args = parser.parse_args()

    try:
        check_ollama()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    project_root = find_project_root()
    index, metadata = load_index(project_root=project_root, index_name=args.index)
    if len(index) == 0:
        print(f"Index '{args.index}' is empty or does not exist. Run tf-upsert first.")
        sys.exit(1)

    query_vector = embed_text(args.query, prefix="search_query: ")
    query_vector = np.array(query_vector, dtype=np.float32)

    matches = index.search(query_vector, count=args.top_k)
    keys = matches.keys
    distances = matches.distances
    # USearch cosine metric returns distance in [0, 2]. Convert to similarity in [0, 1].

    results = []
    for k, dist in zip(keys, distances):
        sim = 1.0 - (dist / 2.0)
        if sim >= args.floor:
            results.append((k, sim))

    # Apply Elbow Method drop-off
    final_results = []
    if results:
        final_results.append(results[0])
        for i in range(1, len(results)):
            prev_sim = results[i-1][1]
            curr_sim = results[i][1]
            # If drop is greater than 15%, we truncate here.
            if prev_sim - curr_sim > 0.15:
                break
            final_results.append(results[i])

    # Deduplicate by file (for file kind) or by content_sha1 (for non-file kinds)
    seen = {}
    display_list = []
    for k, sim in final_results:
        meta = metadata.get(k)
        if not meta:
            continue
        kind = meta.get("kind", "file")
        if kind == "file":
            dedup_key = meta.get("file_path", str(k))
        else:
            dedup_key = meta.get("content_sha1", k)
        if dedup_key not in seen:
            seen[dedup_key] = True
            display_list.append((sim, meta))

    # ── Graph expansion + RRF fusion ──
    if not args.no_graph and display_list:
        display_list = _graph_expand(display_list, metadata, project_root, args)

    if not display_list:
        print(f'tf-search "{args.query}" -- no results found.')
        sys.exit(0)

    # JSON output mode
    if args.json_output:
        json_results = []
        for sim, meta in display_list:
            entry = dict(meta)
            entry["score"] = round(float(sim), 4)
            entry["index"] = args.index
            entry["stale"] = _check_stale(meta, project_root)
            json_results.append(entry)
        print(json.dumps(json_results, indent=2))
        return

    # Count unique files in the index for context
    indexed_files = len(set(
        m.get("file_path", m.get("content_sha1", ""))
        for m in metadata.values()
    ))
    print(f'tf-search "{args.query}" -- {len(display_list)} results from {indexed_files} indexed entries\n')

    if args.visual:
        max_sim = display_list[0][0]
        min_sim = display_list[-1][0]
        bar_max_width = 20
        for idx, (sim, meta) in enumerate(display_list, 1):
            bar = _score_bar(sim, min_sim, max_sim, bar_max_width)
            kind = meta.get("kind", "file")
            _print_visual_result(idx, bar, sim, meta, kind, project_root)
    else:
        for idx, (sim, meta) in enumerate(display_list, 1):
            kind = meta.get("kind", "file")
            _print_text_result(idx, sim, meta, kind, project_root)


_WARNED_MISSING_GRAPH = False


def _rrf(ranked_lists, k=60, weights=None):
    fused = {}
    weights = weights or [1.0] * len(ranked_lists)
    for idx, lst in enumerate(ranked_lists):
        w = weights[idx]
        for rank, item in enumerate(lst):
            fused[item] = fused.get(item, 0.0) + w / (k + rank + 1)
    return sorted(fused, key=lambda x: -fused[x])


def _graph_expand(display_list, metadata, project_root, args):
    """Augment display_list with 1-hop graph neighbors fused via RRF."""
    global _WARNED_MISSING_GRAPH

    config = load_config(project_root)
    graph_cfg = config["search"]["graph"]
    if not graph_cfg.get("enabled", True):
        return display_list

    adj = load_file_adjacency(project_root, edge_weights=graph_cfg.get("edge_weights"))
    if not adj:
        if not _WARNED_MISSING_GRAPH:
            print("[tf-search] .turbofind/graph.json not found — graph expansion disabled", file=sys.stderr)
            _WARNED_MISSING_GRAPH = True
        return display_list

    # Collect seed files (first meta per file preserved)
    seed_files = {}
    seed_meta = {}
    for sim, meta in display_list:
        if meta.get("kind") != "file":
            continue
        fp = meta.get("file_path")
        if fp and fp not in seed_files:
            seed_files[fp] = sim
            seed_meta[fp] = meta
    if not seed_files:
        return display_list

    decay = float(graph_cfg.get("decay", 0.7))
    neighbor_scores = {}
    for sf, s_sim in seed_files.items():
        for nbr, w in adj.get(sf, {}).items():
            if nbr in seed_files:
                continue
            neighbor_scores[nbr] = neighbor_scores.get(nbr, 0.0) + s_sim * w * decay

    # Best representative meta per file (prefer earliest chunk)
    file_to_meta = {}
    for m in metadata.values():
        if m.get("kind") != "file":
            continue
        fp = m.get("file_path")
        if not fp:
            continue
        existing = file_to_meta.get(fp)
        if existing is None or m.get("start_line", 10**9) < existing.get("start_line", 10**9):
            file_to_meta[fp] = m

    seed_ranked = [fp for fp, _ in sorted(seed_files.items(), key=lambda kv: -kv[1])]
    graph_ranked = [fp for fp, _ in sorted(neighbor_scores.items(), key=lambda kv: -kv[1])
                    if fp in file_to_meta]

    if not graph_ranked:
        return display_list

    graph_weight = args.graph_weight if args.graph_weight is not None else float(graph_cfg.get("graph_weight", 1.0))
    if graph_weight <= 0:
        return display_list
    fused_order = _rrf([seed_ranked, graph_ranked], weights=[1.0, graph_weight])

    min_seed_sim = min(seed_files.values())
    floor = float(getattr(args, "floor", 0.0) or 0.0)
    new_display = []
    for fp in fused_order:
        if fp in seed_meta:
            new_display.append((seed_files[fp], seed_meta[fp]))
        else:
            synthetic = min(neighbor_scores[fp], 0.95 * min_seed_sim)
            if synthetic < floor:
                continue
            new_display.append((synthetic, file_to_meta[fp]))
    # Preserve non-file entries in their original order
    for sim, meta in display_list:
        if meta.get("kind") != "file":
            new_display.append((sim, meta))
    return new_display


def _print_text_result(idx, sim, meta, kind, project_root):
    """Print a single result in plain text format."""
    stale_marker = " (STALE)" if _check_stale(meta, project_root) else ""
    if kind == "file":
        filepath = meta.get("file_path", "unknown")
        print(f"[{idx}] [{kind}] {filepath} (score: {sim:.3f}){stale_marker}")
        print(f"    Lines {meta.get('start_line', '?')}-{meta.get('end_line', '?')}: {meta.get('core_intent', '')}")
    elif kind == "coupling":
        summary = meta.get("summary", "")
        ref_files = meta.get("referenced_files", {})
        files_str = " -> ".join(ref_files.keys()) if ref_files else "unknown"
        print(f"[{idx}] [{kind}] {files_str} (score: {sim:.3f}){stale_marker}")
        print(f"    {summary}")
    else:
        # insight, decision, or other
        summary = meta.get("summary", "")
        print(f"[{idx}] [{kind}] (score: {sim:.3f}){stale_marker}")
        print(f"    {summary}")


def _print_visual_result(idx, bar, sim, meta, kind, project_root):
    """Print a single result with colored score bar."""
    stale_marker = " (STALE)" if _check_stale(meta, project_root) else ""
    if kind == "file":
        filepath = meta.get("file_path", "unknown")
        print(f"[{idx}] {bar} {sim:.3f}  [{kind}] {filepath}{stale_marker}")
        print(f"              Lines {meta.get('start_line', '?')}-{meta.get('end_line', '?')}: {meta.get('core_intent', '')}")
    elif kind == "coupling":
        ref_files = meta.get("referenced_files", {})
        files_str = " -> ".join(ref_files.keys()) if ref_files else "unknown"
        print(f"[{idx}] {bar} {sim:.3f}  [{kind}] {files_str}{stale_marker}")
        print(f"              {meta.get('summary', '')}")
    else:
        print(f"[{idx}] {bar} {sim:.3f}  [{kind}]{stale_marker}")
        print(f"              {meta.get('summary', '')}")


def _score_bar(score, min_score, max_score, max_width):
    """Render a colored bar: green=high, yellow=mid, red=low."""
    span = max_score - min_score
    if span > 0:
        t = (score - min_score) / span
    else:
        t = 1.0

    width = max(1, int(t * max_width))

    if t >= 0.66:
        color = "32"   # green
    elif t >= 0.33:
        color = "33"   # yellow
    else:
        color = "31"   # red

    bar = "#" * width
    pad = " " * (max_width - width)
    return f"\033[{color}m{bar}\033[0m{pad}"


if __name__ == "__main__":
    main()

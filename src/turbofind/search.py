import sys
import json
import argparse
from .core import check_ollama, load_index, embed_text, file_sha1, find_project_root, DEFAULT_INDEX
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

    if not display_list:
        print(f'tf-search "{args.query}" -- no results found.')
        sys.exit(0)

    # JSON output mode
    if args.json_output:
        json_results = []
        for sim, meta in display_list:
            entry = dict(meta)
            entry["score"] = round(sim, 4)
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

import sys
import argparse
from .core import check_ollama, load_index, embed_text
import numpy as np

def main():
    parser = argparse.ArgumentParser(description="TurboFind Semantic Search")
    parser.add_argument("query", help="Semantic intent to search for")
    parser.add_argument("--top-k", type=int, default=10, help="Max results to evaluate")
    parser.add_argument("--floor", type=float, default=0.55, help="Absolute floor score below which results are discarded")
    parser.add_argument("--visual", action="store_true", help="Show colored score bars (for human use, not agent consumption)")
    args = parser.parse_args()
    
    try:
        check_ollama()
    except RuntimeError as e:
        print(e)
        sys.exit(1)
        
    index, metadata = load_index()
    if len(index) == 0:
        print("Index is empty or does not exist. Run tf-upsert first.")
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
            
    # Deduplicate by file, keeping highest score
    seen_files = {}
    display_list = []
    for k, sim in final_results:
        meta = metadata.get(k)
        if not meta:
            continue
        filepath = meta["file_path"]
        if filepath not in seen_files:
            seen_files[filepath] = True
            display_list.append((filepath, sim, meta))
            
    if not display_list:
        print(f"tf-search \"{args.query}\" — no results found.")
        sys.exit(0)

    # Count unique files in the index for context
    indexed_files = len(set(m["file_path"] for m in metadata.values()))
    print(f"tf-search \"{args.query}\" — {len(display_list)} results from {indexed_files} indexed files\n")

    if args.visual:
        max_sim = display_list[0][1]
        min_sim = display_list[-1][1]
        bar_max_width = 20
        for idx, (filepath, sim, meta) in enumerate(display_list, 1):
            bar = _score_bar(sim, min_sim, max_sim, bar_max_width)
            print(f"[{idx}] {bar} {sim:.3f}  {filepath}")
            print(f"              Lines {meta['start_line']}-{meta['end_line']}: {meta['core_intent']}")
    else:
        for idx, (filepath, sim, meta) in enumerate(display_list, 1):
            print(f"[{idx}] {filepath} (score: {sim:.3f})")
            print(f"    Lines {meta['start_line']}-{meta['end_line']}: {meta['core_intent']}")

def _score_bar(score, min_score, max_score, max_width):
    """Render a colored bar: green=high, yellow=mid, red=low."""
    # Normalize score to [0, 1] within the result set range
    span = max_score - min_score
    if span > 0:
        t = (score - min_score) / span  # 1.0 = best, 0.0 = worst
    else:
        t = 1.0

    width = max(1, int(t * max_width))

    # Color gradient: red(31) → yellow(33) → green(32)
    if t >= 0.66:
        color = "32"   # green
    elif t >= 0.33:
        color = "33"   # yellow
    else:
        color = "31"   # red

    bar = "█" * width
    pad = " " * (max_width - width)
    return f"\033[{color}m{bar}\033[0m{pad}"


if __name__ == "__main__":
    main()

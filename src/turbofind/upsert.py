import os
import sys
import uuid
import re
import time
import argparse
import glob as globlib
from anthropic import Anthropic, RateLimitError
from .core import (check_ollama, load_index, save_index, embed_text,
                   find_project_root, index_lock, file_sha1, text_sha1,
                   load_graph, save_graph, graph_to_xml, build_file_subgraph, index_graph, DEFAULT_INDEX)
from .prompts import SYSTEM_PROMPT
from .ast_utils import extract_definitions, extract_calls, extract_imports, build_topology
from .config import load_config, load_exclusion_spec, check_file_limits, estimate_file, compute_actual_cost, SOURCE_EXTENSIONS
import numpy as np

CHUNK_SIZE = 100
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

def get_repo_map(project_root):
    repo_map_path = os.path.join(project_root, "repo_map.txt")
    if os.path.exists(repo_map_path):
        with open(repo_map_path, "r") as f:
            return f.read()
    return "No repo_map.txt found."

def nuke_file(filepath, index, metadata):
    ids_to_remove = [vec_id for vec_id, data in metadata.items() if data.get("file_path") == filepath]
    for vid in ids_to_remove:
        index.remove(vid)
        del metadata[vid]
    return len(ids_to_remove)

def synthesize_with_claude(filepath, content, project_root, graph=None, graph_index=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    client = Anthropic(api_key=api_key)
    repo_map = get_repo_map(project_root)

    system_message = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT
        },
        {
            "type": "text",
            "text": f"Global Context (repo_map.txt):\n{repo_map}"
        }
    ]

    # repo_map is stable across files — always cache it so the global-context
    # prefix stays warm regardless of whether a per-file subgraph is attached.
    system_message[-1]["cache_control"] = {"type": "ephemeral"}

    subgraph_xml = None
    if graph:
        sub = build_file_subgraph(graph, filepath, index=graph_index)
        if sub["nodes"]:
            subgraph_xml = graph_to_xml(sub)

    if subgraph_xml:
        # Per-file subgraph varies every request; no cache_control here.
        system_message.append({
            "type": "text",
            "text": f"<global_ast_graph>\n{subgraph_xml}\n</global_ast_graph>",
        })

    model = os.environ.get("TURBOFIND_MODEL", DEFAULT_MODEL)

    # Retry with exponential backoff on rate limit errors
    max_retries = 3
    backoff = 30
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1000,
                temperature=0.2,
                system=system_message,
                messages=[
                    {
                        "role": "user",
                        "content": f"Analyze this file ({filepath}):\n\n{content}"
                    }
                ]
            )
            return response.content[0].text, response.usage
        except RateLimitError as e:
            if attempt < max_retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"  Rate limited, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

def extract_xml_tag(xml_str, tag):
    match = re.search(f"<{tag}>(.*?)</{tag}>", xml_str, re.DOTALL)
    return match.group(1).strip() if match else "None"

def chunk_file(filepath, content):
    lines = content.split('\n')
    chunks = []
    for i in range(0, len(lines), CHUNK_SIZE):
        chunk_lines = lines[i:i+CHUNK_SIZE]
        chunks.append({
            "start": i + 1,
            "end": i + len(chunk_lines),
            "content": '\n'.join(chunk_lines)
        })
    return chunks

def get_unique_id():
    # usearch prefers integer keys, generate a random 64-bit int
    return uuid.uuid4().int >> 64


def upsert_single_file(filepath, project_root, index, metadata, graph=None, graph_index=None):
    """Process a single file through the full Nuke-Synthesize-Chunk-Embed-Pave pipeline."""
    rel_path = os.path.relpath(filepath, project_root)

    removed_count = nuke_file(rel_path, index, metadata)
    if removed_count > 0:
        print(f"  Removed {removed_count} old vectors")

    with open(filepath, 'r') as f:
        content = f.read()

    content_hash = text_sha1(content)

    print(f"  Synthesizing with Claude...")
    synthesis, usage = synthesize_with_claude(rel_path, content, project_root, graph, graph_index)

    actual_cost = compute_actual_cost(usage)
    severity = extract_xml_tag(synthesis, "legacy_coupling_severity")
    core_intent = extract_xml_tag(synthesis, "core_intent")
    key_symbols = extract_xml_tag(synthesis, "key_symbols")
    print(f"  Synthesis complete (severity: {severity}/10, actual: ${actual_cost:.4f})")

    chunks = chunk_file(rel_path, content)

    symbol_line = f"\n\nKey symbols: {key_symbols}" if key_symbols and key_symbols != "None" else ""
    for chunk in chunks:
        contextualized_chunk = f"{synthesis}{symbol_line}\n\n--- Source Code ---\n{chunk['content']}"
        vector = embed_text(contextualized_chunk, prefix="search_document: ")

        vid = get_unique_id()
        index.add(vid, np.array(vector, dtype=np.float32))
        metadata[vid] = {
            "kind": "file",
            "file_path": rel_path,
            "start_line": chunk["start"],
            "end_line": chunk["end"],
            "core_intent": core_intent,
            "content_sha1": content_hash
        }

    print(f"  Embedded {len(chunks)} chunks")
    return len(chunks), actual_cost


def upsert_text_input(text, index, metadata, kind="insight", summary=None, referenced_files=None):
    """Index arbitrary text input (debug insight, coupling, decision, etc.)."""
    content_hash = text_sha1(text)

    # Compute referenced file hashes
    ref_hashes = {}
    if referenced_files:
        for fpath in referenced_files:
            if os.path.exists(fpath):
                ref_hashes[fpath] = file_sha1(fpath)
            else:
                ref_hashes[fpath] = "not_found"

    vector = embed_text(text, prefix="search_document: ")
    vid = get_unique_id()
    index.add(vid, np.array(vector, dtype=np.float32))

    entry = {
        "kind": kind,
        "summary": summary or text[:200],
        "content_sha1": content_hash,
    }
    if ref_hashes:
        entry["referenced_files"] = ref_hashes

    metadata[vid] = entry
    return 1


def resolve_paths(args_paths, project_root, exclusion_spec):
    """Expand paths into a list of indexable source files.

    - If a path is a directory, walk it recursively and collect files matching SOURCE_EXTENSIONS.
    - If a path is a file, include it directly.
    - If a path is a glob pattern, expand it.
    All results are filtered through the exclusion spec (.gitignore + extra patterns).
    """
    all_files = []
    seen = set()

    for p in args_paths:
        abs_p = os.path.abspath(p)

        if os.path.isdir(abs_p):
            for root, dirs, filenames in os.walk(abs_p):
                for fname in filenames:
                    if os.path.splitext(fname)[1] in SOURCE_EXTENSIONS:
                        full = os.path.join(root, fname)
                        rel = os.path.relpath(full, project_root)
                        if full not in seen and not exclusion_spec.match_file(rel):
                            all_files.append(full)
                            seen.add(full)
        elif os.path.isfile(abs_p):
            rel = os.path.relpath(abs_p, project_root)
            if abs_p not in seen and not exclusion_spec.match_file(rel):
                all_files.append(abs_p)
                seen.add(abs_p)
        else:
            # Treat as glob pattern
            for f in globlib.glob(p, recursive=True):
                full = os.path.abspath(f)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, project_root)
                if full not in seen and not exclusion_spec.match_file(rel):
                    all_files.append(full)
                    seen.add(full)

    all_files.sort()
    return all_files


def _ensure_safe_allocator():
    """On macOS, tree-sitter + usearch + httpx trip the nano malloc zone's
    free-list invariants. Re-exec once with MallocNanoZone=0 to sidestep it."""
    if sys.platform != "darwin":
        return
    if os.environ.get("_TURBOFIND_ALLOC_FIXED") == "1":
        return
    env = os.environ.copy()
    env["MallocNanoZone"] = "0"
    env["_TURBOFIND_ALLOC_FIXED"] = "1"
    os.execvpe(sys.executable, [sys.executable, "-m", "turbofind.upsert"] + sys.argv[1:], env)


def main():
    _ensure_safe_allocator()
    parser = argparse.ArgumentParser(description="TurboFind: Semantic index upsert")
    parser.add_argument("paths", nargs="*", help="File path(s) or glob pattern(s) to index")
    parser.add_argument("--index", default=DEFAULT_INDEX, help=f"Named index to upsert into (default: {DEFAULT_INDEX})")
    parser.add_argument("--input", dest="text_input", default=None, help="File path or '-' for stdin; indexes arbitrary text instead of source files")
    parser.add_argument("--kind", default="insight", choices=["insight", "coupling", "decision"],
                        help="Result kind for --input entries (default: insight); 'file' kind is set automatically when indexing source files")
    parser.add_argument("--summary", default=None, help="Short summary for --input entries (auto-generated if omitted)")
    parser.add_argument("--ref", action="append", dest="referenced_files", default=None,
                        help="File path referenced by this --input entry (repeatable); SHA1 stored for staleness detection")
    parser.add_argument("--max-file-size", type=int, default=None, help="Per-file: max size in bytes (default: 51200)")
    parser.add_argument("--max-lines", type=int, default=None, help="Per-file: max line count (default: 2000)")
    parser.add_argument("--max-files", type=int, default=None, help="Per-batch: max number of files to process (default: 100)")
    parser.add_argument("--cost-limit", type=float, default=None, help="Per-batch: pause for confirmation above this $ amount (default: 5.00)")
    parser.add_argument("--remove", action="append", dest="remove_paths", default=None,
                        help="Remove a deleted file from the index (repeatable)")
    parser.add_argument("--prune", action="store_true",
                        help="Remove all index entries whose source files no longer exist on disk")
    parser.add_argument("--graph-only", action="store_true",
                        help="Build topology graph (graph.json) only — no synthesis, no embedding, no API calls")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be indexed without calling any APIs")
    args = parser.parse_args()

    # Validate mutually exclusive modes
    mode_flags = sum([
        bool(args.remove_paths),
        bool(args.prune),
        args.text_input is not None,
        args.graph_only,
    ])
    if mode_flags > 1:
        parser.error("--remove, --prune, --input, and --graph-only are mutually exclusive")
    if args.dry_run and (args.graph_only or args.text_input is not None):
        parser.error("--dry-run cannot be used with --graph-only or --input")

    # ── Remove mode ──
    if args.remove_paths:
        project_root = find_project_root()
        with index_lock(project_root):
            index, metadata = load_index(project_root=project_root, index_name=args.index)
            graph = load_graph(project_root=project_root)
            total_removed = 0
            for filepath in args.remove_paths:
                try:
                    rel_path = os.path.relpath(os.path.abspath(filepath), project_root)
                except ValueError:
                    print(f"Skipping path on a different drive: {filepath}")
                    continue
                count = nuke_file(rel_path, index, metadata)
                # Remove nodes/edges for this file from topology
                removed_ids = {n["id"] for n in graph.get("nodes", []) if n.get("file") == rel_path}
                graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("file") != rel_path]
                graph["edges"] = [e for e in graph.get("edges", [])
                                  if e["from"] not in removed_ids and e["to"] not in removed_ids]
                if count > 0:
                    print(f"Removed {count} vectors for {rel_path}")
                    total_removed += count
                else:
                    print(f"No index entries found for {rel_path}")
            if total_removed > 0:
                save_index(index, metadata, project_root=project_root, index_name=args.index)
            save_graph(graph, project_root=project_root)
        return

    # ── Prune mode ──
    if args.prune:
        project_root = find_project_root()
        with index_lock(project_root):
            index, metadata = load_index(project_root=project_root, index_name=args.index)
            graph = load_graph(project_root=project_root)
            stale_files = set()
            for vid, entry in metadata.items():
                fpath = entry.get("file_path")
                if fpath and not os.path.exists(os.path.join(project_root, fpath)):
                    stale_files.add(fpath)
            total_removed = 0
            for fpath in sorted(stale_files):
                count = nuke_file(fpath, index, metadata)
                # Remove nodes/edges for this file from topology
                removed_ids = {n["id"] for n in graph.get("nodes", []) if n.get("file") == fpath}
                graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("file") != fpath]
                graph["edges"] = [e for e in graph.get("edges", [])
                                  if e["from"] not in removed_ids and e["to"] not in removed_ids]
                print(f"Pruned {count} vectors for {fpath} (file no longer exists)")
                total_removed += count
            if total_removed > 0:
                save_index(index, metadata, project_root=project_root, index_name=args.index)
                print(f"Pruned {total_removed} vectors from {len(stale_files)} deleted files.")
            else:
                print("No stale entries found.")
            save_graph(graph, project_root=project_root)
        return

    # ── Text input mode (Phase 2) ──
    if args.text_input is not None:
        if args.text_input == "-":
            text = sys.stdin.read()
        else:
            with open(args.text_input, 'r') as f:
                text = f.read()

        if not text.strip():
            print("Empty input -- nothing to index.")
            sys.exit(0)

        try:
            check_ollama()
        except RuntimeError as e:
            print(e)
            sys.exit(1)

        # Determine project root from cwd for text input mode
        project_root = find_project_root()

        with index_lock(project_root):
            index, metadata = load_index(project_root=project_root, index_name=args.index)
            count = upsert_text_input(
                text, index, metadata,
                kind=args.kind,
                summary=args.summary,
                referenced_files=args.referenced_files,
            )
            save_index(index, metadata, project_root=project_root, index_name=args.index)

        print(f"Indexed 1 entry into '{args.index}' (kind: {args.kind})")
        return

    # ── Graph-only mode ──
    if args.graph_only:
        if not args.paths:
            parser.error("paths are required when using --graph-only")

        first_path = os.path.abspath(args.paths[0])
        start_dir = os.path.dirname(first_path) if os.path.isfile(first_path) else first_path
        project_root = find_project_root(start_dir)

        config = load_config(project_root)
        if args.max_file_size is not None:
            config["per_file"]["max_size_bytes"] = args.max_file_size
        if args.max_lines is not None:
            config["per_file"]["max_lines"] = args.max_lines
        if args.max_files is not None:
            config["per_batch"]["max_files"] = args.max_files
        exclusion_spec = load_exclusion_spec(project_root, config["exclude"]["patterns"])
        files = resolve_paths(args.paths, project_root, exclusion_spec)[: config["per_batch"]["max_files"]]

        if not files:
            print("No files matched after applying exclusions.")
            sys.exit(0)

        print(f"Building topology graph ({len(files)} files)...")
        all_defs = []
        all_calls = []
        all_imps = []
        successfully_extracted = set()
        total = len(files)
        for i, filepath in enumerate(files, 1):
            rel_path = os.path.relpath(filepath, project_root)
            ok, reason = check_file_limits(filepath, config)
            if not ok:
                print(f"\n  Skipped topology for {rel_path}: {reason}")
            else:
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()
                    all_defs.extend(extract_definitions(rel_path, content))
                    all_calls.extend(extract_calls(rel_path, content))
                    all_imps.extend(extract_imports(rel_path, content))
                    successfully_extracted.add(rel_path)
                except Exception as e:
                    print(f"\n  Skipped topology for {rel_path}: {e}")
            sys.stdout.write(f"\r  [{i}/{total}] extracted — {len(all_defs)} defs, {len(all_calls)} calls, {len(all_imps)} imports")
            sys.stdout.flush()
            if i == total:
                sys.stdout.write("\n")

        with index_lock(project_root):
            graph = load_graph(project_root=project_root)
            existing_nodes = [n for n in graph.get("nodes", []) if n["file"] not in successfully_extracted]
            existing_defs = [{"id": n["id"], "file": n["file"], "type": n["type"], "line": n["line"]}
                             for n in existing_nodes]
            combined_defs = existing_defs + all_defs
            topo = build_topology(combined_defs, all_calls, all_imps)
            final_node_ids = set(topo.nodes)
            reextracted_node_ids = {d["id"] for d in all_defs}
            new_edges = [{"from": u, "to": v, "type": d.get("type", "calls")}
                         for u, v, d in topo.edges(data=True)]
            # Preserve old edges only from non-re-extracted source nodes,
            # if both endpoints still exist, to avoid retaining stale edges
            preserved_edges = [e for e in graph.get("edges", [])
                               if e["from"] in final_node_ids
                               and e["to"] in final_node_ids
                               and e["from"] not in reextracted_node_ids]
            graph["nodes"] = [{"id": n, **topo.nodes[n]} for n in topo.nodes]
            graph["edges"] = new_edges + preserved_edges

            save_graph(graph, project_root=project_root)
        print(f"Done. {len(graph['nodes'])} definitions, {len(graph['edges'])} edges saved to .turbofind/graph.json")
        return

    # ── Source file mode (original behavior) ──
    if not args.paths:
        parser.error("paths are required when not using --input")

    # Discover project root from the first path
    first_path = os.path.abspath(args.paths[0])
    start_dir = os.path.dirname(first_path) if os.path.isfile(first_path) else first_path
    project_root = find_project_root(start_dir)

    # Load config and apply CLI overrides
    config = load_config(project_root)
    if args.max_file_size is not None:
        config["per_file"]["max_size_bytes"] = args.max_file_size
    if args.max_lines is not None:
        config["per_file"]["max_lines"] = args.max_lines
    if args.max_files is not None:
        config["per_batch"]["max_files"] = args.max_files
    if args.cost_limit is not None:
        config["per_batch"]["cost_limit"] = args.cost_limit

    # Build exclusion spec from .gitignore + config
    exclusion_spec = load_exclusion_spec(project_root, config["exclude"]["patterns"])

    # Resolve and filter file list
    files = resolve_paths(args.paths, project_root, exclusion_spec)

    if not files:
        print("No files matched after applying exclusions.")
        sys.exit(0)

    max_files = config["per_batch"]["max_files"]
    cost_limit = config["per_batch"]["cost_limit"]

    # ── Dry run ──
    if args.dry_run:
        total_cost = 0.0
        total_time_ms = 0
        for i, f in enumerate(files[:max_files]):
            rel = os.path.relpath(f, project_root)
            ok, reason = check_file_limits(f, config)
            cost, time_ms = estimate_file(f)
            total_cost += cost
            total_time_ms += time_ms
            status = "OK" if ok else f"SKIP ({reason})"
            print(f"  [{i+1}] {rel} -- ${cost:.4f}, ~{time_ms:,}ms -- {status}")
        if len(files) > max_files:
            print(f"\n  ... and {len(files) - max_files} more files (would exceed --max-files {max_files})")
        total_time_s = total_time_ms / 1000
        time_label = f"{total_time_s:.0f}s" if total_time_s < 120 else f"{total_time_s/60:.1f}min"
        print(f"\n  Total: {min(len(files), max_files)} files, est. ${total_cost:.2f}, ~{time_label}")
        return

    # ── Live run ──
    try:
        check_ollama()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    # ── Phase 1: Build topology in a subprocess ──
    # tree-sitter's Python bindings corrupt the heap when many Parser/Tree
    # objects are allocated and freed in-process; running Phase 1 in a child
    # process isolates that damage so Phase 2 (usearch + HTTP) runs cleanly.
    import subprocess
    subprocess_cmd = [sys.executable, "-m", "turbofind.upsert", "--graph-only",
                      "--index", args.index]
    if args.max_file_size is not None:
        subprocess_cmd += ["--max-file-size", str(args.max_file_size)]
    if args.max_lines is not None:
        subprocess_cmd += ["--max-lines", str(args.max_lines)]
    if args.max_files is not None:
        subprocess_cmd += ["--max-files", str(args.max_files)]
    subprocess_cmd += list(args.paths)
    result = subprocess.run(subprocess_cmd)
    if result.returncode != 0:
        print("Topology build failed; aborting.")
        sys.exit(result.returncode)
    graph = load_graph(project_root=project_root)
    graph_index_data = index_graph(graph)
    print(f"Topology: {len(graph['nodes'])} definitions, {len(graph['edges'])} edges (per-file 1-hop subgraphs injected at synthesis)")

    # ── Phase 2: Synthesize + embed (API calls) ──
    with index_lock(project_root):
        index, metadata = load_index(project_root=project_root, index_name=args.index)

        processed = 0
        skipped = 0
        estimated_cost = 0.0
        actual_cost = 0.0
        confirmed_over_limit = False

        try:
            # Cap by attempted paths (not just processed) so Phase 2 stays
            # aligned with the first max_files entries Phase 1 extracted into
            # graph.json — otherwise skipped files let Phase 2 drift past
            # Phase 1's slice and index files with no subgraph coverage.
            for attempted, filepath in enumerate(files):
                if attempted >= max_files:
                    remaining = len(files) - attempted
                    print(f"\nReached --max-files limit ({max_files}). {remaining} files remaining.")
                    break

                rel_path = os.path.relpath(filepath, project_root)

                # Per-file limit check
                ok, reason = check_file_limits(filepath, config)
                if not ok:
                    print(f"SKIP {rel_path}: {reason}")
                    skipped += 1
                    continue

                # Cost check (uses estimate for pre-confirmation)
                est_cost, _ = estimate_file(filepath)
                estimated_cost += est_cost

                if estimated_cost > cost_limit and not confirmed_over_limit:
                    print(f"\nWARNING: Estimated cumulative cost: ${estimated_cost:.2f} (limit: ${cost_limit:.2f})")
                    response = input("Continue? [y/N] ").strip().lower()
                    if response != "y":
                        print("Stopped.")
                        save_index(index, metadata, project_root=project_root, index_name=args.index)
                        sys.exit(0)
                    confirmed_over_limit = True

                print(f"[{processed+1}/{min(len(files), max_files)}] {rel_path}")

                try:
                    _, file_cost = upsert_single_file(filepath, project_root, index, metadata, graph=graph, graph_index=graph_index_data)
                    actual_cost += file_cost
                    processed += 1
                except Exception as e:
                    print(f"  FAILED: {e}")
                    skipped += 1

        except KeyboardInterrupt:
            print(f"\n\nInterrupted. Saving {processed} files indexed so far...")

        save_index(index, metadata, project_root=project_root, index_name=args.index)
        # graph.json was already written by the --graph-only subprocess under its
        # own lock; resaving here would race with any other process that updated
        # it in between, so we leave it alone.
        print(f"\nDone. Processed {processed} files, skipped {skipped}. Actual cost: ${actual_cost:.4f}")


if __name__ == "__main__":
    main()

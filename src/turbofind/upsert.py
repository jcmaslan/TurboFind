import os
import sys
import uuid
import re
from anthropic import Anthropic
from .core import check_ollama, load_index, save_index, embed_text, find_project_root, index_lock
from .prompts import SYSTEM_PROMPT
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

def synthesize_with_claude(filepath, content, project_root):
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
            "text": f"Global Context (repo_map.txt):\n{repo_map}",
            "cache_control": {"type": "ephemeral"}
        }
    ]
    
    model = os.environ.get("TURBOFIND_MODEL", DEFAULT_MODEL)
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
    return response.content[0].text

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

import argparse
import glob as globlib
from .config import load_config, load_exclusion_spec, check_file_limits, estimate_file, SOURCE_EXTENSIONS


def upsert_single_file(filepath, project_root, index, metadata):
    """Process a single file through the full Nuke-Synthesize-Chunk-Embed-Pave pipeline."""
    rel_path = os.path.relpath(filepath, project_root)

    removed_count = nuke_file(rel_path, index, metadata)
    if removed_count > 0:
        print(f"  🗑️ Removed {removed_count} old vectors")

    with open(filepath, 'r') as f:
        content = f.read()

    print(f"  🤖 Synthesizing with Claude...")
    synthesis = synthesize_with_claude(rel_path, content, project_root)

    severity = extract_xml_tag(synthesis, "legacy_coupling_severity")
    core_intent = extract_xml_tag(synthesis, "core_intent")
    print(f"  ✅ Synthesis complete (severity: {severity}/10)")

    chunks = chunk_file(rel_path, content)

    for chunk in chunks:
        contextualized_chunk = f"{synthesis}\n\n--- Source Code ---\n{chunk['content']}"
        vector = embed_text(contextualized_chunk, prefix="search_document: ")

        vid = get_unique_id()
        index.add(vid, np.array(vector, dtype=np.float32))
        metadata[vid] = {
            "file_path": rel_path,
            "start_line": chunk["start"],
            "end_line": chunk["end"],
            "core_intent": core_intent
        }

    print(f"  📐 Embedded {len(chunks)} chunks")
    return len(chunks)


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


def main():
    parser = argparse.ArgumentParser(description="TurboFind: Semantic index upsert")
    parser.add_argument("paths", nargs="+", help="File path(s) or glob pattern(s) to index")
    parser.add_argument("--max-file-size", type=int, default=None, help="Per-file: max size in bytes (default: 51200)")
    parser.add_argument("--max-lines", type=int, default=None, help="Per-file: max line count (default: 2000)")
    parser.add_argument("--max-files", type=int, default=None, help="Per-batch: max number of files to process (default: 100)")
    parser.add_argument("--cost-limit", type=float, default=None, help="Per-batch: pause for confirmation above this $ amount (default: 5.00)")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be indexed without calling any APIs")
    args = parser.parse_args()

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
            status = "✓" if ok else f"SKIP ({reason})"
            print(f"  [{i+1}] {rel} — ${cost:.4f}, ~{time_ms:,}ms — {status}")
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

    with index_lock(project_root):
        index, metadata = load_index(project_root=project_root)

        processed = 0
        skipped = 0
        cumulative_cost = 0.0
        confirmed_over_limit = False

        try:
            for filepath in files:
                if processed >= max_files:
                    remaining = len(files) - processed - skipped
                    print(f"\n⏸  Reached --max-files limit ({max_files}). {remaining} files remaining.")
                    break

                rel_path = os.path.relpath(filepath, project_root)

                # Per-file limit check
                ok, reason = check_file_limits(filepath, config)
                if not ok:
                    print(f"⏭  Skipping {rel_path}: {reason}")
                    skipped += 1
                    continue

                # Cost check
                est_cost, _ = estimate_file(filepath)
                cumulative_cost += est_cost

                if cumulative_cost > cost_limit and not confirmed_over_limit:
                    print(f"\n⚠️  Estimated cumulative cost: ${cumulative_cost:.2f} (limit: ${cost_limit:.2f})")
                    response = input("Continue? [y/N] ").strip().lower()
                    if response != "y":
                        print("Stopped.")
                        save_index(index, metadata, project_root=project_root)
                        sys.exit(0)
                    confirmed_over_limit = True

                print(f"[{processed+1}/{min(len(files), max_files)}] {rel_path} (est. ${est_cost:.4f}, cumul. ${cumulative_cost:.2f})")

                try:
                    upsert_single_file(filepath, project_root, index, metadata)
                    processed += 1
                except Exception as e:
                    print(f"  ❌ Failed: {e}")
                    skipped += 1

        except KeyboardInterrupt:
            print(f"\n\n⏹  Interrupted. Saving {processed} files indexed so far...")

        save_index(index, metadata, project_root=project_root)
        print(f"\n✅ Done. Processed {processed} files, skipped {skipped}. Est. total cost: ${cumulative_cost:.2f}")


if __name__ == "__main__":
    main()


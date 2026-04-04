import os
import argparse

SENTINEL_START = "<!-- turbofind -->"
SENTINEL_END = "<!-- /turbofind -->"

TURBOFIND_BLOCK = f"""{SENTINEL_START}
## TurboFind Migration Protocol

You have access to two semantic search tools. Use them directly in the terminal:

- `tf-search "<query>"` — Semantic vector search across the indexed codebase
- `tf-upsert <filepath>` — Updates the semantic index after an edit

### PRE-EDIT RULE (Investigation)
When investigating the codebase, planning a migration, or locating dependencies:
Execute `tf-search "<semantic intent>"` FIRST, then proceed with your normal workflow.

### POST-EDIT RULE (Synchronization)
After modifying, refactoring, or creating any file:
IMMEDIATELY execute `tf-upsert <filepath>` before your next step.
{SENTINEL_END}"""

def install():
    path = "CLAUDE.md"

    if os.path.exists(path):
        with open(path, "r") as f:
            existing = f.read()

        if SENTINEL_START in existing:
            print("✅ TurboFind instructions already present in CLAUDE.md — nothing to do.")
            return

        with open(path, "a") as f:
            f.write("\n" + TURBOFIND_BLOCK)
        print("✅ Appended TurboFind instructions to existing CLAUDE.md.")
        print("   Next step: run `tf-upsert .` to build the semantic index.")
    else:
        with open(path, "w") as f:
            f.write(TURBOFIND_BLOCK)
        print("✅ Created CLAUDE.md with TurboFind instructions.")
        print("   Next step: run `tf-upsert .` to build the semantic index.")

def remove():
    path = "CLAUDE.md"

    if not os.path.exists(path):
        print("No CLAUDE.md found — nothing to remove.")
        return

    with open(path, "r") as f:
        content = f.read()

    if SENTINEL_START not in content:
        print("No TurboFind instructions found in CLAUDE.md — nothing to remove.")
        return

    start = content.index(SENTINEL_START)
    # Find the end sentinel, including trailing newline if present
    end = content.index(SENTINEL_END) + len(SENTINEL_END)
    if end < len(content) and content[end] == "\n":
        end += 1

    # Remove the block and any resulting double blank lines
    cleaned = content[:start] + content[end:]
    cleaned = cleaned.strip()

    with open(path, "w") as f:
        f.write(cleaned + "\n" if cleaned else "")
    print("✅ Removed TurboFind instructions from CLAUDE.md.")

def main():
    parser = argparse.ArgumentParser(description="Initialize or remove TurboFind from CLAUDE.md")
    parser.add_argument("--remove", action="store_true", help="Remove TurboFind instructions from CLAUDE.md")
    args = parser.parse_args()

    if args.remove:
        remove()
    else:
        install()

if __name__ == "__main__":
    main()

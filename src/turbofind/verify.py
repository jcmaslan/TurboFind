import argparse
import sys
import os
from .core import find_project_root, load_graph

def main():
    parser = argparse.ArgumentParser(description="TurboFind: Local Verification tool for repository topology")
    subparsers = parser.add_subparsers(dest="command", help="Verification command")

    # check-node command
    check_parser = subparsers.add_parser("check-node", help="Show topology for a specific file")
    check_parser.add_argument("file", help="File path to verify")
    check_parser.add_argument("-n", "--node", help="Filter to a specific definition name", default=None)

    args = parser.parse_args()

    if args.command == "check-node":
        project_root = find_project_root()
        try:
            graph = load_graph(project_root=project_root)
        except Exception as e:
            print(f"Error loading graph: {e}")
            sys.exit(1)

        try:
            rel_path = os.path.relpath(os.path.abspath(args.file), project_root)
        except ValueError:
            print(f"Error: {args.file} is not within the project root {project_root}")
            sys.exit(1)

        # Filter nodes for this file
        file_nodes = [n for n in graph.get("nodes", []) if n.get("file") == rel_path]
        if not file_nodes:
            print(f"No topology entries for {rel_path}.")
            print("Have you run `tf-upsert` on it?")
            sys.exit(1)

        # If filtering by name, narrow down
        if args.node:
            file_nodes = [n for n in file_nodes if args.node in n["id"]]
            if not file_nodes:
                print(f"No definition matching '{args.node}' in {rel_path}.")
                sys.exit(1)

        node_ids = {n["id"] for n in file_nodes}
        all_node_ids = {n["id"] for n in graph.get("nodes", [])}

        # Find edges involving these nodes
        edges_out = [e for e in graph.get("edges", []) if e["from"] in node_ids]
        edges_in = [e for e in graph.get("edges", []) if e["to"] in node_ids]

        print(f"--- Topology for {rel_path} ---")
        print(f"\nDefinitions ({len(file_nodes)}):")
        for n in sorted(file_nodes, key=lambda x: x.get("line", 0)):
            print(f"  [{n['type']}] {n['id']}  (line {n.get('line', '?')})")

        if edges_out:
            print(f"\nCalls out ({len(edges_out)}):")
            for e in edges_out:
                print(f"  {e['from']} -> {e['to']}")

        if edges_in:
            print(f"\nCalled by ({len(edges_in)}):")
            for e in edges_in:
                print(f"  {e['from']} -> {e['to']}")

        if not edges_out and not edges_in:
            print("\nNo cross-file edges detected.")

    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

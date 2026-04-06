import argparse
import sys
import os
import json
from .core import find_project_root, load_graph

def main():
    parser = argparse.ArgumentParser(description="TurboFind: Local Verification tool for AST Graph")
    subparsers = parser.add_subparsers(dest="command", help="Verification command")
    
    # check-node command
    check_parser = subparsers.add_parser("check-node", help="Interrogate the AST for a specific file")
    check_parser.add_argument("file", help="File path to verify")
    check_parser.add_argument("-n", "--node", help="Node identifier or path (e.g. function name)", default=None)
    
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
        
        if rel_path not in graph:
            print(f"Error: File {rel_path} not found in the global AST graph.")
            print("Have you run `tf-upsert` on it?")
            sys.exit(1)
            
        file_ast = graph[rel_path]
        
        if args.node:
            # Stub logic for node check
            print(f"--- Verification result for node '{args.node}' in {rel_path} ---")
            print("(Stub response: displaying file AST snippet)")
            
            # Very basic text search for stub
            node_str = json.dumps(file_ast, indent=2)
            print(node_str[:1500] + ("\n...(truncated)" if len(node_str) > 1500 else ""))
        else:
            print(f"--- Full AST representation for {rel_path} ---")
            node_str = json.dumps(file_ast, indent=2)
            print(node_str[:1500] + ("\n...(truncated)" if len(node_str) > 1500 else ""))
            
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

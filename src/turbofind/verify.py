"""tf-verify: Ground-truth verification oracle for the topology graph.

This CLI is the query interface for the symbolic graph that will back the
Verification API in the Test-Time Training loop. It is not currently exposed
to Claude via CLAUDE.md — the model does not call tf-verify directly.
Instead, these predicates (calls, callers-of, deps-of, dependents-of,
impact, path) will be invoked by the Verification API to validate the
model's structural claims against the ground-truth graph.
"""
import argparse
import json
import sys
import os
import networkx as nx
from .core import find_project_root, load_graph, load_graph_as_nx


def _resolve_node(G, pattern):
    """Resolve a node pattern (substring match) to matching node IDs."""
    return [n for n in G.nodes if pattern in n]


def _resolve_file_nodes(G, filepath):
    """Get all nodes belonging to a file."""
    return [n for n, data in G.nodes(data=True) if data.get("file") == filepath]


def _output(result, as_json):
    """Print result as JSON or human-readable text."""
    if as_json:
        print(json.dumps(result, indent=2))
    elif isinstance(result, bool):
        print("true" if result else "false")
    elif isinstance(result, list):
        for item in result:
            print(f"  {item}")
    elif isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, list):
                print(f"{k}:")
                for item in v:
                    print(f"  {item}")
            else:
                print(f"{k}: {v}")


def cmd_check_node(args):
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

    file_nodes = [n for n in graph.get("nodes", []) if n.get("file") == rel_path]
    if not file_nodes:
        print(f"No topology entries for {rel_path}.")
        print("Have you run `tf-upsert` on it?")
        sys.exit(1)

    if args.node:
        file_nodes = [n for n in file_nodes if args.node in n["id"]]
        if not file_nodes:
            print(f"No definition matching '{args.node}' in {rel_path}.")
            sys.exit(1)

    node_ids = {n["id"] for n in file_nodes}

    edges_out = [e for e in graph.get("edges", []) if e["from"] in node_ids]
    edges_in = [e for e in graph.get("edges", []) if e["to"] in node_ids]

    if args.json:
        print(json.dumps({
            "file": rel_path,
            "definitions": file_nodes,
            "edges_out": edges_out,
            "edges_in": edges_in,
        }, indent=2))
        return

    print(f"--- Topology for {rel_path} ---")
    print(f"\nDefinitions ({len(file_nodes)}):")
    for n in sorted(file_nodes, key=lambda x: x.get("line", 0)):
        print(f"  [{n['type']}] {n['id']}  (line {n.get('line', '?')})")

    if edges_out:
        print(f"\nEdges out ({len(edges_out)}):")
        for e in edges_out:
            print(f"  {e['from']} --[{e.get('type', 'calls')}]--> {e['to']}")

    if edges_in:
        print(f"\nEdges in ({len(edges_in)}):")
        for e in edges_in:
            print(f"  {e['from']} --[{e.get('type', 'calls')}]--> {e['to']}")

    if not edges_out and not edges_in:
        print("\nNo cross-file edges detected.")


def cmd_query(args):
    project_root = find_project_root()
    G = load_graph_as_nx(project_root)

    predicate = args.predicate
    pred_args = args.pred_args
    as_json = args.json

    if predicate == "calls":
        if len(pred_args) != 2:
            print("Usage: tf-verify query calls <source_pattern> <target_pattern>")
            sys.exit(1)
        sources = _resolve_node(G, pred_args[0])
        targets = _resolve_node(G, pred_args[1])
        found = []
        for s in sources:
            for t in targets:
                if G.has_edge(s, t):
                    found.append({"from": s, "to": t, "type": G.edges[s, t].get("type", "calls")})
        result = {"match": len(found) > 0, "edges": found}
        _output(result, as_json)

    elif predicate == "callers-of":
        if len(pred_args) != 1:
            print("Usage: tf-verify query callers-of <node_pattern>")
            sys.exit(1)
        nodes = _resolve_node(G, pred_args[0])
        callers = set()
        for n in nodes:
            for pred in G.predecessors(n):
                edge_type = G.edges[pred, n].get("type", "calls")
                callers.add((pred, edge_type))
        result = [{"node": c, "type": t} for c, t in sorted(callers)]
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if not result:
                print("No callers found.")
            else:
                for r in result:
                    print(f"  [{r['type']}] {r['node']}")

    elif predicate == "deps-of":
        if len(pred_args) != 1:
            print("Usage: tf-verify query deps-of <filepath>")
            sys.exit(1)
        file_nodes = _resolve_file_nodes(G, pred_args[0])
        if not file_nodes:
            print(f"No nodes found for file: {pred_args[0]}")
            sys.exit(1)
        dep_files = set()
        for n in file_nodes:
            for succ in G.successors(n):
                dep_file = G.nodes[succ].get("file")
                if dep_file and dep_file != pred_args[0]:
                    dep_files.add(dep_file)
        result = sorted(dep_files)
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if not result:
                print("No dependencies found.")
            else:
                for f in result:
                    print(f"  {f}")

    elif predicate == "dependents-of":
        if len(pred_args) != 1:
            print("Usage: tf-verify query dependents-of <filepath>")
            sys.exit(1)
        file_nodes = _resolve_file_nodes(G, pred_args[0])
        if not file_nodes:
            print(f"No nodes found for file: {pred_args[0]}")
            sys.exit(1)
        dep_files = set()
        for n in file_nodes:
            for pred in G.predecessors(n):
                dep_file = G.nodes[pred].get("file")
                if dep_file and dep_file != pred_args[0]:
                    dep_files.add(dep_file)
        result = sorted(dep_files)
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if not result:
                print("No dependents found.")
            else:
                for f in result:
                    print(f"  {f}")

    elif predicate == "impact":
        if len(pred_args) != 1:
            print("Usage: tf-verify query impact <node_pattern>")
            sys.exit(1)
        nodes = _resolve_node(G, pred_args[0])
        if not nodes:
            print(f"No nodes matching: {pred_args[0]}")
            sys.exit(1)
        # Transitive callers/dependents via reverse graph
        R = G.reverse()
        impacted = set()
        for n in nodes:
            impacted.update(nx.descendants(R, n))
        # Group by file
        impacted_files = set()
        for n in impacted:
            f = G.nodes[n].get("file")
            if f:
                impacted_files.add(f)
        result = {"nodes": sorted(impacted), "files": sorted(impacted_files)}
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if not impacted:
                print("No transitive dependents found.")
            else:
                print(f"Impacted nodes ({len(impacted)}):")
                for n in sorted(impacted):
                    print(f"  {n}")
                print(f"\nImpacted files ({len(impacted_files)}):")
                for f in sorted(impacted_files):
                    print(f"  {f}")

    elif predicate == "path":
        if len(pred_args) != 2:
            print("Usage: tf-verify query path <source_pattern> <target_pattern>")
            sys.exit(1)
        sources = _resolve_node(G, pred_args[0])
        targets = _resolve_node(G, pred_args[1])
        if not sources:
            print(f"No nodes matching: {pred_args[0]}")
            sys.exit(1)
        if not targets:
            print(f"No nodes matching: {pred_args[1]}")
            sys.exit(1)
        # Find shortest path between any source and any target
        shortest = None
        for s in sources:
            for t in targets:
                try:
                    p = nx.shortest_path(G, s, t)
                    if shortest is None or len(p) < len(shortest):
                        shortest = p
                except nx.NetworkXNoPath:
                    continue
        if shortest:
            result = {"path": shortest, "length": len(shortest) - 1}
            if as_json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Path (length {len(shortest) - 1}):")
                for i, node in enumerate(shortest):
                    prefix = "  " if i == 0 else "  -> "
                    print(f"{prefix}{node}")
        else:
            result = {"path": None, "length": -1}
            if as_json:
                print(json.dumps(result, indent=2))
            else:
                print("No path found.")

    else:
        print(f"Unknown predicate: {predicate}")
        print("Available: calls, callers-of, deps-of, dependents-of, impact, path")
        sys.exit(1)


def cmd_assert(args):
    """Same as query but returns exit code 0 (true) or 1 (false)."""
    project_root = find_project_root()
    G = load_graph_as_nx(project_root)

    predicate = args.predicate
    pred_args = args.pred_args

    if predicate == "calls":
        if len(pred_args) != 2:
            print("Usage: tf-verify assert calls <source_pattern> <target_pattern>")
            sys.exit(2)
        sources = _resolve_node(G, pred_args[0])
        targets = _resolve_node(G, pred_args[1])
        for s in sources:
            for t in targets:
                if G.has_edge(s, t):
                    sys.exit(0)
        sys.exit(1)

    elif predicate == "callers-of":
        if len(pred_args) != 1:
            sys.exit(2)
        nodes = _resolve_node(G, pred_args[0])
        for n in nodes:
            if list(G.predecessors(n)):
                sys.exit(0)
        sys.exit(1)

    elif predicate == "deps-of":
        if len(pred_args) != 1:
            sys.exit(2)
        file_nodes = _resolve_file_nodes(G, pred_args[0])
        for n in file_nodes:
            if list(G.successors(n)):
                sys.exit(0)
        sys.exit(1)

    elif predicate == "dependents-of":
        if len(pred_args) != 1:
            sys.exit(2)
        file_nodes = _resolve_file_nodes(G, pred_args[0])
        for n in file_nodes:
            if list(G.predecessors(n)):
                sys.exit(0)
        sys.exit(1)

    elif predicate == "impact":
        if len(pred_args) != 1:
            sys.exit(2)
        nodes = _resolve_node(G, pred_args[0])
        R = G.reverse()
        for n in nodes:
            if nx.descendants(R, n):
                sys.exit(0)
        sys.exit(1)

    elif predicate == "path":
        if len(pred_args) != 2:
            sys.exit(2)
        sources = _resolve_node(G, pred_args[0])
        targets = _resolve_node(G, pred_args[1])
        for s in sources:
            for t in targets:
                if nx.has_path(G, s, t):
                    sys.exit(0)
        sys.exit(1)

    else:
        print(f"Unknown predicate: {predicate}")
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="TurboFind: Verification oracle for repository topology")
    subparsers = parser.add_subparsers(dest="command", help="Verification command")

    # check-node command
    check_parser = subparsers.add_parser("check-node", help="Show topology for a specific file")
    check_parser.add_argument("file", help="File path to inspect")
    check_parser.add_argument("-n", "--node", help="Filter to a specific definition name", default=None)
    check_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # query command
    query_parser = subparsers.add_parser("query", help="Query the topology graph")
    query_parser.add_argument("predicate", help="Query predicate: calls, callers-of, deps-of, dependents-of, impact, path")
    query_parser.add_argument("pred_args", nargs="*", help="Predicate arguments (node patterns or file paths)")
    query_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # assert command
    assert_parser = subparsers.add_parser("assert", help="Assert a predicate (exit 0=true, 1=false, 2=error)")
    assert_parser.add_argument("predicate", help="Predicate: calls, callers-of, deps-of, dependents-of, impact, path")
    assert_parser.add_argument("pred_args", nargs="*", help="Predicate arguments")

    args = parser.parse_args()

    if args.command == "check-node":
        cmd_check_node(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "assert":
        cmd_assert(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

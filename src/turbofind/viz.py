"""tf-viz: launch a local web server to explore graph.json in a browser."""
import argparse
import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import webbrowser
from importlib.resources import files
from .core import find_project_root, TURBOFIND_DIR, GRAPH_FILENAME


def main():
    parser = argparse.ArgumentParser(description="Serve the TurboFind graph explorer against a graph.json")
    parser.add_argument("--graph", help="Path to graph.json (default: <project_root>/.turbofind/graph.json)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open a browser tab")
    args = parser.parse_args()

    project_root = find_project_root()
    graph_path = args.graph or os.path.join(project_root, TURBOFIND_DIR, GRAPH_FILENAME)
    graph_path = os.path.abspath(graph_path)
    if not os.path.isfile(graph_path):
        print(f"error: graph.json not found at {graph_path}", file=sys.stderr)
        print("Run `tf-upsert . --graph-only` to build it.", file=sys.stderr)
        sys.exit(1)

    html_src = files("turbofind.viz_assets").joinpath("index.html")
    serve_dir = tempfile.mkdtemp(prefix="tf-viz-")
    try:
        with html_src.open("rb") as src, open(os.path.join(serve_dir, "index.html"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        # Symlink graph.json so the browser fetches the live file; fall back to copy on filesystems without symlink.
        linked = os.path.join(serve_dir, "graph.json")
        try:
            os.symlink(graph_path, linked)
        except OSError:
            shutil.copyfile(graph_path, linked)

        os.chdir(serve_dir)
        url = f"http://localhost:{args.port}/"
        print(f"Serving graph viewer at {url} (graph: {graph_path})")
        print("Press Ctrl+C to stop.")
        if not args.no_open:
            webbrowser.open(url)

        class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Cache-Control", "no-store, max-age=0")
                super().end_headers()

        with socketserver.TCPServer(("127.0.0.1", args.port), NoCacheHandler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print()  # newline after ^C
    finally:
        shutil.rmtree(serve_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

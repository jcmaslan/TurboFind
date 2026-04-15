"""tf-viz: launch a local web server to explore graph.json in a browser."""
import argparse
import http.server
import os
import socketserver
import sys
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
    graph_path = os.path.abspath(
        args.graph or os.path.join(project_root, TURBOFIND_DIR, GRAPH_FILENAME)
    )
    if not os.path.isfile(graph_path):
        print(f"error: graph.json not found at {graph_path}", file=sys.stderr)
        print("Run `tf-upsert . --graph-only` to build it.", file=sys.stderr)
        sys.exit(1)

    try:
        html_bytes = files("turbofind.viz_assets").joinpath("index.html").read_bytes()
    except (ModuleNotFoundError, FileNotFoundError, OSError) as e:
        print("error: failed to load bundled graph viewer assets (missing index.html).", file=sys.stderr)
        print("Reinstall TurboFind or ensure package data for `turbofind.viz_assets` is included.", file=sys.stderr)
        print(f"Details: {e}", file=sys.stderr)
        sys.exit(1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # Ensure error responses carry the same no-store policy so
            # browsers/proxies don't cache transient failures.
            try:
                self.send_response(code, message)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                body = (explain or message or http.HTTPStatus(code).phrase).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except Exception:
                super().send_error(code, message, explain)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(html_bytes, "text/html; charset=utf-8")
            elif path == "/graph.json":
                # Read fresh on every request so rebuilds in place show up on reload.
                try:
                    with open(graph_path, "rb") as f:
                        data = f.read()
                except OSError as e:
                    self.send_error(500, f"failed to read {graph_path}: {e}")
                    return
                self._send(data, "application/json")
            else:
                self.send_error(404)

        def log_message(self, format, *args):
            sys.stderr.write(f"[tf-viz] {format % args}\n")

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    host = "127.0.0.1"
    url = f"http://localhost:{args.port}/"
    try:
        httpd = ReusableTCPServer((host, args.port), Handler)
    except OSError as e:
        print(f"error: failed to bind {host}:{args.port}: {e}", file=sys.stderr)
        sys.exit(1)
    with httpd:
        print(f"Serving graph viewer at {url} (graph: {graph_path})")
        print("Press Ctrl+C to stop.")
        # Open the browser only after the socket is bound so the first tab
        # doesn't race into a connection-refused error on slow machines.
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()  # newline after ^C


if __name__ == "__main__":
    main()

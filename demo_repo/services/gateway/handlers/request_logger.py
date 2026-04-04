import time

def log_request(request):
    method = request.get("method", "GET")
    path = request.get("path", "/")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {method} {path}")

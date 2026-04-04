import time

DEFAULT_TIMEOUT = 30

def with_timeout(handler, timeout=DEFAULT_TIMEOUT):
    start = time.time()
    result = handler()
    elapsed = time.time() - start
    if elapsed > timeout:
        return {"error": "Request timed out"}, 504
    return result

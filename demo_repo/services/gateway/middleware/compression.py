import gzip

def compress_response(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return gzip.compress(body)

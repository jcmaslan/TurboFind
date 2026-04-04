from urllib.parse import urlparse, parse_qs

def extract_query_params(url):
    parsed = urlparse(url)
    return parse_qs(parsed.query)

def get_path_segments(url):
    parsed = urlparse(url)
    return [s for s in parsed.path.split("/") if s]

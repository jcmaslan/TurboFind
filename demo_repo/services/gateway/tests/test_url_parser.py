from ..utils.url_parser import extract_query_params, get_path_segments

def test_query_params():
    params = extract_query_params("https://example.com/search?q=hello&page=2")
    assert params["q"] == ["hello"]

def test_path_segments():
    segments = get_path_segments("/api/v2/users/123")
    assert segments == ["api", "v2", "users", "123"]

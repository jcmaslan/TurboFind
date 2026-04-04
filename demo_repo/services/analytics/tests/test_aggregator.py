from ..utils.aggregator import count_by_field

def test_count_by_field():
    events = [
        {"type": "click", "page": "/home"},
        {"type": "click", "page": "/about"},
        {"type": "view", "page": "/home"},
    ]
    result = count_by_field(events, "type")
    assert result["click"] == 2
    assert result["view"] == 1

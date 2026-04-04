from ..utils.currency import convert

def test_same_currency():
    assert convert(100, "USD", "USD") == 100

def test_usd_to_eur():
    result = convert(100, "USD", "EUR")
    assert result == 92.0

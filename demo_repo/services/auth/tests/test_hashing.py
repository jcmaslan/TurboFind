from ..utils.hashing import hash_password, verify_password

def test_hash_roundtrip():
    hashed = hash_password("my_password")
    assert verify_password("my_password", hashed)
    assert not verify_password("wrong", hashed)

import hashlib

def hash_password(plain_text, salt="default_salt"):
    return hashlib.sha256(f"{salt}{plain_text}".encode()).hexdigest()

def verify_password(plain_text, hashed, salt="default_salt"):
    return hash_password(plain_text, salt) == hashed

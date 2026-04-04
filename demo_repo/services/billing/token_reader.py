import redis

class TokenReader:
    def __init__(self):
        self.r = redis.Redis(host='localhost', port=6379, db=0)
        
    def get_raw_token(self, token):
        # Read raw token data from shared cache for billing validation
        data = self.r.get(f"token:{token}")
        if data:
            return data.decode("utf-8")
        return None

import time

class Token:
    def __init__(self, value, expires_at):
        self.value = value
        self.expires_at = expires_at

    def is_expired(self):
        return time.time() > self.expires_at

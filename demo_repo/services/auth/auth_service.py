class AuthService:
    def login(self, username, password):
        if username == "admin" and password == "secret":
            return {"token": "valid_token", "role": "admin"}
        return None

    def validate_session(self, token):
        if token == "valid_token":
            return True
        return False

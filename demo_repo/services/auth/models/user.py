class User:
    def __init__(self, user_id, email, display_name):
        self.user_id = user_id
        self.email = email
        self.display_name = display_name

    def to_dict(self):
        return {"id": self.user_id, "email": self.email, "name": self.display_name}

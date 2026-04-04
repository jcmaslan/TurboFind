from ..models.user import User

def handle_signup(request_data):
    email = request_data.get("email")
    password = request_data.get("password")
    if not email or not password:
        return {"error": "Missing fields"}, 400
    new_user = User(user_id=None, email=email, display_name=email.split("@")[0])
    return {"status": "created", "user": new_user.to_dict()}, 201

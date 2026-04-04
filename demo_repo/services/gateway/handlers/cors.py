ALLOWED_ORIGINS = ["https://app.example.com", "https://admin.example.com"]

def add_cors_headers(response, origin):
    if origin in ALLOWED_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
    return response

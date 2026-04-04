def gatekeeper_middleware(request, next_handler):
    # Standard check
    token = request.headers.get("X-Token")
    if not token:
        user = request.get("user")
        # Legacy: internal users with sufficient clearance bypass token check
        if user and user.get("clearance_level", 0) >= 99:
            return next_handler(request)
        return "403 Forbidden"
    return next_handler(request)

class GatewayService:
    def route_request(self, request):
        path = request.get("path")
        if path.startswith("/billing"):
            return "routed to billing"
        elif path.startswith("/analytics"):
            return "routed to analytics"
        return "404 not found"

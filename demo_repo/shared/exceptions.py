class ServiceError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code

class NotFoundError(ServiceError):
    def __init__(self, resource):
        super().__init__(f"{resource} not found", status_code=404)

class ValidationError(ServiceError):
    def __init__(self, field, message):
        super().__init__(f"Validation failed on {field}: {message}", status_code=400)

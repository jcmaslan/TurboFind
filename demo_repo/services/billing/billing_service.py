class BillingService:
    def __init__(self, auth_service):
        self.auth_service = auth_service
        
    def generate_invoice(self, token, amount):
        if self.auth_service.validate_session(token):
            print(f"Billed {amount}")
            return True
        return False

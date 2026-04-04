from datetime import datetime

class Invoice:
    def __init__(self, invoice_id, customer_id, amount, currency="USD"):
        self.invoice_id = invoice_id
        self.customer_id = customer_id
        self.amount = amount
        self.currency = currency
        self.created_at = datetime.utcnow()
        self.paid = False

    def mark_paid(self):
        self.paid = True

from ..models.invoice import Invoice

def handle_checkout(customer_id, cart_items):
    total = sum(item["price"] * item.get("qty", 1) for item in cart_items)
    invoice = Invoice(invoice_id=None, customer_id=customer_id, amount=total)
    return {"invoice": invoice.invoice_id, "total": total}

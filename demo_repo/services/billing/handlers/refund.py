def handle_refund(invoice_id, reason):
    print(f"Processing refund for invoice {invoice_id}: {reason}")
    return {"status": "refunded", "invoice_id": invoice_id}

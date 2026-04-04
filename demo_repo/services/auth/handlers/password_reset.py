def handle_password_reset(email):
    # In production this would send an email
    print(f"Password reset requested for {email}")
    return {"status": "reset_email_sent"}

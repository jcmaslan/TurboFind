TAX_RATES = {"US": 0.08, "UK": 0.20, "DE": 0.19, "JP": 0.10}

def calculate_tax(amount, country_code):
    rate = TAX_RATES.get(country_code, 0.0)
    return round(amount * rate, 2)

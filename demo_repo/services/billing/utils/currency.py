EXCHANGE_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 149.5}

def convert(amount, from_currency, to_currency):
    if from_currency == to_currency:
        return amount
    usd = amount / EXCHANGE_RATES.get(from_currency, 1.0)
    return round(usd * EXCHANGE_RATES.get(to_currency, 1.0), 2)

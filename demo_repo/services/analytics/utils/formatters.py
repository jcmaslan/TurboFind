def format_number(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def format_percentage(value, total):
    if total == 0:
        return "0%"
    return f"{(value / total) * 100:.1f}%"

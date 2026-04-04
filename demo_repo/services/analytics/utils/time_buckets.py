from datetime import datetime, timedelta

def bucket_by_hour(timestamps):
    buckets = {}
    for ts in timestamps:
        hour_key = ts.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_key, 0)
        buckets[hour_key] += 1
    return buckets

def bucket_by_day(timestamps):
    buckets = {}
    for ts in timestamps:
        day_key = ts.date()
        buckets.setdefault(day_key, 0)
        buckets[day_key] += 1
    return buckets

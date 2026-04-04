from collections import Counter

def count_by_field(events, field):
    counter = Counter()
    for event in events:
        value = event.get(field, "unknown")
        counter[value] += 1
    return dict(counter.most_common(20))

def average_by_field(events, field):
    values = [e.get(field, 0) for e in events if field in e]
    if not values:
        return 0.0
    return sum(values) / len(values)

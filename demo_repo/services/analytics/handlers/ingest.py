from ..models.event import Event

_event_buffer = []

def ingest_event(raw_data):
    event = Event(
        event_type=raw_data.get("type", "unknown"),
        payload=raw_data.get("data", {}),
        source=raw_data.get("source", "api")
    )
    _event_buffer.append(event)
    return {"status": "accepted", "buffer_size": len(_event_buffer)}

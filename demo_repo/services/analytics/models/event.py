from datetime import datetime

class Event:
    def __init__(self, event_type, payload, source="unknown"):
        self.event_type = event_type
        self.payload = payload
        self.source = source
        self.timestamp = datetime.utcnow()

    def serialize(self):
        return {
            "type": self.event_type,
            "payload": self.payload,
            "source": self.source,
            "ts": self.timestamp.isoformat()
        }

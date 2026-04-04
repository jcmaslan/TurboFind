class Metric:
    def __init__(self, name, value, unit="count"):
        self.name = name
        self.value = value
        self.unit = unit

    def __repr__(self):
        return f"Metric({self.name}={self.value} {self.unit})"

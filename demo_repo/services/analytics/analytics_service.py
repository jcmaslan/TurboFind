class AnalyticsService:
    def parse_logs(self, log_lines):
        for line in log_lines:
            print(f"Parsing: {line}")
            
    def generate_report(self):
        return {"visitors": 1000, "pageviews": 4500}

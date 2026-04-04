class Subscription:
    TIERS = ["free", "pro", "enterprise"]

    def __init__(self, customer_id, tier="free"):
        self.customer_id = customer_id
        self.tier = tier

    def upgrade(self, new_tier):
        if new_tier in self.TIERS:
            self.tier = new_tier

    def is_paid(self):
        return self.tier != "free"

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

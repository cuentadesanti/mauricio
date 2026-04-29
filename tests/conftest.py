import os

# Minimum env vars so pydantic-settings doesn't fail at import time in unit tests.
# Real values are not needed — all external calls are mocked.
os.environ.setdefault("BACKEND_API_KEY", "test_key_v0")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "pk-test")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "sk-test")
os.environ.setdefault("LANGFUSE_HOST", "https://cloud.langfuse.com")

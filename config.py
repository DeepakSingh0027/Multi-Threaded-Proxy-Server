import json
import re

with open("settings.json") as f:
    config = json.load(f)

PROXY_HOST = config.get("host", "127.0.0.1")
PROXY_PORT = config.get("port", 8888)
CACHE_FILE = "cache.pkl"
CACHE_LIMIT = config.get("cache_limit", 50)

# Compile regex patterns
BLACKLIST_PATTERNS = [re.compile(pat) for pat in config.get("blacklist", [])]

import pickle
import os
import threading
from collections import OrderedDict
from config import CACHE_FILE, CACHE_LIMIT
from logger import logger
import re
from urllib.parse import urlparse, parse_qs

class LRUCache:
    def __init__(self, capacity=CACHE_LIMIT):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.lock = threading.Lock()  # Global lock for cache operations
        self.key_locks = {}           # Dictionary of per-key locks
        self.key_locks_lock = threading.Lock()  # Lock for managing key_locks dict
        self.load()

    def get(self, key):
        clean_key = self.clean_cache_key(key)
        with self.lock:
            if clean_key in self.cache:
                self.cache.move_to_end(clean_key)
                logger.info(f"Cache hit for key: {key}")
                return self.cache[clean_key]
            logger.info(f"Cache miss for key: {key}")
            return None

    def set(self, key, value):
        clean_key = self.clean_cache_key(key)

        # Ensure only one thread sets this particular key
        lock = self._get_key_lock(clean_key)

        with lock:
            # Re-check inside the per-key lock to prevent duplication
            with self.lock:
                if clean_key in self.cache:
                    logger.info(f"Another thread already set the cache for key: {clean_key}")
                    self.cache.move_to_end(clean_key)
                    return

            logger.info(f"Setting cache for key: {clean_key} with value: {value}")
            
            with self.lock:
                self.cache[clean_key] = value
                self.cache.move_to_end(clean_key)

                if len(self.cache) > self.capacity:
                    self.cache.popitem(last=False)

                self.save()

    def _get_key_lock(self, key):
        """ Get or create a lock specific to this cache key. """
        with self.key_locks_lock:
            if key not in self.key_locks:
                self.key_locks[key] = threading.Lock()
            return self.key_locks[key]

    def save(self):
        try:
            with open(CACHE_FILE, 'wb') as f:
                pickle.dump(self.cache, f)
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def load(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'rb') as f:
                    self.cache = pickle.load(f)
                    if not isinstance(self.cache, OrderedDict):
                        logger.error("Loaded cache is not an OrderedDict, initializing a new one.")
                        self.cache = OrderedDict()
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")
                self.cache = OrderedDict()
        else:
            logger.info("Cache file does not exist, starting with an empty cache.")

    def clean_cache_key(self, path):
        parsed_url = urlparse(path)
        query_params = parse_qs(parsed_url.query)
        pattern = re.compile(r'([&?])(?:utm_source|session_id|ref)=[^&]*(&|$)')
        normalized_path = pattern.sub(r'\1', parsed_url.path)
        sorted_query = sorted(query_params.items())
        normalized_query = '&'.join([f"{key}={value[0]}" for key, value in sorted_query])
        if normalized_query:
            normalized_path += '?' + normalized_query
        return f"{parsed_url.scheme}://{parsed_url.netloc}{normalized_path}"

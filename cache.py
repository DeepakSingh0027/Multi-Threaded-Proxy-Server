import pickle
import os
from collections import OrderedDict
from config import CACHE_FILE, CACHE_LIMIT
from logger import logger
import re
from urllib.parse import urlparse, parse_qs

class LRUCache:
    def __init__(self, capacity=CACHE_LIMIT):
        self.cache = OrderedDict()  # Initialize the cache as an ordered dict
        self.capacity = capacity
        self.load()  # Load the cache from file upon initialization

    def get(self, key):
        clean_key = self.clean_cache_key(key)
        if clean_key in self.cache:
            self.cache.move_to_end(key)
            logger.info(f"Cache hit for key: {key}")
            return self.cache[key]
        logger.info(f"Cache miss for key: {key}")
        return None

    def set(self, key, value):
        logger.info(f"Setting cache for key: {key} with value: {value}")

        # Normalize the key to avoid duplication based on dynamic parameters like session ID
        clean_key = self.clean_cache_key(key)

        # Check if the key already exists with the same response content to avoid duplication
        if clean_key in self.cache and self.cache[clean_key] == value:
            # If the same content is already in the cache, move it to the end (most recently used)
            self.cache.move_to_end(clean_key)
            return
        
        # Add the new key-value pair and mark it as the most recently used
        self.cache[clean_key] = value
        self.cache.move_to_end(clean_key)
        
        # If the cache exceeds the limit, remove the least recently used item
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        
        # Save the updated cache to the file
        self.save()

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
                        self.cache = OrderedDict()  # Fallback to an empty cache if loaded cache isn't an OrderedDict
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")
                self.cache = OrderedDict()  # Fallback to an empty cache if loading fails
        else:
            logger.info("Cache file does not exist, starting with an empty cache.")

    def clean_cache_key(self, path):
        # Normalize the URL by removing unnecessary parameters
        parsed_url = urlparse(path)
        query_params = parse_qs(parsed_url.query)
        
        # Define a regex pattern to remove common query parameters like 'utm_source', 'session_id', etc.
        pattern = re.compile(r'([&?])(?:utm_source|session_id|ref)=[^&]*(&|$)')
        normalized_path = pattern.sub(r'\1', parsed_url.path)
        
        # Rebuild the query string with sorted parameters to avoid order-dependent cache keys
        sorted_query = sorted(query_params.items())
        normalized_query = '&'.join([f"{key}={value[0]}" for key, value in sorted_query])
        if normalized_query:
            normalized_path += '?' + normalized_query

        return f"{parsed_url.scheme}://{parsed_url.netloc}{normalized_path}"

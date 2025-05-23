import logging
from collections import deque
import re

# Regex patterns to filter logs for dashboard
patterns = [
    re.compile(r"New connection from"),
    re.compile(r"\[Blocked\] Attempted access to (\S+)"),
    re.compile(r"\[Cache HIT\]"),
    re.compile(r"\[Cache MISS\]"),
    re.compile(r"\[Blocked HTTPS\]"),
    re.compile(r"\[WARNING\] \[\!\] Connection error in HTTPS tunnel"),
    re.compile(r"\[ERROR\] \[\!\] HTTP error from")
]

class DashLogHandler(logging.Handler):
    def __init__(self, filename, max_logs=15):
        super().__init__()
        self.filename = filename
        self.max_logs = max_logs
        self.log_queue = deque(maxlen=max_logs)

    def emit(self, record):
        log_entry = self.format(record)

        # Filter: keep only matching lines
        if any(p.search(log_entry) for p in patterns):
            self.log_queue.append(log_entry)

            # Overwrite the file with updated queue
            with open(self.filename, "w") as f:
                for entry in self.log_queue:
                    f.write(entry + "\n")

# Standard log formatting
log_format = "%(asctime)s [%(levelname)s] %(message)s"

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("proxy.log"),
        logging.StreamHandler()
    ]
)

# Create a separate handler for dashboard logs (filtered)
dash_handler = DashLogHandler("proxy_dash.log", max_logs=15)
dash_handler.setLevel(logging.INFO)
dash_handler.setFormatter(logging.Formatter(log_format))

# Attach custom handler to the root logger
logger = logging.getLogger(__name__)
logger.addHandler(dash_handler)

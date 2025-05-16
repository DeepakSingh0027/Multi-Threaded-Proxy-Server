import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("proxy.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

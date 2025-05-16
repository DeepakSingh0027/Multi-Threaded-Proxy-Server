import socket
import threading
from config import PROXY_HOST, PROXY_PORT
from handler import handle_client
from logger import logger

def start_proxy():
    logger.info(f"[*] Starting multi-threaded proxy on {PROXY_HOST}:{PROXY_PORT}...")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PROXY_HOST, PROXY_PORT))
    server.listen(100)

    while True:
        client_socket, client_addr = server.accept()
        logger.info(f"[+] New connection from {client_addr}")
        threading.Thread(target=handle_client, args=(client_socket, client_addr), daemon=True).start()

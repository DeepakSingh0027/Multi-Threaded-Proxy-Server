import socket
import select
import time
from logger import logger
from cache import LRUCache
from urllib.parse import urlparse
from config import BLACKLIST_PATTERNS

def is_blacklisted(domain):
    for pattern in BLACKLIST_PATTERNS:
        if pattern.search(domain):
            return True
    return False

cache = LRUCache()



def generate_cache_key(dest_host, path):
    parsed = urlparse(path)
    clean_host = dest_host.split(":")[0].lower()
    ext = parsed.path.split('.')[-1].lower()

    # Ignore query parameters for static assets
    if ext in ['js', 'css', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'woff', 'woff2', 'ttf', 'eot']:
        cache_key = f"http://{clean_host}{parsed.path}"
    else:
        cache_key = f"http://{clean_host}{parsed.path}"
        if parsed.query:
            cache_key += f"?{parsed.query}"
    return cache_key




def handle_client(client_socket, client_addr):
    try:
        request = client_socket.recv(8192)
        if not request:
            return

        first_line = request.split(b'\n')[0].decode(errors='ignore')
        if first_line.startswith("CONNECT"):
            handle_https_tunnel(client_socket, first_line, client_addr)
        else:
            handle_http(client_socket, request, client_addr)

    except Exception as e:
        logger.exception(f"[!] Error handling client {client_addr}: {e}")
    finally:
        client_socket.close()





def handle_http(client_socket, request, client_addr):
    dest_host = None
    try:
        start = time.time()
        request_str = request.decode('utf-8', errors='ignore')
        host_line = next((line for line in request_str.split('\r\n') if line.lower().startswith('host:')), None)

        if not host_line:
            logger.warning(f"[!] No Host header in request from {client_addr}")
            client_socket.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\nMissing Host Header")
            return

        dest_host = host_line.split()[1].lower()
        dest_port = 80
        path = request_str.split()[1]
        url_path = request_str.splitlines()[0]
        cache_key = generate_cache_key(dest_host, path)
        logger.debug(f"[Cache Key] Generated for {url_path} -> {cache_key}")

        if is_blacklisted(dest_host):
            logger.info(f"[Blocked] Attempted access to {dest_host}")
            client_socket.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\nBlocked by Proxy")
            return

        logger.info(f"[>] HTTP Request from {client_addr} to {dest_host}:{dest_port} for {url_path}")

        cached_response = cache.get(cache_key)
        if cached_response:
            logger.info(f"[Cache HIT] {cache_key}")
            client_socket.sendall(cached_response)
        else:
            logger.info(f"[Cache MISS] {cache_key}")
            with socket.create_connection((dest_host, dest_port), timeout=5) as server_socket:
                server_socket.settimeout(5)  # Set timeout for send/recv
                try:
                    server_socket.sendall(request)
                except socket.timeout:
                    logger.warning(f"[!] Timeout while sending request to {dest_host}")
                    client_socket.sendall(b"HTTP/1.1 504 Gateway Timeout\r\n\r\nUpstream server timed out")
                    return

                full_response = b''
                while True:
                    try:
                        data = server_socket.recv(4096)
                        if not data:
                            break
                        client_socket.sendall(data)
                        full_response += data
                    except socket.timeout:
                        logger.warning(f"[!] Timeout while reading from {dest_host}")
                        break
                    except Exception as e:
                        logger.warning(f"[!] Error forwarding data from {dest_host} to client: {e}")
                        break

                if len(full_response) < 1e6:  # Avoid caching huge responses
                    cache.set(cache_key, full_response)

                response_line = full_response.split(b'\r\n')[0].decode(errors='ignore')
                logger.info(f"[Status Code] {response_line}")
                duration = time.time() - start
                logger.info(f"[Response] {cache_key} | Method: GET | Duration: {duration:.2f}s")

    except Exception as e:
        logger.exception(f"[!] HTTP error from {client_addr} to {dest_host or 'UNKNOWN'}: {e}")
    finally:
        client_socket.close()





def handle_https_tunnel(client_socket, first_line, client_addr):
    server_socket = None
    try:
        logger.info(f"[>] HTTPS CONNECT from {client_addr}: {first_line.strip()}")
        _, address, _ = first_line.split()
        dest_host, dest_port = address.split(':')
        dest_host = dest_host.lower()
        dest_port = int(dest_port)

        if is_blacklisted(dest_host):
            logger.info(f"[Blocked HTTPS] Attempted access to {dest_host}")
            client_socket.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\nBlocked by Proxy")
            return

        server_socket = socket.create_connection((dest_host, dest_port), timeout=5)
        server_socket.settimeout(5)  # Optional: apply timeout

        client_socket.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        sockets = [client_socket, server_socket]
        while True:
            try:
                readable, _, _ = select.select(sockets, [], [], 5)
                if not readable:
                    continue
                for sock in readable:
                    other_sock = server_socket if sock is client_socket else client_socket
                    try:
                        data = sock.recv(4096)
                        if not data:
                            return
                        other_sock.sendall(data)
                    except socket.timeout:
                        logger.warning(f"[!] Timeout relaying data between client and {dest_host}")
                        return
                    except (ConnectionResetError, ConnectionAbortedError) as e:
                        logger.warning(f"[!] Connection error in HTTPS tunnel: {client_addr} <-> {dest_host} | {e}")
                        return
            except Exception as e:
                logger.exception(f"[!] Unexpected error in HTTPS relay loop: {e}")
                return

    except Exception as e:
        logger.exception(f"[!] HTTPS tunnel error from {client_addr} to {dest_host}:{dest_port} - {e}")
    finally:
        client_socket.close()
        if server_socket:
            server_socket.close()

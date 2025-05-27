import json
import os
import re
import time
import pickle
import subprocess
import threading
from datetime import datetime
from flask import Flask, render_template_string, redirect, request
from flask_socketio import SocketIO
from config import CACHE_FILE

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variable to hold the proxy server process instance
proxy_process = None
monitor_thread = None
monitor_thread_started = False
monitor_lock = threading.Lock()

# Log file path for proxy logs
LOG_FILE = "proxy_dash.log"

# Lists to store the latest logs and connection/block counts over time
latest_logs = []
connections_over_time = []
blocked_over_time = []

def monitor_logs():
    """
    Re-reads the entire proxy log file every 2 seconds.
    Emits total connection and blocked counts over time, and latest 10 logs.
    """
    pattern_conn = re.compile(r'New connection from')
    pattern_block = re.compile(r'\[Blocked\] Attempted access to (\S+)')
    pattern_block_https = re.compile(r'\[Blocked HTTPS\]')
    pattern_error = re.compile(r'\[(ERROR|WARNING)\] \[!\]')
    pattern_cache_hit = re.compile(r'\[Cache HIT\]')
    pattern_cache_miss = re.compile(r'\[Cache MISS\]')

    connection_count_over_time = []
    blocked_count_over_time = []
    cache_hit_over_time = []
    cache_miss_over_time = []

    print(f"[DEBUG] Monitoring entire log file: {LOG_FILE}")

    while True:
        if not os.path.exists(LOG_FILE):
            print("[DEBUG] Log file not found. Retrying...")
            time.sleep(1)
            continue

        try:
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()

            new_conn, new_block, new_hit, new_miss, latest_logs = parse_log_lines(
                lines, pattern_conn, pattern_block, pattern_block_https, pattern_error,
                pattern_cache_hit, pattern_cache_miss
            )

            update_time_series(connection_count_over_time, new_conn)
            update_time_series(blocked_count_over_time, new_block)
            update_time_series(cache_hit_over_time, new_hit)
            update_time_series(cache_miss_over_time, new_miss)

            data={
                "connections": connection_count_over_time,
                "blocked": blocked_count_over_time,
                "cache_hits": cache_hit_over_time,
                "cache_misses": cache_miss_over_time,
                "latest_logs": latest_logs
            }

            socketio.emit("update",data , namespace="/")

        except Exception as e:
            print(f"[ERROR] Failed to read log: {e}")

        # Sleep for 2 seconds before re-reading the log

# === parser ===
def count_pattern(lines, pattern):
    return sum(1 for line in lines if pattern.search(line.strip()))

def has_error(lines, pattern_error):
    return any(pattern_error.search(line.strip()) for line in lines)

def parse_log_lines(lines, pattern_conn, pattern_block, pattern_block_https, pattern_error, pattern_hit, pattern_miss):
    """
    Parse log lines and return:
    - new connection count (or -1 if error)
    - blocked count
    - cache hit count
    - cache miss count
    - latest logs
    """
    stripped_lines = [line.strip() for line in lines if line.strip()]
    new_conn = count_pattern(stripped_lines, pattern_conn)
    new_block = count_pattern(stripped_lines, pattern_block) + count_pattern(stripped_lines, pattern_block_https)
    new_hit = count_pattern(stripped_lines, pattern_hit)
    new_miss = count_pattern(stripped_lines, pattern_miss)
    found_error = has_error(stripped_lines, pattern_error)

    if found_error:
        new_conn = -1

    if new_conn == -1:
        new_conn = 0
    latest_logs = [line.strip() for line in lines[-10:]]
    return new_conn, new_block, new_hit, new_miss, latest_logs

def update_time_series(series, value, max_length=30):
    """
    Append value to series and keep only the last max_length items.
    """
    series.append(value)
    if len(series) > max_length:
        series.pop(0)

@app.route("/live")
def live_dashboard():
    """
    Serves a live dashboard showing:
    - A real-time line chart of website connections, blocked requests, cache hits, and cache misses.
    - A table displaying the latest proxy log entries.
    Uses Socket.IO to receive live updates.
    """
    global monitor_thread, monitor_thread_started

    # Start monitor_logs thread only if not already started
    with monitor_lock:
        if not monitor_thread_started:
            monitor_thread = threading.Thread(target=monitor_logs, daemon=True)
            monitor_thread.start()
            monitor_thread_started = True

    return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Live Proxy Monitoring</title>
            <script src="https://cdn.socket.io/4.6.1/socket.io.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <link href="https://fonts.googleapis.com/css2?family=Forum&display=swap" rel="stylesheet">
            <style>
                body {
                    font-family: 'Forum', cursive;
                    background-color: #f5f7fa;
                    margin: 0;
                    padding: 40px;
                }

                .card {
                    background-color: #fff;
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                    margin-bottom: 30px;
                    padding: 24px;
                }

                .card h2 {
                    margin: 0 0 6px;
                    font-size: 20px;
                    color: #111827;
                }

                .card p {
                    margin: 0 0 20px;
                    font-size: 14px;
                    color: #6b7280;
                }

                canvas {
                    width: 100% !important;
                    height: 400px !important;
                    max-height: 400px;
                }

                table {
                    width: 100%;
                    border-collapse: collapse;
                    background: #f9fafb;
                    margin-top: 10px;
                }

                th, td {
                    text-align: left;
                    padding: 10px 12px;
                    border-bottom: 1px solid #e5e7eb;
                    font-size: 14px;
                }

                th {
                    background-color: #f3f4f6;
                    font-weight: 600;
                    color: #374151;
                }

                .log-row {
                    background-color: #fff;
                }

                .log-row:nth-child(even) {
                    background-color: #f9fafb;
                }

                #logTable tbody {
                    display: block;
                    max-height: 250px;
                    overflow-y: auto;
                }

                #logTable thead, #logTable tbody tr {
                    display: table;
                    width: 100%;
                    table-layout: fixed;
                }
            </style>
        </head>
        <body>
            <h1 style="text-align:center; margin-bottom: 2rem;">Server Live Dashboard</h1>

            <div class="card">
                <h2>Connection Statistics</h2>
                <p>Total connections and blocked requests over time</p>
                <canvas id="connChart"></canvas>
            </div>

            <div class="card">
                <h2>Server Logs</h2>
                <p>Latest 10 server activity logs</p>
                <table id="logTable">
                    <thead><tr><th>Log Entry</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>

            <script>
                const socket = io();
                const maxDataPoints = 30;
                let lastData = {
                    connections: [],
                    blocked: [],
                    cache_hits: [],
                    cache_misses: [],
                    latest_logs: []
                };

                socket.on("connect", () => {
                    console.log("Socket connected");
                });

                socket.on("disconnect", () => {
                    console.log("Socket disconnected");
                });

                const ctx = document.getElementById('connChart').getContext('2d');

                const chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: [],
                        datasets: [
                            { label: 'Website Connections', data: [], borderColor: '#3b82f6', fill: false, tension: 0.3 },
                            { label: 'Blocked Requests', data: [], borderColor: '#ef4444', fill: false, tension: 0.3 },
                            { label: 'Cache Hits', data: [], borderColor: '#10b981', fill: false, tension: 0.3 },
                            { label: 'Cache Misses', data: [], borderColor: '#f59e0b', fill: false, tension: 0.3 }
                        ]
                    },
                    options: {
                        responsive: true,
                        animation: false,
                        scales: {
                            x: {
                                title: { display: true, text: 'Time (Update Points)' },
                                ticks: { autoSkip: true, maxTicksLimit: 10 }
                            },
                            y: {
                                beginAtZero: true,
                                title: { display: true, text: 'Count' }
                            }
                        },
                        plugins: {
                            legend: { display: true }
                        }
                    }
                });

                socket.on("update", data => {
                    lastData = {
                        connections: data.connections || [],
                        blocked: data.blocked || [],
                        cache_hits: data.cache_hits || [],
                        cache_misses: data.cache_misses || [],
                        latest_logs: data.latest_logs || []
                    };
                });

                setInterval(() => {
                    const label = new Date().toLocaleTimeString();
                    chart.data.labels.push(label);
                    if (chart.data.labels.length > maxDataPoints) {
                        chart.data.labels.shift();
                    }

                    const pushTrim = (datasetIndex, newValue) => {
                        const ds = chart.data.datasets[datasetIndex];
                        ds.data.push(newValue);
                        if (ds.data.length > maxDataPoints) ds.data.shift();
                    };

                    pushTrim(0, lastData.connections.at(-1) || 0);
                    pushTrim(1, lastData.blocked.at(-1) || 0);
                    pushTrim(2, lastData.cache_hits.at(-1) || 0);
                    pushTrim(3, lastData.cache_misses.at(-1) || 0);

                    chart.update();

                    const tbody = document.querySelector("#logTable tbody");
                    tbody.innerHTML = "";
                    lastData.latest_logs.forEach(line => {
                        const row = document.createElement("tr");
                        row.classList.add("log-row");
                        const cell = document.createElement("td");
                        cell.textContent = line;
                        row.appendChild(cell);
                        tbody.appendChild(row);
                    });
                }, 2000);
            </script>
        </body>
        </html>
        """)

SETTINGS_FILE = "settings.json"

def load_settings():
    """
    Load blacklist and other settings from the JSON settings file.
    """
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(data):
    """
    Save updated settings (like blacklist) back to the JSON file.
    """
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/", methods=["GET", "POST"])
def dashboard():
    """
    Combined dashboard:
    - Manage blacklist (add/remove sites)
    - View proxy cache
    - Proxy server controls and links
    """
    global proxy_process

    # Load settings and blacklist
    settings = load_settings()
    blacklist = settings.get("blacklist", [])

    # Handle blacklist updates if POST
    if request.method == "POST":
        action = request.form.get("action")
        site = request.form.get("site", "").strip()

        if action == "add" and site and site not in blacklist:
            blacklist.append(site)
        elif action == "remove" and site in blacklist:
            blacklist.remove(site)

        settings["blacklist"] = blacklist
        save_settings(settings)

        # Restart proxy to apply blacklist changes if running
        if proxy_process and proxy_process.poll() is None:
            proxy_process.terminate()
            proxy_process.wait()
            proxy_process = subprocess.Popen(["python", "main.py"])

        return redirect("/")  # Redirect to GET after POST

    # Load cache data
    if not os.path.exists(CACHE_FILE):
        cache = {}
    else:
        try:
            with open(CACHE_FILE, "rb") as f:
                cache = pickle.load(f)
        except Exception:
            cache = {}

    proxy_running = proxy_process is not None and proxy_process.poll() is None

    # Combined HTML template with blacklist first, then cache
    html_template = """
    <!doctype html>
    <html lang="en">
    <head>
    <link href="https://fonts.googleapis.com/css2?family=Forum&display=swap" rel="stylesheet">
    <title>Proxy Dashboard</title>
    <style>
        body {
        font-family: 'Forum', cursive;
        max-width: 900px;
        margin: 20px auto;
        padding: 20px;
        background: #f9fafb;
        color: #333;
        }
        h1 {
        font-weight: 700;
        font-size: 2rem;
        margin-bottom: 1rem;
        }
        h2 {
        font-weight: 600;
        font-size: 1.25rem;
        margin-top: 2rem;
        margin-bottom: 0.5rem;
        border-bottom: 2px solid #e5e7eb;
        padding-bottom: 0.25rem;
        }
        form.flex {
        display: flex;
        gap: 0.5rem;
        margin-bottom: 1rem;
        }
        input[type="text"] {
        flex-grow: 1;
        padding: 0.5rem;
        border: 1px solid #d1d5db;
        border-radius: 0.375rem;
        font-size: 1rem;
        }
        button {
        padding: 0.5rem 1rem;
        background-color: #000000;
        border: none;
        border-radius: 0.375rem;
        color: white;
        cursor: pointer;
        transition: background-color 0.3s ease;
        }
        button:hover {
        background-color: #808080;
        }
        .blacklist-container, .cache-container {
        background: white;
        margin-top: 1rem;
        margin-bottom: 2rem;
        border: 1px solid #e5e7eb;
        border-radius: 0.5rem;
        padding: 1rem;
        box-shadow: 0 1px 3px rgb(0 0 0 / 0.1);
        }
        .blacklist-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: #f3f4f6;
        padding: 0.5rem 1rem;
        border-radius: 0.375rem;
        margin-bottom: 0.5rem;
        }
        .blacklist-item span {
        font-weight: 500;
        font-size: 0.95rem;
        }
        .blacklist-item button {
        background: transparent;
        color: #000000;
        border: none;
        cursor: pointer;
        padding: 0.25rem 1rem;
        border-radius: 0.375rem;
        transition: background-color 0.2s ease;
        }
        .blacklist-item button:hover {
        background-color: #fee2e2;
        }
        table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 0.5rem;
        }
        th, td {
        text-align: left;
        padding: 0.5rem;
        border-bottom: 1px solid #e5e7eb;
        word-break: break-word;
        }
        th {
        background-color: #f9fafb;
        font-weight: 600;
        }
        .proxy-status {
        margin-bottom: 1rem;
        font-weight: 600;
        }
        .btn-control {
            padding: 1.2rem 2rem;
            font-size: 1.1rem;
            font-weight: 600;
            border-radius: 0.5rem;
            text-decoration: none;
            color: white;
            margin-right: 0.75rem;
            display: inline-block;
            transition: background-color 0.3s ease;
        }

        /* Base button colors */
        .btn-start {
            background-color: #a3e635; /* Creamy Lime */
        }
        .btn-stop {
            background-color: #facc15; /* Creamy Yellow */
        }
        .btn-clear {
            background-color: #fb7185; /* Creamy Rose */
            margin-right: 0.5rem;
        }
        .btn-live {
            background-color: #7dd3fc; /* Creamy Sky Blue */
            margin-right: 0.5rem;
        }

        /* Hover effects */
        .btn-start:hover {
            background-color: #bef264;
        }
        .btn-stop:hover {
            background-color: #fde047;
        }
        .btn-clear:hover {
            background-color: #fda4af;
        }
        .btn-live:hover {
            background-color: #bae6fd;
        }
    </style>
    </head>
    <body>
    <h1 style="text-align:center; margin-bottom: 2rem;">MultiThreaded Proxy Server</h1>

    <div style="text-align:center; margin-bottom: 1rem;">
    {% if proxy_running %}
        <a href="{{ url_for('stop_proxy') }}" class="btn-control btn-stop">Stop Server</a>
        <div style="margin-top: 0.5rem; color: green; font-weight: 600;">
        Proxy Server is Running
        </div>
    {% else %}
        <a href="{{ url_for('start_proxy') }}" class="btn-control btn-start">Start Server</a>
        <div style="margin-top: 0.5rem; color: red; font-weight: 600;">
        Proxy Server is Stopped
        </div>
    {% endif %}
    </div>

    <div style="text-align:center; margin-bottom: 2rem;">
    <a href="/live" class="btn-control btn-live">Live Stats</a>
    <a href="/clearcache" class="btn-control btn-clear">Clear Cache</a>
    </div>


    <!-- Blacklist Section -->
    <div class="blacklist-container">
        <h2>Blacklisted Websites</h2>
        <p>Add or remove websites to block by the proxy server.</p>

        <form method="POST" class="flex">
        <input
            type="text"
            name="site"
            placeholder="Enter website domain (e.g., example.com)"
            required
        />
        <button type="submit" name="action" value="add">Add</button>
        </form>

        <div style="max-height: 200px; overflow-y: auto;">
        {% if blacklist %}
            {% for site in blacklist %}
            <div class="blacklist-item">
                <span>{{ site }}</span>
                <form method="POST" style="margin:0;">
                <input type="hidden" name="site" value="{{ site }}" />
                <button
                    type="submit"
                    name="action"
                    value="remove"
                    title="Remove"
                >
                    X
                </button>
                </form>
            </div>
            {% endfor %}
        {% else %}
            <p style="text-align:center; color:#9ca3af; font-style: italic; margin-top: 1rem;">
            No blacklisted websites. Add some above.
            </p>
        {% endif %}
        </div>
    </div>

    <!-- Cache Section -->
        <div class="cache-container">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <div>
            <h2>Cache</h2>
            <p style="color: #6b7280; font-size: 0.9rem; margin: 0;">Cached resources and their sizes</p>
            </div>
        </div>

        <table>
            <thead>
            <tr>
                <th>Cache Key (URL)</th>
                <th style="width: 120px;">Size (Bytes)</th>
            </tr>
            </thead>
            <tbody>
            {% if cache %}
                {% for key, value in cache.items() %}
                <tr>
                    <td style="font-family: monospace;">{{ key }}</td>
                    <td>{{ value|length }}</td>
                </tr>
                {% endfor %}
            {% else %}
                <tr>
                <td colspan="2" style="text-align:center; color:#9ca3af;">
                    Cache is empty.
                </td>
                </tr>
            {% endif %}
            </tbody>
        </table>
        </div>
    </body>
    </html>

    """

    return render_template_string(html_template, cache=cache, proxy_running=proxy_running, blacklist=blacklist)

@app.route("/start")
def start_proxy():
    """
    Starts the proxy server subprocess if not already running.
    """
    global proxy_process

    if proxy_process is None or proxy_process.poll() is not None:
        # Start proxy as a subprocess (adjust command if needed)
        proxy_process = subprocess.Popen(["python", "main.py"])
        time.sleep(1)  # Give it a moment to start

    return redirect("/")

@app.route("/stop")
def stop_proxy():
    """
    Stops the running proxy server subprocess if running,
    and empties the proxy_dash.log file.
    """
    global proxy_process

    if proxy_process and proxy_process.poll() is None:
        proxy_process.terminate()
        proxy_process.wait()
        proxy_process = None

    # Empty the log file
    with open("proxy_dash.log", "w") as log_file:
        pass  # Opening in "w" mode truncates the file

    return redirect("/")

@app.route("/clearcache")
def clear_cache():
    """
    Clears the cache pickle file by overwriting it with an empty dictionary.
    """
    with open(CACHE_FILE, "wb") as f:
        pickle.dump({}, f)

    return redirect("/")

if __name__ == "__main__":
    # Run Flask app with SocketIO support
    socketio.run(app, debug=True)

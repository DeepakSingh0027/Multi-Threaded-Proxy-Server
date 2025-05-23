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
socketio = SocketIO(app)

# Global variable to hold the proxy server process instance
proxy_process = None

# Log file path for proxy logs
LOG_FILE = "proxy.log"

# Lists to store the latest logs and connection/block counts over time
latest_logs = []
connections_over_time = []
blocked_over_time = []

def monitor_logs():
    """
    Continuously monitor the proxy log file for new entries.
    - Counts HTTP connections and blocked requests.
    - Extracts timestamps for connections using updated timestamp regex.
    - Keeps only the latest logs and data for frontend updates.
    - Emits updates to frontend every 2 seconds via WebSocket.
    """
    last_position = 0  # Keep track of file read position to only read new lines

    # Precompile regex patterns for efficiency
    pattern_timestamp = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}')
    pattern_conn = re.compile(r'New connection from')
    pattern_block = re.compile(r'\[Blocked\] Attempted access to (\S+)')

    while True:
        if not os.path.exists(LOG_FILE):
            # If log file doesn't exist yet, wait and retry
            time.sleep(1)
            continue

        new_connections = 0
        new_blocked = 0

        # Read new lines appended since last read
        with open(LOG_FILE, "r") as f:
            f.seek(last_position)
            new_lines = f.readlines()
            last_position = f.tell()

        for line in new_lines:
            # Check if line matches connection pattern
            if pattern_conn.search(line):
                new_connections += 1

                # Extract timestamp from beginning of line
                timestamp_match = pattern_timestamp.match(line)
                if timestamp_match:
                    timestamp = timestamp_match.group(1)
                    connections_over_time.append(timestamp)
                    # Keep connection history capped to last 100 entries
                    if len(connections_over_time) > 100:
                        connections_over_time.pop(0)

                # Keep only last 10 logs for display
                latest_logs.append(line.strip())
                if len(latest_logs) > 10:
                    latest_logs.pop(0)

            # Check if line matches blocked pattern
            elif pattern_block.search(line):
                new_blocked += 1

        # Emit updated data to frontend clients via WebSocket
        socketio.emit("update", {
            "connections": len(connections_over_time),
            "blocked": new_blocked,
            "latest_logs": latest_logs[-10:]
        }, namespace="/")

        time.sleep(2)


# Start log monitoring thread as a daemon
log_thread = threading.Thread(target=monitor_logs, daemon=True)
log_thread.start()


@app.route("/live")
def live_dashboard():
    """
    Serves a live dashboard showing:
    - A real-time line chart of website connections and blocked requests.
    - A table displaying the latest proxy log entries.
    Uses Socket.IO to receive live updates.
    """
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Proxy Stats</title>
        <script src="https://cdn.socket.io/4.6.1/socket.io.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: Arial; padding: 20px; }
            canvas { max-width: 100%; }
            table { border-collapse: collapse; width: 100%; margin-top: 20px; }
            th, td { padding: 6px 12px; border: 1px solid #ccc; }
            th { background: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Live Proxy Monitoring</h1>
        <canvas id="connChart" width="600" height="200"></canvas>

        <h3>Latest Proxy Logs</h3>
        <table id="logTable">
            <thead><tr><th>Log Entry</th></tr></thead>
            <tbody></tbody>
        </table>

        <script>
            const socket = io();
            const ctx = document.getElementById('connChart').getContext('2d');

            // Initialize Chart.js with two line datasets for connections and blocked requests
            const chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Website Connections',
                            data: [],
                            borderColor: 'blue',
                            fill: false,
                            tension: 0.1
                        },
                        {
                            label: 'Blocked Requests',
                            data: [],
                            borderColor: 'red',
                            fill: false,
                            tension: 0.1
                        }
                    ]
                },
                options: {
                    responsive: true,
                    animation: false,
                    scales: { y: { beginAtZero: true } }
                }
            });

            // Listen for 'update' events from server
            socket.on("update", data => {
                const now = new Date().toLocaleTimeString();

                // Add current time and data points to chart
                chart.data.labels.push(now);
                chart.data.datasets[0].data.push(data.connections);
                chart.data.datasets[1].data.push(data.blocked);

                // Limit chart points to last 30 entries
                if (chart.data.labels.length > 30) {
                    chart.data.labels.shift();
                    chart.data.datasets[0].data.shift();
                    chart.data.datasets[1].data.shift();
                }

                chart.update();

                // Update the log table with latest entries
                const tbody = document.querySelector("#logTable tbody");
                tbody.innerHTML = "";
                data.latest_logs.forEach(line => {
                    const row = document.createElement("tr");
                    const cell = document.createElement("td");
                    cell.textContent = line;
                    row.appendChild(cell);
                    tbody.appendChild(row);
                });
            });
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

@app.route("/blacklist", methods=["GET", "POST"])
def manage_blacklist():
    """
    Display and manage the blacklist of blocked sites.
    Allows adding and removing sites from blacklist via POST form submissions.
    On update, restarts the proxy server if running.
    """
    settings = load_settings()
    blacklist = settings.get("blacklist", [])

    if request.method == "POST":
        action = request.form.get("action")
        site = request.form.get("site", "").strip()

        if action == "add" and site and site not in blacklist:
            blacklist.append(site)
        elif action == "remove" and site in blacklist:
            blacklist.remove(site)

        # Save updated blacklist to settings
        settings["blacklist"] = blacklist
        save_settings(settings)

        # Restart proxy process if it's running to apply changes
        global proxy_process
        if proxy_process and proxy_process.poll() is None:
            proxy_process.terminate()
            proxy_process.wait()
            proxy_process = subprocess.Popen(["python", "main.py"])

        return redirect("/blacklist")

    # Render the blacklist management form and current list
    html_template = """
    <!doctype html>
    <html lang="en">
    <head>
        <title>Manage Blacklist</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            input[type="text"] { padding: 5px; width: 300px; }
            button { padding: 6px 12px; margin-left: 5px; }
            ul { list-style: none; padding: 0; }
            li { margin-bottom: 5px; }
        </style>
    </head>
    <body>
        <h1>Blacklist Management</h1>
        <form method="POST">
            <input type="text" name="site" placeholder="example.com" required>
            <button type="submit" name="action" value="add">Add</button>
        </form>
        <h3>Current Blacklist:</h3>
        <ul>
            {% for site in blacklist %}
            <li>
                {{ site }}
                <form method="POST" style="display:inline;">
                    <input type="hidden" name="site" value="{{ site }}">
                    <button type="submit" name="action" value="remove">Remove</button>
                </form>
            </li>
            {% endfor %}
        </ul>
        <a href="/">← Back to Dashboard</a>
    </body>
    </html>
    """
    return render_template_string(html_template, blacklist=blacklist)


@app.route("/")
def view_cache():
    """
    Dashboard for viewing the current proxy cache.
    Shows cached URL keys and response sizes.
    Also provides controls to start/stop the proxy server,
    clear cache, and navigate to blacklist and live stats.
    """
    global proxy_process

    # Load cache data from pickle file if available, else empty dict
    if not os.path.exists(CACHE_FILE):
        cache = {}
    else:
        try:
            with open(CACHE_FILE, "rb") as f:
                cache = pickle.load(f)
        except Exception:
            cache = {}

    # Check if the proxy process is running
    proxy_running = proxy_process is not None and proxy_process.poll() is None

    html_template = """
    <!doctype html>
    <html lang="en">
    <head>
        <title>Proxy Cache Dashboard</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            table { border-collapse: collapse; width: 100%; margin-top: 10px; }
            th, td { padding: 8px 12px; border: 1px solid #ddd; }
            th { background-color: #f2f2f2; }
            .clear-btn, .control-btn {
                padding: 8px 16px;
                color: white;
                text-decoration: none;
                border-radius: 4px;
                margin-right: 10px;
                display: inline-block;
            }
            .clear-btn { background: #d9534f; }
            .start-btn { background: #5cb85c; }
            .stop-btn { background: #f0ad4e; }
            .disabled { background: #ccc; pointer-events: none; }
        </style>
    </head>
    <body>
        <h1>Proxy Cache Viewer</h1>

        <!-- Proxy server control buttons -->
        {% if proxy_running %}
            <a href="{{ url_for('stop_proxy') }}" class="control-btn stop-btn">Stop Proxy Server</a>
            <span style="color: green; font-weight: bold;">Proxy Server is Running</span>
        {% else %}
            <a href="{{ url_for('start_proxy') }}" class="control-btn start-btn">Start Proxy Server</a>
            <span style="color: red; font-weight: bold;">Proxy Server is Stopped</span>
        {% endif %}

        <br><br>
        <a href="/blacklist" class="clear-btn" style="background:#0275d8;">Manage Blacklist</a>
        <a href="/live" class="clear-btn" style="background:#5cb85c;">Live Stats</a>
        <a href="/clearcache" class="clear-btn">Clear Cache</a>

        <!-- Cache table -->
        <h3>Cached Responses</h3>
        <table>
            <thead>
                <tr><th>URL (Cache Key)</th><th>Response Size (Bytes)</th></tr>
            </thead>
            <tbody>
                {% for key, value in cache.items() %}
                <tr>
                    <td style="word-break: break-all;">{{ key }}</td>
                    <td>{{ value|length }}</td>
                </tr>
                {% endfor %}
                {% if not cache %}
                <tr><td colspan="2" style="text-align:center;">No cached responses available.</td></tr>
                {% endif %}
            </tbody>
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, cache=cache, proxy_running=proxy_running)


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
    Stops the running proxy server subprocess if running.
    """
    global proxy_process

    if proxy_process and proxy_process.poll() is None:
        proxy_process.terminate()
        proxy_process.wait()
        proxy_process = None

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

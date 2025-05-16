from flask import Flask, render_template_string, redirect
import pickle
import os
from config import CACHE_FILE

app = Flask(__name__)

@app.route("/")
def view_cache():
    if not os.path.exists(CACHE_FILE):
        return "No cache found."

    try:
        with open(CACHE_FILE, "rb") as f:
            cache = pickle.load(f)
    except Exception as e:
        return f"Error loading cache: {e}"

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
            .clear-btn {
                padding: 8px 16px;
                background: #d9534f;
                color: white;
                text-decoration: none;
                border-radius: 4px;
            }
        </style>
    </head>
    <body>
        <h1>Proxy Cache Viewer</h1>
        <a href="/clear" class="clear-btn">Clear Cache</a>
        <p>Total Cached Items: {{ cache|length }}</p>
        <table>
            <thead>
                <tr>
                    <th>URL / Key</th>
                    <th>Response Size (bytes)</th>
                </tr>
            </thead>
            <tbody>
                {% for key, value in cache.items() %}
                <tr>
                    <td>{{ key }}</td>
                    <td>{{ value|length }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """

    return render_template_string(html_template, cache=cache)

@app.route("/clear")
def clear_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'wb') as f:
                pickle.dump({}, f)
        except Exception as e:
            return f"Failed to clear cache: {e}"
    return redirect("/")
    
if __name__ == "__main__":
    app.run(port=5000, debug=True)

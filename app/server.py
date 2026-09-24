"""Dependency-free web form and JSON API for the local dblp mirror."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

from . import carts, search
from .importer import SCHEMA_VERSION

DB_PATH = Path(os.environ.get("DBLP_DB", "dblp.sqlite3"))
STATE_PATH = Path(os.environ.get("DBLP_DATA_DIR", "/data")) / "refresh-state.json"
HOST = os.environ.get("DBLP_HOST", "0.0.0.0")
PORT = int(os.environ.get("DBLP_PORT", "8080"))
HTML = Path(__file__).with_name("index.html")


def integer(params, key, default=None, low=None, high=None):
    raw = params.get(key, [None])[0]
    if raw is None or raw == "":
        return default
    value = int(raw)
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


class Handler(BaseHTTPRequestHandler):
    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
            self.end_headers()
            self.wfile.write(body)
            return
        if not parsed.path.startswith("/api/"):
            self.respond({"error": "Not found"}, 404)
            return
        params = parse_qs(parsed.query, keep_blank_values=True)
        get = lambda key, default="": params.get(key, [default])[0]
        try:
            if len(get("q")) > 200 or len(get("author")) > 150 or len(get("venue")) > 150 or len(get("key")) > 300:
                raise ValueError("Query is too long")
            if parsed.path == "/api/status":
                metadata = {}
                try:
                    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as conn:
                        metadata = dict(conn.execute("SELECT key,value FROM metadata"))
                except sqlite3.Error:
                    pass
                try:
                    refresh = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    refresh = {}
                self.respond({"ready": metadata.get("schema_version") in ("2", SCHEMA_VERSION), "metadata": metadata, "refresh": refresh})
                return
            with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                if parsed.path == "/api/search":
                    result = search.search(
                        conn, get("q"), get("author"), get("venue"), integer(params, "year_from", low=0, high=9999),
                        integer(params, "year_to", low=0, high=9999), get("type"),
                        integer(params, "limit", 300 if get("author") else 20, 1, 300), integer(params, "offset", 0, 0, 1000000), get("sort", "relevance"), get("category"),
                    )
                elif parsed.path == "/api/autocomplete":
                    kind = get("kind", "title")
                    if kind not in ("title", "author"):
                        raise ValueError("kind must be title or author")
                    result = {"suggestions": search.autocomplete(conn, get("q"), kind, integer(params, "limit", 10, 1, 25))}
                elif parsed.path == "/api/closest":
                    result = {"results": search.group_results(conn, search.closest(conn, get("q"), integer(params, "limit", 10, 1, 25)))}
                elif parsed.path == "/api/record":
                    result = search.record(conn, get("key"))
                    if result is None:
                        self.respond({"error": "Record not found"}, 404)
                        return
                else:
                    self.respond({"error": "Not found"}, 404)
                    return
            self.respond(result)
        except ValueError as exc:
            self.respond({"error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self.respond({"error": f"Database unavailable: {exc}"}, 503)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/records", "/api/bibtex"):
            self.respond({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 100_000:
                raise ValueError("Request body must be 1 to 100,000 bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            keys = carts.validate_keys(data.get("keys"))
            with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                result = carts.records(conn, keys) if path == "/api/records" else carts.bibliography(conn, keys)
            self.respond(result)
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond({"error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self.respond({"error": f"Database unavailable: {exc}"}, 503)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving dblp at http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

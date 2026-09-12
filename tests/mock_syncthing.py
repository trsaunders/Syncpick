"""A fake Syncthing exposing just the REST endpoints syncpick uses.

It creates a temp folder with a few titles on disk and a larger global
catalogue, so the app can be exercised end to end without a real Syncthing.

    python3 tests/mock_syncthing.py            # prints the folder path and listens on :8384
"""

import json
import os
import socketserver
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("MOCK_API_KEY", "test")
PORT = int(os.environ.get("MOCK_PORT", "8384"))

GB = 1_000_000_000

# Global catalogue: (title, files) where files is {name: size}
FILMS = {
    "Arrival (2016)": {"Arrival (2016) Bluray-1080p.mkv": 9 * GB},
    "Dune (2021) [Bluray-2160p]": {"Dune (2021).mkv": 28 * GB, "Dune (2021).en.srt": 120_000},
    "Heat (1995)": {"Heat (1995).mkv": 14 * GB},
    "Paddington 2 (2017)": {"Paddington 2.mkv": 6 * GB},
    "The Thing {1982}": {"The Thing.mkv": 11 * GB},
    "Whiplash (2014)": {"Whiplash.mkv": 7 * GB},
}
TV = {
    "Severance": {
        "Season 01": {f"Severance S01E{i:02d}.mkv": 3 * GB for i in range(1, 10)},
        "Season 02": {f"Severance S02E{i:02d}.mkv": 3 * GB for i in range(1, 11)},
    },
    "The Bear": {
        "Season 01": {f"The Bear S01E{i:02d}.mkv": 2 * GB for i in range(1, 9)},
        "Season 02": {f"The Bear S02E{i:02d}.mkv": 2 * GB for i in range(1, 11)},
        "Season 03": {f"The Bear S03E{i:02d}.mkv": 2 * GB for i in range(1, 11)},
    },
    "Slow Horses": {
        "Season 01": {f"Slow Horses S01E{i:02d}.mkv": 2 * GB for i in range(1, 7)},
    },
}

# What is on disk initially (subset of the catalogue), plus one local-only stray.
ON_DISK = {
    "films": ["Arrival (2016)", "Whiplash (2014)"],
    "tv": [("Severance", ["Season 01"]), ("The Bear", ["Season 01", "Season 02", "Season 03"])],
}


def to_browse(tree: dict):
    out = []
    for name, value in sorted(tree.items()):
        if isinstance(value, dict):
            out.append({"name": name, "type": "FILE_INFO_TYPE_DIRECTORY", "modTime": "2026-01-01T00:00:00Z", "size": 128, "children": to_browse(value)})
        else:
            out.append({"name": name, "type": "FILE_INFO_TYPE_FILE", "modTime": "2026-01-01T00:00:00Z", "size": value})
    return out


def sparse_file(path: str, size: int):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        if size:
            fh.seek(size - 1)
            fh.write(b"\0")


def seed(root: str):
    films = os.path.join(root, "films")
    tv = os.path.join(root, "tv")
    for base in (films, tv):
        os.makedirs(os.path.join(base, ".stfolder"), exist_ok=True)
    for title in ON_DISK["films"]:
        for fname, size in FILMS[title].items():
            sparse_file(os.path.join(films, title, fname), size)
    for show, seasons in ON_DISK["tv"]:
        for season in seasons:
            for fname, size in TV[show][season].items():
                sparse_file(os.path.join(tv, show, season, fname), size)
    # A partial download and a stray local-only directory.
    sparse_file(os.path.join(films, "Heat (1995)", "Heat (1995).mkv.tmp"), 5 * GB)
    sparse_file(os.path.join(films, "Old Rip (2001)", "old.avi"), 700_000_000)
    return films, tv


class Mock:
    def __init__(self, root):
        self.films_path, self.tv_path = seed(root)
        self.folders = {
            "films": {"id": "films", "label": "Films", "path": self.films_path, "type": "receiveonly"},
            "tv": {"id": "tv", "label": "TV", "path": self.tv_path, "type": "sendreceive"},
        }
        self.catalogue = {"films": to_browse(FILMS), "tv": to_browse(TV)}
        self.lock = threading.Lock()
        self.scans = []

    def ignore_path(self, fid):
        return os.path.join(self.folders[fid]["path"], ".stignore")

    def get_ignores(self, fid):
        try:
            with open(self.ignore_path(fid), encoding="utf-8") as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return []

    def set_ignores(self, fid, lines):
        with open(self.ignore_path(fid), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        return lines


mock: Mock


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("X-API-Key") != API_KEY:
            self._json(403, {"error": "Not Authorized"})
            return False
        return True

    def do_GET(self):
        if not self._auth():
            return
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        fid = (q.get("folder") or [None])[0]
        if u.path == "/rest/system/version":
            return self._json(200, {"version": "v2.0.0-mock", "os": "linux", "arch": "amd64"})
        if u.path == "/rest/config/folders":
            return self._json(200, list(mock.folders.values()))
        if fid not in mock.folders:
            return self._json(404, {"error": "no such folder"})
        if u.path == "/rest/db/ignores":
            return self._json(200, {"ignore": mock.get_ignores(fid), "expanded": []})
        if u.path == "/rest/db/browse":
            return self._json(200, mock.catalogue[fid])
        if u.path == "/rest/db/status":
            return self._json(200, {"state": "idle", "errors": 0, "localBytes": 0, "globalBytes": 0, "needBytes": 0})
        if u.path == "/rest/db/completion":
            return self._json(200, {"completion": 100.0, "needBytes": 0, "needItems": 0, "globalBytes": 0})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        fid = (q.get("folder") or [None])[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if fid not in mock.folders:
            return self._json(404, {"error": "no such folder"})
        if u.path == "/rest/db/ignores":
            with mock.lock:
                lines = mock.set_ignores(fid, body.get("ignore") or [])
            return self._json(200, {"ignore": lines, "expanded": []})
        if u.path == "/rest/db/scan":
            mock.scans.append((fid, (q.get("sub") or [None])[0]))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        if os.environ.get("MOCK_VERBOSE"):
            sys.stderr.write("mock: " + fmt % args + "\n")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # Skip HTTPServer's reverse DNS lookup of the bind address.
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


def main():
    global mock
    root = os.environ.get("MOCK_ROOT") or tempfile.mkdtemp(prefix="syncpick-mock-")
    mock = Mock(root)
    print(f"mock syncthing on http://127.0.0.1:{PORT}  api key: {API_KEY}", flush=True)
    print(f"folders: films={mock.films_path} tv={mock.tv_path}", flush=True)
    httpd = Server(("127.0.0.1", PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

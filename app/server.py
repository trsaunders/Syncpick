"""HTTP server: a JSON API plus the single-page UI."""

import json
import logging
import socketserver
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .service import Service, ServiceError
from .syncthing import SyncthingError

log = logging.getLogger("syncpick.http")
STATIC_DIR = Path(__file__).parent / "static"


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "syncpick/0.1"

        # ---- helpers -----------------------------------------------------

        def _json(self, status: int, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str):
            self._json(status, {"error": message})

        def _static(self, name: str, content_type: str):
            path = STATIC_DIR / name
            try:
                data = path.read_bytes()
            except OSError:
                self._error(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise ServiceError("request body must be JSON")

        def _dispatch(self, method: str):
            parsed = urllib.parse.urlsplit(self.path)
            route = parsed.path.rstrip("/") or "/"
            query = urllib.parse.parse_qs(parsed.query)
            folder = (query.get("folder") or [None])[0]
            refresh = (query.get("refresh") or ["0"])[0] in ("1", "true")
            try:
                if method == "GET" and route == "/":
                    return self._static("index.html", "text/html; charset=utf-8")
                if method == "GET" and route == "/api/overview":
                    return self._json(200, service.overview())
                if method == "GET" and route == "/api/tree":
                    if not folder:
                        raise ServiceError("folder is required")
                    return self._json(200, service.tree(folder, refresh=refresh))
                if method == "POST" and route == "/api/enable":
                    if not folder:
                        raise ServiceError("folder is required")
                    return self._json(200, service.enable(folder))
                if method == "POST" and route in ("/api/plan", "/api/apply"):
                    if not folder:
                        raise ServiceError("folder is required")
                    body = self._body()
                    selected = body.get("selected", [])
                    if route == "/api/plan":
                        return self._json(200, service.plan(folder, selected))
                    return self._json(200, service.apply(folder, selected))
                if method == "GET" and route == "/healthz":
                    return self._json(200, {"ok": True})
                return self._error(404, "not found")
            except ServiceError as e:
                return self._error(e.status, str(e))
            except SyncthingError as e:
                return self._error(HTTPStatus.BAD_GATEWAY, str(e))
            except Exception as e:  # noqa: BLE001
                log.exception("unhandled error on %s %s", method, self.path)
                return self._error(500, f"internal error: {e}")

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup of the bind address, which can
        # stall startup for a long time when DNS is slow or absent. We do not need the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


def serve(service: Service, bind: str, port: int):
    httpd = _Server((bind, port), make_handler(service))
    httpd.daemon_threads = True
    log.info("listening on http://%s:%d", bind, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

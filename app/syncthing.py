"""Thin client for the handful of Syncthing REST endpoints this tool needs."""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("syncpick.syncthing")


class SyncthingError(Exception):
    pass


class Syncthing:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Never go through a proxy to reach Syncthing, and skip the per-request proxy
        # discovery urllib would otherwise do (slow on some platforms).
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---- transport -------------------------------------------------------

    def _request(self, method: str, path: str, params: dict | None = None, body=None, timeout=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = None
        headers = {"X-API-Key": self.api_key}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            raise SyncthingError(f"{method} {path} -> HTTP {e.code}: {detail or e.reason}") from None
        except urllib.error.URLError as e:
            raise SyncthingError(f"{method} {path} -> cannot reach Syncthing at {self.base_url}: {e.reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", "replace")

    def get(self, path, **params):
        return self._request("GET", path, params)

    def post(self, path, body=None, **params):
        return self._request("POST", path, params, body)

    # ---- endpoints ---------------------------------------------------------

    def version(self) -> dict:
        return self.get("/rest/system/version") or {}

    def folders(self) -> list[dict]:
        return self.get("/rest/config/folders") or []

    def folder(self, folder_id: str) -> dict:
        return self.get("/rest/config/folders/" + urllib.parse.quote(folder_id, safe="")) or {}

    def ignores(self, folder_id: str) -> list[str]:
        data = self.get("/rest/db/ignores", folder=folder_id) or {}
        return list(data.get("ignore") or [])

    def set_ignores(self, folder_id: str, lines: list[str]) -> list[str]:
        data = self.post("/rest/db/ignores", {"ignore": lines}, folder=folder_id) or {}
        return list(data.get("ignore") or [])

    def scan(self, folder_id: str, sub: str | None = None) -> None:
        # Scanning can block for a while on large folders; give it room.
        self._request("POST", "/rest/db/scan", {"folder": folder_id, "sub": sub}, timeout=600)

    def status(self, folder_id: str) -> dict:
        return self.get("/rest/db/status", folder=folder_id) or {}

    def completion(self, folder_id: str) -> dict:
        return self.get("/rest/db/completion", folder=folder_id) or {}

    def browse(self, folder_id: str, prefix: str | None = None, levels: int | None = None):
        """Global tree of the folder. Expensive on Syncthing's side; callers cache it."""
        return self._request(
            "GET", "/rest/db/browse", {"folder": folder_id, "prefix": prefix, "levels": levels}, timeout=600
        )

    def wait_until_idle(self, folder_id: str, max_seconds: float) -> bool:
        """Poll until the folder is no longer scanning. Returns True if it settled."""
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            try:
                state = (self.status(folder_id) or {}).get("state", "")
            except SyncthingError as e:
                log.warning("status poll failed: %s", e)
                state = ""
            if state and state not in ("scanning", "scan-waiting"):
                return True
            time.sleep(1.0)
        return False

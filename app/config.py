"""Runtime configuration from environment variables."""

import os
import re
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _read_api_key(config_dir: str) -> str | None:
    """Pull the GUI API key out of a mounted Syncthing config.xml."""
    if not config_dir:
        return None
    path = Path(config_dir) / "config.xml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"<apikey>([^<]+)</apikey>", text)
    return match.group(1).strip() if match else None


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.syncthing_url = env.get("SYNCTHING_URL", "http://syncthing:8384").rstrip("/")
        self.syncthing_config_dir = env.get("SYNCTHING_CONFIG_DIR", "/syncthing-config")
        self.api_key = env.get("SYNCTHING_API_KEY") or _read_api_key(self.syncthing_config_dir)
        self.folder_ids = [f.strip() for f in env.get("FOLDERS", "").split(",") if f.strip()]
        self.path_map = self._parse_path_map(env.get("PATH_MAP", ""))
        self.bind = env.get("BIND", "0.0.0.0")
        self.port = int(env.get("PORT", "8080"))
        self.max_depth = max(1, int(env.get("MAX_DEPTH", "2")))
        self.tree_cache_seconds = int(env.get("TREE_CACHE_SECONDS", "600"))
        self.local_cache_seconds = int(env.get("LOCAL_CACHE_SECONDS", "30"))
        self.scan_wait_seconds = int(env.get("SCAN_WAIT_SECONDS", "120"))
        self.dry_run = _truthy(env.get("DRY_RUN"))
        self.log_level = env.get("LOG_LEVEL", "INFO").upper()

    @staticmethod
    def _parse_path_map(raw: str) -> list[tuple[str, str]]:
        """PATH_MAP="/data:/media,/other:/mnt/other" maps Syncthing's paths to ours."""
        pairs = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                raise ValueError(f"PATH_MAP entry {chunk!r} must look like /remote:/local")
            remote, local = chunk.split(":", 1)
            pairs.append((remote.rstrip("/") or "/", local.rstrip("/") or "/"))
        # Longest remote prefix wins.
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs

    def local_path(self, remote_path: str) -> str:
        """Translate a folder path as Syncthing sees it into a path inside this container."""
        remote_path = remote_path.rstrip("/") or "/"
        for remote, local in self.path_map:
            if remote_path == remote or remote_path.startswith(remote + "/"):
                return local + remote_path[len(remote):]
        return remote_path

    def validate(self) -> list[str]:
        problems = []
        if not self.api_key:
            problems.append(
                "No Syncthing API key. Set SYNCTHING_API_KEY, or mount Syncthing's config "
                f"directory at {self.syncthing_config_dir} so config.xml can be read."
            )
        return problems

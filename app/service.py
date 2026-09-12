"""Folder discovery, caching, and the plan / apply / enable operations."""

import logging
import os
import shutil
import threading
import time

from . import ignores
from . import tree as treemod
from .config import Config
from .syncthing import Syncthing, SyncthingError

log = logging.getLogger("syncpick.service")


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Service:
    def __init__(self, cfg: Config, st: Syncthing):
        self.cfg = cfg
        self.st = st
        self._lock = threading.Lock()
        self._global: dict[str, tuple[float, treemod.Node]] = {}
        self._local: dict[str, tuple[float, treemod.Node]] = {}

    # ---- folders -----------------------------------------------------------

    def folders(self) -> list[dict]:
        raw = self.st.folders()
        out = []
        for f in raw:
            fid = f.get("id")
            if not fid:
                continue
            if self.cfg.folder_ids and fid not in self.cfg.folder_ids:
                continue
            remote = f.get("path") or ""
            out.append(
                {
                    "id": fid,
                    "label": f.get("label") or fid,
                    "remote_path": remote,
                    "local_path": self.cfg.local_path(remote),
                    "type": f.get("type") or "sendreceive",
                    "paused": bool(f.get("paused")),
                }
            )
        return out

    def _folder(self, folder_id: str) -> dict:
        for f in self.folders():
            if f["id"] == folder_id:
                return f
        raise ServiceError(f"Unknown folder {folder_id!r}", 404)

    # ---- overview ----------------------------------------------------------

    def overview(self) -> dict:
        info = {"ok": True, "version": None, "error": None}
        folders = []
        try:
            info["version"] = (self.st.version() or {}).get("version")
            for f in self.folders():
                folders.append(self._folder_summary(f))
        except SyncthingError as e:
            info["ok"] = False
            info["error"] = str(e)
        return {
            "syncthing": info,
            "folders": folders,
            "config": {
                "max_depth": self.cfg.max_depth,
                "dry_run": self.cfg.dry_run,
                "syncthing_url": self.cfg.syncthing_url,
            },
        }

    def _folder_summary(self, f: dict) -> dict:
        summary = dict(f)
        try:
            parsed = ignores.parse(self.st.ignores(f["id"]))
            summary["managed"] = parsed.managed
            summary["selected_count"] = len(parsed.selected or ())
        except SyncthingError as e:
            summary["managed"] = False
            summary["selected_count"] = 0
            summary["error"] = str(e)
        try:
            status = self.st.status(f["id"]) or {}
            completion = self.st.completion(f["id"]) or {}
        except SyncthingError as e:
            status, completion = {}, {}
            summary["error"] = str(e)
        summary["status"] = {
            "state": status.get("state"),
            "errors": status.get("errors") or 0,
            "local_bytes": status.get("localBytes") or 0,
            "global_bytes": status.get("globalBytes") or 0,
            "need_bytes": completion.get("needBytes") or status.get("needBytes") or 0,
            "need_items": completion.get("needItems") or status.get("needFiles") or 0,
            "completion": completion.get("completion"),
        }
        summary["disk"] = self._disk(f["local_path"])
        summary["mount"] = self._mount_check(f)
        return summary

    @staticmethod
    def _disk(path: str) -> dict | None:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        return {"total": usage.total, "used": usage.used, "free": usage.free}

    # ---- mount verification -----------------------------------------------

    def _mount_check(self, f: dict, expected_lines: list[str] | None = None) -> dict:
        """Confirm that the folder path we see is the same directory Syncthing writes to.

        The .stignore file is written by Syncthing through its own mount and read back
        by us through ours.  If they disagree, PATH_MAP is wrong and we must not delete.
        """
        local = f["local_path"]
        if not os.path.isdir(local):
            return {"ok": False, "reason": f"{local} does not exist inside this container"}
        if not os.path.exists(os.path.join(local, ".stfolder")):
            return {"ok": False, "reason": f"{local} has no .stfolder marker; is it the folder root?"}
        try:
            api_lines = expected_lines if expected_lines is not None else self.st.ignores(f["id"])
        except SyncthingError as e:
            return {"ok": False, "reason": f"cannot read ignores from Syncthing: {e}"}
        disk_lines = self._read_local_ignore(local)
        if _same_lines(api_lines, disk_lines):
            return {"ok": True, "reason": None}
        if disk_lines is None:
            return {"ok": False, "reason": f"Syncthing has ignore patterns but {local}/.stignore is missing here"}
        return {"ok": False, "reason": f"{local}/.stignore differs from what Syncthing reports; check PATH_MAP"}

    @staticmethod
    def _read_local_ignore(local: str) -> list[str] | None:
        path = os.path.join(local, ".stignore")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("cannot read %s: %s", path, e)
            return None

    # ---- trees --------------------------------------------------------------

    def _global_tree(self, folder_id: str, refresh: bool) -> tuple[treemod.Node, float]:
        now = time.time()
        with self._lock:
            cached = self._global.get(folder_id)
            if cached and not refresh and now - cached[0] < self.cfg.tree_cache_seconds:
                return cached[1], cached[0]
        log.info("fetching global tree for %s", folder_id)
        raw = self.st.browse(folder_id)
        node = treemod.build_global_tree(raw or [])
        with self._lock:
            self._global[folder_id] = (now, node)
        return node, now

    def _local_tree(self, folder_id: str, local_path: str, refresh: bool) -> treemod.Node:
        now = time.time()
        with self._lock:
            cached = self._local.get(folder_id)
            if cached and not refresh and now - cached[0] < self.cfg.local_cache_seconds:
                return cached[1]
        node = treemod.build_local_tree(local_path, self.cfg.max_depth) if os.path.isdir(local_path) else treemod.Node("", True)
        with self._lock:
            self._local[folder_id] = (now, node)
        return node

    def invalidate(self, folder_id: str, global_too: bool = False):
        with self._lock:
            self._local.pop(folder_id, None)
            if global_too:
                self._global.pop(folder_id, None)

    def tree(self, folder_id: str, refresh: bool = False) -> dict:
        f = self._folder(folder_id)
        parsed = ignores.parse(self.st.ignores(folder_id))
        selected = parsed.selected or set()
        g, fetched_at = self._global_tree(folder_id, refresh)
        l = self._local_tree(folder_id, f["local_path"], refresh)
        items = treemod.merge(g, l, selected, self.cfg.max_depth)
        return {
            "folder": f,
            "managed": parsed.managed,
            "selected": sorted(selected, key=str.casefold),
            "user_patterns": [ln for ln in parsed.user_lines if ln.strip()],
            "items": items,
            "global_size": g.size,
            "local_size": l.size,
            "fetched_at": fetched_at,
            "mount": self._mount_check(f),
        }

    # ---- enable --------------------------------------------------------------

    def enable(self, folder_id: str) -> dict:
        f = self._folder(folder_id)
        parsed = ignores.parse(self.st.ignores(folder_id))
        if parsed.managed:
            return {"already": True, "selected": sorted(parsed.selected)}
        kept, absorbed = ignores.absorb_plain_negations(parsed.user_lines)
        local = self._local_tree(folder_id, f["local_path"], refresh=True)
        seed = set(absorbed) | set(local.children)
        seed = ignores.normalize_selection(seed)
        lines = ignores.render(kept, seed)
        log.info("enabling management on %s with %d seeded entries", folder_id, len(seed))
        if not self.cfg.dry_run:
            self.st.set_ignores(folder_id, lines)
            try:
                self.st.scan(folder_id)
            except SyncthingError as e:
                log.warning("scan after enable failed: %s", e)
        self.invalidate(folder_id)
        return {"already": False, "selected": sorted(seed, key=str.casefold), "absorbed": sorted(absorbed)}

    # ---- plan / apply -----------------------------------------------------

    def _validate_selection(self, selected, g: treemod.Node, l: treemod.Node) -> set[str]:
        if not isinstance(selected, list):
            raise ServiceError("selected must be a list of paths")
        cleaned = set()
        for p in selected:
            if not isinstance(p, str):
                raise ServiceError("selected entries must be strings")
            p = p.strip().strip("/")
            if not p:
                continue
            parts = p.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise ServiceError(f"invalid path {p!r}")
            if len(parts) > self.cfg.max_depth:
                raise ServiceError(f"{p!r} is deeper than MAX_DEPTH={self.cfg.max_depth}")
            if parts[0] in treemod.RESERVED_ROOT_NAMES or parts[0].startswith("."):
                raise ServiceError(f"{p!r} is reserved")
            if treemod.find(g, p) is None and treemod.find(l, p) is None:
                raise ServiceError(f"{p!r} is not in the catalogue or on disk")
            cleaned.add(p)
        return ignores.normalize_selection(cleaned)

    def plan(self, folder_id: str, selected) -> dict:
        f = self._folder(folder_id)
        current = ignores.parse(self.st.ignores(folder_id))
        if not current.managed:
            raise ServiceError("This folder is not managed yet. Enable selective sync first.")
        g, _ = self._global_tree(folder_id, refresh=False)
        l = self._local_tree(folder_id, f["local_path"], refresh=True)
        new_sel = self._validate_selection(selected, g, l)
        old_sel = current.selected or set()

        deletions = _deletions(l, new_sel, self.cfg.max_depth)
        additions = []
        for p in sorted(new_sel, key=str.casefold):
            if ignores.is_covered(p, old_sel):
                continue  # was already whitelisted, directly or through a parent
            node = treemod.find(g, p)
            additions.append({"path": p, "size": node.size if node else 0})
        lines = ignores.render(current.user_lines, new_sel)

        delete_bytes = sum(d["size"] for d in deletions)
        add_bytes = sum(a["size"] for a in additions)
        # Bytes of newly selected items already on disk (partial downloads etc.) need no re-download.
        already = 0
        for a in additions:
            node = treemod.find(l, a["path"])
            if node:
                already += min(node.size, a["size"])
        disk = self._disk(f["local_path"])
        projected_free = None
        if disk:
            projected_free = disk["free"] + delete_bytes - max(0, add_bytes - already)
        return {
            "folder": f,
            "selected": sorted(new_sel, key=str.casefold),
            "additions": additions,
            "deletions": deletions,
            "delete_bytes": delete_bytes,
            "download_bytes": max(0, add_bytes - already),
            "disk": disk,
            "projected_free": projected_free,
            "patterns": lines,
            "patterns_changed": not _same_lines(lines, self.st.ignores(folder_id)),
            "mount": self._mount_check(f),
            "dry_run": self.cfg.dry_run,
        }

    def apply(self, folder_id: str, selected) -> dict:
        plan = self.plan(folder_id, selected)
        f = plan["folder"]
        fid = folder_id
        report = {"folder": f, "wrote_patterns": False, "deleted": [], "skipped": [], "warnings": [], "dry_run": self.cfg.dry_run}

        if plan["deletions"] and not plan["mount"]["ok"]:
            raise ServiceError(
                "Refusing to delete: cannot confirm this container sees the same directory as Syncthing. "
                + (plan["mount"]["reason"] or ""),
                409,
            )

        if self.cfg.dry_run:
            report["warnings"].append("DRY_RUN is set: nothing was written or deleted.")
            report["would_delete"] = plan["deletions"]
            report["would_write"] = plan["patterns"]
            return report

        # 1. Ignore first, so Syncthing stops caring about what we are about to remove.
        if plan["patterns_changed"]:
            self.st.set_ignores(fid, plan["patterns"])
            report["wrote_patterns"] = True
            log.info("%s: wrote %d patterns", fid, len(plan["patterns"]))

        # 2. Let Syncthing take the new patterns on board.
        try:
            self.st.scan(fid)
        except SyncthingError as e:
            report["warnings"].append(f"scan request failed: {e}")
        if plan["deletions"]:
            if not self.st.wait_until_idle(fid, self.cfg.scan_wait_seconds):
                report["warnings"].append("Syncthing was still scanning after the wait limit; deletions went ahead because the paths are ignored.")
            # Re-verify with the lines we just wrote before touching anything.
            check = self._mount_check(f, expected_lines=plan["patterns"])
            if not check["ok"]:
                raise ServiceError("Refusing to delete after writing patterns: " + (check["reason"] or ""), 409)
            local_now = ignores.parse(self._read_local_ignore(f["local_path"]) or [])
            for d in plan["deletions"]:
                if ignores.is_covered(d["path"], local_now.selected or set()):
                    report["skipped"].append({**d, "reason": "still whitelisted on disk"})
                    continue
                try:
                    self._delete(f["local_path"], d["path"])
                    report["deleted"].append(d)
                except ServiceError as e:
                    report["skipped"].append({**d, "reason": str(e)})
            # 3. Tell Syncthing the disk changed.
            try:
                self.st.scan(fid)
            except SyncthingError as e:
                report["warnings"].append(f"post-delete scan failed: {e}")

        self.invalidate(fid)
        report["freed_bytes"] = sum(d["size"] for d in report["deleted"])
        return report

    def _delete(self, root: str, rel: str):
        root_real = os.path.realpath(root)
        target = os.path.realpath(os.path.join(root, rel))
        if target == root_real or not target.startswith(root_real + os.sep):
            raise ServiceError(f"{rel!r} resolves outside the folder; not deleting")
        first = rel.split("/")[0]
        if first in treemod.RESERVED_ROOT_NAMES or first.startswith("."):
            raise ServiceError(f"{rel!r} is reserved; not deleting")
        if os.path.islink(os.path.join(root, rel)):
            os.unlink(os.path.join(root, rel))
        elif os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.exists(target):
            os.remove(target)
        else:
            raise ServiceError(f"{rel!r} vanished before deletion")
        log.info("deleted %s", target)


def _deletions(local: treemod.Node, selected: set[str], max_depth: int) -> list[dict]:
    """Everything on disk (down to max_depth) that the new selection does not cover."""
    out = []

    def walk(node: treemod.Node, prefix: str, depth: int):
        for name, child in sorted(node.children.items(), key=lambda kv: kv[0].casefold()):
            path = f"{prefix}/{name}" if prefix else name
            if ignores.is_covered(path, selected):
                continue
            if child.is_dir and depth + 1 < max_depth and ignores.has_selected_descendant(path, selected):
                walk(child, path, depth + 1)
                continue
            out.append({"path": path, "size": child.size, "is_dir": child.is_dir})

    walk(local, "", 0)
    return out


def _same_lines(a, b) -> bool:
    if a is None or b is None:
        return (a or []) == (b or []) or (not a and not b)
    strip = lambda lines: [ln.rstrip() for ln in lines if ln.strip()]
    return strip(a) == strip(b)

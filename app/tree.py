"""Build the item list a folder shows: the global catalogue merged with what is on disk."""

import logging
import os
import stat

from . import ignores

log = logging.getLogger("syncpick.tree")

DIR_TYPES = {"FILE_INFO_TYPE_DIRECTORY", "directory", "dir"}
RESERVED_ROOT_NAMES = {".stfolder", ".stignore", ".stversions"}


class Node:
    __slots__ = ("name", "is_dir", "size", "children")

    def __init__(self, name: str, is_dir: bool, size: int = 0):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.children: dict[str, "Node"] = {}


# ---- global tree from Syncthing --------------------------------------------


def _entries(raw):
    """Normalise both browse response shapes (list of entries, or dict keyed by name)."""
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and "name" in e:
                yield e
    elif isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, dict):
                yield {"name": name, "type": "FILE_INFO_TYPE_DIRECTORY", "children": value}
            else:
                # Old format: file -> [modTime, size]
                size = value[1] if isinstance(value, list) and len(value) > 1 else 0
                yield {"name": name, "type": "FILE_INFO_TYPE_FILE", "size": size}


def build_global_tree(raw) -> Node:
    root = Node("", True)

    def fill(node: Node, raw_children):
        for e in _entries(raw_children):
            is_dir = e.get("type") in DIR_TYPES or "children" in e
            child = Node(e["name"], is_dir, 0 if is_dir else int(e.get("size") or 0))
            if is_dir:
                fill(child, e.get("children") or [])
                child.size = sum(c.size for c in child.children.values())
            node.children[child.name] = child

    fill(root, raw)
    root.size = sum(c.size for c in root.children.values())
    return root


# ---- local tree from disk ---------------------------------------------------


def _du(path: str) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    total += _du(entry.path)
                elif stat.S_ISREG(st.st_mode):
                    total += st.st_size
    except OSError as e:
        log.warning("cannot read %s: %s", path, e)
    return total


def build_local_tree(root_path: str, max_depth: int) -> Node:
    root = Node("", True)

    def fill(node: Node, path: str, depth: int):
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            log.warning("cannot list %s: %s", path, e)
            return
        for entry in entries:
            name = entry.name
            if depth == 0 and name in RESERVED_ROOT_NAMES:
                continue
            if name.startswith("."):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                child = Node(name, True)
                if depth + 1 < max_depth:
                    fill(child, entry.path, depth + 1)
                    child.size = sum(c.size for c in child.children.values())
                    # Children we did not descend into still count.
                    child.size += _du_shallow_rest(entry.path, child.children)
                else:
                    child.size = _du(entry.path)
            elif stat.S_ISREG(st.st_mode):
                child = Node(name, False, st.st_size)
            else:
                continue
            node.children[name] = child

    fill(root, root_path, 0)
    root.size = sum(c.size for c in root.children.values())
    return root


def _du_shallow_rest(path: str, known: dict) -> int:
    """Size of hidden/skipped entries not represented in `known` (usually zero)."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.name in known:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
                elif stat.S_ISDIR(st.st_mode):
                    total += _du(entry.path)
    except OSError:
        pass
    return total


# ---- merge ------------------------------------------------------------------


def _state(global_size: int, local_size: int, in_global: bool, in_local: bool) -> str:
    if not in_local:
        return "absent"
    if not in_global:
        return "local-only"
    if global_size == 0:
        return "complete"
    if local_size >= global_size:
        return "complete"
    return "partial"


def merge(global_root: Node, local_root: Node, selected: set[str], max_depth: int) -> list[dict]:
    def walk(g: Node | None, l: Node | None, prefix: str, depth: int) -> list[dict]:
        names = set()
        if g:
            names |= set(g.children)
        if l:
            names |= set(l.children)
        items = []
        for name in sorted(names, key=str.casefold):
            gc = g.children.get(name) if g else None
            lc = l.children.get(name) if l else None
            path = f"{prefix}/{name}" if prefix else name
            is_dir = (gc.is_dir if gc else lc.is_dir)
            gsize = gc.size if gc else 0
            lsize = lc.size if lc else 0
            if ignores.is_covered(path, selected):
                sel = "full"
            elif ignores.has_selected_descendant(path, selected):
                sel = "partial"
            else:
                sel = "none"
            item = {
                "path": path,
                "name": name,
                "is_dir": is_dir,
                "size": gsize,
                "local_size": lsize,
                "in_global": gc is not None,
                "state": _state(gsize, lsize, gc is not None, lc is not None),
                "selected": sel,
            }
            if is_dir and depth + 1 < max_depth:
                item["children"] = walk(gc, lc, path, depth + 1)
            items.append(item)
        return items

    return walk(global_root, local_root, "", 0)


def find(root: Node, path: str) -> Node | None:
    node = root
    for part in path.split("/"):
        if not part:
            continue
        node = node.children.get(part)
        if node is None:
            return None
    return node

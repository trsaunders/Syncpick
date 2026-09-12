"""The managed block inside a folder's .stignore.

Layout we write:

    <any patterns the user keeps themselves>
    // >>> syncpick managed block. Edit in the web UI, not here. >>>
    !/Dune (2021)
    !/Severance/Season 01
    *
    // <<< syncpick managed block <<<

Negations must precede the catch-all `*`, and `*` must be last, so the block
always sits at the end of the file.  Everything above it is preserved verbatim,
except a bare catch-all, which would swallow our negations.
"""

BEGIN = "// >>> syncpick managed block. Edit in the web UI, not here. >>>"
END = "// <<< syncpick managed block <<<"
CATCH_ALL = "*"

# Characters that carry glob meaning in Syncthing ignore patterns.
_SPECIAL = set("*?[]{}\\")
_CATCH_ALL_FORMS = {"*", "**", "/*", "/**", "/*/**"}


def escape(path: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in path)


def unescape(pattern: str) -> str:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out.append(pattern[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def negation_for(path: str) -> str:
    return "!/" + escape(path)


def path_from_negation(line: str) -> str | None:
    """'!/Foo \\[x\\]' -> 'Foo [x]'. Returns None for lines that are not plain rooted negations."""
    if not line.startswith("!/"):
        return None
    body = line[2:]
    # A plain path has no unescaped glob characters.
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] in _SPECIAL:
            return None
        i += 1
    path = unescape(body).strip("/")
    return path or None


class Parsed:
    def __init__(self, user_lines: list[str], selected: set[str] | None):
        self.user_lines = user_lines
        self.selected = selected  # None means no managed block present

    @property
    def managed(self) -> bool:
        return self.selected is not None


def parse(lines: list[str]) -> Parsed:
    if BEGIN not in lines:
        return Parsed(list(lines), None)
    start = lines.index(BEGIN)
    try:
        end = lines.index(END, start + 1)
    except ValueError:
        end = len(lines)
    selected = set()
    for line in lines[start + 1 : end]:
        path = path_from_negation(line.strip())
        if path:
            selected.add(path)
    user_lines = lines[:start] + lines[end + 1 :]
    return Parsed(user_lines, selected)


def absorb_plain_negations(user_lines: list[str]) -> tuple[list[str], set[str]]:
    """When enabling management, adopt the user's own '!/path' whitelist lines."""
    kept, absorbed = [], set()
    for line in user_lines:
        path = path_from_negation(line.strip())
        if path:
            absorbed.add(path)
        else:
            kept.append(line)
    return kept, absorbed


def render(user_lines: list[str], selected: set[str]) -> list[str]:
    kept = [l for l in user_lines if l.strip() not in _CATCH_ALL_FORMS]
    # Trim trailing blank lines so the block sits snugly.
    while kept and not kept[-1].strip():
        kept.pop()
    block = [BEGIN]
    block += [negation_for(p) for p in sorted(selected, key=str.casefold)]
    block += [CATCH_ALL, END]
    return kept + ([""] if kept else []) + block


def normalize_selection(selected) -> set[str]:
    """Drop paths whose ancestor is also selected; an ancestor already covers them."""
    cleaned = {p.strip("/") for p in selected if p and p.strip("/")}
    result = set()
    for p in cleaned:
        parts = p.split("/")
        if any("/".join(parts[:i]) in cleaned for i in range(1, len(parts))):
            continue
        result.add(p)
    return result


def is_covered(path: str, selected: set[str]) -> bool:
    parts = path.split("/")
    return any("/".join(parts[:i]) in selected for i in range(1, len(parts) + 1))


def has_selected_descendant(path: str, selected: set[str]) -> bool:
    prefix = path + "/"
    return any(p.startswith(prefix) for p in selected)

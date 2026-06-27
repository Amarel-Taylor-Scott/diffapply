"""Parse the two edit formats LLMs actually emit.

  (A) Aider-style SEARCH/REPLACE blocks::

          path/to/file.py
          <<<<<<< SEARCH
          old code
          =======
          new code
          >>>>>>> REPLACE

  (B) unified diffs::

          --- a/path/to/file.py
          +++ b/path/to/file.py
          @@ -1,3 +1,4 @@
           context
          -removed
          +added

Parsing is tolerant on purpose: markers may carry trailing words and use 5+
fence chars, blank context lines may have lost their leading space, and paths
may keep their ``a/``/``b/`` prefixes or a trailing timestamp. We never raise
on malformed input — we recover what we can and let :mod:`diffapply.apply`
decide what is safe to write.
"""

from __future__ import annotations

from typing import NamedTuple


# --------------------------------------------------------------------------- #
# data types
# --------------------------------------------------------------------------- #

class Block(NamedTuple):
    """One SEARCH/REPLACE edit. Empty ``search`` means create/overwrite ``file``."""
    file: str
    search: str
    replace: str


class Hunk(list):
    """A unified-diff hunk: a ``list`` of ``(tag, line)`` pairs (tag is one of
    ``' '`` context, ``'-'`` removed, ``'+'`` added) plus the 1-based ``@@``
    old-side start line, so iteration yields the pairs and ``.old_start`` gives
    the anchor."""

    def __init__(self, pairs=(), old_start: int = 1):
        super().__init__(pairs)
        self.old_start = old_start


class FilePatch:
    """A unified diff targeting a single ``path`` as an ordered list of ``hunks``."""

    def __init__(self, path: str, hunks=None):
        self.path = path
        self.hunks = hunks if hunks is not None else []

    def __repr__(self):  # pragma: no cover - debug aid only
        return "FilePatch(path=%r, hunks=%d)" % (self.path, len(self.hunks))


# --------------------------------------------------------------------------- #
# format detection
# --------------------------------------------------------------------------- #

def detect_format(text: str) -> str:
    """Return ``'search-replace'``, ``'unified'``, or ``'unknown'``.

    A ``<<<<<<<`` SEARCH fence is decisive (it never appears in a unified diff),
    so we check it first; otherwise a ``@@``/``---``/``+++``/``diff --git`` line
    means a unified diff.
    """
    has_unified = False
    for line in text.splitlines():
        if _is_fence(line, "<"):
            return "search-replace"
        if (line.startswith("@@ ") or line.startswith("--- ")
                or line.startswith("+++ ") or line.startswith("diff --git")):
            has_unified = True
    return "unified" if has_unified else "unknown"


# --------------------------------------------------------------------------- #
# SEARCH/REPLACE blocks
# --------------------------------------------------------------------------- #

def parse_search_replace(text: str) -> list:
    """Parse every Aider-style SEARCH/REPLACE block in ``text`` into ``Block``s."""
    lines = text.splitlines()
    blocks = []
    n = len(lines)
    i = 0
    while i < n:
        if not _is_fence(lines[i], "<"):
            i += 1
            continue

        fname = _filename_before(lines, i)
        i += 1  # step past "<<<<<<< SEARCH"

        # everything up to "=======" is the search text
        search = []
        while i < n and not _is_fence(lines[i], "=") \
                and not _is_fence(lines[i], ">") and not _is_fence(lines[i], "<"):
            search.append(lines[i])
            i += 1
        if i < n and _is_fence(lines[i], "="):
            i += 1  # step past the "=======" divider

        # everything up to ">>>>>>> REPLACE" is the replacement text
        replace = []
        while i < n and not _is_fence(lines[i], ">") and not _is_fence(lines[i], "<"):
            replace.append(lines[i])
            i += 1
        if i < n and _is_fence(lines[i], ">"):
            i += 1  # step past ">>>>>>> REPLACE"

        blocks.append(Block(fname, "\n".join(search), "\n".join(replace)))
    return blocks


def _is_fence(line: str, ch: str) -> bool:
    """True if ``line`` is a marker: 5+ of ``ch`` then nothing or whitespace.

    Tolerates leading indentation and trailing words, so ``<<<<<<< SEARCH`` and
    a bare ``<<<<<<<`` both match for ``ch == '<'``.
    """
    s = line.strip()
    run = 0
    while run < len(s) and s[run] == ch:
        run += 1
    if run < 5:
        return False
    rest = s[run:]
    return rest == "" or rest[0].isspace()


def _filename_before(lines: list, idx: int) -> str:
    """Find the filename for the SEARCH fence at ``lines[idx]``.

    It is the nearest non-empty line above the fence; a wrapping ```` ``` ````
    code fence is skipped, but a path written *on* that fence is honored.
    """
    j = idx - 1
    while j >= 0:
        s = lines[j].strip()
        if not s:
            j -= 1
            continue
        if s.startswith("```") or s.startswith("~~~"):
            on_fence = _path_on_fence(s)
            if on_fence:
                return _clean_filename(on_fence)
            j -= 1  # a bare language fence — the name is above it
            continue
        return _clean_filename(s)
    return ""


def _path_on_fence(fence: str) -> str:
    """Pull a path token off a fence line like ```` ```python:foo.py ```` ."""
    info = fence.lstrip("`~").strip().replace(":", " ")
    for tok in info.split():
        if "/" in tok or "." in tok:  # looks like a path, not a language name
            return tok
    return ""


def _clean_filename(s: str) -> str:
    """Strip wrapping backticks and a trailing colon some tools add to the name."""
    s = s.strip().strip("`").strip()
    if s.endswith(":"):
        s = s[:-1].rstrip()
    return s


# --------------------------------------------------------------------------- #
# unified diffs
# --------------------------------------------------------------------------- #

def parse_unified_diff(text: str) -> list:
    """Parse ``text`` into a list of :class:`FilePatch` (one per file)."""
    lines = text.splitlines()
    patches = []
    n = len(lines)
    i = 0
    old_path = None
    new_path = None
    cur = None
    while i < n:
        line = lines[i]

        if line.startswith("diff --git"):
            # "diff --git a/foo b/foo" — note the paths, defer the FilePatch
            # until we see +++ (or the first @@) so renames resolve correctly.
            parts = line.split()
            if len(parts) >= 4:
                old_path = _strip_path(parts[-2])
                new_path = _strip_path(parts[-1])
            else:
                old_path = new_path = None
            cur = None
            i += 1
            continue

        if line.startswith("--- "):
            old_path = _strip_path(line[4:])
            i += 1
            continue

        if line.startswith("+++ "):
            new_path = _strip_path(line[4:])
            cur = FilePatch(_target_path(old_path, new_path))
            patches.append(cur)
            i += 1
            continue

        if line.startswith("@@"):
            old_start, old_count, _new_start, new_count = _parse_hunk_header(line)
            i += 1
            body, i = _collect_hunk(lines, i, old_count, new_count)
            if cur is None:  # a hunk with no header — best-effort target
                cur = FilePatch(_target_path(old_path, new_path))
                patches.append(cur)
            cur.hunks.append(Hunk(body, old_start))
            continue

        i += 1
    return patches


def _collect_hunk(lines: list, i: int, old_count, new_count):
    """Collect one hunk's ``(tag, line)`` pairs; return ``(pairs, next_index)``.

    When the ``@@`` header gives line counts we stop exactly when both sides are
    satisfied (so trailing prose after the hunk is never swallowed); otherwise
    we read until the next header. A wholly empty line is treated as a blank
    context line, since LLMs routinely drop the leading space on those.
    """
    body = []
    n = len(lines)
    old_seen = new_seen = 0
    while i < n:
        l = lines[i]
        if (l.startswith("@@") or l.startswith("--- ")
                or l.startswith("+++ ") or l.startswith("diff --git")):
            break
        if (old_count is not None and new_count is not None
                and old_seen >= old_count and new_seen >= new_count):
            break

        if l == "":
            body.append((" ", ""))
            old_seen += 1
            new_seen += 1
        else:
            tag, rest = l[0], l[1:]
            if tag == " ":
                body.append((" ", rest))
                old_seen += 1
                new_seen += 1
            elif tag == "-":
                body.append(("-", rest))
                old_seen += 1
            elif tag == "+":
                body.append(("+", rest))
                new_seen += 1
            elif tag == "\\":
                pass  # "\ No newline at end of file" — informational, skip
            else:
                break  # not a diff-body line, so the hunk has ended
        i += 1
    return body, i


def _parse_hunk_header(line: str):
    """Parse ``@@ -a,b +c,d @@`` → ``(old_start, old_count, new_start, new_count)``.

    A missing count defaults to 1 (unified-diff convention). On any parse failure
    we return counts of ``None`` so the caller falls back to prefix-based reading.
    """
    try:
        s = line.strip()
        if s.startswith("@@"):
            s = s[2:]
        if "@@" in s:  # drop a trailing section heading after the closing @@
            s = s[:s.index("@@")]
        old_spec = new_spec = ""
        for tok in s.split():
            if tok.startswith("-"):
                old_spec = tok[1:]
            elif tok.startswith("+"):
                new_spec = tok[1:]
        os_, oc = _range(old_spec)
        ns_, nc = _range(new_spec)
        return os_, oc, ns_, nc
    except Exception:
        return 1, None, 1, None


def _range(spec: str):
    """``"12,7"`` → ``(12, 7)``; ``"12"`` → ``(12, 1)``; ``""`` → ``(1, None)``."""
    if not spec:
        return 1, None
    if "," in spec:
        start, count = spec.split(",", 1)
        return int(start), int(count)
    return int(spec), 1


def _strip_path(raw: str) -> str:
    """Normalize a diff-header path: drop ``a/``/``b/`` and any trailing tab info."""
    p = raw.strip()
    if "\t" in p:  # git/diff may append "\t<timestamp>"
        p = p.split("\t", 1)[0].strip()
    if p == "/dev/null":
        return p
    for pre in ("a/", "b/", "./"):
        if p.startswith(pre):
            return p[len(pre):]
    return p


def _target_path(old_path, new_path) -> str:
    """The file a patch writes to: the new path, or the old path on deletion."""
    if new_path and new_path != "/dev/null":
        return new_path
    if old_path and old_path != "/dev/null":
        return old_path
    return ""

"""Apply parsed edits to text — and, at the top level, to files on disk.

Everything here is done on in-memory strings first; the caller decides whether
to write. The cardinal rule is **never corrupt a file on a failed match**: if a
search string or a diff hunk does not locate its anchor, the original content is
returned untouched and the result is flagged ``ok=False``.

Matching is layered, exact-first then tolerant:
  * SEARCH/REPLACE: exact substring, then a whitespace-insensitive line match.
  * unified hunks: an exact contiguous run, then a ``str.strip()`` comparison,
    searched outward from the line the ``@@`` header points at.
"""

from __future__ import annotations

import os

from . import parse


# --------------------------------------------------------------------------- #
# SEARCH/REPLACE on text
# --------------------------------------------------------------------------- #

def apply_search_replace_to_text(content: str, block):
    """Apply one ``Block`` to ``content``.

    Returns ``(new_content, ok, detail)``. An empty ``block.search`` means
    create/overwrite, so ``block.replace`` is returned wholesale.
    """
    if block.search == "":
        return block.replace, True, "created/overwritten"

    # 1. exact substring replace of the first occurrence
    idx = content.find(block.search)
    if idx != -1:
        new = content[:idx] + block.replace + content[idx + len(block.search):]
        return new, True, "exact match"

    # 2. whitespace-tolerant line match
    fuzzy = _fuzzy_line_replace(content, block.search, block.replace)
    if fuzzy is not None:
        return fuzzy, True, "fuzzy match"

    return content, False, "search not found"


def _fuzzy_line_replace(content: str, search: str, replace: str):
    """Find ``search`` ignoring per-line leading/trailing whitespace, and swap in
    ``replace`` verbatim. Returns the new content, or ``None`` if not found."""
    search_lines = search.splitlines()
    if not search_lines:
        return None
    content_lines = content.splitlines()
    target = [s.strip() for s in search_lines]
    m = len(target)
    for i in range(len(content_lines) - m + 1):
        if [content_lines[i + k].strip() for k in range(m)] == target:
            new_lines = content_lines[:i] + replace.splitlines() + content_lines[i + m:]
            out = "\n".join(new_lines)
            if content.endswith("\n"):
                out += "\n"
            return out
    return None


# --------------------------------------------------------------------------- #
# unified diffs on text
# --------------------------------------------------------------------------- #

def _find_block(lines: list, old_lines: list, hint: int):
    """Index where ``old_lines`` occurs contiguously in ``lines``, or ``None``.

    Searches outward from ``hint`` (the ``@@`` anchor), exact first then a
    ``str.strip()`` comparison. An empty ``old_lines`` is a pure insertion, so
    we return the clamped ``hint`` itself.
    """
    n = len(lines)
    if not old_lines:
        return min(max(hint, 0), n)
    m = len(old_lines)
    if m > n:
        return None
    last = n - m
    hint = min(max(hint, 0), last)
    for eq in (_eq_exact, _eq_strip):
        for idx in _outward(hint, 0, last):
            if all(eq(lines[idx + k], old_lines[k]) for k in range(m)):
                return idx
    return None


def _outward(center: int, lo: int, hi: int):
    """Yield indices in ``[lo, hi]`` ordered by distance from ``center``."""
    if hi < lo:
        return
    center = min(max(center, lo), hi)
    yield center
    d = 1
    while center - d >= lo or center + d <= hi:
        if center - d >= lo:
            yield center - d
        if center + d <= hi:
            yield center + d
        d += 1


def _eq_exact(a: str, b: str) -> bool:
    return a == b


def _eq_strip(a: str, b: str) -> bool:
    return a.strip() == b.strip()


def apply_hunk(lines: list, hunk):
    """Apply one hunk to a list of lines; return the new list, or ``None`` if the
    context could not be located."""
    old = [line for tag, line in hunk if tag in " -"]
    new = [line for tag, line in hunk if tag in " +"]
    idx = _find_block(lines, old, hunk.old_start - 1)
    if idx is None:
        return None
    return lines[:idx] + new + lines[idx + len(old):]


def apply_unified_to_text(content: str, filepatch):
    """Apply every hunk in ``filepatch`` to ``content`` in order.

    Returns ``(new_content, ok, detail)``. If any hunk fails to match, the
    original ``content`` is returned unchanged and ``ok`` is ``False`` — we never
    write a partial result.
    """
    had_newline = content.endswith("\n")
    lines = content.splitlines()
    for n, hunk in enumerate(filepatch.hunks, start=1):
        result = apply_hunk(lines, hunk)
        if result is None:
            return content, False, "hunk %d failed" % n
        lines = result
    new_content = "\n".join(lines)
    if had_newline:
        new_content += "\n"
    return new_content, True, "applied %d hunk(s)" % len(filepatch.hunks)


# --------------------------------------------------------------------------- #
# high-level: apply a whole patch to files under a root
# --------------------------------------------------------------------------- #

def apply(text: str, root: str = ".", dry_run: bool = False) -> dict:
    """Detect, parse, and apply an LLM-emitted patch to files under ``root``.

    Returns a report::

        {"format": ..., "results": [{path, ok, detail, changed}, ...],
         "applied": K, "failed": M}

    A file is written only when ``not dry_run`` and the edit both succeeded and
    actually changed the content. Failed edits never touch disk.
    """
    fmt = parse.detect_format(text)
    results = []

    if fmt == "search-replace":
        for block in parse.parse_search_replace(text):
            results.append(_apply_one_sr(block, root, dry_run))
    elif fmt == "unified":
        for patch in parse.parse_unified_diff(text):
            results.append(_apply_one_unified(patch, root, dry_run))

    applied = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    return {"format": fmt, "results": results, "applied": applied, "failed": failed}


def _apply_one_sr(block, root: str, dry_run: bool) -> dict:
    if not block.file:
        return _result(None, False, "no filename for block", False)
    full = os.path.join(root, block.file)
    original = _read(full) if os.path.exists(full) else ""
    new_content, ok, detail = apply_search_replace_to_text(original, block)
    changed = ok and new_content != original
    if ok and changed and not dry_run:
        _write(full, new_content)
    return _result(block.file, ok, detail, changed)


def _apply_one_unified(patch, root: str, dry_run: bool) -> dict:
    if not patch.path or patch.path == "/dev/null":
        return _result(patch.path, False, "no target path", False)
    full = os.path.join(root, patch.path)
    original = _read(full) if os.path.exists(full) else ""
    new_content, ok, detail = apply_unified_to_text(original, patch)
    changed = ok and new_content != original
    if ok and changed and not dry_run:
        _write(full, new_content)
    return _result(patch.path, ok, detail, changed)


def _result(path, ok: bool, detail: str, changed: bool) -> dict:
    return {"path": path, "ok": ok, "detail": detail, "changed": changed}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)  # support creating files in new subdirs
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

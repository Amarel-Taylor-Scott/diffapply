"""UNG node adapters for diffapply — pure, JSON-in/JSON-out wrappers.

Each function wraps a documented diffapply API with JSON-serializable inputs
and outputs only (dict/list/str/int/float/bool/None); Block namedtuples and
FilePatch/Hunk objects become dicts. The file-writing ``apply()`` is mirrored
over an in-memory {path: content} mapping with identical per-file semantics
(missing file reads as "", failed edits leave content untouched) — nothing is
written to disk. Multi-output nodes return a dict keyed by output name.
No I/O, no network, no filesystem access.
"""

from __future__ import annotations

from .apply import (
    apply_search_replace_to_text as _apply_sr_text,
    apply_unified_to_text as _apply_unified_text,
)
from .parse import (
    detect_format as _detect_format,
    parse_search_replace as _parse_search_replace,
    parse_unified_diff as _parse_unified_diff,
)


def detect_format(patch: str) -> str:
    """Classify a patch as 'search-replace', 'unified', or 'unknown'."""
    return _detect_format(patch)


def parse_search_replace(patch: str) -> list:
    """Parse SEARCH/REPLACE blocks into [{"file", "search", "replace"}]."""
    return [{"file": b.file, "search": b.search, "replace": b.replace}
            for b in _parse_search_replace(patch)]


def parse_unified_diff(patch: str) -> list:
    """Parse a unified diff into [{"path", "hunks": [{"old_start", "lines"}]}]."""
    return [
        {"path": p.path,
         "hunks": [{"old_start": h.old_start,
                    "lines": [[tag, line] for tag, line in h]}
                   for h in p.hunks]}
        for p in _parse_unified_diff(patch)
    ]


def _report(files: dict, results: list) -> dict:
    applied = sum(1 for r in results if r["ok"])
    return {"files": files, "results": results,
            "applied": applied, "failed": len(results) - applied}


def apply_search_replace(files: dict, patch: str) -> dict:
    """Apply SEARCH/REPLACE blocks to a {path: content} map; never corrupts."""
    new_files = dict(files)
    results = []
    for block in _parse_search_replace(patch):
        if not block.file:
            results.append({"path": None, "ok": False,
                            "detail": "no filename for block", "changed": False})
            continue
        original = new_files.get(block.file, "")
        new_content, ok, detail = _apply_sr_text(original, block)
        changed = ok and new_content != original
        if ok and changed:
            new_files[block.file] = new_content
        results.append({"path": block.file, "ok": ok,
                        "detail": detail, "changed": changed})
    return _report(new_files, results)


def apply_unified_diff(files: dict, patch: str) -> dict:
    """Apply unified-diff hunks to a {path: content} map; never corrupts."""
    new_files = dict(files)
    results = []
    for filepatch in _parse_unified_diff(patch):
        if not filepatch.path or filepatch.path == "/dev/null":
            results.append({"path": filepatch.path, "ok": False,
                            "detail": "no target path", "changed": False})
            continue
        original = new_files.get(filepatch.path, "")
        new_content, ok, detail = _apply_unified_text(original, filepatch)
        changed = ok and new_content != original
        if ok and changed:
            new_files[filepatch.path] = new_content
        results.append({"path": filepatch.path, "ok": ok,
                        "detail": detail, "changed": changed})
    return _report(new_files, results)


def apply_patch(files: dict, patch: str) -> dict:
    """Detect the patch format and apply it to a {path: content} map."""
    fmt = _detect_format(patch)
    if fmt == "search-replace":
        report = apply_search_replace(files, patch)
    elif fmt == "unified":
        report = apply_unified_diff(files, patch)
    else:
        report = _report(dict(files), [])
    report["format"] = fmt
    return report


_TAGS = ["license.mit", "runtime.python", "dependency-free"]

_PATCH_IN = {"name": "patch", "type_id": "amarel.types.text",
             "description": "The LLM-emitted patch text."}
_FILES_IN = {"name": "files", "type_id": "amarel.types.files",
             "description": "{path: content} map of the files to edit."}

_APPLY_OUT = [
    {"name": "files", "type_id": "amarel.types.files",
     "description": "The updated {path: content} map (failed edits untouched)."},
    {"name": "results", "type_id": "amarel.types.records",
     "description": "Per-edit {path, ok, detail, changed} results."},
    {"name": "applied", "type_id": "amarel.types.number",
     "description": "Count of edits that matched cleanly."},
    {"name": "failed", "type_id": "amarel.types.number",
     "description": "Count of edits that did not match."},
]


def _node(fn, action, caps, summary, inputs, outputs):
    return {
        "fn": fn,
        "id": "amarel.diffapply." + action,
        "capabilities": caps,
        "summary": summary,
        "inputs": inputs,
        "outputs": outputs,
        "parameters": [],
        "effects": [],
        "determinism": "deterministic",
        "idempotency": "idempotent",
        "tags": _TAGS,
    }


NODES = [
    _node(detect_format, "detect-format", ["diff.detect-format"],
          "Classify an LLM-emitted patch as SEARCH/REPLACE blocks, a unified "
          "diff, or unknown.",
          [_PATCH_IN],
          [{"name": "format", "type_id": "amarel.types.text",
            "description": "'search-replace' | 'unified' | 'unknown'."}]),
    _node(parse_search_replace, "parse-search-replace", ["diff.parse"],
          "Parse Aider-style SEARCH/REPLACE blocks (tolerant of fences and "
          "marker noise) into structured edits.",
          [_PATCH_IN],
          [{"name": "blocks", "type_id": "amarel.types.records",
            "description": "Each block as {file, search, replace}."}]),
    _node(parse_unified_diff, "parse-unified-diff", ["diff.parse"],
          "Parse a unified diff (a/ b/ prefixes and git headers tolerated) "
          "into per-file hunk lists.",
          [_PATCH_IN],
          [{"name": "patches", "type_id": "amarel.types.records",
            "description": "Each file as {path, hunks: [{old_start, lines}]}."}]),
    _node(apply_search_replace, "apply-search-replace", ["diff.apply"],
          "Apply SEARCH/REPLACE blocks to in-memory files: exact match first, "
          "then whitespace-fuzzy; a failed block changes nothing.",
          [_FILES_IN, _PATCH_IN], _APPLY_OUT),
    _node(apply_unified_diff, "apply-unified-diff", ["diff.apply"],
          "Apply unified-diff hunks to in-memory files, anchored at @@ and "
          "matched outward; any failed hunk leaves that file unchanged.",
          [_FILES_IN, _PATCH_IN], _APPLY_OUT),
    _node(apply_patch, "apply-patch", ["diff.apply"],
          "Detect the patch format and apply it to in-memory files, returning "
          "the updated files plus a per-edit report.",
          [_FILES_IN, _PATCH_IN],
          _APPLY_OUT + [{"name": "format", "type_id": "amarel.types.text",
                         "description": "The detected patch format."}]),
]

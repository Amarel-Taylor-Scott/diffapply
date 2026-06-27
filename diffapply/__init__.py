"""diffapply — robustly apply LLM-emitted edits, stdlib only.

Models hand back edits in two shapes: Aider-style ``<<<<<<< SEARCH`` /
``>>>>>>> REPLACE`` blocks, and unified ``diff`` hunks. ``diffapply`` parses
either, matches context tolerantly (exact first, then whitespace-fuzzy), and
applies the edit in memory — never corrupting a file when a hunk fails to match.

    from diffapply import apply
    apply(patch_text, root=".")          # detect format, parse, write changed files
    apply(patch_text, dry_run=True)      # report only, touch nothing
"""

from __future__ import annotations

from .parse import (
    Block, FilePatch, Hunk,
    parse_search_replace, parse_unified_diff, detect_format,
)
from .apply import (
    apply,
    apply_search_replace_to_text,
    apply_unified_to_text,
    apply_hunk,
)

__all__ = [
    "Block", "FilePatch", "Hunk",
    "parse_search_replace", "parse_unified_diff", "detect_format",
    "apply", "apply_search_replace_to_text", "apply_unified_to_text", "apply_hunk",
]
__version__ = "0.1.0"

"""``diffapply`` CLI — apply an LLM-emitted patch from the shell.

    diffapply patch.txt                 # apply edits described in patch.txt
    cat patch.txt | diffapply           # read the patch from stdin
    diffapply patch.txt --root src      # resolve file paths under ./src
    diffapply patch.txt --dry-run       # report what would change, write nothing

A per-file line (✓/✗ path — detail) goes to stderr, then a final ``applied
K/total``. Exit status is non-zero if any file failed (or no edits were found).
"""

from __future__ import annotations

import argparse
import sys

from .apply import apply


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="diffapply",
        description="Apply LLM SEARCH/REPLACE blocks or unified diffs, tolerantly (stdlib only).",
    )
    p.add_argument("file", nargs="?", help="patch file (default: stdin)")
    p.add_argument("--root", default=".",
                   help="resolve file paths under this directory (default: .)")
    p.add_argument("--dry-run", action="store_true",
                   help="report changes without writing any file")
    a = p.parse_args(argv)

    text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
    report = apply(text, root=a.root, dry_run=a.dry_run)
    results = report["results"]

    for r in results:
        mark = "✓" if r["ok"] else "✗"  # ✓ / ✗
        sys.stderr.write("%s %s — %s\n" % (mark, r["path"], r["detail"]))

    if not results:
        sys.stderr.write("diffapply: no edits found (format: %s)\n" % report["format"])
        return 1

    sys.stderr.write("applied %d/%d\n" % (report["applied"], len(results)))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

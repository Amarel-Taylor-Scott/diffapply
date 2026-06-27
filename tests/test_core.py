"""Offline tests for diffapply — tolerant application of LLM edits, no network."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffapply import (  # noqa: E402
    Block,
    parse_search_replace,
    parse_unified_diff,
    detect_format,
    apply,
    apply_search_replace_to_text,
    apply_unified_to_text,
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_parse_search_replace_basic():
    text = (
        "Here is the edit:\n\n"
        "math_utils.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a, b):\n"
        "    return a - b\n"
        "=======\n"
        "def add(a, b):\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse_search_replace(text)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.file == "math_utils.py"
    assert b.search == "def add(a, b):\n    return a - b"
    assert b.replace == "def add(a, b):\n    return a + b"


def test_apply_search_replace_to_temp_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.py")
        _write(p, "def add(a, b):\n    return a - b\n# tail\n")
        patch = (
            "x.py\n<<<<<<< SEARCH\n"
            "    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"
        )
        report = apply(patch, root=d)
        assert report["failed"] == 0
        # exactly the matched region changed; surrounding lines preserved
        assert _read(p) == "def add(a, b):\n    return a + b\n# tail\n"


def test_search_no_match_leaves_file_unchanged():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.py")
        original = "print('hello')\n"
        _write(p, original)
        patch = (
            "x.py\n<<<<<<< SEARCH\n"
            "this line is not present\n=======\nreplacement\n>>>>>>> REPLACE\n"
        )
        report = apply(patch, root=d)
        assert report["failed"] == 1
        assert report["results"][0]["ok"] is False
        assert _read(p) == original  # untouched


def test_whitespace_tolerant_match():
    # the search uses 4-space indent; the file uses 8-space indent
    content = "def f():\n        x = 1\n        return x\n"
    block = Block("f.py", "    x = 1\n    return x", "    x = 2\n    return x")
    new, ok, detail = apply_search_replace_to_text(content, block)
    assert ok
    assert new == "def f():\n    x = 2\n    return x\n"


def test_empty_search_creates_file():
    with tempfile.TemporaryDirectory() as d:
        patch = (
            "sub/newfile.txt\n<<<<<<< SEARCH\n"
            "=======\nhello world\n>>>>>>> REPLACE\n"
        )
        report = apply(patch, root=d)
        assert report["failed"] == 0
        assert report["results"][0]["changed"] is True
        assert _read(os.path.join(d, "sub", "newfile.txt")) == "hello world"


def test_parse_unified_diff_basic():
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "-line2\n"
        "+line2-changed\n"
        "+line2b\n"
        " line3\n"
    )
    patches = parse_unified_diff(diff)
    assert len(patches) == 1
    fp = patches[0]
    assert fp.path == "foo.py"
    assert len(fp.hunks) == 1
    h = fp.hunks[0]
    assert h.old_start == 1
    assert list(h) == [
        (" ", "line1"),
        ("-", "line2"),
        ("+", "line2-changed"),
        ("+", "line2b"),
        (" ", "line3"),
    ]


def test_apply_unified_add_remove():
    content = "line1\nline2\nline3\n"
    diff = (
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,4 @@\n"
        " line1\n-line2\n+line2-changed\n+line2b\n line3\n"
    )
    patches = parse_unified_diff(diff)
    new, ok, detail = apply_unified_to_text(content, patches[0])
    assert ok
    assert new == "line1\nline2-changed\nline2b\nline3\n"


def test_unified_hunk_no_match_unchanged():
    content = "totally\ndifferent\ncontent\n"
    diff = "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n-foo\n+bar\n baz\n"
    patches = parse_unified_diff(diff)
    new, ok, detail = apply_unified_to_text(content, patches[0])
    assert not ok
    assert new == content  # unchanged
    assert "hunk 1 failed" in detail


def test_apply_end_to_end_dry_run_then_write():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "greet.py")
        original = "def greet():\n    return 'hi'\n"
        _write(p, original)
        patch = (
            "greet.py\n<<<<<<< SEARCH\n"
            "    return 'hi'\n=======\n    return 'hello'\n>>>>>>> REPLACE\n"
        )
        # dry run: reports a change but does not modify the file
        rep = apply(patch, root=d, dry_run=True)
        assert rep["results"][0]["ok"] is True
        assert rep["results"][0]["changed"] is True
        assert _read(p) == original  # still unchanged on disk
        # real run: writes the change
        rep2 = apply(patch, root=d, dry_run=False)
        assert rep2["applied"] == 1
        assert _read(p) == "def greet():\n    return 'hello'\n"


def test_detect_format():
    assert detect_format(
        "x.py\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n"
    ) == "search-replace"
    assert detect_format("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n") == "unified"
    assert detect_format("just some prose with no edits") == "unknown"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("\n%d passed" % len(fns))

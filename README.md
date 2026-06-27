# diffapply

> Apply the edits an LLM emits — Aider-style **SEARCH/REPLACE** blocks *and*
> **unified diffs** — **tolerantly and stdlib only.** Fuzzy context match that
> never corrupts a file when a hunk doesn't match.

```python
from diffapply import apply

patch = """math.py
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
"""

apply(patch, root=".")
# {'format': 'search-replace',
#  'results': [{'path': 'math.py', 'ok': True, 'detail': 'exact match', 'changed': True}],
#  'applied': 1, 'failed': 0}
```

No dependencies. No model call. Just a small, format-aware patch applier that
prefers to do nothing over doing the wrong thing.

## The problem

LLMs describe code edits in two different shapes, and both arrive *slightly*
off: indentation drifts, blank context lines lose their leading space, paths
keep their `a/` `b/` prefixes, fences wrap the block. A strict `patch` rejects
all of it. `diffapply` is built to absorb that noise:

| What the model emits | `diffapply` |
|---|---|
| Aider `<<<<<<< SEARCH` / `>>>>>>> REPLACE` blocks | ✓ |
| unified `--- a/f` / `+++ b/f` / `@@` diffs | ✓ |
| `diff --git a/f b/f` headers | ✓ |
| indentation that doesn't quite match the file | ✓ (whitespace-fuzzy) |
| markers with 5+ chars / trailing words / a wrapping ```` ``` ```` fence | ✓ |
| blank context lines missing their leading space | ✓ |
| a hunk whose context is just *wrong* | ✗ → file left **untouched** |

That last row is the point: a failed hunk reports `ok=False` and the file is
never written.

## API

```python
from diffapply import (
    apply, detect_format,
    parse_search_replace, parse_unified_diff,
    apply_search_replace_to_text, apply_unified_to_text, apply_hunk,
    Block, FilePatch, Hunk,
)

apply(text, root=".", dry_run=False)   # detect → parse → write changed files; returns a report
detect_format(text)                    # 'search-replace' | 'unified' | 'unknown'
parse_search_replace(text)             # → [Block(file, search, replace), ...]
parse_unified_diff(text)               # → [FilePatch(path, hunks), ...]
```

The two text-level appliers operate purely in memory — *you* decide whether to
write:

```python
apply_search_replace_to_text(content, block)   # → (new_content, ok, detail)
apply_unified_to_text(content, filepatch)      # → (new_content, ok, detail)
```

`apply()` returns a report dict:

```python
{
  "format": "unified",
  "results": [{"path": "f.py", "ok": True, "detail": "applied 1 hunk(s)", "changed": True}],
  "applied": 1,   # results that matched cleanly
  "failed": 0,    # results that did not
}
```

## CLI

```bash
diffapply patch.txt                 # apply edits described in patch.txt
cat patch.txt | diffapply           # read the patch from stdin
diffapply patch.txt --root src      # resolve file paths under ./src
diffapply patch.txt --dry-run       # report what would change, write nothing
```

It prints a per-file line to stderr (`✓ path — detail` / `✗ path — detail`)
then `applied K/total`, and exits non-zero if any file failed.

## How the matching works (exact-first, then tolerant)

**SEARCH/REPLACE**

1. exact substring replace of the first occurrence of `search`
2. else a whitespace-insensitive line match (compare each line `.strip()`ed),
   swapping in `replace` verbatim
3. else `ok=False`, content unchanged — an empty `search` means create/overwrite

**unified diffs**

1. build the old side (` ` + `-` lines) and new side (` ` + `+` lines)
2. locate the old side as a contiguous run, searched **outward** from the `@@`
   anchor — exact first, then a `str.strip()` comparison
3. splice in the new side; if any hunk can't be located, **the whole file is
   left unchanged** and the result is flagged failed

## Layout

```
diffapply/
  parse.py     SEARCH/REPLACE blocks + unified diffs → Block / FilePatch / Hunk
  apply.py     match & apply on text; high-level apply() over files under a root
  cli.py       the `diffapply` command
  __main__.py  python -m diffapply
```

MIT. Stdlib only — no dependencies, no network, no API keys.

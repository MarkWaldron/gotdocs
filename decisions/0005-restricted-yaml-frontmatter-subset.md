---
id: 0005-restricted-yaml-frontmatter-subset
title: Frontmatter is a restricted YAML subset with a byte-preserving writer
type: decision
summary: Only scalars, quoted scalars, inline flow lists and block lists of scalars are accepted; anything else is a located error, and the writer rewrites only updated and verified_at.
covers:
  - tools/gotdocs/frontmatter.py
  - .gotdocs/schema.json
  - .gotdocs/templates/**
symptoms:
  - nested mappings are not supported; flatten the key
  - block scalars are not supported; use a single line
  - my frontmatter is valid YAML but gotdocs rejects it
  - gotdocs verify changed only two lines and left my comments and quoting alone
  - the error points at a file and line number instead of just failing
  - I cannot put a list of objects in owners
  - a tab in the frontmatter is an error
  - the YAML block in the doc has to sit between two --- lines at the very top
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - format
  - frontmatter
  - parsing
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Frontmatter is a restricted YAML subset with a byte-preserving writer

## Context

Under 0002 there is no PyYAML, so frontmatter has to be parsed by hand. The
tempting move is to write a lenient parser that does its best and moves on. That
is the wrong shape for this data: frontmatter drives whether a commit is blocked
and which documents an agent opens. A silent misparse of `covers` turns a
document into one that covers nothing, and nothing reports it.

There is a second requirement the parser alone does not solve. `bin/gotdocs
verify` writes `updated` and `verified_at` back into the file, and a doc file is
the same object a person is actively editing. A round-trip through a general YAML
serializer reorders keys, drops comments, normalises quotes and rewrites line
endings — turning a two-field stamp into a whole-file diff.

## Decision

Frontmatter is a YAML *subset*, spelled out in `docs/doc-format.md` and
implemented in `tools/gotdocs/frontmatter.py`. Accepted forms:

```yaml
key: scalar                 # unquoted
key: 'single quoted'        # surrounding quotes stripped
key: "double quoted"        # surrounding quotes stripped
key: [a, b, c]              # inline flow list of scalars
key:                        # block list of scalars
  - a
  - b
# full-line comment
```

Everything else — nested maps, lists of maps, block scalars (`|`, `>`), anchors,
aliases, tags, tabs for indentation — is an error with a `file:line` pointer,
never a silent misparse. Parsed values are only ever `str` or `list[str]`.

The writer is byte-preserving. `rewrite_fields` edits only the keys in
`WRITABLE_KEYS = ("updated", "verified_at")`. Every other byte of the file —
key order, comments, quoting style, blank lines, line endings — survives
unchanged.

## Expected behavior

- The block is delimited by `---` on the first line and `---` on a later line.
  A file whose first line is not `---` has no frontmatter, and that is reported
  rather than guessed at.
- Rejections carry a location and a remedy:

  ```console
  $ python3 -c "from tools.gotdocs import frontmatter as fm; \
      print([i.located() for i in fm.parse_text(open('d.md').read(), 'd.md').issues])"
  ["d.md:4: nested mappings are not supported; flatten the key (for example 'owner_name: mark')"]
  ```

  A block scalar reports
  `d.md:3: block scalars ('|' and '>') are not supported; use a single line`,
  and **only that**: the scalar's indented continuation lines belong to the
  construct already reported, so they are not each reported again as a nested
  mapping. Parsing resumes at the next unindented key, which is still checked.
- Accepted input parses to strings and lists of strings only:

  ```pycon
  >>> fm.parse_text("---\nid: x\ntags: [a, b]\nsymptoms:\n  - one\n  - two\n---\n", "d.md").data
  {'id': 'x', 'tags': ['a', 'b'], 'symptoms': ['one', 'two']}
  ```

- `bin/gotdocs verify <id>` changes exactly two lines. `git diff` after a verify
  on an otherwise untouched document shows only `updated:` and `verified_at:`;
  comments, key order and quote style are identical.
- Every parse issue is a `LintIssue(path, line, message)` and is surfaced by
  `bin/gotdocs lint` as a `lint` finding, with `lint` exiting 2 when any exist.
- Parsing never raises for content problems. An unreadable file or a bad encoding
  becomes one issue attached to the document; a malformed key becomes an issue at
  its line. `bin/gotdocs lint` on a repo full of broken docs prints all of them
  in one pass rather than dying on the first.
- `.gotdocs/schema.json` states the same rules in JSON Schema for editors. It
  validates nothing at runtime — there is no `jsonschema` — and says so in its
  `$comment`.

## This is a bug, not this decision, if...

- A construct listed in the accepted table above is mis-parsed. `key: [a, b, c]`
  yielding `['a', ' b', ' c']` with leading spaces, or `key: "a: b"` splitting on
  the inner colon, is a bug in `tools/gotdocs/frontmatter.py`, not this decision.
- A rejected construct is reported *without* a line number when the line is
  knowable. `LintIssue.line` being `None` for a syntax error inside the block is
  a defect; `None` for a whole-file problem (unreadable, no frontmatter at all)
  is correct.
- A rejected construct is accepted silently. Nested maps, block scalars, anchors
  and tabs must each produce an issue; parsing one of them into something
  plausible is worse than failing.
- `bin/gotdocs verify` changes any byte outside the `updated` and `verified_at`
  lines — reorders keys, strips a comment, converts CRLF to LF, adds or removes
  a trailing newline. `rewrite_fields` is supposed to be surgical.
- `bin/gotdocs verify` fails to add `updated`/`verified_at` to a document that
  does not yet have those keys, or adds them in a non-deterministic position.
- A parse issue crashes the run with a traceback instead of landing in
  `Frontmatter.issues`. The contract is that content problems never raise.
- The native validator in `tools/gotdocs/index.py` and `.gotdocs/schema.json`
  disagree about a field's rules. They are two statements of one format and are
  required to be changed in the same commit; a divergence is a bug in whichever
  was not updated.
- Note what is **not** a bug: PyYAML accepting a document that gotdocs rejects.
  That gap is the decision. The fix is to flatten the key, not to widen the
  parser.

## Consequences

Authors occasionally hit a wall — usually wanting structured owners
(`owners: [{name: mark, team: platform}]`) or a multi-line summary. The answer is
always "flatten it" or "shorten it", and that is genuinely annoying the first
time. `summary` in particular is capped at 200 characters with no line
continuation available.

Editor YAML plugins and generic frontmatter tooling will accept things gotdocs
rejects, so a document can look fine in the editor and fail `bin/gotdocs lint`.
`.gotdocs/schema.json` exists to close that gap for editors that support
`yaml.schemas`, but it is opt-in per editor.

`frontmatter.py` is 741 lines of hand-written parser that must be kept in step
with `.gotdocs/schema.json` by discipline alone — there is no test that compares
them mechanically.

## Alternatives considered

- **Full YAML via PyYAML.** Rejected by 0002 (dependency), and independently by
  the round-trip requirement: `yaml.safe_load` + `yaml.dump` destroys comments,
  key order and quoting, turning every `verify` into a whole-file diff.
- **A lenient hand-rolled parser that skips what it does not understand.**
  Rejected: the failure is silent and the blast radius is a document that quietly
  covers nothing. An error with a line number is strictly better than a wrong
  answer.
- **TOML frontmatter.** Rejected: `tomllib` only arrives in Python 3.11, above
  the 3.9 floor, and TOML frontmatter is unrecognised by every markdown renderer
  and static site generator in the target set.
- **JSON frontmatter.** Rejected: unreadable to author by hand, no comments, and
  a trailing-comma error is a common and infuriating failure for a file humans
  edit daily.
- **A separate sidecar metadata file per document.** Rejected: two files to keep
  in sync is the exact drift problem this project exists to fix, one level down.
- **Regex-only extraction with no parser.** Rejected: cannot distinguish a block
  list from a nested map, which is precisely where the dangerous misparse lives.

## Revisit when

Revisit if the 3.11 floor becomes acceptable (making `tomllib` viable for config,
though not for frontmatter) or if a real need for structured values appears in
more than one field. The signal to watch is `extra`-bucket usage in
`index.json`: several repos independently flattening the same structure into
`owner_name` / `owner_team` pairs would argue for a narrow nested-map extension
rather than a general YAML parser.

## References

- `tools/gotdocs/frontmatter.py` — the subset, `WRITABLE_KEYS`, `parse_text`,
  `rewrite_fields`, `LintIssue`.
- `tools/gotdocs/index.py` — the native field validators (`_ID_RE`, `_TAG_RE`,
  `_DATE_RE`, `_SHA_RE`, `REQUIRED_FIELDS`).
- `.gotdocs/schema.json` — the editor-facing statement of the same rules, with
  the `$comment` recording that it is not enforced at runtime.
- `.gotdocs/templates/` — scaffolds that are, by construction, inside the subset.
- `docs/doc-format.md` — the authored reference.
- `tools/gotdocs/tests/test_frontmatter.py` — the executable boundary.

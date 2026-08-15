---
id: 0003-covers-globs-as-the-staleness-signal
title: Staleness is computed from declared covers globs, not from content analysis
type: decision
summary: A doc is impacted when a changed code path matches one of its hand-declared covers globs; there is no content diffing, no LLM judgement and no import graph in the check path.
covers:
  - tools/gotdocs/globs.py
  - tools/gotdocs/check.py
  - .gotdocs/schema.json
symptoms:
  - a doc was marked stale for a change that did not affect what the doc says
  - I renamed a variable and three docs went stale
  - I rewrote a whole module and no doc was reported at all
  - the doc lists covers globs I have to maintain by hand
  - src/* did not match src/a/b.py the way my shell would
  - a doc with an empty covers list is never reported as stale
  - reformatting a file made the docs stale
  - a doc went stale after a whitespace-only change
  - the same change reports different docs on two branches
  - a covers glob that names another document never makes it impacted
  - editing one doc does not mark the doc that covers it stale
  - covers decisions/** matches nothing even though the files are right there
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - architecture
  - staleness
  - globs
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Staleness is computed from declared covers globs, not from content analysis

## Context

The core question is "did this change make this document wrong?". A correct
answer requires understanding both the change and the prose, which means an LLM
call, a per-repo model, or an import-graph analysis per language. All three are
too slow for a pre-commit hook, non-deterministic across machines, and impossible
without network access under 0002.

The cheap approximation is to ask the *document author* to declare which code the
document describes, and then treat "that code changed" as "this document is
suspect". It over-reports and under-reports in known ways, but it is a pure
function of the change set and the frontmatter, it runs in milliseconds, and two
people get the same answer.

## Decision

Each document declares `covers:`, a list of repo-relative glob patterns. A
document is **impacted** when any code path in the change set matches any of its
`covers` patterns. Nothing else contributes to impact: not the diff hunks, not
the doc body, not the language, not the call graph.

The glob dialect is gotdocs' own, compiled to anchored regexes in
`tools/gotdocs/globs.py` and cached. It is deliberately not `fnmatch`, whose `*`
crosses `/`.

## Expected behavior

- `bin/gotdocs impacted <path>` names every document whose `covers` matched, and
  the pattern that matched, so the mapping is auditable:

  ```console
  $ bin/gotdocs impacted tools/gotdocs/globs.py
  tools/gotdocs/globs.py
    0002-python-stdlib-only-cli                decisions/0002-python-stdlib-only-cli.md                (tools/gotdocs/globs.py)
    0003-covers-globs-as-the-staleness-signal  decisions/0003-covers-globs-as-the-staleness-signal.md  (tools/gotdocs/globs.py)
    cli-reference                              docs/cli-reference.md                                   (tools/gotdocs/**)
    doc-format                                 docs/doc-format.md                                      (tools/gotdocs/globs.py)
    gotdocs-architecture                       docs/architecture.md                                    (tools/gotdocs/**)
  ```

- The dialect, verifiable with `tools/gotdocs/globs.py:match(pattern, path)`:

  | pattern | path | result |
  | --- | --- | --- |
  | `src/**` | `src` | False |
  | `src/**` | `src/a.py` | True |
  | `src/**` | `src/a/b/c.py` | True |
  | `src/*` | `src/a.py` | True |
  | `src/*` | `src/a/b.py` | False |
  | `*.py` | `a/b/c.py` | True |
  | `src/` | `src/a/b.py` | True |
  | `src/**.py` | `src/a/b.py` | True |

  That is: `*` never crosses `/`; `**` does; a trailing `/` means the directory
  and everything under it; a pattern with no `/` and no `**` matches the
  basename at any depth; everything else is anchored at the repository root.
- Impact is content-blind. Reformatting a covered file, changing a comment, or
  changing one character all produce the same finding as a rewrite:

  ```console
  $ bin/gotdocs check --paths tools/gotdocs/globs.py
  gotdocs: 5 findings (mode: warn)

  stale (5)
    decisions/0002-python-stdlib-only-cli.md  [0002-python-stdlib-only-cli]
      tools/gotdocs/globs.py changed and is covered by tools/gotdocs/globs.py
      -> update decisions/0002-python-stdlib-only-cli.md, or run: bin/gotdocs verify 0002-python-stdlib-only-cli
    ...
    docs/architecture.md  [gotdocs-architecture]
      tools/gotdocs/globs.py changed and is covered by tools/gotdocs/**
      -> update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture
  ```

  One count for both commands: every document `impacted` names is reported by
  `check`, and this record covers `tools/gotdocs/globs.py` itself, so it is in
  its own worked example.

- A document with `covers: []` is never impacted by any change. That is the
  correct configuration for a glossary or a policy that describes no specific
  code.
- **A `covers` entry that names a path inside a doc root can never fire.** Step 2
  of `check` splits the change set into ignored paths, *doc* paths and code
  paths, and only code paths are matched against `covers`. A file under a
  configured root (`docs/`, `runbooks/`, `decisions/`, …) is classified as a doc
  path, so `covers: decisions/**` on a decision record, or
  `covers: docs/agent-workflow.md` on another doc, is inert — it matches
  nothing, forever, and no error is raised. One document's edits cannot make
  another document stale. Verify with
  `bin/gotdocs impacted docs/agent-workflow.md --json`: `"doc_path": true` and
  an empty `docs` list. Paths under `.gotdocs/` are *not* doc paths and do work
  normally, except the ones the default `ignore` list excludes
  (`.gotdocs/index.json`, `.gotdocs/INDEX.md`).
- The answer depends only on the changed path list and the frontmatter. Running
  the same command on another machine, at another time, with a different
  interpreter, produces the same findings in the same order.
- Patterns are validated: a leading `/`, a leading `./`, a backslash separator,
  leading or trailing whitespace, brace expansion `{a,b}` and leading `!`
  negation are all outside the dialect and are reported by `bin/gotdocs lint`.

## This is a bug, not this decision, if...

- `bin/gotdocs impacted src/a/b.py` returns nothing while a document declares
  `covers: src/**`. That is a bug in `tools/gotdocs/globs.py`, not this decision.
- `bin/gotdocs impacted` names a document whose `covers` patterns do **not**
  match the path — check the parenthesised pattern in the output; if it does not
  actually match under `globs.match(pattern, path)`, the compiler is wrong.
- `*` crosses a `/` — `src/*` matching `src/a/b.py` — or `**` fails to cross one.
  Both are covered by the table above and by `tools/gotdocs/tests/test_globs.py`.
- A path in the change set that matches an `ignore` glob still produces
  findings, or a path that does not match one is silently dropped. Splitting the
  change set is `tools/gotdocs/check.py`, step 2.
- A syntactically invalid `covers` pattern is accepted silently rather than
  reported by `bin/gotdocs lint`, or a valid one is rejected. Validation is
  `globs.validate_pattern`.
- The same change set produces different findings on two runs, or in a different
  order. Findings are sorted by `Finding.sort_key()`; non-determinism there is a
  bug.
- A `covers` glob that matches nothing anywhere in the repository is *not*
  reported by `bin/gotdocs audit` as rotted. Over-reporting is this decision;
  a glob pointing at a deleted directory going unnoticed is a gap in the audit.
- Note what is **not** a bug: being reported stale for a whitespace-only change
  to a covered file, and *not* being reported for a change to a file no glob
  names. Those are this decision working as designed — narrow the glob, or widen
  it, respectively.
- Also **not** a bug: a `covers` entry naming another document (or a directory
  of documents) matching nothing. That is the doc-path exclusion above. Delete
  the entry; do not "fix the glob".

## Consequences

False positives are the daily cost. A `covers: tools/gotdocs/**` on an
architecture doc means every touch anywhere in the package reports it. The
mitigation is not a smarter matcher — it is narrower globs plus
`bin/gotdocs verify` (0006) as the cheap "I read it, it is still true"
acknowledgement.

False negatives are the quiet cost, and the more dangerous one: a document whose
`covers` is empty, wrong, or points at a directory that has since been renamed is
silently never impacted again. Nothing in the check path can detect that; it
takes `bin/gotdocs audit` looking for globs that match zero files.

`covers` is hand-maintained metadata, so it rots like any other. Moving a
directory requires editing every doc that named it, and nothing forces that edit
at move time.

## Alternatives considered

- **LLM-judged staleness ("does this diff invalidate this prose?").** Rejected:
  needs a network call and an API key in a pre-commit hook, is non-deterministic,
  costs money per commit, and cannot be reproduced in CI on a fork. Kept for the
  *resolution* step instead — `/gotdocs-update` reads the real diff — where
  latency and non-determinism are acceptable.
- **Import-graph / AST analysis to derive coverage automatically.** Rejected:
  one implementation per language, and it answers a different question (what code
  reaches this symbol) than the one asked (what does this prose describe).
  Runbooks and onboarding docs have no import edges at all.
- **Content hashing of the covered files, stored in frontmatter.** Rejected:
  identical false-positive profile to globs (any byte change trips it) with a
  much worse diff — every doc's frontmatter churns on every commit.
- **`fnmatch` / shell globbing.** Rejected: `fnmatch`'s `*` crosses `/`, so
  `src/*` would match `src/a/b/c.py` and every narrow glob would silently behave
  like a wide one.
- **Directory-level ownership (docs own a directory, implicitly).** Rejected:
  cannot express a doc covering three files across two trees, which is the
  common case for a runbook.

## Revisit when

Revisit if `bin/gotdocs audit` starts reporting a large share of `covers` globs
matching zero files (indicating hand-maintenance has failed at scale), or if the
false-positive rate makes `verify` a reflex rather than a judgement — the signal
for that is a debt ledger where `stale` entries are almost always resolved by a
`verify` with no accompanying doc edit.

## References

- `tools/gotdocs/globs.py` — the dialect, `compile_pattern`, `validate_pattern`,
  `match`, `match_any`, `matching_patterns`.
- `tools/gotdocs/check.py` — steps 2 and 3 of the core rule, `impacted_for_paths`.
- `docs/doc-format.md#glob-dialect` — the authored reference for the dialect.
- `.gotdocs/schema.json` — the `covers` property and its `$comment` restating the
  dialect for editor integration.
- `tools/gotdocs/tests/test_globs.py` — the executable statement of the table
  above.

---
id: doc-format
title: Doc Format and Frontmatter Reference
type: doc
summary: Every frontmatter field, what covers means, how to pick good covers globs, the glob dialect, and the limits of the supported YAML subset.
covers:
  - .gotdocs/schema.json
  - .gotdocs/templates/**
  - tools/gotdocs/frontmatter.py
  - tools/gotdocs/globs.py
  - tools/gotdocs/index.py
owners: ["@mark"]
tags: [frontmatter, reference, globs, yaml]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Doc Format and Frontmatter Reference

Every file under a configured root (`docs/`, `runbooks/`, `onboarding/`,
`dependencies/`, `decisions/`) must begin with YAML frontmatter delimited by
`---` on its own line, before any other content — no blank lines, no BOM, no
HTML comment above it.

```yaml
---
id: gotdocs-cli
title: Gotdocs CLI
type: doc
summary: One sentence, <= 200 chars. This is what lands in the agent index.
covers:
  - tools/gotdocs/**
  - bin/gotdocs
owners: ["@mark"]
tags: [cli, tooling]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---
```

Scaffold a correct one with `bin/gotdocs new doc my-id --title "My Title" --covers 'src/foo/**'`
rather than copying by hand.

## Fields

| Field | Required | Type | Rules |
| --- | --- | --- | --- |
| `id` | yes | string | Unique repo-wide. Kebab-case, must match `[a-z0-9][a-z0-9-]*`, at most 64 chars. This is the handle used by `gotdocs verify <id>` and by every finding. |
| `title` | yes | string | Human title, at most 120 chars. Carried in `index.json` and used by SSGs. `INDEX.md` lists the `id` and `summary` instead, to stay one line per doc. |
| `type` | yes | enum | `doc` \| `runbook` \| `onboarding` \| `dependency`. Groups the index. |
| `summary` | yes | string | One sentence, at most `max_summary_chars` (default 200). This is the line an agent reads in `INDEX.md` when deciding whether to open the file. Write it so that decision is possible. |
| `covers` | yes | list of globs | Repo-relative glob patterns naming the code this doc describes. Entries must be unique and must not start with `/` or `./`. May be an empty list. Drives everything. |
| `status` | yes | enum | `current` \| `draft` \| `deprecated`. |
| `updated` | yes | date | `YYYY-MM-DD`. Rewritten by `gotdocs verify`. |
| `owners` | no | list of strings | Convention is `@handle`. Used in findings so the reviewer knows who to ask. |
| `tags` | no | list of strings | Lowercase keywords matching `[a-z0-9][a-z0-9._-]*`, unique. Appears in `index.json`. |
| `verified_at` | no | string | Short or long git sha at which `covers` was last reviewed. Absent means "never verified"; every impact is then stale until someone edits or verifies. |
| anything else | no | scalar or list | Kept untouched in the file and carried into `index.json`'s `extra`. **Not** copied into an exported page: `bin/gotdocs export` maps frontmatter per target and writes everything else to `_gotdocs.json`. Do not hand-write generator keys like `sidebar_position` — `export` derives them and `lint --portability` reports them as reserved. Use `extra` for repo-local metadata (a ticket id, a review cadence, a team code). |

### `status` semantics

- `current` — normal. Participates in impact checking.
- `draft` — participates in impact checking exactly like `current`. It is **not**
  a lint error (migrating a repo into gotdocs produces drafts by the dozen, and a
  red `lint` would be useless then). It is counted separately by
  `bin/gotdocs status` (`docs 13 (11 current, 2 draft, 0 deprecated)`) and is one
  of the gaps `/gotdocs-audit` reports, which is where half-written docs get
  found.
- `deprecated` — still indexed, but editing it produces a `deprecated_edit`
  finding: you are spending effort on a doc that is supposed to be going away.
  Delete it or flip it back to `current`.

## What `covers` means

`covers` is a claim: *"if any of these files change, the statements in this
document may no longer be true."*

It is not "files that reference this doc" and it is not a build dependency. It is
the blast radius of the prose. `check` uses it, and only it, to decide whether the
doc is impacted by a change set:

```text
changed code path  --matches-->  covers pattern  =>  doc is impacted
impacted AND (doc not edited) AND (verified_at != head sha)  =>  doc is stale
```

Paths inside a doc root are never treated as code paths, so one doc's `covers`
cannot make another doc impacted.

### Choosing good `covers` globs

The failure modes are symmetric and both are expensive:

- **Too broad** (`src/**` on a general architecture doc) — the doc is impacted by
  every commit. Engineers learn that gotdocs findings are noise and start using
  the skip token. This is the failure mode that kills adoption.
- **Too narrow** (a single file when the doc describes a subsystem) — the doc
  rots silently, which is the problem gotdocs exists to prevent.

Rules that hold up in practice:

1. **Cover the interface you documented, not the tree it lives in.** A doc about
   the HTTP API covers `src/api/routes/**` and `src/api/schema.py`, not `src/**`.
2. **Prefer several precise patterns over one wide one.** `covers` is a list; use it.
3. **If a doc would be impacted by more than roughly one commit in ten, split the
   doc.** High-churn areas need short, narrow docs, not one long one.
4. **Exclude test files unless the doc documents the tests.** Tests change
   constantly and rarely invalidate prose. `src/api/**` will match
   `src/api/tests/test_routes.py`; if that is noisy, narrow the pattern or add
   the test directory to `ignore` in `.gotdocs/config.json`.
5. **Config and schema files are excellent `covers` targets.** They change rarely
   and almost always invalidate documentation when they do.
6. **An empty `covers: []` is legitimate.** Use it for docs describing something
   outside the repo — a vendor's API, a team process. They are indexed and
   readable but never impacted. Do not fake a glob to look thorough.
7. **Runbooks cover the thing that breaks**, not the thing that reports the
   break. A runbook about a failing pre-commit hook covers the hook, not the CI
   workflow that also runs it.

### Overlap is fine

Several docs may cover the same file. All of them become impacted; all of them
must be edited or verified. That is intended — if three documents describe
`src/auth/session.py`, three documents are potentially wrong when it changes.

## Glob dialect

Implemented in `tools/gotdocs/globs.py`. It is not `fnmatch` and it is not shell
globbing. `fnmatch`'s `*` crosses `/`, which would make `src/*` match
`src/a/b/c.py`; gotdocs compiles its own regexes instead.

| Pattern | Matches | Does not match |
| --- | --- | --- |
| `*` | any run of characters except `/` | anything containing `/` |
| `**` | any run of characters including `/`, whether it is a whole segment (`a/**/b`) or shares one (`src/**.py`) | — |
| `?` | exactly one character, not `/` | `/` |
| `[abc]` | one of `a`, `b`, `c` | any other char |
| `[a-z]` | one char in the range | outside the range |
| `[!abc]` | one char that is not `a`, `b`, or `c` | `a`, `b`, `c` |

Worked examples:

| Pattern | Matches | Does not match |
| --- | --- | --- |
| `tools/gotdocs/**` | `tools/gotdocs/cli.py`, `tools/gotdocs/tests/test_globs.py` | `tools/gotdocs` itself, `tools/other/x.py` |
| `tools/gotdocs/*.py` | `tools/gotdocs/cli.py` | `tools/gotdocs/tests/test_cli.py` |
| `bin/gotdocs` | `bin/gotdocs` | `bin/gotdocs.sh` |
| `*.py` | `cli.py`, `a/b/c.py` | `cli.pyc` |
| `src/**.py` | `src/a.py`, `src/pkg/deep/mod.py` | `src/a.ts`, `other/a.py` |
| `scripts/` | `scripts/install-gotdocs.sh`, `scripts/ci/run.sh` | `scripts` itself |
| `src/api/v?/**` | `src/api/v1/routes.go` | `src/api/v10/routes.go` |
| `.github/workflows/**` | `.github/workflows/gotdocs.yml` | `.github/CODEOWNERS` |

Additional rules:

- **A pattern with no `/` and no `**` matches the basename at any depth.** So
  `Makefile` matches `Makefile` and `services/api/Makefile`. If you mean only the
  root one, write `Makefile` differently — anchor it by including a directory, or
  accept the basename semantics.
- **`a/**` matches paths *under* `a`, not `a` itself.** To cover a directory
  entry as well as its contents, list both: `a` and `a/**`.
- **A pattern ending in `/` means "that directory and everything under it"** —
  `scripts/` is equivalent to `scripts/**`.
- **Patterns are repo-relative, `/`-separated, with no leading `./`.** Windows
  separators are not accepted. Absolute paths are not accepted.
- **No brace expansion.** `{a,b}` is a literal. Write two patterns.
- **No leading-`!` negation inside `covers`.** Negation exists only in the
  `ignore` list semantics of config, and `[!abc]` character classes are
  unaffected.
- Compiled patterns are cached, so a large `covers` list is cheap to evaluate
  against a large change set.

The same dialect is used for `ignore` in `.gotdocs/config.json`.

## Supported YAML subset

Gotdocs has zero third-party dependencies, so there is no PyYAML. `frontmatter.py`
implements a deliberately small parser. Anything outside this subset is a **lint
error** with a `file:line` pointer — never a silent misparse.

Supported:

```yaml
key: scalar                 # unquoted
key: 'single quoted'        # surrounding quotes stripped
key: "double quoted"        # surrounding quotes stripped
key: [a, b, c]              # inline flow list of scalars
key:                        # block list of scalars
  - a
  - b
# full-line comment
                            # blank lines
```

- A `#` inside a quoted scalar is part of the string, not a comment.
- Surrounding quotes are stripped from scalars and from flow-list items.
- Values are kept as strings; `updated` and `verified_at` are validated by shape,
  not coerced to date/int types.

Not supported — each of these is a lint error:

| Construct | Example | Do instead |
| --- | --- | --- |
| Nested maps | `owner:\n  name: mark` | Flatten: `owner_name: mark` |
| Lists of maps | `- name: a\n  url: b` | Two parallel scalar lists, or move it into the body |
| Block scalars | `summary: >` / `summary: \|` | One line, under `max_summary_chars` |
| Anchors and aliases | `&base`, `*base` | Repeat the value |
| Multi-document | a second `---` mid-frontmatter | One block only |
| Tags | `!!str 3` | Quote it |
| Tabs for indentation | `\t- item` | Two spaces |

If you need structure richer than this, it belongs in the markdown body. The
frontmatter exists to be machine-read cheaply, not to be a data model.

## Round-trip safety

`gotdocs verify` rewrites exactly two lines — `updated` and `verified_at` — in
place. Every other line, including key order, comments, quoting style and
whitespace, is preserved byte for byte. If the key is absent it is appended at the
end of the frontmatter block. Nothing else in the file is touched.

This matters because `verify` runs often, and a formatter that reflowed the
frontmatter would produce unreviewable diffs.

## Templates and schema

- `.gotdocs/templates/{doc,runbook,onboarding,dependency}.md` — what
  `bin/gotdocs new <type> <id>` copies. Editing a template changes what new docs
  look like; it does not touch existing docs.
- `.gotdocs/schema.json` — JSON Schema for the frontmatter object. It exists for
  editor integration and for humans reading the contract. The CLI does not depend
  on a JSON Schema validator; `lint` implements the same rules directly.

Keep the three in sync: a field added to the schema needs a line in the tables
above and, if it should appear in new docs, a line in each template.

## Linting

```sh
bin/gotdocs lint          # human output
bin/gotdocs lint --json   # machine output
```

`lint` reports: missing required fields, bad `id` shape, duplicate `id` across the
repo, unknown `type` or `status`, `summary` over the limit, malformed `updated`,
unsupported YAML constructs, missing or unterminated frontmatter, and `covers`
patterns that are syntactically invalid. It does **not** report a `covers` pattern
that currently matches zero files — that is legal, because a doc may describe code
that is about to exist or that lives behind a feature flag.

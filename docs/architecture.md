---
id: gotdocs-architecture
title: Gotdocs Architecture
type: doc
summary: End-to-end design of gotdocs — how a git diff becomes an impacted-doc list, a staleness finding, and a blocked commit.
covers:
  - tools/gotdocs/**
  - bin/gotdocs
owners: ["@mark"]
tags: [architecture, cli, internals]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Gotdocs Architecture

Gotdocs answers one question on every commit:

> Given the code that just changed, which documents in this repo now describe
> something that is no longer true?

It answers it without reading a single line of prose. Each doc declares, in its
frontmatter, the set of files it describes (`covers`). If a changed code file
matches a doc's `covers` globs, that doc is *impacted*. An impacted doc that was
not touched and not explicitly re-verified is *stale*, and stale docs are what
the hooks and CI report.

That is the whole idea. Everything below is mechanism.

## The chain

```text
doc frontmatter  ->  covers globs  ->  impacted docs  ->  stale docs  ->  finding
```

- **doc** — a markdown file under a configured root (`docs/`, `runbooks/`,
  `onboarding/`, `dependencies/`) with YAML frontmatter. See
  [doc-format.md](doc-format.md).
- **covers** — repo-relative glob patterns naming the code this doc describes.
- **impacted** — at least one changed code path matched at least one `covers`
  pattern of that doc.
- **stale** — impacted, *and* the doc file itself was not part of the change set,
  *and* its `verified_at` sha is not the head sha of the change set.
- **finding** — a structured record `{kind, doc_id, path, message, remediation}`
  emitted as human text or JSON.

## Components

Everything ships vendored in the target repo. Python 3.9+ standard library only —
no pip, no PyYAML, no network. See [dependencies/python3.md](../dependencies/python3.md).

| Path | Responsibility |
| --- | --- |
| `bin/gotdocs` | POSIX `sh` shim. Locates `python3`, execs `python3 tools/gotdocs`. The only thing engineers and hooks invoke. |
| `tools/gotdocs/__main__.py` | Package entry point, so `python3 tools/gotdocs` works. |
| `tools/gotdocs/cli.py` | Argument parsing, subcommand dispatch, global flags, exit codes. |
| `tools/gotdocs/config.py` | Loads `.gotdocs/config.json`, applies defaults when it is missing. |
| `tools/gotdocs/frontmatter.py` | Parses the supported YAML subset; rewrites `updated`/`verified_at` in place, byte-preserving for every other line. |
| `tools/gotdocs/globs.py` | The glob dialect. Compiles patterns to regexes and caches them. Load-bearing; heavily unit-tested. |
| `tools/gotdocs/gitutil.py` | Every `git` invocation. Diffs, head sha, repo toplevel, merge detection. See [dependencies/git.md](../dependencies/git.md). |
| `tools/gotdocs/index.py` | Walks the roots, reads frontmatter, writes `.gotdocs/index.json` and `.gotdocs/INDEX.md` reproducibly. |
| `tools/gotdocs/check.py` | The core rule: change set -> impacted -> satisfied/stale -> findings. |
| `tools/gotdocs/report.py` | Renders findings as grouped human text or as the `--json` contract. |
| `tools/gotdocs/errors.py` | Error types and the graceful-degradation boundary. |
| `tools/gotdocs/tests/` | `python3 -m unittest discover` — stdlib `unittest`, no runner dependency. |

Non-code artifacts:

| Path | Responsibility |
| --- | --- |
| `.gotdocs/config.json` | Roots, ignore globs, enforcement modes, skip token. |
| `.gotdocs/schema.json` | JSON Schema for frontmatter. Documentation and editor tooling; the CLI does not require a JSON Schema validator to run. |
| `.gotdocs/index.json` | Generated, committed. Machine index of every doc. |
| `.gotdocs/INDEX.md` | Generated, committed. The token-cheap file an agent reads first. |
| `.gotdocs/hooks/pre-commit` | Source of truth for the hook; `scripts/install-gotdocs.sh` puts it in `.git/hooks/`. |
| `.github/workflows/gotdocs.yml` | CI enforcement, `mode=error`. |
| `.claude/skills/gotdocs-*/` | The skills that do the actual writing. |

## Data flow: `git diff` to a pre-commit finding

```text
              engineer runs: git commit
                        |
                        v
            .git/hooks/pre-commit  (installed copy of .gotdocs/hooks/pre-commit)
                        |
                        |  skip if .git/MERGE_HEAD exists (merge/rebase in progress)
                        |  skip if $GOTDOCS_SKIP, or a *pending* COMMIT_EDITMSG,
                        |  carries the skip token
                        |  skip if python3 is missing (warn, exit 0)
                        v
                bin/gotdocs check --staged
                        |
                        v
        +-----------------------------+
        |  gitutil.py                 |   git diff --cached --name-status
        |  git rev-parse HEAD          |   git rev-parse --short HEAD
        +-----------------------------+
                        |
                 changed paths + head sha
                        |
                        v
        +-----------------------------+
        |  config.py                  |   roots[]  ignore[]  enforce.pre_commit
        +-----------------------------+
                        |
             split the change set
        +---------------+---------------------------+
        |                                           |
        v                                           v
   DOC PATHS                                   CODE PATHS
   (inside a root)                             (everything else, minus ignore[])
        |                                           |
        |  used only to mark                        |
        |  "the author edited this doc"             |
        |                                           |
        v                                           |
  +-----------------+                               |
  | .gotdocs/       |   built by index.py from      |
  | index.json      |   frontmatter.py over the     |
  +-----------------+   configured roots            |
        |                                           |
        |  for each doc: covers[]                   |
        +-------------------+     globs.py     +----+
                            v                  v
                    +-------------------------------+
                    |  check.py                     |
                    |                               |
                    |  code path matches covers?    |
                    |        -> IMPACTED            |
                    |                               |
                    |  doc file in change set?      |
                    |  or verified_at == head sha?  |
                    |        -> satisfied           |
                    |  else  -> STALE               |
                    +-------------------------------+
                            |
                            v
                    +-------------------------------+
                    |  report.py                    |
                    |  human text  |  --json        |
                    +-------------------------------+
                            |
              +-------------+-------------------+
              v                                 v
   enforce.pre_commit = warn           enforce.ci = warn   (shipped default)
   print findings, exit 0              report + record debt, job stays green

                                       enforce.ci = error  (opt in)
                                       exit 1, pull request goes red
```

`enforce.ci` is the single knob that makes CI block. See
[doc-debt.md](doc-debt.md#ci-does-not-block-by-default).

## The core rule, precisely

`check` runs these steps in order.

1. **Determine the change set.** One of:
   - `--staged` -> `git diff --cached --name-status`
   - `--base REF` -> `git diff --name-status REF...HEAD` (three-dot: compares
     against the merge base, so unrelated commits on `REF` do not create noise)
   - `--paths P...` -> the literal list, no git required
2. **Split the change set.** A path inside one of `roots` is a *doc path*. Every
   other path is a *code path*, unless it matches an `ignore` glob. `ignore`
   exists so that lockfiles, vendored trees, build output and gotdocs' own
   generated index never count as code changes.
3. **Compute impacted docs.** For each doc in the index, for each code path: if
   any `covers` pattern matches, the doc is impacted. Matching is done by
   `globs.py`, not `fnmatch` (see [doc-format.md](doc-format.md#glob-dialect) for
   why).
4. **Decide satisfied vs stale.** An impacted doc is satisfied if **either**
   - its own path is in the change set — the author edited the doc alongside the
     code, which is the outcome gotdocs actually wants; **or**
   - its `verified_at` equals the head sha of the change set — the author ran
     `gotdocs verify <id>`, asserting "I read this doc against the new code and
     it is still correct".

   Otherwise it is stale and produces a finding.
5. **Uncovered code.** Code paths that match no doc's `covers` produce a finding
   only when `require_coverage` is `true` in config. It defaults to `false`
   because turning it on in an existing repo produces a finding for every file
   you own. See [runbooks/adopting-gotdocs-in-an-existing-repo.md](../runbooks/adopting-gotdocs-in-an-existing-repo.md).
6. **Doc-side findings, always reported.** Lint errors in frontmatter, editing a
   `status: deprecated` doc, duplicate `id` values, and `.gotdocs/index.json`
   being out of date relative to the working tree.
7. **Skip.** If `GOTDOCS_SKIP=1` is set, or the commit message carries the skip
   token (`[gotdocs skip]` by default), `check` reports nothing and exits 0.
   With `--staged` the message comes from `.git/COMMIT_EDITMSG`, but only when
   it differs from HEAD's message: git writes that file *after* the pre-commit
   stage, so the leftover from the previous commit must not be read as this
   commit's intent. `GOTDOCS_SKIP=1` is the deterministic bypass.

### Why "edited OR verified"

The two escape valves are deliberately asymmetric in cost. Editing the doc is
free and is the normal path. `verify` is a single command but it writes
`verified_at` into the file, so the assertion "I looked at this and it is fine"
lands in the diff with your name on it. There is no silent third option.

## Enforcement layers

The same `check` runs in three places with three modes. Modes come from
`.gotdocs/config.json` `enforce`, overridable per invocation with `--mode`.

- `off` — compute nothing user-visible, exit 0.
- `warn` — print findings, exit 0. The commit lands.
- `error` — print findings, exit 1.

Pre-commit defaults to `warn`, CI defaults to `error`. The rationale and the
rollout order are in [enforcement.md](enforcement.md).

## Failure posture

Gotdocs is a pre-commit hook in someone else's repo. It is not allowed to be the
reason a commit cannot happen.

- An unexpected internal exception prints a one-line warning and exits 0. The
  `--strict` flag turns internal errors back into failures; use it when you want
  a run to fail loudly rather than pass quietly.
- Missing `.gotdocs/config.json` falls back to the documented defaults, so
  `gotdocs install` can bootstrap a repo that has no config yet.
- Missing `python3` is handled in the `sh` shim and in the hook: warn, exit 0.
- Not a git repo, or a git question that cannot be answered (unknown base ref,
  no merge base, no commits yet): exit 3. A *malformed* config is exit 2; a
  missing one is not an error at all.

Exit codes are enumerated in [cli-reference.md](cli-reference.md#exit-codes).

## Reproducibility of the index

`.gotdocs/index.json` and `.gotdocs/INDEX.md` are generated *and committed*.
Committing generated files is only tolerable if regenerating them is
deterministic, so:

- docs are sorted by `id`
- JSON uses 2-space indent and a trailing newline
- the only volatile field is `generated_at_sha`, and it is *sticky*: the sha
  already in the file is kept whenever nothing else in the payload changed, so a
  later checkout does not restamp it. A commit cannot contain its own sha, so
  stamping HEAD unconditionally would leave every committed index permanently
  out of date and the CI freshness gate permanently red.

Running `bin/gotdocs index` twice with no source changes produces byte-identical
files, and so does running it on a different commit. That is what makes "the committed index is out of date" a usable CI
finding rather than permanent diff churn.

## What is out of scope

Publishing to a static site. The frontmatter is deliberately SSG-friendly —
unknown keys are passed through untouched, so `sidebar_position`, `slug` and
friends survive — but gotdocs itself renders nothing.

---
id: onboarding-local-setup
title: Local Setup
type: onboarding
summary: Clone, install the hooks, run the tests, then deliberately trigger a stale-doc finding and resolve it — with the exact commands and expected output.
covers:
  - scripts/install-gotdocs.sh
  - scripts/uninstall-gotdocs.sh
  - bin/gotdocs
  - tools/gotdocs/tests/**
owners: ["@mark"]
tags: [onboarding, setup, hooks, testing]
status: current
updated: 2026-08-15
verified_at: 24024f5
---

# Local Setup

About ten minutes. Nothing here downloads anything; gotdocs has no package
dependencies and makes no network calls.

## Prerequisites

```sh
git --version         # need 2.20 or newer
python3 --version     # need 3.9 or newer
```

That is the complete list. No pip, no virtualenv, no node, no brew formula. If
`python3` is missing or too old, see
[dependencies/python3.md](../dependencies/python3.md#installing-python3-per-platform);
if `git` is unusual in your environment (worktree, `core.hooksPath`, shallow
clone), see [dependencies/git.md](../dependencies/git.md#failure-modes).

## 1. Clone

```sh
git clone <repo-url> gotdocs
cd gotdocs
```

Run everything below from the repository root. `bin/gotdocs` resolves its own path
so it works from subdirectories too, but the examples assume the root.

## 2. Install the hooks

`.git/hooks/` is not tracked by git, so hooks do not arrive with a clone or a
pull. Every clone runs this once:

```sh
sh scripts/install-gotdocs.sh
```

Expected output:

```text
gotdocs: repository /Users/you/Code/gotdocs
gotdocs: installing hooks into /Users/you/Code/gotdocs/.git/hooks
  pre-commit: installed
  pre-push: installed
gotdocs: config already present at .gotdocs/config.json (left untouched)
gotdocs: 25 documents indexed, no changes
gotdocs: regenerated .gotdocs/index.json and .gotdocs/INDEX.md

gotdocs: install complete.
  hooks directory: /Users/you/Code/gotdocs/.git/hooks
  next steps:
    - commit .gotdocs/ (config, index.json, INDEX.md) so teammates get it on pull
    - re-run this script after pulling changes to .gotdocs/hooks/
  bypass a single commit with: GOTDOCS_SKIP=1 git commit ...
  uninstall with: scripts/uninstall-gotdocs.sh
```

The script is idempotent — re-run it any time, and specifically after pulling a
change to `.gotdocs/hooks/`, since the installed copies do not update themselves.

If you already had a `pre-commit` hook (husky, `pre-commit`, lefthook, hand-rolled),
it is preserved as `.git/hooks/pre-commit.local` and the gotdocs hook runs it
first; its veto still wins. Nothing is discarded.

Verify:

```sh
ls -l .git/hooks/pre-commit .git/hooks/pre-push
head -2 .git/hooks/pre-commit          # second line: "# gotdocs-managed-hook v1"
```

## 3. Confirm the state

```sh
bin/gotdocs status
```

```text
gotdocs 1  repo /Users/you/Code/gotdocs  head 3d8b6cd
config    .gotdocs/config.json
roots     docs, runbooks, onboarding, dependencies, decisions
docs      21 (13 current, 0 draft, 0 deprecated)
index     .gotdocs/index.json up to date
enforce   pre_commit=warn  pre_push=warn  ci=warn  require_coverage=false
hook      .git/hooks/pre-commit installed (matches .gotdocs/hooks/pre-commit)
```

Every line that reports a problem prints its own fix. If `index` says out of date,
run `bin/gotdocs index`. If `hook` says not installed, re-run step 2.

Two things about that output that look wrong and are not. All three `enforce`
values are `warn` — that is the shipped default, and nothing will block your
commit until somebody raises `enforce.ci` to `error`. And the three status counts
do not add up to the total, because decision records use a different status enum
and are deliberately excluded from those buckets.

## 4. Run the tests

```sh
python3 -m unittest discover -s tools/gotdocs/tests -t .
```

```text
----------------------------------------------------------------------
Ran 739 tests in 34.6s

OK
```

`unittest` is stdlib, so this needs nothing installed. The `-t .` matters: it sets
the top-level directory so `tools.gotdocs` is importable. Without it you get
`Ran 0 tests`.

Run one module while iterating:

```sh
python3 -m unittest tools.gotdocs.tests.test_globs -v
```

`tools/gotdocs/globs.py` is the load-bearing module — every impacted/stale
decision goes through it — so its tests are the ones to run after any change to
glob behavior.

## 5. Make a change and watch the hook fire

This is the part worth doing. You are going to deliberately make a doc stale.

```sh
# 1. see which docs claim this file
bin/gotdocs impacted tools/gotdocs/globs.py
```

```text
tools/gotdocs/globs.py
  0002-python-stdlib-only-cli                decisions/0002-python-stdlib-only-cli.md                (tools/gotdocs/globs.py)
  0003-covers-globs-as-the-staleness-signal  decisions/0003-covers-globs-as-the-staleness-signal.md  (tools/gotdocs/globs.py)
  cli-reference                              docs/cli-reference.md                                   (tools/gotdocs/**)
  doc-format                                 docs/doc-format.md                                      (tools/gotdocs/globs.py)
  gotdocs-architecture                       docs/architecture.md                                    (tools/gotdocs/**)
```

Five documents, and two of them are decision records — `covers` is not limited to
reference docs.

```sh
# 2. change it
printf '\n# touched during local setup\n' >> tools/gotdocs/globs.py
git add tools/gotdocs/globs.py

# 3. ask what that made stale, before committing
bin/gotdocs check --staged
```

```text
gotdocs: 5 findings (mode: warn)

stale (5)
  decisions/0002-python-stdlib-only-cli.md  [0002-python-stdlib-only-cli]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/globs.py
    -> update decisions/0002-python-stdlib-only-cli.md, or run: bin/gotdocs verify 0002-python-stdlib-only-cli
  decisions/0003-covers-globs-as-the-staleness-signal.md  [0003-covers-globs-as-the-staleness-signal]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/globs.py
    -> update decisions/0003-covers-globs-as-the-staleness-signal.md, or run: bin/gotdocs verify 0003-covers-globs-as-the-staleness-signal
  docs/architecture.md  [gotdocs-architecture]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/**
    -> update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture
  docs/cli-reference.md  [cli-reference]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/**
    -> update docs/cli-reference.md, or run: bin/gotdocs verify cli-reference
  docs/doc-format.md  [doc-format]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/globs.py
    -> update docs/doc-format.md, or run: bin/gotdocs verify doc-format

Or ask Claude: /gotdocs-update
```

```sh
# 4. commit, and watch the hook print the same thing
git commit -m "chore: local setup smoke test"
```

The commit **succeeds**. `enforce.pre_commit` is `warn` in this repo, so the hook
prints the findings and a remediation block and exits 0. That is the intended
default: local checks inform, CI enforces. See
[docs/enforcement.md](../docs/enforcement.md).

To feel the blocking behavior once:

```sh
bin/gotdocs check --staged --mode error ; echo "exit=$?"   # exit=1
```

## 6. Resolve it, then clean up

Your comment did not change any documented behavior, so this is the `verify` case:

```sh
bin/gotdocs verify \
  gotdocs-architecture cli-reference doc-format \
  0002-python-stdlib-only-cli 0003-covers-globs-as-the-staleness-signal
git diff docs/ decisions/

# verify rewrote frontmatter, so the committed index is now behind:
bin/gotdocs index
git add docs/ decisions/ .gotdocs/index.json .gotdocs/INDEX.md
bin/gotdocs check --staged            # 0 findings
```

Name every document `check` reported, or the ones you skip stay stale. The short
version of the same thing is `bin/gotdocs verify --all-impacted`, which takes its
targets from the staged `check` result.

You will see exactly two lines changed per file — `updated` and `verified_at` —
and nothing else. That byte-preserving rewrite is deliberate; it keeps `verify`
diffs reviewable.

Now undo the whole experiment:

```sh
git reset --hard HEAD~1
bin/gotdocs status                 # back to clean
```

## The commands you will actually use

```sh
bin/gotdocs status                 # is everything wired up
bin/gotdocs impacted <path>        # which docs describe this file
bin/gotdocs check --staged         # what did I just make stale
bin/gotdocs verify <doc-id>        # I read it; it is still accurate
bin/gotdocs lint                   # is all frontmatter valid
bin/gotdocs index                  # regenerate after doc metadata changes
bin/gotdocs new <type> <id>        # scaffold a new doc
```

Add `--json` to any of them for machine-readable output; the shape is documented
in [docs/cli-reference.md](../docs/cli-reference.md#the---json-contract) and is
stable.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Hook never runs | Not installed in this clone | `sh scripts/install-gotdocs.sh` |
| Hook still never runs | `core.hooksPath` points elsewhere | `git config --get core.hooksPath`; install into that directory |
| `gotdocs: python3 was not found on PATH` | No interpreter for git's environment | `export GOTDOCS_PYTHON=/path/to/python3` in your shell profile |
| `Ran 0 tests` | Wrong top-level dir for discovery | Add `-t .` and run from the repo root |
| `not a git repository` (exit 3) | Wrong directory, or a nested repo | `git rev-parse --show-toplevel` |
| Hook fires during a rebase | Should not happen | See [runbooks/pre-commit-hook-blocking.md](../runbooks/pre-commit-hook-blocking.md) |
| Findings you disagree with | `covers` globs too broad | [runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md) |

## Uninstalling

```sh
sh scripts/uninstall-gotdocs.sh
```

Removes the gotdocs hooks and restores any `.local` hook the installer preserved.
The docs remain ordinary markdown. To disable enforcement without uninstalling,
set `"enforce": {"pre_commit": "off", "ci": "off"}` in `.gotdocs/config.json`.

## Next

- [onboarding/start-here.md](start-here.md) — what gotdocs is and what to read
- [docs/architecture.md](../docs/architecture.md) — how the check actually works
- [docs/agent-workflow.md](../docs/agent-workflow.md) — using it with Claude

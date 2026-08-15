---
id: runbook-pre-commit-hook-blocking
title: "Runbook: Pre-Commit Hook Is Blocking a Commit"
type: runbook
summary: The gotdocs pre-commit hook refused your commit or printed findings you did not expect — how to unblock in under two minutes without breaking the guarantee.
covers:
  - .gotdocs/hooks/**
  - scripts/install-gotdocs.sh
owners: ["@mark"]
tags: [runbook, hooks, pre-commit, unblock]
status: current
updated: 2026-08-15
verified_at: 24024f5
---

# Runbook: Pre-Commit Hook Is Blocking a Commit

You ran `git commit` and got gotdocs output instead of a commit. This gets you
moving. Total time if you follow it in order: under two minutes.

## Symptom

One of these, printed by `.git/hooks/pre-commit`:

```text
gotdocs: 1 finding (mode: error)

stale (1)
  docs/architecture.md  [gotdocs-architecture]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/**
    -> update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture

Or ask Claude: /gotdocs-update
```

or

```text
gotdocs: internal error: ...
```

or the commit simply did not happen and there is no gotdocs output at all.

**It is this runbook if:** the output says `gotdocs`.
**It is not this runbook if:** the output is from a different hook (`husky`,
`pre-commit`, `lefthook`, a linter). Read the tool name in the message.

## Confirm what happened

```sh
bin/gotdocs check --staged
echo "exit=$?"
```

- `exit=0` with findings printed — mode is `warn`; the hook did **not** block you.
  Something else refused the commit. Stop here and read the rest of the terminal
  output.
- `exit=1` with findings — mode is `error`. Go to [Fix it](#fix-it).
- A finding of kind `lint` in the output — frontmatter somewhere does not parse
  or does not validate. `check` reports it as one finding among the others and
  still exits 0 in `warn` and 1 in `error`; **`check` never exits 2 for a lint
  error.** So in `error` mode a lint finding blocks the commit exactly like a
  stale one. Go to [Lint errors](#lint-errors) and run `bin/gotdocs lint`, which
  *is* the command that exits 2, to see it on its own.
- `exit=2` — a usage error: a flag or a path `check` could not accept (a
  `--paths` argument outside the repo, an unknown `--mode`). The message names
  it. This is not a lint error.
- `exit=3` — not a git repo, or git cannot answer the question (unknown base ref,
  no merge base, no commits yet). A missing config is *not* this: the defaults
  apply. Go to [Environment problems](#environment-problems).
- `gotdocs: internal error` — go to [Environment problems](#environment-problems).

## Fix it

You have three options. Pick by what actually changed, not by what is fastest.

### Option A — the documented behavior changed. Edit the doc. (Correct ~70% of the time.)

Open the file named in the finding, fix the parts your change made wrong, stage it,
commit again.

```sh
$EDITOR docs/architecture.md
git add docs/architecture.md
git commit
```

You have the context in your head right now. This is the cheapest it will ever be.

To have Claude do it: `/gotdocs-update`. It reads the finding, opens only the
impacted docs, edits them, and stages them.

### Option B — the code changed but the documented behavior did not. Verify.

Renames, refactors, formatting, a performance fix, a new private helper — the
prose is still accurate.

```sh
bin/gotdocs verify gotdocs-architecture
git add docs/architecture.md
git commit
```

`verify` stamps `verified_at` to the current `HEAD` sha and `updated` to today,
in place. It edits the working tree; you still have to `git add` the doc.

**Read the doc before you run this.** The command is an assertion with your name
on it in the diff. It takes about 30 seconds for a doc of normal length.

Multiple docs at once:

```sh
bin/gotdocs verify gotdocs-architecture cli-reference
# or, everything currently impacted:
bin/gotdocs verify --all-impacted
git add docs/ runbooks/ onboarding/ dependencies/
```

For more than two or three findings, work through
[stale-doc-triage.md](stale-doc-triage.md) instead of verifying in bulk.

### Option C — you genuinely need to ship now. Skip.

```sh
GOTDOCS_SKIP=1 git commit -m "your message"        # always works
git commit -m "your message [gotdocs skip]"        # best effort, see below
```

Use `GOTDOCS_SKIP=1` when it has to work. The commit-message token is checked
against `.git/COMMIT_EDITMSG`, which git does not reliably populate with the
pending message before `pre-commit` runs — the hook additionally compares it
against `HEAD`'s message so a previous commit's token cannot suppress this one.
Use the token when you want the decision recorded in history, and the environment
variable when you need it to take effect.

**What this costs:** the token stays in the commit message permanently and is
greppable (`git log --grep='\[gotdocs skip\]'`). It does **not** skip CI — CI
checks the whole branch diff, so the finding comes back on the pull request. Use
this for WIP commits you will squash, mechanical repo-wide changes, and
emergencies. Then fix it before you open the PR.

`git commit --no-verify` also works and is worse: it skips every hook you have,
including secret scanning, and leaves no record. Prefer the skip token.

## Lint errors

A `lint` finding means frontmatter is malformed somewhere, in a file that may not
be one you touched. `bin/gotdocs check` reports it and exits by mode;
`bin/gotdocs lint` is the command that exits `2` on it.

```sh
bin/gotdocs lint
```

The output points at `file:line`. The common causes, in order of frequency:

| Message mentions | Cause | Fix |
| --- | --- | --- |
| unsupported construct | Nested map, `\|`/`>` block scalar, anchor, or tab indentation in frontmatter | Flatten to the supported subset — see [docs/doc-format.md](../docs/doc-format.md#supported-yaml-subset) |
| duplicate id | Two docs declare the same `id` (usually from copy-pasting a file) | Rename one, then `bin/gotdocs index` |
| missing required field | A hand-written doc missing `covers`, `status` or `summary` | Add it; compare against `.gotdocs/templates/` |
| summary too long | Over `max_summary_chars` (default 200) | Shorten it; the long version goes in the body |
| unterminated frontmatter | Missing closing `---`, or a blank line/BOM before the opening `---` | The opening `---` must be the first bytes of the file |

Fix these first regardless of mode: an unparseable doc cannot be indexed, so its
`covers` protects nothing. `bin/gotdocs lint` exits `2` until they are gone, and
in CI the `Enforce` step turns that into a red job only when `enforce.ci` is
`error` — under the shipped `warn` it is a `::warning::` and the job stays
green.

## Index out of date

```text
index_out_of_date  .gotdocs/index.json
  -> run: bin/gotdocs index && git add .gotdocs/
```

You added, deleted, renamed or re-frontmattered a doc, and the committed index no
longer matches. Fix:

```sh
bin/gotdocs index
git add .gotdocs/index.json .gotdocs/INDEX.md
git commit
```

The regeneration is deterministic, so if `git diff` shows changes to `.gotdocs/`
after you just ran `index`, something else really did change.

## Environment problems

### `gotdocs: internal error: ...`

The hook exits 0 on internal errors, so this alone did not block your commit —
but it means gotdocs checked nothing. Reproduce with the failure surfaced:

```sh
bin/gotdocs check --staged --strict
```

That prints the real traceback. If it is a gotdocs bug, commit with the skip token
and file it.

### `python3: command not found` / hook warns and does nothing

```sh
command -v python3 || echo "no python3"
python3 --version   # must be 3.9 or newer
```

Point gotdocs at a specific interpreter:

```sh
GOTDOCS_PYTHON=/opt/homebrew/bin/python3 git commit
```

Details and the degradation path: [dependencies/python3.md](../dependencies/python3.md).

### `not a git repository` (exit 3)

You are outside the repo, or inside a nested one. `git rev-parse --show-toplevel`
tells you which repo you are actually in.

### The hook does not run at all

`.git/hooks/` is not tracked by git, so a fresh clone has no hook until someone
installs it:

```sh
ls -l .git/hooks/pre-commit
sh scripts/install-gotdocs.sh
```

A gotdocs-installed hook has `gotdocs-managed-hook v1` on its second line:

```sh
head -2 .git/hooks/pre-commit
```

If it is someone else's hook, the installer did not overwrite it — it should have
moved it to `.git/hooks/pre-commit.local` and installed gotdocs' hook, which
chains to `.local` first. If `.local` exists but the gotdocs hook does not, re-run
`sh scripts/install-gotdocs.sh`.

Check it is not merely non-executable:

```sh
test -x .git/hooks/pre-commit || chmod +x .git/hooks/pre-commit
```

### The hook fired during a merge, rebase, cherry-pick or revert

It should not — the hook exits 0 when any of `MERGE_HEAD`, `CHERRY_PICK_HEAD`,
`REVERT_HEAD`, `rebase-merge/` or `rebase-apply/` is present in the git dir. If it
did, `GOTDOCS_SKIP=1 git commit` past it and file a bug with the output of:

```sh
ls -d "$(git rev-parse --git-dir)"/MERGE_HEAD \
      "$(git rev-parse --git-dir)"/CHERRY_PICK_HEAD \
      "$(git rev-parse --git-dir)"/rebase-merge \
      "$(git rev-parse --git-dir)"/rebase-apply 2>&1
```

### The skip token in the commit message did not work

The hook reads `.git/COMMIT_EDITMSG`, and git does not guarantee that file holds
the pending message at `pre-commit` time — it is often still the previous commit's
message, which is why the hook also requires it to differ from `HEAD`'s message
before honoring the token. Use `GOTDOCS_SKIP=1 git commit` instead; it is checked
first and unconditionally.

### The hook ran on `git push`, not `git commit`

That is the pre-push hook (`.gotdocs/hooks/pre-push`), installed by the same
script. It checks the whole branch against its upstream, so it reports findings
from commits you already made. Same three options above; its mode comes from
`enforce.pre_push` in `.gotdocs/config.json` and defaults to `warn`. See
[docs/enforcement.md](../docs/enforcement.md#pre-push).

## Verify you are unblocked

```sh
git commit                       # completes
bin/gotdocs check --staged       # clean on the next staged change
bin/gotdocs status               # hook installed, index up to date
```

## The finding was wrong

If a doc lights up on changes that cannot possibly affect it, its `covers` globs
are too broad. That is a real defect and it is worth fixing immediately — broad
globs are how teams learn to ignore gotdocs.

```sh
bin/gotdocs impacted <the-file-you-changed>
```

Then narrow the `covers` list in that doc's frontmatter (see
[docs/doc-format.md](../docs/doc-format.md#choosing-good-covers-globs)) and run
`bin/gotdocs index`. Do not widen `.gotdocs/config.json`'s `ignore` to solve a
`covers` problem — `ignore` is repo-wide and hides the file from every doc.

## Related

- [docs/enforcement.md](../docs/enforcement.md) — modes, layers, and what each escape hatch costs
- [runbooks/stale-doc-triage.md](stale-doc-triage.md) — when there are many findings, not one
- [runbooks/ci-check-failing.md](ci-check-failing.md) — the same checks, failing in CI instead

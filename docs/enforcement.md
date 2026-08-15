---
id: enforcement
title: Enforcement — Hooks, CI, Modes and Escape Hatches
type: doc
summary: Where gotdocs runs (pre-commit, pre-push, CI), what off/warn/error each mean, every way to bypass a check and what it costs, and the rollout order.
covers:
  - .gotdocs/hooks/**
  - scripts/**
  - .github/workflows/**
owners: ["@mark"]
tags: [enforcement, hooks, ci, rollout]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Enforcement — Hooks, CI, Modes and Escape Hatches

The same computation (`gotdocs check`) runs at up to three points. What differs is
the change set it looks at and what happens when it finds something.

| Layer | Change set | Shipped mode | Config key | Cost of a false positive |
| --- | --- | --- | --- | --- |
| pre-commit | `--staged` | `warn` | `enforce.pre_commit` | Interrupts someone mid-thought. High. |
| pre-push | `--base <remote sha for the ref being pushed>` | `warn` | `enforce.pre_push` | Interrupts a batch. Medium. |
| CI | `--base origin/<PR base>` | `warn` | `enforce.ci` | Blocks a PR, visible to reviewers. Low, but public. |

All three ship as `warn`. **`enforce.ci` is the single knob that makes CI block**;
until it is `error` the job reports, records debt and stays green. The one
exception is the committed-index gate, which is always a hard failure — see
[CI](#ci).

Both hooks are installed by `scripts/install-gotdocs.sh`. `enforce.pre_push` ships in
`.gotdocs/config.json` set to `warn`; both the hook and the CLI fall back to `warn`
when the key is absent, so removing it changes nothing. Set it to `off` or `error`
to move that layer.

Verify what your repo is actually configured to do:

```sh
$ bin/gotdocs status
enforce   pre_commit=warn  pre_push=warn  ci=warn  require_coverage=false
```

## Modes

`enforce` values are `off`, `warn`, `error`. Set per layer in
`.gotdocs/config.json`; override for one invocation with `--mode`.

```json
"enforce": { "pre_commit": "warn", "pre_push": "warn", "ci": "warn" }
```

That is the shipped default, verbatim from `.gotdocs/config.json`. The CLI's
built-in defaults are identical, so a repo with no config behaves the same as a
repo with the stock one.

- **`off`** — the layer does nothing and exits 0. Use it to disable a layer
  without uninstalling the hook, and to stage a rollout repo by repo.
- **`warn`** — findings print, exit 0, the commit or build proceeds. This is
  informational pressure: the engineer sees exactly which docs their change
  touched, at the moment they still remember why. At **pre-commit specifically**,
  `warn` also runs `debt record`, so the findings the commit is being allowed to
  carry land in `.gotdocs/debt.jsonl` instead of scrolling past. That is what
  stops `warn` from meaning "nobody will ever read this". See
  [doc-debt.md](doc-debt.md). (Pre-push does not record — pre-commit already
  did. The CI `check` job does not record either; the separate `record` job does,
  on push to `main`, and it does so regardless of `enforce.ci`.)
- **`error`** — findings print, exit 1. The commit is refused or the build is red.

`warn` does not change `ok` in the JSON output: `check --json --mode warn`
reports `"ok": false` with the findings listed, exit 0. So automation that wants
"is there work to do" should read `ok`, not the exit code — under `warn` the
exit code is 0 either way.

`off` is different, and the difference matters. `off` short-circuits before any
finding is computed, so `check --json --mode off` emits
`{"ok": true, "mode": "off", "findings": []}` — not because the tree is clean but
because nothing was looked at. **Read `mode` alongside `ok`.** `ok: true` with
`mode: "off"` means "not checked"; only `ok: true` with `mode: "warn"` or
`"error"` means "clean".

## Pre-commit

Source of truth: `.gotdocs/hooks/pre-commit`. Installed into `.git/hooks/pre-commit`
by `scripts/install-gotdocs.sh` or `bin/gotdocs install`. `.git/hooks/` is not
committed, so every clone must run the installer once — the hook does not arrive
with a `git pull`.

What the hook does, in order, before it does anything expensive:

1. Run `.git/hooks/pre-commit.local` if it exists — the hook the installer moved
   aside. Its veto wins and it runs first, because it is usually the more
   important check.
2. Exit 0 if `GOTDOCS_SKIP` is `1`/`true`/`yes`.
3. Exit 0 if git is unavailable or this is not a git repo.
4. Exit 0 if a merge, rebase, cherry-pick or revert is in progress
   (`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `rebase-merge/`,
   `rebase-apply/`). Those are somebody else's commits; blocking them produces
   findings nobody can act on.
5. Read `enforce.pre_commit` from `.gotdocs/config.json`. Exit 0 immediately if
   it is `off`, before paying for anything else.
6. Exit 0 if the pending commit message (`.git/COMMIT_EDITMSG`, when readable)
   contains the skip token **and** differs from `HEAD`'s message. This is
   best-effort by construction: git writes the pending message to
   `COMMIT_EDITMSG` after `pre-commit` runs, so the file usually still holds the
   previous commit's message. The HEAD comparison is what stops yesterday's
   skip from silently suppressing today's check. `GOTDOCS_SKIP=1` is the
   deterministic bypass.
7. Exit 0 if nothing is staged.
8. Exit 0 with a one-line warning if `python3` is not on `PATH`, or if
   `bin/gotdocs` is missing. See [dependencies/python3.md](../dependencies/python3.md).
9. Otherwise run `bin/gotdocs check --staged --quiet --mode "$MODE" --message ''`
   and print the findings plus a remediation block on stderr. `--quiet` is what
   makes "empty stdout" mean "nothing to report", and `--message ''` keeps the
   skip-token decision in step 6 rather than re-deriving it inside the CLI.
10. **In `warn` mode only**, if there were findings, run
    `bin/gotdocs debt record --staged --source hook --quiet` and print
    `gotdocs: recorded in .gotdocs/debt.jsonl (see: bin/gotdocs debt list)`.
    Not in `off` (nothing was checked) and not in `error` (the commit was either
    clean or blocked, so nothing was accepted). The ledger is written but
    deliberately **not staged**: silently adding a file to somebody's commit is
    worse than a slightly stale ledger, and `debt record` is idempotent.

The hook exits non-zero only when the mode is `error` and `check` returned 1 or 2.
Any other exit status from the CLI is reported and then swallowed. An exit 1 with
*empty stdout* is treated as a tool failure rather than a finding — a real
findings report is never empty — so a broken vendored tree lets the commit
through instead of blocking it.

Design constraints that are not negotiable:

- **Under 300ms on a small repo.** The hook reads `.gotdocs/index.json` and one
  `git diff --cached --name-status`. It does not walk the tree, does not read doc
  bodies, and does not shell out per file.
- **Never the reason a commit fails.** Internal errors warn and exit 0 unless
  `--strict`.
- **Always prints the next step**, including `ask Claude: /gotdocs-update`.

Start at `warn`. See [rollout](#rollout) below.

If the repo already has a `pre-commit` hook — from `pre-commit`, `husky`, `lefthook`
or hand-rolled — the installer does not delete it. It moves it to
`.git/hooks/pre-commit.local` and the gotdocs hook chains to it first, so the
existing checks still run and still get to veto the commit. Nothing is lost, and
uninstalling gotdocs means moving `.local` back.

## Pre-push

Source of truth: `.gotdocs/hooks/pre-push`, installed alongside the pre-commit
hook. It checks everything the push would publish, not one commit, so it is the
last local chance to catch what CI will catch.

It resolves the comparison base in this order, which is what makes it work on a
branch that has never been pushed:

1. **The remote sha git itself supplies on stdin** for the ref being pushed.
   Git feeds a pre-push hook lines of `<local ref> <local sha> <remote ref>
   <remote sha>`; the hook picks the line whose *local* sha is `HEAD`, and uses
   that line's remote sha. This is the commit the remote is actually on, which
   is the only correct base for `git push origin HEAD:release`, for a push from
   a detached HEAD, and for a branch pushed to a differently-named remote ref.
   Any other usable line is kept as a fallback. A remote sha of all zeros (a
   brand new remote branch) or one not present locally is skipped.
2. `@{upstream}` if the branch has one
3. `origin/HEAD`
4. `origin/main`
5. the repository's root commit (`git rev-list --max-parents=0 HEAD`)

Lines whose *local* sha is all zeros are ignored throughout: that is a remote-ref
deletion, which publishes no code. A push that is only deletions exits 0 without
running anything.

Then it runs `bin/gotdocs check --base "$BASE" --quiet --mode "$MODE"`, where
`MODE` comes from `enforce.pre_push` (default `warn`). It reads git's pre-push
stdin once and replays it to a chained `.local` hook, skips during
merge/rebase/cherry-pick, and honors `GOTDOCS_SKIP`.

Unlike pre-commit it honors the skip token *reliably*, and over a wider range: it
greps `git log --format=%B "$BASE..HEAD"` — **every commit being pushed** — not
just HEAD. Checking only HEAD would re-report a change that was already
deliberately excused at commit time, with no way to excuse it again short of
`GOTDOCS_SKIP=1`. When the token is found it prints
`gotdocs: skipped ([gotdocs skip] in a commit message being pushed)` and exits 0.

Unlike pre-commit, pre-push does **not** record doc debt. The pre-commit hook has
already recorded anything these commits are carrying.

Trade-off against pre-commit: findings arrive later, when the fix means amending
several commits or adding a docs commit on top. Trade-off against CI: it is faster
and private. Keep both at `warn`; the hard gate belongs in CI.

See [dependencies/git.md](../dependencies/git.md#no-upstream) for what happens
when there is no upstream at all.

## CI

`.github/workflows/gotdocs.yml` defines **two jobs**, answering two different
questions.

### Job `check` — "does this change leave the documentation wrong?"

Display name `docs freshness`. Runs on `pull_request` (`opened`, `synchronize`,
`reopened`, `ready_for_review`, `labeled`, `unlabeled`), guarded by
`!contains(github.event.pull_request.labels.*.name, 'gotdocs-skip')`. Job
permissions are `contents: read` plus `pull-requests: write` — the latter only
for the sticky comment, and fork PRs get a read-only token regardless, so every
comment step tolerates failure.

Three gates:

```sh
bin/gotdocs lint
bin/gotdocs check --base "origin/$GITHUB_BASE_REF" --mode error
bin/gotdocs index && git status --porcelain -- .gotdocs/index.json .gotdocs/INDEX.md
```

1. **Frontmatter lints clean.**
2. **No stale docs** relative to the PR's merge base.
3. **The committed index matches the working tree** — regenerate, require no
   drift, then `git checkout --` the generated files back.

`--mode error` on gate 2 is about *collecting* a non-zero status, not about
policy. Each gate records its exit status into a step output instead of failing
immediately, so one run reports every problem.

**What actually blocks the pull request** is decided last, by the `Enforce` step,
from `enforce.ci`:

| `enforce.ci` | lint fails | check finds stale docs | committed index drifted |
| --- | --- | --- | --- |
| `off` / `warn` (**shipped**) | `::warning`, job green | `::warning`, job green | `::error`, **job red** |
| `error` | `::error`, job red | `::error`, job red | `::error`, job red |

The index gate is unconditional on purpose: `.gotdocs/index.json` and
`.gotdocs/INDEX.md` are committed generated files, and a stale copy makes every
later diff lie. That is a defect in the change set, not a documentation debt.
`enforce.ci` is read out of `.gotdocs/config.json` by a stdlib `python3` snippet
in the `Prepare` step, not a shell regex, because the config now has nested
`debt` and `publish` objects and a same-named key in one of them must not change
policy.

Reporting is two steps. `Build the report` turns the two `--json` payloads into a
markdown block — a `gate | result | blocking` table, then
`kind | doc | path | message | remediation` tables and the index diff — and
writes it to `$GITHUB_STEP_SUMMARY`. `Post the sticky pull request comment` then
`PATCH`es or `POST`s that same block as a single pull request comment, found on
re-runs by an HTML-comment marker (`gotdocs-report`) at the top of the body,
using `curl` and the API directly. It is skipped entirely on fork pull requests. When the job is advisory
the report says so in as many words:

```text
This job is **advisory** (`enforce.ci` is `warn`), so it is not failing the
pull request. The findings below are real; they will be recorded in
`.gotdocs/DEBT.md` when this lands on main.
```

### Job `record` — "what did we decide to live with?"

Runs on `push` to `main`, on non-forks, skipped when the head commit message
contains `[gotdocs skip]`. `permissions: contents: write`. It runs
`gotdocs debt record --base <push range> --source ci --resolve-absent`, then
`gotdocs debt render`, and commits `.gotdocs/debt.jsonl` and `.gotdocs/DEBT.md`
back to `main` as `chore(gotdocs): record doc debt [gotdocs skip] [skip ci]`.
It never fails the build. Full treatment in [doc-debt.md](doc-debt.md#the-two-ci-jobs).

This is the only part of gotdocs that writes to a repository.

### Three CI-specific requirements

- **Fetch depth.** `actions/checkout` defaults to a shallow clone with
  `fetch-depth: 1`; `REF...HEAD` then fails because there is no merge base. The
  workflow sets `fetch-depth: 0` and additionally fetches
  `+refs/heads/$BASE:refs/remotes/origin/$BASE` defensively.
- **Fork PRs.** `github.base_ref` is set on `pull_request` events including forks,
  but the checkout is of the merge commit and the token is read-only. `check` only
  reads, so this works; what does not work is any step that tries to push a
  regenerated index back. Do not add one — CI asserts the index is current, it
  never fixes it.
- **Python.** `actions/setup-python` pins `3.11`. Any 3.9+ works; pinning keeps
  the run reproducible.

The workflow does not pass `--strict`. Add it if you want an internal gotdocs
error to fail the build rather than warn — the argument for doing so is that
locally a broken gotdocs must never block a commit, while in CI a gotdocs that
silently does nothing is worse than a red build.

Failure diagnosis is in [runbooks/ci-check-failing.md](../runbooks/ci-check-failing.md).

## Escape hatches, and what each costs

There are six. Each is deliberately visible to someone other than the person
using it.

### 1. Edit the doc

Not really an escape hatch — it is the intended outcome. Cost: the time to write
the change, which is minimal because you have the context loaded right now.

### 2. `bin/gotdocs verify <doc-id>`

Asserts "I read this doc against the new code; it is still accurate." Writes
`verified_at` and `updated` into the file, so the assertion appears in the diff
with your name on the commit.

**Cost:** a reviewer can see you claimed it. Correct use is a refactor, rename or
performance change that did not alter documented behavior. Incorrect use — running
it to clear a finding you did not read — is invisible to the tool and obvious in
the next incident. See [runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md).

### 3. The skip token / `GOTDOCS_SKIP`

```sh
GOTDOCS_SKIP=1 git commit -m "wip: spike, throwing away"      # deterministic
git commit -m "wip: spike, throwing away [gotdocs skip]"       # best effort
```

`GOTDOCS_SKIP=1` is checked before anything else and always works. The
commit-message token is honored when `.git/COMMIT_EDITMSG` already holds the
pending message at hook time, which git does not guarantee — see the ordering
note above. Use the environment variable when it must work; use the token when
you want the decision recorded in history.

**Cost:** the token stays in the commit message forever and is greppable:

```sh
git log --oneline --grep='\[gotdocs skip\]' | wc -l
```

Legitimate uses: WIP commits on a branch you will squash, mechanical
whole-repo changes (license headers, formatter runs), and emergency fixes at 3am.
It does not skip the CI `check` job, which diffs the whole branch and never reads
a commit message — so a skipped commit resurfaces at the pull request, which is
the point. The one place CI does honor the token is the `record` job's `if:`
guard, so gotdocs' own ledger commit cannot re-trigger it.

### 4. `git commit --no-verify`

Skips every hook, not just gotdocs. **Cost:** it also skips your linters,
formatters and secret scanners, and it leaves no trace anywhere. Prefer the skip
token, which at least records that a decision was made.

### 5. The `gotdocs-skip` pull request label

The only way past CI. The `check` job's `if:` includes
`!contains(github.event.pull_request.labels.*.name, 'gotdocs-skip')`, so applying
the label skips the job entirely — including the always-blocking index gate. The
workflow listens for `labeled` and `unlabeled`, so it re-runs the moment the
label is removed.

**Cost:** it is on the pull request, visible to every reviewer and every person
who looks at the PR later, and it requires write access to the repo to apply.
That is the intended friction — this is the one escape hatch that cannot be used
quietly. Legitimate uses: a revert, a mass mechanical change, an incident fix
that will be followed by a docs PR.

### 6. Record it as doc debt

```sh
bin/gotdocs debt record --staged --source manual --note "covers is too broad; fixing in DOCS-214"
```

The honest version of "not now". The finding is written to
`.gotdocs/debt.jsonl`, appears in `.gotdocs/DEBT.md`, and keeps accruing an
`occurrences` count every time it is seen again. In `warn` mode the pre-commit
hook does this for you automatically.

**Cost:** it is a committed file that a reviewer, an audit or a retro can read,
and the count makes an old deferral progressively harder to defend. It does not
suppress the finding — `check` still reports it on the next commit. That is the
difference between this and the skip token: the token makes the finding go away,
the ledger makes it visible for longer. See
[runbooks/doc-debt-review.md](../runbooks/doc-debt-review.md).

### Not an escape hatch: deleting the doc

Deleting a doc removes its findings permanently. That is a legitimate move when
the doc describes something that no longer exists — but it is a reviewable change
in the diff, and `git log --diff-filter=D -- docs/` finds it later.

### Not an escape hatch: widening `ignore`

Adding a path to `ignore` in `.gotdocs/config.json` removes it from consideration
as a code path repo-wide, for every doc. It is the right move for generated code
and vendored trees. It is the wrong move for "this directory produces too many
findings" — that means the `covers` globs are too broad, which is a doc problem.
Config changes are reviewed like any other diff.

## Rollout

Turning on `error` everywhere on day one produces a wall of findings against docs
nobody has written yet, and the team learns to reach for `--no-verify`. Do it in
this order.

0. **Install as shipped.** `"enforce": { "pre_commit": "warn", "pre_push":
   "warn", "ci": "warn" }` is the default, and it is already a safe day-one
   state: nothing goes red, and the CI `record` job starts building
   `.gotdocs/DEBT.md` so you can *see* the size of the problem before you decide
   anything. Only the committed-index gate can fail a pull request.
1. **Or install with everything off.**
   `"enforce": { "pre_commit": "off", "pre_push": "off", "ci": "off" }`. Land the
   CLI, the config, the templates and the installer with zero output at all. Note
   that `off` also disables debt recording, so you learn nothing while you wait.
2. **Write covers for what exists.** Add frontmatter to whatever docs you already
   have and give them honest, narrow `covers`. Run
   `bin/gotdocs check --base origin/main~50` against recent history to see what
   the findings *would* have been. If a doc lights up on most commits, its
   `covers` is too broad — fix it now, before anyone is blocked by it.
3. **Pre-commit to `warn`.** Engineers start seeing which docs their changes
   touch. No one is blocked. Leave it here for a couple of weeks and watch the
   skip-token rate:
   `git log --since=2.weeks --grep='\[gotdocs skip\]' --oneline | wc -l`.
   A rising rate means the findings are not trusted; fix `covers`, do not tighten
   enforcement.
4. **CI to `error` once coverage is real.** "Real" is concrete: the core
   subsystems have docs, the docs have narrow `covers`, and a week of PRs
   produced findings that were mostly acted on rather than skipped. CI is the
   right place for the hard gate — it is visible to reviewers and it cannot be
   bypassed by a local flag.
5. **`require_coverage: true`, last, and only if you mean it.** This turns every
   changed file with no owning doc into a finding. In a repo of any age that is a
   large number. Turn it on for a subtree first by narrowing `roots` expectations
   and `ignore`, or accept that you are committing to documenting everything.

Recommended steady state for most teams: `pre_commit: warn`, `ci: error`,
`require_coverage: false`. Local nudges, hard gate at review time.

## Choosing modes per layer

- **`pre_commit: error`** is right for a small team that has already done the
  rollout and wants docs edited in the same commit as the code. It is wrong for a
  repo where people commit frequently as a form of note-taking.
- **`pre_commit: off`, `ci: error`** is right when the team uses many local hook
  managers and you do not want to fight them, or when commits are squashed anyway.
- **`ci: warn`** used to be a trap — nobody reads a green build's output. It is
  the shipped default now because the output no longer only goes to the build
  log: the `check` job writes a sticky pull request comment saying in plain words
  that it is advisory, and the `record` job commits the findings into
  `.gotdocs/DEBT.md` where they accumulate a visible occurrence count. Treat it
  as a *measurement* phase with a deadline, not a steady state. If you are not
  going to read `DEBT.md`, `ci: warn` is still a trap; use `off` or `error`.

---
id: 0004-ci-records-debt-instead-of-blocking
title: Enforcement defaults to warn and CI records doc debt instead of failing the build
type: decision
summary: enforce.pre_commit, pre_push and ci all default to warn; findings are written to a tracked debt ledger on push to main, and only the stale committed index fails a pull request.
covers:
  - .github/workflows/gotdocs.yml
  - .gotdocs/hooks/pre-commit
  - .gotdocs/hooks/pre-push
  - tools/gotdocs/debt.py
  - tools/gotdocs/check.py
symptoms:
  - gotdocs reported stale docs and the commit went through anyway
  - the CI job printed findings but the pull request is still green
  - check exits 0 even though it listed three stale documents
  - a bot commit keeps appearing on main touching .gotdocs/DEBT.md
  - the only gotdocs failure I ever see is about the index being out of date
  - our pull request went red on gotdocs after somebody changed a config file
  - findings I ignored last month are listed in a file with occurrence counts
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - enforcement
  - ci
  - adoption
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Enforcement defaults to warn and CI records doc debt instead of failing the build

## Context

A repository adopting gotdocs on top of years of undocumented code gets hundreds
of findings on the first run. If that first run is blocking, exactly one thing
happens: someone sets the mode to `off`, or deletes the hook, and the system is
gone on day one. If it is merely advisory, the second thing happens: the
warnings scroll past on every commit and nobody reads them after the first week.

Both failure modes are about the *same* missing piece — there is no record. A
finding is either blocking (and gets bypassed) or transient (and gets forgotten).
What is missing is a third state: knowingly accepted, written down, and countable.

## Decision

Every enforcement context defaults to `warn`:
`{"enforce": {"pre_commit": "warn", "pre_push": "warn", "ci": "warn"}}`. In
`warn`, findings are printed and the run exits 0. Only an explicit `error` makes
gotdocs block, and raising it is a deliberate act by a repo that has caught up.

The second half is the ledger. On push to `main`, the `record` job runs
`bin/gotdocs debt record --base <before> --source ci --resolve-absent` followed by
`bin/gotdocs debt render`, writing `.gotdocs/debt.jsonl` and `.gotdocs/DEBT.md`
and committing them back with `[gotdocs skip] [skip ci]`. A finding accepted
instead of fixed becomes a tracked entry with an occurrence count and a
first/last-seen date, not a lost warning.

One gate is unconditional: a committed `.gotdocs/index.json` or
`.gotdocs/INDEX.md` that does not match what the tree generates fails the pull
request in every mode. That is not documentation debt — it is a generated file in
the change set that is wrong, which makes every later diff lie.

## Expected behavior

- In the default `warn` mode nothing is blocked: `bin/gotdocs check` prints its
  findings and exits 0. Raising the mode to `error` is what makes it block.

  ```console
  $ bin/gotdocs check --paths tools/gotdocs/globs.py
  gotdocs: 3 findings (mode: warn)

  stale (3)
    ...
  $ echo $?
  0
  ```

- The same run with the mode raised exits 1:

  ```console
  $ bin/gotdocs check --paths tools/gotdocs/globs.py --mode error
  $ echo $?
  1
  ```

  `CheckResult.exit_code()` returns 1 only when `mode == "error"` **and** there
  is at least one finding.
- On a pull request the CI job renders a job summary and a single sticky comment
  (found by its `gotdocs-report` HTML-comment marker and edited in place on
  re-runs) that says, in
  `warn`: "This job is **advisory** (`enforce.ci` is `warn`), so it is not
  failing the pull request." The gate table shows `blocking: no` for lint and
  freshness, and `blocking: yes` for the committed index.
- The index gate is the only unconditional failure. With `enforce.ci: warn`, a
  pull request with stale docs is green; a pull request that forgot to run
  `bin/gotdocs index` is red with
  `::error::.gotdocs index is out of date; run 'bin/gotdocs index' and commit`.
- The `gotdocs-skip` label on a pull request skips the whole `check` job. The
  `[gotdocs skip]` token in a commit message, or `GOTDOCS_SKIP=1` in the
  environment, skips a local run.
- The `record` job never fails the build. A ledger that cannot be pushed after
  three rebase attempts emits `::warning::could not push the doc-debt ledger`
  and exits 0, because the ledger is regenerated from the tree on the next push.
- `.gotdocs/debt.jsonl` is one JSON object per line, keyed by a stable digest of
  `(kind, doc_id, path)`. Recording the same finding again bumps `occurrences`
  and `last_seen`; it never appends a second line. Every date comes from a git
  commit date, never the wall clock, so re-running the job produces identical
  bytes.
- The pre-commit hook in `warn` mode records what the commit is being allowed to
  carry into the local ledger. In `off` mode it records nothing.

## This is a bug, not this decision, if...

- `bin/gotdocs check --mode error` exits 0 while printing findings. Advisory
  behaviour in `warn` is this decision; ignoring an explicit `error` is a bug in
  `CheckResult.exit_code()`.
- `bin/gotdocs check` in `warn` mode exits non-zero. The only non-zero exits from
  a `warn` run should be usage (2) or environment (3) errors.
- `bin/gotdocs lint` exits 0 with findings — `lint` returns `EXIT_USAGE` (2) when
  it has any finding, independent of `enforce.*`. Lint findings are malformed
  documents, not debt.
- The `record` job fails the build for any reason. Look for a missing `|| true`
  or a `set -e` path in `.github/workflows/gotdocs.yml`.
- The ledger grows a second line for a finding already open — that is
  `debt.entry_id` not being stable across runs, in `tools/gotdocs/debt.py`.
- A finding that has been fixed stays `open` after a `debt record
  --resolve-absent` run that examined the relevant paths. Note the scoping: a
  narrow push that never looked at a path must *not* resolve entries for it;
  that is correct, not a bug.
- The doc-debt commit triggers the workflow again. `[gotdocs skip]` is read by
  the `record` job's `if:` guard and `[skip ci]` by GitHub; losing either causes
  a loop, and that is a bug in the workflow.
- The sticky comment is duplicated on every run instead of edited. The marker
  lookup is in the `Post the sticky pull request comment` step; a fork pull
  request skipping the comment entirely is by design (read-only token), a
  duplicate is not.
- The index gate goes red on a clean tree. `bin/gotdocs index` preserves the
  `generated_at_sha` already on disk when nothing else changed, precisely so a
  commit that cannot contain its own sha does not make the gate permanently red.
  If a no-op `bin/gotdocs index` produces a diff, that is a bug in
  `tools/gotdocs/index.py`.

## Consequences

The default configuration does not prevent a single stale document from landing.
A repo that never raises `enforce.*` to `error` gets a very good record of its
documentation debt and no enforcement of it — and "we'll turn it on later" is a
sentence that ages badly. The ledger is the counterweight, but it only works if
somebody reads `.gotdocs/DEBT.md`.

The `record` job needs `contents: write` and pushes bot commits to `main`,
which some repositories will not accept and which adds noise to the history of
`main`. The rebase-and-retry loop gives up after three attempts by design.

The index gate being the one hard failure means the first gotdocs failure most
contributors ever see is about a generated file rather than about documentation,
which reads as bureaucratic until it is explained.

## Alternatives considered

- **Block by default (`error` everywhere).** Rejected: the observed adoption
  failure mode. A repo with 200 undocumented files goes red on the first pull
  request and the tool is removed the same day.
- **Warn only, no ledger.** Rejected: this is the "warnings nobody reads" failure.
  Without a record, a finding accepted on Tuesday is indistinguishable from a
  finding that never existed.
- **Ratchet on a count (fail if debt increased).** Considered seriously and
  deferred, not rejected on principle. It needs the ledger to exist and be
  trusted first; layering a ratchet on top is a small change once
  `.gotdocs/debt.jsonl` is stable. A ratchet built before the ledger would have
  had nothing to count.
- **File a GitHub issue per finding.** Rejected: needs write scope on issues, is
  not reproducible, produces hundreds of issues on adoption day, and puts the
  record outside the repo — contradicting 0001.
- **Make the index gate advisory too.** Rejected: a committed generated file that
  disagrees with the tree is a defect in the change set, not documentation debt.
  Letting it through makes every subsequent diff misleading.

## Revisit when

Revisit when the repository's `.gotdocs/DEBT.md` stops growing — that is the
signal the team has caught up and `enforce.ci` should be raised to `error`, at
which point the ratchet alternative becomes the interesting one. Also revisit if
the bot commits on `main` become an operational problem; the alternative shape is
recording the ledger on a separate branch or as a workflow artifact.

## References

- `.github/workflows/gotdocs.yml` — the `check` and `record` jobs, the
  `Enforce` step, and the header comment stating the policy.
- `tools/gotdocs/check.py` — `CheckResult.exit_code()`, `skip_requested()`.
- `tools/gotdocs/config.py` — `MODES = ("off", "warn", "error")`,
  `ENFORCE_CONTEXTS`, and the comment on why `ci` defaults to `warn`.
- `tools/gotdocs/debt.py` — `entry_id`, `record_findings`, `resolve_absent`,
  `render_markdown`.
- `.gotdocs/hooks/pre-commit` — the local half, including ledger recording in
  `warn` mode.
- `docs/enforcement.md` — the rollout order for raising the modes.

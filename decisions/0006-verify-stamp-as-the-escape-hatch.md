---
id: 0006-verify-stamp-as-the-escape-hatch
title: The escape hatch is a recorded verify stamp, not a suppression flag
type: decision
summary: An impacted doc is satisfied by editing it or by running gotdocs verify, which stamps verified_at with the head sha and updated with today's date into the tracked file.
covers:
  - tools/gotdocs/check.py
  - tools/gotdocs/gitutil.py
symptoms:
  - I ran gotdocs verify and the stale finding disappeared
  - verified_at holds a git sha that is not the commit I just made
  - the doc says verified_at 3d8b6cd and updated today but the body did not change
  - there is no ignore or nocheck comment I can put in a doc to silence it
  - verify refuses to run in a repository with no commits
  - editing the doc in the same commit satisfies the check without running anything
  - a short sha in verified_at still matches a full-length head sha
  - I verified a doc and now the committed index is out of date
  - verified_at is ignored when the change set comes from --paths
  - gotdocs verify did not clear the stale finding under check --paths
  - the PostToolUse reminder still names a doc I just verified
  - summary.head is null and no verified_at ever satisfies the check
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - enforcement
  - verify
  - staleness
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# The escape hatch is a recorded verify stamp, not a suppression flag

## Context

0003 guarantees false positives: a whitespace change to a covered file reports
every document that names it. So an escape hatch is mandatory — without one the
tool is unusable, and people will build their own by deleting the hook.

The question is what shape the hatch takes. The obvious shapes are all
suppressions: a `# gotdocs: ignore` comment, a `--no-verify`-style flag, an
allowlist file. Every one of them has the same defect — it silences the signal
permanently and leaves no evidence that a human ever looked. Six months later
nobody can distinguish "reviewed and still accurate" from "suppressed in a hurry
in 2024".

## Decision

An impacted document is **satisfied** in exactly two ways, checked in this order
in `tools/gotdocs/check.py`:

1. the document's own file is in the change set — the author edited it alongside
   the code; or
2. its `verified_at` matches the head sha of the change set — the author ran
   `bin/gotdocs verify <id>`.

`bin/gotdocs verify` writes `verified_at: <short head sha>` and
`updated: <today>` into the document's frontmatter and nothing else. The stamp is
a tracked change in the working tree: it shows up in `git diff`, it is reviewed
in the pull request, and `git log -S` finds every time it was used. There is no
suppression comment, no ignore list and no per-doc opt-out anywhere in the
format.

## Expected behavior

- `bin/gotdocs verify <id>` writes `verified_at` and `updated` into the document
  and exits 0; the stale finding for that document is gone on the next run.

  ```console
  $ bin/gotdocs verify doc-format
  verified doc-format  docs/doc-format.md  verified_at=3d8b6cd updated=2026-08-14
  Now run: bin/gotdocs index && git add docs/doc-format.md
  ```

  Exit 0. The `git diff` on that file is exactly two lines (see 0005).
- `bin/gotdocs verify --all-impacted` takes its targets from a staged
  `check` run's `stale` findings, deduplicated, and refuses to be combined with
  explicit ids:
  `--all-impacted takes the doc ids from 'check'; do not also name them`.
  With nothing impacted it prints `gotdocs: nothing impacted, nothing to verify`
  and exits 0.
- An unknown id is a hard error, not a silent no-op:
  `no document with id 'typo-here'; run 'bin/gotdocs lint' or 'bin/gotdocs status'`.
- In a repository with no commits, `verify` raises `EmptyRepoError`:
  `repository has no commits yet, so there is no sha to record in verified_at`.
  There is no fallback stamp, because a stamp that names no commit means nothing.
- Sha comparison is prefix equality on the shorter of the two values with a
  seven-character floor (`check.sha_satisfies`), so a short `verified_at:
  3d8b6cd` satisfies a long head sha and vice versa. Below seven characters it
  degrades to exact equality rather than matching loosely.
- Editing the document is always sufficient — clause 1 is checked before the sha.
  A normal doc update needs no `verify` at all.
- The stamp is scoped to one commit. `verified_at: 3d8b6cd` satisfies the check
  only while the head sha of the change set is that commit; the next change to a
  covered file reports the document again. There is no way to say "never report
  this document".
- **`bin/gotdocs verify` cannot clear a stale finding reported by
  `check --paths`; `verified_at` is inert for that change set.** `check --paths`
  is a hypothetical — "if I touch these files, what breaks?" — so it
  deliberately does not consult git at all and works in a directory with no
  commits. That
  means the change set has *no head sha* (`summary.head` is `null`), and
  `sha_satisfies(verified_at, None)` is False for every document. So a document
  you just verified is still reported by `check --paths` and by the
  `impacted`-driven PostToolUse reminder, and stops being reported only once the
  edit is staged and `check --staged` (or `--base`) resolves a real head. Under
  `--base`, the stamp satisfies the check only when it names that base's head,
  which is usually not the commit `verify` stamped. This is the price of
  `--paths` being git-free; it is not a `verify` failure.
- `verify` moves `updated` to the local calendar date via
  `datetime.date.today().isoformat()`, so the frontmatter also records *when* a
  human last looked, independent of the sha.
- Because frontmatter changed, `.gotdocs/index.json` and `.gotdocs/INDEX.md` are
  now out of date — hence the printed `bin/gotdocs index` reminder, and the
  always-blocking index gate from 0004.

## This is a bug, not this decision, if...

- Editing a document in the same change set does **not** satisfy its own stale
  finding. Clause 1 (`doc.path in changed_set`) must fire before the sha check;
  a stale finding on a document that is itself in the diff is a bug in
  `tools/gotdocs/check.py`.
- `bin/gotdocs verify <id>` runs, `git diff` shows the stamp, and
  `bin/gotdocs check --staged` still reports that document as stale after the
  file is staged — the stamp did not clear the finding. Either `sha_satisfies`
  or `resolve_head` is wrong. **Read the
  change-set source before concluding this**: the same symptom under
  `check --paths` or `check --base` is the intended behaviour described above,
  not a bug. Check `summary.head` in `--json` — `null` means no sha was
  available to satisfy, so the stamp could not have applied.
- `sha_satisfies("3d8b6cd", "3d8b6cd1f2e...")` returns False, or
  `sha_satisfies("3d8b6cd", "3d8b6ce...")` returns True. Prefix matching on the
  shorter value with a 7-char floor is the contract.
- `verify` writes a sha that is not `git rev-parse --short HEAD` at the moment it
  ran — for instance a sha from an unrelated worktree or a stale cached value.
- `verify` on several ids stamps some and leaves others when one fails; each
  document is rewritten independently and an unknown id is rejected *before* any
  rewrite (targets are all resolved first).
- `verify` silently succeeds against an id that does not exist, or against a path
  outside the configured roots.
- A document is satisfied by a `verified_at` that was never produced by `verify`
  — e.g. any 7-hex-character string that happens to prefix-match. That is
  correct behaviour for a hand-written short sha; it is a bug only if the value
  is not a prefix of the actual head sha.
- Note what is **not** a bug: `verified_at` naming the *parent* commit rather than
  the commit being created. A commit cannot contain its own sha. During
  `--staged` enforcement the head sha of the change set is HEAD, so stamping HEAD
  is correct; after the commit lands, the document is satisfied by clause 1
  because the stamped file was part of that commit.

## Consequences

`verify` is easy, and easy things get done reflexively. Nothing in the tool can
tell a considered "I read the diff and the doc is still accurate" from a
mechanical `verify --all-impacted` run to clear the output. The only defences are
social — the stamp is visible in review — and analytical: a debt ledger where
`stale` entries are almost always closed by a `verify` with no accompanying body
edit is the measurable version of that failure.

Every verify produces frontmatter churn in the diff and an index regeneration.
On a busy repo, `updated:` and `verified_at:` lines move constantly, which adds
noise to `git blame` on the frontmatter block.

The stamp also expires immediately: it satisfies exactly one head sha, so a
document with a wide `covers` glob gets re-reported on the very next commit that
touches anything it names. That is intentional pressure toward narrower globs,
and it is also the most common complaint.

## Alternatives considered

- **An ignore comment in the document (an HTML comment reading
  `gotdocs: ignore`).** Rejected:
  permanent, invisible after the review that introduced it, and indistinguishable
  from a reviewed document. It removes the document from the system forever in
  exchange for one quiet commit.
- **A `--no-verify`-style CLI flag that suppresses findings.** Rejected: leaves
  no artifact at all. `GOTDOCS_SKIP=1` and the `[gotdocs skip]` commit token
  exist for whole-run emergencies and are deliberately coarse and visible in the
  commit message — they are not a per-document hatch.
- **An expiry date (`verified_until: 2026-12-01`).** Rejected: a date does not
  answer the question the check asks. A document can be wrong the day after it is
  verified if the code changed; a document can be right for three years if it did
  not. The sha ties the claim to a specific state of the code, which is the claim
  actually being made.
- **A content hash of the covered files instead of a sha.** Rejected: same
  information as the sha for this purpose, but it churns on every covered-file
  change and makes the frontmatter unreadable.
- **A central allowlist file of (doc, path) pairs.** Rejected: it is a second
  place to keep in sync with `covers`, and it accumulates entries nobody dares
  delete.

## Revisit when

Revisit if the ledger shows `stale` entries overwhelmingly resolved by `verify`
with no body edit in the same commit — that means the hatch has become the
default path and the signal is being laundered rather than answered. The fix at
that point is not to remove the hatch but to make the finding more precise
(narrower `covers`, or diff-aware reporting), because a hatch that is hard to use
gets replaced by deleting the hook.

## References

- `tools/gotdocs/check.py` — `sha_satisfies`, the two satisfaction clauses in
  `run_check`, `resolve_head`.
- `tools/gotdocs/cli.py` — `cmd_verify`, `--all-impacted`, the `EmptyRepoError`
  guard.
- `tools/gotdocs/frontmatter.py` — `rewrite_fields` and `WRITABLE_KEYS`, which
  make the stamp a two-line diff.
- `tools/gotdocs/gitutil.py` — `head_sha_or_none(short=True)`.
- `runbooks/stale-doc-triage.md` — how to decide, per document, between editing,
  verifying, narrowing `covers`, and deleting.

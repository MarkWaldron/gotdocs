---
id: runbook-doc-debt-review
title: "Runbook: Working the Doc-Debt Ledger Down"
type: runbook
summary: DEBT.md has grown and nobody is reading it — triage order by finding kind, the four decisions per entry, when to fix versus close as won't-do, and how to stop it refilling.
covers:
  - tools/gotdocs/debt.py
owners: ["@mark"]
tags: [runbook, debt, triage, maintenance]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Runbook: Working the Doc-Debt Ledger Down

**Symptom:** `.gotdocs/DEBT.md` has dozens of entries, some with a high `seen N×`
count, and nobody has looked at it in weeks. Or: you are about to move
`enforce.ci` to `error` and need to know what will go red.

**This is not an incident.** Nothing is broken. The ledger doing its job is the
list getting long — that is the measurement. What follows is how to work it down
in an hour without turning it into a project.

Concepts, formats and the CI wiring are in [docs/doc-debt.md](../docs/doc-debt.md).
This runbook is the procedure.

## 0. Get the shape before you read any entry

```sh
bin/gotdocs debt stats
```

```text
gotdocs: 7 open, 1 resolved, 7 occurrence(s) recorded  (.gotdocs/debt.jsonl)

open by kind
  stale                7

worst offenders
  0002-python-stdlib-only-cli              1
  0003-covers-globs-as-the-staleness-signal 1
  0004-ci-records-debt-instead-of-blocking 1
  0006-verify-stamp-as-the-escape-hatch    1
  cli-reference                            1
```

Two numbers decide how you spend the hour.

**Open by kind.** These are different problems and they are not equally
expensive. Work them in the order below, not in the order they appear in the
file.

**Worst offenders.** If one document owns a third of the open entries, that
document is the whole problem and everything else is noise. Fix it first and
recount.

## 1. Triage order

Cheapest and most mechanical first. Every one of these removes entries without
requiring anyone to make a judgement call about prose.

### 1. `index_out_of_date` — always first, always free

```sh
bin/gotdocs index
git add .gotdocs/index.json .gotdocs/INDEX.md
```

One command clears every entry of this kind. It should never have been in the
ledger for more than a commit, and if it is there repeatedly, somebody's local
workflow is not running `index` — that is a hook installation problem, not a
docs problem. Check with `bin/gotdocs status`.

### 2. `lint` — a broken file, not a stale one

```sh
bin/gotdocs lint
```

Every message carries `file:line`. These are missing fields, malformed dates,
unsupported YAML — mechanical fixes, minutes each, and they block nothing else
from being trusted. A `lint` entry in the ledger means a document is
*unparseable*, so whatever it claims to cover is not being checked at all. That
makes these more urgent than their triviality suggests.

### 3. `duplicate_id` — two documents fighting over a handle

```sh
bin/gotdocs debt list --kind duplicate_id
```

Rename one. Pick the one with fewer inbound references; `grep -rn '<id>' .`
answers that. Then `bin/gotdocs index`.

### 4. `deprecated_edit` — decide, do not defer

Someone edited a `status: deprecated` document. There are exactly two honest
outcomes and both take one line: delete the file, or set `status: current`
because it turned out to be worth keeping. "Leave it deprecated and keep editing
it" is not one of them, and the finding will recur every time it is touched.

### 5. `stale` — the real work, and where judgement is needed

This is the kind that needs someone who understands the subject. Everything
above was housekeeping so that this list is the only one left. Sort by
`seen N×` and start at the top.

### 6. `uncovered` — only if `require_coverage` is on

If you turned this on and got a wall, turn it back off. It is the last step of
the rollout, not a triage target, and grinding through it before the `stale`
list is clean is the wrong order. See
[docs/enforcement.md](../docs/enforcement.md#rollout).

## 2. Per entry: four decisions

For each `stale` entry, read the ledger line — it already contains the document,
the code path, and the command that clears it:

```text
- **cli-reference**  ·  `docs/cli-reference.md`  ·  tools/gotdocs/check.py changed and is covered by tools/gotdocs/** (and 1 other file)  ·  seen 1× since 2026-08-15  ·  fix: `update docs/cli-reference.md, or run: bin/gotdocs verify cli-reference`  ·  id `0161a4b49144`
```

Then read the actual diff for the covered paths since `first_seen_sha`:

```sh
bin/gotdocs debt list --doc cli-reference --json |
  python3 -c 'import json,sys; e=json.load(sys.stdin)["filtered"][0]; print(e["first_seen_sha"], e["occurrences"])'

git diff <first_seen_sha>..HEAD -- tools/gotdocs/
```

Now pick one of four.

### A. Fix it — the documented behaviour actually changed

Edit the document. This is the default and it is what the ledger exists to
prompt. The entry closes on the next `debt record --resolve-absent`, because the
document's file is now in the change set and `check` stops reporting it.

### B. Verify it — the code changed, the prose did not

```sh
bin/gotdocs verify cli-reference
git add docs/cli-reference.md
```

Legitimate for a refactor, a rename, a performance fix. It is an assertion with
your name on the commit, so **read the document first**. Clearing a ledger entry
you did not read is the exact failure the ledger was built to expose, and it is
invisible to the tool and obvious in the next incident. See
[stale-doc-triage.md](stale-doc-triage.md).

### C. Fix the `covers` glob — the finding is noise

An entry with a high `seen N×` count against a broad glob is usually not debt at
all. `src/**` on an architecture document means it is impacted by every commit,
and the ledger has been faithfully recording a measurement error.

Narrow the glob, then close the entry explicitly, because it will not
auto-resolve — the document is still real, the finding just stopped applying:

```sh
bin/gotdocs debt resolve cli-reference --note "covers narrowed to tools/gotdocs/cli.py; was matching the whole package"
```

`seen 20× since March` against one glob is the single strongest signal in the
ledger. Treat it as a bug report about the `covers` list, not as a backlog item.

### D. Close it as won't-do

The honest option, and it needs the note filled in.

```sh
bin/gotdocs debt resolve 8e37464a483a --note "subsystem is being deleted in Q4; documenting it now is waste"
```

**Close as won't-do when:**

- The code the document covers is scheduled for deletion, and the deletion is
  real (a ticket, a date), not aspirational.
- The document describes a subsystem nobody owns and nobody is changing; the
  drift is theoretical.
- The finding is a duplicate in substance of another entry you are already
  fixing.
- The document should be deleted rather than updated — delete it, which removes
  its findings permanently, and note *that* as the reason.

**Do not close as won't-do when:**

- You do not understand the diff. That is "ask the author", not "won't do".
- It is the third time this quarter. A recurring entry is a decision the team
  keeps not making; escalate it instead of closing it again — it will be
  reopened automatically, with `first_seen_*` intact, the next time it is seen,
  and the ledger will show exactly how long that has been going on.
- The reason is "we are busy". That is what leaving it open means.

An entry closed as won't-do and later seen again is **reopened**, not
re-created: the id and `first_seen_date` are preserved and `occurrences` keeps
counting. You cannot quietly reset the clock on it.

## 3. Regenerate and commit

```sh
bin/gotdocs debt render
git add .gotdocs/debt.jsonl .gotdocs/DEBT.md
```

Commit the ledger with the fixes, in the same commit where possible, so the diff
shows the work and the bookkeeping together.

You do not have to do this by hand on `main`: the CI `record` job re-runs
`debt record --resolve-absent` and `debt render` on every push and commits the
result. Doing it locally just means your branch is not fighting the bot.

## 4. Stop it refilling

Working the list down is pointless if it is back next month. Three checks:

**Is the ledger recording things that are not debt?** Look at the kind
distribution. A ledger dominated by `index_out_of_date` means hooks are not
installed (`bin/gotdocs status`, then `sh scripts/install-gotdocs.sh`). A ledger
dominated by `lint` means somebody is authoring documents without running
`bin/gotdocs lint`.

**Are the `covers` globs honest?** The single most common cause of a growing
ledger is a handful of documents with globs so broad that every commit impacts
them. `bin/gotdocs debt stats` names them under "worst offenders" — that list is
the fix list.

**Is `warn` doing anything?** If the ledger grows every week and nobody reads it,
`warn` has become the thing it was meant to prevent. Two options, and both are
better than the current state: move `enforce.ci` to `error` so findings block at
review time, or move it to `off` and stop pretending. See
[docs/enforcement.md](../docs/enforcement.md#choosing-modes-per-layer).

Before flipping to `error`, dry-run the blast radius:

```sh
bin/gotdocs check --base origin/main --mode error --json |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["summary"]["findings"], "findings would block")'
```

## 5. Narrowing what gets recorded

If a whole finding kind is not worth tracking in your repo, take it out of the
ledger rather than closing entries forever:

```json
"debt": { "record_kinds": ["stale", "lint", "duplicate_id"] }
```

Existing entries of a removed kind stay in the file and stay open — this changes
what gets *recorded*, not what has been. Close them with `debt resolve` if you
want them gone.

That holds under auto-resolve too. `record_kinds` (and `--kinds`) filters which
findings may **open** an entry; `--resolve-absent` and `debt resolve --auto`
always read the check's full, unfiltered finding list. An entry only closes
because the check stopped reporting it, never because a filter hid it. The CI
`record` job runs `--resolve-absent` on every push to `main`, so this is the
difference between "we stopped tracking this kind" and "we deleted every entry
of this kind".

To stop recording entirely without losing history:

```json
"debt": { "enabled": false }
```

## Related

- [docs/doc-debt.md](../docs/doc-debt.md) — the ledger format, dedup and auto-resolve semantics
- [stale-doc-triage.md](stale-doc-triage.md) — edit versus verify, per document
- [docs/enforcement.md](../docs/enforcement.md) — modes, escape hatches, rollout order
- [adopting-gotdocs-in-an-existing-repo.md](adopting-gotdocs-in-an-existing-repo.md) — day one, when the ledger is at its largest

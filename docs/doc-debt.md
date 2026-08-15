---
id: doc-debt
title: Doc Debt — The Ledger of What You Agreed to Live With
type: doc
summary: Why warn mode needs a ledger, the JSONL format and its dedup and auto-resolve rules, the two CI jobs, and how to read DEBT.md. CI does not block unless enforce.ci is error.
covers:
  - tools/gotdocs/debt.py
  - .github/workflows/gotdocs.yml
owners: ["@mark"]
tags: [debt, ledger, ci, jsonl, adoption]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Doc Debt — The Ledger of What You Agreed to Live With

`gotdocs check` answers one question: *is this change set clean right now?* That
answer is close to useless for a repository adopting gotdocs on top of ten years
of undocumented code. The first run reports hundreds of findings, somebody sets
`enforce.pre_commit: warn` so work can continue, and from that moment the
warnings scroll past and nobody reads them again. The tool is installed, green,
and doing nothing.

The ledger is the other half. A finding that is knowingly deferred gets
**recorded once**, tracked across commits with a count of how many times it has
been seen, and reported as a short bounded list that a human can actually work
through.

The distinction that matters:

- The **skip token** makes a finding go away. Nothing is left except a string in
  a commit message.
- The **ledger** makes a finding stay visible for longer than the terminal
  session it appeared in. `check` still reports it tomorrow; the ledger just
  remembers that you saw it, when you first saw it, and how many times since.

## CI does not block by default

Say this out loud because it is the thing people get wrong: **the shipped
configuration does not fail anybody's pull request.**

```json
"enforce": { "pre_commit": "warn", "pre_push": "warn", "ci": "warn" }
```

`enforce.ci` is the single knob. While it is `off` or `warn`, the CI job runs
every gate, writes a full report into the job summary and a sticky pull request
comment, records the findings into the ledger — and stays green. Set it to
`error` and the same findings fail the job. Nothing else changes; there is no
second switch, no per-rule severity, no allowlist.

```json
"enforce": { "pre_commit": "warn", "pre_push": "warn", "ci": "error" }
```

The one exception is the committed-index gate. `.gotdocs/index.json` and
`.gotdocs/INDEX.md` are generated files that are checked in, so a stale copy
makes every later diff lie. That is a defect in the change set, not a
documentation debt, and it fails the job at any `enforce.ci`. See
[enforcement.md](enforcement.md#ci).

The advisory case says so in the pull request comment, in as many words:

```text
This job is **advisory** (`enforce.ci` is `warn`), so it is not failing the
pull request. The findings below are real; they will be recorded in
`.gotdocs/DEBT.md` when this lands on main.
```

## The ledger file

`.gotdocs/debt.jsonl`. One JSON object per line, sorted, LF-terminated, written
via a temp file and `os.replace`.

```text
{"entry_id":"0161a4b49144","kind":"stale","doc_id":"cli-reference","path":"docs/cli-reference.md","message":"tools/gotdocs/check.py changed and is covered by tools/gotdocs/** (and 1 other file)","remediation":"update docs/cli-reference.md, or run: bin/gotdocs verify cli-reference","status":"open","occurrences":1,"first_seen_date":"2026-08-14","first_seen_sha":"3d8b6cd","last_seen_date":"2026-08-14","last_seen_sha":"3d8b6cd","resolved_date":null,"resolved_sha":null,"note":"manual"}
```

| Field | Meaning |
| --- | --- |
| `entry_id` | 12 hex digits. A SHA-1 over `kind\0doc_id\0path`, truncated. Stable across runs, machines and Python versions. |
| `kind` | The `check` finding kind: `stale`, `uncovered`, `lint`, `duplicate_id`, `deprecated_edit`, `index_out_of_date`. |
| `doc_id`, `path` | Which document or code path the finding is anchored to. `doc_id` is `null` for findings that are not about a specific document. |
| `message`, `remediation` | Refreshed from the newest sighting. Not part of the identity. |
| `status` | `open` or `resolved`. |
| `occurrences` | How many times this finding has been recorded. |
| `first_seen_date` / `first_seen_sha` | When it entered the ledger. Never rewritten. |
| `last_seen_date` / `last_seen_sha` | The most recent sighting. |
| `resolved_date` / `resolved_sha` | Set when closed, cleared when reopened. |
| `note` | Free text. Defaults to the `--source` value (`manual`, `hook`, `ci`). |

### Why JSONL and not JSON

Four reasons, in order of weight.

1. **A corrupt line costs one entry, not the ledger.** `load_ledger` parses each
   line independently. A line that is not valid JSON, or is not an object, or is
   missing `kind`, is skipped and reported as a `LedgerError` on stderr and in
   `--json`'s `ledger_errors`. A single JSON array would fail to parse in its
   entirety, and a documentation tool that loses the record of what a team agreed
   to live with because one byte got mangled in a merge is not a tool anybody
   trusts twice.
2. **It diffs per entry.** One finding recorded is one line added. In a single
   JSON array, adding an entry near the top reindents nothing but still forces
   the reviewer to read structure; with pretty-printing it churns brackets and
   trailing commas. A per-line format makes `git diff` on the ledger legible, and
   the ledger is a reviewed, committed file.
3. **It merges.** Two branches that each recorded a different finding produce a
   line-level conflict at worst, and often none at all. Two branches that each
   appended to a JSON array conflict on the closing bracket every time.
4. **It appends cheaply and reads streamably.** Nothing here needs the whole file
   in memory, and a repo with a thousand open items still `grep`s in one pass.

The cost is real and accepted: JSONL is not directly readable by a tool that
expects a JSON document, and it has no place for a file-level header. Both are
handled by `.gotdocs/DEBT.md` (for humans) and `bin/gotdocs debt list --json`
(for machines), which reconstitute a proper JSON payload with a `version` and a
`summary` on demand.

### Determinism

Nothing in `debt.py` reads a clock. Every date is supplied by the caller, and the
CLI supplies HEAD's **commit** date (`git log -1 --format=%cd --date=short`), not
today's. The same inputs therefore produce the same bytes on every machine on
every day, which is what makes it safe for CI to regenerate the ledger and commit
the result without churning the diff. `write_ledger` returns `False` when the
bytes are unchanged, so a no-op run commits nothing.

Override both stamps explicitly when you need to:

```sh
bin/gotdocs debt record --paths src/api/routes.py --date 2026-08-14 --sha 3d8b6cd
```

## Dedup

Identity is `(kind, doc_id, path)` — deliberately **not** the message. Rewording
a finding, or a `stale` message going from "covered by `src/**`" to "covered by
`src/**` (and 3 other files)", must not fork its history.

Recording the same finding again while it is open bumps `occurrences` and
`last_seen_*` **in place**. No second line is ever appended:

```text
$ bin/gotdocs debt record --paths tools/gotdocs/check.py tools/gotdocs/globs.py
gotdocs: debt recorded  (+8 new, ~0 seen again, ^0 reopened, -0 resolved)
         ledger .gotdocs/debt.jsonl @ 2026-08-15 2b4ca2e
         8 open, 0 resolved, 8 occurrence(s) recorded

$ bin/gotdocs debt record --paths tools/gotdocs/check.py tools/gotdocs/globs.py
gotdocs: debt recorded  (+0 new, ~8 seen again, ^0 reopened, -0 resolved)
         ledger .gotdocs/debt.jsonl @ 2026-08-15 2b4ca2e
         8 open, 0 resolved, 16 occurrence(s) recorded
```

The four counters in that header are the whole state machine:

- **`+N new`** — an id not in the ledger. A fresh open entry, `occurrences` 1.
- **`~N seen again`** — already open. `occurrences` + 1, `last_seen_*` updated.
- **`^N reopened`** — was `resolved` and has come back. `resolved_*` cleared,
  status back to `open`, `occurrences` + 1. `first_seen_*` is **not** reset: a
  finding that keeps returning should look old, because it is.
- **`-N resolved`** — closed this run, by `--resolve-absent`.

`occurrences` counts **runs that reported the finding**, not findings. Everything
one `record` call maps to the same entry is a single sighting: five copies of the
same finding, or four different lint errors in one document, are one entry with
`occurrences: 1` after one call. The findings the single line does not quote are
counted in the message — `... (+3 more findings on this document)` — so the
bounded report does not hide them.

## Auto-resolve

```sh
bin/gotdocs debt record --base "$BASE" --source ci --resolve-absent
```

`--resolve-absent` closes open entries that this run no longer reports. Without
it, the ledger only ever grows.

The danger is obvious: a run that examined three files must not conclude that
every other open item is fixed. So auto-resolve is **scoped**, and the scope
differs by finding kind.

**Repo-wide kinds** — `lint`, `duplicate_id`, `deprecated_edit`,
`index_out_of_date` — are produced by looking at the whole repository regardless
of what changed. Any run sees all of them, so any run may close them. No scope.

**Change-set kinds** — `stale`, `uncovered` — are a function of the change set,
and can only be resolved for paths the run actually reached. The scope is:

- every path in the change set (`uncovered` entries are keyed on the code path), plus
- the path of every document this run found impacted (`stale` entries are keyed
  on the *document's* path, not on the code that made it stale).

Anything outside that was not examined, so closing it would silently drop debt
that is still real. Here is the guard doing its job — a run scoped to one file
does not close the entry keyed on a document that file does not cover:

```text
$ bin/gotdocs debt record --paths tools/gotdocs/globs.py --resolve-absent
gotdocs: debt recorded  (+0 new, ~5 seen again, ^0 reopened, -0 resolved)
         8 open, 0 resolved, 21 occurrence(s) recorded
```

Eight entries were open and five were seen again. The other three —
`decisions/0004-ci-records-debt-instead-of-blocking.md`,
`decisions/0006-verify-stamp-as-the-escape-hatch.md` and
`runbooks/stale-doc-triage.md`, all reached only via `tools/gotdocs/check.py` —
were left alone rather than closed. `-0 resolved`, not `-3`.

This is why the CI `record` job runs against the **push range**, not against a
single commit: the wider the examined scope, the more the ledger can honestly
close.

Closing something by hand is `debt resolve`, and it is the right move when the
finding will never come back on its own:

```text
$ bin/gotdocs debt resolve doc-format --note "covers narrowed; no longer relevant"
gotdocs: resolved 1 debt entry: 8e37464a483a
         7 open, 1 resolved, 18 occurrence(s) recorded
```

A `resolve` that matches nothing exits `2` and writes nothing — it does not
create an empty `.gotdocs/debt.jsonl` where there was no ledger before.

Resolved entries stay in the file. The ledger keeps them for history; the report
collapses them to a count.

## The two CI jobs

`.github/workflows/gotdocs.yml` has two jobs because there are two questions.

### `check` — "does this change leave the documentation wrong?"

Runs on `pull_request`. Read-only. Lints, checks freshness against the merge
base, and asserts the committed index is current. Reports into the job summary
and one sticky pull request comment. **Does not write to the ledger**, and does
not fail the job unless `enforce.ci` is `error` (or the index drifted).

It does not record debt on purpose. A pull request is a proposal; its findings
are not yet something the team is living with, and recording them would fill the
ledger with debt from branches that never merge.

### `record` — "what did we decide to live with?"

Runs on `push` to `main`, on non-forks only, skipped when the head commit message
contains `[gotdocs skip]`. Needs `permissions: contents: write`.

```sh
bin/gotdocs debt record --base "${base}" --source ci --resolve-absent
bin/gotdocs debt render
```

`base` is `github.event.before`, verified to still exist (a force push can orphan
it), falling back to `HEAD~1`. Then, only if the two generated files actually
changed, it commits them back:

```text
chore(gotdocs): record doc debt [gotdocs skip] [skip ci]
```

Both markers are needed and they are read by different systems. `[gotdocs skip]`
keeps the commit out of gotdocs' own enforcement *and* out of this job's own
`if:` guard, so the job cannot trigger itself. `[skip ci]` keeps every other
workflow in the repository from running on a bookkeeping commit.

The push retries up to three times, rebasing the single ledger commit onto the
new tip each time, because somebody else may have pushed while the job ran. If it
still cannot push, it emits a `::warning::` and exits **0**. The ledger is
regenerated from the tree on every run, so losing this race entirely is harmless;
a ledger that could not be written is not a reason to mark a green push red.

This job is the only part of gotdocs that writes to a repository.

## Reading `DEBT.md`

`bin/gotdocs debt render` produces it. It is generated, committed, and
deliberately **bounded** — `debt.max_report_lines` (default 20) lines per finding
kind, with the rest collapsed to `- ... and N more`. An unbounded report is a
report nobody opens.

```markdown
# Doc debt

6 open, 0 resolved. Open items are findings that were accepted instead of fixed; each line carries the command that clears it.

## stale (4)

- **cli-reference**  ·  `docs/cli-reference.md`  ·  tools/gotdocs/check.py changed and is covered by tools/gotdocs/** (and 1 other file)  ·  seen 1× since 2026-08-14  ·  fix: `update docs/cli-reference.md, or run: bin/gotdocs verify cli-reference`  ·  id `0161a4b49144`

## index_out_of_date (2)

- **.gotdocs/INDEX.md**  ·  .gotdocs/INDEX.md does not match the documents on disk  ·  seen 1× since 2026-08-14  ·  fix: `run: bin/gotdocs index   (then stage the result)`  ·  id `f44b091c368c`
```

Read it in this order:

1. **The kind headings and their counts.** `## stale (4)` versus
   `## lint (40)` are completely different problems. Lint is a broken file;
   stale is prose that has drifted.
2. **`seen N× since DATE`.** N is the number of `debt record` runs that reported
   this entry, not the number of findings behind it. This is the signal the
   ledger exists to produce: an item seen 40 times since March is not a deferral
   any more, it is a decision nobody made out loud. An item seen once yesterday
   is fine.
3. **The `fix:` command.** Every line carries the exact command that clears it,
   so nobody has to look up how.
4. **The `id`.** Feed it to `bin/gotdocs debt resolve <id>` when you close the
   item some other way.

Resolved entries appear only as a trailing count:
`1 resolved entry kept for history in .gotdocs/debt.jsonl.` The ledger keeps the
history; the report does not spend space on it.

Do not hand-edit `DEBT.md`. It is regenerated from `debt.jsonl` and your edit is
discarded on the next run. Both files are in the default `ignore` list, so
regenerating them can never itself make a document stale.

Working the list down is [runbooks/doc-debt-review.md](../runbooks/doc-debt-review.md).

## Turning it off

```json
"debt": { "enabled": false }
```

Every `debt record` becomes a no-op that exits `0` with
`doc-debt recording is disabled (debt.enabled is false)`. The history already
recorded is left on disk, untouched — this is a switch, not a delete. `debt
list`, `render` and `stats` keep working, so turning it off does not blind you to
what was already written down.

## Related

- [enforcement.md](enforcement.md) — where `warn` and `error` are decided, and the CI job layout
- [cli-reference.md](cli-reference.md#gotdocs-debt) — every `debt` flag and exit code
- [runbooks/doc-debt-review.md](../runbooks/doc-debt-review.md) — triage order, fix versus won't-do
- [runbooks/adopting-gotdocs-in-an-existing-repo.md](../runbooks/adopting-gotdocs-in-an-existing-repo.md) — day one, when the ledger is at its largest

---
id: agent-workflow
title: Agent Workflow — How Claude Uses Gotdocs
type: doc
summary: The index-first pattern a coding agent follows — read INDEX.md, open only relevant docs, use impacted and check --json, and the four skills that do the writing.
covers:
  - .claude/skills/**
  - .claude/settings.json
owners: ["@mark"]
tags: [agents, claude, skills, tokens, workflow]
status: current
updated: 2026-08-15
verified_at: d1956a8
---

# Agent Workflow — How Claude Uses Gotdocs

Gotdocs exists because agents could not see the documentation. Docs lived in
Confluence and Drive, the agent never read them, the agent's changes invalidated
them, and nobody noticed for months. Putting docs in the repo fixes visibility.
This document covers the second half: making them *cheap* to consult, so an agent
actually does.

## The loop

```text
1. read .gotdocs/INDEX.md                  (~1 line per doc)
2. open only the 1-3 docs that matter      (full text, on demand)
3. bin/gotdocs impacted <path>             (before editing unfamiliar code)
3b. bin/gotdocs why "<odd behaviour>"      (before "fixing" anything surprising)
4. ... make the change ...
5. bin/gotdocs check --staged --json       (what did I invalidate?)
6. per doc: edit it, or verify it          (never blanket-verify)
7. bin/gotdocs index && git add .gotdocs/  (if any doc metadata changed)
```

Steps 1 and 3 are the ones people skip, and they are the ones that make the rest
cheap. Step 3b is the one agents skip, and it is the one that stops a
well-intentioned "fix" from deleting a constraint somebody put there on purpose.

## Step 1 — read the index first

`.gotdocs/INDEX.md` is generated, committed, and deliberately terse. Grouped by
type (`## doc`, `## runbook`, `## onboarding`, `## dependency`), sorted by id, one
entry per doc:

```text
- **cli-reference** - Every gotdocs command, flag and exit code, plus the --json output shape that agents and CI depend on.  ·  `docs/cli-reference.md`  ·  covers: `tools/gotdocs/**`, `bin/gotdocs`
```

Decision records get their own `## Decisions` section at the end, with two rules
that make it safe to keep in context forever. **Only `accepted` records are
listed** — a proposed record is not yet the answer to "why does it do this", a
rejected one never was, and a superseded one has been replaced by a record that
*is* listed. And **`symptoms` never appear**: they are several lines per record
and they are the search corpus for `bin/gotdocs why`, which reads the records
itself. Ask `why`; do not expect the index to answer it.

One line, id first, then summary, path and `covers`. Everything needed to decide
*whether to open the file* is in that line: what it is about, its id, where it
lives, and what code it claims. The `title` is deliberately absent — the id and
summary carry the decision, and the line has to stay one line. That is the whole
design. A `summary` that does not support that decision is a bug in the doc —
see [doc-format.md](doc-format.md#fields).

`.gotdocs/index.json` is the same data, machine-readable, for when you want to
filter by `covers`, `tags`, `status` or `verified_at` rather than read prose.

In this repo the read is automatic: a `SessionStart` hook in `.claude/settings.json`
tells the agent that gotdocs is enabled, where the index is, and which commands
exist. It does not inline the index — the agent reads the file when it needs it.

## Step 2 — open only what is relevant

Do not read `docs/**`. Read the index, pick the one to three files whose summaries
match the task, and open those.

This is a habit, not a restriction. Nothing stops an agent from reading
everything; the index exists so it does not need to.

## Step 3 — `impacted` before editing unfamiliar code

```sh
bin/gotdocs impacted src/api/routes.py
```

Answers "which docs claim this file?" — that is, which prose you are about to
invalidate and which prose might already explain what you are about to change.
Read those docs *before* the edit, not after. Docs written by people who
understood the system frequently contain the constraint that makes the naive fix
wrong.

In this repo this is also automatic: a `PostToolUse` hook on `Edit|Write|MultiEdit`
runs `bin/gotdocs impacted <file> --json` after every file edit and injects a line
naming the covering docs, with the reminder to decide per doc. It costs one
subprocess and a sentence of context; it fires only when a doc actually covers the
file.

## Step 3b — `why` before calling anything a bug

```sh
bin/gotdocs why "a POST is retried exactly twice and then fails fast"
bin/gotdocs why --path src/http/client.py
```

The single most expensive agent failure mode is removing a constraint because it
looked wrong. `why` scores your description of the observed behaviour against
every decision record's `symptoms`, and each record states both what it *does*
explain and — in a section headed `This is a bug, not this decision, if...` —
what it does not.

Three outcomes, and the third is as useful as the other two:

- **A record matches and your symptom is under "Expected behavior."** Intentional.
  Read the Consequences section. Do not "fix" it; if it genuinely needs to
  change, that is a new decision record superseding the old one, not a patch.
- **A record matches but your symptom is under "This is a bug, not this decision,
  if..."** The decision does not cover this. Investigate, and note that the
  record usually names the module to look in.
- **Nothing matches.** Exit code is still `0` — that is the point. Treat the
  behaviour as unintended until proven otherwise, and consider writing the record
  once you find the answer.

Consume `--json`, not the text: it carries the full `expected` and `not_this`
sections rather than the one-line clips, plus `score`, `matched_symptom` and
`matched_terms`. Full flow in [decisions.md](decisions.md).

## Step 4-6 — resolve what you invalidated

```sh
bin/gotdocs check --staged --json
```

Parse it. Do not eyeball the human output.

```sh
bin/gotdocs check --staged --json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for f in d["findings"]: print(f["kind"], f.get("doc_id"), f["path"], "|", f["message"])'
```

Then, per stale doc, read the diff for the paths that doc covers and choose:

- **The documented behavior changed** — edit the doc.
- **The code changed but every statement in the doc still holds** — read it, then
  `bin/gotdocs verify <doc-id>`, then `git add` the doc.

The failure mode to avoid is running `verify` on the whole list to clear the
output. It takes ten seconds, produces a green check, and writes a lie into the
frontmatter under a human's name. `verified_at` is the one field in this system
that no tool can validate — it is a claim that someone read something. Per-doc
decisions are covered in
[runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md).

The full `--json` contract, including every `kind` value, is in
[cli-reference.md](cli-reference.md#the---json-contract). It is stable: field
names, types and `kind` values do not change without a version bump, so an agent
can depend on it rather than parsing human text.

## The four skills

Installed at `.claude/skills/gotdocs-*/SKILL.md`. Each is a procedure, not a
prompt — they exist so the agent does the boring, correct thing every time.

| Skill | Invoke when | What it does |
| --- | --- | --- |
| **gotdocs-update** | `check` reports stale docs; the pre-commit hook or the CI job fails; the `PostToolUse` reminder names docs; the user says "fix the stale docs" | Reads `check --json`, gets the real diff for each doc's `covers` paths, then edits or verifies each one individually. Built specifically to prevent blanket-verifying. |
| **gotdocs-author** | "write a runbook for X", "document this service", "add an onboarding doc", or a gap found by the audit | Picks the type, scaffolds with `bin/gotdocs new`, fills the template to the format's quality bar, chooses narrow `covers`. One doc per subject. |
| **gotdocs-audit** | "what documentation are we missing", "which docs are rotted", or as the seeding step after install | Read-only. Produces a ranked gap list: uncovered code areas, `covers` globs matching nothing (including entries that name a path inside a doc root, which are structurally inert), `draft`/`deprecated` docs — `accepted` is healthy for a decision record and is not a gap — docs whose `verified_at` is far behind HEAD, and known-pitfall areas with no runbook. Then offers to author the top few. |
| **gotdocs-install** | "set up gotdocs in this repo", "make this repo keep its docs up to date" | Vendors the CLI, config, templates, hooks and CI workflow; picks roots/ignore/covers for that repo; migrates existing README/CONTRIBUTING/wiki exports into frontmatter rather than starting from zero. |

`gotdocs-install` migrating existing prose matters more than it sounds. A repo
adopting gotdocs usually has documentation — it is just scattered, unowned and
unverifiable. Converting it is what makes day one useful instead of empty. See
[runbooks/adopting-gotdocs-in-an-existing-repo.md](../runbooks/adopting-gotdocs-in-an-existing-repo.md).

## Token economics

This is the reason the index exists, so the numbers are worth being explicit about.

A repo with 40 docs averaging 1,200 words is roughly **65,000 tokens** of prose.
Reading all of it at the start of every session is not a strategy; the agent would
spend most of a context window before touching any code, and re-read it after
every compaction.

The same 40 docs in `INDEX.md` are 40 lines of roughly 35 tokens: about
**1,400 tokens**, plus a short header. Reading the index and then opening the two
docs that matter costs roughly 1,400 + 2,400 = **3,800 tokens** — about 6% of
reading everything, for strictly better relevance.

The pattern that produces that ratio has three properties:

1. **Summaries are discriminative.** The line must support the open/skip decision
   on its own. "Documentation for the API" does not; "Every gotdocs command, flag
   and exit code, plus the `--json` output shape" does. The 200-character limit is
   enforced for this reason, not for tidiness.
2. **`covers` is a machine-readable relevance signal.** The agent does not have to
   guess which docs relate to `src/api/routes.py`; `bin/gotdocs impacted` answers
   in one subprocess and a few dozen tokens, versus reading N documents to find
   out.
3. **`check --json` replaces re-reading.** After a change, the question "which
   docs did I invalidate" is answered by a structured response — a few hundred
   tokens — instead of a re-read of the doc tree.

Same reasoning for the agent-facing hooks: the `SessionStart` hook is one
paragraph, not the index; the `PostToolUse` hook is one sentence, and only when a
doc actually covers the edited file. Anything injected into every turn has to earn
its tokens on every turn.

## What an agent should not do

- **Do not read the whole doc tree "for context".** Read the index.
- **Do not run `verify --all-impacted` to clear findings.** Read each doc first.
  If reading all of them is too expensive for the change, that is a signal the
  `covers` globs are too broad — report it, do not route around it.
- **Do not regenerate the index unprompted mid-session.** Run `bin/gotdocs index`
  when a doc's frontmatter changed or a doc was added, renamed or deleted, and
  stage `.gotdocs/index.json` and `.gotdocs/INDEX.md` with that change.
- **Do not edit `.gotdocs/index.json` or `.gotdocs/INDEX.md` by hand.** They are
  generated and reproducible; a hand edit is reverted by the next `index` run and
  shows up as a CI failure.
- **Do not use the skip token to get past a finding.** It is an escape hatch for
  humans with a deadline, and it is recorded in the commit message forever. An
  agent has time to read the doc.
- **Do not run `bin/gotdocs debt record` to make a finding stop mattering.** The
  ledger is a record of a human decision to defer; recording your own findings so
  the list looks handled is the agent version of blanket-verifying. The
  pre-commit hook already records what `warn` mode let through. If you genuinely
  cannot resolve a finding, say so in your summary and leave it open.
- **Do not "fix" surprising behaviour before running `bin/gotdocs why`.** A miss
  costs one subprocess. A wrongly-removed constraint costs an incident.
- **Do not cite a `rejected` or `superseded` decision record.** They are not in
  force. `why` excludes them by default for exactly this reason; if you passed
  `--all`, check the `status` field before quoting anything.
- **Do not widen `ignore` in `.gotdocs/config.json` to silence findings.** It is
  repo-wide and hides the path from every doc. A noisy doc is a `covers` problem
  in that doc.

## Related

- [architecture.md](architecture.md) — what `check` is actually computing
- [cli-reference.md](cli-reference.md) — the `--json` contract in full
- [decisions.md](decisions.md) — `why`, and the bug-versus-tradeoff loop
- [doc-debt.md](doc-debt.md) — what the ledger is for, and why an agent should not write to it
- [doc-format.md](doc-format.md) — writing summaries and `covers` that make this work
- [runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md) — edit vs verify, per doc

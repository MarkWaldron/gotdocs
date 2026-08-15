---
id: runbook-stale-doc-triage
title: "Runbook: Triaging N Stale Docs"
type: runbook
summary: gotdocs check reported several stale docs — how to decide, per doc, whether to edit it, verify it, narrow its covers, or delete it.
covers:
  - tools/gotdocs/check.py
  - tools/gotdocs/index.py
owners: ["@mark"]
tags: [runbook, triage, stale, verify]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---

# Runbook: Triaging N Stale Docs

`gotdocs check` says several docs are stale. Each one needs an individual
decision. Bulk-verifying the list is the single worst thing you can do here: it
takes ten seconds, clears the output, and converts gotdocs into a formality.

Budget roughly two minutes per doc. Ten findings is twenty minutes. If that is
more time than the change deserves, the `covers` globs are wrong — see
[Step 4](#step-4--if-most-findings-are-wrong-fix-the-globs-instead).

## Get the list

```sh
bin/gotdocs check --staged                 # a commit in progress
bin/gotdocs check --base origin/main       # the whole branch
```

Machine-readable, for scripting or for handing to an agent:

```sh
bin/gotdocs check --base origin/main --json | python3 -m json.tool
```

Just the stale doc ids:

```sh
bin/gotdocs check --base origin/main --json | python3 -c \
  'import json,sys; [print(f["doc_id"]) for f in json.load(sys.stdin)["findings"] if f["kind"]=="stale"]'
```

## Step 1 — see exactly why each doc is impacted

The finding's `message` names the code path that matched and the glob it matched.
Confirm the reverse direction too:

```sh
bin/gotdocs impacted tools/gotdocs/check.py
```

And read the actual change:

```sh
git diff --cached -- tools/gotdocs/check.py       # staged
git diff origin/main...HEAD -- tools/gotdocs/check.py   # whole branch
```

You now have both halves: what the doc claims, and what the code did.

## Step 2 — decide, per doc

Answer one question: **does anything this document states remain true?**

Open the doc. Read only the sections that relate to the changed file — the whole
doc is rarely relevant. Then pick a lane.

| What the change did | Decision | Command |
| --- | --- | --- |
| Changed behavior, an interface, a default, a limit, an error message, or an ordering guarantee | **Edit** | `$EDITOR <doc>` |
| Added a new capability the doc should mention | **Edit** | `$EDITOR <doc>` |
| Renamed a symbol or file the doc names literally | **Edit** (the doc has a stale name in it) | `$EDITOR <doc>` |
| Refactored internals; every documented statement still holds | **Verify** | `bin/gotdocs verify <id>` |
| Formatting, comments, type annotations, test-only changes | **Verify** | `bin/gotdocs verify <id>` |
| Performance change with identical observable behavior | **Verify** | `bin/gotdocs verify <id>` |
| Deleted the thing the doc describes | **Delete the doc** | `git rm <doc> && bin/gotdocs index` |
| The doc has nothing to do with this file | **Narrow `covers`** | edit frontmatter, `bin/gotdocs index` |
| The doc was already wrong before your change | **Edit** — and say so in the commit message | `$EDITOR <doc>` |

The tie-breaker when you are unsure: **if a new engineer read this doc and then
read the new code, would they be surprised?** Surprise means edit.

### Verify honestly

```sh
bin/gotdocs verify runbook-stale-doc-triage
git add runbooks/stale-doc-triage.md
```

`verify` writes `verified_at = <short HEAD sha>` and `updated = <today>` into the
frontmatter and touches nothing else. Both land in the diff under your name.
There is no way for the tool to know whether you read the doc, which is precisely
why the record is public.

`--all-impacted` exists and is appropriate exactly when you have already read
every impacted doc — for example after a rename that swept ten files:

```sh
bin/gotdocs verify --all-impacted
```

Using it before reading is the failure mode this runbook exists to prevent.

## Step 3 — order the work

With more than three findings, do them in this order. It shrinks the list as you
go.

1. **Lint errors and duplicate ids first.** They are fatal (`exit 2`) and they
   suppress nothing else usefully — a doc that will not parse is not protecting
   anything. `bin/gotdocs lint`.
2. **`index_out_of_date` next.** One command: `bin/gotdocs index && git add .gotdocs/`.
   It often disappears several confusing findings at once.
3. **Docs you own.** You can decide immediately.
4. **Docs someone else owns.** Check `owners` in the frontmatter. If the decision
   needs their knowledge, edit what you can, and ask — do not verify on their
   behalf.
5. **`deprecated_edit` findings.** You edited a doc marked `status: deprecated`.
   Either delete the doc (it was supposed to be going away) or flip it back to
   `status: current`. Do not keep editing a deprecated doc.
6. **`uncovered` findings** (only when `require_coverage: true`). Either write a
   doc for the file, add it to an existing doc's `covers`, or add it to `ignore`
   if it is generated. Deciding to leave a file undocumented is legitimate;
   record it by adding it to `ignore` with the reason in the commit message.

## Step 4 — if most findings are wrong, fix the globs instead

The strongest signal that `covers` is too broad: a doc appears in the findings for
changes that plainly cannot affect it. Two ways to measure it:

```sh
# how often would this doc have been impacted over recent history?
for sha in $(git rev-list -n 30 HEAD); do
  bin/gotdocs check --base "$sha~1" --json 2>/dev/null | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(any(f["doc_id"]=="THE-DOC-ID" for f in d["findings"]))'
done | sort | uniq -c
```

```sh
# how many docs does one file wake up?
bin/gotdocs impacted src/some/file.py
```

Rules of thumb, once there is enough history to average over (say 50+ commits):
a doc impacted by more than roughly one commit in ten is too broad. Below that,
percentages are noise — one commit out of nine is already "11%" — so judge by
absolute counts instead: a handful of impacted commits in the window is healthy,
most of them is too broad. Either way, a single file waking up more than three or
four docs means the docs overlap and should be consolidated. Narrow the globs in the doc's frontmatter
(guidance: [docs/doc-format.md](../docs/doc-format.md#choosing-good-covers-globs)),
then `bin/gotdocs index`.

Do not fix a `covers` problem by adding entries to `.gotdocs/config.json`'s
`ignore` — that is repo-wide and hides the file from every doc, including the ones
that legitimately cover it. `ignore` is for generated and vendored code only.

## Step 5 — confirm

```sh
bin/gotdocs check --base origin/main
bin/gotdocs lint
bin/gotdocs index && git status --short .gotdocs/    # should print nothing
```

Clean means: every impacted doc was edited or explicitly verified, frontmatter
parses, and the committed index matches the tree.

## Handing it to Claude

For a large list, `/gotdocs-update` does step 1 and step 2 mechanically: it reads
`check --json`, opens only the impacted docs, edits the ones whose statements the
diff invalidated, and verifies the ones it can justify. Review its verifies as
carefully as you would your own — the same "I read this" assertion applies.

## Related

- [runbooks/pre-commit-hook-blocking.md](pre-commit-hook-blocking.md) — one finding, blocked right now
- [runbooks/ci-check-failing.md](ci-check-failing.md) — the same findings, in CI
- [docs/doc-format.md](../docs/doc-format.md) — the `covers` contract
- [docs/cli-reference.md](../docs/cli-reference.md) — `check`, `impacted`, `verify` in full

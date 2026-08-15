---
name: gotdocs-update
description: Resolve the docs a code change made stale - read the real diff for each impacted doc, then either edit the doc or run `bin/gotdocs verify <id>`. Use when `bin/gotdocs check` reports stale docs, when the pre-commit hook or the gotdocs CI job fails, when the PostToolUse reminder names impacted docs, or when the user says "/gotdocs-update", "gotdocs says my docs are stale", "fix the stale docs", "update the docs for this change", "the pre-commit hook is failing on docs", or "docs are out of date after my change".
---

# gotdocs-update

A doc is stale when code it `covers` changed and the doc was neither edited nor re-verified.
Your job is to decide, per doc, whether the **documented behaviour** actually changed - by
reading the diff, not by guessing from filenames - and then take exactly one of two actions.

**The failure mode this skill exists to prevent: running `bin/gotdocs verify` to make a
finding go away without reading the diff.** That converts a freshness system into a
rubber stamp, and it is worse than having no system, because the `verified_at` sha now lies
with a human's name on it.

## 1. Get the findings

```sh
bin/gotdocs check --staged --json          # pre-commit / mid-session (default)
bin/gotdocs check --base origin/main --json  # CI failure, or a whole branch
bin/gotdocs check --paths <path>... --json   # "if I change this, what breaks?"
```

Parse it, do not eyeball it:

```sh
bin/gotdocs check --staged --json | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("mode",d["mode"],"ok",d["ok"],d.get("summary",{}))
for f in d["findings"]: print(f["kind"], f.get("doc_id"), f["path"], "|", f["message"])'
```

Handle each `kind`:

| kind | Action |
| --- | --- |
| `stale` | The main loop below. |
| `lint` | Fix the frontmatter first - `message` carries `file:line`. Nothing downstream is trustworthy until `bin/gotdocs lint` is clean. |
| `duplicate_id` | Rename the newer doc's `id`, update anything citing it, re-run `bin/gotdocs index`. |
| `deprecated_edit` | You are editing a doc that is supposed to be going away. Delete it and fold anything still true into the doc that replaced it, or flip `status: current` if it is actually alive. Ask the user which. |
| `index_out_of_date` | `bin/gotdocs index && git add .gotdocs/index.json .gotdocs/INDEX.md`. |
| `uncovered` | Only appears when `require_coverage: true`. Either add the path to an existing doc's `covers`, or hand it to the **gotdocs-author** skill. |

If `check` exits 3, you are outside a git repo or git cannot answer the question (unknown
base ref, no merge base, no commits). If it exits 2, there is a fatal lint problem. Neither
is fixed by editing docs. A missing `.gotdocs/config.json` is not an error - the defaults
apply - so exit 3 always means git.

## 2. Read the diff before you read the doc

For each stale doc, get the actual change to the paths it covers - not the whole diff:

```sh
# which globs matched (the finding message names them; confirm)
bin/gotdocs impacted <changed-file> --json | python3 -m json.tool

# the diff that made it stale, restricted to that doc's covers paths
git diff --cached -- <covers-path>...            # staged
git diff origin/main...HEAD -- <covers-path>...  # branch
```

Read the diff **first**, the doc **second**. Reading the doc first primes you to see the
change as consistent with it.

Read only the impacted docs. Do not open `.gotdocs/INDEX.md` and read everything; the point
of the index is that you do not have to.

## 2b. Check whether a decision governs this file — before you touch the doc

Run this for every changed path, **before** deciding what the doc should say:

```sh
bin/gotdocs why --path <changed-file>
bin/gotdocs why --path <changed-file> --json   # to parse
```

A doc records *what the system does*. An accepted decision record (`decisions/NNNN-*.md`)
records *what it was deliberately decided to do, and why*. Those are not the same authority,
and a diff can silently put them in conflict.

Exactly three outcomes:

**No record covers it.** Nothing was written down. Carry on with step 3.

**An accepted record covers it and the change conforms.** The decision still holds; the
change is an implementation of it. Update the doc normally. If the change makes one of the
record's `symptoms` or its `## Expected behavior` section wrong in detail (a number moved,
a name changed) but the decision itself is intact, edit that record too, in the same change.

**An accepted record covers it and the change contradicts it.** Read the record's
`## This is a bug, not this decision, if...` section: that section exists precisely to tell
you which side of the line you are on. If the new behaviour is on the wrong side, **stop and
say so, loudly and by name**, before editing anything:

> `bin/gotdocs why --path src/api/client.py` returns `0007-retry-budget-per-request`
> (accepted): "a POST is retried at most twice, then fails fast". This diff makes it retry
> five times with exponential backoff. That contradicts the decision, it does not implement
> it. Either the diff is wrong, or 0007 is out of date. If 0007 is out of date, supersede it:
> `bin/gotdocs new decision "Retry budget is per hop"`, set `supersedes: [0007-...]` on the
> new record, and `status: superseded` + `superseded_by: [NNNN-...]` on 0007. I have not
> changed either file yet.

**Never resolve this by editing the doc — or the record — to match the new code.** The whole
value of a decision record is that it does not quietly move when somebody changes the
implementation; rewriting it turns the audit trail into a description of whatever happened
last. Superseding is cheap, visible in review, and keeps the old reasoning readable.
`bin/gotdocs lint` enforces both halves of the supersede link, so a half-done supersede
fails rather than rots.

A `proposed` record is not in force: mention it, but do not treat it as a constraint.
A `rejected` or `superseded` record is history — `why` hides those unless you pass `--all`.

## 3. Decide, per doc

Ask one question: **does any sentence in this doc now describe something false, missing, or
misleading?**

Behaviour changed - **edit the doc**:
- an input, output, flag, route, env var, exit code, schema field or error string changed
- a default, limit, timeout, retry policy or ordering guarantee changed
- a control-flow path was added or removed (new branch, new failure mode, removed fallback)
- a dependency, data store or queue was added, removed or swapped
- the doc's worked example, command or code block no longer produces the documented result
- a file the doc names by path moved or was renamed
- something the doc claims cannot happen now can

Behaviour did not change - **`bin/gotdocs verify <id>`**:
- pure refactor: extracted function, renamed local, moved code between files the doc does
  not name
- formatting, lint fixes, type annotations, comments
- tests added or changed with no production behaviour change
- performance change with identical semantics
- a dependency version bump with no behavioural difference *that you checked*

Uncertain -> treat as changed. Read further, or ask the user. Never resolve uncertainty by
verifying.

## 4a. If it changed: edit

1. Edit the smallest region of the doc that is now wrong. Do not rewrite the file.
2. Update the concrete artifacts too: command output blocks, tables of flags/fields,
   diagrams, cross-references.
3. If the change moved or split the covered code, update `covers` in the same edit -
   frontmatter that points at a deleted path is how docs rot silently.
4. Set `updated:` to today's date. Leave `verified_at` alone; editing the doc file is itself
   what satisfies the check.
5. If it was `status: draft` and is now accurate, promote it to `current`.
6. `git add` the doc.

## 4b. If it did not change: verify

```sh
bin/gotdocs verify <doc-id>
git add <doc-path>
```

This rewrites exactly `updated` and `verified_at` and preserves every other byte. `verify`
does not stage - `git add` afterwards or the finding persists.

`bin/gotdocs verify --all-impacted` exists. Use it only after reading every impacted doc
against the diff, one at a time. It is not a shortcut past step 3.

## 5. Look for the missing runbook

A behaviour change with no corresponding runbook is a signal, not a nuisance. When the diff
introduces or changes any of these, propose a runbook (do not silently skip it):

- a new failure mode, error path, or thing that can now time out / retry / drop work
- a new external dependency that can be down
- a new migration, backfill, or manual operational step
- a new limit, quota or circuit breaker someone will hit at 3am
- a fix for an incident - the fix and the runbook belong in the same PR

Check first:

```sh
bin/gotdocs impacted <changed-file> --json | python3 -c '
import json,sys
p=json.load(sys.stdin)["paths"]
ids=[d["doc_id"] for e in p for d in e["docs"]]
print("docs covering it:", ids)'
grep -rl "<symptom keyword>" runbooks/ 2>/dev/null
```

If nothing covers the symptom, say so explicitly and offer to run **gotdocs-author**:
> `src/payments/webhooks.py` now dead-letters after 3 retries. No runbook covers
> "webhook events stop arriving". Want me to write `runbooks/webhook-dead-letter-growing.md`?

Same for the reverse: if the change deleted the subsystem a runbook describes, propose
deleting or deprecating that runbook.

## 6. Close the loop

```sh
bin/gotdocs index                 # if any frontmatter, covers, id or file set changed
bin/gotdocs lint                  # must be clean
bin/gotdocs check --staged        # must report 0 findings
bin/gotdocs debt list             # anything you deliberately left, now on the record
git status --short
```

Then report per doc: `edited` (and what claim was wrong) or `verified` (and what the change
actually was, in one clause). The user should be able to audit your judgement without
re-reading the diff. Do not commit unless asked.

## Common mistakes

- **Verifying without reading the diff.** The one unrecoverable mistake here.
- Verifying because the diff "looked like a refactor" from the file names.
- Editing the doc to match the code line-by-line. Docs state behaviour and decisions; the
  code is the code.
- Reading every doc in the repo instead of the impacted ones.
- Forgetting `git add` after `verify` - the finding comes right back.
- Forgetting `bin/gotdocs index` after changing frontmatter, leaving an
  `index_out_of_date` finding.
- Using the skip token (`[gotdocs skip]` / `GOTDOCS_SKIP=1`) to get past a finding. That is
  for genuine emergencies and mass-rename commits, and it is visible in the commit message.
- Bumping `updated` on a doc you did not change, or leaving `updated` stale on one you did.
- Treating an impacted doc that is genuinely out of scope as "fine" instead of fixing its
  over-broad `covers`. If a doc keeps firing on unrelated changes, narrow the glob - that is
  a real fix, and it belongs in this same change.
- Skipping `bin/gotdocs why --path` and editing a doc into agreement with code that
  contradicts an accepted decision. That is the same failure as rubber-stamping `verify`,
  one level up: the doc and the code now agree, and both are wrong.
- Editing a decision record so it matches new behaviour instead of superseding it.

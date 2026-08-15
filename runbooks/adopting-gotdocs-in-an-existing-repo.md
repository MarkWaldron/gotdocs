---
id: runbook-adopting-gotdocs
title: "Runbook: Adopting Gotdocs in an Existing Repo"
type: runbook
summary: Migrate a repo that already has 200 undocumented files onto gotdocs without producing a wall of false positives on day one.
covers:
  - scripts/install-gotdocs.sh
  - .gotdocs/config.json
  - tools/gotdocs/cli.py
owners: ["@mark"]
tags: [runbook, adoption, migration, rollout]
status: current
updated: 2026-08-15
verified_at: d1956a8
---

# Runbook: Adopting Gotdocs in an Existing Repo

Goal: gotdocs installed and useful in a repo with existing code and little or no
existing documentation, with zero findings on day one and a hard gate within a
few weeks.

The failure mode to design against: turning enforcement on before coverage
exists, producing dozens of findings on the first commit, and teaching the team
that `--no-verify` is part of committing.

Time: about an hour for the mechanical parts, then one doc at a time.

## Before you start

```sh
git --version        # 2.20+
python3 --version    # 3.9+
git rev-parse --show-toplevel
git status --short   # start clean
```

Work on a branch. Nothing here needs network access.

## Step 1 — vendor the files

Copy from this repo into the target, preserving paths:

```text
bin/gotdocs
tools/gotdocs/            (the whole package, including tests/)
.gotdocs/config.json
.gotdocs/schema.json
.gotdocs/templates/
.gotdocs/hooks/pre-commit
.gotdocs/hooks/pre-push
scripts/install-gotdocs.sh
scripts/uninstall-gotdocs.sh
.github/workflows/gotdocs.yml
.claude/settings.json
.claude/skills/gotdocs-*/
```

Both hooks matter: `scripts/install-gotdocs.sh` installs `pre-commit` *and*
`pre-push`, and the shipped `.gotdocs/config.json` carries an `enforce.pre_push`
mode. Vendor only one and the installer prints
`pre-push: SKIPPED (no source at .gotdocs/hooks/pre-push)` and you get a
half-installed setup. Vendor `scripts/uninstall-gotdocs.sh` too — it is what the
rollback section and `onboarding/local-setup.md` tell people to run.

Then:

```sh
chmod +x bin/gotdocs scripts/install-gotdocs.sh scripts/uninstall-gotdocs.sh \
    .gotdocs/hooks/pre-commit .gotdocs/hooks/pre-push
python3 -m unittest discover -s tools/gotdocs/tests -t .
```

There is nothing to install and nothing to fetch — the CLI is Python 3.9+ stdlib
only.

The intended path is to ask Claude to do this step: open the target repo and ask
it to set up gotdocs. The rest of this runbook is what it should do, and what to
check afterwards.

## Step 2 — turn everything off first

Edit `.gotdocs/config.json`:

```json
"enforce": { "pre_commit": "off", "ci": "off" },
"require_coverage": false
```

`require_coverage: false` is the single most important setting on day one. With
it `true`, every changed file that no doc covers is a finding — in a repo with
200 undocumented files that is 200 findings on the first broad commit.

Commit this. Nothing changes for anyone yet.

## Step 3 — set the roots to what you actually have

Default roots are `docs`, `runbooks`, `onboarding`, `dependencies`. Adapt to the
repo rather than reorganizing it:

```json
"roots": ["docs", "runbooks", "onboarding", "dependencies"]
```

- If the repo already has `documentation/` or `adr/`, add it to `roots` instead of
  moving files.
- Every file under a root must have valid frontmatter, so do not add a root full
  of files you are not ready to annotate — that produces lint errors immediately.
- Directories in `roots` that do not exist are fine; they are skipped.

Create the ones you want:

```sh
mkdir -p docs runbooks onboarding dependencies
```

## Step 4 — tune `ignore` for this repo's generated code

The `ignore` list in this repo's `.gotdocs/config.json` (75 entries, mirrored by
`config.DEFAULT_IGNORE`) already covers `node_modules`, build output, lockfiles,
minified assets, protobuf output, caches, binaries and images. Copy that file
rather than letting `scripts/install-gotdocs.sh` bootstrap one: the installer's
starter template has only 10 entries, so `__pycache__`, generated protobufs and
images would mark docs stale. Then add what is specific to this repo — generated clients, vendored SDKs, snapshots, migrations
if they are machine-written:

```json
"ignore": ["...defaults...", "api/generated/**", "db/migrations/**", "**/*.pb.dart"]
```

`ignore` removes a path from consideration as a *code* path everywhere, for every
doc. Getting this list right now prevents most future false positives. Verify:

```sh
bin/gotdocs impacted api/generated/client.ts     # should report "ignored": true
```

## Step 5 — annotate the docs you already have

For each existing markdown file you move or keep under a root, add frontmatter.
Start from a template:

```sh
bin/gotdocs new doc payments-api --title "Payments API" --covers 'src/payments/**'
```

For files that already exist, paste the frontmatter block in by hand and set:

- `status: current` if it is accurate, `draft` if it is half-written,
  `deprecated` if it describes something being removed.
- `verified_at:` the current short sha **only if you actually read it against the
  code**. If you did not, leave `verified_at` out. An absent `verified_at` means
  every impact is stale until someone reads it, which is the honest state for an
  inherited doc.
- `covers:` narrow. See [Step 6](#step-6--write-covers-narrow-and-prove-it).

Then:

```sh
bin/gotdocs lint
bin/gotdocs index
git add .gotdocs/index.json .gotdocs/INDEX.md
```

If you have no docs at all, that is fine. Skip to step 7 and write the first one
there.

## Step 6 — write `covers` narrow, and prove it

This is the step that determines whether adoption succeeds. Broad globs produce
noise, noise produces skips, skips produce a dead tool.

Test each doc against real history before anyone is exposed to it:

```sh
# what would the findings have been for the last 30 commits?
for sha in $(git rev-list -n 30 HEAD); do
  bin/gotdocs check --base "$sha~1" --json 2>/dev/null | python3 -c \
    'import json,sys; d=json.load(sys.stdin); [print(f["doc_id"]) for f in d["findings"]]'
done | sort | uniq -c | sort -rn
```

Read the counts:

- A doc appearing in more than ~3 of 30 commits has `covers` that is too broad, or
  it is one long doc that should be several short ones.
- A doc appearing in 0 of 30 either covers genuinely stable code (fine — config,
  schemas, protocols) or has globs that match nothing (a bug). Check with
  `bin/gotdocs impacted <a file it should cover>`.

Fix and re-run until the distribution looks like "occasionally impacted", not
"impacted by everything".

## Step 7 — the "we have 200 undocumented files" case

You do not document 200 files. You document the handful that generate the most
questions, and you let the rest stay uncovered, which costs nothing while
`require_coverage` is `false`.

Rank by where knowledge is actually missing:

```sh
# most-churned files over the last year — high churn plus no doc is where people get hurt
git log --since=1.year --name-only --pretty=format: \
  | grep -v '^$' | sort | uniq -c | sort -rn | head -40
```

```sh
# files touched by the most distinct authors — shared surfaces need shared docs
git log --since=1.year --format='%an' --name-only \
  | python3 -c '
import sys,collections
a=None; m=collections.defaultdict(set)
for line in sys.stdin:
    line=line.rstrip("\n")
    if not line: continue
    if "/" not in line and "." not in line: a=line
    else: m[line].add(a)
for p,s in sorted(m.items(), key=lambda kv: -len(kv[1]))[:40]:
    print(len(s), p)'
```

Then write, in this order, and stop when you have covered the top few subsystems:

1. **`onboarding/start-here.md`** — what this repo is, how to run it, where things
   are. It pays for itself the first time someone joins.
2. **A runbook for every recurring incident you can remember.** These are the
   highest-value files in the repo and the ones most likely to be missing
   entirely. One symptom per runbook. If the team has a channel full of "how do I
   fix X again?", each of those is a runbook.
3. **`dependencies/*.md` for each external system you cannot fix yourself** —
   the database, the payment provider, the queue. What breaks, what the failure
   looks like, who to contact.
4. **`docs/*.md` for the two or three subsystems from the churn list.**

Five to ten files is a successful adoption. Aiming for complete coverage is how
adoptions stall.

Everything you have not documented remains invisible to gotdocs. That is the
correct behavior: gotdocs enforces that documentation you *have* stays true, not
that documentation exists.

## Step 8 — install hooks and go to `warn`

```sh
sh scripts/install-gotdocs.sh
bin/gotdocs status
```

The installer finishes by running `bin/gotdocs ci doctor`. Read its output — it
is the only place the CI prerequisites that are *not* in the workflow file get
checked: a read-only `GITHUB_TOKEN` (which silently caps the ledger job's
`contents: write`), a default branch the workflow never triggers on, and
`bin/gotdocs` committed without its executable bit. `bin/gotdocs ci doctor
--apply` fixes the ones that do not need a human.

Set `"pre_commit": "warn"` and commit. Tell the team two things and nothing more:

- You will sometimes see gotdocs output after `git commit`. It does not block you.
- If a doc it names is wrong, fix it, or run the `verify` command it prints.

Add the install step to the repo's setup instructions and to any `make setup` /
`npm run setup` target — `.git/hooks/` is not tracked, so every clone needs it.

## Step 9 — CI to `error` once coverage is real

After a week or two in `warn`, measure:

```sh
git log --since=2.weeks --oneline | wc -l
git log --since=2.weeks --grep='\[gotdocs skip\]' --oneline | wc -l
```

A skip rate above roughly 10% means findings are not trusted — go back to step 6
before tightening anything.

When the rate is low, set `"ci": "error"` and enable
`.github/workflows/gotdocs.yml`. Confirm the workflow uses `fetch-depth: 0`, or
`REF...HEAD` will fail on shallow clones — see
[runbooks/ci-check-failing.md](ci-check-failing.md).

Leave `pre_commit` at `warn`. The hard gate belongs at review time, where it is
visible; the local hook's job is to remind you while you still have the context.

## Step 10 — `require_coverage`, much later or never

Turning it on says "every non-ignored file in this repo must be covered by some
doc." For most repos that is not the goal. If you do want it, get there by
expanding `ignore` and writing docs until
`bin/gotdocs check --base <old-sha> --json` reports zero `uncovered` findings
across recent history — then flip it, so the day you turn it on nothing changes.

## Verify the adoption worked

```sh
bin/gotdocs status                       # roots, doc count, index state, hook installed
bin/gotdocs lint                         # exit 0
bin/gotdocs check --base origin/main     # exit 0 on a normal branch
python3 -m unittest discover -s tools/gotdocs/tests -t .
```

Then make a real change to a covered file, commit, and watch the hook print the
finding. If it does not, the hook is not installed — `ls -l .git/hooks/pre-commit`.

## Rollback

Adoption is entirely additive and reversible:

```sh
sh scripts/uninstall-gotdocs.sh   # removes both hooks, restores any <hook>.local
```

The installer never writes a `.bak`: it preserves a pre-existing foreign hook as
`<hook>.local` and the gotdocs hook chains to it. (`bin/gotdocs install --force`,
the single-hook path, is the one that writes `pre-commit.bak`.)

Set `"enforce": {"pre_commit": "off", "ci": "off"}` to disable without
uninstalling. Deleting `bin/gotdocs`, `tools/gotdocs/` and `.gotdocs/` removes it
completely; the markdown under the roots is ordinary markdown and stays useful.

## Related

- [docs/enforcement.md](../docs/enforcement.md) — modes and the rollout rationale
- [docs/doc-format.md](../docs/doc-format.md) — frontmatter and glob dialect
- [onboarding/local-setup.md](../onboarding/local-setup.md) — per-engineer setup after adoption
- [runbooks/stale-doc-triage.md](stale-doc-triage.md) — what to do with the findings that follow

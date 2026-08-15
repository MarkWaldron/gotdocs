---
id: onboarding-start-here
title: Start Here
type: onboarding
summary: What gotdocs is, why documentation lives in the repo, what to read in your first 30 minutes, and the first useful change to make.
covers:
  - README.md
  - .gotdocs/config.json
owners: ["@mark"]
tags: [onboarding, orientation]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---

# Start Here

You are looking at a repo that documents itself and enforces that the
documentation stays true. This page orients you in about 30 minutes and ends with
a change you can actually make.

## What gotdocs is

A vendored CLI plus two git hooks plus a CI workflow. Every document under
`docs/`, `runbooks/`, `onboarding/`, `dependencies/` and `decisions/` declares in
its frontmatter which code it describes:

```yaml
covers:
  - tools/gotdocs/**
  - bin/gotdocs
```

When you change a file matching one of those globs, that document is **impacted**.
If you neither edit it nor explicitly re-verify it in the same change, it is
**stale**, and the hooks and CI say so — naming the doc and the exact command to
resolve it.

**Nothing blocks you by default.** All three enforcement layers ship as `warn`;
they report, and anything you let through is recorded in `.gotdocs/DEBT.md`
rather than scrolling past. Raising `enforce.ci` to `"error"` is the one change
that makes CI fail a pull request.

Three things the tool does that are not "is this doc stale":

- `bin/gotdocs why "<what you observed>"` — is this surprising behaviour an
  intentional tradeoff, or a bug? Answered from decision records in `decisions/`.
- `bin/gotdocs debt list` — what did we knowingly defer, and how long ago?
- `bin/gotdocs export --target hugo --out build/site` — publish to any of six
  static site generators without editing a single source file.

There is no server, no database, no account, no network call. `bin/gotdocs` is a
POSIX `sh` shim over a Python 3.9+ stdlib-only package that reads `git diff`
output and a JSON index.

## Why documentation in the repo

The problem, stated plainly:

1. Docs lived in Confluence, Google Drive, a wiki, someone's Notion. Coding agents
   could not see any of it, so they never read it.
2. Agents changed the code. Nobody updated the docs. Nothing detected the drift.
   Documentation aged into actively misleading.
3. Known pitfalls had no runbooks. The knowledge stayed in the heads of whoever
   was on-call the last time it broke.

Putting the docs next to the code fixes (1) — an agent can read them, and so can
you, in the same editor, in the same review, in the same diff. Gotdocs fixes
(2): drift is detected mechanically at commit time, when the person who caused
it still remembers why. (3) is a writing problem, not a tooling one, but
`runbooks/` being a first-class directory with a template and an enforced format
makes it a normal thing to produce instead of a special project.

The cost of in-repo docs is that they have to be maintained in-repo. That cost is
the point. Documentation nobody is required to update is documentation nobody can
trust.

## Your first 30 minutes

Read in this order. Skip anything you already know.

| Minutes | Read | You will know |
| --- | --- | --- |
| 0-2 | `.gotdocs/INDEX.md` | Every doc in the repo, one entry each. **Always start here.** Open only what you need. |
| 2-10 | [docs/architecture.md](../docs/architecture.md) | The doc -> covers -> impacted -> stale chain and what each module does. |
| 10-18 | [docs/doc-format.md](../docs/doc-format.md) | Every frontmatter field, the glob dialect, and how to choose `covers` that will not generate noise. |
| 18-24 | [docs/enforcement.md](../docs/enforcement.md) | Where checks run, what `off`/`warn`/`error` mean, and every escape hatch with its cost. |
| 24-30 | [docs/cli-reference.md](../docs/cli-reference.md) | Every command and the `--json` shape. Skim; come back to it as reference. |

Then, when you need them:

- [docs/decisions.md](../docs/decisions.md) — decision records and `gotdocs why`.
  Read this the first time something surprises you.
- [docs/doc-debt.md](../docs/doc-debt.md) — the ledger, and why CI is advisory
  until somebody says otherwise.
- [docs/publishing.md](../docs/publishing.md) — portability rules and
  `gotdocs export`, when you want the docs on a site.
- [docs/agent-workflow.md](../docs/agent-workflow.md) — how Claude is meant to use
  this, the four skills, and why the index-first pattern exists.
- [onboarding/local-setup.md](local-setup.md) — clone, install, run the tests,
  watch the hook fire. Do this before your first commit.
- [runbooks/](../runbooks/) — read the titles now so you recognize them later.
- [dependencies/git.md](../dependencies/git.md) and
  [dependencies/python3.md](../dependencies/python3.md) — the only two external
  things gotdocs needs.

## The six things you actually need to remember

```sh
bin/gotdocs status                    # where am I, is everything wired up
bin/gotdocs impacted <path>           # which docs describe this file?
bin/gotdocs why "<odd behaviour>"     # did we mean it, or is it a bug?
bin/gotdocs check --staged            # what did my staged change make stale?
bin/gotdocs verify <doc-id>           # "I read it, it is still accurate"
bin/gotdocs index                     # regenerate the index after doc metadata changes
```

And one rule: **`verify` is an assertion, not a formality.** It writes
`verified_at` and `updated` into the file with your name on the commit. Run it
only after reading the doc against the change. Clearing findings you did not read
turns the whole system into decoration — see
[runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md).

## The layout

```text
bin/gotdocs                  sh shim -> python3 -m tools.gotdocs
tools/gotdocs/               the CLI (stdlib only)
.gotdocs/
  config.json                roots, ignore globs, enforcement modes, debt, publish
  schema.json                JSON Schema for frontmatter (docs + editors)
  index.json                 GENERATED, committed: machine index
  INDEX.md                   GENERATED, committed: the file agents read first
  debt.jsonl                 GENERATED, committed: the doc-debt ledger
  DEBT.md                    GENERATED, committed: the bounded human report
  templates/                 doc, runbook, onboarding, dependency, decision
  hooks/pre-commit           source of truth for the hooks
  hooks/pre-push
scripts/install-gotdocs.sh   installs the hooks; run once per clone
.github/workflows/gotdocs.yml
.claude/skills/gotdocs-*/    install, update, author, audit
docs/ runbooks/ onboarding/ dependencies/ decisions/    the docs themselves
```

`docs/` is how it works. `runbooks/` is what to do when something is wrong.
`onboarding/` is how to start. `dependencies/` is what we rely on and how it
fails. `decisions/` is what we chose on purpose — and, just as importantly, what
each of those choices does *not* excuse. If you are unsure where something
belongs, that list is the answer.

## Your first useful change

Pick whichever fits what you noticed while reading.

**Something in a doc was wrong or unclear.** Fix it. That is a complete, welcome
contribution. Edit the file, and if you changed `covers`, `id`, `status`, `title`
or `summary`, run `bin/gotdocs index` and stage `.gotdocs/` with it.

**A `covers` list looks too broad.** Check it:

```sh
bin/gotdocs impacted <a file that should not wake that doc>
```

If a doc claims files it does not actually describe, narrow the globs. Broad
`covers` is the main way this system degrades — every false finding trains someone
to skip.

**A recurring problem has no runbook.** Write it:

```sh
bin/gotdocs new runbook <symptom-in-kebab-case> \
  --title "Runbook: <the symptom, as the alert states it>" \
  --covers '<the code that breaks>'
```

Fill in the template. Title it by symptom, not cause. Every check and every fix
must be a literal, copy-pasteable command. Runbooks are the highest-value files
here and the ones most likely to be missing.

**You want to see the machinery work.** Follow
[onboarding/local-setup.md](local-setup.md) — it ends with you deliberately
triggering a stale finding and resolving it.

## Before you commit

```sh
bin/gotdocs check --staged     # findings, with the fix for each
bin/gotdocs lint               # frontmatter valid
```

If the hook stops you and you need to ship right now, read
[runbooks/pre-commit-hook-blocking.md](../runbooks/pre-commit-hook-blocking.md).
There is always a way through, and each one costs something visible.

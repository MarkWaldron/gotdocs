---
id: gotdocs-readme
title: Gotdocs
type: doc
summary: Repo front door — what gotdocs is, the problem it solves, a quickstart, the layout, and where to go next.
covers:
  - scripts/install-gotdocs.sh
  - .gotdocs/config.json
owners: ["@mark"]
tags: [readme, overview]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Gotdocs

Documentation, runbooks, decision records and onboarding notes that live in the
repo, declare which code they describe, and are checked at commit time for having
gone stale.

This repo is both the tool and the reference example. Everything under `docs/`,
`runbooks/`, `onboarding/`, `dependencies/` and `decisions/` is enforced by the
CLI in `tools/gotdocs/`.

## The problem

Docs lived in Confluence, Drive, a wiki. Coding agents could not see any of it, so
they never read it. Agents changed the code; nobody updated the docs; nothing
detected the drift. Documentation aged into actively misleading. And when
something broke in a known way, there was no runbook — the knowledge stayed with
whoever was last on-call.

Moving docs into the repo makes them visible to agents and to reviewers. That is
necessary but not sufficient: in-repo docs rot just as fast unless something
notices. Gotdocs is the something.

## The 60-second version

Every document declares the code it describes:

```yaml
---
id: payments-api
title: Payments API
type: doc
summary: How charges, refunds and idempotency keys are handled.
covers:
  - src/payments/**
  - src/api/routes/charges.py
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---
```

Change `src/payments/gateway.py` and that document is **impacted**. If you neither
edit it nor explicitly re-verify it in the same change, it is **stale**:

```text
$ git commit -m "feat: retry declined charges"

gotdocs: 1 finding (mode: warn)

stale (1)
  docs/payments-api.md  [payments-api]
    src/payments/gateway.py changed and is covered by src/payments/**
    -> update docs/payments-api.md, or run: bin/gotdocs verify payments-api

Or ask Claude: /gotdocs-update
```

Two ways to resolve it. Edit the doc — the normal path, and cheapest right now
while you still have the context. Or run `bin/gotdocs verify payments-api`, which
stamps `verified_at` and `updated` into the frontmatter: an assertion that you
read the doc against the new code, recorded in the diff under your name.

**Out of the box nothing blocks.** All three layers — pre-commit, pre-push and CI
— ship as `warn`. They report, and anything you let through is recorded in
`.gotdocs/DEBT.md` so it does not simply scroll past. Setting
`enforce.ci: "error"` is the single change that makes CI fail a pull request; do
it once the `covers` globs are honest. The one thing that always blocks is a
committed index that no longer matches the tree, because that makes every later
diff lie.

Two more questions the tool answers, which are not "is this doc stale":

```sh
bin/gotdocs why "a POST is retried exactly twice"   # intentional tradeoff, or bug?
bin/gotdocs export --target hugo --out build/site   # publish, without editing a file
```

## Constraints

- **Zero dependencies.** Python 3.9+ standard library only. No pip, no PyYAML, no
  lockfile, no install step. The CLI is vendored as source.
- **No network.** Ever, anywhere in the tool.
- **Language-agnostic.** Nothing about it assumes a Python repo.
- **Never blocks you by accident.** An internal error warns and exits 0. A missing
  `python3` warns and exits 0. There is always a documented way through, and each
  one costs something visible.

## Quickstart

```sh
git clone <repo-url> gotdocs && cd gotdocs
sh scripts/install-gotdocs.sh          # installs pre-commit + pre-push hooks
bin/gotdocs status                     # roots, doc count, index state, hooks
```

Then the commands that matter:

```sh
bin/gotdocs impacted src/payments/gateway.py   # which docs describe this file?
bin/gotdocs check --staged                     # what did my change make stale?
bin/gotdocs verify payments-api                # I read it; it is still accurate
bin/gotdocs lint                               # is all frontmatter valid?
bin/gotdocs index                              # regenerate the committed index
bin/gotdocs why "requests retry twice"         # intentional decision, or bug?
bin/gotdocs debt list                          # what did we agree to live with?
bin/gotdocs export --target hugo --out build/site
```

Add `--json` to any of them. The JSON shape is a documented, stable contract —
it is the interface agents and CI use.

Scaffold a new document:

```sh
bin/gotdocs new runbook queue-backlog-growing \
  --title "Runbook: Queue Backlog Growing" \
  --covers 'src/workers/**'

bin/gotdocs new decision "Retry budget is per request" \
  --covers 'src/http/**' \
  --symptom "a POST is retried exactly twice and then fails fast"
```

For a decision the positional argument is the title, not the id — the number is
allocated as `NNNN-slug` from what is already on disk.

Adding gotdocs to a different repo: the `gotdocs-install` skill vendors the
files, picks roots and `covers` globs for that codebase, and migrates existing
README/CONTRIBUTING/wiki prose into frontmatter instead of starting from zero.

The skills live in *this* repo's `.claude/skills/`, so they are not visible from
another repo until you put them where Claude Code looks. Once, per machine:

```sh
mkdir -p ~/.claude/skills
cp -R .claude/skills/gotdocs-* ~/.claude/skills/
```

Then open the target repo with Claude and say "set up gotdocs", naming this
checkout as the source. The manual path is
[runbooks/adopting-gotdocs-in-an-existing-repo.md](runbooks/adopting-gotdocs-in-an-existing-repo.md).

## Layout

```text
bin/gotdocs                    sh shim -> python3 -m tools.gotdocs
tools/gotdocs/                 the CLI, stdlib only
  cli.py config.py frontmatter.py globs.py gitutil.py
  index.py check.py report.py errors.py
  debt.py decisions.py portability.py export.py
  tests/                       python3 -m unittest discover -s tools/gotdocs/tests -t .
.gotdocs/
  config.json                  roots, ignore globs, enforcement modes, debt, publish
  schema.json                  JSON Schema for frontmatter (editors + reference)
  index.json                   GENERATED, committed: machine index
  INDEX.md                     GENERATED, committed: read this first
  debt.jsonl                   GENERATED, committed: the doc-debt ledger
  DEBT.md                      GENERATED, committed: the bounded human report
  templates/                   doc, runbook, onboarding, dependency, decision
  hooks/pre-commit, pre-push   source of truth; installed into .git/hooks
scripts/install-gotdocs.sh     run once per clone
scripts/uninstall-gotdocs.sh
.github/workflows/gotdocs.yml  job "check" on every PR; job "record" on push to main
.claude/skills/gotdocs-*/      install, update, author, audit
docs/                          how things work
runbooks/                      what to do when something is wrong
onboarding/                    how to start
dependencies/                  what we rely on and how it fails
decisions/                     what we decided on purpose, and what that does not excuse
```

## Where to go next

- **New here?** [onboarding/start-here.md](onboarding/start-here.md) — 30 minutes,
  ends with a change you can make.
- **Setting up your machine?** [onboarding/local-setup.md](onboarding/local-setup.md)
- **Looking for a specific doc?** [.gotdocs/INDEX.md](.gotdocs/INDEX.md) — one
  entry per document, grouped by type. Read it before opening anything else.
- **An agent working in this repo?** [docs/agent-workflow.md](docs/agent-workflow.md)

Reference:

- [docs/architecture.md](docs/architecture.md) — the doc -> covers -> impacted ->
  stale chain, end to end
- [docs/doc-format.md](docs/doc-format.md) — frontmatter fields, glob dialect,
  supported YAML subset
- [docs/cli-reference.md](docs/cli-reference.md) — every command, flag, exit code
  and the `--json` contract
- [docs/enforcement.md](docs/enforcement.md) — hooks, CI, modes, escape hatches,
  rollout
- [docs/doc-debt.md](docs/doc-debt.md) — the ledger of what you agreed to live
  with, and why CI is advisory until you say otherwise
- [docs/decisions.md](docs/decisions.md) — decision records and
  `gotdocs why`: is this a bug, or did we mean it?
- [docs/publishing.md](docs/publishing.md) — portability guarantees, the lint
  rules, and `gotdocs export` for six static site generators

When something goes wrong:

- [runbooks/pre-commit-hook-blocking.md](runbooks/pre-commit-hook-blocking.md)
- [runbooks/stale-doc-triage.md](runbooks/stale-doc-triage.md)
- [runbooks/ci-check-failing.md](runbooks/ci-check-failing.md)
- [runbooks/doc-debt-review.md](runbooks/doc-debt-review.md)
- [runbooks/adopting-gotdocs-in-an-existing-repo.md](runbooks/adopting-gotdocs-in-an-existing-repo.md)

## Status

Publishing works: `bin/gotdocs export` renders the documents into Docusaurus,
MkDocs, Astro Starlight, Jekyll, Hugo or plain GitHub conventions, and
`bin/gotdocs lint --portability` checks a document will render on all six before
anybody picks one. The source files stay canonical — export reads the repo and
writes a separate tree, and switching generators is a `--target` change rather
than an edit to 40 files. Frontmatter is **mapped, not passed through**: each
target gets the keys it understands, and everything else — including keys
gotdocs does not own — is written to `_gotdocs.json` beside the export rather
than into the page. Do not hand-write `sidebar_position`; `export` derives it,
and `lint --portability` flags the key as reserved. See
[docs/publishing.md](docs/publishing.md).

---
id: 0001-docs-live-in-the-repo
title: Documentation lives in the repository, in git, next to the code
type: decision
summary: Docs are markdown files in the working tree under configured roots, versioned by git, with no external wiki, database or hosted service in the loop.
covers:
  - .gotdocs/config.json
  - tools/gotdocs/config.py
  - tools/gotdocs/index.py
symptoms:
  - I edited the wiki and gotdocs still says the doc is stale
  - there is no web UI, no server and no login for this documentation system
  - the docs directory is full of markdown files with a YAML block at the top
  - a doc I deleted on a branch is back after I switched branches
  - the docs went out of date the moment we started a long-lived feature branch
  - my documentation edit needs a code review before it lands
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - architecture
  - storage
  - git
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Documentation lives in the repository, in git, next to the code

## Context

The failure this system exists to prevent is documentation drifting away from the
code it describes. Drift is not caused by people being lazy; it is caused by the
doc and the code living in two different places with two different change
processes. A wiki page has no diff against `src/api/client.py`, no merge base, no
reviewer, and no way for a tool to say "this page and this commit disagree".

Everything gotdocs does downstream — computing a change set, matching `covers`
globs against changed paths, comparing `verified_at` to a head sha — requires the
document and the code to be under the same version control at the same revision.
There is no version of that computation that works across a repo and a hosted
wiki.

## Decision

Every gotdocs document is a UTF-8 markdown file in the working tree, under one of
the roots listed in `.gotdocs/config.json` (`docs`, `runbooks`, `onboarding`,
`dependencies`, `decisions` by default), carrying a frontmatter block. Documents
are created, edited, reviewed, branched, merged and reverted by exactly the same
git operations as source code. The two generated files, `.gotdocs/index.json` and
`.gotdocs/INDEX.md`, are committed alongside them.

There is no server, no database, no account and no network call anywhere in the
tool. `tools/gotdocs/index.py` reaches the document set by walking the configured
roots with `os.walk`; that walk is the only definition of "what documents exist".

## Expected behavior

- `bin/gotdocs status` and `bin/gotdocs lint` work with the network disabled, on
  a laptop on a plane, in a container with no egress.
- A document is visible to the tool the moment the file exists on disk. It does
  not need to be committed, registered, or added to a list:

  ```console
  $ bin/gotdocs lint
  gotdocs: no lint errors in 25 documents
  ```

- Deleting a file deletes the document. `bin/gotdocs index` regenerates
  `.gotdocs/INDEX.md` from the remaining files with no trace of the removed one.
- `git checkout <branch>` changes the document set, because the document set *is*
  the working tree. A doc written on a feature branch is invisible on `main`
  until the branch merges.
- `git revert` of a code change reverts the accompanying doc change in the same
  commit, because they were the same commit.
- Docs are subject to code review: a documentation edit arrives in a pull request
  and is reviewed by the same people, in the same diff, as the code it describes.
- The roots are configuration, not a hardcoded path. Setting
  `"roots": ["documentation"]` in `.gotdocs/config.json` moves the entire
  document set; nothing else needs to change.

## This is a bug, not this decision, if...

- `bin/gotdocs` makes any network call at all. There is no code path that opens a
  socket; if you see one under `strace`/`dtruss`, that is a defect.
- A markdown file placed under a configured root with valid frontmatter does not
  appear in `bin/gotdocs status` or `.gotdocs/INDEX.md` after `bin/gotdocs index`.
  Walking is done in `tools/gotdocs/index.py:scan`; a miss there is a bug in the
  walk or in the `ignore` globs, not this decision.
- A file under a configured root is silently ignored *without* a lint finding.
  Files that are not `.md`/`.markdown` are skipped by design; anything else that
  disappears without a message is a bug.
- `bin/gotdocs lint` reports documents that are not in any configured root, or
  misses documents that are. Root resolution lives in
  `tools/gotdocs/config.py`.
- Changing `roots` in `.gotdocs/config.json` does not change which files are
  scanned on the next run. Config is read fresh on every invocation; a cached
  root list would be a bug.
- The tool writes anything outside the repository working tree (a cache in
  `~/.cache`, a lockfile in `/tmp` that outlives the run). Every artifact it
  produces is a tracked file inside `.gotdocs/`.

## Consequences

Documentation now costs what code costs: a branch, a review, a merge conflict
when two people edit the same paragraph. That is the point, and it is also the
thing people will complain about — a one-line typo fix in a wiki takes ten
seconds and here it takes a pull request.

Non-engineers cannot edit documentation without a git workflow. For a repo whose
docs are meant to be edited by support or product staff, this is a real cost with
no mitigation inside gotdocs; the answer is to publish (see `bin/gotdocs
publish`) rather than to move the source of truth out.

Documentation is also branch-scoped, which surprises people: a doc written on a
long-lived branch does not help anybody on `main` until it merges. And the whole
document set is as large as the repo clone — there is no partial fetch of "just
the docs".

## Alternatives considered

- **A hosted wiki (Confluence, Notion, GitHub Wiki).** Rejected: no shared
  revision with the code, so the central computation — "did this commit make this
  page wrong?" — cannot be expressed. Every wiki-based scheme degrades to a
  human remembering.
- **A separate docs repository.** Rejected for the same reason in weaker form:
  two repos means two merge bases and a submodule pin that is itself a thing to
  keep fresh. It converts one staleness problem into two.
- **A database or service with an API the CLI talks to.** Rejected: introduces a
  network dependency, an availability requirement and an auth story into a tool
  whose job is to run inside a pre-commit hook in under a second.
- **Docstrings/comments in source files as the only documentation.** Rejected:
  runbooks, onboarding guides and dependency notes have no source file to live
  in, and comments cannot carry `covers` globs spanning several files.

## Revisit when

Revisit if the repository grows a documentation audience that genuinely cannot
use git and `bin/gotdocs publish` proves insufficient, or if a monorepo split
makes "the same repository" no longer true for the code a document covers. The
first symptom of the latter is a `covers` glob that has to point outside the
repo root — which the glob dialect deliberately cannot express.

## References

- `tools/gotdocs/index.py` — `scan()` walks the configured roots; that walk is
  the document set.
- `tools/gotdocs/config.py` — `roots`, `ignore`; `DEFAULTS["roots"]`.
- `.gotdocs/config.json` — the roots in force for this repository.
- `docs/architecture.md` — end-to-end design.
- `onboarding/start-here.md` — the "why docs live in the repo" explanation for
  new engineers.

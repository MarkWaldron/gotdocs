---
id: replace-me-onboarding-id
title: Onboarding — Replace Me Service
type: onboarding
summary: "Getting productive on <service>: what it is, how to run it locally, and a first change worth making. Max 200 chars."
covers:
  - path/to/code/**
  - Makefile
owners: ["@your-handle"]
tags: [onboarding, replace-me]
status: draft
updated: 2026-08-14
verified_at: 0000000
---

# Onboarding — Replace Me Service

<!--
  Delete this comment block when you fill the template in.
  Written for someone (or some agent) on day one with no context and no tribal knowledge.
  Rules:
  - Every command must be runnable as written, from a clean checkout.
  - State prerequisites and versions explicitly. "Recent Node" is not a version.
  - If a step needs access someone must grant, say who grants it and how to ask.
  - Re-run this doc yourself on a clean machine whenever `covers` changes materially.
-->

## What this service is

Two or three sentences, no jargon. What it does, who uses it, why the business cares.
Then one sentence on where it sits: what calls it, what it depends on.

- Language / runtime:
- Deployed as:
- Talks to:
- Deeper detail: `docs/replace-me.md`

## Your first 30 minutes

Enough to have opinions, before you touch setup.

1. Read `.gotdocs/INDEX.md` — the one-line map of every doc in this repo. Open only what you need.
2. Read `docs/replace-me.md` — how the thing actually works.
3. Skim the entrypoint: `path/to/code/main.ext` — follow one request end to end.
4. Skim `runbooks/` — how this breaks in production tells you what matters.
5. Note the vocabulary: the 5–10 domain words used here and what each means.

| Term | Means |
| --- | --- |
| `replace-me` | the thing it actually refers to |

## Local setup

Prerequisites, exact versions:

- `tool` >= `x.y`
- `tool` >= `x.y`

```sh
git clone <repo-url>
cd <repo>
replace-me bootstrap
```

Configuration and secrets:

```sh
cp .env.example .env
# fill in: KEY_ONE (ask @your-handle), KEY_TWO (from <where>)
```

Run it:

```sh
replace-me dev
```

Confirm it works:

```sh
curl -s localhost:PORT/health   # expect: {"status":"ok"}
replace-me test
```

Common setup failures:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `literal error` | what is actually wrong | the command that fixes it |

## Your first useful change

A real, small, mergeable task — not a toy.

1. Pick up a `good-first-issue`, or make this concrete change: <describe one>.
2. Edit `path/to/code/file.ext`.
3. Add or update a test in `path/to/tests/`.
4. Run `replace-me test` and `replace-me lint`.
5. Update the docs your change impacts — `bin/gotdocs check --staged` tells you which, and
   `/gotdocs-update` asks Claude to do it.
6. Open a PR. Expect review from `@your-handle`. CI runs `bin/gotdocs check --base main`.

House rules worth knowing before your first review:

- Commit/PR conventions:
- Test expectations:
- What requires a second opinion (migrations, public API changes, anything irreversible):

## Who to ask

Ask early. Nobody here scores points for being stuck quietly.

| Topic | Person / team | Where |
| --- | --- | --- |
| This service | `@your-handle` | `#your-channel` |
| Access and credentials | `@your-team` | `#your-channel` |
| Deploys and infra | `@your-team` | `#your-channel` |
| On-call / production | rotation | `#your-incident-channel` |

## Related

- `docs/replace-me.md`
- `runbooks/replace-me.md`
- `dependencies/replace-me.md`
- `.gotdocs/README.md` — how docs in this repo are kept current

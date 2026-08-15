---
id: replace-me-dependency-id
title: "Dependency: Replace Me"
type: dependency
summary: "What we use <dependency> for, who owns it, and what breaks here when it is unavailable. Max 200 chars."
covers:
  - path/to/integration/**
  - path/to/config/replace-me.*
owners: ["@your-handle"]
tags: [dependency, replace-me]
status: draft
updated: 2026-08-14
verified_at: 0000000
---

# Dependency: Replace Me

<!--
  Delete this comment block when you fill the template in.
  One file per external dependency: SaaS vendor, internal service owned by another team,
  database, queue, or a library big enough that swapping it is a project.
  Rules:
  - This is the file someone opens during an incident asking "is it them or us?"
  - NEVER put secret values here. Names and locations of secrets only.
  - Keep `covers` on the integration code and its config, not the whole app.
-->

## What it is

One sentence: what the dependency is and who provides it (vendor, or the internal team).

- Kind: SaaS / internal service / datastore / library
- Version or plan tier:
- Docs: <url>
- Status page: <url>

## What we use it for

The specific capabilities we actually depend on. Be exact — this is what determines the
blast radius, and it is usually smaller than "we use X".

- Capability 1 — used by `path/to/code`
- Capability 2 — used by `path/to/code`
- Explicitly NOT used: features people assume we use but do not

## Where it is configured

Every place the integration is wired up, so a change can be made completely.

| What | Where |
| --- | --- |
| Client / SDK setup | `path/to/code/client.ext` |
| Config file | `path/to/config/replace-me.yaml` |
| Environment variables | `REPLACE_ME_URL`, `REPLACE_ME_TIMEOUT` |
| Infrastructure | `infra/path` |
| Local development | `.env.example`, docker-compose service `replace-me` |

Settings that matter and their current values: timeouts, retries, pool sizes, rate limits,
region, consistency mode.

## Credentials and ownership

Names and locations only. **No secret values in this repo.**

- Secret name: `REPLACE_ME_API_KEY` — stored in <secret manager / path>
- Rotation: how often, who does it, the procedure or its runbook
- Account owner / billing: `@your-team`
- Technical owner here: `@your-handle`
- Vendor support: plan tier, how to open a ticket, response SLA
- Access requests: who approves, how to ask

## Failure modes

How it fails in practice, and what each failure looks like on our side.

| Failure | Looks like | Our behaviour | Runbook |
| --- | --- | --- | --- |
| Hard outage | connection refused / 5xx | describe: fail fast, retry, degrade? | `runbooks/replace-me.md` |
| Latency spike | p99 climbs, timeouts | do we shed load or queue? | |
| Rate limit | 429s | backoff, and what gets dropped | |
| Auth failure | 401/403 after rotation | | |
| Partial / stale data | silently wrong results | how we would even notice | |

Detection: the alert or metric that tells us this dependency is the problem.

## What breaks if it is down

Be concrete about degradation. This is the paragraph an incident commander reads.

- Hard-down (no fallback): which user-facing features stop entirely
- Degraded (fallback exists): what the fallback is, how long it holds, what is lost
- Unaffected: what keeps working, so we do not over-declare impact
- Data risk: is anything dropped, or is it buffered and replayed? Where does it buffer, and
  what is the buffer's limit?
- Recovery: does it self-heal on their recovery, or does something need a restart or replay?

## Upgrade notes

- Current version / API version pinned, and where the pin lives
- Upgrade cadence and who tracks EOL / deprecation notices
- How to test an upgrade: staging path, contract tests, canary
- Known breaking changes and migrations already survived
- Rollback plan
- Exit plan: what replacing this would take, and how locked in we are

## Related

- `docs/replace-me.md` — the component that uses it
- `runbooks/replace-me.md` — what to do when it is broken

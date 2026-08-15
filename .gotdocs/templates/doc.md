---
id: replace-me-doc-id
title: Replace Me — What This Component Is
type: doc
summary: "One sentence describing what this component does and why it exists. Max 200 chars. This is the only text an agent sees before deciding to open the file."
covers:
  - path/to/code/**
  - path/to/entrypoint
owners: ["@your-handle"]
tags: [replace-me]
status: draft
updated: 2026-08-14
verified_at: 0000000
---

# Replace Me — What This Component Is

<!--
  Delete this comment block when you fill the template in.
  Rules of thumb:
  - Document behaviour and decisions, not the code line-by-line. The code is the code.
  - Every claim here should be checkable against something in `covers`.
  - If you cannot point at code for a section, it probably belongs in an ADR or a runbook.
  - Keep `covers` tight: broad globs make this doc impacted by unrelated changes and
    train people to re-verify without reading.
-->

## What it does

One paragraph. The job this component has in the system, stated so someone who has never
seen the repo understands why it exists.

## How it fits

What calls it, what it calls, what data it owns. Name the actual modules, services, tables
and queues.

- Upstream: who sends it work
- Downstream: what it depends on (link the `dependencies/` doc, do not duplicate it)
- Owns: the state/tables/topics it is authoritative for

## Key behaviour

The rules that are not obvious from reading a single file: ordering guarantees, retry and
idempotency semantics, validation, limits, timeouts, what happens under concurrency.

## Interfaces

The public surface: commands, HTTP routes, function signatures, message schemas, exit codes.
Include what callers are allowed to rely on and what is internal.

| Surface | Contract | Notes |
| --- | --- | --- |
| `example()` | in → out | stability, versioning |

## Configuration

Environment variables, config files and flags that change behaviour, with defaults and the
blast radius of getting one wrong.

## Failure modes

How it fails, what the failure looks like from outside, and where the corresponding runbook
lives.

## Decisions and trade-offs

Why it is built this way. Record the alternatives that were rejected and the reason, so the
next person does not re-litigate a settled question or undo it by accident.

## Gotchas

Sharp edges a newcomer or an agent will hit. Be specific and blunt.

## Related

- `runbooks/replace-me.md`
- `dependencies/replace-me.md`

---
id: replace-me-runbook-id
title: "Runbook: Replace Me — Observable Symptom"
type: runbook
summary: "What is on fire and what this runbook resolves, in one sentence. Name the symptom an on-call sees, not the internal cause. Max 200 chars."
covers:
  - path/to/code/**
owners: ["@your-handle"]
tags: [runbook, replace-me]
status: draft
updated: 2026-08-14
verified_at: 0000000
---

# Runbook: Replace Me — Observable Symptom

<!--
  Delete this comment block when you fill the template in.
  A runbook is written for 3am, for someone who did not build this, possibly an agent.
  Rules:
  - Title it by the SYMPTOM (what the pager says), not the cause.
  - Every check and every fix is a literal, copy-pasteable command. No "check the logs".
  - Say what the good output looks like AND what the bad output looks like.
  - Mark anything destructive or irreversible in bold, with the blast radius.
  - One runbook per symptom. If you are writing "or, if instead...", split the file.
-->

## Symptom

What you observe from outside. The alert name, the error string, the graph shape, the
customer report. Quote the literal text where you can.

- Alert: `AlertNameHere`
- Error: `literal error message as it appears in logs`
- Dashboard: which panel, what it looks like when this is happening

## Confirm it is this

Fast checks that distinguish this problem from things that look like it. Run these before
doing anything else.

```sh
# 1. the one command that proves it
replace-me --check
```

- **It is this if:** the specific output you see.
- **It is NOT this if:** the output that means you are in a different runbook — say which one.

## Impact

Who is affected and how badly, so you can decide whether to page others or wait.

- User-visible effect:
- Data loss / correctness risk: yes / no — explain
- Severity guidance: when to escalate immediately vs. handle in hours
- Does it self-heal? If yes, in how long

## Investigate

Ordered checks. Stop as soon as one identifies the cause and jump to the matching
remediation. Each step: the command, what good looks like, what bad looks like.

1. **Is the service up and serving?**
   ```sh
   replace-me status
   ```
   Good: `ok`. Bad: non-zero exit or `unreachable` → go to Remediate → "Service is down".

2. **Recent deploy or config change?**
   ```sh
   git log --oneline -10 -- path/to/code
   ```
   Good: nothing in the incident window. Bad: a change landed just before onset → go to
   Remediate → "Roll back".

3. **Dependency healthy?**
   ```sh
   replace-me ping-dependency
   ```
   Bad: timeouts → this is the dependency's incident; see `dependencies/replace-me.md`.

4. **Resource exhaustion (connections, memory, disk, quota)?**
   ```sh
   replace-me metrics --resource
   ```
   Bad: at or near limit → go to Remediate → "Relieve pressure".

5. **Backlog or stuck work?**
   ```sh
   replace-me queue-depth
   ```
   Bad: depth growing monotonically → go to Remediate → "Drain the backlog".

## Remediate

One subsection per cause found above. Fastest safe mitigation first; the real fix can wait
for daylight.

### Service is down
```sh
replace-me restart
```
Expected effect and how long it takes. Note anything in-flight that is lost.

### Roll back
```sh
replace-me deploy --to <previous-sha>
```
**Destructive/irreversible?** State it here with the blast radius.

### Relieve pressure
```sh
replace-me scale --replicas +2
```
Temporary. Note the follow-up ticket this requires.

### Drain the backlog
```sh
replace-me drain --batch 100
```
Note rate limits and what happens if you run it twice (must be idempotent, or say so).

## Escalate

When mitigation does not work, or before you do anything irreversible.

- Escalate if: the mitigation has not moved the symptom within N minutes, or impact is
  data correctness, or it needs a credential you do not have.
- Owner / on-call: `@your-handle`, `@your-team`
- Channel: `#your-channel`
- Vendor support path and account/plan tier, if applicable
- Say what to hand over: the checks already run and their output

## Verify resolved

Do not close the incident on a hunch.

```sh
replace-me status
replace-me metrics --resource
```

- The alert clears and stays clear for N minutes.
- The specific metric returns to its normal range (state the range).
- No new errors matching the symptom string.
- Backlog drained to zero / normal depth.
- File a follow-up if you applied a temporary mitigation.

## Related

- `docs/replace-me.md` — how the component actually works
- `dependencies/replace-me.md` — the dependency this often turns out to be
- `runbooks/replace-me-adjacent.md` — the runbook this is easily confused with

---
id: replace-me
title: Replace Me
type: decision
summary: One sentence naming the decision and the tradeoff it made. Max 200 characters.
covers: []
symptoms: []
supersedes: []
superseded_by: []
owners: []
tags: []
status: proposed
decided_on: 1970-01-01
updated: 1970-01-01
verified_at: 0000000
---

# Replace Me

## Context

What was true when this was decided: the constraint, the traffic, the deadline, the
thing that broke. Someone reading this in two years has none of that in their head.

## Decision

What was decided, in the present tense and in one paragraph. State it as a rule the
code follows, not as a story about a meeting.

## Expected behavior

The observable consequences of the decision. This section is quoted verbatim by
`bin/gotdocs why`, so write it as things a person can watch happen:

- a POST is retried at most twice, then fails fast
- the second retry waits 400ms, not 4s

## This is a bug, not this decision, if...

The boundary. What would someone see that this decision does **not** explain, and that
therefore needs a real investigation:

- retries continue past the third attempt
- a GET is retried at all

Without this section the record cannot settle anything: every symptom looks like it
might be covered.

## Consequences

What this costs, and what was given up. Include the thing that will annoy someone
later - that is the part they will otherwise re-litigate.

## Alternatives considered

Each rejected option and the specific reason it was rejected. "We didn't think of it"
is a valid entry and is more useful than silence.

<!--
Fill `symptoms:` in the frontmatter with the phrases someone would actually type when
they hit this - one per line, in their words, not yours. That list is the entire
search corpus for `bin/gotdocs why`, and it never appears in .gotdocs/INDEX.md.

Set `status: accepted` once it is agreed. Superseding a decision is two edits:
this record gets `status: superseded` plus `superseded_by: [NNNN-slug]`, and the new
record gets `supersedes: [this-id]`. `bin/gotdocs lint` enforces both halves.
-->

---
id: 0007-symptoms-routing-for-intent-lookup
title: Decision records are routed by observer-written symptoms, scored with token overlap
type: decision
summary: gotdocs why ranks decision records by weighted token overlap against a hand-written symptoms list, so an agent can ask is this intentional before treating behaviour as a bug.
covers:
  - tools/gotdocs/decisions.py
symptoms:
  - I cannot tell whether this behaviour is intentional or a defect
  - gotdocs why returned nothing and told me to treat it as unintended
  - why matched a decision that has nothing to do with what I typed
  - the top match is obviously right but the second one is noise
  - a superseded decision was cited as the reason for current behaviour
  - lint says an accepted decision must list at least one entry under symptoms
  - searching for retries found a record that says retried
  - why exits 0 even when it finds no answer at all
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - decisions
  - search
  - agents
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Decision records are routed by observer-written symptoms, scored with token overlap

## Context

An agent or engineer looking at odd behaviour has to answer one question before
doing anything else: *is this an intentional tradeoff, or a bug?* Getting that
wrong is expensive in both directions — "fixing" a deliberate constraint, or
spending a day documenting a real defect as expected behaviour.

The information needed to answer it usually exists, buried in an ADR whose title
is written from the *decider's* side ("Retry budget per request"). The person
hitting it does not know that phrase. They know what they saw: "a POST is retried
twice and then fails fast". Title search never connects the two, and reading
every record to find out is exactly the cost this is meant to avoid.

Under 0002 there are no embeddings and no search index available.

## Decision

Every decision record carries `symptoms:` in its frontmatter — a list of phrases
written from the **observer's** side, in the words someone would use when they hit
it, not the words used to describe the choice. That list is the entire search
corpus for `bin/gotdocs why`.

Ranking is plain weighted token overlap in `tools/gotdocs/decisions.py`. Each
field contributes `weight * (matched unique query terms / total unique query
terms)`, with `SYMPTOM_WEIGHT = 4.0`, `TITLE_WEIGHT = 2.0`,
`SUMMARY_WEIGHT = 1.0`, `TAG_WEIGHT = 0.5`, plus `PHRASE_BONUS = 2.0` when the
query's terms appear in order and adjacent inside one symptom.

Ranking alone is not enough to be decisive: with eight records and a six-word
symptom, almost every record shares *some* word with the query, and a ranked
list of eight is a list of seven wrong answers. So the ranking is then cut by
two floors, both relative to the query rather than to an absolute score:
`MIN_TERM_COVERAGE = 0.34` drops a record that overlaps fewer than about a
third of the query's distinct terms (sharing one word out of six is a
coincidence), and `RELATIVE_FLOOR = 0.45` drops anything scoring under 45% of
the leader. Both apply to the leader too — if the best thing in the repository
shares one word out of six with the symptom, then nothing was written down
about it, and saying so beats pointing at the least-irrelevant record.

The answer is two body sections, quoted back: `## Expected behavior` and
`## This is a bug, not this decision, if...`. `bin/gotdocs lint` requires both,
plus at least one symptom, on any record marked `accepted`.

## Expected behavior

- A query returns at most the top three surviving records, five lines each — id,
  status, path, title, the symptom that matched, and the two sections reduced to
  their leading claim and clipped to 80 columns. Records that fail either
  relevance floor are not shown at all, so a decisive query returns one answer:

  ```console
  $ bin/gotdocs why "a doc went stale after a whitespace change"
  2 decisions match "a doc went stale after a whitespace change" (of 8 searched):

  [1] 0003-covers-globs-as-the-staleness-signal  (accepted)  decisions/0003-...md
      Staleness is computed from declared covers globs, not from content analysis
      symptom:  a doc went stale after a whitespace-only change
      expected: `bin/gotdocs impacted <path>` names every document whose `covers`...
      bug if:   `bin/gotdocs impacted src/a/b.py` returns nothing while a document...

  [2] 0004-ci-records-debt-instead-of-blocking  (accepted)  decisions/0004-...md
      Enforcement defaults to warn and CI records doc debt instead of failing the build
      symptom:  gotdocs reported stale docs and the commit went through anyway
      expected: In the default `warn` mode nothing is blocked: `bin/gotdocs check`...
      bug if:   `bin/gotdocs check --mode error` exits 0 while printing findings.
  ```

- The `expected:` and `bug if:` lines are the *first claim* of each section, not
  the first 80 characters of it: the list marker is stripped and the text ends
  at a sentence boundary, so the line reads as a statement. `--full` prints the
  sections whole.
- No match is a first-class answer and still exits 0, because the absence of
  information must not abort an agent's diagnosis loop:

  ```console
  $ bin/gotdocs why "kubernetes pod eviction"
  no decision matches "kubernetes pod eviction" (of 8 searched).

  Nothing was written down that explains this. Treat it as unintended
  until proven otherwise, and consider recording the answer you find.
  $ echo $?
  0
  ```

- `rejected` and `superseded` records are excluded unless `--all` is passed.
  Citing a record that is not in force as the reason for current behaviour is
  precisely the mistake this prevents.
- `--path <file>` filters to records whose `covers` match that file, and can be
  used with no query at all to list what governs a file.
- `--json` emits `{ok, query, path, searched, match_count, matches}`; each match
  carries `score`, `matched_symptom` and `matched_terms`, so a ranking can be
  explained rather than trusted.
- Tokenisation is lowercase, NFKD-normalised, punctuation-split, stopword-filtered
  and lightly stemmed: `retry`/`retries`/`retried` collapse to one term, and
  domain words that look like noise (`error`, `fail`, `slow`) are deliberately
  kept.
- Ordering is stable: ties break on `decision.display_id`, so the same query
  produces the same ranking on every machine and every run.
- `bin/gotdocs lint` refuses an `accepted` record with no symptoms:
  `an accepted decision must list at least one entry under 'symptoms': without
  one, 'gotdocs why' can never surface it` — and exits 2.
- `bin/gotdocs why` never opens more of a record than the two sections; the path
  is printed for a reader who wants the rest.

## This is a bug, not this decision, if...

- A query that is a verbatim copy of a record's symptom does not rank that record
  first. The phrase bonus (2.0) is sized to outweigh a rival matching every term
  scattered across symptom, title, summary and tags (4.0 + 2.0 + 1.0 + 0.5 =
  7.5); a near-quotation losing is a scoring bug in `decisions.why`.
- A record with a matching symptom scores below a record that matched only its
  title. The weights are strictly descending by design; an inversion means the
  field contributions are being computed wrong.
- `bin/gotdocs why` exits non-zero on a query with no matches, or on a repository
  with no `decisions/` directory. `decisions.load` returns `[]` for a missing
  directory on purpose; both cases are exit 0.
- `bin/gotdocs why --all` still hides `rejected` or `superseded` records, or the
  default run shows them.
- A query returns a record that shares a single common word with it while a much
  stronger match is also listed. That is the tail the two relevance floors exist
  to cut; a record surviving below 45% of the leader's score, or on under a third
  of the query's terms, is a bug in `decisions._drop_noise`.
- The `expected:` line renders a leading `- ` or stops mid-word where a sentence
  boundary was available. That is `decisions.lead_claim` failing, not a record
  formatting problem.
- The `## Expected behavior` / `## This is a bug, not this decision, if...`
  sections are not extracted from a record that plainly has them. Heading
  matching in `decisions.extract_sections` is deliberately forgiving — level,
  case, British spelling, a typographic ellipsis, markdown emphasis, and setext
  underlines are all meant to work. A record whose heading reads
  `### Expected Behaviour:` and still shows `(not recorded -- this record cannot
  settle it)` is a bug in `_heading_key`.
- A `#` inside a fenced code block ends a section early. Fences are tracked and
  skipped; if a shell snippet truncates a section, that is a bug.
- `bin/gotdocs lint` accepts an `accepted` record missing symptoms or either
  section, or reports a `proposed` record for the same thing (the requirement is
  scoped to `accepted` on purpose).
- Two records claim the same four-digit number, or the sequence has a gap, and
  `lint` does not say so. Numbering rules live in `decisions.validate` and
  `_numbering_issues`.
- Note what is **not** a bug: a query in vocabulary no record uses returning
  nothing. Token overlap has no synonyms and no semantics. The fix is to add the
  observer's phrasing to that record's `symptoms`, not to change the scorer.

## Consequences

Recall depends entirely on how well the symptoms were written. A record whose
symptoms restate the decision ("we chose a per-request retry budget") is
effectively invisible to the person who needs it, and nothing in the tool can
detect that — `lint` can only check that the list is non-empty, never that it is
written from the observer's side.

There are no synonyms, no stemming beyond plurals, and no semantic similarity.
"latency spike" will not find a record whose symptoms say "requests are slow".
The maintenance burden is real: every time someone describes a symptom in a new
way, that phrasing should be appended to the record.

Scoring over every record on every query is O(records x terms). At the scale
decision records actually reach — dozens, not thousands — that is microseconds,
but it does mean there is no index to consult and no incremental update path.

## Alternatives considered

- **Embeddings / vector search.** Rejected by 0002: needs a model, a dependency
  and usually a network call, inside a tool that must run offline in a hook.
- **Full-text search over the whole record body.** Rejected: the body is prose
  about the decision, so it is dense with the decider's vocabulary and matches
  almost any query weakly. Symptoms are a deliberately small, high-signal corpus.
- **Title-and-tag search only.** Rejected: this is the status quo that fails.
  Titles are written from the decider's side; that mismatch is the whole problem.
- **`grep -r decisions/`.** Rejected as the *primary* interface: it has no
  ranking, no status filtering (so it happily returns superseded records), and
  returns whole files into an agent's context. It remains a fine fallback.
- **Putting `symptoms` in `.gotdocs/INDEX.md` so an agent sees them for free.**
  Rejected — see 0008. Symptoms are several lines per record and `INDEX.md` is
  read whole on every session.
- **An LLM call to route the question.** Rejected for the check-path reasons in
  0003, and unnecessary: the agent asking the question is already an LLM. This
  command's job is to hand it the right two paragraphs cheaply.

## Revisit when

Revisit if the record count passes a few hundred (at which point scanning every
record per query stops being free and an index becomes worth its complexity), or
if `bin/gotdocs why` starts returning nothing for queries that a human can
immediately match to an existing record. The second is the important signal, and
the first response to it is always to add symptoms, not to change the scorer.

## References

- `tools/gotdocs/decisions.py` — `why`, `format_why`, `tokenize`, `_stem`,
  `extract_sections`, `validate`, and the weight constants.
- `tools/gotdocs/cli.py` — `cmd_why`, the `--all` / `--path` / `--full` /
  `--limit` flags and the "never an error" docstring.
- `tools/gotdocs/report.py` — `render_why_json`, `render_why_path_text`.
- `.gotdocs/templates/decision.md` — the scaffold, including the note that
  `symptoms` must be written in the observer's words.
- `.claude/skills/gotdocs-update/SKILL.md` — step 2b, where an agent is told to
  ask `why` before editing a document.

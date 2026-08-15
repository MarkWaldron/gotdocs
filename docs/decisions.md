---
id: decisions
title: Decision Records — Is This a Bug, or Did We Mean It?
type: doc
summary: The ADR flow in gotdocs — how a record is numbered, linted and superseded, and how `gotdocs why` answers the bug-versus-intentional-tradeoff question from a symptom description.
covers:
  - tools/gotdocs/decisions.py
  - .gotdocs/templates/decision.md
owners: ["@mark"]
tags: [decisions, adr, why, debugging]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Decision Records — Is This a Bug, or Did We Mean It?

Every other document in gotdocs answers *how does this work*. Decision records
answer a different question, and it is the one that gets asked at the worst
moment:

> A POST is being retried exactly twice and then failing fast. Is that a bug I
> should fix, or a tradeoff somebody made on purpose in 2024?

Getting that wrong is expensive in both directions. Treating an intentional
tradeoff as a bug means "fixing" it, breaking whatever it was protecting, and
finding out in an incident. Treating a bug as intentional means shipping it
forever because someone assumed the odd behaviour was load-bearing.

Reference documentation does not resolve this. It describes what the system does,
not what was deliberately given up. So decision records carry two things nothing
else does: a list of **symptoms** in the frontmatter, and two body sections that
say exactly where the decision's authority stops.

## The loop

```text
1. observe something odd
2. bin/gotdocs why "<what you observed, in your own words>"
3a. a record matches, and the symptom is in its "Expected behavior"
       -> intentional. Read Consequences. Do not "fix" it.
3b. a record matches, but your symptom is in its
       "This is a bug, not this decision, if..."
       -> bug. The decision does not cover this. Investigate.
3c. nothing matches
       -> treat as unintended until proven otherwise, then write the record
          once you know the answer
```

Step 3b is the part that makes this work and the part people leave out of ADRs.
A decision record that only says what was decided will be cited to explain
anything vaguely nearby. A record that also states what it does **not** explain
stops that.

## Layout and numbering

One file per record under `decisions/`, named `NNNN-slug.md`:

```text
decisions/0001-docs-live-in-the-repo.md
decisions/0007-retry-budget-per-request.md
```

The four-digit number is the record's identity and it comes from the **filename**
— there is no `number` frontmatter key that could drift out of sync. `id` must
equal the filename stem, so a cross-reference like
`supersedes: [0004-retry-per-hop]` is unambiguous and greppable.

`bin/gotdocs new decision` allocates the number as one past the highest already
on disk, reading filenames only (a record with broken frontmatter still holds its
number). **Gaps are never filled.** A number that was once `0004` is referenced
from other records, commit messages and review threads; handing it to a different
decision would silently reroute all of those. A gap is a lint finding, closed by
renaming the tail of the sequence, not by reuse.

The root is configurable: `decisions_root` matches any configured root named
`decisions` or ending in `/decisions`, so a repo that uses `adr/` or
`docs/decisions/` still gets `why` and the ADR lint rules.

## Frontmatter

Everything an ordinary document has, plus four keys:

```yaml
---
id: 0001-docs-live-in-the-repo
title: Documentation lives in the repository, in git, next to the code
type: decision
summary: Docs are markdown files in the working tree under configured roots, versioned by git, with no external wiki, database or hosted service in the loop.
covers:
  - .gotdocs/config.json
  - tools/gotdocs/config.py
symptoms:
  - I edited the wiki and gotdocs still says the doc is stale
  - a doc I deleted on a branch is back after I switched branches
  - my documentation edit needs a code review before it lands
supersedes: []
superseded_by: []
status: accepted
decided_on: 2026-08-14
updated: 2026-08-14
verified_at: 3d8b6cd
---
```

| Key | Meaning |
| --- | --- |
| `symptoms` | Observable behaviours this record explains. **This is what `why` searches.** Write them the way somebody would describe the problem, not the way you would name the design. |
| `supersedes` | Ids of records this one replaces. |
| `superseded_by` | Ids of records that replaced this one. |
| `decided_on` | A real calendar date, `YYYY-MM-DD`. |

`status` uses its own enum: `proposed` | `accepted` | `rejected` | `superseded`.
"current" says nothing about a decision and "accepted" says nothing about a doc,
so the two enums are disjoint and `lint` picks the right one from `type`.

Decision records also have `covers`, like any other document, and participate in
staleness checking like any other document. If the code a decision governs
changes, the decision is impacted and someone has to confirm it still holds.

## The two body sections

`bin/gotdocs why` quotes these two headings and nothing else from the body:

```markdown
## Expected behavior

The observable consequences. If you are seeing one of these, the system is
working as designed.

## This is a bug, not this decision, if...

What this decision does NOT explain, and therefore needs investigating.
```

Heading matching is normalized — case, trailing punctuation and the trailing
ellipsis are all tolerated — so `## This is a bug, not this decision, if…`
resolves the same way.

Write the second section as concretely as the first. From
`decisions/0001-docs-live-in-the-repo.md`:

> - `bin/gotdocs` makes any network call at all. There is no code path that opens
>   a socket; if you see one under `strace`/`dtruss`, that is a defect.
> - A markdown file placed under a configured root with valid frontmatter does
>   not appear in `bin/gotdocs status` or `.gotdocs/INDEX.md` after
>   `bin/gotdocs index`. Walking is done in `tools/gotdocs/index.py:scan`; a miss
>   there is a bug in the walk or in the `ignore` globs, not this decision.

Each bullet names the symptom, the module, and the verdict. Somebody triaging at
2am can act on that.

## `gotdocs why`

```sh
bin/gotdocs why "<what you actually observed>"
```

Scoring is plain token overlap with light stemming — no index, no embeddings, no
network. Each field contributes `weight × (matched query terms ÷ total query
terms)`:

| Field | Weight |
| --- | --- |
| `symptoms` (best-matching one) | 4.0 |
| `title` | 2.0 |
| `summary` | 1.0 |
| `tags` | 0.5 |

A query that appears verbatim inside one symptom earns a further `+2.0`.
Normalizing by the number of query terms is what stops a wordy record from
beating a short precise query. Records scoring zero are dropped; ties break on
the record id, so ordering is stable across runs and machines.

Ranking alone is not decisive enough. With eight records and a six-word symptom,
almost every record shares *some* word with the query, so a ranked list of eight
is seven wrong answers with the right one on top. Two relevance floors cut the
tail:

| Floor | Rule |
| --- | --- |
| `MIN_TERM_COVERAGE` (0.34) | The record must overlap at least about a third of the query's distinct terms. Sharing one word out of six is a coincidence. |
| `RELATIVE_FLOOR` (0.45) | The record must score at least 45% of the leader's score. |

Both apply to the leader too. If the best record in the repository shares one
word out of six with the symptom, then nothing was written down about it, and
saying so beats pointing at the least-irrelevant record. The fix for a genuine
miss is to add the observer's phrasing to that record's `symptoms`, not to
loosen the floors.

A real invocation, against the record shown above:

```text
$ bin/gotdocs why "my documentation edit needs a code review before it lands"
1 decision matches "my documentation edit needs a code review before it lands" (of 8 searched):

[1] 0001-docs-live-in-the-repo  (accepted)  decisions/0001-docs-live-in-the-repo.md
    Documentation lives in the repository, in git, next to the code
    symptom:  my documentation edit needs a code review before it lands
    expected: `bin/gotdocs status` and `bin/gotdocs lint` work with the network disabled,...
    bug if:   `bin/gotdocs` makes any network call at all.
```

Five lines per record: id, status and path; title; the symptom that matched; then
the leading claim of each of the two sections. Those two lines are the first
*claim*, not the first 80 characters: the list marker is stripped and the text
ends at a sentence boundary. `--full` prints the sections whole, `--limit N`
changes how many records print (default 3, `0` for all).

A miss is a first-class answer, not an error:

```text
$ bin/gotdocs why "kubernetes pod eviction"
no decision matches "kubernetes pod eviction" (of 8 searched).

Nothing was written down that explains this. Treat it as unintended
until proven otherwise, and consider recording the answer you find.
$ echo $?
0
```

That exit code is deliberate. A repository with no decisions, or a query nothing
matches, is the common case — making it exit non-zero would put a hard failure in
the middle of an agent's diagnosis loop for the *absence* of information.

### By path

With no query, `--path` lists every decision governing a file:

```text
$ bin/gotdocs why --path tools/gotdocs/config.py
1 decision covers tools/gotdocs/config.py (of 8 searched):

[1] 0001-docs-live-in-the-repo  (accepted)  decisions/0001-docs-live-in-the-repo.md
    Documentation lives in the repository, in git, next to the code
    expected: `bin/gotdocs status` and `bin/gotdocs lint` work with the network disabled, o...
    bug if:   `bin/gotdocs` makes any network call at all.
```

Run this before changing unfamiliar code, not after. It is the difference between
removing a constraint and discovering why it was there.

`--path` also combines with a query, narrowing the search to decisions that cover
that file.

### Which line you get

Each of the two sections is a bullet list, and the default output shows **one**
bullet from each: the one sharing the most terms with your query, falling back to
the first when nothing overlaps or two tie. A record's `Expected behavior` leads
with the common case, and the case you asked about is usually further down —
showing the first bullet made a correctly-routed record read as if it did not
apply. `--full` prints both sections whole; use it when the clipped line does not
settle the question.

### Records not in force

`rejected` and `superseded` records are **excluded by default**. Citing a
superseded decision as the reason for current behaviour is precisely the mistake
this command exists to prevent. `--all` includes them, for when you are
reconstructing history rather than diagnosing today.

### `--json`

Adds `query`, `path`, `searched`, `match_count`, and per match the `score`, the
`matched_symptom`, the `matched_terms` after stemming, and the **full** text of
both sections rather than the clipped lines. That is the shape an agent should
consume:

```json
{
  "ok": true,
  "query": "I edited the wiki and gotdocs still says the doc is stale",
  "path": null,
  "searched": 8,
  "match_count": 1,
  "matches": [
    {
      "id": "0001-docs-live-in-the-repo",
      "number": "0001",
      "status": "accepted",
      "score": 6.2857,
      "matched_symptom": "I edited the wiki and gotdocs still says the doc is stale",
      "matched_terms": ["edited", "wiki", "gotdoc", "still", "say", "doc", "stale"]
    }
  ]
}
```

## Writing one

```sh
bin/gotdocs new decision "Retry budget is per request" \
  --covers 'src/http/**' \
  --symptom "a POST is retried exactly twice and then fails fast" \
  --symptom "the retry count does not grow with the number of hops"
```

The positional argument is the **title**, not the id — the id is allocated. The
CLI then tells you what is left to do:

```text
gotdocs: created decisions/0002-retry-budget-is-per-request.md
gotdocs: fill in 'symptoms', 'Expected behavior' and 'This is a bug, not this decision, if...',
         set status: accepted when it is agreed, then run: bin/gotdocs index
```

The scaffold comes out at `status: proposed`, `decided_on: 1970-01-01` and
`verified_at: 0000000` — placeholders, on purpose. Stamping a real sha would
assert that somebody read an empty file against the code, and the audit skill
keys its `never-verified` detection off exactly that placeholder. Leave the
status at `proposed` while it is under discussion; `lint` only demands the full
shape once it is `accepted`.

## What `lint` enforces

`bin/gotdocs lint` runs these over every file under the decisions root. Selection
is **by root, not by `type`**: a file under `decisions/` that forgot
`type: decision` is exactly the file that most needs these rules run over it, and
selecting on the field it is missing would let it through.

| Rule | Applies to |
| --- | --- |
| `type` is `decision`; `status` is in the decision enum | every record |
| filename is `NNNN-slug.md` and `id` equals the stem | every record |
| numbers are unique and contiguous from `0001` | the set |
| every `supersedes` / `superseded_by` id resolves to a real record | every record |
| supersession is **bidirectional** — if A supersedes B, B must name A back | every record |
| `symptoms` is non-empty | `accepted` records |
| both body sections are present | `accepted` records |
| a `superseded` record names its successor | `superseded` records |
| a record that names a successor is marked `superseded` | every record |
| the `superseded_by` chain reaches a record that is **in force** (`accepted` or `proposed`) | `superseded` records |

The bidirectional rule is worth the friction. A one-way link means the old record
still reads as authoritative to anyone who lands on it directly, which is the
common case — people find the old one by searching for the old behaviour.

Superseding a record is therefore a three-line change in two files:

```yaml
# decisions/0004-retry-per-hop.md
status: superseded
superseded_by: [0007-retry-budget-per-request]

# decisions/0007-retry-budget-per-request.md
supersedes: [0004-retry-per-hop]
```

Do not delete the old record. It is the only thing that explains why the code
looked the way it did, and `why --all` is how someone finds that later.

The reachability rule catches the two shapes bidirectionality alone lets
through, both of which retire a decision and leave the behaviour it described
with no live explanation:

- **a cycle** — 0001 superseded by 0002, 0002 superseded by 0001; the mirrors are
  consistent and the chain never lands anywhere.
- **a chain ending in a retired record** — the realistic one. You supersede 0001
  with 0002, then 0002 is rejected. 0001 stays `superseded`, `why` excludes both,
  and asking about the symptom returns "nothing was written down".

```text
$ bin/gotdocs lint
gotdocs: 1 lint error in 2 documents

  decisions/0001-first.md:15: 'superseded_by' never reaches a decision that is in force:
    it ends at 0002-second, which is not in force. Point it at an accepted or
    proposed record, or reopen this one.
```

The fix is one of: point `superseded_by` at the record that actually replaced it,
or move the original back to `accepted` because the replacement did not happen.

## Related

- [cli-reference.md](cli-reference.md#gotdocs-why) — every `why` flag and its exit codes
- [doc-format.md](doc-format.md) — the frontmatter rules decision records share with every other document
- [agent-workflow.md](agent-workflow.md) — where `why` sits in an agent's loop
- [enforcement.md](enforcement.md) — the CI comment tells reviewers to run `why` before assuming a bug

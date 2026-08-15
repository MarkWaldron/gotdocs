---
id: 0008-index-first-context-economy
title: A committed one-line-per-doc index is the entry point, and it is budgeted
type: decision
summary: Agents read .gotdocs/INDEX.md first and open only what they need; the file is one line per document, excludes symptoms and bodies, and is committed and byte-reproducible.
covers:
  - tools/gotdocs/index.py
  - .gotdocs/INDEX.md
  - .gotdocs/index.json
symptoms:
  - the index lists documents but not their contents
  - symptoms are in the decision files but not in INDEX.md
  - only accepted decisions appear in the index
  - INDEX.md and index.json are generated but still committed to git
  - CI failed because the committed index does not match the tree
  - my summary was rejected for being over 200 characters
  - the agent opened three docs instead of reading all thirteen
  - running gotdocs index twice produces no diff the second time
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - agents
  - index
  - tokens
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: 3d8b6cd
---

# A committed one-line-per-doc index is the entry point, and it is budgeted

## Context

The primary consumer of this documentation is a coding agent with a finite
context window, and the naive access pattern destroys it: read every document
under `docs/`, `runbooks/`, `onboarding/` and `dependencies/` at the start of
every session. Thirteen documents in this repository already run to tens of
thousands of tokens, and the ratio only gets worse — a repo with 200 documents
cannot be read at all.

The agent does not need the documents. It needs to know *which* document to open.
That is a routing problem, and routing needs one line per document, not the
document.

The second half of the problem is trust. If the index is generated on demand, it
is generated from whatever tree happens to be checked out, and nothing in a
review ever shows that it changed. If it is committed but not enforced, it drifts
and starts lying.

## Decision

`.gotdocs/INDEX.md` is the entry point and is budgeted to **exactly one line per
document**, grouped by type:

```text
- **id** - summary  ·  `path`  ·  covers: pattern, pattern
```

Nothing else goes in it. Bodies do not, and `symptoms` explicitly do not — they
are several lines per decision record and are the search corpus for
`bin/gotdocs why`, which reads the records directly. Only `accepted` decision
records are listed; the rest appear as a count.

`summary` is capped at 200 characters (`max_summary_chars`) because that string
is the *only* text an agent sees before deciding whether to open the file.

`.gotdocs/index.json` is the machine-readable twin. Both are committed, both are
byte-reproducible, and a committed copy that disagrees with the tree fails CI
unconditionally (0004).

## Expected behavior

- `.gotdocs/INDEX.md` is exactly one line per document, grouped by type, after a
  short instruction and a count. Bodies and `symptoms` are not in it.

  ```text
  **Read this file first, then open only the docs you need.** ...

  25 documents under `docs/`, `runbooks/`, `onboarding/`, `dependencies/`, `decisions/`.

  ## doc

  - **doc-format** - Every frontmatter field, what covers means, ...  ·  `docs/doc-format.md`  ·  covers: `.gotdocs/schema.json`, ...
  ```

- The `## Decisions` section lists `accepted` records only, with no `symptoms`,
  and points at the right tool:
  "Architecture decisions in force. Before calling behaviour a bug, ask
  `bin/gotdocs why "<what you observed>"` — it searches what each record
  explains, which is deliberately not reproduced here."
  Non-accepted records are summarised as
  `Not listed: 1 proposed, 2 superseded. Run 'bin/gotdocs why --all' to see every record.`
- `bin/gotdocs index` is reproducible: docs sorted by `id` then `path`, JSON at
  2-space indent with explicit key order and a trailing newline. Running it twice
  with no source change rewrites identical bytes and reports no change.
- `generated_at_sha` is the only volatile field, and `index_is_current` excludes
  it from the comparison — so a new commit does not make every checkout report
  `index_out_of_date`, and the CI index gate is not permanently red.
- `.gotdocs/index.json` carries `{version, generated_at_sha, roots, doc_count,
  docs}`, and each doc carries `id, path, type, title, summary, status, covers,
  owners, tags, updated, verified_at` plus the decision-only fields
  (`symptoms`, `supersedes`, `superseded_by`, `decided_on`) as first-class keys.
  Symptoms live here, in the machine index that is read by tools, not in the
  markdown one that is read into context.
- A summary over the cap is a lint finding with the exact count:
  `'summary' is 214 characters; the limit is 200`, and `bin/gotdocs lint`
  exits 2.
- Both generated files are in the default `ignore` list, so regenerating them
  never itself trips a staleness finding.
- The agent workflow that follows from this is: read `.gotdocs/INDEX.md`, open
  only the matching documents, then use `bin/gotdocs impacted <path>` and
  `bin/gotdocs check --json` for the structured answers.

## This is a bug, not this decision, if...

- `bin/gotdocs index` run twice produces a diff on the second run. Only
  `generated_at_sha` may differ, and only when HEAD moved; a no-op run that
  churns bytes is a reproducibility bug in `tools/gotdocs/index.py`.
- A document under a configured root is missing from `.gotdocs/INDEX.md` after
  `bin/gotdocs index`, or a deleted one survives.
- A document occupies more than one line in `.gotdocs/INDEX.md` — for instance
  because a `summary` contains a newline. Multi-line summaries are impossible in
  the frontmatter subset (0005); if one gets through, that is a parser bug.
- `symptoms` appear in `.gotdocs/INDEX.md`. They belong in `index.json` and in
  the record files only. This is the one field whose absence from the markdown
  index is load-bearing.
- A `proposed`, `rejected` or `superseded` decision is listed in the
  `## Decisions` section rather than counted under "Not listed".
- The CI index gate goes red on a tree where `bin/gotdocs index` produces no
  change — usually a sign that `generated_at_sha` leaked into the comparison in
  `index_is_current`.
- `index.json` key order or float/int formatting varies between machines or
  Python patch versions, making the committed file churn for two contributors
  with identical trees.
- `bin/gotdocs status` and `.gotdocs/INDEX.md` disagree about the document set.
  Both derive from `index.scan`; a disagreement means one of them is reading
  something else.
- Note what is **not** a bug: the index telling you almost nothing about a
  document's contents. That is the budget. If a one-line summary is not enough to
  route on, the fix is a better `summary`, not more lines in the index.

## Consequences

The whole system now leans on the quality of one 200-character sentence per
document. A vague summary ("Notes about the API") makes the document effectively
unreachable — the agent has no basis to open it, and the index gives no second
chance. That failure is silent; nothing lints for a useless summary.

Two generated files are committed, so every documentation change is a two-file
change and `bin/gotdocs index` must be run before committing. Forgetting it is
the most common way to turn a pull request red (0004), and it reads as
bureaucratic the first few times.

Merge conflicts in `.gotdocs/index.json` are routine when two branches add
documents. They are always resolved the same way — take either side and re-run
`bin/gotdocs index` — but they are noise in the review.

## Alternatives considered

- **No index; let the agent glob and read.** Rejected: this is the failure being
  fixed. It scales as O(total documentation) per session.
- **Generate the index on demand instead of committing it.** Rejected: a
  generated-on-demand index cannot be reviewed, cannot be diffed, and means every
  agent session pays to walk and parse the whole tree before it can route.
  Committing it also lets CI prove it is honest.
- **`index.json` only, no markdown twin.** Rejected: an agent reading a JSON blob
  into context pays for punctuation and key names on every entry. The markdown
  form is roughly half the tokens for the same routing information.
- **Include `symptoms` in `INDEX.md` so `why` is unnecessary.** Rejected: that is
  several lines per decision record, in the one file that is read whole on every
  session, to serve a lookup that happens occasionally. `bin/gotdocs why` reads
  the records on demand instead.
- **A per-directory `README.md` index instead of one central file.** Rejected:
  the agent then has to discover and read N index files to route, which is the
  original problem with extra steps.
- **Include the first paragraph of each document.** Rejected: unbounded, and it
  duplicates the job `summary` already does under a hard cap.

## Revisit when

Revisit when a repository's `.gotdocs/INDEX.md` itself becomes too large to read
whole — roughly a few hundred documents. The shape that follows is a two-level
index (one line per *root*, with per-root index files fetched on demand), not
more fields per line. Also revisit if agents are observed opening documents the
index did not point them at, which would mean the summaries are not carrying
enough routing signal.

## References

- `tools/gotdocs/index.py` — `render_markdown`, `_decision_section`,
  `render_json`, `write_index`, `index_is_current`, and the reproducibility rules
  in the module docstring.
- `tools/gotdocs/config.py` — `max_summary_chars`, `INDEX_JSON_PATH`,
  `INDEX_MD_PATH`, and the `ignore` entries for both generated files.
- `.gotdocs/INDEX.md` / `.gotdocs/index.json` — the artifacts themselves.
- `docs/agent-workflow.md` — the index-first pattern an agent follows.
- `.github/workflows/gotdocs.yml` — the always-blocking index gate.

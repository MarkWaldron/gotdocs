---
id: cli-reference
title: Gotdocs CLI Reference
type: doc
summary: Every gotdocs command, flag and exit code, plus the --json output shape that agents and CI depend on.
covers:
  - tools/gotdocs/**
  - bin/gotdocs
owners: ["@mark"]
tags: [cli, reference, json, agent-interface]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Gotdocs CLI Reference

```text
bin/gotdocs <command> [args] [flags]
```

`bin/gotdocs` is a POSIX `sh` shim that locates `python3` and execs
`python3 tools/gotdocs`. Both spellings work; use `bin/gotdocs` in documentation,
hooks and CI so there is one string to grep for.

## Global flags

Accepted by every command.

| Flag | Effect |
| --- | --- |
| `--repo PATH` | Operate on this repo instead of discovering the toplevel from the current directory. |
| `--quiet` | Suppress informational output. Findings and errors still print. |
| `--no-color` | Disable ANSI color. Color is also disabled automatically when stdout is not a TTY, and when `NO_COLOR` is set. |
| `--strict` | Turn internal errors into failures instead of warn-and-exit-0. |
| `-h`, `--help` | Usage for the CLI or the subcommand. |

`--version` is top-level only: `bin/gotdocs --version`, not
`bin/gotdocs check --version`. It prints `gotdocs 1`.

Every subcommand that produces output accepts `--json`. The two exceptions are
the bare group commands `bin/gotdocs` and `bin/gotdocs debt`, which only print
help.

## Commands

| Command | What it answers |
| --- | --- |
| [`check`](#gotdocs-check) | What did this change set make stale? |
| [`impacted`](#gotdocs-impacted) | Which documents describe these files? |
| [`verify`](#gotdocs-verify) | I read it against the new code; it is still accurate. |
| [`index`](#gotdocs-index) | Regenerate the committed index. |
| [`lint`](#gotdocs-lint) | Is every document's frontmatter valid — and will it publish? |
| [`status`](#gotdocs-status) | What does gotdocs think of this repo right now? |
| [`install`](#gotdocs-install) | Put the pre-commit hook in `.git/hooks/`. |
| [`new`](#gotdocs-new) | Scaffold a document from a template. |
| [`why`](#gotdocs-why) | Is this behaviour an intentional decision, or a bug? |
| [`export`](#gotdocs-export) | Render the documents for a static site generator. |
| [`debt`](#gotdocs-debt) | What did we knowingly defer? |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Clean, or findings exist but the effective mode is `warn` or `off`. Also the exit code of every read-only command (`impacted`, `status`, `why`, `debt list`, `debt stats`) whatever it found. |
| `1` | Findings exist **and** the effective mode is `error`. Produced only by `check`. |
| `2` | Usage error; a fatal lint problem (unparseable frontmatter, duplicate `id`); a doc id that does not exist; a `debt resolve` reference that matched nothing; a malformed `.gotdocs/config.json`. |
| `3` | Not a git repo, or git cannot answer the question asked (unknown base ref, no merge base, no commits yet). |

Exit `3` is about **git and repository location only**. A *missing*
`.gotdocs/config.json` is never an error at all: the built-in defaults are used,
which is what lets `install` bootstrap a repo that has no config yet. A config
file that exists but is malformed is exit `2`, not `3` — silently ignoring it
would change enforcement without telling anyone. The only config-shaped path to
exit `3` is failing to locate a repository at all: no git toplevel *and* no
`.gotdocs/` directory in any parent.

An unexpected internal exception prints `gotdocs: internal error: <msg>` on stderr
and exits `0` — unless `--strict` is set, in which case it prints a traceback and
exits `2`. Gotdocs is never the reason a commit cannot happen.

---

## `gotdocs check`

The core command. Computes impacted docs for a change set and reports the stale ones.

```text
bin/gotdocs check [--staged | --base REF | --paths PATH...] [--json]
                  [--mode off|warn|error] [--message TEXT | --message-file PATH]
```

| Flag | Effect |
| --- | --- |
| `--staged` | Change set is `git diff --cached --name-status`. What the pre-commit hook uses. |
| `--base REF` | Change set is `git diff --name-status REF...HEAD` — three-dot, so it compares against the merge base of `REF` and `HEAD`. What CI and the pre-push hook use. |
| `--paths PATH...` | Change set is the literal list of paths. Needs no git history; useful for testing and for agents reasoning about files they are about to change. |
| `--json` | Emit the JSON contract below instead of human text. |
| `--mode MODE` | Override the configured mode for this run. `off` \| `warn` \| `error`. |
| `--message TEXT` | Scan this string for the skip token instead of `.git/COMMIT_EDITMSG`. Pass `''` to disable the message check entirely. |
| `--message-file PATH` | Read the commit message from this file instead of `.git/COMMIT_EDITMSG`. |

Every path argument — `--paths` here, and the positional paths of `impacted` —
is resolved against the current directory and re-expressed relative to the
repository root, the way git resolves a pathspec. Absolute paths, `..` paths and
plain relative paths all work, so `cd src && ../bin/gotdocs impacted app.py`
names `src/app.py`. When a plain relative path exists under the cwd it is read
as cwd-relative; when it does not exist there but does exist at the repository
root, the repo-root reading wins, so a script that passes repo-relative paths
from a subdirectory keeps working. A path that lands outside the repository is a
usage error (exit 2), never a silent "no findings".

Source selection is mutually exclusive. With none of the three, `check` defaults
to `--staged`.

Without `--mode`, the effective mode is `enforce.pre_commit` for `--staged` and
`enforce.ci` for `--base` and `--paths`. The shipped config sets **all three to
`warn`**, so on a stock repo every source exits `0` regardless of findings:

```sh
$ bin/gotdocs check --paths tools/gotdocs/check.py
gotdocs: 6 findings (mode: warn)
...
$ echo $?
0
```

Pass `--mode error` explicitly when the exit code matters — that is exactly what
CI does, and why the CI job can collect a non-zero status while `enforce.ci` is
still `warn`.

Rule, in order:

1. Build the change set.
2. Split it into doc paths (inside a configured root) and code paths (everything
   else, minus `ignore` globs).
3. A doc is **impacted** when any code path matches any of its `covers` globs.
4. An impacted doc is **satisfied** when its own file is in the change set, or
   when its `verified_at` equals the head sha of the change set. Otherwise it is
   **stale** and produces a finding.
5. Code paths matching no doc's `covers` produce `uncovered` findings only when
   `require_coverage` is `true`.
6. Doc-side findings are always reported: lint errors, edits to
   `status: deprecated` docs, duplicate ids, and a committed index that no longer
   matches the working tree.
7. Everything is skipped when `GOTDOCS_SKIP=1` is set, or when the commit
   message carries the skip token. With `--staged` and no `--message`, the
   message is read from `.git/COMMIT_EDITMSG` **only when it differs from
   HEAD's message** — git writes that file after the pre-commit stage, so a
   leftover from the previous commit is ignored rather than treated as a skip
   request.

Human output is grouped by finding kind and every line carries a remediation:

```text
$ bin/gotdocs check --paths tools/gotdocs/check.py
gotdocs: 6 findings (mode: warn)

stale (6)
  decisions/0003-covers-globs-as-the-staleness-signal.md  [0003-covers-globs-as-the-staleness-signal]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/check.py
    -> update decisions/0003-covers-globs-as-the-staleness-signal.md, or run: bin/gotdocs verify 0003-covers-globs-as-the-staleness-signal
  decisions/0004-ci-records-debt-instead-of-blocking.md  [0004-ci-records-debt-instead-of-blocking]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/check.py
    -> update decisions/0004-ci-records-debt-instead-of-blocking.md, or run: bin/gotdocs verify 0004-ci-records-debt-instead-of-blocking
  decisions/0006-verify-stamp-as-the-escape-hatch.md  [0006-verify-stamp-as-the-escape-hatch]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/check.py
    -> update decisions/0006-verify-stamp-as-the-escape-hatch.md, or run: bin/gotdocs verify 0006-verify-stamp-as-the-escape-hatch
  docs/architecture.md  [gotdocs-architecture]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/**
    -> update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture
  docs/cli-reference.md  [cli-reference]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/**
    -> update docs/cli-reference.md, or run: bin/gotdocs verify cli-reference
  runbooks/stale-doc-triage.md  [runbook-stale-doc-triage]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/check.py
    -> update runbooks/stale-doc-triage.md, or run: bin/gotdocs verify runbook-stale-doc-triage

Or ask Claude: /gotdocs-update
```

`index_out_of_date` is absent above because the committed index matches the tree.
It appears — as its own group, in addition to the `stale` ones — whenever
`.gotdocs/index.json` or `.gotdocs/INDEX.md` is behind the documents on disk.

Examples:

```sh
bin/gotdocs check --staged                      # what the hook runs
bin/gotdocs check --base origin/main            # what CI runs
bin/gotdocs check --base origin/main --mode error --strict
bin/gotdocs check --paths src/api/routes.py     # "if I changed this, what breaks?"
bin/gotdocs check --staged --json | python3 -m json.tool
```

---

## `gotdocs impacted`

```text
bin/gotdocs impacted <path>... [--json]
```

Answers "which docs describe these files?" for an arbitrary path list. No git, no
staleness, no exit-code enforcement — it is the read-only lookup, and it is the
first thing an agent should run before editing unfamiliar code.

It classifies each path exactly as `check` does: a path inside a doc root is a
*doc path* (reported as `(doc)`, never matched against anyone's `covers`), a path
matching `ignore` is `(ignored)`, everything else is a code path. Absolute and
`../` paths are accepted and resolved against the repository root.

```text
$ bin/gotdocs impacted tools/gotdocs/globs.py bin/gotdocs
tools/gotdocs/globs.py
  0002-python-stdlib-only-cli  decisions/0002-python-stdlib-only-cli.md  (tools/gotdocs/globs.py)
  cli-reference                docs/cli-reference.md                     (tools/gotdocs/**)
  doc-format                   docs/doc-format.md                        (tools/gotdocs/globs.py)
  gotdocs-architecture         docs/architecture.md                      (tools/gotdocs/**)
bin/gotdocs
  0002-python-stdlib-only-cli  decisions/0002-python-stdlib-only-cli.md  (bin/gotdocs)
  cli-reference                docs/cli-reference.md                     (bin/gotdocs)
  dependency-python3           dependencies/python3.md                   (bin/gotdocs)
  gotdocs-architecture         docs/architecture.md                      (bin/gotdocs)
  onboarding-local-setup       onboarding/local-setup.md                 (bin/gotdocs)
```

Decision records participate like any other document: they have `covers`, so
they show up here, and `bin/gotdocs why --path <file>` is the narrower lookup
that returns only them.

Exit code is `0` whether or not anything matched. `--json` shape:

```json
{
  "ok": true,
  "paths": [
    {
      "path": "tools/gotdocs/globs.py",
      "ignored": false,
      "doc_path": false,
      "docs": [
        { "doc_id": "gotdocs-architecture", "path": "docs/architecture.md", "matched": ["tools/gotdocs/**"] }
      ]
    }
  ]
}
```

`"ignored": true` means the path matched an `ignore` glob and was not considered
at all.

---

## `gotdocs verify`

```text
bin/gotdocs verify <doc-id>... [--all-impacted]
```

Stamps `verified_at` to the short sha of `HEAD` and `updated` to today's date, in
place, for each named doc. Only those two lines change; every other byte of the
file is preserved. With `--all-impacted`, the doc ids are taken from the current
`check --staged` result instead of the command line.

This is an assertion, not a formality: it says *"I read this document against the
new code and it is still accurate."* It lands in the diff, so a reviewer can see
who asserted it. Use it when the code changed but the documented behavior did
not — a refactor, a rename, a performance fix. Do not use it to clear a finding
you did not read. See [runbooks/stale-doc-triage.md](../runbooks/stale-doc-triage.md).

```sh
bin/gotdocs verify gotdocs-architecture
bin/gotdocs verify cli-reference doc-format
bin/gotdocs verify --all-impacted
```

Exit `2` if a doc id does not exist. Exit `3` outside a git repo (it needs a sha).
After running it, `git add` the changed doc files — `verify` edits the working
tree, it does not stage.

---

## `gotdocs index`

```text
bin/gotdocs index [--json]
```

Regenerates `.gotdocs/index.json` and `.gotdocs/INDEX.md` from the frontmatter of
every file under the configured roots. Both are committed; both are reproducible,
so running this twice with no source change rewrites identical bytes and produces
no diff.

That holds across commits too. `generated_at_sha` records the commit at which the
*documents* were last indexed, not whichever commit happens to be checked out: if
nothing else in the payload changed, the sha already in the file is kept. A commit
can never contain its own sha, so re-stamping HEAD on every run would leave every
committed index permanently "drifted" and the CI freshness gate permanently red.

Prints the number of docs written and whether either file changed. `--json` emits
`{"ok": true, "doc_count": 13, "changed": [".gotdocs/index.json"]}`.

Run it after adding, deleting, renaming or re-frontmattering a doc. `check`
reports an out-of-date index as a finding rather than silently regenerating,
because regenerating files during a pre-commit hook surprises people.

---

## `gotdocs lint`

```text
bin/gotdocs lint [--json] [--portability] [--targets NAME[,NAME]] [--rules NAME[,NAME]]
```

Validates frontmatter across all roots without looking at any change set. Reports
missing required fields, malformed `id`, duplicate `id`, unknown `type` or
`status`, over-long `summary`, malformed `updated`, unsupported YAML constructs
with a `file:line` pointer, syntactically invalid `covers` patterns, invalid
`ignore` patterns in `.gotdocs/config.json`, and the ADR-specific rules for
anything under `decisions/`.

```text
$ bin/gotdocs lint
gotdocs: no lint errors in 14 documents
```

Exit `0` clean, `2` when there are lint errors. A `covers` pattern that matches
zero files today is legal and is not reported.

### `--portability`

Also checks that every document renders correctly on all six supported static
site generators. Reported as **warnings** by default — a document that renders
oddly on one of six generators is worth saying out loud, but it is not worth
failing a commit that has nothing to do with it:

```text
$ bin/gotdocs lint --portability
gotdocs: no lint errors in 25 documents

gotdocs: no portability warnings
$ echo $?
0
```

`--strict` promotes them to findings, so the command exits `2`. `--targets` and
`--rules` narrow the run; an unknown name in either is a usage error (exit `2`)
that lists the valid ones. The rules, the six targets and what each severity
means are in [publishing.md](publishing.md#the-lint-rules).

In `--json`, portability warnings are a separate `warnings` array with a
`summary.warnings` count. `findings` and `ok` keep their exact meaning, so a
consumer that reads only those behaves as it did before `--portability` existed.

---

## `gotdocs status`

```text
bin/gotdocs status
```

One screen of state, for humans and for the first thing an agent runs in an
unfamiliar repo:

```text
$ bin/gotdocs status
gotdocs 1  repo /Users/mark/Code/gotdocs  head 3d8b6cd
config    .gotdocs/config.json
roots     docs, runbooks, onboarding, dependencies, decisions
docs      21 (13 current, 0 draft, 0 deprecated)
index     out of date: .gotdocs/index.json, .gotdocs/INDEX.md — run: bin/gotdocs index
enforce   pre_commit=warn  pre_push=warn  ci=warn  require_coverage=false
hook      .git/hooks/pre-commit installed but differs from .gotdocs/hooks/pre-commit — run: bin/gotdocs install --force
```

The parenthesised counts use the ordinary `current` / `draft` / `deprecated`
enum only. Decision records use a different enum (`proposed` / `accepted` /
`rejected` / `superseded`) and are deliberately excluded from all three buckets
rather than being counted as `unknown`, so the three numbers do not add up to
the total in a repo that has decisions. That is why `21 (13 current, ...)` above
is correct and not a bug.

Every line that reports a problem carries its own remediation command.

Each line that reports a problem carries its own remediation command. Exit `0`
always, including in a directory that has a `.gotdocs/` but no git (head shows
`(no commits)` and the hook line says `unknown`); exit `3` only when neither a
repository nor a `.gotdocs/` can be found. `--json` emits the same state as an
object.

---

## `gotdocs install`

```text
bin/gotdocs install [--force]
```

Installs `.gotdocs/hooks/pre-commit` into `<git-dir>/hooks/pre-commit` and makes
it executable. Idempotent: if the installed file is byte-identical to the source
it reports `already up to date` and does nothing.

If a different hook is already there:

- it contains `gotdocs` — treated as an older gotdocs hook, overwritten, previous
  contents copied to `pre-commit.bak`
- it does not — `install` refuses with exit 2 and tells you to chain it manually
  or re-run with `--force`, which overwrites and leaves `pre-commit.bak`

**Use `scripts/install-gotdocs.sh` instead for normal setup.** It does more: it
installs *both* the pre-commit and pre-push hooks, honors `core.hooksPath`,
preserves any pre-existing hook as `<hook>.local` (which the gotdocs hook then
runs first, veto included) rather than backing it up and dropping it, creates
`.gotdocs/config.json` from a starter template if absent (a deliberately short
12-entry `ignore` list, *not* the CLI's 78-entry built-in `DEFAULT_IGNORE` —
widen it for your repo), and regenerates the index.
`bin/gotdocs install` is the minimal single-hook path.

`scripts/install-gotdocs.sh` is the bootstrap wrapper engineers actually run —
it works before the CLI is on any path. See
[onboarding/local-setup.md](../onboarding/local-setup.md).

---

## `gotdocs new`

```text
bin/gotdocs new <type> <id> [--title T] [--covers GLOB]... [--symptom TEXT]...
```

Scaffolds a doc from `.gotdocs/templates/<type>.md` into the root matching the
type, with `id`, `title`, `covers` and `updated` filled in. `verified_at` is left
at the template's `0000000` placeholder and `status` at the template's value: an
empty scaffold is not a doc anyone has read. Run `bin/gotdocs verify <id>` once
it is. If the template file is missing, a type-aware built-in fallback scaffold
is used instead, so `new` never produces a file that fails `lint`.

| Type | Written to | Status enum |
| --- | --- | --- |
| `doc` | `docs/<id>.md` | `current` \| `draft` \| `deprecated` |
| `runbook` | `runbooks/<id>.md` | same |
| `onboarding` | `onboarding/<id>.md` | same |
| `dependency` | `dependencies/<id>.md` | same |
| `decision` | `decisions/NNNN-<slug>.md` | `proposed` \| `accepted` \| `rejected` \| `superseded` |

`--covers` is repeatable. Quote the glob so the shell does not expand it.

```sh
bin/gotdocs new runbook queue-backlog-growing \
  --title "Runbook: Queue Backlog Growing" \
  --covers 'src/workers/**' --covers 'src/queue.py'
```

### `new decision` is different

For `decision`, the positional argument is the **title**, not the id. The id is
allocated as `NNNN-slug`, where `NNNN` is one more than the highest four-digit
prefix already on disk in the decisions root — read from filenames, never from
frontmatter, and never reused — and `slug` is the title kebab-cased.

`--symptom` is repeatable and applies to `new decision` only (a usage error
anywhere else). Each one is an observable behaviour this record explains, and it
is what [`gotdocs why`](#gotdocs-why) searches, so write them the way somebody
would describe the problem, not the way you would name the design.

```sh
bin/gotdocs new decision "Retry budget is per request" \
  --covers 'src/http/**' \
  --symptom "a POST is retried exactly twice and then fails fast" \
  --symptom "the retry count does not grow with the number of hops"
```

Symptom text is quoted automatically when leaving it bare would misparse (it
contains `: `, ends with a colon, starts with a YAML indicator character). Glob
patterns never take that path, so `covers` entries stay unquoted and diffs stay
small.

Exit `2` if the id is not kebab-case, is longer than 64 characters, is already
used, or the target file exists. For `new decision`, exit `2` if the title
contains no letter or digit to slugify.

---

## `gotdocs why`

```text
bin/gotdocs why [TEXT...] [--path PATH] [--limit N] [--all] [--full] [--json]
```

Answers *"is this behaviour an intentional decision, or a bug?"* by scoring your
free-text description against the `symptoms`, `title`, `summary` and `tags` of
every decision record. Plain token overlap with stemming — no index, no
embeddings, no network.

The ranking is then cut by two relevance floors, because a ranked list of every
record that shares *a* word with your query is a list of wrong answers with the
right one buried in it. A record must overlap at least about a third of your
query's distinct terms, and must score at least 45% of the leader. Both floors
apply to the leader too: if the best record in the repository shares one word
out of six with your symptom, `why` says nothing matched rather than pointing at
the least-irrelevant record. Widen a genuine miss by adding your phrasing to
that record's `symptoms` — not by loosening the scorer.

| Flag | Effect |
| --- | --- |
| `TEXT...` | What you actually observed, in your own words. Joined with spaces. |
| `--path PATH` | Restrict to decisions whose `covers` match this file. Usable with or without a query; with neither, it lists every decision governing that path. |
| `--limit N` | How many records to print. `0` prints all. Default `3`. |
| `--all` | Search `rejected` and `superseded` records too. They are excluded by default because they are **not in force**, and citing one as the reason for current behaviour is exactly the mistake this prevents. |
| `--full` | Print the two quoted sections whole, instead of their leading claim. |

The `expected:` and `bug if:` lines are the **first claim** of each section, not
its first 80 characters: the list marker is stripped and the text ends at a
sentence boundary, so the line reads as a statement. `--full` prints the
sections whole.

```text
$ bin/gotdocs why "my documentation edit needs a code review before it lands"
1 decision matches "my documentation edit needs a code review before it lands" (of 8 searched):

[1] 0001-docs-live-in-the-repo  (accepted)  decisions/0001-docs-live-in-the-repo.md
    Documentation lives in the repository, in git, next to the code
    symptom:  my documentation edit needs a code review before it lands
    expected: `bin/gotdocs status` and `bin/gotdocs lint` work with the network disabled,...
    bug if:   `bin/gotdocs` makes any network call at all.
```

Nothing matching is a first-class answer, not an error:

```text
$ bin/gotdocs why "kubernetes pod eviction"
no decision matches "kubernetes pod eviction" (of 8 searched).

Nothing was written down that explains this. Treat it as unintended
until proven otherwise, and consider recording the answer you find.
$ echo $?
0
```

**`why` never exits non-zero for a miss.** A repository with no decisions, or a
query nothing matches, is the common case; a hard failure there would put an
error in the middle of an agent's diagnosis loop for the *absence* of
information. The only non-zero exit is `2`, for calling it with neither a query
nor `--path`.

`--json` adds `query`, `path`, `searched`, `match_count` and a `matches` array
carrying each record's `score`, `matched_symptom` and `matched_terms` alongside
the full `expected` and `not_this` section text. The flow this fits into is in
[decisions.md](decisions.md).

---

## `gotdocs export`

```text
bin/gotdocs export [--target NAME] [--out DIR] [--url-prefix P] [--source-url URL]
                   [--layout NAME] [--include-drafts] [--clean] [--dry-run]
                   [--list-targets] [--json]
```

Renders the documents into a static site generator's own conventions: keys are
mapped, gotdocs-only keys are stripped into `_gotdocs.json`, relative links are
rewritten for the target's URL scheme, referenced images are copied, and drafts
are skipped. Output is byte-deterministic.

| Flag | Effect |
| --- | --- |
| `--target NAME` | One of `docusaurus`, `mkdocs`, `starlight`, `jekyll`, `hugo`, `github`. Default `publish.target`. An unknown name is exit `2` and lists the valid ones. |
| `--out DIR` | Output directory. Default `publish.out_dir`. Relative paths resolve against the repo root. Exit `2` if neither is set. |
| `--url-prefix P` | Site path the export is served under. Default `publish.url_prefix`. |
| `--source-url URL` | Base URL for links that point at code rather than another document. Default `publish.source_url`. |
| `--layout NAME` | Jekyll `layout:` value. Default `publish.layout`, then `page`. |
| `--include-drafts` | Export `status: draft` documents too. ORs with `publish.include_drafts`. |
| `--clean` | Delete files in the output tree this export did not write. |
| `--dry-run` | Render and report, write nothing. |
| `--list-targets` | Print every target with its key mapping and exit. |

```text
$ bin/gotdocs export --target hugo --out build/gotdocs-site
gotdocs: exported 25 documents for hugo -> build/gotdocs-site
         26 file(s) changed on disk
```

```text
$ bin/gotdocs export --target mkdocs --out build/site --dry-run
gotdocs: would export 14 documents for mkdocs -> build/site
```

Exit `0` always, including when nothing changed on disk. Each target's full key
mapping is in
[publishing.md](publishing.md#1-frontmatter-keys-are-mapped-not-passed-through),
and `--list-targets` prints the live version.

---

## `gotdocs debt`

```text
bin/gotdocs debt <record | list | resolve | render | stats> [flags]
```

The ledger of findings that were knowingly deferred. `bin/gotdocs debt` with no
subcommand prints help and exits `0`. Concepts, file format and the CI wiring are
in [doc-debt.md](doc-debt.md); this is the flag reference.

### `debt record`

```text
bin/gotdocs debt record [--staged | --base REF | --paths PATH...]
                        [--source manual|hook|ci] [--note TEXT] [--kinds KIND[,KIND]]
                        [--resolve-absent] [--date YYYY-MM-DD] [--sha SHA]
                        [--dry-run] [--json]
```

Runs a `check` purely to harvest findings (enforcement mode is irrelevant and is
forced to `warn`), then merges them into the ledger. Source selection is the same
three mutually exclusive flags as `check`, defaulting to `--staged`.

`--source` is stored as the entry's `note` unless `--note` overrides it.
`--kinds` narrows to specific finding kinds for this run; without it,
`debt.record_kinds` from the config applies. `--date` and `--sha` override the
stamp, which otherwise comes from HEAD's **commit** date
(`git log -1 --format=%cd --date=short`) and short sha — never the wall clock, so
the ledger regenerates to identical bytes on any machine on any day. A `--date`
that is not `YYYY-MM-DD` is exit `2`.

```text
$ bin/gotdocs debt record --paths tools/gotdocs/check.py --dry-run
gotdocs: debt preview  (+6 new, ~0 seen again, ^0 reopened, -0 resolved)
         ledger .gotdocs/debt.jsonl @ 2026-08-15 2b4ca2e
         6 open, 0 resolved, 6 occurrence(s) recorded
```

When `debt.enabled` is `false` this exits `0` having done nothing, reporting
`doc-debt recording is disabled (debt.enabled is false)`.

### `debt list`

```text
bin/gotdocs debt list [--status open|resolved] [--all] [--kind K] [--doc ID]
                      [--path PATH] [--limit N] [--json]
```

Defaults to open entries. `--all` shows open and resolved. `--limit 0` (or
omitting it) means no limit.

```text
$ bin/gotdocs debt list --limit 3
gotdocs: 3 of 8 entries  (8 open, 0 resolved, 8 occurrence(s) recorded)

  b3defc51238e  open  stale  x1  first 2026-08-15  last 2026-08-15
    decisions/0002-python-stdlib-only-cli.md  [0002-python-stdlib-only-cli]
    tools/gotdocs/globs.py changed and is covered by tools/gotdocs/globs.py
    -> update decisions/0002-python-stdlib-only-cli.md, or run: bin/gotdocs verify 0002-python-stdlib-only-cli
  ...
```

### `debt resolve`

```text
bin/gotdocs debt resolve <REF>... [--note TEXT] [--date YYYY-MM-DD] [--sha SHA]
bin/gotdocs debt resolve --auto [--staged | --base REF | --paths PATH...]
```

A `REF` is a full entry id, an unambiguous id prefix, a doc id, or a path.

```text
$ bin/gotdocs debt resolve doc-format --note "covers narrowed; no longer relevant"
gotdocs: resolved 1 debt entry: 8e37464a483a
         7 open, 1 resolved, 7 occurrence(s) recorded
```

`--auto` closes every open entry the current check no longer reports, which is
the same rule as [`debt record --resolve-absent`](#debt-record) reached from the
verb you use when you want something closed. It records nothing new: an entry
closes only because the check that opened it stopped reporting it. The change
set defaults to `--staged`. `debt.record_kinds` does **not** apply here: it
limits which kinds may be *opened*, and filtering the evidence would close
entries the check is still reporting.

```text
$ bin/gotdocs debt resolve --auto --staged
gotdocs: resolved 1 debt entry: 121d03206efc
         0 open, 1 resolved, 0 occurrence(s) recorded
```

Explicit refs and `--auto` compose, so one call can close an entry by hand and
the rest by evidence. Called with neither, it is a usage error (exit `2`) rather
than a silent no-op:

```text
$ bin/gotdocs debt resolve
gotdocs: debt resolve needs a REF, or --auto to close what the current check no longer reports
$ echo $?
2
```

An **unmatched** reference is exit `2` — the caller named debt that does not
exist and would otherwise believe it was closed:

```text
$ bin/gotdocs debt resolve nope-not-here
gotdocs: no debt entry matches nope-not-here
         run: bin/gotdocs debt list --all
$ echo $?
2
```

An **ambiguous** reference is also exit `2`, naming every candidate, rather than
closing the wrong one.

### `debt render`

```text
bin/gotdocs debt render [--out PATH] [--limit N] [--stdout] [--json]
```

Writes the human report, default `debt.report` (`.gotdocs/DEBT.md`). `--limit`
caps lines per finding kind, defaulting to `debt.max_report_lines` (20).
`--stdout` prints it instead of writing. Exit `0`.

### `debt stats`

```text
bin/gotdocs debt stats [--json]
```

```text
$ bin/gotdocs debt stats
gotdocs: 7 open, 1 resolved, 7 occurrence(s) recorded  (.gotdocs/debt.jsonl)

open by kind
  stale                7

worst offenders
  0002-python-stdlib-only-cli              1
  0003-covers-globs-as-the-staleness-signal 1
  0004-ci-records-debt-instead-of-blocking 1
  0006-verify-stamp-as-the-escape-hatch    1
  cli-reference                            1
```

A ledger line that cannot be parsed is reported on stderr and skipped, and shows
up as `ledger_errors` in `--json`. One bad line costs one entry, never the whole
ledger — that is the reason the format is JSONL.

---

## The `--json` contract

**`--json` is the agent interface. Its shape is stable.** Fields may be added in a
minor version; existing field names, types and `kind` values do not change without
a version bump. Consumers should ignore unknown fields.

`check --json`:

```json
{
  "ok": false,
  "mode": "warn",
  "findings": [
    {
      "kind": "stale",
      "doc_id": "gotdocs-architecture",
      "path": "docs/architecture.md",
      "message": "tools/gotdocs/check.py changed and is covered by tools/gotdocs/**",
      "remediation": "update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture"
    }
  ],
  "summary": {
    "changed_paths": 3,
    "code_paths": 2,
    "doc_paths": 1,
    "docs_indexed": 13,
    "impacted": 2,
    "stale": 1,
    "uncovered": 0,
    "findings": 1,
    "head": "3d8b6cd"
  }
}
```

`summary.head` is `null` when the change set came from `--paths`, since no git
revision was consulted.

| Field | Type | Meaning |
| --- | --- | --- |
| `ok` | bool | `true` when `findings` is empty. Independent of `mode` and of the exit code — a `warn`-mode run with findings is `"ok": false` and exit `0`. |
| `mode` | string | The effective mode: `off`, `warn` or `error`. |
| `findings` | array | Possibly empty. Order is stable: grouped by `kind`, then by `path`. |
| `findings[].kind` | string | One of the closed set below. |
| `findings[].doc_id` | string or null | Null for findings that are not about a specific doc, such as `uncovered`. |
| `findings[].path` | string | Repo-relative path the finding is anchored to — the doc for doc findings, the code file for `uncovered`. |
| `findings[].message` | string | Human explanation. Not machine-parseable; do not regex it. |
| `findings[].remediation` | string | The exact next action, usually a copy-pasteable command. |
| `summary` | object | Counts for the run, plus `head`, the short sha the change set was computed against. |
| `skipped` | bool | Present **only** when the run was skipped. `skip_reason` then says why: `GOTDOCS_SKIP is set`, `GOTDOCS_SKIP contains <token>`, or `commit message contains <token>`. `findings` is empty and `ok` is `true`, so a consumer that reads only `ok` cannot tell a skip from a clean run — check for this key when that distinction matters. |

Finding kinds:

| `kind` | Raised when |
| --- | --- |
| `stale` | An impacted doc was neither edited nor verified at the head sha. |
| `uncovered` | A changed code path matches no doc's `covers`. Only when `require_coverage` is `true`. |
| `lint` | Frontmatter is invalid. `message` carries `file:line`. |
| `duplicate_id` | Two docs declare the same `id`. |
| `deprecated_edit` | A `status: deprecated` doc is in the change set. |
| `index_out_of_date` | `.gotdocs/index.json` or `.gotdocs/INDEX.md` does not match what regenerating would produce. |

Errors use the same envelope so a consumer never has to parse two shapes:

```json
{ "ok": false, "mode": "error", "findings": [], "summary": {},
  "error": { "code": 3, "message": "not a git repository" } }
```

`lint --json` and `impacted --json` reuse `ok` and `findings` / `paths` with the
same field semantics.

That includes **argument** errors. An unknown `--target`, an unrecognised flag,
a bad `--mode` and a missing required argument all emit the same envelope with
`error.code` 2 rather than argparse's usage text on stderr and nothing on
stdout, so a consumer that parses stdout never gets a decode error:

```json
{ "ok": false, "mode": "error", "findings": [], "summary": {},
  "error": { "code": 2,
             "message": "argument --target: invalid choice: 'nope' (choose from 'docusaurus', ...)" } }
```

`--help` and `--version` still print human text and exit `0`.

### Consuming it

```sh
# exit non-zero on any finding regardless of configured mode
bin/gotdocs check --base origin/main --mode error --json

# just the stale doc ids, no jq required
bin/gotdocs check --staged --json | python3 -c \
  'import json,sys; print(*[f["doc_id"] for f in json.load(sys.stdin)["findings"] if f["kind"]=="stale"])'
```

Read `mode` and `ok` rather than inferring intent from the exit code: exit `0`
means "do not block", not "nothing to do". Read them **together**: `mode: "off"`
short-circuits before any finding is computed, so it reports `ok: true` with an
empty `findings` list because nothing was examined, not because the tree is
clean.

## Environment variables

| Variable | Effect |
| --- | --- |
| `GOTDOCS_SKIP` | `1`, `true`, `yes` or `on` (case-insensitive) skips all checking for this invocation. Any other value is still checked for the configured `skip_token` as a substring. Honored by both hooks and by `check`, for every source — including `--base`. |
| `NO_COLOR` | Any value, including empty, disables ANSI color. |
| `GOTDOCS_PYTHON` | Path to the interpreter `bin/gotdocs` should exec. Set it when `python3` is not on `PATH` or is too old. If it is not executable, the shim warns and exits `0`. |
| `GOTDOCS_CWD` | The directory the CLI resolves the repository and relative paths from. `bin/gotdocs` sets it to `$PWD` before `exec`, because it does not `cd`. Set it yourself only when invoking `python3 -m tools.gotdocs` directly from somewhere other than the tree you mean. |

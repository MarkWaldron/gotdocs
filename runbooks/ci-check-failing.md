---
id: runbook-ci-check-failing
title: "Runbook: The Gotdocs CI Job Is Failing"
type: runbook
summary: A pull request is red on the gotdocs job — identify which of the four failure modes it is (stale doc, stale committed index, lint error, bad base ref) and fix it.
covers:
  - .github/workflows/**
  - tools/gotdocs/gitutil.py
owners: ["@mark"]
tags: [runbook, ci, github-actions, pull-request]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Runbook: The Gotdocs CI Job Is Failing

The `gotdocs` job on a pull request is red. There are five things it can be.
Identify which from the job log, then jump to that section.

## Symptom

The `check` job (display name `docs freshness`) in
`.github/workflows/gotdocs.yml` fails. It runs three gates, each recording its
exit status rather than failing immediately, so one run reports every problem:

```sh
bin/gotdocs lint
bin/gotdocs check --base "origin/$GITHUB_BASE_REF" --mode error
bin/gotdocs index && git status --porcelain -- .gotdocs/index.json .gotdocs/INDEX.md
```

**Check `enforce.ci` first.** On the shipped default (`warn`), gates 1 and 2 are
advisory — they emit `::warning::` and the job stays green. If the job is red on
a `warn` config, it is **gate 3**, the committed index, which blocks
unconditionally: jump straight to [2](#2--stale-committed-index). The job summary
states the mode in its header: ``Base: `origin/main` · enforcement: `enforce.ci =
warn` ``.

## Identify which failure it is

**Read the job summary first.** The workflow publishes a `gotdocs` section to
`$GITHUB_STEP_SUMMARY` with a pass/fail row per gate and a table of every finding
(`kind | doc | path | message | remediation`), plus the index diff in a collapsed
block. It usually tells you everything without opening a log.

If you are in the raw log, match the last 30 lines of the failing step:

| Log contains | Failure mode | Section |
| --- | --- | --- |
| `stale (N)` with doc paths and `-> update ... or run: bin/gotdocs verify` | Stale docs | [1](#1--stale-docs) |
| `index_out_of_date` **or** a `git diff` showing changes under `.gotdocs/` | Stale committed index | [2](#2--stale-committed-index) |
| `lint`, `file:line`, `duplicate id`, `missing required field`, `unsupported construct` | Lint error | [3](#3--lint-error) |
| `fatal: no merge base`, `unknown revision`, `ambiguous argument`, `origin/...: not found`, or `exit 3` | Bad base ref | [4](#4--bad-base-ref-fork-or-shallow-clone) |
| `gotdocs: internal error` | CLI bug | [5](#5--internal-error) |

Reproduce any of them locally — this is faster than pushing to CI repeatedly:

```sh
git fetch origin main
bin/gotdocs lint
bin/gotdocs check --base origin/main --mode error
bin/gotdocs index && git status --short .gotdocs/
```

Substitute the PR's actual base branch for `main`.

---

## 1 — Stale docs

The most common failure, and the one the job exists for. Code you changed is
covered by a doc that was neither edited nor verified on this branch.

```text
stale (2)
  docs/architecture.md  [gotdocs-architecture]
    tools/gotdocs/check.py changed and is covered by tools/gotdocs/**
    -> update docs/architecture.md, or run: bin/gotdocs verify gotdocs-architecture
```

Note the scope difference from the pre-commit hook: CI checks
`origin/<base>...HEAD`, the *whole branch*. A commit you waved through locally
with `[gotdocs skip]` still shows up here. That is intentional — the skip token is
for the moment, not for the pull request.

Fix:

```sh
git fetch origin main
bin/gotdocs check --base origin/main            # get the full list
# then, per doc: edit it, or read it and verify it
$EDITOR docs/architecture.md
bin/gotdocs verify cli-reference                # only for docs you read
bin/gotdocs index                               # if any covers/id/status changed
git add docs .gotdocs
git commit -m "docs: update for check.py change"
git push
```

Decide edit-vs-verify per doc using
[stale-doc-triage.md](stale-doc-triage.md). Do not run `verify --all-impacted` to
turn the build green; a reviewer can see that you did.

Or ask Claude: `/gotdocs-update`, which reads `check --json` and works through
them.

If this PR genuinely cannot carry the doc changes — a revert, a mass mechanical
change, an incident fix — apply the **`gotdocs-skip` label** to the pull request.
The job is skipped entirely while the label is present, and re-runs when it is
removed (the workflow listens for `labeled` and `unlabeled`). The label is visible
to every reviewer, which is the point; it is not a quiet bypass.

**Do not** fix this by weakening the workflow's `--mode error` in the same PR. If
that is genuinely the right call, it is a separate PR with its own review — see
[docs/enforcement.md](../docs/enforcement.md#choosing-modes-per-layer).

---

## 2 — Stale committed index

`.gotdocs/index.json` and `.gotdocs/INDEX.md` are generated and committed. CI runs
`bin/gotdocs index` and then requires `git status --porcelain` on those two files
to be empty. Failure means you added, deleted, renamed or re-frontmattered a doc
without regenerating.

The log shows `::error::.gotdocs/index.json or .gotdocs/INDEX.md is out of date`
followed by the diff, and the job summary has an "Index out of date" section with
the same diff collapsed. CI restores the files afterwards; it never commits them.

Fix:

```sh
bin/gotdocs index
git add .gotdocs/index.json .gotdocs/INDEX.md
git commit -m "chore: regenerate gotdocs index"
git push
```

If `git status` is still dirty under `.gotdocs/` immediately after running
`index`, something is non-deterministic — regeneration is supposed to be
byte-identical. Check:

- Are you on the same short-sha length as CI? `generated_at_sha` is the only
  volatile field. `git config core.abbrev` differing from CI's default produces a
  one-line diff. Set `core.abbrev` consistently, or regenerate on CI's sha.
- Line endings. `git config core.autocrlf` on a Windows checkout will rewrite the
  generated files. Set `* text=auto eol=lf` in `.gitattributes` for `.gotdocs/`.
- A merge that resolved `.gotdocs/index.json` by hand. Never hand-merge it —
  take either side and re-run `bin/gotdocs index`.

---

## 3 — Lint error

Frontmatter somewhere under a root does not parse or does not validate.

Two commands see this and they do not behave the same way:

- `bin/gotdocs lint` exits **2**. That is the only command that treats a lint
  error as fatal.
- `bin/gotdocs check` reports it as a finding of kind `lint` and exits by mode
  like any other finding — 0 under `warn`, 1 under `error`. It never exits 2 for
  a lint error.

In CI the `Enforce` step reads `enforce.ci`. Under the shipped default of `warn`
a non-zero `lint` becomes `::warning::bin/gotdocs lint failed ...; advisory
because enforce.ci is warn` and the job stays green. Set `enforce.ci` to `error`
to make it red. An unindexable doc protects nothing, so fix it either way.

```sh
bin/gotdocs lint
```

| Message | Cause | Fix |
| --- | --- | --- |
| `unsupported construct` at `file:line` | Nested map, `\|`/`>` block scalar, anchor, or tab indentation | Flatten to the supported subset: [docs/doc-format.md](../docs/doc-format.md#supported-yaml-subset) |
| `duplicate id` | Two docs with the same `id`, usually from copying a file | Rename one, update anything citing it, `bin/gotdocs index` |
| `missing required field` | Hand-written doc without `covers`/`status`/`summary` | Add it; compare against `.gotdocs/templates/` |
| `summary too long` | Over `max_summary_chars` (200) | Shorten; the long version belongs in the body |
| `invalid id` | Not `[a-z0-9][a-z0-9-]*`, or over 64 chars | Rename to kebab-case |
| `unterminated frontmatter` | Missing closing `---`, or a BOM/blank line before the opening `---` | The opening `---` must be the first bytes of the file |
| `invalid covers pattern` | Leading `/` or `./`, brace expansion, backslashes | Repo-relative, `/`-separated, no braces |

A lint error is often in a file the PR did not touch — someone merged it and the
job only now runs on your branch. Fix it anyway; it is one line.

---

## 4 — Bad base ref (fork or shallow clone)

`check --base REF` runs `git diff --name-status REF...HEAD`. Three dots need a
merge base, which needs history. Two things commonly remove it.

### Shallow clone

`actions/checkout` defaults to `fetch-depth: 1`. There is one commit, so there is
no merge base and git reports `fatal: no merge base` or an unknown revision.

The shipped workflow already sets `fetch-depth: 0` and re-fetches the base ref
defensively in its `Prepare` step, so this only happens if the checkout step was
edited. Restore it:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- run: |
    git fetch --no-tags --quiet origin \
      "+refs/heads/${BASE_REF}:refs/remotes/origin/${BASE_REF}" || true
```

If `Prepare` printed `::error::could not resolve origin/<base>`, the base branch
does not exist under that name on `origin` — check for a renamed default branch.

Confirm in the job log before the failing step:

```sh
git rev-parse --is-shallow-repository     # must print false
git merge-base HEAD "origin/$GITHUB_BASE_REF"
```

### Fork pull request

On a `pull_request` event from a fork, the checkout is the merge commit, the
`GITHUB_TOKEN` is read-only, and secrets are unavailable. `GITHUB_BASE_REF` is
still set, so the fix is to make sure the base ref exists locally as a remote-
tracking ref:

```yaml
env:
  BASE: ${{ github.base_ref || 'main' }}
steps:
  - uses: actions/checkout@v4
    with: { fetch-depth: 0 }
  - run: git fetch --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE"
  - run: bin/gotdocs check --base "origin/$BASE" --mode error --strict
```

Two things that must **not** be in the workflow, because they cannot work on a
fork PR:

- any step that pushes a regenerated `.gotdocs/index.json` back to the branch —
  the token is read-only. CI asserts the index is current; it never fixes it.
- any step depending on a secret. Gotdocs needs none; it makes no network calls.

On `pull_request` events `GITHUB_BASE_REF` is the base branch name; on `push`
events it is empty. The shipped workflow triggers on `pull_request` only and so
uses a bare `${{ github.base_ref }}`. The `|| 'main'` in the snippet above is for
a workflow you extend to `push` events — without it, `--base origin/` is passed
and the job fails with an unknown ref.

### Detached HEAD

CI checks out a detached HEAD. That is fine — `check` uses `HEAD` and never needs
a branch name. If a log line suggests otherwise (`--base @{u}`, `git symbolic-ref`),
the workflow was edited to use the pre-push form. Use `--base origin/$BASE`.

---

## 5 — Internal error

`gotdocs: internal error: ...` means the CLI raised an unexpected exception. By
default it warns and exits 0, so this alone will not fail the job — but it means
the gate checked nothing, and the exit-2 path will fail it.

Surface the traceback:

```sh
bin/gotdocs check --base origin/main --strict    # full traceback
```

Reproduce on the Python the workflow uses (`actions/setup-python` pins `3.11`;
minimum supported is 3.9 — see
[dependencies/python3.md](../dependencies/python3.md)). If it is a genuine bug,
unblock the PR with the `gotdocs-skip` label, file the bug, and fix it.

---

## 6 — The ledger job never ran, or could not push

This one does not turn the pull request red. It fails silently on `main` after
the merge, so the symptom is `.gotdocs/debt.jsonl` never changing no matter how
much doc debt accumulates.

**First, run the preflight — it identifies all three causes:**

```sh
bin/gotdocs ci doctor
```

### The job never ran

`ci doctor` reports `FAIL default-branch`. The workflow triggers on
`push: branches: [main]` and your default branch is something else, so nothing
ever invoked it. Check the Actions tab: there will be no `record` runs at all,
which is the tell that distinguishes this from a push failure.

```sh
bin/gotdocs ci init --force    # rewrites the branch list to the real default
```

### The job ran and failed at `git push`

Open the run and look at the last step. Two different causes, two different
fixes:

| Log line | Cause | Fix |
| --- | --- | --- |
| `remote: Permission to ... denied to github-actions[bot]` or `403` | `GITHUB_TOKEN` is read-only for the repository. The job's `permissions: contents: write` is capped by the repository setting and cannot raise itself. | Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save. Or `bin/gotdocs ci doctor --apply` with `gh` authenticated. |
| `protected branch hook declined` or `required status check` | Branch protection on the default branch rejects a direct push. | Pick one: set `enforce.ci` to `error` and delete the record job (CI blocks instead of recording); allow `github-actions[bot]` to bypass the rule; or change the job to open a pull request instead of pushing. |

`ci doctor` cannot fix branch protection for you — which of those three is right
depends on why the branch is protected in the first place.

### The job ran, pushed nothing, and that was correct

`gotdocs: ledger unchanged` in the log means there was no new debt. Not a
failure. `write_ledger` returns "unchanged" precisely so the job does not commit
an identical file on every merge.

---

## Verify the fix

```sh
git fetch origin main
bin/gotdocs lint                                      # exit 0
bin/gotdocs check --base origin/main --mode error     # exit 0
bin/gotdocs index && git status --short .gotdocs/     # prints nothing
```

All three clean locally means the job will be green, since CI runs exactly these
three commands.

## Related

- [runbooks/stale-doc-triage.md](stale-doc-triage.md) — deciding edit vs verify per doc
- [runbooks/pre-commit-hook-blocking.md](pre-commit-hook-blocking.md) — the same checks, locally
- [docs/enforcement.md](../docs/enforcement.md) — why CI is `error` and `--strict`
- [dependencies/git.md](../dependencies/git.md) — merge bases, shallow clones, detached HEAD

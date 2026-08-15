---
id: dependency-git
title: "Dependency: git"
type: dependency
summary: gotdocs shells out to git for every change set and sha it uses — the exact commands, the environments where they break, and what gotdocs does instead.
covers:
  - tools/gotdocs/gitutil.py
  - .gotdocs/hooks/**
owners: ["@mark"]
tags: [dependency, git, plumbing]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---

# Dependency: git

Git is gotdocs' one hard external dependency. There is no libgit binding (that
would be a third-party package), so `tools/gotdocs/gitutil.py` shells out to the
`git` executable and parses its output. Inside the Python package that module is
the only one that runs a subprocess — but it is not the only *part of gotdocs*
that runs git. The two shell hooks, `scripts/install-gotdocs.sh` and
`.github/workflows/gotdocs.yml` each call git directly, before the CLI is
reached at all. Both lists are below.

- **Required version:** 2.20 or newer. Older versions work for the diff commands
  but `--show-toplevel`, `--is-shallow-repository` and `REF...HEAD` behavior are
  only reliably consistent from 2.20 on.
- **Optional?** No, for `check --staged`, `check --base` and `verify`. Yes, for
  `check --paths`, `lint`, `index`, `new`, `why`, `export` and every `debt`
  subcommand, which are pure filesystem operations — they work in a tarball with
  no `.git` at all, except that `index` wants a sha for `generated_at_sha` and
  records JSON `null` when git is absent.

## Commands the CLI runs

Every one of these is issued by `tools/gotdocs/gitutil.py`, verbatim, with no
shell in between (`subprocess.run(["git", ...])`).

| Command | Used by | Purpose |
| --- | --- | --- |
| `git rev-parse --show-toplevel` | everything | Find the repo root. All paths are relative to it. |
| `git rev-parse --absolute-git-dir` | `status`, `install`, skip-token read | Locate `COMMIT_EDITMSG`, `MERGE_HEAD`, hooks dir. Not always `.git/` — worktrees and submodules use a file pointer. `--absolute-git-dir`, not `--git-dir`: the CLI does not `cd`, so a relative answer would be resolved against the wrong directory. |
| `git rev-parse --verify --quiet HEAD` | everything that needs a sha | Does this repository have any commits at all? Drives the empty-repo branches below. |
| `git rev-parse --short HEAD` | `check`, `verify`, `index`, `debt` | The head sha a doc's `verified_at` is compared against and stamped with. |
| `git rev-parse --verify --quiet REF^{commit}` | `check --base` | Does the base ref exist at all? |
| `git log -1 --format=%B` | `check --staged` | HEAD's message, used only to recognise a leftover `COMMIT_EDITMSG`. |
| `git log -1 --format=%cd --date=short` | `debt record`, `debt resolve` | HEAD's **commit** date, stamped on ledger entries. The ledger never reads a wall clock, so it regenerates to identical bytes on any machine on any day. |
| `git diff --cached --name-status -z --no-color HEAD` | `check --staged` | The staged change set. In a repo with no commits the `HEAD` argument is replaced by the empty-tree sha, so the first commit still produces a change set. |
| `git diff --name-status -z --no-color REF...HEAD` | `check --base` | Branch change set against the merge base of `REF` and `HEAD`. |
| `git rev-parse --is-shallow-repository` | `check --base` diagnostics | Detect shallow clones so the error message can say so. |
| `git merge-base HEAD REF` | `check --base` diagnostics | Confirm a merge base exists and report a usable error when it does not. |

`gitutil.py` also defines `git --version` (`git_available`),
`git diff --name-status -z --no-color` (unstaged changes, `working_tree_changes`)
and `git ls-files -z [--others --exclude-standard]` (`working_tree_changes`,
`tracked_files`). **No CLI command reaches any of them.** They are library
surface with no caller today; do not cite them as things gotdocs does, and if
you are auditing subprocess use, they are the three to ignore.

gotdocs never calls `git status`: `bin/gotdocs status` reports gotdocs' own state
(config, roots, doc counts, index freshness, hook wiring), not the tree's
dirtiness.

All of the above are read-only plumbing. The CLI never runs `git add`,
`git commit`, `git push`, `git checkout` or anything else that mutates
repository state. If it edits a file (`verify`, `index`, `new`, `debt record`),
it edits the working tree and leaves staging to you.

## Commands the shell parts run

These run before, or instead of, the CLI, so they are not in `gitutil.py` and do
not obey its error handling. They fail open: every one is wrapped so that a
non-zero exit ends in `exit 0` rather than a blocked commit.

| Command | Where | Purpose |
| --- | --- | --- |
| `git rev-parse --show-toplevel` | both hooks | `cd` to the repo root before doing anything. |
| `git rev-parse --git-dir` | both hooks | Find `MERGE_HEAD` / `COMMIT_EDITMSG`. Relative is fine here — the hook has already `cd`-ed to the root. |
| `git log -1 --format=%B HEAD` | pre-commit | Compare against `COMMIT_EDITMSG` to reject a leftover message. |
| `git diff --cached --quiet --` | pre-commit | Nothing staged means nothing to check; leave immediately. |
| `git rev-parse HEAD` | pre-push | Identify which stdin line describes the ref actually being pushed. |
| `git rev-parse --verify --quiet <sha>^{commit}` | pre-push | Is the remote sha git handed us present locally? |
| `git symbolic-ref --quiet refs/remotes/origin/HEAD` | pre-push | Base fallback. |
| `git rev-list --max-parents=0 HEAD` | pre-push | Last-resort base: the root commit. |
| `git log --format=%B BASE..HEAD` | pre-push | Skip-token scan over **every** commit being pushed. |
| `git fetch --no-tags origin +refs/heads/$BASE:...` | CI `check` job | Guarantee the base ref exists before diffing. |
| `git status --porcelain -- .gotdocs/index.json .gotdocs/INDEX.md` | CI `check` job | The always-blocking committed-index gate. |
| `git add` / `git commit` / `git push` / `git rebase` | CI `record` job only | Commits the regenerated doc-debt ledger back to `main`. This is the one place in the whole system that writes to a repository, it runs only on `push` to the default branch of a non-fork, and its commit message carries `[gotdocs skip] [skip ci]` so it cannot trigger itself. See [docs/doc-debt.md](../docs/doc-debt.md#the-two-ci-jobs). |

## Why `REF...HEAD` and not `REF..HEAD`

Three dots diffs `HEAD` against the **merge base** of `REF` and `HEAD` — the point
where your branch diverged. Two dots diffs against `REF` itself, which means every
commit that landed on `main` since you branched shows up as part of "your change".
On an active repo that is dozens of unrelated files and a corresponding flood of
false stale findings.

Cost: three dots requires a merge base to exist, which is the source of most of
the failure modes below.

## Name-status parsing

`--name-status` output is one record per path:

```text
M	tools/gotdocs/check.py
A	docs/new-thing.md
D	docs/removed.md
R096	docs/old-name.md	docs/new-name.md
```

- `M`, `A`, `D`, `T` — one path.
- `R<score>`, `C<score>` — two paths, tab-separated. Gotdocs counts **both** the
  old and the new path as changed: a rename can invalidate a doc that names the
  old path literally, and the new path is what future readers will look for.
- Paths containing unusual bytes are quoted by git with C-style escapes. Gotdocs
  unquotes them. `core.quotepath` differences therefore do not change behavior.
- Records are NUL-separated internally (`-z`) so tabs and newlines in filenames
  cannot desynchronize the parse.

## Failure modes

### Shallow clone

**Symptom:** `fatal: no merge base`, or `unknown revision or path not in the
working tree`, from `check --base`.

**Cause:** the clone has truncated history (`git clone --depth=1`, or
`actions/checkout` with its default `fetch-depth: 1`). There is no common ancestor
to diff against.

**Detect:** `git rev-parse --is-shallow-repository` prints `true`.

**Fix:** deepen the clone. In CI, `fetch-depth: 0`. Locally,
`git fetch --unshallow`, or fetch just the base branch:

```sh
git fetch --no-tags origin "+refs/heads/main:refs/remotes/origin/main"
```

**What gotdocs does:** reports the condition explicitly rather than emitting an
empty change set. An empty change set would look like "no findings" and silently
turn the check off, which is the worst possible failure.

### No upstream

**Symptom:** `fatal: no upstream configured for branch 'x'` when a pre-push hook
or a script uses `@{u}`.

**Cause:** the branch was created locally and never pushed, so
`branch.<name>.merge` is unset.

**Fix:** pass an explicit base, and make scripts fall back:

```sh
BASE=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo origin/main)
bin/gotdocs check --base "$BASE"
```

`check --staged` never needs an upstream. Only `--base @{u}` does.

### Detached HEAD

**Symptom:** none, usually.

**Cause:** CI checkouts, `git bisect`, checking out a tag.

**What gotdocs does:** nothing special. It uses `HEAD` and never resolves a branch
name, so detached HEAD is fully supported. `verify` stamps the detached commit's
sha, which is correct. The only thing that breaks is `@{u}`, which does not exist
in a detached state — see above.

### Empty repository (no commits)

**Symptom:** `git rev-parse HEAD` fails with `ambiguous argument 'HEAD'`.

**Cause:** a freshly `git init`ed repo before the first commit. This is real:
`scripts/install-gotdocs.sh` can run at that point.

**What gotdocs does:** `check --staged` compares against the empty tree, so the
initial commit's staged files are the change set and everything works.
`verify` cannot stamp a sha that does not exist — it reports the condition and
exits 3. `index` writes `"generated_at_sha": null` and regenerates cleanly after
the first commit.

### Fork pull requests

**Symptom:** CI cannot resolve `origin/<base>`, or a step that writes back to the
branch fails with a permissions error.

**Cause:** on a `pull_request` event from a fork, GitHub checks out the merge
commit, `GITHUB_TOKEN` is read-only, and the base branch may not exist as a
remote-tracking ref.

**Fix:** fetch the base ref explicitly and never write back. Gotdocs is read-only,
so a fork PR works as long as the base ref is present:

```sh
git fetch --no-tags origin "+refs/heads/$GITHUB_BASE_REF:refs/remotes/origin/$GITHUB_BASE_REF"
bin/gotdocs check --base "origin/$GITHUB_BASE_REF" --mode error --strict
```

Details in [runbooks/ci-check-failing.md](../runbooks/ci-check-failing.md#4--bad-base-ref-fork-or-shallow-clone).

### Worktrees and submodules

**Symptom:** the hook cannot find `COMMIT_EDITMSG`, or a hook is not installed
where you expect.

**Cause:** in a linked worktree, `.git` is a *file* containing
`gitdir: /path/to/main/.git/worktrees/<name>`. In a submodule it points into the
superproject.

**What gotdocs does:** never hardcodes `.git/`. The CLI asks
`git rev-parse --absolute-git-dir`, because it does not change directory and a
relative answer would be resolved against the caller's cwd. The two shell hooks
ask `git rev-parse --git-dir` and prefix `$REPO_ROOT` themselves when the answer
comes back relative — they have already `cd`-ed to the toplevel by that point.
Hooks are shared across worktrees via the common hooks directory, so installing
once covers all of them — unless `core.hooksPath` is set (see below).

### `core.hooksPath` is set

**Symptom:** `scripts/install-gotdocs.sh` reports success but the hook never runs.

**Cause:** the repo or the user's global config sets `core.hooksPath` to a
different directory (common with `husky`, or a company-wide hooks directory).
Git then ignores `.git/hooks/` entirely.

**Detect:**

```sh
git config --get core.hooksPath
```

**Fix:** install into that directory instead, or add
`bin/gotdocs check --staged` to the `pre-commit` file that lives there.

### `git` not on PATH

**Symptom:** the hook exits 0 silently; the CLI exits 3 with `not a git
repository`.

**What gotdocs does:** the hook checks `command -v git` and exits 0. Commands that
need git exit 3 with a clear message; commands that do not (`lint`, `new`,
`check --paths`) keep working.

### Non-UTF-8 or unusual paths

Paths are decoded as UTF-8 with surrogate escaping, so a file with undecodable
bytes in its name does not crash the run — it simply will not match a glob written
in ASCII, which is the correct outcome.

### Very large change sets

A rename of 5,000 files produces 5,000 code paths matched against every doc's
`covers`. Compiled glob patterns are cached, so the cost is one regex match per
(path, pattern) pair and stays well inside the hook's 300ms budget for normal
commits. For genuinely huge mechanical changes, use the skip token — that is what
it is for.

## What breaks without git

| Command | Without git |
| --- | --- |
| `check --staged` | Exit 3. No change set can be computed. |
| `check --base` | Exit 3. |
| `check --paths` | Works. Pure filesystem. |
| `impacted` | Works. |
| `lint`, `lint --portability` | Works. |
| `index` | Works; `generated_at_sha` becomes JSON `null`. |
| `new` | Works. |
| `why` | Works. It reads `decisions/` off disk and never consults a revision. |
| `export` | Works. The export is byte-deterministic and carries no sha. |
| `debt list` / `render` / `stats` | Works. Reading and rendering the ledger touches no revision. |
| `debt record` / `resolve` | Works, with `sha` recorded as `null` and the date falling back to today's — pass `--date`/`--sha` to keep it reproducible. |
| `verify` | Exit 3. There is no sha to stamp. |
| `status` | Works when `.gotdocs/` is present (head shows `(no commits)`, hook shows `unknown (not a git repository)`); exit 3 when there is no `.gotdocs/` either. |
| `install` | Exit 3. There is no `.git/hooks` to install into. |
| pre-commit / pre-push hook | Exits 0 immediately. |

The degradation is deliberate: without git there is no notion of "changed", so
enforcement is meaningless, but the authoring and validation half of the tool
still works.

## Related

- [docs/architecture.md](../docs/architecture.md) — where the change set enters the pipeline
- [docs/doc-debt.md](../docs/doc-debt.md) — the ledger, and the one CI job that writes to the repository
- [runbooks/ci-check-failing.md](../runbooks/ci-check-failing.md) — base-ref failures in CI
- [dependencies/python3.md](python3.md) — the other hard dependency

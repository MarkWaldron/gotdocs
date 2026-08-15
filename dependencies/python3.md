---
id: dependency-python3
title: "Dependency: python3"
type: dependency
summary: gotdocs is Python 3.9+ standard library only — why there is no PyYAML, which stdlib modules it uses, and what happens when python3 is missing.
covers:
  - bin/gotdocs
  - tools/gotdocs/__main__.py
  - tools/gotdocs/frontmatter.py
owners: ["@mark"]
tags: [dependency, python, stdlib, portability]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
---

# Dependency: python3

The gotdocs CLI is Python. It is vendored into the target repo as source, runs
under whatever `python3` is on `PATH`, and imports nothing that is not in the
standard library.

- **Minimum version:** 3.9.
- **Install step:** none. There is no `pip install`, no virtualenv, no
  `requirements.txt`, no lockfile, no `pyproject.toml`, and no network access at
  any point.
- **How it is invoked:** `bin/gotdocs` is a POSIX `sh` shim. It resolves its own
  path through symlinks, finds the repo root above `tools/gotdocs/__main__.py`,
  prepends that root to `PYTHONPATH`, exports `GOTDOCS_CWD` so the package can
  recover the directory you called it from, and then
  `exec "$py" -m tools.gotdocs "$@"`. Running `python3 tools/gotdocs ...`
  directly also works — `__main__.py` detects the missing package context and
  repairs `sys.path` itself — but the shim uses `-m`.

## Why 3.9

3.9 is the oldest version still present by default on machines people actually
use: macOS ships 3.9 in the Command Line Tools, Debian 11 ships 3.9, RHEL 9 ships
3.9, Ubuntu 20.04 ships 3.8 but 3.9 is one `apt install` away. Requiring 3.10+
would exclude a real fraction of laptops and CI images for no benefit.

Concretely this means the source avoids:

- `match` statements (3.10)
- `X | Y` union syntax in annotations (3.10). Nothing replaced it: there are no
  type annotations anywhere in `tools/gotdocs/`, and `typing` is not imported by
  any module. Verify with
  `grep -rn '^import typing\|^from typing' tools/gotdocs/` — no hits.
- `tomllib` (3.11) — irrelevant anyway, config is JSON
- `str.removeprefix` / `removesuffix`, which are 3.9 and therefore fine
- `dict |` merge, which is 3.9 and therefore fine

Anything 3.9-compatible is fair game. Do not raise the floor without changing this
document — that is what `covers` on this file is for.

## Why no PyYAML

The frontmatter is YAML, and the obvious implementation is
`yaml.safe_load(block)`. Gotdocs implements its own parser in
`tools/gotdocs/frontmatter.py` instead. The reasons, in order of weight:

1. **The tool is vendored, not installed.** Gotdocs is copied into someone else's
   repo. A dependency would mean every engineer and every CI image needs a working
   install step before `git commit` works. A pre-commit hook that can fail because
   a virtualenv is not active is a pre-commit hook people delete.
2. **A dependency in a hook is a dependency in every developer environment.**
   Version skew between a Go engineer's Python and a data scientist's Python is
   not a problem gotdocs is willing to own.
3. **The needed subset is tiny.** Scalars, quoted scalars, flow lists, block
   lists, comments. That is a few hundred lines with tests, and the limits are
   documented and enforced rather than implicit.
4. **Round-tripping is a hard requirement and PyYAML is bad at it.** `verify`
   rewrites `updated` and `verified_at` and must leave every other byte — key
   order, comments, quoting style, blank lines — untouched. A load-then-dump cycle
   through PyYAML destroys all of that and produces unreviewable diffs. The
   line-oriented rewriter gotdocs uses is the right tool regardless of whether
   PyYAML were available.
5. **Unsupported constructs must be errors, not surprises.** PyYAML would happily
   parse a nested map that the rest of gotdocs cannot represent. Gotdocs raises a
   lint error with `file:line` instead. See
   [docs/doc-format.md](../docs/doc-format.md#supported-yaml-subset).

The trade-off is real and accepted: gotdocs frontmatter is a strict subset of
YAML. If you need anchors or nested structures, they belong in the markdown body.

## Standard library modules used

The complete set, and where each one is imported. Regenerate this table with
`grep -rh '^import \|^from ' tools/gotdocs/*.py | sort -u`.

| Module | Imported by | Used for |
| --- | --- | --- |
| `argparse` | `cli.py` | CLI parsing |
| `contextlib` | `cli.py` | `ExitStack` / `redirect_stdout` so `main(argv, stdout, stderr)` is testable |
| `datetime` | `cli.py` | today's date for `updated` |
| `hashlib` | `debt.py` | the 12-hex-digit `entry_id`, a SHA-1 of `(kind, doc_id, path)` |
| `io` | `cli.py`, `debt.py`, `export.py`, `frontmatter.py`, `index.py` | every file read and write, always in binary mode |
| `json` | `config.py`, `debt.py`, `export.py`, `index.py`, `report.py` | config, `index.json`, `debt.jsonl`, `_gotdocs.json`, `--json` output |
| `os`, `os.path` | most modules | paths, walking roots, `os.replace` for atomic writes |
| `posixpath` | `export.py`, `portability.py` | resolving `/`-separated links independently of the host separator |
| `re` | `cli.py`, `debt.py`, `decisions.py`, `export.py`, `frontmatter.py`, `globs.py`, `index.py`, `portability.py` | compiled glob patterns, the frontmatter line grammar, the markdown scanner |
| `shutil` | `cli.py` | `copyfile` when `install` backs a hook up to `.bak` |
| `stat` | `cli.py` | making the installed hook executable |
| `subprocess` | `gitutil.py` | every `git` call |
| `sys` | `cli.py`, `__main__.py` | exit codes, stdout/stderr, the `sys.version_info` gate |
| `unicodedata` | `decisions.py` | NFKD-folding a `why` query so accented prose tokenizes like ASCII |
| `unittest` | `tools/gotdocs/tests/` | the test suite |

Records are plain classes with `__slots__` (`Finding`, `Doc`, `DebtEntry`,
`Decision`, `Issue`) rather than `dataclasses`, there are no type annotations,
and the glob cache is a module-level dict in `globs.py` rather than
`functools.lru_cache` — all three so the modules stay readable on any 3.9+
interpreter with nothing imported that is not needed.

Nothing else, and in particular nothing third-party. Notably absent: `yaml`,
`jsonschema`, `click`, `rich`, `pytest`, `requests`, and any markdown or YAML
library — `export.py` and `portability.py` do their own markdown scanning and
emit their own YAML. `.gotdocs/schema.json` exists as documentation and for
editor integration; no runtime code validates against it, because that would
require `jsonschema`.

## Running the tests

```sh
python3 -m unittest discover -s tools/gotdocs/tests -t .
```

`unittest` is stdlib, so this works on a bare interpreter with nothing installed.
There is no `pytest`, no coverage tool and no test runner config.

## When python3 is absent or too old

The degradation path, in order:

1. **The `sh` shim** (`bin/gotdocs`) resolves an interpreter: `$GOTDOCS_PYTHON`
   first, then `python3`, then `python`. It also resolves its own path through
   symlinks so it works from any working directory. If no interpreter is usable
   it prints a one-line message on stderr and **exits 0** — the shim obeys the
   same "never block a commit" rule as the hook.
2. **The pre-commit hook** checks `command -v python3` *before* invoking the CLI.
   If it is missing it prints

   ```text
   gotdocs: python3 was not found on PATH
   gotdocs: skipping the pre-commit check (commit not blocked).
   ```

   and exits 0. The commit proceeds. This is the rule that matters: **gotdocs is
   never the reason someone cannot commit.**
3. **The package entry point** (`tools/gotdocs/__main__.py`) compares
   `sys.version_info[:2]` against `MIN_PYTHON = (3, 9)` before importing
   anything else, and on an older interpreter writes this to stderr and raises
   `SystemExit(2)` rather than failing later with a confusing import error:

   ```text
   gotdocs: python 3.7 is too old; gotdocs needs python 3.9 or newer
   gotdocs: set GOTDOCS_PYTHON to a newer interpreter, for example: GOTDOCS_PYTHON=/usr/local/bin/python3 bin/gotdocs status
   ```

   The check is in `__main__.py` and not in `cli.py` on purpose: `cli.py`
   imports the whole package, so putting the gate there would mean the import
   error happened before the gate could report it.
4. **CI** pins `python-version` with `actions/setup-python`, so a missing or
   wrong interpreter fails the build instead of silently passing. It does *not*
   pass `--strict`; add it if you want an internal gotdocs error to fail the
   build rather than warn. See
   [docs/enforcement.md](../docs/enforcement.md#ci).

Diagnosing a machine where the hook does nothing:

```sh
command -v python3 || echo "no python3 on PATH"
python3 --version
python3 -c 'import sys; print(sys.version_info >= (3,9))'
bin/gotdocs status
```

Pointing gotdocs at a specific interpreter, for example when only a Homebrew or
`pyenv` Python is new enough:

```sh
export GOTDOCS_PYTHON=/opt/homebrew/bin/python3
bin/gotdocs status
```

Set it in your shell profile if the machine's default `python3` is too old. Do not
solve this with a virtualenv — the hook runs in whatever environment git gives it,
which is usually not your activated shell.

## Installing python3, per platform

No network access is needed by gotdocs, but installing an interpreter obviously
is. In rough order of least surprise:

| Platform | Command |
| --- | --- |
| macOS | `xcode-select --install` (ships 3.9), or `brew install python@3.12` |
| Debian / Ubuntu | `sudo apt install python3` |
| RHEL / Fedora | `sudo dnf install python3` |
| Alpine (CI images) | `apk add python3` |
| GitHub Actions | `actions/setup-python` with `python-version: '3.x'` |

Alpine-based CI images are the usual culprit for a mysteriously skipped check —
they frequently have no `python3` at all, and the hook's exit-0 behavior means the
first sign is a doc that went stale without anyone being told.

## What this constraint forbids

Do not add, in any part of gotdocs:

- a third-party import anywhere under `tools/gotdocs/`
- a `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` or lockfile
- a network call, including "just to check for updates"
- a bash-only construct in `bin/gotdocs`, the hook, or `scripts/install-gotdocs.sh`
  (POSIX `sh`; these run under `dash` on Debian)
- a Python 3.10+ syntax feature

Each of those converts gotdocs from "copy four directories into your repo" into
"onboard a build dependency", which is the thing it exists to avoid.

## Related

- [docs/doc-format.md](../docs/doc-format.md#supported-yaml-subset) — exactly which YAML is supported
- [docs/architecture.md](../docs/architecture.md) — module layout
- [dependencies/git.md](git.md) — the other hard dependency

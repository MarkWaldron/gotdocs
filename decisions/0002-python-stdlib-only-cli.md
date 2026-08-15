---
id: 0002-python-stdlib-only-cli
title: The CLI is vendored Python 3.9+ standard library only, behind a POSIX sh shim
type: decision
summary: gotdocs ships as source inside the repo it guards, imports nothing outside the Python 3.9 stdlib, and its sh shim exits 0 rather than blocking a commit when python is missing.
covers:
  - bin/gotdocs
  - tools/gotdocs/__main__.py
  - tools/gotdocs/frontmatter.py
  - tools/gotdocs/globs.py
symptoms:
  - there is no pip install step and no requirements.txt anywhere
  - gotdocs printed a warning about python and then let my commit through
  - the YAML in frontmatter rejects something PyYAML would happily parse
  - gotdocs says my python is too old and exits 2
  - the whole tool is checked into my repo instead of installed from a package index
  - a colleague on a different machine gets identical gotdocs output to mine
  - gotdocs works in a container with no network access
supersedes: []
superseded_by: []
owners:
  - "@mark"
tags:
  - architecture
  - dependencies
  - python
status: accepted
decided_on: 2026-08-14
updated: 2026-08-15
verified_at: d1956a8
---

# The CLI is vendored Python 3.9+ standard library only, behind a POSIX sh shim

## Context

gotdocs runs in a pre-commit hook. That places it in the most hostile position in
the toolchain: it executes on every commit, on every contributor's machine, in
whatever shell and interpreter that machine happens to have, before anybody has
run a setup script. A tool in that position that needs `pip install` will, on
some machine, fail to be installed — and a documentation checker that breaks
commits is uninstalled within a day.

The two candidate dependencies were obvious and both were tempting: PyYAML to
parse frontmatter, and `pathspec`/`fnmatch`-adjacent libraries to match `covers`
globs. Each would have removed a few hundred lines of code here.

## Decision

The CLI is written in Python and imports only the Python 3.9+ standard library.
The complete import surface is `os`, `io`, `re`, `sys`, `json`, `datetime`,
`hashlib`, `subprocess`, `argparse`, `unicodedata` and friends — nothing from
PyPI, ever. The package is *vendored*: `tools/gotdocs/` is committed into the
repository it guards, alongside `bin/gotdocs`, a POSIX `sh` shim.

The shim's failure posture is the other half of the decision: when it cannot find
an interpreter or cannot find the package, it writes one line to stderr and
**exits 0**. gotdocs is never the reason a commit cannot happen.

## Expected behavior

- There is no `requirements.txt`, no `pyproject.toml`, no lockfile and no install
  step. Cloning the repo is the installation.

  ```console
  $ git clone <repo> && cd <repo> && bin/gotdocs status
  ```

  works on a machine that has never heard of gotdocs.
- `bin/gotdocs` resolves its own path through symlinks (up to 40 hops), works
  from any working directory, and tolerates spaces in paths.
- Interpreter selection order is `$GOTDOCS_PYTHON`, then `python3`, then
  `python`. With none of them present:

  ```console
  $ bin/gotdocs check --staged
  gotdocs: python3 not found; skipping (set GOTDOCS_PYTHON to override)
  $ echo $?
  0
  ```

  Exit 0, on stderr, so the pre-commit hook does not veto the commit.
- On an interpreter older than 3.9 the message is explicit and the exit code is
  2, checked in `tools/gotdocs/__main__.py` *before* the package is imported so
  the user gets one readable line rather than a SyntaxError traceback:

  ```text
  gotdocs: python 3.7 is too old; gotdocs needs python 3.9 or newer
  gotdocs: set GOTDOCS_PYTHON to a newer interpreter, for example: GOTDOCS_PYTHON=/usr/local/bin/python3 bin/gotdocs status
  ```

- Both `python3 -m tools.gotdocs check --staged` and
  `python3 tools/gotdocs check --staged` work; the second form has no package
  context, and `__main__.py` puts the repo root on `sys.path` to compensate.
- `python3 -m unittest discover -s tools/gotdocs/tests -t .` runs the full suite
  with no test runner installed.
- Two machines with the same repo produce byte-identical `bin/gotdocs index`
  output, because there is no dependency whose version could differ.

## This is a bug, not this decision, if...

- Any file under `tools/gotdocs/` imports a module that is not in the Python 3.9
  standard library. `grep -rn "^import \|^from " tools/gotdocs/*.py` is the
  check; a third-party name there is a defect, not a tradeoff.
- `bin/gotdocs` exits non-zero when the interpreter is missing. The shim is
  supposed to exit 0 on every environment failure; a non-zero exit from a missing
  `python3` is a bug in `bin/gotdocs`.
- Conversely, `bin/gotdocs` exits 0 while *swallowing a real finding*. Exit 0 on a
  missing interpreter is by design; exit 0 from a `check --mode error` run that
  found stale docs is a bug in `CheckResult.exit_code`.
- Something in `tools/gotdocs/` uses a syntax or stdlib API newer than 3.9 —
  `match` statements, `str.removeprefix`, `dict |` merge, `X | Y` type unions at
  runtime. The floor is `MIN_PYTHON = (3, 9)` in `__main__.py`; anything that
  breaks on 3.9 is a bug even though it is "just stdlib".
- The frontmatter parser mis-parses a construct that is *inside* the documented
  subset (`docs/doc-format.md`). Rejecting nested maps is this decision;
  mangling `key: [a, b, c]` is a bug in `tools/gotdocs/frontmatter.py`. See
  0005 for where that boundary sits.
- A glob in the documented dialect matches incorrectly. Reimplementing globbing
  instead of using `fnmatch` is this decision; `bin/gotdocs impacted
  tools/gotdocs/globs.py` returning nothing while a doc declares
  `covers: tools/gotdocs/**` is a bug in `tools/gotdocs/globs.py`.
- The shim's symlink resolution loops forever instead of stopping at 40 hops, or
  fails to find `tools/gotdocs/__main__.py` when invoked through a `PATH`
  symlink in a tree where `git rev-parse --show-toplevel` succeeds.

## Consequences

Roughly 1,500 lines of this codebase exist only because PyYAML and a glob library
are unavailable: `frontmatter.py` (741 lines) and `globs.py` (338 lines) are
re-implementations, and both must be maintained and tested. `.gotdocs/schema.json`
carries a `$comment` saying it validates nothing at runtime, because there is no
`jsonschema` — the rules are re-implemented natively in `index.py`, and the two
must be changed in the same commit or they drift.

Vendoring means upgrading gotdocs is a file copy into each adopting repo, not a
version bump. There is no `gotdocs --version` that a package manager can resolve,
and a fleet of repos will sit on different vintages of the tool.

The stdlib-only rule also rules out fuzzy matching for `gotdocs why`: no
embeddings, no search index, just token overlap in `decisions.py`.

## Alternatives considered

- **Depend on PyYAML.** Rejected: it is the single most common way a hook fails
  on a fresh machine, and the frontmatter this tool needs is a dozen lines of
  `key: value`. The subset is a feature (see 0005), not just a consequence.
- **Distribute on PyPI and `pip install gotdocs`.** Rejected: the hook then
  depends on the user's Python environment being provisioned, which for a
  pre-commit hook is exactly the failure this decision avoids. Also makes the
  tool's own source invisible to the repo it guards.
- **Write it in Go or Rust and ship a binary.** Rejected: requires per-platform
  binaries committed to every adopting repo, and makes the tool unreadable and
  unpatchable by the people it runs for. A repo that can run its own test suite
  can already run Python.
- **Write it in pure POSIX sh.** Rejected: the glob dialect, JSON output and
  frontmatter parsing are not writable in sh at a quality anybody would trust.
  The sh footprint is deliberately confined to the shim and the hooks.
- **Bundle vendored copies of PyYAML into `tools/`.** Rejected: same maintenance
  burden as writing the subset, plus a much larger surface and a licence to
  track, for parsing features the format forbids anyway.

## Revisit when

Revisit the 3.9 floor when the oldest interpreter in the fleet of adopting repos
is 3.11 or newer — that would allow `tomllib` for config and remove some
`unicodedata` gymnastics. Revisit the vendoring model if a repo count in the
hundreds makes the copy-in upgrade path genuinely unmanageable; the fix then is a
`bin/gotdocs self-update` that pulls from a pinned commit, not a package
dependency.

## References

- `bin/gotdocs` — interpreter resolution, symlink walk, exit-0 posture.
- `tools/gotdocs/__main__.py` — `MIN_PYTHON = (3, 9)` and `_version_error`.
- `tools/gotdocs/frontmatter.py` — the PyYAML replacement.
- `tools/gotdocs/globs.py` — the `fnmatch`/`pathspec` replacement.
- `dependencies/python3.md` — which stdlib modules are used and what breaks
  without them.
- `.gotdocs/schema.json` — the `$comment` recording that nothing validates
  against it at runtime.

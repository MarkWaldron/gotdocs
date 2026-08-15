#!/bin/sh
# Install the gotdocs git hooks into this repository.
#
# Idempotent and safe to re-run. It:
#   * verifies the vendored file set is complete and reports what is missing
#   * copies .gotdocs/hooks/{pre-commit,pre-push} into the repository hooks
#     directory and makes them executable
#   * preserves any pre-existing, non-gotdocs hook by renaming it to
#     <hook>.local, which the gotdocs hook then runs first
#   * honours core.hooksPath when it is configured
#   * creates .gotdocs/config.json from the documented defaults if absent
#   * regenerates .gotdocs/index.json and .gotdocs/INDEX.md
#
# Usage: scripts/install-gotdocs.sh [--force]
#
#   --force   Reinstall even when the hooks are already current, and rotate an
#             existing <hook>.local aside instead of refusing to overwrite it.

set -eu

HOOKS='pre-commit pre-push'
MARKER='gotdocs-managed-hook'
FORCE=0

# Every file gotdocs expects to find vendored in the target repository. Missing
# entries are reported, not fixed: this script cannot invent them, and a repo
# that silently half-installs is worse than one that says what it is missing.
# Split into two lists because only the first group breaks the hooks.
REQUIRED_FILES='bin/gotdocs
tools/gotdocs/__init__.py
tools/gotdocs/__main__.py
tools/gotdocs/check.py
tools/gotdocs/cli.py
tools/gotdocs/config.py
tools/gotdocs/debt.py
tools/gotdocs/decisions.py
tools/gotdocs/errors.py
tools/gotdocs/export.py
tools/gotdocs/frontmatter.py
tools/gotdocs/gitutil.py
tools/gotdocs/globs.py
tools/gotdocs/index.py
tools/gotdocs/portability.py
tools/gotdocs/report.py
.gotdocs/hooks/pre-commit
.gotdocs/hooks/pre-push'

OPTIONAL_FILES='scripts/uninstall-gotdocs.sh
.gotdocs/README.md
.gotdocs/schema.json
.gotdocs/templates/doc.md
.gotdocs/templates/runbook.md
.gotdocs/templates/onboarding.md
.gotdocs/templates/dependency.md
.gotdocs/templates/decision.md
.github/workflows/gotdocs.yml
.claude/settings.json
.claude/skills/gotdocs-update/SKILL.md
.claude/skills/gotdocs-author/SKILL.md
.claude/skills/gotdocs-audit/SKILL.md
.claude/skills/gotdocs-install/SKILL.md'

usage() {
    cat <<'USAGE'
Usage: scripts/install-gotdocs.sh [--force] [--help]

  --force   Reinstall hooks unconditionally and rotate aside a conflicting
            <hook>.local backup instead of stopping.
  --help    Show this message.
USAGE
}

while [ "$#" -gt 0 ]; do
    case $1 in
        -f | --force)
            FORCE=1
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            printf 'install-gotdocs: unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

say() {
    printf '%s\n' "$1"
}

fail() {
    printf 'install-gotdocs: error: %s\n' "$1" >&2
    exit 1
}

if ! command -v git >/dev/null 2>&1; then
    fail 'git was not found on PATH'
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
    fail 'not inside a git repository (run this from the repository you want to instrument)'
fi
cd "$REPO_ROOT"

SRC_DIR="$REPO_ROOT/.gotdocs/hooks"
if [ ! -d "$SRC_DIR" ]; then
    fail ".gotdocs/hooks was not found under $REPO_ROOT"
fi

say "gotdocs: repository $REPO_ROOT"

# ---------------------------------------------------------------------------
# Vendored file set. Everything gotdocs needs is committed in the repository --
# there is nothing to download and no package to install -- so a missing file
# means the vendoring step was incomplete, and saying which one is the only
# useful thing this script can do about it.
# ---------------------------------------------------------------------------
MISSING_REQUIRED=''
MISSING_OPTIONAL=''
for wanted in $REQUIRED_FILES; do
    [ -f "$REPO_ROOT/$wanted" ] || MISSING_REQUIRED="$MISSING_REQUIRED $wanted"
done
for wanted in $OPTIONAL_FILES; do
    [ -f "$REPO_ROOT/$wanted" ] || MISSING_OPTIONAL="$MISSING_OPTIONAL $wanted"
done

if [ -n "$MISSING_REQUIRED" ]; then
    say ''
    say 'gotdocs: WARNING these required files are missing from this repository:'
    for wanted in $MISSING_REQUIRED; do
        say "           $wanted"
    done
    say '         Copy them from the gotdocs repository (or re-run the gotdocs-install'
    say '         skill) and run this script again. The hooks degrade to a no-op until'
    say '         then; they will not block anything.'
    say ''
fi
if [ -n "$MISSING_OPTIONAL" ]; then
    say 'gotdocs: note, these optional files are not vendored here:'
    for wanted in $MISSING_OPTIONAL; do
        say "           $wanted"
    done
    say '         (CI job, Claude skills, editor schema, uninstall script, templates)'
    say ''
fi

# The vendored entry points must stay executable through a `cp -R`, a zip
# download, or a checkout on a filesystem that dropped the mode bits.
for executable in bin/gotdocs scripts/install-gotdocs.sh scripts/uninstall-gotdocs.sh \
    .gotdocs/hooks/pre-commit .gotdocs/hooks/pre-push; do
    if [ -f "$REPO_ROOT/$executable" ] && [ ! -x "$REPO_ROOT/$executable" ]; then
        chmod +x "$REPO_ROOT/$executable" 2>/dev/null || true
    fi
done

# ---------------------------------------------------------------------------
# Where do this repository's hooks actually live?
#
# core.hooksPath, when set, wins over .git/hooks for every hook git runs. It is
# commonly set by husky, lefthook, pre-commit or a corporate dotfiles setup. We
# install into that directory instead, and say so loudly, because a copy left in
# .git/hooks would silently never execute.
# ---------------------------------------------------------------------------
HOOKS_PATH=$(git config --get core.hooksPath 2>/dev/null || true)
USING_HOOKS_PATH=0

if [ -n "$HOOKS_PATH" ]; then
    USING_HOOKS_PATH=1
    case $HOOKS_PATH in
        /*) HOOKS_DIR=$HOOKS_PATH ;;
        '~/'*) HOOKS_DIR="$HOME/${HOOKS_PATH#'~/'}" ;;
        *) HOOKS_DIR="$REPO_ROOT/$HOOKS_PATH" ;;
    esac
    say ''
    say 'gotdocs: core.hooksPath is set on this repository.'
    say "         value:  $HOOKS_PATH"
    say "         target: $HOOKS_DIR"
    say '         git runs hooks from that directory only, so gotdocs is being'
    say '         installed there. Anything in .git/hooks is ignored by git while'
    say '         core.hooksPath is set. If another hook manager owns that'
    say '         directory, the existing hook is preserved and chained (below).'
    say '         To go back to the default location:  git config --unset core.hooksPath'
    say ''
else
    GIT_DIR_PATH=$(git rev-parse --git-dir 2>/dev/null || true)
    if [ -z "$GIT_DIR_PATH" ]; then
        fail 'could not resolve the git directory'
    fi
    case $GIT_DIR_PATH in
        /*) ;;
        *) GIT_DIR_PATH="$REPO_ROOT/$GIT_DIR_PATH" ;;
    esac
    HOOKS_DIR="$GIT_DIR_PATH/hooks"
fi

mkdir -p "$HOOKS_DIR" || fail "could not create $HOOKS_DIR"
if [ ! -w "$HOOKS_DIR" ]; then
    fail "$HOOKS_DIR is not writable"
fi

is_gotdocs_hook() {
    [ -f "$1" ] || return 1
    grep -q "$MARKER" "$1" 2>/dev/null
}

files_identical() {
    [ -f "$1" ] && [ -f "$2" ] || return 1
    cmp -s "$1" "$2"
}

install_hook() {
    hook=$1
    src="$SRC_DIR/$hook"
    dst="$HOOKS_DIR/$hook"

    if [ ! -f "$src" ]; then
        say "  $hook: SKIPPED (no source at .gotdocs/hooks/$hook)"
        return 0
    fi

    if [ -e "$dst" ] || [ -L "$dst" ]; then
        if is_gotdocs_hook "$dst"; then
            if files_identical "$src" "$dst" && [ "$FORCE" -eq 0 ]; then
                chmod +x "$dst" 2>/dev/null || true
                say "  $hook: already current"
                return 0
            fi
            rm -f "$dst"
            copy_hook "$src" "$dst"
            say "  $hook: updated"
            return 0
        fi

        # A hook we do not own. Never clobber it: move it to <hook>.local, which
        # the gotdocs hook runs first and whose non-zero exit still vetoes.
        if [ -e "$dst.local" ] || [ -L "$dst.local" ]; then
            if [ "$FORCE" -eq 0 ]; then
                printf 'install-gotdocs: error: %s\n' \
                    "both $dst and $dst.local exist" >&2
                printf '  %s\n' \
                    "$dst is not a gotdocs hook and moving it would overwrite the existing backup." >&2
                printf '  %s\n' \
                    'Resolve it by hand, or re-run with --force to rotate the backup aside.' >&2
                exit 1
            fi
            rotated="$dst.local.$(date +%Y%m%d%H%M%S)"
            mv "$dst.local" "$rotated"
            say "  $hook: rotated previous backup to $(basename "$rotated")"
        fi
        mv "$dst" "$dst.local"
        chmod +x "$dst.local" 2>/dev/null || true
        copy_hook "$src" "$dst"
        say "  $hook: installed (existing hook preserved as $hook.local and chained first)"
        return 0
    fi

    copy_hook "$src" "$dst"
    say "  $hook: installed"
}

copy_hook() {
    cp "$1" "$2" || fail "could not write $2"
    chmod +x "$2" || fail "could not make $2 executable"
}

say "gotdocs: installing hooks into $HOOKS_DIR"
for hook in $HOOKS; do
    install_hook "$hook"
done

# ---------------------------------------------------------------------------
# When core.hooksPath is in play, stale copies under .git/hooks are dead weight
# and actively confusing. Point them out rather than deleting anything.
# ---------------------------------------------------------------------------
if [ "$USING_HOOKS_PATH" -eq 1 ]; then
    GIT_DIR_PATH=$(git rev-parse --git-dir 2>/dev/null || true)
    if [ -n "$GIT_DIR_PATH" ]; then
        case $GIT_DIR_PATH in
            /*) ;;
            *) GIT_DIR_PATH="$REPO_ROOT/$GIT_DIR_PATH" ;;
        esac
        for hook in $HOOKS; do
            if is_gotdocs_hook "$GIT_DIR_PATH/hooks/$hook"; then
                say "  note: $GIT_DIR_PATH/hooks/$hook is a leftover gotdocs hook that git will never run"
                say '        (core.hooksPath overrides it); delete it when convenient'
            fi
        done
    fi
fi

# ---------------------------------------------------------------------------
# Config: write a starter config only when there is nothing there. An existing
# config is user data and is never rewritten, not even with --force.
#
# NOTE: an explicit "ignore" list REPLACES the CLI's built-in DEFAULT_IGNORE
# (78 patterns) rather than extending it, and the starter below is deliberately
# short (12 patterns) so it is readable. Widen it for the repo -- caches, generated code and
# binary assets that are not listed here will mark docs stale.
# ---------------------------------------------------------------------------
CONFIG_FILE="$REPO_ROOT/.gotdocs/config.json"
if [ -f "$CONFIG_FILE" ]; then
    say "gotdocs: config already present at .gotdocs/config.json (left untouched)"
else
    mkdir -p "$REPO_ROOT/.gotdocs"
    cat >"$CONFIG_FILE" <<'CONFIG'
{
  "version": 1,
  "roots": ["docs", "runbooks", "onboarding", "dependencies", "decisions"],
  "enforce": { "pre_commit": "warn", "pre_push": "warn", "ci": "warn" },
  "ignore": [
    "**/node_modules/**", "**/.git/**", "**/dist/**", "**/build/**",
    "**/vendor/**", "**/*.lock", "**/*.min.*", "**/testdata/**",
    ".gotdocs/index.json", ".gotdocs/INDEX.md",
    ".gotdocs/debt.jsonl", ".gotdocs/DEBT.md"
  ],
  "require_coverage": false,
  "skip_token": "[gotdocs skip]",
  "max_summary_chars": 200,
  "debt": {
    "enabled": true,
    "ledger": ".gotdocs/debt.jsonl",
    "report": ".gotdocs/DEBT.md",
    "record_kinds": [
      "stale", "uncovered", "lint",
      "duplicate_id", "deprecated_edit", "index_out_of_date"
    ],
    "max_report_lines": 20
  },
  "publish": {
    "target": "docusaurus",
    "out_dir": "build/gotdocs-site",
    "url_prefix": "",
    "source_url": "",
    "layout": "",
    "include_drafts": false,
    "h1_in_body": true
  }
}
CONFIG
    say 'gotdocs: created .gotdocs/config.json with starter settings'
    say '         widen "ignore" for this repo (caches, generated code, binaries)'
    say '         enforce.ci starts at "warn" on purpose: raise it to "error" once'
    say '         the existing docs are in shape, not on day one'
fi

# ---------------------------------------------------------------------------
# Build the index so the very first check has something to work with.
# ---------------------------------------------------------------------------
INDEX_OK=0
if ! command -v python3 >/dev/null 2>&1; then
    say 'gotdocs: WARNING python3 was not found on PATH; index not generated'
    say '         (the hooks degrade to a no-op until python3 is available)'
elif [ ! -f "$REPO_ROOT/bin/gotdocs" ]; then
    say 'gotdocs: WARNING bin/gotdocs is missing; index not generated'
else
    if [ ! -x "$REPO_ROOT/bin/gotdocs" ]; then
        chmod +x "$REPO_ROOT/bin/gotdocs" 2>/dev/null || true
    fi
    set +e
    if [ -x "$REPO_ROOT/bin/gotdocs" ]; then
        "$REPO_ROOT/bin/gotdocs" index
    else
        sh "$REPO_ROOT/bin/gotdocs" index
    fi
    INDEX_STATUS=$?
    set -e
    if [ "$INDEX_STATUS" -eq 0 ]; then
        INDEX_OK=1
        say 'gotdocs: regenerated .gotdocs/index.json and .gotdocs/INDEX.md'
    else
        say "gotdocs: WARNING 'bin/gotdocs index' exited with status $INDEX_STATUS"
    fi
fi

say ''
say 'gotdocs: install complete.'
say "  hooks directory: $HOOKS_DIR"
say '  next steps:'
say '    - commit .gotdocs/ (config, index.json, INDEX.md) so teammates get it on pull'
say '    - re-run this script after pulling changes to .gotdocs/hooks/'
if [ "$INDEX_OK" -eq 0 ]; then
    say '    - run: bin/gotdocs index   (once python3 and bin/gotdocs are available)'
fi
say '    - seed the docs: ask Claude to run /gotdocs-audit, or bin/gotdocs new doc <id>'
say '    - record the first architecture decision: bin/gotdocs new decision "<title>"'
say '  see what has been deferred with: bin/gotdocs debt list'
say '  bypass a single commit with: GOTDOCS_SKIP=1 git commit ...'
say '  uninstall with: scripts/uninstall-gotdocs.sh'

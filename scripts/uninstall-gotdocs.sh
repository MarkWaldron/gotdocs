#!/bin/sh
# Remove the gotdocs git hooks from this repository.
#
# Reverses scripts/install-gotdocs.sh:
#   * deletes the installed gotdocs hooks
#   * restores any <hook>.local the installer preserved back to <hook>
#   * honours core.hooksPath the same way the installer does
#
# It deliberately leaves .gotdocs/ (config, index, templates) and the vendored
# CLI in place: those are committed repository content, not local state.
#
# Usage: scripts/uninstall-gotdocs.sh [--force]
#
#   --force   Remove the hook file even when it is not recognisably a gotdocs
#             hook (no marker comment). Without it, unrecognised hooks are left
#             alone and reported.

set -eu

HOOKS='pre-commit pre-push'
MARKER='gotdocs-managed-hook'
FORCE=0

usage() {
    cat <<'USAGE'
Usage: scripts/uninstall-gotdocs.sh [--force] [--help]

  --force   Remove the installed hook even if it no longer carries the gotdocs
            marker comment.
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
            printf 'uninstall-gotdocs: unknown option: %s\n\n' "$1" >&2
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
    printf 'uninstall-gotdocs: error: %s\n' "$1" >&2
    exit 1
}

if ! command -v git >/dev/null 2>&1; then
    fail 'git was not found on PATH'
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
    fail 'not inside a git repository'
fi
cd "$REPO_ROOT"

HOOKS_PATH=$(git config --get core.hooksPath 2>/dev/null || true)
if [ -n "$HOOKS_PATH" ]; then
    case $HOOKS_PATH in
        /*) HOOKS_DIR=$HOOKS_PATH ;;
        '~/'*) HOOKS_DIR="$HOME/${HOOKS_PATH#'~/'}" ;;
        *) HOOKS_DIR="$REPO_ROOT/$HOOKS_PATH" ;;
    esac
    say "gotdocs: core.hooksPath is set ($HOOKS_PATH); uninstalling from $HOOKS_DIR"
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

if [ ! -d "$HOOKS_DIR" ]; then
    say "gotdocs: no hooks directory at $HOOKS_DIR; nothing to do"
    exit 0
fi

is_gotdocs_hook() {
    [ -f "$1" ] || return 1
    grep -q "$MARKER" "$1" 2>/dev/null
}

restore_local() {
    hook=$1
    dst="$HOOKS_DIR/$hook"
    if [ -e "$dst.local" ] || [ -L "$dst.local" ]; then
        if [ -e "$dst" ] || [ -L "$dst" ]; then
            say "  $hook: $hook.local kept in place ($hook still exists and was not removed)"
            return 0
        fi
        mv "$dst.local" "$dst"
        chmod +x "$dst" 2>/dev/null || true
        say "  $hook: restored the previously preserved hook from $hook.local"
    fi
}

remove_hook() {
    hook=$1
    dst="$HOOKS_DIR/$hook"

    if [ ! -e "$dst" ] && [ ! -L "$dst" ]; then
        say "  $hook: not installed"
        restore_local "$hook"
        return 0
    fi

    if is_gotdocs_hook "$dst"; then
        rm -f "$dst"
        say "  $hook: removed"
        restore_local "$hook"
        return 0
    fi

    if [ "$FORCE" -eq 1 ]; then
        rm -f "$dst"
        say "  $hook: removed (--force; the file did not carry the gotdocs marker)"
        restore_local "$hook"
        return 0
    fi

    say "  $hook: left in place (not a gotdocs hook; re-run with --force to remove it)"
}

say "gotdocs: uninstalling hooks from $HOOKS_DIR"
for hook in $HOOKS; do
    remove_hook "$hook"
done

# Leftover copies that git ignores while core.hooksPath is set: clean those too,
# since they are unambiguously ours.
if [ -n "$HOOKS_PATH" ]; then
    GIT_DIR_PATH=$(git rev-parse --git-dir 2>/dev/null || true)
    if [ -n "$GIT_DIR_PATH" ]; then
        case $GIT_DIR_PATH in
            /*) ;;
            *) GIT_DIR_PATH="$REPO_ROOT/$GIT_DIR_PATH" ;;
        esac
        for hook in $HOOKS; do
            if is_gotdocs_hook "$GIT_DIR_PATH/hooks/$hook"; then
                rm -f "$GIT_DIR_PATH/hooks/$hook"
                say "  $hook: also removed the leftover copy in $GIT_DIR_PATH/hooks"
            fi
        done
    fi
fi

say ''
say 'gotdocs: uninstall complete.'
say '  left in place (committed repository content, remove by hand if you want it gone):'
say '    .gotdocs/   bin/gotdocs   tools/gotdocs/   .github/workflows/gotdocs.yml'
say '    .claude/skills/gotdocs-*/   .claude/settings.json   docs/ runbooks/ onboarding/'
say '    decisions/  dependencies/'
say '  local state this leaves alone as well (it is history, not configuration):'
say '    .gotdocs/debt.jsonl   .gotdocs/DEBT.md'
say '  the CI workflow keeps running until .github/workflows/gotdocs.yml is deleted;'
say '  removing the hooks only stops the local checks.'
say '  the Claude hooks in .claude/settings.json (PostToolUse / SessionStart) are also'
say '  independent of the git hooks; delete that file to stop them.'
say '  reinstall with: scripts/install-gotdocs.sh'

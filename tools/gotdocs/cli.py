"""Argument parsing, subcommand dispatch, global flags and exit codes.

The whole CLI runs inside one wrapper, :func:`main`, which implements the
failure posture from docs/architecture.md#failure-posture: gotdocs is a
pre-commit hook in someone else's repository, so an unexpected internal
exception prints ``gotdocs: internal error: <msg>`` on stderr and exits 0.
``--strict`` turns that back into exit 2; CI sets it.
"""

import argparse
import contextlib
import datetime
import io
import os
import re
import shutil
import stat
import sys

from . import check as check_module
from . import ci as ci_module
from . import config as config_module
from . import debt as debt_module
from . import decisions as decisions_module
from . import export as export_module
from . import frontmatter as fm_module
from . import gitutil
from . import globs
from . import index as index_module
from . import portability
from . import report
from .errors import (
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_USAGE,
    DocNotFoundError,
    EmptyRepoError,
    GitError,
    GotdocsError,
    NotAGitRepoError,
    UsageError,
)

__all__ = ["main", "build_parser"]

PROGRAM = "gotdocs"
VERSION = "1"

TYPE_TO_ROOT = {
    "doc": "docs",
    "runbook": "runbooks",
    "onboarding": "onboarding",
    "dependency": "dependencies",
    "decision": config_module.DECISIONS_ROOT,
}

DEBT_SOURCES = ("manual", "hook", "ci")

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# entry point and the graceful-degradation boundary
# ---------------------------------------------------------------------------


def main(argv=None, stdout=None, stderr=None):
    """Run the CLI. Returns a process exit code; never raises for user input."""
    argv = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    strict = "--strict" in argv
    # `--json` is read straight off argv because the failure may happen before
    # (or instead of) parsing: a consumer that asked for JSON must never get
    # zero bytes on stdout.
    json_mode = "--json" in argv

    with contextlib.ExitStack() as stack:
        # argparse writes usage and --help straight to sys.stdout / sys.stderr,
        # so redirect them when a caller supplied its own streams.
        if stdout is not None:
            stack.enter_context(contextlib.redirect_stdout(out))
        if stderr is not None:
            stack.enter_context(contextlib.redirect_stderr(err))
        return _run(argv, out, err, strict, json_mode)


def _run(argv, out, err, strict, json_mode=False):
    try:
        return _dispatch(argv, out, err)
    except SystemExit as exc:  # argparse --help and usage errors
        code = exc.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return code
        err.write("%s: %s\n" % (PROGRAM, code))
        return EXIT_USAGE
    except GotdocsError as exc:
        err.write("%s: %s\n" % (PROGRAM, _message_of(exc)))
        _json_error(out, json_mode, exc.exit_code, _message_of(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        err.write("%s: interrupted\n" % (PROGRAM,))
        _json_error(out, json_mode, 130, "interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - this is the whole point
        message = "internal error: %s: %s" % (type(exc).__name__, exc)
        err.write("%s: %s\n" % (PROGRAM, message))
        if strict:
            import traceback

            traceback.print_exc(file=err)
            _json_error(out, json_mode, EXIT_USAGE, message)
            return EXIT_USAGE
        _json_error(out, json_mode, EXIT_OK, message)
        return EXIT_OK


def _json_error(out, json_mode, code, message):
    """Emit the documented error envelope when the caller asked for --json."""
    if not json_mode:
        return
    try:
        out.write(report.dumps(report.error_envelope(code, message)))
    except Exception:  # pragma: no cover - stdout is already broken
        pass


def _message_of(exc):
    located = getattr(exc, "located", None)
    if callable(located):
        return located()
    return str(exc)


def _dispatch(argv, out, err):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help(out)
        return EXIT_OK
    handler = args.handler
    context = Context(args, out, err)
    return handler(context)


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """``argparse.ArgumentParser`` that reports usage errors like every other one.

    Stock argparse prints its own message to stderr and calls ``sys.exit(2)``
    with nothing on stdout. That breaks the promise that ``--json`` always emits
    the error envelope: ``export --target nope --json`` would hand a consumer
    zero bytes to parse while ``lint --rules nope --json`` handed it
    ``{"ok": false, ... "error": {...}}``. Raising :class:`UsageError` instead
    routes an unknown ``--target``, an unknown flag and a bad ``--mode`` through
    the same handler as every hand-written usage error.
    """

    def error(self, message):  # noqa: D401 - argparse hook
        raise UsageError(message)

    def exit(self, status=0, message=None):  # noqa: D401 - argparse hook
        if status and message:
            raise UsageError(message.strip())
        super(_Parser, self).exit(status, message)


def _global_flags():
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--repo",
        metavar="PATH",
        help="operate on this repo instead of discovering the toplevel from the cwd",
    )
    parent.add_argument(
        "--quiet", action="store_true", help="suppress informational output"
    )
    parent.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parent.add_argument(
        "--strict",
        action="store_true",
        help="turn internal errors into failures instead of warn-and-exit-0",
    )
    return parent


def build_parser():
    """Build the full argument parser, including every subcommand."""
    parent = _global_flags()
    parser = _Parser(
        prog=PROGRAM,
        parents=[parent],
        description="Keep a repository's documentation honest about its code.",
    )
    parser.add_argument(
        "--version", action="version", version="%s %s" % (PROGRAM, VERSION)
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # check ----------------------------------------------------------------
    check_parser = subparsers.add_parser(
        "check",
        parents=[parent],
        help="report documents made stale by a change set",
    )
    source = check_parser.add_mutually_exclusive_group()
    source.add_argument(
        "--staged", action="store_true", help="change set is `git diff --cached`"
    )
    source.add_argument(
        "--base",
        metavar="REF",
        help="change set is `git diff REF...HEAD` (three-dot, against the merge base)",
    )
    source.add_argument(
        "--paths",
        metavar="PATH",
        nargs="+",
        help="change set is this literal list of paths; needs no git history",
    )
    check_parser.add_argument("--json", action="store_true", help="emit the JSON contract")
    check_parser.add_argument(
        "--mode",
        choices=config_module.MODES,
        help="override the configured enforcement mode for this run",
    )
    check_parser.add_argument(
        "--message",
        metavar="TEXT",
        help="commit message to scan for the skip token instead of COMMIT_EDITMSG",
    )
    check_parser.add_argument(
        "--message-file",
        metavar="PATH",
        help="read the commit message from this file",
    )
    check_parser.set_defaults(handler=cmd_check)

    # impacted -------------------------------------------------------------
    impacted_parser = subparsers.add_parser(
        "impacted",
        parents=[parent],
        help="which documents describe these files?",
    )
    impacted_parser.add_argument("paths", nargs="+", metavar="PATH")
    impacted_parser.add_argument("--json", action="store_true")
    impacted_parser.set_defaults(handler=cmd_impacted)

    # verify ---------------------------------------------------------------
    verify_parser = subparsers.add_parser(
        "verify",
        parents=[parent],
        help="stamp verified_at/updated on documents you have re-read",
    )
    verify_parser.add_argument("doc_ids", nargs="*", metavar="DOC-ID")
    verify_parser.add_argument(
        "--all-impacted",
        action="store_true",
        help="take the doc ids from the current `check --staged` result",
    )
    verify_parser.add_argument("--json", action="store_true")
    verify_parser.set_defaults(handler=cmd_verify)

    # index ----------------------------------------------------------------
    index_parser = subparsers.add_parser(
        "index",
        parents=[parent],
        help="regenerate .gotdocs/index.json and .gotdocs/INDEX.md",
    )
    index_parser.add_argument("--json", action="store_true")
    index_parser.set_defaults(handler=cmd_index)

    # lint -----------------------------------------------------------------
    lint_parser = subparsers.add_parser(
        "lint", parents=[parent], help="validate frontmatter across all roots"
    )
    lint_parser.add_argument("--json", action="store_true")
    lint_parser.add_argument(
        "--portability",
        action="store_true",
        help="also check that every document renders on the supported static site "
        "generators; reported as warnings unless --strict is given",
    )
    lint_parser.add_argument(
        "--targets",
        metavar="NAME[,NAME]",
        help="limit --portability to these targets (default: %s)"
        % (",".join(portability.TARGETS),),
    )
    lint_parser.add_argument(
        "--rules",
        metavar="NAME[,NAME]",
        help="limit --portability to these rule names (%s)"
        % (", ".join(portability.rule_names()),),
    )
    lint_parser.set_defaults(handler=cmd_lint)

    # status ---------------------------------------------------------------
    status_parser = subparsers.add_parser(
        "status", parents=[parent], help="one screen of gotdocs state"
    )
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=cmd_status)

    # install --------------------------------------------------------------
    install_parser = subparsers.add_parser(
        "install", parents=[parent], help="install the pre-commit hook"
    )
    install_parser.add_argument(
        "--force", action="store_true", help="overwrite a foreign hook, leaving a .bak"
    )
    install_parser.add_argument("--json", action="store_true")
    install_parser.set_defaults(handler=cmd_install)

    # new ------------------------------------------------------------------
    new_parser = subparsers.add_parser(
        "new", parents=[parent], help="scaffold a document from a template"
    )
    new_parser.add_argument("type", choices=sorted(TYPE_TO_ROOT))
    new_parser.add_argument(
        "id",
        metavar="ID",
        help="kebab-case document id; for `new decision` this is the title and the "
        "id is allocated as NNNN-slug",
    )
    new_parser.add_argument("--title", metavar="T")
    new_parser.add_argument(
        "--covers", metavar="GLOB", action="append", default=[], help="repeatable"
    )
    new_parser.add_argument(
        "--symptom",
        metavar="TEXT",
        action="append",
        default=[],
        help="decision records only: an observable behaviour this record explains "
        "(repeatable; this is what `gotdocs why` searches)",
    )
    new_parser.add_argument("--json", action="store_true")
    new_parser.set_defaults(handler=cmd_new)

    # why ------------------------------------------------------------------
    why_parser = subparsers.add_parser(
        "why",
        parents=[parent],
        help="is this behaviour an intentional decision, or a bug?",
    )
    why_parser.add_argument(
        "query",
        nargs="*",
        metavar="TEXT",
        help="what you actually observed, in your own words",
    )
    why_parser.add_argument(
        "--path",
        metavar="PATH",
        help="restrict to decisions whose 'covers' match this file",
    )
    why_parser.add_argument(
        "--limit",
        type=int,
        default=decisions_module.DEFAULT_LIMIT,
        metavar="N",
        help="how many records to print (0 for all; default %d)"
        % (decisions_module.DEFAULT_LIMIT,),
    )
    why_parser.add_argument(
        "--all",
        action="store_true",
        help="search rejected and superseded records too (they are excluded by "
        "default: they are not in force)",
    )
    why_parser.add_argument(
        "--full", action="store_true", help="do not clip the two sections to one line"
    )
    why_parser.add_argument("--json", action="store_true")
    why_parser.set_defaults(handler=cmd_why)

    # export ---------------------------------------------------------------
    export_parser = subparsers.add_parser(
        "export",
        parents=[parent],
        help="render the documents into a static site generator's conventions",
    )
    export_parser.add_argument(
        "--target",
        metavar="NAME",
        choices=export_module.target_names(),
        help="one of: %s (default: publish.target)" % (", ".join(export_module.target_names()),),
    )
    export_parser.add_argument(
        "--out", metavar="DIR", help="output directory (default: publish.out_dir)"
    )
    export_parser.add_argument(
        "--url-prefix", metavar="PREFIX", help="site path the export is served under"
    )
    export_parser.add_argument(
        "--source-url", metavar="URL", help="base URL for links that point at code"
    )
    export_parser.add_argument("--layout", metavar="NAME", help="Jekyll layout name")
    export_parser.add_argument(
        "--include-drafts", action="store_true", help="export status: draft documents too"
    )
    export_parser.add_argument(
        "--clean", action="store_true", help="delete files in the output tree the export did not write"
    )
    export_parser.add_argument(
        "--dry-run", action="store_true", help="render and report, write nothing"
    )
    export_parser.add_argument(
        "--list-targets", action="store_true", help="print the supported targets and exit"
    )
    export_parser.add_argument("--json", action="store_true")
    export_parser.set_defaults(handler=cmd_export)

    # debt -----------------------------------------------------------------
    debt_parser = subparsers.add_parser(
        "debt",
        parents=[parent],
        help="the ledger of findings that were knowingly deferred",
    )
    debt_parser.set_defaults(handler=_debt_help_handler(debt_parser))
    debt_sub = debt_parser.add_subparsers(dest="debt_command", metavar="<subcommand>")

    record_parser = debt_sub.add_parser(
        "record", parents=[parent], help="add the current findings to the ledger"
    )
    record_source = record_parser.add_mutually_exclusive_group()
    record_source.add_argument("--staged", action="store_true", help="findings from `git diff --cached`")
    record_source.add_argument("--base", metavar="REF", help="findings from `git diff REF...HEAD`")
    record_source.add_argument("--paths", metavar="PATH", nargs="+", help="findings for this literal path list")
    record_parser.add_argument(
        "--source",
        choices=DEBT_SOURCES,
        default="manual",
        help="who recorded this (stored as the entry note); default manual",
    )
    record_parser.add_argument("--note", metavar="TEXT", help="override the note stored on new entries")
    record_parser.add_argument("--kinds", metavar="KIND[,KIND]", help="record only these finding kinds")
    record_parser.add_argument(
        "--resolve-absent",
        action="store_true",
        help="also close open entries this run no longer reports (scoped to the paths it examined)",
    )
    record_parser.add_argument("--date", metavar="YYYY-MM-DD", help="override the commit date stamped on entries")
    record_parser.add_argument("--sha", metavar="SHA", help="override the sha stamped on entries")
    record_parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    record_parser.add_argument("--json", action="store_true")
    record_parser.set_defaults(handler=cmd_debt_record)

    list_parser = debt_sub.add_parser("list", parents=[parent], help="list ledger entries")
    list_parser.add_argument("--status", choices=debt_module.STATUSES, help="default: open")
    list_parser.add_argument("--all", action="store_true", help="open and resolved entries")
    list_parser.add_argument("--kind", metavar="KIND")
    list_parser.add_argument("--doc", metavar="DOC-ID")
    list_parser.add_argument("--path", metavar="PATH")
    list_parser.add_argument("--limit", type=int, metavar="N", help="0 for all")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=cmd_debt_list)

    resolve_parser = debt_sub.add_parser(
        "resolve", parents=[parent], help="close entries by id, doc id or path"
    )
    resolve_parser.add_argument("refs", nargs="*", metavar="REF")
    resolve_parser.add_argument(
        "--auto",
        action="store_true",
        help="close every open entry the current check no longer reports",
    )
    resolve_auto_source = resolve_parser.add_mutually_exclusive_group()
    resolve_auto_source.add_argument(
        "--staged", action="store_true", help="with --auto: findings from `git diff --cached`"
    )
    resolve_auto_source.add_argument(
        "--base", metavar="REF", help="with --auto: findings from `git diff REF...HEAD`"
    )
    resolve_auto_source.add_argument(
        "--paths", metavar="PATH", nargs="+", help="with --auto: findings for this literal path list"
    )
    resolve_parser.add_argument("--note", metavar="TEXT", help="why it was closed")
    resolve_parser.add_argument("--date", metavar="YYYY-MM-DD")
    resolve_parser.add_argument("--sha", metavar="SHA")
    resolve_parser.add_argument("--json", action="store_true")
    resolve_parser.set_defaults(handler=cmd_debt_resolve)

    render_parser = debt_sub.add_parser(
        "render", parents=[parent], help="write the human report (default .gotdocs/DEBT.md)"
    )
    render_parser.add_argument("--out", metavar="PATH", help="repo-relative output path")
    render_parser.add_argument("--limit", type=int, metavar="N", help="max lines per finding kind")
    render_parser.add_argument("--stdout", action="store_true", help="print the report instead of writing it")
    render_parser.add_argument("--json", action="store_true")
    render_parser.set_defaults(handler=cmd_debt_render)

    stats_parser = debt_sub.add_parser("stats", parents=[parent], help="ledger totals")
    stats_parser.add_argument("--json", action="store_true")
    stats_parser.set_defaults(handler=cmd_debt_stats)

    ci_parser = subparsers.add_parser(
        "ci",
        parents=[parent],
        help="set up and verify continuous integration",
    )
    ci_parser.set_defaults(handler=_ci_help_handler(ci_parser))
    ci_sub = ci_parser.add_subparsers(dest="ci_command", metavar="<subcommand>")

    doctor_parser = ci_sub.add_parser(
        "doctor",
        parents=[parent],
        help="check the CI prerequisites that do not live in the workflow file",
        description=(
            "Verify the repository state the workflow cannot declare for itself: "
            "GITHUB_TOKEN write permission (the usual cause of a ledger push "
            "failing after everything else passed), branch protection, whether "
            "the workflow triggers on the real default branch, and whether "
            "bin/gotdocs is committed executable. Remote checks need an "
            "authenticated `gh`; without one they report 'unknown' and print "
            "the click path instead."
        ),
    )
    doctor_parser.add_argument(
        "--apply",
        action="store_true",
        help="fix what can be fixed without a human (needs `gh` for the token setting)",
    )
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=cmd_ci_doctor)

    init_parser = ci_sub.add_parser(
        "init",
        parents=[parent],
        help="write or refresh the CI definition for this repo",
    )
    init_parser.add_argument(
        "--provider",
        choices=ci_module.PROVIDERS,
        default=ci_module.GITHUB,
        help="default: github",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing definition"
    )
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(handler=cmd_ci_init)

    return parser


def _ci_help_handler(ci_parser):
    def handler(context):
        ci_parser.print_help(context.out)
        return EXIT_OK

    return handler


def _debt_help_handler(debt_parser):
    def handler(context):
        debt_parser.print_help(context.out)
        return EXIT_OK

    return handler


# ---------------------------------------------------------------------------
# shared per-invocation state
# ---------------------------------------------------------------------------


class Context(object):
    """Resolved repo root, config and output helpers for one invocation."""

    def __init__(self, args, out, err):
        self.args = args
        self.out = out
        self.err = err
        self._root = None
        self._config = None
        self._repo = None
        self.palette = report.Palette(self._color_enabled())

    def _color_enabled(self):
        if getattr(self.args, "no_color", False):
            return False
        if os.environ.get("NO_COLOR") is not None:
            return False
        isatty = getattr(self.out, "isatty", None)
        return bool(isatty and isatty())

    @property
    def quiet(self):
        return bool(getattr(self.args, "quiet", False))

    @property
    def json_mode(self):
        return bool(getattr(self.args, "json", False))

    @property
    def root(self):
        if self._root is None:
            self._root = _resolve_root(getattr(self.args, "repo", None))
        return self._root

    @property
    def config(self):
        if self._config is None:
            self._config = config_module.load(self.root)
        return self._config

    @property
    def repo(self):
        if self._repo is None:
            self._repo = gitutil.GitRepo(self.root)
        return self._repo

    def write(self, text):
        if text:
            self.out.write(text)

    def note(self, text):
        if not self.quiet:
            self.err.write("%s: %s\n" % (PROGRAM, text))


def _resolve_root(explicit):
    """Resolve the repository root, preferring git, falling back to `.gotdocs`."""
    start = os.environ.get("GOTDOCS_CWD") or os.getcwd()
    if explicit:
        start = os.path.abspath(os.path.expanduser(explicit))
        if not os.path.isdir(start):
            raise UsageError("--repo is not a directory: %s" % (explicit,))
    try:
        return gitutil.find_repo_root(start)
    except NotAGitRepoError:
        found = _find_gotdocs_dir(start)
        if found is not None:
            return found
        raise
    except GitError:
        found = _find_gotdocs_dir(start)
        if found is not None:
            return found
        raise


def _find_gotdocs_dir(start):
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, config_module.CONFIG_DIR)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _cwd():
    return os.environ.get("GOTDOCS_CWD") or os.getcwd()


def _cwd_prefix(root):
    """The repo-relative path of the cwd, or ``None`` when it is the root itself.

    ``None`` also covers a cwd outside the repository (``--repo`` was pointed
    somewhere else), where cwd-relative resolution would be meaningless.
    """
    try:
        cwd_real = os.path.realpath(os.path.abspath(_cwd()))
        root_real = os.path.realpath(os.path.abspath(root))
    except OSError:  # pragma: no cover - cwd was deleted underneath us
        return None
    relative = os.path.relpath(cwd_real, root_real)
    if relative == os.curdir:
        return None
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    return relative.replace(os.sep, "/")


def _repo_relative(root, raw):
    """Turn one user-supplied path into a repo-relative, ``/``-separated path.

    ``--paths`` and ``impacted`` are the interface agents use for files they are
    about to touch, and an agent naturally has absolute paths. A path that is
    absolute, or that walks up out of the current directory, is resolved against
    the process cwd and re-expressed relative to the repository root. Anything
    that lands outside the repository is a usage error rather than a silent
    "no documents cover this path".

    A plain relative path is resolved against the cwd too, which is what git
    does with a pathspec and what a caller in a subdirectory means. ``cd src &&
    gotdocs impacted app.py`` used to be read as the repo-root path ``app.py``
    and answer "no document covers this" -- the exact silent-wrong-answer this
    function exists to prevent. When the cwd *is* the repository root the two
    spellings coincide and nothing changes; when a cwd-relative path names
    nothing on disk but the repo-root spelling does, the repo-root spelling
    wins, so a script that passes repo-relative paths from a subdirectory keeps
    working.
    """
    text = str(raw)
    parts = text.replace(os.sep, "/").split("/")
    if not os.path.isabs(text) and ".." not in parts and not text.startswith("~"):
        plain = globs.normalize_path(text)
        prefix = _cwd_prefix(root)
        if not prefix:
            return plain
        from_cwd = globs.normalize_path("%s/%s" % (prefix, plain))
        if _exists_in(root, from_cwd) or not _exists_in(root, plain):
            return from_cwd
        return plain

    absolute = os.path.expanduser(text)
    if not os.path.isabs(absolute):
        absolute = os.path.join(_cwd(), absolute)
    absolute = os.path.realpath(os.path.abspath(absolute))
    root_real = os.path.realpath(os.path.abspath(root))
    relative = os.path.relpath(absolute, root_real)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise UsageError(
            "path is outside the repository %s: %s" % (root_real, raw)
        )
    if relative == os.curdir:
        raise UsageError("path is the repository root itself: %s" % (raw,))
    return globs.normalize_path(relative.replace(os.sep, "/"))


def _exists_in(root, relative):
    return os.path.exists(os.path.join(root, relative.replace("/", os.sep)))


def _repo_relative_paths(root, paths):
    return [_repo_relative(root, path) for path in paths if path]


def _today():
    return datetime.date.today().isoformat()


def _abs(root, rel_path):
    return os.path.join(root, rel_path.replace("/", os.sep))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_check(context):
    args = context.args

    paths = args.paths
    if paths:
        paths = _repo_relative_paths(context.root, paths)

    if args.paths:
        source = check_module.SOURCE_PATHS
    elif args.base:
        source = check_module.SOURCE_BASE
    else:
        source = check_module.SOURCE_STAGED

    message = args.message
    if message is None and args.message_file:
        message = _read_message_file(args.message_file)

    try:
        result = check_module.run_check(
            context.root,
            context.config,
            source=source,
            base=args.base,
            paths=paths,
            mode=args.mode,
            message=message,
            repo=None if source == check_module.SOURCE_PATHS else context.repo,
        )
    except GotdocsError as exc:
        if context.json_mode:
            context.write(
                report.dumps(
                    report.error_envelope(exc.exit_code, _message_of(exc), args.mode or "error")
                )
            )
            return exc.exit_code
        raise

    if context.json_mode:
        context.write(report.render_check_json(result))
    else:
        context.write(report.render_check_text(result, context.palette, context.quiet))
    return result.exit_code()


def _read_message_file(path):
    try:
        with io.open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except (IOError, OSError):
        # A missing message file is normal in some hook flows; it just means we
        # cannot look for a skip token.
        return None


def cmd_impacted(context):
    paths = _repo_relative_paths(context.root, context.args.paths)
    entries = check_module.impacted_for_paths(context.root, context.config, paths)
    if context.json_mode:
        context.write(report.render_impacted_json(entries))
    else:
        context.write(report.render_impacted_text(entries, context.palette))
    return EXIT_OK


def cmd_verify(context):
    args = context.args
    config = context.config
    root = context.root

    head = context.repo.head_sha_or_none(short=True)
    if head is None:
        raise EmptyRepoError(
            "repository has no commits yet, so there is no sha to record in verified_at"
        )

    doc_set = index_module.scan(root, config)
    by_id = doc_set.by_id()
    by_path = doc_set.by_path()

    if args.all_impacted:
        if args.doc_ids:
            raise UsageError("--all-impacted takes the doc ids from `check`; do not also name them")
        result = check_module.run_check(
            root,
            config,
            source=check_module.SOURCE_STAGED,
            repo=context.repo,
            doc_set=doc_set,
            mode="warn",
        )
        targets = []
        seen = set()
        for finding in result.findings:
            if finding.kind != check_module.KIND_STALE:
                continue
            key = finding.doc_id or finding.path
            if key in seen:
                continue
            seen.add(key)
            targets.append(key)
        if not targets:
            if not context.quiet:
                context.write("gotdocs: nothing impacted, nothing to verify\n")
            if context.json_mode:
                context.write(report.dumps({"ok": True, "verified": [], "head": head}))
            return EXIT_OK
    else:
        targets = list(args.doc_ids)
        if not targets:
            raise UsageError("verify needs at least one doc id, or --all-impacted")

    docs = []
    for target in targets:
        doc = by_id.get(target) or by_path.get(globs.normalize_path(target))
        if doc is None:
            raise DocNotFoundError(
                "no document with id %r; run `bin/gotdocs lint` or `bin/gotdocs status`"
                % (target,)
            )
        docs.append(doc)

    today = _today()
    verified = []
    for doc in docs:
        changed = fm_module.rewrite_fields(
            _abs(root, doc.path),
            {"updated": today, "verified_at": head},
            rel_path=doc.path,
        )
        verified.append({"doc_id": doc.display_id, "path": doc.path, "changed": changed})

    if context.json_mode:
        context.write(report.dumps({"ok": True, "head": head, "verified": verified}))
    elif not context.quiet:
        for item in verified:
            context.write(
                "verified %s  %s  verified_at=%s updated=%s\n"
                % (item["doc_id"], item["path"], head, today)
            )
        context.write(
            "Now run: bin/gotdocs index && git add %s\n"
            % (" ".join(i["path"] for i in verified),)
        )
    return EXIT_OK


def cmd_index(context):
    head = None
    try:
        head = context.repo.head_sha_or_none(short=True)
    except GitError:
        head = None
    doc_set, changed = index_module.write_index(context.root, context.config, head_sha=head)
    if context.json_mode:
        context.write(report.render_index_json(len(doc_set.docs), changed))
    else:
        context.write(
            report.render_index_text(len(doc_set.docs), changed, context.palette, context.quiet)
        )
    return EXIT_OK


def cmd_lint(context):
    args = context.args
    config = context.config
    doc_set = index_module.scan(context.root, config)
    findings = check_module._lint_findings(doc_set)
    findings.extend(check_module._duplicate_findings(doc_set))
    findings.extend(_decision_findings(context, doc_set))
    for pattern, message in config.bad_ignore_patterns():
        findings.append(
            check_module.Finding(
                check_module.KIND_LINT,
                config.path or config_module.CONFIG_PATH,
                "%s: invalid ignore pattern %r: %s"
                % (config.path or config_module.CONFIG_PATH, pattern, message),
                "fix the 'ignore' list in %s" % (config.path or config_module.CONFIG_PATH,),
            )
        )
    findings = _dedupe_findings(findings)
    findings.sort(key=lambda finding: finding.sort_key())

    warnings = []
    warnings.extend(_rotted_cover_warnings(context, doc_set))
    if getattr(args, "portability", False):
        issues = _portability_issues(context, doc_set)
        # Warnings by default: a document that renders oddly on one of six
        # generators is worth saying out loud, but it is not worth failing a
        # commit that has nothing to do with it. `--strict` is the opt-in.
        promoted = bool(getattr(args, "strict", False))
        converted = portability.as_findings(issues, kind="portability")
        if promoted:
            findings.extend(converted)
            findings.sort(key=lambda finding: finding.sort_key())
        else:
            warnings = converted

    if context.json_mode:
        context.write(report.render_lint_json(findings, not findings, warnings))
    else:
        context.write(
            report.render_lint_text(
                findings, len(doc_set.docs), context.palette, context.quiet, warnings
            )
        )
    return EXIT_USAGE if findings else EXIT_OK


def _rotted_cover_warnings(context, doc_set):
    """Warn about ``covers`` globs that match no file in the repository.

    A glob that matches nothing is indistinguishable from a document nobody
    touches: `check` never marks it impacted, so it rots in total silence. The
    common cause is a path that really contains glob metacharacters -- a Next.js
    or SvelteKit route segment like ``app/posts/[id]/page.tsx``, where ``[id]``
    is read as a character class matching one of "i" or "d" and therefore
    matches nothing. Escape it: ``app/posts/\\[id\\]/page.tsx``.

    This is a warning, never a finding: a document may legitimately name a path
    that is about to exist, and upgrading gotdocs should not start failing a
    commit that has nothing to do with the stale glob.
    """
    repo = context.repo
    if repo is None or not getattr(repo, "exists", True):
        return []
    try:
        paths = repo.tracked_files()
    except Exception:
        return []
    if not paths:
        return []
    warnings = []
    for doc in doc_set.docs:
        for pattern in doc.covers:
            try:
                compiled = globs.compile_pattern(pattern)
            except Exception:
                continue  # already reported by the frontmatter lint pass
            if any(compiled.match(path) for path in paths):
                continue
            hint = "no tracked file matches it"
            if "[" in pattern:
                hint = (
                    "no tracked file matches it -- '[...]' is a character class; "
                    "escape a literal bracket as '\\[' and '\\]'"
                )
            warnings.append(
                check_module.Finding(
                    check_module.KIND_LINT,
                    doc.path,
                    "%s: covers %r matches nothing (%s)" % (doc.path, pattern, hint),
                    "fix the glob in %s, or drop it if the code is gone" % (doc.path,),
                    doc_id=doc.id,
                )
            )
    return warnings


def _dedupe_findings(findings):
    """Drop exact repeats: two validators can report the same problem."""
    seen = set()
    result = []
    for finding in findings:
        key = (finding.kind, finding.path, finding.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def _decision_findings(context, doc_set):
    """Run the ADR rules over every decision record in *doc_set*."""
    records = _records_from(doc_set, context.config.decisions_root)
    if not records:
        return []
    findings = []
    for issue in decisions_module.validate(records, root=context.config.decisions_root):
        findings.append(
            check_module.Finding(
                check_module.KIND_LINT,
                issue.path,
                "%s: %s" % (_issue_location(issue), issue.message),
                "fix the decision record %s; see .gotdocs/templates/decision.md"
                % (issue.path,),
                doc_id=None,
            )
        )
    return findings


def _records_from(doc_set, decisions_root):
    """Adapt the already-scanned decision documents into Decision records.

    Selected by *root*, not by ``type``: a file under ``decisions/`` that forgot
    ``type: decision`` is exactly the file that most needs the ADR rules run
    over it, and selecting on the type it is missing would let it through.

    ``issues`` is cleared: the frontmatter parse problems on each document were
    already turned into ``lint`` findings by the scan, and reporting them twice
    would double every count.
    """
    records = []
    for doc in doc_set.docs:
        if doc.root != decisions_root and doc.type != decisions_module.DECISION_TYPE:
            continue
        record = decisions_module.from_doc(doc, root=doc.root)
        record.issues = []
        records.append(record)
    return records


def _issue_location(issue):
    if issue.line is None:
        return issue.path
    return "%s:%d" % (issue.path, issue.line)


def _portability_issues(context, doc_set):
    args = context.args
    targets = _split_list(getattr(args, "targets", None))
    if targets:
        unknown = [name for name in targets if name not in portability.TARGETS]
        if unknown:
            raise UsageError(
                "unknown portability target(s) %s; expected one of %s"
                % (", ".join(unknown), ", ".join(portability.TARGETS))
            )
    rules = _split_list(getattr(args, "rules", None))
    if rules:
        unknown = [name for name in rules if name not in portability.RULES_BY_NAME]
        if unknown:
            raise UsageError(
                "unknown portability rule(s) %s; expected one of %s"
                % (", ".join(unknown), ", ".join(portability.rule_names()))
            )
    issues = portability.check_doc_set(
        context.root, context.config, doc_set=doc_set, targets=targets or None
    )
    if rules:
        issues = portability.filter_issues(issues, rules=rules)
    return issues


def _split_list(raw):
    if not raw:
        return []
    return [part.strip() for part in raw.replace(" ", ",").split(",") if part.strip()]


def cmd_status(context):
    root = context.root
    config = context.config
    head = context.repo.head_sha_or_none(short=True)
    doc_set = index_module.scan(root, config)
    index_ok, stale = index_module.index_is_current(root, config, doc_set, head)

    state = {
        "version": VERSION,
        "repo": root,
        "head": head,
        "config": config.path if config.exists else "%s (missing, using defaults)" % (config_module.CONFIG_PATH,),
        "roots": list(config.roots),
        "doc_count": len(doc_set.docs),
        "status_counts": doc_set.counts_by_status(),
        "index": "%s up to date" % (config_module.INDEX_JSON_PATH,)
        if index_ok
        else "out of date: %s — run: bin/gotdocs index" % (", ".join(stale),),
        "enforce": {
            "pre_commit": config.mode_for("pre_commit"),
            "pre_push": config.mode_for("pre_push"),
            "ci": config.mode_for("ci"),
        },
        "require_coverage": config.require_coverage,
        "hook": _hook_state(root, context.repo),
        "lint_errors": len(doc_set.issues) + len(doc_set.duplicate_ids),
    }

    if context.json_mode:
        context.write(report.dumps({"ok": True, "status": state}))
    else:
        context.write(report.render_status_text(state, context.palette))
    return EXIT_OK


def _hook_state(root, repo):
    source = os.path.join(root, config_module.HOOK_SOURCE_PATH)
    try:
        git_dir = repo.git_dir()
    except GitError:
        return "unknown (not a git repository)"
    installed = os.path.join(git_dir, "hooks", "pre-commit")
    if not os.path.exists(installed):
        return ".git/hooks/pre-commit not installed — run: bin/gotdocs install"
    installed_bytes = _read_bytes(installed)
    if not os.path.exists(source):
        if installed_bytes and b"gotdocs" in installed_bytes:
            return ".git/hooks/pre-commit installed (gotdocs)"
        return ".git/hooks/pre-commit installed (not gotdocs')"
    if installed_bytes == _read_bytes(source):
        return ".git/hooks/pre-commit installed (matches %s)" % (config_module.HOOK_SOURCE_PATH,)
    if installed_bytes and b"gotdocs" in installed_bytes:
        return ".git/hooks/pre-commit installed but differs from %s — run: bin/gotdocs install --force" % (
            config_module.HOOK_SOURCE_PATH,
        )
    return ".git/hooks/pre-commit is a foreign hook — run: bin/gotdocs install --force"


def _read_bytes(path):
    try:
        with io.open(path, "rb") as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def cmd_install(context):
    root = context.root
    source = os.path.join(root, config_module.HOOK_SOURCE_PATH)
    if not os.path.isfile(source):
        raise GotdocsError(
            "%s does not exist; nothing to install" % (config_module.HOOK_SOURCE_PATH,)
        )
    git_dir = context.repo.git_dir()
    hooks_dir = os.path.join(git_dir, "hooks")
    if not os.path.isdir(hooks_dir):
        os.makedirs(hooks_dir)
    target = os.path.join(hooks_dir, "pre-commit")

    source_bytes = _read_bytes(source)
    backup = None
    if os.path.exists(target):
        existing = _read_bytes(target)
        if existing == source_bytes:
            if context.json_mode:
                context.write(
                    report.dumps({"ok": True, "installed": False, "reason": "already up to date"})
                )
            elif not context.quiet:
                context.write("gotdocs: pre-commit hook already up to date\n")
            return EXIT_OK
        is_gotdocs = bool(existing and b"gotdocs" in existing)
        if not is_gotdocs and not context.args.force:
            raise GotdocsError(
                "a non-gotdocs pre-commit hook already exists at %s; chain it manually "
                "or re-run with --force" % (target,)
            )
        if context.args.force or not is_gotdocs:
            backup = target + ".bak"
            shutil.copyfile(target, backup)

    with io.open(target, "wb") as handle:
        handle.write(source_bytes)
    mode = os.stat(target).st_mode
    os.chmod(target, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if context.json_mode:
        context.write(report.dumps({"ok": True, "installed": True, "backup": backup}))
    elif not context.quiet:
        if backup:
            context.write("gotdocs: previous hook saved to %s\n" % (backup,))
        context.write("gotdocs: installed %s -> %s\n" % (config_module.HOOK_SOURCE_PATH, target))
    return EXIT_OK


def cmd_new(context):
    args = context.args
    root = context.root
    config = context.config

    is_decision = args.type == decisions_module.DECISION_TYPE
    if args.symptom and not is_decision:
        raise UsageError("--symptom applies to `new decision` only")

    target_root = TYPE_TO_ROOT[args.type]
    if is_decision:
        target_root = config.decisions_root
    if target_root not in config.roots:
        for candidate in config.roots:
            if globs.normalize_path(candidate) == target_root:
                target_root = candidate
                break
    target_root = globs.normalize_path(target_root)

    title = args.title
    if is_decision:
        # `new decision "Retry budget is per request"` -- the positional is the
        # title, and the number is allocated from the filenames already on disk
        # (never reused, never inferred from frontmatter).
        slug = _slugify(args.id)
        if not slug:
            raise UsageError(
                "a decision title must contain at least one letter or digit: %r" % (args.id,)
            )
        number = decisions_module.next_number(root, target_root)
        doc_id = "%s-%s" % (number, slug)
        if len(doc_id) > index_module._ID_MAX:
            doc_id = doc_id[: index_module._ID_MAX].rstrip("-")
        if title is None:
            title = args.id.strip()
    else:
        doc_id = args.id

    if not index_module._ID_RE.match(doc_id) or len(doc_id) > index_module._ID_MAX:
        raise UsageError(
            "id %r must be kebab-case matching [a-z0-9][a-z0-9-]* and at most %d characters"
            % (doc_id, index_module._ID_MAX)
        )

    for pattern in args.covers:
        globs.validate_pattern(pattern)

    doc_set = index_module.scan(root, config)
    if doc_id in doc_set.by_id():
        raise UsageError(
            "id %r is already used by %s" % (doc_id, doc_set.by_id()[doc_id].path)
        )

    rel_path = "%s/%s.md" % (target_root, doc_id)
    absolute = _abs(root, rel_path)
    if os.path.exists(absolute):
        raise UsageError("%s already exists" % (rel_path,))

    template_path = os.path.join(root, config_module.TEMPLATE_DIR, "%s.md" % (args.type,))
    if os.path.isfile(template_path):
        text = fm_module.read_text(template_path)
    else:
        text = _fallback_template(args.type)

    title = title or _default_title(args.type, doc_id)
    lists = [("covers", list(args.covers), False)]
    if is_decision:
        lists.append(("symptoms", list(args.symptom), True))
    # `verified_at` is deliberately left at the template's placeholder. Stamping
    # HEAD here would assert "somebody read this against the code" about an
    # empty scaffold, and it is exactly what the audit skill's `never-verified`
    # detection keys off. Run `bin/gotdocs verify <id>` once the doc is real.
    filled = _fill_template(
        text,
        doc_id=doc_id,
        doc_type=args.type,
        title=title,
        covers=list(args.covers),
        updated=_today(),
        verified_at=None,
        rel_path=rel_path,
        lists=lists,
    )

    directory = os.path.dirname(absolute)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(absolute, "wb") as handle:
        handle.write(filled.encode("utf-8"))

    if context.json_mode:
        context.write(
            report.dumps({"ok": True, "path": rel_path, "id": doc_id, "type": args.type})
        )
    elif not context.quiet:
        context.write("gotdocs: created %s\n" % (rel_path,))
        if is_decision:
            context.write(
                "gotdocs: fill in 'symptoms', 'Expected behavior' and "
                "'This is a bug, not this decision, if...',\n"
                "         set status: accepted when it is agreed, then run: bin/gotdocs index\n"
            )
        else:
            context.write("gotdocs: fill it in, then run: bin/gotdocs index\n")
    return EXIT_OK


def _slugify(text):
    """Kebab-case slug for a decision filename: ``Retry budget`` -> ``retry-budget``."""
    lowered = str(text).strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", lowered).strip("-")
    return slug


def _default_title(doc_type, doc_id):
    words = " ".join(word.capitalize() for word in doc_id.split("-"))
    if doc_type == "runbook":
        return "Runbook: %s" % (words,)
    if doc_type == "onboarding":
        return "Onboarding — %s" % (words,)
    if doc_type == "dependency":
        return "Dependency: %s" % (words,)
    if doc_type == decisions_module.DECISION_TYPE:
        return words
    return words


def _fallback_template(doc_type):
    """The scaffold used when ``.gotdocs/templates/<type>.md`` is not vendored.

    Type-aware on purpose. A decision record has a different status enum and two
    load-bearing body sections, so a generic scaffold would produce a file that
    fails ``gotdocs lint`` the moment it is marked accepted -- and the whole
    point of the scaffold is that it is correct before anybody edits it.
    """
    if doc_type == decisions_module.DECISION_TYPE:
        return (
            "---\n"
            "id: replace-me\n"
            "title: Replace Me\n"
            "type: decision\n"
            "summary: One sentence naming the decision and the tradeoff it made.\n"
            "covers: []\n"
            "symptoms: []\n"
            "supersedes: []\n"
            "superseded_by: []\n"
            "owners: []\n"
            "tags: []\n"
            "status: proposed\n"
            "decided_on: 1970-01-01\n"
            "updated: 1970-01-01\n"
            "verified_at: 0000000\n"
            "---\n"
            "\n"
            "# Replace Me\n"
            "\n"
            "## Context\n"
            "\n"
            "What was true when this was decided.\n"
            "\n"
            "## Decision\n"
            "\n"
            "What was decided, in the present tense.\n"
            "\n"
            "## Expected behavior\n"
            "\n"
            "The observable consequences. `bin/gotdocs why` quotes this section.\n"
            "\n"
            "## This is a bug, not this decision, if...\n"
            "\n"
            "What this decision does NOT explain, and therefore needs investigating.\n"
            "\n"
            "## Consequences\n"
            "\n"
            "What this costs and what was given up.\n"
        )
    return (
        "---\n"
        "id: replace-me\n"
        "title: Replace Me\n"
        "type: %s\n"
        "summary: One sentence describing this document. Max 200 characters.\n"
        "covers: []\n"
        "owners: []\n"
        "tags: []\n"
        "status: draft\n"
        "updated: 1970-01-01\n"
        "verified_at: 0000000\n"
        "---\n"
        "\n"
        "# Replace Me\n"
        "\n"
        "Write the document here.\n" % (doc_type,)
    )


def _fill_template(
    text, doc_id, doc_type, title, covers, updated, verified_at, rel_path, lists=None
):
    """Fill a scaffold's frontmatter.

    Unlike :func:`frontmatter.rewrite_fields` this rewrites identity fields as
    well, because the file is brand new and nothing downstream has seen it yet.

    *lists* is an ordered ``[(key, values)]`` of block-list fields to write;
    it defaults to just ``covers``. Order matters: a key the template does not
    already contain is appended in this order, so two runs of the same command
    produce byte-identical files.
    """
    list_fields = list(lists) if lists is not None else [("covers", list(covers), False)]
    list_values = dict((key, (values, quoted)) for key, values, quoted in list_fields)
    lines = text.splitlines(True)
    if not lines or lines[0].rstrip("\r\n") != fm_module.DELIMITER:
        raise GotdocsError("template %s has no frontmatter block" % (rel_path,))
    close_index = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == fm_module.DELIMITER:
            close_index = index
            break
    if close_index is None:
        raise GotdocsError("template for %s has unterminated frontmatter" % (rel_path,))

    eol = "\r\n" if lines[0].endswith("\r\n") else "\n"

    scalars = {
        "id": doc_id,
        "type": doc_type,
        "title": title,
        "updated": updated,
    }
    if verified_at:
        scalars["verified_at"] = verified_at

    out = [lines[0]]
    seen = set()
    index = 1
    while index < close_index:
        raw = lines[index]
        stripped = raw.rstrip("\r\n")
        key = _key_of(stripped)
        if key in list_values:
            seen.add(key)
            out.extend(_render_list(key, list_values[key][0], eol, list_values[key][1]))
            index += 1
            while index < close_index and _is_list_item(lines[index]):
                index += 1
            continue
        if key in scalars:
            seen.add(key)
            quote = _quote_of(stripped)
            out.append("%s: %s%s" % (key, fm_module.render_scalar(scalars[key], quote), eol))
            index += 1
            continue
        out.append(raw)
        index += 1

    for key in ("id", "title", "type", "updated", "verified_at"):
        if key in scalars and key not in seen:
            out.append("%s: %s%s" % (key, fm_module.render_scalar(scalars[key]), eol))
    for key, values, quoted in list_fields:
        if key not in seen:
            out.extend(_render_list(key, values, eol, quoted))

    out.append(lines[close_index])
    out.extend(lines[close_index + 1 :])

    body = "".join(out)
    if title:
        body = _replace_first_heading(body, title, close_index)
    return body


def _render_list(key, values, eol, quoted=False):
    if not values:
        return ["%s: []%s" % (key, eol)]
    rendered = ["%s:%s" % (key, eol)]
    for value in values:
        quote = _quote_for(value) if quoted else None
        rendered.append("  - %s%s" % (fm_module.render_scalar(value, quote), eol))
    return rendered


def _quote_for(value):
    """Double-quote a free-prose list item when leaving it bare would misparse.

    Symptom lines are prose typed on the command line, so
    ``- a POST is retried: twice`` has to survive the round trip. Glob patterns
    never take this path: quoting ``src/**`` would churn the diff of every
    document that already writes it bare.
    """
    text = "" if value is None else str(value)
    if text == "":
        return '"'
    if ": " in text or text.endswith(":") or " #" in text:
        return '"'
    if text[0] in "-?:,[]{}#&*!|>'\"%@`" or text[0].isspace() or text[-1].isspace():
        return '"'
    return None


def _key_of(line):
    match = fm_module._KEY_RE.match(line)
    return match.group(1) if match else None


def _quote_of(line):
    match = fm_module._KEY_RE.match(line)
    if not match:
        return None
    value, _comment = fm_module._split_comment(match.group(2).strip())
    value = value.strip()
    return value[0] if value[:1] in ("'", '"') else None


def _is_list_item(line):
    stripped = line.rstrip("\r\n")
    return stripped[:1] in (" ", "\t") and stripped.strip().startswith("-")


def _replace_first_heading(text, title, skip_lines):
    lines = text.splitlines(True)
    for index in range(skip_lines, len(lines)):
        if lines[index].startswith("# "):
            eol = "\r\n" if lines[index].endswith("\r\n") else "\n"
            lines[index] = "# %s%s" % (title, eol)
            break
    return "".join(lines)


# ---------------------------------------------------------------------------
# why: the decision lookup
# ---------------------------------------------------------------------------


def cmd_why(context):
    """Answer "is this behaviour intentional?" from the decision records.

    Never an error: a repository with no decisions, or a query nothing matches,
    is the common case and exits 0 with an explicit "nothing was written down".
    Making it exit non-zero would put a hard failure in the middle of an agent's
    diagnosis loop for the *absence* of information.
    """
    args = context.args
    config = context.config
    root = config.decisions_root
    records = decisions_module.load(context.root, root)

    path = None
    if args.path:
        path = _repo_relative(context.root, args.path)

    query = " ".join(part for part in args.query if part).strip()
    if not query and path is None:
        raise UsageError(
            'why needs something to look up: bin/gotdocs why "requests are retried twice" '
            "or bin/gotdocs why --path src/api/client.py"
        )

    pool = records
    if not args.all:
        # rejected and superseded records are not in force; citing one as the
        # reason for current behaviour is exactly the mistake this prevents.
        pool = [record for record in pool if record.status not in ("rejected", "superseded")]
    if path is not None:
        pool = [record for record in pool if _covers_match(record, path)]

    limit = None if args.limit is not None and args.limit <= 0 else args.limit

    if query:
        matches = decisions_module.why(query, pool, limit=None)
        if context.json_mode:
            context.write(
                report.render_why_json(matches, query, path, len(pool), limit)
            )
        else:
            context.write(
                decisions_module.format_why(
                    matches, query=query, limit=limit, total=len(pool), full=args.full
                )
            )
        return EXIT_OK

    matches = [decisions_module.Match(record, 0.0) for record in pool]
    if context.json_mode:
        context.write(report.render_why_json(matches, None, path, len(records), limit))
    else:
        context.write(
            report.render_why_path_text(
                matches, path, len(records), limit, args.full, context.palette
            )
        )
    return EXIT_OK


def _covers_match(record, path):
    for pattern in record.covers:
        try:
            if globs.compile_pattern(pattern).match(path):
                return True
        except Exception:
            # Invalid patterns are a lint finding; here they simply never match.
            continue
    return False


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def cmd_export(context):
    args = context.args
    config = context.config

    if args.list_targets:
        targets = [export_module.get_target(name) for name in export_module.target_names()]
        if context.json_mode:
            context.write(
                report.dumps(
                    {"ok": True, "targets": [target.as_dict() for target in targets]}
                )
            )
        else:
            context.write(report.render_targets_text(targets, context.palette))
        return EXIT_OK

    target_name = args.target or config.publish_option("target")
    target = export_module.get_target(target_name)

    out_rel = args.out or config.publish_option("out_dir")
    if not out_rel:
        raise UsageError("export needs an output directory: --out DIR, or set publish.out_dir")
    out_dir = out_rel
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(context.root, out_rel.replace("/", os.sep))

    url_prefix = args.url_prefix
    if url_prefix is None:
        url_prefix = config.publish_option("url_prefix") or ""
    source_url = args.source_url
    if source_url is None:
        source_url = config.publish_option("source_url") or ""
    layout = args.layout or config.publish_option("layout") or None
    include_drafts = args.include_drafts or bool(config.publish_option("include_drafts"))

    kwargs = {
        "include_drafts": include_drafts,
        "url_prefix": url_prefix,
        "source_url": source_url or None,
        "layout": layout,
    }

    if args.dry_run:
        result = export_module.export_docs(context.root, config, target, **kwargs)
        result.out_dir = out_rel
    else:
        result = export_module.write_export(
            context.root, config, target, out_dir, clean=args.clean, **kwargs
        )
        result.out_dir = out_rel

    if context.json_mode:
        context.write(report.render_export_json(result, args.dry_run))
    else:
        context.write(
            report.render_export_text(result, args.dry_run, context.palette, context.quiet)
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# debt
# ---------------------------------------------------------------------------


def _debt_ledger(context):
    """Load the ledger, reporting (but never dying on) unusable lines."""
    config = context.config
    entries, errors = debt_module.load_ledger(context.root, config.debt_ledger)
    for error in errors:
        context.note(error.located(config.debt_ledger))
    return entries, errors


def _debt_stamp(context):
    """The ``(date, sha)`` every ledger write is stamped with.

    The date is HEAD's *commit* date, not today: the ledger must regenerate to
    identical bytes on any machine on any day, so nothing here may read a clock
    that the repository does not also record.
    """
    args = context.args
    date = getattr(args, "date", None)
    sha = getattr(args, "sha", None)
    if date is None or sha is None:
        head = None
        commit_date = None
        try:
            repo = context.repo
            head = repo.head_sha_or_none(short=True)
            ok, out = repo.try_run(["log", "-1", "--format=%cd", "--date=short"])
            commit_date = out.strip() if ok else None
        except GotdocsError:
            pass
        if sha is None:
            sha = head
        if date is None:
            date = commit_date or _today()
    if not _DATE_RE.match(str(date)):
        raise UsageError("--date must be an ISO date as YYYY-MM-DD, got %r" % (date,))
    return date, sha


def _debt_findings(context):
    """Run a check purely to harvest findings; enforcement mode is irrelevant."""
    args = context.args
    paths = args.paths
    if paths:
        paths = _repo_relative_paths(context.root, paths)
    if args.paths:
        source = check_module.SOURCE_PATHS
    elif args.base:
        source = check_module.SOURCE_BASE
    else:
        source = check_module.SOURCE_STAGED
    return check_module.run_check(
        context.root,
        context.config,
        source=source,
        base=args.base,
        paths=paths,
        mode="warn",
        message="",
        repo=None if source == check_module.SOURCE_PATHS else context.repo,
    )


# Finding kinds a check produces by looking at the whole repository, whatever
# the change set was. Everything else is a function of the change set and can
# only be resolved for the paths that change set actually reached.
_REPO_WIDE_KINDS = frozenset(
    (
        check_module.KIND_LINT,
        check_module.KIND_DUPLICATE_ID,
        check_module.KIND_DEPRECATED_EDIT,
        check_module.KIND_INDEX_OUT_OF_DATE,
    )
)


def _change_set_scope(result):
    """The entry paths a change-set-driven finding could have named this run.

    ``stale`` entries are keyed on the *document's* path, not on the code that
    made it stale, so the scope is the documents this run found impacted plus
    the documents the change set itself touched. ``uncovered`` entries are keyed
    on the code path, which is in ``changed_paths``. Anything outside that was
    not examined, and closing it would silently drop debt that is still real.
    """
    scope = set(result.changed_paths)
    for doc, _matches in result.impacted or []:
        scope.add(doc.path)
    return scope


def _resolve_absent(entries, findings, result, date, sha):
    """``resolve_absent`` applied with the right scope for each finding kind."""
    scoped = [entry for entry in entries if entry.kind not in _REPO_WIDE_KINDS]
    repo_wide = [entry for entry in entries if entry.kind in _REPO_WIDE_KINDS]

    scoped, resolved_scoped = debt_module.resolve_absent(
        scoped, findings, date, sha, paths=_change_set_scope(result)
    )
    repo_wide, resolved_wide = debt_module.resolve_absent(
        repo_wide, findings, date, sha, paths=None
    )
    merged = debt_module.sort_entries(scoped + repo_wide)
    return merged, resolved_scoped + resolved_wide


def cmd_debt_record(context):
    args = context.args
    config = context.config

    if not config.debt_enabled:
        if context.json_mode:
            context.write(
                report.dumps({"ok": True, "recorded": False, "reason": "debt.enabled is false"})
            )
        else:
            context.note("doc-debt recording is disabled (debt.enabled is false)")
        return EXIT_OK

    result = _debt_findings(context)
    # ``recordable`` is what this run is allowed to *open*; ``reported`` is
    # everything the check actually found. Auto-resolve must read ``reported``,
    # or a kind excluded by the filter looks absent and its open entries are
    # silently closed while the finding is still in the tree.
    reported = list(result.findings)
    recordable = reported

    kinds = _split_list(args.kinds) or config.debt_record_kinds
    if kinds:
        allowed = set(kinds)
        recordable = [finding for finding in reported if finding.kind in allowed]

    date, sha = _debt_stamp(context)
    note = args.note if args.note is not None else args.source
    entries, errors = _debt_ledger(context)

    recorded = debt_module.record_findings(entries, recordable, date, sha, note=note)
    entries = recorded.entries
    resolved = []
    if args.resolve_absent:
        entries, resolved = _resolve_absent(entries, reported, result, date, sha)

    changed = False
    if not args.dry_run:
        changed = debt_module.write_ledger(context.root, entries, config.debt_ledger)

    payload = {
        "ok": True,
        "ledger": config.debt_ledger,
        "dry_run": bool(args.dry_run),
        "written": changed,
        "date": date,
        "sha": sha,
        "source": args.source,
        "added": list(recorded.added),
        "updated": list(recorded.updated),
        "reopened": list(recorded.reopened),
        "resolved": list(resolved),
        "ledger_errors": [error.as_dict() for error in errors],
        "summary": debt_module.summarize(entries),
    }
    if context.json_mode:
        context.write(report.dumps(payload))
    else:
        context.write(report.render_debt_record_text(payload, context.palette, context.quiet))
    return EXIT_OK


def cmd_debt_list(context):
    args = context.args
    entries, errors = _debt_ledger(context)
    status = None if args.all else (args.status or debt_module.STATUS_OPEN)
    selected = debt_module.filter_entries(
        entries, status=status, kind=args.kind, doc_id=args.doc, path=args.path
    )
    limit = args.limit if args.limit and args.limit > 0 else None
    shown = selected if limit is None else selected[:limit]

    if context.json_mode:
        payload = debt_module.build_payload(entries)
        payload["ok"] = True
        payload["filtered"] = [entry.as_dict() for entry in shown]
        payload["filter"] = {
            "status": status,
            "kind": args.kind,
            "doc_id": args.doc,
            "path": args.path,
            "limit": limit,
        }
        payload["ledger_errors"] = [error.as_dict() for error in errors]
        context.write(report.dumps(payload))
    else:
        context.write(
            report.render_debt_list_text(
                shown, len(selected), debt_module.summarize(entries), context.palette
            )
        )
    return EXIT_OK


def cmd_debt_resolve(context):
    """Close ledger entries, either by explicit reference or by re-checking.

    ``--auto`` is the same close-what-is-no-longer-reported rule as
    ``debt record --resolve-absent``, reachable from the verb a caller who
    wants something closed actually reaches for. It records nothing new: an
    entry closes only because the check that opened it stopped reporting it.
    Explicit refs and ``--auto`` compose, so a single call can close one entry
    by hand and the rest by evidence.
    """
    args = context.args
    config = context.config
    auto = getattr(args, "auto", False)
    refs = list(args.refs or [])

    if not refs and not auto:
        raise UsageError(
            "debt resolve needs a REF, or --auto to close what the current "
            "check no longer reports"
        )

    entries, _errors = _debt_ledger(context)
    date, sha = _debt_stamp(context)

    resolved = []
    unmatched = []
    if refs:
        entries, resolved, unmatched = debt_module.resolve_entries(
            entries, refs, date, sha, note=args.note
        )
    if auto:
        result = _debt_findings(context)
        # Deliberately unfiltered by ``debt.record_kinds``: that setting says
        # which kinds may be *opened*, not which findings count as evidence
        # that an entry is still real. Filtering here would close entries the
        # check is still reporting.
        entries, auto_resolved = _resolve_absent(
            entries, list(result.findings), result, date, sha
        )
        for entry_id in auto_resolved:
            if entry_id not in resolved:
                resolved.append(entry_id)

    changed = debt_module.write_ledger(context.root, entries, config.debt_ledger)

    payload = {
        "ok": not unmatched,
        "ledger": config.debt_ledger,
        "written": changed,
        "resolved": resolved,
        "unmatched": unmatched,
        "summary": debt_module.summarize(entries),
    }
    if context.json_mode:
        context.write(report.dumps(payload))
    else:
        context.write(report.render_debt_resolve_text(payload, context.palette))
    # An unmatched reference is a usage error: the caller named debt that does
    # not exist and would otherwise believe it was closed.
    return EXIT_USAGE if unmatched else EXIT_OK


def cmd_debt_render(context):
    args = context.args
    config = context.config
    entries, _errors = _debt_ledger(context)
    limit = args.limit if args.limit and args.limit > 0 else config.debt_option("max_report_lines")
    out_rel = args.out or config.debt_report

    if args.stdout:
        text = debt_module.render_markdown(entries, limit=limit)
        if context.json_mode:
            context.write(report.dumps({"ok": True, "path": None, "written": False, "markdown": text}))
        else:
            context.write(text)
        return EXIT_OK

    changed = debt_module.write_markdown(context.root, entries, path=out_rel, limit=limit)
    if context.json_mode:
        context.write(
            report.dumps(
                {
                    "ok": True,
                    "path": out_rel,
                    "written": changed,
                    "summary": debt_module.summarize(entries),
                }
            )
        )
    elif not context.quiet:
        context.write(
            "gotdocs: %s %s\n" % ("wrote" if changed else "no change to", out_rel)
        )
    return EXIT_OK


_CI_MARK = {
    ci_module.OK: ("green", "ok"),
    ci_module.FAIL: ("red", "FAIL"),
    ci_module.WARN: ("yellow", "warn"),
    ci_module.UNKNOWN: ("dim", "?"),
}


def cmd_ci_doctor(context):
    checks, applied = ci_module.run_doctor(
        context.root, apply_fixes=bool(getattr(context.args, "apply", False))
    )
    summary = ci_module.summarize(checks)

    if context.json_mode:
        context.write(
            report.dumps(
                {
                    "ok": summary["ok"],
                    "counts": summary["counts"],
                    "applied": applied,
                    "checks": [check.as_dict() for check in checks],
                }
            )
        )
        return EXIT_OK if summary["ok"] else EXIT_FINDINGS

    palette = context.palette
    lines = []
    for check in checks:
        color, label = _CI_MARK[check.status]
        lines.append(
            "  %-6s %-28s %s"
            % (getattr(palette, color)(label), check.name, check.detail)
        )
        if check.status in (ci_module.FAIL, ci_module.UNKNOWN, ci_module.WARN):
            if check.fix:
                lines.append("         %s %s" % (palette.dim("->"), check.fix))
            if check.gh_command:
                lines.append("         %s %s" % (palette.dim("or:"), check.gh_command))

    if applied:
        lines.append("")
        lines.append(palette.green("  fixed: %s" % (", ".join(applied),)))

    header = (
        palette.green("gotdocs: CI prerequisites look good")
        if summary["ok"]
        else palette.red(
            "gotdocs: %d CI prerequisite(s) will break the first run"
            % (summary["counts"].get(ci_module.FAIL, 0),)
        )
    )
    unknown = summary["counts"].get(ci_module.UNKNOWN, 0)
    context.write(header + "\n\n" + "\n".join(lines) + "\n")
    if unknown and not context.quiet:
        context.write(
            "\n%s\n"
            % palette.dim(
                "  %d check(s) need an authenticated `gh` to verify "
                "(brew install gh && gh auth login), then: bin/gotdocs ci doctor --apply"
                % (unknown,)
            )
        )
    return EXIT_OK if summary["ok"] else EXIT_FINDINGS


def cmd_ci_init(context):
    args = context.args
    provider = getattr(args, "provider", ci_module.GITHUB)
    written = ci_module.init_workflow(
        context.root,
        provider,
        force=bool(getattr(args, "force", False)),
        mode=context.config.mode_for("ci"),
    )
    if context.json_mode:
        context.write(
            report.dumps({"ok": True, "provider": provider, "written": written})
        )
        return EXIT_OK
    if written:
        context.write("gotdocs: wrote %s (%s)\n" % (written, provider))
    else:
        context.write(
            "gotdocs: nothing to write for %s -- already current "
            "(use --force to rewrite)\n" % (provider,)
        )
    return EXIT_OK


def cmd_debt_stats(context):
    entries, errors = _debt_ledger(context)
    summary = debt_module.summarize(entries)
    if context.json_mode:
        context.write(
            report.dumps(
                {
                    "ok": True,
                    "ledger": context.config.debt_ledger,
                    "summary": summary,
                    "ledger_errors": [error.as_dict() for error in errors],
                }
            )
        )
    else:
        context.write(
            report.render_debt_stats_text(summary, context.config.debt_ledger, context.palette)
        )
    return EXIT_OK

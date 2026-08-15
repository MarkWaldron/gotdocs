"""Every ``git`` invocation gotdocs makes.

Rules this module enforces:

* ``git`` is run via :mod:`subprocess` with an argument list -- never a shell
  string -- so paths with spaces, quotes or newlines survive intact.
* Path output is requested with ``-z`` wherever git supports it, for the same
  reason.
* A subprocess failure never reaches the user as a traceback: it is mapped to a
  :class:`~tools.gotdocs.errors.GitError` (or one of its subclasses) with a
  one-line message.
* A repository with no commits is a supported state, not a crash.
"""

import os
import subprocess

from .errors import EmptyRepoError, GitError, NotAGitRepoError

__all__ = [
    "EMPTY_TREE",
    "GitRepo",
    "find_repo_root",
    "git_available",
]

# The well-known sha of git's empty tree object. Diffing the index against it
# gives "everything staged" in a repository that has no commits yet.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_ENV_OVERRIDES = {
    # Never take the index lock just to read state, and never let a user's
    # pager, editor or i18n settings change what we parse.
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "LANG": "C",
}


def _child_env():
    env = dict(os.environ)
    env.update(_ENV_OVERRIDES)
    return env


def _decode(raw):
    return raw.decode("utf-8", "surrogateescape")


def _run_git(args, cwd):
    """Run ``git *args`` in *cwd*; return ``(returncode, stdout, stderr)``."""
    command = ["git"] + list(args)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env(),
        )
    except FileNotFoundError:
        raise GitError("git executable not found on PATH")
    except NotADirectoryError:
        raise GitError("not a directory: %s" % (cwd,))
    except PermissionError as exc:
        raise GitError("cannot run git: %s" % (exc.strerror or exc,))
    except OSError as exc:
        raise GitError("cannot run git: %s" % (exc.strerror or exc,))
    return (
        completed.returncode,
        _decode(completed.stdout),
        _decode(completed.stderr),
    )


def git_available():
    """True when a usable ``git`` binary is on PATH."""
    try:
        code, _out, _err = _run_git(["--version"], None)
    except GitError:
        return False
    return code == 0


def find_repo_root(start=None):
    """Return the absolute toplevel of the repo containing *start*.

    Raises :class:`NotAGitRepoError` when *start* is not inside a repository.
    """
    cwd = os.path.abspath(start or os.getcwd())
    if not os.path.isdir(cwd):
        cwd = os.path.dirname(cwd) or os.getcwd()
    code, out, err = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if code != 0:
        message = _first_line(err) or "not a git repository"
        if "not a git repository" in message.lower():
            raise NotAGitRepoError("not a git repository: %s" % (cwd,))
        raise GitError(message)
    root = out.strip()
    if not root:
        raise NotAGitRepoError("not a git repository: %s" % (cwd,))
    return os.path.realpath(root)


def _first_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


class GitRepo(object):
    """Thin, read-mostly wrapper over the ``git`` CLI for one repository."""

    def __init__(self, root):
        self.root = os.path.abspath(root)

    # -- plumbing ----------------------------------------------------------

    def run(self, args, check=True):
        """Run ``git *args``; return stdout. Raise :class:`GitError` on failure."""
        code, out, err = _run_git(args, self.root)
        if code != 0 and check:
            raise GitError(
                "git %s failed: %s" % (args[0], _first_line(err) or "exit %d" % code)
            )
        return out

    def try_run(self, args):
        """Run ``git *args``; return ``(ok, stdout)`` without raising."""
        code, out, _err = _run_git(args, self.root)
        return code == 0, out

    # -- repository state --------------------------------------------------

    def git_dir(self):
        """Absolute path of the ``.git`` directory (or file-linked dir)."""
        out = self.run(["rev-parse", "--absolute-git-dir"]).strip()
        return out

    def has_commits(self):
        """False for a freshly ``git init``-ed repository."""
        code, _out, _err = _run_git(["rev-parse", "--verify", "--quiet", "HEAD"], self.root)
        return code == 0

    def head_sha(self, short=True):
        """Return HEAD's sha, or raise :class:`EmptyRepoError` if there is none."""
        args = ["rev-parse"]
        if short:
            args.append("--short")
        args.append("HEAD")
        code, out, err = _run_git(args, self.root)
        if code != 0:
            if not self.has_commits():
                raise EmptyRepoError(
                    "repository has no commits yet, so there is no HEAD sha"
                )
            raise GitError(_first_line(err) or "cannot resolve HEAD")
        return out.strip()

    def head_sha_or_none(self, short=True):
        """Like :meth:`head_sha` but returns None instead of raising."""
        try:
            return self.head_sha(short=short)
        except GitError:
            return None

    def resolve(self, ref):
        """Resolve *ref* to a full sha, or None when it does not exist."""
        code, out, _err = _run_git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], self.root)
        if code != 0:
            return None
        return out.strip() or None

    def is_shallow(self):
        """True when the clone has truncated history (``--depth``)."""
        ok, out = self.try_run(["rev-parse", "--is-shallow-repository"])
        return ok and out.strip() == "true"

    def merge_base(self, ref, other="HEAD"):
        """The merge base of *ref* and *other*, or None when there is none."""
        ok, out = self.try_run(["merge-base", other, ref])
        if not ok:
            return None
        return out.strip() or None

    def merge_in_progress(self):
        """True when a merge, rebase, cherry-pick or bisect is underway."""
        try:
            git_dir = self.git_dir()
        except GitError:
            return False
        for name in (
            "MERGE_HEAD",
            "REBASE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
        ):
            if os.path.exists(os.path.join(git_dir, name)):
                return True
        return False

    def commit_message(self):
        """Return the raw contents of ``COMMIT_EDITMSG``, or None.

        Callers almost always want :meth:`pending_commit_message` instead: at
        pre-commit time this file has *not* been written for the commit being
        made, so it still holds the previous commit's message.
        """
        try:
            git_dir = self.git_dir()
        except GitError:
            return None
        path = os.path.join(git_dir, "COMMIT_EDITMSG")
        try:
            with open(path, "rb") as handle:
                return handle.read().decode("utf-8", "replace")
        except (IOError, OSError):
            return None

    def pending_commit_message(self):
        """The message being written *now*, or None when it cannot be known.

        Git writes ``COMMIT_EDITMSG`` after the pre-commit stage, so during a
        pre-commit run the file normally still contains the message of HEAD.
        Trusting it verbatim means one commit carrying the skip token silently
        suppresses gotdocs on every later commit. So the leftover is rejected:
        the file is only treated as pending when its content differs from
        HEAD's message. This mirrors the guard in .gotdocs/hooks/pre-commit and
        makes a skip token best effort at pre-commit -- ``GOTDOCS_SKIP=1`` is
        the reliable bypass there.
        """
        raw = self.commit_message()
        if raw is None:
            return None
        pending = _strip_comment_lines(raw)
        if pending.strip() == "":
            return None
        head_message = self.last_commit_message()
        if head_message is not None and pending.strip() == head_message.strip():
            return None
        return pending

    def last_commit_message(self):
        """Message of HEAD, or None in an empty repository."""
        if not self.has_commits():
            return None
        ok, out = self.try_run(["log", "-1", "--format=%B"])
        return out if ok else None

    # -- change sets -------------------------------------------------------

    def staged_changes(self):
        """Repo-relative paths staged for commit, sorted and de-duplicated."""
        if self.has_commits():
            args = ["diff", "--cached", "--name-status", "-z", "--no-color", "HEAD"]
        else:
            args = ["diff", "--cached", "--name-status", "-z", "--no-color", EMPTY_TREE]
        out = self.run(args)
        return _parse_name_status_z(out)

    def base_changes(self, ref):
        """Paths changed between the merge base of *ref* and HEAD.

        Uses three-dot syntax so unrelated commits on *ref* do not create noise.
        """
        if not self.has_commits():
            raise EmptyRepoError(
                "repository has no commits yet, so --base has nothing to compare"
            )
        if self.resolve(ref) is None:
            raise GitError("unknown ref: %s%s" % (ref, self._shallow_hint()))
        if self.merge_base(ref) is None:
            # An empty change set here would read as "no findings" and silently
            # turn the check off, so say what actually happened instead.
            raise GitError(
                "no merge base between HEAD and %s, so REF...HEAD cannot be "
                "computed%s" % (ref, self._shallow_hint())
            )
        out = self.run(
            ["diff", "--name-status", "-z", "--no-color", "%s...HEAD" % (ref,)]
        )
        return _parse_name_status_z(out)

    def _shallow_hint(self):
        if not self.is_shallow():
            return ""
        return (
            " (this is a shallow clone: use fetch-depth: 0 in CI, or "
            "`git fetch --unshallow` locally)"
        )

    def working_tree_changes(self, include_untracked=True):
        """Paths modified in the working tree or index, plus untracked files."""
        paths = set(self.staged_changes())
        out = self.run(["diff", "--name-status", "-z", "--no-color"])
        paths.update(_parse_name_status_z(out))
        if include_untracked:
            out = self.run(["ls-files", "-z", "--others", "--exclude-standard"])
            paths.update(part for part in out.split("\0") if part)
        return sorted(paths)

    def tracked_files(self):
        """Every tracked path in the repository."""
        out = self.run(["ls-files", "-z"])
        return sorted(part for part in out.split("\0") if part)


def _strip_comment_lines(text):
    """Drop git's ``#`` scissors/instruction lines from a commit message."""
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("#")
    )


def _parse_name_status_z(out):
    """Parse ``git diff --name-status -z`` output into a sorted path list.

    ``-z`` emits ``STATUS\\0path\\0`` records; renames and copies emit
    ``R100\\0old\\0new\\0``. Both sides of a rename are reported, because the
    old path really did stop existing.
    """
    fields = [part for part in out.split("\0")]
    if fields and fields[-1] == "":
        fields.pop()
    paths = set()
    index = 0
    count = len(fields)
    while index < count:
        status = fields[index]
        index += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            if index < count:
                paths.add(fields[index])
                index += 1
            if index < count:
                paths.add(fields[index])
                index += 1
            continue
        if index < count:
            paths.add(fields[index])
            index += 1
    paths.discard("")
    return sorted(paths)

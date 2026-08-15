"""Error types and the graceful-degradation boundary for gotdocs.

Exit codes (see docs/cli-reference.md#exit-codes):

    0  clean, or findings exist but the effective mode is ``warn`` / ``off``
    1  findings exist and the effective mode is ``error``
    2  usage error, or a fatal lint problem
    3  not a git repository, or a required repo/config could not be located

Every error raised anywhere in the package must be a :class:`GotdocsError` so
that ``cli.main`` can turn it into a one-line message and a documented exit
code. Anything else that escapes is an *internal* error: the CLI prints
``gotdocs: internal error: <msg>`` and exits 0 unless ``--strict`` is set.
"""

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3


class GotdocsError(Exception):
    """Base class for every expected, user-facing gotdocs failure."""

    exit_code = EXIT_USAGE

    def __init__(self, message, exit_code=None):
        super().__init__(message)
        self.message = str(message)
        if exit_code is not None:
            self.exit_code = exit_code

    def __str__(self):
        return self.message


class UsageError(GotdocsError):
    """Bad command line: unknown flag, missing argument, conflicting sources."""

    exit_code = EXIT_USAGE


class ConfigError(GotdocsError):
    """`.gotdocs/config.json` exists but is unreadable or malformed."""

    exit_code = EXIT_USAGE


class GlobError(GotdocsError):
    """A glob pattern is outside the documented dialect."""

    exit_code = EXIT_USAGE

    def __init__(self, message, pattern=None):
        super().__init__(message)
        self.pattern = pattern


class FrontmatterError(GotdocsError):
    """Frontmatter is missing, unterminated, or outside the YAML subset.

    Carries ``path`` and ``line`` so callers can render ``file:line: message``.
    """

    exit_code = EXIT_USAGE

    def __init__(self, message, path=None, line=None):
        super().__init__(message)
        self.path = path
        self.line = line

    def located(self):
        """Return ``path:line: message``, degrading gracefully if unknown."""
        if self.path is None:
            return self.message
        if self.line is None:
            return "%s: %s" % (self.path, self.message)
        return "%s:%d: %s" % (self.path, self.line, self.message)


class GitError(GotdocsError):
    """A ``git`` invocation failed, or git is unusable in this environment."""

    exit_code = EXIT_ENVIRONMENT


class NotAGitRepoError(GitError):
    """The working directory is not inside a git repository."""


class EmptyRepoError(GitError):
    """The repository has no commits yet, so there is no HEAD sha."""


class DocNotFoundError(GotdocsError):
    """A doc id referenced on the command line does not exist."""

    exit_code = EXIT_USAGE

"""Shared helpers: throwaway git repositories built with tempfile + subprocess.

No test touches the repository the suite lives in. Every repo created here is
isolated from the developer's global git config (``GIT_CONFIG_GLOBAL`` and
``GIT_CONFIG_SYSTEM`` point at ``/dev/null``) so hooks, templates and
``init.defaultBranch`` cannot leak in and change results.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Make `tools.gotdocs` importable no matter how the suite was invoked.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_TESTS_DIR)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.gotdocs import config as config_module  # noqa: E402
from tools.gotdocs import globs  # noqa: E402

DEVNULL_CONFIG = os.devnull

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": DEVNULL_CONFIG,
    "GIT_CONFIG_SYSTEM": DEVNULL_CONFIG,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Gotdocs Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Gotdocs Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}


def git_env():
    env = dict(os.environ)
    env.update(GIT_ENV)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def git(root, *args):
    """Run git in *root* and return stdout; raise on failure."""
    completed = subprocess.run(
        ["git"] + list(args),
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_env(),
    )
    if completed.returncode != 0:
        raise AssertionError(
            "git %s failed (%d): %s"
            % (" ".join(args), completed.returncode, completed.stderr.decode("utf-8", "replace"))
        )
    return completed.stdout.decode("utf-8", "surrogateescape")


def write(root, rel_path, text):
    """Write *text* to ``root/rel_path``, creating parent directories."""
    path = os.path.join(root, rel_path.replace("/", os.sep))
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


def read(root, rel_path):
    path = os.path.join(root, rel_path.replace("/", os.sep))
    with io.open(path, "rb") as handle:
        return handle.read().decode("utf-8")


def read_bytes(root, rel_path):
    path = os.path.join(root, rel_path.replace("/", os.sep))
    with io.open(path, "rb") as handle:
        return handle.read()


def doc_text(
    doc_id="sample",
    title="Sample",
    doc_type="doc",
    summary="A sample document used by the gotdocs test suite.",
    covers=("src/**",),
    status="current",
    updated="2026-01-01",
    verified_at=None,
    owners=("@tester",),
    tags=("test",),
    body="\n# Sample\n\nBody text.\n",
    extra_lines=(),
):
    """Build a well-formed document with the given frontmatter."""
    lines = ["---"]
    lines.append("id: %s" % (doc_id,))
    lines.append("title: %s" % (title,))
    lines.append("type: %s" % (doc_type,))
    lines.append("summary: %s" % (summary,))
    if covers:
        lines.append("covers:")
        for pattern in covers:
            lines.append("  - %s" % (pattern,))
    else:
        lines.append("covers: []")
    lines.append("owners: [%s]" % (", ".join('"%s"' % owner for owner in owners),))
    lines.append("tags: [%s]" % (", ".join(tags),))
    lines.append("status: %s" % (status,))
    lines.append("updated: %s" % (updated,))
    if verified_at is not None:
        lines.append("verified_at: %s" % (verified_at,))
    lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + body


DEFAULT_CONFIG = {
    "version": 1,
    "roots": ["docs"],
    "enforce": {"pre_commit": "warn", "ci": "error"},
    "ignore": ["*.lock", "**/node_modules/**", ".gotdocs/index.json", ".gotdocs/INDEX.md"],
    "require_coverage": False,
    "skip_token": "[gotdocs skip]",
    "max_summary_chars": 200,
}


class TempRepoTestCase(unittest.TestCase):
    """Base class that gives each test its own initialized git repository."""

    def setUp(self):
        globs.cache_clear()
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="gotdocs-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q")
        git(self.root, "symbolic-ref", "HEAD", "refs/heads/main")
        self.write_config()

    # -- helpers -----------------------------------------------------------

    def write_config(self, **overrides):
        payload = dict(DEFAULT_CONFIG)
        payload.update(overrides)
        write(self.root, config_module.CONFIG_PATH, json.dumps(payload, indent=2) + "\n")
        return payload

    def config(self):
        return config_module.load(self.root)

    def write(self, rel_path, text):
        return write(self.root, rel_path, text)

    def read(self, rel_path):
        return read(self.root, rel_path)

    def git(self, *args):
        return git(self.root, *args)

    def add(self, *paths):
        self.git("add", "--", *paths)

    def commit(self, message="commit"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self, short=True):
        args = ["rev-parse"]
        if short:
            args.append("--short")
        args.append("HEAD")
        return self.git(*args).strip()

    def assertPathExists(self, rel_path):
        path = os.path.join(self.root, rel_path.replace("/", os.sep))
        self.assertTrue(os.path.exists(path), "expected %s to exist" % (rel_path,))

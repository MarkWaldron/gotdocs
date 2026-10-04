"""The git hooks, driven by real ``git commit`` in a throwaway repo.

Every other module tests the CLI the hooks call. This one runs the shell in
.gotdocs/hooks/ the way git runs it, so a hook that decides wrongly *before*
reaching the CLI is caught too.
"""

import os
import shutil
import subprocess
import sys
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support
from tools.gotdocs import index as index_module

SOURCE_ROOT = support._REPO_ROOT
SKIP_TOKEN = support.DEFAULT_CONFIG["skip_token"]

MODE_WARN = "warn"
MODE_ERROR = "error"

EXIT_OK = 0
EXIT_BLOCKED = 1


class HookTestCase(support.TempRepoTestCase):
    """A repo with gotdocs vendored, pre-commit installed, one doc on ``src/**``."""

    mode = MODE_ERROR

    def setUp(self):
        super().setUp()
        self.vendor()
        self.write_config(enforce={"pre_commit": self.mode, "ci": "error"})
        self.write(".gitignore", "__pycache__/\n")
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        index_module.write_index(self.root, self.config())
        self.commit("initial")
        self.install_hook("pre-commit")

    def vendor(self):
        """Copy the CLI in, the way gotdocs-install does. Tests are not needed."""
        shutil.copytree(
            os.path.join(SOURCE_ROOT, "tools", "gotdocs"),
            os.path.join(self.root, "tools", "gotdocs"),
            ignore=shutil.ignore_patterns("tests", "__pycache__"),
        )
        os.makedirs(os.path.join(self.root, "bin"))
        shutil.copy2(os.path.join(SOURCE_ROOT, "bin", "gotdocs"), os.path.join(self.root, "bin", "gotdocs"))

    def install_hook(self, name):
        target = os.path.join(self.root, ".git", "hooks", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(SOURCE_ROOT, ".gotdocs", "hooks", name), target)
        os.chmod(target, 0o755)

    def try_commit(self, message, env=None):
        """Run ``git commit`` through the hook; return (exit code, stderr)."""
        environ = support.git_env()
        environ.pop("GOTDOCS_SKIP", None)
        # Same interpreter as the suite, so a 3.9 run tests the hook on 3.9.
        environ["GOTDOCS_PYTHON"] = sys.executable
        environ.update(env or {})

        completed = subprocess.run(
            ["git", "commit", "-q", "-m", message],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environ,
        )
        return completed.returncode, completed.stderr.decode("utf-8", "replace")

    def stage_covered_change(self, text="print('v2')\n"):
        self.write("src/app.py", text)
        self.add("src/app.py")

    def stage_uncovered_change(self, text="notes\n"):
        self.write("notes.txt", text)
        self.add("notes.txt")


class PreCommitErrorModeTests(HookTestCase):
    mode = MODE_ERROR

    def test_stale_doc_blocks_the_commit(self):
        before = self.head()
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_BLOCKED, err)
        self.assertIn("docs/component.md", err)
        self.assertEqual(self.head(), before)

    def test_uncovered_change_commits(self):
        self.stage_uncovered_change()

        code, err = self.try_commit("add notes")

        self.assertEqual(code, EXIT_OK, err)

    def test_gotdocs_skip_env_bypasses(self):
        self.stage_covered_change()

        code, err = self.try_commit("change the app", env={"GOTDOCS_SKIP": "1"})

        self.assertEqual(code, EXIT_OK, err)

    def test_skip_token_from_another_branch_does_not_skip(self):
        """Regression: a token committed elsewhere lingers in COMMIT_EDITMSG.

            main  o---------------o  <- stale change here must still block
                   \\
            spike   o "wip [gotdocs skip]"   COMMIT_EDITMSG now holds this

        Back on main the file differs from HEAD's message, so it used to be
        read as the pending message and the check was skipped.
        """
        self.git("checkout", "-q", "-b", "spike")
        self.stage_uncovered_change()
        code, err = self.try_commit("wip %s" % (SKIP_TOKEN,))
        self.assertEqual(code, EXIT_OK, err)

        self.git("checkout", "-q", "main")
        before = self.head()
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_BLOCKED, err)
        self.assertEqual(self.head(), before)

    def test_skip_token_from_an_aborted_commit_does_not_skip(self):
        """Regression: a blocked commit leaves no message, but an editor abort does."""
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        with open(os.path.join(git_dir, "COMMIT_EDITMSG"), "w") as handle:
            handle.write("abandoned %s\n" % (SKIP_TOKEN,))
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_BLOCKED, err)


class PreCommitWarnModeTests(HookTestCase):
    mode = MODE_WARN

    def test_stale_doc_is_reported_but_never_blocks(self):
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("docs/component.md", err)


if __name__ == "__main__":
    unittest.main()

"""The git hooks, driven by real ``git commit`` in a throwaway repo.

Every other module tests the CLI the hooks call. This one runs the shell in
.gotdocs/hooks/ the way git runs it, so a hook that decides wrongly *before*
reaching the CLI is caught too.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support
from tools.gotdocs import debt as debt_module
from tools.gotdocs import index as index_module

SOURCE_ROOT = support._REPO_ROOT
SKIP_TOKEN = support.DEFAULT_CONFIG["skip_token"]

MODE_WARN = "warn"
MODE_ERROR = "error"

HOOK_PRE_COMMIT = "pre-commit"
HOOK_PRE_PUSH = "pre-push"

TRACKED_LEDGER = debt_module.LEDGER_PATH

REMOTE = "origin"
# What the CI record job commits to the default branch; see gotdocs.yml.
BOT_MESSAGE = "chore(gotdocs): record doc debt %s [skip ci]" % (SKIP_TOKEN,)

EXIT_OK = 0
EXIT_BLOCKED = 1


class HookTestCase(support.TempRepoTestCase):
    """A repo with gotdocs vendored, one hook installed, one doc on ``src/**``."""

    hook = HOOK_PRE_COMMIT
    mode = MODE_ERROR

    def setUp(self):
        super().setUp()
        self.vendor()
        self.write_config(enforce={self.hook.replace("-", "_"): self.mode, "ci": "error"})
        self.write(".gitignore", "__pycache__/\n")
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        index_module.write_index(self.root, self.config())
        self.commit("initial")
        self.install_hook(self.hook)

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

    def try_git(self, args, env=None):
        """Run a git command that fires a hook; return (exit code, stderr)."""
        environ = support.git_env()
        environ.pop("GOTDOCS_SKIP", None)
        # Same interpreter as the suite, so a 3.9 run tests the hook on 3.9.
        environ["GOTDOCS_PYTHON"] = sys.executable
        environ.update(env or {})

        completed = subprocess.run(
            ["git"] + list(args),
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environ,
        )
        return completed.returncode, completed.stderr.decode("utf-8", "replace")

    def try_commit(self, message, env=None):
        return self.try_git(["commit", "-q", "-m", message], env=env)

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

    def test_warned_commit_leaves_a_tracked_ledger_untouched(self):
        """Regression: the hook rewrote the tracked ledger after every warning.

            .gotdocs/debt.jsonl   tracked, written by CI on main
            .git/gotdocs/...      untracked, written by this hook

        A modified tracked file makes `git rebase` and `git pull` refuse to run,
        so warn mode, the default, got in the way of git itself.
        """
        self.write(TRACKED_LEDGER, "")
        self.commit("ci: start the ledger")
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_OK, err)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.git("rebase", "-q", "HEAD~1")

    def test_warned_commit_does_not_create_a_ledger_in_the_tree(self):
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_OK, err)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_warned_commit_is_recorded_in_the_local_ledger(self):
        self.stage_covered_change()

        code, err = self.try_commit("change the app")

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("debt list --local", err)
        entries, errors = debt_module.load_ledger(self.root, self.local_ledger())
        self.assertEqual(errors, [])
        self.assertEqual([entry.doc_id for entry in entries], ["component"])

    def local_ledger(self):
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        return debt_module.local_ledger_path(git_dir)


class PrePushTestCase(HookTestCase):
    """Adds a bare remote that already has ``main`` and an empty ``feat``."""

    hook = HOOK_PRE_PUSH

    def setUp(self):
        super().setUp()
        remote = os.path.realpath(tempfile.mkdtemp(prefix="gotdocs-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        support.git(remote, "init", "-q", "--bare")
        self.git("remote", "add", REMOTE, remote)

        self.git("branch", "feat")
        self.publish("main")
        self.publish("feat")
        self.git("checkout", "-q", "feat")

    def publish(self, branch):
        """Push without the check: fixture setup, not the push under test."""
        code, err = self.try_git(["push", "-q", REMOTE, branch], env={"GOTDOCS_SKIP": "1"})
        self.assertEqual(code, EXIT_OK, err)

    def try_push(self, refspec="feat"):
        return self.try_git(["push", "-q", REMOTE, refspec])

    def commit_stale_change(self, message="change the app"):
        self.write("src/app.py", "print('v2')\n")
        return self.commit(message)

    def land_bot_commit_on_main(self):
        self.git("checkout", "-q", "main")
        self.write(".gotdocs/debt.jsonl", "{}\n")
        self.commit(BOT_MESSAGE)
        self.publish("main")
        self.git("checkout", "-q", "feat")


class PrePushErrorModeTests(PrePushTestCase):
    mode = MODE_ERROR

    def test_stale_commit_blocks_the_push(self):
        self.commit_stale_change()

        code, err = self.try_push()

        self.assertEqual(code, EXIT_BLOCKED, err)
        self.assertIn("docs/component.md", err)

    def test_skip_token_in_a_pushed_commit_skips(self):
        self.commit_stale_change("spike %s" % (SKIP_TOKEN,))

        code, err = self.try_push()

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("skipped", err)

    def test_skip_token_on_a_branch_pushed_to_main_still_skips(self):
        self.commit_stale_change("spike %s" % (SKIP_TOKEN,))

        code, err = self.try_push("feat:main")

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("skipped", err)

    def test_bot_commit_merged_from_main_does_not_skip(self):
        """Regression: the CI ledger commit carries the token, to stop CI loops.

            main  A---B          B = "record doc debt [gotdocs skip] [skip ci]"
                   \\   \\
            feat    S---M        S is stale; M merges main

        B is in the range being pushed, so its token used to skip the check
        for S. A commit main already has cannot excuse this push.
        """
        self.commit_stale_change()
        self.land_bot_commit_on_main()
        self.git("merge", "-q", "--no-edit", "main")

        code, err = self.try_push()

        self.assertEqual(code, EXIT_BLOCKED, err)
        self.assertIn("docs/component.md", err)

    def test_bot_commit_rebased_under_the_branch_does_not_skip(self):
        """Same bug without a merge commit: ``A---B---S'`` after a rebase."""
        self.commit_stale_change()
        self.land_bot_commit_on_main()
        self.git("rebase", "-q", "main")

        code, err = self.try_push()

        self.assertEqual(code, EXIT_BLOCKED, err)


if __name__ == "__main__":
    unittest.main()

"""gitutil: subprocess handling, empty repos, non-repos and awkward paths."""

import os
import shutil
import subprocess
import tempfile
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import gitutil
from tools.gotdocs.errors import EmptyRepoError, GitError, NotAGitRepoError


class RepoDiscoveryTests(support.TempRepoTestCase):
    def test_finds_the_toplevel_from_a_subdirectory(self):
        os.makedirs(os.path.join(self.root, "a", "b"))
        found = gitutil.find_repo_root(os.path.join(self.root, "a", "b"))
        self.assertEqual(found, self.root)

    def test_not_a_git_repo_raises_the_typed_error(self):
        outside = tempfile.mkdtemp(prefix="gotdocs-not-a-repo-")
        self.addCleanup(shutil.rmtree, outside, True)
        # Guard: the temp dir must not sit inside someone's repo.
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=outside,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=support.git_env(),
        )
        if probe.returncode == 0:  # pragma: no cover - environment dependent
            self.skipTest("temp directory is inside a git repository")
        with self.assertRaises(NotAGitRepoError) as caught:
            gitutil.find_repo_root(outside)
        self.assertIn("not a git repository", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 3)

    def test_git_available(self):
        self.assertTrue(gitutil.git_available())


class EmptyRepositoryTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo = gitutil.GitRepo(self.root)

    def test_has_commits_is_false(self):
        self.assertFalse(self.repo.has_commits())

    def test_head_sha_raises_empty_repo_error(self):
        with self.assertRaises(EmptyRepoError):
            self.repo.head_sha()

    def test_head_sha_or_none_returns_none(self):
        self.assertIsNone(self.repo.head_sha_or_none())

    def test_staged_changes_diff_against_the_empty_tree(self):
        self.write("src/a.py", "x = 1\n")
        self.git("add", "-A")
        self.assertIn("src/a.py", self.repo.staged_changes())

    def test_base_changes_reports_a_clear_error(self):
        with self.assertRaises(EmptyRepoError):
            self.repo.base_changes("main")

    def test_last_commit_message_is_none(self):
        self.assertIsNone(self.repo.last_commit_message())


class ChangeSetTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo = gitutil.GitRepo(self.root)
        self.write("src/a.py", "x = 1\n")
        self.write("src/b.py", "y = 1\n")
        self.commit("initial")

    def test_staged_changes(self):
        self.write("src/a.py", "x = 2\n")
        self.add("src/a.py")
        self.assertEqual(self.repo.staged_changes(), ["src/a.py"])

    def test_unstaged_changes_are_not_staged_changes(self):
        self.write("src/a.py", "x = 3\n")
        self.assertEqual(self.repo.staged_changes(), [])

    def test_deletions_are_reported(self):
        self.git("rm", "-q", "src/b.py")
        self.assertEqual(self.repo.staged_changes(), ["src/b.py"])

    def test_renames_report_both_paths(self):
        self.git("mv", "src/a.py", "src/c.py")
        self.assertEqual(self.repo.staged_changes(), ["src/a.py", "src/c.py"])

    def test_paths_with_spaces_and_quotes(self):
        awkward = "src/a file 'with' quotes.py"
        self.write(awkward, "z = 1\n")
        self.git("add", "-A")
        self.assertIn(awkward, self.repo.staged_changes())

    def test_paths_with_unicode(self):
        name = "src/café–naïve.py"
        self.write(name, "z = 1\n")
        self.git("add", "-A")
        self.assertIn(name, self.repo.staged_changes())

    def test_base_changes_uses_three_dot(self):
        base = self.head(short=False)
        self.git("checkout", "-q", "-b", "feature")
        self.write("src/a.py", "x = 9\n")
        self.commit("feature change")
        self.assertEqual(self.repo.base_changes(base), ["src/a.py"])

    def test_base_changes_ignores_unrelated_commits_on_the_base(self):
        self.git("checkout", "-q", "-b", "feature")
        self.write("src/a.py", "x = 9\n")
        self.commit("feature change")
        self.git("checkout", "-q", "main")
        self.write("src/b.py", "y = 9\n")
        self.commit("main change")
        self.git("checkout", "-q", "feature")
        # Three-dot compares against the merge base, so src/b.py is not noise.
        self.assertEqual(self.repo.base_changes("main"), ["src/a.py"])

    def test_unknown_ref_raises_git_error(self):
        with self.assertRaises(GitError) as caught:
            self.repo.base_changes("definitely-not-a-ref")
        self.assertIn("unknown ref", str(caught.exception))

    def test_missing_merge_base_is_reported_not_silently_empty(self):
        """Unrelated histories must not read as "no findings"."""
        self.git("checkout", "-q", "--orphan", "orphan")
        self.write("src/c.py", "z = 1\n")
        self.commit("orphan root")
        with self.assertRaises(GitError) as caught:
            self.repo.base_changes("main")
        self.assertIn("no merge base", str(caught.exception))

    def test_merge_base_and_shallow_probes(self):
        self.assertFalse(self.repo.is_shallow())
        self.assertIsNotNone(self.repo.merge_base("HEAD"))
        self.assertIsNone(self.repo.merge_base("definitely-not-a-ref"))

    def test_working_tree_changes_include_untracked(self):
        self.write("src/new.py", "n = 1\n")
        self.assertIn("src/new.py", self.repo.working_tree_changes())

    def test_tracked_files(self):
        self.assertEqual(
            self.repo.tracked_files(),
            [".gotdocs/config.json", "src/a.py", "src/b.py"],
        )


class RepoStateTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo = gitutil.GitRepo(self.root)
        self.write("src/a.py", "x = 1\n")
        self.commit("initial")

    def test_head_sha_short_and_long(self):
        short = self.repo.head_sha(short=True)
        long_sha = self.repo.head_sha(short=False)
        self.assertEqual(len(long_sha), 40)
        self.assertTrue(long_sha.startswith(short))

    def test_resolve(self):
        self.assertEqual(self.repo.resolve("HEAD"), self.head(short=False))
        self.assertIsNone(self.repo.resolve("nope"))

    def test_git_dir(self):
        self.assertEqual(
            os.path.realpath(self.repo.git_dir()),
            os.path.realpath(os.path.join(self.root, ".git")),
        )

    def test_merge_in_progress_is_false_normally(self):
        self.assertFalse(self.repo.merge_in_progress())

    def test_merge_in_progress_detects_merge_head(self):
        with open(os.path.join(self.root, ".git", "MERGE_HEAD"), "w") as handle:
            handle.write(self.head(short=False) + "\n")
        self.assertTrue(self.repo.merge_in_progress())

    def test_commit_message_reads_commit_editmsg(self):
        with open(os.path.join(self.root, ".git", "COMMIT_EDITMSG"), "w") as handle:
            handle.write("a pending message\n")
        self.assertEqual(self.repo.commit_message(), "a pending message\n")

    def test_last_commit_message(self):
        self.assertIn("initial", self.repo.last_commit_message())

    def test_failed_command_raises_git_error_not_a_traceback(self):
        with self.assertRaises(GitError) as caught:
            self.repo.run(["cat-file", "-p", "0000000000000000000000000000000000000000"])
        self.assertNotIn("Traceback", str(caught.exception))

    def test_try_run_does_not_raise(self):
        ok, _out = self.repo.try_run(["rev-parse", "nope"])
        self.assertFalse(ok)


class NameStatusParsingTests(unittest.TestCase):
    def test_plain_records(self):
        raw = "M\0src/a.py\0A\0src/b.py\0D\0src/c.py\0"
        self.assertEqual(
            gitutil._parse_name_status_z(raw), ["src/a.py", "src/b.py", "src/c.py"]
        )

    def test_rename_records_report_both_sides(self):
        raw = "R100\0old/name.py\0new/name.py\0"
        self.assertEqual(gitutil._parse_name_status_z(raw), ["new/name.py", "old/name.py"])

    def test_copy_records(self):
        raw = "C75\0src/a.py\0src/b.py\0"
        self.assertEqual(gitutil._parse_name_status_z(raw), ["src/a.py", "src/b.py"])

    def test_paths_with_spaces(self):
        raw = "M\0src/a file.py\0"
        self.assertEqual(gitutil._parse_name_status_z(raw), ["src/a file.py"])

    def test_empty_output(self):
        self.assertEqual(gitutil._parse_name_status_z(""), [])

    def test_truncated_record_does_not_crash(self):
        self.assertEqual(gitutil._parse_name_status_z("M\0"), [])


class MissingGitTests(unittest.TestCase):
    def test_missing_binary_maps_to_git_error(self):
        original = gitutil.subprocess.run

        def explode(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "git")

        gitutil.subprocess.run = explode
        try:
            with self.assertRaises(GitError) as caught:
                gitutil.find_repo_root(os.getcwd())
            self.assertIn("git executable not found", str(caught.exception))
        finally:
            gitutil.subprocess.run = original


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

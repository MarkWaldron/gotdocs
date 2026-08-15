"""Tests for the CI preflight (``gotdocs ci doctor`` / ``ci init``).

The remote checks shell out to ``gh``. These tests never do: ``gh_available`` is
stubbed, so the suite behaves identically on a machine with gh, without gh, and
on a runner with no network.
"""

import io
import os
import stat
import unittest

from tools.gotdocs import ci, cli
from tools.gotdocs.tests import support


WORKFLOW = """\
name: gotdocs

on:
  pull_request:
    types: [opened, synchronize]
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
"""


class WorkflowParsingTests(unittest.TestCase):
    def test_reads_a_flow_style_branch_list(self):
        self.assertEqual(ci.workflow_push_branches(WORKFLOW), ["main"])

    def test_reads_a_block_style_branch_list(self):
        text = WORKFLOW.replace(
            "    branches: [main]", "    branches:\n      - main\n      - release"
        )
        self.assertEqual(ci.workflow_push_branches(text), ["main", "release"])

    def test_strips_quotes(self):
        text = WORKFLOW.replace("[main]", "['main', \"master\"]")
        self.assertEqual(ci.workflow_push_branches(text), ["main", "master"])

    def test_returns_empty_when_there_is_no_push_trigger(self):
        text = WORKFLOW.replace("  push:\n    branches: [main]\n", "")
        self.assertEqual(ci.workflow_push_branches(text), [])


class DoctorTestCase(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write(".github/workflows/gotdocs.yml", WORKFLOW)
        self.write("bin/gotdocs", "#!/bin/sh\nexec python3 -m tools.gotdocs \"$@\"\n")
        path = os.path.join(self.root, "bin", "gotdocs")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.git("add", "-A")
        self.commit("base")
        # Remote checks are covered separately with an explicit stub.
        self._real_gh = ci.gh_available
        ci.gh_available = lambda: False
        self.addCleanup(self._restore_gh)

    def _restore_gh(self):
        ci.gh_available = self._real_gh

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            list(args) + ["--repo", self.root, "--no-color"], stdout=out, stderr=err
        )
        return code, out.getvalue(), err.getvalue()

    def named(self, checks, name):
        for check in checks:
            if check.name == name:
                return check
        self.fail("no check named %r" % (name,))


class LocalCheckTests(DoctorTestCase):
    def test_a_healthy_repo_passes(self):
        checks = ci.local_checks(self.root)
        for name in ("workflow-present", "full-history", "cli-vendored"):
            self.assertEqual(self.named(checks, name).status, ci.OK, name)

    def test_a_missing_workflow_fails_and_stops_there(self):
        os.remove(os.path.join(self.root, ".github/workflows/gotdocs.yml"))
        checks = ci.local_checks(self.root)
        self.assertEqual(checks[0].name, "workflow-present")
        self.assertEqual(checks[0].status, ci.FAIL)
        self.assertEqual(len(checks), 1, "no point checking a file that is not there")

    def test_a_shallow_checkout_fails(self):
        self.write(
            ".github/workflows/gotdocs.yml", WORKFLOW.replace("          fetch-depth: 0\n", "")
        )
        self.assertEqual(
            self.named(ci.local_checks(self.root), "full-history").status, ci.FAIL
        )

    def test_a_default_branch_the_workflow_ignores_fails(self):
        self.git("branch", "-m", "master")
        check = self.named(ci.local_checks(self.root), "default-branch")
        self.assertEqual(check.status, ci.FAIL)
        self.assertIn("master", check.detail)
        self.assertTrue(check.auto, "ci init --force can fix this")

    def test_a_matching_default_branch_passes(self):
        self.assertEqual(
            self.named(ci.local_checks(self.root), "default-branch").status, ci.OK
        )

    def test_a_non_executable_cli_fails(self):
        path = os.path.join(self.root, "bin", "gotdocs")
        os.chmod(path, 0o644)
        check = self.named(ci.local_checks(self.root), "cli-vendored")
        self.assertEqual(check.status, ci.FAIL)
        self.assertIn("not executable", check.detail)

    def test_an_exec_bit_missing_from_the_index_fails(self):
        # The nastiest variant: right on disk, wrong in git, so it only breaks in CI.
        self.git("update-index", "--chmod=-x", "bin/gotdocs")
        check = self.named(ci.local_checks(self.root), "cli-exec-bit-committed")
        self.assertEqual(check.status, ci.FAIL)
        self.assertIn("100644", check.detail)


class RemoteCheckTests(DoctorTestCase):
    def test_without_gh_remote_checks_are_unknown_not_failures(self):
        checks = ci.remote_checks(self.root, have_gh=False)
        self.assertTrue(all(check.status == ci.UNKNOWN for check in checks))

    def test_unknown_checks_still_carry_a_click_path_and_a_command(self):
        check = self.named(ci.remote_checks(self.root, have_gh=False),
                           "workflow-token-permissions")
        self.assertIn("Settings", check.fix)
        self.assertIn("Read and write", check.fix)
        self.assertIn("gh api", check.gh_command)

    def test_unknown_does_not_make_the_run_fail(self):
        checks = ci.remote_checks(self.root, have_gh=False)
        self.assertTrue(ci.summarize(checks)["ok"])


class ApplyTests(DoctorTestCase):
    def test_apply_rewrites_the_branch_list(self):
        self.git("branch", "-m", "master")
        checks, applied = ci.run_doctor(self.root, apply_fixes=True)
        self.assertIn("default-branch", applied)
        self.assertEqual(
            ci.workflow_push_branches(self.read(".github/workflows/gotdocs.yml")),
            ["master"],
        )
        self.assertEqual(self.named(checks, "default-branch").status, ci.OK)

    def test_apply_restores_the_exec_bit_in_tree_and_index(self):
        self.git("update-index", "--chmod=-x", "bin/gotdocs")
        os.chmod(os.path.join(self.root, "bin", "gotdocs"), 0o644)
        ci.run_doctor(self.root, apply_fixes=True)
        mode = self.git("ls-files", "-s", "bin/gotdocs").split()[0]
        self.assertEqual(mode, "100755")
        self.assertTrue(os.access(os.path.join(self.root, "bin", "gotdocs"), os.X_OK))

    def test_apply_is_idempotent(self):
        self.git("branch", "-m", "master")
        ci.run_doctor(self.root, apply_fixes=True)
        checks, applied = ci.run_doctor(self.root, apply_fixes=True)
        self.assertEqual(applied, [], "nothing left to fix on the second pass")
        self.assertTrue(ci.summarize(checks)["ok"])

    def test_doctor_without_apply_changes_nothing(self):
        self.git("branch", "-m", "master")
        before = self.read(".github/workflows/gotdocs.yml")
        ci.run_doctor(self.root, apply_fixes=False)
        self.assertEqual(self.read(".github/workflows/gotdocs.yml"), before)


class CliSurfaceTests(DoctorTestCase):
    def test_doctor_exits_1_when_something_will_break(self):
        self.git("branch", "-m", "master")
        code, out, _ = self.run_cli("ci", "doctor")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)

    def test_doctor_exits_0_when_clean(self):
        code, out, _ = self.run_cli("ci", "doctor")
        self.assertEqual(code, 0)
        self.assertIn("look good", out)

    def test_doctor_json_is_valid_and_shaped(self):
        import json

        code, out, _ = self.run_cli("ci", "doctor", "--json")
        payload = json.loads(out)
        self.assertIn("ok", payload)
        self.assertIn("checks", payload)
        self.assertIn("applied", payload)
        for check in payload["checks"]:
            self.assertEqual(
                sorted(check),
                ["auto_fixable", "detail", "fix", "gh_command", "name", "status"],
            )

    def test_init_gitlab_writes_a_file(self):
        code, out, _ = self.run_cli("ci", "init", "--provider", "gitlab")
        self.assertEqual(code, 0)
        self.assertIn(".gitlab-ci.gotdocs.yml", out)
        text = self.read(".gitlab-ci.gotdocs.yml")
        self.assertIn("GIT_DEPTH: 0", text)
        self.assertIn("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", text)

    def test_init_gitlab_does_not_clobber_without_force(self):
        self.run_cli("ci", "init", "--provider", "gitlab")
        self.write(".gitlab-ci.gotdocs.yml", "# edited by hand\n")
        self.run_cli("ci", "init", "--provider", "gitlab")
        self.assertEqual(self.read(".gitlab-ci.gotdocs.yml"), "# edited by hand\n")

    def test_init_gitlab_force_overwrites(self):
        self.run_cli("ci", "init", "--provider", "gitlab")
        self.write(".gitlab-ci.gotdocs.yml", "# edited by hand\n")
        self.run_cli("ci", "init", "--provider", "gitlab", "--force")
        self.assertIn("GIT_DEPTH", self.read(".gitlab-ci.gotdocs.yml"))

    def test_gitlab_mode_follows_the_configured_ci_enforcement(self):
        self.write_config(enforce={"pre_commit": "warn", "ci": "error"})
        self.run_cli("ci", "init", "--provider", "gitlab", "--force")
        text = self.read(".gitlab-ci.gotdocs.yml")
        self.assertIn("--mode error", text)
        self.assertIn("allow_failure: false", text)


if __name__ == "__main__":
    unittest.main()

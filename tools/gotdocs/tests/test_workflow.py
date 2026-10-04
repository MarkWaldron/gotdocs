"""The CI `record` job, run for real against a bare remote.

The job's logic is shell inside .github/workflows/gotdocs.yml. These tests lift
the `run:` blocks out of that file and execute them the way a runner would: in
a fresh clone, checked out at the pushed sha, with the step's environment.

    dev repo --push--> origin (bare) <--push-- runner clone, one per CI run
"""

import io
import os
import shutil
import subprocess
import tempfile
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support
from tools.gotdocs import debt as debt_module
from tools.gotdocs import index as index_module

WORKFLOW_PATH = ".github/workflows/gotdocs.yml"
STEP_RECORD = "Record, resolve and render the ledger"
STEP_COMMIT = "Commit the ledger back to main"

REMOTE = "origin"
BRANCH = "main"
SKIP_TOKEN = support.DEFAULT_CONFIG["skip_token"]
SKIP_CI = "[skip ci]"

# Printed by the commit step when its push is rejected and it starts over.
RETRY_NOTICE = "re-recording on the new tip"

OUTPUT_CHANGED = "changed"
OUTPUT_BASE = "base"
CHANGED_YES = "1"
STEP_NAME_PREFIX = "- name: "
RUN_BLOCK = "run: |"


def step_script(workflow_text, step_name):
    """Return the shell of the `run: |` block of the step called *step_name*.

    Deliberately not a YAML parser (the CLI is stdlib-only, and so is this):
    find the step, find its `run: |`, take every deeper-indented line.
    """
    lines = workflow_text.splitlines()
    header = STEP_NAME_PREFIX + step_name
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == RUN_BLOCK)
    indent = len(lines[run_at]) - len(lines[run_at].lstrip())

    body = []
    for line in lines[run_at + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)

    width = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    return "\n".join(line[width:] for line in body) + "\n"


class RecordJobTestCase(support.VendoredRepoTestCase):
    """Two docs on two directories, so two pushes make two different findings."""

    def setUp(self):
        super().setUp()
        self.vendor()
        self.write_config(enforce={"pre_commit": "warn", "ci": "warn"})
        self.write("docs/app.md", support.doc_text(doc_id="app", covers=["src/**"]))
        self.write("docs/lib.md", support.doc_text(doc_id="lib", covers=["lib/**"]))
        self.write("src/app.py", "print('v1')\n")
        self.write("lib/util.py", "print('v1')\n")
        index_module.write_index(self.root, self.config())
        self.commit("initial")

        self.remote = self.temp_dir("gotdocs-remote-")
        support.git(self.remote, "init", "-q", "--bare")
        support.git(self.remote, "symbolic-ref", "HEAD", "refs/heads/%s" % (BRANCH,))
        self.git("remote", "add", REMOTE, self.remote)
        self.push()

        with io.open(self.source_path(WORKFLOW_PATH), encoding="utf-8") as handle:
            workflow = handle.read()
        self.record_script = step_script(workflow, STEP_RECORD)
        self.commit_script = step_script(workflow, STEP_COMMIT)

    def temp_dir(self, prefix):
        path = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def push(self):
        self.git("push", "-q", REMOTE, "HEAD:%s" % (BRANCH,))

    def push_change(self, path, message):
        """Commit a change to *path* and push it; return ``(before, after)``."""
        before = self.head(short=False)
        self.write(path, "print('%s')\n" % (message,))
        self.commit(message)
        self.push()
        return before, self.head(short=False)

    def run_record_job(self, before, sha):
        """One CI run for the push ``before..sha``; return the combined log."""
        runner = self.temp_dir("gotdocs-runner-")
        support.git(runner, "clone", "-q", self.remote, ".")
        support.git(runner, "checkout", "-q", "--detach", sha)
        output = os.path.join(self.temp_dir("gotdocs-output-"), "github_output")
        env = self.shell_env(BEFORE=before, BRANCH=BRANCH, GITHUB_OUTPUT=output)

        log = self.run_step(self.record_script, runner, env)
        outputs = self.step_outputs(output)

        # The commit step carries `if: steps.ledger.outputs.changed == '1'`
        # and reads `steps.ledger.outputs.base` as BASE.
        if outputs.get(OUTPUT_CHANGED) == CHANGED_YES:
            env["BASE"] = outputs[OUTPUT_BASE]
            log += self.run_step(self.commit_script, runner, env)
        return log

    def step_outputs(self, path):
        """Parse the ``name=value`` lines a step appended to $GITHUB_OUTPUT."""
        with io.open(path, encoding="utf-8") as handle:
            pairs = [line.rstrip("\n").split("=", 1) for line in handle if "=" in line]
        return dict(pairs)

    def run_step(self, script, cwd, env):
        # Actions runs `run:` blocks with `bash -e`.
        completed = subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log = completed.stdout.decode("utf-8", "replace")
        self.assertEqual(completed.returncode, 0, log)
        return log

    def remote_ledger(self):
        """Doc ids with open debt in the ledger on the remote branch."""
        self.git("fetch", "-q", REMOTE)
        checkout = self.temp_dir("gotdocs-verify-")
        support.git(checkout, "clone", "-q", self.remote, ".")
        entries, errors = debt_module.load_ledger(checkout)
        self.assertEqual(errors, [])
        return sorted(entry.doc_id for entry in entries if entry.status == debt_module.STATUS_OPEN)

    def remote_head_message(self):
        return support.git(self.remote, "log", "-1", "--format=%B", BRANCH)


class RecordJobTests(RecordJobTestCase):
    def test_a_push_with_stale_docs_lands_a_ledger_commit(self):
        before, sha = self.push_change("src/app.py", "change the app")

        self.run_record_job(before, sha)

        self.assertEqual(self.remote_ledger(), ["app"])
        message = self.remote_head_message()
        self.assertIn(SKIP_TOKEN, message)
        self.assertIn(SKIP_CI, message)

    def test_a_clean_push_before_any_debt_exists_does_not_fail_the_job(self):
        """Regression: `git add` on the not-yet-created ledger was fatal.

        A repository's first clean push has a rendered report and no ledger.
        The step staged both by name, exited 128, and turned the push red.
        """
        before = self.head(short=False)
        self.write("notes.txt", "notes\n")
        self.commit("add notes")
        self.push()

        self.run_record_job(before, self.head(short=False))

        self.assertEqual(self.remote_ledger(), [])

    def test_two_runs_racing_both_land_their_debt(self):
        """Regression: the second run's debt was dropped, silently.

            main   P---A---B            two pushes, close together
            run A  records P..A, pushes  -> rejected, B is ahead
            run B  records A..B, pushes  -> rejected, run A's ledger is ahead

        Each run used to rebase its ledger commit onto the new tip. Run B's
        ledger was built without run A's entries, so that rebase conflicted,
        was aborted, and the job warned and exited 0. Nothing ever recorded
        B's range again: a later push only records its own.
        """
        p_sha, a_sha = self.push_change("src/app.py", "change the app")
        _a_sha, b_sha = self.push_change("lib/util.py", "change the lib")

        self.run_record_job(p_sha, a_sha)
        log = self.run_record_job(a_sha, b_sha)

        self.assertIn(RETRY_NOTICE, log)
        self.assertEqual(self.remote_ledger(), ["app", "lib"])

    def test_a_late_run_does_not_undo_a_later_one(self):
        """The same race in the other order: run B finishes first."""
        p_sha, a_sha = self.push_change("src/app.py", "change the app")
        _a_sha, b_sha = self.push_change("lib/util.py", "change the lib")

        self.run_record_job(a_sha, b_sha)
        log = self.run_record_job(p_sha, a_sha)

        self.assertIn(RETRY_NOTICE, log)
        self.assertEqual(self.remote_ledger(), ["app", "lib"])


if __name__ == "__main__":
    unittest.main()

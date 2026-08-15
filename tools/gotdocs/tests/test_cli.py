"""CLI surface: subcommands, flags, exit codes, the JSON contract, the
graceful-degradation boundary, and the POSIX sh shim.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import check as check_module
from tools.gotdocs import cli
from tools.gotdocs.config import INDEX_JSON_PATH, INDEX_MD_PATH


class CliTestCase(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        self.run_cli("index")
        self.commit("initial")

    def run_cli(self, *args):
        """Run the CLI against this repo; return ``(code, stdout, stderr)``."""
        out = io.StringIO()
        err = io.StringIO()
        argv = list(args) + ["--repo", self.root, "--no-color"]
        code = cli.main(argv, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *args):
        code, out, err = self.run_cli(*(list(args) + ["--json"]))
        return code, json.loads(out), err


class CheckCommandTests(CliTestCase):
    def test_clean_run(self):
        code, out, _err = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("no findings", out)

    def test_stale_run_in_warn_mode(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, out, _err = self.run_cli("check", "--mode", "warn")
        self.assertEqual(code, 0)
        self.assertIn("gotdocs: 1 finding (mode: warn)", out)
        self.assertIn("stale (1)", out)
        self.assertIn("docs/component.md  [component]", out)
        self.assertIn("src/app.py changed and is covered by src/**", out)
        self.assertIn("-> update docs/component.md, or run: bin/gotdocs verify component", out)
        self.assertIn("Or ask Claude: /gotdocs-update", out)

    def test_stale_run_in_error_mode_exits_one(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, _out, _err = self.run_cli("check", "--mode", "error")
        self.assertEqual(code, 1)

    def test_json_contract(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, payload, _err = self.run_json("check", "--mode", "warn")
        self.assertEqual(code, 0)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["mode"], "warn")
        finding = payload["findings"][0]
        self.assertEqual(
            sorted(finding), ["doc_id", "kind", "message", "path", "remediation"]
        )
        self.assertEqual(finding["kind"], "stale")
        self.assertEqual(finding["doc_id"], "component")
        self.assertEqual(finding["path"], "docs/component.md")
        summary = payload["summary"]
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["impacted"], 1)
        self.assertEqual(summary["docs_indexed"], 1)
        self.assertEqual(summary["head"], self.head())

    def test_json_ok_is_independent_of_mode(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, payload, _err = self.run_json("check", "--mode", "warn")
        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])

    def test_paths_source(self):
        code, payload, _err = self.run_json("check", "--paths", "src/app.py", "--mode", "warn")
        self.assertEqual(code, 0)
        self.assertEqual(payload["findings"][0]["doc_id"], "component")

    def test_base_source(self):
        base = self.head()
        self.write("src/app.py", "print('v2')\n")
        self.commit("code change")
        code, payload, _err = self.run_json("check", "--base", base, "--mode", "warn")
        self.assertEqual(code, 0)
        self.assertEqual(payload["findings"][0]["kind"], "stale")

    def test_sources_are_mutually_exclusive(self):
        code, _out, err = self.run_cli("check", "--staged", "--base", "main")
        self.assertEqual(code, 2)
        self.assertIn("not allowed with", err)

    def test_off_mode(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, out, _err = self.run_cli("check", "--mode", "off")
        self.assertEqual(code, 0)
        self.assertIn("enforcement is off", out)

    def test_message_flag_skips(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, out, _err = self.run_cli("check", "--message", "wip [gotdocs skip]")
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)

    def test_message_file_flag_skips(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        message_path = os.path.join(self.root, "msg.txt")
        with io.open(message_path, "w") as handle:
            handle.write("chore [gotdocs skip]\n")
        code, out, _err = self.run_cli("check", "--message-file", message_path)
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)

    def test_missing_message_file_is_tolerated(self):
        code, _out, _err = self.run_cli("check", "--message-file", "/nonexistent/msg")
        self.assertEqual(code, 0)

    def test_unknown_base_ref_json_error_envelope(self):
        code, payload, _err = self.run_json("check", "--base", "no-such-ref")
        self.assertEqual(code, 3)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], 3)
        self.assertIn("unknown ref", payload["error"]["message"])
        self.assertEqual(payload["findings"], [])

    def test_quiet_suppresses_clean_output(self):
        code, out, _err = self.run_cli("check", "--quiet")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class ImpactedCommandTests(CliTestCase):
    def test_text_output(self):
        code, out, _err = self.run_cli("impacted", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("component", out)
        self.assertIn("(src/**)", out)

    def test_json_output(self):
        code, payload, _err = self.run_json("impacted", "src/app.py", "other/x.py", "a.lock")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["paths"][0]["docs"][0]["doc_id"], "component")
        self.assertEqual(payload["paths"][1]["docs"], [])
        self.assertTrue(payload["paths"][2]["ignored"])

    def test_exit_zero_when_nothing_matches(self):
        code, _out, _err = self.run_cli("impacted", "nowhere/x.py")
        self.assertEqual(code, 0)


class VerifyCommandTests(CliTestCase):
    def test_stamps_verified_at_and_updated(self):
        head = self.head()
        code, out, _err = self.run_cli("verify", "component")
        self.assertEqual(code, 0)
        text = self.read("docs/component.md")
        self.assertIn("verified_at: %s" % (head,), text)
        self.assertIn("updated: %s" % (cli._today(),), text)
        self.assertIn("verified component", out)

    def test_only_the_two_lines_change(self):
        before = self.read("docs/component.md").splitlines()
        self.run_cli("verify", "component")
        after = self.read("docs/component.md").splitlines()
        removed = [line for line in before if line not in after]
        added = [line for line in after if line not in before]
        # `updated` is rewritten in place; `verified_at` is appended because the
        # fixture document does not declare it. Nothing else moves.
        self.assertEqual(removed, ["updated: 2026-01-01"])
        self.assertEqual(
            sorted(added),
            sorted(["updated: %s" % (cli._today(),), "verified_at: %s" % (self.head(),)]),
        )
        self.assertEqual(len(after), len(before) + 1)

    def test_verified_doc_is_no_longer_stale(self):
        self.run_cli("verify", "component")
        self.run_cli("index")
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, payload, _err = self.run_json("check", "--mode", "error")
        self.assertEqual(payload["findings"], [])
        self.assertEqual(code, 0)

    def test_accepts_a_path_as_well_as_an_id(self):
        code, _out, _err = self.run_cli("verify", "docs/component.md")
        self.assertEqual(code, 0)

    def test_unknown_id_exits_two(self):
        code, _out, err = self.run_cli("verify", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no document with id", err)

    def test_no_arguments_is_a_usage_error(self):
        code, _out, err = self.run_cli("verify")
        self.assertEqual(code, 2)
        self.assertIn("at least one doc id", err)

    def test_all_impacted(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        code, _out, _err = self.run_cli("verify", "--all-impacted")
        self.assertEqual(code, 0)
        self.assertIn("verified_at: %s" % (self.head(),), self.read("docs/component.md"))

    def test_all_impacted_with_nothing_impacted(self):
        code, out, _err = self.run_cli("verify", "--all-impacted")
        self.assertEqual(code, 0)
        self.assertIn("nothing impacted", out)


class IndexCommandTests(CliTestCase):
    def test_writes_both_files(self):
        self.run_cli("index")
        code, out, _err = self.run_cli("index")
        self.assertEqual(code, 0)
        self.assertPathExists(INDEX_JSON_PATH)
        self.assertPathExists(INDEX_MD_PATH)
        self.assertIn("no changes", out)

    def test_json_output_shape(self):
        self.write("docs/second.md", support.doc_text(doc_id="second", covers=[]))
        code, payload, _err = self.run_json("index")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["doc_count"], 2)
        self.assertEqual(sorted(payload["changed"]), [INDEX_MD_PATH, INDEX_JSON_PATH])

    def test_running_twice_produces_no_diff(self):
        self.write("docs/second.md", support.doc_text(doc_id="second", covers=[]))
        self.run_cli("index")
        first = support.read_bytes(self.root, INDEX_JSON_PATH)
        code, payload, _err = self.run_json("index")
        self.assertEqual(payload["changed"], [])
        self.assertEqual(support.read_bytes(self.root, INDEX_JSON_PATH), first)
        self.assertEqual(code, 0)


class LintCommandTests(CliTestCase):
    def test_clean_repo_exits_zero(self):
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 0)
        self.assertIn("no lint errors", out)

    def test_errors_exit_two_with_file_and_line(self):
        self.write("docs/broken.md", "---\nid: broken\nowner:\n  name: mark\n---\n\nBody\n")
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 2)
        self.assertIn("docs/broken.md:4:", out)
        self.assertIn("nested mappings", out)

    def test_duplicate_ids_are_reported(self):
        self.write("docs/twin.md", support.doc_text(doc_id="component", covers=[]))
        code, payload, _err = self.run_json("lint")
        self.assertEqual(code, 2)
        self.assertIn("duplicate_id", [f["kind"] for f in payload["findings"]])

    def test_covers_matching_zero_files_is_legal(self):
        self.write(
            "docs/future.md", support.doc_text(doc_id="future", covers=["not/built/yet/**"])
        )
        code, _out, _err = self.run_cli("lint")
        self.assertEqual(code, 0)

    def test_invalid_ignore_pattern_is_reported(self):
        self.write_config(ignore=["/absolute"])
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 2)
        self.assertIn("invalid ignore pattern", out)


class StatusCommandTests(CliTestCase):
    def test_status_lines(self):
        code, out, _err = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("gotdocs 1", out)
        self.assertIn("head %s" % (self.head(),), out)
        self.assertIn("roots     docs", out)
        self.assertIn("docs      1 (1 current, 0 draft, 0 deprecated)", out)
        self.assertIn(
            "enforce   pre_commit=warn  pre_push=warn  ci=error  require_coverage=false",
            out,
        )
        self.assertIn("up to date", out)
        self.assertIn("not installed", out)

    def test_status_json(self):
        code, payload, _err = self.run_json("status")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"]["doc_count"], 1)
        self.assertEqual(payload["status"]["roots"], ["docs"])

    def test_status_reports_a_missing_config(self):
        os.remove(os.path.join(self.root, ".gotdocs", "config.json"))
        code, out, _err = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("missing, using defaults", out)


class NewCommandTests(CliTestCase):
    def test_creates_a_document(self):
        code, out, _err = self.run_cli(
            "new", "doc", "my-thing", "--title", "My Thing", "--covers", "src/**"
        )
        self.assertEqual(code, 0)
        self.assertIn("created docs/my-thing.md", out)
        text = self.read("docs/my-thing.md")
        self.assertIn("id: my-thing", text)
        self.assertIn("title: My Thing", text)
        self.assertIn("covers:\n  - src/**\n", text)
        self.assertIn("updated: %s" % (cli._today(),), text)
        self.assertIn("# My Thing", text)
        # A scaffold is not a doc anybody has read: verified_at stays at the
        # template placeholder so `never-verified` detection still sees it.
        self.assertNotIn("verified_at: %s" % (self.head(),), text)
        self.assertIn("verified_at: 0000000", text)
        self.assertIn("status: draft", text)

    def test_created_document_parses_and_lints_after_a_summary_is_written(self):
        self.run_cli("new", "doc", "my-thing", "--title", "My Thing")
        from tools.gotdocs import index as index_module

        doc_set = index_module.scan(self.root, self.config())
        created = [doc for doc in doc_set.docs if doc.path == "docs/my-thing.md"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].id, "my-thing")
        self.assertEqual([issue.message for issue in doc_set.issues], [])

    def test_runbook_goes_to_the_runbooks_root(self):
        self.write_config(roots=["docs", "runbooks"])
        code, _out, _err = self.run_cli("new", "runbook", "queue-backlog")
        self.assertEqual(code, 0)
        self.assertPathExists("runbooks/queue-backlog.md")

    def test_repeatable_covers(self):
        self.run_cli("new", "doc", "multi", "--covers", "a/**", "--covers", "b/*.py")
        text = self.read("docs/multi.md")
        self.assertIn("covers:\n  - a/**\n  - b/*.py\n", text)

    def test_bad_id_exits_two(self):
        code, _out, err = self.run_cli("new", "doc", "Not_Kebab")
        self.assertEqual(code, 2)
        self.assertIn("kebab-case", err)

    def test_duplicate_id_exits_two(self):
        code, _out, err = self.run_cli("new", "doc", "component")
        self.assertEqual(code, 2)
        self.assertIn("already used", err)

    def test_existing_file_exits_two(self):
        self.write("docs/taken.md", "placeholder\n")
        code, _out, err = self.run_cli("new", "doc", "taken")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_invalid_covers_pattern_exits_two(self):
        code, _out, err = self.run_cli("new", "doc", "bad-glob", "--covers", "/abs/**")
        self.assertEqual(code, 2)
        self.assertIn("repo-relative", err)

    def test_uses_the_repo_template_when_present(self):
        self.write(
            ".gotdocs/templates/doc.md",
            "---\nid: replace-me\ntitle: Replace Me\ntype: doc\n"
            "summary: Template summary.\ncovers:\n  - path/to/**\nstatus: draft\n"
            "updated: 1970-01-01\nverified_at: 0000000\n---\n\n"
            "# Replace Me\n\nTEMPLATE MARKER\n",
        )
        self.run_cli("new", "doc", "from-template", "--covers", "src/**")
        text = self.read("docs/from-template.md")
        self.assertIn("TEMPLATE MARKER", text)
        self.assertIn("id: from-template", text)
        self.assertIn("covers:\n  - src/**\n", text)
        self.assertNotIn("path/to/**", text)


class InstallCommandTests(CliTestCase):
    def hook_path(self):
        return os.path.join(self.root, ".git", "hooks", "pre-commit")

    def test_missing_hook_source_is_a_clear_error(self):
        code, _out, err = self.run_cli("install")
        self.assertEqual(code, 2)
        self.assertIn(".gotdocs/hooks/pre-commit", err)

    def test_installs_and_is_idempotent(self):
        self.write(".gotdocs/hooks/pre-commit", "#!/bin/sh\n# gotdocs hook\nexit 0\n")
        code, out, _err = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertIn("installed", out)
        self.assertTrue(os.stat(self.hook_path()).st_mode & stat.S_IXUSR)

        code, out, _err = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertIn("already up to date", out)

    def test_refuses_a_foreign_hook(self):
        self.write(".gotdocs/hooks/pre-commit", "#!/bin/sh\n# gotdocs hook\nexit 0\n")
        os.makedirs(os.path.join(self.root, ".git", "hooks"), exist_ok=True)
        with io.open(self.hook_path(), "w") as handle:
            handle.write("#!/bin/sh\necho someone elses hook\n")
        code, _out, err = self.run_cli("install")
        self.assertEqual(code, 2)
        self.assertIn("--force", err)

    def test_force_overwrites_and_leaves_a_backup(self):
        self.write(".gotdocs/hooks/pre-commit", "#!/bin/sh\n# gotdocs hook\nexit 0\n")
        os.makedirs(os.path.join(self.root, ".git", "hooks"), exist_ok=True)
        with io.open(self.hook_path(), "w") as handle:
            handle.write("#!/bin/sh\necho someone elses hook\n")
        code, out, _err = self.run_cli("install", "--force")
        self.assertEqual(code, 0)
        self.assertIn(".bak", out)
        self.assertTrue(os.path.exists(self.hook_path() + ".bak"))


class PathArgumentTests(CliTestCase):
    """Regression: absolute and ``../`` paths were silently mangled.

    ``normalize_path`` only strips a leading ``/``, so ``/abs/repo/src/app.py``
    became ``abs/repo/src/app.py``, matched no ``covers``, and both commands
    reported a clean "nothing here" instead of the real answer.
    """

    def test_impacted_accepts_an_absolute_path(self):
        absolute = os.path.join(self.root, "src", "app.py")
        code, out, _err = self.run_cli("impacted", absolute)
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("docs/component.md", out)

    def test_check_paths_accepts_an_absolute_path(self):
        absolute = os.path.join(self.root, "src", "app.py")
        code, out, _err = self.run_cli("check", "--paths", absolute, "--mode", "error")
        self.assertEqual(code, 1)
        self.assertIn("docs/component.md", out)

    def test_dot_dot_is_resolved_against_the_cwd(self):
        os.makedirs(os.path.join(self.root, "sub"))
        original = os.environ.get("GOTDOCS_CWD")
        os.environ["GOTDOCS_CWD"] = os.path.join(self.root, "sub")
        try:
            code, out, _err = self.run_cli("impacted", "../src/app.py")
        finally:
            if original is None:
                os.environ.pop("GOTDOCS_CWD", None)
            else:  # pragma: no cover - restoring a pre-existing value
                os.environ["GOTDOCS_CWD"] = original
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("docs/component.md", out)

    def test_a_path_outside_the_repository_is_a_usage_error(self):
        outside = os.path.join(os.path.dirname(self.root), "elsewhere.py")
        code, _out, err = self.run_cli("impacted", outside)
        self.assertEqual(code, 2)
        self.assertIn("outside the repository", err)

    def test_ordinary_relative_paths_are_unchanged(self):
        code, out, _err = self.run_cli("impacted", "./src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("docs/component.md", out)

    def in_subdir(self, subdir, *args):
        original = os.environ.get("GOTDOCS_CWD")
        os.environ["GOTDOCS_CWD"] = os.path.join(self.root, subdir)
        try:
            return self.run_cli(*args)
        finally:
            if original is None:
                os.environ.pop("GOTDOCS_CWD", None)
            else:  # pragma: no cover - restoring a pre-existing value
                os.environ["GOTDOCS_CWD"] = original

    def test_a_plain_relative_path_is_resolved_against_the_cwd(self):
        """Regression: `cd src && gotdocs impacted app.py` answered "no docs"."""
        code, out, _err = self.in_subdir("src", "impacted", "app.py")
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("docs/component.md", out)
        self.assertNotIn("no documents cover this path", out)

    def test_a_repo_relative_path_still_works_from_a_subdirectory(self):
        code, out, _err = self.in_subdir("src", "impacted", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("docs/component.md", out)

    def test_check_paths_resolves_against_the_cwd_too(self):
        code, out, _err = self.in_subdir("src", "check", "--paths", "app.py", "--mode", "error")
        self.assertEqual(code, 1)
        self.assertIn("docs/component.md", out)

    def test_a_path_that_exists_nowhere_is_read_as_cwd_relative(self):
        code, out, _err = self.in_subdir("src", "impacted", "new_module.py")
        self.assertEqual(code, 0)
        self.assertIn("src/new_module.py", out)

    def test_resolution_from_the_repository_root_is_unchanged(self):
        code, out, _err = self.run_cli("impacted", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("src/app.py", out)
        self.assertIn("docs/component.md", out)

    def test_impacted_reports_doc_paths_as_doc_paths(self):
        code, out, _err = self.run_cli("impacted", "docs/component.md")
        self.assertEqual(code, 0)
        self.assertIn("(doc)", out)
        self.assertNotIn("no documents cover this path", out)


class GlobalBehaviourTests(CliTestCase):
    def test_help_exits_zero(self):
        out = io.StringIO()
        code = cli.main(["--help"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 0)

    def test_no_command_prints_help(self):
        out = io.StringIO()
        code = cli.main(["--repo", self.root], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn("usage:", out.getvalue())

    def test_an_unknown_choice_still_emits_the_json_envelope(self):
        """Regression: argparse printed usage to stderr and left stdout empty."""
        out = io.StringIO()
        err = io.StringIO()
        code = cli.main(
            ["export", "--target", "nope", "--out", "/tmp/x", "--json",
             "--repo", self.root, "--no-color"],
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], 2)
        self.assertIn("--target", payload["error"]["message"])

    def test_every_argparse_usage_error_emits_the_envelope(self):
        for argv in (
            ["check", "--nope"],
            ["check", "--mode", "sideways"],
            ["new", "widget", "some-id"],
            ["debt", "list", "--status", "pending"],
            ["frobnicate"],
        ):
            out = io.StringIO()
            err = io.StringIO()
            code = cli.main(
                argv + ["--json", "--repo", self.root, "--no-color"],
                stdout=out,
                stderr=err,
            )
            self.assertEqual(code, 2, argv)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["error"]["code"], 2, argv)
            self.assertTrue(payload["error"]["message"], argv)

    def test_help_and_version_still_exit_zero(self):
        for argv in (["--help"], ["--version"], ["check", "--help"]):
            out = io.StringIO()
            code = cli.main(argv, stdout=out, stderr=io.StringIO())
            self.assertEqual(code, 0, argv)
            self.assertTrue(out.getvalue().strip(), argv)

    def test_unknown_command_exits_two(self):
        code, _out, _err = self.run_cli("frobnicate")
        self.assertEqual(code, 2)

    def test_unknown_flag_exits_two(self):
        code, _out, _err = self.run_cli("check", "--nope")
        self.assertEqual(code, 2)

    def test_version(self):
        out = io.StringIO()
        code = cli.main(["--version"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 0)

    def test_repo_flag_must_be_a_directory(self):
        out = io.StringIO()
        err = io.StringIO()
        code = cli.main(["status", "--repo", "/nonexistent/dir"], stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err.getvalue())

    def test_outside_a_git_repo_exits_three(self):
        outside = tempfile.mkdtemp(prefix="gotdocs-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=outside,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=support.git_env(),
        )
        if probe.returncode == 0:  # pragma: no cover - environment dependent
            self.skipTest("temp directory is inside a git repository")
        err = io.StringIO()
        code = cli.main(["status", "--repo", outside], stdout=io.StringIO(), stderr=err)
        self.assertEqual(code, 3)
        self.assertIn("not a git repository", err.getvalue())


class GracefulDegradationTests(CliTestCase):
    def _explode(self, *args, **kwargs):
        raise RuntimeError("boom from a unit test")

    def test_internal_error_warns_and_exits_zero(self):
        original = check_module.run_check
        check_module.run_check = self._explode
        try:
            code, _out, err = self.run_cli("check")
        finally:
            check_module.run_check = original
        self.assertEqual(code, 0)
        self.assertIn("gotdocs: internal error:", err)
        self.assertIn("boom from a unit test", err)
        self.assertEqual(len([line for line in err.splitlines() if line.strip()]), 1)

    def test_strict_turns_internal_errors_into_exit_two(self):
        original = check_module.run_check
        check_module.run_check = self._explode
        try:
            code, _out, err = self.run_cli("check", "--strict")
        finally:
            check_module.run_check = original
        self.assertEqual(code, 2)
        self.assertIn("internal error", err)

    def test_expected_errors_keep_their_exit_codes(self):
        code, _out, err = self.run_cli("verify", "nope")
        self.assertEqual(code, 2)
        self.assertNotIn("internal error", err)

    def test_internal_error_still_emits_the_json_envelope(self):
        """Regression: --json produced zero bytes on stdout and exit 0.

        A consumer doing `json.load(stdout)` raised, and one keying off the
        exit code concluded "clean".
        """
        original = check_module.run_check
        check_module.run_check = self._explode
        try:
            code, out, err = self.run_cli("check", "--json")
        finally:
            check_module.run_check = original
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["error"]["code"], 0)
        self.assertIn("boom from a unit test", payload["error"]["message"])
        self.assertIn("internal error", err)

    def test_internal_error_json_envelope_under_strict(self):
        original = check_module.run_check
        check_module.run_check = self._explode
        try:
            code, out, _err = self.run_cli("check", "--json", "--strict")
        finally:
            check_module.run_check = original
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], 2)

    def test_expected_errors_also_emit_the_json_envelope(self):
        code, out, _err = self.run_cli("verify", "nope", "--json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], 2)
        self.assertIn("nope", payload["error"]["message"])


class PythonVersionGateTests(unittest.TestCase):
    """The entry point refuses an interpreter older than the documented floor."""

    def test_current_interpreter_passes(self):
        from tools.gotdocs import __main__ as entry

        self.assertIsNone(entry._version_error(sys.version_info))

    def test_old_interpreter_gets_one_readable_line(self):
        from tools.gotdocs import __main__ as entry

        message = entry._version_error((3, 6, 0))
        self.assertIsNotNone(message)
        self.assertIn("python 3.6 is too old", message)
        self.assertIn("GOTDOCS_PYTHON", message)


class ShimTests(support.TempRepoTestCase):
    """The POSIX sh shim must work from any cwd and through symlinks."""

    def setUp(self):
        super().setUp()
        source_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        ))))
        os.makedirs(os.path.join(self.root, "bin"))
        shutil.copy2(
            os.path.join(source_root, "bin", "gotdocs"),
            os.path.join(self.root, "bin", "gotdocs"),
        )
        shutil.copytree(
            os.path.join(source_root, "tools", "gotdocs"),
            os.path.join(self.root, "tools", "gotdocs"),
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
        self.shim = os.path.join(self.root, "bin", "gotdocs")
        os.chmod(self.shim, os.stat(self.shim).st_mode | stat.S_IXUSR)
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        self.commit("initial")

    def run_shim(self, args, cwd=None, command=None):
        env = support.git_env()
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            (command or [self.shim]) + args,
            cwd=cwd or self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        return (
            completed.returncode,
            completed.stdout.decode("utf-8"),
            completed.stderr.decode("utf-8"),
        )

    def test_is_executable(self):
        self.assertTrue(os.stat(self.shim).st_mode & stat.S_IXUSR)

    def test_runs_from_the_repo_root(self):
        code, out, err = self.run_shim(["--version"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "gotdocs 1")

    def test_runs_from_a_subdirectory(self):
        subdir = os.path.join(self.root, "src")
        code, out, err = self.run_shim(["status"], cwd=subdir)
        self.assertEqual(code, 0, err)
        self.assertIn("roots     docs", out)

    def test_runs_from_an_unrelated_directory_via_absolute_path(self):
        elsewhere = tempfile.mkdtemp(prefix="gotdocs-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        code, out, err = self.run_shim(["status", "--repo", self.root], cwd=elsewhere)
        self.assertEqual(code, 0, err)
        self.assertIn("roots     docs", out)

    def test_runs_through_a_symlink(self):
        link_dir = tempfile.mkdtemp(prefix="gotdocs-link-")
        self.addCleanup(shutil.rmtree, link_dir, True)
        link = os.path.join(link_dir, "gotdocs")
        os.symlink(self.shim, link)
        code, out, err = self.run_shim(["--version"], command=[link])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "gotdocs 1")

    def test_check_through_the_shim(self):
        self.write("src/app.py", "print('v2')\n")
        self.git("add", "--", "src/app.py")
        code, out, err = self.run_shim(["check", "--staged", "--mode", "error", "--no-color"])
        self.assertEqual(code, 1, err)
        self.assertIn("stale (1)", out)

    def test_python_module_spelling_also_works(self):
        env = support.git_env()
        env["PYTHONPATH"] = self.root
        completed = subprocess.run(
            ["python3", "-m", "tools.gotdocs", "--version"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_directory_spelling_also_works(self):
        completed = subprocess.run(
            ["python3", os.path.join(self.root, "tools", "gotdocs"), "--version"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=support.git_env(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout.decode().strip(), "gotdocs 1")

    def test_missing_interpreter_warns_and_exits_zero(self):
        env = support.git_env()
        env["GOTDOCS_PYTHON"] = os.path.join(self.root, "no-such-python3")
        completed = subprocess.run(
            [self.shim, "check"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("is not executable", completed.stderr.decode())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""The staleness rule matrix, exercised against real throwaway git repos.

Covers every branch of docs/architecture.md#the-core-rule-precisely:
impacted / satisfied-by-edit / satisfied-by-verify / stale / uncovered, the
doc-side findings, the skip token, and the mode-to-exit-code mapping.
"""

import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import check as check_module
from tools.gotdocs import index as index_module
from tools.gotdocs.check import (
    KIND_DEPRECATED_EDIT,
    KIND_DUPLICATE_ID,
    KIND_INDEX_OUT_OF_DATE,
    KIND_LINT,
    KIND_STALE,
    KIND_UNCOVERED,
)
from tools.gotdocs.errors import UsageError


class CheckTestCase(support.TempRepoTestCase):
    """A repo with one doc covering ``src/**`` and a committed index."""

    def setUp(self):
        super().setUp()
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        self.write("other/util.py", "print('other')\n")
        self.write("Cargo.lock", "lock\n")
        self.refresh_index()
        self.commit("initial")

    def refresh_index(self, head=None):
        index_module.write_index(self.root, self.config(), head_sha=head)

    def run_check(self, **kwargs):
        kwargs.setdefault("env", {})
        return check_module.run_check(self.root, self.config(), **kwargs)

    def kinds(self, result):
        return sorted(finding.kind for finding in result.findings)

    def findings_of(self, result, kind):
        return [finding for finding in result.findings if finding.kind == kind]

    def stale_ids(self, result):
        return sorted(finding.doc_id for finding in self.findings_of(result, KIND_STALE))


class ImpactMatrixTests(CheckTestCase):
    def test_change_outside_covers_is_not_impacted(self):
        self.write("other/util.py", "print('changed')\n")
        self.add("other/util.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])
        self.assertEqual(result.summary["impacted"], 0)
        self.assertEqual(result.summary["code_paths"], 1)

    def test_impacted_and_untouched_is_stale(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), ["component"])
        self.assertEqual(result.summary["impacted"], 1)
        self.assertEqual(result.summary["stale"], 1)
        finding = self.findings_of(result, KIND_STALE)[0]
        self.assertEqual(finding.path, "docs/component.md")
        self.assertEqual(finding.message, "src/app.py changed and is covered by src/**")
        self.assertEqual(
            finding.remediation,
            "update docs/component.md, or run: bin/gotdocs verify component",
        )

    def test_satisfied_by_editing_the_doc(self):
        self.write("src/app.py", "print('v2')\n")
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], body="\n# Sample\n\nNew prose.\n"),
        )
        self.add("src/app.py", "docs/component.md")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])
        self.assertEqual(result.summary["impacted"], 1)
        self.assertEqual(result.summary["doc_paths"], 1)

    def test_satisfied_by_verified_at_equal_to_head(self):
        # This is the real `gotdocs verify` workflow: verify stamps the sha of
        # the commit that is currently HEAD, then the author stages the code
        # change on top of it.
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], verified_at=self.head()),
        )
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])
        self.assertEqual(result.summary["impacted"], 1)

    def test_verified_at_long_sha_matches_short_head(self):
        long_sha = self.git("rev-parse", "HEAD").strip()
        self.assertGreater(len(long_sha), len(self.head()))
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], verified_at=long_sha),
        )
        self.write("src/app.py", "print('v3')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])

    def test_verified_at_from_an_older_commit_is_still_stale(self):
        stale_sha = "0000000"
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], verified_at=stale_sha),
        )
        self.refresh_index()
        self.commit("template sha")
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), ["component"])

    def test_doc_paths_never_count_as_code_paths(self):
        self.write("docs/other.md", support.doc_text(doc_id="other", covers=["docs/**"]))
        self.refresh_index()
        self.commit("second doc")
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"], body="\n# S\n\nEdited.\n"))
        self.add("docs/component.md")
        result = self.run_check()
        # docs/component.md is a doc path, so docs/other.md (covers docs/**) is
        # not impacted by it.
        self.assertEqual(self.stale_ids(result), [])
        self.assertEqual(result.summary["code_paths"], 0)
        self.assertEqual(result.summary["doc_paths"], 1)

    def test_ignored_paths_are_not_code_paths(self):
        self.write("docs/lockwatch.md", support.doc_text(doc_id="lockwatch", covers=["*.lock"]))
        self.refresh_index()
        self.commit("lock doc")
        self.write("Cargo.lock", "lock v2\n")
        self.add("Cargo.lock")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])
        self.assertEqual(result.summary["code_paths"], 0)
        self.assertEqual(result.summary["changed_paths"], 1)

    def test_several_docs_covering_one_file_are_all_stale(self):
        self.write("docs/second.md", support.doc_text(doc_id="second", covers=["src/app.py"]))
        self.refresh_index()
        self.commit("second doc")
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), ["component", "second"])

    def test_message_counts_additional_files(self):
        self.write("src/app.py", "print('v2')\n")
        self.write("src/other.py", "print('new')\n")
        self.git("add", "-A")
        result = self.run_check()
        message = self.findings_of(result, KIND_STALE)[0].message
        self.assertIn("(and 1 other file)", message)

    def test_deleted_file_still_counts_as_a_change(self):
        self.git("rm", "-q", "src/app.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), ["component"])

    def test_renamed_file_counts_both_sides(self):
        self.git("mv", "src/app.py", "src/renamed.py")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), ["component"])
        self.assertEqual(result.summary["code_paths"], 2)

    def test_paths_with_spaces_survive(self):
        self.write("src/a file with spaces.py", "x = 1\n")
        self.git("add", "-A")
        result = self.run_check()
        self.assertIn("src/a file with spaces.py", result.code_paths)
        self.assertEqual(self.stale_ids(result), ["component"])


class RequireCoverageTests(CheckTestCase):
    def test_uncovered_is_silent_by_default(self):
        self.write("other/util.py", "print('changed')\n")
        self.add("other/util.py")
        result = self.run_check()
        self.assertEqual(self.findings_of(result, KIND_UNCOVERED), [])

    def test_uncovered_reported_when_required(self):
        self.write_config(require_coverage=True)
        self.commit("turn on require_coverage")
        self.write("other/util.py", "print('changed')\n")
        self.add("other/util.py")
        result = self.run_check()
        uncovered = self.findings_of(result, KIND_UNCOVERED)
        self.assertEqual([finding.path for finding in uncovered], ["other/util.py"])
        self.assertIsNone(uncovered[0].doc_id)

    def test_covered_paths_are_not_uncovered(self):
        self.write_config(require_coverage=True)
        self.commit("turn on require_coverage")
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(self.findings_of(result, KIND_UNCOVERED), [])


class SkipTests(CheckTestCase):
    def test_skip_token_in_the_supplied_message(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check(message="wip: refactor [gotdocs skip]")
        self.assertTrue(result.skipped)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.exit_code(), 0)
        self.assertIn("[gotdocs skip]", result.skip_reason)

    def test_skip_token_in_commit_editmsg(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        with open(git_dir + "/COMMIT_EDITMSG", "w") as handle:
            handle.write("chore: noise [gotdocs skip]\n")
        result = self.run_check()
        self.assertTrue(result.skipped)

    def test_leftover_commit_editmsg_does_not_skip(self):
        """Regression: a skip token in the *previous* commit must not linger.

        Git writes COMMIT_EDITMSG after the pre-commit stage, so during a
        commit the file still holds HEAD's message. Trusting it meant one
        commit carrying the token silently disabled gotdocs for every commit
        after it.
        """
        self.write("src/other.py", "print('x')\n")
        self.commit("chore: noise [gotdocs skip]")
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        with open(git_dir + "/COMMIT_EDITMSG", "rb") as handle:
            self.assertIn("[gotdocs skip]", handle.read().decode("utf-8"))

        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertFalse(result.skipped)
        self.assertEqual(self.stale_ids(result), ["component"])

    def test_commit_editmsg_matching_head_is_ignored_even_with_comments(self):
        self.write("src/other.py", "print('x')\n")
        self.commit("chore: noise [gotdocs skip]")
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        with open(git_dir + "/COMMIT_EDITMSG", "w") as handle:
            handle.write(
                "chore: noise [gotdocs skip]\n"
                "# Please enter the commit message for your changes.\n"
            )
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        self.assertFalse(self.run_check().skipped)

    def test_gotdocs_skip_environment_variable(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check(env={"GOTDOCS_SKIP": "1"})
        self.assertTrue(result.skipped)
        self.assertEqual(result.skip_reason, "GOTDOCS_SKIP is set")

    def test_gotdocs_skip_zero_does_not_skip(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check(env={"GOTDOCS_SKIP": "0"})
        self.assertFalse(result.skipped)

    def test_commit_editmsg_is_ignored_for_base_runs(self):
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        with open(git_dir + "/COMMIT_EDITMSG", "w") as handle:
            handle.write("old message [gotdocs skip]\n")
        base = self.head()
        self.write("src/app.py", "print('v2')\n")
        self.commit("real change")
        result = self.run_check(source=check_module.SOURCE_BASE, base=base)
        self.assertFalse(result.skipped)
        self.assertEqual(self.stale_ids(result), ["component"])


class ModeTests(CheckTestCase):
    def setUp(self):
        super().setUp()
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")

    def test_warn_mode_exits_zero_with_findings(self):
        result = self.run_check(mode="warn")
        self.assertTrue(result.findings)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code(), 0)

    def test_error_mode_exits_one_with_findings(self):
        result = self.run_check(mode="error")
        self.assertEqual(result.exit_code(), 1)

    def test_off_mode_computes_nothing(self):
        result = self.run_check(mode="off")
        self.assertEqual(result.findings, [])
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.exit_code(), 0)

    def test_staged_defaults_to_the_pre_commit_mode(self):
        self.write_config(enforce={"pre_commit": "warn", "ci": "error"})
        result = self.run_check()
        self.assertEqual(result.mode, "warn")

    def test_base_defaults_to_the_ci_mode(self):
        base = self.head()
        self.commit("change")
        result = self.run_check(source=check_module.SOURCE_BASE, base=base)
        self.assertEqual(result.mode, "error")

    def test_clean_run_exits_zero_in_error_mode(self):
        self.git("reset", "-q", "HEAD")
        self.write("src/app.py", "print('v1')\n")
        result = self.run_check(mode="error")
        self.assertEqual(result.findings, [])
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code(), 0)


class SourceSelectionTests(CheckTestCase):
    def test_paths_source_needs_no_git_state(self):
        result = self.run_check(source=check_module.SOURCE_PATHS, paths=["src/app.py"])
        self.assertEqual(self.stale_ids(result), ["component"])
        self.assertEqual(result.changed_paths, ["src/app.py"])

    def test_paths_source_normalizes_input(self):
        result = self.run_check(source=check_module.SOURCE_PATHS, paths=["./src/app.py"])
        self.assertEqual(result.changed_paths, ["src/app.py"])

    def test_base_source_uses_the_merge_base(self):
        base = self.head()
        self.write("src/app.py", "print('v2')\n")
        self.commit("code change")
        result = self.run_check(source=check_module.SOURCE_BASE, base=base)
        self.assertEqual(self.stale_ids(result), ["component"])

    def test_base_requires_a_ref(self):
        with self.assertRaises(UsageError):
            self.run_check(source=check_module.SOURCE_BASE, base=None)

    def test_paths_requires_paths(self):
        with self.assertRaises(UsageError):
            self.run_check(source=check_module.SOURCE_PATHS, paths=[])

    def test_unknown_base_ref_is_a_git_error(self):
        from tools.gotdocs.errors import GitError

        with self.assertRaises(GitError):
            self.run_check(source=check_module.SOURCE_BASE, base="no-such-ref")


class DocSideFindingTests(CheckTestCase):
    def test_lint_findings_are_always_reported(self):
        self.write("docs/broken.md", "---\nid: broken\nowner:\n  name: mark\n---\n\nBody\n")
        result = self.run_check()
        lint = self.findings_of(result, KIND_LINT)
        self.assertTrue(lint)
        self.assertTrue(any("docs/broken.md:4:" in finding.message for finding in lint))

    def test_duplicate_ids_are_reported(self):
        self.write("docs/twin.md", support.doc_text(doc_id="component", covers=[]))
        result = self.run_check()
        duplicates = self.findings_of(result, KIND_DUPLICATE_ID)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].doc_id, "component")

    def test_editing_a_deprecated_doc_is_reported(self):
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], status="deprecated"),
        )
        self.add("docs/component.md")
        result = self.run_check()
        deprecated = self.findings_of(result, KIND_DEPRECATED_EDIT)
        self.assertEqual([finding.path for finding in deprecated], ["docs/component.md"])

    def test_deprecated_doc_left_alone_is_not_reported(self):
        self.write(
            "docs/component.md",
            support.doc_text(doc_id="component", covers=["src/**"], status="deprecated"),
        )
        self.refresh_index()
        self.commit("deprecate")
        result = self.run_check()
        self.assertEqual(self.findings_of(result, KIND_DEPRECATED_EDIT), [])

    def test_out_of_date_index_is_reported(self):
        self.write("docs/new.md", support.doc_text(doc_id="new-doc", covers=[]))
        result = self.run_check()
        stale = self.findings_of(result, KIND_INDEX_OUT_OF_DATE)
        self.assertEqual(
            sorted(finding.path for finding in stale),
            [".gotdocs/INDEX.md", ".gotdocs/index.json"],
        )

    def test_index_is_current_ignores_the_volatile_sha(self):
        # The committed index was generated at the previous sha; a new commit
        # must not make every checkout report index_out_of_date.
        self.write("src/app.py", "print('v2')\n")
        self.commit("move head")
        result = self.run_check()
        self.assertEqual(self.findings_of(result, KIND_INDEX_OUT_OF_DATE), [])


class FindingOrderTests(CheckTestCase):
    def test_findings_are_grouped_by_kind_then_path(self):
        self.write_config(require_coverage=True)
        self.write("docs/broken.md", "---\nid: broken\nbad line\n---\n\nBody\n")
        self.write("src/app.py", "print('v2')\n")
        self.write("other/util.py", "print('x')\n")
        self.git("add", "-A")
        result = self.run_check()
        kinds = [finding.kind for finding in result.findings]
        self.assertEqual(kinds, sorted(kinds, key=check_module.KIND_ORDER.index))

    def test_summary_shape(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")
        result = self.run_check()
        self.assertEqual(
            sorted(result.summary),
            [
                "changed_paths",
                "code_paths",
                "doc_paths",
                "docs_indexed",
                "findings",
                "head",
                "impacted",
                "stale",
                "uncovered",
            ],
        )
        self.assertEqual(result.summary["head"], self.head())


class ShaComparisonTests(unittest.TestCase):
    def test_prefix_equality_both_directions(self):
        self.assertTrue(check_module.sha_satisfies("3d8b6cd", "3d8b6cdaaaa"))
        self.assertTrue(check_module.sha_satisfies("3d8b6cdaaaa", "3d8b6cd"))
        self.assertTrue(check_module.sha_satisfies("3D8B6CD", "3d8b6cd"))

    def test_mismatch(self):
        self.assertFalse(check_module.sha_satisfies("0000000", "3d8b6cd"))
        self.assertFalse(check_module.sha_satisfies("3d8b6cd", "3d8b6ce"))

    def test_missing_values(self):
        self.assertFalse(check_module.sha_satisfies(None, "3d8b6cd"))
        self.assertFalse(check_module.sha_satisfies("3d8b6cd", None))
        self.assertFalse(check_module.sha_satisfies("", ""))

    def test_short_values_require_exact_equality(self):
        self.assertFalse(check_module.sha_satisfies("3d8", "3d8b6cd"))
        self.assertTrue(check_module.sha_satisfies("3d8", "3d8"))


class EmptyRepoTests(support.TempRepoTestCase):
    def test_check_works_before_the_first_commit(self):
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "x = 1\n")
        index_module.write_index(self.root, self.config(), head_sha=None)
        # Stage only the code file: staging the doc too would satisfy it by edit.
        self.git("add", "--", "src/app.py")
        result = check_module.run_check(self.root, self.config(), env={})
        self.assertIsNone(result.summary["head"])
        self.assertIn("src/app.py", result.code_paths)
        stale = [f for f in result.findings if f.kind == KIND_STALE]
        self.assertEqual([f.doc_id for f in stale], ["component"])


class ImpactedLookupTests(CheckTestCase):
    def test_lookup_reports_matching_docs_and_patterns(self):
        entries = check_module.impacted_for_paths(
            self.root, self.config(), ["src/app.py", "other/util.py", "Cargo.lock"]
        )
        self.assertEqual([entry["path"] for entry in entries], ["src/app.py", "other/util.py", "Cargo.lock"])
        self.assertEqual(
            entries[0]["docs"],
            [{"doc_id": "component", "path": "docs/component.md", "matched": ["src/**"]}],
        )
        self.assertEqual(entries[1]["docs"], [])
        self.assertTrue(entries[2]["ignored"])
        self.assertEqual(entries[2]["docs"], [])
        self.assertFalse(any(entry["doc_path"] for entry in entries))

    def test_paths_inside_a_doc_root_are_never_code_paths(self):
        """Regression: `impacted docs/x.md` must not report docs covering docs.

        ``check`` classifies anything under a root as a doc path, so a match
        reported here would promise an impact ``check`` can never produce.
        """
        self.write("docs/meta.md", support.doc_text(doc_id="meta", covers=["docs/**"]))
        entries = check_module.impacted_for_paths(
            self.root, self.config(), ["docs/component.md", "src/app.py"]
        )
        self.assertTrue(entries[0]["doc_path"])
        self.assertEqual(entries[0]["docs"], [])
        self.assertFalse(entries[0]["ignored"])
        self.assertFalse(entries[1]["doc_path"])
        self.assertEqual(
            [doc["doc_id"] for doc in entries[1]["docs"]], ["component"]
        )

        # ... and check agrees: editing a doc impacts nothing.
        self.add("docs/component.md")
        result = self.run_check()
        self.assertEqual(self.stale_ids(result), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

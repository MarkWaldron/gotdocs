"""Portability rules: the code/prose split, every rule, and the repo's own docs.

The scanner's whole value is that it does not cry wolf, so most of this file is
negative tests: braces inside code spans, links inside fences, autolinks,
generics, tab-indented lines inside a fenced block. The final case runs every
document this repository ships through the checker inside a temp copy.
"""

import os
import shutil
import unittest

try:  # works both as a package and under `discover -s tools/gotdocs/tests`
    from . import support
except ImportError:  # pragma: no cover - import shim
    import support
from tools.gotdocs import config as config_module
from tools.gotdocs import portability


REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

FRONTMATTER = "\n".join(
    [
        "---",
        "id: sample",
        "title: Sample",
        "type: doc",
        "summary: A sample document.",
        "covers:",
        "  - src/**",
        "status: current",
        "updated: 2026-01-01",
        "---",
        "",
    ]
)


def document(body):
    """A well-formed document whose body starts on line 11."""
    return FRONTMATTER + body


def rules(issues):
    return [issue.rule for issue in issues]


class RuleMetadataTests(unittest.TestCase):
    def test_rule_names_are_unique(self):
        names = portability.rule_names()
        self.assertEqual(len(names), len(set(names)))

    def test_every_rule_has_a_known_severity_and_targets(self):
        for rule in portability.RULES:
            self.assertIn(rule.severity, portability.SEVERITIES, rule.name)
            self.assertTrue(rule.targets, rule.name)
            for target in rule.targets:
                self.assertIn(target, portability.TARGETS, rule.name)
            self.assertTrue(rule.description)

    def test_mdx_rules_only_apply_to_mdx_targets(self):
        for rule in portability.RULES:
            if rule.name.startswith("mdx-"):
                self.assertEqual(tuple(rule.targets), portability.MDX_TARGETS)

    def test_rules_for_target_is_a_subset(self):
        for target in portability.TARGETS:
            selected = portability.rules_for_target(target)
            self.assertTrue(set(rule.name for rule in selected) <= set(portability.rule_names()))
        self.assertTrue(
            len(portability.rules_for_target("github"))
            < len(portability.rules_for_target("docusaurus"))
        )

    def test_issue_dict_and_location(self):
        issue = portability.Issue("mdx-brace", "docs/a.md", 12, "message", "fix it")
        self.assertEqual(issue.severity, "warn")
        self.assertEqual(issue.located(), "docs/a.md:12: message")
        payload = issue.as_dict()
        self.assertEqual(payload["rule"], "mdx-brace")
        self.assertEqual(payload["line"], 12)
        self.assertEqual(payload["remediation"], "fix it")
        self.assertEqual(payload["targets"], list(portability.MDX_TARGETS))

    def test_issue_without_a_line_still_locates(self):
        issue = portability.Issue("h1-count", "docs/a.md", None, "message", "fix it")
        self.assertEqual(issue.located(), "docs/a.md: message")

    def test_as_findings_carries_rule_and_location(self):
        issues = [portability.Issue("mdx-brace", "docs/a.md", 3, "braces", "escape it")]
        findings = portability.as_findings(issues)
        self.assertEqual(findings[0].kind, "portability")
        self.assertIn("docs/a.md:3", findings[0].message)
        self.assertIn("mdx-brace", findings[0].message)
        self.assertEqual(findings[0].remediation, "escape it")


class H1ConfigTests(unittest.TestCase):
    def test_defaults_to_true_without_a_publish_section(self):
        self.assertTrue(portability.h1_in_body_for(None))
        self.assertTrue(portability.h1_in_body_for(config_module.Config()))

    def test_reads_a_dict_config(self):
        self.assertFalse(portability.h1_in_body_for({"publish": {"h1_in_body": False}}))
        self.assertTrue(portability.h1_in_body_for({"publish": {}}))

    def test_reads_a_config_object_with_a_publish_attribute(self):
        class Stub(object):
            publish = {"h1_in_body": False}

        self.assertFalse(portability.h1_in_body_for(Stub()))


class ScannerTests(unittest.TestCase):
    def test_fenced_code_is_masked(self):
        scan = portability.scan_markdown("prose\n\n```py\n{ not prose }\n```\n")
        self.assertEqual(scan.line_kinds[2], "code")
        self.assertEqual(scan.line_kinds[3], "code")
        self.assertNotIn("{", scan.masked)
        self.assertIn("prose", scan.masked)

    def test_tilde_fence_and_longer_backtick_fence(self):
        text = "~~~\n<div>\n~~~\n\n````text\n```\n{}\n````\n"
        scan = portability.scan_markdown(text)
        self.assertNotIn("<div>", scan.masked)
        self.assertNotIn("{", scan.masked)

    def test_inline_code_span_is_masked(self):
        scan = portability.scan_markdown("use `{a: 1}` and ``a ` b`` here\n")
        self.assertNotIn("{", scan.masked)
        self.assertIn("use", scan.masked)
        self.assertIn("here", scan.masked)

    def test_masking_preserves_offsets(self):
        text = "a `{x}` b\n"
        scan = portability.scan_markdown(text)
        self.assertEqual(len(scan.masked), len(text))
        self.assertEqual(scan.masked.count("\n"), text.count("\n"))

    def test_line_offset_is_added_to_reported_lines(self):
        scan = portability.scan_markdown("# Title\n", line_offset=10)
        self.assertEqual(scan.h1_lines, [11])

    def test_h1_in_a_fence_is_not_a_heading(self):
        scan = portability.scan_markdown("```md\n# Nope\n```\n\n# Yes\n")
        self.assertEqual(scan.h1_lines, [5])

    def test_setext_h1_is_counted(self):
        scan = portability.scan_markdown("Title\n=====\n\ntext\n")
        self.assertEqual(scan.h1_lines, [1])

    def test_links_are_collected_with_line_numbers(self):
        scan = portability.scan_markdown("intro\n\n[a](b.md) and ![i](c.png)\n")
        self.assertEqual([link.dest for link in scan.links], ["b.md", "c.png"])
        self.assertEqual([link.line for link in scan.links], [3, 3])
        self.assertEqual([link.is_image for link in scan.links], [False, True])

    def test_link_inside_a_fence_is_not_collected(self):
        scan = portability.scan_markdown("```md\n[a](missing.md)\n```\n")
        self.assertEqual(scan.links, [])

    def test_reference_definitions_are_collected(self):
        scan = portability.scan_markdown("see [a][ref]\n\n[ref]: ./target.md\n")
        self.assertEqual([link.dest for link in scan.links], ["./target.md"])
        self.assertEqual(scan.links[0].kind, "reference")

    def test_link_with_title_and_angle_brackets(self):
        scan = portability.scan_markdown('[a](<my file.md> "Title")\n')
        self.assertEqual([link.dest for link in scan.links], ["my file.md"])


class LinkRuleTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write("docs/target.md", document("\n# Target\n"))
        self.write("docs/img/logo.png", "not really a png")

    def check(self, body, path="docs/sample.md", **kwargs):
        return portability.check_text(document(body), path, repo_root=self.root, **kwargs)

    def test_link_to_an_existing_file_is_clean(self):
        self.assertEqual(self.check("\n# S\n\nSee [target](target.md).\n"), [])

    def test_link_to_a_missing_file_is_an_error(self):
        issues = self.check("\n# S\n\nSee [gone](missing.md).\n")
        self.assertEqual(rules(issues), ["link-target-missing"])
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].line, 14)
        self.assertIn("docs/missing.md", issues[0].message)

    def test_link_case_mismatch_is_reported_separately(self):
        issues = self.check("\n# S\n\nSee [t](Target.md).\n")
        self.assertEqual(rules(issues), ["link-case-mismatch"])
        self.assertIn("docs/target.md", issues[0].message)

    def test_parent_directory_link_resolves(self):
        self.write("README.md", "# Readme\n")
        self.assertEqual(self.check("\n# S\n\n[up](../README.md)\n"), [])

    def test_link_escaping_the_repository(self):
        issues = self.check("\n# S\n\n[out](../../elsewhere/notes.md)\n")
        self.assertEqual(rules(issues), ["link-escapes-repo"])

    def test_absolute_filesystem_paths(self):
        for destination in ("/Users/mark/notes.md", "file:///tmp/x.md", "~/notes.md"):
            issues = self.check("\n# S\n\n[a](%s)\n" % (destination,))
            self.assertEqual(rules(issues), ["link-absolute-path"], destination)

    def test_windows_absolute_path(self):
        issues = self.check("\n# S\n\n[a](C:\\docs\\notes.md)\n")
        self.assertEqual(rules(issues), ["link-absolute-path"])

    def test_site_absolute_link_is_a_warning(self):
        issues = self.check("\n# S\n\n[a](/guide/intro)\n")
        self.assertEqual(rules(issues), ["link-site-absolute"])
        self.assertEqual(issues[0].severity, "warn")

    def test_external_urls_and_anchors_are_ignored(self):
        body = (
            "\n# S\n\n[a](https://example.com/x.md) [b](mailto:x@y.z) "
            "[c](#section) [d](//cdn.example.com/x.md)\n"
        )
        self.assertEqual(self.check(body), [])

    def test_anchor_and_query_are_stripped_before_resolving(self):
        self.assertEqual(self.check("\n# S\n\n[a](target.md#heading)\n"), [])
        issues = self.check("\n# S\n\n[a](missing.md#heading)\n")
        self.assertEqual(rules(issues), ["link-target-missing"])

    def test_percent_encoded_paths_are_decoded(self):
        self.write("docs/a b.md", document("\n# A B\n"))
        self.assertEqual(self.check("\n# S\n\n[a](a%20b.md)\n"), [])

    def test_missing_image_uses_the_image_rule(self):
        issues = self.check("\n# S\n\n![logo](img/missing.png)\n")
        self.assertEqual(rules(issues), ["image-missing"])
        issues = self.check('\n# S\n\n<img src="img/missing.png" alt="x">\n')
        self.assertEqual(rules(issues), ["image-missing"])

    def test_existing_image_is_clean(self):
        self.assertEqual(self.check("\n# S\n\n![logo](img/logo.png)\n"), [])

    def test_reference_definition_targets_are_checked(self):
        issues = self.check("\n# S\n\nSee [a][ref].\n\n[ref]: missing.md\n")
        self.assertEqual(rules(issues), ["link-target-missing"])

    def test_link_inside_code_is_never_checked(self):
        body = "\n# S\n\n```md\n[a](missing.md)\n```\n\nInline `[a](missing.md)` too.\n"
        self.assertEqual(self.check(body), [])

    def test_link_to_a_directory_that_exists(self):
        self.assertEqual(self.check("\n# S\n\n[dir](img/)\n"), [])

    def test_existence_rules_are_skipped_without_a_repo_root(self):
        issues = portability.check_text(document("\n# S\n\n[a](missing.md)\n"), "docs/s.md")
        self.assertEqual(issues, [])


class MdxRuleTests(unittest.TestCase):
    def check(self, body, **kwargs):
        return portability.check_text(document(body), "docs/sample.md", **kwargs)

    def test_brace_in_prose_is_flagged_once_per_line(self):
        issues = self.check("\n# S\n\nUse {braces} carefully {here}.\n")
        self.assertEqual(rules(issues), ["mdx-brace"])
        self.assertEqual(issues[0].line, 14)

    def test_brace_inside_code_is_not_flagged(self):
        body = "\n# S\n\nUse `{a}` and:\n\n```json\n{\"a\": 1}\n```\n"
        self.assertEqual(self.check(body), [])

    def test_capitalized_tag_is_a_bare_component(self):
        issues = self.check("\n# S\n\nReturns a Vec<String> of names.\n")
        self.assertEqual(rules(issues), ["mdx-bare-tag"])
        self.assertEqual(issues[0].severity, "warn")

    def test_unclosed_lowercase_tag(self):
        issues = self.check("\n# S\n\n<div>content\n")
        self.assertEqual(rules(issues), ["mdx-unclosed-tag"])
        self.assertEqual(issues[0].severity, "error")

    def test_matched_tags_are_clean(self):
        self.assertEqual(self.check("\n# S\n\n<div>content</div>\n"), [])

    def test_void_and_self_closing_tags_are_clean(self):
        self.assertEqual(self.check("\n# S\n\nline<br>break<hr/> and <img src=x.png />\n"), [])

    def test_closing_tag_without_an_opener(self):
        issues = self.check("\n# S\n\ntext</span>\n")
        self.assertEqual(rules(issues), ["mdx-unclosed-tag"])
        self.assertIn("</span>", issues[0].message)

    def test_tag_never_terminated_on_its_line(self):
        issues = self.check("\n# S\n\nthe <div element is unterminated\n")
        self.assertEqual(rules(issues), ["mdx-unclosed-tag"])
        self.assertIn("never terminated", issues[0].message)

    def test_autolinks_are_not_tags(self):
        body = "\n# S\n\n<https://example.com> and <user@example.com>\n"
        self.assertEqual(self.check(body), [])

    def test_comparisons_in_prose_are_not_tags(self):
        self.assertEqual(self.check("\n# S\n\nwhen a < b, stop\n"), [])

    def test_html_comment_is_flagged_for_mdx(self):
        issues = self.check("\n# S\n\n<!-- a note -->\n")
        self.assertEqual(rules(issues), ["mdx-html-comment"])

    def test_comment_contents_are_masked(self):
        issues = self.check("\n# S\n\n<!-- {braces} and <div> -->\n")
        self.assertEqual(rules(issues), ["mdx-html-comment"])

    def test_mdx_rules_disappear_for_non_mdx_targets(self):
        body = "\n# S\n\nUse {braces} and <div>text\n"
        self.assertEqual(self.check(body, targets=("mkdocs", "github")), [])
        self.assertTrue(self.check(body, targets=("docusaurus",)))


class CodeBlockRuleTests(unittest.TestCase):
    def check(self, body, **kwargs):
        return portability.check_text(document(body), "docs/sample.md", **kwargs)

    def test_fence_without_a_language(self):
        issues = self.check("\n# S\n\n```\nplain\n```\n")
        self.assertEqual(rules(issues), ["fence-language-missing"])
        self.assertEqual(issues[0].line, 14)

    def test_fence_with_a_language_is_clean(self):
        self.assertEqual(self.check("\n# S\n\n```sh\nls\n```\n"), [])
        self.assertEqual(self.check("\n# S\n\n~~~text\nls\n~~~\n"), [])

    def test_tab_indented_code_block(self):
        issues = self.check("\n# S\n\nExample:\n\n\tcode line\n\tmore\n")
        self.assertEqual(rules(issues), ["code-block-tab-indent"])

    def test_tabs_inside_a_fence_are_not_flagged(self):
        self.assertEqual(self.check("\n# S\n\n```text\n\tM\tfile.py\n```\n"), [])

    def test_space_indented_code_is_not_flagged_for_tabs(self):
        self.assertEqual(self.check("\n# S\n\nExample:\n\n    code line\n"), [])

    def test_indented_list_continuation_is_not_a_code_block(self):
        body = "\n# S\n\n- item\n\n\tcontinuation of the item\n"
        self.assertEqual(self.check(body), [])


class HeadingRuleTests(unittest.TestCase):
    def check(self, body, **kwargs):
        return portability.check_text(document(body), "docs/sample.md", **kwargs)

    def test_single_h1_is_expected_by_default(self):
        self.assertEqual(self.check("\n# S\n\ntext\n"), [])

    def test_missing_h1_when_required(self):
        issues = self.check("\n## S\n\ntext\n")
        self.assertEqual(rules(issues), ["h1-count"])
        self.assertIn("no '# ' heading", issues[0].message)

    def test_two_h1s_when_one_is_required(self):
        issues = self.check("\n# One\n\n# Two\n")
        self.assertEqual(rules(issues), ["h1-count"])
        self.assertIn("2 '# ' headings", issues[0].message)
        self.assertEqual(issues[0].line, 14)

    def test_h1_forbidden_when_h1_in_body_is_false(self):
        issues = self.check("\n# S\n\ntext\n", h1_in_body=False)
        self.assertEqual(rules(issues), ["h1-count"])
        self.assertIn("renders twice", issues[0].message)

    def test_no_h1_is_clean_when_h1_in_body_is_false(self):
        self.assertEqual(self.check("\n## S\n\ntext\n", h1_in_body=False), [])


class FrontmatterRuleTests(unittest.TestCase):
    def test_reserved_extra_key_is_reported(self):
        text = FRONTMATTER.replace("updated: 2026-01-01", "updated: 2026-01-01\nlayout: post")
        issues = portability.check_text(text + "\n# S\n", "docs/sample.md")
        self.assertEqual(rules(issues), ["frontmatter-reserved-key"])
        self.assertIn("jekyll", issues[0].message)
        self.assertEqual(issues[0].line, 10)

    def test_gotdocs_keys_are_not_reported_on_a_default_run(self):
        self.assertEqual(portability.check_text(document("\n# S\n"), "docs/s.md"), [])

    def test_type_collides_with_hugo_when_the_target_is_explicit(self):
        issues = portability.check_text(document("\n# S\n"), "docs/s.md", targets=("hugo",))
        self.assertEqual(rules(issues), ["frontmatter-reserved-key"])
        self.assertIn("content type", issues[0].message)
        self.assertIn("export", issues[0].remediation)

    def test_reserved_key_is_scoped_to_the_selected_targets(self):
        text = FRONTMATTER.replace("updated: 2026-01-01", "updated: 2026-01-01\nlayout: post")
        body = text + "\n# S\n"
        self.assertTrue(portability.check_text(body, "docs/s.md", targets=("jekyll",)))
        self.assertEqual(portability.check_text(body, "docs/s.md", targets=("mkdocs",)), [])


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.issues = [
            portability.Issue("mdx-brace", "a.md", 1, "m", "r"),
            portability.Issue("link-target-missing", "a.md", 2, "m", "r"),
        ]

    def test_filter_by_severity(self):
        self.assertEqual(
            rules(portability.filter_issues(self.issues, severity="error")),
            ["link-target-missing"],
        )

    def test_filter_by_rule_and_target(self):
        self.assertEqual(
            rules(portability.filter_issues(self.issues, rules=["mdx-brace"])), ["mdx-brace"]
        )
        self.assertEqual(
            rules(portability.filter_issues(self.issues, targets=["github"])),
            ["link-target-missing"],
        )


class DocSetTests(support.TempRepoTestCase):
    def test_check_doc_set_covers_every_document(self):
        self.write("docs/a.md", support.doc_text(doc_id="a", body="\n# A\n\n[x](missing.md)\n"))
        self.write("docs/b.md", support.doc_text(doc_id="b", body="\n# B\n\nfine\n"))
        issues = portability.check_doc_set(self.root, self.config())
        self.assertEqual(rules(issues), ["link-target-missing"])
        self.assertEqual(issues[0].path, "docs/a.md")

    def test_issues_are_sorted_by_path_then_line(self):
        self.write(
            "docs/b.md",
            support.doc_text(doc_id="b", body="\n# B\n\n[x](missing.md)\n\n```\nx\n```\n"),
        )
        self.write("docs/a.md", support.doc_text(doc_id="a", body="\n# A\n\n{brace}\n"))
        issues = portability.check_doc_set(self.root, self.config())
        self.assertEqual([issue.path for issue in issues], ["docs/a.md", "docs/b.md", "docs/b.md"])
        self.assertTrue(issues[1].line < issues[2].line)

    def test_an_unreadable_document_is_reported_not_crashed(self):
        path = os.path.join(self.root, "docs", "bad.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"---\nid: bad\n---\n\xff\xfe not utf-8\n")
        issues = portability.check_doc_set(self.root, self.config())
        self.assertIn("document-unreadable", rules(issues))
        self.assertEqual(
            [issue.severity for issue in issues if issue.rule == "document-unreadable"], ["error"]
        )

    def test_a_document_with_no_frontmatter_is_still_scanned(self):
        self.write("docs/a.md", "# A\n\n[x](missing.md)\n")
        issues = portability.check_doc_set(self.root, self.config())
        self.assertEqual(rules(issues), ["link-target-missing"])


class RepoFixtureTests(unittest.TestCase):
    """Run the checker over this repository's own documents, in a temp copy."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="gotdocs-portability-"))
        for name in ("docs", "runbooks", "onboarding", "dependencies"):
            source = os.path.join(REPO_ROOT, name)
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(cls.root, name))
        os.makedirs(os.path.join(cls.root, config_module.CONFIG_DIR))
        shutil.copyfile(
            os.path.join(REPO_ROOT, config_module.CONFIG_PATH),
            os.path.join(cls.root, config_module.CONFIG_PATH),
        )
        shutil.copyfile(os.path.join(REPO_ROOT, "README.md"), os.path.join(cls.root, "README.md"))
        cls.config = config_module.load(cls.root)
        cls.issues = portability.check_doc_set(cls.root, cls.config)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def test_the_fixture_actually_has_documents(self):
        from tools.gotdocs import index as index_module

        docs = index_module.scan(self.root, self.config).docs
        self.assertGreaterEqual(len(docs), 8)

    def test_shipped_documents_have_no_error_severity_issues(self):
        errors = portability.filter_issues(self.issues, severity="error")
        self.assertEqual(
            [issue.located() for issue in errors],
            [],
            "the repo's own docs must stay portable",
        )

    def test_no_false_mdx_positives_in_the_shipped_documents(self):
        mdx = [issue for issue in self.issues if issue.rule.startswith("mdx-")]
        self.assertEqual([issue.located() for issue in mdx], [])

    def test_results_are_deterministic(self):
        again = portability.check_doc_set(self.root, self.config)
        self.assertEqual(
            [issue.as_dict() for issue in again], [issue.as_dict() for issue in self.issues]
        )

    def test_originals_are_untouched(self):
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, "docs", "architecture.md")))
        self.assertNotEqual(os.path.realpath(self.root), os.path.realpath(REPO_ROOT))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

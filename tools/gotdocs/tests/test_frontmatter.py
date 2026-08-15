"""Frontmatter: parsing the supported YAML subset, rejecting everything else,
and byte-preserving in-place rewrites of ``updated`` / ``verified_at``.
"""

import io
import os
import shutil
import tempfile
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import frontmatter as fm
from tools.gotdocs.errors import FrontmatterError


VALID = """---
id: sample-doc
title: Sample Doc
type: doc
summary: "A summary with: a colon, a # hash and 'quotes' inside."
covers:
  - src/**
  - bin/tool
owners: ["@mark", '@acme/platform']
tags: [cli, tooling]
status: current
updated: 2026-08-14
verified_at: 3d8b6cd
sidebar_position: 3
empty_value:
flow_empty: []
# a full-line comment

trailing: value  # trailing comment
---

# Sample Doc

Body.
"""


# (name, frontmatter body, substring expected in the reported message)
REJECTED = [
    ("nested map", "owner:\n  name: mark\n", "nested mappings"),
    ("list of maps", "items:\n  - name: a\n", "lists of mappings"),
    ("block scalar pipe", "summary: |\n", "block scalars"),
    ("block scalar fold", "summary: >\n", "block scalars"),
    ("block scalar strip", "summary: |-\n", "block scalars"),
    ("anchor", "base: &anchor value\n", "anchors"),
    ("alias", "other: *anchor\n", "aliases"),
    ("tag", "count: !!str 3\n", "tags"),
    ("tab indent", "covers:\n\t- src/**\n", "tabs"),
    ("inline map", "owner: {name: mark}\n", "inline mappings"),
    ("top level list", "- one\n", "top-level lists"),
    ("garbage line", "this is not yaml\n", "expected 'key: value'"),
    ("duplicate key", "id: a\nid: b\n", "duplicate key"),
    ("unterminated double quote", 'title: "unclosed\n', "unterminated double-quoted"),
    ("unterminated single quote", "title: 'unclosed\n", "unterminated single-quoted"),
    ("unterminated flow list", "tags: [a, b\n", "unterminated flow list"),
    ("nested flow list", "tags: [[a], b]\n", "nested structures"),
    ("orphan list item", "  - orphan\n", "does not belong to any key"),
    ("empty list item", "tags:\n  -\n", "empty list item"),
]


def block(inner):
    return "---\n" + inner + "---\n\nBody\n"


class ParseValidTests(unittest.TestCase):
    def setUp(self):
        self.parsed = fm.parse_text(VALID, "docs/sample.md")

    def test_no_issues(self):
        self.assertEqual([issue.located() for issue in self.parsed.issues], [])
        self.assertTrue(self.parsed.present)

    def test_scalars(self):
        self.assertEqual(self.parsed.get("id"), "sample-doc")
        self.assertEqual(self.parsed.get("title"), "Sample Doc")
        self.assertEqual(self.parsed.get("status"), "current")
        self.assertEqual(self.parsed.get("updated"), "2026-08-14")
        self.assertEqual(self.parsed.get("verified_at"), "3d8b6cd")

    def test_quoted_scalar_keeps_hash_and_colon(self):
        self.assertEqual(
            self.parsed.get("summary"),
            "A summary with: a colon, a # hash and 'quotes' inside.",
        )

    def test_block_list(self):
        self.assertEqual(self.parsed.get("covers"), ["src/**", "bin/tool"])

    def test_flow_lists(self):
        self.assertEqual(self.parsed.get("owners"), ["@mark", "@acme/platform"])
        self.assertEqual(self.parsed.get("tags"), ["cli", "tooling"])
        self.assertEqual(self.parsed.get("flow_empty"), [])

    def test_unknown_keys_pass_through(self):
        self.assertEqual(self.parsed.get("sidebar_position"), "3")

    def test_empty_value_and_trailing_comment(self):
        self.assertEqual(self.parsed.get("empty_value"), "")
        self.assertEqual(self.parsed.get("trailing"), "value")

    def test_line_numbers(self):
        self.assertEqual(self.parsed.line_of("id"), 2)
        self.assertEqual(self.parsed.line_of("title"), 3)
        self.assertEqual(self.parsed.line_of("verified_at"), 13)

    def test_body_is_everything_after_the_close(self):
        self.assertEqual(self.parsed.body, "\n# Sample Doc\n\nBody.\n")

    def test_get_list_coercions(self):
        self.assertEqual(self.parsed.get_list("covers"), ["src/**", "bin/tool"])
        self.assertEqual(self.parsed.get_list("empty_value"), [])
        self.assertEqual(self.parsed.get_list("absent"), [])
        self.assertIsNone(self.parsed.get_list("title"))


class ParseEdgeTests(unittest.TestCase):
    def test_crlf_frontmatter(self):
        text = "---\r\nid: a\r\ntitle: b\r\n---\r\n\r\nBody\r\n"
        parsed = fm.parse_text(text, "docs/a.md")
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.get("id"), "a")

    def test_missing_frontmatter(self):
        parsed = fm.parse_text("# Just a heading\n", "docs/a.md")
        self.assertFalse(parsed.present)
        self.assertIn("missing frontmatter", parsed.issues[0].message)
        self.assertEqual(parsed.issues[0].line, 1)

    def test_frontmatter_not_first(self):
        parsed = fm.parse_text("\n---\nid: a\n---\n", "docs/a.md")
        self.assertFalse(parsed.present)
        self.assertIn("missing frontmatter", parsed.issues[0].message)

    def test_bom_is_reported(self):
        parsed = fm.parse_text("﻿---\nid: a\n---\n", "docs/a.md")
        self.assertTrue(any("BOM" in issue.message for issue in parsed.issues))

    def test_unterminated_frontmatter(self):
        parsed = fm.parse_text("---\nid: a\ntitle: b\n", "docs/a.md")
        self.assertFalse(parsed.present)
        self.assertIn("unterminated", parsed.issues[0].message)

    def test_empty_frontmatter_block(self):
        parsed = fm.parse_text("---\n---\n\nBody\n", "docs/a.md")
        self.assertTrue(parsed.present)
        self.assertEqual(parsed.data, {})
        self.assertEqual(parsed.issues, [])

    def test_horizontal_rule_in_body_is_not_a_delimiter(self):
        parsed = fm.parse_text("---\nid: a\n---\n\ntext\n\n---\n\nmore\n", "docs/a.md")
        self.assertEqual(parsed.get("id"), "a")
        self.assertIn("---", parsed.body)

    def test_single_quote_escaping(self):
        parsed = fm.parse_text("---\ntitle: 'it''s fine'\n---\n", "docs/a.md")
        self.assertEqual(parsed.get("title"), "it's fine")

    def test_double_quote_escaping(self):
        parsed = fm.parse_text('---\ntitle: "a \\"b\\" c"\n---\n', "docs/a.md")
        self.assertEqual(parsed.get("title"), 'a "b" c')

    def test_comment_only_when_preceded_by_space(self):
        parsed = fm.parse_text("---\nid: a#b\n---\n", "docs/a.md")
        self.assertEqual(parsed.get("id"), "a#b")

    def test_list_item_with_url_is_not_a_map(self):
        parsed = fm.parse_text("---\nlinks:\n  - https://example.com/x\n---\n", "docs/a.md")
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.get("links"), ["https://example.com/x"])

    def test_quoted_list_items(self):
        parsed = fm.parse_text('---\ncovers:\n  - "src/**"\n  - \'bin/x\'\n---\n', "docs/a.md")
        self.assertEqual(parsed.get("covers"), ["src/**", "bin/x"])


class RejectionTests(unittest.TestCase):
    def test_unsupported_constructs_are_reported(self):
        for name, inner, expected in REJECTED:
            with self.subTest(construct=name):
                parsed = fm.parse_text(block(inner), "docs/a.md")
                messages = " | ".join(issue.message for issue in parsed.issues)
                self.assertTrue(
                    parsed.issues, "%s should have produced an issue" % (name,)
                )
                self.assertIn(expected, messages)

    def test_issues_carry_file_and_line(self):
        parsed = fm.parse_text(block("id: a\nowner:\n  name: mark\n"), "docs/a.md")
        self.assertEqual(len(parsed.issues), 1)
        issue = parsed.issues[0]
        self.assertEqual(issue.path, "docs/a.md")
        self.assertEqual(issue.line, 4)
        self.assertEqual(issue.located(), "docs/a.md:4: " + issue.message)

    def test_multiple_issues_are_all_collected(self):
        parsed = fm.parse_text(block("owner:\n  name: mark\nbad line here\n"), "docs/a.md")
        self.assertEqual(len(parsed.issues), 2)

    def test_as_error_produces_a_frontmatter_error(self):
        parsed = fm.parse_text(block("owner:\n  name: mark\n"), "docs/a.md")
        error = parsed.issues[0].as_error()
        self.assertIsInstance(error, FrontmatterError)
        self.assertEqual(error.exit_code, 2)
        self.assertIn("docs/a.md:3:", error.located())


class BlockListCommentTests(unittest.TestCase):
    """Regression: a trailing ``#`` comment on a block-list item.

    Comments were stripped from ``key: value`` lines (and so from flow lists)
    but not from block-list items, so ``- src/**  # the CLI`` became a glob
    with the comment glued on: it matched nothing, silently, with no lint
    error. The two list syntaxes now agree.
    """

    def test_comment_is_stripped_from_a_block_list_item(self):
        parsed = fm.parse_text(
            block("covers:\n  - src/**   # the CLI\n  - bin/tool\n"), "docs/a.md"
        )
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.data["covers"], ["src/**", "bin/tool"])

    def test_comment_after_a_quoted_block_list_item(self):
        parsed = fm.parse_text(block('covers:\n  - "src/**" # c\n'), "docs/a.md")
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.data["covers"], ["src/**"])

    def test_hash_inside_a_quoted_item_survives(self):
        parsed = fm.parse_text(block('tags:\n  - "c#"\n  - a#b\n'), "docs/a.md")
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.data["tags"], ["c#", "a#b"])

    def test_block_and_flow_lists_agree(self):
        flow = fm.parse_text(block('owners: ["@mark"]   # inline\n'), "docs/a.md")
        blocked = fm.parse_text(block('owners:\n  - "@mark"   # inline\n'), "docs/a.md")
        self.assertEqual(flow.data["owners"], blocked.data["owners"])


class SecondDelimiterTests(unittest.TestCase):
    """Regression: a second ``---`` mid-frontmatter is a lint error.

    The parser closed the block at the first ``---`` and silently dropped the
    remaining keys into the body, so ``verified_at`` written after it was
    invisible and the document could never be satisfied by ``verify``.
    """

    MULTI = (
        "---\n"
        "id: x\n"
        "title: T\n"
        "---\n"
        "verified_at: deadbeef\n"
        'owners: ["@mark"]\n'
        "---\n"
        "\n"
        "# Body\n"
    )

    def test_second_delimiter_is_reported(self):
        parsed = fm.parse_text(self.MULTI, "docs/a.md")
        self.assertEqual(len(parsed.issues), 1)
        self.assertEqual(parsed.issues[0].line, 7)
        self.assertIn("second '---'", parsed.issues[0].message)

    def test_ordinary_horizontal_rule_is_not_reported(self):
        text = "---\nid: x\n---\n\nSome prose.\n\n---\n\nMore prose.\n"
        self.assertEqual(fm.parse_text(text, "docs/a.md").issues, [])

    def test_prose_before_a_rule_is_not_reported(self):
        text = "---\nid: x\n---\nA sentence of prose, no colon.\n---\n"
        self.assertEqual(fm.parse_text(text, "docs/a.md").issues, [])


class RewriteTextTests(unittest.TestCase):
    def test_replaces_only_the_named_lines(self):
        result = fm.rewrite_text(
            VALID, {"updated": "2026-09-01", "verified_at": "abcdef1234"}, "docs/a.md"
        )
        before = VALID.splitlines()
        after = result.splitlines()
        self.assertEqual(len(before), len(after))
        differing = [i for i in range(len(before)) if before[i] != after[i]]
        self.assertEqual([before[i].split(":")[0] for i in differing], ["updated", "verified_at"])
        self.assertIn("updated: 2026-09-01", result)
        self.assertIn("verified_at: abcdef1234", result)

    def test_preserves_quoting_style(self):
        text = "---\nupdated: \"2026-01-01\"\nverified_at: 'aaaaaaa'\n---\nBody\n"
        result = fm.rewrite_text(text, {"updated": "2026-02-02", "verified_at": "bbbbbbb"})
        self.assertIn('updated: "2026-02-02"', result)
        self.assertIn("verified_at: 'bbbbbbb'", result)

    def test_preserves_trailing_comment(self):
        text = "---\nupdated: 2026-01-01  # set by hand\n---\nBody\n"
        result = fm.rewrite_text(text, {"updated": "2026-02-02"})
        self.assertIn("updated: 2026-02-02 # set by hand", result)

    def test_preserves_crlf(self):
        text = "---\r\nid: a\r\nupdated: 2026-01-01\r\n---\r\nBody\r\n"
        result = fm.rewrite_text(text, {"updated": "2026-02-02"})
        self.assertEqual(result.count("\r\n"), text.count("\r\n"))
        self.assertIn("updated: 2026-02-02\r\n", result)

    def test_inserted_key_uses_cr_only_line_endings(self):
        # Regression: a CR-only (classic Mac) file used to gain a stray "\n"
        # when an absent key was appended, so the file ended up with two
        # different line endings.
        text = "---\rid: a\rupdated: 2026-01-01\r---\r\rBody\r"
        result = fm.rewrite_text(text, {"verified_at": "3d8b6cd"})
        self.assertEqual(
            result, "---\rid: a\rupdated: 2026-01-01\rverified_at: 3d8b6cd\r---\r\rBody\r"
        )
        self.assertNotIn("\n", result)

    def test_inserted_key_uses_crlf_line_endings(self):
        text = "---\r\nid: a\r\nupdated: 2026-01-01\r\n---\r\nBody\r\n"
        result = fm.rewrite_text(text, {"verified_at": "3d8b6cd"})
        self.assertIn("verified_at: 3d8b6cd\r\n", result)
        self.assertEqual(result.count("\n"), result.count("\r\n"))

    def test_inserts_missing_key_before_the_closing_delimiter(self):
        text = "---\nid: a\nupdated: 2026-01-01\n---\n\nBody\n"
        result = fm.rewrite_text(text, {"verified_at": "3d8b6cd"})
        self.assertEqual(
            result,
            "---\nid: a\nupdated: 2026-01-01\nverified_at: 3d8b6cd\n---\n\nBody\n",
        )

    def test_body_is_untouched(self):
        text = "---\nupdated: 2026-01-01\n---\n\n# Title\n\nupdated: 2026-01-01\n"
        result = fm.rewrite_text(text, {"updated": "2026-02-02"})
        self.assertTrue(result.endswith("\n# Title\n\nupdated: 2026-01-01\n"))

    def test_round_trip_is_stable(self):
        once = fm.rewrite_text(VALID, {"updated": "2026-09-01", "verified_at": "abcdef1"})
        twice = fm.rewrite_text(once, {"updated": "2026-09-01", "verified_at": "abcdef1"})
        self.assertEqual(once, twice)

    def test_rewrite_reparses_cleanly(self):
        result = fm.rewrite_text(VALID, {"updated": "2026-09-01", "verified_at": "abcdef1"})
        parsed = fm.parse_text(result, "docs/a.md")
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.get("updated"), "2026-09-01")
        self.assertEqual(parsed.get("verified_at"), "abcdef1")
        self.assertEqual(parsed.get("covers"), ["src/**", "bin/tool"])

    def test_rewrite_requires_frontmatter(self):
        with self.assertRaises(FrontmatterError):
            fm.rewrite_text("no frontmatter here\n", {"updated": "2026-01-01"})


class RewriteFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="gotdocs-fm-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.path = os.path.join(self.directory, "doc.md")
        with io.open(self.path, "wb") as handle:
            handle.write(VALID.encode("utf-8"))

    def read(self):
        with io.open(self.path, "rb") as handle:
            return handle.read()

    def test_only_the_two_lines_change_on_disk(self):
        original = self.read()
        changed = fm.rewrite_fields(self.path, {"updated": "2026-09-01"}, "docs/doc.md")
        self.assertTrue(changed)
        updated = self.read()
        self.assertNotEqual(original, updated)
        original_lines = original.decode("utf-8").splitlines()
        updated_lines = updated.decode("utf-8").splitlines()
        differing = [
            i for i in range(len(original_lines)) if original_lines[i] != updated_lines[i]
        ]
        self.assertEqual(len(differing), 1)
        self.assertTrue(updated_lines[differing[0]].startswith("updated:"))

    def test_writing_the_same_value_is_a_no_op(self):
        self.assertFalse(fm.rewrite_fields(self.path, {"updated": "2026-08-14"}))
        self.assertEqual(self.read(), VALID.encode("utf-8"))

    def test_refuses_other_keys(self):
        with self.assertRaises(FrontmatterError):
            fm.rewrite_fields(self.path, {"title": "nope"})
        self.assertEqual(self.read(), VALID.encode("utf-8"))

    def test_file_mode_is_preserved(self):
        os.chmod(self.path, 0o640)
        fm.rewrite_fields(self.path, {"updated": "2026-09-02"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)

    def test_missing_file_is_a_frontmatter_error(self):
        with self.assertRaises(FrontmatterError):
            fm.rewrite_fields(os.path.join(self.directory, "nope.md"), {"updated": "x"})

    def test_no_temp_files_are_left_behind(self):
        fm.rewrite_fields(self.path, {"updated": "2026-09-03"})
        self.assertEqual(sorted(os.listdir(self.directory)), ["doc.md"])


class BlockScalarContinuationTests(unittest.TestCase):
    """Regression: the indented body of a block scalar was reported line by line."""

    def issues(self, text):
        return [issue.message for issue in fm.parse(text, path="d.md").issues]

    def test_a_block_scalar_reports_once(self):
        text = (
            "---\n"
            "id: d\n"
            "notes: |\n"
            "  a block scalar\n"
            "  continued here\n"
            "---\n"
            "\n# D\n"
        )
        messages = self.issues(text)
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("block scalars", messages[0])

    def test_a_folded_scalar_reports_once(self):
        text = "---\nid: d\nnotes: >\n  folded\n  text\n---\n\n# D\n"
        self.assertEqual(len(self.issues(text)), 1)

    def test_the_next_key_is_still_parsed_and_still_checked(self):
        text = (
            "---\n"
            "id: d\n"
            "notes: |\n"
            "  swallowed\n"
            "title: D\n"
            "other:\n"
            "  nested: yes\n"
            "---\n"
            "\n# D\n"
        )
        parsed = fm.parse(text, path="d.md")
        self.assertEqual(parsed.data.get("title"), "D")
        messages = [issue.message for issue in parsed.issues]
        self.assertEqual(len(messages), 2, messages)
        self.assertIn("block scalars", messages[0])
        self.assertIn("nested mappings", messages[1])



if __name__ == "__main__":  # pragma: no cover
    unittest.main()

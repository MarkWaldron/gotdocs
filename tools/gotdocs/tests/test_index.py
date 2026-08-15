"""Index generation: reproducibility, ordering, validation and the INDEX.md shape."""

import json
import os
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import index as index_module
from tools.gotdocs.config import INDEX_JSON_PATH, INDEX_MD_PATH


class IndexDeterminismTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write("docs/beta.md", support.doc_text(doc_id="beta", title="Beta", covers=["src/b/**"]))
        self.write("docs/alpha.md", support.doc_text(doc_id="alpha", title="Alpha", covers=["src/a/**"]))
        self.write(
            "docs/nested/gamma.md",
            support.doc_text(doc_id="gamma", title="Gamma", doc_type="runbook", covers=[]),
        )

    def test_generating_twice_is_byte_identical(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        first_json = support.read_bytes(self.root, INDEX_JSON_PATH)
        first_md = support.read_bytes(self.root, INDEX_MD_PATH)

        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.assertEqual(support.read_bytes(self.root, INDEX_JSON_PATH), first_json)
        self.assertEqual(support.read_bytes(self.root, INDEX_MD_PATH), first_md)

    def test_second_run_reports_no_changes(self):
        _docs, changed = index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.assertEqual(sorted(changed), [INDEX_MD_PATH, INDEX_JSON_PATH])
        _docs, changed = index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.assertEqual(changed, [])

    def test_rendering_is_pure(self):
        config = self.config()
        first = index_module.render_json(index_module.build_payload(index_module.scan(self.root, config), "abc1234"))
        second = index_module.render_json(index_module.build_payload(index_module.scan(self.root, config), "abc1234"))
        self.assertEqual(first, second)

    def test_docs_are_sorted_by_id(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        payload = json.loads(support.read(self.root, INDEX_JSON_PATH))
        self.assertEqual([doc["id"] for doc in payload["docs"]], ["alpha", "beta", "gamma"])

    def test_json_formatting_contract(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        text = support.read(self.root, INDEX_JSON_PATH)
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('\n  "version": 1,', text)
        self.assertIn('\n  "generated_at_sha": "aaaaaaa",', text)

    def test_generated_at_sha_is_the_only_volatile_field(self):
        config = self.config()
        left = index_module.build_payload(index_module.scan(self.root, config), "aaaaaaa")
        right = index_module.build_payload(index_module.scan(self.root, config), "bbbbbbb")
        self.assertNotEqual(left, right)
        left.pop("generated_at_sha")
        right.pop("generated_at_sha")
        self.assertEqual(left, right)

    def test_regenerating_at_a_new_head_does_not_touch_the_file(self):
        """Regression: the committed index must not be permanently 'drifted'.

        A commit can never contain its own sha, so stamping HEAD on every run
        rewrote ``generated_at_sha`` on every later checkout and the CI
        freshness gate (a byte-level ``git status``) failed on every PR.
        """
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        before = support.read_bytes(self.root, INDEX_JSON_PATH)

        _docs, changed = index_module.write_index(
            self.root, self.config(), head_sha="bbbbbbb"
        )
        self.assertEqual(changed, [])
        self.assertEqual(support.read_bytes(self.root, INDEX_JSON_PATH), before)
        payload = json.loads(support.read(self.root, INDEX_JSON_PATH))
        self.assertEqual(payload["generated_at_sha"], "aaaaaaa")

    def test_a_document_change_restamps_the_sha(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.write("docs/delta.md", support.doc_text(doc_id="delta", covers=[]))
        _docs, changed = index_module.write_index(
            self.root, self.config(), head_sha="bbbbbbb"
        )
        self.assertEqual(sorted(changed), [INDEX_MD_PATH, INDEX_JSON_PATH])
        payload = json.loads(support.read(self.root, INDEX_JSON_PATH))
        self.assertEqual(payload["generated_at_sha"], "bbbbbbb")

    def test_a_corrupt_index_is_restamped_rather_than_preserved(self):
        self.write(INDEX_JSON_PATH, "{ not json")
        index_module.write_index(self.root, self.config(), head_sha="bbbbbbb")
        payload = json.loads(support.read(self.root, INDEX_JSON_PATH))
        self.assertEqual(payload["generated_at_sha"], "bbbbbbb")

    def test_index_is_current_after_writing(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        ok, stale = index_module.index_is_current(self.root, self.config(), head_sha="aaaaaaa")
        self.assertTrue(ok)
        self.assertEqual(stale, [])

    def test_index_is_current_across_a_sha_change(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        ok, stale = index_module.index_is_current(self.root, self.config(), head_sha="ffffffff")
        self.assertTrue(ok, stale)

    def test_index_becomes_stale_when_a_doc_changes(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.write("docs/delta.md", support.doc_text(doc_id="delta", covers=[]))
        ok, stale = index_module.index_is_current(self.root, self.config(), head_sha="aaaaaaa")
        self.assertFalse(ok)
        self.assertEqual(sorted(stale), [INDEX_MD_PATH, INDEX_JSON_PATH])

    def test_missing_index_files_are_stale(self):
        ok, stale = index_module.index_is_current(self.root, self.config(), head_sha="aaaaaaa")
        self.assertFalse(ok)
        self.assertEqual(sorted(stale), [INDEX_MD_PATH, INDEX_JSON_PATH])

    def test_corrupt_index_json_is_stale_not_a_crash(self):
        index_module.write_index(self.root, self.config(), head_sha="aaaaaaa")
        self.write(INDEX_JSON_PATH, "{ not json")
        ok, stale = index_module.index_is_current(self.root, self.config(), head_sha="aaaaaaa")
        self.assertFalse(ok)
        self.assertIn(INDEX_JSON_PATH, stale)


class IndexContentTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write(
            "docs/one.md",
            support.doc_text(
                doc_id="one",
                title="One",
                summary="The first document.",
                covers=["src/**", "bin/tool"],
                owners=["@mark"],
                tags=["cli"],
                verified_at="3d8b6cd",
                extra_lines=["sidebar_position: 4"],
            ),
        )

    def test_entry_fields(self):
        doc_set = index_module.scan(self.root, self.config())
        payload = index_module.build_payload(doc_set, "abc1234")
        entry = payload["docs"][0]
        self.assertEqual(entry["id"], "one")
        self.assertEqual(entry["path"], "docs/one.md")
        self.assertEqual(entry["type"], "doc")
        self.assertEqual(entry["title"], "One")
        self.assertEqual(entry["summary"], "The first document.")
        self.assertEqual(entry["status"], "current")
        self.assertEqual(entry["covers"], ["src/**", "bin/tool"])
        self.assertEqual(entry["owners"], ["@mark"])
        self.assertEqual(entry["tags"], ["cli"])
        self.assertEqual(entry["updated"], "2026-01-01")
        self.assertEqual(entry["verified_at"], "3d8b6cd")

    def test_unknown_keys_are_passed_through_as_extra(self):
        doc_set = index_module.scan(self.root, self.config())
        payload = index_module.build_payload(doc_set, None)
        self.assertEqual(payload["docs"][0]["extra"], {"sidebar_position": "4"})

    def test_markdown_index_is_one_line_per_doc(self):
        # Contract: `- **id** - summary  ·  `path`  ·  covers: pattern, pattern`,
        # exactly one line per document, because this file is read into agent
        # context constantly.
        doc_set = index_module.scan(self.root, self.config())
        text = index_module.render_markdown(doc_set)
        self.assertTrue(text.startswith("# Docs index\n"))
        self.assertIn("Read this file first", text)
        self.assertIn(
            "- **one** - The first document.  ·  `docs/one.md`  ·  "
            "covers: `src/**`, `bin/tool`",
            text,
        )
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\n\n\n", text)

        entries = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(entries), len(doc_set.docs))
        for line in entries:
            self.assertIn("  ·  covers: ", line)

    def test_markdown_index_marks_non_current_status(self):
        self.write("docs/two.md", support.doc_text(doc_id="two", status="deprecated", covers=[]))
        doc_set = index_module.scan(self.root, self.config())
        text = index_module.render_markdown(doc_set)
        self.assertIn("- **two** _(deprecated)_ - ", text)
        self.assertIn("covers: none", text)

    def test_markdown_groups_by_type(self):
        self.write("docs/rb.md", support.doc_text(doc_id="rb", doc_type="runbook", covers=[]))
        doc_set = index_module.scan(self.root, self.config())
        text = index_module.render_markdown(doc_set)
        self.assertLess(text.index("## doc"), text.index("## runbook"))

    def test_empty_repo_renders_an_empty_index(self):
        os.remove(os.path.join(self.root, "docs", "one.md"))
        doc_set = index_module.scan(self.root, self.config())
        text = index_module.render_markdown(doc_set)
        self.assertIn("No documents found", text)


class DecisionRenderingTests(support.TempRepoTestCase):
    """Decision records are indexed like documents, listed unlike them."""

    DECISION = (
        "---\n"
        "id: 0001-retry-budget\n"
        "title: Retry budget is per request\n"
        "type: decision\n"
        "summary: Retries are budgeted once per request.\n"
        "covers: [src/**]\n"
        "symptoms:\n"
        '  - "a POST is retried exactly twice"\n'
        "supersedes: []\n"
        "superseded_by: []\n"
        "status: %s\n"
        "decided_on: 2026-08-01\n"
        "updated: 2026-08-01\n"
        "---\n"
        "\n# Retry budget\n\n## Expected behavior\n\nit retries twice\n"
        "\n## This is a bug, not this decision, if...\n\nit retries more\n"
    )

    def setUp(self):
        super().setUp()
        self.write_config(roots=["docs", "decisions"])
        self.write("docs/one.md", support.doc_text(doc_id="one", covers=["src/**"]))

    def add(self, status="accepted", doc_id="0001-retry-budget"):
        text = self.DECISION % (status,)
        if doc_id != "0001-retry-budget":
            text = text.replace("0001-retry-budget", doc_id)
        self.write("decisions/%s.md" % (doc_id,), text)

    def scan(self):
        return index_module.scan(self.root, self.config())

    def test_decision_fields_are_first_class_not_extra(self):
        self.add()
        entry = [
            doc.as_entry() for doc in self.scan().docs if doc.type == "decision"
        ][0]
        self.assertNotIn("extra", entry)
        self.assertEqual(entry["symptoms"], ["a POST is retried exactly twice"])
        self.assertEqual(entry["decided_on"], "2026-08-01")
        self.assertEqual(entry["supersedes"], [])
        self.assertEqual(entry["superseded_by"], [])

    def test_ordinary_documents_gain_no_new_keys(self):
        self.add()
        entry = [doc.as_entry() for doc in self.scan().docs if doc.id == "one"][0]
        for key in ("symptoms", "decided_on", "supersedes", "superseded_by"):
            self.assertNotIn(key, entry)

    def test_the_decisions_section_carries_no_symptom_text(self):
        self.add()
        text = index_module.render_markdown(self.scan())
        self.assertIn("## Decisions", text)
        self.assertIn("- **0001-retry-budget** - Retries are budgeted once per request.", text)
        self.assertNotIn("retried exactly twice", text)

    def test_only_accepted_records_are_listed(self):
        self.add(status="accepted")
        self.add(status="proposed", doc_id="0002-draft-idea")
        text = index_module.render_markdown(self.scan())
        self.assertIn("- **0001-retry-budget**", text)
        self.assertNotIn("- **0002-draft-idea**", text)
        self.assertIn("Not listed: 1 proposed.", text)

    def test_decisions_do_not_appear_in_the_type_sections(self):
        self.add()
        text = index_module.render_markdown(self.scan())
        self.assertNotIn("## decision\n", text)

    def test_regenerating_is_byte_identical(self):
        self.add()
        doc_set = self.scan()
        self.assertEqual(
            index_module.render_markdown(doc_set), index_module.render_markdown(doc_set)
        )
        self.assertEqual(
            index_module.render_json(index_module.build_payload(doc_set, "abc1234")),
            index_module.render_json(index_module.build_payload(self.scan(), "abc1234")),
        )


class ScanValidationTests(support.TempRepoTestCase):
    def issues_for(self, text, name="docs/x.md"):
        self.write(name, text)
        doc_set = index_module.scan(self.root, self.config())
        return [issue.message for issue in doc_set.issues]

    def test_valid_document_has_no_issues(self):
        self.assertEqual(self.issues_for(support.doc_text(doc_id="valid")), [])

    def test_missing_required_fields(self):
        messages = self.issues_for("---\nid: x\n---\n\nBody\n")
        joined = " | ".join(messages)
        for field in ("title", "type", "summary", "covers", "status", "updated"):
            self.assertIn("missing required frontmatter field %r" % (field,), joined)

    def test_bad_id_shape(self):
        messages = self.issues_for(support.doc_text(doc_id="Not_Kebab"))
        self.assertTrue(any("kebab-case" in message for message in messages))

    def test_unknown_type_and_status(self):
        messages = self.issues_for(support.doc_text(doc_id="x", doc_type="wiki", status="live"))
        joined = " | ".join(messages)
        self.assertIn("unknown 'type'", joined)
        self.assertIn("unknown 'status'", joined)

    def test_summary_over_the_limit(self):
        messages = self.issues_for(support.doc_text(doc_id="x", summary="y" * 201))
        self.assertTrue(any("'summary' is 201 characters" in message for message in messages))

    def test_summary_limit_follows_config(self):
        self.write_config(max_summary_chars=10)
        messages = self.issues_for(support.doc_text(doc_id="x", summary="y" * 11))
        self.assertTrue(any("the limit is 10" in message for message in messages))

    def test_malformed_updated(self):
        messages = self.issues_for(support.doc_text(doc_id="x", updated="14-08-2026"))
        self.assertTrue(any("'updated' must be a calendar date" in message for message in messages))

    def test_impossible_date_is_rejected(self):
        messages = self.issues_for(support.doc_text(doc_id="x", updated="2026-02-30"))
        self.assertTrue(any("calendar date" in message for message in messages))

    def test_leap_day_is_accepted(self):
        self.assertEqual(self.issues_for(support.doc_text(doc_id="x", updated="2024-02-29")), [])

    def test_bad_verified_at(self):
        messages = self.issues_for(support.doc_text(doc_id="x", verified_at="not-a-sha"))
        self.assertTrue(any("verified_at" in message for message in messages))

    def test_invalid_covers_pattern(self):
        messages = self.issues_for(support.doc_text(doc_id="x", covers=["/absolute/**"]))
        self.assertTrue(any("repo-relative" in message for message in messages))

    def test_bad_tag_shape(self):
        messages = self.issues_for(support.doc_text(doc_id="x", tags=["Bad Tag"]))
        self.assertTrue(any("must match" in message for message in messages))

    def test_duplicate_covers_entry(self):
        messages = self.issues_for(support.doc_text(doc_id="x", covers=["src/**", "src/**"]))
        self.assertTrue(any("duplicate entry" in message for message in messages))

    def test_empty_covers_is_legal(self):
        self.assertEqual(self.issues_for(support.doc_text(doc_id="x", covers=[])), [])

    def test_duplicate_ids_are_collected(self):
        self.write("docs/a.md", support.doc_text(doc_id="twin"))
        self.write("docs/b.md", support.doc_text(doc_id="twin"))
        doc_set = index_module.scan(self.root, self.config())
        self.assertEqual(doc_set.duplicate_ids, [("twin", "docs/a.md", "docs/b.md")])

    def test_nested_directories_are_scanned(self):
        self.write("docs/deep/deeper/x.md", support.doc_text(doc_id="deep"))
        doc_set = index_module.scan(self.root, self.config())
        self.assertEqual([doc.path for doc in doc_set.docs], ["docs/deep/deeper/x.md"])

    def test_non_markdown_files_are_skipped(self):
        self.write("docs/notes.txt", "not a doc")
        doc_set = index_module.scan(self.root, self.config())
        self.assertEqual(doc_set.docs, [])

    def test_missing_root_directory_is_not_an_error(self):
        self.write_config(roots=["docs", "runbooks"])
        doc_set = index_module.scan(self.root, self.config())
        self.assertEqual(doc_set.docs, [])

    def test_counts_by_status(self):
        self.write("docs/a.md", support.doc_text(doc_id="a", status="current"))
        self.write("docs/b.md", support.doc_text(doc_id="b", status="draft"))
        self.write("docs/c.md", support.doc_text(doc_id="c", status="deprecated"))
        counts = index_module.scan(self.root, self.config()).counts_by_status()
        self.assertEqual(counts["current"], 1)
        self.assertEqual(counts["draft"], 1)
        self.assertEqual(counts["deprecated"], 1)


class FirstHeadingTests(unittest.TestCase):
    def test_finds_the_first_atx_heading(self):
        self.assertEqual(index_module.first_heading("\n# Title\n\ntext\n"), "Title")

    def test_ignores_headings_inside_fences(self):
        body = "\n```\n# Not a heading\n```\n\n# Real\n"
        self.assertEqual(index_module.first_heading(body), "Real")

    def test_returns_none_when_absent(self):
        self.assertIsNone(index_module.first_heading("just text\n"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

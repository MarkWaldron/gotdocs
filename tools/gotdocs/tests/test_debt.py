"""The doc-debt ledger: identity, dedup, atomic writes, tolerant loading, reports."""

import io
import json
import os
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import debt
from tools.gotdocs.check import Finding
from tools.gotdocs.errors import UsageError

DATE_A = "2026-08-01"
DATE_B = "2026-08-14"
SHA_A = "aaaaaaa"
SHA_B = "bbbbbbb"


def stale(doc_id="architecture", path="docs/architecture.md"):
    return Finding(
        "stale",
        path,
        "%s is stale" % (doc_id,),
        "bin/gotdocs verify %s" % (doc_id,),
        doc_id=doc_id,
    )


def uncovered(path="src/app.py"):
    return Finding("uncovered", path, "%s is covered by no doc" % (path,), "bin/gotdocs new doc app")


class EntryIdTests(unittest.TestCase):
    def test_id_is_stable_across_calls(self):
        first = debt.entry_id("stale", "architecture", "docs/architecture.md")
        second = debt.entry_id("stale", "architecture", "docs/architecture.md")
        self.assertEqual(first, second)

    def test_id_is_a_literal_known_value(self):
        # Pinned: changing the digest recipe silently forks every ledger.
        self.assertEqual(debt.entry_id("stale", "architecture", "docs/architecture.md"), "73674a38f63a")

    def test_id_shape(self):
        ident = debt.entry_id("uncovered", None, "src/app.py")
        self.assertEqual(len(ident), 12)
        self.assertTrue(all(char in "0123456789abcdef" for char in ident))

    def test_each_component_changes_the_id(self):
        base = debt.entry_id("stale", "arch", "docs/a.md")
        self.assertNotEqual(base, debt.entry_id("lint", "arch", "docs/a.md"))
        self.assertNotEqual(base, debt.entry_id("stale", "other", "docs/a.md"))
        self.assertNotEqual(base, debt.entry_id("stale", "arch", "docs/b.md"))

    def test_none_doc_id_is_not_confused_with_empty_string(self):
        self.assertEqual(debt.entry_id("uncovered", None, "src/a.py"), debt.entry_id("uncovered", "", "src/a.py"))

    def test_fields_cannot_collide_across_the_separator(self):
        self.assertNotEqual(debt.entry_id("stale", "a", "b"), debt.entry_id("stale", "ab", ""))

    def test_message_is_not_part_of_identity(self):
        first = debt.record_findings([], [stale()], DATE_A).entries[0]
        reworded = Finding("stale", "docs/architecture.md", "reworded", "cmd", doc_id="architecture")
        second = debt.record_findings([first], [reworded], DATE_B)
        self.assertEqual(len(second.entries), 1)
        self.assertEqual(second.entries[0].entry_id, first.entry_id)

    def test_entry_computes_its_own_id(self):
        entry = debt.DebtEntry("stale", "docs/a.md", doc_id="a")
        self.assertEqual(entry.entry_id, debt.entry_id("stale", "a", "docs/a.md"))


class EntryFromFindingTests(unittest.TestCase):
    def test_copies_the_finding_fields(self):
        entry = debt.entry_from_finding(stale(), DATE_A, SHA_A)
        self.assertEqual(entry.kind, "stale")
        self.assertEqual(entry.doc_id, "architecture")
        self.assertEqual(entry.path, "docs/architecture.md")
        self.assertEqual(entry.remediation, "bin/gotdocs verify architecture")
        self.assertEqual(entry.status, debt.STATUS_OPEN)
        self.assertEqual(entry.occurrences, 1)
        self.assertEqual(entry.first_seen_date, DATE_A)
        self.assertEqual(entry.last_seen_date, DATE_A)
        self.assertEqual(entry.first_seen_sha, SHA_A)

    def test_accepts_a_dict_shaped_finding(self):
        entry = debt.entry_from_finding(
            {"kind": "lint", "path": "docs/a.md", "doc_id": None, "message": "m", "remediation": "r"},
            DATE_A,
        )
        self.assertEqual(entry.kind, "lint")
        self.assertEqual(entry.path, "docs/a.md")

    def test_rejects_a_non_iso_date(self):
        with self.assertRaises(UsageError):
            debt.entry_from_finding(stale(), "August 1 2026")

    def test_rejects_a_finding_with_no_kind(self):
        with self.assertRaises(UsageError):
            debt.entry_from_finding({"path": "src/a.py"}, DATE_A)

    def test_rejects_a_finding_with_no_path_or_doc_id(self):
        with self.assertRaises(UsageError):
            debt.entry_from_finding({"kind": "lint"}, DATE_A)


class RecordDedupTests(unittest.TestCase):
    def test_first_record_adds_one_open_entry(self):
        result = debt.record_findings([], [stale()], DATE_A, SHA_A)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.updated, [])
        self.assertTrue(result.entries[0].is_open)

    def test_recording_twice_bumps_occurrences_without_a_second_entry(self):
        first = debt.record_findings([], [stale()], DATE_A, SHA_A)
        second = debt.record_findings(first.entries, [stale()], DATE_B, SHA_B)
        self.assertEqual(len(second.entries), 1)
        self.assertEqual(second.entries[0].occurrences, 2)
        self.assertEqual(second.added, [])
        self.assertEqual(len(second.updated), 1)

    def test_recording_five_times_is_one_line_with_five_occurrences(self):
        entries = []
        for _ in range(5):
            entries = debt.record_findings(entries, [stale()], DATE_A, SHA_A).entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].occurrences, 5)
        self.assertEqual(debt.render_jsonl(entries).count("\n"), 1)

    def test_five_duplicates_in_one_call_are_one_sighting(self):
        result = debt.record_findings([], [stale() for _ in range(5)], DATE_A, SHA_A)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].occurrences, 1)
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.updated, [])

    def test_distinct_findings_on_one_document_do_not_inflate_occurrences(self):
        """Regression: four lint errors in one file were recorded as 'seen 4x'."""
        findings = [
            Finding("lint", "docs/bad.md", "docs/bad.md:3: one", "fix it", doc_id="bad"),
            Finding("lint", "docs/bad.md", "docs/bad.md:4: two", "fix it", doc_id="bad"),
            Finding("lint", "docs/bad.md", "docs/bad.md:5: three", "fix it", doc_id="bad"),
        ]
        result = debt.record_findings([], findings, DATE_A, SHA_A)
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual(entry.occurrences, 1)
        # and the two findings the single line does not quote are still counted
        self.assertIn("(+2 more findings on this document)", entry.message)

    def test_a_second_run_of_the_same_group_bumps_occurrences_by_one(self):
        findings = [
            Finding("lint", "docs/bad.md", "docs/bad.md:3: one", "fix it", doc_id="bad"),
            Finding("lint", "docs/bad.md", "docs/bad.md:4: two", "fix it", doc_id="bad"),
        ]
        entries = debt.record_findings([], findings, DATE_A, SHA_A).entries
        entries = debt.record_findings(entries, findings, DATE_B, SHA_B).entries
        self.assertEqual(entries[0].occurrences, 2)
        self.assertIn("(+1 more finding on this document)", entries[0].message)

    def test_last_seen_moves_and_first_seen_does_not(self):
        first = debt.record_findings([], [stale()], DATE_A, SHA_A).entries
        entry = debt.record_findings(first, [stale()], DATE_B, SHA_B).entries[0]
        self.assertEqual(entry.first_seen_date, DATE_A)
        self.assertEqual(entry.first_seen_sha, SHA_A)
        self.assertEqual(entry.last_seen_date, DATE_B)
        self.assertEqual(entry.last_seen_sha, SHA_B)

    def test_different_findings_stay_separate(self):
        result = debt.record_findings([], [stale(), uncovered(), uncovered("src/b.py")], DATE_A)
        self.assertEqual(len(result.entries), 3)

    def test_message_and_remediation_refresh_to_the_latest(self):
        entries = debt.record_findings([], [stale()], DATE_A).entries
        newer = Finding("stale", "docs/architecture.md", "new message", "new cmd", doc_id="architecture")
        entry = debt.record_findings(entries, [newer], DATE_B).entries[0]
        self.assertEqual(entry.message, "new message")
        self.assertEqual(entry.remediation, "new cmd")

    def test_recording_does_not_mutate_the_input_list_or_entries(self):
        original = debt.record_findings([], [stale()], DATE_A, SHA_A).entries
        snapshot = debt.render_jsonl(original)
        debt.record_findings(original, [stale(), uncovered()], DATE_B, SHA_B)
        self.assertEqual(len(original), 1)
        self.assertEqual(debt.render_jsonl(original), snapshot)

    def test_recording_a_resolved_entry_reopens_it(self):
        entries = debt.record_findings([], [stale()], DATE_A, SHA_A).entries
        entries, _resolved, _missing = debt.resolve_entries(entries, [entries[0].entry_id], DATE_A, SHA_A)
        result = debt.record_findings(entries, [stale()], DATE_B, SHA_B)
        entry = result.entries[0]
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.reopened, [entry.entry_id])
        self.assertTrue(entry.is_open)
        self.assertIsNone(entry.resolved_date)
        self.assertIsNone(entry.resolved_sha)
        self.assertEqual(entry.occurrences, 2)

    def test_record_rejects_a_bad_date(self):
        with self.assertRaises(UsageError):
            debt.record_findings([], [stale()], "2026/08/01")

    def test_record_result_counts(self):
        result = debt.record_findings([], [stale(), uncovered()], DATE_A)
        self.assertEqual(result.counts(), {"added": 2, "updated": 0, "reopened": 0, "total": 2})
        self.assertTrue(result.changed)
        self.assertFalse(debt.record_findings(result.entries, [], DATE_B).changed)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.entries = debt.record_findings([], [stale(), uncovered()], DATE_A, SHA_A).entries
        self.stale_id = debt.entry_id("stale", "architecture", "docs/architecture.md")

    def test_resolve_by_full_id(self):
        entries, resolved, unmatched = debt.resolve_entries(self.entries, [self.stale_id], DATE_B, SHA_B)
        self.assertEqual(resolved, [self.stale_id])
        self.assertEqual(unmatched, [])
        closed = debt.filter_entries(entries, status=debt.STATUS_RESOLVED)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].resolved_date, DATE_B)
        self.assertEqual(closed[0].resolved_sha, SHA_B)

    def test_resolve_by_id_prefix_and_by_doc_id(self):
        entries, resolved, _unmatched = debt.resolve_entries(self.entries, [self.stale_id[:6]], DATE_B)
        self.assertEqual(resolved, [self.stale_id])
        _entries, resolved, _unmatched = debt.resolve_entries(entries, ["architecture"], DATE_B)
        self.assertEqual(resolved, [self.stale_id])

    def test_unknown_ref_is_reported_not_raised(self):
        entries, resolved, unmatched = debt.resolve_entries(self.entries, ["nope"], DATE_B)
        self.assertEqual(resolved, [])
        self.assertEqual(unmatched, ["nope"])
        self.assertEqual(debt.summarize(entries)["open"], 2)

    def test_ambiguous_ref_raises(self):
        entries = debt.record_findings(
            [],
            [stale(), Finding("lint", "docs/architecture.md", "m", "r", doc_id="architecture")],
            DATE_A,
        ).entries
        with self.assertRaises(UsageError):
            debt.resolve_entries(entries, ["architecture"], DATE_B)

    def test_resolving_twice_is_idempotent(self):
        entries, _resolved, _unmatched = debt.resolve_entries(self.entries, [self.stale_id], DATE_A, SHA_A)
        again, resolved, _unmatched = debt.resolve_entries(entries, [self.stale_id], DATE_B, SHA_B)
        self.assertEqual(resolved, [self.stale_id])
        self.assertEqual(debt.render_jsonl(again), debt.render_jsonl(entries))

    def test_resolve_absent_closes_findings_that_stopped_appearing(self):
        entries, resolved = debt.resolve_absent(self.entries, [stale()], DATE_B, SHA_B)
        self.assertEqual(resolved, [debt.entry_id("uncovered", None, "src/app.py")])
        self.assertEqual(debt.summarize(entries)["open"], 1)

    def test_resolve_absent_respects_the_examined_path_scope(self):
        entries, resolved = debt.resolve_absent(self.entries, [], DATE_B, paths=["src/app.py"])
        self.assertEqual(resolved, [debt.entry_id("uncovered", None, "src/app.py")])
        self.assertEqual(debt.summarize(entries)["open"], 1)

    def test_resolve_absent_with_no_findings_and_no_scope_closes_everything(self):
        entries, resolved = debt.resolve_absent(self.entries, [], DATE_B)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(debt.summarize(entries)["open"], 0)

    def test_resolve_absent_ignores_already_resolved_entries(self):
        entries, _resolved, _unmatched = debt.resolve_entries(self.entries, [self.stale_id], DATE_A)
        _entries, resolved = debt.resolve_absent(entries, [], DATE_B)
        self.assertNotIn(self.stale_id, resolved)

    def test_resolve_rejects_a_bad_date(self):
        with self.assertRaises(UsageError):
            debt.resolve_entries(self.entries, [self.stale_id], "yesterday")
        with self.assertRaises(UsageError):
            debt.resolve_absent(self.entries, [], "yesterday")


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.entries = debt.record_findings(
            [], [stale(), stale("cli", "docs/cli-reference.md"), uncovered()], DATE_A
        ).entries

    def test_sort_order_is_kind_then_doc_then_path(self):
        kinds = [entry.kind for entry in self.entries]
        self.assertEqual(kinds, ["stale", "stale", "uncovered"])
        self.assertEqual([entry.doc_id for entry in self.entries[:2]], ["architecture", "cli"])

    def test_sort_is_independent_of_status(self):
        before = [entry.entry_id for entry in self.entries]
        entries, _resolved, _unmatched = debt.resolve_entries(self.entries, [before[0]], DATE_B)
        self.assertEqual([entry.entry_id for entry in entries], before)

    def test_unknown_kinds_sort_after_known_ones(self):
        entries = debt.record_findings(
            self.entries, [Finding("zebra", "src/z.py", "m", "r")], DATE_A
        ).entries
        self.assertEqual(entries[-1].kind, "zebra")

    def test_filter_entries(self):
        self.assertEqual(len(debt.filter_entries(self.entries, kind="stale")), 2)
        self.assertEqual(len(debt.filter_entries(self.entries, doc_id="cli")), 1)
        self.assertEqual(len(debt.filter_entries(self.entries, path="src/app.py")), 1)
        self.assertEqual(len(debt.filter_entries(self.entries, status=debt.STATUS_RESOLVED)), 0)
        self.assertEqual(len(debt.filter_entries(self.entries, status=debt.STATUS_OPEN, kind="uncovered")), 1)

    def test_find_entries_prefers_an_exact_id(self):
        ident = self.entries[0].entry_id
        self.assertEqual([entry.entry_id for entry in debt.find_entries(self.entries, ident)], [ident])

    def test_find_entries_by_path_and_empty_ref(self):
        self.assertEqual(len(debt.find_entries(self.entries, "src/app.py")), 1)
        self.assertEqual(debt.find_entries(self.entries, ""), [])
        self.assertEqual(debt.find_entries(self.entries, None), [])

    def test_summarize(self):
        stats = debt.summarize(self.entries)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["open"], 3)
        self.assertEqual(stats["resolved"], 0)
        self.assertEqual(stats["open_by_kind"], {"stale": 2, "uncovered": 1})
        self.assertEqual(stats["open_occurrences"], 3)
        self.assertEqual(stats["open_by_doc"]["architecture"], 1)

    def test_summarize_counts_resolved_separately(self):
        entries, _resolved, _unmatched = debt.resolve_entries(self.entries, [self.entries[0].entry_id], DATE_B)
        stats = debt.summarize(entries)
        self.assertEqual((stats["open"], stats["resolved"]), (2, 1))
        self.assertEqual(stats["open_by_kind"], {"stale": 1, "uncovered": 1})


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.entries = debt.record_findings(
            [], [stale(), stale("cli", "docs/cli-reference.md"), uncovered()], DATE_A, SHA_A
        ).entries

    def test_jsonl_is_one_line_per_entry_and_parses(self):
        text = debt.render_jsonl(self.entries)
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(text.endswith("\n"))
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)

    def test_jsonl_key_order_is_fixed(self):
        payload = json.loads(debt.render_jsonl(self.entries).splitlines()[0])
        self.assertEqual(list(payload.keys()), list(debt.ENTRY_FIELDS))

    def test_jsonl_is_empty_for_an_empty_ledger(self):
        self.assertEqual(debt.render_jsonl([]), "")

    def test_jsonl_sorts_regardless_of_input_order(self):
        shuffled = [self.entries[2], self.entries[0], self.entries[1]]
        self.assertEqual(debt.render_jsonl(shuffled), debt.render_jsonl(self.entries))

    def test_markdown_is_deterministic(self):
        first = debt.render_markdown(self.entries)
        second = debt.render_markdown(list(reversed(self.entries)))
        self.assertEqual(first, second)

    def test_markdown_groups_open_entries_by_kind_with_remediation(self):
        text = debt.render_markdown(self.entries)
        self.assertIn("## stale (2)", text)
        self.assertIn("## uncovered (1)", text)
        self.assertIn("fix: `bin/gotdocs verify architecture`", text)
        self.assertIn("3 open, 0 resolved", text)
        self.assertLess(text.index("## stale"), text.index("## uncovered"))

    def test_markdown_prints_one_line_per_open_entry(self):
        bullets = [line for line in debt.render_markdown(self.entries).splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 3)

    def test_markdown_collapses_resolved_entries_to_a_count(self):
        entries, _resolved, _unmatched = debt.resolve_entries(self.entries, [self.entries[0].entry_id], DATE_B)
        text = debt.render_markdown(entries)
        self.assertIn("2 open, 1 resolved", text)
        self.assertIn("1 resolved entry kept for history", text)
        self.assertNotIn("architecture", text.split("kept for history")[0].split("## uncovered")[-1])
        bullets = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 2)

    def test_markdown_for_an_empty_ledger(self):
        text = debt.render_markdown([])
        self.assertIn("No open doc debt.", text)
        self.assertEqual(text[-1], "\n")

    def test_markdown_limit_bounds_each_kind(self):
        findings = [stale("doc%02d" % (number,), "docs/doc%02d.md" % (number,)) for number in range(10)]
        entries = debt.record_findings([], findings, DATE_A).entries
        text = debt.render_markdown(entries, limit=3)
        bullets = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 4)  # 3 entries + the "and N more" line
        self.assertIn("- ... and 7 more", text)

    def test_markdown_shows_occurrences_and_first_seen(self):
        entries = debt.record_findings(self.entries, [stale()], DATE_B).entries
        self.assertIn("seen 2× since 2026-08-01", debt.render_markdown(entries))

    def test_markdown_ends_with_exactly_one_newline(self):
        text = debt.render_markdown(self.entries)
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_json_payload(self):
        payload = debt.build_payload(self.entries)
        self.assertEqual(payload["version"], debt.LEDGER_VERSION)
        self.assertEqual(payload["summary"]["open"], 3)
        self.assertEqual(len(payload["entries"]), 3)
        text = debt.render_json(payload)
        self.assertEqual(json.loads(text)["entries"][0]["entry_id"], self.entries[0].entry_id)
        self.assertTrue(text.endswith("\n"))

    def test_render_json_is_pure(self):
        self.assertEqual(
            debt.render_json(debt.build_payload(self.entries)),
            debt.render_json(debt.build_payload(self.entries)),
        )


class LedgerIoTests(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.entries = debt.record_findings([], [stale(), uncovered()], DATE_A, SHA_A).entries

    def ledger_lines(self):
        return support.read(self.root, debt.LEDGER_PATH).splitlines()

    def test_missing_ledger_loads_as_empty(self):
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(errors, [])

    def test_write_creates_the_file_and_reports_a_change(self):
        self.assertTrue(debt.write_ledger(self.root, self.entries))
        self.assertPathExists(debt.LEDGER_PATH)
        self.assertEqual(len(self.ledger_lines()), 2)

    def test_write_is_a_no_op_when_bytes_are_unchanged(self):
        debt.write_ledger(self.root, self.entries)
        before = support.read_bytes(self.root, debt.LEDGER_PATH)
        self.assertFalse(debt.write_ledger(self.root, self.entries))
        self.assertEqual(support.read_bytes(self.root, debt.LEDGER_PATH), before)

    def test_write_is_a_no_op_for_reordered_input(self):
        debt.write_ledger(self.root, self.entries)
        self.assertFalse(debt.write_ledger(self.root, list(reversed(self.entries))))

    def test_write_reports_a_change_when_occurrences_move(self):
        debt.write_ledger(self.root, self.entries)
        bumped = debt.record_findings(self.entries, [stale()], DATE_B, SHA_B).entries
        self.assertTrue(debt.write_ledger(self.root, bumped))
        self.assertEqual(len(self.ledger_lines()), 2)

    def test_write_leaves_no_temp_file_behind(self):
        debt.write_ledger(self.root, self.entries)
        leftovers = [name for name in os.listdir(os.path.join(self.root, ".gotdocs")) if ".tmp-" in name]
        self.assertEqual(leftovers, [])

    def test_write_replaces_rather_than_truncating(self):
        # A pre-existing ledger keeps its inode contents until os.replace lands;
        # the observable proof is that the old bytes are fully gone afterwards,
        # never interleaved with the new ones.
        debt.write_ledger(self.root, self.entries)
        smaller = debt.write_ledger(self.root, self.entries[:1])
        self.assertTrue(smaller)
        self.assertEqual(len(self.ledger_lines()), 1)

    def test_write_creates_missing_directories(self):
        path = "nested/deeper/debt.jsonl"
        self.assertTrue(debt.write_ledger(self.root, self.entries, path=path))
        self.assertPathExists(path)

    def test_round_trip(self):
        debt.write_ledger(self.root, self.entries)
        loaded, errors = debt.load_ledger(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(loaded, self.entries)

    def test_round_trip_preserves_every_field(self):
        entries = debt.record_findings(self.entries, [stale()], DATE_B, SHA_B, note="deferred to Q4").entries
        entries, _resolved, _unmatched = debt.resolve_entries(
            entries, [debt.entry_id("uncovered", None, "src/app.py")], DATE_B, SHA_B
        )
        debt.write_ledger(self.root, entries)
        loaded, errors = debt.load_ledger(self.root)
        self.assertEqual(errors, [])
        self.assertEqual([entry.as_dict() for entry in loaded], [entry.as_dict() for entry in entries])

    def test_round_trip_of_an_empty_ledger(self):
        debt.write_ledger(self.root, [])
        loaded, errors = debt.load_ledger(self.root)
        self.assertEqual((loaded, errors), ([], []))

    def test_round_trip_survives_unicode_and_separators(self):
        finding = Finding("lint", "docs/ünïcode.md", "line 3: bad — value", "bin/gotdocs lint", doc_id="ünï")
        entries = debt.record_findings([], [finding], DATE_A).entries
        debt.write_ledger(self.root, entries)
        loaded, errors = debt.load_ledger(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(loaded, entries)

    def test_load_is_tolerant_of_a_truncated_last_line(self):
        debt.write_ledger(self.root, self.entries)
        raw = support.read(self.root, debt.LEDGER_PATH)
        support.write(self.root, debt.LEDGER_PATH, raw + '{"kind":"stale","path":"docs/x.md"')
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].line, 3)
        self.assertIn("unparseable JSON", errors[0].message)
        self.assertIn(debt.LEDGER_PATH, errors[0].located())

    def test_load_skips_a_corrupt_line_in_the_middle(self):
        lines = debt.render_jsonl(self.entries).splitlines()
        support.write(self.root, debt.LEDGER_PATH, "\n".join([lines[0], "}}not json{{", lines[1]]) + "\n")
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(len(entries), 2)
        self.assertEqual([error.line for error in errors], [2])

    def test_load_skips_semantically_invalid_entries(self):
        bad = [
            json.dumps({"path": "docs/a.md"}),                                   # no kind
            json.dumps({"kind": "stale"}),                                       # no path or doc_id
            json.dumps({"kind": "stale", "path": "a.md", "status": "maybe"}),    # bad status
            json.dumps({"kind": "stale", "path": "a.md", "occurrences": 0}),     # bad count
            json.dumps({"kind": "stale", "path": "a.md", "occurrences": "two"}),
            json.dumps({"kind": "stale", "path": "a.md", "first_seen_date": "nope"}),
            json.dumps({"kind": "stale", "path": "a.md", "message": 7}),
            json.dumps({"kind": "stale", "path": "a.md", "entry_id": "deadbeef"}),
            json.dumps({"kind": "stale", "path": "a.md", "entry_id": "0" * 12}),
            json.dumps(["not", "an", "object"]),
            "12345",
        ]
        support.write(self.root, debt.LEDGER_PATH, "\n".join(bad) + "\n")
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(len(errors), len(bad))

    def test_load_skips_duplicate_ids(self):
        line = debt.render_jsonl(self.entries[:1]).strip()
        support.write(self.root, debt.LEDGER_PATH, line + "\n" + line + "\n")
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("duplicate entry", errors[0].message)

    def test_load_ignores_blank_and_comment_lines(self):
        text = debt.render_jsonl(self.entries)
        support.write(self.root, debt.LEDGER_PATH, "# header\n\n" + text + "\n")
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(len(entries), 2)
        self.assertEqual(errors, [])

    def test_load_survives_undecodable_bytes(self):
        path = os.path.join(self.root, ".gotdocs")
        if not os.path.isdir(path):
            os.makedirs(path)
        with io.open(os.path.join(path, "debt.jsonl"), "wb") as handle:
            handle.write(b"\xff\xfe not utf-8\n")
        entries, errors = debt.load_ledger(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(len(errors), 1)

    def test_load_sorts_a_hand_edited_ledger(self):
        lines = debt.render_jsonl(self.entries).splitlines()
        support.write(self.root, debt.LEDGER_PATH, "\n".join(reversed(lines)) + "\n")
        entries, _errors = debt.load_ledger(self.root)
        self.assertEqual([entry.entry_id for entry in entries], [entry.entry_id for entry in self.entries])

    def test_load_and_write_accept_an_explicit_path(self):
        debt.write_ledger(self.root, self.entries, path="custom/ledger.jsonl")
        entries, errors = debt.load_ledger(self.root, path="custom/ledger.jsonl")
        self.assertEqual(errors, [])
        self.assertEqual(entries, self.entries)
        self.assertFalse(os.path.exists(os.path.join(self.root, debt.LEDGER_PATH)))

    def test_write_markdown_is_atomic_and_idempotent(self):
        self.assertTrue(debt.write_markdown(self.root, self.entries))
        self.assertPathExists(debt.MARKDOWN_PATH)
        self.assertFalse(debt.write_markdown(self.root, self.entries))
        self.assertIn("## stale (1)", support.read(self.root, debt.MARKDOWN_PATH))

    def test_full_cycle_across_commits_keeps_one_line(self):
        entries = []
        for date, sha in ((DATE_A, SHA_A), (DATE_B, SHA_B), ("2026-08-15", "ccccccc")):
            entries = debt.record_findings(entries, [stale()], date, sha).entries
            debt.write_ledger(self.root, entries)
        self.assertEqual(len(self.ledger_lines()), 1)
        loaded, _errors = debt.load_ledger(self.root)
        self.assertEqual(loaded[0].occurrences, 3)
        self.assertEqual(loaded[0].last_seen_sha, "ccccccc")
        self.assertEqual(loaded[0].first_seen_date, DATE_A)


class NoWallClockTests(unittest.TestCase):
    def test_module_never_reads_the_clock(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debt.py")
        with io.open(path, "rb") as handle:
            source = handle.read().decode("utf-8")
        for banned in ("import time", "import datetime", "datetime.", "time.time", "utcnow", "today("):
            self.assertNotIn(banned, source, "debt.py must not read the wall clock (%s)" % (banned,))


if __name__ == "__main__":
    unittest.main()

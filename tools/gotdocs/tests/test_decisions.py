"""Tests for architecture decision records and the ``why`` lookup.

The bar these tests hold the module to: a record written by a human who did not
read the spec must still be found by ``why``. That means heading extraction is
tested against headings that are wrong in the ways real headings are wrong --
different levels, trailing colons, a typographic ellipsis, bold, uppercase, a
setext underline -- not just the canonical form.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_TESTS_DIR)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.gotdocs import decisions  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


DEFAULT_BODY = """
# Retry budget is per request, not per hop

## Context

Retrying at every hop multiplied load during an incident.

## Expected behavior

Every request carries one budget of two retries, shared by all hops.

## This is a bug, not this decision, if...

Retries continue after the budget is exhausted.
"""


def decision_text(
    doc_id="0001-retry-budget-per-request",
    title="Retry budget is per request, not per hop",
    status="accepted",
    summary="One retry budget per request, consumed by whichever hop retries first.",
    symptoms=("a POST is retried exactly twice and then fails fast",),
    supersedes=(),
    superseded_by=(),
    tags=("retry", "resilience"),
    doc_type="decision",
    covers=("src/http/**",),
    updated="2026-08-14",
    body=DEFAULT_BODY,
    extra_lines=(),
):
    """Build a well-formed decision record."""
    lines = ["---"]
    if doc_id is not None:
        lines.append("id: %s" % (doc_id,))
    if title is not None:
        lines.append("title: %s" % (title,))
    if doc_type is not None:
        lines.append("type: %s" % (doc_type,))
    if summary is not None:
        lines.append("summary: %s" % (summary,))
    lines.append("covers: [%s]" % (", ".join(covers),))
    if symptoms:
        lines.append("symptoms:")
        for symptom in symptoms:
            lines.append("  - %s" % (symptom,))
    else:
        lines.append("symptoms: []")
    lines.append("supersedes: [%s]" % (", ".join(supersedes),))
    lines.append("superseded_by: [%s]" % (", ".join(superseded_by),))
    lines.append("tags: [%s]" % (", ".join(tags),))
    if status is not None:
        lines.append("status: %s" % (status,))
    lines.append("updated: %s" % (updated,))
    lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + body


def make_decision(
    number="0001",
    slug="retry-budget-per-request",
    title="Retry budget is per request, not per hop",
    status="accepted",
    summary="",
    symptoms=(),
    tags=(),
    expected="",
    not_this="",
    supersedes=(),
    superseded_by=(),
):
    """Build a :class:`Decision` directly, without touching the filesystem."""
    path = "decisions/%s-%s.md" % (number, slug)
    decision = decisions.Decision(path)
    decision.number = number
    decision.slug = slug
    decision.id = "%s-%s" % (number, slug)
    decision.type = "decision"
    decision.title = title
    decision.status = status
    decision.summary = summary
    decision.symptoms = list(symptoms)
    decision.tags = list(tags)
    decision.supersedes = list(supersedes)
    decision.superseded_by = list(superseded_by)
    decision.sections = {
        decisions.SECTION_EXPECTED: expected,
        decisions.SECTION_NOT_THIS: not_this,
    }
    return decision


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="gotdocs-decisions-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, rel_path, text):
        path = os.path.join(self.root, rel_path.replace("/", os.sep))
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
        return path


# ---------------------------------------------------------------------------
# extract_sections
# ---------------------------------------------------------------------------


class ExtractSectionsTests(unittest.TestCase):
    def both(self, body):
        sections = decisions.extract_sections(body)
        return sections[decisions.SECTION_EXPECTED], sections[decisions.SECTION_NOT_THIS]

    def test_canonical_headings(self):
        expected, not_this = self.both(DEFAULT_BODY)
        self.assertEqual(
            expected, "Every request carries one budget of two retries, shared by all hops."
        )
        self.assertEqual(not_this, "Retries continue after the budget is exhausted.")

    def test_both_keys_always_present(self):
        sections = decisions.extract_sections("")
        self.assertEqual(sorted(sections), ["expected", "not_this"])
        self.assertEqual(sections["expected"], "")
        self.assertEqual(sections["not_this"], "")

    def test_none_body(self):
        self.assertEqual(decisions.extract_sections(None)["expected"], "")

    def test_heading_level_is_irrelevant(self):
        for hashes in ("#", "##", "###", "####", "#####", "######"):
            body = "%s Expected behavior\nyes\n\n%s This is a bug, not this decision, if...\nno\n" % (
                hashes,
                hashes,
            )
            self.assertEqual(self.both(body), ("yes", "no"), hashes)

    def test_a_shorter_fence_run_does_not_close_a_longer_one(self):
        """Regression: a ``` inside a ```` block ate every heading after it."""
        body = (
            "## Expected behavior\n"
            "\n"
            "A malformed document, shown inside a four-backtick fence:\n"
            "\n"
            "````\n"
            "```\n"
            "unclosed inner fence\n"
            "````\n"
            "\n"
            "## This is a bug, not this decision, if...\n"
            "\n"
            "the parser accepts it\n"
        )
        expected, not_this = self.both(body)
        self.assertIn("four-backtick fence", expected)
        self.assertEqual(not_this, "the parser accepts it")

    def test_a_longer_run_closes_a_shorter_fence(self):
        body = (
            "## Expected behavior\n"
            "\n"
            "```\n"
            "code\n"
            "`````\n"
            "\n"
            "## This is a bug, not this decision, if...\n"
            "\n"
            "no\n"
        )
        self.assertEqual(self.both(body)[1], "no")

    def test_a_closing_fence_may_not_carry_an_info_string(self):
        body = (
            "## Expected behavior\n"
            "\n"
            "```\n"
            "# not a heading\n"
            "```python\n"
            "still code\n"
            "```\n"
            "\n"
            "## This is a bug, not this decision, if...\n"
            "\n"
            "no\n"
        )
        self.assertEqual(self.both(body)[1], "no")

    def test_a_tilde_run_does_not_close_a_backtick_fence(self):
        body = (
            "## Expected behavior\n"
            "\n"
            "```\n"
            "~~~\n"
            "```\n"
            "\n"
            "## This is a bug, not this decision, if...\n"
            "\n"
            "no\n"
        )
        self.assertEqual(self.both(body)[1], "no")

    def test_mixed_heading_levels_are_siblings_not_nesting(self):
        # A deeper 'bug if' heading under a shallower 'expected' heading is
        # what people actually write; it must not be swallowed as prose.
        body = "### Expected Behaviour:\nyes\n\n##### This is a bug, not this decision, if...\nno\n"
        self.assertEqual(self.both(body), ("yes", "no"))

    def test_reversed_order(self):
        body = "## This is a bug, not this decision, if...\nno\n\n## Expected behavior\nyes\n"
        self.assertEqual(self.both(body), ("yes", "no"))

    def test_trailing_colon_and_period(self):
        body = "## Expected behavior:\nyes\n\n## This is a bug, not this decision, if:\nno\n"
        self.assertEqual(self.both(body), ("yes", "no"))

    def test_british_spelling(self):
        body = "## Expected behaviour\nyes\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_uppercase(self):
        body = "## EXPECTED BEHAVIOR\nyes\n\n## THIS IS A BUG, NOT THIS DECISION, IF...\nno\n"
        self.assertEqual(self.both(body), ("yes", "no"))

    def test_typographic_ellipsis(self):
        body = u"## This is a bug, not this decision, if…\nno\n"
        self.assertEqual(self.both(body)[1], "no")

    def test_two_dot_and_no_ellipsis(self):
        self.assertEqual(self.both("## This is a bug, not this decision, if\nno\n")[1], "no")
        self.assertEqual(self.both("## This is a bug, not this decision, if..\nno\n")[1], "no")

    def test_bold_heading(self):
        body = "## **Expected behavior**\nyes\n\n## __This is a bug, not this decision, if...__\nno\n"
        self.assertEqual(self.both(body), ("yes", "no"))

    def test_parenthesised_variant(self):
        body = "## this is a bug (not this decision) if:\nno\n"
        self.assertEqual(self.both(body)[1], "no")

    def test_em_dash_variant(self):
        body = u"## Not this decision — a bug — if\nno\n"
        self.assertEqual(self.both(body)[1], "no")

    def test_closed_atx_heading(self):
        body = "## Expected behavior ##\nyes\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_indented_heading(self):
        body = "   ## Expected behavior\nyes\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_alias_what_should_happen(self):
        self.assertEqual(self.both("## What should happen\nyes\n")[0], "yes")

    def test_setext_underline(self):
        body = "Expected behavior\n-----------------\nyes\n\n## Consequences\nlater\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_setext_equals_underline(self):
        body = "Expected behavior\n=================\nyes\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_horizontal_rule_after_paragraph_is_not_a_heading(self):
        # Only *our* headings may be underlined; otherwise a stray rule would
        # silently truncate the record.
        body = "## Expected behavior\nyes\n\nsome paragraph\n---\nstill expected\n"
        self.assertIn("still expected", self.both(body)[0])

    def test_section_ends_at_next_same_level_heading(self):
        body = "## Expected behavior\nyes\n\n## Consequences\nnot part of it\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_subheading_inside_section_is_kept(self):
        body = "## Expected behavior\nyes\n\n### Detail\nmore\n\n## Consequences\nno\n"
        expected = self.both(body)[0]
        self.assertIn("### Detail", expected)
        self.assertNotIn("Consequences", expected)

    def test_fenced_code_block_hashes_do_not_end_a_section(self):
        body = (
            "## Expected behavior\n"
            "yes\n"
            "\n"
            "```sh\n"
            "# Expected behavior\n"
            "## Consequences\n"
            "```\n"
            "\n"
            "tail\n"
            "\n"
            "## Consequences\n"
            "no\n"
        )
        expected = self.both(body)[0]
        self.assertIn("tail", expected)
        self.assertNotIn("\nno", expected)

    def test_tilde_fence(self):
        body = "## Expected behavior\nyes\n\n~~~\n## Consequences\n~~~\n"
        self.assertIn("## Consequences", self.both(body)[0])

    def test_blank_lines_are_trimmed(self):
        body = "## Expected behavior\n\n\nyes\n\n\n## Consequences\nno\n"
        self.assertEqual(self.both(body)[0], "yes")

    def test_multi_paragraph_section_is_kept_whole(self):
        body = "## Expected behavior\none\n\ntwo\n\n## Consequences\nno\n"
        self.assertEqual(self.both(body)[0], "one\n\ntwo")

    def test_empty_section_body(self):
        body = "## Expected behavior\n\n## Consequences\nno\n"
        self.assertEqual(self.both(body)[0], "")

    def test_first_occurrence_wins(self):
        body = "## Expected behavior\nfirst\n\n## Expected behavior\nsecond\n"
        self.assertEqual(self.both(body)[0], "first")

    def test_unrelated_headings_are_ignored(self):
        body = "## Decision\nx\n\n## Alternatives considered\ny\n"
        self.assertEqual(self.both(body), ("", ""))

    def test_bare_expected_heading(self):
        self.assertEqual(self.both("## Expected\nyes\n")[0], "yes")

    def test_expected_word_in_prose_is_not_a_heading(self):
        body = "Expected behavior is documented elsewhere.\n"
        self.assertEqual(self.both(body), ("", ""))


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


class LeadClaimTests(unittest.TestCase):
    SECTION = (
        "- verify writes the stamp and exits 0\n"
        "- verified_at is inert for a --paths change set, so the stale\n"
        "  finding survives\n"
        "- the stamp is scoped to one commit\n"
    )

    def test_without_a_query_the_first_bullet_wins(self):
        self.assertEqual(
            decisions.lead_claim(self.SECTION),
            "verify writes the stamp and exits 0",
        )

    def test_the_query_selects_the_bullet_that_answers_it(self):
        claim = decisions.lead_claim(
            self.SECTION, query="verified_at is ignored under --paths"
        )
        self.assertIn("--paths", claim)
        self.assertIn("inert", claim)

    def test_a_query_with_no_overlap_keeps_the_first_bullet(self):
        self.assertEqual(
            decisions.lead_claim(self.SECTION, query="zebra quokka"),
            "verify writes the stamp and exits 0",
        )

    def test_a_tie_keeps_the_first_bullet(self):
        section = "- alpha beta\n- alpha gamma\n"
        self.assertEqual(decisions.lead_claim(section, query="alpha"), "alpha beta")

    def test_full_returns_the_section_untouched(self):
        self.assertEqual(decisions.lead_claim(self.SECTION, full=True), self.SECTION)

    def test_a_section_without_bullets_is_its_first_paragraph(self):
        section = "One sentence. Another.\nsame paragraph\n\nlater paragraph\n"
        self.assertEqual(decisions.lead_claim(section), "One sentence.")

    def test_continuation_lines_join_their_bullet(self):
        bullets = decisions._bullets(self.SECTION)
        self.assertEqual(len(bullets), 3)
        self.assertIn("finding survives", bullets[1])


class TokenizeTests(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(decisions.tokenize("Retry-Budget, exhausted!"), ["retry", "budget", "exhausted"])

    def test_drops_stopwords(self):
        self.assertEqual(decisions.tokenize("the request is in the queue"), ["request", "queue"])

    def test_drops_single_characters(self):
        self.assertEqual(decisions.tokenize("a b retry 9 xy"), ["retry", "xy"])

    def test_stems_simple_plurals(self):
        self.assertEqual(decisions.tokenize("retries"), decisions.tokenize("retry"))
        self.assertEqual(decisions.tokenize("retried"), decisions.tokenize("retry"))
        self.assertEqual(decisions.tokenize("applied"), decisions.tokenize("apply"))
        self.assertEqual(decisions.tokenize("timeouts"), decisions.tokenize("timeout"))
        self.assertEqual(decisions.tokenize("classes"), decisions.tokenize("class"))

    def test_double_s_is_not_stripped(self):
        self.assertEqual(decisions.tokenize("bypass"), ["bypass"])

    def test_empty_and_none(self):
        self.assertEqual(decisions.tokenize(""), [])
        self.assertEqual(decisions.tokenize(None), [])

    def test_all_stopwords(self):
        self.assertEqual(decisions.tokenize("is it the one that we do"), [])

    def test_order_is_preserved(self):
        self.assertEqual(decisions.tokenize("budget retry"), ["budget", "retry"])


# ---------------------------------------------------------------------------
# why
# ---------------------------------------------------------------------------


class WhyTests(unittest.TestCase):
    def setUp(self):
        self.retry = make_decision(
            number="0001",
            slug="retry-budget",
            title="Retry budget is per request",
            summary="One budget per request.",
            symptoms=["a POST is retried exactly twice and then fails fast"],
            tags=["resilience"],
            expected="Two retries, shared by all hops.",
            not_this="Retries continue after the budget is exhausted.",
        )
        self.cache = make_decision(
            number="0002",
            slug="stale-cache-reads",
            title="Reads may return data up to 60 seconds stale",
            summary="The read path serves from a cache refreshed once a minute.",
            symptoms=["a value written a moment ago is still missing from a read"],
            tags=["cache"],
            expected="A write is visible within 60 seconds.",
            not_this="Staleness outlives a full refresh interval.",
        )
        self.all = [self.retry, self.cache]

    def test_symptom_match_wins(self):
        matches = decisions.why("a POST is retried exactly twice", self.all)
        self.assertEqual(matches[0].decision.id, "0001-retry-budget")
        self.assertEqual(matches[0].symptom, "a POST is retried exactly twice and then fails fast")

    def test_symptom_outranks_title(self):
        by_title = make_decision(
            number="0003", slug="pearl", title="retried twice", symptoms=[], summary="", tags=[]
        )
        matches = decisions.why("retried twice", [by_title, self.retry])
        self.assertEqual(matches[0].decision.id, "0001-retry-budget")

    def test_title_outranks_summary(self):
        titled = make_decision(number="0003", slug="a-doc", title="throttle", symptoms=[], summary="")
        summarised = make_decision(
            number="0004", slug="b-doc", title="unrelated", symptoms=[], summary="throttle"
        )
        matches = decisions.why("throttle", [summarised, titled])
        self.assertEqual(matches[0].decision.id, "0003-a-doc")

    def test_summary_outranks_tags(self):
        summarised = make_decision(
            number="0003", slug="a-doc", title="x", summary="throttle", symptoms=[]
        )
        tagged = make_decision(
            number="0004", slug="b-doc", title="x", summary="", symptoms=[], tags=["throttle"]
        )
        matches = decisions.why("throttle", [tagged, summarised])
        self.assertEqual(matches[0].decision.id, "0003-a-doc")

    def test_tags_alone_still_match(self):
        matches = decisions.why("resilience", self.all)
        self.assertEqual([m.decision.id for m in matches], ["0001-retry-budget"])

    def test_zero_score_records_are_dropped(self):
        matches = decisions.why("kubernetes ingress", self.all)
        self.assertEqual(matches, [])

    def test_one_shared_term_out_of_many_is_not_a_match(self):
        """A coincidental single-word overlap is noise, not a rival answer."""
        coincidence = make_decision(
            number="0009",
            slug="coincidence",
            title="Unrelated",
            summary="",
            symptoms=["the retry never happens"],
        )
        matches = decisions.why(
            "a stale value is served from the cache after a write", [coincidence]
        )
        self.assertEqual(matches, [])

    def test_a_record_far_below_the_leader_is_dropped(self):
        leader = make_decision(
            number="0001",
            slug="leader",
            title="Leader",
            summary="",
            symptoms=["the write is dropped silently under load"],
        )
        tail = make_decision(
            number="0002",
            slug="tail",
            title="Tail",
            summary="",
            symptoms=["the write is fine"],
        )
        matches = decisions.why("the write is dropped silently under load", [leader, tail])
        self.assertEqual([m.decision.id for m in matches], ["0001-leader"])

    def test_a_genuine_rival_survives_the_floor(self):
        leader = make_decision(
            number="0001",
            slug="leader",
            title="Leader",
            summary="",
            symptoms=["the write is dropped silently"],
        )
        rival = make_decision(
            number="0002",
            slug="rival",
            title="Rival",
            summary="",
            symptoms=["the write is dropped silently"],
        )
        matches = decisions.why("the write is dropped silently", [leader, rival])
        self.assertEqual(len(matches), 2)

    def test_empty_query(self):
        self.assertEqual(decisions.why("", self.all), [])
        self.assertEqual(decisions.why(None, self.all), [])

    def test_all_stopword_query(self):
        self.assertEqual(decisions.why("is it the one", self.all), [])

    def test_no_decisions(self):
        self.assertEqual(decisions.why("retry", []), [])

    def test_phrase_bonus_beats_scattered_overlap(self):
        quoting = make_decision(
            number="0003",
            slug="a-quote",
            title="x",
            summary="",
            symptoms=["the write is dropped silently"],
        )
        scattered = make_decision(
            number="0004",
            slug="b-scatter",
            title="write silently",
            summary="dropped",
            symptoms=["dropped connections cause a silently retried write"],
        )
        matches = decisions.why("write is dropped silently", [scattered, quoting])
        self.assertEqual(matches[0].decision.id, "0003-a-quote")

    def test_partial_overlap_scores_proportionally(self):
        matches = decisions.why("POST retried twice fails fast", self.all)
        full = matches[0].score
        matches = decisions.why("POST retried twice fails fast kubernetes ingress mesh", self.all)
        self.assertLess(matches[0].score, full)

    def test_tie_breaks_on_id(self):
        first = make_decision(number="0009", slug="zulu", title="throttle", symptoms=[], summary="")
        second = make_decision(number="0002", slug="alpha", title="throttle", symptoms=[], summary="")
        matches = decisions.why("throttle", [first, second])
        self.assertEqual(matches[0].score, matches[1].score)
        self.assertEqual([m.decision.id for m in matches], ["0002-alpha", "0009-zulu"])

    def test_ordering_is_stable_regardless_of_input_order(self):
        first = make_decision(number="0009", slug="zulu", title="throttle", symptoms=[], summary="")
        second = make_decision(number="0002", slug="alpha", title="throttle", symptoms=[], summary="")
        forward = [m.decision.id for m in decisions.why("throttle", [first, second])]
        backward = [m.decision.id for m in decisions.why("throttle", [second, first])]
        self.assertEqual(forward, backward)

    def test_best_symptom_is_reported_not_the_first(self):
        many = make_decision(
            number="0005",
            slug="many",
            title="x",
            summary="",
            symptoms=[
                "an unrelated warning appears in the log",
                "the request is retried exactly twice and then fails fast",
            ],
        )
        match = decisions.why("retried exactly twice", [many])[0]
        self.assertEqual(match.symptom, "the request is retried exactly twice and then fails fast")

    def test_symptom_is_none_when_only_metadata_matched(self):
        match = decisions.why("resilience", self.all)[0]
        self.assertIsNone(match.symptom)

    def test_limit(self):
        self.assertEqual(len(decisions.why("retried stale value budget", self.all, limit=1)), 1)
        self.assertEqual(decisions.why("retried", self.all, limit=0), [])

    def test_limit_none_returns_everything(self):
        matches = decisions.why("retried stale read budget", self.all, limit=None)
        self.assertEqual(len(matches), 2)

    def test_plural_query_matches_singular_symptom(self):
        match = decisions.why("retries", self.all)
        self.assertEqual(match[0].decision.id, "0001-retry-budget")

    def test_matched_terms_are_reported_in_query_order(self):
        # Reported in query order, in their normalized (stemmed) form.
        match = decisions.why("twice retried", self.all)[0]
        self.assertEqual(match.terms, ["twice", "retry"])

    def test_fields_explain_the_score(self):
        match = decisions.why("retried exactly twice", self.all)[0]
        self.assertIn("symptoms", match.fields)
        self.assertAlmostEqual(match.score, sum(match.fields.values()))

    def test_match_as_entry_is_json_ready(self):
        entry = decisions.why("retried exactly twice", self.all)[0].as_entry()
        self.assertEqual(entry["id"], "0001-retry-budget")
        self.assertIn("score", entry)
        self.assertIn("matched_symptom", entry)
        self.assertIn("expected", entry)


# ---------------------------------------------------------------------------
# format_why
# ---------------------------------------------------------------------------


class FormatWhyTests(unittest.TestCase):
    def setUp(self):
        self.decisions = [
            make_decision(
                number="%04d" % index,
                slug="decision-%d" % index,
                title="Title %d" % index,
                summary="throttle summary %d" % index,
                symptoms=["throttle symptom %d happens" % index],
                expected="Expected %d." % index,
                not_this="Bug %d." % index,
            )
            for index in range(1, 6)
        ]

    def render(self, query="throttle symptom", **kwargs):
        matches = decisions.why(query, self.decisions)
        return decisions.format_why(matches, query=query, **kwargs)

    def test_default_shows_three(self):
        text = self.render()
        self.assertEqual(text.count("symptom:"), 3)

    def test_default_is_compact(self):
        text = self.render()
        # header + 5 rendered lines and one blank separator per match, plus the
        # "further matches" note. Anything much larger is not a cheap lookup.
        self.assertLessEqual(len(text.splitlines()), 22)

    def test_shows_id_title_status_symptom_and_both_sections(self):
        text = decisions.format_why(
            decisions.why("throttle symptom 1", self.decisions), query="throttle symptom 1", limit=1
        )
        self.assertIn("0001-decision-1", text)
        self.assertIn("Title 1", text)
        self.assertIn("(accepted)", text)
        self.assertIn("throttle symptom 1 happens", text)
        self.assertIn("Expected 1.", text)
        self.assertIn("Bug 1.", text)
        self.assertIn("decisions/0001-decision-1.md", text)

    def test_body_beyond_the_two_sections_is_never_printed(self):
        decision = self.decisions[0]
        decision.body = "SECRET CONTEXT PARAGRAPH"
        text = decisions.format_why(decisions.why("throttle symptom 1", [decision]), limit=1)
        self.assertNotIn("SECRET CONTEXT PARAGRAPH", text)

    def test_hidden_matches_are_counted(self):
        text = self.render()
        self.assertIn("2 further matches scored lower.", text)

    def test_singular_further_match(self):
        text = decisions.format_why(decisions.why("throttle", self.decisions[:4]), limit=3)
        self.assertIn("1 further match scored lower.", text)

    def test_no_hidden_note_when_everything_shown(self):
        text = decisions.format_why(decisions.why("throttle", self.decisions[:2]), limit=3)
        self.assertNotIn("further match", text)

    def test_limit_none_renders_all(self):
        text = self.render(limit=None)
        self.assertEqual(text.count("symptom:"), 5)

    def test_no_matches_is_decisive(self):
        text = decisions.format_why([], query="nothing like this")
        self.assertIn('no decision matches "nothing like this"', text)
        self.assertIn("Treat it as unintended", text)

    def test_no_matches_without_a_query(self):
        self.assertIn("that description", decisions.format_why([]))

    def test_total_searched_is_reported(self):
        text = decisions.format_why(decisions.why("throttle", self.decisions), query="x", total=9)
        self.assertIn("(of 9 searched)", text)

    def test_singular_match_wording(self):
        text = decisions.format_why(decisions.why("throttle symptom 1", self.decisions[:1]), query="x")
        self.assertTrue(text.startswith("1 decision matches "), text)

    def test_plural_match_wording(self):
        text = decisions.format_why(decisions.why("throttle", self.decisions[:2]), query="x")
        self.assertTrue(text.startswith("2 decisions match "), text)

    def test_missing_sections_say_so(self):
        bare = make_decision(number="0007", slug="bare", title="Bare", symptoms=["a thing breaks"])
        text = decisions.format_why(decisions.why("thing breaks", [bare]))
        self.assertIn("not recorded", text)

    def test_long_sections_are_clipped_by_default(self):
        wordy = make_decision(
            number="0007",
            slug="wordy",
            title="Wordy",
            symptoms=["a thing breaks"],
            expected="word " * 120,
        )
        text = decisions.format_why(decisions.why("thing breaks", [wordy]))
        self.assertIn("...", text)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 100)

    def test_full_does_not_clip(self):
        wordy = make_decision(
            number="0007",
            slug="wordy",
            title="Wordy",
            symptoms=["a thing breaks"],
            expected="word " * 120,
        )
        text = decisions.format_why(decisions.why("thing breaks", [wordy]), full=True)
        self.assertNotIn("...", text)

    def test_wrapped_claim_collapses_to_one_line(self):
        multi = make_decision(
            number="0007",
            slug="multi",
            title="Multi",
            symptoms=["a thing breaks"],
            expected="first line\nsecond line",
            not_this="never",
        )
        text = decisions.format_why(decisions.why("thing breaks", [multi]))
        self.assertIn("expected: first line second line", text)

    def test_only_the_leading_claim_is_shown(self):
        """A section is a list of claims; one line of output is the first one."""
        multi = make_decision(
            number="0007",
            slug="multi",
            title="Multi",
            symptoms=["a thing breaks"],
            expected="first line\n\nsecond line",
            not_this="never",
        )
        text = decisions.format_why(decisions.why("thing breaks", [multi]))
        self.assertIn("expected: first line", text)
        self.assertNotIn("second line", text)

    def test_bullet_marker_is_not_rendered(self):
        bulleted = make_decision(
            number="0007",
            slug="bul",
            title="Bul",
            symptoms=["a thing breaks"],
            expected="- `cmd` does the thing.\n- and another claim\n",
            not_this="never",
        )
        text = decisions.format_why(decisions.why("thing breaks", [bulleted]))
        self.assertIn("expected: `cmd` does the thing.", text)
        self.assertNotIn("- `cmd`", text)
        self.assertNotIn("another claim", text)

    def test_full_keeps_the_whole_section(self):
        bulleted = make_decision(
            number="0007",
            slug="bul",
            title="Bul",
            symptoms=["a thing breaks"],
            expected="- one claim.\n- another claim",
            not_this="never",
        )
        text = decisions.format_why(decisions.why("thing breaks", [bulleted]), full=True)
        self.assertIn("another claim", text)

    def test_unknown_status_is_labelled(self):
        unknown = make_decision(number="0007", slug="x", symptoms=["a thing breaks"], status=None)
        text = decisions.format_why(decisions.why("thing breaks", [unknown]))
        self.assertIn("status unknown", text)

    def test_output_ends_with_a_newline(self):
        self.assertTrue(self.render().endswith("\n"))
        self.assertTrue(decisions.format_why([]).endswith("\n"))


# ---------------------------------------------------------------------------
# load / parse
# ---------------------------------------------------------------------------


class LoadTests(TempDirTestCase):
    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(decisions.load(self.root), [])

    def test_empty_directory(self):
        os.makedirs(os.path.join(self.root, "decisions"))
        self.assertEqual(decisions.load(self.root), [])

    def test_parses_frontmatter_and_sections(self):
        self.write("decisions/0001-retry-budget-per-request.md", decision_text())
        loaded = decisions.load(self.root)
        self.assertEqual(len(loaded), 1)
        record = loaded[0]
        self.assertEqual(record.id, "0001-retry-budget-per-request")
        self.assertEqual(record.number, "0001")
        self.assertEqual(record.slug, "retry-budget-per-request")
        self.assertEqual(record.status, "accepted")
        self.assertEqual(record.type, "decision")
        self.assertEqual(record.tags, ["retry", "resilience"])
        self.assertEqual(len(record.symptoms), 1)
        self.assertTrue(record.expected.startswith("Every request carries"))
        self.assertTrue(record.not_this.startswith("Retries continue"))
        self.assertEqual(record.path, "decisions/0001-retry-budget-per-request.md")

    def test_sorted_by_number(self):
        self.write("decisions/0003-c.md", decision_text(doc_id="0003-c"))
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        self.write("decisions/0002-b.md", decision_text(doc_id="0002-b"))
        self.assertEqual([d.number for d in decisions.load(self.root)], ["0001", "0002", "0003"])

    def test_non_conforming_filenames_sort_last_and_keep_no_number(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        self.write("decisions/README.md", decision_text(doc_id="readme"))
        loaded = decisions.load(self.root)
        self.assertEqual([d.number for d in loaded], ["0001", None])

    def test_non_markdown_files_are_ignored(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        self.write("decisions/notes.txt", "not a record")
        self.assertEqual(len(decisions.load(self.root)), 1)

    def test_nested_directories_are_walked(self):
        self.write("decisions/archive/0009-old.md", decision_text(doc_id="0009-old"))
        loaded = decisions.load(self.root)
        self.assertEqual(loaded[0].path, "decisions/archive/0009-old.md")

    def test_custom_root(self):
        self.write("adr/0001-a.md", decision_text(doc_id="0001-a"))
        loaded = decisions.load(self.root, root="adr")
        self.assertEqual(loaded[0].path, "adr/0001-a.md")

    def test_missing_frontmatter_yields_an_issue_not_an_exception(self):
        self.write("decisions/0001-a.md", "# Just a heading\n")
        record = decisions.load(self.root)[0]
        self.assertTrue(record.issues)

    def test_scalar_where_a_list_belongs_is_tolerated(self):
        text = decision_text(doc_id="0001-a", symptoms=())
        text = text.replace("symptoms: []", "symptoms: a single symptom line")
        self.write("decisions/0001-a.md", text)
        record = decisions.load(self.root)[0]
        self.assertEqual(record.symptoms, ["a single symptom line"])

    def test_parse_decision_directly(self):
        path = self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        record = decisions.parse_decision(path, "decisions/0001-a.md")
        self.assertEqual(record.id, "0001-a")

    def test_parse_decision_of_an_unreadable_path(self):
        record = decisions.parse_decision(os.path.join(self.root, "nope.md"), "decisions/nope.md")
        self.assertTrue(record.issues)
        self.assertEqual(record.symptoms, [])

    def test_from_doc_adapts_an_already_scanned_document(self):
        path = self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        parsed = decisions.parse_decision(path, "decisions/0001-a.md")

        class FakeDoc(object):
            pass

        doc = FakeDoc()
        doc.path = "decisions/0001-a.md"
        doc.root = "decisions"
        doc.frontmatter = parsed.frontmatter

        record = decisions.from_doc(doc)
        self.assertEqual(record.id, "0001-a")
        self.assertEqual(record.number, "0001")
        self.assertTrue(record.expected)

    def test_from_doc_without_frontmatter(self):
        class FakeDoc(object):
            path = "decisions/0001-a.md"
            root = "decisions"
            frontmatter = None

        record = decisions.from_doc(FakeDoc())
        self.assertEqual(record.number, "0001")
        self.assertEqual(record.symptoms, [])

    def test_as_entry_is_ordered(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        entry = decisions.load(self.root)[0].as_entry()
        self.assertEqual(entry["_order"][0], "id")
        self.assertIn("expected", entry)

    def test_why_over_loaded_records(self):
        self.write("decisions/0001-retry-budget-per-request.md", decision_text())
        loaded = decisions.load(self.root)
        matches = decisions.why("POST retried twice", loaded)
        self.assertEqual(matches[0].decision.id, "0001-retry-budget-per-request")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class ValidateTests(TempDirTestCase):
    def messages(self, records):
        return [issue.message for issue in decisions.validate(records)]

    def loaded(self):
        return decisions.load(self.root)

    def test_a_clean_record_produces_nothing(self):
        self.write("decisions/0001-retry-budget-per-request.md", decision_text())
        self.assertEqual(decisions.validate(self.loaded()), [])

    def test_empty_set(self):
        self.assertEqual(decisions.validate([]), [])

    def test_unknown_status(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", status="current"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("unknown decision 'status'" in m for m in messages))

    def test_every_documented_status_is_accepted(self):
        for status in decisions.DECISION_STATUSES:
            record = make_decision(status=status, symptoms=["x"], expected="e", not_this="n")
            if status == "superseded":
                record.superseded_by = ["0002-next"]
                other = make_decision(number="0002", slug="next", supersedes=[record.id])
                other.symptoms = ["y"]
                other.sections = {"expected": "e", "not_this": "n"}
                records = [record, other]
            else:
                records = [record]
            self.assertEqual(decisions.validate(records), [], status)

    def test_missing_status(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", status=None))
        self.assertIn("missing required frontmatter field 'status'", self.messages(self.loaded()))

    def test_wrong_type(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", doc_type="doc"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must have type 'decision'" in m for m in messages))

    def test_missing_type(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", doc_type=None))
        self.assertIn("missing required frontmatter field 'type'", self.messages(self.loaded()))

    def test_non_conforming_filename(self):
        self.write("decisions/notes-on-retries.md", decision_text(doc_id="notes-on-retries"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must be NNNN-slug.md" in m for m in messages))

    def test_three_digit_number_is_rejected(self):
        self.write("decisions/001-a.md", decision_text(doc_id="001-a"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must be NNNN-slug.md" in m for m in messages))

    def test_id_must_equal_the_filename_stem(self):
        self.write("decisions/0001-retry-budget.md", decision_text(doc_id="retry-budget"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must equal the filename stem" in m for m in messages))

    def test_missing_id(self):
        self.write("decisions/0001-a.md", decision_text(doc_id=None))
        self.assertIn("missing required frontmatter field 'id'", self.messages(self.loaded()))

    def test_duplicate_number(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        self.write("decisions/0001-b.md", decision_text(doc_id="0001-b"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("duplicate decision number 0001" in m for m in messages))

    def test_numbering_gap(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a"))
        self.write("decisions/0003-c.md", decision_text(doc_id="0003-c"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("not contiguous" in m and "0002" in m for m in messages))

    def test_numbering_must_start_at_one(self):
        self.write("decisions/0002-b.md", decision_text(doc_id="0002-b"))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must start at 0001" in m for m in messages))

    def test_contiguous_numbering_is_silent(self):
        for index in (1, 2, 3):
            self.write(
                "decisions/%04d-x%d.md" % (index, index),
                decision_text(doc_id="%04d-x%d" % (index, index)),
            )
        self.assertEqual(decisions.validate(self.loaded()), [])

    def test_accepted_requires_symptoms(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", symptoms=()))
        messages = self.messages(self.loaded())
        self.assertTrue(any("must list at least one entry under 'symptoms'" in m for m in messages))

    def test_proposed_may_omit_symptoms(self):
        self.write(
            "decisions/0001-a.md", decision_text(doc_id="0001-a", symptoms=(), status="proposed")
        )
        self.assertEqual(decisions.validate(self.loaded()), [])

    def test_symptoms_as_scalar_is_reported(self):
        text = decision_text(doc_id="0001-a", symptoms=()).replace("symptoms: []", "symptoms: one")
        self.write("decisions/0001-a.md", text)
        messages = self.messages(self.loaded())
        self.assertTrue(any("'symptoms' must be a list" in m for m in messages))

    def test_accepted_requires_the_expected_section(self):
        body = "\n## This is a bug, not this decision, if...\n\nno\n"
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", body=body))
        messages = self.messages(self.loaded())
        self.assertTrue(any("missing an 'Expected behavior' section" in m for m in messages))

    def test_accepted_requires_the_not_this_section(self):
        body = "\n## Expected behavior\n\nyes\n"
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", body=body))
        messages = self.messages(self.loaded())
        self.assertTrue(any("not this decision" in m and "missing" in m for m in messages))

    def test_rejected_record_needs_no_sections(self):
        self.write(
            "decisions/0001-a.md",
            decision_text(doc_id="0001-a", status="rejected", symptoms=(), body="\n# Nope\n"),
        )
        self.assertEqual(decisions.validate(self.loaded()), [])

    def test_supersedes_unknown_id(self):
        self.write(
            "decisions/0001-a.md", decision_text(doc_id="0001-a", supersedes=("0000-ghost",))
        )
        messages = self.messages(self.loaded())
        self.assertTrue(any("references unknown decision '0000-ghost'" in m for m in messages))

    def test_superseded_by_unknown_id(self):
        record = make_decision(status="superseded", symptoms=["x"], superseded_by=["0099-ghost"])
        messages = self.messages([record])
        self.assertTrue(any("references unknown decision '0099-ghost'" in m for m in messages))

    def test_supersedes_must_be_bidirectional(self):
        old = make_decision(number="0001", slug="old", status="superseded", symptoms=["x"])
        old.superseded_by = []
        new = make_decision(
            number="0002", slug="new", symptoms=["y"], supersedes=["0001-old"], expected="e", not_this="n"
        )
        messages = self.messages([old, new])
        self.assertTrue(any("does not list" in m and "superseded_by" in m for m in messages))

    def test_superseded_by_must_be_bidirectional(self):
        old = make_decision(
            number="0001",
            slug="old",
            status="superseded",
            symptoms=["x"],
            superseded_by=["0002-new"],
            expected="e",
            not_this="n",
        )
        new = make_decision(number="0002", slug="new", symptoms=["y"], expected="e", not_this="n")
        messages = self.messages([old, new])
        self.assertTrue(any("does not list" in m and "supersedes" in m for m in messages))

    def test_a_correct_supersession_pair_is_silent(self):
        old = make_decision(
            number="0001",
            slug="old",
            status="superseded",
            symptoms=["x"],
            superseded_by=["0002-new"],
            expected="e",
            not_this="n",
        )
        new = make_decision(
            number="0002",
            slug="new",
            symptoms=["y"],
            supersedes=["0001-old"],
            expected="e",
            not_this="n",
        )
        self.assertEqual(decisions.validate([old, new]), [])

    def test_a_chain_ending_in_a_rejected_record_is_reported(self):
        """Regression: bidirectional links alone let a decision retire with no live successor."""
        old = make_decision(
            number="0001",
            slug="old",
            status="superseded",
            symptoms=["x"],
            superseded_by=["0002-new"],
            expected="e",
            not_this="n",
        )
        new = make_decision(
            number="0002",
            slug="new",
            status="rejected",
            symptoms=["y"],
            supersedes=["0001-old"],
            expected="e",
            not_this="n",
        )
        messages = self.messages([old, new])
        self.assertTrue(
            any("never reaches a decision that is in force" in m for m in messages), messages
        )
        self.assertTrue(any("0002-new" in m for m in messages), messages)

    def test_a_supersession_cycle_is_reported(self):
        first = make_decision(
            number="0001",
            slug="old",
            status="superseded",
            symptoms=["x"],
            supersedes=["0002-new"],
            superseded_by=["0002-new"],
            expected="e",
            not_this="n",
        )
        second = make_decision(
            number="0002",
            slug="new",
            status="superseded",
            symptoms=["y"],
            supersedes=["0001-old"],
            superseded_by=["0001-old"],
            expected="e",
            not_this="n",
        )
        messages = self.messages([first, second])
        loops = [m for m in messages if "loops back to this record" in m]
        self.assertEqual(len(loops), 2, messages)

    def test_a_chain_through_a_superseded_record_to_an_accepted_one_is_silent(self):
        first = make_decision(
            number="0001", slug="a", status="superseded", symptoms=["x"],
            superseded_by=["0002-b"], expected="e", not_this="n",
        )
        second = make_decision(
            number="0002", slug="b", status="superseded", symptoms=["y"],
            supersedes=["0001-a"], superseded_by=["0003-c"], expected="e", not_this="n",
        )
        third = make_decision(
            number="0003", slug="c", symptoms=["z"], supersedes=["0002-b"],
            expected="e", not_this="n",
        )
        self.assertEqual(decisions.validate([first, second, third]), [])

    def test_self_reference_is_rejected(self):
        record = make_decision(symptoms=["x"], expected="e", not_this="n")
        record.supersedes = [record.id]
        messages = self.messages([record])
        self.assertTrue(any("names this decision itself" in m for m in messages))

    def test_superseded_must_name_a_successor(self):
        record = make_decision(status="superseded", symptoms=["x"], expected="e", not_this="n")
        messages = self.messages([record])
        self.assertTrue(any("must name its successor" in m for m in messages))

    def test_naming_a_successor_requires_the_superseded_status(self):
        old = make_decision(
            number="0001", slug="old", symptoms=["x"], superseded_by=["0002-new"], expected="e", not_this="n"
        )
        new = make_decision(
            number="0002", slug="new", symptoms=["y"], supersedes=["0001-old"], expected="e", not_this="n"
        )
        messages = self.messages([old, new])
        self.assertTrue(any("expected 'superseded'" in m for m in messages))

    def test_issues_carry_a_path(self):
        self.write("decisions/0001-a.md", decision_text(doc_id="0001-a", status="current"))
        issue = decisions.validate(self.loaded())[0]
        self.assertEqual(issue.path, "decisions/0001-a.md")
        self.assertIn("decisions/0001-a.md", issue.located())

    def test_parse_issues_are_included(self):
        self.write("decisions/0001-a.md", "no frontmatter here\n")
        self.assertTrue(decisions.validate(self.loaded()))

    def test_a_file_without_frontmatter_reports_once(self):
        self.write("decisions/0001-a.md", "no frontmatter here\n")
        issues = decisions.validate(self.loaded())
        self.assertEqual(len(issues), 1)

    def test_custom_root_is_named_in_messages(self):
        record = make_decision(number="0005", slug="x", symptoms=["y"], expected="e", not_this="n")
        issues = decisions.validate([record], root="adr")
        self.assertTrue(any(issue.path == "adr" for issue in issues))


# ---------------------------------------------------------------------------
# numbering
# ---------------------------------------------------------------------------


class NumberingTests(TempDirTestCase):
    def test_absent_directory(self):
        self.assertEqual(decisions.next_number(self.root), "0001")
        self.assertEqual(decisions.numbers(self.root), [])

    def test_empty_directory(self):
        os.makedirs(os.path.join(self.root, "decisions"))
        self.assertEqual(decisions.next_number(self.root), "0001")

    def test_sequential(self):
        self.write("decisions/0001-a.md", "x")
        self.write("decisions/0002-b.md", "x")
        self.assertEqual(decisions.next_number(self.root), "0003")

    def test_gaps_are_not_filled(self):
        self.write("decisions/0001-a.md", "x")
        self.write("decisions/0004-d.md", "x")
        self.assertEqual(decisions.next_number(self.root), "0005")

    def test_non_conforming_filenames_are_ignored(self):
        self.write("decisions/README.md", "x")
        self.write("decisions/draft.md", "x")
        self.write("decisions/12-short.md", "x")
        self.write("decisions/00007b-odd.md", "x")
        self.write("decisions/0002-real.md", "x")
        self.assertEqual(decisions.numbers(self.root), ["0002"])
        self.assertEqual(decisions.next_number(self.root), "0003")

    def test_rolls_past_four_digits_predictably(self):
        self.write("decisions/9999-last.md", "x")
        self.assertEqual(decisions.next_number(self.root), "10000")

    def test_nested_directories_count(self):
        self.write("decisions/archive/0007-old.md", "x")
        self.assertEqual(decisions.next_number(self.root), "0008")

    def test_markdown_extension_variant(self):
        self.write("decisions/0003-c.markdown", "x")
        self.assertEqual(decisions.next_number(self.root), "0004")

    def test_custom_root(self):
        self.write("adr/0006-f.md", "x")
        self.assertEqual(decisions.next_number(self.root, root="adr"), "0007")
        self.assertEqual(decisions.next_number(self.root), "0001")

    def test_next_number_from_records(self):
        records = [make_decision(number="0001"), make_decision(number="0004")]
        self.assertEqual(decisions.next_number_from(records), "0005")

    def test_next_number_from_empty(self):
        self.assertEqual(decisions.next_number_from([]), "0001")

    def test_next_number_from_ignores_unnumbered_records(self):
        record = make_decision()
        record.number = None
        self.assertEqual(decisions.next_number_from([record]), "0001")

    def test_numbers_does_not_read_file_contents(self):
        # A record whose frontmatter is unparseable still holds its number.
        self.write("decisions/0005-broken.md", "---\nnot: [closed\n")
        self.assertEqual(decisions.numbers(self.root), ["0005"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""The v2 command surface: decisions, `why`, the debt ledger, export, portability.

These are wiring tests. The three modules under test (`decisions`, `debt`,
`export`/`portability`) have their own exhaustive suites; what is checked here is
that the CLI reaches them with the right arguments, that every new subcommand has
a working ``--json`` form, and that the two contracts the rest of the system
depends on are not broken by any of it:

* ``.gotdocs/INDEX.md`` never grows a ``symptoms`` line - it is loaded whole into
  an agent's context on every session
* the pre-existing ``--json`` shapes (``ok`` / ``mode`` / ``findings`` /
  ``summary``) keep their meaning
"""

import io
import json
import os
import unittest

try:  # works both as a package and via `discover -s tools/gotdocs/tests`
    from . import support
except ImportError:  # pragma: no cover - depends on how the suite was invoked
    import support

from tools.gotdocs import cli
from tools.gotdocs import config as config_module
from tools.gotdocs import debt as debt_module
from tools.gotdocs import index as index_module


DECISION_BODY = """
# {title}

## Context

Something was true.

## Decision

We decided a thing.

## Expected behavior

- a POST is retried exactly twice and then fails fast

## This is a bug, not this decision, if...

- retries continue past the third attempt
"""


def decision_text(
    doc_id="0001-retry-budget",
    title="Retry budget is per request",
    status="accepted",
    summary="Retries are budgeted once per end-to-end request.",
    covers=("src/**",),
    symptoms=("a POST is retried exactly twice and then fails fast",),
    supersedes=(),
    superseded_by=(),
    decided_on="2026-08-01",
    updated="2026-08-01",
):
    lines = ["---", "id: %s" % (doc_id,), "title: %s" % (title,), "type: decision"]
    lines.append("summary: %s" % (summary,))
    lines.append("covers: [%s]" % (", ".join(covers),) if covers else "covers: []")
    if symptoms:
        lines.append("symptoms:")
        for symptom in symptoms:
            lines.append('  - "%s"' % (symptom,))
    else:
        lines.append("symptoms: []")
    lines.append("supersedes: [%s]" % (", ".join(supersedes),) if supersedes else "supersedes: []")
    lines.append(
        "superseded_by: [%s]" % (", ".join(superseded_by),)
        if superseded_by
        else "superseded_by: []"
    )
    lines.append("owners: []")
    lines.append("tags: []")
    lines.append("status: %s" % (status,))
    if decided_on:
        lines.append("decided_on: %s" % (decided_on,))
    lines.append("updated: %s" % (updated,))
    lines.append("---")
    return "\n".join(lines) + DECISION_BODY.format(title=title)


class WiringTestCase(support.TempRepoTestCase):
    """A repo with a docs root, a decisions root and one commit."""

    def setUp(self):
        super().setUp()
        self.write_config(roots=["docs", "decisions"])
        self.write("docs/component.md", support.doc_text(doc_id="component", covers=["src/**"]))
        self.write("src/app.py", "print('v1')\n")
        self.run_cli("index")
        self.commit("initial")

    def run_cli(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        code = cli.main(list(args) + ["--repo", self.root, "--no-color"], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *args):
        code, out, err = self.run_cli(*(list(args) + ["--json"]))
        return code, json.loads(out), err

    def add_decision(self, **kwargs):
        doc_id = kwargs.get("doc_id", "0001-retry-budget")
        self.write("decisions/%s.md" % (doc_id,), decision_text(**kwargs))
        return doc_id


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class ConfigBlockTests(support.TempRepoTestCase):
    def load(self, payload):
        support.write(
            self.root, config_module.CONFIG_PATH, json.dumps(payload, indent=2) + "\n"
        )
        return config_module.load(self.root)

    def test_missing_blocks_fall_back_to_defaults(self):
        config = self.load({"roots": ["docs"]})
        self.assertTrue(config.debt_enabled)
        self.assertEqual(config.debt_ledger, ".gotdocs/debt.jsonl")
        self.assertEqual(config.debt_report, ".gotdocs/DEBT.md")
        self.assertEqual(config.publish_option("target"), "docusaurus")
        self.assertTrue(config.publish_option("h1_in_body"))

    def test_partial_block_keeps_the_other_defaults(self):
        config = self.load({"debt": {"enabled": False}})
        self.assertFalse(config.debt_enabled)
        self.assertEqual(config.debt_ledger, ".gotdocs/debt.jsonl")

    def test_unknown_sub_keys_are_recorded_not_rejected(self):
        # An older CLI must still load a config written by a newer one.
        config = self.load({"debt": {"future_option": 1}, "publish": {"whatever": "x"}})
        self.assertEqual(config.unknown_keys, ["debt.future_option", "publish.whatever"])
        self.assertTrue(config.debt_enabled)

    def test_wrong_types_are_rejected(self):
        from tools.gotdocs.errors import ConfigError

        for payload in (
            {"debt": {"enabled": "yes"}},
            {"debt": {"record_kinds": "stale"}},
            {"debt": {"max_report_lines": 0}},
            {"debt": []},
            {"publish": {"h1_in_body": "true"}},
            {"publish": {"target": 3}},
        ):
            with self.assertRaises(ConfigError, msg=repr(payload)):
                self.load(payload)

    def test_null_block_is_the_default_not_a_crash(self):
        config = self.load({"debt": None, "publish": None})
        self.assertTrue(config.debt_enabled)
        self.assertEqual(config.publish_option("out_dir"), "build/gotdocs-site")

    def test_decisions_root_follows_the_configured_roots(self):
        self.assertEqual(self.load({"roots": ["docs"]}).decisions_root, "decisions")
        self.assertEqual(self.load({"roots": ["docs", "decisions"]}).decisions_root, "decisions")
        self.assertEqual(
            self.load({"roots": ["docs", "docs/decisions"]}).decisions_root, "docs/decisions"
        )

    def test_debt_paths_are_ignored_by_default(self):
        config = config_module.Config()
        self.assertTrue(config.is_ignored(".gotdocs/debt.jsonl"))
        self.assertTrue(config.is_ignored(".gotdocs/DEBT.md"))


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


class DecisionIndexTests(WiringTestCase):
    def test_decision_fields_reach_index_json(self):
        self.add_decision()
        self.run_cli("index")
        payload = json.loads(self.read(config_module.INDEX_JSON_PATH))
        entry = [doc for doc in payload["docs"] if doc["type"] == "decision"][0]
        self.assertEqual(entry["id"], "0001-retry-budget")
        self.assertEqual(entry["status"], "accepted")
        self.assertEqual(
            entry["symptoms"], ["a POST is retried exactly twice and then fails fast"]
        )
        self.assertEqual(entry["decided_on"], "2026-08-01")
        self.assertEqual(entry["supersedes"], [])
        self.assertEqual(entry["superseded_by"], [])

    def test_ordinary_documents_keep_their_exact_key_set(self):
        # index.json is a published contract; adding decision fields to every
        # entry would change the shape for every existing consumer.
        self.add_decision()
        self.run_cli("index")
        payload = json.loads(self.read(config_module.INDEX_JSON_PATH))
        entry = [doc for doc in payload["docs"] if doc["id"] == "component"][0]
        self.assertEqual(
            sorted(entry),
            sorted(
                [
                    "id",
                    "path",
                    "type",
                    "title",
                    "summary",
                    "status",
                    "covers",
                    "owners",
                    "tags",
                    "updated",
                    "verified_at",
                ]
            ),
        )

    def test_symptoms_never_reach_index_md(self):
        self.add_decision()
        self.run_cli("index")
        text = self.read(config_module.INDEX_MD_PATH)
        self.assertIn("## Decisions", text)
        self.assertIn("0001-retry-budget", text)
        # The symptom corpus is several lines per record and this file is read
        # whole on every session; only `gotdocs why` may pay for it.
        self.assertNotIn("retried exactly twice", text)
        self.assertNotIn("symptoms:", text)
        self.assertNotIn("supersedes", text)
        self.assertNotIn("decided_on", text)

    def test_index_md_lists_accepted_and_counts_the_rest(self):
        self.add_decision()
        self.write(
            "decisions/0002-old.md",
            decision_text(
                doc_id="0002-old",
                title="Old",
                status="superseded",
                superseded_by=["0003-new"],
                symptoms=(),
            ),
        )
        self.write(
            "decisions/0003-new.md",
            decision_text(doc_id="0003-new", title="New", status="rejected", symptoms=()),
        )
        self.run_cli("index")
        text = self.read(config_module.INDEX_MD_PATH)
        self.assertIn("- **0001-retry-budget**", text)
        self.assertNotIn("- **0002-old**", text)
        self.assertNotIn("- **0003-new**", text)
        self.assertIn("Not listed: 1 rejected, 1 superseded.", text)

    def test_no_decisions_means_no_section(self):
        self.run_cli("index")
        self.assertNotIn("## Decisions", self.read(config_module.INDEX_MD_PATH))

    def test_index_stays_byte_identical_across_runs(self):
        self.add_decision()
        self.run_cli("index")
        first = self.read(config_module.INDEX_JSON_PATH), self.read(config_module.INDEX_MD_PATH)
        code, out, _err = self.run_cli("index")
        self.assertEqual(code, 0)
        self.assertIn("no changes", out)
        self.assertEqual(
            (self.read(config_module.INDEX_JSON_PATH), self.read(config_module.INDEX_MD_PATH)),
            first,
        )

    def test_decision_statuses_are_a_separate_enum(self):
        self.add_decision(status="current")
        doc_set = index_module.scan(self.root, self.config())
        messages = " | ".join(issue.message for issue in doc_set.issues)
        self.assertIn("unknown decision 'status' 'current'", messages)
        self.assertIn("proposed, accepted, rejected, superseded", messages)

    def test_ordinary_documents_reject_decision_statuses(self):
        self.write("docs/x.md", support.doc_text(doc_id="x", status="accepted"))
        doc_set = index_module.scan(self.root, self.config())
        messages = " | ".join(issue.message for issue in doc_set.issues)
        self.assertIn("unknown 'status' 'accepted'", messages)

    def test_decision_statuses_do_not_pollute_the_status_counts(self):
        self.add_decision()
        doc_set = index_module.scan(self.root, self.config())
        self.assertEqual(doc_set.counts_by_status()["unknown"], 0)
        self.assertEqual(len(doc_set.decisions()), 1)


# ---------------------------------------------------------------------------
# new decision
# ---------------------------------------------------------------------------


class NewDecisionTests(WiringTestCase):
    def test_allocates_the_first_number_and_slugifies_the_title(self):
        code, out, _err = self.run_cli(
            "new", "decision", "Retry budget is per request, not per hop"
        )
        self.assertEqual(code, 0)
        self.assertIn("decisions/0001-retry-budget-is-per-request-not-per-hop.md", out)
        text = self.read("decisions/0001-retry-budget-is-per-request-not-per-hop.md")
        self.assertIn("id: 0001-retry-budget-is-per-request-not-per-hop", text)
        self.assertIn("type: decision", text)
        self.assertIn("status: proposed", text)
        self.assertIn("title: Retry budget is per request, not per hop", text)

    def test_numbers_increase_and_are_never_reused(self):
        self.run_cli("new", "decision", "First")
        self.run_cli("new", "decision", "Second")
        self.assertPathExists("decisions/0002-second.md")
        os.remove(os.path.join(self.root, "decisions", "0001-first.md"))
        self.run_cli("new", "decision", "Third")
        # 0001 is free again but must not be handed out: other records, commit
        # messages and review threads already point at it.
        self.assertPathExists("decisions/0003-third.md")

    def test_symptoms_are_written_as_a_quoted_block_list(self):
        self.run_cli(
            "new",
            "decision",
            "Retry budget",
            "--symptom",
            "a POST is retried: exactly twice",
            "--symptom",
            "the wait is 400ms",
        )
        text = self.read("decisions/0001-retry-budget.md")
        self.assertIn('  - "a POST is retried: exactly twice"\n', text)
        self.assertIn("  - the wait is 400ms\n", text)

    def test_the_scaffold_lints_clean(self):
        self.run_cli("new", "decision", "Retry budget", "--symptom", "it retries twice")
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 0, out)

    def test_covers_globs_are_still_written_bare(self):
        self.run_cli("new", "decision", "Retry budget", "--covers", "src/**")
        self.assertIn("  - src/**\n", self.read("decisions/0001-retry-budget.md"))

    def test_symptom_on_a_non_decision_is_a_usage_error(self):
        code, _out, err = self.run_cli("new", "doc", "thing", "--symptom", "x")
        self.assertEqual(code, 2)
        self.assertIn("--symptom applies to `new decision` only", err)

    def test_a_title_with_no_word_characters_is_rejected(self):
        code, _out, err = self.run_cli("new", "decision", "!!!")
        self.assertEqual(code, 2)
        self.assertIn("at least one letter or digit", err)

    def test_json_form(self):
        code, payload, _err = self.run_json("new", "decision", "Retry budget")
        self.assertEqual(code, 0)
        self.assertEqual(payload["id"], "0001-retry-budget")
        self.assertEqual(payload["type"], "decision")
        self.assertEqual(payload["path"], "decisions/0001-retry-budget.md")


# ---------------------------------------------------------------------------
# why
# ---------------------------------------------------------------------------


class WhyTests(WiringTestCase):
    def test_no_decisions_at_all_is_not_an_error(self):
        code, out, _err = self.run_cli("why", "requests are retried twice")
        self.assertEqual(code, 0)
        self.assertIn("no decision matches", out)
        self.assertIn("Treat it as unintended", out)

    def test_matches_on_a_symptom(self):
        self.add_decision()
        code, out, _err = self.run_cli("why", "a POST is retried twice")
        self.assertEqual(code, 0)
        self.assertIn("0001-retry-budget", out)
        self.assertIn("(accepted)", out)
        self.assertIn("symptom:", out)
        self.assertIn("bug if:", out)

    def test_superseded_records_are_hidden_unless_all(self):
        self.add_decision(
            status="superseded",
            superseded_by=["0002-next"],
            symptoms=("a POST is retried exactly twice and then fails fast",),
        )
        code, out, _err = self.run_cli("why", "a POST is retried twice")
        self.assertEqual(code, 0)
        self.assertIn("no decision matches", out)

        code, out, _err = self.run_cli("why", "a POST is retried twice", "--all")
        self.assertEqual(code, 0)
        self.assertIn("0001-retry-budget", out)

    def test_path_lookup_uses_covers(self):
        self.add_decision(covers=("src/**",))
        code, out, _err = self.run_cli("why", "--path", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("1 decision covers src/app.py", out)
        self.assertIn("0001-retry-budget", out)

        code, out, _err = self.run_cli("why", "--path", "elsewhere/other.py")
        self.assertEqual(code, 0)
        self.assertIn("no decision record covers elsewhere/other.py", out)

    def test_path_and_query_together_narrow_then_rank(self):
        self.add_decision(covers=("src/**",))
        self.write(
            "decisions/0002-other.md",
            decision_text(
                doc_id="0002-other",
                title="Other",
                covers=("web/**",),
                symptoms=("a POST is retried exactly twice and then fails fast",),
            ),
        )
        code, out, _err = self.run_cli("why", "a POST is retried twice", "--path", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("0001-retry-budget", out)
        self.assertNotIn("0002-other", out)

    def test_json_form_carries_the_sections_and_the_score(self):
        self.add_decision()
        code, payload, _err = self.run_json("why", "a POST is retried twice")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["searched"], 1)
        self.assertEqual(payload["match_count"], 1)
        match = payload["matches"][0]
        self.assertEqual(match["id"], "0001-retry-budget")
        self.assertEqual(match["status"], "accepted")
        self.assertIn("retried exactly twice", match["matched_symptom"])
        self.assertIn("retried exactly twice", match["expected"])
        self.assertIn("past the third attempt", match["not_this"])
        self.assertGreater(match["score"], 0)

    def test_json_form_for_a_path_lookup(self):
        self.add_decision()
        code, payload, _err = self.run_json("why", "--path", "src/app.py")
        self.assertEqual(code, 0)
        self.assertEqual(payload["path"], "src/app.py")
        self.assertIsNone(payload["query"])
        self.assertEqual(len(payload["matches"]), 1)

    def test_needs_something_to_look_up(self):
        code, _out, err = self.run_cli("why")
        self.assertEqual(code, 2)
        self.assertIn("why needs something to look up", err)

    def test_limit_zero_means_every_match(self):
        for number in range(1, 6):
            self.write(
                "decisions/000%d-x.md" % (number,),
                decision_text(
                    doc_id="000%d-x" % (number,),
                    title="X%d" % (number,),
                    symptoms=("retried twice %d" % (number,),),
                ),
            )
        code, payload, _err = self.run_json("why", "retried twice", "--limit", "0")
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["matches"]), 5)


# ---------------------------------------------------------------------------
# lint: decisions and portability
# ---------------------------------------------------------------------------


class LintWiringTests(WiringTestCase):
    def test_decision_rules_are_enforced(self):
        # accepted, but with no symptoms: `why` could never find it.
        self.add_decision(symptoms=())
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 2)
        self.assertIn("symptoms", out)

    def test_broken_supersede_link_is_reported(self):
        self.add_decision(supersedes=["0002-missing"])
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 2)
        self.assertIn("unknown decision", out)

    def test_filename_number_must_match_the_id(self):
        self.write("decisions/0001-retry-budget.md", decision_text(doc_id="wrong-id"))
        code, out, _err = self.run_cli("lint")
        self.assertEqual(code, 2)
        self.assertIn("0001-retry-budget", out)

    def test_a_lint_problem_is_reported_once_not_twice(self):
        # index and decisions.validate both notice a missing 'type'.
        self.write(
            "decisions/0001-x.md",
            "---\nid: 0001-x\ntitle: X\nsummary: s\ncovers: []\nstatus: accepted\n"
            "updated: 2026-08-01\n---\n\n# X\n",
        )
        _code, payload, _err = self.run_json("lint")
        messages = [
            finding["message"] for finding in payload["findings"] if "'type'" in finding["message"]
        ]
        self.assertEqual(len(messages), len(set(messages)))

    def test_portability_issues_are_warnings_by_default(self):
        self.write(
            "docs/bad.md",
            support.doc_text(
                doc_id="bad", covers=[], body="\n# Bad\n\n[gone](../nowhere/missing.md)\n"
            ),
        )
        code, payload, _err = self.run_json("lint", "--portability")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])
        self.assertTrue(payload["warnings"])
        self.assertEqual(payload["summary"]["warnings"], len(payload["warnings"]))

    def test_strict_promotes_them_to_errors(self):
        self.write(
            "docs/bad.md",
            support.doc_text(
                doc_id="bad", covers=[], body="\n# Bad\n\n[gone](../nowhere/missing.md)\n"
            ),
        )
        code, payload, _err = self.run_json("lint", "--portability", "--strict")
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["findings"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["findings"][0]["kind"], "portability")

    def test_plain_lint_is_unaffected_by_portability_problems(self):
        self.write(
            "docs/bad.md",
            support.doc_text(
                doc_id="bad", covers=[], body="\n# Bad\n\n[gone](../nowhere/missing.md)\n"
            ),
        )
        code, payload, _err = self.run_json("lint")
        self.assertEqual(code, 0)
        self.assertEqual(payload["warnings"], [])

    def test_unknown_target_and_rule_are_usage_errors(self):
        code, _out, err = self.run_cli("lint", "--portability", "--targets", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown portability target", err)
        code, _out, err = self.run_cli("lint", "--portability", "--rules", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown portability rule", err)


# ---------------------------------------------------------------------------
# debt
# ---------------------------------------------------------------------------


class DebtCommandTests(WiringTestCase):
    def make_stale(self):
        self.write("src/app.py", "print('v2')\n")
        self.add("src/app.py")

    def ledger(self):
        entries, errors = debt_module.load_ledger(self.root)
        self.assertEqual([error.message for error in errors], [])
        return entries

    def test_record_writes_the_ledger_stamped_with_the_commit_date(self):
        self.make_stale()
        code, out, _err = self.run_cli("debt", "record", "--staged", "--source", "hook")
        self.assertEqual(code, 0)
        self.assertIn("+1 new", out)
        entries = self.ledger()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, "stale")
        self.assertEqual(entries[0].doc_id, "component")
        self.assertEqual(entries[0].note, "hook")
        # The commit date, not today: the ledger has to regenerate to the same
        # bytes on any machine on any day.
        self.assertEqual(entries[0].first_seen_date, "2026-01-01")
        self.assertEqual(entries[0].first_seen_sha, self.head())

    def test_recording_twice_bumps_occurrences_instead_of_appending(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        self.run_cli("debt", "record", "--staged")
        entries = self.ledger()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].occurrences, 2)
        self.assertEqual(self.read(debt_module.LEDGER_PATH).count("\n"), 1)

    def test_record_is_a_no_op_when_debt_is_disabled(self):
        self.write_config(roots=["docs", "decisions"], debt={"enabled": False})
        self.make_stale()
        code, _out, err = self.run_cli("debt", "record", "--staged")
        self.assertEqual(code, 0)
        self.assertIn("disabled", err)
        self.assertFalse(
            os.path.exists(os.path.join(self.root, debt_module.LEDGER_PATH.replace("/", os.sep)))
        )

    def test_record_honours_the_configured_kinds(self):
        self.write_config(roots=["docs", "decisions"], debt={"record_kinds": ["lint"]})
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        self.assertEqual(self.ledger(), [])

    def test_dry_run_writes_nothing(self):
        self.make_stale()
        code, out, _err = self.run_cli("debt", "record", "--staged", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("preview", out)
        self.assertEqual(self.ledger(), [])

    def test_resolve_absent_closes_what_is_gone(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        self.run_cli("verify", "component")
        self.run_cli("index")
        self.add("docs/component.md", ".gotdocs/index.json", ".gotdocs/INDEX.md")
        self.run_cli("debt", "record", "--staged", "--resolve-absent")
        entries = self.ledger()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].is_open)
        self.assertEqual(entries[0].resolved_date, "2026-01-01")

    def test_resolve_absent_cannot_wipe_debt_for_paths_it_did_not_look_at(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        # A run scoped to an unrelated path must leave the entry alone.
        self.run_cli("debt", "record", "--paths", "unrelated/other.py", "--resolve-absent")
        self.assertTrue(self.ledger()[0].is_open)

    def make_lint_error(self):
        """A second, repo-wide finding kind alongside the stale one."""
        self.write(
            "docs/broken.md",
            support.doc_text(doc_id="broken", covers=["src/**"], updated="not-a-date"),
        )
        self.add("docs/broken.md")

    def test_a_kind_filter_does_not_auto_resolve_the_kinds_it_hid(self):
        """Regression: --kinds filtered the findings *before* --resolve-absent."""
        self.make_stale()
        self.make_lint_error()
        self.run_cli("debt", "record", "--staged")
        kinds = sorted({entry.kind for entry in self.ledger()})
        self.assertIn("lint", kinds)

        code, _out, _err = self.run_cli(
            "debt", "record", "--staged", "--kinds", "stale", "--resolve-absent"
        )
        self.assertEqual(code, 0)
        still_open = sorted(e.kind for e in self.ledger() if e.is_open)
        self.assertIn("lint", still_open)
        self.assertEqual([e.kind for e in self.ledger() if not e.is_open], [])

    def test_record_kinds_config_does_not_auto_resolve_the_kinds_it_hid(self):
        self.make_stale()
        self.make_lint_error()
        self.run_cli("debt", "record", "--staged")
        self.write_config(roots=["docs", "decisions"], debt={"record_kinds": ["stale"]})
        code, _out, _err = self.run_cli("debt", "resolve", "--auto")
        self.assertEqual(code, 0)
        self.assertIn("lint", sorted(e.kind for e in self.ledger() if e.is_open))

    def test_a_failed_resolve_does_not_create_an_empty_ledger(self):
        """Regression: `debt resolve <unknown>` left an untracked zero-byte file."""
        path = os.path.join(self.root, debt_module.LEDGER_PATH.replace("/", os.sep))
        self.assertFalse(os.path.exists(path))
        code, out, err = self.run_cli("debt", "resolve", "zzz-nope")
        self.assertEqual(code, 2)
        self.assertIn("no debt entry matches", out + err)
        self.assertFalse(os.path.exists(path))

    def test_several_findings_on_one_document_are_one_sighting(self):
        """Regression: N lint errors in one file were recorded as `seen Nx`."""
        self.write(
            "docs/broken.md",
            support.doc_text(
                doc_id="broken",
                covers=["src/**"],
                status="bogus",
                updated="not-a-date",
                verified_at="zzz",
            ),
        )
        self.add("docs/broken.md")
        self.run_cli("debt", "record", "--staged", "--kinds", "lint")
        lint = [e for e in self.ledger() if e.kind == "lint"]
        self.assertEqual(len(lint), 1)
        self.assertEqual(lint[0].occurrences, 1)
        self.assertIn("more finding", lint[0].message)

    def test_list_defaults_to_open_and_filters(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        code, out, _err = self.run_cli("debt", "list")
        self.assertEqual(code, 0)
        self.assertIn("component", out)

        code, payload, _err = self.run_json("debt", "list", "--kind", "uncovered")
        self.assertEqual(code, 0)
        self.assertEqual(payload["filtered"], [])
        self.assertEqual(payload["summary"]["open"], 1)

    def test_resolve_by_doc_id_then_by_entry_id(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        entry_id = self.ledger()[0].entry_id
        code, out, _err = self.run_cli("debt", "resolve", "component", "--note", "fixed")
        self.assertEqual(code, 0)
        self.assertIn(entry_id, out)
        entry = self.ledger()[0]
        self.assertFalse(entry.is_open)
        self.assertEqual(entry.note, "fixed")

    def test_resolving_an_unknown_reference_is_a_usage_error(self):
        code, out, _err = self.run_cli("debt", "resolve", "nothing-like-this")
        self.assertEqual(code, 2)
        self.assertIn("no debt entry matches", out)

    def test_render_writes_the_markdown_report(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        code, _out, _err = self.run_cli("debt", "render")
        self.assertEqual(code, 0)
        text = self.read(debt_module.MARKDOWN_PATH)
        self.assertIn("# Doc debt", text)
        self.assertIn("component", text)
        # Deterministic: a second render changes nothing.
        self.run_cli("debt", "render")
        self.assertEqual(self.read(debt_module.MARKDOWN_PATH), text)

    def test_render_to_stdout_writes_no_file(self):
        code, out, _err = self.run_cli("debt", "render", "--stdout")
        self.assertEqual(code, 0)
        self.assertIn("# Doc debt", out)
        self.assertFalse(
            os.path.exists(os.path.join(self.root, debt_module.MARKDOWN_PATH.replace("/", os.sep)))
        )

    def test_stats_json(self):
        self.make_stale()
        self.run_cli("debt", "record", "--staged")
        code, payload, _err = self.run_json("debt", "stats")
        self.assertEqual(code, 0)
        self.assertEqual(payload["summary"]["open"], 1)
        self.assertEqual(payload["summary"]["open_by_kind"], {"stale": 1})
        self.assertEqual(payload["ledger"], ".gotdocs/debt.jsonl")

    def test_a_corrupt_ledger_line_costs_one_entry_not_the_command(self):
        self.write(debt_module.LEDGER_PATH, "not json at all\n")
        code, payload, err = self.run_json("debt", "stats")
        self.assertEqual(code, 0)
        self.assertEqual(payload["summary"]["total"], 0)
        self.assertTrue(payload["ledger_errors"])
        self.assertIn("unparseable JSON", err)

    def test_bare_debt_prints_help(self):
        code, out, _err = self.run_cli("debt")
        self.assertEqual(code, 0)
        self.assertIn("record", out)
        self.assertIn("resolve", out)

    def test_a_bad_date_is_a_usage_error(self):
        code, _out, err = self.run_cli("debt", "record", "--staged", "--date", "yesterday")
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


class ExportCommandTests(WiringTestCase):
    def test_defaults_come_from_the_publish_block(self):
        self.write_config(
            roots=["docs", "decisions"],
            publish={"target": "mkdocs", "out_dir": "site-out"},
        )
        code, payload, _err = self.run_json("export")
        self.assertEqual(code, 0)
        self.assertEqual(payload["target"], "mkdocs")
        self.assertEqual(payload["out_dir"], "site-out")
        self.assertPathExists("site-out/docs/component.md")
        self.assertPathExists("site-out/_gotdocs.json")

    def test_flags_win_over_the_config(self):
        code, payload, _err = self.run_json("export", "--target", "hugo", "--out", "other")
        self.assertEqual(code, 0)
        self.assertEqual(payload["target"], "hugo")
        self.assertPathExists("other/docs/component.md")

    def test_running_twice_writes_nothing_the_second_time(self):
        self.run_cli("export", "--out", "site-out")
        code, payload, _err = self.run_json("export", "--out", "site-out")
        self.assertEqual(code, 0)
        self.assertEqual(payload["written"], [])

    def test_dry_run_writes_nothing(self):
        code, payload, _err = self.run_json("export", "--out", "site-out", "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["documents"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "site-out")))

    def test_list_targets(self):
        code, payload, _err = self.run_json("export", "--list-targets")
        self.assertEqual(code, 0)
        names = [target["target"] for target in payload["targets"]]
        self.assertIn("docusaurus", names)
        self.assertIn("github", names)

    def test_unknown_target_is_a_usage_error(self):
        code, _out, err = self.run_cli("export", "--target", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_drafts_are_skipped_unless_asked_for(self):
        self.write("docs/wip.md", support.doc_text(doc_id="wip", status="draft", covers=[]))
        code, payload, _err = self.run_json("export", "--out", "site-out")
        self.assertEqual(payload["skipped"], 1)
        code, payload, _err = self.run_json("export", "--out", "site-out2", "--include-drafts")
        self.assertEqual(payload["skipped"], 0)


# ---------------------------------------------------------------------------
# the stable surface
# ---------------------------------------------------------------------------


class ContractTests(WiringTestCase):
    def test_every_subcommand_has_a_help_and_a_json_form(self):
        commands = [
            ["check"],
            ["impacted", "src/app.py"],
            ["index"],
            ["lint"],
            ["status"],
            ["why", "anything"],
            ["export", "--list-targets"],
            ["debt", "list"],
            ["debt", "stats"],
            ["debt", "render", "--stdout"],
        ]
        for command in commands:
            code, out, _err = self.run_cli(*(command[:1] + ["--help"]))
            self.assertEqual(code, 0, command)
            self.assertTrue(out.strip(), command)
            code, out, err = self.run_cli(*(command + ["--json"]))
            self.assertIn(code, (0, 1, 2), command)
            json.loads(out)  # must be parseable, whatever the exit code

    def test_check_json_keeps_its_documented_shape(self):
        code, payload, _err = self.run_json("check")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), ["findings", "mode", "ok", "summary"])

    def test_top_level_help_lists_the_new_commands(self):
        code, out, _err = self.run_cli("--help")
        self.assertEqual(code, 0)
        for name in ("why", "export", "debt"):
            self.assertIn(name, out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

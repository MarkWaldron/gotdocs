"""Export: per-target frontmatter, link rewriting, the manifest, determinism.

Every target is checked against its own real conventions -- Hugo's
``weight``/``date``, Jekyll's ``layout``, Docusaurus' ``sidebar_position``/
``slug``, Starlight's ``description``/``sidebar.order``, MkDocs' plain
frontmatter, and plain GitHub's no-frontmatter-at-all. The last case exports
this repository's own documents, twice, from a temp copy.
"""

import io
import json
import os
import shutil
import tempfile
import unittest

try:  # works both as a package and under `discover -s tools/gotdocs/tests`
    from . import support
except ImportError:  # pragma: no cover - import shim
    import support
from tools.gotdocs import config as config_module
from tools.gotdocs import export
from tools.gotdocs import frontmatter as fm_module
from tools.gotdocs import index as index_module
from tools.gotdocs.errors import UsageError


REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Targets whose frontmatter is flat enough for the gotdocs parser to read back.
FLAT_TARGETS = ("mkdocs", "jekyll", "hugo")


def frontmatter_of(text):
    parsed = fm_module.parse_text(text, "output.md")
    return parsed


def keys_of(text):
    """The frontmatter keys of an exported file, in emitted order."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return []
    keys = []
    for line in lines[1:]:
        if line == "---":
            break
        if not line or line.startswith(" ") or line.startswith("#"):
            continue
        keys.append(line.split(":", 1)[0])
    return keys


class TargetTests(unittest.TestCase):
    def test_all_six_targets_exist(self):
        self.assertEqual(
            export.target_names(),
            ["docusaurus", "mkdocs", "starlight", "jekyll", "hugo", "github"],
        )

    def test_unknown_target_is_a_usage_error(self):
        with self.assertRaises(UsageError) as caught:
            export.get_target("sphinx")
        self.assertIn("docusaurus", str(caught.exception))

    def test_key_map_describes_the_mapping(self):
        hugo = export.get_target("hugo").key_map()
        self.assertEqual(hugo["summary"], "description")
        self.assertEqual(hugo["updated"], "date, lastmod")
        self.assertEqual(hugo["(derived order)"], "weight")
        self.assertEqual(export.get_target("github").key_map(), {})

    def test_target_as_dict_lists_stripped_keys(self):
        payload = export.get_target("starlight").as_dict()
        self.assertEqual(payload["link_style"], "extensionless")
        self.assertIn("covers", payload["stripped"])


class YamlScalarTests(unittest.TestCase):
    def test_plain_values_are_not_quoted(self):
        self.assertEqual(export._yaml_scalar("Start Here"), "Start Here")
        self.assertEqual(export._yaml_scalar("End-to-end design — of gotdocs"),
                         "End-to-end design — of gotdocs")
        self.assertEqual(export._yaml_scalar("2026-01-01"), "2026-01-01")

    def test_ambiguous_values_are_quoted(self):
        self.assertEqual(export._yaml_scalar("yes"), '"yes"')
        self.assertEqual(export._yaml_scalar("3.14"), '"3.14"')
        self.assertEqual(export._yaml_scalar("- leading dash"), '"- leading dash"')
        self.assertEqual(export._yaml_scalar("key: value"), '"key: value"')
        self.assertEqual(export._yaml_scalar("trailing "), '"trailing "')
        self.assertEqual(export._yaml_scalar(""), '""')

    def test_booleans_and_integers(self):
        self.assertEqual(export._yaml_scalar(True), "true")
        self.assertEqual(export._yaml_scalar(False), "false")
        self.assertEqual(export._yaml_scalar(30), "30")

    def test_quotes_are_escaped_only_when_the_value_must_be_quoted(self):
        # A plain scalar may contain quotes; it may not start with one.
        self.assertEqual(export._yaml_scalar('a "b"'), 'a "b"')
        self.assertEqual(export._yaml_scalar('"quoted"'), '"\\"quoted\\""')
        self.assertEqual(export._yaml_scalar("a\nb"), '"a\\nb"')


class ExportBase(support.TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                summary="End-to-end design of the thing.",
                covers=["src/**"],
                tags=("design", "internals"),
                owners=("@mark",),
                updated="2026-08-01",
                verified_at="abc1234",
                body="\n# How It Works\n\nSee [the guide](guide.md) and [code](../src/app.py).\n",
            ),
        )
        self.write(
            "docs/guide.md",
            support.doc_text(
                doc_id="guide",
                title="Guide",
                summary="How to use it.",
                covers=["src/**"],
                updated="2026-08-02",
                body="\n# Guide\n\nBack to [architecture](architecture.md).\n",
            ),
        )

    def export(self, target, **kwargs):
        return export.export_docs(self.root, self.config(), target, **kwargs)

    def file_for(self, result, source):
        for exported in result.files:
            if exported.source == source:
                return exported
        raise AssertionError("no export of %s" % (source,))


class FrontmatterMappingTests(ExportBase):
    def test_docusaurus(self):
        text = self.file_for(self.export("docusaurus"), "docs/architecture.md").text
        self.assertEqual(
            keys_of(text),
            ["title", "description", "id", "slug", "sidebar_position", "tags", "last_update"],
        )
        self.assertIn("title: How It Works", text)
        self.assertIn("description: End-to-end design of the thing.", text)
        self.assertIn("slug: /docs/architecture", text)
        self.assertIn("sidebar_position: 1", text)
        self.assertIn("last_update:\n  date: 2026-08-01", text)

    def test_mkdocs_is_plain(self):
        text = self.file_for(self.export("mkdocs"), "docs/architecture.md").text
        self.assertEqual(keys_of(text), ["title", "description", "date", "tags"])
        self.assertIn("date: 2026-08-01", text)

    def test_starlight_only_emits_schema_keys(self):
        text = self.file_for(self.export("starlight"), "docs/architecture.md").text
        self.assertEqual(keys_of(text), ["title", "description", "sidebar", "lastUpdated"])
        self.assertIn("sidebar:\n  order: 1", text)
        self.assertNotIn("tags:", text)

    def test_jekyll_needs_a_layout(self):
        text = self.file_for(self.export("jekyll"), "docs/architecture.md").text
        self.assertEqual(
            keys_of(text), ["layout", "title", "description", "date", "nav_order", "tags"]
        )
        self.assertTrue(text.startswith("---\nlayout: page\n"))

    def test_jekyll_layout_is_configurable(self):
        text = self.file_for(self.export("jekyll", layout="doc"), "docs/architecture.md").text
        self.assertIn("layout: doc", text)

    def test_hugo_uses_weight_and_both_dates(self):
        text = self.file_for(self.export("hugo"), "docs/architecture.md").text
        self.assertEqual(
            keys_of(text), ["title", "description", "date", "lastmod", "weight", "tags"]
        )
        self.assertIn("weight: 10", text)
        self.assertIn("lastmod: 2026-08-01", text)

    def test_hugo_never_emits_type(self):
        text = self.file_for(self.export("hugo"), "docs/architecture.md").text
        self.assertNotIn("type:", text)

    def test_github_emits_no_frontmatter(self):
        text = self.file_for(self.export("github"), "docs/architecture.md").text
        self.assertFalse(text.startswith("---"))
        self.assertTrue(text.startswith("# How It Works\n"))

    def test_internal_keys_are_stripped_everywhere(self):
        for name in export.target_names():
            text = self.file_for(self.export(name), "docs/architecture.md").text
            keys = keys_of(text)
            for stripped in ("covers", "owners", "status", "verified_at", "type", "summary"):
                self.assertNotIn(stripped, keys, "%s leaked %s" % (name, stripped))

    def test_body_h1_is_removed_when_the_target_renders_the_title(self):
        for name in ("docusaurus", "mkdocs", "starlight", "jekyll", "hugo"):
            text = self.file_for(self.export(name), "docs/architecture.md").text
            body = text.split("---\n", 2)[2]
            self.assertFalse(body.lstrip().startswith("# How It Works"), name)

    def test_github_inserts_a_missing_h1(self):
        self.write(
            "docs/nohead.md",
            support.doc_text(doc_id="nohead", title="No Head", body="\nJust prose.\n"),
        )
        text = self.file_for(self.export("github"), "docs/nohead.md").text
        self.assertEqual(text.splitlines()[0], "# No Head")

    def test_flat_targets_reparse_with_the_gotdocs_parser(self):
        for name in FLAT_TARGETS:
            text = self.file_for(self.export(name), "docs/architecture.md").text
            parsed = frontmatter_of(text)
            self.assertTrue(parsed.present, name)
            self.assertEqual([issue.message for issue in parsed.issues], [], name)
            self.assertEqual(parsed.get_scalar("title"), "How It Works", name)


class OrderingTests(ExportBase):
    def test_order_is_derived_per_directory_by_path(self):
        result = self.export("hugo")
        orders = dict((item.source, item.order) for item in result.files)
        self.assertEqual(orders["docs/architecture.md"], 1)
        self.assertEqual(orders["docs/guide.md"], 2)

    def test_index_pages_sort_first(self):
        self.write(
            "docs/index.md",
            support.doc_text(doc_id="index", title="Index", body="\n# Index\n"),
        )
        result = self.export("hugo")
        orders = dict((item.source, item.order) for item in result.files)
        self.assertEqual(orders["docs/index.md"], 1)
        self.assertEqual(orders["docs/architecture.md"], 2)

    def test_an_explicit_order_key_wins(self):
        self.write(
            "docs/guide.md",
            support.doc_text(
                doc_id="guide",
                title="Guide",
                body="\n# Guide\n",
                extra_lines=["order: 99"],
            ),
        )
        result = self.export("jekyll")
        text = self.file_for(result, "docs/guide.md").text
        self.assertIn("nav_order: 99", text)

    def test_orders_restart_in_each_directory(self):
        self.write(
            "docs/deep/one.md",
            support.doc_text(doc_id="deep-one", title="One", body="\n# One\n"),
        )
        result = self.export("docusaurus")
        text = self.file_for(result, "docs/deep/one.md").text
        self.assertIn("sidebar_position: 1", text)


class LinkRewritingTests(ExportBase):
    def dest_list(self, text):
        import re

        return re.findall(r"\]\(([^)]+)\)", text)

    def test_markdown_targets_keep_relative_md_links(self):
        for name in ("docusaurus", "mkdocs", "github"):
            text = self.file_for(self.export(name), "docs/architecture.md").text
            self.assertIn("guide.md", self.dest_list(text)[0], name)

    def test_jekyll_rewrites_to_html(self):
        text = self.file_for(self.export("jekyll"), "docs/architecture.md").text
        self.assertEqual(self.dest_list(text)[0], "guide.html")

    def test_hugo_uses_site_absolute_pretty_urls(self):
        text = self.file_for(self.export("hugo"), "docs/architecture.md").text
        self.assertEqual(self.dest_list(text)[0], "/docs/guide/")

    def test_starlight_uses_extensionless_routes(self):
        text = self.file_for(self.export("starlight"), "docs/architecture.md").text
        self.assertEqual(self.dest_list(text)[0], "/docs/guide")

    def test_url_prefix_is_applied(self):
        text = self.file_for(
            self.export("hugo", url_prefix="/handbook/"), "docs/architecture.md"
        ).text
        self.assertEqual(self.dest_list(text)[0], "/handbook/docs/guide/")

    def test_anchors_survive_rewriting(self):
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                body="\n# How It Works\n\n[g](guide.md#section)\n",
            ),
        )
        text = self.file_for(self.export("hugo"), "docs/architecture.md").text
        self.assertEqual(self.dest_list(text)[0], "/docs/guide/#section")

    def test_external_and_absolute_links_are_untouched(self):
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                body="\n# How It Works\n\n[a](https://x.test/y.md) [b](/already/absolute)\n",
            ),
        )
        text = self.file_for(self.export("jekyll"), "docs/architecture.md").text
        self.assertEqual(self.dest_list(text), ["https://x.test/y.md", "/already/absolute"])

    def test_links_inside_code_are_untouched(self):
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                body="\n# How It Works\n\n```md\n[g](guide.md)\n```\n\nInline `[g](guide.md)`.\n",
            ),
        )
        text = self.file_for(self.export("jekyll"), "docs/architecture.md").text
        self.assertNotIn("guide.html", text)
        self.assertEqual(text.count("[g](guide.md)"), 2)

    def test_links_to_code_are_left_alone_without_a_source_url(self):
        text = self.file_for(self.export("hugo"), "docs/architecture.md").text
        self.assertIn("../src/app.py", text)

    def test_source_url_rewrites_links_to_code(self):
        text = self.file_for(
            self.export("hugo", source_url="https://github.test/o/r/blob/main/"),
            "docs/architecture.md",
        ).text
        self.assertIn("https://github.test/o/r/blob/main/src/app.py", text)

    def test_relative_links_from_a_nested_document(self):
        self.write(
            "docs/deep/one.md",
            support.doc_text(
                doc_id="deep-one",
                title="One",
                body="\n# One\n\n[up](../guide.md)\n",
            ),
        )
        text = self.file_for(self.export("jekyll"), "docs/deep/one.md").text
        self.assertEqual(self.dest_list(text)[0], "../guide.html")


class LinkEncodingTests(ExportBase):
    """Regression: destinations were percent-*decoded* and re-emitted raw."""

    def setUp(self):
        super().setUp()
        self.write(
            "docs/release notes.md",
            support.doc_text(
                doc_id="release-notes",
                title="Release Notes",
                summary="What shipped.",
                covers=["src/**"],
                updated="2026-08-03",
                body="\n# Release Notes\n\nNotes.\n",
            ),
        )
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                summary="End-to-end design of the thing.",
                covers=["src/**"],
                updated="2026-08-01",
                body=(
                    "\n# How It Works\n\n"
                    "See [a](./release%20notes.md) and [b](<./release notes.md>).\n"
                ),
            ),
        )

    def dests(self, text):
        import re

        return re.findall(r"\]\(<?([^)>]+)>?\)", text)

    def test_a_space_is_re_encoded_for_every_target(self):
        for name in ("docusaurus", "mkdocs", "starlight", "jekyll", "hugo", "github"):
            text = self.file_for(self.export(name), "docs/architecture.md").text
            for dest in self.dests(text):
                self.assertNotIn(" ", dest, "%s: %r" % (name, dest))
                self.assertIn("%20", dest, "%s: %r" % (name, dest))

    def test_both_spellings_resolve_to_the_same_destination(self):
        text = self.file_for(self.export("mkdocs"), "docs/architecture.md").text
        found = self.dests(text)
        self.assertEqual(found[0], found[1], found)

    def test_an_ordinary_path_is_left_alone(self):
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                summary="End-to-end design of the thing.",
                covers=["src/**"],
                updated="2026-08-01",
                body="\n# How It Works\n\nSee [g](guide.md).\n",
            ),
        )
        text = self.file_for(self.export("mkdocs"), "docs/architecture.md").text
        self.assertEqual(self.dests(text), ["guide.md"])


class AssetTests(ExportBase):
    def setUp(self):
        super().setUp()
        self.write("docs/img/logo.png", "PNGDATA")
        self.write(
            "docs/architecture.md",
            support.doc_text(
                doc_id="architecture",
                title="How It Works",
                body="\n# How It Works\n\n![logo](img/logo.png)\n",
            ),
        )

    def test_assets_are_recorded_in_the_manifest(self):
        result = self.export("hugo")
        self.assertEqual(
            result.manifest["assets"],
            [{"source": "docs/img/logo.png", "output": "docs/img/logo.png"}],
        )

    def test_assets_are_copied_by_write_export(self):
        out = os.path.join(self.root, "build")
        result = export.write_export(self.root, self.config(), "hugo", out)
        copied = os.path.join(out, "docs", "img", "logo.png")
        self.assertTrue(os.path.isfile(copied))
        with io.open(copied, "rb") as handle:
            self.assertEqual(handle.read(), b"PNGDATA")
        self.assertIn("docs/img/logo.png", result.written)


class DraftTests(ExportBase):
    def setUp(self):
        super().setUp()
        self.write(
            "docs/wip.md",
            support.doc_text(doc_id="wip", title="WIP", status="draft", body="\n# WIP\n"),
        )

    def test_drafts_are_skipped_by_default(self):
        result = self.export("hugo")
        self.assertNotIn("docs/wip.md", [item.source for item in result.files])
        self.assertEqual(result.skipped, [{"path": "docs/wip.md", "id": "wip", "reason": "draft"}])

    def test_drafts_are_included_on_request_and_marked(self):
        result = self.export("hugo", include_drafts=True)
        text = self.file_for(result, "docs/wip.md").text
        self.assertIn("draft: true", text)
        self.assertEqual(result.skipped, [])

    def test_jekyll_marks_drafts_unpublished(self):
        text = self.file_for(self.export("jekyll", include_drafts=True), "docs/wip.md").text
        self.assertIn("published: false", text)

    def test_docusaurus_unlists_deprecated_documents(self):
        self.write(
            "docs/old.md",
            support.doc_text(doc_id="old", title="Old", status="deprecated", body="\n# Old\n"),
        )
        text = self.file_for(self.export("docusaurus"), "docs/old.md").text
        self.assertIn("unlisted: true", text)

    def test_starlight_badges_deprecated_documents(self):
        self.write(
            "docs/old.md",
            support.doc_text(doc_id="old", title="Old", status="deprecated", body="\n# Old\n"),
        )
        text = self.file_for(self.export("starlight"), "docs/old.md").text
        self.assertIn("badge: Deprecated", text)


class ManifestTests(ExportBase):
    def test_manifest_carries_the_stripped_keys(self):
        result = self.export("hugo")
        entry = [item for item in result.manifest["docs"] if item["id"] == "architecture"][0]
        self.assertEqual(entry["source"], "docs/architecture.md")
        self.assertEqual(entry["output"], "docs/architecture.md")
        self.assertEqual(entry["url"], "/docs/architecture/")
        self.assertEqual(entry["covers"], ["src/**"])
        self.assertEqual(entry["owners"], ["@mark"])
        self.assertEqual(entry["verified_at"], "abc1234")
        self.assertEqual(entry["type"], "doc")
        self.assertEqual(entry["status"], "current")
        self.assertEqual(entry["order"], 1)

    def test_manifest_carries_the_decision_fields(self):
        """Regression: symptoms/supersedes/superseded_by/decided_on vanished from the export."""
        self.write("docs/guide.md", support.doc_text(doc_id="guide", covers=["src/**"]))
        self.write(
            "docs/adr.md",
            "\n".join(
                [
                    "---",
                    "id: 0001-a-decision",
                    "title: A Decision",
                    "type: decision",
                    "summary: Retries are budgeted once per end-to-end request here.",
                    "covers: [src/**]",
                    "symptoms:",
                    "  - a POST is retried exactly twice",
                    "supersedes: []",
                    "superseded_by: []",
                    "owners: []",
                    "tags: []",
                    "status: accepted",
                    "decided_on: 2026-08-01",
                    "updated: 2026-08-01",
                    "---",
                    "",
                    "# A Decision",
                    "",
                    "Body.",
                ]
            )
            + "\n",
        )
        result = self.export("hugo")
        entry = [i for i in result.manifest["docs"] if i["id"] == "0001-a-decision"][0]
        self.assertEqual(entry["symptoms"], ["a POST is retried exactly twice"])
        self.assertEqual(entry["supersedes"], [])
        self.assertEqual(entry["superseded_by"], [])
        self.assertEqual(entry["decided_on"], "2026-08-01")
        # ...and an ordinary doc is untouched, so existing manifests do not churn.
        plain = [i for i in result.manifest["docs"] if i["id"] == "architecture"][0]
        self.assertNotIn("symptoms", plain)

    def test_manifest_header(self):
        manifest = self.export("starlight", url_prefix="handbook").manifest
        self.assertEqual(manifest["version"], export.MANIFEST_VERSION)
        self.assertEqual(manifest["target"], "starlight")
        self.assertEqual(manifest["link_style"], "extensionless")
        self.assertEqual(manifest["url_prefix"], "handbook")
        self.assertEqual(manifest["doc_count"], len(manifest["docs"]))

    def test_manifest_keeps_unknown_frontmatter_in_extra(self):
        self.write(
            "docs/guide.md",
            support.doc_text(
                doc_id="guide", title="Guide", body="\n# Guide\n", extra_lines=["team: platform"]
            ),
        )
        manifest = self.export("mkdocs").manifest
        entry = [item for item in manifest["docs"] if item["id"] == "guide"][0]
        self.assertEqual(entry["extra"]["team"], "platform")

    def test_manifest_is_valid_json_with_a_trailing_newline(self):
        text = self.export("mkdocs").manifest_text()
        self.assertTrue(text.endswith("\n"))
        json.loads(text)

    def test_manifest_has_no_volatile_fields(self):
        text = self.export("mkdocs").manifest_text()
        for volatile in ("generated_at", "timestamp", "sha"):
            self.assertNotIn(volatile, text)


class WriteTests(ExportBase):
    def test_writes_the_tree_and_the_manifest(self):
        out = os.path.join(self.root, "build")
        result = export.write_export(self.root, self.config(), "mkdocs", out)
        self.assertTrue(os.path.isfile(os.path.join(out, "docs", "architecture.md")))
        self.assertTrue(os.path.isfile(os.path.join(out, export.MANIFEST_NAME)))
        self.assertIn("docs/architecture.md", result.written)
        self.assertIn(export.MANIFEST_NAME, result.written)

    def test_second_run_writes_nothing(self):
        out = os.path.join(self.root, "build")
        export.write_export(self.root, self.config(), "mkdocs", out)
        again = export.write_export(self.root, self.config(), "mkdocs", out)
        self.assertEqual(again.written, [])

    def test_output_is_byte_identical_across_runs(self):
        first = os.path.join(self.root, "one")
        second = os.path.join(self.root, "two")
        export.write_export(self.root, self.config(), "hugo", first)
        export.write_export(self.root, self.config(), "hugo", second)
        for relative in ("docs/architecture.md", "docs/guide.md", export.MANIFEST_NAME):
            with io.open(os.path.join(first, relative.replace("/", os.sep)), "rb") as handle:
                left = handle.read()
            with io.open(os.path.join(second, relative.replace("/", os.sep)), "rb") as handle:
                right = handle.read()
            self.assertEqual(left, right, relative)

    def test_clean_removes_documents_that_no_longer_exist(self):
        out = os.path.join(self.root, "build")
        export.write_export(self.root, self.config(), "mkdocs", out)
        os.remove(os.path.join(self.root, "docs", "guide.md"))
        export.write_export(self.root, self.config(), "mkdocs", out, clean=True)
        self.assertFalse(os.path.exists(os.path.join(out, "docs", "guide.md")))
        self.assertTrue(os.path.exists(os.path.join(out, "docs", "architecture.md")))

    def test_clean_leaves_foreign_files_alone(self):
        out = os.path.join(self.root, "build")
        export.write_export(self.root, self.config(), "mkdocs", out)
        support.write(out, "docs/handwritten.md", "# Hand written\n")
        os.remove(os.path.join(self.root, "docs", "guide.md"))
        export.write_export(self.root, self.config(), "mkdocs", out, clean=True)
        self.assertTrue(os.path.exists(os.path.join(out, "docs", "handwritten.md")))

    def test_every_output_ends_with_exactly_one_newline(self):
        for name in export.target_names():
            for exported in self.export(name).files:
                self.assertTrue(exported.text.endswith("\n"), name)
                self.assertFalse(exported.text.endswith("\n\n"), name)

    def test_summary_reports_counts(self):
        out = os.path.join(self.root, "build")
        summary = export.write_export(self.root, self.config(), "mkdocs", out).summary()
        self.assertEqual(summary["documents"], 2)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["out_dir"], out)


class CrlfTests(ExportBase):
    def test_windows_line_endings_are_normalized(self):
        self.write(
            "docs/guide.md",
            support.doc_text(doc_id="guide", title="Guide", body="\n# Guide\n\ntext\n").replace(
                "\n", "\r\n"
            ),
        )
        text = self.file_for(self.export("mkdocs"), "docs/guide.md").text
        self.assertNotIn("\r", text)


class RepoFixtureTests(unittest.TestCase):
    """Export this repository's own documents, twice, in a temp copy."""

    @classmethod
    def setUpClass(cls):
        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="gotdocs-export-"))
        for name in ("docs", "runbooks", "onboarding", "dependencies"):
            source = os.path.join(REPO_ROOT, name)
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(cls.root, name))
        os.makedirs(os.path.join(cls.root, config_module.CONFIG_DIR))
        shutil.copyfile(
            os.path.join(REPO_ROOT, config_module.CONFIG_PATH),
            os.path.join(cls.root, config_module.CONFIG_PATH),
        )
        cls.config = config_module.load(cls.root)
        cls.doc_count = len(index_module.scan(cls.root, cls.config).docs)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def test_the_fixture_has_documents(self):
        self.assertGreaterEqual(self.doc_count, 8)

    def test_every_target_exports_every_document(self):
        for name in export.target_names():
            result = export.export_docs(self.root, self.config, name)
            self.assertEqual(len(result.files), self.doc_count, name)

    def test_every_target_is_byte_deterministic(self):
        for name in export.target_names():
            first = export.export_docs(self.root, self.config, name)
            second = export.export_docs(self.root, self.config, name)
            self.assertEqual(
                [(item.path, item.text) for item in first.files],
                [(item.path, item.text) for item in second.files],
                name,
            )
            self.assertEqual(first.manifest_text(), second.manifest_text(), name)

    def test_flat_targets_produce_parsable_frontmatter(self):
        for name in FLAT_TARGETS:
            for exported in export.export_docs(self.root, self.config, name).files:
                parsed = fm_module.parse_text(exported.text, exported.path)
                self.assertTrue(parsed.present, exported.path)
                self.assertEqual(
                    [issue.located() for issue in parsed.issues], [], "%s %s" % (name, exported.path)
                )
                self.assertTrue(parsed.get_scalar("title"), exported.path)

    def test_github_export_has_a_single_leading_h1(self):
        for exported in export.export_docs(self.root, self.config, "github").files:
            self.assertTrue(exported.text.startswith("# "), exported.path)

    def test_writing_the_repo_export_twice_produces_no_diff(self):
        out = os.path.join(self.root, "build-docusaurus")
        export.write_export(self.root, self.config, "docusaurus", out)
        again = export.write_export(self.root, self.config, "docusaurus", out)
        self.assertEqual(again.written, [])

    def test_originals_are_untouched(self):
        self.assertNotEqual(os.path.realpath(self.root), os.path.realpath(REPO_ROOT))
        source = os.path.join(REPO_ROOT, "docs", "architecture.md")
        with io.open(source, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"---"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

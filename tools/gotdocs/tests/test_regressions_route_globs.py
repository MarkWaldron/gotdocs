"""Regression tests for two defects found installing gotdocs into a Next.js repo.

Run just these:

    python3 -m unittest tools.gotdocs.tests.test_regressions_route_globs -v

Defect 1 -- a framework route directory written literally in ``covers``
(``src/app/posts/[id]/page.tsx``) compiled to a character class, so it matched nothing
and the document silently never became impacted.

Defect 2 -- nothing warned about it. ``lint`` reported a glob matching zero files as
clean, which is what made defect 1 invisible rather than merely wrong.
"""

import io
import os
import unittest

from tools.gotdocs import cli, globs
from tools.gotdocs.errors import GlobError
from tools.gotdocs.tests import support


ROUTE_PATH = "src/app/(app)/posts/[id]/page.tsx"
ESCAPED = r"src/app/(app)/posts/\[id\]/page.tsx"


class RouteGlobEscapingTests(unittest.TestCase):
    """Defect 1: literal brackets in a path must be expressible."""

    def setUp(self):
        globs.cache_clear()

    def test_unescaped_brackets_are_still_a_character_class(self):
        # Not a bug -- this is what "[id]" means. The bug was having no alternative.
        self.assertFalse(globs.match("src/posts/[id]/page.tsx", "src/posts/[id]/page.tsx"))
        self.assertTrue(globs.match("src/posts/[id]/page.tsx", "src/posts/i/page.tsx"))

    def test_escaped_brackets_match_the_literal_path(self):
        self.assertTrue(globs.match(ESCAPED, ROUTE_PATH))

    def test_escaped_brackets_do_not_match_the_class_expansion(self):
        self.assertFalse(globs.match(ESCAPED, "src/app/(app)/posts/i/page.tsx"))
        self.assertFalse(globs.match(ESCAPED, "src/app/(app)/posts/d/page.tsx"))

    def test_parentheses_are_literal_and_need_no_escaping(self):
        # Next.js route groups. Never broken; assert it stays that way.
        self.assertTrue(globs.match("src/app/(app)/**", "src/app/(app)/posts/page.tsx"))

    def test_escaping_a_star_makes_it_literal(self):
        self.assertTrue(globs.match(r"src/a\*b.ts", "src/a*b.ts"))
        self.assertFalse(globs.match(r"src/a\*b.ts", "src/axxb.ts"))

    def test_character_classes_still_work(self):
        self.assertTrue(globs.match("src/[abc].ts", "src/a.ts"))
        self.assertFalse(globs.match("src/[abc].ts", "src/z.ts"))

    def test_windows_separators_are_still_rejected(self):
        with self.assertRaises(GlobError):
            globs.compile_pattern("src\\lib\\thing.ts")

    def test_dangling_backslash_is_rejected(self):
        with self.assertRaises(GlobError):
            globs.compile_pattern("src/thing.ts\\")

    def test_wildcard_segment_is_the_other_valid_workaround(self):
        self.assertTrue(globs.match("src/app/(app)/posts/*/page.tsx", ROUTE_PATH))


class RottedCoverWarningTests(support.TempRepoTestCase):
    """Defect 2: a covers glob matching nothing must not be reported as clean."""

    def setUp(self):
        super().setUp()
        self.write(ROUTE_PATH, "export default function Page() {}\n")

    def run_cli(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        argv = list(args) + ["--repo", self.root, "--no-color"]
        code = cli.main(argv, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def _write_doc(self, covers):
        self.write(
            "docs/routes.md",
            support.doc_text(doc_id="routes", title="Routes", covers=[covers]),
        )
        self.add(".")

    def test_a_glob_matching_nothing_produces_a_warning(self):
        self._write_doc(ROUTE_PATH)
        code, out, _ = self.run_cli("lint")
        self.assertEqual(code, 0, "a rotted glob warns, it does not fail the run")
        self.assertIn("matches nothing", out)
        self.assertIn("routes.md", out)

    def test_the_warning_names_the_bracket_cause(self):
        self._write_doc(ROUTE_PATH)
        _, out, _ = self.run_cli("lint")
        self.assertIn("character class", out)
        self.assertIn("\\[", out)

    def test_an_escaped_glob_produces_no_warning(self):
        self._write_doc(ESCAPED)
        code, out, _ = self.run_cli("lint")
        self.assertEqual(code, 0)
        self.assertNotIn("matches nothing", out)

    def test_a_glob_that_matches_is_silent(self):
        self._write_doc("src/**")
        _, out, _ = self.run_cli("lint")
        self.assertNotIn("matches nothing", out)

    def test_the_escaped_glob_actually_routes(self):
        self._write_doc(ESCAPED)
        code, out, _ = self.run_cli("impacted", ROUTE_PATH)
        self.assertEqual(code, 0)
        self.assertIn("routes", out)

    def test_the_unescaped_glob_routes_to_nothing(self):
        # The original silent failure, asserted so it cannot come back unnoticed.
        self._write_doc(ROUTE_PATH)
        _, out, _ = self.run_cli("impacted", ROUTE_PATH)
        self.assertNotIn("routes.md", out)


class InstallSkillContractTests(unittest.TestCase):
    """The install skill must warn adopters about both traps.

    Not a code path -- a documentation contract. A nested git repo staged by
    ``git add -A`` becomes a gitlink, which is why it is called out by name.
    """

    def _skill_text(self):
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        path = os.path.join(root, ".claude", "skills", "gotdocs-install", "SKILL.md")
        if not os.path.exists(path):
            self.skipTest("install skill not present in this checkout")
        with open(path) as handle:
            return handle.read()

    def test_warns_about_nested_agent_worktrees(self):
        text = self._skill_text()
        self.assertIn(".claude/worktrees/", text)
        self.assertIn("embedded git repository", text)
        self.assertIn("git rm --cached", text)

    def test_documents_bracket_escaping(self):
        text = self._skill_text()
        self.assertIn(r"\[id\]", text)


if __name__ == "__main__":
    unittest.main()

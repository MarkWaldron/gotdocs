"""Exhaustive, table-driven tests for the gotdocs glob dialect.

The tables below are the executable form of the "Glob dialect" section of
docs/doc-format.md. Every worked example in that document appears here.
"""

import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import globs
from tools.gotdocs.errors import GlobError


# (pattern, [paths that must match], [paths that must not match])
MATCH_TABLE = [
    # --- the worked examples straight out of docs/doc-format.md -----------
    (
        "tools/gotdocs/**",
        ["tools/gotdocs/cli.py", "tools/gotdocs/tests/test_globs.py", "tools/gotdocs/a/b/c/d.py"],
        ["tools/gotdocs", "tools/other/x.py", "gotdocs/cli.py", "atools/gotdocs/cli.py"],
    ),
    (
        "tools/gotdocs/*.py",
        ["tools/gotdocs/cli.py", "tools/gotdocs/__init__.py"],
        ["tools/gotdocs/tests/test_cli.py", "tools/gotdocs/cli.pyc", "tools/gotdocs/py"],
    ),
    ("bin/gotdocs", ["bin/gotdocs"], ["bin/gotdocs.sh", "x/bin/gotdocs", "bin/gotdocs/x"]),
    (
        "*.py",
        ["cli.py", "a/b/c.py", "tools/gotdocs/globs.py"],
        ["cli.pyc", "py", "a/b/c.pyi", "x.py/y"],
    ),
    (
        "scripts/",
        ["scripts/install-gotdocs.sh", "scripts/ci/run.sh"],
        ["scripts", "myscripts/x.sh", "a/scripts/x.sh"],
    ),
    (
        "src/api/v?/**",
        ["src/api/v1/routes.go", "src/api/v2/a/b.go", "src/api/vx/x.go"],
        ["src/api/v10/routes.go", "src/api/v1", "src/api/v/x.go"],
    ),
    (
        ".github/workflows/**",
        [".github/workflows/gotdocs.yml", ".github/workflows/ci/inner.yml"],
        [".github/CODEOWNERS", ".github/workflows"],
    ),
    # --- basename rule ----------------------------------------------------
    (
        "Makefile",
        ["Makefile", "services/api/Makefile", "a/b/c/Makefile"],
        ["Makefile.in", "makefile", "MyMakefile"],
    ),
    (
        "package-lock.json",
        ["package-lock.json", "web/package-lock.json"],
        ["package.json", "package-lock.json.bak"],
    ),
    ("*.lock", ["Cargo.lock", "a/b/Cargo.lock"], ["lock", "Cargo.lock.bak"]),
    ("*.min.*", ["a.min.js", "web/static/app.min.css"], ["a.min", "min.js"]),
    ("*_pb2.py", ["x_pb2.py", "gen/proto/x_pb2.py"], ["x_pb2.pyi", "pb2.py"]),
    # --- `a/**` vs `a` ----------------------------------------------------
    ("a/**", ["a/b", "a/b/c", "a/b/c/d"], ["a", "ab/c", "b/a"]),
    ("a", ["a", "x/a", "x/y/a"], ["ab", "a/b"]),
    # --- leading, middle and lone `**` ------------------------------------
    ("**", ["a", "a/b", "a/b/c", ".hidden"], []),
    ("**/foo", ["foo", "a/foo", "a/b/foo"], ["foo/bar", "foobar", "a/foobar"]),
    ("a/**/b", ["a/b", "a/x/b", "a/x/y/b"], ["a/b/c", "b", "ab", "a/xb"]),
    (
        "**/node_modules/**",
        ["node_modules/x", "a/node_modules/b", "a/b/node_modules/c/d"],
        ["node_modules", "a/node_modules", "node_modulesx/y"],
    ),
    ("**/.git/**", [".git/config", "sub/.git/objects/aa"], [".git", "git/config"]),
    ("a/**/**/b", ["a/b", "a/x/b", "a/x/y/b"], ["a/b/c"]),
    ("src/**/*.py", ["src/a.py", "src/a/b.py"], ["src/a.pyc", "src.py", "src/a/b.txt"]),
    # --- single-segment wildcards do not cross `/` ------------------------
    ("src/*", ["src/a.py", "src/b"], ["src/a/b.py", "src", "asrc/a"]),
    ("a/?/c", ["a/b/c", "a/x/c"], ["a/bb/c", "a//c", "a/c"]),
    ("?", ["a", "x/y"], ["ab", "x/yz"]),
    # --- character classes ------------------------------------------------
    ("[abc].py", ["a.py", "b.py", "x/c.py"], ["d.py", "ab.py"]),
    ("[a-z].py", ["a.py", "m.py", "z.py"], ["A.py", "1.py", "aa.py"]),
    ("[!abc].py", ["d.py", "x/z.py"], ["a.py", "b.py", "c.py"]),
    ("src/[a-z]*/x.py", ["src/api/x.py", "src/a/x.py"], ["src/API/x.py", "src/1a/x.py"]),
    ("v[0-9][0-9]", ["v10", "a/v99"], ["v1", "v1a", "vv1"]),
    # a "/" inside a negated class must still never match the separator
    ("a/[!x]/c", ["a/y/c"], ["a/x/c", "a//c"]),
    # --- literals that look like syntax -----------------------------------
    ("{a,b}.py", ["{a,b}.py", "x/{a,b}.py"], ["a.py", "b.py"]),
    ("a+b.py", ["a+b.py"], ["ab.py", "aab.py"]),
    ("a.b.c", ["a.b.c", "x/a.b.c"], ["axbxc"]),
    ("(x).py", ["(x).py"], ["x.py"]),
    # --- dotfiles are ordinary characters ---------------------------------
    (".gotdocs/index.json", [".gotdocs/index.json"], ["gotdocs/index.json", ".gotdocs/INDEX.md"]),
    ("**/.venv/**", [".venv/lib/x.py", "a/.venv/x"], [".venv"]),
]


NORMALIZE_TABLE = [
    ("./a/b", "a/b"),
    ("a//b", "a/b"),
    ("/a/b", "a/b"),
    ("a/b/", "a/b"),
    ("./././a", "a"),
    ("a", "a"),
    ("", ""),
]


INVALID_PATTERNS = [
    "",
    "/absolute/path",
    "./relative",
    "!negated",
    "windows\\separator",
    "a//b",
    "[unterminated",
    "src/[a-z",
    " leading-space",
    "trailing-space ",
]


class GlobMatchTests(unittest.TestCase):
    def test_match_table(self):
        for pattern, matches, non_matches in MATCH_TABLE:
            for path in matches:
                with self.subTest(pattern=pattern, path=path, expect=True):
                    self.assertTrue(
                        globs.match(pattern, path),
                        "%r should match %r (regex %s)"
                        % (pattern, path, globs.compile_pattern(pattern).regex_source),
                    )
            for path in non_matches:
                with self.subTest(pattern=pattern, path=path, expect=False):
                    self.assertFalse(
                        globs.match(pattern, path),
                        "%r should NOT match %r (regex %s)"
                        % (pattern, path, globs.compile_pattern(pattern).regex_source),
                    )

    def test_trailing_slash_is_double_star(self):
        left = globs.compile_pattern("scripts/").regex_source
        right = globs.compile_pattern("scripts/**").regex_source
        self.assertEqual(left, right)

    def test_basename_detection(self):
        self.assertTrue(globs.compile_pattern("*.py").basename_only)
        self.assertTrue(globs.compile_pattern("Makefile").basename_only)
        self.assertFalse(globs.compile_pattern("a/*.py").basename_only)
        # "**" is a path construct even with no "/" in the pattern.
        self.assertFalse(globs.compile_pattern("**").basename_only)

    def test_empty_path_never_matches(self):
        for pattern in ("**", "*", "a"):
            self.assertFalse(globs.match(pattern, ""))

    def test_paths_are_normalized_before_matching(self):
        self.assertTrue(globs.match("src/**", "./src/a.py"))
        self.assertTrue(globs.match("src/**", "src//a.py"))

    def test_match_any_and_matching_patterns(self):
        patterns = ["src/**", "*.md", "bin/gotdocs"]
        self.assertTrue(globs.match_any(patterns, "src/a.py"))
        self.assertFalse(globs.match_any(patterns, "docs/x.txt"))
        self.assertEqual(globs.matching_patterns(patterns, "src/a.md"), ["src/**", "*.md"])
        self.assertEqual(globs.matching_patterns(patterns, "nope.txt"), [])


class DoubleStarInsideASegmentTests(unittest.TestCase):
    """Regression: ``**`` crosses ``/`` even when it shares a segment.

    ``_translate_segment`` used to collapse any run of ``*`` to ``[^/]*``, so
    ``src/**.py`` matched nothing below ``src/`` and ``**.py`` matched strictly
    fewer files than ``*.py``.
    """

    def setUp(self):
        globs.cache_clear()

    def test_double_star_with_a_suffix_crosses_directories(self):
        self.assertTrue(globs.match("src/**.py", "src/a/b.py"))
        self.assertTrue(globs.match("src/**.py", "src/a/b/c/d.py"))
        self.assertTrue(globs.match("src/**.py", "src/b.py"))
        self.assertFalse(globs.match("src/**.py", "other/b.py"))
        self.assertFalse(globs.match("src/**.py", "src/a/b.ts"))

    def test_double_star_with_a_prefix_crosses_directories(self):
        self.assertTrue(globs.match("src/test_**", "src/test_a/b.py"))
        self.assertTrue(globs.match("**.py", "a/b/c.py"))

    def test_single_star_still_does_not_cross(self):
        self.assertFalse(globs.match("src/*.py", "src/a/b.py"))
        self.assertTrue(globs.match("src/*.py", "src/a.py"))

    def test_whole_segment_double_star_is_unchanged(self):
        self.assertTrue(globs.match("a/**", "a/b"))
        self.assertTrue(globs.match("a/**", "a/b/c"))
        self.assertFalse(globs.match("a/**", "a"))
        self.assertTrue(globs.match("a/**/b", "a/b"))
        self.assertTrue(globs.match("a/**/b", "a/x/y/b"))


class GlobNormalizeTests(unittest.TestCase):
    def test_normalize_table(self):
        for raw, expected in NORMALIZE_TABLE:
            with self.subTest(raw=raw):
                self.assertEqual(globs.normalize_path(raw), expected)

    def test_backslash_is_not_a_separator(self):
        self.assertEqual(globs.normalize_path("a\\b"), "a\\b")


class GlobValidationTests(unittest.TestCase):
    def test_invalid_patterns_raise(self):
        for pattern in INVALID_PATTERNS:
            with self.subTest(pattern=pattern):
                with self.assertRaises(GlobError):
                    globs.compile_pattern(pattern)

    def test_non_string_pattern_raises(self):
        with self.assertRaises(GlobError):
            globs.compile_pattern(None)

    def test_error_carries_the_pattern(self):
        try:
            globs.compile_pattern("/abs")
        except GlobError as exc:
            self.assertEqual(exc.pattern, "/abs")
        else:  # pragma: no cover
            self.fail("expected GlobError")


class GlobCacheTests(unittest.TestCase):
    def setUp(self):
        globs.cache_clear()

    def test_repeated_compiles_hit_the_cache(self):
        first = globs.compile_pattern("src/**")
        second = globs.compile_pattern("src/**")
        self.assertIs(first, second)
        size, hits, misses = globs.cache_info()
        self.assertEqual(size, 1)
        self.assertEqual(misses, 1)
        self.assertEqual(hits, 1)

    def test_cache_clear_resets(self):
        globs.compile_pattern("src/**")
        globs.cache_clear()
        self.assertEqual(globs.cache_info(), (0, 0, 0))

    def test_invalid_patterns_are_not_cached(self):
        with self.assertRaises(GlobError):
            globs.compile_pattern("[bad")
        self.assertEqual(globs.cache_info()[0], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

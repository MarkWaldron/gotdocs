"""Config loading: defaults when absent, validation when present."""

import json
import os
import unittest

try:  # works both as a package (`-m unittest tools.gotdocs.tests...`)
    from . import support  # noqa: F401
except ImportError:  # ...and as a top-level module (`discover -s tools/gotdocs/tests`)
    import support  # noqa: F401
from tools.gotdocs import config as config_module
from tools.gotdocs.errors import ConfigError


class DefaultsTests(support.TempRepoTestCase):
    def test_missing_config_falls_back_to_defaults(self):
        os.remove(os.path.join(self.root, config_module.CONFIG_PATH))
        config = self.config()
        self.assertFalse(config.exists)
        self.assertEqual(config.roots, config_module.DEFAULT_ROOTS)
        self.assertEqual(config.skip_token, "[gotdocs skip]")
        self.assertEqual(config.max_summary_chars, 200)
        self.assertFalse(config.require_coverage)
        self.assertEqual(config.mode_for("pre_commit"), "warn")
        # ci defaults to warn: adopting gotdocs must not turn every open pull
        # request red on the first push. Raising it to "error" is a deliberate act.
        self.assertEqual(config.mode_for("ci"), "warn")
        self.assertEqual(config.mode_for("pre_push"), "warn")
        self.assertIn("decisions", config.roots)

    def test_defaults_ignore_the_generated_index(self):
        os.remove(os.path.join(self.root, config_module.CONFIG_PATH))
        config = self.config()
        self.assertTrue(config.is_ignored(".gotdocs/index.json"))
        self.assertTrue(config.is_ignored(".gotdocs/INDEX.md"))

    def test_explicit_path_that_does_not_exist_is_an_error(self):
        with self.assertRaises(ConfigError):
            config_module.load(self.root, os.path.join(self.root, "nope.json"))


class ValidationTests(support.TempRepoTestCase):
    def write_raw(self, text):
        support.write(self.root, config_module.CONFIG_PATH, text)

    def test_valid_config(self):
        config = self.config()
        self.assertTrue(config.exists)
        self.assertEqual(config.roots, ["docs"])
        self.assertEqual(config.path, config_module.CONFIG_PATH)

    def test_invalid_json(self):
        self.write_raw("{ not json")
        with self.assertRaises(ConfigError) as caught:
            self.config()
        self.assertIn("not valid JSON", str(caught.exception))

    def test_non_object_json(self):
        self.write_raw("[1, 2, 3]")
        with self.assertRaises(ConfigError):
            self.config()

    def test_roots_must_be_strings(self):
        self.write_raw(json.dumps({"roots": ["docs", 3]}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_require_coverage_must_be_boolean(self):
        self.write_raw(json.dumps({"require_coverage": "yes"}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_unknown_enforce_context(self):
        self.write_raw(json.dumps({"enforce": {"nightly": "error"}}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_unknown_mode(self):
        self.write_raw(json.dumps({"enforce": {"ci": "explode"}}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_partial_enforce_keeps_the_other_default(self):
        self.write_raw(json.dumps({"enforce": {"ci": "warn"}}))
        config = self.config()
        self.assertEqual(config.mode_for("ci"), "warn")
        self.assertEqual(config.mode_for("pre_commit"), "warn")
        self.assertEqual(config.mode_for("pre_push"), "warn")

    def test_pre_push_is_a_supported_enforce_context(self):
        # .gotdocs/hooks/pre-push reads enforce.pre_push and docs/enforcement.md
        # documents it, so the loader must accept it rather than hard-failing
        # every command with a ConfigError.
        self.write_raw(json.dumps({"enforce": {"pre_push": "error"}}))
        config = self.config()
        self.assertEqual(config.mode_for("pre_push"), "error")
        self.assertEqual(config.mode_for("pre_commit"), "warn")
        self.assertEqual(config.mode_for("ci"), "warn")

    def test_pre_push_rejects_an_unknown_mode(self):
        self.write_raw(json.dumps({"enforce": {"pre_push": "explode"}}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_bad_max_summary_chars(self):
        self.write_raw(json.dumps({"max_summary_chars": 0}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_empty_skip_token(self):
        self.write_raw(json.dumps({"skip_token": ""}))
        with self.assertRaises(ConfigError):
            self.config()

    def test_unknown_keys_are_recorded_not_rejected(self):
        self.write_raw(json.dumps({"roots": ["docs"], "future_option": True}))
        config = self.config()
        self.assertEqual(config.unknown_keys, ["future_option"])


class NestedBlockTests(support.TempRepoTestCase):
    """The `debt` and `publish` blocks, which older configs simply do not have."""

    def load(self, payload):
        support.write(
            self.root, config_module.CONFIG_PATH, json.dumps(payload, indent=2) + "\n"
        )
        return self.config()

    def test_an_old_config_still_loads_and_gets_the_defaults(self):
        # Exactly the v1 config shape: no `debt`, no `publish`, no decisions root.
        config = self.load(
            {
                "version": 1,
                "roots": ["docs"],
                "enforce": {"pre_commit": "warn", "ci": "error"},
                "require_coverage": False,
                "skip_token": "[gotdocs skip]",
                "max_summary_chars": 200,
            }
        )
        self.assertEqual(config.unknown_keys, [])
        self.assertEqual(config.debt, config_module.DEFAULT_DEBT)
        self.assertEqual(config.publish, config_module.DEFAULT_PUBLISH)
        self.assertEqual(config.mode_for("ci"), "error")

    def test_a_partial_block_is_overlaid_on_the_defaults(self):
        config = self.load({"publish": {"target": "hugo"}})
        self.assertEqual(config.publish_option("target"), "hugo")
        self.assertTrue(config.publish_option("h1_in_body"))
        self.assertEqual(config.publish_option("out_dir"), "build/gotdocs-site")

    def test_an_unknown_sub_key_is_reported_not_fatal(self):
        config = self.load({"publish": {"sidebar_style": "flat"}})
        self.assertEqual(config.unknown_keys, ["publish.sidebar_style"])
        self.assertEqual(config.publish_option("sidebar_style"), "flat")

    def test_a_wrong_type_on_a_known_sub_key_is_fatal(self):
        # Silently ignoring it would change enforcement without telling anyone.
        support.write(
            self.root, config_module.CONFIG_PATH, json.dumps({"debt": {"enabled": "no"}})
        )
        with self.assertRaises(ConfigError):
            self.config()


class ClassificationTests(support.TempRepoTestCase):
    def test_is_doc_path(self):
        config = self.config()
        self.assertTrue(config.is_doc_path("docs/a.md"))
        self.assertTrue(config.is_doc_path("docs/nested/a.md"))
        self.assertTrue(config.is_doc_path("docs"))
        self.assertFalse(config.is_doc_path("documents/a.md"))
        self.assertFalse(config.is_doc_path("src/a.py"))

    def test_is_ignored(self):
        config = self.config()
        self.assertTrue(config.is_ignored("Cargo.lock"))
        self.assertTrue(config.is_ignored("a/b/Cargo.lock"))
        self.assertTrue(config.is_ignored("web/node_modules/x/y.js"))
        self.assertTrue(config.is_ignored(".gotdocs/index.json"))
        self.assertFalse(config.is_ignored("src/a.py"))

    def test_bad_ignore_pattern_is_reported_not_raised(self):
        self.write_config(ignore=["/absolute", "*.lock"])
        config = self.config()
        self.assertFalse(config.is_ignored("src/a.py"))
        self.assertTrue(config.is_ignored("a.lock"))
        self.assertEqual([pattern for pattern, _ in config.bad_ignore_patterns()], ["/absolute"])

    def test_as_dict_round_trips(self):
        config = self.config()
        self.assertEqual(sorted(config.as_dict()), sorted(config_module.DEFAULTS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

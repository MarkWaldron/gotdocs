"""Loads ``.gotdocs/config.json`` and applies the documented defaults.

A missing config file is not an error: gotdocs falls back to the defaults so
``gotdocs install`` can bootstrap a repo that has no config yet. A config file
that exists but is malformed *is* an error, because silently ignoring it would
change enforcement behaviour without telling anyone.
"""

import json
import os

from .errors import ConfigError
from . import globs

__all__ = [
    "Config",
    "CONFIG_DIR",
    "CONFIG_PATH",
    "INDEX_JSON_PATH",
    "INDEX_MD_PATH",
    "DEFAULT_ROOTS",
    "DEFAULT_IGNORE",
    "DEFAULT_DEBT",
    "DEFAULT_PUBLISH",
    "DEFAULTS",
    "MODES",
    "ENFORCE_CONTEXTS",
    "DECISIONS_ROOT",
    "load",
]

CONFIG_DIR = ".gotdocs"
CONFIG_PATH = ".gotdocs/config.json"
INDEX_JSON_PATH = ".gotdocs/index.json"
INDEX_MD_PATH = ".gotdocs/INDEX.md"
TEMPLATE_DIR = ".gotdocs/templates"
HOOK_SOURCE_PATH = ".gotdocs/hooks/pre-commit"
PRE_PUSH_SOURCE_PATH = ".gotdocs/hooks/pre-push"

MODES = ("off", "warn", "error")

# Where enforcement can run. `pre_push` is read by .gotdocs/hooks/pre-push and
# documented in docs/enforcement.md, so the loader must accept it.
ENFORCE_CONTEXTS = ("pre_commit", "pre_push", "ci")

# The root decision records live under. It is a normal doc root (it is walked,
# linted and indexed like any other) but several commands need to name it.
DECISIONS_ROOT = "decisions"

DEFAULT_ROOTS = ["docs", "runbooks", "onboarding", "dependencies", "decisions"]

# Kept identical to the shipped .gotdocs/config.json so that a repo without a
# config behaves the same as a repo with the stock one.
DEFAULT_IGNORE = [
    "**/.git/**",
    "**/node_modules/**",
    "**/bower_components/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/target/**",
    "**/bin/Debug/**",
    "**/bin/Release/**",
    "**/obj/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.svelte-kit/**",
    "**/.turbo/**",
    "**/.gradle/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.tox/**",
    "**/.terraform/**",
    "**/coverage/**",
    "**/htmlcov/**",
    "**/testdata/**",
    "**/fixtures/**",
    "**/__snapshots__/**",
    "**/generated/**",
    "**/gen/**",
    "*.lock",
    "*.lockb",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "go.sum",
    "*.min.*",
    "*.map",
    "*.snap",
    "*.pb.go",
    "*.pb.cc",
    "*.pb.h",
    "*.pb.swift",
    "*_pb2.py",
    "*_pb2.pyi",
    "*_pb2_grpc.py",
    "*_pb.js",
    "*_pb.d.ts",
    "*.pb.ts",
    "*_grpc.pb.go",
    "*.generated.*",
    "*.g.dart",
    "*.designer.cs",
    "*.pyc",
    "*.class",
    "*.o",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.jar",
    "*.wasm",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.pdf",
    "*.woff",
    "*.woff2",
    "*.ttf",
    ".gotdocs/index.json",
    ".gotdocs/INDEX.md",
    # Generated the same way the index is: a debt ledger written by the hook
    # or by CI must not itself make a document stale.
    ".gotdocs/debt.jsonl",
    ".gotdocs/DEBT.md",
]

# The doc-debt ledger (tools/gotdocs/debt.py). `enabled: false` turns every
# ledger write into a no-op without removing the recorded history.
DEFAULT_DEBT = {
    "enabled": True,
    "ledger": ".gotdocs/debt.jsonl",
    "report": ".gotdocs/DEBT.md",
    "record_kinds": [
        "stale",
        "uncovered",
        "lint",
        "duplicate_id",
        "deprecated_edit",
        "index_out_of_date",
    ],
    "max_report_lines": 20,
}

# Static-site publishing (tools/gotdocs/export.py, tools/gotdocs/portability.py).
DEFAULT_PUBLISH = {
    "target": "docusaurus",
    "out_dir": "build/gotdocs-site",
    "url_prefix": "",
    "source_url": "",
    "layout": "",
    "include_drafts": False,
    "h1_in_body": True,
}

DEFAULTS = {
    "version": 1,
    "roots": DEFAULT_ROOTS,
    # ci defaults to `warn`, not `error`: a repository that has just adopted
    # gotdocs must not have every pull request go red on day one. The CI job
    # blocks only when this is explicitly raised to "error"; until then it
    # reports, records debt and stays green.
    "enforce": {"pre_commit": "warn", "pre_push": "warn", "ci": "warn"},
    "ignore": DEFAULT_IGNORE,
    "require_coverage": False,
    "skip_token": "[gotdocs skip]",
    "max_summary_chars": 200,
    "debt": DEFAULT_DEBT,
    "publish": DEFAULT_PUBLISH,
}

_KNOWN_KEYS = frozenset(DEFAULTS)

# Sub-keys of the two nested blocks that the loader type-checks. Anything else
# inside them is passed through untouched and reported in `unknown_keys`, so a
# newer gotdocs can add a key without breaking an older CLI.
_DEBT_TYPES = {
    "enabled": bool,
    "ledger": str,
    "report": str,
    "record_kinds": list,
    "max_report_lines": int,
}
_PUBLISH_TYPES = {
    "target": str,
    "out_dir": str,
    "url_prefix": str,
    "source_url": str,
    "layout": str,
    "include_drafts": bool,
    "h1_in_body": bool,
}


class Config(object):
    """Effective gotdocs configuration for one repository."""

    __slots__ = (
        "version",
        "roots",
        "enforce",
        "ignore",
        "require_coverage",
        "skip_token",
        "max_summary_chars",
        "debt",
        "publish",
        "path",
        "exists",
        "unknown_keys",
    )

    def __init__(self, **kwargs):
        self.version = kwargs.get("version", DEFAULTS["version"])
        self.roots = list(kwargs.get("roots", DEFAULT_ROOTS))
        self.enforce = dict(kwargs.get("enforce", DEFAULTS["enforce"]))
        self.ignore = list(kwargs.get("ignore", DEFAULT_IGNORE))
        self.require_coverage = bool(
            kwargs.get("require_coverage", DEFAULTS["require_coverage"])
        )
        self.skip_token = kwargs.get("skip_token", DEFAULTS["skip_token"])
        self.max_summary_chars = int(
            kwargs.get("max_summary_chars", DEFAULTS["max_summary_chars"])
        )
        self.debt = _merged(DEFAULT_DEBT, kwargs.get("debt"))
        self.publish = _merged(DEFAULT_PUBLISH, kwargs.get("publish"))
        self.path = kwargs.get("path")
        self.exists = bool(kwargs.get("exists", False))
        self.unknown_keys = list(kwargs.get("unknown_keys", []))

    # -- derived helpers ---------------------------------------------------

    def mode_for(self, context):
        """Return the configured mode for ``pre_commit``, ``pre_push`` or ``ci``."""
        value = self.enforce.get(context)
        if value in MODES:
            return value
        return DEFAULTS["enforce"].get(context, "warn")

    # -- nested blocks -----------------------------------------------------

    def debt_option(self, key):
        """One ``debt`` setting, falling back to the documented default."""
        return self.debt.get(key, DEFAULT_DEBT.get(key))

    def publish_option(self, key):
        """One ``publish`` setting, falling back to the documented default."""
        return self.publish.get(key, DEFAULT_PUBLISH.get(key))

    @property
    def debt_enabled(self):
        return bool(self.debt_option("enabled"))

    @property
    def debt_ledger(self):
        return self.debt_option("ledger") or DEFAULT_DEBT["ledger"]

    @property
    def debt_report(self):
        return self.debt_option("report") or DEFAULT_DEBT["report"]

    @property
    def debt_record_kinds(self):
        """Finding kinds the ledger accepts, or None for "every kind"."""
        kinds = self.debt_option("record_kinds")
        if kinds is None:
            return None
        return [str(kind) for kind in kinds]

    @property
    def decisions_root(self):
        """The configured root holding decision records.

        Matched by name against ``roots`` so a repo that renamed the directory
        (``adr/``, ``docs/decisions/``) still gets ``why`` and ADR linting. Falls
        back to the default name when no root looks like one, which keeps
        ``gotdocs new decision`` working in a repo that has not created it yet.
        """
        for root in self.roots:
            normalized = globs.normalize_path(root)
            if normalized == DECISIONS_ROOT or normalized.endswith("/" + DECISIONS_ROOT):
                return normalized
        return DECISIONS_ROOT

    def is_doc_path(self, path):
        """True when *path* lives inside one of the configured roots."""
        candidate = globs.normalize_path(path)
        if candidate == "":
            return False
        for root in self.roots:
            root = globs.normalize_path(root)
            if root == "":
                continue
            if candidate == root or candidate.startswith(root + "/"):
                return True
        return False

    def is_ignored(self, path):
        """True when *path* matches an ``ignore`` glob."""
        candidate = globs.normalize_path(path)
        if candidate == "":
            return False
        for pattern in self.ignore:
            try:
                if globs.compile_pattern(pattern).match(candidate):
                    return True
            except Exception:
                # A bad ignore pattern must not stop a commit; it is reported
                # separately by `lint` / `status`.
                continue
        return False

    def bad_ignore_patterns(self):
        """Return ``[(pattern, message)]`` for ignore globs that do not compile."""
        bad = []
        for pattern in self.ignore:
            try:
                globs.compile_pattern(pattern)
            except Exception as exc:
                bad.append((pattern, str(exc)))
        return bad

    def as_dict(self):
        return {
            "version": self.version,
            "roots": list(self.roots),
            "enforce": dict(self.enforce),
            "ignore": list(self.ignore),
            "require_coverage": self.require_coverage,
            "skip_token": self.skip_token,
            "max_summary_chars": self.max_summary_chars,
            "debt": dict(self.debt),
            "publish": dict(self.publish),
        }


def _merged(defaults, value):
    """Overlay a config block on its defaults; anything unusable is ignored.

    A missing block, ``null``, or a value of the wrong type all yield the plain
    defaults. This is the "old config must not crash" rule: every consumer can
    read every key without guarding.
    """
    merged = dict(defaults)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def load(repo_root, path=None):
    """Load the config for *repo_root*.

    *path* overrides the default location. A missing file yields the documented
    defaults with ``exists = False``.
    """
    config_path = path or os.path.join(repo_root, CONFIG_PATH)
    if not os.path.isfile(config_path):
        if path is not None:
            raise ConfigError("config file not found: %s" % (path,))
        return Config(path=os.path.relpath(config_path, repo_root), exists=False)

    try:
        with open(config_path, "rb") as handle:
            raw = handle.read()
    except (IOError, OSError) as exc:
        raise ConfigError("cannot read %s: %s" % (config_path, exc.strerror or exc))

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ConfigError("%s is not valid UTF-8" % (config_path,))
    except ValueError as exc:
        raise ConfigError("%s is not valid JSON: %s" % (config_path, exc))

    if not isinstance(parsed, dict):
        raise ConfigError("%s must contain a JSON object" % (config_path,))

    values = {}
    _take_int(parsed, "version", values, config_path)
    _take_str_list(parsed, "roots", values, config_path)
    _take_str_list(parsed, "ignore", values, config_path)
    _take_int(parsed, "max_summary_chars", values, config_path)

    if "require_coverage" in parsed:
        value = parsed["require_coverage"]
        if not isinstance(value, bool):
            raise ConfigError(
                "%s: 'require_coverage' must be true or false" % (config_path,)
            )
        values["require_coverage"] = value

    if "skip_token" in parsed:
        value = parsed["skip_token"]
        if not isinstance(value, str) or value == "":
            raise ConfigError(
                "%s: 'skip_token' must be a non-empty string" % (config_path,)
            )
        values["skip_token"] = value

    if "enforce" in parsed:
        value = parsed["enforce"]
        if not isinstance(value, dict):
            raise ConfigError("%s: 'enforce' must be an object" % (config_path,))
        enforce = dict(DEFAULTS["enforce"])
        for context, mode in value.items():
            if context not in ENFORCE_CONTEXTS:
                raise ConfigError(
                    "%s: unknown enforce context %r (expected one of %s)"
                    % (config_path, context, ", ".join(ENFORCE_CONTEXTS))
                )
            if mode not in MODES:
                raise ConfigError(
                    "%s: enforce.%s must be one of %s, got %r"
                    % (config_path, context, ", ".join(MODES), mode)
                )
            enforce[context] = mode
        values["enforce"] = enforce

    if values.get("max_summary_chars", DEFAULTS["max_summary_chars"]) < 1:
        raise ConfigError("%s: 'max_summary_chars' must be >= 1" % (config_path,))

    nested_unknown = []
    _take_block(parsed, "debt", _DEBT_TYPES, values, config_path, nested_unknown)
    _take_block(parsed, "publish", _PUBLISH_TYPES, values, config_path, nested_unknown)

    if "debt" in values:
        kinds = values["debt"].get("record_kinds")
        if isinstance(kinds, list) and any(not isinstance(k, str) for k in kinds):
            raise ConfigError(
                "%s: 'debt.record_kinds' must be a list of strings" % (config_path,)
            )
        limit = values["debt"].get("max_report_lines")
        if isinstance(limit, int) and not isinstance(limit, bool) and limit < 1:
            raise ConfigError("%s: 'debt.max_report_lines' must be >= 1" % (config_path,))

    unknown = sorted(set(parsed) - _KNOWN_KEYS) + nested_unknown
    values["unknown_keys"] = unknown
    values["path"] = os.path.relpath(config_path, repo_root)
    values["exists"] = True
    return Config(**values)


def _take_int(parsed, key, values, config_path):
    if key not in parsed:
        return
    value = parsed[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s: %r must be an integer" % (config_path, key))
    values[key] = value


def _take_block(parsed, key, types, values, config_path, unknown):
    """Validate one nested object block (``debt`` / ``publish``).

    Known sub-keys are type-checked and a wrong type is a hard error, because a
    string where a bool belongs silently changes enforcement. Unknown sub-keys
    are kept verbatim and only *reported*, so a config written by a newer
    gotdocs still loads here.
    """
    if key not in parsed:
        return
    block = parsed[key]
    if block is None:
        return
    if not isinstance(block, dict):
        raise ConfigError("%s: %r must be an object" % (config_path, key))
    for name, value in sorted(block.items()):
        expected = types.get(name)
        if expected is None:
            unknown.append("%s.%s" % (key, name))
            continue
        if expected is bool:
            if not isinstance(value, bool):
                raise ConfigError(
                    "%s: '%s.%s' must be true or false" % (config_path, key, name)
                )
        elif expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(
                    "%s: '%s.%s' must be an integer" % (config_path, key, name)
                )
        elif expected is str:
            if not isinstance(value, str):
                raise ConfigError(
                    "%s: '%s.%s' must be a string" % (config_path, key, name)
                )
        elif expected is list:
            if not isinstance(value, list):
                raise ConfigError(
                    "%s: '%s.%s' must be a list" % (config_path, key, name)
                )
    values[key] = block


def _take_str_list(parsed, key, values, config_path):
    if key not in parsed:
        return
    value = parsed[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError("%s: %r must be a list of strings" % (config_path, key))
    values[key] = value

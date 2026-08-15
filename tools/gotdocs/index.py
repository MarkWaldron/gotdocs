"""Walks the configured roots and writes the two generated index files.

``.gotdocs/index.json`` is the machine index; ``.gotdocs/INDEX.md`` is the
token-cheap file an agent reads first. Both are committed, so both must be
reproducible: running ``gotdocs index`` twice with no source change must rewrite
identical bytes.

Reproducibility rules (docs/architecture.md#reproducibility-of-the-index):

* docs are sorted by ``id`` (then by path, so docs missing an ``id`` are still
  deterministically ordered)
* JSON uses 2-space indent, explicit key order, and a trailing newline
* the only volatile field is ``generated_at_sha``

``generated_at_sha`` is excluded when :func:`index_is_current` compares the
committed index against a freshly computed one, so a new commit does not make
every checkout report ``index_out_of_date``.
"""

import io
import json
import os
import re

from . import frontmatter as fm_module
from . import globs
from .config import INDEX_JSON_PATH, INDEX_MD_PATH
from .decisions import DECISION_STATUSES, DECISION_TYPE

__all__ = [
    "Doc",
    "DocSet",
    "DOC_TYPES",
    "DOC_STATUSES",
    "DECISION_FIELDS",
    "REQUIRED_FIELDS",
    "scan",
    "build_payload",
    "render_json",
    "render_markdown",
    "write_index",
    "index_is_current",
]

INDEX_VERSION = 1
MARKDOWN_SUFFIXES = (".md", ".markdown")

DOC_TYPES = ("doc", "runbook", "onboarding", "dependency", "decision")
DOC_STATUSES = ("current", "draft", "deprecated")
REQUIRED_FIELDS = ("id", "title", "type", "summary", "covers", "status", "updated")

LIST_FIELDS = ("covers", "owners", "tags")

# Frontmatter only decision records carry. They are indexed as first-class
# fields (rather than falling into `extra`) because `gotdocs why` and the ADR
# lint rules read them straight out of index.json.
DECISION_LIST_FIELDS = ("symptoms", "supersedes", "superseded_by")
DECISION_FIELDS = DECISION_LIST_FIELDS + ("decided_on",)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$")

_ID_MAX = 64
_TITLE_MAX = 120


class Doc(object):
    """One document under a configured root."""

    __slots__ = (
        "path",
        "root",
        "id",
        "title",
        "type",
        "summary",
        "status",
        "updated",
        "verified_at",
        "owners",
        "tags",
        "covers",
        "symptoms",
        "supersedes",
        "superseded_by",
        "decided_on",
        "extra",
        "issues",
        "frontmatter",
    )

    def __init__(self, path, root):
        self.path = path
        self.root = root
        self.id = None
        self.title = None
        self.type = None
        self.summary = None
        self.status = None
        self.updated = None
        self.verified_at = None
        self.owners = []
        self.tags = []
        self.covers = []
        self.symptoms = []
        self.supersedes = []
        self.superseded_by = []
        self.decided_on = None
        self.extra = {}
        self.issues = []
        self.frontmatter = None

    @property
    def display_id(self):
        return self.id or self.path

    @property
    def is_decision(self):
        return self.type == DECISION_TYPE

    @property
    def sort_key(self):
        return (self.id is None, self.id or "", self.path)

    def covers_matches(self, code_path):
        """Return the ``covers`` patterns of this doc that match *code_path*."""
        matched = []
        for pattern in self.covers:
            try:
                if globs.compile_pattern(pattern).match(code_path):
                    matched.append(pattern)
            except Exception:
                # Invalid patterns are reported by lint; they simply never match.
                continue
        return matched

    def as_entry(self):
        """The ordered dict that lands in ``index.json``."""
        entry = [
            ("id", self.id),
            ("path", self.path),
            ("type", self.type),
            ("title", self.title),
            ("summary", self.summary),
            ("status", self.status),
            ("covers", list(self.covers)),
            ("owners", list(self.owners)),
            ("tags", list(self.tags)),
            ("updated", self.updated),
            ("verified_at", self.verified_at),
        ]
        if self.is_decision:
            # Only decisions carry these, so an ordinary doc's entry keeps the
            # exact key set it has always had.
            entry.extend(
                [
                    ("symptoms", list(self.symptoms)),
                    ("decided_on", self.decided_on),
                    ("supersedes", list(self.supersedes)),
                    ("superseded_by", list(self.superseded_by)),
                ]
            )
        result = dict(entry)
        result["_order"] = [name for name, _ in entry]
        if self.extra:
            result["extra"] = self.extra
            result["_order"].append("extra")
        return result


class DocSet(object):
    """The result of scanning every configured root."""

    __slots__ = ("docs", "issues", "duplicate_ids", "roots")

    def __init__(self, docs, issues, duplicate_ids, roots):
        self.docs = docs
        self.issues = issues
        self.duplicate_ids = duplicate_ids
        self.roots = roots

    def by_id(self):
        mapping = {}
        for doc in self.docs:
            if doc.id and doc.id not in mapping:
                mapping[doc.id] = doc
        return mapping

    def by_path(self):
        return dict((doc.path, doc) for doc in self.docs)

    def counts_by_status(self):
        counts = dict((status, 0) for status in DOC_STATUSES)
        counts["unknown"] = 0
        for doc in self.docs:
            if doc.is_decision:
                # Decision statuses live in a different enum; counting them as
                # "unknown" would make `status` report phantom problems.
                continue
            if doc.status in counts:
                counts[doc.status] += 1
            else:
                counts["unknown"] += 1
        return counts

    def decisions(self):
        """Every decision record in the set, in index order."""
        return [doc for doc in self.docs if doc.is_decision]


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------


def scan(repo_root, config):
    """Read every doc under ``config.roots`` and validate its frontmatter.

    Returns a :class:`DocSet`. Parsing never raises for content problems; each
    one becomes a :class:`~tools.gotdocs.frontmatter.LintIssue`.
    """
    docs = []
    issues = []
    for root in config.roots:
        normalized_root = globs.normalize_path(root)
        if not normalized_root:
            continue
        absolute_root = os.path.join(repo_root, normalized_root)
        if not os.path.isdir(absolute_root):
            continue
        for rel_path in _walk_markdown(absolute_root, normalized_root):
            doc = _load_doc(repo_root, rel_path, normalized_root, config)
            docs.append(doc)
            issues.extend(doc.issues)

    docs.sort(key=lambda item: item.sort_key)

    seen = {}
    duplicates = []
    for doc in docs:
        if not doc.id:
            continue
        if doc.id in seen:
            duplicates.append((doc.id, seen[doc.id].path, doc.path))
        else:
            seen[doc.id] = doc

    return DocSet(docs, issues, duplicates, list(config.roots))


def _walk_markdown(absolute_root, relative_root):
    found = []
    for dirpath, dirnames, filenames in os.walk(absolute_root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            if not filename.lower().endswith(MARKDOWN_SUFFIXES):
                continue
            absolute = os.path.join(dirpath, filename)
            relative = os.path.relpath(absolute, absolute_root)
            found.append(globs.normalize_path(relative_root + "/" + relative.replace(os.sep, "/")))
    return sorted(found)


def _load_doc(repo_root, rel_path, root, config):
    doc = Doc(rel_path, root)
    absolute = os.path.join(repo_root, rel_path.replace("/", os.sep))
    try:
        parsed = fm_module.parse_file(absolute, rel_path)
    except Exception as exc:  # unreadable file, bad encoding
        doc.issues.append(fm_module.LintIssue(rel_path, None, str(exc)))
        return doc

    doc.frontmatter = parsed
    doc.issues.extend(parsed.issues)
    if not parsed.present:
        return doc

    _validate(doc, parsed, config)
    return doc


def _validate(doc, parsed, config):
    issues = doc.issues
    add = issues.append

    for field in REQUIRED_FIELDS:
        if field not in parsed.data:
            add(fm_module.LintIssue(doc.path, parsed.end_line, "missing required frontmatter field %r" % (field,)))

    # id
    doc_id = parsed.get_scalar("id")
    if doc_id is not None:
        if doc_id == "":
            add(fm_module.LintIssue(doc.path, parsed.line_of("id"), "'id' must not be empty"))
        elif not _ID_RE.match(doc_id):
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("id"),
                    "'id' must be kebab-case matching [a-z0-9][a-z0-9-]*, got %r" % (doc_id,),
                )
            )
        elif len(doc_id) > _ID_MAX:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("id"),
                    "'id' is %d characters; the limit is %d" % (len(doc_id), _ID_MAX),
                )
            )
        else:
            doc.id = doc_id
    elif "id" in parsed.data:
        add(fm_module.LintIssue(doc.path, parsed.line_of("id"), "'id' must be a scalar, not a list"))

    # title
    title = parsed.get_scalar("title")
    if title is not None:
        if title == "":
            add(fm_module.LintIssue(doc.path, parsed.line_of("title"), "'title' must not be empty"))
        elif len(title) > _TITLE_MAX:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("title"),
                    "'title' is %d characters; the limit is %d" % (len(title), _TITLE_MAX),
                )
            )
        else:
            doc.title = title
    elif "title" in parsed.data:
        add(fm_module.LintIssue(doc.path, parsed.line_of("title"), "'title' must be a scalar, not a list"))

    # type
    doc_type = parsed.get_scalar("type")
    if doc_type is not None:
        if doc_type not in DOC_TYPES:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("type"),
                    "unknown 'type' %r; expected one of %s" % (doc_type, ", ".join(DOC_TYPES)),
                )
            )
        else:
            doc.type = doc_type

    # summary
    summary = parsed.get_scalar("summary")
    if summary is not None:
        if summary == "":
            add(fm_module.LintIssue(doc.path, parsed.line_of("summary"), "'summary' must not be empty"))
        elif len(summary) > config.max_summary_chars:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("summary"),
                    "'summary' is %d characters; the limit is %d"
                    % (len(summary), config.max_summary_chars),
                )
            )
            doc.summary = summary
        else:
            doc.summary = summary
    elif "summary" in parsed.data:
        add(fm_module.LintIssue(doc.path, parsed.line_of("summary"), "'summary' must be a scalar, not a list"))

    # status. A decision record uses its own enum: "current" says nothing about
    # a decision and "accepted" says nothing about a doc.
    is_decision = doc.type == DECISION_TYPE
    allowed_statuses = DECISION_STATUSES if is_decision else DOC_STATUSES
    status = parsed.get_scalar("status")
    if status is not None:
        if status not in allowed_statuses:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("status"),
                    # Worded exactly as decisions.validate() words it, so that
                    # `lint` -- which runs both -- reports one problem once.
                    "unknown decision 'status' %r; expected one of %s"
                    % (status, ", ".join(allowed_statuses))
                    if is_decision
                    else "unknown 'status' %r; expected one of %s"
                    % (status, ", ".join(allowed_statuses)),
                )
            )
        else:
            doc.status = status

    # updated
    updated = parsed.get_scalar("updated")
    if updated is not None:
        if not _DATE_RE.match(updated) or not _is_real_date(updated):
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("updated"),
                    "'updated' must be a calendar date as YYYY-MM-DD, got %r" % (updated,),
                )
            )
        else:
            doc.updated = updated
    elif "updated" in parsed.data:
        add(fm_module.LintIssue(doc.path, parsed.line_of("updated"), "'updated' must be a scalar date"))

    # verified_at
    if "verified_at" in parsed.data:
        verified = parsed.get_scalar("verified_at")
        if verified is None:
            add(
                fm_module.LintIssue(
                    doc.path, parsed.line_of("verified_at"), "'verified_at' must be a scalar git sha"
                )
            )
        elif verified == "":
            doc.verified_at = None
        elif not _SHA_RE.match(verified):
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of("verified_at"),
                    "'verified_at' must be a lowercase git sha of 7-40 hex characters, got %r"
                    % (verified,),
                )
            )
        else:
            doc.verified_at = verified

    # list fields
    for field in LIST_FIELDS:
        if field not in parsed.data:
            continue
        values = parsed.get_list(field)
        if values is None:
            add(
                fm_module.LintIssue(
                    doc.path,
                    parsed.line_of(field),
                    "%r must be a list (block list or [a, b] flow list)" % (field,),
                )
            )
            continue
        seen = set()
        cleaned = []
        for value in values:
            if value in seen:
                add(
                    fm_module.LintIssue(
                        doc.path, parsed.line_of(field), "duplicate entry %r in %r" % (value, field)
                    )
                )
                continue
            seen.add(value)
            cleaned.append(value)
        if field == "covers":
            valid = []
            for pattern in cleaned:
                try:
                    globs.validate_pattern(pattern)
                except Exception as exc:
                    add(fm_module.LintIssue(doc.path, parsed.line_of(field), str(exc)))
                    continue
                valid.append(pattern)
            doc.covers = valid
        elif field == "tags":
            valid = []
            for tag in cleaned:
                if not _TAG_RE.match(tag):
                    add(
                        fm_module.LintIssue(
                            doc.path,
                            parsed.line_of(field),
                            "tag %r must match [a-z0-9][a-z0-9._-]*" % (tag,),
                        )
                    )
                    continue
                valid.append(tag)
            doc.tags = valid
        else:
            valid = []
            for owner in cleaned:
                if owner == "":
                    add(fm_module.LintIssue(doc.path, parsed.line_of(field), "empty entry in 'owners'"))
                    continue
                valid.append(owner)
            doc.owners = valid

    # decision-only fields
    if doc.type == DECISION_TYPE:
        for field in DECISION_LIST_FIELDS:
            if field not in parsed.data:
                continue
            values = parsed.get_list(field)
            if values is None:
                add(
                    fm_module.LintIssue(
                        doc.path,
                        parsed.line_of(field),
                        "%r must be a list (block list or [a, b] flow list)" % (field,),
                    )
                )
                continue
            cleaned = []
            for value in values:
                if value == "":
                    add(
                        fm_module.LintIssue(
                            doc.path, parsed.line_of(field), "empty entry in %r" % (field,)
                        )
                    )
                    continue
                if value in cleaned:
                    add(
                        fm_module.LintIssue(
                            doc.path,
                            parsed.line_of(field),
                            "duplicate entry %r in %r" % (value, field),
                        )
                    )
                    continue
                cleaned.append(value)
            setattr(doc, field, cleaned)

        if "decided_on" in parsed.data:
            decided = parsed.get_scalar("decided_on")
            if decided is None or not _DATE_RE.match(decided) or not _is_real_date(decided):
                add(
                    fm_module.LintIssue(
                        doc.path,
                        parsed.line_of("decided_on"),
                        "'decided_on' must be a calendar date as YYYY-MM-DD, got %r"
                        % (decided,),
                    )
                )
            else:
                doc.decided_on = decided

    known = set(REQUIRED_FIELDS) | {"owners", "tags", "verified_at"}
    if doc.type == DECISION_TYPE:
        known |= set(DECISION_FIELDS)
    for key in parsed.data:
        if key in known:
            continue
        doc.extra[key] = parsed.data[key]


def _is_real_date(text):
    try:
        year, month, day = (int(part) for part in text.split("-"))
    except ValueError:
        return False
    if not 1 <= month <= 12:
        return False
    days = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= day <= days[month - 1]


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def first_heading(body):
    """Return the first ``# heading`` in *body*, or None."""
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            return match.group(1)
    return None


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def build_payload(doc_set, head_sha=None):
    """Build the ``index.json`` payload for *doc_set*."""
    entries = []
    for doc in doc_set.docs:
        entry = doc.as_entry()
        order = entry.pop("_order")
        entries.append(_ordered(entry, order))
    payload = _ordered(
        {
            "version": INDEX_VERSION,
            "generated_at_sha": head_sha,
            "roots": list(doc_set.roots),
            "doc_count": len(entries),
            "docs": entries,
        },
        ["version", "generated_at_sha", "roots", "doc_count", "docs"],
    )
    return payload


def _ordered(mapping, order):
    result = {}
    for key in order:
        if key in mapping:
            result[key] = mapping[key]
    for key in mapping:
        if key not in result:
            result[key] = mapping[key]
    return result


def render_json(payload):
    """Serialize the index payload deterministically, with a trailing newline."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def render_markdown(doc_set):
    """Render the terse ``.gotdocs/INDEX.md``.

    Exactly **one line per document**, grouped by type:

        - **id** - summary  ·  `path`  ·  covers: pattern, pattern

    This file is read into agent context constantly, so it stays terse: it exists
    so an agent can decide which document to open without paying for the
    documents themselves.
    """
    lines = []
    lines.append("# Docs index")
    lines.append("")
    lines.append("<!-- Generated by gotdocs. Do not edit by hand; run: bin/gotdocs index -->")
    lines.append("")
    lines.append(
        "**Read this file first, then open only the docs you need.** One line per "
        "document, grouped by type. `covers` lists the code each document describes; "
        "changing a covered file makes that document impacted."
    )
    lines.append("")
    lines.append(
        "%d document%s under %s."
        % (
            len(doc_set.docs),
            "" if len(doc_set.docs) == 1 else "s",
            ", ".join("`%s/`" % root for root in doc_set.roots) or "no configured roots",
        )
    )
    lines.append("")

    grouped = {}
    for doc in doc_set.docs:
        grouped.setdefault(doc.type or "unknown", []).append(doc)

    section_order = [
        key for key in DOC_TYPES if key != DECISION_TYPE
    ] + sorted(key for key in grouped if key not in DOC_TYPES)
    for section in section_order:
        docs = grouped.get(section)
        if not docs:
            continue
        lines.append("## %s" % (section,))
        lines.append("")
        for doc in docs:
            summary = doc.summary or doc.title or first_heading(
                doc.frontmatter.body if doc.frontmatter else ""
            ) or doc.path
            status = ""
            if doc.status and doc.status != "current":
                status = " _(%s)_" % (doc.status,)
            covers = (
                ", ".join("`%s`" % pattern for pattern in doc.covers)
                if doc.covers
                else "none"
            )
            lines.append(
                "- **%s**%s - %s  ·  `%s`  ·  covers: %s"
                % (doc.display_id, status, summary, doc.path, covers)
            )
        lines.append("")

    lines.extend(_decision_section(grouped.get(DECISION_TYPE) or []))

    if not doc_set.docs:
        lines.append("No documents found. Create one with `bin/gotdocs new doc <id>`.")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _decision_section(decisions):
    """The ``## Decisions`` block: accepted records only, the rest as a count.

    Two rules make this section safe to keep in an agent's context forever:

    * only ``accepted`` records are listed. A proposed record is not yet the
      answer to "why does it do this", a rejected one never was, and a
      superseded one has been replaced by a record that *is* listed. Naming
      them here would invite an agent to cite a decision that is not in force.
    * ``symptoms`` never appear. They are the search corpus for
      ``gotdocs why`` - several lines per record - and this file is read whole
      on every session. Ask ``bin/gotdocs why`` instead; it reads the records.
    """
    if not decisions:
        return []

    accepted = [doc for doc in decisions if doc.status == "accepted"]
    other = {}
    for doc in decisions:
        if doc.status == "accepted":
            continue
        other[doc.status or "unknown"] = other.get(doc.status or "unknown", 0) + 1

    lines = ["## Decisions", ""]
    lines.append(
        "Architecture decisions in force. Before calling behaviour a bug, ask "
        "`bin/gotdocs why \"<what you observed>\"` - it searches what each record "
        "explains, which is deliberately not reproduced here."
    )
    lines.append("")
    for doc in accepted:
        summary = doc.summary or doc.title or doc.path
        lines.append(
            "- **%s** - %s  ·  `%s`" % (doc.display_id, summary, doc.path)
        )
    if not accepted:
        lines.append("_No accepted decisions yet._")
    if other:
        counted = ", ".join(
            "%d %s" % (other[status], status) for status in sorted(other)
        )
        lines.append("")
        lines.append(
            "Not listed: %s. Run `bin/gotdocs why --all` to see every record."
            % (counted,)
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# writing / comparison
# ---------------------------------------------------------------------------


def write_index(repo_root, config, doc_set=None, head_sha=None):
    """Regenerate both index files. Returns ``(doc_set, changed_paths)``.

    ``generated_at_sha`` records the commit at which the *documents* were last
    indexed, not the commit that happens to be checked out now. When nothing
    else in the payload changed, the sha already on disk is kept, so
    regenerating on a later checkout rewrites identical bytes: the index never
    churns the diff and the CI freshness gate (a byte-level `git status`) is not
    permanently red. A commit can never contain its own sha, so stamping HEAD
    unconditionally would make every committed index stale on arrival.
    """
    if doc_set is None:
        doc_set = scan(repo_root, config)
    payload = build_payload(doc_set, head_sha)
    json_path_abs = os.path.join(repo_root, INDEX_JSON_PATH)
    payload["generated_at_sha"] = _preserved_sha(json_path_abs, payload, head_sha)
    json_text = render_json(payload)
    md_text = render_markdown(doc_set)

    changed = []
    if _write_if_changed(os.path.join(repo_root, INDEX_JSON_PATH), json_text):
        changed.append(INDEX_JSON_PATH)
    if _write_if_changed(os.path.join(repo_root, INDEX_MD_PATH), md_text):
        changed.append(INDEX_MD_PATH)
    return doc_set, changed


def _preserved_sha(json_path, payload, head_sha):
    """The sha to write: the existing one when the rest of the index is equal."""
    existing = _read_text(json_path)
    if existing is None:
        return head_sha
    try:
        parsed = json.loads(existing)
    except ValueError:
        return head_sha
    if not isinstance(parsed, dict):
        return head_sha
    if _comparable(parsed) != _comparable(payload):
        return head_sha
    previous = parsed.get("generated_at_sha")
    if previous is None:
        return head_sha
    return previous


def _write_if_changed(path, text):
    data = text.encode("utf-8")
    try:
        with io.open(path, "rb") as handle:
            if handle.read() == data:
                return False
    except (IOError, OSError):
        pass
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "wb") as handle:
        handle.write(data)
    return True


def index_is_current(repo_root, config, doc_set=None, head_sha=None):
    """Return ``(ok, stale_paths)`` comparing committed indexes to fresh ones.

    ``generated_at_sha`` is excluded from the JSON comparison: it is the one
    volatile field, and including it would report every checkout as out of date
    the moment a new commit landed.
    """
    if doc_set is None:
        doc_set = scan(repo_root, config)
    payload = build_payload(doc_set, head_sha)
    expected_md = render_markdown(doc_set)

    stale = []

    json_path = os.path.join(repo_root, INDEX_JSON_PATH)
    existing = _read_text(json_path)
    if existing is None:
        stale.append(INDEX_JSON_PATH)
    else:
        try:
            parsed = json.loads(existing)
        except ValueError:
            stale.append(INDEX_JSON_PATH)
            parsed = None
        if parsed is not None:
            if _comparable(parsed) != _comparable(payload):
                stale.append(INDEX_JSON_PATH)

    md_path = os.path.join(repo_root, INDEX_MD_PATH)
    existing_md = _read_text(md_path)
    if existing_md is None or existing_md != expected_md:
        stale.append(INDEX_MD_PATH)

    return (not stale, stale)


def _comparable(payload):
    if not isinstance(payload, dict):
        return payload
    clone = dict(payload)
    clone.pop("generated_at_sha", None)
    return clone


def _read_text(path):
    try:
        with io.open(path, "rb") as handle:
            return handle.read().decode("utf-8")
    except (IOError, OSError, UnicodeDecodeError):
        return None

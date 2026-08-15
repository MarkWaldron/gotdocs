"""The doc-debt ledger: findings that were accepted instead of fixed.

``gotdocs check`` answers "is this change set clean right now". That answer is
useless for a repo adopting gotdocs on top of ten years of undocumented code:
the first run reports hundreds of findings, someone sets ``mode: warn``, and the
warnings are never read again. The ledger is the other half: a finding that is
knowingly deferred is *recorded* once, tracked across commits, and reported as a
short, bounded list.

Shape and guarantees:

* ``.gotdocs/debt.jsonl`` - one JSON object per line, so the file diffs per
  entry and a corrupt line costs one entry rather than the whole ledger.
* An entry is identified by :func:`entry_id`, a stable digest of
  ``(kind, doc_id, path)``. Recording the same finding again while it is open
  bumps ``occurrences`` and ``last_seen_*`` in place; it never appends a second
  line.
* Every date is supplied by the caller (a git commit date). Nothing in this
  module reads the wall clock, so the same inputs always produce the same bytes
  and the ledger can be regenerated in CI without churning the diff.
* :func:`write_ledger` writes a temp file and ``os.replace``s it, and returns
  ``False`` when the bytes are unchanged, so a caller can skip a no-op commit.
* :func:`load_ledger` never raises on bad content: it returns the entries it
  could parse plus a list of :class:`LedgerError` describing what it skipped.
"""

import hashlib
import io
import json
import os
import re

from .errors import UsageError

__all__ = [
    "DebtEntry",
    "LedgerError",
    "RecordResult",
    "LEDGER_PATH",
    "LEDGER_VERSION",
    "MARKDOWN_PATH",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "STATUSES",
    "KIND_ORDER",
    "ENTRY_FIELDS",
    "entry_id",
    "entry_from_finding",
    "entry_from_dict",
    "sort_entries",
    "filter_entries",
    "find_entries",
    "record_findings",
    "resolve_entries",
    "resolve_absent",
    "summarize",
    "build_payload",
    "render_json",
    "render_jsonl",
    "render_markdown",
    "load_ledger",
    "write_ledger",
    "write_markdown",
]

LEDGER_PATH = ".gotdocs/debt.jsonl"
MARKDOWN_PATH = ".gotdocs/DEBT.md"
LEDGER_VERSION = 1

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUSES = (STATUS_OPEN, STATUS_RESOLVED)

# Kept in step with check.KIND_ORDER by value, not by import: the ledger must
# stay loadable and renderable on its own. Unknown kinds sort after these,
# alphabetically, so a new finding kind never reorders the existing report.
KIND_ORDER = (
    "stale",
    "uncovered",
    "lint",
    "duplicate_id",
    "deprecated_edit",
    "index_out_of_date",
)

# Serialized key order. Also the constructor keyword names.
ENTRY_FIELDS = (
    "entry_id",
    "kind",
    "doc_id",
    "path",
    "message",
    "remediation",
    "status",
    "occurrences",
    "first_seen_date",
    "first_seen_sha",
    "last_seen_date",
    "last_seen_sha",
    "resolved_date",
    "resolved_sha",
    "note",
)

_ID_LEN = 12
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[0-9a-f]{%d}$" % (_ID_LEN,))


class DebtEntry(object):
    """One deferred finding, tracked across commits."""

    __slots__ = ENTRY_FIELDS

    def __init__(self, kind, path, **kwargs):
        self.kind = kind
        self.path = path
        self.doc_id = kwargs.get("doc_id")
        self.message = kwargs.get("message") or ""
        self.remediation = kwargs.get("remediation") or ""
        self.status = kwargs.get("status") or STATUS_OPEN
        self.occurrences = kwargs.get("occurrences", 1)
        self.first_seen_date = kwargs.get("first_seen_date")
        self.first_seen_sha = kwargs.get("first_seen_sha")
        self.last_seen_date = kwargs.get("last_seen_date", self.first_seen_date)
        self.last_seen_sha = kwargs.get("last_seen_sha", self.first_seen_sha)
        self.resolved_date = kwargs.get("resolved_date")
        self.resolved_sha = kwargs.get("resolved_sha")
        self.note = kwargs.get("note")
        self.entry_id = kwargs.get("entry_id") or entry_id(kind, self.doc_id, path)

    # -- derived ----------------------------------------------------------

    @property
    def is_open(self):
        return self.status == STATUS_OPEN

    @property
    def display_id(self):
        """What the report prints for this entry: doc id if it has one."""
        return self.doc_id or self.path or self.entry_id

    def sort_key(self):
        """Total order, independent of ``status``.

        Resolving an entry rewrites its line in place instead of moving it, so
        the ledger diff stays one line per state change.
        """
        try:
            rank = KIND_ORDER.index(self.kind)
        except ValueError:
            rank = len(KIND_ORDER)
        return (rank, self.kind or "", self.doc_id or "", self.path or "", self.entry_id)

    # -- serialization ----------------------------------------------------

    def as_dict(self):
        """Ordered mapping using :data:`ENTRY_FIELDS`; safe to json.dumps."""
        result = {}
        for field in ENTRY_FIELDS:
            result[field] = getattr(self, field)
        return result

    def copy(self):
        return entry_from_dict(self.as_dict())

    def __eq__(self, other):
        if not isinstance(other, DebtEntry):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self):  # pragma: no cover - debugging aid
        return "DebtEntry(%r, %r, %s)" % (self.kind, self.path, self.status)


class LedgerError(object):
    """A line of the ledger that could not be used, and why."""

    __slots__ = ("line", "message", "text")

    def __init__(self, line, message, text=None):
        self.line = line
        self.message = message
        self.text = text

    def as_dict(self):
        return {"line": self.line, "message": self.message, "text": self.text}

    def located(self, path=LEDGER_PATH):
        return "%s:%d: %s" % (path, self.line, self.message)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "LedgerError(%d, %r)" % (self.line, self.message)


class RecordResult(object):
    """What :func:`record_findings` did: the new ledger plus the three deltas."""

    __slots__ = ("entries", "added", "updated", "reopened")

    def __init__(self, entries, added, updated, reopened):
        self.entries = entries
        self.added = added
        self.updated = updated
        self.reopened = reopened

    @property
    def changed(self):
        return bool(self.added or self.updated or self.reopened)

    def counts(self):
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "reopened": len(self.reopened),
            "total": len(self.entries),
        }

    def __repr__(self):  # pragma: no cover - debugging aid
        return "RecordResult(+%d ~%d ^%d)" % (
            len(self.added),
            len(self.updated),
            len(self.reopened),
        )


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def entry_id(kind, doc_id, path):
    """Stable 12-hex-digit id for the finding ``(kind, doc_id, path)``.

    Deterministic across runs, machines and Python versions: the digest is taken
    over NUL-separated UTF-8, and ``None`` is encoded as the empty string, so an
    entry recorded today keeps its id forever. ``message`` is deliberately not
    part of the identity - rewording a finding must not fork its history.
    """
    parts = [kind or "", doc_id or "", path or ""]
    raw = "\x00".join(parts).encode("utf-8", "surrogateescape")
    return hashlib.sha1(raw).hexdigest()[:_ID_LEN]


def entry_from_finding(finding, seen_date, seen_sha=None, note=None):
    """Build a fresh open :class:`DebtEntry` from a ``check.Finding``.

    Duck-typed on ``kind``/``path``/``doc_id``/``message``/``remediation`` so a
    plain object or a dict-backed shim works in tests.
    """
    kind, path, doc_id, message, remediation = _finding_fields(finding)
    _check_date(seen_date)
    return DebtEntry(
        kind,
        path,
        doc_id=doc_id,
        message=message,
        remediation=remediation,
        status=STATUS_OPEN,
        occurrences=1,
        first_seen_date=seen_date,
        first_seen_sha=seen_sha,
        last_seen_date=seen_date,
        last_seen_sha=seen_sha,
        note=note,
    )


def entry_from_dict(data):
    """Rebuild an entry from a parsed ledger line. Raises ValueError if unusable."""
    if not isinstance(data, dict):
        raise ValueError("entry must be a JSON object")
    kind = _text_field(data, "kind")
    if not kind:
        raise ValueError("entry is missing 'kind'")
    path = _text_field(data, "path")
    doc_id = _text_field(data, "doc_id")
    if not path and not doc_id:
        raise ValueError("entry needs at least one of 'path' or 'doc_id'")

    status = _text_field(data, "status") or STATUS_OPEN
    if status not in STATUSES:
        raise ValueError("unknown status %r" % (status,))

    occurrences = data.get("occurrences", 1)
    if isinstance(occurrences, bool) or not isinstance(occurrences, int):
        raise ValueError("'occurrences' must be an integer")
    if occurrences < 1:
        raise ValueError("'occurrences' must be >= 1")

    for field in ("first_seen_date", "last_seen_date", "resolved_date"):
        value = _text_field(data, field)
        if value is not None:
            _check_date(value, field)

    stored_id = _text_field(data, "entry_id")
    computed = entry_id(kind, doc_id, path)
    if stored_id is not None and not _ID_RE.match(stored_id):
        raise ValueError("'entry_id' must be %d lowercase hex digits" % (_ID_LEN,))
    if stored_id is not None and stored_id != computed:
        raise ValueError(
            "'entry_id' %s does not match (kind, doc_id, path) digest %s"
            % (stored_id, computed)
        )

    first_date = _text_field(data, "first_seen_date")
    first_sha = _text_field(data, "first_seen_sha")
    return DebtEntry(
        kind,
        path,
        entry_id=computed,
        doc_id=doc_id,
        message=_text_field(data, "message") or "",
        remediation=_text_field(data, "remediation") or "",
        status=status,
        occurrences=occurrences,
        first_seen_date=first_date,
        first_seen_sha=first_sha,
        last_seen_date=_text_field(data, "last_seen_date") or first_date,
        last_seen_sha=_text_field(data, "last_seen_sha") or first_sha,
        resolved_date=_text_field(data, "resolved_date"),
        resolved_sha=_text_field(data, "resolved_sha"),
        note=_text_field(data, "note"),
    )


def _finding_fields(finding):
    if isinstance(finding, dict):
        get = finding.get
    else:
        def get(name, default=None):
            return getattr(finding, name, default)

    kind = get("kind")
    if not kind:
        raise UsageError("cannot record a finding with no 'kind'")
    path = get("path")
    doc_id = get("doc_id")
    if not path and not doc_id:
        raise UsageError("cannot record a finding with neither 'path' nor 'doc_id'")
    return (
        kind,
        path,
        doc_id,
        get("message") or "",
        get("remediation") or "",
    )


def _text_field(data, key):
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("%r must be a string" % (key,))
    return value


def _check_date(value, field="date"):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise UsageError(
            "%s must be an ISO date (YYYY-MM-DD) taken from git, got %r" % (field, value)
        )
    return value


# ---------------------------------------------------------------------------
# querying
# ---------------------------------------------------------------------------


def sort_entries(entries):
    """Return *entries* in the ledger's canonical order (a new list)."""
    return sorted(entries, key=lambda entry: entry.sort_key())


def filter_entries(entries, status=None, kind=None, doc_id=None, path=None):
    """Filter by any combination of fields, preserving order."""
    result = []
    for entry in entries:
        if status is not None and entry.status != status:
            continue
        if kind is not None and entry.kind != kind:
            continue
        if doc_id is not None and entry.doc_id != doc_id:
            continue
        if path is not None and entry.path != path:
            continue
        result.append(entry)
    return result


def find_entries(entries, ref):
    """Entries matching *ref*: a full entry id, an id prefix, a doc id or a path.

    Returned in canonical order. An empty list means nothing matched; more than
    one means the reference is ambiguous and the caller should say so.
    """
    if not ref:
        return []
    exact = [entry for entry in entries if entry.entry_id == ref]
    if exact:
        return sort_entries(exact)
    matched = [
        entry
        for entry in entries
        if entry.entry_id.startswith(ref) or entry.doc_id == ref or entry.path == ref
    ]
    return sort_entries(matched)


def summarize(entries):
    """Counts for the CLI header and ``--json``: totals, open by kind, top docs."""
    open_entries = [entry for entry in entries if entry.is_open]
    by_kind = {}
    by_doc = {}
    occurrences = 0
    for entry in open_entries:
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
        key = entry.doc_id or entry.path or entry.entry_id
        by_doc[key] = by_doc.get(key, 0) + 1
        occurrences += entry.occurrences
    return {
        "total": len(entries),
        "open": len(open_entries),
        "resolved": len(entries) - len(open_entries),
        "open_occurrences": occurrences,
        "open_by_kind": by_kind,
        "open_by_doc": by_doc,
    }


# ---------------------------------------------------------------------------
# mutation (pure: every function returns a new list)
# ---------------------------------------------------------------------------


def record_findings(entries, findings, seen_date, seen_sha=None, note=None):
    """Merge *findings* into *entries*, returning a :class:`RecordResult`.

    * unknown ``(kind, doc_id, path)`` -> a new open entry, ``occurrences`` 1
    * already open -> ``occurrences`` + 1 and ``last_seen_*`` updated, in place;
      no second line is ever appended
    * resolved -> reopened, ``resolved_*`` cleared, ``occurrences`` + 1
    * every finding in one call that maps to the same entry counts as **one**
      sighting, so ``occurrences`` is "runs that reported this", not "findings".
      Five copies of the same finding, or five different lint errors in one
      file, are one entry with ``occurrences`` 1 after one call. The extra
      findings are named in ``message`` as ``(+N more findings on this
      document)`` so the bounded report does not hide them.

    ``message`` and ``remediation`` are refreshed from the newest finding: the
    identity is the finding, not its wording. *entries* is not mutated.
    """
    _check_date(seen_date, "seen_date")
    merged = [entry.copy() for entry in entries]
    by_id = {}
    for entry in merged:
        by_id.setdefault(entry.entry_id, entry)

    added = []
    updated = []
    reopened = []
    for ident, group in _group_by_entry(findings):
        kind, path, doc_id, message, remediation = _finding_fields(group[0])
        message = _group_message(message, len(group))
        existing = by_id.get(ident)
        if existing is None:
            entry = DebtEntry(
                kind,
                path,
                entry_id=ident,
                doc_id=doc_id,
                message=message,
                remediation=remediation,
                status=STATUS_OPEN,
                occurrences=1,
                first_seen_date=seen_date,
                first_seen_sha=seen_sha,
                last_seen_date=seen_date,
                last_seen_sha=seen_sha,
                note=note,
            )
            merged.append(entry)
            by_id[ident] = entry
            added.append(ident)
            continue

        was_resolved = not existing.is_open
        existing.message = message
        existing.remediation = remediation
        existing.occurrences += 1
        existing.last_seen_date = seen_date
        existing.last_seen_sha = seen_sha
        if note is not None:
            existing.note = note
        if was_resolved:
            existing.status = STATUS_OPEN
            existing.resolved_date = None
            existing.resolved_sha = None
            reopened.append(ident)
        else:
            updated.append(ident)

    return RecordResult(sort_entries(merged), added, _dedupe(updated), _dedupe(reopened))


def _group_by_entry(findings):
    """``[(entry_id, [finding, ...]), ...]`` in first-seen order.

    One document can produce several distinct findings of the same kind - four
    separate lint errors in one file, say - and they all hash to the same
    ``(kind, doc_id, path)``. They are one ledger entry, but they are *one*
    sighting of it, not four: bumping ``occurrences`` per finding made a single
    run read as "seen 4x since <today>". Grouping here is what makes
    ``occurrences`` mean "runs that reported this", which is what the report
    renders.
    """
    order = []
    groups = {}
    for finding in findings:
        kind, path, doc_id, _message, _remediation = _finding_fields(finding)
        ident = entry_id(kind, doc_id, path)
        if ident not in groups:
            groups[ident] = []
            order.append(ident)
        groups[ident].append(finding)
    return [(ident, groups[ident]) for ident in order]


def _group_message(message, count):
    """Keep the extra findings visible on the single line the report prints."""
    if count <= 1:
        return message
    extra = count - 1
    return "%s (+%d more finding%s on this document)" % (
        message,
        extra,
        "" if extra == 1 else "s",
    )


def resolve_entries(entries, refs, resolved_date, resolved_sha=None, note=None):
    """Close the entries named by *refs*.

    Returns ``(entries, resolved_ids, unmatched)`` where *unmatched* holds the
    references that matched nothing, and ambiguous references are reported by
    raising :class:`~.errors.UsageError` naming the candidates - a CLI must not
    silently close the wrong debt. Refs that are already resolved are accepted
    and reported in *resolved_ids* without changing their ``resolved_*`` stamps.
    """
    _check_date(resolved_date, "resolved_date")
    updated = [entry.copy() for entry in entries]
    by_id = {entry.entry_id: entry for entry in updated}

    resolved = []
    unmatched = []
    for ref in refs:
        matches = find_entries(updated, ref)
        if not matches:
            unmatched.append(ref)
            continue
        if len(matches) > 1:
            raise UsageError(
                "%r matches %d debt entries (%s); use a full entry id"
                % (ref, len(matches), ", ".join(entry.entry_id for entry in matches))
            )
        entry = by_id[matches[0].entry_id]
        if entry.is_open:
            entry.status = STATUS_RESOLVED
            entry.resolved_date = resolved_date
            entry.resolved_sha = resolved_sha
            if note is not None:
                entry.note = note
        if entry.entry_id not in resolved:
            resolved.append(entry.entry_id)

    return (sort_entries(updated), resolved, unmatched)


def resolve_absent(entries, findings, resolved_date, resolved_sha=None, paths=None):
    """Close open entries that the current *findings* no longer report.

    Only safe for a run that examined the whole repository. For a partial run
    pass *paths* - the set of paths the run actually looked at - and only open
    entries whose ``path`` is in it are eligible, so a staged-only check cannot
    wipe the ledger. Returns ``(entries, resolved_ids)``.
    """
    _check_date(resolved_date, "resolved_date")
    present = set()
    for finding in findings:
        kind, path, doc_id, _message, _remediation = _finding_fields(finding)
        present.add(entry_id(kind, doc_id, path))

    scope = None if paths is None else set(paths)
    updated = [entry.copy() for entry in entries]
    resolved = []
    for entry in updated:
        if not entry.is_open or entry.entry_id in present:
            continue
        if scope is not None and entry.path not in scope:
            continue
        entry.status = STATUS_RESOLVED
        entry.resolved_date = resolved_date
        entry.resolved_sha = resolved_sha
        resolved.append(entry.entry_id)

    return (sort_entries(updated), resolved)


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_jsonl(entries):
    """Serialize the ledger: one compact JSON object per line, sorted, LF-terminated."""
    lines = []
    for entry in sort_entries(entries):
        lines.append(
            json.dumps(
                entry.as_dict(),
                ensure_ascii=False,
                sort_keys=False,
                separators=(",", ":"),
            )
        )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def build_payload(entries):
    """The ``--json`` payload: version, summary and every entry, in ledger order."""
    ordered = sort_entries(entries)
    return {
        "version": LEDGER_VERSION,
        "summary": summarize(ordered),
        "entries": [entry.as_dict() for entry in ordered],
    }


def render_json(payload):
    """Serialize a payload deterministically, with a trailing newline."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def render_markdown(entries, limit=None, title="Doc debt"):
    """Render the human report: open entries by kind, resolved as a count.

    Deliberately bounded. Open entries are grouped by finding kind and printed
    one line each, carrying the remediation command so a reader never has to
    look up how to clear the item. Resolved entries collapse to a single count -
    the ledger keeps them for history, the report does not spend space on them.
    *limit* caps the lines printed per kind, with an explicit "and N more".
    """
    ordered = sort_entries(entries)
    stats = summarize(ordered)
    open_entries = [entry for entry in ordered if entry.is_open]

    lines = ["# %s" % (title,), ""]
    lines.append(
        "<!-- Generated by gotdocs. Do not edit by hand; run: bin/gotdocs debt report -->"
    )
    lines.append("")
    lines.append(
        "%d open, %d resolved. Open items are findings that were accepted "
        "instead of fixed; each line carries the command that clears it."
        % (stats["open"], stats["resolved"])
    )
    lines.append("")

    if not open_entries:
        lines.append("No open doc debt.")
        lines.append("")
    else:
        grouped = {}
        for entry in open_entries:
            grouped.setdefault(entry.kind, []).append(entry)
        for kind in _kind_sections(grouped):
            bucket = grouped[kind]
            lines.append("## %s (%d)" % (kind, len(bucket)))
            lines.append("")
            shown = bucket if limit is None else bucket[:limit]
            for entry in shown:
                lines.append(_entry_line(entry))
            hidden = len(bucket) - len(shown)
            if hidden > 0:
                lines.append("- ... and %d more" % (hidden,))
            lines.append("")

    if stats["resolved"]:
        lines.append(
            "%d resolved entr%s kept for history in `%s`."
            % (
                stats["resolved"],
                "y" if stats["resolved"] == 1 else "ies",
                LEDGER_PATH,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _entry_line(entry):
    parts = ["- **%s**" % (entry.display_id,)]
    if entry.path and entry.path != entry.display_id:
        parts.append("`%s`" % (entry.path,))
    if entry.message:
        parts.append(entry.message)
    seen = "seen %d×" % (entry.occurrences,)
    if entry.first_seen_date:
        seen += " since %s" % (entry.first_seen_date,)
    parts.append(seen)
    if entry.remediation:
        parts.append("fix: `%s`" % (entry.remediation,))
    parts.append("id `%s`" % (entry.entry_id,))
    return "  ·  ".join(parts)


def _kind_sections(grouped):
    known = [kind for kind in KIND_ORDER if kind in grouped]
    rest = sorted(kind for kind in grouped if kind not in KIND_ORDER)
    return known + rest


# ---------------------------------------------------------------------------
# reading / writing
# ---------------------------------------------------------------------------


def load_ledger(repo_root, path=None):
    """Read the ledger. Returns ``(entries, errors)`` and never raises on content.

    A missing file is an empty ledger, not an error. A truncated, malformed or
    duplicated line is skipped and described by a :class:`LedgerError`, so one
    bad merge conflict cannot take the whole ledger - or the CLI - down.
    """
    ledger_path = os.path.join(repo_root, _rel(path))
    text = _read_text(ledger_path)
    if text is None:
        return ([], [])

    entries = []
    errors = []
    seen = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except ValueError as exc:
            errors.append(LedgerError(number, "unparseable JSON: %s" % (exc,), raw))
            continue
        try:
            entry = entry_from_dict(data)
        except (ValueError, UsageError) as exc:
            errors.append(LedgerError(number, str(exc), raw))
            continue
        if entry.entry_id in seen:
            errors.append(
                LedgerError(
                    number,
                    "duplicate entry %s (first seen on line %d); line skipped"
                    % (entry.entry_id, seen[entry.entry_id]),
                    raw,
                )
            )
            continue
        seen[entry.entry_id] = number
        entries.append(entry)

    return (sort_entries(entries), errors)


def write_ledger(repo_root, entries, path=None):
    """Write the ledger atomically. Returns True only if the bytes changed.

    The rendered text is compared against what is on disk first: an unchanged
    ledger is not rewritten at all, so a caller can skip an empty commit and CI
    can regenerate the file without producing a diff. When it does write, it
    writes ``<file>.tmp-<pid>`` in the same directory and ``os.replace``s it, so
    a reader never observes a half-written ledger and a crash cannot truncate
    the old one.
    """
    return _write_atomic(os.path.join(repo_root, _rel(path)), render_jsonl(entries))


def write_markdown(repo_root, entries, path=None, limit=None):
    """Write the rendered report atomically. Returns True only if bytes changed."""
    target = os.path.join(repo_root, path if path is not None else MARKDOWN_PATH)
    return _write_atomic(target, render_markdown(entries, limit=limit))


def _rel(path):
    return LEDGER_PATH if path is None else path


def _write_atomic(target, text):
    data = text.encode("utf-8")
    existing = _read_bytes(target)
    if existing == data:
        return False
    if existing is None and not data:
        # Nothing to record and no file yet: creating an empty ledger would
        # leave a committable artifact behind for a command that failed or
        # changed nothing (`debt resolve <unknown-ref>` used to do exactly
        # that). "No debt" and "an empty debt file" must not be different
        # states of the working tree.
        return False
    directory = os.path.dirname(target)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temp = "%s.tmp-%d" % (target, os.getpid())
    handle = io.open(temp, "wb")
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    try:
        os.replace(temp, target)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    return True


def _read_bytes(path):
    try:
        with io.open(path, "rb") as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def _read_text(path):
    raw = _read_bytes(path)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")

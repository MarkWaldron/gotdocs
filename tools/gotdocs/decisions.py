"""Architecture decision records, and the ``why`` lookup built on top of them.

The problem this module exists to solve: an agent (or a person) sees a feature
behaving oddly and has to answer one question before doing anything else --
*is this an intentional tradeoff, or a bug?* Reading every document to find out
is expensive and usually fruitless. So each decision record carries, in its
frontmatter, the list of **symptoms** it explains, and in its body two
load-bearing sections:

    ## Expected behavior
    ## This is a bug, not this decision, if...

:func:`why` scores a free-text symptom description against every record's
``symptoms``/``title``/``summary``/``tags`` with plain token overlap, and
:func:`format_why` renders the top few as a handful of lines. No index is
built, no document body is loaded into the answer beyond those two sections.

Layout. Decision records live under one root (``decisions/`` by default), one
file per record, named ``NNNN-slug.md``:

.. code-block:: text

    decisions/0007-retry-budget-per-request.md

The four-digit number is the record's identity and comes from the *filename* --
there is no ``number`` frontmatter key to drift out of sync. The ``id`` key
must equal the filename stem, so ``supersedes: [0004-retry-per-hop]`` is
unambiguous and greppable.

Frontmatter, beyond the fields every gotdocs document has::

    status: accepted            # proposed | accepted | rejected | superseded
    symptoms:                   # observable behaviour this record explains
      - a POST is retried exactly twice and then fails fast
    supersedes: [0004-retry-per-hop]
    superseded_by: []

Zero third-party dependencies: stdlib only, no embeddings, no search index.
"""

import os
import re
import unicodedata

from . import frontmatter as fm_module

__all__ = [
    "Decision",
    "Match",
    "DECISION_TYPE",
    "DECISION_STATUSES",
    "IN_FORCE_STATUSES",
    "DEFAULT_ROOT",
    "LIST_FIELDS",
    "SECTION_EXPECTED",
    "SECTION_NOT_THIS",
    "SECTION_KEYS",
    "FILENAME_RE",
    "SYMPTOM_WEIGHT",
    "TITLE_WEIGHT",
    "SUMMARY_WEIGHT",
    "TAG_WEIGHT",
    "PHRASE_BONUS",
    "MIN_TERM_COVERAGE",
    "RELATIVE_FLOOR",
    "DEFAULT_LIMIT",
    "load",
    "parse_decision",
    "from_doc",
    "extract_sections",
    "lead_claim",
    "tokenize",
    "why",
    "format_why",
    "validate",
    "next_number",
    "next_number_from",
    "numbers",
]

DECISION_TYPE = "decision"

# Per-type status enum. Deliberately *not* index.DOC_STATUSES: "current" means
# nothing for a decision, and "accepted"/"superseded" mean nothing for a doc.
DECISION_STATUSES = ("proposed", "accepted", "rejected", "superseded")

DEFAULT_ROOT = "decisions"

LIST_FIELDS = ("symptoms", "supersedes", "superseded_by", "tags", "owners", "covers")

SECTION_EXPECTED = "expected"
SECTION_NOT_THIS = "not_this"
SECTION_KEYS = (SECTION_EXPECTED, SECTION_NOT_THIS)

MARKDOWN_SUFFIXES = (".md", ".markdown")

# `0007-retry-budget-per-request.md`
FILENAME_RE = re.compile(r"^(\d{4})-([a-z0-9][a-z0-9-]*)\.(?:md|markdown)$")
_NUMBER_RE = re.compile(r"^\d{4}$")

# Scoring weights, strictly descending: a symptom is what the caller actually
# observed, so a symptom hit must outrank a title hit outranking a summary hit
# outranking a tag hit.
SYMPTOM_WEIGHT = 4.0
TITLE_WEIGHT = 2.0
SUMMARY_WEIGHT = 1.0
TAG_WEIGHT = 0.5

# Awarded when the query's terms appear *in order and adjacent* inside one
# symptom: that is a near-quotation of the symptom and should be decisive.
# Sized to outweigh a rival that matches every term but only scattered across
# its symptom, title and summary (SYMPTOM + TITLE + SUMMARY + TAG = 7.5).
PHRASE_BONUS = 2.0

# A record that overlaps the query on fewer than this fraction of the query's
# distinct terms is a coincidence, not an answer: a six-word symptom that
# shares only "docs" with a record is noise, and noise below a real answer is
# worse than no answer at all. Sized so a one- or two-term query (where every
# hit necessarily covers 50-100%) is unaffected.
MIN_TERM_COVERAGE = 0.34

# A record scoring below this fraction of the *leader's* score is not a rival
# explanation, it is the tail. `why` exists to be decisive; showing a runner-up
# at a fifth of the top score invites the reader to weigh two answers when only
# one was written down.
RELATIVE_FLOOR = 0.45

DEFAULT_LIMIT = 3

# Trimmed to words that carry no signal in a symptom description. Domain nouns
# that look like noise ("error", "fail", "slow") are kept on purpose.
STOPWORDS = frozenset(
    """
    a about after all also am an and any are aren as at be because been before
    being but by can cannot could did do does doing done dont for from get gets
    getting had has have having he her here hers him his how i if in into is isnt
    it its just me might must my no nor not of off on once one only or other our out
    over own re same seem seems shall she should so some such than that the their
    them then there these they this those through to too under until up us very
    was we were what when where which while who whom why will with would you your
    doesnt didnt wasnt werent isnt arent havent hasnt hadnt wont wouldnt couldnt
    shouldnt cant
    """.split()
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_ATX_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]*(.*?)[ \t]*#*[ \t]*$")
_SETEXT_RE = re.compile(r"^[ \t]{0,3}(=+|-+)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class Decision(object):
    """One architecture decision record.

    ``number`` is the four-digit string from the filename (``"0007"``), or
    ``None`` when the filename does not conform. ``sections`` is the mapping
    returned by :func:`extract_sections`.
    """

    __slots__ = (
        "path",
        "root",
        "number",
        "slug",
        "id",
        "title",
        "type",
        "status",
        "summary",
        "symptoms",
        "supersedes",
        "superseded_by",
        "tags",
        "owners",
        "covers",
        "updated",
        "verified_at",
        "body",
        "sections",
        "frontmatter",
        "issues",
    )

    def __init__(self, path, root=DEFAULT_ROOT):
        self.path = path
        self.root = root
        self.number = None
        self.slug = None
        self.id = None
        self.title = None
        self.type = None
        self.status = None
        self.summary = None
        self.symptoms = []
        self.supersedes = []
        self.superseded_by = []
        self.tags = []
        self.owners = []
        self.covers = []
        self.updated = None
        self.verified_at = None
        self.body = ""
        self.sections = {SECTION_EXPECTED: "", SECTION_NOT_THIS: ""}
        self.frontmatter = None
        self.issues = []

    # -- accessors ---------------------------------------------------------

    @property
    def display_id(self):
        return self.id or self.path

    @property
    def expected(self):
        """The ``Expected behavior`` section, or ``""``."""
        return self.sections.get(SECTION_EXPECTED, "")

    @property
    def not_this(self):
        """The ``This is a bug, not this decision, if...`` section, or ``""``."""
        return self.sections.get(SECTION_NOT_THIS, "")

    @property
    def sort_key(self):
        return (self.number is None, self.number or "", self.path)

    def as_entry(self):
        """A JSON-ready dict, ordered the way the CLI prints it."""
        entry = [
            ("id", self.id),
            ("number", self.number),
            ("path", self.path),
            ("title", self.title),
            ("status", self.status),
            ("summary", self.summary),
            ("symptoms", list(self.symptoms)),
            ("supersedes", list(self.supersedes)),
            ("superseded_by", list(self.superseded_by)),
            ("tags", list(self.tags)),
            ("expected", self.expected),
            ("not_this", self.not_this),
        ]
        result = dict(entry)
        result["_order"] = [name for name, _ in entry]
        return result

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Decision(%r, status=%r)" % (self.display_id, self.status)


class Match(object):
    """One :func:`why` hit: a decision, its score, and *why* it scored."""

    __slots__ = ("decision", "score", "symptom", "terms", "fields")

    def __init__(self, decision, score, symptom=None, terms=None, fields=None):
        self.decision = decision
        self.score = score
        #: the single best-matching entry of ``decision.symptoms``, or None
        self.symptom = symptom
        #: the query terms that matched anywhere, in query order
        self.terms = list(terms or [])
        #: field name -> contribution, for explaining a ranking
        self.fields = dict(fields or {})

    @property
    def id(self):
        return self.decision.display_id

    def as_entry(self):
        entry = self.decision.as_entry()
        order = entry.pop("_order")
        entry["score"] = round(self.score, 4)
        entry["matched_symptom"] = self.symptom
        entry["matched_terms"] = list(self.terms)
        entry["_order"] = order + ["score", "matched_symptom", "matched_terms"]
        return entry

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Match(%r, score=%.3f)" % (self.decision.display_id, self.score)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load(repo_root, root=DEFAULT_ROOT):
    """Parse every decision record under ``repo_root/root``.

    Returns a list sorted by number then path. A missing directory yields
    ``[]`` -- a repository that has not adopted decision records is not an
    error. Content problems never raise; they land in ``decision.issues`` and
    are reported by :func:`validate`.
    """
    absolute_root = os.path.join(repo_root, root.replace("/", os.sep))
    if not os.path.isdir(absolute_root):
        return []

    found = []
    for dirpath, dirnames, filenames in os.walk(absolute_root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            if not filename.lower().endswith(MARKDOWN_SUFFIXES):
                continue
            absolute = os.path.join(dirpath, filename)
            relative = os.path.relpath(absolute, absolute_root).replace(os.sep, "/")
            found.append((absolute, root.rstrip("/") + "/" + relative))

    decisions = [parse_decision(absolute, rel, root=root) for absolute, rel in found]
    decisions.sort(key=lambda item: item.sort_key)
    return decisions


def parse_decision(path, rel_path=None, root=DEFAULT_ROOT):
    """Parse the file at *path* into a :class:`Decision`.

    *rel_path* is the repo-relative path used in lint messages; it defaults to
    *path*. Unreadable files come back as a Decision carrying one issue.
    """
    rel_path = rel_path if rel_path is not None else path
    decision = Decision(rel_path, root)
    _apply_filename(decision)

    try:
        parsed = fm_module.parse_file(path, rel_path)
    except Exception as exc:  # unreadable file, bad encoding
        decision.issues.append(fm_module.LintIssue(rel_path, None, str(exc)))
        return decision

    _apply_frontmatter(decision, parsed)
    return decision


def from_doc(doc, root=None):
    """Adapt an already-scanned document into a :class:`Decision`.

    Accepts anything with ``path`` and ``frontmatter`` attributes -- in
    practice a :class:`tools.gotdocs.index.Doc` -- so a caller that has already
    walked the roots does not pay to read every file twice. Duck-typed on
    purpose: this module does not import ``index``.
    """
    decision = Decision(doc.path, root if root is not None else getattr(doc, "root", DEFAULT_ROOT))
    _apply_filename(decision)
    parsed = getattr(doc, "frontmatter", None)
    if parsed is None:
        return decision
    _apply_frontmatter(decision, parsed)
    return decision


def _apply_filename(decision):
    filename = decision.path.rsplit("/", 1)[-1]
    matched = FILENAME_RE.match(filename)
    if matched:
        decision.number = matched.group(1)
        decision.slug = matched.group(2)


def _apply_frontmatter(decision, parsed):
    decision.frontmatter = parsed
    decision.issues.extend(parsed.issues)
    if not parsed.present:
        return

    decision.body = parsed.body
    decision.sections = extract_sections(parsed.body)

    for field in ("id", "title", "type", "status", "summary", "updated", "verified_at"):
        value = parsed.get_scalar(field)
        if value is not None:
            setattr(decision, field, value)

    for field in LIST_FIELDS:
        value = parsed.get_list(field)
        if value is None:
            # A scalar where a list belongs; validate() reports it. Treat a
            # lone scalar as a one-item list so `why` still has something.
            raw = parsed.get_scalar(field)
            value = [raw] if raw else []
        setattr(decision, field, [item for item in value if item != ""])


# ---------------------------------------------------------------------------
# section extraction
# ---------------------------------------------------------------------------


def extract_sections(body):
    """Pull the two load-bearing sections out of a decision record body.

    Returns ``{"expected": str, "not_this": str}``; a section that is absent
    comes back as ``""``. Both keys are always present.

    This is deliberately forgiving, because a heading a human typed slightly
    differently must not silently disable ``gotdocs why``. All of these are
    recognised as the same two headings:

    .. code-block:: text

        ## Expected behavior
        ### Expected Behaviour:
        # EXPECTED BEHAVIOR
        Expected behavior
        ----------------
        ## This is a bug, not this decision, if...
        ##### This is a bug, not this decision, if…
        ## this is a bug (not this decision) if:
        ## **Not this decision -- a bug -- if**

    Fenced code blocks are skipped, so a ``#`` inside a shell snippet cannot
    end a section early.
    """
    if not body:
        return {SECTION_EXPECTED: "", SECTION_NOT_THIS: ""}

    lines = body.splitlines()
    headings = []  # (index, level, key)
    fence = None  # the open fence's marker character
    fence_len = 0  # ...and its length; a shorter run does not close it

    for position, line in enumerate(lines):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            run = fence_match.group(1)
            marker = run[0]
            if fence is None:
                fence = marker
                fence_len = len(run)
                continue
            # CommonMark: a closing fence uses the same character, is at least
            # as long as the opening one, and carries no info string. Ignoring
            # the length let a ``` inside a ```` block close it, and every
            # heading after that vanished from the record - which showed up as
            # a lint error for a section that is plainly in the file.
            closes = (
                fence == marker
                and len(run) >= fence_len
                and line.strip() == marker * len(line.strip())
            )
            if closes:
                fence = None
                fence_len = 0
            continue
        if fence is not None:
            continue

        atx = _ATX_RE.match(line)
        if atx:
            headings.append((position, len(atx.group(1)), _heading_key(atx.group(2))))
            continue

        setext = _SETEXT_RE.match(line)
        if setext and position > 0:
            previous = lines[position - 1].strip()
            if previous and not _ATX_RE.match(lines[position - 1]):
                key = _heading_key(previous)
                # Only treat an underline as a heading when the text above it
                # is one of ours: otherwise a horizontal rule after a
                # paragraph would silently swallow the rest of the record.
                if key is not None:
                    level = 1 if setext.group(1)[0] == "=" else 2
                    headings.append((position - 1, level, key))

    sections = {SECTION_EXPECTED: "", SECTION_NOT_THIS: ""}
    for order, (position, level, key) in enumerate(headings):
        if key is None or sections.get(key):
            continue
        end = len(lines)
        for later_position, later_level, later_key in headings[order + 1 :]:
            # A shallower-or-equal heading ends the section, as in ordinary
            # markdown -- but so does either of *our* headings at any depth.
            # Authors mix levels ("### Expected behavior" then "##### This is
            # a bug...") and mean them as siblings, never as nested prose.
            if later_level <= level or later_key is not None:
                end = later_position
                break
        start = position + 1
        # A setext heading occupies two lines; skip its underline as well.
        if start < len(lines) and _SETEXT_RE.match(lines[start]) and not _ATX_RE.match(lines[position]):
            start += 1
        sections[key] = _trim_block(lines[start:end])

    return sections


def _heading_key(text):
    """Map heading text to ``"expected"``, ``"not_this"``, or ``None``."""
    normalized = _normalize_heading(text)
    if not normalized:
        return None

    if normalized == "expected" or normalized.startswith("expected behavio"):
        return SECTION_EXPECTED
    if normalized in ("what should happen", "what happens", "intended behavior", "intended behaviour"):
        return SECTION_EXPECTED

    if "bug" in normalized and (
        "not this decision" in normalized
        or "not the decision" in normalized
        or "not this record" in normalized
    ):
        return SECTION_NOT_THIS
    if normalized.startswith("this is a bug if") or normalized.startswith("this is a bug when"):
        return SECTION_NOT_THIS
    return None


def _normalize_heading(text):
    """Lowercase, strip markdown emphasis/punctuation/ellipses, collapse space."""
    if not text:
        return ""
    # Decompose so a typographic ellipsis, en/em dashes and smart quotes reduce
    # to something the punctuation strip below can see.
    # NFKD turns a typographic ellipsis into three dots; the split below then
    # erases those along with commas, colons, parens and markdown emphasis.
    text = unicodedata.normalize("NFKD", text)
    lowered = text.lower()
    collapsed = _WORD_SPLIT_RE.sub(" ", lowered)
    return " ".join(collapsed.split())


def _trim_block(lines):
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def tokenize(text):
    """Lowercase, strip punctuation, drop stopwords, light-stem. Order kept."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKD", text).lower()
    tokens = []
    for raw in _WORD_SPLIT_RE.split(normalized):
        if not raw or len(raw) < 2:
            continue
        if raw in STOPWORDS:
            continue
        stemmed = _stem(raw)
        if stemmed and stemmed not in STOPWORDS:
            tokens.append(stemmed)
    return tokens


def _stem(word):
    """A deliberately tiny suffix stripper: enough to match plurals."""
    if len(word) > 4 and (word.endswith("ies") or word.endswith("ied")):
        # retry / retries / retried must be one term: that trio is the single
        # most common way a symptom and a record disagree in wording.
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def why(query, decisions, limit=None):
    """Rank *decisions* against a free-text symptom description.

    Scoring is token overlap, weighted by field: ``symptoms`` highest, then
    ``title``, ``summary``, ``tags``. Each field contributes
    ``weight * (matched query terms / total query terms)``, so a short precise
    query cannot be beaten by a record that merely happens to be wordy. A query
    that appears verbatim inside one symptom earns :data:`PHRASE_BONUS` on top.

    Records with a score of zero are dropped. Ties break on
    ``decision.display_id`` so the ordering is stable across runs and machines.

    Returns a list of :class:`Match`, best first, truncated to *limit* when
    *limit* is not ``None``.
    """
    terms = tokenize(query)
    if not terms:
        return []
    unique_terms = []
    for term in terms:
        if term not in unique_terms:
            unique_terms.append(term)
    total = float(len(unique_terms))
    query_phrase = " ".join(terms)

    matches = []
    for decision in decisions:
        score = 0.0
        fields = {}
        hit_terms = []

        best_symptom = None
        best_symptom_score = 0.0
        for symptom in decision.symptoms:
            symptom_tokens = tokenize(symptom)
            overlap = [term for term in unique_terms if term in symptom_tokens]
            if not overlap:
                continue
            symptom_score = SYMPTOM_WEIGHT * (len(overlap) / total)
            if query_phrase and query_phrase in " ".join(symptom_tokens):
                symptom_score += PHRASE_BONUS
            if symptom_score > best_symptom_score or (
                symptom_score == best_symptom_score and best_symptom is None
            ):
                best_symptom_score = symptom_score
                best_symptom = symptom
            for term in overlap:
                if term not in hit_terms:
                    hit_terms.append(term)
        if best_symptom_score:
            score += best_symptom_score
            fields["symptoms"] = best_symptom_score

        for field, weight, value in (
            ("title", TITLE_WEIGHT, decision.title),
            ("summary", SUMMARY_WEIGHT, decision.summary),
            ("tags", TAG_WEIGHT, " ".join(decision.tags)),
        ):
            field_tokens = tokenize(value)
            if not field_tokens:
                continue
            overlap = [term for term in unique_terms if term in field_tokens]
            if not overlap:
                continue
            contribution = weight * (len(overlap) / total)
            score += contribution
            fields[field] = contribution
            for term in overlap:
                if term not in hit_terms:
                    hit_terms.append(term)

        if score <= 0.0:
            continue
        ordered_hits = [term for term in unique_terms if term in hit_terms]
        matches.append(Match(decision, score, best_symptom, ordered_hits, fields))

    matches.sort(key=lambda match: (-match.score, match.decision.display_id))
    matches = _drop_noise(matches, total)
    if limit is not None:
        matches = matches[: max(0, limit)]
    return matches


def _drop_noise(matches, total):
    """Strip the coincidental tail off a ranked ``why`` result.

    Two independent floors, both relative to the query rather than to an
    absolute score, so they behave the same for a terse query and a wordy one:

    * :data:`MIN_TERM_COVERAGE` - the record must overlap the query on a real
      fraction of its distinct terms. Sharing one word out of six is a
      coincidence.
    * :data:`RELATIVE_FLOOR` - the record must score a meaningful fraction of
      the leader's score.

    Both are applied to every match including the leader: if the best thing in
    the repository shares one word out of six with the symptom, then nothing
    was written down about it, and saying so is more useful than pointing at
    the least-irrelevant record.
    """
    if not matches or not total:
        return matches
    kept = [
        match for match in matches if (len(match.terms) / total) >= MIN_TERM_COVERAGE
    ]
    if not kept:
        return []
    top = kept[0].score
    if top <= 0.0:
        return kept
    return [match for match in kept if (match.score / top) >= RELATIVE_FLOOR]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def format_why(matches, query=None, limit=DEFAULT_LIMIT, total=None, full=False, width=80):
    """Render :func:`why` results as a compact, decisive block of text.

    Default shape -- top three, five lines each: id, title, status, the symptom
    that matched, and the two load-bearing sections, each clipped to one line.
    Nothing else from the record is printed; the path is there for a reader who
    wants the rest.

    *total* is the number of decision records searched, used only in the header
    line. *full* prints the two sections unclipped. Set *limit* to ``None`` to
    render every match.
    """
    shown = matches if limit is None else matches[: max(0, limit)]
    lines = []

    quoted = '"%s"' % (query,) if query else "that description"
    if not matches:
        lines.append("no decision matches %s%s." % (quoted, _searched(total)))
        lines.append("")
        lines.append("Nothing was written down that explains this. Treat it as unintended")
        lines.append("until proven otherwise, and consider recording the answer you find.")
        return "\n".join(lines) + "\n"

    singular = len(matches) == 1
    lines.append(
        "%d %s %s %s%s:"
        % (
            len(matches),
            "decision" if singular else "decisions",
            "matches" if singular else "match",
            quoted,
            _searched(total),
        )
    )

    for position, match in enumerate(shown, 1):
        decision = match.decision
        lines.append("")
        lines.append(
            "[%d] %s  (%s)  %s"
            % (position, decision.display_id, decision.status or "status unknown", decision.path)
        )
        if decision.title:
            lines.append("    %s" % (decision.title,))
        lines.append("    symptom:  %s" % (_one_line(match.symptom, width, full) or "-",))
        lines.append(
            "    expected: %s"
            % (
                _one_line(lead_claim(decision.expected, full, query), width, full)
                or _absent(),
            )
        )
        lines.append(
            "    bug if:   %s"
            % (
                _one_line(lead_claim(decision.not_this, full, query), width, full)
                or _absent(),
            )
        )

    hidden = len(matches) - len(shown)
    if hidden > 0:
        lines.append("")
        lines.append("%d further match%s scored lower." % (hidden, "" if hidden == 1 else "es"))

    return "\n".join(lines) + "\n"


def _searched(total):
    if not total:
        return ""
    return " (of %d searched)" % (total,)


def _absent():
    return "(not recorded -- this record cannot settle it)"


_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END_RE = re.compile(r"(?<![A-Z])[.!?](?=\s|$)")


def lead_claim(section, full=False, query=None):
    """Reduce an ``## Expected behavior`` section to one whole claim.

    The sections are bullet lists, so rendering the raw text puts a ``- `` in
    the output and then clips mid-bullet. Instead take one bullet (with its
    wrapped continuation lines), drop the list marker, and end at the first
    sentence boundary. The result is a statement that reads as a statement.

    Which bullet: the one that shares the most terms with *query*, falling back
    to the first. A record's sections carry one bullet per case, and the first
    is rarely the one the caller asked about - `0006` leads with "verify writes
    the stamp and exits 0" while the reader who typed "verified_at is ignored
    under --paths" needs the bullet six items down. Showing the first turned a
    correct routing decision into an answer that looked like it did not apply.
    Ties and no-overlap keep the first bullet, so behaviour without a query is
    unchanged.

    *full* returns the section untouched: ``--full`` means "show me the record".
    """
    if not section or full:
        return section
    bullets = _bullets(section)
    if not bullets:
        return ""
    chosen = bullets[0]
    if query:
        wanted = set(tokenize(query))
        if wanted:
            best = 0
            for bullet in bullets:
                overlap = len(wanted & set(tokenize(bullet)))
                if overlap > best:
                    best, chosen = overlap, bullet
    text = " ".join(chosen.split())
    found = _SENTENCE_END_RE.search(text)
    if found:
        text = text[: found.end()]
    return text


def _bullets(section):
    """Split a section into whole claims: one per top-level bullet.

    Wrapped continuation lines join their bullet. A section with no bullets at
    all is one claim ending at its first blank line, which is what the previous
    first-paragraph behaviour produced.
    """
    lines = section.split("\n")
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines:
        return []

    if not _BULLET_RE.match(lines[0]):
        claim = []
        for line in lines:
            if not line.strip():
                break
            claim.append(line.strip())
        return [" ".join(claim)] if claim else []

    claims = []
    current = None
    for line in lines:
        if _BULLET_RE.match(line):
            if current is not None:
                claims.append(" ".join(current))
            current = [_BULLET_RE.sub("", line, count=1).strip()]
            continue
        if current is None:
            continue
        if not line.strip():
            claims.append(" ".join(current))
            current = None
            continue
        if not line.startswith((" ", "\t")):
            # An unindented line under a bullet ends the list.
            claims.append(" ".join(current))
            current = None
            continue
        current.append(line.strip())
    if current is not None:
        claims.append(" ".join(current))
    return [claim for claim in claims if claim]


def _one_line(text, width, full=False):
    """Collapse *text* to a single line, clipped to *width* unless *full*."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if full or len(collapsed) <= width:
        return collapsed
    cut = collapsed[: width - 3]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip() + "..."


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate(decisions, root=DEFAULT_ROOT):
    """Lint a set of decision records. Returns a list of ``LintIssue``.

    Checked here, in the order reported per record:

    * ``type`` is ``decision`` and ``status`` is in :data:`DECISION_STATUSES`
    * the filename is ``NNNN-slug.md`` and ``id`` equals the filename stem
    * numbers are unique and contiguous from ``0001``
    * every ``supersedes`` / ``superseded_by`` id resolves to a record, and the
      link is bidirectional: if A supersedes B, B must name A back
    * an ``accepted`` record has non-empty ``symptoms`` and both body sections
    * a ``superseded`` record names its successor, and a record that names a
      successor is marked ``superseded``
    * a ``superseded_by`` chain reaches a record that is in force: a cycle, or a
      chain ending in a ``rejected`` record, retires a decision with no live
      replacement and is reported

    Frontmatter parse issues already attached to each record come first, so a
    caller can splice the result straight into its existing lint output.
    """
    issues = []
    add = issues.append

    by_id = {}
    for decision in decisions:
        if decision.id and decision.id not in by_id:
            by_id[decision.id] = decision

    seen_numbers = {}
    for decision in decisions:
        issues.extend(decision.issues)
        path = decision.path
        parsed = decision.frontmatter
        line_of = parsed.line_of if parsed is not None else (lambda key, default=None: default)
        end_line = parsed.end_line if parsed is not None else None
        if parsed is not None and not parsed.present:
            # A file with no frontmatter at all is already reported by the
            # parser; nothing below can say anything useful about it.
            continue

        # -- type ----------------------------------------------------------
        if decision.type is not None and decision.type != DECISION_TYPE:
            add(
                fm_module.LintIssue(
                    path,
                    line_of("type", end_line),
                    "a document under %r must have type %r, got %r"
                    % (root, DECISION_TYPE, decision.type),
                )
            )
        elif decision.type is None:
            add(fm_module.LintIssue(path, end_line, "missing required frontmatter field 'type'"))

        # -- status --------------------------------------------------------
        if decision.status is None:
            add(fm_module.LintIssue(path, end_line, "missing required frontmatter field 'status'"))
        elif decision.status not in DECISION_STATUSES:
            add(
                fm_module.LintIssue(
                    path,
                    line_of("status", end_line),
                    "unknown decision 'status' %r; expected one of %s"
                    % (decision.status, ", ".join(DECISION_STATUSES)),
                )
            )

        # -- filename and id ----------------------------------------------
        filename = path.rsplit("/", 1)[-1]
        if decision.number is None:
            add(
                fm_module.LintIssue(
                    path,
                    None,
                    "decision filename %r must be NNNN-slug.md, e.g. %s-%s.md"
                    % (filename, next_number_from(decisions), "some-slug"),
                )
            )
        else:
            stem = "%s-%s" % (decision.number, decision.slug)
            if decision.id is None:
                add(fm_module.LintIssue(path, end_line, "missing required frontmatter field 'id'"))
            elif decision.id != stem:
                add(
                    fm_module.LintIssue(
                        path,
                        line_of("id", end_line),
                        "'id' must equal the filename stem %r, got %r" % (stem, decision.id),
                    )
                )
            first = seen_numbers.get(decision.number)
            if first is not None:
                add(
                    fm_module.LintIssue(
                        path,
                        None,
                        "duplicate decision number %s; already used by %s"
                        % (decision.number, first),
                    )
                )
            else:
                seen_numbers[decision.number] = path

        # -- symptoms ------------------------------------------------------
        if parsed is not None and parsed.get_list("symptoms") is None:
            add(
                fm_module.LintIssue(
                    path, line_of("symptoms", end_line), "'symptoms' must be a list, not a scalar"
                )
            )
        if decision.status == "accepted" and not decision.symptoms:
            add(
                fm_module.LintIssue(
                    path,
                    line_of("symptoms", end_line),
                    "an accepted decision must list at least one entry under 'symptoms': "
                    "without one, `gotdocs why` can never surface it",
                )
            )

        # -- body sections -------------------------------------------------
        if decision.status == "accepted":
            if not decision.expected:
                add(
                    fm_module.LintIssue(
                        path, None, "missing an 'Expected behavior' section in the body"
                    )
                )
            if not decision.not_this:
                add(
                    fm_module.LintIssue(
                        path,
                        None,
                        "missing a 'This is a bug, not this decision, if...' section in the body",
                    )
                )

        # -- supersession --------------------------------------------------
        for field in ("supersedes", "superseded_by"):
            if parsed is not None and parsed.get_list(field) is None:
                add(
                    fm_module.LintIssue(
                        path, line_of(field, end_line), "%r must be a list, not a scalar" % (field,)
                    )
                )
            for target_id in getattr(decision, field):
                if target_id == decision.id:
                    add(
                        fm_module.LintIssue(
                            path, line_of(field, end_line), "%r names this decision itself" % (field,)
                        )
                    )
                    continue
                target = by_id.get(target_id)
                if target is None:
                    add(
                        fm_module.LintIssue(
                            path,
                            line_of(field, end_line),
                            "%r references unknown decision %r" % (field, target_id),
                        )
                    )
                    continue
                mirror = "superseded_by" if field == "supersedes" else "supersedes"
                if decision.id and decision.id not in getattr(target, mirror):
                    add(
                        fm_module.LintIssue(
                            path,
                            line_of(field, end_line),
                            "%r names %r, but %s does not list %r under %r"
                            % (field, target_id, target.path, decision.id, mirror),
                        )
                    )

        if decision.status == "superseded" and not decision.superseded_by:
            add(
                fm_module.LintIssue(
                    path,
                    line_of("status", end_line),
                    "a superseded decision must name its successor under 'superseded_by'",
                )
            )
        if decision.superseded_by and decision.status not in ("superseded", None):
            add(
                fm_module.LintIssue(
                    path,
                    line_of("status", end_line),
                    "names a successor under 'superseded_by' but status is %r; expected 'superseded'"
                    % (decision.status,),
                )
            )

        if decision.status == "superseded" and decision.superseded_by:
            issue = _chain_issue(decision, by_id, path, line_of("status", end_line))
            if issue is not None:
                add(issue)

    issues.extend(_numbering_issues(decisions, seen_numbers, root))
    return issues


# Statuses `why` will cite. A record outside this set is retired: it is not an
# answer, so a supersession chain that ends in one leaves the behavior it
# describes with no explanation anywhere.
IN_FORCE_STATUSES = ("proposed", "accepted")


def _chain_issue(decision, by_id, path, line):
    """Report a ``superseded_by`` chain that never reaches an in-force record.

    Bidirectionality alone lets two shapes through, and both retire a decision
    without a replacement anyone can find:

    * a cycle -- 0001 superseded by 0002, 0002 superseded by 0001
    * a chain ending in a retired record -- you supersede 0001 with 0002 and
      then 0002 is rejected, so the question 0001 answered has no live answer

    ``why`` excludes ``rejected`` and ``superseded``, so in both shapes the
    symptom is documented and the tool still says "nothing was written down".
    """
    seen = {decision.id}
    frontier = list(decision.superseded_by)
    reached = []
    looped = False
    while frontier:
        current = frontier.pop(0)
        if current == decision.id:
            looped = True
            continue
        if current in seen:
            continue
        seen.add(current)
        record = by_id.get(current)
        if record is None:
            # An unresolvable id is already reported as its own issue.
            return None
        if record.status in IN_FORCE_STATUSES:
            return None
        reached.append(current)
        if record.status == "superseded" and not record.superseded_by:
            # Already reported as "must name its successor".
            return None
        frontier.extend(record.superseded_by)

    if looped and not reached:
        detail = "the chain loops straight back to this record"
    elif looped:
        detail = "the chain loops back to this record through %s" % (
            ", ".join(sorted(reached)),
        )
    else:
        detail = "it ends at %s, which is not in force" % (", ".join(sorted(reached)),)
    return fm_module.LintIssue(
        path,
        line,
        "'superseded_by' never reaches a decision that is in force: %s. "
        "Point it at an accepted or proposed record, or reopen this one." % (detail,),
    )


def _numbering_issues(decisions, seen_numbers, root):
    """Report gaps in the ``0001..NNNN`` run, once, against the whole set."""
    if not seen_numbers:
        return []
    issues = []
    values = sorted(int(number) for number in seen_numbers)
    if values[0] != 1:
        issues.append(
            fm_module.LintIssue(
                root,
                None,
                "decision numbering must start at 0001; the lowest in use is %04d" % (values[0],),
            )
        )
    in_use = set(values)
    missing = [value for value in range(values[0], values[-1] + 1) if value not in in_use]
    if missing:
        issues.append(
            fm_module.LintIssue(
                root,
                None,
                "decision numbering is not contiguous: %s missing between %04d and %04d"
                % (", ".join("%04d" % value for value in missing), values[0], values[-1]),
            )
        )
    return issues


# ---------------------------------------------------------------------------
# numbering
# ---------------------------------------------------------------------------


def numbers(repo_root, root=DEFAULT_ROOT):
    """Every four-digit number already claimed under ``repo_root/root``.

    Reads filenames only -- no file is opened -- so a record whose frontmatter
    is broken still holds its number. Filenames that do not conform
    (``README.md``, ``draft.md``, ``12-old.md``, ``0007b-x.md``) are ignored.
    """
    absolute_root = os.path.join(repo_root, root.replace("/", os.sep))
    if not os.path.isdir(absolute_root):
        return []
    found = set()
    for dirpath, dirnames, filenames in os.walk(absolute_root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in filenames:
            matched = FILENAME_RE.match(filename)
            if matched:
                found.add(matched.group(1))
    return sorted(found)


def next_number(repo_root, root=DEFAULT_ROOT):
    """The four-digit number a new decision record should take.

    ``"0001"`` for an empty or absent directory, otherwise *one past the
    highest* number in use. Gaps are deliberately **not** filled: a number that
    was once ``0004`` is referenced from other records, commit messages and
    review threads, so handing it to a different decision would silently
    reroute those references. A gap is a lint finding to be closed by renaming
    the tail of the sequence, not by reuse.
    """
    return _next_from(numbers(repo_root, root))


def next_number_from(decisions):
    """Same rule as :func:`next_number`, applied to already-loaded records."""
    return _next_from([d.number for d in decisions if d.number])


def _next_from(existing):
    highest = 0
    for number in existing:
        if _NUMBER_RE.match(str(number)):
            highest = max(highest, int(number))
    return "%04d" % (highest + 1,)

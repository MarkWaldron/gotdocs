"""Static-site-generator portability rules for gotdocs documents.

The owner's requirement is blunt: *the host does not matter, the files must
render correctly on most static site generators*. The six targets gotdocs
promises are Docusaurus, MkDocs (+Material), Astro Starlight, Jekyll, Hugo and
plain GitHub.

This module is a pure analyser. It reads a document and returns
:class:`Issue` records -- ``(rule, path, line, severity, message, remediation)``
-- and never writes, never touches git and never exits. ``cli`` turns issues
into findings for ``lint --portability``; :mod:`tools.gotdocs.export` uses the
same markdown scanner to rewrite links.

Every rule is documented in :data:`RULES` with its default severity and the
targets it applies to:

* ``error``  the document will fail a build, or a link is already broken
* ``warn``   the document builds but renders differently than intended

**False positives are worse than missed issues here.** The scanner therefore
masks, before any rule runs:

* fenced code blocks (``` and ~~~, any info string, any fence length)
* indented code blocks (4 spaces or a tab after a blank line)
* inline code spans, including multi-line spans and doubled-backtick spans
* HTML comments

Only the surviving *prose* text is scanned for braces, tags and links, and link
destinations that carry a URL scheme (``https:``, ``mailto:``, ...) are never
treated as file paths.
"""

import os
import posixpath
import re

from . import frontmatter as fm_module

__all__ = [
    "Issue",
    "Rule",
    "RULES",
    "RULES_BY_NAME",
    "TARGETS",
    "MDX_TARGETS",
    "SEVERITIES",
    "DEFAULT_H1_IN_BODY",
    "RESERVED_KEYS",
    "GOTDOCS_KEYS",
    "rule_names",
    "rules_for_target",
    "h1_in_body_for",
    "check_text",
    "check_file",
    "check_doc_set",
    "filter_issues",
    "as_findings",
    "scan_markdown",
    "Scan",
    "Link",
]

TARGETS = ("docusaurus", "mkdocs", "starlight", "jekyll", "hugo", "github")

# Targets whose markdown is parsed as MDX, where a bare ``{`` or an unclosed
# tag is a build failure rather than a rendering quirk.
MDX_TARGETS = ("docusaurus", "starlight")

SEVERITIES = ("error", "warn")

# Default when the config carries no ``publish.h1_in_body``: documents keep a
# single ``# Title`` in the body, which is what plain GitHub needs.
DEFAULT_H1_IN_BODY = True

# Frontmatter keys gotdocs owns. Export maps or strips every one of them, so a
# collision on these is only worth reporting when the *value* misleads a target
# (see ``frontmatter-reserved-key`` and CORE_KEY_COLLISIONS below).
GOTDOCS_KEYS = (
    "id",
    "title",
    "type",
    "summary",
    "covers",
    "owners",
    "tags",
    "status",
    "updated",
    "verified_at",
)

# Frontmatter keys each target reserves. Sourced from each project's own
# frontmatter reference; a document that sets one of these by hand fights the
# generator (or the exporter) for control of the same field.
RESERVED_KEYS = {
    "docusaurus": (
        "description",
        "draft",
        "hide_table_of_contents",
        "hide_title",
        "id",
        "image",
        "keywords",
        "last_update",
        "pagination_label",
        "pagination_next",
        "pagination_prev",
        "sidebar_class_name",
        "sidebar_custom_props",
        "sidebar_label",
        "sidebar_position",
        "slug",
        "tags",
        "title",
        "toc_max_heading_level",
        "toc_min_heading_level",
        "unlisted",
    ),
    "mkdocs": (
        "date",
        "description",
        "hide",
        "icon",
        "search",
        "status",
        "subtitle",
        "tags",
        "template",
        "title",
    ),
    "starlight": (
        "banner",
        "description",
        "draft",
        "editUrl",
        "head",
        "hero",
        "lastUpdated",
        "next",
        "pagefind",
        "prev",
        "sidebar",
        "slug",
        "tableOfContents",
        "template",
        "title",
    ),
    "jekyll": (
        "author",
        "categories",
        "category",
        "date",
        "excerpt",
        "layout",
        "nav_order",
        "parent",
        "permalink",
        "published",
        "redirect_from",
        "sitemap",
        "tags",
        "title",
    ),
    "hugo": (
        "aliases",
        "cascade",
        "categories",
        "date",
        "description",
        "draft",
        "expiryDate",
        "headless",
        "keywords",
        "lastmod",
        "layout",
        "linkTitle",
        "menu",
        "outputs",
        "params",
        "publishDate",
        "resources",
        "series",
        "slug",
        "summary",
        "tags",
        "title",
        "translationKey",
        "type",
        "url",
        "weight",
    ),
    "github": (),
}

# gotdocs' own keys that a target reads with a *different* meaning. ``type:
# runbook`` makes Hugo look for a ``runbook`` content type and fail the build,
# so it is reported even though the exporter strips it.
CORE_KEY_COLLISIONS = {
    "type": ("hugo",),
}

_VOID_ELEMENTS = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "command",
        "embed",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)

# Absolute paths that are unmistakably somebody's filesystem rather than a site
# route. A leading "/" alone is ambiguous, so it gets its own (warn) rule.
_FS_ABSOLUTE_RE = re.compile(
    r"^/(Users|home|root|var|tmp|opt|etc|srv|private|mnt|media|Volumes|usr|Applications)(/|$)"
)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

_FENCE_OPEN_RE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_ATX_H1_RE = re.compile(r"^ {0,3}#(?!#)\s*(.*?)\s*#*\s*$")
_ATX_ANY_RE = re.compile(r"^ {0,3}(#{1,6})(\s|$)")
_SETEXT_H1_RE = re.compile(r"^ {0,3}=+\s*$")
_LIST_ITEM_RE = re.compile(r"^ {0,3}([-*+]|\d{1,9}[.)])(\s|$)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")

_INLINE_LINK_RE = re.compile(
    r"(!?)\[(?P<text>(?:[^\[\]\\]|\\.|\[[^\[\]]*\])*)\]\("
    r"\s*(?P<dest><[^<>\n]*>|[^\s()\n]*(?:\([^\s()\n]*\)[^\s()\n]*)*)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
)
_REF_DEF_RE = re.compile(
    r"^ {0,3}\[(?P<label>[^\]\n]+)\]:\s*(?P<dest><[^<>\n]*>|\S+)"
)
_HTML_ATTR_URL_RE = re.compile(
    r"<(?P<tag>img|a|source|iframe|video|audio|embed)\b[^>\n]*?"
    r"\b(?P<attr>src|href)\s*=\s*(?P<quote>[\"'])(?P<dest>[^\"'\n]*)(?P=quote)",
    re.IGNORECASE,
)
# A tag name is followed immediately by ">", "/>" or whitespace. Requiring that
# keeps autolinks (<https://example.com>, <user@example.com>) out of the tag
# rules entirely -- ":" and "@" simply are not tag syntax.
_TAG_RE = re.compile(
    r"<(?P<close>/?)(?P<name>[A-Za-z][A-Za-z0-9.\-]*)"
    r"(?P<attrs>(?:\s(?:\"[^\"\n]*\"|'[^'\n]*'|[^>'\"\n])*)?)>"
)
_TAGISH_START_RE = re.compile(r"<[A-Za-z]")
_IMAGE_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".ico",
    ".bmp",
    ".apng",
)


class Rule(object):
    """Metadata for one portability rule."""

    __slots__ = ("name", "severity", "targets", "description")

    def __init__(self, name, severity, targets, description):
        self.name = name
        self.severity = severity
        self.targets = tuple(targets)
        self.description = description

    def as_dict(self):
        return {
            "rule": self.name,
            "severity": self.severity,
            "targets": list(self.targets),
            "description": self.description,
        }

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Rule(%r, %r)" % (self.name, self.severity)


RULES = (
    Rule(
        "link-target-missing",
        "error",
        TARGETS,
        "a relative link points at a file that does not exist in the repository",
    ),
    Rule(
        "link-case-mismatch",
        "error",
        TARGETS,
        "a relative link differs in case from the file on disk; it resolves on "
        "macOS/Windows and 404s on a Linux build",
    ),
    Rule(
        "link-absolute-path",
        "error",
        TARGETS,
        "a link is an absolute filesystem path (/Users/..., C:\\..., file://)",
    ),
    Rule(
        "link-escapes-repo",
        "error",
        TARGETS,
        "a relative link resolves above the repository root, so it cannot be published",
    ),
    Rule(
        "image-missing",
        "error",
        TARGETS,
        "a referenced image does not exist in the repository",
    ),
    Rule(
        "mdx-unclosed-tag",
        "error",
        MDX_TARGETS,
        "an HTML-ish tag in prose is never closed; MDX parses it as JSX and fails the build",
    ),
    Rule(
        "mdx-bare-tag",
        "warn",
        MDX_TARGETS,
        "a capitalized tag in prose is parsed by MDX as an undefined React component",
    ),
    Rule(
        "mdx-brace",
        "warn",
        MDX_TARGETS,
        "a '{' or '}' in prose is parsed by MDX as a JavaScript expression",
    ),
    Rule(
        "mdx-html-comment",
        "warn",
        MDX_TARGETS,
        "an HTML comment is not valid MDX; use {/* ... */} or move it into frontmatter",
    ),
    Rule(
        "link-site-absolute",
        "warn",
        TARGETS,
        "a root-relative link ('/guide') resolves against the site root, so it "
        "breaks when the file is read on GitHub or under a base path",
    ),
    Rule(
        "fence-language-missing",
        "warn",
        TARGETS,
        "a fenced code block has no language tag, so it is not highlighted",
    ),
    Rule(
        "code-block-tab-indent",
        "warn",
        TARGETS,
        "a code block is indented with tabs; tab width differs per generator, "
        "use a fenced block",
    ),
    Rule(
        "h1-count",
        "warn",
        TARGETS,
        "the number of H1 headings in the body does not match publish.h1_in_body",
    ),
    Rule(
        "document-unreadable",
        "error",
        TARGETS,
        "the document could not be read as UTF-8 text, so nothing can publish it",
    ),
    Rule(
        "frontmatter-reserved-key",
        "warn",
        TARGETS,
        "a frontmatter key is reserved by a target generator and will be "
        "reinterpreted or overwritten",
    ),
)

RULES_BY_NAME = dict((rule.name, rule) for rule in RULES)


class Issue(object):
    """One portability problem, anchored at ``path:line``."""

    __slots__ = ("rule", "path", "line", "severity", "message", "remediation", "targets")

    def __init__(self, rule, path, line, message, remediation, severity=None, targets=None):
        self.rule = rule
        self.path = path
        self.line = line
        self.message = message
        self.remediation = remediation
        known = RULES_BY_NAME.get(rule)
        self.severity = severity or (known.severity if known else "warn")
        if targets is not None:
            self.targets = tuple(targets)
        else:
            self.targets = known.targets if known else TARGETS

    def located(self):
        if self.line is None:
            return "%s: %s" % (self.path, self.message)
        return "%s:%d: %s" % (self.path, self.line, self.message)

    def as_dict(self):
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "remediation": self.remediation,
            "targets": list(self.targets),
        }

    def sort_key(self):
        return (self.path or "", self.line or 0, self.rule, self.message)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Issue(%r, %r)" % (self.rule, self.located())


def rule_names():
    """Every rule name, in report order."""
    return [rule.name for rule in RULES]


def rules_for_target(target):
    """The rules that apply to *target*."""
    return [rule for rule in RULES if target in rule.targets]


def h1_in_body_for(config):
    """Read ``publish.h1_in_body`` from *config*, defaulting to True.

    The publish section is optional and is owned by the config loader, so this
    accepts a :class:`~tools.gotdocs.config.Config`, a plain dict, or None
    without ever raising.
    """
    if config is None:
        return DEFAULT_H1_IN_BODY
    publish = getattr(config, "publish", None)
    if publish is None and isinstance(config, dict):
        publish = config.get("publish")
    if isinstance(publish, dict):
        value = publish.get("h1_in_body", DEFAULT_H1_IN_BODY)
    else:
        value = getattr(publish, "h1_in_body", DEFAULT_H1_IN_BODY)
    if value is None:
        return DEFAULT_H1_IN_BODY
    return bool(value)


def filter_issues(issues, severity=None, rules=None, targets=None):
    """Narrow a list of issues by severity, rule name and/or target."""
    selected = list(issues)
    if severity is not None:
        selected = [issue for issue in selected if issue.severity == severity]
    if rules is not None:
        wanted = set(rules)
        selected = [issue for issue in selected if issue.rule in wanted]
    if targets is not None:
        wanted = set(targets)
        selected = [issue for issue in selected if wanted & set(issue.targets)]
    return selected


def as_findings(issues, kind="portability"):
    """Adapt issues to :class:`~tools.gotdocs.check.Finding` for the reporters."""
    from .check import Finding

    findings = []
    for issue in issues:
        findings.append(
            Finding(
                kind,
                issue.path,
                "%s: %s (%s)" % (_location(issue), issue.message, issue.rule),
                issue.remediation,
                doc_id=None,
            )
        )
    return findings


def _location(issue):
    if issue.line is None:
        return issue.path or ""
    return "%s:%d" % (issue.path or "", issue.line)


# ---------------------------------------------------------------------------
# markdown scanning
# ---------------------------------------------------------------------------


class Link(object):
    """One link or image reference found in prose.

    ``start``/``end`` are offsets of the destination inside the *document*
    text, which is what :mod:`tools.gotdocs.export` needs to rewrite it without
    re-parsing.
    """

    __slots__ = ("dest", "line", "start", "end", "is_image", "bracketed", "kind")

    def __init__(self, dest, line, start, end, is_image=False, bracketed=False, kind="inline"):
        self.dest = dest
        self.line = line
        self.start = start
        self.end = end
        self.is_image = is_image
        self.bracketed = bracketed
        self.kind = kind

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Link(%r, line=%r, image=%r)" % (self.dest, self.line, self.is_image)


class Scan(object):
    """The result of :func:`scan_markdown`."""

    __slots__ = (
        "text",
        "masked",
        "lines",
        "line_kinds",
        "line_starts",
        "links",
        "fences",
        "tab_blocks",
        "h1_lines",
        "comments",
        "offset",
    )

    def __init__(self, **kwargs):
        self.text = kwargs["text"]
        self.masked = kwargs["masked"]
        self.lines = kwargs["lines"]
        self.line_kinds = kwargs["line_kinds"]
        self.line_starts = kwargs["line_starts"]
        self.links = kwargs["links"]
        self.fences = kwargs["fences"]
        self.tab_blocks = kwargs["tab_blocks"]
        self.h1_lines = kwargs["h1_lines"]
        self.comments = kwargs["comments"]
        self.offset = kwargs.get("offset", 0)

    def line_of(self, offset):
        """The 1-based *file* line containing character *offset* of the body."""
        return _line_of(self.line_starts, offset) + self.offset


def scan_markdown(text, line_offset=0):
    """Segment *text* into code and prose, and collect links, fences and H1s.

    *line_offset* is added to every reported line number, so a caller that
    passes only a document body can still report absolute file lines.

    Returns a :class:`Scan`. The returned ``masked`` string has the same length
    as ``text`` -- every code character replaced by a space -- so offsets from
    either string index into the other.
    """
    lines = text.splitlines(True)
    line_starts = []
    position = 0
    for line in lines:
        line_starts.append(position)
        position += len(line)

    kinds = ["prose"] * len(lines)
    fences = []
    tab_blocks = []

    index = 0
    total = len(lines)
    fence_char = None
    fence_len = 0
    fence_start = None
    while index < total:
        raw = _strip_eol(lines[index])
        if fence_char is not None:
            kinds[index] = "code"
            stripped = raw.strip()
            if (
                stripped
                and stripped[0] == fence_char
                and stripped == stripped[0] * len(stripped)
                and len(stripped) >= fence_len
                and len(raw) - len(raw.lstrip(" ")) <= 3
            ):
                fence_char = None
                fence_len = 0
                fence_start = None
            index += 1
            continue

        match = _FENCE_OPEN_RE.match(raw)
        if match is not None:
            fence = match.group("fence")
            info = match.group("info")
            if fence[0] == "`" and "`" in info:
                # An inline code span such as ```a``` on its own line, not a fence.
                pass
            else:
                fence_char = fence[0]
                fence_len = len(fence)
                fence_start = index
                kinds[index] = "code"
                fences.append(
                    {
                        "line": index + 1 + line_offset,
                        "info": info.strip(),
                        "fence": fence,
                    }
                )
                index += 1
                continue

        # Indented code block: 4 spaces or a tab, and it cannot interrupt a
        # paragraph, so the previous line must be blank (or the file start) and
        # the block must not be list-item continuation.
        if _is_indented_code_start(lines, kinds, index):
            block_start = index
            uses_tab = False
            while index < total:
                current = _strip_eol(lines[index])
                if current.strip() == "":
                    # A blank line inside an indented block only continues it
                    # when an indented line follows.
                    look = index + 1
                    while look < total and _strip_eol(lines[look]).strip() == "":
                        look += 1
                    if look < total and _indent_width(_strip_eol(lines[look])) >= 4:
                        kinds[index] = "code"
                        index += 1
                        continue
                    break
                if _indent_width(current) < 4:
                    break
                if current[:1] == "\t":
                    uses_tab = True
                kinds[index] = "code"
                index += 1
            if uses_tab:
                tab_blocks.append(block_start + 1 + line_offset)
            continue

        index += 1

    if fence_char is not None and fence_start is not None:
        # An unterminated fence swallows the rest of the file; that is a
        # markdown bug in its own right but it is reported by the fence rules
        # below, not here.
        pass

    # Mask code lines, then inline spans and comments across the prose stream.
    buffer = []
    for position, line in enumerate(lines):
        if kinds[position] == "code":
            buffer.append(_blank_like(line))
        else:
            buffer.append(line)
    masked = "".join(buffer)
    masked, comments = _mask_comments(masked, line_starts, line_offset)
    masked = _mask_code_spans(masked)

    links = _collect_links(masked, lines, kinds, line_starts, line_offset)
    h1_lines = _collect_h1_lines(lines, kinds, line_offset)

    return Scan(
        text=text,
        masked=masked,
        lines=lines,
        line_kinds=kinds,
        line_starts=line_starts,
        links=links,
        fences=fences,
        tab_blocks=tab_blocks,
        h1_lines=h1_lines,
        comments=comments,
        offset=line_offset,
    )


def _strip_eol(line):
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def _blank_like(line):
    """Same length as *line*, all spaces except the line ending."""
    body = _strip_eol(line)
    return " " * len(body) + line[len(body) :]


def _indent_width(line):
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4 - (width % 4)
        else:
            break
    return width


def _is_indented_code_start(lines, kinds, index):
    current = _strip_eol(lines[index])
    if current.strip() == "":
        return False
    if _indent_width(current) < 4:
        return False
    # Previous line must be blank, and the last non-blank line must not be a
    # list item or a table row (both legitimately carry indented continuations).
    previous = index - 1
    if previous >= 0 and _strip_eol(lines[previous]).strip() != "":
        return False
    while previous >= 0 and _strip_eol(lines[previous]).strip() == "":
        previous -= 1
    if previous >= 0:
        anchor = _strip_eol(lines[previous])
        if kinds[previous] == "code":
            return False
        if _LIST_ITEM_RE.match(anchor) or _TABLE_ROW_RE.match(anchor):
            return False
        if _indent_width(anchor) >= 4:
            return False
    return True


def _mask_comments(text, line_starts, line_offset):
    """Blank out ``<!-- ... -->`` spans; return ``(masked, comment_lines)``."""
    out = list(text)
    comments = []
    position = 0
    length = len(text)
    while True:
        start = text.find("<!--", position)
        if start == -1:
            break
        end = text.find("-->", start + 4)
        if end == -1:
            end = length
        else:
            end += 3
        comments.append(_line_of(line_starts, start) + line_offset)
        for offset in range(start, min(end, length)):
            if out[offset] != "\n" and out[offset] != "\r":
                out[offset] = " "
        position = end
    return "".join(out), comments


def _mask_code_spans(text):
    """Blank out inline code spans, honouring CommonMark backtick-run matching."""
    out = list(text)
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "`":
            index += 1
            continue
        run_start = index
        while index < length and text[index] == "`":
            index += 1
        run = index - run_start
        # Find a closing run of exactly the same length.
        search = index
        closing = -1
        while search < length:
            if text[search] != "`":
                search += 1
                continue
            close_start = search
            while search < length and text[search] == "`":
                search += 1
            if search - close_start == run:
                closing = close_start
                break
        if closing == -1:
            continue  # literal backticks, not a span
        for offset in range(run_start, closing + run):
            if out[offset] not in ("\n", "\r"):
                out[offset] = " "
        index = closing + run
    return "".join(out)


def _line_of(line_starts, offset):
    low = 0
    high = len(line_starts) - 1
    if high < 0:
        return 1
    while low < high:
        middle = (low + high + 1) // 2
        if line_starts[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low + 1


def _collect_links(masked, lines, kinds, line_starts, line_offset):
    links = []
    for match in _INLINE_LINK_RE.finditer(masked):
        dest = match.group("dest")
        start = match.start("dest")
        end = match.end("dest")
        bracketed = dest.startswith("<") and dest.endswith(">")
        if bracketed:
            dest = dest[1:-1]
            start += 1
            end -= 1
        links.append(
            Link(
                dest,
                _line_of(line_starts, match.start()) + line_offset,
                start,
                end,
                is_image=bool(match.group(1)),
                bracketed=bracketed,
                kind="inline",
            )
        )

    for match in _HTML_ATTR_URL_RE.finditer(masked):
        tag = match.group("tag").lower()
        attr = match.group("attr").lower()
        links.append(
            Link(
                match.group("dest"),
                _line_of(line_starts, match.start()) + line_offset,
                match.start("dest"),
                match.end("dest"),
                is_image=(tag in ("img", "source") or attr == "src"),
                kind="html",
            )
        )

    # Reference definitions live at the start of a prose line.
    for position, line in enumerate(lines):
        if kinds[position] == "code":
            continue
        base = line_starts[position]
        segment = masked[base : base + len(line)]
        match = _REF_DEF_RE.match(_strip_eol(segment))
        if match is None:
            continue
        dest = match.group("dest")
        start = base + match.start("dest")
        end = base + match.end("dest")
        bracketed = dest.startswith("<") and dest.endswith(">")
        if bracketed:
            dest = dest[1:-1]
            start += 1
            end -= 1
        links.append(
            Link(
                dest,
                position + 1 + line_offset,
                start,
                end,
                is_image=_looks_like_image(dest),
                bracketed=bracketed,
                kind="reference",
            )
        )

    links.sort(key=lambda link: link.start)
    return links


def _looks_like_image(dest):
    path = dest.split("#", 1)[0].split("?", 1)[0].lower()
    return path.endswith(_IMAGE_SUFFIXES)


def _collect_h1_lines(lines, kinds, line_offset):
    found = []
    for position, line in enumerate(lines):
        if kinds[position] == "code":
            continue
        stripped = _strip_eol(line)
        if _ATX_H1_RE.match(stripped) and _indent_width(stripped) < 4:
            found.append(position + 1 + line_offset)
            continue
        if _SETEXT_H1_RE.match(stripped) and position > 0:
            previous = _strip_eol(lines[position - 1])
            if (
                kinds[position - 1] != "code"
                and previous.strip() != ""
                and not _ATX_ANY_RE.match(previous)
                and not _LIST_ITEM_RE.match(previous)
                and _indent_width(previous) < 4
            ):
                found.append(position + line_offset)
    return found


# ---------------------------------------------------------------------------
# destination classification
# ---------------------------------------------------------------------------


def _split_target(dest):
    """Split a destination into ``(path, suffix)`` where suffix is #anchor/?query."""
    cut = len(dest)
    for char in ("#", "?"):
        position = dest.find(char)
        if position != -1:
            cut = min(cut, position)
    return dest[:cut], dest[cut:]


def _percent_decode(text):
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "%" and index + 2 < length:
            hexes = text[index + 1 : index + 3]
            if len(hexes) == 2 and all(c in "0123456789abcdefABCDEF" for c in hexes):
                out.append(chr(int(hexes, 16)))
                index += 3
                continue
        out.append(char)
        index += 1
    return "".join(out)


class _Resolver(object):
    """Case-exact existence checks with a per-directory listing cache.

    ``os.path.exists`` is case-insensitive on macOS and Windows, so a link to
    ``Architecture.md`` passes locally and 404s on a Linux build. Every lookup
    here goes through the real directory listing.
    """

    def __init__(self, repo_root):
        self.repo_root = repo_root
        self._listings = {}

    def _listing(self, rel_dir):
        if rel_dir in self._listings:
            return self._listings[rel_dir]
        absolute = self.repo_root
        if rel_dir:
            absolute = os.path.join(self.repo_root, rel_dir.replace("/", os.sep))
        try:
            names = set(os.listdir(absolute))
        except (IOError, OSError):
            names = None
        self._listings[rel_dir] = names
        return names

    def check(self, rel_path):
        """Return ``"ok"``, ``"case"``, or ``"missing"`` for a repo-relative path."""
        if rel_path in ("", "."):
            return "ok"
        parts = rel_path.split("/")
        prefix = ""
        for part in parts:
            if part in ("", "."):
                continue
            listing = self._listing(prefix)
            if listing is None:
                return "missing"
            if part in listing:
                prefix = part if not prefix else prefix + "/" + part
                continue
            lowered = part.lower()
            for name in listing:
                if name.lower() == lowered:
                    return "case"
            return "missing"
        return "ok"

    def actual(self, rel_path):
        """The on-disk spelling of *rel_path*, best effort."""
        parts = [part for part in rel_path.split("/") if part not in ("", ".")]
        prefix = ""
        resolved = []
        for part in parts:
            listing = self._listing(prefix)
            if listing is None:
                return None
            chosen = None
            if part in listing:
                chosen = part
            else:
                lowered = part.lower()
                for name in sorted(listing):
                    if name.lower() == lowered:
                        chosen = name
                        break
            if chosen is None:
                return None
            resolved.append(chosen)
            prefix = chosen if not prefix else prefix + "/" + chosen
        return "/".join(resolved)


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------


def check_text(
    text,
    path,
    repo_root=None,
    h1_in_body=DEFAULT_H1_IN_BODY,
    targets=TARGETS,
    resolver=None,
):
    """Check one document given as text. Returns a sorted list of :class:`Issue`.

    *path* is the repo-relative path used both in messages and to resolve
    relative links. When *repo_root* is None the existence rules are skipped
    (there is nothing to resolve against) and every other rule still runs.
    """
    targets = tuple(targets) if targets else TARGETS
    parsed = fm_module.parse_text(text, path)
    body_offset = parsed.end_line if parsed.present and parsed.end_line else 0
    body = parsed.body if parsed.present else text
    scan = scan_markdown(body, line_offset=body_offset)

    issues = []
    if resolver is None and repo_root is not None:
        resolver = _Resolver(repo_root)

    issues.extend(_link_issues(scan, path, resolver))
    issues.extend(_mdx_issues(scan, path))
    issues.extend(_fence_issues(scan, path))
    issues.extend(_h1_issues(scan, path, parsed, h1_in_body))
    issues.extend(_frontmatter_issues(parsed, path, targets))

    issues = [issue for issue in issues if set(issue.targets) & set(targets)]
    issues.sort(key=lambda issue: issue.sort_key())
    return issues


def check_file(repo_root, rel_path, h1_in_body=DEFAULT_H1_IN_BODY, targets=TARGETS, resolver=None):
    """Check the document at ``repo_root/rel_path``."""
    absolute = os.path.join(repo_root, rel_path.replace("/", os.sep))
    text = fm_module.read_text(absolute)
    return check_text(
        text,
        rel_path,
        repo_root=repo_root,
        h1_in_body=h1_in_body,
        targets=targets,
        resolver=resolver,
    )


def check_doc_set(repo_root, config, doc_set=None, targets=None, h1_in_body=None):
    """Check every document in *doc_set* (scanning the roots when not given).

    This is the entry point behind ``gotdocs lint --portability``.
    """
    from . import index as index_module

    if doc_set is None:
        doc_set = index_module.scan(repo_root, config)
    if h1_in_body is None:
        h1_in_body = h1_in_body_for(config)
    targets = tuple(targets) if targets else TARGETS

    resolver = _Resolver(repo_root)
    issues = []
    for doc in doc_set.docs:
        try:
            issues.extend(
                check_file(
                    repo_root,
                    doc.path,
                    h1_in_body=h1_in_body,
                    targets=targets,
                    resolver=resolver,
                )
            )
        except Exception as exc:  # unreadable file: reported by lint already
            issues.append(
                Issue(
                    "document-unreadable",
                    doc.path,
                    None,
                    "cannot read document: %s" % (exc,),
                    "fix or remove %s" % (doc.path,),
                )
            )
    issues.sort(key=lambda issue: issue.sort_key())
    return issues


def _link_issues(scan, path, resolver):
    issues = []
    doc_dir = posixpath.dirname(path or "")
    for link in scan.links:
        raw = link.dest.strip()
        if raw == "":
            continue
        target, _suffix = _split_target(raw)
        rule = "image-missing" if link.is_image else "link-target-missing"

        if target == "":
            continue  # pure anchor or query
        if target.startswith("#"):
            continue
        if raw.startswith("//"):
            continue  # protocol relative
        if _WINDOWS_ABSOLUTE_RE.match(target):
            issues.append(_absolute_issue(path, link, raw))
            continue
        if target.startswith("~/") or target == "~":
            issues.append(_absolute_issue(path, link, raw))
            continue
        scheme = _SCHEME_RE.match(target)
        if scheme is not None:
            if target.lower().startswith("file:"):
                issues.append(_absolute_issue(path, link, raw))
            continue
        if target.startswith("/"):
            if _FS_ABSOLUTE_RE.match(target):
                issues.append(_absolute_issue(path, link, raw))
                continue
            issues.append(
                Issue(
                    "link-site-absolute",
                    path,
                    link.line,
                    "root-relative link %r only resolves at a site root" % (raw,),
                    "use a path relative to %s (for example ../%s) so the link "
                    "works on GitHub and under a base path"
                    % (path, target.lstrip("/")),
                )
            )
            continue

        decoded = _percent_decode(target)
        if decoded.startswith("\\") or "\\" in decoded and "/" not in decoded:
            # Windows separators never resolve on a Linux build.
            issues.append(_absolute_issue(path, link, raw))
            continue

        resolved = posixpath.normpath(posixpath.join(doc_dir, decoded))
        if resolved == "." or resolved == "":
            continue
        if resolved.startswith("../") or resolved == "..":
            issues.append(
                Issue(
                    "link-escapes-repo",
                    path,
                    link.line,
                    "link %r resolves to %r, outside the repository" % (raw, resolved),
                    "point the link at a file inside the repository, or use an https:// URL",
                )
            )
            continue

        if resolver is None:
            continue
        state = resolver.check(resolved)
        if state == "ok":
            continue
        if state == "case":
            actual = resolver.actual(resolved) or resolved
            issues.append(
                Issue(
                    "link-case-mismatch",
                    path,
                    link.line,
                    "link %r resolves to %r only on a case-insensitive filesystem; "
                    "the file on disk is %r" % (raw, resolved, actual),
                    "correct the capitalization to match %s" % (actual,),
                )
            )
            continue
        if rule == "image-missing":
            issues.append(
                Issue(
                    "image-missing",
                    path,
                    link.line,
                    "image %r does not exist (resolved to %r)" % (raw, resolved),
                    "add the image at %s, or remove the reference" % (resolved,),
                )
            )
        else:
            issues.append(
                Issue(
                    "link-target-missing",
                    path,
                    link.line,
                    "link %r does not exist (resolved to %r)" % (raw, resolved),
                    "point the link at an existing file, or create %s" % (resolved,),
                )
            )
    return issues


def _absolute_issue(path, link, raw):
    return Issue(
        "link-absolute-path",
        path,
        link.line,
        "link %r is an absolute filesystem path" % (raw,),
        "replace it with a path relative to %s, or an https:// URL" % (path,),
    )


def _mdx_issues(scan, path):
    issues = []
    masked = scan.masked

    for line in scan.comments:
        issues.append(
            Issue(
                "mdx-html-comment",
                path,
                line,
                "HTML comments are not valid MDX",
                "use {/* ... */}, or move the note into frontmatter",
            )
        )

    # Braces: one issue per line, pointing at the first occurrence.
    seen_lines = set()
    for index, char in enumerate(masked):
        if char not in "{}":
            continue
        line = scan.line_of(index)
        if line in seen_lines:
            continue
        seen_lines.add(line)
        issues.append(
            Issue(
                "mdx-brace",
                path,
                line,
                "%r in prose is parsed by MDX as a JavaScript expression" % (char,),
                "wrap it in backticks (`%s`), or escape it as \\%s" % (char, char),
            )
        )

    # Tags.
    stack = []
    consumed = set()
    for match in _TAG_RE.finditer(masked):
        consumed.add(match.start())
        name = match.group("name")
        line = scan.line_of(match.start())
        if match.group("close"):
            lowered = name.lower()
            for position in range(len(stack) - 1, -1, -1):
                if stack[position][0] == lowered:
                    del stack[position:]
                    break
            else:
                issues.append(
                    Issue(
                        "mdx-unclosed-tag",
                        path,
                        line,
                        "closing tag </%s> has no matching opening tag" % (name,),
                        "remove it, or open <%s> earlier in the document" % (name,),
                    )
                )
            continue
        if name[0].isupper():
            issues.append(
                Issue(
                    "mdx-bare-tag",
                    path,
                    line,
                    "<%s> is parsed by MDX as a React component, which is not "
                    "imported here" % (name,),
                    "wrap it in backticks (`<%s>`) or a fenced code block" % (name,),
                )
            )
            continue
        if _is_self_closing(match.group(0)) or name.lower() in _VOID_ELEMENTS:
            continue
        stack.append((name.lower(), line, name))

    for lowered, line, name in stack:
        issues.append(
            Issue(
                "mdx-unclosed-tag",
                path,
                line,
                "<%s> is never closed" % (name,),
                "add </%s>, write it as <%s />, or wrap it in backticks" % (name, name),
            )
        )

    # Tag-ish starts that never reach a '>' on their line.
    for match in _TAGISH_START_RE.finditer(masked):
        start = match.start()
        if start in consumed:
            continue
        line_end = masked.find("\n", start)
        if line_end == -1:
            line_end = len(masked)
        if ">" in masked[start:line_end]:
            continue  # a full tag started later on the line, or punctuation
        issues.append(
            Issue(
                "mdx-unclosed-tag",
                path,
                scan.line_of(start),
                "%r opens a tag that is never terminated with '>'"
                % (masked[start : min(start + 24, line_end)].strip(),),
                "close the tag, or wrap the text in backticks so MDX leaves it alone",
            )
        )

    return issues


def _is_self_closing(tag):
    """True for ``<br/>`` and ``<Foo attr="x" />``."""
    return tag[:-1].rstrip().endswith("/")


def _fence_issues(scan, path):
    issues = []
    for fence in scan.fences:
        info = fence["info"]
        if info:
            continue
        issues.append(
            Issue(
                "fence-language-missing",
                path,
                fence["line"],
                "fenced code block has no language tag",
                "add a language after the fence (```sh, ```python, ```text)",
            )
        )
    for line in scan.tab_blocks:
        issues.append(
            Issue(
                "code-block-tab-indent",
                path,
                line,
                "code block is indented with tabs; tab width differs per generator",
                "convert it to a fenced code block (```)",
            )
        )
    return issues


def _h1_issues(scan, path, parsed, h1_in_body):
    count = len(scan.h1_lines)
    anchor = parsed.end_line if parsed.present and parsed.end_line else 1
    if h1_in_body:
        if count == 1:
            return []
        if count == 0:
            return [
                Issue(
                    "h1-count",
                    path,
                    anchor,
                    "publish.h1_in_body is true but the body has no '# ' heading",
                    "add a single '# Title' as the first heading of the body",
                )
            ]
        return [
            Issue(
                "h1-count",
                path,
                scan.h1_lines[1],
                "publish.h1_in_body is true but the body has %d '# ' headings; "
                "generators use the first one as the page title" % (count,),
                "demote the extra headings to '## '",
            )
        ]
    if count == 0:
        return []
    return [
        Issue(
            "h1-count",
            path,
            scan.h1_lines[0],
            "publish.h1_in_body is false but the body has %d '# ' heading%s, which "
            "renders twice next to the frontmatter title" % (count, "" if count == 1 else "s"),
            "remove the body H1, or demote it to '## '",
        )
    ]


def _frontmatter_issues(parsed, path, targets):
    issues = []
    if not parsed.present:
        return issues
    core = set(GOTDOCS_KEYS)
    # A gotdocs key that a target reinterprets is only worth reporting when the
    # caller asked about that target specifically: `type` is required by
    # gotdocs and stripped by `export --target hugo`, so reporting it on every
    # document of every default run would be pure noise.
    narrowed = set(targets) != set(TARGETS)
    for key in parsed.keys():
        line = parsed.line_of(key, parsed.end_line)
        if key in core:
            collides = CORE_KEY_COLLISIONS.get(key)
            if not collides or not narrowed:
                continue
            hit = [name for name in collides if name in targets]
            if not hit:
                continue
            issues.append(
                Issue(
                    "frontmatter-reserved-key",
                    path,
                    line,
                    "'%s: %s' is also a reserved key for %s, where it selects a "
                    "content type" % (key, parsed.get_scalar(key, ""), ", ".join(hit)),
                    "keep it (gotdocs needs it) and export with "
                    "'bin/gotdocs export --target %s', which strips it" % (hit[0],),
                    targets=hit,
                )
            )
            continue
        hit = [name for name in TARGETS if name in targets and key in RESERVED_KEYS.get(name, ())]
        if not hit:
            continue
        issues.append(
            Issue(
                "frontmatter-reserved-key",
                path,
                line,
                "frontmatter key '%s' is reserved by %s" % (key, ", ".join(hit)),
                "remove it and let 'bin/gotdocs export' derive the key, or rename "
                "it to something gotdocs owns",
                targets=hit,
            )
        )
    return issues

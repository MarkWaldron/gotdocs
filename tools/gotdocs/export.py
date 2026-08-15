"""Renders gotdocs documents into a static site generator's own conventions.

``gotdocs export --target <name> --out <dir>`` takes the documents exactly as
they are committed -- gotdocs frontmatter, repo-relative links, a body H1 --
and writes a tree the target generator can build without further editing:

* frontmatter keys are **mapped**, not passed through: ``summary`` becomes the
  target's description key, ``updated`` becomes its date key, and an ordering
  key (``sidebar_position`` / ``weight`` / ``nav_order`` / ``sidebar.order``)
  is derived per directory
* keys the generator has no meaning for (``id``, ``covers``, ``owners``,
  ``status``, ``verified_at``, ``type``) are **stripped into**
  ``_gotdocs.json`` so nothing is lost and the publishing job can still see
  ownership and coverage
* relative links are rewritten for the target's URL scheme (``.html`` for
  Jekyll, pretty ``/section/page/`` for Hugo, extensionless routes for
  Starlight, untouched ``.md`` for Docusaurus, MkDocs and GitHub)
* referenced images are copied and their links repointed
* drafts are skipped unless ``include_drafts`` is set

Output is **byte-deterministic**: the same documents produce the same bytes on
every machine and every run. There is no timestamp, no head sha and no
dictionary iteration order anywhere in the output, so an export can be
committed or diffed in CI.
"""

import io
import json
import os
import posixpath
import re

from urllib.parse import quote

from . import frontmatter as fm_module
from . import portability
from .errors import UsageError

__all__ = [
    "TARGETS",
    "TARGETS_BY_NAME",
    "MANIFEST_NAME",
    "MANIFEST_VERSION",
    "DEFAULT_JEKYLL_LAYOUT",
    "Target",
    "ExportedFile",
    "ExportResult",
    "target_names",
    "get_target",
    "export_docs",
    "write_export",
    "render_document",
    "render_manifest",
    "rewrite_links",
]

MANIFEST_NAME = "_gotdocs.json"
MANIFEST_VERSION = 1
DEFAULT_JEKYLL_LAYOUT = "page"

_MD_SUFFIXES = (".md", ".markdown")
_INDEX_STEMS = ("index", "readme", "_index")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A plain (unquoted) YAML scalar cannot start with an indicator character, end
# with a colon or a space, or contain ": " / " #". Everything else -- including
# em dashes, accents and CJK -- is safe unquoted, and staying unquoted keeps
# the exported frontmatter readable.
_LEADING_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+)$")
_YAML_KEYWORDS = frozenset(
    ["y", "n", "yes", "no", "true", "false", "on", "off", "null", "~", "none"]
)


class Target(object):
    """One static site generator's conventions.

    ``link_style`` is how a link from one exported document to another is
    written:

    ``md``          keep the ``.md`` path (Docusaurus, MkDocs and GitHub all
                    resolve markdown links themselves)
    ``html``        swap ``.md`` for ``.html`` (Jekyll copies files through)
    ``pretty``      site-absolute ``/section/page/`` (Hugo's default pretty URLs)
    ``extensionless`` site-absolute ``/section/page`` (Astro/Starlight routes)
    """

    __slots__ = (
        "name",
        "link_style",
        "emit_frontmatter",
        "title_in_frontmatter",
        "description_key",
        "date_keys",
        "order_key",
        "order_scale",
        "tags_key",
        "draft_key",
        "draft_value",
        "notes",
    )

    def __init__(self, **kwargs):
        self.name = kwargs["name"]
        self.link_style = kwargs["link_style"]
        self.emit_frontmatter = kwargs.get("emit_frontmatter", True)
        self.title_in_frontmatter = kwargs.get("title_in_frontmatter", True)
        self.description_key = kwargs.get("description_key")
        self.date_keys = tuple(kwargs.get("date_keys", ()))
        self.order_key = kwargs.get("order_key")
        self.order_scale = kwargs.get("order_scale", 1)
        self.tags_key = kwargs.get("tags_key")
        self.draft_key = kwargs.get("draft_key")
        self.draft_value = kwargs.get("draft_value", True)
        self.notes = kwargs.get("notes", "")

    def key_map(self):
        """``{gotdocs key: target key}`` for the report and for ``--json``."""
        mapping = {}
        if self.title_in_frontmatter:
            mapping["title"] = "title"
        if self.description_key:
            mapping["summary"] = self.description_key
        if self.date_keys:
            mapping["updated"] = ", ".join(self.date_keys)
        if self.tags_key:
            mapping["tags"] = self.tags_key
        if self.order_key:
            mapping["(derived order)"] = self.order_key
        if self.draft_key:
            mapping["status"] = self.draft_key
        return mapping

    def as_dict(self):
        return {
            "target": self.name,
            "link_style": self.link_style,
            "keys": self.key_map(),
            "stripped": list(STRIPPED_KEYS),
            "notes": self.notes,
        }

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Target(%r)" % (self.name,)


# Keys that never reach the output frontmatter; they land in _gotdocs.json.
STRIPPED_KEYS = ("id", "type", "covers", "owners", "status", "verified_at")

TARGETS = (
    Target(
        name="docusaurus",
        link_style="md",
        description_key="description",
        date_keys=("last_update",),
        order_key="sidebar_position",
        tags_key="tags",
        draft_key="draft",
        notes="slug is set from the output path; deprecated docs get unlisted: true",
    ),
    Target(
        name="mkdocs",
        link_style="md",
        description_key="description",
        date_keys=("date",),
        order_key=None,
        tags_key="tags",
        draft_key=None,
        notes="plain frontmatter; ordering lives in mkdocs.yml nav, not in the page",
    ),
    Target(
        name="starlight",
        link_style="extensionless",
        description_key="description",
        date_keys=("lastUpdated",),
        order_key="sidebar.order",
        tags_key=None,
        draft_key="draft",
        notes="Astro validates the schema, so only Starlight's own keys are emitted",
    ),
    Target(
        name="jekyll",
        link_style="html",
        description_key="description",
        date_keys=("date",),
        order_key="nav_order",
        tags_key="tags",
        draft_key="published",
        draft_value=False,
        notes="layout is required by Jekyll and defaults to 'page'",
    ),
    Target(
        name="hugo",
        link_style="pretty",
        description_key="description",
        date_keys=("date", "lastmod"),
        order_key="weight",
        order_scale=10,
        tags_key="tags",
        draft_key="draft",
        notes="'type' is stripped: Hugo reads it as a content type and would fail the build",
    ),
    Target(
        name="github",
        link_style="md",
        emit_frontmatter=False,
        title_in_frontmatter=False,
        notes="no frontmatter (GitHub renders it as a table); the title becomes the body H1",
    ),
)

TARGETS_BY_NAME = dict((target.name, target) for target in TARGETS)


def target_names():
    return [target.name for target in TARGETS]


def get_target(name):
    """Look up a target by name, raising :class:`UsageError` when unknown."""
    try:
        return TARGETS_BY_NAME[name]
    except KeyError:
        raise UsageError(
            "unknown export target %r; expected one of %s"
            % (name, ", ".join(target_names()))
        )


class ExportedFile(object):
    """One rendered document, in memory."""

    __slots__ = ("path", "text", "doc_id", "source", "url", "order", "assets")

    def __init__(self, path, text, doc_id, source, url=None, order=None, assets=None):
        self.path = path
        self.text = text
        self.doc_id = doc_id
        self.source = source
        self.url = url
        self.order = order
        self.assets = assets if assets is not None else []

    def __repr__(self):  # pragma: no cover - debugging aid
        return "ExportedFile(%r)" % (self.path,)


class ExportResult(object):
    """Everything one export produced, before or after it hit the disk."""

    __slots__ = ("target", "files", "manifest", "skipped", "assets", "written", "out_dir")

    def __init__(self, target, files, manifest, skipped, assets, written=None, out_dir=None):
        self.target = target
        self.files = files
        self.manifest = manifest
        self.skipped = skipped
        self.assets = assets
        self.written = written if written is not None else []
        self.out_dir = out_dir

    @property
    def doc_count(self):
        return len(self.files)

    def manifest_text(self):
        return render_manifest(self.manifest)

    def summary(self):
        return {
            "target": self.target.name,
            "documents": len(self.files),
            "skipped": len(self.skipped),
            "assets": len(self.assets),
            "out_dir": self.out_dir,
        }


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------


def export_docs(
    repo_root,
    config,
    target,
    doc_set=None,
    include_drafts=False,
    url_prefix="",
    layout=None,
    source_url=None,
    h1_in_body=None,
):
    """Render every publishable document. Returns an :class:`ExportResult`.

    Nothing is written; :func:`write_export` does that. *target* may be a name
    or a :class:`Target`. *url_prefix* is prepended to site-absolute links for
    the targets that use them (Hugo, Starlight), so a site served under
    ``/handbook`` still links correctly. *source_url* rewrites links that point
    at code rather than at another document, for example
    ``https://github.com/org/repo/blob/main/``.
    """
    from . import index as index_module

    target = target if isinstance(target, Target) else get_target(target)
    if doc_set is None:
        doc_set = index_module.scan(repo_root, config)
    if h1_in_body is None:
        h1_in_body = portability.h1_in_body_for(config)

    selected = []
    skipped = []
    for doc in doc_set.docs:
        if doc.status == "draft" and not include_drafts:
            skipped.append({"path": doc.path, "id": doc.id, "reason": "draft"})
            continue
        selected.append(doc)

    outputs = dict((doc.path, _output_path(doc.path)) for doc in selected)
    orders = _derive_orders(selected, outputs)

    files = []
    entries = []
    assets = {}
    for doc in selected:
        rendered = render_document(
            repo_root,
            doc,
            target,
            outputs,
            orders.get(doc.path, 1),
            url_prefix=url_prefix,
            layout=layout,
            source_url=source_url,
            h1_in_body=h1_in_body,
        )
        files.append(rendered)
        for asset in rendered.assets:
            assets[asset["source"]] = asset
        entries.append(_manifest_entry(doc, rendered, target))

    manifest = {
        "version": MANIFEST_VERSION,
        "target": target.name,
        "link_style": target.link_style,
        "url_prefix": url_prefix or "",
        "doc_count": len(entries),
        "docs": entries,
        "assets": [assets[key] for key in sorted(assets)],
        "skipped": sorted(skipped, key=lambda item: item["path"]),
    }
    return ExportResult(target, files, manifest, manifest["skipped"], manifest["assets"])


def write_export(repo_root, config, target, out_dir, clean=False, **kwargs):
    """Run :func:`export_docs` and write the tree to *out_dir*.

    Returns the :class:`ExportResult` with ``written`` listing every path that
    changed on disk, relative to *out_dir*. Files whose bytes already match are
    left alone, so re-running an export produces no diff.
    """
    result = export_docs(repo_root, config, target, **kwargs)
    result.out_dir = out_dir

    if clean and os.path.isdir(out_dir):
        _clean_dir(out_dir, result)

    written = []
    for exported in result.files:
        if _write_if_changed(os.path.join(out_dir, exported.path.replace("/", os.sep)),
                             exported.text.encode("utf-8")):
            written.append(exported.path)

    for asset in result.assets:
        source = os.path.join(repo_root, asset["source"].replace("/", os.sep))
        destination = os.path.join(out_dir, asset["output"].replace("/", os.sep))
        if _copy_if_changed(source, destination):
            written.append(asset["output"])

    manifest_path = os.path.join(out_dir, MANIFEST_NAME)
    if _write_if_changed(manifest_path, result.manifest_text().encode("utf-8")):
        written.append(MANIFEST_NAME)

    result.written = sorted(written)
    return result


def render_manifest(manifest):
    """Serialize ``_gotdocs.json`` deterministically, with a trailing newline."""
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# one document
# ---------------------------------------------------------------------------


def render_document(
    repo_root,
    doc,
    target,
    outputs=None,
    order=1,
    url_prefix="",
    layout=None,
    source_url=None,
    h1_in_body=True,
):
    """Render a single :class:`~tools.gotdocs.index.Doc` for *target*."""
    target = target if isinstance(target, Target) else get_target(target)
    if outputs is None:
        outputs = {doc.path: _output_path(doc.path)}
    output = outputs.get(doc.path) or _output_path(doc.path)

    absolute = os.path.join(repo_root, doc.path.replace("/", os.sep))
    text = fm_module.read_text(absolute)
    parsed = fm_module.parse_text(text, doc.path)
    body = parsed.body if parsed.present else text
    body = body.replace("\r\n", "\n").replace("\r", "\n")

    title = doc.title or _first_h1(body) or (doc.id or _stem(doc.path))

    body, assets = rewrite_links(
        body,
        doc.path,
        output,
        target,
        outputs,
        repo_root=repo_root,
        url_prefix=url_prefix,
        source_url=source_url,
    )
    body = _adjust_h1(body, title, target, h1_in_body)

    if target.emit_frontmatter:
        pairs = _frontmatter_pairs(doc, target, output, order, title, layout, url_prefix)
        text_out = _emit_frontmatter(pairs) + body
    else:
        text_out = body

    if not text_out.endswith("\n"):
        text_out += "\n"

    return ExportedFile(
        output,
        text_out,
        doc.id,
        doc.path,
        url=_site_url(output, url_prefix, target),
        order=order,
        assets=assets,
    )


def _frontmatter_pairs(doc, target, output, order, title, layout, url_prefix):
    """The ordered ``(key, value)`` list this target's frontmatter needs."""
    pairs = []
    name = target.name

    if name == "jekyll":
        pairs.append(("layout", layout or DEFAULT_JEKYLL_LAYOUT))

    if target.title_in_frontmatter:
        pairs.append(("title", title))
    if target.description_key and doc.summary:
        pairs.append((target.description_key, doc.summary))

    if name == "docusaurus":
        if doc.id:
            pairs.append(("id", doc.id))
        pairs.append(("slug", _site_url(output, url_prefix, target, style="extensionless")))
        pairs.append(("sidebar_position", order * target.order_scale))
        if doc.tags:
            pairs.append(("tags", list(doc.tags)))
        if doc.updated:
            pairs.append(("last_update", [("date", doc.updated)]))
        if doc.status == "draft":
            pairs.append((target.draft_key, target.draft_value))
        elif doc.status == "deprecated":
            pairs.append(("unlisted", True))
        return pairs

    if name == "starlight":
        sidebar = [("order", order * target.order_scale)]
        if doc.status == "deprecated":
            sidebar.append(("badge", "Deprecated"))
        pairs.append(("sidebar", sidebar))
        if doc.updated:
            pairs.append(("lastUpdated", doc.updated))
        if doc.status == "draft":
            pairs.append((target.draft_key, target.draft_value))
        return pairs

    if name == "mkdocs":
        if doc.updated:
            pairs.append(("date", doc.updated))
        if doc.tags:
            pairs.append(("tags", list(doc.tags)))
        return pairs

    if name == "jekyll":
        if doc.updated:
            pairs.append(("date", doc.updated))
        pairs.append(("nav_order", order * target.order_scale))
        if doc.tags:
            pairs.append(("tags", list(doc.tags)))
        if doc.status == "draft":
            pairs.append((target.draft_key, target.draft_value))
        return pairs

    if name == "hugo":
        if doc.updated:
            pairs.append(("date", doc.updated))
            pairs.append(("lastmod", doc.updated))
        pairs.append(("weight", order * target.order_scale))
        if doc.tags:
            pairs.append(("tags", list(doc.tags)))
        if doc.status == "draft":
            pairs.append((target.draft_key, target.draft_value))
        return pairs

    return pairs


def _manifest_entry(doc, rendered, target):
    extra = {}
    for key in sorted(doc.extra):
        value = doc.extra[key]
        extra[key] = list(value) if isinstance(value, list) else value
    entry = {
        "id": doc.id,
        "source": doc.path,
        "output": rendered.path,
        "url": rendered.url,
        "type": doc.type,
        "title": doc.title,
        "summary": doc.summary,
        "status": doc.status,
        "covers": list(doc.covers),
        "owners": list(doc.owners),
        "tags": list(doc.tags),
        "updated": doc.updated,
        "verified_at": doc.verified_at,
        "order": rendered.order,
        "assets": [asset["output"] for asset in rendered.assets],
    }
    # Decision-record fields are known keys, so they never land in ``doc.extra``
    # and used to fall out of the export entirely - including ``symptoms``,
    # which is the whole search corpus for ``why``. No generator has a frontmatter
    # key for them, so the sidecar is where they belong. Emitted only for the
    # records that carry them, so every other entry is byte-identical to before.
    if doc.type == "decision" or doc.symptoms or doc.supersedes or doc.superseded_by:
        entry["symptoms"] = list(doc.symptoms)
        entry["supersedes"] = list(doc.supersedes)
        entry["superseded_by"] = list(doc.superseded_by)
        entry["decided_on"] = doc.decided_on
    if extra:
        entry["extra"] = extra
    return entry


# ---------------------------------------------------------------------------
# paths, ordering and URLs
# ---------------------------------------------------------------------------


def _output_path(doc_path):
    """The path inside the export tree. The repo layout is preserved."""
    return doc_path


def _stem(path):
    name = posixpath.basename(path)
    for suffix in _MD_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_index(path):
    return _stem(path).lower() in _INDEX_STEMS


def _derive_orders(docs, outputs):
    """Assign 1..N per output directory, deterministically.

    An explicit integer ``order`` in the document's frontmatter wins; the rest
    are numbered by path, with index pages first. Two documents can share a
    number only when their authors set the same explicit ``order``.
    """
    by_dir = {}
    for doc in docs:
        output = outputs[doc.path]
        by_dir.setdefault(posixpath.dirname(output), []).append(doc)

    orders = {}
    for directory in sorted(by_dir):
        siblings = sorted(
            by_dir[directory],
            key=lambda item: (not _is_index(outputs[item.path]), outputs[item.path]),
        )
        position = 0
        for doc in siblings:
            explicit = _explicit_order(doc)
            if explicit is not None:
                orders[doc.path] = explicit
                continue
            position += 1
            orders[doc.path] = position
    return orders


def _explicit_order(doc):
    value = doc.extra.get("order") if doc.extra else None
    if value is None or isinstance(value, list):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _site_url(output, url_prefix, target, style=None):
    """The site path an exported document is served at."""
    style = style or target.link_style
    base = output
    for suffix in _MD_SUFFIXES:
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    if _is_index(output):
        base = posixpath.dirname(base)

    prefix = (url_prefix or "").strip("/")
    parts = [part for part in (prefix, base) if part]
    path = "/" + "/".join(parts) if parts else "/"

    if style == "pretty":
        return path if path.endswith("/") else path + "/"
    if style == "extensionless":
        return path
    if style == "html":
        return (path + ".html") if path != "/" else "/index.html"
    return path


# ---------------------------------------------------------------------------
# link rewriting
# ---------------------------------------------------------------------------


def rewrite_links(
    body,
    source_path,
    output_path,
    target,
    outputs,
    repo_root=None,
    url_prefix="",
    source_url=None,
):
    """Rewrite relative links in *body* for *target*.

    Returns ``(new_body, assets)`` where each asset is
    ``{"source": repo path, "output": export path}``. Links inside code fences
    and code spans are never touched -- the scanner in
    :mod:`tools.gotdocs.portability` decides what is prose.
    """
    target = target if isinstance(target, Target) else get_target(target)
    scan = portability.scan_markdown(body)
    source_dir = posixpath.dirname(source_path)
    output_dir = posixpath.dirname(output_path)

    replacements = []
    assets = {}
    for link in scan.links:
        raw = link.dest
        stripped = raw.strip()
        if stripped == "" or stripped.startswith("#") or stripped.startswith("//"):
            continue
        if portability._SCHEME_RE.match(stripped) or stripped.startswith("/"):
            continue
        if portability._WINDOWS_ABSOLUTE_RE.match(stripped):
            continue

        path_part, suffix = portability._split_target(stripped)
        if path_part == "":
            continue
        decoded = portability._percent_decode(path_part)
        resolved = posixpath.normpath(posixpath.join(source_dir, decoded))
        if resolved.startswith("../"):
            continue

        if resolved in outputs:
            new_dest = _encode_dest(
                _link_to_doc(resolved, outputs, output_dir, url_prefix, target)
            )
        elif link.is_image or _is_asset(resolved, repo_root):
            asset_output = resolved
            assets[resolved] = {"source": resolved, "output": asset_output}
            new_dest = _encode_dest(posixpath.relpath(asset_output, output_dir or "."))
        elif source_url:
            new_dest = source_url.rstrip("/") + "/" + _encode_dest(resolved)
        else:
            continue

        if new_dest == path_part:
            continue
        replacements.append((link.start, link.end, new_dest + suffix))

    if not replacements:
        return body, [assets[key] for key in sorted(assets)]

    replacements.sort()
    out = []
    cursor = 0
    for start, end, value in replacements:
        out.append(body[cursor:start])
        out.append(value)
        cursor = end
    out.append(body[cursor:])
    return "".join(out), [assets[key] for key in sorted(assets)]


def _encode_dest(dest):
    """Percent-encode a rewritten destination so it stays a link.

    Destinations are resolved through :func:`portability._percent_decode`, so a
    source link written ``./release%20notes.md`` arrives here as
    ``release notes.md``. Emitting that raw ends the link: a bare CommonMark
    destination cannot contain a space, and ``[a](release notes.md)`` renders as
    literal text. Re-encoding is correct for the angle-bracket form too --
    ``<release%20notes.md>`` still resolves to the same file.

    ``/`` is the only reserved character kept, because everything reaching here
    is a repo-relative path or a site path built from one.
    """
    return quote(dest, safe="/")


def _link_to_doc(resolved, outputs, output_dir, url_prefix, target):
    destination = outputs[resolved]
    if target.link_style in ("pretty", "extensionless"):
        return _site_url(destination, url_prefix, target)
    relative = posixpath.relpath(destination, output_dir or ".")
    if target.link_style == "html":
        for suffix in _MD_SUFFIXES:
            if relative.lower().endswith(suffix):
                return relative[: -len(suffix)] + ".html"
        return relative
    return relative


def _is_asset(resolved, repo_root):
    """True when a link points at a file that must travel with the docs."""
    return resolved.lower().endswith(portability._IMAGE_SUFFIXES)


# ---------------------------------------------------------------------------
# body and frontmatter rendering
# ---------------------------------------------------------------------------


def _first_h1(body):
    scan = portability.scan_markdown(body)
    if not scan.h1_lines:
        return None
    line = scan.lines[scan.h1_lines[0] - 1]
    match = portability._ATX_H1_RE.match(line.rstrip("\r\n"))
    if match:
        return match.group(1).strip()
    return line.strip()


def _adjust_h1(body, title, target, h1_in_body):
    """Reconcile the body H1 with the target's title handling.

    A generator that prints the frontmatter title renders a body H1 as a second
    title, so the leading H1 is removed. ``github`` has no frontmatter, so the
    title has to *be* the H1 and one is inserted when missing.
    """
    scan = portability.scan_markdown(body)
    if target.title_in_frontmatter:
        if not scan.h1_lines:
            return _normalize_body(body)
        first = scan.h1_lines[0]
        lines = scan.lines
        # Only strip a leading H1: an H1 further down is real content.
        if any(lines[index].strip() for index in range(0, first - 1)):
            return _normalize_body(body)
        remainder = lines[first:]
        while remainder and remainder[0].strip() == "":
            remainder.pop(0)
        return _normalize_body("".join(remainder))
    # No frontmatter to carry the title, so the body must hold it. This is the
    # case the `h1_in_body: false` convention creates: the title lives only in
    # gotdocs frontmatter, which this target drops.
    if scan.h1_lines or not title:
        return _normalize_body(body)
    return "# %s\n\n%s" % (title, _normalize_body(body))


def _normalize_body(body):
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = body.lstrip("\n")
    body = body.rstrip("\n")
    return body + "\n" if body else ""


def _emit_frontmatter(pairs):
    lines = ["---"]
    for key, value in pairs:
        lines.extend(_emit_pair(key, value, indent=""))
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def _emit_pair(key, value, indent):
    if isinstance(value, list) and value and isinstance(value[0], tuple):
        out = ["%s%s:" % (indent, key)]
        for sub_key, sub_value in value:
            out.extend(_emit_pair(sub_key, sub_value, indent + "  "))
        return out
    if isinstance(value, list):
        if not value:
            return ["%s%s: []" % (indent, key)]
        return ["%s%s:" % (indent, key)] + [
            "%s  - %s" % (indent, _yaml_scalar(item)) for item in value
        ]
    return ["%s%s: %s" % (indent, key, _yaml_scalar(value))]


def _yaml_scalar(value):
    """Render one scalar, quoting only when YAML would otherwise misread it."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return '""'
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if _DATE_RE.match(text):
        return text
    if text.lower() in _YAML_KEYWORDS or _NUMERIC_RE.match(text):
        return _quote(text)
    if _CONTROL_RE.search(text):
        return _quote(text)
    if text[0] in _LEADING_INDICATORS or text[0] == " ":
        return _quote(text)
    if text.endswith(":") or text.endswith(" "):
        return _quote(text)
    if ": " in text or " #" in text:
        return _quote(text)
    return text


def _quote(text):
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    return '"%s"' % (escaped,)


# ---------------------------------------------------------------------------
# disk
# ---------------------------------------------------------------------------


def _write_if_changed(path, data):
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


def _copy_if_changed(source, destination):
    try:
        with io.open(source, "rb") as handle:
            data = handle.read()
    except (IOError, OSError):
        return False
    return _write_if_changed(destination, data)


def _clean_dir(out_dir, result):
    """Remove files the export no longer produces, leaving foreign files alone.

    Only paths listed in a previous ``_gotdocs.json`` are removed, so pointing
    ``--out`` at a directory that also holds hand-written pages cannot delete
    somebody's work.
    """
    manifest_path = os.path.join(out_dir, MANIFEST_NAME)
    try:
        with io.open(manifest_path, "rb") as handle:
            previous = json.loads(handle.read().decode("utf-8"))
    except (IOError, OSError, ValueError):
        return
    keep = set(item.path for item in result.files)
    keep.update(asset["output"] for asset in result.assets)
    previous_paths = []
    for entry in previous.get("docs", []):
        if entry.get("output"):
            previous_paths.append(entry["output"])
    previous_paths.extend(previous.get("assets_paths", []))
    for asset in previous.get("assets", []):
        if isinstance(asset, dict) and asset.get("output"):
            previous_paths.append(asset["output"])
    for relative in previous_paths:
        if relative in keep:
            continue
        path = os.path.join(out_dir, relative.replace("/", os.sep))
        try:
            os.remove(path)
        except OSError:
            continue
        _prune_empty(out_dir, os.path.dirname(path))


def _prune_empty(root, directory):
    root = os.path.abspath(root)
    directory = os.path.abspath(directory)
    while directory.startswith(root) and directory != root:
        try:
            os.rmdir(directory)
        except OSError:
            return
        directory = os.path.dirname(directory)

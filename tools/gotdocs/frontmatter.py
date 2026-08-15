"""Parser and byte-preserving writer for gotdocs frontmatter.

Gotdocs has zero third-party dependencies, so there is no PyYAML. This module
implements the deliberately small YAML subset documented in
``docs/doc-format.md``:

.. code-block:: yaml

    key: scalar                 # unquoted
    key: 'single quoted'        # surrounding quotes stripped
    key: "double quoted"        # surrounding quotes stripped
    key: [a, b, c]              # inline flow list of scalars
    key:                        # block list of scalars
      - a
      - b
    # full-line comment

Anything else -- nested maps, lists of maps, block scalars, anchors, aliases,
tags, tabs for indentation -- is an error with a ``file:line`` pointer, never a
silent misparse.

The writer rewrites only ``updated`` and ``verified_at``. Every other byte of
the file, including key order, comments, quoting style and line endings, is
preserved exactly.
"""

import io
import os
import re

from .errors import FrontmatterError

__all__ = [
    "Frontmatter",
    "DELIMITER",
    "WRITABLE_KEYS",
    "parse",
    "parse_text",
    "parse_file",
    "read_text",
    "rewrite_fields",
    "render_scalar",
]

DELIMITER = "---"
WRITABLE_KEYS = ("updated", "verified_at")

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)[ \t]*:(.*)$")
_ITEM_MAP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*[ \t]*:([ \t]|$)")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+\-0-9]*$")


class LintIssue(object):
    """One frontmatter problem, anchored at ``path:line``."""

    __slots__ = ("path", "line", "message")

    def __init__(self, path, line, message):
        self.path = path
        self.line = line
        self.message = message

    def located(self):
        if self.line is None:
            return "%s: %s" % (self.path, self.message)
        return "%s:%d: %s" % (self.path, self.line, self.message)

    def as_error(self):
        return FrontmatterError(self.message, path=self.path, line=self.line)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "LintIssue(%r)" % (self.located(),)


class Frontmatter(object):
    """A parsed frontmatter block.

    Attributes:
        data:        ``{key: value}`` where value is ``str`` or ``list[str]``.
        key_lines:   ``{key: 1-based line number in the file}``.
        start_line:  1-based line number of the opening ``---``.
        end_line:    1-based line number of the closing ``---``.
        body:        everything after the closing delimiter, verbatim.
        issues:      list of :class:`LintIssue` found while parsing.
        path:        the file the block came from (may be None).
    """

    __slots__ = (
        "data",
        "key_lines",
        "start_line",
        "end_line",
        "body",
        "issues",
        "path",
        "present",
    )

    def __init__(
        self,
        data=None,
        key_lines=None,
        start_line=None,
        end_line=None,
        body="",
        issues=None,
        path=None,
        present=True,
    ):
        self.data = data if data is not None else {}
        self.key_lines = key_lines if key_lines is not None else {}
        self.start_line = start_line
        self.end_line = end_line
        self.body = body
        self.issues = issues if issues is not None else []
        self.path = path
        self.present = present

    # -- accessors ---------------------------------------------------------

    def __contains__(self, key):
        return key in self.data

    def get(self, key, default=None):
        value = self.data.get(key, default)
        return value

    def line_of(self, key, default=None):
        return self.key_lines.get(key, default)

    def get_scalar(self, key, default=None):
        """Return a scalar value, or *default*. Lists are not coerced."""
        value = self.data.get(key)
        if value is None:
            return default
        if isinstance(value, list):
            return default
        return value

    def get_list(self, key):
        """Return a list value. An absent or empty key yields ``[]``.

        A scalar where a list is expected returns ``None`` so callers can emit
        a precise lint message instead of guessing.
        """
        if key not in self.data:
            return []
        value = self.data[key]
        if isinstance(value, list):
            return value
        if value == "":
            return []
        return None

    def is_scalar(self, key):
        return key in self.data and not isinstance(self.data[key], list)

    def keys(self):
        return list(self.data.keys())


def read_text(path):
    """Read *path* as UTF-8 text, preserving line endings.

    Undecodable bytes raise a :class:`FrontmatterError` rather than a
    ``UnicodeDecodeError`` traceback.
    """
    try:
        with io.open(path, "rb") as handle:
            raw = handle.read()
    except (IOError, OSError) as exc:
        raise FrontmatterError("cannot read file: %s" % (exc.strerror or exc,), path=path)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError(
            "file is not valid UTF-8 (byte %d)" % (exc.start,), path=path
        )


def parse_file(path, rel_path=None):
    """Parse the frontmatter of the file at *path*.

    *rel_path* is used in messages when given, so findings can quote the
    repo-relative path while reading an absolute one.
    """
    text = read_text(path)
    return parse_text(text, rel_path if rel_path is not None else path)


def parse(text, path=None):
    """Alias for :func:`parse_text`, kept for readability at call sites."""
    return parse_text(text, path)


def parse_text(text, path=None):
    """Parse *text* and return a :class:`Frontmatter`.

    Never raises for content problems: every problem is appended to
    ``result.issues`` so ``lint`` can report them all at once. ``result.present``
    is False when there is no frontmatter block at all.
    """
    issues = []

    if text.startswith("﻿"):
        issues.append(
            LintIssue(path, 1, "file starts with a UTF-8 BOM; frontmatter must be first")
        )
        text = text[1:]

    lines = text.splitlines(True)
    if not lines or _strip_eol(lines[0]) != DELIMITER:
        issues.append(
            LintIssue(
                path,
                1,
                "missing frontmatter: the file must begin with a '---' line",
            )
        )
        return Frontmatter(body=text, issues=issues, path=path, present=False)

    close_index = None
    for index in range(1, len(lines)):
        if _strip_eol(lines[index]) == DELIMITER:
            close_index = index
            break
    if close_index is None:
        issues.append(
            LintIssue(path, len(lines), "unterminated frontmatter: no closing '---'")
        )
        return Frontmatter(body="", issues=issues, path=path, present=False)

    second_document = _second_document_line(lines, close_index)
    if second_document is not None:
        issues.append(
            LintIssue(
                path,
                second_document,
                "a second '---' inside the frontmatter is not supported "
                "(YAML multi-document); keys after line %d would be silently "
                "dropped into the body" % (close_index + 1,),
            )
        )

    data = {}
    key_lines = {}
    block_keys = set()
    pending_key = None
    # A block scalar (`notes: |`) is rejected on its header line. Its indented
    # continuation lines are part of that one construct, not separate mistakes,
    # so they are swallowed until the next unindented line - otherwise one
    # unsupported key produced N+1 issues and buried the one that mattered.
    swallow_indent = False

    for offset in range(1, close_index):
        raw = lines[offset]
        lineno = offset + 1
        line = _strip_eol(raw)
        stripped = line.strip()

        if stripped == "":
            continue
        if stripped.startswith("#"):
            continue

        indent = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in indent:
            issues.append(
                LintIssue(path, lineno, "tabs are not allowed for indentation; use spaces")
            )
            continue

        if indent:
            if swallow_indent:
                continue
            # Indented line: only block-list items are legal here.
            content = line.strip()
            if not content.startswith("-"):
                issues.append(
                    LintIssue(
                        path,
                        lineno,
                        "nested mappings are not supported; flatten the key "
                        "(for example 'owner_name: mark')",
                    )
                )
                continue
            item = content[1:]
            if item and not item[:1].isspace():
                issues.append(
                    LintIssue(
                        path,
                        lineno,
                        "list item must be written as '- value'",
                    )
                )
                continue
            # A trailing "# ..." is a comment here exactly as it is on a
            # "key: value" line. Without this, `- src/**   # the CLI` parses as
            # a glob with the comment glued on, which then silently matches
            # nothing.
            item, _item_comment = _split_comment(item.strip())
            item = item.strip()
            if pending_key is None:
                issues.append(
                    LintIssue(path, lineno, "list item does not belong to any key")
                )
                continue
            if _ITEM_MAP_RE.match(item):
                issues.append(
                    LintIssue(
                        path,
                        lineno,
                        "lists of mappings are not supported; use parallel "
                        "scalar lists or move the structure into the body",
                    )
                )
                continue
            if item == "":
                issues.append(LintIssue(path, lineno, "empty list item"))
                continue
            value, item_issue = _parse_scalar(item, path, lineno)
            if item_issue is not None:
                issues.append(item_issue)
                continue
            if pending_key not in block_keys:
                data[pending_key] = []
                block_keys.add(pending_key)
            data[pending_key].append(value)
            continue

        # Unindented line: must be "key: ..." .
        swallow_indent = False
        if line.startswith("- "):
            issues.append(
                LintIssue(path, lineno, "top-level lists are not supported in frontmatter")
            )
            continue
        match = _KEY_RE.match(line)
        if match is None:
            issues.append(
                LintIssue(path, lineno, "expected 'key: value', got %r" % (line,))
            )
            pending_key = None
            continue

        key = match.group(1)
        rest = match.group(2)
        if key in data:
            issues.append(
                LintIssue(path, lineno, "duplicate key %r in frontmatter" % (key,))
            )
            continue
        key_lines[key] = lineno

        value_text, _comment = _split_comment(rest.strip())
        value_text = value_text.strip()

        if value_text == "":
            # Either an empty scalar or the header of a block list.
            data[key] = ""
            pending_key = key
            continue

        pending_key = None
        if value_text.startswith("["):
            items, list_issue = _parse_flow_list(value_text, path, lineno)
            if list_issue is not None:
                issues.append(list_issue)
                data[key] = ""
                continue
            data[key] = items
            block_keys.add(key)
            continue

        value, issue = _parse_scalar(value_text, path, lineno)
        if issue is not None:
            issues.append(issue)
            data[key] = ""
            swallow_indent = _BLOCK_SCALAR_RE.match(value_text) is not None
            continue
        data[key] = value

    body = "".join(lines[close_index + 1 :])
    return Frontmatter(
        data=data,
        key_lines=key_lines,
        start_line=1,
        end_line=close_index + 1,
        body=body,
        issues=issues,
        path=path,
        present=True,
    )


# ---------------------------------------------------------------------------
# scalar / list parsing
# ---------------------------------------------------------------------------


def _second_document_line(lines, close_index):
    """Detect ``---`` used as a *second* frontmatter delimiter.

    The closing ``---`` ends the block, so anything after it is body text. When
    the body immediately continues with ``key: value`` lines and then hits
    another ``---`` with no blank line in between, the author meant those keys
    to be frontmatter and they are being silently dropped. Returns the 1-based
    line number of that second delimiter, or None.

    Blank lines end the scan, so an ordinary body ("---" as a horizontal rule
    after a paragraph) is not flagged.
    """
    saw_key = False
    for index in range(close_index + 1, len(lines)):
        line = _strip_eol(lines[index])
        stripped = line.strip()
        if stripped == "":
            return None
        if stripped == DELIMITER:
            return (index + 1) if saw_key else None
        if stripped.startswith("#"):
            continue
        if line[:1] in (" ", "\t") and stripped.startswith("-"):
            continue  # a block-list item belonging to the key above
        if _KEY_RE.match(line):
            saw_key = True
            continue
        return None
    return None


def _strip_eol(line):
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def _split_comment(text):
    """Split a value into ``(value, comment)``.

    A ``#`` starts a comment only at the start of the value or when preceded by
    whitespace, and never inside a quoted scalar.
    """
    quote = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote is not None:
            if char == "\\" and quote == '"' and index + 1 < length:
                index += 2
                continue
            if char == quote:
                if quote == "'" and index + 1 < length and text[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index], text[index:]
        index += 1
    return text, ""


def _parse_scalar(text, path, lineno):
    """Parse one scalar. Returns ``(value, issue_or_None)``."""
    if text == "":
        return "", None
    first = text[0]
    if first == "&":
        return None, LintIssue(path, lineno, "YAML anchors are not supported")
    if first == "*":
        return None, LintIssue(path, lineno, "YAML aliases are not supported")
    if first == "!":
        return None, LintIssue(path, lineno, "YAML tags are not supported; quote the value")
    if first == "{":
        return None, LintIssue(
            path,
            lineno,
            "inline mappings are not supported; flatten the key",
        )
    if first == "[":
        return None, LintIssue(
            path, lineno, "nested lists are not supported"
        )
    if _BLOCK_SCALAR_RE.match(text):
        return None, LintIssue(
            path,
            lineno,
            "block scalars ('|' and '>') are not supported; use a single line",
        )
    if first == '"':
        if len(text) < 2 or not text.endswith('"') or _unbalanced_double(text):
            return None, LintIssue(path, lineno, "unterminated double-quoted string")
        return _unescape_double(text[1:-1]), None
    if first == "'":
        if len(text) < 2 or not text.endswith("'"):
            return None, LintIssue(path, lineno, "unterminated single-quoted string")
        return text[1:-1].replace("''", "'"), None
    return text, None


def _unbalanced_double(text):
    """True when a double-quoted scalar's closing quote is escaped."""
    index = 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index != length - 1
        index += 1
    return True


def _unescape_double(text):
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            nxt = text[index + 1]
            out.append(
                {
                    "n": "\n",
                    "t": "\t",
                    "r": "\r",
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "0": "\0",
                }.get(nxt, "\\" + nxt)
            )
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _parse_flow_list(text, path, lineno):
    """Parse ``[a, b, "c"]``. Returns ``(items, issue_or_None)``."""
    if not text.endswith("]"):
        return None, LintIssue(path, lineno, "unterminated flow list; expected ']'")
    inner = text[1:-1].strip()
    if inner == "":
        return [], None
    if "[" in inner or "{" in inner:
        return None, LintIssue(
            path, lineno, "nested structures are not supported inside a flow list"
        )
    items = []
    for raw_item in _split_flow_items(inner):
        item = raw_item.strip()
        if item == "":
            return None, LintIssue(path, lineno, "empty item in flow list")
        value, issue = _parse_scalar(item, path, lineno)
        if issue is not None:
            return None, issue
        items.append(value)
    return items, None


def _split_flow_items(inner):
    parts = []
    buffer = []
    quote = None
    index = 0
    length = len(inner)
    while index < length:
        char = inner[index]
        if quote is not None:
            buffer.append(char)
            if char == "\\" and quote == '"' and index + 1 < length:
                buffer.append(inner[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == ",":
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer))
    return parts


# ---------------------------------------------------------------------------
# byte-preserving writer
# ---------------------------------------------------------------------------


def render_scalar(value, quote=None):
    """Render *value* for the right-hand side of a frontmatter key.

    *quote* may be ``'"'`` or ``"'"`` to preserve the original quoting style.
    """
    text = "" if value is None else str(value)
    if quote == '"':
        return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')
    if quote == "'":
        return "'%s'" % text.replace("'", "''")
    return text


def rewrite_fields(path, updates, rel_path=None):
    """Rewrite ``updated`` / ``verified_at`` in the file at *path*, in place.

    Only the lines for the given keys change. Key order, comments, quoting
    style, indentation, line endings and every other byte are preserved. A key
    that is absent is appended as the last line of the frontmatter block.

    Returns True when the file's bytes changed.
    """
    for key in updates:
        if key not in WRITABLE_KEYS:
            raise FrontmatterError(
                "refusing to rewrite frontmatter key %r; gotdocs only writes %s"
                % (key, " and ".join(WRITABLE_KEYS)),
                path=rel_path or path,
            )

    text = read_text(path)
    display = rel_path if rel_path is not None else path
    new_text = rewrite_text(text, updates, path=display)
    if new_text == text:
        return False
    _atomic_write(path, new_text)
    return True


def rewrite_text(text, updates, path=None):
    """Pure-text form of :func:`rewrite_fields`; returns the new text."""
    had_bom = text.startswith("﻿")
    body = text[1:] if had_bom else text

    lines = body.splitlines(True)
    if not lines or _strip_eol(lines[0]) != DELIMITER:
        raise FrontmatterError(
            "missing frontmatter: the file must begin with a '---' line",
            path=path,
            line=1,
        )
    close_index = None
    for index in range(1, len(lines)):
        if _strip_eol(lines[index]) == DELIMITER:
            close_index = index
            break
    if close_index is None:
        raise FrontmatterError(
            "unterminated frontmatter: no closing '---'", path=path, line=len(lines)
        )

    default_eol = _dominant_eol(lines)

    for key, value in updates.items():
        target = None
        for index in range(1, close_index):
            match = _KEY_RE.match(_strip_eol(lines[index]))
            if match is not None and match.group(1) == key:
                target = index
                break
        rendered_line = None
        if target is None:
            rendered_line = "%s: %s%s" % (key, render_scalar(value), default_eol)
            lines.insert(close_index, rendered_line)
            close_index += 1
            continue

        original = lines[target]
        eol = original[len(_strip_eol(original)) :]
        match = _KEY_RE.match(_strip_eol(original))
        rest = match.group(2)
        old_value, comment = _split_comment(rest.strip())
        old_value = old_value.strip()
        quote = old_value[0] if old_value[:1] in ("'", '"') else None
        trailing = (" " + comment) if comment else ""
        lines[target] = "%s: %s%s%s" % (key, render_scalar(value, quote), trailing, eol)

    result = "".join(lines)
    return ("﻿" + result) if had_bom else result


def _dominant_eol(lines):
    """The line ending an inserted key should use.

    All three endings ``str.splitlines`` recognises are counted, including
    CR-only (classic Mac) files: inserting a key with ``\\n`` into a CR-only
    file would add a line ending the file does not otherwise use, which breaks
    the byte-preservation promise for every other line.
    """
    crlf = 0
    lf = 0
    cr = 0
    for line in lines:
        if line.endswith("\r\n"):
            crlf += 1
        elif line.endswith("\n"):
            lf += 1
        elif line.endswith("\r"):
            cr += 1
    best = max(crlf, lf, cr)
    if best == 0:
        return "\n"
    if lf == best:
        return "\n"
    if crlf == best:
        return "\r\n"
    return "\r"


def _atomic_write(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp_path = os.path.join(directory, ".gotdocs.%d.tmp" % (os.getpid(),))
    data = text.encode("utf-8")
    try:
        mode = os.stat(path).st_mode
    except OSError:
        mode = None
    with io.open(temp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        try:
            os.chmod(temp_path, mode & 0o7777)
        except OSError:
            pass
    os.replace(temp_path, path)

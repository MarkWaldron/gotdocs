"""The gotdocs glob dialect.

This is deliberately *not* :mod:`fnmatch` and *not* shell globbing. ``fnmatch``
lets ``*`` cross ``/``, which would make ``src/*`` match ``src/a/b/c.py``.
Patterns are compiled to anchored regexes and cached.

Dialect (docs/doc-format.md#glob-dialect):

===================  =========================================================
``*``                any run of characters except ``/``
``**``               any run of characters including ``/``, whether it is a
                     whole segment (``a/**/b``) or shares one with other
                     characters (``src/**.py`` matches ``src/a/b.py``)
``?``                exactly one character, not ``/``
``[abc]``            one of ``a``, ``b``, ``c``
``[a-z]``            one character in the range
``[!abc]``           one character that is not ``a``, ``b`` or ``c``
===================  =========================================================

Additional rules:

* A pattern with no ``/`` **and** no ``**`` matches the *basename* at any
  depth, so ``*.py`` matches ``cli.py`` and ``a/b/c.py``.
* Otherwise the pattern is anchored at the repository root.
* ``a/**`` matches paths *under* ``a``, not ``a`` itself.
* A trailing ``/`` means "that directory and everything under it":
  ``scripts/`` is exactly ``scripts/**``.
* Patterns are repo-relative and ``/``-separated. A leading ``/``, a leading
  ``./`` or a backslash separator is a syntax error.
* No brace expansion; ``{a,b}`` is three literal characters plus ``a,b``.
* No leading ``!`` negation.
"""

import re

from .errors import GlobError

__all__ = [
    "CompiledPattern",
    "compile_pattern",
    "validate_pattern",
    "match",
    "match_any",
    "matching_patterns",
    "normalize_path",
    "cache_clear",
    "cache_info",
]

_CACHE = {}
_CACHE_HITS = [0]
_CACHE_MISSES = [0]


def normalize_path(path):
    """Normalize a repo-relative path for matching.

    Accepts ``./a/b`` and ``a//b`` and returns ``a/b``. Backslashes are *not*
    treated as separators (Windows separators are not part of the dialect), so
    a literal backslash in a filename survives.
    """
    if path is None:
        return ""
    text = str(path)
    text = text.replace("\x00", "")
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("/"):
        text = text.lstrip("/")
    while "//" in text:
        text = text.replace("//", "/")
    if text.endswith("/") and text != "/":
        text = text.rstrip("/")
    return text


def validate_pattern(pattern):
    """Raise :class:`GlobError` if *pattern* is outside the dialect.

    Returns the pattern unchanged so it can be used inline.
    """
    if not isinstance(pattern, str):
        raise GlobError("glob pattern must be a string, got %r" % (pattern,), pattern)
    if pattern == "":
        raise GlobError("empty glob pattern", pattern)
    if pattern.strip() != pattern:
        raise GlobError(
            "glob pattern has leading or trailing whitespace: %r" % (pattern,), pattern
        )
    if pattern.startswith("/"):
        raise GlobError(
            "glob pattern must be repo-relative, not absolute: %r" % (pattern,), pattern
        )
    if pattern.startswith("./"):
        raise GlobError(
            "glob pattern must not start with './': %r" % (pattern,), pattern
        )
    if pattern.startswith("!"):
        raise GlobError(
            "negation is not supported in glob patterns: %r" % (pattern,), pattern
        )
    if "\\" in pattern:
        raise GlobError(
            "backslash separators are not supported; use '/': %r" % (pattern,), pattern
        )
    if "//" in pattern:
        raise GlobError("empty path segment in glob pattern: %r" % (pattern,), pattern)
    _check_classes(pattern)
    return pattern


def _check_classes(pattern):
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                raise GlobError(
                    "unterminated character class '[' in glob pattern: %r" % (pattern,),
                    pattern,
                )
            i = j + 1
            continue
        i += 1


class CompiledPattern(object):
    """A compiled gotdocs glob pattern.

    Attributes:
        pattern:       the source pattern, exactly as written.
        regex:         the compiled :mod:`re` object.
        regex_source:  the regex source string (useful in tests and errors).
        basename_only: True when the pattern matches a path's basename at any
                       depth rather than the full repo-relative path.
    """

    __slots__ = ("pattern", "regex", "regex_source", "basename_only")

    def __init__(self, pattern, regex_source, basename_only):
        self.pattern = pattern
        self.regex_source = regex_source
        self.regex = re.compile(regex_source)
        self.basename_only = basename_only

    def match(self, path):
        """Return True when *path* matches this pattern."""
        candidate = normalize_path(path)
        if candidate == "":
            return False
        if self.basename_only:
            candidate = candidate.rsplit("/", 1)[-1]
        return self.regex.match(candidate) is not None

    def __repr__(self):  # pragma: no cover - debugging aid
        return "CompiledPattern(%r, basename_only=%r)" % (
            self.pattern,
            self.basename_only,
        )


def compile_pattern(pattern):
    """Compile *pattern*, memoized. Raises :class:`GlobError` on bad syntax."""
    cached = _CACHE.get(pattern)
    if cached is not None:
        _CACHE_HITS[0] += 1
        return cached
    _CACHE_MISSES[0] += 1
    validate_pattern(pattern)
    basename_only = "/" not in pattern and "**" not in pattern
    if basename_only:
        source = "(?s:" + _translate_segment(pattern, pattern) + ")\\Z"
    else:
        source = "(?s:" + _translate_path(pattern) + ")\\Z"
    compiled = CompiledPattern(pattern, source, basename_only)
    _CACHE[pattern] = compiled
    return compiled


def _translate_path(pattern):
    """Translate an anchored, ``/``-separated pattern into a regex body."""
    work = pattern
    if work.endswith("/"):
        # "scripts/" == "scripts/**"
        work = work + "**"
    segments = work.split("/")

    pieces = []
    # `pending_sep` is False when the next literal segment must be emitted with
    # no leading "/" -- either because it is the first segment, or because a
    # preceding "**" already supplied the trailing separators.
    pending_sep = False
    i = 0
    count = len(segments)
    while i < count:
        segment = segments[i]
        if segment == "**":
            # Collapse runs of "**" segments: a/**/**/b == a/**/b
            j = i
            while j + 1 < count and segments[j + 1] == "**":
                j += 1
            if j == count - 1:
                # Trailing "**": at least one further segment is required, so
                # "a/**" does not match "a" itself.
                pieces.append("/.+" if pending_sep else ".+")
                pending_sep = True
            else:
                if pending_sep:
                    # ...a/**/b  ->  a(?:/[^/]+)*/b
                    pieces.append("(?:/[^/]+)*")
                    pending_sep = True
                else:
                    # **/b  ->  (?:[^/]+/)*b   (zero segments allowed)
                    pieces.append("(?:[^/]+/)*")
                    pending_sep = False
            i = j + 1
            continue

        if segment == "":
            raise GlobError(
                "empty path segment in glob pattern: %r" % (pattern,), pattern
            )
        body = _translate_segment(segment, pattern)
        pieces.append(("/" + body) if pending_sep else body)
        pending_sep = True
        i += 1

    return "".join(pieces)


def _translate_segment(segment, pattern):
    """Translate a single path segment (no ``/``) into a regex body."""
    out = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "*":
            # A single "*" never crosses "/". Two or more in a row are "**" and
            # do cross it, even when the run shares a segment with other
            # characters: "src/**.py" matches "src/a/b.py". A bare "**" segment
            # is handled by the caller, which additionally requires at least one
            # path component after a trailing "**".
            start = i
            while i < n and segment[i] == "*":
                i += 1
            out.append(".*" if i - start > 1 else "[^/]*")
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            body, i = _translate_class(segment, i, pattern)
            out.append(body)
            continue
        if ch == "]":
            out.append("\\]")
            i += 1
            continue
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


def _translate_class(segment, start, pattern):
    """Translate ``[...]`` starting at *start*; returns (regex, next_index)."""
    i = start + 1
    n = len(segment)
    negate = False
    if i < n and segment[i] in "!^":
        negate = True
        i += 1
    body_start = i
    if i < n and segment[i] == "]":
        # A "]" immediately after "[" or "[!" is a literal member.
        i += 1
    while i < n and segment[i] != "]":
        i += 1
    if i >= n:
        raise GlobError(
            "unterminated character class '[' in glob pattern: %r" % (pattern,), pattern
        )
    body = segment[body_start:i]
    i += 1  # consume "]"

    if body == "":
        # "[]" cannot match anything; keep it literal rather than emitting an
        # invalid regex.
        return re.escape(segment[start:i]), i

    # Escape backslashes so a literal "\" inside the class stays literal, and
    # protect "^" in a non-negated class from being read as negation.
    escaped = body.replace("\\", "\\\\")
    if escaped.startswith("^") and not negate:
        escaped = "\\^" + escaped[1:]
    # Never let a character class match the path separator.
    if negate:
        return "[^/%s]" % escaped, i
    return "[%s]" % escaped, i


def match(pattern, path):
    """Return True when *path* matches the single glob *pattern*."""
    return compile_pattern(pattern).match(path)


def match_any(patterns, path):
    """Return True when *path* matches at least one pattern in *patterns*."""
    for pattern in patterns:
        if compile_pattern(pattern).match(path):
            return True
    return False


def matching_patterns(patterns, path):
    """Return the patterns from *patterns* that match *path*, in input order."""
    return [p for p in patterns if compile_pattern(p).match(path)]


def cache_clear():
    """Drop the compiled-pattern cache. Used by tests."""
    _CACHE.clear()
    _CACHE_HITS[0] = 0
    _CACHE_MISSES[0] = 0


def cache_info():
    """Return ``(size, hits, misses)`` for the compiled-pattern cache."""
    return (len(_CACHE), _CACHE_HITS[0], _CACHE_MISSES[0])

"""Package entry point.

Both spellings work, and both end up here:

    python3 -m tools.gotdocs check --staged
    python3 tools/gotdocs check --staged

The second form runs this file with ``tools/gotdocs`` itself on ``sys.path``
and no package context, so relative imports are unavailable. The fallback below
puts the repository root on ``sys.path`` and imports the package by name.
"""

import sys

MIN_PYTHON = (3, 9)


def _version_error(version_info):
    """Return the message for a too-old interpreter, or None.

    Checked before the package is imported so an old interpreter gets one
    readable line instead of an import-time failure further in.
    """
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return None
    return (
        "gotdocs: python %d.%d is too old; gotdocs needs python %d.%d or newer\n"
        "gotdocs: set GOTDOCS_PYTHON to a newer interpreter, "
        "for example: GOTDOCS_PYTHON=/usr/local/bin/python3 bin/gotdocs status\n"
        % (version_info[0], version_info[1], MIN_PYTHON[0], MIN_PYTHON[1])
    )


_TOO_OLD = _version_error(sys.version_info)
if _TOO_OLD is not None:  # pragma: no cover - needs an old interpreter
    sys.stderr.write(_TOO_OLD)
    raise SystemExit(2)

if __package__ in (None, ""):  # `python3 tools/gotdocs`
    import os

    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.dirname(os.path.dirname(_here))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from tools.gotdocs.cli import main
else:
    from .cli import main


if __name__ == "__main__":
    sys.exit(main())

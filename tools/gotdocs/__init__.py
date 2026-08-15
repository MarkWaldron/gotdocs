"""gotdocs — keep a repository's documentation honest about its code.

Vendored, stdlib-only (Python 3.9+), zero third-party dependencies, no network.

Each document under a configured root declares in its frontmatter the set of
files it describes (``covers``). When a changed code file matches those globs
the document is *impacted*; an impacted document that was neither edited nor
re-verified at the head sha is *stale*, and stale documents are what the hooks
and CI report.

Public entry point:

.. code-block:: python

    from tools.gotdocs.cli import main
    raise SystemExit(main())

or from a shell::

    bin/gotdocs check --staged
"""

__all__ = ["__version__"]

__version__ = "1"

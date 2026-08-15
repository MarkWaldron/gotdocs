"""Stdlib unittest suite for gotdocs.

Run from the repository root::

    python3 -m unittest discover -s tools/gotdocs/tests

Every test that needs git builds its own throwaway repository under
:mod:`tempfile`. Nothing here reads or writes the repository the suite is
checked into.
"""

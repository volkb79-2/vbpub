"""Vendored DAMON library.

``damon_analysis.py`` here is a byte-identical copy of
``scripts/damon-analysis/lib/damon_analysis.py`` (SPDX GPL-2.0, upstream of
the sibling tool). It is vendored, not published as its own wheel, so the
cgprofile wheel is self-contained. ``tests/test_damon_vendor.py`` fails when
the source and the copy differ: change the source, then copy it here.
"""

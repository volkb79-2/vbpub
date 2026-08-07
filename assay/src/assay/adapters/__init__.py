"""Language adapters — the only place a source language's own syntax may
live (DESIGN-GUIDE §11).

Nothing under this package is imported by :mod:`assay.evaluate` or
:mod:`assay.registry` by name: both depend only on
:class:`assay.adapters.base.LanguageAdapter`'s structural shape, never on a
concrete adapter module. A concrete adapter (Python, Go, ...) registers
itself explicitly through :mod:`assay.registry` — there is no implicit
discovery or default here, which is exactly what lets an unknown declared
language be REFUSED rather than silently mapped to whichever adapter happens
to be importable (A-097, O2).

This package deliberately ships no real adapter (P05's own scope note: "no
real language adapter belongs here"). Python lands in P06, Go in P08.
"""

from __future__ import annotations

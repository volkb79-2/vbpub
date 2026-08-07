"""One module per coverage FORMAT, never per language (DESIGN-GUIDE §11).

Each sibling module here exports exactly two functions with the same
signature, so :mod:`assay.coverage` can register them uniformly:

* ``sniff(text: str) -> bool`` — does *text*'s content match THIS format's
  own signature. Cheap and structural; never a full validation pass.
* ``parse(text: str) -> assay.coverage_parsers.model.CoverageProfile`` —
  strict parsing. Raises :class:`~assay.errors.AssayError`
  (``ERROR``/``UNREADABLE_ARTIFACT``) on any malformed record.

No module here imports another sibling module or :mod:`assay.coverage` —
only :mod:`assay.coverage_parsers.model` (the shared normalized types) and
:mod:`assay.errors`. That keeps the dependency graph a strict DAG:
``assay.coverage`` imports every module in this package to build its
registry; nothing in this package imports back.
"""

from __future__ import annotations

"""One module per coverage FORMAT, never per language (DESIGN-GUIDE §11).

Each sibling module here exports exactly two functions with the same
signature, so :mod:`assay.coverage` can register them uniformly:

* ``sniff(text: str) -> bool`` — does *text*'s content match THIS format's
  own signature. Cheap and structural; never a full validation pass.
* ``parse(text: str, *, producer: str | None) ->
  assay.coverage_parsers.model.CoverageProfile`` — strict parsing. Raises
  :class:`~assay.errors.AssayError` (``ERROR``/``UNREADABLE_ARTIFACT``) on
  any malformed record.

**Why ``parse`` takes the PRODUCER (B045, schema v9).** A format is the shape
of a document; a producer is the toolchain that wrote it, and one format can
have several producers that disagree about what a field MEANS.
``coverage-istanbul-json`` is the measured case: ``@vitest/coverage-istanbul``
writes ``branchMap`` entries typed ``if``/``cond-expr``/``binary-expr`` with
one location and one count per ARM, while ``@vitest/coverage-v8`` and ``c8``
write entries all typed ``branch`` describing v8's executed RANGES (A-344).
The same bytes therefore mean two different things, and only the lane's
``judge.coverage.producer`` declaration says which (A-007: declared, never
sniffed).

The parameter is on EVERY parser's signature, not only on the one format that
reads it today, and it is keyword-only with **no default**. Both are
deliberate: a uniform protocol is what lets :mod:`assay.coverage` register the
five modules without a per-format special case, and a missing default turns
"a new caller forgot to pass the producer" into a ``TypeError`` at the call
site instead of a silently arc-less profile. Four of the five modules accept
it and ignore it, which is honest — their formats have exactly one producer
(or none whose disagreements assay has measured), so there is nothing for
them to branch on. Validating that the name belongs to the format's
vocabulary is NOT a parser's job: :mod:`assay.config`'s loader already
refuses an unknown or out-of-format producer at config-load time (A-068),
long before any artifact is read.

No module here imports another sibling module or :mod:`assay.coverage` —
only :mod:`assay.coverage_parsers.model` (the shared normalized types) and
:mod:`assay.errors`. That keeps the dependency graph a strict DAG:
``assay.coverage`` imports every module in this package to build its
registry; nothing in this package imports back.
"""

from __future__ import annotations

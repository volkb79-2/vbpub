"""Tier-2 ADJUDICATED evidence: read a declared, closed-vocabulary document a
third-party tool wrote, and render THAT tool's own decision (B004/A-430).

DESIGN-GUIDE §3's Tier 2 was defined as assay **"invokes"** a declared
third-party tool -- true of a future adjudicator, false of this one. assay
never runs ciu: A-030 forbids assay orchestrating infrastructure, and at
S3/S4 assay runs *inside* a container where the docker socket is not
reachable. So what this module does is narrower and entirely mechanical:
read one file the caller's own harness produced *before* the lane ran, parse
it against `ciu provenance --json`'s closed shape, and render CIU'S OWN
decision -- never assay's -- as an :class:`~assay.verdict.Evidence` entry.
The one fact assay itself checks, rather than relaying, is that the
document's ``commit_under_test`` names the same commit assay resolved as
HEAD for this run (the carve's §2: *"assay confirmed the commit ciu was
talking about is the commit assay measured"*).

Because HEAD arrives already resolved -- the CLI's documented sequence
(``lane/output reserved -> deadline -> HEAD -> attestation -> adjudication ->
adapter -> command -> emit once``) resolves it before this module is ever
called -- :func:`evaluate_provenance` needs no Git call at all and is a PURE,
TOTAL function of the document's bytes and that one string. It never raises:
every malformed, unreadable, absent, or non-green shape renders a judged
``(Outcome, ReasonCode | None)`` pair, the same "never raises for a judged
outcome" contract :func:`assay.attestation.evaluate_attestation` states for
its own tier.

**§3.3's rule, the single most important one in this module:** assay
validates *exactly* what it consumes -- ``schema_version``, ``overall``, and,
on the green path only, ``commit_under_test`` -- and asserts NOTHING about
``containers``, ``status``, ``labelled_revision``, ``image`` or
``tree_state``. Every one of those was MEASURED (A-334; the carve's §9,
``nyxloom-trove/W2-CARVE-B004-provenance-verified.md``) to carry a real shape
that a plausible tightening would refuse: ``labelled_revision`` is not a sha
grammar (a real value is ``"refs/heads/master"``), ``image`` is not
``name:tag`` (a real value is a bare image id), ``containers`` is JSON
``null`` -- not ``[]`` -- whenever ciu's own enumeration could not run. ciu
decided; assay adjudicates ciu's decision.

**The registry (A-078).** :data:`ADJUDICATORS` is a one-entry closed mapping,
``"image-provenance" -> evaluate_provenance``, consulted at RUN
(:func:`load_adjudicated_evidence` dispatches through it) and, at LOAD, via
:data:`assay.vocabulary.ADJUDICATED_EVIDENCE_KEYS` -- NOT by
:mod:`assay.config` importing this module directly. :mod:`assay.verdict`
imports FROM `config.py` and this module imports FROM `verdict.py` (for
`Evidence`/`EvidenceDeclaration`, exactly as :mod:`assay.attestation` already
does one tier over), so `config -> adjudication -> verdict -> config` would
be a real import cycle; `assay.vocabulary` is a leaf both modules can import
without opening it. `vocabulary.ADJUDICATED_EVIDENCE_KEYS` and this module's
own :data:`ADJUDICATORS` are therefore two statements of one fact, checked
against each other by ``tests/test_adjudication_registry.py`` rather than
left to drift silently -- the same shape DA-R1/A-406 already established for
`STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE`, applied here for an import-cycle
reason rather than DA-R1's `assay.adapters`-specific O2 guarantee. A dict of
one reachable entry, not a plugin-discovery mechanism: A-078's objection to a
registry was that a zero-integration one "could only validate its own empty
set", which does not apply once a real key reaches it from a loadable
``assay.toml``.

**Independent of :mod:`assay.attestation`, deliberately.** ``config.py``'s
own comment states the rule this module follows one tier over: "config.py
and attestation.py stay independent readers of the same closed grammar, and
neither trusts the other to have already validated it." This module is a
third independent reader of the ``*_dir`` grammar attestation_dir already
uses, not an import of attestation.py's copy -- the carve's own W3 sketch
("promote `_validate_attestation_dir` to a shared `_validate_evidence_dir`")
predates that shipped comment and is superseded by it; sharing across
`config.py`/`attestation.py` was examined and rejected before this module was
written, and duplicating a THIRD reader is the smaller deviation, recorded
here rather than silently done.

No function in this module ever constructs a :class:`~assay.verdict.Claim` --
only :class:`~assay.verdict.Evidence`, exactly as :mod:`assay.attestation`
does for Tier 3.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import git, safeio
from .errors import AssayError, Outcome, ReasonCode
from .verdict import Evidence, EvidenceDeclaration

__all__ = [
    "ADJUDICATORS",
    "Adjudicator",
    "MAX_ADJUDICATION_BYTES",
    "MAX_EVIDENCE_DECLARATIONS",
    "evaluate_provenance",
    "load_adjudicated_evidence",
]

#: The measured real document is 2,377-3,512 bytes (carve §3.1/§9); 1 MiB
#: admits roughly 8,800 containers, which is not a provenance verdict.
MAX_ADJUDICATION_BYTES = 1_048_576

#: Duplicated from :mod:`assay.attestation`'s own bound, independently, for
#: the same "neither trusts the other" reason `config.py`'s comment states
#: for the directory grammar (see module docstring).
MAX_EVIDENCE_DECLARATIONS = 64

#: DA-R12: ciu's `schema_version` integer, accepted as the CLOSED set {1, 2}
#: through ONE parser -- measured (the carve's re-capture, generation 4's
#: REPORT "B004's ciu assets, RE-CAPTURED") to be the ONLY schema-relevant
#: delta between ciu 6.0.3 (schema 1) and 7.10.1 (schema 2): keys, container
#: count, status vocabulary (`unlabelled` already present in schema 1) and
#: `overall` are identical. Refusing `1` would be a hard cut against a
#: measured-identical shape; a lane-declared version would be config surface
#: for nothing.
_ACCEPTED_SCHEMA_VERSIONS = (1, 2)

#: ciu's own closed `overall` vocabulary (measured against `deploy.py`'s
#: producer, carve §3.3/§9 M5): six values, exactly one green.
_GREEN_OVERALL = "verified-match"
_KNOWN_OVERALL_VALUES = frozenset(
    {
        "verified-match",
        "mismatch",
        "not-verified-dirty",
        "not-verified-unknown",
        "not-verified-no-evidence",
        "refused-no-identity",
    }
)

#: ciu emits `git rev-parse --short=8 HEAD` (carve §9 M10) -- a MINIMUM, not
#: an exact width, so the grammar is 8..40 hex digits, never "exactly 8".
_COMMIT_RE = re.compile(r"^[0-9a-f]{8,40}$")


def evaluate_provenance(document_bytes: bytes, head: str) -> tuple[Outcome, ReasonCode | None]:
    """The ONE registered ``image-provenance`` adjudicator (§3.2/§3.4).

    Total over *document_bytes*: decoding, parsing, and every closed-grammar
    check happen here, and none of them raises -- a malformed, unreadable, or
    non-green document renders a judged terminal exactly like a legible one.
    The caller (:func:`load_adjudicated_evidence`) is the only place an
    exception can originate, and only for the file-level cases (an absent,
    symlinked, oversized, or permission-denied path) that never reach this
    function at all, because `document_bytes` was never produced.
    """
    try:
        text = document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Row 5: present but untrustworthy -- the same terminal
        # `safeio.read_bounded_input` would raise for a race or an oversized
        # read, stated here because a decode failure is caught at this layer
        # instead.
        return Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        # Row 5: invalid JSON is "present but unreadable", the same
        # classification :func:`assay.attestation.parse_attestation` gives
        # invalid JSON one tier over. `RecursionError` (R-2/round-2) is a
        # real member of this class, not a hypothetical: `json.loads` raises
        # it -- as a genuine C-stack-depth check on current CPython, not
        # merely `sys.getrecursionlimit()` -- for a pathologically nested
        # document well inside `MAX_ADJUDICATION_BYTES`'s 1 MiB bound, so the
        # loader's size limit alone does not exclude it. Legible-but-too-deep
        # is "present but unreadable", the same as unparseable JSON, not a
        # new terminal and not a raise this TOTAL function is allowed to let
        # through.
        return Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT
    if not isinstance(document, dict):
        # Row 6: legible JSON that is not the shape ciu's schema describes.
        # Deliberately FORMAT_MISMATCH and not UNREADABLE_ARTIFACT -- carve
        # N1's recorded asymmetry with the attested pipeline: this document
        # decoded cleanly, so "unreadable" would be a false diagnosis of a
        # document assay read perfectly well and judged to be the wrong
        # shape.
        return Outcome.ERROR, ReasonCode.FORMAT_MISMATCH
    schema_version = document.get("schema_version")
    # `bool` is a subtype of `int` in Python (`True == 1`); excluded
    # explicitly so a document carrying JSON `true` cannot pass this check by
    # coincidence -- a check this project has no measured need for, but a
    # silent accident it must not have either.
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in _ACCEPTED_SCHEMA_VERSIONS
    ):
        return Outcome.ERROR, ReasonCode.FORMAT_MISMATCH
    overall = document.get("overall")
    if overall not in _KNOWN_OVERALL_VALUES:
        # Carve O5: an unrecognised `overall` is refused, not guessed. A
        # `.get(overall, PROVENANCE_UNVERIFIED)` default would make a
        # genuine non-green verdict and a future ciu vocabulary addition
        # indistinguishable from each other.
        return Outcome.ERROR, ReasonCode.FORMAT_MISMATCH
    if overall != _GREEN_OVERALL:
        # Row 8: all five non-green states collapse to ONE terminal (carve
        # §4.2) -- the discriminating detail lives in the retained input
        # document, never re-told in assay's own vocabulary.
        # F7(d): `commit_under_test` may legitimately be JSON `null` on a
        # non-green document (ciu's own `refused-no-identity` shape), so it
        # is never inspected on this branch.
        return Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED
    commit_under_test = document.get("commit_under_test")
    if not isinstance(commit_under_test, str) or not _COMMIT_RE.fullmatch(commit_under_test):
        # Row 6, green-path-only per the carve's own table: a `verified-match`
        # document is not fully ciu's schema without a legible commit to bind.
        return Outcome.ERROR, ReasonCode.FORMAT_MISMATCH
    if not head.startswith(commit_under_test):
        # Row 7 / carve O4: a document from a commit that is not a prefix of
        # THIS run's HEAD does not attest to it -- staleness or the wrong
        # commit, indistinguishable from here and both honestly
        # PROVENANCE_UNVERIFIED.
        return Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED
    return Outcome.PASS, None


#: The one shape this callable takes, named for the registry's own type
#: (§3.2): `(document_bytes, head) -> (outcome, reason_code)`.
Adjudicator = Callable[[bytes, str], "tuple[Outcome, ReasonCode | None]"]

#: A dict of ONE entry, deliberately -- A-078's objection to a registry was
#: that a zero-integration one "could only validate its own empty set",
#: which a one-entry registry with a reachable unknown-key refusal does not
#: suffer from (any `assay.toml` naming `key = "no-such-adjudicator"` reaches
#: it, per :mod:`assay.config`'s load-time check).
ADJUDICATORS: Mapping[str, Adjudicator] = {"image-provenance": evaluate_provenance}


def load_adjudicated_evidence(
    project_root: Path,
    *,
    head: str,
    declared: Sequence[EvidenceDeclaration],
    adjudication_dir: str,
    remaining: git.Remaining,
) -> tuple[Evidence, ...]:
    """The entry point a caller (:mod:`assay.cli`, or this module's own
    tests) uses directly -- mirrors
    :func:`assay.attestation.load_attested_evidence`'s "one result per
    declaration, same order" contract for the Tier-2 sibling.

    Repeats the closed source/key grammar at this public boundary (this
    function does not assume every caller came through :mod:`assay.config`);
    maps misuse to ``ERROR``/``BAD_LANE_CONFIG``.

    Every declared identity's document is read through the descriptor-safe
    seam (:func:`assay.safeio.read_bounded_input`) before its adjudicator
    runs. Absence (`None`, the safe seam's own "no producer supplied this
    file" contract) renders ``NO_MEASUREMENT``/``PROVENANCE_UNVERIFIED``
    (carve row 4) without ever reaching :func:`evaluate_provenance`; a
    symlink, non-directory parent, non-regular final object, permission
    failure, race, or oversized read raises, caught here and rendered
    ``ERROR``/``UNREADABLE_ARTIFACT`` (row 5).

    This function makes NO Git call of its own -- `safeio.read_bounded_input`
    is a single bounded local read per declared identity, never an unbounded
    external process. *remaining* is still accepted and sampled once at
    entry and once before returning (A-212: ONE `LaneDeadline` governs the
    whole lane, every stage of it, not only the stages that shell out) --
    this is what lets a mixed `[attested, adjudicated]` lane whose Git-heavy
    attested pass already exhausted the budget raise `LANE_TIMEOUT` HERE
    rather than silently doing file work on a dead deadline, exactly the
    atomic-timeout contract :mod:`assay.cli`'s caller preserves across the
    two sequential loaders by discarding whichever loader's results already
    landed and rendering EVERY declared identity (both sources) the same
    payload-free `BUDGET_EXCEEDED`/`LANE_TIMEOUT` pair.
    """
    remaining()
    for item in declared:
        if item.source != "adjudicated":
            raise AssayError(
                f"evidence declaration {item.identity} is not "
                f"source='adjudicated'; this loader handles only adjudicated "
                f"evidence -- attested evidence has its own loader",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        if item.key not in ADJUDICATORS:
            raise AssayError(
                f"evidence declaration {item.identity}: {item.key!r} is not "
                f"a registered adjudicator; expected one of "
                f"{sorted(ADJUDICATORS)}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
    if len(declared) > MAX_EVIDENCE_DECLARATIONS:
        raise AssayError(
            f"{len(declared)} evidence declarations exceeds the "
            f"{MAX_EVIDENCE_DECLARATIONS} bound",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )

    results: list[Evidence] = []
    for item in declared:
        relative_path = f"{adjudication_dir}/{item.key}.json"
        try:
            # `read_bounded_input` is a single bounded LOCAL read with no
            # `remaining=` parameter of its own -- it cannot raise
            # `LANE_TIMEOUT` (unlike `git`'s subprocess calls, which
            # `attestation.load_attested_evidence`'s equivalent try/except
            # must distinguish from a genuine read failure). Every exception
            # this call can raise is therefore a read failure.
            raw = safeio.read_bounded_input(
                project_root, relative_path, limit=MAX_ADJUDICATION_BYTES
            )
        except AssayError:
            results.append(
                Evidence(
                    source="adjudicated",
                    key=item.key,
                    status=Outcome.ERROR,
                    verified_by_assay=False,
                    reason_code=ReasonCode.UNREADABLE_ARTIFACT,
                )
            )
            continue
        if raw is None:
            results.append(
                Evidence(
                    source="adjudicated",
                    key=item.key,
                    status=Outcome.NO_MEASUREMENT,
                    verified_by_assay=False,
                    reason_code=ReasonCode.PROVENANCE_UNVERIFIED,
                )
            )
            continue
        adjudicator = ADJUDICATORS[item.key]
        outcome, reason_code = adjudicator(raw, head)
        results.append(
            Evidence(
                source="adjudicated",
                key=item.key,
                status=outcome,
                verified_by_assay=False,
                reason_code=reason_code,
            )
        )
        # Sampled after every declared identity, exactly as
        # `load_attested_evidence` samples after every staged read -- a
        # batch of up to `MAX_EVIDENCE_DECLARATIONS` identities stays bounded
        # by the SAME lane deadline that bounds everything else in the lane,
        # never by "however long file I/O happens to take".
        remaining()
    # Sampled once more, unconditionally, immediately before returning --
    # `load_attested_evidence`'s own final call, including for an EMPTY
    # *declared* (whose loop body above never runs at all): expiry between
    # entry and return is observed here rather than silently ignored.
    remaining()
    return tuple(results)

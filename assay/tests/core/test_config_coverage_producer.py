"""B045 — ``judge.coverage.producer``: the coverage PRODUCER as a DECLARED
fact, from a closed vocabulary that is closed PER FORMAT.

The decision this module tests is the one B038 and B040 exist to force. A
coverage FORMAT says what shape a document has; it does not say what wrote it,
and ``coverage-istanbul-json`` is written by several producers that disagree
about what parts of it MEAN — about ``branchMap`` (A-344) and about whether a
line ran at all (A-346). Through v8 a lane could declare only the format, so
assay had exactly two honest options for both disagreements: refuse to answer
(``branches = None``, ``branch_capability = "unavailable"``) or sniff the
producer out of the artifact's own shape, which is the declaration-versus-
sniffing collapse ``coverage.py``'s module docstring forbids (A-007).

Every test below is a REFUSAL or an ACCEPTANCE at load time, i.e. before the
lane's command can run. That placement is the point: a lane that names an
unsound producer must be told so while a human is editing ``assay.toml``, not
after a gate has already reported a green verdict over coverage nothing has
qualified.

Companion modules: ``test_config_coverage_format.py`` (the FORMAT half of the
same key), ``test_coverage_istanbul_provider_accuracy.py`` (the measured
evidence behind the ``vitest-v8`` refusal).
"""

from __future__ import annotations

import pytest
from conftest import R1_LANE, Project, set_key

from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode
from assay.vocabulary import (
    ARC_BEARING_COVERAGE_PRODUCERS,
    COVERAGE_PRODUCERS_BY_FORMAT,
    COVERAGE_PRODUCER_REQUIRED_FORMATS,
    REFUSED_COVERAGE_PRODUCERS,
)


def _coverage(project: Project, table: str):
    return load_lane_file(project.write(set_key(R1_LANE, "coverage", table)))


def _refusal(project: Project, table: str) -> LaneConfigError:
    with pytest.raises(LaneConfigError) as excinfo:
        _coverage(project, table)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    return excinfo.value


# --------------------------------------------------------------------------
# The accepted shapes
# --------------------------------------------------------------------------


def test_an_istanbul_producer_is_accepted_and_recorded_verbatim(project: Project):
    lane_file = _coverage(
        project,
        '{ format = "coverage-istanbul-json", artifact = "cov.json", '
        'producer = "istanbul" }',
    )
    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.producer == "istanbul"


def test_an_omitted_producer_stays_none_on_a_format_that_allows_it(project: Project):
    """``None`` here means ABSENT FROM THE FILE, never "assay chose one".

    ``coverage-py-json`` has exactly one producer, so there is no second
    producer for an omission to silently pick wrongly between — which is
    precisely why the key is optional for it and required for
    ``coverage-istanbul-json``.
    """
    lane_file = _coverage(
        project, '{ format = "coverage-py-json", artifact = "cov.json" }'
    )
    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.producer is None


def test_the_only_coverage_py_producer_is_accepted_when_declared(project: Project):
    lane_file = _coverage(
        project,
        '{ format = "coverage-py-json", artifact = "cov.json", '
        'producer = "coverage.py" }',
    )
    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.producer == "coverage.py"


def test_as_declared_reproduces_the_producer_key_and_omits_it_when_absent(
    project: Project,
):
    """A-051/`Lane.as_declared`'s own contract: omitted, never null.

    This is the mechanical form of "no key present that the file did not
    declare" — the reconstruction must compare equal to what was written, so
    a producer that was never declared must not appear as ``None``.
    """
    declared = _coverage(
        project,
        '{ format = "coverage-istanbul-json", artifact = "cov.json", '
        'producer = "istanbul" }',
    ).lane("package").judge
    assert declared is not None and declared.coverage is not None
    assert declared.coverage.as_declared() == {
        "format": "coverage-istanbul-json",
        "artifact": "cov.json",
        "producer": "istanbul",
    }

    omitted = _coverage(
        project, '{ format = "coverage-py-json", artifact = "cov.json" }'
    ).lane("package").judge
    assert omitted is not None and omitted.coverage is not None
    assert omitted.coverage.as_declared() == {
        "format": "coverage-py-json",
        "artifact": "cov.json",
    }
    assert "producer" not in omitted.coverage.as_declared()


# --------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------


def test_istanbul_json_refuses_an_omitted_producer_naming_the_disagreement(
    project: Project,
):
    """The REQUIRED half of B045's contract.

    DESIGN-GUIDE §5's test applied literally: if an implied ``istanbul`` were
    wrong (the lane really runs ``@vitest/coverage-v8``), nothing would fail
    loudly — the run would PASS over lines that never executed. So there is
    no value assay may imply, and the omission is refused.
    """
    error = _refusal(
        project, '{ format = "coverage-istanbul-json", artifact = "cov.json" }'
    )
    message = str(error)
    assert "judge.coverage.producer" in message
    # The message must say WHY, not merely that a field is missing.
    assert "A-344" in message and "A-346" in message
    assert "istanbul" in message
    # ...and it must not offer a producer assay refuses as the fix.
    for refused in REFUSED_COVERAGE_PRODUCERS:
        assert f"'{refused}'" not in message


@pytest.mark.parametrize("producer", sorted(REFUSED_COVERAGE_PRODUCERS))
def test_a_known_but_refused_producer_is_refused_BY_NAME_with_its_reason(
    producer: str, project: Project
):
    """B040(b): refused by NAME, with the reason and the fix.

    This is `WITHDRAWN_MUTATION_OPERATORS`' pattern one field over. "That is
    not a known producer" would be a false statement — assay knows all three
    of these perfectly well — and a consumer given it would reasonably
    conclude they had made a typo rather than that their coverage is unsound.
    """
    error = _refusal(
        project,
        f'{{ format = "coverage-istanbul-json", artifact = "cov.json", '
        f'producer = "{producer}" }}',
    )
    message = str(error)
    assert producer in message
    assert "REFUSES" in message
    # The whole shipped reason string reaches the consumer, not a summary.
    assert REFUSED_COVERAGE_PRODUCERS[producer] in message
    # Every refusal names a concrete fix, not just a complaint.
    assert "istanbul" in message


def test_the_vitest_v8_refusal_names_A_346_and_the_provider_fix(project: Project):
    """B040(b) calls out this one specifically, so it gets its own test
    rather than only the parametrized sweep above: the reason must be the
    MEASURED defect and the fix must be the provider switch."""
    message = str(
        _refusal(
            project,
            '{ format = "coverage-istanbul-json", artifact = "cov.json", '
            'producer = "vitest-v8" }',
        )
    )
    assert "A-346" in message
    assert "never-executed lines as executed" in message
    assert "provider: 'istanbul'" in message
    assert "probe-js-provider-defect" in message


def test_an_unknown_producer_is_refused_and_the_message_lists_the_open_set(
    project: Project,
):
    message = str(
        _refusal(
            project,
            '{ format = "coverage-istanbul-json", artifact = "cov.json", '
            'producer = "not-a-real-producer" }',
        )
    )
    assert "not-a-real-producer" in message
    assert "closed per" in message
    assert "['istanbul']" in message


def test_a_producer_from_ANOTHER_formats_vocabulary_is_refused(project: Project):
    """The vocabulary is closed PER FORMAT, not globally.

    ``coverage.py`` is a perfectly real producer name — of a different
    format. Accepting it here would mean the key answered "is this a producer
    somewhere?" while its message claims "this is the producer of this
    artifact": AGENTS.md's own name-for-object anti-pattern.
    """
    message = str(
        _refusal(
            project,
            '{ format = "coverage-istanbul-json", artifact = "cov.json", '
            'producer = "coverage.py" }',
        )
    )
    assert "coverage.py" in message
    assert "coverage-istanbul-json" in message


def test_a_format_with_no_open_vocabulary_refuses_any_producer(project: Project):
    """DESIGN-GUIDE §5 — no speculative names.

    **``go-cover`` used to head this list and no longer does (A-398).** It
    was closed by A-354, which flagged itself as "the B045 call most open to
    challenge" and named its own reopening condition: the Go wave, which
    could measure what the two names mean. That wave has landed and ``go``
    is registered (A-394), so the names are now backed by a lane this build
    actually runs — see the acceptance tests below.

    ``lcov`` and ``cobertura`` remain closed for the original reason, and
    they are what keeps this test honest: the mechanism that refuses an
    unexplainable producer name is still live and still tested, so opening
    one format did not quietly open the door for every format.
    """
    for fmt, producer in (
        ("lcov", "istanbul"),
        ("cobertura", "istanbul"),
    ):
        message = str(
            _refusal(
                project,
                f'{{ format = "{fmt}", artifact = "cov.out", '
                f'producer = "{producer}" }}',
            )
        )
        assert "no open producer vocabulary" in message
        assert fmt in message


@pytest.mark.parametrize("producer", ["go-test", "covdata"])
def test_both_go_cover_producers_are_accepted_and_recorded_verbatim(
    project: Project, producer: str
):
    """(B047 item 3 / A-398) The vocabulary A-354 deliberately closed, now
    open — and parametrized over BOTH names, because a test that only proved
    ``go-test`` would leave ``covdata`` in exactly the speculative-name state
    A-354 refused to ship.

    ``covdata`` is not a synonym for ``go-test``. It names
    ``go tool covdata textfmt`` over the counter data a ``go build -cover``
    binary writes while doing real work — evidence about a running program,
    not about a test binary — which is the integration path (an S3 lane).
    Both emit ``cmd/cover``'s own textfmt, so assay's parser is
    producer-independent here; the name records the PROVENANCE of the
    evidence, which a reviewer cares about even when the bytes are
    interchangeable.
    """
    lane_file = _coverage(
        project,
        f'{{ format = "go-cover", artifact = "cover.out", '
        f'producer = "{producer}" }}',
    )
    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.producer == producer


def test_go_cover_is_open_but_still_CLOSED_against_a_name_it_does_not_know(
    project: Project,
):
    """The anti-vacuity control for the pair above. Opening a vocabulary is
    not the same as accepting free-form text, and the difference is the whole
    point of a per-format table: ``gcov`` is a real coverage tool, a
    plausible thing to type, and not a producer of this format."""
    message = str(
        _refusal(
            project,
            '{ format = "go-cover", artifact = "cover.out", producer = "gcov" }',
        )
    )
    assert "gcov" in message
    # It must fail as an UNKNOWN NAME for a known vocabulary, never as
    # "this format has no vocabulary" -- that message is now false of
    # `go-cover` and would send a reader to the wrong fix.
    assert "no open producer vocabulary" not in message


def test_go_covers_producer_is_OPTIONAL_because_its_two_producers_agree(
    project: Project,
):
    """(A-398) Why ``go-cover`` is absent from
    ``COVERAGE_PRODUCER_REQUIRED_FORMATS`` even though it now has two
    producers — the count is not the test, DISAGREEMENT is.

    ``coverage-istanbul-json`` requires the key because ``vitest-v8`` and
    ``istanbul`` genuinely disagree about whether a line ran (A-346), so no
    implied value is correct in every context. ``go-test`` and ``covdata``
    emit the same instrumenter's same textfmt, so there is no reading of a
    ``go-cover`` artifact that depends on which one wrote it — and requiring
    a key that changes no answer would be ceremony, which DESIGN-GUIDE §5
    treats as its own kind of dishonesty.
    """
    assert "go-cover" not in COVERAGE_PRODUCER_REQUIRED_FORMATS
    lane_file = _coverage(
        project, '{ format = "go-cover", artifact = "cover.out" }'
    )
    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.producer is None


def test_an_empty_producer_string_is_refused(project: Project):
    message = str(
        _refusal(
            project,
            '{ format = "coverage-istanbul-json", artifact = "cov.json", '
            'producer = "" }',
        )
    )
    assert "'judge.coverage.producer' is empty" in message


def test_a_non_string_producer_is_refused(project: Project):
    message = str(
        _refusal(
            project,
            '{ format = "coverage-istanbul-json", artifact = "cov.json", '
            "producer = 7 }",
        )
    )
    assert "judge.coverage.producer" in message
    assert "must be a string" in message


def test_an_unknown_coverage_key_still_names_the_full_expected_set(project: Project):
    """Adding an optional third key must not weaken the unknown-key refusal,
    and the refusal's own "expected only" list must now include it — a
    consumer who typo'd ``producers`` needs to see that ``producer`` exists.
    """
    message = str(
        _refusal(
            project,
            '{ format = "coverage-py-json", artifact = "cov.json", '
            'producers = "coverage.py" }',
        )
    )
    assert "producers" in message
    assert "format" in message and "artifact" in message and "producer" in message


# --------------------------------------------------------------------------
# Vocabulary invariants — the table itself, not one lane file
# --------------------------------------------------------------------------


def test_every_required_format_has_at_least_one_producer_a_lane_may_declare():
    """A superset-refusal guard (AGENTS.md's own anti-pattern 4).

    A format that REQUIRES a producer and whose entire vocabulary is refused
    would be a format no lane could ever load — a refusal whose condition
    also matches every legitimate state. That would be a configuration
    dead-end shipped as a feature.
    """
    for fmt in COVERAGE_PRODUCER_REQUIRED_FORMATS:
        vocabulary = COVERAGE_PRODUCERS_BY_FORMAT[fmt]
        declarable = [
            name for name in vocabulary if name not in REFUSED_COVERAGE_PRODUCERS
        ]
        assert declarable, f"{fmt} requires a producer but refuses every one it knows"


def test_every_refused_producer_belongs_to_some_formats_vocabulary():
    """A refused name that no format lists is unreachable: the refusal branch
    could never fire, and the reason string would be documentation nothing
    tests. Keeping the two tables in agreement is what makes the by-name
    refusals above real."""
    spellable = {
        name
        for vocabulary in COVERAGE_PRODUCERS_BY_FORMAT.values()
        for name in vocabulary
    }
    assert set(REFUSED_COVERAGE_PRODUCERS) <= spellable


def test_every_arc_bearing_producer_is_itself_declarable():
    """B038(a) turns on this set. A producer listed as arc-bearing but not
    declarable (or refused) would enable a branch path no lane could reach."""
    spellable = {
        name
        for vocabulary in COVERAGE_PRODUCERS_BY_FORMAT.values()
        for name in vocabulary
    }
    assert ARC_BEARING_COVERAGE_PRODUCERS <= spellable
    assert not (ARC_BEARING_COVERAGE_PRODUCERS & set(REFUSED_COVERAGE_PRODUCERS))

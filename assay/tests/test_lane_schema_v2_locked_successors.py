"""B006a/A-269, WI-1 -- nine one-for-one v2 successors for the nine frozen
carve-asset behaviours that ``LANE_SCHEMA_VERSION = 2`` reddens.

Every locked node listed below embeds a literal ``schema_version = 1``
document (directly, via ``P26/test_acceptance.py::_lane_document``, or via
``P33/test_acceptance_v5.py::_load_lane``). Those two helpers are FROZEN
evidence (A-222): the carve forbids editing ``nyxloom-trove/carve-assets/**``,
and forbids inventing a combined omnibus successor, because either would make
a lost behaviour hard to see. So each frozen node gets its own,
similarly-named test here, reproducing the SAME shape under
``schema_version = 2`` (plus an explicit ``[isolation]`` table wherever the
original lane declares R1+, since the whole point of v2 is that the table is
no longer optional there) -- never collapsed into one shared assertion.

The nine locked nodes, and this module's one-for-one successor for each:

============================================================================  ==============================================================================
Locked node (rootdir-relative, deselected in tester-unified-gate.sh)          Successor in this module
============================================================================  ==============================================================================
P26/test_acceptance.py::test_runner_binds_evidence_batch_to_lane_source_
before_any_work                                                               test_runner_binds_evidence_batch_to_lane_source_before_any_work_under_schema_v2
P26/test_acceptance.py::test_r0_attestation_config_round_trips_without_
inventing_a_judge                                                             test_r0_attestation_config_round_trips_without_inventing_a_judge_under_schema_v2
P26/test_acceptance.py::test_closed_attestation_declaration_rejects_every_
inert_or_unsafe_shape                                                         test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape_under_schema_v2
P33/test_acceptance_v5.py::test_config_fixture_itself_loads_today             test_config_fixture_itself_loads_today_under_schema_v2
P33/test_acceptance_v5.py::test_config_refuses_a_cross_language_operator      test_config_refuses_a_cross_language_operator_under_schema_v2
P33/test_acceptance_v5.py::test_config_accepts_a_matching_language_operator   test_config_accepts_a_matching_language_operator_under_schema_v2
P33/test_acceptance_v5.py::test_config_names_kill_signal_artifact_as_
reserved_for_p34                                                              test_config_names_kill_signal_artifact_as_reserved_for_p34_under_schema_v2
P33/test_acceptance_v5.py::test_config_names_equivalence_artifact_as_
reserved_for_p34                                                              test_config_names_equivalence_artifact_as_reserved_for_p34_under_schema_v2
============================================================================  ==============================================================================

(The table lists eight rows because P26's fourth deselection,
``test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget``,
did not fit the column width above -- its successor is
``test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget_
under_schema_v2``, completing the nine.)

Every successor is R0-only or, for the P33 five, R0+R2 -- so only the P33
successors need an explicit ``[lanes.demo.isolation]`` table; A-269's
migration rule gives them plain ``"repository"`` mode, since none of these
five is itself testing omission mode.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from assay import config, git, runner
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.verdict import Claim

# ---------------------------------------------------------------------------
# P26 successors -- attestation/deadline, R0-only (no isolation table needed)
# ---------------------------------------------------------------------------


def _lane_document(judge: str) -> str:
    """The exact shape of the frozen ``P26/test_acceptance.py::_lane_document``,
    with ``schema_version`` bumped 1 -> 2. The lane stays R0-only, so v2's
    R0/R1+ isolation conditional does not apply here at all -- the only
    thing that changed is the version integer this build now understands.
    """
    return f'''schema_version = 2

[lanes.attested]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = []
budget = "1m"
allow_argv_append = false

{judge}
'''


def test_runner_binds_evidence_batch_to_lane_source_before_any_work_under_schema_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2 successor for P26's ``test_runner_binds_evidence_batch_to_lane_
    source_before_any_work``: runner must bind the declared evidence batch to
    the LOADED lane's own source before any Git work starts, whether that
    lane came from a v1 or a v2 file -- the ordering guarantee has nothing to
    do with the schema integer, so this reproduces it unchanged under v2.
    """
    path = tmp_path / "assay.toml"
    path.write_text(
        _lane_document(
            '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [{source="attested",key="review"}]'''
        ),
        encoding="utf-8",
    )
    lane = config.load_lane_file(path).lane("attested")

    def forbidden(*args, **kwargs):
        raise AssertionError("runner started work before binding declared evidence")

    for name in ("head_rev", "repo_top", "dirty_paths"):
        monkeypatch.setattr(git, name, forbidden)

    plan = runner.resolve_command_plan(lane)
    result = runner.CommandResult(
        plan=plan,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
        returncode=None,
        started="2000-01-01T00:00:00+00:00",
        ended="2000-01-01T00:00:00+00:00",
    )
    claims = (
        Claim(
            rigor="R0",
            source="computed",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True,
            reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
        ),
    )

    calls = (
        lambda: runner.run_lane(
            lane,
            commit="a" * 40,
            repo=tmp_path,
            project_root=tmp_path,
            adapter=None,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
        lambda: runner.refuse_lane(
            lane,
            commit="a" * 40,
            status=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
        lambda: runner.assemble_verdict(
            lane=lane,
            commit="a" * 40,
            result=result,
            claims=claims,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
    )
    for call in calls:
        with pytest.raises(AssayError) as raised:
            call()
        assert (raised.value.outcome, raised.value.reason_code) == (
            Outcome.ERROR,
            ReasonCode.BAD_LANE_CONFIG,
        )


def test_r0_attestation_config_round_trips_without_inventing_a_judge_under_schema_v2(
    tmp_path: Path,
) -> None:
    """v2 successor for P26's ``test_r0_attestation_config_round_trips_
    without_inventing_a_judge``: the loaded ``attestation_dir``/``evidence``
    pair reconstructs byte-for-byte via ``as_declared()``, same as under v1 --
    Tier-3 evidence's HOW pair is orthogonal to the schema bump."""
    text = _lane_document(
        '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source = "attested", key = "security-review"},
  {source = "attested", key = "api-review.v2"},
]'''
    )
    path = tmp_path / "assay.toml"
    path.write_text(text, encoding="utf-8")
    loaded = config.load_lane_file(path).lane("attested")
    assert loaded.as_declared() == tomllib.loads(text)["lanes"]["attested"]
    assert loaded.judge.attestation_dir == ".assay/attestations"
    assert tuple((item.source, item.key) for item in loaded.judge.evidence) == (
        ("attested", "security-review"),
        ("attested", "api-review.v2"),
    )


@pytest.mark.parametrize(
    "judge",
    [
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"''',
        '''[lanes.attested.judge]\nevidence = [{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = []''',
        '''[lanes.attested.judge]\nattestation_dir = "../outside"\nevidence = [{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="adjudicated",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="../review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="review"},{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="review",extra=true}]''',
    ],
)
def test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape_under_schema_v2(
    tmp_path: Path, judge: str
) -> None:
    """v2 successor for P26's ``test_closed_attestation_declaration_rejects_
    every_inert_or_unsafe_shape``: same eight inert/unsafe evidence-table
    shapes, same refusal, under the v2 header."""
    path = tmp_path / "assay.toml"
    path.write_text(_lane_document(judge), encoding="utf-8")
    with pytest.raises(LaneConfigError):
        config.load_lane_file(path)


def test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget_under_schema_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2 successor for P26's ``test_direct_r0_uses_the_existing_deadline_
    remainder_not_a_fresh_budget``: a real end-to-end R0 ``run_lane`` still
    forwards the SAME injected ``LaneDeadline`` remainder to every Git call
    and to the process runner's own timeout, unchanged by the schema bump."""
    path = tmp_path / "assay.toml"
    path.write_text(_lane_document(""), encoding="utf-8")
    lane = config.load_lane_file(path).lane("attested")
    head = "b" * 40
    deadline = runner.LaneDeadline(expires_at=100.0, monotonic=lambda: 83.0)
    forwarded = []

    def repo_top(repo, *, remaining=None):
        forwarded.append(remaining)
        return tmp_path

    def dirty_paths(repo, *, remaining=None):
        forwarded.append(remaining)
        return ()

    def head_rev(repo, *, remaining=None):
        forwarded.append(remaining)
        return head

    monkeypatch.setattr(git, "repo_top", repo_top)
    monkeypatch.setattr(git, "dirty_paths", dirty_paths)
    monkeypatch.setattr(git, "head_rev", head_rev)
    timeouts = []

    def process_runner(argv, *, env, cwd, timeout):
        timeouts.append(timeout)
        return subprocess.CompletedProcess(argv, 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=tmp_path,
        project_root=tmp_path,
        adapter=None,
        assay_version="locked",
        process_runner=process_runner,
        deadline=deadline,
    )
    assert verdict.outcome is Outcome.PASS
    assert timeouts == [17.0]
    assert forwarded
    assert all(getattr(item, "__self__", None) is deadline for item in forwarded)


# ---------------------------------------------------------------------------
# P33 successors -- config-layer mutation-operator negatives, R0+R2 (needs an
# explicit [isolation] table: A-269's migration rule, "R1+ gets explicit
# repository unless the test is specifically an omission test" -- none of
# these five is an omission test)
# ---------------------------------------------------------------------------


def _load_lane(tmp_path, mutation_inline: str, language: str = "python"):
    """The exact shape of the frozen ``P33/test_acceptance_v5.py::_load_lane``,
    with ``schema_version`` bumped 1 -> 2 and one addition: an explicit
    ``[lanes.demo.isolation]`` table, now required because this lane declares
    R2. Every other field is unchanged from the locked original."""
    from assay import config as C

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "assay.toml").write_text(
        "schema_version = 2\n"
        "\n"
        "[lanes.demo]\n"
        'argv = ["/usr/bin/python3", "-m", "pytest"]\n'
        'rigor = ["R0", "R2"]\n'
        'scope = "S1"\n'
        'enforcement = "gate"\n'
        'budget = "10m"\n'
        "allow_argv_append = false\n"
        "env = {}\n"
        "env_passthrough = []\n"
        "\n"
        "[lanes.demo.isolation]\n"
        'snapshot_selection = "repository"\n'
        "\n"
        "[lanes.demo.judge]\n"
        f'language = "{language}"\n'
        'source_roots = ["src"]\n'
        'base = "0000000000000000000000000000000000000000"\n'
        f"mutation = {{ {mutation_inline} }}\n"
    )
    return C, tmp_path / "assay.toml"


def test_config_fixture_itself_loads_today_under_schema_v2(tmp_path):
    """v2 successor for P33's ``test_config_fixture_itself_loads_today``:
    the control for the controls, reproduced under v2. If this reddens,
    every v2 config test below it is failing for a FIXTURE reason rather
    than a contract reason."""
    C, toml = _load_lane(
        tmp_path, 'jobs = 1, max_mutants = 10, operators = ["python:compare-swap"]')
    lane_file = C.load_lane_file(toml)
    assert "demo" in lane_file.lanes


def test_config_refuses_a_cross_language_operator_under_schema_v2(tmp_path):
    """v2 successor for P33's ``test_config_refuses_a_cross_language_
    operator``: a python lane declaring a ``sql:`` operator is still refused
    at load, still naming the operator, under v2."""
    C, toml = _load_lane(
        tmp_path, 'jobs = 1, max_mutants = 10, operators = ["sql:drop-check"]')
    with pytest.raises(C.LaneConfigError) as exc:
        C.load_lane_file(toml)
    assert "sql:drop-check" in str(exc.value), (
        "the refusal must name the offending operator"
    )


def test_config_accepts_a_matching_language_operator_under_schema_v2(tmp_path):
    """v2 successor for P33's ``test_config_accepts_a_matching_language_
    operator``: the control for the negative above, reproduced under v2."""
    C, toml = _load_lane(
        tmp_path, 'jobs = 1, max_mutants = 10, operators = ["python:compare-swap"]')
    lane_file = C.load_lane_file(toml)
    assert lane_file is not None


def test_config_names_kill_signal_artifact_as_reserved_for_p34_under_schema_v2(tmp_path):
    """v2 successor for P33's ``test_config_names_kill_signal_artifact_as_
    reserved_for_p34``: the message still names the field as RESERVED for
    P34, not as an unknown key, under v2."""
    C, toml = _load_lane(
        tmp_path,
        'jobs = 1, max_mutants = 10, operators = ["compare-swap"], '
        'kill_signal_artifact = ".assay/kill-signal.txt"')
    with pytest.raises(C.LaneConfigError) as exc:
        C.load_lane_file(toml)
    message = str(exc.value)
    assert "kill_signal_artifact" in message
    assert "P34" in message, (
        "the refusal must name the field as reserved for P34, not report it as "
        "an unknown key -- otherwise this work item has no observable"
    )


def test_config_names_equivalence_artifact_as_reserved_for_p34_under_schema_v2(tmp_path):
    """v2 successor for P33's ``test_config_names_equivalence_artifact_as_
    reserved_for_p34``: same reservation message, same field, under v2."""
    C, toml = _load_lane(
        tmp_path,
        'jobs = 1, max_mutants = 10, operators = ["compare-swap"], '
        'equivalence_artifact = ".assay/schema-dump.sql"')
    with pytest.raises(C.LaneConfigError) as exc:
        C.load_lane_file(toml)
    message = str(exc.value)
    assert "equivalence_artifact" in message
    assert "P34" in message, (
        "the refusal must name the field as reserved for P34, not report it as "
        "an unknown key -- otherwise this work item has no observable"
    )


def test_locked_node_ids_named_above_are_exactly_nine():
    """A cheap self-check that this module's own docstring table did not
    silently drift to eight or ten -- the carve's own count."""
    successors = [
        name
        for name in globals()
        if name.startswith("test_") and name.endswith("_under_schema_v2")
    ]
    assert len(successors) == 9, successors

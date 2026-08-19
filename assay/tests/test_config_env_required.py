"""A-254 — `env_required`: a declared environment name whose ABSENCE refuses.

The hole this closes was measured, not theorised. `resolve_command_plan` copies
a passthrough name only ``if name in source``, so an absent one is silently
dropped:

    for name in lane.env_passthrough:
        if name in source:
            env_effective[name] = source[name]

A lane that passes an identity through in order to RECORD it in
``env_effective`` — a container image revision, a ciu instance id — therefore
produces a clean PASS carrying no identity at all when the variable is missing,
with nothing anywhere saying so. Driven before the fix: a lane declaring
``env_passthrough = ["CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"]`` with neither set
emitted ``env_effective`` without them and PASSed.

That is A-025's rule broken one layer down — the absence of a value read as the
absence of a requirement — and it is the difference between an invariant holding
"by construction" and holding by hope.

Load-time and run-time halves are both here because they refuse different
things: a name outside `env_passthrough` can never be satisfied by ANY
environment (a declaration bug, caught at load), while a declared-and-reachable
name that is simply unset is an environment fact (caught at run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import GitRepo

from assay.config import LaneConfigError, load_lane_file
from assay.errors import Outcome, ReasonCode
from assay.verify import verify_document

_LANE = """schema_version = 2
[lanes.real]
scope = "S3"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = {passthrough}
{required}budget = "1m"
allow_argv_append = false
"""


def _write_lane(directory: Path, *, passthrough: list[str], required: list[str] | None) -> Path:
    path = directory / "assay.toml"
    path.write_text(
        _LANE.format(
            passthrough=json.dumps(passthrough),
            required="" if required is None else f"env_required = {json.dumps(required)}\n",
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Load time
# ---------------------------------------------------------------------------


def test_a_lane_without_env_required_loads_and_declares_none(tmp_path: Path):
    """The default is empty, so every lane written before this field existed is
    unchanged — the control for everything below."""
    lane_file = load_lane_file(_write_lane(tmp_path, passthrough=["PATH"], required=None))
    lane = lane_file.lanes["real"]
    assert lane.env_required == ()
    assert "env_required" not in lane.as_declared(), (
        "an empty env_required must not appear in the reconstructed table, or "
        "as_declared() stops round-tripping the file it was loaded from"
    )


def test_a_declared_env_required_round_trips_through_as_declared(tmp_path: Path):
    lane_file = load_lane_file(
        _write_lane(tmp_path, passthrough=["PATH", "CIU_INSTANCE_ID"],
                    required=["CIU_INSTANCE_ID"])
    )
    lane = lane_file.lanes["real"]
    assert lane.env_required == ("CIU_INSTANCE_ID",)
    assert lane.as_declared()["env_required"] == ["CIU_INSTANCE_ID"]


def test_requiring_a_name_env_passthrough_does_not_declare_is_refused(tmp_path: Path):
    """A required name outside `env_passthrough` is unreachable by construction:
    `resolve_command_plan` only ever copies declared passthrough names, so the
    lane would refuse every run for a reason no environment could satisfy.
    Caught at load, where a typo is cheap."""
    with pytest.raises(LaneConfigError, match="env_passthrough' does not declare"):
        load_lane_file(
            _write_lane(tmp_path, passthrough=["PATH"], required=["CIU_INSTANCE_ID"])
        )


def test_the_refusal_names_the_offending_variable(tmp_path: Path):
    """A message that does not name the variable makes an operator diff two
    lists by eye."""
    with pytest.raises(LaneConfigError, match="CIU_IMAGE_REVISION"):
        load_lane_file(
            _write_lane(tmp_path, passthrough=["PATH", "CIU_INSTANCE_ID"],
                        required=["CIU_INSTANCE_ID", "CIU_IMAGE_REVISION"])
        )


# ---------------------------------------------------------------------------
# Run time, through the real entry point
# ---------------------------------------------------------------------------


def _repo(git_repo: GitRepo, *, required: list[str] | None) -> GitRepo:
    git_repo.write(".gitignore", "*.json\n")
    git_repo.write("src/m.py", "x = 1\n")
    _write_lane(
        git_repo.path,
        passthrough=["PATH", "CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"],
        required=required,
    )
    git_repo.commit_all("lane")
    return git_repo


def _run(repo: GitRepo, target: Path, env: dict[str, str], monkeypatch) -> tuple[int, dict]:
    from assay.cli import main

    for name in ("CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(repo.path)
    code = main(["run", "real", "--verdict-json", str(target)])
    return code, json.loads(target.read_text(encoding="utf-8"))


def test_an_absent_required_variable_refuses_before_the_command_runs(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    repo = _repo(git_repo, required=["CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"])
    code, document = _run(repo, tmp_path / "v.json", {}, monkeypatch)

    assert code == Outcome.ERROR.exit_code
    assert (document["outcome"], document["reason_code"]) == (
        "ERROR", ReasonCode.BAD_LANE_CONFIG.value,
    )
    assert [c["rigor"] for c in document["claims"]] == ["R0"], (
        "every declared level renders the same pair (refuse_lane's own rule)"
    )
    assert verify_document(document) == [], (
        "a refusal artifact must still be one its own verify accepts"
    )


def test_the_same_lane_without_env_required_silently_passes_recording_nothing(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """The differential half, and the whole reason this field exists. Identical
    lane, identical empty environment, `env_required` omitted: a clean PASS
    whose `env_effective` carries neither identity. This test documents the
    behaviour that is still correct for a lane that never asked — and shows
    exactly what a lane that DID ask would have got without A-254."""
    repo = _repo(git_repo, required=None)
    code, document = _run(repo, tmp_path / "v.json", {}, monkeypatch)

    assert code == 0
    assert document["outcome"] == "PASS"
    assert "CIU_IMAGE_REVISION" not in document["env_effective"]
    assert "CIU_INSTANCE_ID" not in document["env_effective"]


def test_a_partially_satisfied_requirement_still_refuses(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """Two required names, one set. Half an identity is not an identity, and a
    check that accepted it would record a provenance value beside a missing
    instance id as though both were known."""
    repo = _repo(git_repo, required=["CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"])
    code, document = _run(
        repo, tmp_path / "v.json", {"CIU_IMAGE_REVISION": "1b369e23"}, monkeypatch
    )
    assert code == Outcome.ERROR.exit_code
    assert document["reason_code"] == ReasonCode.BAD_LANE_CONFIG.value


def test_satisfied_requirements_pass_and_record_both_values_verbatim(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """The positive, and the thing a consumer actually wants: the identities are
    in the artifact, byte-for-byte, so "which instance produced this verdict" is
    answerable FROM the artifact rather than asserted by whoever ran it."""
    repo = _repo(git_repo, required=["CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"])
    code, document = _run(
        repo, tmp_path / "v.json",
        {"CIU_IMAGE_REVISION": "1b369e23", "CIU_INSTANCE_ID": "dstdns-pkgP96"},
        monkeypatch,
    )
    assert code == 0
    assert document["outcome"] == "PASS"
    assert document["env_effective"]["CIU_IMAGE_REVISION"] == "1b369e23"
    assert document["env_effective"]["CIU_INSTANCE_ID"] == "dstdns-pkgP96"
    assert verify_document(document) == []


def test_the_precondition_precedes_every_repository_guard(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """Ordering matters and is asserted, not assumed: the refusal is decided
    before any Git work, so a dirty tree does NOT mask a missing required
    variable. Both are true here; the environment one wins because it is the
    one the operator can act on without looking at the repository."""
    repo = _repo(git_repo, required=["CIU_INSTANCE_ID"])
    (repo.path / "untracked.py").write_text("left behind\n", encoding="utf-8")

    code, document = _run(repo, tmp_path / "v.json", {}, monkeypatch)

    assert document["reason_code"] == ReasonCode.BAD_LANE_CONFIG.value, (
        "DIRTY_TREE masked the missing variable"
    )
    assert code == Outcome.ERROR.exit_code


def test_a_refused_run_still_records_what_it_was_asked_to_do(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """A-036: a refused run is not an unrecorded one. The lane's resolved argv
    and declared env are in the artifact, so the refusal is auditable."""
    repo = _repo(git_repo, required=["CIU_INSTANCE_ID"])
    _, document = _run(repo, tmp_path / "v.json", {}, monkeypatch)

    assert document["argv_effective"] == ["/bin/true"]
    assert document["scope"] == "S3"
    assert document["declared_rigor"] == ["R0"]

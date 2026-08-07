"""O3 — ``write_verdict`` writes atomically on every outcome (including
``NO_MEASUREMENT``, constructed directly rather than driven through the
runner), and an injected replacement failure preserves whatever artifact was
already there.

The negative this defends (verbatim): *writing only success paths or
truncating in place makes the corresponding filesystem fixture absent or
corrupt.* ``NO_MEASUREMENT`` is not something this package's runner ever
produces (P05's job, A-090) — its own artifact-writer test constructs the
``Verdict`` directly, the same way
``test_verdict_serialises.py::build_no_measurement`` already does, proving the
WRITER is outcome-agnostic rather than re-testing the runner.

"Without --verdict-json no artifact is created" is a CLI-level claim and is
proved in ``tests/test_cli_run.py``, since :func:`~assay.runner.write_verdict`
itself has no opinion on when it is called — that decision belongs to the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.errors import Outcome, ReasonCode
from assay.verdict import Claim, Verdict
from assay import runner

VERSION = "0.1.0"


def _pass_verdict() -> Verdict:
    return Verdict(
        lane="package",
        commit="e" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T11:00:00+00:00",
        ended="2026-08-07T11:00:01+00:00",
        assay_version=VERSION,
        declared_rigor=("R0",),
        declared_evidence=(),
        argv_declared=("/bin/sh", "-c", "exit 0"),
        argv_appended=(),
        argv_effective=("/bin/sh", "-c", "exit 0"),
        env_declared={},
        env_effective={},
        claims=(
            Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True),
        ),
    )


def _no_measurement_verdict() -> Verdict:
    # Same shape as test_verdict_serialises.py's build_no_measurement(): built
    # directly, never through the runner (this package's runner cannot
    # produce NO_MEASUREMENT at all).
    return Verdict(
        lane="package",
        commit="f" * 40,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.DIRTY_TREE,
        started="2026-08-07T11:10:00+00:00",
        ended="2026-08-07T11:10:01+00:00",
        assay_version=VERSION,
        declared_rigor=("R0", "R1"),
        declared_evidence=(),
        argv_declared=("pytest", "tests", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "tests", "-q"),
        env_declared={},
        env_effective={},
        claims=(
            Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True),
            Claim(
                rigor="R1",
                source="computed",
                status=Outcome.NO_MEASUREMENT,
                verified_by_assay=True,
                reason_code=ReasonCode.DIRTY_TREE,
            ),
        ),
    )


@pytest.mark.parametrize("build", [_pass_verdict, _no_measurement_verdict])
def test_write_verdict_creates_the_file_with_exact_json(tmp_path: Path, build):
    verdict = build()
    target = tmp_path / "verdict.json"

    runner.write_verdict(verdict, str(target), stdout=None)  # type: ignore[arg-type]

    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(verdict.to_json())


def test_write_verdict_leaves_no_temp_file_behind(tmp_path: Path):
    target = tmp_path / "verdict.json"

    runner.write_verdict(_pass_verdict(), str(target), stdout=None)  # type: ignore[arg-type]

    leftovers = [p for p in tmp_path.iterdir() if p.name != "verdict.json"]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_write_verdict_dash_writes_json_to_stdout_and_no_file(tmp_path: Path):
    import io

    out = io.StringIO()
    verdict = _pass_verdict()

    runner.write_verdict(verdict, "-", stdout=out)

    assert json.loads(out.getvalue()) == json.loads(verdict.to_json())
    assert list(tmp_path.iterdir()) == [], "a '-' target must never touch the filesystem"


def test_an_injected_replacement_failure_preserves_the_old_artifact(tmp_path: Path):
    target = tmp_path / "verdict.json"
    old_verdict = _pass_verdict()
    target.write_text(old_verdict.to_json(), encoding="utf-8")

    def broken_replace(src: str, dst: str) -> None:
        raise OSError("simulated replace failure -- disk full, permissions, whatever")

    new_verdict = _no_measurement_verdict()
    with pytest.raises(OSError):
        runner.write_verdict(
            new_verdict, str(target), stdout=None, replace=broken_replace  # type: ignore[arg-type]
        )

    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(old_verdict.to_json()), (
        "the old artifact must survive a failed replace untouched"
    )
    leftovers = [p for p in tmp_path.iterdir() if p.name != "verdict.json"]
    assert leftovers == [], f"a failed replace must not leave its temp file: {leftovers}"


def test_an_injected_replacement_failure_with_no_prior_artifact_leaves_none(tmp_path: Path):
    target = tmp_path / "verdict.json"

    def broken_replace(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    with pytest.raises(OSError):
        runner.write_verdict(
            _pass_verdict(), str(target), stdout=None, replace=broken_replace  # type: ignore[arg-type]
        )

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_write_verdict_is_stable_and_matches_to_json_exactly(tmp_path: Path):
    verdict = _pass_verdict()
    target = tmp_path / "verdict.json"

    runner.write_verdict(verdict, str(target), stdout=None)  # type: ignore[arg-type]

    assert target.read_text(encoding="utf-8") == verdict.to_json()

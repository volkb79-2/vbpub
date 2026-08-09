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

import io
import json
from pathlib import Path

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.output import reserve_verdict_output
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
        scope="S1",
        enforcement="gate",
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
        scope="S1",
        enforcement="gate",
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
def test_write_verdict_emits_exact_json_through_a_reservation(tmp_path: Path, build):
    """The writer stays outcome-agnostic: a NO_MEASUREMENT verdict is
    serialised exactly like a PASS. P21 changed WHERE the destination is
    decided, not WHAT gets written."""
    verdict = build()
    target = tmp_path / "verdict.json"

    with reserve_verdict_output(str(target), stdout=io.StringIO()) as destination:
        runner.write_verdict(verdict, destination)

    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(verdict.to_json())
    assert target.read_text(encoding="utf-8") == verdict.to_json()


def test_write_verdict_leaves_no_temp_file_behind(tmp_path: Path):
    with reserve_verdict_output(
        str(tmp_path / "verdict.json"), stdout=io.StringIO()
    ) as destination:
        runner.write_verdict(_pass_verdict(), destination)

    leftovers = [p for p in tmp_path.iterdir() if p.name != "verdict.json"]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_write_verdict_through_a_stdout_reservation_touches_no_file(tmp_path: Path):
    out = io.StringIO()
    verdict = _pass_verdict()

    with reserve_verdict_output("-", stdout=out) as destination:
        runner.write_verdict(verdict, destination)

    assert json.loads(out.getvalue()) == json.loads(verdict.to_json())
    assert list(tmp_path.iterdir()) == [], "a '-' target must never touch the filesystem"


def test_a_lost_destination_preserves_the_object_that_took_its_place(tmp_path: Path):
    """P21/A-181: the old artifact-preservation claim, now expressed through
    the reservation that owns it. The failure is the CLOSED
    ``ERROR``/``OUTPUT_WRITE_FAILED`` terminal rather than a bare
    ``OSError`` -- which is the whole point of A-O14: a consumer used to
    read that as FAIL."""
    target = tmp_path / "verdict.json"
    target.write_text(_pass_verdict().to_json(), encoding="utf-8")

    with reserve_verdict_output(str(target), stdout=io.StringIO()) as destination:
        target.unlink()
        target.write_text("someone else's artifact\n", encoding="utf-8")

        with pytest.raises(AssayError) as caught:
            runner.write_verdict(_no_measurement_verdict(), destination)

    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert target.read_text(encoding="utf-8") == "someone else's artifact\n"
    leftovers = [p for p in tmp_path.iterdir() if p.name != "verdict.json"]
    assert leftovers == [], f"a failed emission must not leave its temp file: {leftovers}"

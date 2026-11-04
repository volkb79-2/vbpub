"""B187: R0 result classification uses the shared observation mapping."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from termination_table import LIMITS_CLEAR

from assay import contention, runner
from assay.errors import Outcome, ReasonCode
from assay.runner import CommandResult


def _failed(observation) -> CommandResult:
    return CommandResult(
        plan=runner.CommandPlan(
            argv_declared=("check",),
            argv_appended=(),
            argv_effective=("check",),
            env_declared={},
            env_effective={},
            env_passthrough=(),
            allow_argv_append=False,
            budget_seconds=60.0,
            project_prefix=PurePosixPath("."),
        ),
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=1,
        started="2026-08-07T12:00:00+00:00",
        ended="2026-08-07T12:00:01+00:00",
        observation=observation,
    )


@pytest.mark.parametrize(
    ("observation", "expected_outcome"),
    [
        (contention.ContentionObservation("clear", limits=LIMITS_CLEAR), Outcome.FAIL),
        (contention.ContentionObservation("unavailable"), Outcome.ERROR),
        (contention.ContentionObservation("unobserved"), Outcome.ERROR),
        (None, Outcome.ERROR),
    ],
)
def test_required_observation_decides_whether_a_fail_stays_a_fail(
    observation, expected_outcome, monkeypatch
):
    seen: list[str] = []
    real = contention.observation_input

    def spy(obs):
        seen.append(obs.status)
        return real(obs)

    monkeypatch.setattr(contention, "observation_input", spy)
    classified = runner._classify_r0_result(
        _failed(observation), observation_required=True
    )

    assert classified.outcome is expected_outcome
    assert seen == ([] if observation is None else [observation.status])

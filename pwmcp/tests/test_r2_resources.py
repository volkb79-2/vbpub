"""PWMCP-61: the `r2` lane bounds memory-plus-swap, and the bound cannot be dropped silently.

`pwmcp/run-gate.toml` declares a 3 GiB `memory` cap for the mutation lane but originally no
`memory_swap`, and Docker's default leaves swap UNBOUNDED for the container: a runaway mutant then
swaps on the HOST instead of dying at its cap. One host campaign drove the container to its memory
cap while continuing to use host swap, with three concurrent executions pushing host memory PSI
above the operator's 25% stop threshold for about ten minutes.

The rule this oracle enforces:

* the lane declares `memory_swap`, it parses as a Docker size, and it EQUALS `memory`, so the
  combined ceiling is the same 3 GiB the memory cap already declares (a larger swap ceiling would
  re-open exactly the swapping the item reported);
* the observer-backed lane keeps the `pids` and `memory` declarations its closure needs;
* the change is RESOURCE-ONLY: the mutation judge identity (`operators`, `fail_under`,
  coverage artifact, `require_branch`) is untouched, which is what lets Assay's B529 reuse killed
  and survived records from earlier campaigns instead of re-judging them.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[1]
RUN_GATE = PWMCP / "run-gate.toml"
ASSAY = PWMCP / "assay.toml"

SIZE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgt]?)$", re.IGNORECASE)
_BYTES = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _bytes(value: str) -> int:
    match = SIZE.match(value.strip())
    assert match, f"not a docker size: {value!r}"
    return int(float(match.group("value")) * _BYTES[match.group("unit").lower()])


def _r2() -> dict:
    return tomllib.loads(RUN_GATE.read_text(encoding="utf-8"))["lanes"]["r2"]


def test_the_r2_lane_caps_memory_plus_swap_at_the_memory_ceiling() -> None:
    resources = _r2()["resources"]
    assert "memory_swap" in resources, (
        "the mutation lane declares no memory_swap, so Docker leaves host swap unbounded for it "
        "and a runaway mutant swaps instead of dying at its memory cap"
    )
    assert "memory" in resources, "the mutation lane declares no memory cap at all"
    memory = _bytes(resources["memory"])
    swap = _bytes(resources["memory_swap"])
    # Docker's --memory-swap is the COMBINED memory-plus-swap limit.
    assert swap == memory, (
        f"memory_swap={resources['memory_swap']} does not equal memory={resources['memory']}; "
        "the combined ceiling must be the same 3 GiB, not a larger swap budget"
    )
    assert swap >= memory  # docker refuses --memory-swap below --memory


def test_the_observer_backed_lane_keeps_the_declarations_its_counters_need() -> None:
    lane = _r2()
    assert lane["resource_observer"] == "cgprofile"
    resources = lane["resources"]
    # RG-20: an observer-backed lane requires pids and memory so local event counters are
    # attributable -- a swap ceiling must not replace either of them.
    assert "pids" in resources, "the observer-backed lane lost its pids limit"
    assert isinstance(resources["pids"], int) and resources["pids"] > 0
    assert _bytes(resources["memory"]) > 0
    assert resources["cpus"] == "3"
    assert _bytes(resources["candidate_ram"]) > 0


def test_the_swap_ceiling_is_a_resource_only_change_for_the_judge_identity() -> None:
    """What Assay keys record reuse on is unchanged, so earlier killed/survived records stay reusable."""

    mutation = tomllib.loads(ASSAY.read_text(encoding="utf-8"))["lanes"]["r2"]["judge"]
    assert mutation["mutation"]["operators"] == [
        "python:compare-swap",
        "python:boolop-swap",
        "python:bool-const-flip",
        "python:falsy-swap",
    ]
    assert mutation["fail_under"] == 100.0
    assert mutation["require_branch"] is True
    assert mutation["coverage"]["format"] == "coverage-py-json"
    assert mutation["coverage"]["artifact"] == "coverage.json"
    # no lane resource leaks into the judge identity (a resource key must not be a judge key)
    for key in ("memory", "memory_swap", "pids", "cpus", "candidate_ram"):
        assert key not in mutation and key not in mutation["mutation"], key

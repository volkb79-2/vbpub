"""CLI-EXT-35: the `r2` run-gate lane is a native Assay lane that run-gate can shard.

The lane used to be a `kind = "command"` lane that called `assay run` itself, so run-gate refused
`--shard K/N`, `--merge-shards N`, `--carry-over` and `--only-candidates` (they apply only to
`kind = "assay"` lanes whose Assay lane reports native R2) and the 1,561-candidate campaign could
only run as one container for the whole window.

What this file pins, without starting docker or a lane:

* the lane is `kind = "assay"` and names the Assay lane `cli-extended`, which declares R2 and a
  mutation judge (the inventory fact run-gate reads as "native R2");
* it keeps the shard admission preconditions of run-gate's `refuse_serialized_shard`: an
  ephemeral environment (inherited `tester-unified`) and no `resources.shared`, so N shards are N
  containers that really run at once;
* the worker count comes from `jobs = "auto"` under the `jobs_max` ceiling, and the container
  memory covers `jobs_max` workers at the declared `candidate_ram`;
* the observer, pids and clean-tree declarations the sibling native R2 lanes carry.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SIZE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgt]?)$", re.IGNORECASE)
UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _bytes(text: str) -> int:
    match = SIZE.match(text.strip())
    assert match, f"not a docker size: {text!r}"
    return int(float(match.group("value")) * UNITS[match.group("unit").lower()])


def _lane() -> dict:
    document = tomllib.loads((PACKAGE_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    return document["lanes"]["r2"]


def _assay_lane() -> dict:
    document = tomllib.loads((PACKAGE_ROOT / "assay.toml").read_text(encoding="utf-8"))
    return document["lanes"]["cli-extended"]


def test_r2_is_a_native_assay_lane_so_run_gate_accepts_the_shard_flags() -> None:
    lane = _lane()
    assert lane["kind"] == "assay", (
        "a kind = 'command' lane makes run-gate refuse --shard, --merge-shards, --carry-over "
        "and --only-candidates"
    )
    assert "argv" not in lane, "a native lane is run by run-gate; it carries no argv of its own"
    assert lane["assay_lane"] in tomllib.loads(
        (PACKAGE_ROOT / "assay.toml").read_text(encoding="utf-8")
    )["lanes"]
    assay = _assay_lane()
    assert "R2" in assay["rigor"], "the inventory reports native R2 only for a lane that declares it"
    assert assay["judge"]["mutation"]["operators"], "native R2 needs a declared mutation judge"


def test_r2_keeps_the_conditions_run_gate_requires_to_shard_a_lane() -> None:
    lane = _lane()
    # refuse_serialized_shard: an exec-mode environment or resources.shared serializes the shards.
    assert lane["environment"] == "tester-unified", "tester-unified is the ephemeral environment"
    assert "shared" not in lane.get("resources", {})
    # refuse_unpulled_shard_image / observer: each shard is its own observed container.
    assert lane["resource_observer"] == "cgprofile"
    assert lane["clean_tree"] is True


def test_r2_workers_are_chosen_by_run_gate_within_the_assay_ceiling() -> None:
    lane = _lane()
    assert lane["jobs"] == "auto"
    ceiling = _assay_lane()["judge"]["mutation"]["jobs_max"]
    assert ceiling > 1, "auto would never exceed one worker"
    resources = lane["resources"]
    assert resources["pids"] == 8192
    # Each shard is its own container: the container must hold `jobs_max` workers.
    assert _bytes(resources["candidate_ram"]) * ceiling <= _bytes(resources["memory"])
    # The measured candidate RSS maximum is 256 MiB (docs/design/RESOURCE-PROFILES.md).
    assert _bytes(resources["candidate_ram"]) >= 256 * 1024**2


def test_r2_budget_is_the_unsharded_ceiling_of_the_assay_lane() -> None:
    assert _lane()["budget"] == "720m"
    assert _assay_lane()["budget"] == "720m"

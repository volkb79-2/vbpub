"""CLI-EXT-41: the `r2` run-gate lane is a native Assay lane that run-gate can shard.

The lane used to be a `kind = "command"` lane that called `assay run` itself, so run-gate refused
`--shard K/N`, `--merge-shards N`, `--carry-over` and `--only-candidates` (they apply only to
`kind = "assay"` lanes whose Assay lane reports native R2) and the 1,561-candidate campaign could
only run as one container for the whole window.

These tests ask run-gate's OWN code, loaded from the repository's `run-gate-project/run_gate.py`,
about this project's real `run-gate.toml` and `assay.toml`, so they restate no run-gate rule: the
config loader and its validators, `merge_lanes`, `attach_r2_flex`, `refuse_serialized_shard` and the
`select_r2_jobs` worker selection. Nothing starts docker or a lane.

The module is loaded by file path (no `sys.path` change). `run-gate-project/` lives in the same
monorepo and is part of every checkout the gate lanes mount, so a missing `run_gate.py` means a
broken checkout and FAILS the test (never skips).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tomllib
from functools import cache
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUN_GATE_SOURCE = PACKAGE_ROOT.parents[1] / "run-gate-project" / "run_gate.py"
SECONDS = {"s": 1, "m": 60, "h": 3600}
#: The outer container budget exceeds the inner Assay deadline by at least this much, for container
#: start and the editable assay install. cmru's gate tests (cmru/tests/test_gate_budgets.py) enforce
#: this rule for cmru; cli-extended adopts it and enforces it here.
OUTER_MARGIN_SECONDS = 5 * 60
CANDIDATES = 1561


@cache
def _run_gate():
    if not RUN_GATE_SOURCE.is_file():
        pytest.fail(f"run-gate-project/run_gate.py is missing at {RUN_GATE_SOURCE}: broken checkout")
    spec = importlib.util.spec_from_file_location("_cli_extended_run_gate_under_test", RUN_GATE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seconds(text: str) -> int:
    match = re.fullmatch(r"(\d+)([smh])", text.strip())
    assert match, f"unparsed duration {text!r}"
    return int(match.group(1)) * SECONDS[match.group(2)]


def _assay_lane() -> dict:
    document = tomllib.loads((PACKAGE_ROOT / "assay.toml").read_text(encoding="utf-8"))
    return document["lanes"]["cli-extended"]


def _effective():
    """(lane, environment, environment name) exactly as run-gate resolves them for this project."""
    rg = _run_gate()
    project, project_path, central, central_path = rg.load_config(PACKAGE_ROOT)
    lanes = rg.merge_lanes(project.get("lanes", {}), central, PACKAGE_ROOT, project_path, central_path)
    lane = lanes["r2"]
    name = rg.lane_environment_name(lane)
    environment = (project.get("environments") or {}).get(name) or central["environments"][name]
    return lane, environment, name


def _args(**overrides) -> argparse.Namespace:
    values = {
        "shard": None,
        "merge_shards": None,
        "carry_over": [],
        "carry_over_reason": None,
        "merge_state": [],
        "only_candidates": None,
        "exclude_candidates": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_run_gate_loads_the_project_config_and_resolves_r2_as_a_native_assay_lane() -> None:
    lane, environment, name = _effective()
    assert lane["kind"] == "assay", (
        "a kind = 'command' lane makes run-gate refuse --shard, --merge-shards, --carry-over "
        "and --only-candidates"
    )
    assert lane["assay_lane"] == "cli-extended"
    assert environment["mode"] == "ephemeral", f"{name} must be an ephemeral environment"


def test_run_gate_admits_a_shard_of_the_r2_lane_as_its_own_instance() -> None:
    rg = _run_gate()
    lane, environment, name = _effective()
    sharded, instance = rg.attach_r2_flex(lane, "r2", _args(shard="0/2"))
    assert instance == "r2.shard0of2"
    assert rg.assay_artifact_lane(sharded) == "cli-extended.r2.shard0of2"
    # Raises GateError for an exec-mode environment or a lane with resources.shared.
    rg.refuse_serialized_shard(sharded, "r2", environment, name)


def test_run_gate_accepts_the_merge_and_carry_over_requests_for_the_r2_lane() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    merged, instance = rg.attach_r2_flex(lane, "r2", _args(merge_shards="3"))
    assert instance == "r2"
    assert merged["_r2_flex"]["merge_shards"] == 3
    carried, _ = rg.attach_r2_flex(
        lane,
        "r2",
        _args(carry_over=["/store"], carry_over_reason="judge-only change"),
    )
    assert carried["_r2_flex"]["carry_over"] == ["/store"]


def test_run_gate_would_refuse_the_old_command_lane() -> None:
    """The controlled wrong case: the pre-CLI-EXT-41 lane shape is refused by the same helper."""
    rg = _run_gate()
    lane, _environment, _name = _effective()
    old = {"kind": "command", "environment": lane["environment"], "argv": ["true"]}
    with pytest.raises(rg.GateError, match="assay-only"):
        rg.attach_r2_flex(old, "r2", _args(shard="0/2"))


def test_run_gate_picks_three_workers_per_shard_with_cheap_clone_and_one_without() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    resources = lane["resources"]
    memory_budget = rg.r2_memory_budget_bytes("75%", rg.parse_size_bytes(resources["memory"]))

    def workers(cheap_clone: bool) -> int:
        selection = rg.select_r2_jobs(
            lane["jobs"],
            jobs_max=_assay_lane()["judge"]["mutation"]["jobs_max"],
            cpu_cap=int(resources["cpus"]),
            memory_budget_bytes=memory_budget,
            candidate_ram_bytes=rg.parse_size_bytes(resources["candidate_ram"]),
            candidates_remaining=CANDIDATES,
            cheap_clone=cheap_clone,
        )
        return selection["jobs"]

    assert workers(True) == 3
    assert workers(False) == 1


def test_candidate_ram_covers_the_measured_rss_maximum() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    # docs/design/RESOURCE-PROFILES.md: cli-extended candidate RSS p50 82, p99 230, max 256 MiB.
    assert rg.parse_size_bytes(lane["resources"]["candidate_ram"]) >= 256 * 1024**2


def test_r2_outer_budget_exceeds_the_assay_lane_deadline_by_the_margin() -> None:
    lane = tomllib.loads((PACKAGE_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["r2"]
    inner = _seconds(_assay_lane()["budget"])
    assert _seconds(lane["budget"]) >= inner + OUTER_MARGIN_SECONDS


def test_r2_keeps_the_observer_and_clean_tree_declarations_of_the_sibling_native_lanes() -> None:
    lane = tomllib.loads((PACKAGE_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["r2"]
    assert lane["resource_observer"] == "cgprofile"
    assert lane["clean_tree"] is True
    assert lane["resources"]["pids"] == 8192
    assert "argv" not in lane, "a native lane is run by run-gate; it carries no argv of its own"

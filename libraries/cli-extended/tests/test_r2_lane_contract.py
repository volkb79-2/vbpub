"""CLI-EXT-41: the `r2` run-gate lane is a native Assay lane that run-gate can shard.

The lane used to be a `kind = "command"` lane that called `assay run` itself, so run-gate refused
`--shard K/N`, `--merge-shards N`, `--carry-over` and `--only-candidates` (they apply only to
`kind = "assay"` lanes whose Assay lane reports native R2) and the 1,561-candidate campaign could
only run as one container for the whole window.

These tests ask run-gate's OWN code, loaded from the repository's `run-gate-project/run_gate.py`,
about this project's real `run-gate.toml` and `assay.toml`, so they restate no run-gate rule: the
config loader and its validators, `merge_lanes`, `attach_r2_flex`, `refuse_serialized_shard` and the
`select_r2_jobs` worker selection. Nothing starts docker or a lane.

What is NOT here: the shard-instance shape (`r2.shard0of2` plus `refuse_serialized_shard`), the
`merge_shards` acceptance and the outer-budget margin are decided by
`run-gate lanes --check --all --strict`, which the `estate-lint` lane runs over the estate
(P-core-12, CLI-EXT-45); cli-extended does not restate them. What remains here is what that check
does NOT decide, each listed with the rule it guards.

The module is loaded by file path (no `sys.path` change). `run-gate-project/` lives in the same
monorepo and is part of every checkout the gate lanes mount, so a missing `run_gate.py` means a
broken checkout and FAILS the test (never skips).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from functools import cache
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUN_GATE_SOURCE = PACKAGE_ROOT.parents[1] / "run-gate-project" / "run_gate.py"
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


#: Uncovered by `lanes --check`: it inspects only `kind = "assay"` lanes' budget, canary and shard
#: shape — never the lane kind/assay_lane/environment resolution this test asks for.
def test_run_gate_loads_the_project_config_and_resolves_r2_as_a_native_assay_lane() -> None:
    lane, environment, name = _effective()
    assert lane["kind"] == "assay", (
        "a kind = 'command' lane makes run-gate refuse --shard, --merge-shards, --carry-over "
        "and --only-candidates"
    )
    assert lane["assay_lane"] == "cli-extended"
    assert environment["mode"] == "ephemeral", f"{name} must be an ephemeral environment"


#: Uncovered by `lanes --check`: it never calls `assay_artifact_lane`, so the artifact-lane NAME a
#: shard derives is not checked there (the shard instance shape itself is).
def test_a_sharded_r2_lane_names_its_own_artifact_lane() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    sharded, _instance = rg.attach_r2_flex(lane, "r2", _args(shard="0/2"))
    assert rg.assay_artifact_lane(sharded) == "cli-extended.r2.shard0of2"


#: Uncovered by `lanes --check`: it checks `--merge-shards`, not `--carry-over`.
def test_run_gate_accepts_the_carry_over_request_for_the_r2_lane() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    carried, _ = rg.attach_r2_flex(
        lane,
        "r2",
        _args(carry_over=["/store"], carry_over_reason="judge-only change"),
    )
    assert carried["_r2_flex"]["carry_over"] == ["/store"]


#: Uncovered by `lanes --check`: nothing there feeds a command-kind lane to `attach_r2_flex`.
def test_run_gate_would_refuse_the_old_command_lane() -> None:
    """The controlled wrong case: the pre-CLI-EXT-41 lane shape is refused by the same helper."""
    rg = _run_gate()
    lane, _environment, _name = _effective()
    old = {"kind": "command", "environment": lane["environment"], "argv": ["true"]}
    with pytest.raises(rg.GateError, match="assay-only"):
        rg.attach_r2_flex(old, "r2", _args(shard="0/2"))


#: Uncovered by `lanes --check`: the worker count `select_r2_jobs` derives from the declared
#: resources is never computed there.
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


#: Uncovered by `lanes --check`: it does not compare `candidate_ram` with any measured RSS.
def test_candidate_ram_covers_the_measured_rss_maximum() -> None:
    rg = _run_gate()
    lane, _environment, _name = _effective()
    # docs/design/RESOURCE-PROFILES.md: cli-extended candidate RSS p50 82, p99 230, max 256 MiB.
    assert rg.parse_size_bytes(lane["resources"]["candidate_ram"]) >= 256 * 1024**2


#: Uncovered by `lanes --check`: `resource_observer`, `clean_tree`, `pids` and the absent `argv`
#: of a native Assay lane are not inspected there.
def test_r2_keeps_the_observer_and_clean_tree_declarations_of_the_sibling_native_lanes() -> None:
    lane = tomllib.loads((PACKAGE_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["r2"]
    assert lane["resource_observer"] == "cgprofile"
    assert lane["clean_tree"] is True
    assert lane["resources"]["pids"] == 8192
    assert "argv" not in lane, "a native lane is run by run-gate; it carries no argv of its own"

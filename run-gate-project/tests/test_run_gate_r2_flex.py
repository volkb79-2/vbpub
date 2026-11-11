"""RG-265/RG-266: flexible native-R2 campaigns.

* RG-266 -- `jobs = "auto"` oversubscribes CPUs (`r2_oversubscribe`, default
  1.5) because R2 candidates spend part of their wall time in IO; memory,
  `jobs_max` and the remaining candidates still cap the count.
* RG-265 -- Assay's flexible-campaign flags pass through (`--carry-over`,
  `--merge-state`, `--only-candidates`, `--exclude-candidates`), and
  `--shard K/N` turns one lane into N independent lane instances that can
  run at the same time, each with its own name, container, in-flight record,
  `.assay` artifacts and Assay state store; `--merge-shards N` merges them.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_run_gate import (  # noqa: E402
    commit_all,
    fake_docker,
    install_fake_assay,
    make_project,
    make_repo,
    run_gate,
)
from test_run_gate import (  # noqa: E402,F401
    ambient_cgroup, isolate_host_profiler_socket, profiling_off_by_default)


def _args(**over) -> argparse.Namespace:
    values = {"shard": None, "merge_shards": None, "carry_over": [],
              "carry_over_reason": None, "merge_state": [],
              "only_candidates": None, "exclude_candidates": None}
    values.update(over)
    return argparse.Namespace(**values)


_LANE = {"kind": "assay", "assay_lane": "unit", "assay_command": ["assay"],
         "pins": {}}


# --- RG-266: oversubscription ---------------------------------------------


@pytest.mark.parametrize(
    "factor,cpu_cap,memory_workers,jobs_max,remaining,expected",
    [
        (1.5, 3, 99, 8, 99, 4),   # 3 cores x 1.5 = 4 workers
        (1.0, 3, 99, 8, 99, 3),   # no oversubscription
        (2.0, 3, 99, 8, 99, 6),
        (1.5, 3, 2, 8, 99, 2),    # memory is never oversubscribed
        (1.5, 4, 99, 5, 99, 5),   # jobs_max stays the ceiling
        (1.5, 4, 99, 8, 3, 3),    # never more than the remaining candidates
        (1.5, 1, 99, 8, 99, 1),   # floor(1.5) = 1
    ],
)
def test_auto_oversubscribes_cpu_but_not_memory(
        factor, cpu_cap, memory_workers, jobs_max, remaining, expected):
    selection = run_gate.select_r2_jobs(
        "auto", jobs_max=jobs_max, cpu_cap=cpu_cap,
        memory_budget_bytes=memory_workers * 100, candidate_ram_bytes=100,
        candidates_remaining=remaining, cheap_clone=True,
        oversubscribe=factor)
    assert selection["jobs"] == expected
    assert selection["oversubscribe"] == factor


def test_explicit_jobs_record_no_oversubscription():
    selection = run_gate.select_r2_jobs(
        2, jobs_max=4, cheap_clone=True, oversubscribe=2.0)
    assert selection["jobs"] == 2
    assert selection["oversubscribe"] is None


@pytest.mark.parametrize("factor", [0.5, 4.5, True, "1.5"])
def test_an_invalid_factor_is_refused(factor):
    with pytest.raises(run_gate.GateError, match="oversubscription factor"):
        run_gate.select_r2_jobs(
            "auto", jobs_max=4, cpu_cap=2, memory_budget_bytes=1000,
            candidate_ram_bytes=100, candidates_remaining=10,
            cheap_clone=True, oversubscribe=factor)


@pytest.mark.parametrize("value", ["1.0", "1.5", "4", "4.0"])
def test_resource_table_accepts_oversubscribe(value):
    run_gate._validate_resource_table({"r2_oversubscribe": value}, "t", "lane")


@pytest.mark.parametrize("value", ["0.9", "4.1", "1,5", 1.5, ""])
def test_resource_table_refuses_bad_oversubscribe(value):
    with pytest.raises(run_gate.GateError, match="r2_oversubscribe"):
        run_gate._validate_resource_table({"r2_oversubscribe": value}, "t", "lane")


def test_oversubscribe_resolves_default_then_lane():
    effective, sources = run_gate.resolve_resource_settings(
        {}, {}, {}, {}, mode="ephemeral")
    assert effective["r2_oversubscribe"] == run_gate.R2_OVERSUBSCRIBE_DEFAULT
    assert sources["r2_oversubscribe"] == "default"
    effective, sources = run_gate.resolve_resource_settings(
        {"resources": {"r2_oversubscribe": "2.0"}},
        {"resources": {"r2_oversubscribe": "1.0"}}, {}, {}, mode="ephemeral")
    assert effective["r2_oversubscribe"] == "2.0"
    assert sources["r2_oversubscribe"] == "lane"


# --- RG-265: flag validation ------------------------------------------------


def test_flags_given_detects_each_flag(tmp_path):
    assert not run_gate.r2_flex_flags_given(_args())
    for over in ({"shard": "0/2"}, {"merge_shards": "2"},
                 {"carry_over": ["x"]}, {"carry_over_reason": "r"},
                 {"merge_state": ["x"]}, {"only_candidates": "f"},
                 {"exclude_candidates": "f"}):
        assert run_gate.r2_flex_flags_given(_args(**over)), over


def test_a_shard_becomes_its_own_lane_instance():
    lane, name = run_gate.attach_r2_flex(dict(_LANE), "r2", _args(shard="1/3"))
    assert name == "r2.shard1of3"
    assert lane["_artifact_lane"] == "unit.shard1of3"
    assert lane["_r2_flex"] == {"shard": [1, 3], "base_lane": "r2"}
    assert run_gate.assay_artifact_lane(lane) == "unit.shard1of3"
    assert run_gate.assay_artifact_lane(dict(_LANE)) == "unit"
    assert "_r2_flex" not in _LANE, "the configured lane is never mutated"
    assert run_gate.r2_flex_record(lane["_r2_flex"]) == {
        "shard": [1, 3], "base_lane": "r2"}


@pytest.mark.parametrize("spec", ["3/3", "0/0", "1", "a/b", "0/65", "-1/2"])
def test_a_bad_shard_spec_is_refused(spec):
    with pytest.raises(run_gate.GateError, match="--shard"):
        run_gate.attach_r2_flex(dict(_LANE), "r2", _args(shard=spec))


@pytest.mark.parametrize(
    "over,message",
    [
        ({"shard": "0/2", "merge_shards": "2"}, "cannot be combined"),
        ({"merge_shards": "0"}, "--merge-shards must be"),
        ({"merge_shards": "65"}, "--merge-shards must be"),
        ({"carry_over": ["d"]}, "required together"),
        ({"carry_over_reason": "why"}, "required together"),
        ({"carry_over": ["d"], "carry_over_reason": "  "}, "non-empty"),
        ({"only_candidates": "/nonexistent/ids"}, "is not a file"),
    ],
)
def test_invalid_flag_combinations_are_refused(over, message):
    with pytest.raises(run_gate.GateError, match=message):
        run_gate.attach_r2_flex(dict(_LANE), "r2", _args(**over))


def test_flex_flags_are_assay_only():
    with pytest.raises(run_gate.GateError, match="assay-only"):
        run_gate.attach_r2_flex({"kind": "command", "argv": ["true"]}, "c",
                                _args(shard="0/2"))


def test_paths_are_made_absolute_and_must_be_visible(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".run-gate").mkdir(parents=True)
    ids = repo / ".run-gate" / "ids.txt"
    ids.write_text("")
    monkeypatch.chdir(repo)
    lane, _name = run_gate.attach_r2_flex(
        dict(_LANE), "r2",
        _args(carry_over=[".run-gate"], carry_over_reason="why",
              merge_state=[".run-gate"], only_candidates=".run-gate/ids.txt",
              exclude_candidates=".run-gate/ids.txt"))
    flex = lane["_r2_flex"]
    assert flex["carry_over"] == [str(repo / ".run-gate")]
    assert flex["only_candidates"] == str(ids)
    run_gate.validate_r2_flex_paths(flex, repo, repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    for key in ("carry_over", "merge_state"):
        with pytest.raises(run_gate.GateError, match="outside the checkout"):
            run_gate.validate_r2_flex_paths({key: [str(outside)]}, repo, repo)
    with pytest.raises(run_gate.GateError, match="outside the checkout"):
        run_gate.validate_r2_flex_paths(
            {"exclude_candidates": str(outside)}, repo, repo)


# --- RG-265: the generated script -------------------------------------------


def _inner(tmp_path, flex_args, *, judge_help="--merge-state --carry-over"):
    repo = tmp_path / "repo"
    project = repo / "proj"
    project.mkdir(parents=True)
    root = repo / ".run-gate"
    root.mkdir()
    log = tmp_path / "calls"
    judge = tmp_path / "assay"
    judge.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        'case "$*" in *"run --help"*) '
        f"echo {shlex.quote(judge_help)}; exit 0;; esac\n"
        "exit 0\n")
    judge.chmod(0o755)
    lane, _name = run_gate.attach_r2_flex(
        {**_LANE, "assay_command": [str(judge)]}, "r2", flex_args)
    inner = run_gate.build_assay_inner(lane, project, repo, worktree=repo,
                                       state_root=root)
    return repo, project, inner, log


def _run_call(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines()
            if line.startswith("run unit")]


def test_a_shard_runs_in_its_own_store_with_its_own_artifacts(tmp_path):
    repo, project, inner, log = _inner(tmp_path, _args(shard="1/3"))
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    main_store = repo / ".run-gate" / "assay-state" / "proj"
    shard_store = repo / ".run-gate" / "assay-shards" / "proj" / "unit" / "1of3"
    assert f"--state-dir {shard_store}" in call
    assert f"--shard 1/3 --merge-state {main_store}" in call
    assert "--verdict-json .assay/verdict-unit.shard1of3.json" in call
    assert "--progress .assay/progress-unit.shard1of3.jsonl" in call
    assert shard_store.is_dir() and main_store.is_dir()


def test_merge_shards_merges_every_shard_store(tmp_path):
    repo, project, inner, log = _inner(tmp_path, _args(merge_shards="2"))
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    shards = repo / ".run-gate" / "assay-shards" / "proj" / "unit"
    assert (f"--merge-state {shards / '0of2'} --merge-state {shards / '1of2'}"
            in call)
    assert f"--state-dir {repo / '.run-gate' / 'assay-state' / 'proj'}" in call
    assert "--verdict-json .assay/verdict-unit.json" in call
    assert (shards / "0of2").is_dir() and (shards / "1of2").is_dir()


def test_selection_and_carry_over_flags_pass_through(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    monkeypatch.chdir(tmp_path)
    ids = tmp_path / "ids.txt"
    ids.write_text("")
    _repo, project, inner, log = _inner(
        tmp_path, _args(carry_over=[str(tmp_path)], carry_over_reason="a b",
                        merge_state=[str(tmp_path)],
                        only_candidates=str(ids), exclude_candidates=str(ids)))
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    assert (f"--merge-state {tmp_path} --carry-over {tmp_path} "
            f"--carry-over-reason a b --only-candidates {ids} "
            f"--exclude-candidates {ids}") in call
    assert repo.is_dir()


def test_an_old_judge_is_refused_by_name_before_the_lane(tmp_path):
    _repo, project, inner, log = _inner(
        tmp_path, _args(shard="0/2"), judge_help="usage: assay run LANE")
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert "does not support --merge-state" in proc.stderr
    assert _run_call(log) == []


def test_a_lane_without_flex_is_byte_unchanged(tmp_path):
    lane = dict(_LANE)
    inner = run_gate.build_assay_inner(lane, Path("/p"), Path("/r"))
    assert "--merge-state" not in inner and "--shard" not in inner
    assert "run --help" not in inner
    assert inner.rstrip().split()[-2] == "--state-dir"


def test_shard_store_of_an_external_project_uses_its_own_namespace(tmp_path):
    root = tmp_path / ".run-gate"
    main_store = root / "assay-state-external" / ("a" * 64)
    assert run_gate.assay_shard_state_dir(root, main_store, "unit", 0, 2) == (
        root / "assay-shards-external" / ("a" * 64) / "unit" / "0of2")


# --- RG-265: end to end through main() ---------------------------------------


_R2_CFG = """\
    schema_version = 1

    [environments.bare-host]
    mode = "host"

    [lanes.r2]
    kind = "assay"
    environment = "bare-host"
    assay_lane = "unit"
    assay_command = ["assay"]
    clean_tree = false
"""


def _r2_inventory(**mutation) -> str:
    entry = {"name": "unit", "scope": "S1", "rigor": ["R0", "R2"],
             "enforcement": "gate", "language": "python",
             "rigor_reachable": ["R0", "R2"], "coverage": None,
             "mutation": {"jobs_max": 4, "max_mutants": 100, **mutation},
             "canary": None, "base_source": None, "external_tools": [],
             "argv0": None, "env_required": [], "environment_command": False,
             "infrastructure_facts": [], "budget": None, "cwd": None,
             "link_paths": [], "snapshot_selection": "repository"}
    return json.dumps({"assay_version": "12.2.0", "inventory_schema": 1,
                       "lanes": [entry]})


def _main_project(tmp_path, monkeypatch, inventory):
    repo = make_repo(tmp_path)
    proj = make_project(repo, _R2_CFG)
    (proj / "assay.toml").write_text("# judged by the fake judge\n")
    commit_all(repo, "assay config")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(sys, "argv", ["run-gate"])
    fake_docker(tmp_path, monkeypatch)
    calls = tmp_path / "judge-calls"
    install_fake_assay(monkeypatch, f"""\
        #!/bin/sh
        printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
        case "$*" in
          *"lanes --json"*) cat <<'JSON'
{inventory}
JSON
            exit 0 ;;
          *--version*) echo "assay 12.2.0"; exit 0 ;;
          *"run --help"*) echo "--merge-state --carry-over"; exit 0 ;;
        esac
        exit 0
    """)
    return repo, proj, calls


def test_main_runs_a_shard_under_its_instance_name(tmp_path, monkeypatch, capfd):
    repo, proj, calls = _main_project(tmp_path, monkeypatch, _r2_inventory())
    assert run_gate.main(["r2", "--shard", "0/2"]) == 0
    out = capfd.readouterr().out
    runs = [line for line in calls.read_text().splitlines()
            if line.startswith("run unit")]
    assert len(runs) == 1
    assert "--shard 0/2" in runs[0]
    assert (proj / ".assay" / "verdict-unit.shard0of2.json").is_file()
    assert not (proj / ".assay" / "verdict-unit.json").exists()
    history = json.loads(run_gate.history_store_path(proj).read_text())
    assert "r2.shard0of2" in history["lanes"]
    record = history["lanes"]["r2.shard0of2"]["latest"]
    assert record["selective"] is True
    assert record["r2_flex"]["shard"] == [0, 2]
    assert "r2.shard0of2" in out


def test_main_refuses_flex_flags_on_a_non_r2_lane(tmp_path, monkeypatch, capfd):
    inventory = _r2_inventory()
    doc = json.loads(inventory)
    doc["lanes"][0]["rigor"] = ["R0"]
    doc["lanes"][0]["mutation"] = None
    _main_project(tmp_path, monkeypatch, json.dumps(doc))
    assert run_gate.main(["r2", "--merge-shards", "2"]) == 2
    assert "native R2 assay lane" in capfd.readouterr().err


def test_main_refuses_flex_flags_on_a_query_verb(tmp_path, monkeypatch, capfd):
    _main_project(tmp_path, monkeypatch, _r2_inventory())
    assert run_gate.main(["history", "--shard", "0/2"]) == 2
    assert "apply only to an assay lane" in capfd.readouterr().err


def test_main_refuses_a_source_outside_the_checkout(tmp_path, monkeypatch, capfd):
    _main_project(tmp_path, monkeypatch, _r2_inventory())
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert run_gate.main(["r2", "--merge-state", str(outside)]) == 2
    assert "outside the checkout" in capfd.readouterr().err
    assert os.path.isdir(outside)

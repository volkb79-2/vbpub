"""RG-265/RG-266: flexible native-R2 campaigns.

* RG-266 -- `jobs = "auto"` can oversubscribe CPUs (`r2_oversubscribe`,
  default 1.0, recommended opt-in 1.5) because R2 candidates spend part of
  their wall time in IO; memory, `jobs_max` and the remaining candidates
  still cap the count.
* RG-265 -- Assay's flexible-campaign flags pass through (`--carry-over`,
  `--merge-state`, `--only-candidates`, `--exclude-candidates`), and
  `--shard K/N` turns one lane into N independent lane instances that can
  run at the same time, each with its own name, container, in-flight record,
  `.assay` artifacts and Assay state store; `--merge-shards N` merges them
  STRICTLY (independent review B1-B3: missing stores refused, Assay >= 12.2.0
  by inventory version, artifacts keyed by the Run-Gate lane too).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import re

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
    assert lane["_artifact_lane"] == "unit.r2.shard1of3"
    assert lane["_r2_flex"] == {"shard": [1, 3], "base_lane": "r2", "lane": "r2"}
    assert run_gate.assay_artifact_lane(lane) == "unit.r2.shard1of3"
    assert run_gate.assay_artifact_lane(dict(_LANE)) == "unit"
    assert "_r2_flex" not in _LANE, "the configured lane is never mutated"
    assert run_gate.r2_flex_record(lane["_r2_flex"]) == {
        "shard": [1, 3], "lane": "r2", "base_lane": "r2"}


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


def _inner(tmp_path, flex_args, *, judge_help="--merge-shard-store --carry-over",
           lane_name="r2"):
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
        {**_LANE, "assay_command": [str(judge)]}, lane_name, flex_args)
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
    shard_store = (repo / ".run-gate" / "assay-shards" / "proj" / "r2" / "unit"
                   / "1of3")
    assert f"--state-dir {shard_store}" in call
    assert f"--shard 1/3 --merge-state {main_store}" in call
    assert "--verdict-json .assay/verdict-unit.r2.shard1of3.json" in call
    assert "--progress .assay/progress-unit.r2.shard1of3.jsonl" in call
    assert shard_store.is_dir() and main_store.is_dir()


def test_merge_shards_strictly_merges_every_shard_store(tmp_path):
    repo, project, inner, log = _inner(tmp_path, _args(merge_shards="2"))
    shards = repo / ".run-gate" / "assay-shards" / "proj" / "r2" / "unit"
    for name in ("0of2", "1of2"):
        (shards / name).mkdir(parents=True)
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    assert (f"--merge-shard-store {shards / '0of2'} "
            f"--merge-shard-store {shards / '1of2'}") in call
    assert "--merge-state" not in call
    assert f"--state-dir {repo / '.run-gate' / 'assay-state' / 'proj'}" in call
    assert "--verdict-json .assay/verdict-unit.json" in call


def test_merge_shards_never_creates_a_missing_shard_store(tmp_path):
    """Review B1: a lost or not-yet-copied shard is refused, not fabricated."""
    repo, project, inner, log = _inner(tmp_path, _args(merge_shards="2"))
    shards = repo / ".run-gate" / "assay-shards" / "proj" / "r2" / "unit"
    (shards / "0of2").mkdir(parents=True)
    proc = subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert f"shard store {shards / '1of2'} is missing" in proc.stderr
    assert not (shards / "1of2").exists()
    assert not log.exists(), "the judge is never invoked"


def test_two_run_gate_lanes_sharing_an_assay_lane_never_share_a_shard(tmp_path):
    """Review B3: store, verdict and progress are keyed by the Run-Gate lane."""
    _repo, _project, first, _log = _inner(tmp_path / "a", _args(shard="0/2"),
                                         lane_name="r2a")
    _repo, _project, second, _log = _inner(tmp_path / "b", _args(shard="0/2"),
                                          lane_name="r2b")
    for inner, lane in ((first, "r2a"), (second, "r2b")):
        assert f"/assay-shards/proj/{lane}/unit/0of2" in inner
        assert f".assay/verdict-unit.{lane}.shard0of2.json" in inner
    a, _ = run_gate.attach_r2_flex(dict(_LANE), "r2a", _args(shard="0/2"))
    b, _ = run_gate.attach_r2_flex(dict(_LANE), "r2b", _args(shard="0/2"))
    assert run_gate.assay_artifact_lane(a) != run_gate.assay_artifact_lane(b)
    repo = tmp_path / "r"
    assert (run_gate.lane_assay_state_dir(a, repo, repo / "p")
            != run_gate.lane_assay_state_dir(b, repo, repo / "p"))


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
    assert "does not support --merge-shard-store" in proc.stderr
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
    assert run_gate.assay_shard_state_dir(root, main_store, "r2", "unit", 0, 2) == (
        root / "assay-shards-external" / ("a" * 64) / "r2" / "unit" / "0of2")


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


def _r2_inventory(version: str = "12.2.0", **mutation) -> str:
    entry = {"name": "unit", "scope": "S1", "rigor": ["R0", "R2"],
             "enforcement": "gate", "language": "python",
             "rigor_reachable": ["R0", "R2"], "coverage": None,
             "mutation": {"jobs_max": 4, "max_mutants": 100, **mutation},
             "canary": None, "base_source": None, "external_tools": [],
             "argv0": None, "env_required": [], "environment_command": False,
             "infrastructure_facts": [], "budget": None, "cwd": None,
             "link_paths": [], "snapshot_selection": "repository"}
    return json.dumps({"assay_version": version, "inventory_schema": 1,
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
          *"run --help"*) echo "--merge-shard-store --carry-over"; exit 0 ;;
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
    assert (proj / ".assay" / "verdict-unit.r2.shard0of2.json").is_file()
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


# --- independent review: B1 / B1a / B2 / image preflight ----------------------


@pytest.mark.parametrize(
    "version", ["12.1.99", "12.2.0.dev3+gabcdef0", "0+unknown", "garbage"])
def test_main_refuses_a_judge_below_the_flex_floor(
        tmp_path, monkeypatch, capfd, version):
    _repo, _proj, calls = _main_project(
        tmp_path, monkeypatch, _r2_inventory(version=version))
    assert run_gate.main(["r2", "--shard", "0/2"]) == 2
    err = capfd.readouterr().err
    assert "need assay >= 12.2.0" in err and "(judge floor)" in err
    assert not any(line.startswith("run unit")
                   for line in calls.read_text().splitlines())


@pytest.mark.parametrize("version", ["12.2.0", "12.3.0", "12.2.1.dev4+g0123abc"])
def test_main_accepts_a_judge_at_or_above_the_flex_floor(
        tmp_path, monkeypatch, version):
    _main_project(tmp_path, monkeypatch, _r2_inventory(version=version))
    assert run_gate.main(["r2", "--shard", "0/2"]) == 0


def test_main_refuses_merge_shards_with_a_missing_store(tmp_path, monkeypatch, capfd):
    repo, proj, calls = _main_project(tmp_path, monkeypatch, _r2_inventory())
    shards = repo / ".run-gate" / "assay-shards" / "proj" / "r2" / "unit"
    (shards / "0of2").mkdir(parents=True)
    assert run_gate.main(["r2", "--merge-shards", "2"]) == 2
    err = capfd.readouterr().err
    assert "shard store(s) missing" in err and str(shards / "1of2") in err
    assert not (shards / "1of2").exists()
    assert not any(line.startswith("run unit")
                   for line in calls.read_text().splitlines())


def test_main_merges_when_every_store_exists(tmp_path, monkeypatch):
    repo, proj, calls = _main_project(tmp_path, monkeypatch, _r2_inventory())
    shards = repo / ".run-gate" / "assay-shards" / "proj" / "r2" / "unit"
    for name in ("0of2", "1of2"):
        (shards / name).mkdir(parents=True)
    assert run_gate.main(["r2", "--merge-shards", "2"]) == 0
    [run] = [line for line in calls.read_text().splitlines()
             if line.startswith("run unit")]
    assert run.count("--merge-shard-store") == 2


def test_a_container_state_root_is_left_to_the_lane_script(tmp_path):
    lane, _ = run_gate.attach_r2_flex(dict(_LANE), "r2", _args(merge_shards="2"))
    run_gate.refuse_missing_merge_shard_stores(
        lane, tmp_path, tmp_path / "p", {"state_root": "/in/container"})
    assert run_gate.merge_shard_store_dirs(dict(_LANE), tmp_path, tmp_path) == []


@pytest.mark.parametrize(
    "lane_over,env,message",
    [
        ({}, {"mode": "exec"}, "runs in exec environment 'e'"),
        ({"resources": {"shared": ["pg"]}}, {"mode": "ephemeral"},
         "declares resources.shared ['pg']"),
    ],
)
def test_a_shard_of_a_serialized_lane_is_refused(lane_over, env, message):
    lane, _ = run_gate.attach_r2_flex({**_LANE, **lane_over}, "r2",
                                      _args(shard="0/2"))
    with pytest.raises(run_gate.GateError, match=re.escape(message)):
        run_gate.refuse_serialized_shard(lane, "r2", env, "e")


def test_serialized_lanes_may_still_run_unsharded_or_merge():
    for over in ({"merge_shards": "2"}, {"merge_state": ["/x"]}):
        lane, _ = run_gate.attach_r2_flex(
            {**_LANE, "resources": {"shared": ["pg"]}}, "r2", _args(**over))
        run_gate.refuse_serialized_shard(lane, "r2", {"mode": "exec"}, "e")
    run_gate.refuse_serialized_shard(dict(_LANE), "r2", {"mode": "exec"}, "e")
    lane, _ = run_gate.attach_r2_flex(dict(_LANE), "r2", _args(shard="0/2"))
    run_gate.refuse_serialized_shard(lane, "r2", {"mode": "ephemeral"}, "e")


def test_main_refuses_a_shared_infra_shard(tmp_path, monkeypatch, capfd):
    repo, proj, calls = _main_project(tmp_path, monkeypatch, _r2_inventory())
    cfg = proj / "run-gate.toml"
    cfg.write_text(cfg.read_text() + '\n[lanes.r2.resources]\nshared = ["pg"]\n')
    commit_all(repo, "shared")
    assert run_gate.main(["r2", "--shard", "0/2"]) == 2
    assert "declares resources.shared" in capfd.readouterr().err


def _fake_docker_image(tmp_path, present: bool) -> str:
    docker = tmp_path / "docker-image"
    docker.write_text("#!/bin/sh\n" + ("echo sha256:" + "a" * 64 + "\nexit 0\n"
                      if present else "echo 'No such image' >&2\nexit 1\n"))
    docker.chmod(0o755)
    return str(docker)


def test_a_shard_needs_its_image_on_the_host(tmp_path):
    run_gate.refuse_unpulled_shard_image(_fake_docker_image(tmp_path, True), "img:1")
    with pytest.raises(run_gate.GateError, match="is not present on this host"):
        run_gate.refuse_unpulled_shard_image(
            _fake_docker_image(tmp_path, False), "img:1")
    with pytest.raises(run_gate.GateInfraError, match="cannot inspect image"):
        run_gate.refuse_unpulled_shard_image(str(tmp_path / "no-docker"), "img:1")


def test_the_default_does_not_oversubscribe():
    assert run_gate.R2_OVERSUBSCRIBE_DEFAULT == "1.0"


# --- documentation contract (review B4) ----------------------------------------


_DOCS = Path(__file__).resolve().parents[1]


def _doc(name: str) -> str:
    return " ".join((_DOCS / name).read_text(encoding="utf-8").split())


@pytest.mark.parametrize(
    "name", ["README.md", "SPEC.md", "CONSUMERS.md", "LANE-AUTHORING.md", "CHANGES.md"])
def test_every_user_document_states_the_judge_floor(name):
    assert "12.2.0" in _doc(name)


@pytest.mark.parametrize("name", ["SPEC.md", "CONSUMERS.md"])
def test_the_auto_formula_includes_oversubscription(name):
    assert "floor(cpu_cap * r2_oversubscribe)" in _doc(name)


@pytest.mark.parametrize(
    "name", ["README.md", "SPEC.md", "CONSUMERS.md", "LANE-AUTHORING.md"])
def test_documents_state_the_default_factor(name):
    text = _doc(name)
    assert 'default `"1.0"`' in text and '"1.5"' in text


def test_documents_describe_the_strict_merge_not_re_execution():
    consumers = _doc("CONSUMERS.md")
    assert "simply executed by the merge run" not in consumers
    assert "a lost shard is never silently executed" in consumers
    assert "STRICT" in _doc("SPEC.md") and "--merge-shard-store" in _doc("SPEC.md")


def test_documents_list_the_whole_flag_surface():
    for name in ("README.md", "SPEC.md", "CONSUMERS.md"):
        text = _doc(name)
        for flag in ("--shard", "--merge-shards", "--carry-over",
                     "--merge-state", "--only-candidates", "--exclude-candidates"):
            assert flag in text, (name, flag)


def test_consumers_state_the_resource_identity_split():
    assert "not judge identity since Assay 12.2.0 (B529)" in _doc("CONSUMERS.md")


def test_lane_authoring_no_longer_says_candidate_ram_does_not_select_workers():
    assert "do not currently select workers" not in _doc("LANE-AUTHORING.md")


def test_main_checks_the_image_before_an_ephemeral_shard(tmp_path, monkeypatch, capfd):
    repo, proj, calls = _main_project(tmp_path, monkeypatch, _r2_inventory())
    cfg = proj / "run-gate.toml"
    cfg.write_text(cfg.read_text().replace(
        'mode = "host"', 'mode = "ephemeral"\nimage = "tester:local"'))
    commit_all(repo, "ephemeral")
    seen: list[str] = []

    def refuse(docker, image):
        seen.append(image)
        run_gate.fail(f"--shard: image {image!r} is not present on this host")

    monkeypatch.setattr(run_gate, "refuse_unpulled_shard_image", refuse)
    assert run_gate.main(["r2", "--shard", "0/2"]) == 2
    assert seen == ["tester:local"]
    assert "is not present on this host" in capfd.readouterr().err

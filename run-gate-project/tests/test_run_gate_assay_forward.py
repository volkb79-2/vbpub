"""RG-297 (T12): Run-Gate forwards an assay lane's arguments after `--`.

Each tool validates only its own rules. Assay owns the semantics of its
mutation-store flags (`--merge-manifest`, `--carry-forward`, `--merge-state`,
`--merge-shard-store`, `--reuse-from`, `--only-candidates`, ...); Run-Gate
forwards them OPAQUELY and checks only what is genuinely its own:

* the flags it injects or owns itself may never be forwarded twice,
* every PATH-valued flag must be visible in the lane environment (the
  checkout and the judged worktree are the only mounts),
* a forwarded flag must appear in the judge's own `capabilities --json`
  document under `commands.run` (Assay >= 12.3.0, whose floor Run-Gate
  states from the judge inventory before any container starts; the fake
  judge here answers both).

An inventory that cannot be read at all is refused too, in Run-Gate's own
words, before a container starts -- otherwise the floor above could never
apply to the one judge RG-318 exists to refuse.

Nothing here parses a merge manifest: an absent or inconsistent manifest is
Assay's refusal, passed through unreworded.
"""
from __future__ import annotations

import json
import re
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
    make_worktree,
    run_gate,
)
from test_run_gate import (  # noqa: E402,F401
    ambient_cgroup, isolate_host_profiler_socket, profiling_off_by_default)
from test_run_gate_r2_flex import _LANE, _args  # noqa: E402

#: A native-R2 assay lane on a bare-host environment (the r2-flex fixture
#: shape): `main()` drives it end to end against the fake judge.
_CFG = """\
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

#: A capability document in the shipped shape (`assay-capabilities/2`):
#: `commands.run` is the closed list of long options the judge accepts.
_CAPABILITIES = {
    "schema": "assay-capabilities/2",
    "assay_version": "12.3.0",
    "verdict_schema_version": 2,
    "commands": {
        "run": ["--carry-forward", "--carry-over", "--carry-over-reason",
                "--candidates-file", "--exclude-candidates",
                "--merge-manifest", "--merge-shard-store", "--merge-state",
                "--only-candidates", "--rejudge", "--rejudge-outcome",
                "--reuse-from", "--resume", "--state-dir"],
        "plan": ["--file"],
    },
    "mutation_store_policies": {},
    "combine_policies": [],
}

#: The fake judge's `lanes --json` inventory: a native R2 lane at the
#: RG-265 floor (12.2.0), exactly the shape `test_run_gate_r2_flex` uses.
_INVENTORY_LANE = {
    "name": "unit", "scope": "S1", "rigor": ["R0", "R2"],
    "enforcement": "gate", "language": "python",
    "rigor_reachable": ["R0", "R2"], "coverage": None,
    "mutation": {"jobs_max": 4, "max_mutants": 100}, "canary": None,
    "base_source": None, "external_tools": [], "argv0": None,
    "env_required": [], "environment_command": False,
    "infrastructure_facts": [], "budget": None, "cwd": None,
    "link_paths": [], "snapshot_selection": "repository",
}

_VALID = object()


def _caps(**over) -> str:
    return json.dumps({**_CAPABILITIES, **over})


def _inventory(version: str = "12.2.0") -> str:
    return json.dumps({"assay_version": version, "inventory_schema": 1,
                       "lanes": [_INVENTORY_LANE]})


# --- the pure parser ---------------------------------------------------------


@pytest.mark.parametrize("flag", ["--state-dir", "--progress",
                                  "--progress-heartbeat", "--verdict-json",
                                  "--file", "--request-base"])
def test_every_flag_run_gate_injects_is_refused_by_name(flag, tmp_path):
    with pytest.raises(run_gate.GateError,
                       match=f"{flag} is injected by Run-Gate"):
        run_gate.parse_assay_forward([flag, "x"], tmp_path)


@pytest.mark.parametrize("flag", ["--jobs", "--shard", "--merge-shards"])
def test_every_flag_run_gate_owns_itself_is_refused_by_name(flag, tmp_path):
    with pytest.raises(
            run_gate.GateError,
            match=re.escape(f"{flag} is owned by Run-Gate: give it before "
                            "'--' (or drop it)")):
        run_gate.parse_assay_forward([flag, "x"], tmp_path)


def test_the_owned_rule_also_sees_the_equals_spelling(tmp_path):
    with pytest.raises(run_gate.GateError,
                       match="--verdict-json is injected by Run-Gate"):
        run_gate.parse_assay_forward(["--verdict-json=x.json"], tmp_path)
    with pytest.raises(run_gate.GateError,
                       match="--jobs is owned by Run-Gate"):
        run_gate.parse_assay_forward(["--jobs=4"], tmp_path)


def test_a_leading_bare_token_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(run_gate.GateError,
                       match="can only be the value of the preceding flag"):
        run_gate.parse_assay_forward(["extra"], tmp_path)


@pytest.mark.parametrize("tokens", [["--merge-state"],
                                    ["--merge-state="],
                                    ["--merge-manifest", "--rejudge"]])
def test_a_path_flag_without_a_value_is_refused(tokens, tmp_path):
    with pytest.raises(run_gate.GateError, match="requires a value"):
        run_gate.parse_assay_forward(tokens, tmp_path)


def test_both_spellings_are_kept_and_path_values_are_made_absolute(tmp_path):
    store = tmp_path / "store"
    manifest = store / "merge.json"
    tokens, pairs = run_gate.parse_assay_forward([
        "--merge-state", str(store),
        f"--merge-manifest={manifest}",
        "--carry-forward", str(store),
        "--rejudge", "a,b",
    ], tmp_path)
    assert tokens == ["--merge-state", str(store),
                      f"--merge-manifest={manifest}",
                      "--carry-forward", str(store),
                      "--rejudge", "a,b"]
    assert pairs == [("--merge-state", str(store)),
                     ("--merge-manifest", str(manifest)),
                     ("--carry-forward", str(store))]


def test_a_relative_value_resolves_against_the_lane_cwd_not_the_invokers(
        tmp_path, monkeypatch):
    """Review M1: the judge resolves a relative value against the project
    directory the lane script `cd`s into, so the host must resolve the same
    one. Run-Gate never `chdir`s, so the invoking cwd is routinely a
    different tree (a monorepo root, `--worktree`)."""
    invoking = tmp_path / "repo"
    project = invoking / "proj"
    (project / ".run-gate").mkdir(parents=True)
    (invoking / ".run-gate").mkdir()
    monkeypatch.chdir(invoking)
    tokens, pairs = run_gate.parse_assay_forward(
        ["--merge-state", ".run-gate/store"], project)
    assert tokens == ["--merge-state", ".run-gate/store"]  # token as given
    assert pairs == [("--merge-state", str(project / ".run-gate" / "store"))]


def test_a_bare_token_may_be_a_flag_value(tmp_path):
    tokens, pairs = run_gate.parse_assay_forward(
        ["--carry-over-reason", "-abc"], tmp_path)
    assert tokens == ["--carry-over-reason", "-abc"] and pairs == []


# --- the generated inner script ---------------------------------------------


def _inner(tmp_path, tokens, *, flex_args=None, capabilities=_VALID,
           capacity_exit: int = 0, judge_help="--merge-shard-store "
           "--carry-over", lane_name="r2", base_dir="project",
           refusal_marker=None):
    """Build an assay inner for a lane whose forwarded tokens are `tokens`.

    `capabilities` is the document the fake judge prints for
    `capabilities --json`; `None` prints nothing (the shape of a judge that
    answers neither the probe nor an inventory, only reachable here by
    calling the generated script directly -- `main()` refuses such a judge by
    name first).
    `refusal_marker` makes the fake judge print that line on stderr and exit
    `capacity_exit` INSTEAD of printing a document, the shape of a judge that
    refuses `capabilities` itself (a build that cannot import its analysis
    package, or one below Assay 12.3.0 whose inventory was still readable).
    `base_dir` is what the relative path values resolve against ("project":
    the directory the lane `cd`s into; anything else is passed as the base).
    """
    repo = tmp_path / "repo"
    project = repo / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (repo / ".run-gate").mkdir(exist_ok=True)
    log = tmp_path / "calls"
    judge = tmp_path / "assay"
    if capabilities is _VALID:
        capabilities = _caps()
    document = "" if capabilities is None else capabilities
    if refusal_marker is None:
        capability_branch = (
            "  *\"capabilities --json\"*) cat <<'JSON'\n"
            f"{document}\n"
            "JSON\n"
            f"     exit {capacity_exit} ;;\n")
    else:
        capability_branch = (
            "  *\"capabilities --json\"*) "
            f"printf '%s\\n' {shlex.quote(refusal_marker)} >&2; "
            f"exit {capacity_exit} ;;\n")
    judge.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        "case \"$*\" in\n"
        f"{capability_branch}"
        "  *\"run --help\"*) "
        f"echo {shlex.quote(judge_help)}; exit 0;;\n"
        "esac\n"
        "exit 0\n")
    judge.chmod(0o755)
    lane = {**_LANE, "assay_command": [str(judge)]}
    if flex_args is not None:
        lane, _name = run_gate.attach_r2_flex(lane, lane_name, flex_args)
    base = project if base_dir == "project" else base_dir
    forward, pairs = run_gate.parse_assay_forward(list(tokens), base)
    lane["_assay_forward"] = forward
    lane["_assay_forward_paths"] = pairs
    inner = run_gate.build_assay_inner(lane, project, repo, worktree=repo,
                                       state_root=repo / ".run-gate")
    return repo, project, inner, log


def _shell(inner: str, project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", inner], cwd=project,
                          capture_output=True, text=True)


def _run_call(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines()
            if line.startswith("run unit")]


def _cap_call(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines()
            if line.startswith("capabilities")]


@pytest.mark.parametrize("tokens,expected", [
    (["--merge-manifest", "{repo}/.run-gate/m.json"],
     "--merge-manifest {repo}/.run-gate/m.json"),
    (["--carry-forward={repo}/.run-gate/store"],
     "--carry-forward={repo}/.run-gate/store"),
    (["--merge-state", "{repo}/.run-gate/s", "--rejudge-outcome", "killed"],
     "--merge-state {repo}/.run-gate/s --rejudge-outcome killed"),
])
def test_forwarded_tokens_reach_the_judge_verbatim_and_in_order(
        tmp_path, tokens, expected):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".run-gate").mkdir()
    tokens = [token.replace("{repo}", str(repo)) for token in tokens]
    expected = expected.replace("{repo}", str(repo))
    _repo, project, inner, log = _inner(tmp_path, tokens)
    proc = _shell(inner, project)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    assert call.endswith(expected), call
    assert _cap_call(log) == ["capabilities --json"]


def test_forwarded_tokens_follow_the_run_gate_flex_argv(tmp_path):
    repo = tmp_path / "repo"
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True)
    _repo, project, inner, log = _inner(
        tmp_path, ["--carry-forward", str(store)],
        flex_args=_args(carry_over=[str(store)], carry_over_reason="why"))
    proc = _shell(inner, project)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    own = f"--carry-over {store} --carry-over-reason why"
    forwarded = f"--carry-forward {store}"
    assert call.index(own) < call.index(forwarded), call
    assert call.endswith(forwarded), call


def test_a_lane_without_forwarded_tokens_is_byte_unchanged():
    inner = run_gate.build_assay_inner(dict(_LANE), Path("/p"), Path("/r"))
    assert "capabilities" not in inner
    assert "run --help" not in inner
    assert inner.rstrip().split()[-2] == "--state-dir"


def test_the_probe_refuses_a_flag_the_judge_does_not_list(tmp_path):
    repo = tmp_path / "repo"
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True)
    document = _caps(commands={"run": ["--merge-state", "--carry-forward"]})
    _repo, project, inner, log = _inner(
        tmp_path, ["--merge-manifest", str(store / "m.json")],
        capabilities=document)
    proc = _shell(inner, project)
    assert proc.returncode == 2
    assert ("this judge does not support --merge-manifest "
            "(assay capabilities)") in proc.stderr
    assert _run_call(log) == []


def test_an_absent_manifest_is_assays_refusal_not_run_gates(tmp_path):
    repo = tmp_path / "repo"
    manifest = repo / ".run-gate" / "m.json"
    _repo, project, inner, log = _inner(
        tmp_path, ["--merge-manifest", str(manifest)], capabilities=_caps())
    assert not manifest.exists(), "run-gate never opens the manifest"
    proc = _shell(inner, project)
    assert proc.returncode == 0, proc.stderr
    (call,) = _run_call(log)
    assert f"--merge-manifest {manifest}" in call


@pytest.mark.parametrize(("capabilities", "message"), [
    (_caps(schema="assay-capabilities/1"),
     "capability document schema 'assay-capabilities/1'"),
    ("", "did not return a capability document"),
    (None, "did not return a capability document"),
])
def test_the_probe_refuses_an_unusable_capability_document(
        tmp_path, capabilities, message):
    repo = tmp_path / "repo"
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True)
    _repo, project, inner, log = _inner(
        tmp_path, ["--merge-state", str(store)], capabilities=capabilities)
    proc = _shell(inner, project)
    assert proc.returncode == 2
    assert message in proc.stderr
    assert _run_call(log) == []


def test_the_probe_passes_the_judges_own_refusal_through(tmp_path):
    """A judge that refuses `capabilities` itself keeps ITS OWN line.

    The fake judge here prints a marker on stderr and exits 2, which is what
    a judge with a broken analysis import does; the assertion is that the
    marker IS in the lane's
    stderr (not merely that Run-Gate's own wording is absent, which would also
    hold if nothing at all were passed through) and that the status is the
    judge's own 2."""
    repo = tmp_path / "repo"
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True)
    marker = ("assay: the required assay_analysis package could not be "
              "imported")
    _repo, project, inner, log = _inner(
        tmp_path, ["--merge-state", str(store)],
        refusal_marker=marker, capacity_exit=2)
    proc = _shell(inner, project)
    assert proc.returncode == 2
    assert marker in proc.stderr
    assert "did not return a capability document" not in proc.stderr
    assert "(assay capabilities)" not in proc.stderr
    assert _run_call(log) == []


def test_the_help_probe_still_covers_the_run_gate_own_flags(tmp_path):
    """The own flags keep the `run --help` probe: Assay 12.2.0 has none."""
    repo = tmp_path / "repo"
    store = repo / ".run-gate" / "store"
    _repo, project, inner, log = _inner(
        tmp_path, ["--merge-state", str(store)],
        flex_args=_args(merge_state=[str(store)]), judge_help="usage: assay run")
    assert "does not support --merge-shard-store" in inner
    proc = _shell(inner, project)
    assert proc.returncode == 2, proc.stderr


# --- end to end through main() ----------------------------------------------


def _fake_judge(tmp_path, monkeypatch, *, capabilities=_VALID,
                capacity_exit: int = 0, version: str = "12.3.0",
                inventory_exit: int = 0) -> Path:
    """The fake judge `main()` talks to; returns the log of its argv.

    `inventory_exit` makes `assay lanes --json` FAIL the way an unreadable
    judge does (a probe that exits non-zero is `assay_inventory()`'s
    `(None, why)` shape at `run_gate.py:9828-9834`), which is the state
    RG-318's floor must still refuse in."""
    calls = tmp_path / "judge-calls"
    if capabilities is _VALID:
        capabilities = _caps()
    capability_branch = (
        "  *\"capabilities --json\"*) printf '%s\\n' "
        f"{shlex.quote(capabilities or '')}; exit {capacity_exit} ;;\n")
    install_fake_assay(monkeypatch, f"""\
        #!/bin/sh
        printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
        case "$*" in
          *"lanes --json"*) printf '%s\\n' {shlex.quote(_inventory(version))}
            exit {inventory_exit} ;;
          *--version*) echo "assay {version}"; exit 0 ;;
          *"run --help"*) echo "--merge-shard-store --carry-over"; exit 0 ;;
{capability_branch}        esac
        exit 0
    """)
    return calls


def _main_project(tmp_path, monkeypatch, *, capabilities=_VALID,
                  capacity_exit: int = 0, version: str = "12.3.0",
                  inventory_exit: int = 0):
    repo = make_repo(tmp_path)
    proj = make_project(repo, _CFG)
    (proj / "assay.toml").write_text("# judged by the fake judge\n")
    commit_all(repo, "assay config")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(sys, "argv", ["run-gate"])
    fake_docker(tmp_path, monkeypatch)
    calls = _fake_judge(tmp_path, monkeypatch, capabilities=capabilities,
                        capacity_exit=capacity_exit, version=version,
                        inventory_exit=inventory_exit)
    return repo, proj, calls


def _judge_runs(calls: Path) -> list[str]:
    if not calls.exists():
        return []
    return [line for line in calls.read_text().splitlines()
            if line.startswith("run unit")]


def test_main_refuses_a_judge_below_the_forward_floor(tmp_path, monkeypatch,
                                                       capfd):
    """RG-318: forwarded store flags need Assay >= 12.3.0 (inventory floor).

    The floor is read from the judge's OWN inventory version, so the refusal
    lands before a container starts; the in-lane capability probe remains the
    check that decides whether the judge really supports the flag."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch,
                                       version="12.2.0")
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--", "--merge-state", str(store)]) == 3
    err = capfd.readouterr().err
    assert "requires assay >= 12.3.0" in err, err
    # Review r2: the refusal names the ACTION, not only the requirement.
    assert "update the checkout, or re-pin the judge to >= 12.3.0" in err, err
    assert _judge_runs(calls) == []


def test_main_refuses_forwarded_tokens_without_an_inventory(
        tmp_path, monkeypatch, capfd):
    """Review r2 M1: an unreadable inventory refuses the forwarded form too.

    RG-318's floor is read FROM the inventory version, so before this refusal
    a judge whose `assay lanes --json` cannot answer at all skipped the floor
    entirely and reached a container, where only the in-lane probe refused it
    -- the one judge the floor exists for. The refusal is Run-Gate's own (not
    the flex/R2 text, which names a requirement forwarded arguments do not
    have), it is exit 2 (the convention of the no-inventory branch it joins),
    and no judge run is started."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch,
                                       inventory_exit=1)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--", "--merge-state", str(store)]) == 2
    err = capfd.readouterr().err
    assert "arguments after '--' for lane 'r2'" in err, err
    assert "need an inventory proving the judge accepts them" in err, err
    assert "assay >= 12.3.0, RG-318" in err, err
    assert "`assay lanes --json` did not run" in err, err
    assert "selective assay flags" not in err, err
    assert _judge_runs(calls) == []


def test_main_keeps_todays_behaviour_for_a_lane_without_forwarded_tokens(
        tmp_path, monkeypatch, capfd):
    """The M1 refusal is about forwarded tokens only: without them an
    unreadable inventory changes nothing (the lane still runs)."""
    _repo, _proj, calls = _main_project(tmp_path, monkeypatch,
                                        inventory_exit=1)
    assert run_gate.main(["r2"]) == 0
    assert _judge_runs(calls) != []


def test_main_forwards_a_relative_value_inside_the_selected_worktree(
        tmp_path, monkeypatch, capfd):
    """Review r2 M2: the base a RELATIVE forwarded value resolves against.

    The invoking tree is `w1`, the SELECTED tree (--worktree) is the main
    checkout, so the lane `cd`s into `<repo>/proj` while this process's cwd
    stays `<w1>/proj`. `../store` is legal against the lane's directory
    (`<repo>/store`, under the selected tree) and ILLEGAL against the cwd
    (`<w1>/store`, outside both mounted roots) -- which is exactly the
    difference a call site passing the process cwd instead of the project dir
    makes, and the only shape in which a relative value can tell them apart.
    The token itself is still handed over verbatim."""
    repo = make_repo(tmp_path)
    proj = make_project(repo, _CFG)
    (proj / "assay.toml").write_text("# judged by the fake judge\n")
    commit_all(repo, "assay config")
    invoking = make_worktree(repo, tmp_path, "w1")
    monkeypatch.chdir(invoking / "proj")
    monkeypatch.setattr(sys, "argv", ["run-gate"])
    fake_docker(tmp_path, monkeypatch)
    calls = _fake_judge(tmp_path, monkeypatch)
    # Only the SELECTED tree has the store the relative value names.
    (repo / "store").mkdir()
    assert not (invoking / "store").exists()
    assert run_gate.main(["r2", "--worktree", str(repo), "--",
                          "--merge-state", "../store"]) == 0, \
        capfd.readouterr().err
    [call] = _judge_runs(calls)
    assert call.endswith("--merge-state ../store"), call


def test_main_forwards_store_flags_and_records_them(tmp_path, monkeypatch,
                                                     capfd):
    repo, proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "carried"
    store.mkdir(parents=True, exist_ok=True)
    manifest = repo / ".run-gate" / "merge.json"
    assert run_gate.main(["r2", "--", "--merge-state", str(store),
                          "--merge-manifest", str(manifest)]) == 0
    [call] = _judge_runs(calls)
    assert call.endswith(f"--merge-state {store} "
                         f"--merge-manifest {manifest}"), call
    history = json.loads(run_gate.history_store_path(proj).read_text())
    record = history["lanes"]["r2"]["latest"]
    assert record["selective"] is True
    assert record["assay_forward"] == ["--merge-state", str(store),
                                       "--merge-manifest", str(manifest)]


def test_main_refuses_a_path_outside_the_checkout_and_worktree(
        tmp_path, monkeypatch, capfd):
    _repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert run_gate.main(["r2", "--", "--merge-state", str(outside)]) == 2
    err = capfd.readouterr().err
    assert f"--merge-state {str(outside)!r} is outside the checkout" in err
    assert _judge_runs(calls) == []


def test_main_refuses_a_manifest_directory_outside_the_checkout(
        tmp_path, monkeypatch, capfd):
    _repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert run_gate.main(["r2", "--",
                          "--merge-manifest", str(elsewhere / "m.json")]) == 2
    err = capfd.readouterr().err
    assert (f"--merge-manifest {str(elsewhere)!r} is outside the checkout"
            in err)
    assert _judge_runs(calls) == []


def test_main_refuses_own_flags_after_the_separator(tmp_path, monkeypatch,
                                                    capfd):
    _repo, _proj, _calls = _main_project(tmp_path, monkeypatch)
    assert run_gate.main(["r2", "--", "--verdict-json", "v.json"]) == 2
    assert "--verdict-json is injected by Run-Gate" in capfd.readouterr().err
    assert run_gate.main(["r2", "--", "--shard", "0/2"]) == 2
    assert ("--shard is owned by Run-Gate: give it before '--' (or drop it)"
            in capfd.readouterr().err)


def test_a_forwarded_replace_valued_flag_is_refused_when_run_gate_sets_it(
        tmp_path, monkeypatch, capfd):
    """Review M5, replace-valued shape: `--rejudge-outcome` is last-wins.

    `run-gate r2 --rejudge-outcome hung -- --rejudge-outcome crashed` would
    otherwise put TWO copies on the judge's argv, and argparse would run the
    forwarded selection while the history record names the own one: a verdict
    describing a selection Run-Gate never recorded."""
    _repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    assert run_gate.main(["r2", "--rejudge-outcome", "hung", "--",
                          "--rejudge-outcome", "crashed"]) == 2
    assert ("--rejudge-outcome is already set by a Run-Gate option in this "
            "invocation; drop it after '--'") in capfd.readouterr().err
    assert _judge_runs(calls) == []


def test_a_forwarded_append_valued_flag_is_refused_when_run_gate_sets_it(
        tmp_path, monkeypatch, capfd):
    """Review M5, append-valued shape: a second `--carry-over` would ADD a
    store next to the one the operator declared, with no record of it."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    first = repo / ".run-gate" / "one"
    second = repo / ".run-gate" / "two"
    first.mkdir(parents=True, exist_ok=True)
    second.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--carry-over", str(first),
                          "--carry-over-reason", "why", "--",
                          "--carry-over", str(second)]) == 2
    assert ("--carry-over is already set by a Run-Gate option in this "
            "invocation; drop it after '--'") in capfd.readouterr().err
    assert _judge_runs(calls) == []


def test_a_forwarded_merge_state_is_refused_when_a_shard_appends_one(
        tmp_path, monkeypatch, capfd):
    """`--shard K/N` really puts `--merge-state` on the argv (the main
    store), so a forwarded one is a duplicate of Run-Gate's own token."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--shard", "0/2", "--",
                          "--merge-state", str(store)]) == 2
    assert ("--merge-state is already set by a Run-Gate option in this "
            "invocation; drop it after '--'") in capfd.readouterr().err
    assert _judge_runs(calls) == []


def test_rejudge_contaminated_forbids_forwarded_tokens(tmp_path, monkeypatch,
                                                       capfd):
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--rejudge-contaminated", "--",
                          "--merge-state", str(store)]) == 2
    assert ("--rejudge-contaminated forces its own selection; it cannot be "
            "combined with arguments after '--'") in capfd.readouterr().err
    assert _judge_runs(calls) == []


def test_main_refuses_forwarded_arguments_on_a_lane_kind_that_takes_none(
        tmp_path, monkeypatch, capfd):
    """A lane whose kind is neither `command` nor `assay` takes no `--`
    arguments at all; the refusal names the kind it found."""
    _repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    monkeypatch.setattr(run_gate, "merge_lanes",
                        lambda *_a, **_k: {"weird": {"kind": "widget"}})
    assert run_gate.main(["weird", "--", "extra"]) == 2
    err = capfd.readouterr().err
    assert ("lane 'weird' has kind 'widget' and does not accept selective "
            "command arguments") in err, err
    assert _judge_runs(calls) == []


#: One row per Run-Gate own option that puts an Assay flag on the judge's
#: argv: the arguments given before `--`, the flag forwarded after it, and the
#: refusal each must produce (the two per-kind OWNED flags are refused by the
#: owned list before the duplicate-name rule can speak).
_DUPLICATE_ROWS = [
    (["--carry-over", "{store}", "--carry-over-reason", "why"],
     "--carry-over", "{store}/two", True),
    (["--carry-over-reason", "why"], "--carry-over-reason", "again", True),
    (["--merge-state", "{store}"], "--merge-state", "{store}", True),
    (["--shard", "0/2"], "--shard", "0/2", False),
    (["--merge-shards", "1"], "--merge-shard-store", "{store}/0of1", True),
    (["--only-candidates", "{store}/ids.txt"], "--only-candidates",
     "{store}/ids.txt", True),
    (["--exclude-candidates", "{store}/ids.txt"], "--exclude-candidates",
     "{store}/ids.txt", True),
    (["--reuse-from", "{store}/verdict.json"], "--reuse-from",
     "{store}/verdict.json", True),
    (["--rejudge", "m1"], "--rejudge", "m2", True),
    (["--rejudge-outcome", "killed"], "--rejudge-outcome", "survived", True),
]


@pytest.mark.parametrize("own_args,forwarded,value,duplicate", _DUPLICATE_ROWS)
def test_every_own_option_refuses_its_forwarded_duplicate(
        tmp_path, monkeypatch, capfd, own_args, forwarded, value, duplicate):
    """Review r2 coverage: each own option's duplicate-name branch.

    Every row is the same operator mistake at the judge (`--flag` once from a
    Run-Gate option and once after `--`), refused by name before a judge run,
    whichever option produced it. `--shard` is listed as OWNED rather than
    duplicated because its forwarded copy is a Run-Gate flag by definition; it
    is here so each own option's row names the refusal it really gets."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    own = [arg.replace("{store}", str(store)) for arg in own_args]
    forwarded_value = value.replace("{store}", str(store))
    assert run_gate.main(["r2", *own, "--", forwarded,
                          forwarded_value]) == 2
    err = capfd.readouterr().err
    if duplicate:
        assert (f"{forwarded} is already set by a Run-Gate option in this "
                "invocation; drop it after '--'") in err, err
    else:
        assert (f"{forwarded} is owned by Run-Gate: give it before '--' "
                "(or drop it)") in err, err
    assert _judge_runs(calls) == []


def test_lanes_check_neither_probes_nor_forwards(tmp_path, monkeypatch, capfd):
    """`lanes --check` never sees `_assay_forward`: no probe, no forwarding."""
    _repo, proj, calls = _main_project(tmp_path, monkeypatch)
    # 1: the fixture's assay.toml does not declare the lane, which is the
    # finding this check reports. What matters here is that the forward path
    # is invisible to it.
    assert run_gate.main(["lanes", "--check", "--project", str(proj)]) == 1
    logged = calls.read_text().splitlines() if calls.exists() else []
    assert not [line for line in logged if line.startswith("capabilities")]
    assert not [line for line in logged if "merge-manifest" in line]


# --- Assay owns three checks Run-Gate used to duplicate (subtask 3) -----------


def test_a_carry_over_without_its_reason_reaches_assay(tmp_path, monkeypatch):
    """Assay pairs `--carry-over` with `--carry-over-reason` (B530).

    Run-Gate used to refuse the pair itself; it now only resolves the path and
    proves it is visible, so the flag reaches the judge and Assay refuses it
    there, in its own words."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--", "--carry-over", str(store)]) == 0
    [call] = _judge_runs(calls)
    assert call.endswith(f"--carry-over {store}"), call


def test_an_absent_candidate_file_reaches_assay(tmp_path, monkeypatch):
    """Assay opens the candidate file; Run-Gate only resolves its path."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    ids = repo / ".run-gate" / "not-there.txt"
    assert not ids.exists()
    assert run_gate.main(["r2", "--", "--only-candidates", str(ids)]) == 0
    [call] = _judge_runs(calls)
    assert call.endswith(f"--only-candidates {ids}"), call


def test_rejudge_outcome_without_rejudge_reaches_the_judge_argv(
        tmp_path, monkeypatch):
    """The forwarded form: Run-Gate no longer pairs the bucket with ids."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--", "--merge-state", str(store),
                          "--rejudge-outcome", "killed"]) == 0
    [call] = _judge_runs(calls)
    assert f"--merge-state {store} --rejudge-outcome killed" in call, call


def test_the_own_rejudge_outcome_flag_reaches_the_judge_argv(
        tmp_path, monkeypatch):
    """Review M4: the OWN flag form the deleted row used to cover.

    `run-gate r2 --rejudge-outcome killed` has no `--` at all, so the flag
    travels through `_selective_assay` rather than the opaque forwarded
    path. The old `requires at least one --rejudge ID` refusal is gone (Assay
    documents the bucket as a union with the ids), and what has to hold now is
    that the own flag still reaches the judge's argv at all."""
    repo, _proj, calls = _main_project(tmp_path, monkeypatch)
    store = repo / ".run-gate" / "store"
    store.mkdir(parents=True, exist_ok=True)
    assert run_gate.main(["r2", "--rejudge-outcome", "killed"]) == 0
    [call] = _judge_runs(calls)
    assert call.endswith("--rejudge-outcome killed"), call
    assert call.count("--rejudge-outcome") == 1, call

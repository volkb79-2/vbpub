"""RG-296: drive the real CLI entry against fake Git projects."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

RUN_GATE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RUN_GATE_DIR.parent
RUN_GATE_SOURCE = RUN_GATE_DIR / "run_gate.py"

_spec = importlib.util.spec_from_file_location("run_gate_lanes_check_under_test", RUN_GATE_SOURCE)
run_gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run_gate
_spec.loader.exec_module(run_gate)

_OMIT = object()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "maintenance.auto", "false")
    _git(repo, "config", "user.email", "lane-check@example.invalid")
    _git(repo, "config", "user.name", "lane-check")
    return repo


def _project(repo: Path, name: str, gate_config: str, assay_config: str | object = _OMIT) -> Path:
    project = repo / name
    project.mkdir()
    (project / "run-gate.toml").write_text(gate_config, encoding="utf-8")
    if assay_config is not _OMIT:
        (project / "assay.toml").write_text(str(assay_config), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"fixture {name}")
    return project


def _gate_config(outer_budget: str, *, lane_name: str = "outer", shared: bool = False) -> str:
    parts = ["schema_version = 1", "", "[environments.tester]", 'mode = "ephemeral"',
             'image = "tester-unified:local"', "", f"[lanes.{lane_name}]",
             'kind = "assay"', 'environment = "tester"', 'assay_lane = "inner"',
             f'budget = "{outer_budget}"']
    if shared:
        parts += ["", f"[lanes.{lane_name}.resources]", 'shared = ["service"]']
    return "\n".join(parts) + "\n"


def _assay_config(inner_budget: str | None = "45m", *, canary: str | None = None,
                  mutation: bool = False, lane_name: str = "inner") -> str:
    parts = ["schema_version = 2", "", f"[lanes.{lane_name}]"]
    if inner_budget is not None: parts.append(f'budget = "{inner_budget}"')
    if mutation: parts += ["", f"[lanes.{lane_name}.judge.mutation]", "jobs_max = 2"]
    if canary is not None: parts += ["", f"[lanes.{lane_name}.judge.canary]",
                                     f'budget_per_attempt = "{canary}"']
    return "\n".join(parts) + "\n"


def _run(monkeypatch, capsys, cwd: Path, *argv: str) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    code = run_gate.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _one_project(tmp_path: Path, gate_config: str, assay_config: str | object = _OMIT) -> tuple[Path, Path]:
    repo = _repo(tmp_path)
    project = _project(repo, "project", gate_config, assay_config)
    return repo, project


def _check_one(tmp_path, monkeypatch, capsys, gate, assay=_OMIT, *args):
    _repo_dir, project = _one_project(tmp_path, gate, assay)
    return _run(monkeypatch, capsys, project, "lanes", "--check", *args)


def test_outer_budget_below_inner_fails_with_or_without_strict(tmp_path, monkeypatch, capsys):
    _repo_dir, project = _one_project(tmp_path, _gate_config("44m"),
                                      _assay_config("45m"))
    for argv in (("lanes", "--check"),
                 ("lanes", "--check", "--strict")):
        code, out, _err = _run(monkeypatch, capsys, project, *argv)
        assert code == 1
        assert "FAIL project:outer budget " in out
        assert "44m is less than assay lane 'inner' budget 45m" in out
        assert "checked 1 projects, 1 lanes, 1 failures, 0 warnings\n" in out


@pytest.mark.parametrize(("outer", "strict", "code"), (
    ("45m", False, 0), ("49m", False, 0), ("49m", True, 1)))
def test_budget_margin_warns_unless_strict(tmp_path, monkeypatch, capsys, outer, strict, code):
    actual, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config(outer),
        _assay_config("45m"), *(("--strict",) if strict else ()))
    marker = "FAIL" if strict else "WARN"
    failures, warnings = ((1, 0) if strict else (0, 1))
    assert actual == code
    assert f"{marker} project:outer budget-margin " in out
    assert f"outer budget {outer} leaves" in out
    assert (f"checked 1 projects, 1 lanes, {failures} failures, "
            f"{warnings} warnings\n" in out)


def test_five_minute_margin_is_clean(tmp_path, monkeypatch, capsys):
    code, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("45m"))
    assert code == 0 and "FAIL " not in out and "WARN " not in out
    assert "checked 1 projects, 1 lanes, 0 failures, 0 warnings\n" in out


@pytest.mark.parametrize(("canary", "code"), (("46m", 1), ("45m", 0)))
def test_canary_attempt_is_bounded_by_inner_budget(tmp_path, monkeypatch, capsys, canary, code):
    actual, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("45m", canary=canary))
    assert actual == code
    if code:
        assert "FAIL project:outer canary" in out
        assert "exceeds assay lane 'inner' budget 45m" in out
    else:
        assert "canary" not in out


def test_unbounded_inner_budget_skips_budget_and_canary_comparisons(
        tmp_path, monkeypatch, capsys):
    code, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("unbounded", canary="10m"))
    assert code == 0 and "FAIL " not in out and "WARN " not in out
    assert "checked 1 projects, 1 lanes, 0 failures, 0 warnings\n" in out


@pytest.mark.parametrize(("strict", "code", "failures"), (
    (False, 0, 0), (True, 1, 1)))
def test_unreadable_inner_budget_is_one_warning(
        tmp_path, monkeypatch, capsys, strict, code, failures):
    actual, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("1h30m", canary="1m30s"),
        *(("--strict",) if strict else ()))
    assert actual == code
    assert out.count("budget-unreadable") == 1
    assert "comparable budget grammar" in out
    assert "project:outer canary" not in out
    assert (f"checked 1 projects, 1 lanes, {failures} failures, "
            f"{1 - failures} warnings\n" in out)


def test_unreadable_canary_attempt_is_a_canary_failure(
        tmp_path, monkeypatch, capsys):
    code, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("45m", canary="auto"))
    assert code == 1
    assert "FAIL project:outer canary " in out
    assert "budget_per_attempt" in out


@pytest.mark.parametrize(("gate", "assay"), (
    (_gate_config("50m").replace('budget = "50m"\n', ""), _assay_config("45m")),
    (_gate_config("50m"), _assay_config(None)),
))
def test_budget_rule_needs_both_declared_budgets(tmp_path, monkeypatch, capsys, gate, assay):
    code, out, _err = _check_one(tmp_path, monkeypatch, capsys, gate, assay)
    assert code == 0 and "FAIL " not in out and "WARN " not in out
    assert "checked 1 projects, 1 lanes, 0 failures, 0 warnings\n" in out


@pytest.mark.parametrize(("assay", "detail"), (
    (_assay_config("45m", lane_name="other"), "'inner' is not declared"),
    (_OMIT, "assay.toml does not exist"),
))
def test_assay_lane_must_exist_in_the_project_manifest(tmp_path, monkeypatch, capsys, assay, detail):
    code, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"), assay)
    assert code == 1
    assert "FAIL project:outer assay-lane " in out
    assert detail in out


@pytest.mark.parametrize(("shared", "mutation", "expected_code"), (
    (False, True, 0), (True, True, 1), (True, False, 0)))
def test_shard_rule_only_applies_to_native_r2(
        tmp_path, monkeypatch, capsys, shared, mutation, expected_code):
    actual, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m", shared=shared),
        _assay_config("45m", mutation=mutation), "--json")
    findings = json.loads(out)["projects"][0]["findings"]
    assert actual == expected_code
    if mutation and shared:
        assert [(item["lane"], item["rule"], item["level"])
                for item in findings] == [("outer", "shard", "fail")]
        assert "declares resources.shared" in findings[0]["detail"]
        assert "serializes every run" in findings[0]["detail"]
    else:
        assert findings == []


@pytest.mark.parametrize("assay", (
    "schema_version = 2\nlanes = 1\n",
    'schema_version = 2\n[lanes.inner]\nbudget = "45m"\njudge = "x"\n',
    'schema_version = 2\n[lanes.inner]\nbudget = "45m"\n'
    '[lanes.inner.judge]\ncanary = "x"\n',
))
def test_malformed_assay_shape_is_a_load_finding(
        tmp_path, monkeypatch, capsys, assay):
    code, out, err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"), assay)
    assert code == 1
    assert "FAIL project:assay.toml load" in out
    assert "Traceback" not in out + err


def test_all_continues_after_malformed_shapes_and_parse_errors(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    _project(repo, "00-lanes-shape", _gate_config("50m"),
             "schema_version = 2\nlanes = 1\n")
    _project(repo, "01-judge-shape", _gate_config("50m"),
             'schema_version = 2\n[lanes.inner]\nbudget = "45m"\njudge = "x"\n')
    _project(repo, "02-canary-shape", _gate_config("50m"),
             'schema_version = 2\n[lanes.inner]\nbudget = "45m"\n'
             '[lanes.inner.judge]\ncanary = "x"\n')
    _project(repo, "03-parse", _gate_config("50m"), "[lanes.inner\n")
    _project(repo, "04-later", _gate_config("44m"), _assay_config("45m"))
    code, out, _err = _run(
        monkeypatch, capsys, repo, "lanes", "--check", "--all")
    assert code == 1
    for name in ("00-lanes-shape", "01-judge-shape", "02-canary-shape",
                 "03-parse"):
        assert f"FAIL {name}:assay.toml load" in out
    assert "FAIL 04-later:outer budget " in out
    assert "checked 5 projects, 4 lanes, 5 failures, 0 warnings\n" in out


def test_shard_instance_and_merge_refusals_are_findings(tmp_path, monkeypatch, capsys):
    def fake_flex(lane, lane_name, args):
        if args.merge_shards is not None:
            raise run_gate.GateError("merge refused")
        return lane, "wrong.shard"

    monkeypatch.setattr(run_gate, "attach_r2_flex", fake_flex)
    code, out, _err = _check_one(
        tmp_path, monkeypatch, capsys, _gate_config("50m"),
        _assay_config("45m", mutation=True), "--json")
    findings = json.loads(out)["projects"][0]["findings"]
    assert code == 1
    assert [item["rule"] for item in findings] == ["shard", "shard"]
    assert "produced instance 'wrong.shard', not 'outer.shard0of2'" \
        in findings[0]["detail"]
    assert findings[1]["detail"] == "merge refused"


def test_all_skips_untracked_and_continues_after_load_failure(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    clean = """\
schema_version = 1
[environments.tester]
mode = "ephemeral"
image = "tester-unified:local"
[lanes.clean]
kind = "command"
environment = "tester"
argv = ["true"]
"""
    _project(repo, "00-clean", clean)
    _project(repo, "01-load-error", "schema_version = 1\n[lanes.bad\n")
    _project(repo, "02-broken", _gate_config("44m"), _assay_config("45m"))
    (repo / "03-untracked").mkdir()
    (repo / "03-untracked" / "run-gate.toml").write_text("[lanes.bad\n", encoding="utf-8")
    code, out, _err = _run(
        monkeypatch, capsys, repo, "lanes", "--check", "--all")
    assert code == 1
    assert "FAIL 00-clean:" not in out
    assert "FAIL 01-load-error:run-gate.toml load" in out
    assert "FAIL 02-broken:outer budget " in out
    assert "03-untracked" not in out
    assert "checked 3 projects, 2 lanes, 2 failures, 0 warnings\n" in out


def test_json_shape_has_level_on_each_finding(tmp_path, monkeypatch, capsys):
    _repo_dir, project = _one_project(
        tmp_path, _gate_config("49m"), _assay_config("45m"))
    code, out, err = _run(
        monkeypatch, capsys, project, "lanes", "--check", "--json")
    assert code == 0 and err == ""
    assert json.loads(out) == {"projects": [{
        "project": "project", "lanes_checked": 1, "findings": [{
            "lane": "outer", "rule": "budget-margin", "level": "warn",
            "detail": "outer budget 49m leaves 240s above assay lane 'inner' budget 45m; require 5m"}]}]}


def test_project_option_checks_a_non_current_project(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    project = _project(
        repo, "chosen", _gate_config("44m"), _assay_config("45m"))
    code, out, _err = _run(
        monkeypatch, capsys, repo, "lanes", "--check",
        "--project", str(project))
    assert code == 1
    assert "FAIL chosen:outer budget " in out
    assert "checked 1 projects, 1 lanes, 1 failures, 0 warnings\n" in out


def test_lanes_check_has_a_closed_option_surface(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    project = _project(repo, "project", _gate_config("50m"))
    code, _out, err = _run(
        monkeypatch, capsys, project, "lanes", "--check",
        "--worktree", "elsewhere")
    assert code == 2 and "refusing --worktree" in err
    code, _out, err = _run(
        monkeypatch, capsys, project, "lanes", "--check",
        "--project", str(project), "--all")
    assert code == 2 and "either --project DIR or --all" in err
    code, _out, err = _run(monkeypatch, capsys, project, "doctor", "--all")
    assert code == 2 and "honored by `run-gate lanes --check` only" in err
    for argv in (("doctor", "--strict"),
                 ("doctor", "--project", str(project))):
        code, _out, err = _run(monkeypatch, capsys, project, *argv)
        assert code == 2
        assert "honored by `run-gate lanes --check` only" in err
    code, _out, err = _run(
        monkeypatch, capsys, project, "lanes", "--project", str(project))
    assert code == 2 and "requires --check" in err
    for argv in (("lanes", "--check", "extra"), ("lanes", "--check", ""),
                 ("lanes", "--check", "--", "extra")):
        code, _out, err = _run(monkeypatch, capsys, project, *argv)
        assert code == 2 and "unexpected positional argument(s)" in err
        assert "--target" not in err and "--lane-args" not in err
    code, _out, err = _run(monkeypatch, capsys, project, "doctor", "--json")
    assert code == 2 and "`lanes --check`" in err


def test_a_lane_named_lanes_is_refused_at_load(tmp_path, monkeypatch, capsys):
    config = """\
schema_version = 1
[environments.tester]
mode = "ephemeral"
image = "tester-unified:local"
[lanes.lanes]
kind = "command"
environment = "tester"
argv = ["true"]
"""
    _repo_dir, project = _one_project(tmp_path, config)
    code, _out, err = _run(monkeypatch, capsys, project, "--list")
    assert code == 2 and "lane name 'lanes' is reserved" in err


def test_lanes_without_check_is_a_closed_usage_refusal(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    code, out, err = _run(monkeypatch, capsys, repo, "lanes")
    assert code == 2
    assert "`run-gate lanes` requires --check" in err
    assert "lane listing is `run-gate --list`" in err
    assert "run-gate.toml; * = inherited" not in out + err


def test_current_project_and_single_config_refusals(tmp_path, monkeypatch, capsys):
    repo, project = _one_project(
        tmp_path, _gate_config("50m"), "[lanes.inner\n")
    code, _out, err = _run(
        monkeypatch, capsys, project, "lanes", "--check")
    assert code == 2 and "cannot read" in err
    code, _out, err = _run(monkeypatch, capsys, repo, "lanes", "--check")
    assert code == 2 and "no run-gate.toml found" in err


def test_real_repository_all_has_only_budget_margin_warnings(tmp_path, monkeypatch, capsys):
    probe = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
    if probe.returncode:
        raise pytest.skip.Exception(
            "Git metadata is unavailable in this lane container")

    code, out, err = _run(
        monkeypatch, capsys, REPO_ROOT, "lanes", "--check", "--all", "--json")
    assert code == 0, err
    findings = [finding for project in json.loads(out)["projects"]
                for finding in project["findings"]]
    assert all(item["level"] == "warn" and item["rule"] == "budget-margin"
               for item in findings)

    strict_code, out, err = _run(
        monkeypatch, capsys, REPO_ROOT,
        "lanes", "--check", "--all", "--strict", "--json")
    assert strict_code == (1 if findings else 0), err
    strict = [finding for project in json.loads(out)["projects"]
              for finding in project["findings"]]
    assert all(item["level"] == "fail" and item["rule"] == "budget-margin"
               for item in strict)

"""W2-PKG2: the nine delegate registries (cli-extended adoption + redesign B8-B12).

Each test pins one observable behaviour that a planted revert breaks:
the shared registry factory, the handler/tester-gate renames, run-time env
fallbacks, resolve's selector-driven shape and config-free mode, get-py's
multi-project refusal, standards --json, and the narrowed exception sites.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import (
    cli, cli_support, delegate_targets, getpy, handlers,
    resolve, runner, scaffold, standards, tester_gate, tool_deps, versions,
)

getpy_module = getpy

# --- the shared registry factory (D1) -----------------------------------------


@pytest.mark.parametrize(
    "builder",
    [
        scaffold.init_cli, versions.versions_cli, runner.runner_cli, handlers.handlers_cli,
        tester_gate.tester_gate_cli, resolve.resolve_cli, getpy.getpy_cli,
        standards.standards_cli, tool_deps.tool_deps_cli,
    ],
    ids=lambda b: b.__module__.rsplit(".", 1)[-1],
)
def test_every_delegate_builder_takes_its_policy_from_the_shared_factory(monkeypatch, builder):
    """A builder that constructs its own CliRegistry keeps "raise" here."""
    monkeypatch.setattr(cli_support, "UNEXPECTED_EXCEPTIONS_POLICY", "report")
    assert builder().unexpected_exceptions == "report"


def test_delegate_builders_carry_the_shared_log_prefix_option(capsys):
    for main in (handlers.main, tester_gate.main, resolve.resolve_main, standards.standards_main):
        assert main(["--help"]) == 0
        assert "--log-prefix-time-short" in capsys.readouterr().out


# --- handler: python -m entry, bake target, exit 3 without a distribution ------


def _missing_distribution(monkeypatch):
    def missing(distribution):
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr("cli_extended.identity.installed_version", missing)


def test_handler_module_entry_exits_3_without_a_traceback_when_cmru_is_not_installed(
    monkeypatch, capsys,
):
    _missing_distribution(monkeypatch)
    assert handlers.main(["--help"]) == 3
    err = capsys.readouterr().err
    assert "cmru is not installed as a distribution; install the wheel (see README)" in err
    assert "Traceback" not in err


def test_python_dash_m_handlers_and_cmru_handler_share_one_builder(tmp_path, capsys):
    """Both entries print the same help: they are the same ``handlers_cli``."""
    src = Path(__file__).resolve().parents[1] / "src"
    root = src.parents[1]
    env = {
        **os.environ, "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(
            [str(src), str(root / "libraries" / "cli-extended" / "src"),
             str(root / "libraries" / "worktree" / "src")]),
    }
    module = subprocess.run(
        [sys.executable, "-m", "cmru.handlers", "--help"],
        capture_output=True, text=True, check=False, env=env, cwd=tmp_path,
    )
    assert module.returncode == 0, module.stderr
    assert cli.main(["handler", "--help"]) == 0
    assert module.stdout == capsys.readouterr().out


def test_handler_entry_runs_the_registered_builder(monkeypatch):
    seen = []
    monkeypatch.setattr(
        handlers, "handlers_cli",
        lambda: SimpleNamespace(run=lambda argv: seen.append(argv) or 77),
    )
    assert handlers.main(["wheel-build"]) == 77
    assert seen == [["wheel-build"]]


def test_oci_handlers_take_bake_target_and_refuse_the_old_spelling(tmp_path, capsys):
    base = ["oci-image-build", "--cwd", str(tmp_path), "--bake-file", "b.hcl"]
    assert handlers.main([*base, "--bake-target", "image", "--dry-run"]) == 0
    assert "'bake_target': 'image'" in capsys.readouterr().out
    assert handlers.main([*base, "--target", "image", "--dry-run"]) == 2
    assert "--bake-target" in capsys.readouterr().err


def test_mutating_handlers_have_the_library_dry_run_and_validators_do_not(capsys):
    assert handlers.main(["wheel-validate", "--prefix", "x", "--dry-run"]) == 2
    assert "unrecognized arguments: --dry-run" in capsys.readouterr().err
    assert handlers.main(["bundle-manifest", "--help"]) == 0
    assert "--dry-run" in capsys.readouterr().out


# --- tester-gate: renamed flags, run-time env fallback, exit 3 -----------------

_ENV = {
    "CMRU_TESTER_UNIFIED_IMAGE": "tester:1", "CMRU_TESTER_MEMORY": "7g",
    "CMRU_TESTER_MEMORY_SWAP": "8g", "CMRU_TESTER_CPUS": "2", "CMRU_TESTER_PIDS_LIMIT": "99",
    "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "b" * 64,
    "CMRU_TESTER_CGROUP_PARENT": "gates.slice",
}
_OPTIONAL_ENV = (
    "CMRU_TESTER_DEVICE_READ_IOPS", "CMRU_TESTER_DEVICE_WRITE_IOPS",
    "CMRU_TESTER_DEVICE_READ_BPS", "CMRU_TESTER_DEVICE_WRITE_BPS",
    "CMRU_TESTER_CGROUP_FORWARD_VAR", "CMRU_TESTER_CGROUP_FORWARD_GATES_VAR",
    "CMRU_TESTER_DIND_IMAGE", "CMRU_TESTER_DIND_MEMORY", "CMRU_TESTER_DIND_CPUS",
    "CMRU_TESTER_DIND_PIDS_LIMIT",
)


def _clean_env(monkeypatch):
    for name in (*_ENV, *_OPTIONAL_ENV):
        monkeypatch.delenv(name, raising=False)


def _dry_run_argv(monkeypatch, tmp_path, capsys, *extra):
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_a: (tmp_path, "."))
    assert tester_gate.main(["--cwd", ".", "--dry-run", *extra, "--", "true"]) == 0
    line = [row for row in capsys.readouterr().out.splitlines() if row.startswith("[DRY RUN] docker")]
    assert len(line) == 1
    return line[0]


def test_tester_gate_reads_every_env_fallback_at_run_time_not_at_parser_build(
    monkeypatch, tmp_path, capsys,
):
    """CLI-17: the parser is built BEFORE the environment exists; the values
    set afterwards must still be honoured (they used to freeze at build time)."""
    _clean_env(monkeypatch)
    registered = tester_gate.tester_gate_cli()
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CMRU_TESTER_DEVICE_READ_IOPS", "/dev/vda:10")
    monkeypatch.setenv("CMRU_TESTER_DEVICE_WRITE_BPS", "/dev/vda:20")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_FORWARD_VAR", "bg.slice")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_FORWARD_GATES_VAR", "gt.slice")
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_a: (tmp_path, "."))

    assert registered.run(argv=["--cwd", ".", "--dry-run", "--", "true"]) == 0
    line = capsys.readouterr().out
    for fragment in (
        "--memory 7g", "--memory-swap 8g", "--cpus 2", "--pids-limit 99",
        "--cgroup-parent=gates.slice", "--device-read-iops /dev/vda:10",
        "--device-write-bps /dev/vda:20", "CGROUP_PARENT_DEV_BACKGROUND=bg.slice",
        "CGROUP_PARENT_DEV_GATES=gt.slice",
    ):
        assert fragment in line


def test_tester_gate_explicit_options_beat_the_environment_and_use_the_new_names(
    monkeypatch, tmp_path, capsys,
):
    _clean_env(monkeypatch)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CMRU_TESTER_CGROUP_FORWARD_VAR", "env-bg.slice")
    argv = _dry_run_argv(
        monkeypatch, tmp_path, capsys, "--memory", "1g",
        "--forward-background-slice", "opt-bg.slice", "--forward-gates-slice", "opt-gt.slice",
    )
    assert "--memory 1g" in argv
    assert "CGROUP_PARENT_DEV_BACKGROUND=opt-bg.slice" in argv
    assert "CGROUP_PARENT_DEV_GATES=opt-gt.slice" in argv


@pytest.mark.parametrize("old", ["--forward-cgroup-parent-var", "--forward-cgroup-parent-gates-var"])
def test_tester_gate_old_forward_flag_names_are_gone(old, capsys):
    assert tester_gate.main(["--cwd", ".", old, "x", "--", "true"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_every_tester_gate_env_fallback_is_named_as_a_default_in_its_help(capsys):
    assert tester_gate.main(["--help"]) == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for name in (*_ENV, *_OPTIONAL_ENV):
        assert f"(default: ${name}" in help_text, name


def test_tester_gate_missing_configuration_exits_3(monkeypatch, capsys):
    _clean_env(monkeypatch)
    assert tester_gate.main(["--cwd", ".", "--", "true"]) == 3
    err = capsys.readouterr().err
    assert "missing required configuration: " in err
    assert "CMRU_TESTER_CGROUP_PARENT" in err


# --- init: library controls ----------------------------------------------------


def test_init_has_the_library_dry_run_and_yes_controls(capsys):
    assert scaffold.init_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out and "--yes" in out


# --- the one delegate target resolver (B13) ------------------------------------


def _projects(*names):
    return {name: SimpleNamespace(prefix=f"{name}-v") for name in names}


def test_resolve_target_uses_the_standalone_project_as_context(tmp_path):
    configs = _projects("solo")
    assert delegate_targets.current_project(tmp_path / "cmru.toml", configs) == "solo"
    assert delegate_targets.resolve_target(None, tmp_path / "cmru.toml", configs, ["solo"]) == ["solo"]


def test_resolve_target_reads_the_invocation_context_at_the_estate_root(monkeypatch, tmp_path):
    configs = _projects("alpha", "beta")
    for project_name, expected in (("beta", ["beta"]), (None, ["alpha", "beta"])):
        monkeypatch.setattr(
            "cmru.config.resolve_invocation_context",
            lambda *_a, _p=project_name, **_k: SimpleNamespace(project_name=_p, scope="estate"),
        )
        assert delegate_targets.resolve_target(
            None, tmp_path / "cmru.orchestration.toml", configs, ["alpha", "beta"],
        ) == expected
    # an explicit target never consults the invocation context
    monkeypatch.setattr(
        "cmru.config.resolve_invocation_context",
        lambda *_a, **_k: pytest.fail("explicit target read the invocation context"),
    )
    assert delegate_targets.resolve_target(
        ("beta", "alpha"), None, configs, ["alpha", "beta"]) == ["alpha", "beta"]


def test_resolve_target_renders_selector_problems_as_usage_failures(tmp_path):
    from cli_extended import CliFailure

    with pytest.raises(CliFailure) as error:
        delegate_targets.resolve_target(("ghost",), tmp_path / "cmru.toml", _projects("a"), ["a"])
    assert error.value.exit_code == 2 and error.value.show_help
    assert "unknown project(s): ghost" in error.value.message


# --- resolve: shape follows the selector syntax; config-free mode --------------


def _loaded(tmp_path, names):
    github = SimpleNamespace(owner="octo", repo="vbpub", token="")
    projects = {
        name: SimpleNamespace(prefix=f"{name}-v", github_token="", installer=None) for name in names
    }
    return (tmp_path, projects, list(names), [], [], "", {}, None, github, None)


def _resolve_env(monkeypatch, tmp_path, names, *, context=None):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _a: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda _p: _loaded(tmp_path, names))
    monkeypatch.setattr(
        "cmru.config.resolve_invocation_context",
        lambda *_a, **_k: SimpleNamespace(project_name=context, scope="estate"),
    )
    calls = []

    def fake_resolve(host, prefix, **kwargs):
        calls.append((prefix, kwargs.get("gh_releases_url")))
        return {
            "version": "1.0.0", "tag": f"{prefix}1.0.0", "asset": "a", "sha256": None,
            "url": f"https://x/{prefix}",
        }

    monkeypatch.setattr(resolve, "resolve", fake_resolve)
    return calls


@pytest.mark.parametrize(
    ("argv", "names", "context", "shape"),
    [
        (["alpha"], ["alpha", "beta"], None, "object"),
        (["alpha,beta"], ["alpha", "beta"], None, "map"),
        (["all"], ["alpha", "beta"], None, "map"),
        # CLI-13: the count never decides the shape, the selector does.
        (["all"], ["alpha"], None, "map"),
        (["alpha,alpha2"], ["alpha", "alpha2"], None, "map"),
        ([], ["alpha"], None, "map"),           # estate root, omitted: every orchestrated project
        ([], ["alpha", "beta"], "alpha", "object"),  # omitted inside a project: the current one
    ],
)
def test_resolve_json_shape_follows_the_selector_syntax(
    monkeypatch, tmp_path, capsys, argv, names, context, shape,
):
    _resolve_env(monkeypatch, tmp_path, names, context=context)
    assert resolve.resolve_main(argv) == 0
    document = json.loads(capsys.readouterr().out)
    if shape == "object":
        assert document["version"] == "1.0.0" and "url" in document
    else:
        expected = set(names) if argv in (["all"], []) else set(argv[0].split(","))
        assert set(document) == expected
        assert all(entry["version"] == "1.0.0" for entry in document.values())


def test_resolve_config_free_mode_reads_no_configuration_and_uses_the_given_prefix(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr("cmru.cli.load_config", lambda _p: pytest.fail("config-free read config"))
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _a: pytest.fail("config-free read config"))
    seen = []

    class Host:
        def __init__(self, owner, repo, token):
            seen.append((owner, repo, token))

    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", Host)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    calls = []

    def fake_resolve(host, prefix, **kwargs):
        calls.append((prefix, kwargs["gh_releases_url"]))
        return {"version": "2.0.0", "tag": "ciu-v2.0.0", "asset": "w", "sha256": "d" * 64, "url": "https://u"}

    monkeypatch.setattr(resolve, "resolve", fake_resolve)
    assert resolve.resolve_main(["--repo", "octo/vbpub", "--prefix", "ciu-v", "--format", "env"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "CIU_VERSION=2.0.0" in out and f"CIU_SHA256={'d' * 64}" in out
    assert calls == [("ciu-v", "https://github.com/octo/vbpub/releases")]
    assert seen == [("octo", "vbpub", "from-env")]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--repo", "o/r", "--prefix", "p", "--config", "x.toml"], "--repo and --config cannot be used together"),
        (["--repo", "o/r"], "--repo requires --prefix"),
        (["--prefix", "p"], "--prefix requires --repo"),
        (["alpha", "--repo", "o/r", "--prefix", "p"], "cannot be combined with a project target"),
        (["--repo", "not-a-repo", "--prefix", "p"], "must look like OWNER/REPO"),
    ],
)
def test_resolve_config_free_mode_refuses_conflicting_or_malformed_input(argv, message, capsys):
    assert resolve.resolve_main(argv) == 2
    assert message in capsys.readouterr().err


def test_resolve_config_free_mode_reports_a_missing_release_and_a_failed_lookup(monkeypatch, capsys):
    monkeypatch.setattr(resolve, "resolve", lambda *_a, **_k: None)
    assert resolve.resolve_main(["--repo", "o/r", "--prefix", "p-v"]) == 1
    assert "No releases found in o/r (prefix 'p-v')" in capsys.readouterr().err

    def failing(*_a, **_k):
        raise RuntimeError("bad digest")

    monkeypatch.setattr(resolve, "resolve", failing)
    assert resolve.resolve_main(["--repo", "o/r", "--prefix", "p-v"]) == 1
    assert "cannot resolve prefix 'p-v': bad digest" in capsys.readouterr().err


# --- resolve.resolve_via_latest_json: only network/body failures are swallowed --


class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._payload


def _latest(monkeypatch, behaviour):
    monkeypatch.setattr("urllib.request.urlopen", behaviour)
    return resolve.resolve_via_latest_json("https://github.com/o/r/releases", "demo-v")


def test_latest_json_pointer_is_read_and_a_valid_body_returned(monkeypatch):
    body = json.dumps({"version": "1.2.3", "tag": "demo-v1.2.3", "url": "https://u", "sha256": "a" * 64})
    result = _latest(monkeypatch, lambda *_a, **_k: _Body(body.encode()))
    assert result["version"] == "1.2.3" and result["url"] == "https://u"


@pytest.mark.parametrize(
    "behaviour",
    [
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("slow")),
        lambda *_a, **_k: _Body(b"not json"),
        lambda *_a, **_k: _Body(b"\xff\xfe"),
        lambda *_a, **_k: _Body(b"[1, 2]"),
        lambda *_a, **_k: _Body(b'{"version": "1"}'),
    ],
    ids=["urlerror", "timeout", "bad-json", "bad-utf8", "non-object", "missing-url"],
)
def test_latest_json_failures_fall_back_to_the_release_scan(monkeypatch, behaviour):
    assert _latest(monkeypatch, behaviour) is None


def test_latest_json_does_not_disguise_a_programming_error(monkeypatch):
    def broken(*_a, **_k):
        raise TypeError("bug in the opener")

    with pytest.raises(TypeError, match="bug in the opener"):
        _latest(monkeypatch, broken)


# --- get-py -------------------------------------------------------------------


def _getpy_env(monkeypatch, tmp_path, names):
    config = tmp_path / "cmru.orchestration.toml"
    projects = {name: SimpleNamespace(name=name) for name in names}
    monkeypatch.setattr(cli, "_resolve_config", lambda _p: config)
    monkeypatch.setattr(cli, "load_config", lambda _p: (tmp_path, projects, list(names)))
    monkeypatch.setattr(getpy_module, "render_from_config", lambda name, _p: f"# {name}\n")


def test_get_py_refuses_multi_project_stdout_and_writes_one_project_to_stdout(
    monkeypatch, tmp_path, capsys,
):
    _getpy_env(monkeypatch, tmp_path, ("alpha", "beta"))
    assert getpy.getpy_main(["all"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing half-written to a redirected get.py
    assert "2 projects selected (alpha, beta)" in captured.err
    assert getpy.getpy_main(["beta"]) == 0
    assert capsys.readouterr().out == "# beta\n"


def test_get_py_dry_run_is_a_declared_constraint_on_a_write_destination(
    monkeypatch, tmp_path, capsys,
):
    _getpy_env(monkeypatch, tmp_path, ("alpha",))
    assert getpy.getpy_main(["alpha", "--dry-run"]) == 2
    assert "--dry-run requires --output or --output-dir" in capsys.readouterr().err
    destination = tmp_path / "out"
    assert getpy.getpy_main(["alpha", "--dry-run", "--output-dir", str(destination)]) == 0
    assert not destination.exists()
    assert getpy.getpy_main(["alpha", "--output-dir", str(destination)]) == 0
    assert (destination / "alpha-get.py").read_text(encoding="utf-8") == "# alpha\n"


# --- standards: --json, exit 4, constraints ------------------------------------


def test_standards_json_prints_one_document_and_exit_4_names_the_issues(tmp_path, capsys):
    from tests.test_standards import _config

    config, _project = _config(tmp_path)
    assert standards.standards_main(["demo", "--config", str(config), "--json"]) == 4
    captured = capsys.readouterr()
    report = json.loads(captured.out)  # stdout is exactly one JSON document
    assert report["schema_version"] == 1 and report["conforms"] is False
    (entry,) = report["projects"]
    assert entry["name"] == "demo" and entry["conforms"] is False
    assert any("template revision" in problem for problem in entry["problems"])
    assert "[WARN] demo:" in captured.err and "issue(s)" in captured.err


def test_standards_json_after_update_conforms_with_exit_0(tmp_path, capsys):
    from tests.test_standards import _config

    config, _project = _config(tmp_path)
    assert standards.standards_main(["demo", "--config", str(config), "--update", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["conforms"] is True and report["projects"][0]["problems"] == []
    assert "Updated CMRU-owned template revision" in captured.err
    assert "Updated CMRU" not in captured.out


def test_standards_dry_run_update_previews_on_stderr_in_json_mode(tmp_path, capsys):
    from tests.test_standards import _config

    config, project = _config(tmp_path)
    before = project.read_text(encoding="utf-8")
    assert standards.standards_main(
        ["demo", "--config", str(config), "--update", "--dry-run", "--json"]) == 4
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "(planned)" in captured.err and "Marker updates were previewed" in captured.err
    assert project.read_text(encoding="utf-8") == before


# --- versions: library dry-run only where a write exists -----------------------


def test_versions_check_has_no_dry_run_but_init_and_resolve_do(capsys):
    assert versions.main(["check", "--dry-run"]) == 2
    assert "unrecognized arguments: --dry-run" in capsys.readouterr().err
    for verb in ("init", "resolve"):
        assert versions.main([verb, "--help"]) == 0
        assert "--dry-run" in capsys.readouterr().out


# --- runner (compatibility export until PKG-1 removes the mount) ---------------


def test_run_step_delegate_still_resolves_one_project_through_the_shared_helper(
    monkeypatch, tmp_path, capsys,
):
    project = SimpleNamespace(name="demo", project_root=tmp_path / "demo", runner_steps={})
    monkeypatch.setattr(cli, "_resolve_config", lambda _p: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        cli, "load_config", lambda _p: (tmp_path, {"demo": project, "other": project}, ["demo", "other"]),
    )
    assert runner.runner_cli().run(argv=["demo,other", "--step", "build"]) == 2
    assert "run-step requires exactly one project target" in capsys.readouterr().err

"""`ciu deploy` dispatcher contracts without external bootstrap boundaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def _args(**overrides):
    values = {
        "define_root": None,
        "update_cert_permission": False,
        "profile": None,
        "phases": None,
        "dry_run": False,
        "no_preflight": False,
        "ignore_errors": False,
        "strict": False,
        "live": False,
        "graph_format": "mermaid",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _patch_setup(monkeypatch, profile: Profile, selection: list[dict], root: Path):
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _defined: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {"global": True})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)


def test_run_dispatches_non_deploy_actions_and_caches_render_for_check_then_graph(monkeypatch, tmp_path):
    """Explicit actions retain ordering and `--check`/`--graph` share one render."""

    profile = Profile(name="edge", config={"deploy": {}})
    selection = [{"path": "apps/api"}]
    _patch_setup(monkeypatch, profile, selection, tmp_path)
    actions = ["list_phases", "list_profiles", "stop", "clean", "healthcheck", "preflight", "check", "graph"]
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: actions)
    renders: list[tuple[Path, Profile, list[dict]]] = []
    calls: list[tuple[str, object]] = []

    def render(root, resolved_profile, resolved_selection, ciu_context=None):
        renders.append((root, resolved_profile, resolved_selection))
        return {"apps/api": {"api": {}}}

    monkeypatch.setattr(deploy, "render_selected_stacks", render)
    monkeypatch.setattr(deploy, "action_list_phases", lambda config: calls.append(("phases", config)) or 0)
    monkeypatch.setattr(deploy, "action_list_profiles", lambda config: calls.append(("profiles", config)) or 0)
    monkeypatch.setattr(deploy, "action_stop", lambda config: calls.append(("stop", config)) or 0)
    monkeypatch.setattr(deploy, "action_clean", lambda *args, **kwargs: calls.append(("clean", kwargs["ignore_errors"])) or 0)
    monkeypatch.setattr(deploy, "action_healthcheck", lambda *_args: calls.append(("health", None)) or 0)
    monkeypatch.setattr(deploy, "action_healthcheck_preflight", lambda *_args, **kwargs: calls.append(("preflight", kwargs["strict"])) or 0)
    monkeypatch.setattr(deploy, "action_check", lambda *args, **kwargs: calls.append(("check", kwargs["live"])) or 0)
    monkeypatch.setattr(deploy, "action_graph", lambda *args, **kwargs: calls.append(("graph", kwargs["fmt"])) or 0)

    assert deploy._run(_args(live=True), [f"--{action}" for action in actions]) == 0
    assert [name for name, _value in calls] == ["phases", "profiles", "stop", "clean", "health", "preflight", "check", "graph"]
    assert len(renders) == 1
    assert calls[-2:] == [("check", True), ("graph", "mermaid")]


def test_run_expands_comma_profiles_and_defaults_to_deploy_when_no_action(monkeypatch, tmp_path, capsys):
    """Repeated/comma profile input is normalized before the default deploy action."""

    profile = Profile(name="edge,db", config={"deploy": {}})
    _patch_setup(monkeypatch, profile, [], tmp_path)
    received_profiles: list[list[str] | None] = []
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, names: received_profiles.append(names) or profile)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: [])
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {})
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "registry_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "governance_slice_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "action_deploy", lambda *_args, **_kwargs: 0)

    assert deploy._run(_args(profile=["edge, db", "cache", "  "]), []) == 0
    assert received_profiles == [["edge", "db", "cache"]]
    assert "No action specified; defaulting to --deploy" in capsys.readouterr().out


def test_run_info_routes_to_stderr_under_json_output(monkeypatch, tmp_path, capsys):
    """CIU-84's narrower unit proof: `_run`'s own top-level `info()` calls
    ("Active service profile(s)", "No action specified") move to stderr
    the moment `args.json_output` is set — even for actions that never reach
    `action_check` at all, since the invariant this fix establishes is
    "no `_run`-level prose on stdout under --json", not "only when check is
    among the actions" (see the comment on `_run_info` in deploy.py for why
    the broader invariant was chosen). `deploy_needs_preflight` is False
    here (no `deploy` action), so the health-gate `_run_info` call is not
    exercised by this test; it is a separate `_run_info` call site, not a
    branch of the two exercised here.
    """
    profile = Profile(name="edge,db", config={"deploy": {}})
    _patch_setup(monkeypatch, profile, [], tmp_path)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: [])
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {})
    # No explicit action -> `_run` defaults to ["deploy"], which runs the
    # REAL preflight block (`deploy_needs_preflight`) ahead of the action
    # dispatch loop -- `check_preflight` is stubbed alongside its siblings
    # so ITS OWN prose (deliberately NOT gated by the outer --json, since
    # --json is documented as meaningful only "With --check" as the
    # explicit action, not as a deploy-preflight side effect) does not
    # confound this test's exact-equality assertion on stdout below.
    monkeypatch.setattr(deploy, "check_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "registry_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "governance_slice_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "action_deploy", lambda *_args, **_kwargs: 0)

    assert deploy._run(_args(json_output=True), []) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Active service profile(s)" in captured.err
    assert "No action specified; defaulting to --deploy" in captured.err


def test_check_json_stdout_is_exactly_one_json_document(monkeypatch, tmp_path, capsys):
    """CIU-84's own required oracle: `ciu check --json`'s stdout parses as
    ONE JSON document via `json.loads` on the ENTIRE captured stdout — not a
    substring check (`"[INFO]" not in out`), which would miss a stray write
    that is itself valid-looking text but still not part of the document, or
    one that landed AFTER it. `json.loads` fails outright on anything else
    sharing stdout with the document, from any source on the call path —
    `_run`'s own prose (the two known-bad sites this package fixes) or
    anything reachable through the REAL `action_check` this test does not
    mock, run end to end via `deploy._run(["--check", "--json"])`, the exact
    entry point an operator's `ciu check --json | jq` invokes.

    Selection is intentionally EMPTY: nothing to check, so the run reaches
    the document with no ERROR-severity finding, isolating this test to the
    stdout-purity contract rather than any one config's specific validation
    outcome (already covered elsewhere, e.g. test_ciu_deploy_actions.py).
    """
    profile = Profile(name="", config={"deploy": {}})
    _patch_setup(monkeypatch, profile, [], tmp_path)
    # Real action_check reaches `hosts_pkg.load_hosts(repo_root)`, which
    # falls back to `Path.home()/.ciu/hosts.toml` when nothing is found at
    # `repo_root` — stubbed so this test's outcome cannot depend on the
    # invoking machine's own home directory.
    monkeypatch.setattr(deploy.hosts_pkg, "load_hosts", lambda _root: {})

    rc = deploy._run(_args(json_output=True), ["--check", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "[WARN]" not in captured.err
    document = json.loads(captured.out)  # raises if stdout carries anything else
    assert document["operation"] == "config-check"
    assert document["status"] == "pass"

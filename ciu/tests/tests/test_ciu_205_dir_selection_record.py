"""CIU-205 — `ciu up --dir` selection facts, and the opt-in selection record.

(a) The single-stack `ciu up --dir <stack>` path used to call
    ``engine.main_execution`` with no ``ciu_context``, so a template or hook
    reading ``ciu.selected_profiles`` / ``ciu.deployed_stacks`` (S3.12) failed
    there. It now derives them from the checkout's default selection, resolved
    like a plain ``ciu up`` (``[ciu.instance] service_profiles`` first), with
    ``deployed_stacks`` = the one stack.
(b) ``[ciu.selection_record] profiles = [...]`` (opt-in) records which guarded
    profiles the first deploying invocation selected, refuses a later
    conflicting selection before any Docker mutation, and an unrestricted,
    successful ``ciu clean`` removes the record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, deploy, engine, selection_record  # noqa: E402
from tests.test_ciu_render_selection_context import _add_stack, _build_repo  # noqa: E402

PROBE = (
    "\n[ciumeta]\n"
    'selected_profiles = "{{ ciu.selected_profiles | join(\',\') }}"\n'
    'deployed_stacks = "{{ ciu.deployed_stacks | join(\';\') }}"\n'
    'ctx_profiles = "{{ app_config.ctx_profiles }}"\n'
)
HOOK = (
    "def run(config, ctx):\n"
    "    return {'app_config.ctx_profiles': {\n"
    "        'value': ','.join(ctx.selected_profiles or ()) + '|' + ';'.join(ctx.deployed_stacks or ()),\n"
    "        'apply_to_config': True,\n"
    "    }}\n"
)


def _probe_stack(repo_root: Path) -> Path:
    stack = _add_stack(repo_root)
    cfg = stack / "config.toml.j2"
    cfg.write_text(cfg.read_text(encoding="utf-8") + PROBE, encoding="utf-8")
    (stack / "pre_compose_ctx_probe.py").write_text(HOOK, encoding="utf-8")
    defaults = stack / "ciu.defaults.toml.j2"
    defaults.write_text(
        defaults.read_text(encoding="utf-8").replace(
            "[app_config.hooks]",
            '[app_config.hooks]\npre_secrets = ["pre_compose_ctx_probe.py"]\n',
            1,
        ),
        encoding="utf-8",
    )
    return stack


def _instance_overlay(repo_root: Path, body: str) -> None:
    (repo_root / "ciu.global.instance.toml.j2").write_text(body, encoding="utf-8")


def _rendered(stack: Path) -> str:
    files = list((stack / ".ciu" / "rendered").rglob("config.toml"))
    assert files, "configfile was not rendered"
    return files[0].read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    monkeypatch.delenv("CIU_SERVICES_PROFILE", raising=False)
    return _build_repo(tmp_path, monkeypatch)


# --- (a) selection facts under `ciu up --dir` --------------------------------


@pytest.mark.ciu_test_repo_reader
def test_dir_up_renders_the_instance_default_selection(repo):
    """THE ACCEPTANCE TEST (a): templates and hooks see the checkout's
    configured default selection under the single-stack parser."""
    _instance_overlay(repo, '[ciu.instance]\nservice_profiles = ["core"]\n')
    stack = _probe_stack(repo)

    assert engine.main(["-d", str(stack), "--dry-run"]) == 0

    body = _rendered(stack)
    assert 'selected_profiles = "core"' in body
    assert 'deployed_stacks = "applications/app-config"' in body
    assert 'ctx_profiles = "core|applications/app-config"' in body


@pytest.mark.ciu_test_repo_reader
def test_dir_up_without_a_configured_selection_reports_the_default_profile(repo):
    stack = _probe_stack(repo)
    assert engine.main(["-d", str(stack), "--dry-run"]) == 0
    body = _rendered(stack)
    assert 'selected_profiles = ""' in body
    assert 'deployed_stacks = "applications/app-config"' in body


@pytest.mark.ciu_test_repo_reader
def test_dir_up_honours_ciu_services_profile_like_plain_up(repo, monkeypatch):
    monkeypatch.setenv("CIU_SERVICES_PROFILE", "db")
    stack = _probe_stack(repo)
    assert engine.main(["-d", str(stack), "--dry-run"]) == 0
    assert 'selected_profiles = "db"' in _rendered(stack)


def test_single_stack_context_uses_the_wrapper_that_reads_service_profiles(tmp_path, monkeypatch):
    seen = {}

    def resolve(global_config, names):
        seen["args"] = (global_config, names)
        return deploy.profiles_pkg.Profile(name="public", config=global_config)

    monkeypatch.setattr(deploy, "load_global_config", lambda root, **kw: {"marker": kw})
    monkeypatch.setattr(deploy, "resolve_profiles", resolve)
    (tmp_path / "tooling" / "app").mkdir(parents=True)
    assert engine.single_stack_ciu_context(tmp_path, tmp_path / "tooling" / "app") == {
        "selected_profiles": ["public"],
        "deployed_stacks": ["tooling/app"],
    }
    assert seen["args"] == ({"marker": {"write_rendered": False}}, None)


# --- (b) the selection record: unit -----------------------------------------

GLOBAL = {
    "ciu": {"selection_record": {"profiles": ["internal", "public"]}},
    "deploy": {"profiles": {"internal": {}, "public": {}, "tools": {}}},
}


def test_without_the_opt_in_key_nothing_is_read_or_written(tmp_path):
    cfg = {"deploy": {"profiles": {"public": {}}}}
    (tmp_path / ".ciu").mkdir()
    (tmp_path / ".ciu" / "selection-record.json").write_text("garbage", encoding="utf-8")
    assert selection_record.enforce(tmp_path, cfg, ["public"], write=True) is None
    assert selection_record.enforce(tmp_path, {}, [], write=True) is None


def test_first_selection_is_recorded_and_a_conflict_refuses(tmp_path, capsys):
    assert selection_record.enforce(tmp_path, GLOBAL, ["public", "tools"], write=True) == ["public"]
    doc = json.loads(selection_record.record_path(tmp_path).read_text(encoding="utf-8"))
    assert doc == {"api": "ciu/selection-record", "api_version": 1, "profiles": ["public"]}
    assert "recorded this instance's guarded profile selection ['public']" in capsys.readouterr().out
    # same guarded subset, other unguarded profiles: fine
    assert selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True) == ["public"]
    assert selection_record.enforce(tmp_path, GLOBAL, ["tools", "public"], write=False) == ["public"]
    # a different guarded subset refuses, naming both and the remedy
    with pytest.raises(ValueError) as exc:
        selection_record.enforce(tmp_path, GLOBAL, ["internal"], write=True)
    message = str(exc.value)
    assert "[CIU-205]" in message
    assert "['public']" in message and "['internal']" in message
    assert "ciu clean" in message
    # dropping the guarded profile is a conflict too (it would render untyped)
    with pytest.raises(ValueError):
        selection_record.enforce(tmp_path, GLOBAL, ["tools"], write=True)


def test_an_empty_guarded_choice_is_recorded_and_pinned(tmp_path):
    assert selection_record.enforce(tmp_path, GLOBAL, ["tools"], write=True) == []
    with pytest.raises(ValueError, match=r"\[CIU-205\]"):
        selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True)


def test_a_dry_check_never_creates_the_record(tmp_path):
    assert selection_record.enforce(tmp_path, GLOBAL, ["public"], write=False) == ["public"]
    assert not selection_record.record_path(tmp_path).exists()


def test_guarded_order_does_not_matter(tmp_path):
    both = {**GLOBAL, "ciu": {"selection_record": {"profiles": ["public", "internal"]}}}
    selection_record.enforce(tmp_path, GLOBAL, ["internal", "public"], write=True)
    assert selection_record.enforce(tmp_path, both, ["public", "internal"], write=True) == [
        "public", "internal",
    ]


@pytest.mark.parametrize("table", [
    "nope",
    {"profiles": ["public"], "extra": 1},
    {},
    {"profiles": []},
    {"profiles": "public"},
    {"profiles": ["public", "public"]},
    {"profiles": [" public"]},
    {"profiles": [""]},
    {"profiles": [3]},
])
def test_a_malformed_declaration_is_a_config_error(tmp_path, table):
    cfg = {"ciu": {"selection_record": table}, "deploy": {"profiles": {"public": {}}}}
    with pytest.raises(ValueError, match=r"\[CIU-205\]"):
        selection_record.enforce(tmp_path, cfg, ["public"], write=True)
    assert not selection_record.record_path(tmp_path).exists()


def test_a_guarded_name_must_be_a_declared_profile(tmp_path):
    cfg = {"ciu": {"selection_record": {"profiles": ["pubic"]}}, "deploy": {"profiles": {"public": {}}}}
    with pytest.raises(ValueError, match=r"undeclared profile\(s\) \['pubic'\]"):
        selection_record.enforce(tmp_path, cfg, ["public"], write=True)


@pytest.mark.parametrize("payload, reason", [
    ("not json", "cannot read the selection record"),
    (json.dumps(["public"]), "is not a ciu/selection-record v1 record"),
    (json.dumps({"api": "other", "api_version": 1, "profiles": ["public"]}), "is not a ciu/selection-record v1 record"),
    (json.dumps({"api": "ciu/selection-record", "api_version": 2, "profiles": ["public"]}), "is not a ciu/selection-record v1 record"),
    (json.dumps({"api": "ciu/selection-record", "api_version": 1, "profiles": "public"}), "is not a ciu/selection-record v1 record"),
    (json.dumps({"api": "ciu/selection-record", "api_version": 1, "profiles": [1]}), "is not a ciu/selection-record v1 record"),
])
def test_an_unreadable_record_refuses_instead_of_being_replaced(tmp_path, payload, reason):
    """Each payload otherwise agrees with the selection, so only the schema
    check can refuse it."""
    path = selection_record.record_path(tmp_path)
    path.parent.mkdir()
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True)
    assert path.read_text(encoding="utf-8") == payload


def test_a_dangling_record_symlink_refuses(tmp_path):
    path = selection_record.record_path(tmp_path)
    path.parent.mkdir()
    path.symlink_to(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="cannot read the selection record"):
        selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True)


def test_losing_the_creation_race_compares_with_the_winner(tmp_path, monkeypatch):
    def winner_first(path, _profiles):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"api": "ciu/selection-record", "api_version": 1, "profiles": ["internal"]}
        ), encoding="utf-8")
        return False

    monkeypatch.setattr(selection_record, "_write_exclusive", winner_first)
    with pytest.raises(ValueError, match=r"first deployed with guarded profiles \['internal'\]"):
        selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True)


def test_exclusive_write_does_not_overwrite_an_existing_record(tmp_path):
    path = selection_record.record_path(tmp_path)
    assert selection_record._write_exclusive(path, ["public"]) is True
    assert selection_record._write_exclusive(path, ["internal"]) is False
    assert json.loads(path.read_text(encoding="utf-8"))["profiles"] == ["public"]


def test_clear_removes_the_record_once(tmp_path):
    selection_record.enforce(tmp_path, GLOBAL, ["public"], write=True)
    assert selection_record.clear(tmp_path) is True
    assert selection_record.clear(tmp_path) is False


# --- (b) the selection record: engine (`ciu up --dir`) -------------------------

GUARD = (
    "[ciu.selection_record]\n"
    'profiles = ["core", "db"]\n'
)


class _StopAfterGuard(RuntimeError):
    pass


@pytest.mark.ciu_test_repo_reader
def test_dir_up_records_the_first_selection_and_refuses_a_conflicting_one(repo, monkeypatch, capsys):
    """THE ACCEPTANCE TEST (b) on the single-stack parser."""
    _instance_overlay(repo, '[ciu.instance]\nservice_profiles = ["core"]\n' + GUARD)
    stack = _probe_stack(repo)

    # A dry run checks but does not record.
    assert engine.main(["-d", str(stack), "--dry-run"]) == 0
    assert not selection_record.record_path(repo).exists()

    # A real run records before its first Docker mutation; stop it right after.
    def stop(*_a, **_k):
        raise _StopAfterGuard("stop after the guard")

    with monkeypatch.context() as m:
        m.setattr(engine.config_model, "render_stack", stop)
        assert engine.main(["-d", str(stack)]) == 1
    doc = json.loads(selection_record.record_path(repo).read_text(encoding="utf-8"))
    assert doc["profiles"] == ["core"]

    # Same selection: fine.
    assert engine.main(["-d", str(stack), "--dry-run"]) == 0

    # Another guarded selection: refused with exit 2 before rendering the stack.
    _instance_overlay(repo, '[ciu.instance]\nservice_profiles = ["db"]\n' + GUARD)
    capsys.readouterr()
    assert engine.main(["-d", str(stack), "--dry-run"]) == 2
    out = capsys.readouterr().out
    assert "[CIU-205]" in out and "['core']" in out and "['db']" in out


@pytest.mark.ciu_test_repo_reader
def test_render_and_print_context_are_not_guarded(repo):
    _instance_overlay(repo, '[ciu.instance]\nservice_profiles = ["db"]\n' + GUARD)
    path = selection_record.record_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"api": "ciu/selection-record", "api_version": 1, "profiles": ["core"]}
    ), encoding="utf-8")
    stack = _probe_stack(repo)
    assert engine.main(["-d", str(stack), "--render-toml"]) == 0


@pytest.mark.ciu_test_repo_reader
def test_prepare_only_runs_check_but_do_not_record(repo):
    _instance_overlay(repo, GUARD)
    stack = _probe_stack(repo)
    ctx = {"selected_profiles": ["core"], "deployed_stacks": ["applications/app-config"]}
    engine.prepare_stack(stack, ciu_context=ctx, skip_hostdir_check=True)
    assert not selection_record.record_path(repo).exists()


def test_main_execution_without_any_context_is_not_guarded(monkeypatch, tmp_path):
    """Library callers without selection facts (e.g. shipped-mode helpers) keep
    the old path: the record is never consulted."""
    calls = []
    monkeypatch.setattr(selection_record, "enforce", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_k: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _d: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_a: tmp_path)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *a, **k: {})

    def stop(*_a, **_k):
        raise _StopAfterGuard("stop")

    monkeypatch.setattr(engine.config_model, "render_stack", stop)
    with pytest.raises(_StopAfterGuard):
        engine.main_execution(tmp_path)
    assert calls == []


# --- (b) the selection record: profile mode (deploy._run) ----------------------


def _run(monkeypatch, root: Path, raw: list[str], *, profile_names=None, phases=None,
         dry_run=False, global_cfg=None):
    from ciu.deploy_pkg.profiles import Profile

    cfg = global_cfg if global_cfg is not None else GLOBAL
    names = profile_names or []
    profile = Profile(name=",".join(names) or None, config=cfg)
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_k: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _c: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _d: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _c, _n: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _p, _ph=None: [{"path": "apps/api"}])
    args = argparse.Namespace(
        define_root=None, update_cert_permission=False, profile=profile_names,
        phases=phases, dry_run=dry_run, no_preflight=False, ignore_errors=False,
        skip_check=True,
    )
    return deploy._run(args, raw)


def _seed_record(root: Path, profiles: list[str]) -> Path:
    path = selection_record.record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"api": "ciu/selection-record", "api_version": 1, "profiles": profiles}
    ), encoding="utf-8")
    return path


def test_profile_up_refuses_a_conflict_before_any_render(monkeypatch, tmp_path):
    _seed_record(tmp_path, ["public"])
    monkeypatch.setattr(
        deploy, "render_selected_stacks",
        lambda *a, **k: pytest.fail("a conflicting selection must refuse before rendering"),
    )
    with pytest.raises(ValueError, match=r"\[CIU-205\]"):
        _run(monkeypatch, tmp_path, ["--deploy"], profile_names=["internal"])


def test_profile_up_early_check_never_writes(monkeypatch, tmp_path):
    seen = []

    def stop(*a, **k):
        seen.append(selection_record.record_path(tmp_path).exists())
        raise _StopAfterGuard("stop")

    monkeypatch.setattr(deploy, "render_selected_stacks", stop)
    with pytest.raises(_StopAfterGuard):
        _run(monkeypatch, tmp_path, ["--deploy"], profile_names=["public"])
    assert seen == [False]


def _pass_preflights(monkeypatch):
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
    for name in ("check_preflight", "vault_preflight", "producer_preflight",
                 "provisioning_preflight", "registry_preflight",
                 "governance_slice_preflight"):
        monkeypatch.setattr(deploy, name, lambda *a, **k: None)


def _deploy(monkeypatch, root: Path, *, entries, profile_name="public", dry_run=False,
            rendered=None, global_cfg=None):
    from ciu.deploy_pkg.profiles import Profile

    profile = Profile(name=profile_name, config=global_cfg if global_cfg is not None else GLOBAL)
    for entry in entries:
        (root / entry["path"]).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deploy, "profile_env", lambda _p: {})
    return deploy.action_deploy(
        root, profile, entries, dry_run=dry_run, ignore_errors=False,
        health_after_phase=False, update_cert_permission=False,
        rendered=rendered if rendered is not None else {}, no_preflight=False,
    )


def _entry(path, *, shipped=False, phase="phase_1"):
    return {"path": path, "name": Path(path).name, "phase_key": phase, "phase_num": 1,
            "service": {"path": path, "name": Path(path).name, "enabled": True,
                        "shipped": shipped}}


def test_profile_up_records_immediately_before_the_first_stack_starts(monkeypatch, tmp_path):
    """Review round 1 (B1) + round 2 (B2): shipped stacks never reach the
    engine's write, so profile mode records just before its first stack
    (shipped or not) starts, after that stack's live preflights."""
    seen = []

    def run_stack(stack_dir, **kw):
        path = selection_record.record_path(tmp_path)
        seen.append((Path(stack_dir).name, kw["shipped"],
                     json.loads(path.read_text(encoding="utf-8"))["profiles"]))
        return True

    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "mem_min_admission_check", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "apply_mem_min_injections", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_run_stack", run_stack)
    rc = _deploy(monkeypatch, tmp_path, entries=[
        _entry("vendor/vault", shipped=True), _entry("apps/api"),
    ])
    assert rc == 0
    assert seen == [("vault", True, ["public"]), ("api", False, ["public"])]


def test_a_concurrent_conflicting_run_is_refused_at_its_next_stack(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "mem_min_admission_check", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "apply_mem_min_injections", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_run_stack", lambda d, **k: started.append(Path(d).name) or True)
    _seed_record(tmp_path, ["internal"])  # the other run recorded first
    with pytest.raises(ValueError, match=r"\[CIU-205\]"):
        _deploy(monkeypatch, tmp_path, entries=[_entry("apps/api")])
    assert started == []


def test_a_live_provisioning_refusal_records_nothing(monkeypatch, tmp_path):
    def refuse(*_a, **k):
        if k.get("probe"):
            raise ValueError("[S13.2] stack:db:healthy is not satisfied")

    monkeypatch.setattr(deploy, "provisioning_preflight", refuse)
    monkeypatch.setattr(deploy, "_run_stack", lambda *a, **k: pytest.fail("no stack may start"))
    assert _deploy(monkeypatch, tmp_path, entries=[_entry("apps/api")]) == 1
    assert not selection_record.record_path(tmp_path).exists()


def test_a_mem_min_admission_refusal_records_nothing(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise ValueError("[S15.23] mem_min does not fit")

    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "mem_min_admission_check", refuse)
    monkeypatch.setattr(deploy, "_run_stack", lambda *a, **k: pytest.fail("no stack may start"))
    assert _deploy(monkeypatch, tmp_path, entries=[_entry("apps/api")]) == 1
    assert not selection_record.record_path(tmp_path).exists()


def test_a_profile_dry_run_deploy_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "_run_stack", lambda *a, **k: True)
    assert _deploy(monkeypatch, tmp_path, entries=[_entry("apps/api")], dry_run=True) == 0
    assert not selection_record.record_path(tmp_path).exists()


def test_profile_up_preflights_never_record(monkeypatch, tmp_path):
    """The whole static preflight chain and the network run before any stack:
    none of them may leave a record behind."""
    _pass_preflights(monkeypatch)

    def first_mutation(**_k):
        assert not selection_record.record_path(tmp_path).exists()
        raise _StopAfterGuard("stop at the network")

    monkeypatch.setattr(deploy, "ensure_workspace_network", first_mutation)
    with pytest.raises(_StopAfterGuard):
        _run(monkeypatch, tmp_path, ["--deploy"], profile_names=["public"])


def test_profile_dry_run_does_not_record(monkeypatch, tmp_path):
    _pass_preflights(monkeypatch)
    monkeypatch.setattr(deploy, "action_deploy", lambda *a, **k: 0, raising=False)

    def stop(*_a, **_k):
        raise _StopAfterGuard("stop")

    monkeypatch.setattr(deploy, "check_preflight", stop)
    with pytest.raises(_StopAfterGuard):
        _run(monkeypatch, tmp_path, ["--deploy"], profile_names=["public"], dry_run=True)
    assert not selection_record.record_path(tmp_path).exists()


def test_the_record_is_published_whole_and_no_temp_file_remains(tmp_path, monkeypatch):
    seen = []
    real_link = os.link

    def watch(src, dst, **kw):
        seen.append(json.loads(Path(src).read_text(encoding="utf-8")))
        return real_link(src, dst, **kw)

    monkeypatch.setattr(selection_record.os, "link", watch)
    path = selection_record.record_path(tmp_path)
    assert selection_record._write_exclusive(path, ["public"]) is True
    assert seen == [{"api": "ciu/selection-record", "api_version": 1, "profiles": ["public"]}]
    assert sorted(p.name for p in path.parent.iterdir()) == ["selection-record.json"]
    assert selection_record._write_exclusive(path, ["internal"]) is False
    assert sorted(p.name for p in path.parent.iterdir()) == ["selection-record.json"]


@pytest.mark.parametrize("kw, cleared", [
    ({}, True),
    ({"profile_names": ["public"]}, False),
    ({"phases": "1"}, False),
    ({"dry_run": True}, False),
])
def test_only_an_unrestricted_successful_clean_clears_the_record(monkeypatch, tmp_path, capsys, kw, cleared):
    path = _seed_record(tmp_path, ["public"])
    monkeypatch.setattr(deploy, "action_clean", lambda *a, **k: 0)
    monkeypatch.setattr(deploy, "_parse_phase_filter", lambda raw: ["phase_1"] if raw else None)
    assert _run(monkeypatch, tmp_path, ["--clean"], **kw) == 0
    assert path.exists() is (not cleared)
    if cleared:
        assert "[CIU-205] removed the selection record" in capsys.readouterr().out


def test_a_failed_clean_keeps_the_record(monkeypatch, tmp_path):
    path = _seed_record(tmp_path, ["public"])
    monkeypatch.setattr(deploy, "action_clean", lambda *a, **k: 1)
    assert _run(monkeypatch, tmp_path, ["--clean"]) == 1
    assert path.exists()


def test_a_partial_ignore_errors_clean_keeps_the_record(monkeypatch, tmp_path):
    """The real action_clean with --ignore-errors: a docker failure in one pass
    is reported, the remaining passes run, and the exit is 1, so the record
    (which protects data that may still exist) is kept."""
    from ciu.deploy_pkg.profiles import Profile

    path = _seed_record(tmp_path, ["public"])
    profile = Profile(name=None, config={"deploy": {"project_name": "p", "environment_tag": "t"}})
    (tmp_path / "apps" / "api").mkdir(parents=True)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {"apps/api": {}})
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_clean_owner", lambda _r: None)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *a, **k: ["p-t-api"])
    monkeypatch.setattr(deploy, "_known_stack_projects", lambda *a, **k: [])
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda args, **k: __import__("subprocess").CompletedProcess(args, 1, "", "daemon gone"),
    )
    rc = deploy.action_clean(tmp_path, profile, [{"path": "apps/api", "service": {}}], ignore_errors=True)
    assert rc == 1
    seen = {}
    monkeypatch.setattr(deploy, "action_clean", lambda *a, **k: seen.setdefault("rc", rc))
    args_rc = _run(monkeypatch, tmp_path, ["--clean"])
    assert args_rc == 1 and seen["rc"] == 1
    assert path.exists()


def test_clean_without_a_record_says_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(deploy, "action_clean", lambda *a, **k: 0)
    assert _run(monkeypatch, tmp_path, ["--clean"]) == 0
    assert "CIU-205" not in capsys.readouterr().out

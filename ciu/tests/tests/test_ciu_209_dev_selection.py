"""CIU-209 — `ciu dev` selects its profile like `ciu up` and honours the record.

`ciu dev` used to call the profile package's resolver directly, so it ignored
`[ciu.instance] service_profiles` (rendering a different profile than `ciu up`
for the same checkout) and never consulted the CIU-205 selection record before
starting its Docker container. It now resolves through ``deploy.resolve_profiles``
and checks (never creates) the record before any stack render or Docker call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, selection_record  # noqa: E402
from ciu.dev import run_dev  # noqa: E402

GLOBAL = {
    "deploy": {"profiles": {"internal": {}, "public": {}, "tools": {}}},
}
INSTANCE_PUBLIC = {"ciu": {"instance": {"service_profiles": ["public"]}}}
GUARD = {"ciu": {"selection_record": {"profiles": ["internal", "public"]}}}


def _config(*extra: dict) -> dict:
    merged: dict = {}
    for part in (GLOBAL, *extra):
        for key, value in part.items():
            merged.setdefault(key, {}).update(value)
    return merged


def _stack(repo: Path) -> str:
    stack = repo / "apps" / "webapp-ui"
    stack.mkdir(parents=True, exist_ok=True)
    (stack / "ciu.defaults.toml.j2").write_text(
        "[webapp_ui]\n"
        "[webapp_ui.dev]\n"
        'image = "node:22-alpine"\n'
        'command = "serve --selected={{ ciu.selected_profiles | join(\',\') }}'
        ' --public={{ \'public\' in ciu.selected_profiles }}"\n',
        encoding="utf-8",
    )
    return "apps/webapp-ui"


def _dev(repo: Path, config: dict, *, profile_name: str | None = None):
    """Run `ciu dev` with Docker replaced by a recorder; returns (rc, argvs)."""
    argvs: list = []

    def run_fn(argv, **_kw):
        argvs.append(argv)
        return 0

    rc = run_dev(
        _stack(repo), repo_root=repo, profile_name=profile_name,
        global_loader=lambda _root: config, run_fn=run_fn, interactive=False,
    )
    return rc, argvs


def _command(argvs: list) -> str:
    assert len(argvs) == 1
    return argvs[0][-1]


def _seed_record(repo: Path, profiles: list[str]) -> Path:
    path = selection_record.record_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"api": "ciu/selection-record", "api_version": 1, "profiles": profiles}
    ), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_ambient_profile(monkeypatch):
    monkeypatch.delenv("CIU_SERVICES_PROFILE", raising=False)


# --- (a) the instance default selection ---------------------------------------


def test_the_instance_default_wins_over_the_ambient_profile(tmp_path, monkeypatch):
    """THE ORACLE (a): same selection facts as `ciu up` for this checkout."""
    monkeypatch.setenv("CIU_SERVICES_PROFILE", "internal")
    rc, argvs = _dev(tmp_path, _config(INSTANCE_PUBLIC))
    assert rc == 0
    command = _command(argvs)
    assert "--selected=public" in command and "--public=True" in command


def test_an_explicit_profile_still_wins_over_the_instance_default(tmp_path):
    rc, argvs = _dev(tmp_path, _config(INSTANCE_PUBLIC), profile_name="internal")
    assert rc == 0
    command = _command(argvs)
    assert "--selected=internal" in command and "--public=False" in command


def test_without_a_configured_selection_the_ambient_profile_still_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("CIU_SERVICES_PROFILE", "internal")
    rc, argvs = _dev(tmp_path, _config())
    assert rc == 0
    assert "--selected=internal" in _command(argvs)


def test_an_invalid_instance_selection_is_the_same_exit_2_as_ciu_up(tmp_path, capsys):
    bad = {"ciu": {"instance": {"service_profiles": "public"}}}
    rc, argvs = _dev(tmp_path, _config(bad))
    assert rc == 2 and argvs == []
    assert "[S16] ciu.instance.service_profiles" in capsys.readouterr().err


def test_dev_resolves_through_the_ciu_up_wrapper(tmp_path, monkeypatch):
    seen = {}
    real = deploy.resolve_profiles

    def spy(global_cfg, names, **kw):
        seen["names"] = names
        return real(global_cfg, names, **kw)

    monkeypatch.setattr(deploy, "resolve_profiles", spy)
    rc, _ = _dev(tmp_path, _config(INSTANCE_PUBLIC), profile_name="tools")
    assert rc == 0 and seen["names"] == ["tools"]
    rc, _ = _dev(tmp_path, _config(INSTANCE_PUBLIC))
    assert rc == 0 and seen["names"] is None


# --- (b) the CIU-205 selection record ------------------------------------------


def test_a_conflicting_record_refuses_before_any_docker_call(tmp_path, capsys):
    """THE ORACLE (b): the same refusal the other verbs raise, no Docker."""
    _seed_record(tmp_path, ["public"])
    rc, argvs = _dev(tmp_path, _config(GUARD), profile_name="internal")
    assert rc == 2 and argvs == []
    err = capsys.readouterr().err
    assert "[CIU-205]" in err
    assert "['public']" in err and "['internal']" in err
    assert "ciu clean" in err


def test_a_matching_record_proceeds(tmp_path):
    _seed_record(tmp_path, ["public"])
    rc, argvs = _dev(tmp_path, _config(GUARD), profile_name="public")
    assert rc == 0 and len(argvs) == 1


def test_the_instance_default_is_what_the_record_is_checked_against(tmp_path):
    _seed_record(tmp_path, ["internal"])
    rc, argvs = _dev(tmp_path, _config(GUARD, INSTANCE_PUBLIC))
    assert rc == 2 and argvs == []


def test_dev_checks_the_record_but_never_creates_it(tmp_path):
    rc, argvs = _dev(tmp_path, _config(GUARD), profile_name="public")
    assert rc == 0 and len(argvs) == 1
    assert not selection_record.record_path(tmp_path).exists()


def test_dev_calls_enforce_read_only_with_the_resolved_selection(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        selection_record, "enforce", lambda *a, **k: calls.append((a, k)),
    )
    rc, _ = _dev(tmp_path, _config(GUARD), profile_name="public")
    assert rc == 0
    ((args, kwargs),) = calls
    assert args[0] == tmp_path.resolve()
    assert args[2] == ["public"]
    assert kwargs == {"write": False}


def test_the_record_is_checked_before_the_stack_renders(tmp_path, monkeypatch):
    _seed_record(tmp_path, ["public"])
    monkeypatch.setattr(
        "ciu.dev.config_model.render_stack",
        lambda *a, **k: pytest.fail("a conflicting selection must refuse before rendering"),
    )
    rc, argvs = _dev(tmp_path, _config(GUARD), profile_name="internal")
    assert rc == 2 and argvs == []


def test_without_the_opt_in_the_record_is_not_consulted(tmp_path):
    path = _seed_record(tmp_path, ["public"])
    path.write_text("garbage", encoding="utf-8")
    rc, argvs = _dev(tmp_path, _config(), profile_name="internal")
    assert rc == 0 and len(argvs) == 1

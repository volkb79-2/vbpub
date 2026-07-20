"""Tests for P39 ntfy URL single-source resolution (config.NotifyConfig).

The NTFY_URL env var is authoritative over the TOML source: the ntfy server
owns its own URL (a deployment fact), so no project's nyxloom.toml has to
re-hardcode it. Resolution happens at config load, so a caller constructing
NotifyConfig(...) directly still keeps the url it passes.
"""

from __future__ import annotations

from pathlib import Path

from nyxloom.config import NotifyConfig, ProjectConfig

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROJECT_TOML = """\
[project]
id = "p39proj"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]

[policy]

[notify]
{notify_lines}
"""


def _write_project(root: Path, notify_lines: str = "") -> Path:
    """Write a minimal nyxloom-trove/nyxloom.toml and return the repo root."""
    trove = root / "nyxloom-trove"
    trove.mkdir(parents=True, exist_ok=True)
    (trove / "nyxloom.toml").write_text(
        _PROJECT_TOML.format(notify_lines=notify_lines), encoding="utf-8"
    )
    return root


# =========================================================================
# Oracle O1: NTFY_URL env overrides a project's [notify] ntfy_url in toml
# =========================================================================

def test_ntfy_url_env_overrides_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://env-wins.example")
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://env-wins.example"


# =========================================================================
# Oracle O2: fallback chain env -> toml -> None
# =========================================================================

def test_ntfy_url_falls_back_to_toml_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://toml-value.example"


def test_ntfy_url_none_without_env_or_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    root = _write_project(tmp_path)

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url is None


def test_empty_ntfy_url_env_does_not_shadow_toml(tmp_path, monkeypatch):
    """An empty/blank NTFY_URL is treated as unset, not as a url that
    disables notifications by pointing them at ''."""
    monkeypatch.setenv("NTFY_URL", "")
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://toml-value.example"


# =========================================================================
# The env is authoritative over the TOML source ONLY. Direct construction
# keeps the url the caller passes -- otherwise NotifyConfig(ntfy_url=None)
# could not express "notifications disabled", and callers aiming at a
# specific endpoint (a local stub, a closed port) would be silently
# retargeted at the deployment server whenever NTFY_URL happened to be set.
# =========================================================================

def test_direct_construction_is_not_overridden_by_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")

    nc = NotifyConfig(ntfy_url="http://127.0.0.1:8099")

    assert nc.ntfy_url == "http://127.0.0.1:8099"


def test_direct_construction_can_express_disabled_under_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")

    nc = NotifyConfig(ntfy_url=None)

    assert nc.ntfy_url is None


# =========================================================================
# Oracle O3: nyxloom's own nyxloom-trove/nyxloom.toml no longer hardcodes
# ntfy_url; config still loads and NTFY_URL resolves the URL.
# =========================================================================

def test_repo_own_config_has_no_toml_ntfy_url():
    import tomllib

    toml_path = REPO_ROOT / "nyxloom-trove" / "nyxloom.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert "ntfy_url" not in data.get("notify", {})


def test_repo_own_config_loads_and_resolves_ntfy_url_from_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")
    cfg = ProjectConfig.load(REPO_ROOT)
    assert cfg.notify.ntfy_url == "https://deployment.example"


def test_repo_own_config_notifications_disabled_without_env(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    cfg = ProjectConfig.load(REPO_ROOT)
    assert cfg.notify.ntfy_url is None


# =========================================================================
# PACKAGE F1 (docs/spine-documents-spec.md): north_star/product_definition/
# roadmap/backlog are optional trove-relative-path config keys on
# ProjectConfig. Cross-doc validator behavior (S1-S4) is tests/test_spine.py;
# this covers only that ProjectConfig.load reads (or defaults) them.
# =========================================================================

def test_spine_keys_default_to_none_when_unset(tmp_path):
    root = _write_project(tmp_path)
    cfg = ProjectConfig.load(root)

    assert cfg.north_star is None
    assert cfg.product_definition is None
    assert cfg.roadmap is None
    assert cfg.backlog is None


def test_spine_keys_load_when_set(tmp_path):
    # _write_project's template only exposes one templated slot (under
    # [notify]) and TOML forbids redeclaring [project] -- write the toml
    # directly here instead of routing the new keys through that helper.
    trove = tmp_path / "nyxloom-trove"
    trove.mkdir(parents=True)
    (trove / "nyxloom.toml").write_text(
        '[project]\n'
        'id = "spineproj"\n'
        'default_branch = "main"\n'
        'handoff_globs = ["handoff/*.md"]\n'
        'north_star = "nyxloom-trove/1-north-star.md"\n'
        'product_definition = "nyxloom-trove/2-product-definition.md"\n'
        'roadmap = "nyxloom-trove/3-roadmap.md"\n'
        'backlog = "nyxloom-trove/4-backlog.md"\n',
        encoding="utf-8",
    )

    cfg = ProjectConfig.load(tmp_path)

    assert cfg.north_star == "nyxloom-trove/1-north-star.md"
    assert cfg.product_definition == "nyxloom-trove/2-product-definition.md"
    assert cfg.roadmap == "nyxloom-trove/3-roadmap.md"
    assert cfg.backlog == "nyxloom-trove/4-backlog.md"
    assert cfg.notify.ntfy_url is None


# ==========================================================================
# http_bind is INFRA-sourced (NYXLOOM_HTTP_BIND), never a toml [policy] key
# (2026-07-20). Mirrors the NTFY_URL env-authority pattern above: a per-target
# deployment fact the shared, bind-mounted toml structurally cannot carry --
# and here also a security boundary (the bind guards an unauthenticated control
# plane), so a hand-edited toml must never be able to widen it.
# ==========================================================================

_POLICY_TOML = """\
[project]
id = "bindproj"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]

[policy]
{policy_lines}

[notify]
"""


def _write_policy_project(root: Path, policy_lines: str = "") -> Path:
    trove = root / "nyxloom-trove"
    trove.mkdir(parents=True, exist_ok=True)
    (trove / "nyxloom.toml").write_text(
        _POLICY_TOML.format(policy_lines=policy_lines), encoding="utf-8")
    return root


def test_http_bind_defaults_to_loopback_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "127.0.0.1"


def test_http_bind_env_is_the_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "0.0.0.0"


def test_toml_http_bind_is_ignored_THE_DISCRIMINATOR(tmp_path, monkeypatch):
    """The whole point: a hand-edited toml http_bind must NOT reach the running
    bind, even with no env set. If toml were still a source, this would be
    "0.0.0.0" and a shared, host-mounted toml could silently expose the
    unauthenticated control plane on the host's LAN. It must stay loopback."""
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    root = _write_policy_project(tmp_path, policy_lines='http_bind = "0.0.0.0"')
    cfg = ProjectConfig.load(root)  # must still LOAD, not raise
    assert cfg.policy.http_bind == "127.0.0.1"


def test_env_wins_over_toml_http_bind_toml_is_not_even_a_fallback(tmp_path, monkeypatch):
    """Paired with the discriminator: toml is not a source AT ALL, not merely a
    lower-priority one. With both set, the env value wins and the toml value
    ("0.0.0.0") never appears -- so toml can neither widen NOR narrow the bind."""
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "10.1.2.3")
    root = _write_policy_project(tmp_path, policy_lines='http_bind = "0.0.0.0"')
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "10.1.2.3"


def test_empty_http_bind_env_does_not_shadow_the_default(tmp_path, monkeypatch):
    """Mirrors test_empty_ntfy_url_env_does_not_shadow_toml: an empty env var
    is falsy, so the safe loopback default stands rather than an empty bind."""
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "")
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "127.0.0.1"

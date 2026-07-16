"""Tests for P39 ntfy URL single-source resolution (config.NotifyConfig)."""

from __future__ import annotations

from pathlib import Path

from nyxloom.config import NotifyConfig, ProjectConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


# =========================================================================
# Oracle O1: NTFY_URL env overrides a project's [notify] ntfy_url in toml
# =========================================================================

def test_ntfy_url_env_overrides_toml(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://env-wins.example")
    nc = NotifyConfig(ntfy_url="https://toml-value.example")
    assert nc.ntfy_url == "https://env-wins.example"


# =========================================================================
# Oracle O2: fallback chain env -> toml -> None
# =========================================================================

def test_ntfy_url_falls_back_to_toml_without_env(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    nc = NotifyConfig(ntfy_url="https://toml-value.example")
    assert nc.ntfy_url == "https://toml-value.example"


def test_ntfy_url_none_without_env_or_toml(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    nc = NotifyConfig()
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

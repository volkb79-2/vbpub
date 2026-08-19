"""Deploy layout model + validation (S7.5c, CIU-34).

Resolution/validation contracts: environment closed vocabulary, ordered
host→bundles preservation, and the three tagged error paths (unknown bundle,
unknown host, empty hosts table) — all resolved BEFORE any transport opens.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg.layouts import (
    ENVIRONMENTS,
    Layout,
    list_layouts,
    resolve_layout,
)


GLOBAL = {
    "deploy": {
        "profiles": {
            "core": {"phases": ["phase_1"]},
            "db": {"phases": ["phase_1"]},
            "worker-io": {"stacks": ["infra/worker"]},
        },
        "layouts": {
            "dev-local": {
                "environment": "dev",
                "description": "single dev host",
                "hosts": {"devbox": {"bundles": ["core", "db"]}},
            },
            "three-host": {
                "environment": "prod",
                "hosts": {
                    "edge-a": {"bundles": ["core"]},
                    "edge-b": {"bundles": ["core"]},
                    "backend": {"bundles": ["db", "worker-io"]},
                },
            },
        },
    }
}

HOSTS = {"devbox": {}, "edge-a": {}, "edge-b": {}, "backend": {}, "other": {}}


def test_environments_are_closed_vocabulary():
    assert ENVIRONMENTS == ("dev", "test", "staging", "prod")


def test_resolve_layout_happy_path():
    layout = resolve_layout(GLOBAL, HOSTS, "dev-local")
    assert isinstance(layout, Layout)
    assert layout.name == "dev-local"
    assert layout.environment == "dev"
    assert layout.description == "single dev host"
    assert layout.hosts == ["devbox"]
    assert layout.bundles == {"devbox": ["core", "db"]}


def test_resolve_layout_preserves_declaration_order():
    layout = resolve_layout(GLOBAL, HOSTS, "three-host")
    assert layout.hosts == ["edge-a", "edge-b", "backend"]
    assert layout.bundles["backend"] == ["db", "worker-io"]


def test_resolve_layout_unknown_layout():
    with pytest.raises(ValueError, match=r"\[S7.5c\] Unknown layout 'nope'"):
        resolve_layout(GLOBAL, HOSTS, "nope")


def test_resolve_layout_missing_environment():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"hosts": {"devbox": {"bundles": []}}}},
        }
    }
    with pytest.raises(ValueError, match=r"environment.*required.*dev.*test.*staging.*prod"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_entry_not_a_table():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": "junk"},
        }
    }
    with pytest.raises(ValueError, match=r"\[S7.5c\] Layout 'x' must be a \[deploy\.layouts\.x\] table"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_invalid_environment():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"environment": "prod2", "hosts": {"devbox": {"bundles": []}}}},
        }
    }
    with pytest.raises(ValueError, match=r"environment.*prod2"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_empty_hosts_table():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"environment": "dev", "hosts": {}}},
        }
    }
    with pytest.raises(ValueError, match=r"\[S7.5c\] Layout 'x': 'hosts' must be a non-empty"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_hosts_not_a_table():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"environment": "dev", "hosts": ["devbox"]}},
        }
    }
    with pytest.raises(ValueError, match=r"\[S7.5c\] Layout 'x': 'hosts' must be a non-empty"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_unknown_host_names_layout_and_host():
    cfg = {
        "deploy": {
            "profiles": {"core": {"phases": ["phase_1"]}},
            "layouts": {
                "x": {
                    "environment": "dev",
                    "hosts": {"devbox": {"bundles": ["core"]}, "ghost": {"bundles": ["core"]}},
                }
            },
        }
    }
    with pytest.raises(
        ValueError,
        match=r"\[S7.5c\] Layout 'x': host 'ghost' is not in the hosts inventory",
    ):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_unknown_bundle_names_layout_host_bundle():
    cfg = {
        "deploy": {
            "profiles": {"core": {"phases": ["phase_1"]}},
            "layouts": {
                "x": {
                    "environment": "dev",
                    "hosts": {"devbox": {"bundles": ["core", "ghost-bundle"]}},
                }
            },
        }
    }
    with pytest.raises(
        ValueError,
        match=r"\[S7.5c\] Layout 'x', host 'devbox': bundle profile 'ghost-bundle' failed to resolve",
    ):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_host_entry_not_a_table():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"environment": "dev", "hosts": {"devbox": "junk"}}},
        }
    }
    with pytest.raises(
        ValueError,
        match=r"\[S7.5c\] Layout 'x', host 'devbox': must be a \[deploy\.layouts\.x\.hosts\.devbox\] table",
    ):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_bundles_must_be_list():
    cfg = {
        "deploy": {
            "profiles": {},
            "layouts": {"x": {"environment": "dev", "hosts": {"devbox": {"bundles": "core"}}}},
        }
    }
    with pytest.raises(ValueError, match=r"'bundles' must be a list"):
        resolve_layout(cfg, HOSTS, "x")


def test_resolve_layout_ignores_ambient_profile_env(monkeypatch):
    # env={} in the bundle validation must mean the ambient CIU_SERVICES_PROFILE
    # / CIU_HOST_PROFILE cannot contaminate resolution.
    monkeypatch.setenv("CIU_HOST_PROFILE", "stale")
    monkeypatch.setenv("CIU_SERVICES_PROFILE", "worker-io")
    layout = resolve_layout(GLOBAL, HOSTS, "dev-local")
    assert layout.bundles == {"devbox": ["core", "db"]}


def test_list_layouts_returns_declaration_order_without_validation():
    rows = list_layouts(GLOBAL)
    assert rows == [
        ("dev-local", "dev", ["devbox"]),
        ("three-host", "prod", ["edge-a", "edge-b", "backend"]),
    ]


def test_list_layouts_is_lenient_about_broken_entries():
    cfg = {
        "deploy": {
            "layouts": {
                "ok": {"environment": "dev", "hosts": {"a": {}}},
                "broken-env": {"hosts": {"a": {}}},
                "broken-hosts": {"environment": "dev", "hosts": ["not-a-dict"]},
                "not-a-table": "junk",
            }
        }
    }
    rows = list_layouts(cfg)
    assert rows == [
        ("ok", "dev", ["a"]),
        ("broken-env", "", ["a"]),
        ("broken-hosts", "dev", []),
        ("not-a-table", "", []),
    ]

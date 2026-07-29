"""Direct contracts for the copyable CIU v2 hook example modules."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.hooks.examples import post_compose_example, pre_compose_example  # noqa: E402


@pytest.mark.parametrize(
    ("config", "expected_value"),
    [
        ({"deploy": {"project_name": "catalog"}}, "catalog-ready"),
        ({}, "unknown-ready"),
    ],
)
def test_pre_compose_example_returns_documented_config_apply_directive(
    config: dict, expected_value: str
) -> None:
    """The example makes a template-visible deploy tag, including its fallback."""
    assert pre_compose_example.run(config, SimpleNamespace()) == {
        "deploy.computed_tag": {
            "value": expected_value,
            "apply_to_config": True,
        }
    }


@pytest.mark.parametrize(
    ("config", "expected_value"),
    [
        (
            {"deploy": {"project_name": "catalog", "environment_tag": "staging"}},
            "placeholder-catalog-staging",
        ),
        ({}, "placeholder-unknown-dev"),
    ],
)
def test_post_compose_example_returns_documented_state_persist_directive(
    config: dict, expected_value: str
) -> None:
    """The example emits the runtime token and only the S9.4 state persistence intent."""
    assert post_compose_example.run(config, SimpleNamespace()) == {
        "root_token": {
            "value": expected_value,
            "persist": "state",
        }
    }

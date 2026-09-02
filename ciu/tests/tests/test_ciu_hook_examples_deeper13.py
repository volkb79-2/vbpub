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
def test_post_compose_example_returns_documented_secret_persist_directive(
    config: dict, expected_value: str
) -> None:
    """The example emits the runtime token via S9.4a's secret channel, only.

    ciu-P46 moved this example off `persist:'state'`. It was demonstrating the
    exact anti-pattern the `state-secrets` stage now REFUSES (S3.4a):
    `root_token` paired with a literal 8+ character value is secret-shaped, so
    a consumer who copied this example verbatim would ship a stack `ciu check`
    rejects. `persist:'secret'` is both the correct destination for a minted
    runtime token and the more useful thing for this package to ship as an
    example. Nothing else about the shape changed.
    """
    assert post_compose_example.run(config, SimpleNamespace()) == {
        "root_token": {
            "value": expected_value,
            "persist": "secret",
        }
    }


def test_post_compose_example_is_accepted_by_the_state_secrets_stage() -> None:
    """The shipped example must not be refused by CIU's own always-on rule.

    A regression guard with teeth: if this example ever drifts back to
    `persist:'state'` for a secret-shaped key, this asserts it against the
    SAME predicate `ciu check`'s `state-secrets` stage uses, rather than
    against a restated copy of the rule.
    """
    from ciu.config_model import is_secret_shaped

    returned = post_compose_example.run({}, SimpleNamespace())
    for key, meta in returned.items():
        if is_secret_shaped(key, meta["value"]):
            assert meta.get("persist") != "state", (
                f"{key!r} is secret-shaped; persist:'state' would be refused "
                "by S3.4a"
            )

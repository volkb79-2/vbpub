"""P84 — the gate environment is declared, and the gate knows about it.

The bug this guards: ``mcp`` was a declared optional extra whose absence hid 16
tests behind a module-level ``importorskip``, while the gate only knew about
``zstandard``. Any *new* optional extra would repeat that silently. These tests
tie the three places that must agree — the declared extras, the ``[dev]`` extra
that builds the gate env, and the conftest gate — so they cannot drift apart.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from conftest import _REQUIRED_TEST_EXTRAS

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _optional_dependencies() -> dict[str, list[str]]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_dev_extra_installs_every_other_optional_extra() -> None:
    """``pip install -e 'topos[dev]'`` must build a gate env that reaches
    every optional code path — that is the whole contract of the [dev] extra."""
    optional = _optional_dependencies()
    dev_requirements = set(optional["dev"])
    for extra in optional:
        if extra == "dev":
            continue
        assert f"topos[{extra}]" in dev_requirements, (
            f"optional extra {extra!r} is not installed by 'topos[dev]', so the "
            f"documented gate environment cannot reach the code it guards"
        )


def test_gate_knows_about_every_optional_extra() -> None:
    """Every declared extra is checked by the conftest gate.

    Without this, adding an extra + tests that skip without it reproduces the
    exact defect P84 exists to remove: a green suite that never ran them.
    """
    optional = _optional_dependencies()
    declared = {extra for extra in optional if extra != "dev"}
    gated = {extra_name for _, extra_name in _REQUIRED_TEST_EXTRAS}
    assert declared == gated, (
        f"extras declared in pyproject.toml but not gated in conftest: "
        f"{sorted(declared - gated)}; gated but not declared: {sorted(gated - declared)}"
    )

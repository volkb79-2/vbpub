"""PWMCP-31: a consumer can install the bundled client from the bundle's own wheels, with no index.

The documented recipe is ``pip install --no-index --find-links <bundle>/wheels ./client``. It only
works when the bundle's wheelhouse holds EVERY runtime dependency of ``pwmcp-client``: the client
wheel pins its dependencies exactly (``cli-extended>=0.3.0``, ``playwright==<contract>``), and pip
resolves them from the wheelhouse alone. This module compares the client's declared runtime
dependencies with the wheels ``cmru.toml`` tells the bundle build to stage, and keeps the two
surfaces that document the recipe in sync.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[1]
README = PWMCP / "README.md"
USAGE = PWMCP / "docs" / "USAGE.md"
CONSUMERS = PWMCP / "docs" / "CONSUMERS.md"


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _client_runtime_dependencies() -> list[str]:
    project = tomllib.loads((PWMCP / "client" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list) and dependencies
    return list(dependencies)


def _staged_wheels() -> set[str]:
    config = tomllib.loads((PWMCP / "cmru.toml").read_text(encoding="utf-8"))
    specs = config["project"]["installer"]["wheels"]
    assert isinstance(specs, list) and specs
    found: set[str] = set()
    for spec in specs:
        assert spec["path"].startswith("wheels/"), spec
        assert spec["path"].endswith(".whl"), spec
        distribution = _normalised(spec["distribution"])
        assert distribution not in found, f"two installer wheel specs for {distribution}"
        found.add(distribution)
    return found


def test_the_bundle_wheelhouse_covers_every_client_runtime_dependency() -> None:
    """The recipe's promise: --no-index works because the closure is in the wheelhouse."""

    staged = _staged_wheels()
    client = _client_runtime_dependencies()
    missing = [
        requirement
        for requirement in client
        if _normalised(re.split(r"[<>=!~; \[ ]", requirement, 1)[0]) not in staged
    ]
    assert missing == [], (
        "the bundle wheelhouse cannot install the client offline: a documented --no-index recipe "
        f"needs [project.installer.wheels] to stage {missing} as well"
    )
    # The client wheel itself is staged too, so a bundle always yields an installable client.
    assert "pwmcp-client" in staged


def test_the_client_wheel_is_the_one_the_bundle_builds() -> None:
    """`[wheel].project_root` builds the client from the tree whose dependencies we just compared."""

    bundle = tomllib.loads((PWMCP / "bundle.toml").read_text(encoding="utf-8"))
    assert Path(bundle["wheel"]["project_root"]).parts[0] == "client"
    assert bundle["wheel"]["enabled"] is True


def test_the_documented_install_recipe_is_the_offline_wheelhouse_one() -> None:
    """Both consumer surfaces name the same recipe, and it is offline by construction."""

    recipes = {
        "README.md": README.read_text(encoding="utf-8"),
        "docs/USAGE.md": USAGE.read_text(encoding="utf-8"),
    }
    for name, text in recipes.items():
        assert "--no-index --find-links ./wheels" in text, name
        assert "tar -xJf" in text and "bundle" in text, name
        # the two dependencies are named from the bundle, not left to an index
        assert '"playwright==$PW_VER"' in text and '"cli-extended>=0.3.0"' in text, name
    # A bare `pip install ./client` is the pre-PWMCP-31 instruction: it reaches a public index
    # for the GitHub-only cli-extended wheel, which is exactly the failure this item reported.
    for name, text in recipes.items():
        for line in text.splitlines():
            if re.search(r"pip install(?!.*--no-index).*\./client", line):
                raise AssertionError(f"{name}: unbounded client install still documented: {line.strip()}")


def test_the_bundle_build_refuses_a_wheel_outside_the_installer_manifest() -> None:
    """`scripts/build-bundle.py` is what keeps the wheelhouse closed (the coverage guarantee)."""

    script = (PWMCP / "scripts" / "build-bundle.py").read_text(encoding="utf-8")
    assert "[project.installer.wheels]" in script
    assert "bundle contains wheel(s) missing from" in script
    # and the same list is the one this module compared against
    assert "installer.wheels" in script

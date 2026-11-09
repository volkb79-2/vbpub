"""Keep the HC-4 lens, helpers, docs, tests, and owed items synchronized."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from augeaswrap import load_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pam_env_docs_link_what_why_how_and_compatibility_limits():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    spec = (PROJECT_ROOT / "SPEC.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    changes = (PROJECT_ROOT / "CHANGES.md").read_text(encoding="utf-8")
    backlog = (PROJECT_ROOT / "BACKLOG.md").read_text(encoding="utf-8")
    spec_words = " ".join(spec.split())
    design_words = " ".join(design.split())

    assert "docs/CONSUMERS.md#pam_env-overlay-and-environment-helpers" in readme
    assert "docs/DESIGN-GUIDE.md#pam_env-overlay" in readme
    assert "The packaged `Pam_env` overlay lens" in spec
    assert "numeric index (`1`, `2`)" in spec_words
    assert "environment_item(name, value)" in spec
    assert "pam_env_item(name, default=..., override=...)" in spec
    assert "export NAME=VALUE" in spec and "AW-BL-25" in spec
    assert "## pam_env overlay" in design
    assert "decodes both representations before comparison" in design_words
    assert "inserted under the literal `01` label" in design_words
    assert "fresh observation of the previous staged bytes" in design
    assert "### pam_env overlay and environment helpers" in consumers
    assert "hand-built mappings with the wrong fields or field order" in consumers
    assert "original `pam_env.aug` overlay lens" in changes
    assert "AW-BL-25" in changes and "AW-BL-26" in changes
    assert "AW-BL-25 (compatibility)" in backlog
    assert "AW-BL-26 (acceptance, live/VM smoke)" in backlog


def test_pam_env_lens_and_fixture_pair_exists_with_package_data():
    lens_root = PROJECT_ROOT / "src" / "augeaswrap" / "lenses"
    assert (lens_root / "pam_env.aug").is_file()
    assert (lens_root / "tests" / "test_pam_env.aug").is_file()
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["augeaswrap"]
    assert "lenses/*.aug" in package_data
    assert "lenses/tests/*.aug" in package_data


def test_pam_env_consumer_toml_and_python_examples_are_executable_contracts():
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    section = consumers.split("### pam_env overlay and environment helpers", 1)[1].split(
        "\n### ", 1
    )[0]
    toml_blocks = re.findall(r"```toml\s*\n(.*?)\n```", section, flags=re.DOTALL)
    python_blocks = re.findall(r"```python\s*\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(toml_blocks) == 1 and len(python_blocks) == 1

    request = load_request(tomllib.loads(toml_blocks[0]))
    assert [resource.kind for resource in request.resources] == ["edit", "edit"]
    assert [resource.file for resource in request.resources] == [
        "/etc/environment",
        "/etc/security/pam_env.conf",
    ]
    ast.parse(python_blocks[0])

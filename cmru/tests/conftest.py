"""Make ``cmru`` importable from the source tree without installing the package.

Also enables the cli-extended pytest plugin (W2-PKG5, CLI-T3): at collection it loads
``docs/cli-review.toml`` (found through ``[tool.cli-extended]`` in ``pyproject.toml``) and fails
the session unless every active reviewed case links a collected test through
``@pytest.mark.cli_case``. A focused run of a few test files must pass
``--cli-case-partial`` (the full gate and the full suite need no flag).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest_plugins = ["cli_extended.pytest_plugin"]

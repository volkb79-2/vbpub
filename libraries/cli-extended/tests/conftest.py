"""Load the estate test-policy plugin from this repository's source tree."""

from pathlib import Path
import sys


def _policy_source() -> Path:
    """Find the repository's source tree, including pytest runs from a copied suite."""
    project = Path(__file__).resolve().parents[1]
    cwd = Path.cwd().resolve()
    for root in (*project.parents, cwd, *cwd.parents):
        candidate = root / "libraries" / "test-policy" / "src"
        if (candidate / "estate_test_policy" / "plugin.py").is_file():
            return candidate.resolve()
    raise RuntimeError("cannot locate libraries/test-policy/src from cli-extended tests")


POLICY_SRC = _policy_source()
sys.path.insert(0, str(POLICY_SRC))
pytest_plugins = ["pytester", "estate_test_policy.plugin"]


def direct_children():
    """Compatibility shape for ``test_procio.py``'s existing orphan-reaping fixture."""
    from estate_test_policy.children import direct_children as policy_direct_children

    return {
        record.identity: (record.command, record.state)
        for record in policy_direct_children().values()
    }

"""The analysis package reads judge evidence and never reaches into the judge's privates (A-478, CD18).

Anything analysis needs from the judge is a PUBLIC judge name. The allowlist
below exists so an exception would be a reviewed, reasoned line, and it stays
empty. The checker is an AST walk (never grep), and each negative source proves
one way of reaching a private name that the walk must see.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from analysis_support import PROJECT_ROOT
from assay.config import load_lane_file

#: ``"<file>:<private name>" -> reason``. CD18: stays empty.
ALLOWED_PRIVATE_JUDGE_NAMES: dict[str, str] = {}

ANALYSIS_SRC = PROJECT_ROOT / "analysis" / "src"
JUDGE_SRC = PROJECT_ROOT / "src" / "assay"
PRIVATE = re.compile(r"^_[^_]")


def _judge_modules() -> set[str]:
    """Dotted names (below ``assay``) of every judge module and subpackage."""
    names = set()
    for path in JUDGE_SRC.rglob("*.py"):
        parts = list(path.relative_to(JUDGE_SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.add(".".join(parts))
    return names


JUDGE_MODULES = _judge_modules()


def _is_judge_module(dotted: str) -> bool:
    return dotted == "assay" or dotted.removeprefix("assay.") in JUDGE_MODULES


def _judge_bindings(tree: ast.AST) -> set[str]:
    """Local names that are bound to a judge module by an import statement."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "assay" or alias.name.startswith("assay."):
                    bound.add(alias.asname or "assay")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "assay" or module.startswith("assay."):
                for alias in node.names:
                    if _is_judge_module(f"{module}.{alias.name}"):
                        bound.add(alias.asname or alias.name)
    return bound


def private_judge_uses(sources: dict[str, str]) -> list[str]:
    """``"<file>:<private name>"`` for every private judge name the sources reach."""
    trees = {name: ast.parse(text) for name, text in sources.items()}
    bindings = {name: _judge_bindings(tree) for name, tree in trees.items()}
    siblings = {Path(name).stem: name for name in sources}
    found: list[str] = []

    def resolves_to_judge(node: ast.AST, own: str) -> bool:
        if isinstance(node, ast.Name):
            return node.id in bindings[own]
        if isinstance(node, ast.Attribute):
            if resolves_to_judge(node.value, own):
                return True
            # `evidence.git`: an attribute of a sibling module that is itself a judge module.
            base = node.value
            return (
                isinstance(base, ast.Name)
                and base.id in siblings
                and node.attr in bindings[siblings[base.id]]
            )
        return False

    for name, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module == "assay" or module.startswith("assay."):
                    found.extend(
                        f"{name}:{alias.name}" for alias in node.names if PRIVATE.match(alias.name)
                    )
            elif isinstance(node, ast.Attribute):
                if PRIVATE.match(node.attr) and resolves_to_judge(node.value, name):
                    found.append(f"{name}:{node.attr}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and PRIVATE.match(node.args[1].value)
                and resolves_to_judge(node.args[0], name)
            ):
                found.append(f"{name}:{node.args[1].value}")
    return sorted(found)


def _analysis_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(ANALYSIS_SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def test_the_private_name_allowlist_is_empty():
    assert ALLOWED_PRIVATE_JUDGE_NAMES == {}


def test_analysis_reaches_no_private_judge_name():
    sources = _analysis_sources()
    assert set(sources) == {"__init__.py", "cli.py", "evidence.py"}  # guard the guard
    offenders = [
        use for use in private_judge_uses(sources) if use not in ALLOWED_PRIVATE_JUDGE_NAMES
    ]
    assert offenders == []


def test_the_checker_sees_every_way_of_reaching_a_private_name():
    assert private_judge_uses({"x.py": "from assay.mutation import _x\n"}) == ["x.py:_x"]
    assert private_judge_uses({"x.py": "from assay import _x\n"}) == ["x.py:_x"]
    assert private_judge_uses({"x.py": "from assay import cli as c\nc._x()\n"}) == ["x.py:_x"]
    assert private_judge_uses({"x.py": "import assay.cli\nassay.cli._x()\n"}) == ["x.py:_x"]
    assert private_judge_uses({"x.py": "import assay as a\na.cli._x\n"}) == ["x.py:_x"]
    assert private_judge_uses(
        {"x.py": 'from assay import git\ngetattr(git, "_x")\n'}
    ) == ["x.py:_x"]
    chain = {
        "evidence.py": "from assay import git\n",
        "cli.py": "from assay_analysis import evidence\nevidence.git._x()\n",
    }
    assert private_judge_uses(chain) == ["cli.py:_x"]


def test_the_checker_allows_privates_of_self_cls_and_the_analysis_siblings():
    allowed = {
        "evidence.py": (
            "from assay import git\n"
            "def _helper():\n    return git.public\n"
            "class K:\n"
            "    def m(self):\n        return self._x\n"
            "    @classmethod\n    def c(cls):\n        return cls._y\n"
        ),
        "cli.py": (
            "from assay import __version__, git\n"
            "from assay_analysis import evidence\n"
            "evidence._helper()\n"
        ),
    }
    assert private_judge_uses(allowed) == []


def test_the_analysis_lane_is_a_whole_target_r0_r1_lane_over_every_analysis_source():
    lane = load_lane_file(PROJECT_ROOT / "assay.toml").lane("analysis")
    discovered = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (ANALYSIS_SRC / "assay_analysis").rglob("*.py")
    )

    assert lane.rigor == ("R0", "R1")
    assert lane.judge is not None and lane.judge.mode == "whole_target"
    assert list(lane.judge.targets or ()) == discovered
    assert lane.judge.allow_excluded is False
    assert lane.judge.mutation is None
    assert "tests" not in lane.argv and "analysis/tests" in lane.argv
    gate = tomllib.loads(
        (PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml").read_text(encoding="utf-8")
    )["gates"]["tester-unified"]
    assert lane.budget_seconds == float(gate["timeout_seconds"])

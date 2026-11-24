"""Enforce D10.2-3 coverage exclusions.

Only ``os-error``, ``main-guard``, and ``tty-only`` exclusions are allowed.
Every exclusion pragma requires a non-empty reason. The package may exclude at
most 12 coverage-counted lines; raising that budget requires the package
reviewer's approval.

The matchers come from coverage.py's configured defaults so every spelling
coverage honours, including non-pragma defaults, is subject to this policy.
"""

from __future__ import annotations

import io
import re
import tokenize
import tomllib
from pathlib import Path

import coverage
import pytest
from coverage.config import DEFAULT_PARTIAL_ALWAYS
from coverage.exceptions import NotPython
from coverage.misc import join_regex
from coverage.parser import PythonParser

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "tls_edge_cli"
PYPROJECT = ROOT / "pyproject.toml"
MAX_EXCLUDED_LINES = 12
VALID_PRAGMA = re.compile(
    r"# pragma: no cover -- (os-error|main-guard|tty-only): \S(?:.*\S)?$"
)


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def coverage_policy_violations(
    source: Path,
    *,
    config_file: Path | None = PYPROJECT,
) -> list[str]:
    """Return D10 violations for every Python file at or below ``source``."""
    coverage_config = coverage.Coverage(
        config_file=(
            str(config_file)
            if config_file is not None and config_file.exists()
            else False
        )
    ).config
    exclude_matcher = re.compile(join_regex(coverage_config.exclude_list))
    partial_regex = join_regex(coverage_config.partial_list)
    violations: list[str] = []
    excluded: list[tuple[Path, int]] = []
    if list(coverage_config.partial_always_list) != list(DEFAULT_PARTIAL_ALWAYS):
        violations.append(
            "coverage partial_always_list differs from coverage.py's default: "
            f"{coverage_config.partial_always_list!r}"
        )

    for path in sorted(source.rglob("*.py")):
        relative = _display_path(path)
        text = path.read_text(encoding="utf-8")
        exclude_comment_lines: set[int] = set()
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except tokenize.TokenError as exc:
            violations.append(f"{relative}: tokenization failed: {exc}")
            continue

        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            comment = token.string
            if exclude_matcher.search(comment):
                exclude_comment_lines.add(token.start[0])
                if VALID_PRAGMA.fullmatch(comment) is None:
                    violations.append(
                        f"{relative}:{token.start[0]}: invalid coverage exclusion "
                        f"{comment!r}; expected '# pragma: no cover -- "
                        "<os-error|main-guard|tty-only>: <reason>'"
                    )
        try:
            parser = PythonParser(
                text=text,
                filename=str(path),
                exclude=join_regex(coverage_config.exclude_list),
            )
            parser.parse_source()
        except NotPython as exc:
            violations.append(f"{relative}: coverage.py could not parse source: {exc}")
            continue

        for line in sorted(parser.lines_matching(partial_regex)):
            violations.append(
                f"{relative}:{line}: 'pragma: no branch' is not permitted by D10.2-3"
            )

        source_lines = text.splitlines() or [""]
        for line in sorted(parser.raw_excluded):
            if line in exclude_comment_lines:
                continue
            display_line = min(line, len(source_lines))
            source_line = source_lines[display_line - 1].strip()
            violations.append(
                f"{relative}:{display_line}: non-pragma coverage default excludes "
                f"{source_line!r}; use the classified D10 pragma form"
            )
        excluded.extend((relative, line) for line in sorted(parser.excluded))

    if len(excluded) > MAX_EXCLUDED_LINES:
        relative, line = excluded[MAX_EXCLUDED_LINES]
        violations.append(
            f"{relative}:{line}: {len(excluded)} coverage-excluded lines exceed "
            f"D10's budget of {MAX_EXCLUDED_LINES}"
        )

    return violations


def executable_package_targets() -> set[str]:
    """Return package-relative paths with at least one executable statement."""
    coverage_config = coverage.Coverage(
        config_file=str(PYPROJECT) if PYPROJECT.exists() else False
    ).config
    targets: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        parser = PythonParser(
            text=path.read_text(encoding="utf-8"),
            filename=str(path),
            exclude=join_regex(coverage_config.exclude_list),
        )
        parser.parse_source()
        if parser.statements:
            targets.add(path.relative_to(ROOT).as_posix())
    return targets


def _source_tree(tmp_path: Path, source: str) -> Path:
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "sample.py").write_text(source, encoding="utf-8")
    return tree


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("def f():\n    return 1  # pragma no cover\n", 2),
        ("def f():\n    return 1  # PRAGMA: NO COVER\n", 2),
        ("def f():\n    return 1  # pragma: no  cover\n", 2),
        ("def f():\n    return 1  # pragma: no cover\n", 2),
        ("def f():\n    return 1  # pragma: no branch\n", 2),
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    T = int\n",
            2,
        ),
        ("def f() -> None:\n    ...\n", 2),
    ],
    ids=[
        "optional-colon",
        "uppercase",
        "double-space",
        "bare-no-cover",
        "no-branch",
        "type-checking",
        "ellipsis-body",
    ],
)
def test_coverage_policy_rejects_unclassified_or_forbidden_forms(
    tmp_path: Path, source: str, line: int
) -> None:
    violations = coverage_policy_violations(_source_tree(tmp_path, source))
    assert any(f"sample.py:{line}:" in violation for violation in violations), violations


def test_coverage_policy_accepts_the_classified_pragma(tmp_path: Path) -> None:
    source = (
        "def f() -> int:\n"
        "    return 1  # pragma: no cover -- os-error: synthetic unreachable line\n"
    )
    assert coverage_policy_violations(_source_tree(tmp_path, source)) == []


def test_coverage_policy_matches_no_branch_pragmas_in_string_literals(
    tmp_path: Path,
) -> None:
    source = (
        "def f(mode: str) -> int:\n"
        "    if mode == \"# pragma: no branch\":\n"
        "        return 1\n"
        "    return 2\n"
    )
    violations = coverage_policy_violations(_source_tree(tmp_path, source))
    assert any("sample.py:2:" in violation for violation in violations), violations


def test_coverage_policy_rejects_changed_partial_always_list(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "pyproject.toml"
    config_file.write_text(
        """\
[tool.coverage.report]
partial_branches_always = ["# pragma: no mystery"]
""",
        encoding="utf-8",
    )
    violations = coverage_policy_violations(
        _source_tree(tmp_path, "value = 1\n"),
        config_file=config_file,
    )
    assert any(
        "coverage partial_always_list differs from coverage.py's default" in violation
        for violation in violations
    ), violations


def test_coverage_policy_applies_to_the_real_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert coverage_policy_violations(SOURCE) == []


def test_release_gate_declares_every_executable_package_module() -> None:
    with (ROOT / "assay.toml").open("rb") as stream:
        judge = tomllib.load(stream)["lanes"]["release-gate"]["judge"]
    assert judge["mode"] == "whole_target"
    assert set(judge["targets"]) == executable_package_targets()

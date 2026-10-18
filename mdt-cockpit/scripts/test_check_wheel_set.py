"""An image must never be built without the first-party wheels it declares (C6)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_wheel_set  # noqa: E402

DECLARED = "# comment\ncli-extended  # trailing\nciu\ncmru\n\ntopos\nnyxloom\n"


def _setup(tmp_path: Path, wheels: list[str], declared: str = DECLARED) -> tuple[Path, Path]:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    for name in wheels:
        (wheels_dir / name).write_bytes(b"")
    wheels_list = tmp_path / "wheels.list"
    wheels_list.write_text(declared, encoding="utf-8")
    return wheels_dir, wheels_list


COMPLETE = [
    "cli_extended-0.4.0-py3-none-any.whl",
    "ciu-7.16.0-py3-none-any.whl",
    "cmru-6.1.0-py3-none-any.whl",
    "topos-0.4.1-py3-none-any.whl",
    "nyxloom-0.10.1-py3-none-any.whl",
]


def test_a_complete_set_passes_and_names_are_compared_as_distributions(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _setup(tmp_path, COMPLETE)

    assert check_wheel_set.check(wheels_dir, wheels_list) == []


def test_an_empty_wheel_directory_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _setup(tmp_path, [])

    problems = check_wheel_set.check(wheels_dir, wheels_list)

    assert len(problems) == 1 and "contains no wheels" in problems[0]
    assert "cli-extended" in problems[0]


def test_a_missing_wheel_directory_is_an_error(tmp_path: Path) -> None:
    wheels_list = tmp_path / "wheels.list"
    wheels_list.write_text(DECLARED, encoding="utf-8")

    problems = check_wheel_set.check(tmp_path / "does-not-exist", wheels_list)

    assert len(problems) == 1 and "contains no wheels" in problems[0]


def test_a_declared_distribution_that_is_absent_is_named(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _setup(tmp_path, [w for w in COMPLETE if not w.startswith("topos")])

    problems = check_wheel_set.check(wheels_dir, wheels_list)

    assert len(problems) == 1
    assert "absent" in problems[0] and "topos" in problems[0]
    assert "ciu" in problems[0].split("present:")[1], "the present set is listed as well"


def test_a_wheel_of_another_name_does_not_satisfy_a_declaration(tmp_path: Path) -> None:
    """Prefix matching would let `cmru-extras` stand in for `cmru`."""
    wheels = [w for w in COMPLETE if not w.startswith("cmru")] + ["cmru_extras-1.0-py3-none-any.whl"]
    wheels_dir, wheels_list = _setup(tmp_path, wheels)

    problems = check_wheel_set.check(wheels_dir, wheels_list)

    assert len(problems) == 1 and "cmru" in problems[0].split("absent from")[1].split("(present")[0]


def test_a_list_that_declares_nothing_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _setup(tmp_path, COMPLETE, declared="# nothing\n")

    assert "declares no first-party wheels" in check_wheel_set.check(wheels_dir, wheels_list)[0]


@pytest.mark.parametrize("wheels", [[], COMPLETE[:-1]])
def test_the_command_exits_non_zero_and_prints_the_reason(tmp_path: Path, wheels: list[str]) -> None:
    wheels_dir, wheels_list = _setup(tmp_path, wheels)

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_wheel_set.py"),
         "--wheels-dir", str(wheels_dir), "--wheels-list", str(wheels_list)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert result.stderr.startswith("[ERROR]")


def test_the_shipped_wheel_list_is_satisfied_by_the_shipped_directory_layout() -> None:
    """The real declaration parses to the five names the lane and docs rely on."""
    assert check_wheel_set.declared_names(ROOT / "pip" / "wheels.list") == [
        "cli-extended", "ciu", "cmru", "topos", "nyxloom",
    ]


def test_the_dockerfile_runs_the_check_and_has_no_empty_set_escape() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'python3 /tmp/check_wheel_set.py --wheels-dir "${wheels_dir}" --wheels-list "${wheels_list}"' in dockerfile
    assert "no first-party wheels to install" not in dockerfile
    # the check precedes the install inside the same RUN, under `set -e`
    run = dockerfile[dockerfile.index('wheels_dir="/tmp/pip/wheels"'):]
    assert run.index("check_wheel_set.py") < run.index("pip install")
    assert "COPY scripts/check_wheel_set.py /tmp/check_wheel_set.py" in dockerfile
    assert "!scripts/check_wheel_set.py" in (ROOT / ".dockerignore").read_text(encoding="utf-8")

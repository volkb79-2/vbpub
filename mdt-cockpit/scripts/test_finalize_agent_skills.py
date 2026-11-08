"""Tests for the finalizer's agent-skills step (fake CLIs on a temporary PATH).

No real CLI runs and nothing is installed into a real home: every CLI here is a small
script in a temporary directory that is the only entry of PATH, and HOME is a temporary
directory. The exit-code contract under test is the one of `<cli> skills check`:
0 = current, 1 = not current, anything else = no `skills` verb in that version.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import finalize_container_environment as finalize  # noqa: E402


def _fake_cli(
    bin_dir: Path, name: str, *, check_rc: int | None = None, install_rc: int = 0, state_name: str | None = None
) -> None:
    """A CLI whose `skills check` is current once ~/.claude/skills/<state_name> exists.

    ``check_rc`` forces the check exit code (None: derive it from the state directory).
    Every invocation is appended to ``$HOME/calls.log`` outside the skills tree.
    """
    state = state_name or f"{name}-skill"
    script = f"""#!{sys.executable}
import os, sys
from pathlib import Path
home = Path(os.environ["HOME"])
with (home / "calls.log").open("a") as log:
    log.write(" ".join(["{name}"] + sys.argv[1:]) + "\\n")
skill = home / ".claude" / "skills" / "{state}"
if sys.argv[1:3] == ["skills", "check"]:
    forced = {check_rc!r}
    sys.exit(forced if forced is not None else (0 if skill.is_dir() else 1))
if sys.argv[1:3] == ["skills", "install"]:
    if {install_rc} == 0:
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("skill\\n")
    else:
        sys.stderr.write("boom: cannot write\\n")
    sys.exit({install_rc})
sys.exit(2)
"""
    target = bin_dir / name
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(finalize, "SKILLS_MARKER_DIR", tmp_path / "markers")
    return home, bin_dir


def _calls(home: Path) -> list[str]:
    log = home / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_cli_list_is_one_constant_of_the_first_party_skill_publishers() -> None:
    assert finalize.SKILLS_CLIS == ("cmru", "ciu", "pwmcp", "topos", "cgprofile")


def test_a_not_current_cli_gets_skills_installed(env: tuple[Path, Path]) -> None:
    home, bin_dir = env
    _fake_cli(bin_dir, "cmru")

    finalize.install_agent_skills()

    assert _calls(home) == ["cmru skills check", "cmru skills install"]
    assert (home / ".claude" / "skills" / "cmru-skill" / "SKILL.md").is_file()


def test_a_current_cli_is_only_checked(env: tuple[Path, Path]) -> None:
    home, bin_dir = env
    _fake_cli(bin_dir, "cmru", check_rc=0)

    finalize.install_agent_skills()

    assert _calls(home) == ["cmru skills check"]


def test_a_cli_without_a_skills_verb_is_skipped_without_a_warning(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, bin_dir = env
    _fake_cli(bin_dir, "ciu", check_rc=2)

    finalize.install_agent_skills()

    assert _calls(home) == ["ciu skills check"]
    assert "WARN" not in capsys.readouterr().err


def test_a_crashing_check_is_a_warning_and_no_install_is_attempted(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, bin_dir = env
    crash = bin_dir / "cmru"
    crash.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.write('Traceback (most recent call last):\\nImportError: x\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    crash.chmod(0o755)

    finalize.install_agent_skills()

    assert "cmru skills check is broken (exit 1)" in capsys.readouterr().err
    assert not (home / ".claude").exists()


def test_an_unexpected_check_code_is_a_warning_not_a_no_verb_skip(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, bin_dir = env
    _fake_cli(bin_dir, "cmru", check_rc=7)

    finalize.install_agent_skills()

    assert _calls(home) == ["cmru skills check"]
    assert "cmru skills check is broken (exit 7)" in capsys.readouterr().err


def test_a_refused_install_warns_once_per_container_then_logs_info(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, bin_dir = env
    _fake_cli(bin_dir, "cmru", install_rc=1)

    finalize.install_agent_skills()
    first = capsys.readouterr()
    finalize.install_agent_skills()
    second = capsys.readouterr()

    assert "WARN" in first.err and "--overwrite-modified" in first.err
    assert "WARN" not in second.err
    assert "cmru skills install failed" in second.out


def test_the_step_is_bounded_by_a_per_call_timeout_and_a_total_budget(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    home, bin_dir = env
    for name in ("cmru", "ciu", "pwmcp"):
        _fake_cli(bin_dir, name)
    timeouts: list[float] = []

    def hung(argv, **kw):
        timeouts.append(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(finalize.subprocess, "run", hung)
    clock = iter([0.0, 0.0, 30.0, 61.0, 61.0, 61.0])
    monkeypatch.setattr(finalize.time, "monotonic", lambda: next(clock))

    finalize.install_agent_skills()

    assert finalize.SKILLS_TIMEOUT_SECONDS == 20 and finalize.SKILLS_TOTAL_BUDGET_SECONDS == 60
    assert timeouts == [20, 20]  # min(20, 60) and min(20, 30)
    assert "time budget" in capsys.readouterr().err


def test_absent_clis_are_ignored(env: tuple[Path, Path]) -> None:
    home, _ = env

    finalize.install_agent_skills()

    assert _calls(home) == []


def test_an_install_failure_is_a_warning_and_later_clis_still_run(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, bin_dir = env
    _fake_cli(bin_dir, "cmru", install_rc=3)
    _fake_cli(bin_dir, "pwmcp")

    finalize.install_agent_skills()

    assert _calls(home) == [
        "cmru skills check",
        "cmru skills install",
        "pwmcp skills check",
        "pwmcp skills install",
    ]
    err = capsys.readouterr().err
    assert "WARN" in err and "cmru skills install failed (exit 3): boom: cannot write" in err


def test_a_cli_that_cannot_be_executed_is_a_warning_not_an_exception(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, bin_dir = env
    broken = bin_dir / "cmru"
    broken.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    broken.chmod(0o755)

    finalize.install_agent_skills()

    assert "WARN" in capsys.readouterr().err


def test_a_second_run_leaves_the_home_byte_identical(env: tuple[Path, Path]) -> None:
    home, bin_dir = env
    for name in finalize.SKILLS_CLIS:
        _fake_cli(bin_dir, name)

    finalize.install_agent_skills()
    skills = home / ".claude" / "skills"
    first = {str(p.relative_to(skills)): p.read_bytes() for p in skills.rglob("*") if p.is_file()}
    calls_after_first = len(_calls(home))
    finalize.install_agent_skills()

    assert {str(p.relative_to(skills)): p.read_bytes() for p in skills.rglob("*") if p.is_file()} == first
    assert sorted(first) == sorted(f"{n}-skill/SKILL.md" for n in finalize.SKILLS_CLIS)
    # The second run only checks: no install call is added.
    assert [c for c in _calls(home)[calls_after_first:] if "install" in c] == []


def test_the_step_is_part_of_the_generic_steps() -> None:
    import inspect

    assert "install_agent_skills()" in inspect.getsource(finalize.run_generic_steps)

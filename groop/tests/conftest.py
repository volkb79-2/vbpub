from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def fixture_root() -> Path:
    return ROOT / "tests" / "fixtures"


def fixture_frame():
    import json

    from groop.model import frame_from_jsonable

    payload = json.loads((fixture_root() / "frames" / "gstammtisch-once.jsonl").read_text())
    payload.pop("type", None)
    return frame_from_jsonable(payload)


def systemctl_fixture_runner(name: str):
    from groop.drift.origin import ShowResult

    base = fixture_root() / "systemctl" / name

    def _runner(unit: str, _properties: tuple[str, ...]) -> ShowResult:
        path = base / f"{unit}.show"
        if not path.exists():
            return ShowResult("", stderr=f"Unit {unit} not found", returncode=1)
        return ShowResult(path.read_text(), returncode=0)

    return _runner


# ---------------------------------------------------------------------------
# P84 — Optional-extra gate: a skipped oracle must never read as a pass
# ---------------------------------------------------------------------------
# "Green with N skips" is indistinguishable from "green" at a glance, and that
# is how P79 shipped a bug: it was validated in a venv without ``zstandard``,
# so every zstd oracle skipped and the defect the package existed to fix was
# never executed.
#
# The gate is keyed on the *extras the gate environment must provide*, not on
# test names: a name-matching gate only covers the tests someone remembered to
# name, which is how the ``mcp`` extra (16 tests, module-level importorskip)
# stayed invisible. Any test that skips while a required extra is missing is
# reported, whatever it is called.
#
# ``pip install -e 'groop[dev]'`` installs every extra listed here.

_REQUIRED_TEST_EXTRAS: tuple[tuple[str, str], ...] = (
    # (import name, pip extra name)
    ("zstandard", "zstandard"),
    ("mcp", "mcp"),
)


def _missing_test_extras() -> list[str]:
    """Return the pip extra names the gate needs that are not importable."""
    missing = []
    for module_name, extra_name in _REQUIRED_TEST_EXTRAS:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(extra_name)
    return missing


_SKIPPED: list[tuple[str, str]] = []


def _skip_reason(report) -> str:
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        return report.longrepr[2]
    return ""


def pytest_runtest_logreport(report) -> None:
    """Record every test that actually skipped, with its reason."""
    if report.skipped and report.when in {"setup", "call"}:
        _SKIPPED.append((report.nodeid, _skip_reason(report)))


def pytest_collectreport(report) -> None:
    """Record modules skipped at collection.

    A module-level ``pytest.importorskip`` (test_mcp_server.py hides 16 tests
    behind one) never produces a runtest report, so counting only runtest
    skips would under-report the damage.
    """
    if report.skipped:
        _SKIPPED.append((report.nodeid, _skip_reason(report) or "whole module skipped"))


def pytest_sessionfinish(session, exitstatus) -> None:
    """Fail the session when a required test extra is missing.

    The observable outcome is the process exit code, not just a banner: a
    reviewer (or CI) that only reads ``$?`` must still see the gate fail.
    """
    missing = _missing_test_extras()
    if not missing:
        return

    try:
        from _pytest.config import ExitCode as _EC

        session.exitstatus = _EC.TESTS_FAILED
    except ImportError:
        session.exitstatus = 1

    bar = "!" * 64
    print(
        f"\n\n{bar}\n"
        f"!!  GATE FAILED: missing test extra(s): {', '.join(missing)}\n"
        f"!!  {len(_SKIPPED)} test(s) skipped -- this run is NOT a gate.\n"
        f"!!  Install with: pip install -e 'groop[dev]'\n"
        f"{bar}",
        file=sys.stderr,
    )
    for nodeid, reason in sorted(_SKIPPED):
        print(f"!!  SKIPPED: {nodeid} -- {reason}", file=sys.stderr)
    print(f"{bar}\n", file=sys.stderr)

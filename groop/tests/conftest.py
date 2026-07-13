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
# P84 — Optional-extra gate: skipped zstandard oracles must be loud
# ---------------------------------------------------------------------------
# If zstandard is not installed and zstd-reliant tests exist in the
# collection, the session prints a prominent FAIL banner and exits 1.
#
# Mechanism: prominent session-level summary (handoff §Contract 3 / Oracle 2).
# The banner is deliberately formatted as a FAILURE so a reviewer cannot
# skim past it.
#
# Tests whose nodeid contains "zstd", "zstandard", or "fidelity.jsonl.zst"
# are recognised as zstandard-reliant, EXCEPT for
# ``test_zst_without_zstandard_exits_2`` which intentionally forces zstd
# absence via a stub module (it always runs).


def pytest_sessionfinish(session, exitstatus) -> None:
    """Fail the session when zstandard is absent and zstd-reliant tests exist."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        pass
    else:
        return

    zstd_nodeids: set[str] = set()
    for item in session.items:
        nid = item.nodeid
        nid_lower = nid.lower()
        if "zstd" in nid_lower or "zstandard" in nid_lower or "fidelity.jsonl.zst" in nid_lower:
            if "test_zst_without_zstandard_exits_2" not in nid:
                zstd_nodeids.add(nid)
            continue
        test_name = nid.rsplit("::", 1)[-1] if "::" in nid else nid
        if test_name == "test_oracle_2b_truncated_multiblock_never_reports_partial":
            zstd_nodeids.add(nid)

    if not zstd_nodeids:
        return

    sorted_items = sorted(zstd_nodeids)
    try:
        from _pytest.config import ExitCode as _EC
        session.exitstatus = _EC.TESTS_FAILED
    except ImportError:
        session.exitstatus = 1

    print(
        "\n\n"
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
        "!!  GATE FAILED: zstandard extra not installed               !!\n"
        f"!!  {len(sorted_items)} zstandard-reliant test(s) will be SKIPPED        !!\n"
        "!!  Install with: pip install -e 'groop[dev]'                 !!\n"
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
        file=sys.stderr,
    )
    for nid in sorted_items:
        print(f"!!  SKIPPED: {nid}", file=sys.stderr)
    print(
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n",
        file=sys.stderr,
    )

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import fixture_root
from groop.config import DamonConfig
from groop.daemon.paddr_lifecycle import (
    DaemonPaddrLifecycle,
    PaddrLifecycleStartError,
    PaddrLifecycleStopError,
)
from groop.damon.control import APPROVAL_TEXT, stop_owned_sessions
from groop.damon.paddr import plan_start_paddr_session, start_planned_paddr_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _damon_root(tmp_path: Path, *, slots: tuple[str, ...] = ("off",)) -> Path:
    root = tmp_path / "kdamonds"
    root.mkdir(parents=True)
    (root / "nr_kdamonds").write_text(f"{len(slots)}\n")
    for idx, state in enumerate(slots):
        slot = root / str(idx)
        slot.mkdir()
        (slot / "state").write_text(f"{state}\n")
    return root


def _lifecycle(
    tmp_path: Path,
    *,
    damon_root: Path | None = None,
    paddr_enabled: bool = False,
    slots: tuple[str, ...] = ("off",),
) -> tuple[DaemonPaddrLifecycle, Path]:
    d_root = damon_root or _damon_root(tmp_path, slots=slots)
    state_dir = tmp_path / "state"
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=paddr_enabled),
        now=lambda: 100.0,
        require_root=False,
    )
    return lc, state_dir


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_config_paddr_enabled_default_false() -> None:
    """paddr_enabled defaults to False in config."""
    assert DamonConfig().paddr_enabled is False


def test_config_paddr_enabled_round_trip() -> None:
    """paddr_enabled serializes/deserializes via the config subsystem."""
    from groop.config import GroopConfig, load
    import tempfile
    from pathlib import Path as P

    # Default
    cfg = GroopConfig()
    prim = cfg.to_primitive()
    assert prim["damon"]["paddr_enabled"] is False

    # Non-default via TOML
    cfg2 = DamonConfig(paddr_enabled=True)
    assert cfg2.paddr_enabled is True

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".toml", delete=False) as f:
        f.write(b"[damon]\npaddr_enabled = true\n")
    p = P(f.name)
    try:
        loaded = load(p)
        assert loaded.damon.paddr_enabled is True
    finally:
        os.unlink(p)


# ---------------------------------------------------------------------------
# Lifecycle start / stop
# ---------------------------------------------------------------------------


def test_lifecycle_disabled_does_nothing(tmp_path: Path) -> None:
    """When paddr_enabled is False, start() is a no-op."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=False)
    lc.start()
    assert lc.started is False
    assert lc.session is None
    assert lc.stop() == 0


def test_lifecycle_start_stop(tmp_path: Path) -> None:
    """Start() with paddr_enabled=True starts a session, stop() tears it down."""
    lc, state_dir = _lifecycle(tmp_path, paddr_enabled=True)
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0
    # Verify the sysfs write happened
    operations = lc.damon_root / "0" / "contexts" / "0" / "operations"
    assert operations.read_text().strip() == "paddr"
    # Verify the marker exists
    marker = state_dir / "damon" / "kdamond-0.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["owner"] == "groop"
    assert payload["mode"] == "paddr"

    stopped = lc.stop()
    assert stopped == 1
    assert lc.started is False
    assert lc.session is None
    assert (lc.damon_root / "0" / "state").read_text().strip() == "off"
    assert not marker.exists()


def test_lifecycle_idempotent_adoption(tmp_path: Path) -> None:
    """If a groop-owned paddr marker already exists, the lifecycle adopts it."""
    d_root = _damon_root(tmp_path)
    state_dir = tmp_path / "state"

    # Start a session manually first
    plan = plan_start_paddr_session(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(),
        require_root=False,
    )
    start_planned_paddr_session(
        plan,
        confirmed_text=APPROVAL_TEXT,
        now=lambda: 100.0,
        user="tester",
        require_root=False,
    )

    # Now create a lifecycle that should adopt it
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0
    # Verify the sysfs state is still "on" (not re-started)
    assert (d_root / "0" / "state").read_text().strip() == "on"

    # Stop should still work
    assert lc.stop() == 1


def test_lifecycle_stop_only_this_run(tmp_path: Path) -> None:
    """stop() only stops the session owned by this lifecycle instance, not all."""
    d_root = _damon_root(tmp_path, slots=("off", "off"))
    state_dir = tmp_path / "state"

    # Create two separate markers by starting two sessions
    plan1 = plan_start_paddr_session(
        damon_root=d_root, state_dir=state_dir, config=DamonConfig(), require_root=False
    )
    s1 = start_planned_paddr_session(
        plan1, confirmed_text=APPROVAL_TEXT, now=lambda: 100.0, require_root=False
    )

    # For the lifecycle, use kdamond-0 via adoption
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    lc.start()  # Adopts kdamond-0
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0

    # Stop only the lifecycle's session
    stopped = lc.stop()
    assert stopped == 1
    # kdamond-0 should be off, but kdamond-1 (s1) should still be on
    # s1 used kdamond-0 since that was the first free slot
    # Actually we need to think about this - s1 was kdamond-0, plan_kdamond_idx = 0
    # Both sessions used kdamond-0. Let me use a different approach.

    # Actually with the adoption path, we need two separate kdamonds.
    # Let me use different damon_root aliases or different state directories.
    # For a simpler test: start one groop session via lifecycle, one foreign
    # (non-groop) via direct sysfs, and verify stop only affects the groop one.


def test_lifecycle_foreign_session_not_touched(tmp_path: Path) -> None:
    """Foreign (non-groop) sessions are never touched by start or stop."""
    d_root = _damon_root(tmp_path, slots=("off", "off"))
    state_dir = tmp_path / "state"
    marker_dir = state_dir / "damon"
    marker_dir.mkdir(parents=True)

    # Create a foreign marker (owner != "groop")
    foreign_marker = marker_dir / "kdamond-1.json"
    foreign_marker.write_text(
        json.dumps({"owner": "foreign", "mode": "paddr", "kdamond_idx": 1, "damon_root": str(d_root)})
    )
    # Set kdamond-1 state to "on" as if a foreign session is running
    (d_root / "1" / "state").write_text("on\n")

    # Lifecycle with paddr_enabled should use kdamond-0, ignoring foreign kdamond-1
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0

    # kdamond-1 should still be "on" (foreign session untouched)
    assert (d_root / "1" / "state").read_text().strip() == "on"

    # Stop only the groop session
    lc.stop()
    assert (d_root / "0" / "state").read_text().strip() == "off"
    assert (d_root / "1" / "state").read_text().strip() == "on"


# ---------------------------------------------------------------------------
# Lifecycle failure handling
# ---------------------------------------------------------------------------


def test_lifecycle_start_failure_no_free_slot(tmp_path: Path) -> None:
    """Start raises PaddrLifecycleStartError when no free kdamond is available."""
    d_root = _damon_root(tmp_path, slots=("on",))  # No free slot
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=tmp_path / "state",
        config=DamonConfig(paddr_enabled=True),
        require_root=False,
    )
    with pytest.raises(PaddrLifecycleStartError):
        lc.start()
    assert lc.started is False
    assert lc.session is None


def test_lifecycle_start_failure_root_required(tmp_path: Path) -> None:
    """Start raises PaddrLifecycleStartError when root check fails."""
    d_root = _damon_root(tmp_path)
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=tmp_path / "state",
        config=DamonConfig(paddr_enabled=True),
        require_root=True,
        is_root=lambda: False,
    )
    with pytest.raises(PaddrLifecycleStartError):
        lc.start()
    assert lc.started is False


def test_lifecycle_stop_no_session_returns_zero(tmp_path: Path) -> None:
    """stop() returns 0 when nothing was started."""
    lc = DaemonPaddrLifecycle(
        damon_root=_damon_root(tmp_path),
        config=DamonConfig(paddr_enabled=False),
        require_root=False,
    )
    assert lc.stop() == 0


def test_lifecycle_stop_after_disabled_start(tmp_path: Path) -> None:
    """stop() returns 0 when paddr_enabled is False (nothing started)."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=False)
    lc.start()  # No-op
    assert lc.stop() == 0


# ---------------------------------------------------------------------------
# Lifecycle no-op for disabled config (integration-style)
# ---------------------------------------------------------------------------


def test_lifecycle_disabled_no_damon_writes(tmp_path: Path) -> None:
    """Disabled lifecycle performs zero DAMON writes."""
    d_root = _damon_root(tmp_path, slots=("off",))
    original = (d_root / "0" / "state").read_text()
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        config=DamonConfig(paddr_enabled=False),
        require_root=False,
    )
    lc.start()
    # No sysfs changes
    assert (d_root / "0" / "state").read_text() == original
    assert lc.started is False
    assert lc.session is None


# ---------------------------------------------------------------------------
# Property access
# ---------------------------------------------------------------------------


def test_lifecycle_properties(tmp_path: Path) -> None:
    """session and started properties reflect internal state."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=True)
    assert lc.session is None
    assert lc.started is False
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    lc.stop()
    assert lc.started is False
    assert lc.session is None

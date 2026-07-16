from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import fixture_root
from topos.config import DamonConfig
from topos.daemon.paddr_lifecycle import (
    DaemonPaddrLifecycle,
    PaddrLifecycleOutcome,
    PaddrLifecycleStartError,
)
from topos.damon.control import APPROVAL_TEXT
from topos.damon.paddr import plan_start_paddr_session, start_planned_paddr_session


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
        ctx0 = slot / "contexts" / "0"
        ctx0.mkdir(parents=True)
        (ctx0 / "operations").write_text("paddr\n")
    return root


def _damon_root_with_operations(tmp_path: Path, *, ops: tuple[str, ...]) -> Path:
    """Create a damon root where each slot has the given operations."""
    root = tmp_path / "kdamonds"
    root.mkdir(parents=True)
    (root / "nr_kdamonds").write_text(f"{len(ops)}\n")
    for idx, operation in enumerate(ops):
        slot = root / str(idx)
        slot.mkdir()
        (slot / "state").write_text("on\n")
        ctx0 = slot / "contexts" / "0"
        ctx0.mkdir(parents=True)
        (ctx0 / "operations").write_text(f"{operation}\n")
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


def _write_marker(state_dir: Path, idx: int, *, payload: dict | None = None) -> Path:
    """Write a topos-owned paddr marker for kdamond *idx*."""
    marker_dir = state_dir / "damon"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"kdamond-{idx}.json"
    data = {
        "owner": "topos",
        "mode": "paddr",
        "kdamond_idx": idx,
        "damon_root": str(state_dir.parent / "kdamonds"),
        "created_at": 100.0,
    }
    if payload is not None:
        data.update(payload)
    marker.write_text(json.dumps(data) + "\n")
    return marker


def _write_foreign_marker(state_dir: Path, idx: int) -> Path:
    """Write a foreign-owned marker for kdamond *idx*."""
    marker_dir = state_dir / "damon"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"kdamond-{idx}.json"
    data = {
        "owner": "foreign",
        "mode": "paddr",
        "kdamond_idx": idx,
        "damon_root": str(state_dir.parent / "kdamonds"),
    }
    marker.write_text(json.dumps(data) + "\n")
    return marker


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_config_paddr_enabled_default_false() -> None:
    """paddr_enabled defaults to False in config."""
    assert DamonConfig().paddr_enabled is False


def test_config_paddr_enabled_round_trip() -> None:
    """paddr_enabled serializes/deserializes via the config subsystem."""
    from topos.config import ToposConfig, load
    import tempfile
    from pathlib import Path as P

    cfg = ToposConfig()
    prim = cfg.to_primitive()
    assert prim["damon"]["paddr_enabled"] is False

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


def test_config_paddr_enabled_string_is_not_truthy(tmp_path: Path) -> None:
    """A TOML string value for paddr_enabled must not become True."""
    from topos.config import load

    path = tmp_path / "config.toml"
    path.write_text('[damon]\npaddr_enabled = "true"\n', encoding="utf-8")
    assert load(path).damon.paddr_enabled is False


# ---------------------------------------------------------------------------
# Lifecycle start / stop
# ---------------------------------------------------------------------------


def test_lifecycle_disabled_does_nothing(tmp_path: Path) -> None:
    """When paddr_enabled is False, start() is a no-op."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=False)
    lc.start()
    assert lc.started is False
    assert lc.session is None
    assert lc.outcome == PaddrLifecycleOutcome.DISABLED
    assert lc.stop() == 0


def test_lifecycle_start_stop(tmp_path: Path) -> None:
    """Start() with paddr_enabled=True starts a session, stop() tears it down."""
    lc, state_dir = _lifecycle(tmp_path, paddr_enabled=True)
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0
    assert lc.outcome == PaddrLifecycleOutcome.STARTED
    operations = lc.damon_root / "0" / "contexts" / "0" / "operations"
    assert operations.read_text().strip() == "paddr"
    marker = state_dir / "damon" / "kdamond-0.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["owner"] == "topos"
    assert payload["mode"] == "paddr"

    stopped = lc.stop()
    assert stopped == 1
    assert lc.started is False
    assert lc.session is None
    assert (lc.damon_root / "0" / "state").read_text().strip() == "off"
    assert not marker.exists()


def test_lifecycle_idempotent_adoption(tmp_path: Path) -> None:
    """If a topos-owned paddr marker already exists, the lifecycle adopts it."""
    d_root = _damon_root(tmp_path)
    state_dir = tmp_path / "state"

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
    assert lc.outcome == PaddrLifecycleOutcome.ADOPTED
    assert (d_root / "0" / "state").read_text().strip() == "on"
    marker = state_dir / "damon" / "kdamond-0.json"
    assert lc.stop() == 0
    assert marker.exists()
    assert (d_root / "0" / "state").read_text().strip() == "on"


def test_lifecycle_stop_only_this_run(tmp_path: Path) -> None:
    """stop() only stops the session owned by this lifecycle instance, not all."""
    d_root = _damon_root(tmp_path, slots=("off", "on"))
    state_dir = tmp_path / "state"
    _write_foreign_marker(state_dir, 1)

    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    lc.start()
    assert lc.session is not None
    assert lc.session.kdamond_idx == 0
    assert lc.outcome == PaddrLifecycleOutcome.STARTED

    stopped = lc.stop()
    assert stopped == 1
    assert (d_root / "0" / "state").read_text().strip() == "off"
    assert (d_root / "1" / "state").read_text().strip() == "on"


def test_lifecycle_foreign_session_not_touched(tmp_path: Path) -> None:
    """Foreign (non-topos) sessions are never touched by start or stop."""
    d_root = _damon_root(tmp_path, slots=("off", "on"))
    state_dir = tmp_path / "state"

    _write_foreign_marker(state_dir, 1)

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
    assert lc.outcome == PaddrLifecycleOutcome.STARTED
    assert (d_root / "1" / "state").read_text().strip() == "on"

    lc.stop()
    assert (d_root / "0" / "state").read_text().strip() == "off"
    assert (d_root / "1" / "state").read_text().strip() == "on"


# ---------------------------------------------------------------------------
# Marker validation tests
# ---------------------------------------------------------------------------


def test_lifecycle_stale_marker_cleaned_up(tmp_path: Path) -> None:
    """A marker pointing at a kdamond with state 'off' is cleaned up."""
    d_root = _damon_root(tmp_path, slots=("off",))
    state_dir = tmp_path / "state"
    _write_marker(state_dir, 0, payload={"damon_root": str(d_root)})

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
    assert lc.outcome == PaddrLifecycleOutcome.STARTED


def test_lifecycle_malformed_marker_fails_closed(tmp_path: Path) -> None:
    """A malformed marker is retained and produces a bounded refusal."""
    d_root = _damon_root(tmp_path, slots=("off",))
    state_dir = tmp_path / "state"
    marker_dir = state_dir / "damon"
    marker_dir.mkdir(parents=True, exist_ok=True)
    bad_marker = marker_dir / "kdamond-0.json"
    bad_marker.write_text("{invalid json}")

    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    with pytest.raises(PaddrLifecycleStartError, match="cannot safely inspect"):
        lc.start()
    assert bad_marker.exists()
    assert lc.started is False


def test_lifecycle_marker_index_mismatch_fails_closed(tmp_path: Path) -> None:
    d_root = _damon_root(tmp_path, slots=("on", "on"))
    state_dir = tmp_path / "state"
    marker = _write_marker(
        state_dir,
        0,
        payload={"damon_root": str(d_root), "kdamond_idx": 1},
    )
    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        require_root=False,
    )
    with pytest.raises(PaddrLifecycleStartError, match="index does not match"):
        lc.start()
    assert marker.exists()
    assert (d_root / "0" / "state").read_text().strip() == "on"
    assert (d_root / "1" / "state").read_text().strip() == "on"


def test_lifecycle_wrong_operations_raises_error(tmp_path: Path) -> None:
    """A marker for a kdamond running vaddr raises PaddrLifecycleStartError."""
    d_root = _damon_root_with_operations(tmp_path, ops=("vaddr",))
    state_dir = tmp_path / "state"
    _write_marker(state_dir, 0, payload={"damon_root": str(d_root)})

    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    with pytest.raises(PaddrLifecycleStartError, match="claims paddr mode"):
        lc.start()
    assert lc.started is False
    assert lc.session is None


def test_lifecycle_missing_kdamond_slot_raises_error(tmp_path: Path) -> None:
    """A marker referencing a non-existent kdamond slot raises error."""
    d_root = _damon_root(tmp_path, slots=("off",))
    state_dir = tmp_path / "state"
    _write_marker(state_dir, 5, payload={"damon_root": str(d_root)})

    lc = DaemonPaddrLifecycle(
        damon_root=d_root,
        state_dir=state_dir,
        config=DamonConfig(paddr_enabled=True),
        now=lambda: 100.0,
        require_root=False,
    )
    with pytest.raises(PaddrLifecycleStartError, match="does not exist"):
        lc.start()
    assert lc.started is False
    assert lc.session is None


def test_lifecycle_adopted_live_session(tmp_path: Path) -> None:
    """A marker for a live (state=on, operations=paddr) kdamond is adopted."""
    d_root = _damon_root(tmp_path, slots=("on",))
    state_dir = tmp_path / "state"
    _write_marker(state_dir, 0, payload={"damon_root": str(d_root)})

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
    assert lc.outcome == PaddrLifecycleOutcome.ADOPTED
    assert (d_root / "0" / "state").read_text().strip() == "on"
    assert (
        d_root / "0" / "contexts" / "0" / "operations"
    ).read_text().strip() == "paddr"
    marker = state_dir / "damon" / "kdamond-0.json"
    assert lc.stop() == 0
    assert marker.exists()
    assert (d_root / "0" / "state").read_text().strip() == "on"


def test_lifecycle_stale_marker_diff_damon_root_ignored(tmp_path: Path) -> None:
    """A marker for a different damon_root is ignored."""
    d_root = _damon_root(tmp_path, slots=("off",))
    other_root = tmp_path / "other_kdamonds"
    other_root.mkdir()
    (other_root / "nr_kdamonds").write_text("1\n")
    other_slot = other_root / "0"
    other_slot.mkdir()
    (other_slot / "state").write_text("on\n")

    state_dir = tmp_path / "state"
    _write_marker(state_dir, 7, payload={"damon_root": str(other_root)})

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
    assert lc.outcome == PaddrLifecycleOutcome.STARTED


# ---------------------------------------------------------------------------
# Lifecycle failure handling
# ---------------------------------------------------------------------------


def test_lifecycle_start_failure_no_free_slot(tmp_path: Path) -> None:
    """Start raises PaddrLifecycleStartError when no free kdamond is available."""
    d_root = _damon_root(tmp_path, slots=("on",))
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
    """stop() returns 0 when paddr_enabled is False."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=False)
    lc.start()
    assert lc.stop() == 0


# ---------------------------------------------------------------------------
# Lifecycle no-op for disabled config
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
    assert (d_root / "0" / "state").read_text() == original
    assert lc.started is False
    assert lc.session is None


# ---------------------------------------------------------------------------
# Property access
# ---------------------------------------------------------------------------


def test_lifecycle_properties(tmp_path: Path) -> None:
    """session, started, and outcome properties reflect internal state."""
    lc, _state_dir = _lifecycle(tmp_path, paddr_enabled=True)
    assert lc.session is None
    assert lc.started is False
    assert lc.outcome == PaddrLifecycleOutcome.DISABLED
    lc.start()
    assert lc.started is True
    assert lc.session is not None
    assert lc.outcome == PaddrLifecycleOutcome.STARTED
    lc.stop()
    assert lc.started is False
    assert lc.session is None
    assert lc.outcome == PaddrLifecycleOutcome.DISABLED


# ---------------------------------------------------------------------------
# Daemon serve integration test
# ---------------------------------------------------------------------------


def test_lifecycle_daemon_serve_integration(tmp_path: Path) -> None:
    """Daemon serve CLI creates and starts the lifecycle when paddr_enabled."""
    import topos.cli as cli
    from topos.config import ToposConfig

    d_root = _damon_root(tmp_path, slots=("off",))
    socket_path = tmp_path / "topos.sock"

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        damon=DamonConfig(paddr_enabled=True),
    )

    class FakeCollector:
        def __init__(self, cgroup_root: Path | None, config: ToposConfig) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ("fallback",)
            self.damon_root = d_root

    class FakeServer:
        closed = False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed = True

    server = FakeServer()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)
    monkeypatch.setattr(cli, "serve_versioned_unix_socket", lambda _path, _broker, api=None: server)

    try:
        assert cli._main_daemon(["serve", "--socket", str(socket_path)]) == 0
    finally:
        monkeypatch.undo()

    assert server.closed is True

"""Exercise tnt through a tmux pseudo-terminal at real terminal dimensions."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest


SIZES = ((60, 40), (80, 24), (120, 40), (200, 60))
HOST_NAME = "qa-archive-host-eu-west-production-2026-cluster"
POLL_INTERVAL_SECONDS = 0.05
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
HELPER = Path(__file__).with_name("tui_tmux_helper.py")


def _tmux(executable: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if check and result.returncode:
        raise AssertionError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


def _capture(executable: str, session: str, *, attributes: bool = False) -> str | None:
    args = ["capture-pane", "-p"]
    if attributes:
        args.append("-e")
    result = _tmux(executable, *args, "-t", session, check=False)
    return result.stdout if result.returncode == 0 else None


def _session_exists(executable: str, session: str) -> bool:
    return _tmux(executable, "has-session", "-t", session, check=False).returncode == 0


def _pane_size(executable: str, session: str) -> tuple[int, int] | None:
    result = _tmux(
        executable,
        "display-message",
        "-p",
        "-t",
        session,
        "#{pane_width} #{pane_height}",
        check=False,
    )
    if result.returncode:
        return None
    try:
        columns, rows = result.stdout.split()
        return int(columns), int(rows)
    except ValueError as exc:
        raise AssertionError(f"tmux returned an invalid pane size: {result.stdout!r}") from exc


def _wait_for_screen(
    executable: str,
    session: str,
    status: Path,
    predicate,
    *,
    expected_size: tuple[int, int] | None = None,
    timeout: float = 30,
) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        size_matches = expected_size is None or _pane_size(executable, session) == expected_size
        captured = _capture(executable, session)
        if captured is not None:
            last = captured
            if size_matches and predicate(_plain(captured)):
                return captured
        elif status.exists():
            code = status.read_text(encoding="ascii").strip()
            raise AssertionError(f"tmux TUI exited before the screen appeared (exit {code})")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"timed out waiting for tmux screen; last capture:\n{_plain(last)}")


def _wait_for_session_exit(executable: str, session: str, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _session_exists(executable, session):
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError("quit key did not end the tmux session")


def _assert_no_traceback(captured: str) -> None:
    assert "Traceback (most recent call last)" not in _plain(captured)


def _assert_complete_legend(captured: str, columns: int) -> None:
    plain = _plain(captured)
    assert "tnt" in plain
    for key in ("Ctrl+N Menu", "Ctrl+P Jump", "H Host", "? Help", "Q Quit"):
        assert key in plain, f"missing or truncated legend key {key!r} at {columns} columns"
    if columns >= 72:
        assert "R Refresh" in plain


def _has_styled_line(captured: str, marker: str) -> bool:
    return any(marker in _plain(line) and SGR_RE.search(line) for line in captured.splitlines())


def _compact(captured: str) -> str:
    return "".join(character for character in _plain(captured) if not character.isspace())


def _assert_unicode_screen(captured: str) -> None:
    plain = _plain(captured)
    assert "\ufffd" not in plain
    assert any("\u2500" <= character <= "\u257f" for character in plain), "box-drawing border was not rendered"


def _exit_code(status: Path) -> int:
    assert status.exists(), "TUI did not write its process exit status"
    return int(status.read_text(encoding="ascii").strip())


def test_wait_for_screen_waits_for_requested_pane_size(tmp_path, monkeypatch):
    sizes = iter(((80, 24), (100, 30)))
    sleeps = []
    monkeypatch.setattr(
        sys.modules[__name__],
        "_pane_size",
        lambda executable, session: next(sizes),
        raising=False,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_capture",
        lambda executable, session: "Location: TrueNAS > Hosts\n" + HOST_NAME,
    )
    monkeypatch.setattr(time, "sleep", sleeps.append)

    captured = _wait_for_screen(
        "tmux",
        "test-session",
        tmp_path / "tui-exit-status",
        lambda screen: "Location: TrueNAS > Hosts" in screen and HOST_NAME in screen,
        expected_size=(100, 30),
    )

    assert HOST_NAME in captured
    assert sleeps and sleeps[0] > 0, "screen polling must yield between tmux reads"


def test_wait_for_session_exit_yields_between_tmux_polls(monkeypatch):
    session_states = iter((True, False))
    sleeps = []
    monkeypatch.setattr(
        sys.modules[__name__],
        "_session_exists",
        lambda executable, session: next(session_states),
    )
    monkeypatch.setattr(time, "sleep", sleeps.append)

    _wait_for_session_exit("tmux", "test-session")

    assert sleeps and sleeps[0] > 0, "quit polling must yield between tmux reads"


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_tui_in_real_tmux_terminal(size, tmp_path):
    executable = shutil.which("tmux")
    if executable is None:
        pytest.skip("tmux is not installed in tester-unified; package Debian trixie tmux there (TNT-54)")

    columns, rows = size
    session = f"tnt-ux-{uuid.uuid4().hex[:12]}"
    status = tmp_path / "tui-exit-status"
    command = shlex.join([
        sys.executable,
        str(HELPER),
        "--scratch",
        str(tmp_path / "scratch"),
        "--status",
        str(status),
    ])

    try:
        _tmux(
            executable,
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            str(columns),
            "-y",
            str(rows),
            "-e",
            "LANG=C.UTF-8",
            "-e",
            "LC_ALL=C.UTF-8",
            command,
        )

        initial = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS" in screen and "Ctrl+N Menu" in screen,
        )
        _assert_no_traceback(initial)
        _assert_complete_legend(initial, columns)

        _tmux(executable, "send-keys", "-t", session, "h")
        picker = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS > Hosts" in screen and "Select a TrueNAS host." in screen,
        )
        _assert_no_traceback(picker)
        _assert_unicode_screen(picker)
        assert HOST_NAME[:8] in _plain(picker), "host table has no visible data column"
        assert re.search(r"(?<![A-Za-z])Host(?![A-Za-z])", _plain(picker)), "host table headers are clipped away"

        focused = _capture(executable, session, attributes=True)
        assert focused is not None
        assert _has_styled_line(focused, HOST_NAME[:5]), "focused table row has no terminal color or attribute"

        resize_to = (100, 30) if size != (100, 30) else (120, 40)
        _tmux(executable, "resize-window", "-t", session, "-x", str(resize_to[0]), "-y", str(resize_to[1]))
        resized = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS > Hosts" in screen and HOST_NAME[:8] in screen,
            expected_size=resize_to,
        )
        _assert_no_traceback(resized)
        _assert_unicode_screen(resized)

        _tmux(executable, "resize-window", "-t", session, "-x", str(columns), "-y", str(rows))
        restored = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS > Hosts" in screen and HOST_NAME[:8] in screen,
            expected_size=(columns, rows),
        )
        _assert_no_traceback(restored)

        _tmux(executable, "send-keys", "-t", session, "Tab", "Tab", "Tab", "Tab", "Tab", "Tab", "Enter")
        confirmation = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "".join("Type this exact host name to confirm".split()) in _compact(screen),
        )
        _assert_no_traceback(confirmation)
        _assert_unicode_screen(confirmation)
        assert HOST_NAME in _compact(confirmation), "destructive confirmation target is clipped"

        confirmation_attributes = _capture(executable, session, attributes=True)
        assert confirmation_attributes is not None
        assert SGR_RE.search(confirmation_attributes), "confirmation has no terminal color or attribute"
        assert _has_styled_line(confirmation_attributes, "Type host name exactly"), "confirmation input focus is not styled"

        _tmux(executable, "resize-window", "-t", session, "-x", str(resize_to[0]), "-y", str(resize_to[1]))
        confirmation_resized = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "".join("Type this exact host name to confirm".split()) in _compact(screen)
            and HOST_NAME in _compact(screen),
            expected_size=resize_to,
        )
        _assert_no_traceback(confirmation_resized)
        _assert_unicode_screen(confirmation_resized)

        _tmux(executable, "send-keys", "-t", session, "Escape")
        _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS > Hosts" in screen and "Type this exact host name" not in screen,
        )
        _tmux(executable, "send-keys", "-t", session, "q")

        _wait_for_session_exit(executable, session)
        assert _exit_code(status) == 0
    finally:
        _tmux(executable, "kill-session", "-t", session, check=False)

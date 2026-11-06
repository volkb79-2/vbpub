"""Exercise tnt through a tmux pseudo-terminal at real terminal dimensions."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest


SIZES = ((60, 40), (80, 24), (120, 40), (200, 60))
HOST_NAME = "qa-archive-host-eu-west-production-2026-cluster"
POLL_INTERVAL_SECONDS = 0.05
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SGR_RE = re.compile(r"\x1b\[([0-9:;]*)m")
HELPER = Path(__file__).with_name("tui_tmux_helper.py")


def _argv(executable: str | Sequence[str]) -> list[str]:
    """``executable`` is the tmux binary, or the binary plus its private-socket options (the fixture's form)."""
    return [executable] if isinstance(executable, str) else list(executable)


def _pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            state = handle.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


def _wait_pids_gone(pids: Sequence[int], timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    alive = [pid for pid in pids if _pid_alive(pid)]
    while alive and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        alive = [pid for pid in alive if _pid_alive(pid)]
    return alive


class PrivateTmux(list):
    """tmux argv bound to a private socket, remembering the pids of its server and pane processes."""

    def __init__(self, argv: Sequence[str]) -> None:
        super().__init__(argv)
        self.pids: set[int] = set()

    def track(self) -> None:
        """Record the server and pane pids while the server is alive (a no-op once it has gone)."""
        listed = _tmux(self, "list-panes", "-a", "-F", "#{pane_pid}", check=False)
        if listed.returncode == 0:
            self.pids.update(int(token) for token in listed.stdout.split() if token.isdigit())
        server = _tmux(self, "display-message", "-p", "#{pid}", check=False)
        if server.returncode == 0 and server.stdout.strip().isdigit():
            self.pids.add(int(server.stdout.strip()))


def _stop_private_server(command: PrivateTmux) -> None:
    """Kill the private tmux server and everything in its panes, and wait until they are really gone.

    ``kill-session`` on the last session (or the helper exiting) only asks the server to
    quit; the server and the pane process then linger for a moment after the test
    returns, which is what assay's leaked-process-tree check saw. Remembering the pids
    while the server is alive and waiting for them makes the teardown synchronous,
    whether the test passed, failed or timed out.
    """
    command.track()
    pids = sorted(command.pids)
    _tmux(command, "kill-server", check=False)
    survivors = _wait_pids_gone(pids, 15)
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    survivors = _wait_pids_gone(survivors, 15)
    assert not survivors, f"tmux test teardown left processes behind: {survivors}"


@pytest.fixture
def tmux_command():
    """A tmux command bound to a private socket, so the test never touches (or leaks) the default server."""
    executable = shutil.which("tmux")
    if executable is None:
        pytest.skip("tmux is not installed in tester-unified; package Debian trixie tmux there (TNT-54)")
    # AF_UNIX paths are short; pytest's tmp_path can exceed the limit, so use a dedicated short directory.
    socket_dir = Path(tempfile.mkdtemp(prefix="tnt-tmux-"))
    command = PrivateTmux((executable, "-f", "/dev/null", "-S", str(socket_dir / "sock")))
    try:
        yield command
    finally:
        try:
            _stop_private_server(command)
        finally:
            shutil.rmtree(socket_dir, ignore_errors=True)


def _tmux(executable: str | Sequence[str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [*_argv(executable), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
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
    attributes: bool = False,
    timeout: float = 60,
) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        size_matches = expected_size is None or _pane_size(executable, session) == expected_size
        captured = _capture(executable, session, attributes=attributes)
        if captured is not None:
            last = captured
            screen = captured if attributes else _plain(captured)
            if size_matches and predicate(screen):
                return captured
        elif status.exists():
            code = status.read_text(encoding="ascii").strip()
            raise AssertionError(f"tmux TUI exited before the screen appeared (exit {code})")
        time.sleep(POLL_INTERVAL_SECONDS)
    raw_input_rows = [line for line in last.splitlines() if "Type host name exactly" in _plain(line)]
    raise AssertionError(
        f"timed out waiting for tmux screen; last capture:\n{_plain(last)}\n"
        f"raw input row(s) with escape sequences: {raw_input_rows!r}"
    )


def _wait_for_session_exit(executable: str, session: str, *, timeout: float = 90) -> None:
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
    for key in ("Ctrl+N Menu", "Ctrl+P Jump", "H Host", "? Help"):
        assert key in plain, f"missing or truncated legend key {key!r} at {columns} columns"
    assert "Q Quit" in plain or "q Quit" in plain, f"missing or truncated quit legend at {columns} columns"
    if columns >= 72:
        assert "R Refresh" in plain


def _complete_legend_visible(screen: str, columns: int) -> bool:
    keys = ["Ctrl+N Menu", "Ctrl+P Jump", "H Host", "? Help"]
    if columns >= 72:
        keys.append("R Refresh")
    return all(key in screen for key in keys) and ("Q Quit" in screen or "q Quit" in screen)


def _has_styled_line(captured: str, marker: str) -> bool:
    return any(marker in _plain(line) and SGR_RE.search(line) for line in captured.splitlines())


def _has_focused_input(captured: str, marker: str) -> bool:
    for line in captured.splitlines():
        plain_line = _plain(line)
        marker_start = plain_line.find(marker)
        if marker_start < 0:
            continue
        # Use the input widget's own frame when present. A narrow modal can
        # overlap the app's box-drawing prefix column, so the old pair of `█`
        # sentinels did not necessarily bracket the input row at 60 columns.
        left = plain_line.rfind("▊", 0, marker_start)
        right = plain_line.find("▎", marker_start + len(marker))
        if left < 0:
            left = marker_start - 1
        if right < 0:
            right = marker_start + len(marker)
        inverse = False
        column = 0
        for token in re.finditer(r"\x1b\[([0-9:;]*)m|.", line):
            parameters = token.group(1)
            if parameters is None:
                # tmux-256color renders Textual's focused cursor as reverse
                # video on its current cell, which may be an ordinary space.
                # Textual may apply reverse video to the input's leading
                # cursor cell, which is the left frame glyph at narrow sizes.
                if inverse and left <= column < right:
                    return True
                column += 1
                continue
            codes = set(parameters.replace(":", ";").split(";"))
            if not parameters or "0" in codes or "27" in codes:
                inverse = False
            if "7" in codes:
                inverse = True
    return False


def _confirmation_content(captured: str) -> str:
    # Textual wraps the prompt and target at narrow pane widths. Keep only the
    # modal interior on each row so text behind the overlay cannot interrupt
    # the target name as wrapped lines are joined.
    content = []
    for line in _plain(captured).splitlines():
        left = line.find("█")
        right = line.rfind("█")
        if left >= 0 and right > left:
            line = line[left + 1:right]
        content.extend(character for character in line if not character.isspace())
    return "".join(content)


def _host_name_visible_after_prompt(captured: str) -> bool:
    content = _confirmation_content(captured)
    prompt = "Typethisexacthostnametoconfirm:"
    prompt_end = content.find(prompt)
    if prompt_end < 0:
        return False
    return HOST_NAME in content[prompt_end + len(prompt):]


def _host_remove_confirmation_visible(captured: str) -> bool:
    content = _confirmation_content(captured)
    return _host_name_visible_after_prompt(captured) and all(
        marker in content for marker in ("Typehostnameexactly", "Removehost", "Cancel")
    )


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
    def capture(executable, session, *, attributes=False):
        assert attributes is False
        return "Location: TrueNAS > Hosts\n" + HOST_NAME

    monkeypatch.setattr(sys.modules[__name__], "_capture", capture)
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
def test_tui_in_real_tmux_terminal(size, tmp_path, tmux_command):
    executable = tmux_command

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
            "TERM=tmux-256color",
            "-e",
            "LANG=C.UTF-8",
            "-e",
            "LC_ALL=C.UTF-8",
            command,
        )
        executable.track()

        initial = _wait_for_screen(
            executable,
            session,
            status,
            lambda screen: "Location: TrueNAS" in screen and _complete_legend_visible(screen, columns),
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
        confirmation_attributes = _wait_for_screen(
            executable,
            session,
            status,
            lambda capture: (
                _host_name_visible_after_prompt(capture)
                and _has_focused_input(capture, "Type host name exactly")
            ),
            attributes=True,
        )
        confirmation = confirmation_attributes
        _assert_no_traceback(confirmation)
        _assert_unicode_screen(confirmation)
        assert _host_name_visible_after_prompt(confirmation), "destructive confirmation target is clipped"

        assert SGR_RE.search(confirmation_attributes), "confirmation has no terminal color or attribute"
        assert _has_focused_input(confirmation_attributes, "Type host name exactly"), "confirmation input focus is not styled"

        _tmux(executable, "resize-window", "-t", session, "-x", str(resize_to[0]), "-y", str(resize_to[1]))
        confirmation_resized = _wait_for_screen(
            executable,
            session,
            status,
            _host_remove_confirmation_visible,
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
        # The tmux_command fixture kills the whole private server and waits for it; this only
        # ends the session early so a failing test does not keep a TUI running while it unwinds.
        _tmux(executable, "kill-session", "-t", session, check=False)


def test_private_server_teardown_kills_server_and_pane_synchronously(tmux_command):
    """The fixture's teardown must leave neither a tmux server nor a pane process (assay leak check)."""
    _tmux(tmux_command, "new-session", "-d", "-s", "leakprobe", "sleep 300")
    tmux_command.track()
    pids = sorted(tmux_command.pids)
    assert len(pids) == 2 and all(_pid_alive(pid) for pid in pids), pids

    _stop_private_server(tmux_command)

    assert not [pid for pid in pids if _pid_alive(pid)]
    # A second teardown (the fixture's own) on an already-dead server is a harmless no-op.
    _stop_private_server(tmux_command)

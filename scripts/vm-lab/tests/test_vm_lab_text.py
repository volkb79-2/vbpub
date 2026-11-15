"""`console text` and `console wait-for-text` without vmscreen, pwmcp, QEMU, Docker, or real time."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import vm_lab
import vm_lab_text
from vm_lab import GENERIC_PROFILE, RunnerError
from vm_lab_text import (
    OCR_TEMP_FILENAME,
    RunResult,
    ScreenReader,
    ScreenText,
    TextEngineError,
    TextNotApplicable,
    TextReadError,
    TextUsageError,
)

REPO = Path(__file__).resolve().parents[3]
SURFACE = REPO / "libraries" / "vmscreen" / "docs" / "cli-surface.json"
TRUENAS_FRONT = REPO / "scripts" / "truenas" / "tcg_vm.py"


# --- fakes --------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def vm_ok(text: str, exact=True) -> RunResult:
    return RunResult(0, json.dumps({"source": "vga-text", "exact": exact, "text": text}), "")


def vm_inexact(exact) -> RunResult:
    return RunResult(
        0, json.dumps({"source": "vga-text", "exact": exact, "exact_reason": "ink map mismatch", "text": "x"}), ""
    )


def vm_none() -> RunResult:
    return RunResult(4, json.dumps({"ok": False, "exact": False, "error": "no source", "exit_code": 4}), "no source")


PROBE_OK = RunResult(0, "usage: vmscreen text [--qmp SOCK] [--dump-dir DIR] [--qemu-dump-dir DIR] [--json]", "")
# What `python -m vmscreen text --help` really prints from a bare source tree (verified by running it).
PROBE_NOT_INSTALLED = RunResult(
    1,
    "",
    "Traceback (most recent call last):\n  File \"cli.py\", line 26, in identity\n"
    "cli_extended.identity.VersionLookupError: distribution 'vmscreen' is not installed\n",
)
PROBE_NO_MODULE = RunResult(1, "", "/usr/bin/python3: No module named vmscreen\n")
PROBE_OLD = RunResult(0, "usage: vmscreen text [--qmp SOCK] [--dump-dir DIR] [--json]", "")


def ocr_ok(text: str) -> RunResult:
    return RunResult(0, json.dumps({"engine": "tesseract", "text": text, "lines": [], "words": []}), "")


class Rig:
    """A reader wired to fakes; ``calls`` is the ordered record of everything that ran."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        vmscreen=(),
        pwmcp=(),
        engine="auto",
        have_pwmcp=True,
        vmscreen_on_path=False,
        probe=PROBE_OK,
        environ=None,
    ):
        self.state = tmp_path
        self.calls: list[tuple] = []
        self.probes: list[tuple] = []
        self.probe_result = probe
        self.notes: list[str] = []
        self.vmscreen = list(vmscreen)
        self.pwmcp = list(pwmcp)
        self.qmp_error: Exception | None = None
        self.png_bytes = b"\x89PNG fake"
        self.png_seen_by_pwmcp: list[bool] = []
        self.reader = ScreenReader(
            state_dir=tmp_path,
            vmscreen_qmp_path=lambda: tmp_path / "short-qmp.sock",
            qmp_socket=tmp_path / "qmp.sock",
            engine=engine,
            qmp_call=self.qmp,
            runner=self.run,
            which=lambda name: {
                "pwmcp": "/usr/bin/pwmcp" if have_pwmcp else None,
                "vmscreen": "/opt/bin/vmscreen" if vmscreen_on_path else None,
            }.get(name),
            environ={} if environ is None else environ,
            note=self.notes.append,
        )

    def qmp(self, socket_path, command, arguments, timeout):
        self.calls.append(("qmp", socket_path, command, arguments, timeout))
        if self.qmp_error is not None:
            raise self.qmp_error
        (self.state / OCR_TEMP_FILENAME).write_bytes(self.png_bytes)

    def run(self, argv, env, timeout):
        if "--help" in argv:  # the once-per-run probe of the resolved vmscreen command
            self.probes.append((list(argv), dict(env), timeout))
            if isinstance(self.probe_result, BaseException):
                raise self.probe_result
            return self.probe_result
        kind = "pwmcp" if argv[0] == "pwmcp" else "vmscreen"
        self.calls.append((kind, list(argv), dict(env), timeout))
        if kind == "pwmcp":
            self.png_seen_by_pwmcp.append((self.state / OCR_TEMP_FILENAME).exists())
        queue = self.vmscreen if kind == "vmscreen" else self.pwmcp
        item = queue.pop(0)
        if callable(item):  # a scripted effect that sees the timeout it was given
            item = item(argv, timeout)
        if isinstance(item, BaseException):
            raise item
        return item

    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]


class Script:
    """A scripted ``read``: ScreenText or an exception per poll; the last item repeats."""

    def __init__(self, *items):
        self.items = list(items)
        self.reads = 0
        self.budgets: list = []

    def __call__(self, budget=None) -> ScreenText:
        item = self.items[min(self.reads, len(self.items) - 1)]
        self.reads += 1
        self.budgets.append(budget)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return ScreenText(item, "vmscreen", True)
        return item


def make_spec(pattern="MENU", **overrides):
    values = {
        "pattern": pattern,
        "literal": False,
        "fail_pattern": None,
        "loop_pattern": None,
        "loop_count": None,
        "timeout": 10.0,
        "interval": 2.0,
        "json": False,
    }
    values.update(overrides)
    return vm_lab_text.wait_spec(SimpleNamespace(**values))


def wait(spec, script, clock=None):
    clock = clock or FakeClock()
    outcome = vm_lab_text.wait_loop(spec, script, clock=clock, sleep=clock.sleep)
    return outcome, clock


# --- vmscreen engine ------------------------------------------------------------


TEXT_ARGS = ["text", "--source", "vga", "--json"]


def test_vmscreen_on_path_is_used_as_the_command_and_probed_once(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB")], vmscreen_on_path=True)
    rig.reader.read()
    kind, argv, _env, _timeout = rig.calls[0]
    assert kind == "vmscreen"
    assert argv == [
        "/opt/bin/vmscreen", *TEXT_ARGS,
        "--qmp", str(tmp_path / "short-qmp.sock"),
        "--dump-dir", str(tmp_path),
        "--qemu-dump-dir", "/state",
    ]
    assert [probe[0] for probe in rig.probes] == [["/opt/bin/vmscreen", "text", "--help"]]


def test_without_vmscreen_on_path_the_module_of_this_python_is_the_fallback(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB")])
    rig.reader.read()
    argv = rig.calls[0][1]
    assert argv[:3] == [sys.executable, "-m", "vmscreen"]
    assert argv[3:] == [
        *TEXT_ARGS,
        "--qmp", str(tmp_path / "short-qmp.sock"),
        "--dump-dir", str(tmp_path),
        "--qemu-dump-dir", "/state",
    ]
    assert [probe[0] for probe in rig.probes] == [[sys.executable, "-m", "vmscreen", "text", "--help"]]


def test_the_inherited_environment_is_passed_through_unchanged(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("a")], environ={"KEEP": "1"})
    rig.reader.read()
    assert rig.calls[0][2] == {"KEEP": "1"}
    assert rig.probes[0][1] == {"KEEP": "1"}


def test_the_probe_runs_once_for_a_run_of_many_polls(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("a"), vm_none(), vm_ok("b")], pwmcp=[ocr_ok("o")])
    for _ in range(3):
        rig.reader.read()
    assert len(rig.probes) == 1
    assert rig.kinds().count("vmscreen") == 3


def test_a_probe_in_a_multi_poll_wait_happens_once(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("a")] * 4 + [vm_ok("MENU")])
    spec = make_spec("MENU", timeout=100.0)
    clock = FakeClock()
    assert vm_lab_text.wait_loop(spec, rig.reader.read, clock=clock, sleep=clock.sleep).result == "matched"
    assert len(rig.probes) == 1


def test_the_resolver_and_the_probe_are_injectable(tmp_path):
    seen = []
    runs = []

    def runner(argv, env, timeout):
        runs.append(list(argv))
        return vm_ok("injected")

    reader = ScreenReader(
        state_dir=tmp_path,
        vmscreen_qmp_path=lambda: tmp_path / "s.sock",
        qmp_socket=tmp_path / "qmp.sock",
        engine="vmscreen",
        qmp_call=None,
        runner=runner,
        resolve_vmscreen=lambda: ["/custom/vmscreen"],
        probe_vmscreen=lambda command, timeout: seen.append((command, timeout)),
        environ={},
    )
    assert reader.read().text == "injected"
    assert runs[0][:2] == ["/custom/vmscreen", "text"]
    assert seen == [(["/custom/vmscreen"], 30.0)]  # the probe is injected: the runner never saw `--help`
    assert all("--help" not in run for run in runs)


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        (PROBE_NOT_INSTALLED, "is not installed or not runnable.*distribution 'vmscreen' is not installed"),
        (PROBE_NO_MODULE, "is not installed or not runnable.*No module named vmscreen"),
        (PROBE_OLD, "is too old: `text --help` lacks --qemu-dump-dir"),
        (FileNotFoundError("no such file"), "cannot run .*no such file"),
        (TextReadError("vmscreen timed out after 30s"), "did not answer `text --help`"),
    ],
)
def test_an_unusable_vmscreen_is_not_available_with_a_reason(tmp_path, probe, reason):
    forced = Rig(tmp_path, probe=probe, engine="vmscreen")
    with pytest.raises(TextEngineError, match=f"vmscreen is not available: .*{reason}"):
        forced.reader.read()
    assert forced.kinds() == []
    auto = Rig(tmp_path, probe=probe, pwmcp=[ocr_ok("from ocr")])
    assert auto.reader.read().engine == "ocr"
    assert len(auto.notes) == 1 and auto.notes[0].startswith("vm-lab: vmscreen is not available: ")
    assert auto.notes[0].endswith("; using OCR only")
    assert "vmscreen" not in auto.kinds()


def test_every_vmscreen_option_the_vm_tool_passes_exists_for_the_text_command(tmp_path):
    surface = json.loads(SURFACE.read_text())
    route = next(item for item in surface["routes"] if item["id"] == "route:entrypoint:vmscreen/text")
    known = set(route["common_controls"])
    choices: dict[str, list[str]] = {}
    for action in route["actions"]:
        known.update(action["flags"])
        for flag in action["flags"]:
            choices[flag] = action.get("choices") or []
    rig = Rig(tmp_path, vmscreen=[vm_ok("a")])
    rig.reader.read()
    argv = rig.calls[0][1]
    passed = [token for token in argv[argv.index("text") + 1:] if token.startswith("--")]
    assert passed, "no options found in the vmscreen argv"
    unknown = [token for token in passed if token not in known]
    assert unknown == [], f"vmscreen text has no option {unknown}"
    assert argv[argv.index("--source") + 1] in choices["--source"]


def test_vmscreen_accepts_exact_and_partial_text_only(tmp_path):
    for exact in (True, "partial"):
        rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB", exact)])
        assert rig.reader.read() == ScreenText("GNU GRUB", "vmscreen", exact)
    for exact in ("unverified", "stream", False, None, 1):
        rig = Rig(tmp_path, vmscreen=[vm_inexact(exact)], engine="vmscreen")
        with pytest.raises(TextNotApplicable, match="not verified"):
            rig.reader.read()


# --- auto and forced engines ------------------------------------------------------


def test_auto_uses_verified_vmscreen_text_and_does_not_start_ocr(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB")], pwmcp=[ocr_ok("never")])
    assert rig.reader.read() == ScreenText("GNU GRUB", "vmscreen", True)
    assert rig.kinds() == ["vmscreen"]
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_auto_falls_back_to_ocr_when_vmscreen_says_no_source_applies(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_none()], pwmcp=[ocr_ok("Debian GNU/Linux")])
    assert rig.reader.read() == ScreenText("Debian GNU/Linux", "ocr", None)
    assert rig.kinds() == ["vmscreen", "qmp", "pwmcp"]


@pytest.mark.parametrize("exact", ["unverified", "stream", False])
def test_auto_falls_back_to_ocr_for_an_unverified_vmscreen_result(tmp_path, exact):
    rig = Rig(tmp_path, vmscreen=[vm_inexact(exact)], pwmcp=[ocr_ok("from ocr")])
    shot = rig.reader.read()
    assert (shot.text, shot.engine, shot.exact) == ("from ocr", "ocr", None)
    assert rig.kinds() == ["vmscreen", "qmp", "pwmcp"]


def test_auto_notes_once_for_the_whole_run_when_vmscreen_cannot_start(tmp_path):
    rig = Rig(tmp_path, probe=PROBE_NOT_INSTALLED, pwmcp=[ocr_ok("one"), ocr_ok("two")])
    assert rig.reader.read().text == "one"
    assert rig.reader.read().text == "two"
    assert len(rig.notes) == 1
    assert rig.notes[0].startswith("vm-lab: vmscreen is not available: ")
    assert "distribution 'vmscreen' is not installed" in rig.notes[0]
    assert rig.notes[0].endswith("; using OCR only")
    assert len(rig.probes) == 1, "an unavailable vmscreen must not be probed again every poll"
    assert rig.kinds().count("vmscreen") == 0
    assert rig.kinds().count("pwmcp") == 2


def test_forced_vmscreen_that_cannot_start_is_an_error_and_is_not_probed_again(tmp_path):
    rig = Rig(tmp_path, probe=PROBE_NOT_INSTALLED, engine="vmscreen")
    with pytest.raises(TextEngineError, match="vmscreen is not available: .*is not installed"):
        rig.reader.read()
    with pytest.raises(TextEngineError, match="vmscreen is not available"):
        rig.reader.read()
    assert len(rig.probes) == 1 and rig.kinds() == []
    assert rig.notes == []


def test_forced_vmscreen_reports_a_screen_it_cannot_read_as_not_applicable(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_none()], engine="vmscreen")
    with pytest.raises(TextNotApplicable, match="no exact-text source"):
        rig.reader.read()
    assert rig.kinds() == ["vmscreen"]


def test_forced_ocr_never_runs_vmscreen(tmp_path):
    rig = Rig(tmp_path, pwmcp=[ocr_ok("text")], engine="ocr")
    assert rig.reader.read().engine == "ocr"
    assert "vmscreen" not in rig.kinds()


def test_vmscreen_exit_codes_are_classified(tmp_path):
    qmp = Rig(tmp_path, vmscreen=[RunResult(5, "", "QMP socket closed")], engine="vmscreen")
    with pytest.raises(TextReadError, match="QMP problem"):
        qmp.reader.read()
    usage = Rig(tmp_path, vmscreen=[RunResult(2, "", "bad option")], engine="vmscreen")
    with pytest.raises(TextEngineError, match="rejected the arguments"):
        usage.reader.read()
    # exit 1 that is not a start-up failure: fatal until vmscreen has worked once, then skippable.
    first = Rig(tmp_path, vmscreen=[RunResult(1, "", "cannot write dump")], engine="vmscreen")
    with pytest.raises(TextEngineError, match="before it ever worked"):
        first.reader.read()
    later = Rig(tmp_path, vmscreen=[vm_ok("a"), RunResult(1, "", "cannot write dump")], engine="vmscreen")
    later.reader.read()
    with pytest.raises(TextReadError, match="cannot write dump"):
        later.reader.read()
    garbage = Rig(tmp_path, vmscreen=[RunResult(0, "not json", "")], engine="vmscreen")
    with pytest.raises(TextReadError, match="not JSON"):
        garbage.reader.read()


# --- OCR engine -------------------------------------------------------------------


def test_ocr_screendump_goes_to_the_fixed_name_and_the_file_is_removed_after_success(tmp_path):
    rig = Rig(tmp_path, pwmcp=[ocr_ok("login:")], engine="ocr")
    assert rig.reader.read().text == "login:"
    qmp, pwmcp = rig.calls
    assert qmp[:4] == (
        "qmp",
        tmp_path / "qmp.sock",
        "screendump",
        {"filename": "/state/.vm-lab-console-text.png", "format": "png"},
    )
    assert OCR_TEMP_FILENAME == ".vm-lab-console-text.png"
    assert pwmcp[1] == ["pwmcp", "ocr", "--image", str(tmp_path / OCR_TEMP_FILENAME), "--json"]
    assert rig.png_seen_by_pwmcp == [True]
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_ocr_file_is_removed_after_pwmcp_fails(tmp_path):
    rig = Rig(tmp_path, pwmcp=[RunResult(2, "", "OCR is not configured")], engine="ocr")
    with pytest.raises(TextEngineError):
        rig.reader.read()
    assert rig.png_seen_by_pwmcp == [True]
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_ocr_file_is_removed_after_the_screendump_call_fails(tmp_path):
    rig = Rig(tmp_path, engine="ocr")
    rig.qmp_error = RunnerError("QMP screendump failed")
    with pytest.raises(RunnerError):
        rig.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()
    assert "pwmcp" not in rig.kinds()


def test_ocr_removes_a_file_qmp_left_behind_even_when_the_call_raised_after_writing(tmp_path):
    rig = Rig(tmp_path, engine="ocr")

    def write_then_fail(*_args):
        (tmp_path / OCR_TEMP_FILENAME).write_bytes(b"partial")
        raise RunnerError("QMP died mid-dump")

    rig.reader.qmp_call = write_then_fail
    with pytest.raises(RunnerError):
        rig.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_ocr_refuses_an_existing_temp_file_and_does_not_delete_it(tmp_path, kind):
    existing = tmp_path / OCR_TEMP_FILENAME
    if kind == "file":
        existing.write_bytes(b"someone else's dump")
    else:
        existing.symlink_to(tmp_path / "nowhere")
    rig = Rig(tmp_path, pwmcp=[ocr_ok("never")], engine="ocr")
    with pytest.raises(TextReadError, match="refusing to overwrite existing OCR screendump"):
        rig.reader.read()
    assert existing.exists() or existing.is_symlink()
    if kind == "file":
        assert existing.read_bytes() == b"someone else's dump"
    assert rig.calls == []


def test_ocr_without_pwmcp_on_path_names_pwmcp_and_the_sidecar(tmp_path):
    rig = Rig(tmp_path, engine="ocr", have_pwmcp=False)
    with pytest.raises(TextEngineError) as info:
        rig.reader.read()
    message = str(info.value)
    assert "pwmcp" in message and "not on PATH" in message and "sidecar must be enabled" in message
    assert rig.calls == []


def test_auto_without_vmscreen_text_and_without_pwmcp_is_an_error(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_none()], have_pwmcp=False)
    with pytest.raises(TextEngineError, match="pwmcp"):
        rig.reader.read()


def test_ocr_failure_shows_pwmcps_own_message_and_the_sidecar_hint(tmp_path):
    rig = Rig(tmp_path, pwmcp=[RunResult(1, "", "pwmcp ocr failed: sidecar unreachable at http://x:8941")], engine="ocr")
    with pytest.raises(TextEngineError) as info:
        rig.reader.read()
    message = str(info.value)
    assert "sidecar unreachable at http://x:8941" in message
    assert "sidecar must be enabled" in message


def test_ocr_failure_after_a_success_is_a_skippable_read_error(tmp_path):
    rig = Rig(tmp_path, pwmcp=[ocr_ok("fine"), RunResult(1, "", "busy")], engine="ocr")
    rig.reader.read()
    with pytest.raises(TextReadError, match="busy"):
        rig.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_pwmcp_exit_2_is_immediate_even_after_ocr_has_worked(tmp_path):
    """Exit 2 means the sidecar is not configured: never a skippable poll."""
    rig = Rig(tmp_path, pwmcp=[ocr_ok("fine"), RunResult(2, "", "no OCR sidecar URL is known")], engine="ocr")
    rig.reader.read()
    with pytest.raises(TextEngineError, match="exit 2.*no OCR sidecar URL is known.*sidecar must be enabled"):
        rig.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_pwmcp_failing_on_the_very_first_poll_is_an_immediate_error_by_design(tmp_path):
    rig = Rig(tmp_path, pwmcp=[RunResult(1, "", "HTTP 429 busy")], engine="ocr")
    clock = FakeClock()
    with pytest.raises(TextEngineError, match="HTTP 429 busy"):
        vm_lab_text.wait_loop(make_spec("MENU"), rig.reader.read, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == []


def test_ocr_empty_or_missing_screendump_and_bad_json_are_skippable(tmp_path):
    empty = Rig(tmp_path, engine="ocr")
    empty.png_bytes = b""
    with pytest.raises(TextReadError, match="empty"):
        empty.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()
    missing = Rig(tmp_path, engine="ocr")
    missing.reader.qmp_call = lambda *_a: None  # QEMU reported success but wrote nothing
    with pytest.raises(TextReadError, match="did not create"):
        missing.reader.read()
    bad = Rig(tmp_path, pwmcp=[RunResult(0, "{}", "")], engine="ocr")
    with pytest.raises(TextReadError, match="no text field"):
        bad.reader.read()
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_engines_run_strictly_one_after_the_other(tmp_path):
    """The QMP screendump happens only after the vmscreen subprocess has returned."""
    rig = Rig(tmp_path, vmscreen=[vm_none()], pwmcp=[ocr_ok("x")])
    rig.reader.read()
    assert rig.kinds() == ["vmscreen", "qmp", "pwmcp"]


def test_default_runner_maps_subprocess_results_and_timeouts(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 3, stdout="out", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vm_lab_text.run_subprocess(["x", "y"], {"A": "1"}, 7.0)
    assert result == RunResult(3, "out", "err")
    assert captured["argv"] == ["x", "y"] and captured["env"] == {"A": "1"}
    assert captured["timeout"] == 7.0 and captured["stdin"] == subprocess.DEVNULL

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 7.0)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(TextReadError, match="timed out after 7s"):
        vm_lab_text.run_subprocess(["pwmcp", "ocr"], {}, 7.0)


# --- console text ---------------------------------------------------------------------


class StubReader:
    def __init__(self, *items):
        self.script = Script(*items)

    def read(self, budget=None):
        return self.script(budget)


def test_console_text_prints_text_and_one_stderr_line(capsys):
    assert vm_lab_text.run_text(StubReader(ScreenText("line1\nline2", "vmscreen", True)), as_json=False) == 0
    captured = capsys.readouterr()
    assert captured.out == "line1\nline2\n"
    assert captured.err == "vm-lab: engine=vmscreen exact=true\n"
    vm_lab_text.run_text(StubReader(ScreenText("p\n", "vmscreen", "partial")), as_json=False)
    captured = capsys.readouterr()
    assert captured.out == "p\n" and captured.err == "vm-lab: engine=vmscreen exact=partial\n"
    vm_lab_text.run_text(StubReader(ScreenText("shot", "ocr", None)), as_json=False)
    assert capsys.readouterr().err == "vm-lab: engine=ocr exact=n/a\n"


def test_console_text_json_is_one_object_on_stdout_and_nothing_on_stderr(capsys):
    vm_lab_text.run_text(StubReader(ScreenText("a\nb", "vmscreen", True)), as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"text": "a\nb", "engine": "vmscreen", "exact": True}
    assert captured.out.count("\n") == 1 and captured.err == ""
    vm_lab_text.run_text(StubReader(ScreenText("x", "ocr", None)), as_json=True)
    assert json.loads(capsys.readouterr().out) == {"text": "x", "engine": "ocr", "exact": None}


def test_console_text_with_a_forced_engine_that_does_not_apply_is_an_error():
    rig_reader = StubReader(TextNotApplicable("vmscreen: no exact-text source applies to the screen"))
    with pytest.raises(TextEngineError, match="no exact-text source"):
        vm_lab_text.run_text(rig_reader, as_json=False)


# --- wait loop ---------------------------------------------------------------------------


def test_match_on_the_first_poll_does_not_sleep(capsys):
    outcome, clock = wait(make_spec("GNU GRUB"), Script("GNU GRUB  version 2.12\nfoo"))
    assert (outcome.result, outcome.exit_code) == ("matched", 0)
    assert outcome.matched_text == "GNU GRUB  version 2.12"
    assert clock.sleeps == []


def test_match_on_a_later_poll_sleeps_the_interval_between_polls():
    script = Script("booting", "booting", "Welcome\nMENU: debian\nother")
    outcome, clock = wait(make_spec("MENU", interval=3.0), script)
    assert outcome.result == "matched" and outcome.matched_text == "MENU: debian"
    assert script.reads == 3 and clock.sleeps == [3.0, 3.0]
    assert outcome.elapsed_s == 6.0


def test_timeout_uses_the_fake_monotonic_clock_and_exits_3(capsys):
    script = Script("nothing here")
    outcome, clock = wait(make_spec("MENU", timeout=10.0, interval=2.0), script)
    assert (outcome.result, outcome.exit_code, outcome.elapsed_s) == ("timeout", 3, 10.0)
    assert script.reads == 6  # t = 0, 2, 4, 6, 8, 10: a last poll at the deadline
    assert outcome.last_text == "nothing here"


def test_the_last_sleep_is_clipped_to_the_deadline():
    script = Script("x")
    outcome, clock = wait(make_spec("MENU", timeout=10.0, interval=4.0), script)
    assert clock.sleeps == [4.0, 4.0, 2.0] and outcome.elapsed_s == 10.0 and script.reads == 4


def test_fail_pattern_stops_at_once_with_exit_4_and_any_of_several_counts():
    spec = make_spec("MENU", fail_pattern=["kernel panic", "^Oops"])
    outcome, clock = wait(spec, Script("booting", "all fine\nOops: bug"))
    assert (outcome.result, outcome.exit_code) == ("failed", 4)
    assert outcome.pattern == "^Oops" and outcome.matched_text == "Oops: bug"
    assert clock.sleeps == [2.0]


def test_match_beats_a_fail_pattern_on_the_same_screen():
    spec = make_spec("MENU", fail_pattern=["error"])
    outcome, _ = wait(spec, Script("MENU\nerror: something"))
    assert (outcome.result, outcome.exit_code) == ("matched", 0)


def test_fail_pattern_beats_the_loop_counter_and_match_beats_the_loop_counter():
    spec = make_spec("DONE", fail_pattern=["PANIC"], loop_pattern="GRUB", loop_count=2)
    outcome, _ = wait(spec, Script("GRUB", "x", "GRUB PANIC"))
    assert outcome.result == "failed"
    outcome, _ = wait(make_spec("DONE", loop_pattern="GRUB", loop_count=2), Script("GRUB", "x", "GRUB DONE"))
    assert outcome.result == "matched"


def test_loop_detection_counts_transitions_not_polls():
    spec = make_spec("NEVER", loop_pattern="GNU GRUB", loop_count=3, timeout=100.0)
    # Five polls with the menu up count once.
    outcome, _ = wait(make_spec("NEVER", loop_pattern="GNU GRUB", loop_count=3, timeout=8.0), Script("GNU GRUB"))
    assert (outcome.result, outcome.loop_count) == ("timeout", 1)
    script = Script(*(["GNU GRUB"] * 5), "kernel log", "GNU GRUB", "GNU GRUB", "reboot", "GNU GRUB")
    outcome, _ = wait(spec, script)
    assert (outcome.result, outcome.exit_code, outcome.loop_count) == ("boot-loop", 5, 3)
    assert script.reads == 10  # stops on the poll that shows the third appearance
    assert outcome.pattern == "GNU GRUB" and outcome.matched_text == "GNU GRUB"


def test_a_skipped_poll_does_not_reset_the_loop_state():
    spec = make_spec("NEVER", loop_pattern="GRUB", loop_count=2, timeout=100.0)
    script = Script("GRUB", TextReadError("qmp busy"), "GRUB", "off", "GRUB")
    outcome, _ = wait(spec, script)
    assert (outcome.result, outcome.loop_count) == ("boot-loop", 2)
    assert script.reads == 5, "the error poll between two sightings must not count as a transition"


def test_literal_treats_the_pattern_as_plain_text():
    outcome, _ = wait(make_spec("a.c(1)", literal=True), Script("abc(1)", "xx a.c(1) yy"))
    assert outcome.result == "matched" and outcome.matched_text == "xx a.c(1) yy"
    regex, _ = wait(make_spec("a.c", timeout=2.0), Script("abc"))
    assert regex.result == "matched"
    miss, _ = wait(make_spec("a.c", literal=True, timeout=2.0), Script("abc"))
    assert miss.result == "timeout"


def test_the_pattern_is_searched_with_multiline_anchors():
    outcome, _ = wait(make_spec("^Login:$"), Script("Debian\nLogin:\n"))
    assert outcome.result == "matched" and outcome.matched_text == "Login:"


def test_matching_lines_covers_every_line_a_multiline_match_touches():
    import re

    text = "one\ntwo\nthree\nfour"
    match = re.search(r"two\nthree", text, re.MULTILINE)
    assert vm_lab_text.matching_lines(text, match) == "two\nthree"
    match = re.search(r"e\n", text, re.MULTILINE)
    assert vm_lab_text.matching_lines(text, match) == "one"
    match = re.search(r"our", text, re.MULTILINE)
    assert vm_lab_text.matching_lines(text, match) == "four"


def test_skipped_polls_are_retried_and_ten_in_a_row_end_the_wait(capsys):
    spec = make_spec("MENU", timeout=1000.0)
    nine = [TextReadError(f"boom {n}") for n in range(9)]
    outcome, clock = wait(spec, Script(*nine, "MENU"))
    assert outcome.result == "matched" and len(clock.sleeps) == 9
    # A good read in between resets the counter.
    outcome, _ = wait(spec, Script(*nine, "no", *nine, "MENU"))
    assert outcome.result == "matched"
    outcome, clock = wait(spec, Script(*nine, TextReadError("last one")))
    assert (outcome.result, outcome.exit_code) == ("error", 1)
    assert outcome.error == "10 consecutive screen reads failed: last one"
    assert len(clock.sleeps) == 9
    assert vm_lab_text.report_outcome(spec, outcome) == 1
    assert capsys.readouterr().err == "vm-lab: 10 consecutive screen reads failed: last one\n"


def test_every_tolerated_error_kind_skips_a_poll():
    spec = make_spec("MENU")
    for error in (RunnerError("QMP down"), OSError("socket"), ValueError("bad json"), TextReadError("png")):
        outcome, _ = wait(spec, Script(error, "MENU"))
        assert outcome.result == "matched", error


def test_an_engine_that_is_unavailable_from_the_start_is_an_immediate_error():
    script = Script(TextEngineError("vmscreen is not available: no module"), "MENU")
    clock = FakeClock()
    with pytest.raises(TextEngineError):
        vm_lab_text.wait_loop(make_spec("MENU"), script, clock=clock, sleep=clock.sleep)
    assert script.reads == 1 and clock.sleeps == []


def test_forced_vmscreen_on_an_unreadable_screen_keeps_polling_until_the_timeout():
    script = Script(TextNotApplicable("graphical"), TextNotApplicable("graphical"), "MENU")
    outcome, _ = wait(make_spec("MENU"), script)
    assert outcome.result == "matched"
    outcome, _ = wait(make_spec("MENU", timeout=4.0), Script(TextNotApplicable("graphical")))
    assert (outcome.result, outcome.exit_code) == ("timeout", 3)
    assert outcome.error == "graphical"


def test_the_wait_loop_hands_each_read_the_time_left_to_the_deadline():
    script = Script("x")
    wait(make_spec("MENU", timeout=10.0, interval=2.0), script)
    assert script.budgets == [10.0, 8.0, 6.0, 4.0, 2.0, 0.0]


def test_every_external_call_is_bounded_by_the_remaining_time_but_at_least_one_second(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_none()] * 6, pwmcp=[ocr_ok("x")] * 6)
    clock = FakeClock()
    outcome = vm_lab_text.wait_loop(
        make_spec("MENU", timeout=10.0, interval=2.0), rig.reader.read, clock=clock, sleep=clock.sleep
    )
    assert outcome.result == "timeout"
    expected = [10.0, 8.0, 6.0, 4.0, 2.0, 1.0]  # the last poll has no time left: the 1 s floor
    assert [call[3] for call in rig.calls if call[0] == "vmscreen"] == expected
    assert [call[4] for call in rig.calls if call[0] == "qmp"] == expected
    assert [call[3] for call in rig.calls if call[0] == "pwmcp"] == expected
    assert [probe[2] for probe in rig.probes] == [10.0]


def test_long_deadlines_and_console_text_keep_the_fixed_defaults(tmp_path):
    long = Rig(tmp_path, vmscreen=[vm_none()], pwmcp=[ocr_ok("MENU")])
    clock = FakeClock()
    vm_lab_text.wait_loop(make_spec("MENU", timeout=1000.0), long.reader.read, clock=clock, sleep=clock.sleep)
    once = Rig(tmp_path, vmscreen=[vm_none()], pwmcp=[ocr_ok("x")])
    once.reader.read()
    for rig in (long, once):
        assert [call[3] for call in rig.calls if call[0] == "vmscreen"] == [vm_lab_text.VMSCREEN_TIMEOUT_SECONDS]
        assert [call[4] for call in rig.calls if call[0] == "qmp"] == [vm_lab_text.QMP_TIMEOUT_SECONDS]
        assert [call[3] for call in rig.calls if call[0] == "pwmcp"] == [vm_lab_text.PWMCP_TIMEOUT_SECONDS]
        assert [probe[2] for probe in rig.probes] == [vm_lab_text.VMSCREEN_PROBE_TIMEOUT_SECONDS]


@pytest.mark.parametrize(("deadline", "given"), [(10.0, 10.0), (3.0, 3.0), (0.4, 1.0)])
def test_a_subprocess_that_hits_its_timeout_ends_the_wait_with_exit_3_at_the_deadline(tmp_path, deadline, given):
    clock = FakeClock()
    seen = []

    def hang(_argv, timeout):
        seen.append(timeout)
        clock.now += timeout  # the process ran until its timeout
        raise TextReadError(f"pwmcp timed out after {timeout:g}s")

    rig = Rig(tmp_path, pwmcp=[hang] * 5, engine="ocr")
    spec = make_spec("MENU", timeout=deadline, interval=2.0, json=True)
    outcome = vm_lab_text.wait_loop(spec, rig.reader.read, clock=clock, sleep=clock.sleep)
    assert (outcome.result, outcome.exit_code) == ("timeout", 3)
    assert seen == [given] and clock.sleeps == []
    assert outcome.elapsed_s == given
    assert "timed out" in outcome.error
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


def test_not_applicable_polls_leave_the_loop_state_unchanged():
    """Only a successfully read screen moves the loop state: unknown is neither seen nor gone."""
    spec = make_spec("NEVER", loop_pattern="GRUB", loop_count=2, timeout=100.0)
    not_applicable = TextNotApplicable("unverified")
    script = Script("GRUB", not_applicable, not_applicable, not_applicable, "GRUB", "away", "GRUB")
    outcome, _ = wait(spec, script)
    # GRUB was on screen before and after the three unknown polls: still one sighting, so the
    # second one is the GRUB after "away" (read 7), not the one directly after the unknown polls.
    assert (outcome.result, outcome.loop_count) == ("boot-loop", 2)
    assert script.reads == 7
    stays = Script("GRUB", not_applicable, not_applicable, not_applicable, "GRUB")
    outcome, _ = wait(make_spec("NEVER", loop_pattern="GRUB", loop_count=2, timeout=10.0), stays)
    assert (outcome.result, outcome.loop_count) == ("timeout", 1)


def test_not_applicable_resets_the_consecutive_failure_count():
    spec = make_spec("MENU", timeout=1000.0)
    nine = [TextReadError("boom")] * 9
    outcome, _ = wait(spec, Script(*nine, TextNotApplicable("graphical"), *nine, "MENU"))
    assert outcome.result == "matched"


# --- usage and argument bounds --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"pattern": "("}, "PATTERN is not a valid regular expression"),
        ({"pattern": ""}, "PATTERN must not be empty"),
        ({"fail_pattern": ["ok", "["]}, "--fail-pattern is not a valid regular expression"),
        ({"loop_pattern": "(?P<"}, "--loop-pattern is not a valid regular expression"),
        ({"loop_count": 3}, "--loop-count needs --loop-pattern"),
        ({"loop_pattern": "x", "loop_count": 1}, "--loop-count must be an integer >= 2"),
        ({"timeout": 0}, "--timeout must be a finite number greater than zero"),
        ({"timeout": -1.0}, "--timeout must be a finite number greater than zero"),
        ({"timeout": math.nan}, "--timeout must be a finite number greater than zero"),
        ({"timeout": math.inf}, "--timeout must be a finite number greater than zero"),
        ({"interval": 0}, "--interval must be a finite number greater than zero"),
    ],
)
def test_wait_spec_rejects_invalid_input(overrides, message):
    with pytest.raises(TextUsageError, match=message.replace("(", r"\(")):
        make_spec(**overrides)


def test_literal_pattern_that_would_be_an_invalid_regex_is_accepted():
    assert make_spec("(", literal=True).pattern.search("a(b")


def test_wait_spec_defaults():
    spec = make_spec(loop_pattern="GRUB")
    assert (spec.interval, spec.loop_count, spec.timeout) == (2.0, 3, 10.0)
    assert make_spec().loop_pattern is None


def parse(*arguments):
    return vm_lab.build_parser(GENERIC_PROFILE).parse_args(
        ["console", *arguments]
    )


WAIT = ["wait-for-text", "--name", "deb1", "--state-dir", "x"]


@pytest.mark.parametrize(
    "arguments",
    [
        WAIT + ["MENU"],  # --timeout is required
        WAIT + ["MENU", "--timeout", "0"],
        WAIT + ["MENU", "--timeout", "-3"],
        WAIT + ["MENU", "--timeout", "nan"],
        WAIT + ["MENU", "--timeout", "5", "--interval", "0"],
        WAIT + ["MENU", "--timeout", "5", "--loop-pattern", "x", "--loop-count", "1"],
        WAIT + ["MENU", "--timeout", "5", "--loop-pattern", "x", "--loop-count", "two"],
        WAIT + ["MENU", "--timeout", "5", "--engine", "tesseract"],
        ["wait-for-text", "--name", "deb1", "MENU", "--timeout", "5"],  # --state-dir is required
        ["text", "--name", "deb1"],
        ["text", "--name", "deb1", "--state-dir", "x", "--engine", "other"],
    ],
)
def test_the_parser_refuses_bad_arguments_with_exit_2(arguments, capsys):
    with pytest.raises(SystemExit) as info:
        parse(*arguments)
    assert info.value.code == 2


def test_parser_defaults_and_repeatable_options():
    args = parse(*WAIT, "MENU", "--timeout", "30", "--fail-pattern", "a", "--fail-pattern", "b",
                 "--loop-pattern", "GNU GRUB", "--loop-count", "4", "--literal", "--json")
    assert args.pattern == "MENU" and args.timeout == 30.0 and args.interval == 2.0
    assert args.fail_pattern == ["a", "b"] and args.loop_pattern == "GNU GRUB" and args.loop_count == 4
    assert args.engine == "auto" and args.literal is True and args.json is True
    assert args.func is vm_lab.cmd_console_wait_for_text
    text = parse("text", "--name", "deb1", "--state-dir", "x")
    assert (text.engine, text.json) == ("auto", False) and text.func is vm_lab.cmd_console_text


def labelled_guest(state_dir: Path, *, running: bool = True, profile=GENERIC_PROFILE) -> dict:
    return {
        "Config": {"Labels": {
            profile.vm_label: profile.label_value,
            profile.state_label: vm_lab.state_identity(state_dir),
        }},
        "State": {"Running": running},
    }


def forbid_everything(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("something connected or started")

    monkeypatch.setattr(vm_lab, "docker_inspect", boom)
    monkeypatch.setattr(vm_lab, "console_screen_reader", boom)
    monkeypatch.setattr(vm_lab, "qmp_command", boom)


@pytest.mark.parametrize(
    "extra",
    [
        ["(unclosed"],
        ["ok", "--fail-pattern", "["],
        ["ok", "--loop-pattern", "(?P<"],
        ["ok", "--loop-count", "3"],
    ],
)
def test_invalid_input_is_exit_2_before_anything_connects(extra, monkeypatch, capsys, tmp_path):
    forbid_everything(monkeypatch)
    args = parse(*WAIT[:-1], str(tmp_path), "--timeout", "5", *extra)
    assert vm_lab.cmd_console_wait_for_text(GENERIC_PROFILE, args) == 2
    err = capsys.readouterr().err
    assert err.startswith("vm-lab: ")
    assert "regular expression" in err or "--loop-count needs --loop-pattern" in err


def test_the_new_verbs_refuse_what_the_other_console_verbs_refuse(monkeypatch, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    started = []
    monkeypatch.setattr(vm_lab, "console_screen_reader", lambda *_a: started.append(1))
    cases = [
        ({}, "is not a managed vm-lab TCG VM"),
        (labelled_guest(other), "does not belong to state directory"),
        (labelled_guest(tmp_path, running=False), "is not running"),
    ]
    for guest, message in cases:
        monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name, guest=guest: guest)
        wait_args = parse(*WAIT[:-1], str(tmp_path), "MENU", "--timeout", "5")
        text_args = parse("text", "--name", "deb1", "--state-dir", str(tmp_path))
        for run, verb_args in ((vm_lab.cmd_console_wait_for_text, wait_args), (vm_lab.cmd_console_text, text_args)):
            assert run(GENERIC_PROFILE, verb_args) == 1
            err = capsys.readouterr().err
            assert err.startswith("vm-lab: ") and message in err and "Traceback" not in err
    assert started == []


def test_the_verbs_run_the_reader_for_a_valid_guest_and_report_engine_errors_as_exit_1(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    engines = []

    def reader(profile, state_dir, engine):
        engines.append((profile, state_dir, engine))
        return StubReader(ScreenText("Boot menu", "vmscreen", True))

    monkeypatch.setattr(vm_lab, "console_screen_reader", reader)
    args = parse("text", "--name", "deb1", "--state-dir", str(tmp_path), "--engine", "vmscreen")
    assert vm_lab.cmd_console_text(GENERIC_PROFILE, args) == 0
    assert engines == [(GENERIC_PROFILE, tmp_path.resolve(), "vmscreen")]
    assert capsys.readouterr().out == "Boot menu\n"
    wait_args = parse(*WAIT[:-1], str(tmp_path), "Boot", "--timeout", "5", "--engine", "ocr")
    assert vm_lab.cmd_console_wait_for_text(GENERIC_PROFILE, wait_args) == 0
    assert engines[-1][2] == "ocr" and capsys.readouterr().out == "Boot menu\n"

    monkeypatch.setattr(
        vm_lab, "console_screen_reader", lambda *_a: StubReader(TextEngineError("vmscreen is not available: x"))
    )
    capsys.readouterr()
    assert vm_lab.cmd_console_wait_for_text(GENERIC_PROFILE, wait_args) == 1
    assert capsys.readouterr().err == "vm-lab: vmscreen is not available: x\n"
    assert vm_lab.cmd_console_text(GENERIC_PROFILE, args) == 1
    assert capsys.readouterr().err == "vm-lab: vmscreen is not available: x\n"


# --- every read error is one `vm-lab:` line and exit 1, never a traceback (through main) ----------------


def run_main(front, monkeypatch, tmp_path, rig_kwargs, *verb_args, profile=GENERIC_PROFILE):
    rig = Rig(tmp_path, **rig_kwargs)
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path, profile=profile))
    monkeypatch.setattr(vm_lab, "console_screen_reader", lambda *_a: rig.reader)
    argv = ["console", *verb_args, "--name", "deb1", "--state-dir", str(tmp_path)]
    return (front(argv) if callable(front) else vm_lab.main(GENERIC_PROFILE, argv)), rig


READ_ERROR_CASES = [
    ("vmscreen-exit-5", {"vmscreen": [RunResult(5, "", "QMP socket closed")], "engine": "vmscreen"}, "QMP problem"),
    ("bad-pwmcp-json", {"pwmcp": [RunResult(0, "nope", "")], "engine": "ocr"}, "not JSON"),
    ("vmscreen-timeout", {"vmscreen": [TextReadError("vmscreen timed out after 90s")], "engine": "vmscreen"}, "timed out"),
]


@pytest.mark.parametrize("front_name", ["generic", "truenas"])
@pytest.mark.parametrize(("label", "rig_kwargs", "message"), READ_ERROR_CASES)
@pytest.mark.parametrize("as_json", [False, True])
def test_console_text_read_errors_are_one_line_and_exit_1(
    front_name, label, rig_kwargs, message, as_json, monkeypatch, tmp_path, capsys
):
    front = load_truenas_front().main if front_name == "truenas" else None
    profile = load_truenas_front().PROFILE if front_name == "truenas" else GENERIC_PROFILE
    extra = ["--json"] if as_json else []
    code, _rig = run_main(front, monkeypatch, tmp_path, rig_kwargs, "text", *extra, profile=profile)
    captured = capsys.readouterr()
    assert code == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("vm-lab: ") and message in lines[0]
    assert "Traceback" not in captured.err + captured.out
    if as_json:
        data = json.loads(captured.out)
        assert data == {"result": "error", "error": lines[0][len("vm-lab: "):]}
    else:
        assert captured.out == ""
    assert not (tmp_path / OCR_TEMP_FILENAME).exists()


@pytest.mark.parametrize("front_name", ["generic", "truenas"])
def test_console_text_with_a_stale_ocr_temp_file_is_one_line_and_exit_1(front_name, monkeypatch, tmp_path, capsys):
    (tmp_path / OCR_TEMP_FILENAME).write_bytes(b"stale")
    front = load_truenas_front().main if front_name == "truenas" else None
    profile = load_truenas_front().PROFILE if front_name == "truenas" else GENERIC_PROFILE
    code, _ = run_main(front, monkeypatch, tmp_path, {"engine": "ocr"}, "text", profile=profile)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("vm-lab: refusing to overwrite existing OCR screendump")
    assert len(captured.err.splitlines()) == 1 and "Traceback" not in captured.err
    assert (tmp_path / OCR_TEMP_FILENAME).read_bytes() == b"stale"


def test_wait_for_text_errors_are_one_line_and_exit_1_and_json_gets_an_error_object(monkeypatch, tmp_path, capsys):
    code, _ = run_main(
        None, monkeypatch, tmp_path, {"engine": "ocr", "have_pwmcp": False}, "wait-for-text", "MENU", "--timeout", "5", "--json"
    )
    captured = capsys.readouterr()
    assert code == 1 and captured.err.startswith("vm-lab: OCR needs the pwmcp command")
    assert json.loads(captured.out)["result"] == "error" and "pwmcp" in json.loads(captured.out)["error"]
    code, _ = run_main(
        None, monkeypatch, tmp_path, {"engine": "ocr", "have_pwmcp": False}, "wait-for-text", "MENU", "--timeout", "5"
    )
    captured = capsys.readouterr()
    assert code == 1 and captured.out == "" and len(captured.err.splitlines()) == 1


def test_ten_failed_polls_with_json_print_an_error_object_too(capsys):
    spec = make_spec("MENU", json=True, timeout=1000.0)
    code, out, err = run_wait(spec, Script(TextReadError("down")), capsys)
    assert code == 1
    assert json.loads(out) == {"result": "error", "error": "10 consecutive screen reads failed: down"}
    assert err == "vm-lab: 10 consecutive screen reads failed: down\n"


def test_a_qmp_error_in_a_main_run_is_one_line_too(monkeypatch, tmp_path, capsys):
    rig = Rig(tmp_path, engine="ocr")
    rig.qmp_error = RunnerError("QMP screendump failed: {'class': 'GenericError'}")
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    monkeypatch.setattr(vm_lab, "console_screen_reader", lambda *_a: rig.reader)
    code = vm_lab.main(GENERIC_PROFILE, ["console", "text", "--name", "deb1", "--state-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("vm-lab: QMP screendump failed") and "Traceback" not in err


def test_the_real_reader_is_wired_to_the_launchers_qmp_path_and_short_socket_helper(monkeypatch, tmp_path):
    reader = vm_lab.console_screen_reader(GENERIC_PROFILE, tmp_path.resolve(), "auto")
    assert reader.qmp_socket == tmp_path.resolve() / "qmp.sock"
    assert reader.qmp_call is vm_lab.qmp_command
    assert reader.state_dir == tmp_path.resolve() and reader.engine == "auto"
    assert reader.vmscreen_qmp_path() == tmp_path.resolve() / "qmp.sock"
    # A state directory whose socket path is too long gets the same short alias the other verbs use.
    deep = tmp_path.resolve()
    while len(os.fsencode(deep / "qmp.sock")) <= vm_lab.QMP_SOCKET_PATH_MAX_BYTES:
        deep = deep / ("d" * 40)
    deep.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="vmt-") as alias_dir:  # short on purpose: the alias must fit
        monkeypatch.setattr(vm_lab.tempfile, "gettempdir", lambda: alias_dir)
        long_reader = vm_lab.console_screen_reader(GENERIC_PROFILE, deep, "auto")
        alias = long_reader.vmscreen_qmp_path()
        assert len(os.fsencode(alias)) <= vm_lab.QMP_SOCKET_PATH_MAX_BYTES
        assert alias.is_symlink() and os.readlink(alias) == str(deep / "qmp.sock")
        assert alias == vm_lab.unix_socket_client_path(GENERIC_PROFILE, deep, "qmp.sock", "qmp")


def load_truenas_front():
    spec =importlib.util.spec_from_file_location("tcg_vm_text_under_test", TRUENAS_FRONT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_truenas_front_gains_both_verbs_through_the_shared_parser():
    front = load_truenas_front()
    state = ["--name", "tns-a", "--state-dir", "x"]
    text = front.build_parser().parse_args(["console", "text", *state])
    assert text.func is vm_lab.cmd_console_text and text.engine == "auto"
    waited = front.build_parser().parse_args(["console", "wait-for-text", *state, "Login", "--timeout", "9"])
    assert waited.func is vm_lab.cmd_console_wait_for_text and waited.timeout == 9.0


# --- JSON and plain output ---------------------------------------------------------------------------------------


def run_wait(spec, script, capsys):
    clock = FakeClock()
    code = vm_lab_text.report_outcome(spec, vm_lab_text.wait_loop(spec, script, clock=clock, sleep=clock.sleep))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


JSON_KEYS = {"result", "engine", "exact", "elapsed_s", "pattern", "matched_text", "loop_count", "last_text"}


def test_json_object_for_a_match(capsys):
    code, out, err = run_wait(make_spec("MENU", json=True), Script(ScreenText("a\nMENU x\nb", "vmscreen", "partial")), capsys)
    assert code == 0 and err == ""
    data = json.loads(out)
    assert set(data) == JSON_KEYS and out.count("\n") == 1
    assert data["result"] == "matched" and data["engine"] == "vmscreen" and data["exact"] == "partial"
    assert data["pattern"] == "MENU" and data["matched_text"] == "MENU x" and data["loop_count"] == 0
    assert data["last_text"] == "a\nMENU x\nb" and isinstance(data["elapsed_s"], float)


def test_json_object_for_a_timeout(capsys):
    code, out, err = run_wait(make_spec("MENU", json=True, timeout=2.0), Script(ScreenText("busy", "ocr", None)), capsys)
    data = json.loads(out)
    assert code == 3 and set(data) == JSON_KEYS
    assert data["result"] == "timeout" and data["engine"] == "ocr" and data["exact"] is None
    assert data["matched_text"] is None and data["last_text"] == "busy" and data["elapsed_s"] == 2.0
    assert err.startswith("vm-lab: timeout") and "busy" not in err


def test_json_object_for_a_fail_pattern(capsys):
    spec = make_spec("MENU", json=True, fail_pattern=["Kernel panic"])
    code, out, err = run_wait(spec, Script("x\nKernel panic - not syncing\ny"), capsys)
    data = json.loads(out)
    assert code == 4 and data["result"] == "failed"
    assert data["pattern"] == "Kernel panic" and data["matched_text"] == "Kernel panic - not syncing"
    assert err.startswith("vm-lab: fail pattern")


def test_json_object_for_a_boot_loop(capsys):
    spec = make_spec("NEVER", json=True, loop_pattern="GNU GRUB", loop_count=2, timeout=50.0)
    code, out, err = run_wait(spec, Script("GNU GRUB", "dark", "GNU GRUB 2"), capsys)
    data = json.loads(out)
    assert code == 5 and data["result"] == "boot-loop" and data["loop_count"] == 2
    assert data["pattern"] == "GNU GRUB" and data["matched_text"] == "GNU GRUB 2"
    assert isinstance(data["loop_count"], int) and isinstance(data["engine"], str)
    assert err.startswith("vm-lab: boot loop")


def test_plain_output_prints_the_matching_line_or_the_reason_and_the_last_screen(capsys):
    code, out, err = run_wait(make_spec("MENU"), Script("x\nMENU entry\ny"), capsys)
    assert (code, out, err) == (0, "MENU entry\n", "")
    code, out, err = run_wait(make_spec("MENU", timeout=2.0), Script("the last screen"), capsys)
    assert code == 3 and out == ""
    lines = err.splitlines()
    assert lines[0].startswith("vm-lab: timeout after 2s") and lines[1] == "the last screen"
    code, out, err = run_wait(make_spec("MENU", fail_pattern=["bad"]), Script("is bad"), capsys)
    assert code == 4 and out == "" and err.splitlines()[0].startswith("vm-lab: fail pattern 'bad'")
    assert err.splitlines()[1] == "is bad"
    spec = make_spec("NEVER", loop_pattern="GRUB", loop_count=2, timeout=50.0)
    code, out, err = run_wait(spec, Script("GRUB", "x", "GRUB"), capsys)
    assert code == 5 and out == "" and err.splitlines()[0].startswith("vm-lab: boot loop")
    assert err.splitlines()[1] == "GRUB"


def test_exit_codes_are_documented_module_constants():
    assert (
        vm_lab_text.EXIT_MATCHED, vm_lab_text.EXIT_ERROR, vm_lab_text.EXIT_USAGE,
        vm_lab_text.EXIT_TIMEOUT, vm_lab_text.EXIT_FAIL_PATTERN, vm_lab_text.EXIT_BOOT_LOOP,
    ) == (0, 1, 2, 3, 4, 5)


def test_help_text_of_both_verbs_names_the_exit_codes(capsys):
    for verb, expected in (
        ("wait-for-text", "0 matched, 1 error, 2 usage, 3 timeout, 4 a --fail-pattern matched, 5 boot loop detected"),
        ("text", "0 ok, 1 error"),
    ):
        with pytest.raises(SystemExit):
            parse(verb, "--help")
        assert expected in " ".join(capsys.readouterr().out.split())


def test_main_returns_the_wait_exit_code_unchanged(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    monkeypatch.setattr(vm_lab, "console_screen_reader", lambda *_a: StubReader("never matches"))
    ticks = iter(range(0, 1000, 2))
    monkeypatch.setattr(
        vm_lab_text,
        "time",
        SimpleNamespace(sleep=lambda _s: None, monotonic=lambda: float(next(ticks))),
    )
    code = vm_lab.main(
        GENERIC_PROFILE,
        ["console", "wait-for-text", "--name", "deb1", "--state-dir", str(tmp_path), "MENU", "--timeout", "5"],
    )
    assert code == 3
    assert vm_lab.main(
        GENERIC_PROFILE,
        ["console", "wait-for-text", "--name", "deb1", "--state-dir", str(tmp_path), "(", "--timeout", "5"],
    ) == 2

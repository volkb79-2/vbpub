"""The serial reader engine, `console mark` and `--since-mark` (VML-04), with fakes only.

The shapes of the fakes were checked against a real UEFI guest on 2026-11-17: `vmscreen mark --json`
prints ``{"ok": true, "serial_log", "offset", "mark"}``, `vmscreen text --source serial --json` needs
``--qmp`` even for the serial source, and the token is six dot-separated fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vm_lab import core as vm_lab
from vm_lab import text as vm_lab_text
from test_vm_lab_text import (  # noqa: F401  (fakes shared with the other text tests)
    SURFACE,
    FakeClock,
    Rig,
    Script,
    StubReader,
    labelled_guest,
    make_spec,
    ocr_ok,
    vm_none,
    vm_ok,
)
from vm_lab import GENERIC_PROFILE
from vm_lab.text import RunResult, ScreenText, TextEngineError, TextNotApplicable, TextUsageError

MARK = "240.278.64.39434cc16c6da873.64.4c3d433ba88b74a6"
MARK2 = "999.278.64.39434cc16c6da873.64.4c3d433ba88b74a6"


def serial_ok(text: str, exact="stream") -> RunResult:
    return RunResult(0, json.dumps({"source": "serial", "exact": exact, "text": text}), "")


def mark_ok(token: str = MARK) -> RunResult:
    return RunResult(0, json.dumps({"ok": True, "serial_log": "serial.log", "offset": 240, "mark": token}), "")


def with_log(tmp_path: Path, *, started_with_serial_console: bool = True, **kwargs) -> Rig:
    """The real state: QEMU always writes serial.log; runner.json says how the guest was started."""
    (tmp_path / "serial.log").write_text("boot\n")
    if started_with_serial_console:
        (tmp_path / "runner.json").write_text(json.dumps({"schema": 1, "serial_console": True}))
    return Rig(tmp_path, **kwargs)


def serial_calls(rig: Rig) -> list[list[str]]:
    return [call[1] for call in rig.calls if call[0] == "vmscreen"]


# --- the engine ---------------------------------------------------------------------------------


def test_the_serial_engine_runs_vmscreen_text_with_the_serial_log(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok("localhost login:")], vmscreen_on_path=True)
    assert rig.reader.read() == ScreenText("localhost login:", "serial", "stream")
    assert serial_calls(rig) == [[
        "/opt/bin/vmscreen", "text", "--source", "serial",
        "--serial-log", str(tmp_path / "serial.log"), "--json",
        "--qmp", str(tmp_path / "short-qmp.sock"),
    ]]


def test_a_mark_is_passed_to_the_serial_read(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok("x")], vmscreen_on_path=True)
    rig.reader.since_mark = MARK
    rig.reader.read()
    assert serial_calls(rig)[0][-2:] == ["--mark", MARK]


def test_auto_prefers_serial_over_vga_and_ocr(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok("from serial")])
    assert rig.reader.read().engine == "serial"
    assert rig.kinds() == ["vmscreen"] and "vga" not in serial_calls(rig)[0]


def test_auto_without_a_serial_log_goes_straight_to_vga(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB")])
    assert rig.reader.read().engine == "vmscreen"
    assert "serial" not in serial_calls(rig)[0]


@pytest.mark.parametrize(
    "record",
    [None, {"schema": 1}, {"schema": 1, "serial_console": False}, {"serial_console": "true"}, "not json"],
    ids=["no-runner-json", "no-key", "false", "not-a-bool", "unreadable"],
)
def test_auto_ignores_a_serial_log_of_a_guest_not_started_with_serial_console(tmp_path, record):
    # The real state: QEMU writes serial.log for EVERY guest (`-serial file:`), and a guest that
    # prints to ttyS0 must not shadow the VGA text buffer or OCR.
    (tmp_path / "serial.log").write_text("SeaBIOS\nkernel: lots of text\n")
    if record is not None:
        (tmp_path / "runner.json").write_text(record if isinstance(record, str) else json.dumps(record))
    rig = Rig(tmp_path, vmscreen=[vm_ok("GNU GRUB menu")])
    shot = rig.reader.read()
    assert shot.engine == "vmscreen" and shot.text == "GNU GRUB menu"
    assert all("serial" not in argv for argv in serial_calls(rig))
    rig = Rig(tmp_path, vmscreen=[vm_none()], pwmcp=[ocr_ok("only OCR sees this")])
    assert rig.reader.read().engine == "ocr"


def test_forced_serial_is_an_explicit_choice_that_works_without_the_record(tmp_path):
    rig = with_log(tmp_path, started_with_serial_console=False, engine="serial", vmscreen=[serial_ok("kernel text")])
    assert rig.reader.read() == ScreenText("kernel text", "serial", "stream")


def test_a_wait_in_auto_does_not_take_an_implicit_mark_without_the_record(tmp_path):
    rig = with_log(tmp_path, started_with_serial_console=False, vmscreen=[vm_ok("MENU")])
    rig.reader._implicit_mark = True
    rig.reader.read()
    assert all("mark" not in argv[:6] for argv in serial_calls(rig))


def test_the_record_is_read_from_runner_json(tmp_path):
    assert vm_lab_text.recorded_serial_console(tmp_path) is False
    (tmp_path / "runner.json").write_text(json.dumps({"serial_console": True}))
    assert vm_lab_text.recorded_serial_console(tmp_path) is True


def test_an_empty_serial_result_does_not_apply_yet_and_auto_falls_through(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok(""), vm_ok("GNU GRUB")])
    assert rig.reader.read().engine == "vmscreen"
    assert [argv[argv.index("--source") + 1] for argv in serial_calls(rig)] == ["serial", "vga"]
    rig = with_log(tmp_path, vmscreen=[serial_ok("  \n"), vm_none()], pwmcp=[ocr_ok("from ocr")])
    assert rig.reader.read().engine == "ocr"


def test_an_empty_serial_log_with_no_other_engine_is_a_quiet_read_not_an_error(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok(""), vm_none()], have_pwmcp=False)
    shot = rig.reader.read()
    assert shot == ScreenText("", "serial", "stream")
    assert len(rig.notes) == 1 and "serial log has no text yet" in rig.notes[0]
    rig.vmscreen += [serial_ok(""), vm_none()]
    rig.reader.read()
    assert len(rig.notes) == 1  # said once


def test_a_wait_on_a_quiet_serial_log_times_out_with_exit_3_not_an_error(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok(""), vm_none()] * 10, have_pwmcp=False)
    clock = FakeClock()
    out = vm_lab_text.wait_loop(make_spec("NEVER", timeout=4.0), rig.reader.read, clock=clock, sleep=clock.sleep)
    assert out.result == "timeout" and out.exit_code == 3 and out.engine == "serial"


def test_forced_serial_without_a_log_is_an_error_naming_the_log(tmp_path):
    rig = Rig(tmp_path, engine="serial")
    with pytest.raises(TextEngineError, match="no serial log at .*serial.log"):
        rig.reader.read()
    assert rig.kinds() == []


# What `vmscreen text --source serial --json --mark <stale>` really printed (exit 4) when serial.log
# was truncated after the mark was taken (live run, 2026-11-17).
STALE_STDOUT = json.dumps({
    "ok": False,
    "error": "no exact-text source applies to this screen: serial: serial log was truncated or replaced since "
             "the mark (inode, first bytes or the bytes before the offset changed): marks no longer apply; "
             "none: graphical framebuffer or no applicable source: exact text is unavailable (use screen + OCR)",
    "exit_code": 4,
    "exact": False,
    "tried": [
        {"source": "serial", "ok": False, "reason": "serial log was truncated or replaced since the mark "
         "(inode, first bytes or the bytes before the offset changed): marks no longer apply"},
        {"source": "none", "ok": False,
         "reason": "graphical framebuffer or no applicable source: exact text is unavailable (use screen + OCR)"},
    ],
})
STALE = RunResult(4, STALE_STDOUT, "[ERROR] no exact-text source applies to this screen: serial: serial log was truncated or replaced")
NO_SOURCE = RunResult(4, json.dumps({"ok": False, "error": "no exact-text source applies to this screen: none: graphical "
                                     "framebuffer", "exit_code": 4}), "[ERROR] no exact-text source applies")


@pytest.mark.parametrize("engine", ["auto", "serial"])
def test_a_stale_mark_is_an_error_in_auto_and_forced_and_never_falls_through(tmp_path, engine):
    rig = with_log(tmp_path, engine=engine, vmscreen=[STALE, vm_ok("would be a fall-through")], pwmcp=[ocr_ok("o")])
    rig.reader.since_mark = MARK
    with pytest.raises(TextEngineError, match="serial log was truncated or replaced since the mark was taken"):
        rig.reader.read()
    assert len(serial_calls(rig)) == 1 and "pwmcp" not in rig.kinds()


def test_a_stale_mark_ends_a_wait_with_an_error_not_a_timeout(tmp_path):
    rig = with_log(tmp_path, engine="serial", vmscreen=[STALE])
    rig.reader.since_mark = MARK
    clock = FakeClock()
    with pytest.raises(TextEngineError, match="truncated or replaced"):
        vm_lab_text.wait_loop(make_spec("NEVER", timeout=30.0), rig.reader.read, clock=clock, sleep=clock.sleep)


def test_a_stale_implicit_mark_in_a_wait_is_an_error(tmp_path):
    rig = with_log(tmp_path, vmscreen=[mark_ok(), serial_ok("a"), STALE])
    rig.reader._implicit_mark = True
    assert rig.reader.read().text == "a"
    with pytest.raises(TextEngineError, match="truncated or replaced"):
        rig.reader.read()


def test_the_other_exit_4_cause_with_a_mark_is_still_not_applicable(tmp_path):
    rig = with_log(tmp_path, engine="serial", vmscreen=[NO_SOURCE])
    rig.reader.since_mark = MARK
    with pytest.raises(TextNotApplicable):
        rig.reader.read()


def test_exit_4_without_a_mark_is_never_a_stale_mark_error(tmp_path):
    rig = with_log(tmp_path, engine="serial", vmscreen=[STALE])
    with pytest.raises(TextNotApplicable):
        rig.reader.read()


def test_a_quiet_serial_log_with_an_explicit_mark_gives_no_ignored_note(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok(""), vm_ok("a")])
    rig.reader.since_mark = MARK
    rig.reader._explicit_mark = True
    rig.reader.read()
    assert rig.notes == []


def test_forced_serial_returns_empty_text_and_never_falls_through(tmp_path):
    rig = with_log(tmp_path, engine="serial", vmscreen=[serial_ok("")])
    assert rig.reader.read() == ScreenText("", "serial", "stream")
    assert len(serial_calls(rig)) == 1 and rig.notes == []


@pytest.mark.parametrize("code, exc", [(5, "QMP problem"), (3, "timed out")])
def test_serial_qmp_and_timeout_failures_are_skippable_reads(tmp_path, code, exc):
    rig = with_log(tmp_path, engine="serial", vmscreen=[RunResult(code, "", "boom")])
    with pytest.raises(vm_lab_text.TextReadError, match=exc):
        rig.reader.read()


def test_serial_rejected_arguments_and_a_first_failure_are_engine_errors(tmp_path):
    rig = with_log(tmp_path, engine="serial", vmscreen=[RunResult(2, "", "[ERROR] malformed serial mark")])
    with pytest.raises(TextEngineError, match="rejected the arguments.*malformed serial mark"):
        rig.reader.read()
    rig = with_log(tmp_path, engine="serial", vmscreen=[RunResult(1, "", "no")])
    with pytest.raises(TextEngineError, match="before it ever worked"):
        rig.reader.read()


def test_serial_uses_the_same_vmscreen_probe_and_reports_unavailability(tmp_path):
    rig = with_log(tmp_path, engine="serial", probe=RunResult(1, "", "No module named vmscreen"))
    with pytest.raises(TextEngineError, match="vmscreen is not available"):
        rig.reader.read()
    auto = with_log(tmp_path, probe=RunResult(1, "", "No module named vmscreen"), pwmcp=[ocr_ok("o")])
    assert auto.reader.read().engine == "ocr"
    assert auto.notes[0].endswith("; using OCR only")


def test_every_vmscreen_option_of_serial_and_mark_exists_in_the_cli_surface(tmp_path):
    surface = json.loads(SURFACE.read_text())

    def flags(route_id: str) -> set[str]:
        route = next(item for item in surface["routes"] if item["id"] == route_id)
        known = set(route["common_controls"])
        for action in route["actions"]:
            known.update(action["flags"])
        return known

    rig = with_log(tmp_path, vmscreen=[mark_ok(), serial_ok("a")])
    rig.reader.since_mark = None
    rig.reader.take_mark()
    rig.reader.since_mark = MARK
    rig.reader.read()
    mark_argv, text_argv = serial_calls(rig)
    for argv, verb in ((mark_argv, "mark"), (text_argv, "text")):
        passed = [token for token in argv[argv.index(verb) + 1:] if token.startswith("--")]
        unknown = [token for token in passed if token not in flags(f"route:entrypoint:vmscreen/{verb}")]
        assert passed and unknown == [], f"vmscreen {verb} has no option {unknown}"
    assert {"--mark", "--serial-log"} <= set(text_argv)


# --- marks --------------------------------------------------------------------------------------


def test_take_mark_runs_vmscreen_mark_and_returns_the_token(tmp_path):
    rig = with_log(tmp_path, vmscreen=[mark_ok()], vmscreen_on_path=True)
    assert rig.reader.take_mark() == MARK
    assert serial_calls(rig) == [["/opt/bin/vmscreen", "mark", "--serial-log", str(tmp_path / "serial.log"), "--json"]]


def test_take_mark_without_a_log_is_an_error(tmp_path):
    with pytest.raises(TextEngineError, match="no serial log at"):
        Rig(tmp_path).reader.take_mark()


@pytest.mark.parametrize(
    "result",
    [RunResult(2, "", "[ERROR] no such command"), RunResult(0, "not json", ""), RunResult(0, "{}", ""),
     RunResult(0, json.dumps({"mark": "5"}), "")],
)
def test_a_failed_or_malformed_mark_is_an_engine_error(tmp_path, result):
    rig = with_log(tmp_path, vmscreen=[result])
    with pytest.raises(TextEngineError, match="vmscreen mark"):
        rig.reader.take_mark()


def test_console_mark_prints_only_the_token_on_stdout(monkeypatch, tmp_path, capsys):
    rig = with_log(tmp_path, vmscreen=[mark_ok()])
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    monkeypatch.setattr(vm_lab, "console_screen_reader", lambda *_a, **_k: rig.reader)
    code = vm_lab.main(GENERIC_PROFILE, ["console", "mark", "--name", "deb1", "--state-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0 and captured.out == MARK + "\n" and captured.err == ""


def test_console_mark_without_a_serial_log_exits_1_with_a_vm_lab_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    code = vm_lab.main(GENERIC_PROFILE, ["console", "mark", "--name", "deb1", "--state-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == "" and "no serial log at" in captured.err


def test_console_mark_builds_a_serial_reader(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    monkeypatch.setattr(
        vm_lab, "console_screen_reader",
        lambda profile, state, engine, **kw: seen.append((engine, kw)) or StubMarkReader(),
    )
    vm_lab.main(GENERIC_PROFILE, ["console", "mark", "--name", "deb1", "--state-dir", str(tmp_path)])
    assert seen == [("serial", {})]


class StubMarkReader:
    def take_mark(self):
        return MARK


def run_verb(monkeypatch, tmp_path, *verb, reader=None):
    seen = []
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: labelled_guest(tmp_path))
    monkeypatch.setattr(
        vm_lab, "console_screen_reader",
        lambda profile, state, engine, **kw: seen.append((engine, kw)) or (reader or StubReader("MENU x")),
    )
    code = vm_lab.main(GENERIC_PROFILE, ["console", *verb, "--name", "deb1", "--state-dir", str(tmp_path)])
    return code, seen


def test_since_mark_reaches_the_reader_of_text_and_wait(monkeypatch, tmp_path):
    code, seen = run_verb(monkeypatch, tmp_path, "text", "--since-mark", MARK)
    assert code == 0 and seen == [("auto", {"since_mark": MARK})]
    code, seen = run_verb(monkeypatch, tmp_path, "wait-for-text", "MENU", "--timeout", "5", "--since-mark", MARK)
    assert code == 0 and seen == [("auto", {"since_mark": MARK, "implicit_mark": False})]


def test_wait_without_since_mark_asks_for_the_implicit_mark_and_text_does_not(monkeypatch, tmp_path):
    code, seen = run_verb(monkeypatch, tmp_path, "wait-for-text", "MENU", "--timeout", "5")
    assert code == 0 and seen == [("auto", {"since_mark": None, "implicit_mark": True})]
    code, seen = run_verb(monkeypatch, tmp_path, "text")
    assert seen == [("auto", {"since_mark": None})]


@pytest.mark.parametrize("verb", [("text",), ("wait-for-text", "MENU", "--timeout", "5")])
@pytest.mark.parametrize("token", ["0.1.2", "garbage", "240.278.64.zz.64.aa"])
def test_a_malformed_since_mark_is_a_usage_error_before_anything_connects(monkeypatch, tmp_path, capsys, verb, token):
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _n: pytest.fail("connected"))
    code = vm_lab.main(
        GENERIC_PROFILE, ["console", *verb, "--name", "deb1", "--state-dir", str(tmp_path), "--since-mark", token]
    )
    assert code == 2 and "not a mark token" in capsys.readouterr().err


def test_validate_mark_accepts_what_vmscreen_prints():
    assert vm_lab_text.validate_mark(MARK) == MARK
    with pytest.raises(TextUsageError):
        vm_lab_text.validate_mark("")


def test_the_implicit_mark_is_taken_once_before_the_first_serial_read(tmp_path):
    rig = with_log(tmp_path, vmscreen=[mark_ok(MARK), serial_ok(""), vm_none(), serial_ok("login:")], have_pwmcp=False)
    rig.reader._implicit_mark = True
    rig.reader.read()
    rig.reader.read()
    verbs = [argv[argv.index("mark" if "mark" in argv else "text")] for argv in serial_calls(rig)]
    assert verbs[0] == "mark" and verbs.count("mark") == 1
    serial_reads = [argv for argv in serial_calls(rig) if "serial" in argv and "text" in argv]
    assert len(serial_reads) == 2 and all(argv[-2:] == ["--mark", MARK] for argv in serial_reads)


def test_an_explicit_since_mark_takes_no_implicit_mark(tmp_path):
    rig = with_log(tmp_path, vmscreen=[serial_ok("x")])
    rig.reader.since_mark = MARK2
    rig.reader._implicit_mark = True
    rig.reader.read()
    calls = serial_calls(rig)
    assert len(calls) == 1 and "text" in calls[0] and calls[0][-2:] == ["--mark", MARK2]


def test_implicit_mark_failure_fails_closed(tmp_path):
    rig = with_log(tmp_path, vmscreen=[RunResult(2, "", "bad")])
    rig.reader._implicit_mark = True
    with pytest.raises(TextEngineError, match="vmscreen mark failed"):
        rig.reader.read()
    assert len(serial_calls(rig)) == 1


@pytest.mark.parametrize("engine", ["vmscreen", "ocr"])
def test_other_engines_ignore_since_mark_with_one_note(tmp_path, engine):
    (tmp_path / "serial.log").write_text("x")
    rig = Rig(tmp_path, engine=engine, vmscreen=[vm_ok("a"), vm_ok("b")], pwmcp=[ocr_ok("a"), ocr_ok("b")])
    rig.reader.since_mark = MARK
    rig.reader._explicit_mark = True
    rig.reader.read()
    rig.reader.read()
    assert rig.notes == ["vm-lab: --since-mark applies to the serial engine only; this engine ignores it"]
    assert all("--mark" not in argv for argv in serial_calls(rig))


def test_auto_note_for_an_explicit_mark_when_serial_does_not_apply(tmp_path):
    rig = Rig(tmp_path, vmscreen=[vm_ok("a")])  # no serial log
    rig.reader.since_mark = MARK
    rig.reader._explicit_mark = True
    rig.reader.read()
    assert len(rig.notes) == 1 and "serial engine only" in rig.notes[0]


def test_ctor_rejects_a_malformed_mark(tmp_path):
    with pytest.raises(TextUsageError, match="not a mark token"):
        vm_lab_text.ScreenReader(
            state_dir=tmp_path, vmscreen_qmp_path=lambda: tmp_path, qmp_socket=tmp_path, qmp_call=None,
            since_mark="nope",
        )


def test_real_reader_factory_passes_marks_through(tmp_path):
    reader = vm_lab.console_screen_reader(
        GENERIC_PROFILE, tmp_path.resolve(), "serial", since_mark=MARK, implicit_mark=True
    )
    assert reader.since_mark == MARK and reader._implicit_mark and reader.engine == "serial"
    assert reader.serial_log == tmp_path.resolve() / "serial.log"


# --- help texts and the parser ------------------------------------------------------------------


def test_engines_list_serial_first_in_auto_order_and_help_names_the_new_options(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1000")  # no wrapping inside a hyphenated option name
    assert vm_lab_text.ENGINES == ("auto", "serial", "vmscreen", "ocr")
    parser = vm_lab.build_parser(GENERIC_PROFILE)
    for verb in ("text", "wait-for-text", "mark"):
        with pytest.raises(SystemExit):
            parser.parse_args(["console", verb, "--help"])
        out = " ".join(capsys.readouterr().out.split())
        assert "serial" in out
        if verb != "mark":
            assert "--since-mark TOKEN" in out and "serial engine only" in out
    with pytest.raises(SystemExit):
        parser.parse_args(["console", "mark", "--help"])
    assert "--serial-console" in " ".join(capsys.readouterr().out.split())

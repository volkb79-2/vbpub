"""VM console typing and recorder behavior without QEMU, Docker, or real time."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

import vm_lab
import vm_lab_console


class FakeClock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def record_args(out: Path, **overrides):
    values = {
        "out": str(out),
        "name": "deb1",
        "state_dir": "state",
        "stop_guest_on_exit": False,
        "interval": 3.0,
        "diff_threshold": 0.00002,
        "cursor_w": 24,
        "cursor_h": 40,
        "cap": 2000,
        "max_runtime": 14400.0,
        "qmp_timeout": 30.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def screen(sequence, pause):
    frames = iter(sequence)
    return lambda: next(frames)


def blank(size=(100, 100)) -> Image.Image:
    return Image.new("RGB", size, "black")


def changed(size=(100, 100), pixels=1) -> Image.Image:
    image = blank(size)
    for index in range(pixels):
        image.putpixel((index % size[0], index // size[0]), (255, 255, 255))
    return image


def run_record(
    tmp_path: Path,
    screens,
    *,
    clock=None,
    stop=None,
    qmp_call=None,
    args=None,
):
    clock = clock or FakeClock()
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    out = tmp_path / "capture"
    args = args or record_args(out)

    def next_screen():
        item = next(screens)
        if isinstance(item, BaseException):
            (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).write_bytes(b"partial")
            raise item
        return item

    rc = vm_lab_console.cmd_console_record(
        args,
        state_dir,
        qmp_call=qmp_call or (lambda *_a, **_k: {}),
        stop_guest=stop,
        clock=clock,
        sleep=clock.sleep,
        next_screen=next_screen,
        image_module=Image,
        image_chops=ImageChops,
    )
    return rc, out, state_dir


def done_reason(out: Path) -> str:
    return (out / "DONE").read_text().strip()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abcXYZ", [["a"], ["b"], ["c"], ["shift", "x"], ["shift", "y"], ["shift", "z"]]),
        ("09", [["0"], ["9"]]),
        ("\n\t", [["ret"], ["tab"]]),
    ],
)
def test_text_mapping_preserves_letters_capitals_digits_newline_and_tab(text, expected):
    assert vm_lab_console.text_to_keys(text) == expected


def test_text_mapping_covers_each_base_and_shifted_punctuation_group():
    assert vm_lab_console.text_to_keys(" !@#$%^&*()") == [
        ["spc"],
        ["shift", "1"],
        ["shift", "2"],
        ["shift", "3"],
        ["shift", "4"],
        ["shift", "5"],
        ["shift", "6"],
        ["shift", "7"],
        ["shift", "8"],
        ["shift", "9"],
        ["shift", "0"],
    ]
    assert vm_lab_console.text_to_keys("-=.,/;'[]\\`") == [
        ["minus"],
        ["equal"],
        ["dot"],
        ["comma"],
        ["slash"],
        ["semicolon"],
        ["apostrophe"],
        ["bracket_left"],
        ["bracket_right"],
        ["backslash"],
        ["grave_accent"],
    ]
    assert vm_lab_console.text_to_keys('_+:"><{}|~?') == [
        ["shift", "minus"],
        ["shift", "equal"],
        ["shift", "semicolon"],
        ["shift", "apostrophe"],
        ["shift", "dot"],
        ["shift", "comma"],
        ["shift", "bracket_left"],
        ["shift", "bracket_right"],
        ["shift", "backslash"],
        ["shift", "grave_accent"],
        ["shift", "slash"],
    ]


@pytest.mark.parametrize("text", ["é", "£", "\r", "\x00"])
def test_unmappable_text_is_rejected_before_any_send(text):
    calls = []

    def qmp_call(*args):
        calls.append(args)

    with pytest.raises(vm_lab_console.ConsoleError, match=re.escape(repr(text[0]))):
        vm_lab_console.send_text(
            Path("/state/qmp.sock"),
            "ok" + text,
            qmp_call=qmp_call,
            timeout=3,
            sleep=lambda _seconds: None,
        )
    assert calls == []


def test_console_type_refuses_a_container_without_the_profile_labels(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        vm_lab,
        "docker_inspect",
        lambda _name: {"Config": {"Labels": {}}, "State": {"Running": True}},
    )
    monkeypatch.setattr(vm_lab, "qmp_command", lambda *args: calls.append(args))
    with pytest.raises(vm_lab.RunnerError, match="not a managed vm-lab TCG VM"):
        vm_lab.cmd_console_type(
            vm_lab.GENERIC_PROFILE,
            SimpleNamespace(name="deb1", state_dir=str(tmp_path), text="abc", text_file=None),
        )
    assert calls == []


def test_console_type_validates_the_whole_text_before_opening_qmp(monkeypatch, tmp_path):
    calls = []
    labels = {
        vm_lab.GENERIC_PROFILE.vm_label: vm_lab.GENERIC_PROFILE.label_value,
        vm_lab.GENERIC_PROFILE.state_label: vm_lab.state_identity(tmp_path),
    }
    monkeypatch.setattr(
        vm_lab,
        "docker_inspect",
        lambda _name: {"Config": {"Labels": labels}, "State": {"Running": True}},
    )
    monkeypatch.setattr(vm_lab, "qmp_command", lambda *args: calls.append(args))
    monkeypatch.setattr(vm_lab_console.time, "sleep", lambda _seconds: None)
    args = SimpleNamespace(name="deb1", state_dir=str(tmp_path), text="abcé", text_file=None)
    assert vm_lab.main(vm_lab.GENERIC_PROFILE, ["console", "type", "--name", "deb1", "--state-dir", str(tmp_path), "--text", "abcé"]) == 1
    with pytest.raises(vm_lab.RunnerError, match="unmappable character"):
        vm_lab.cmd_console_type(vm_lab.GENERIC_PROFILE, args)
    assert calls == []


def test_console_type_sends_one_short_qmp_call_per_character(monkeypatch, tmp_path):
    calls = []
    labels = {
        vm_lab.GENERIC_PROFILE.vm_label: vm_lab.GENERIC_PROFILE.label_value,
        vm_lab.GENERIC_PROFILE.state_label: vm_lab.state_identity(tmp_path),
    }
    monkeypatch.setattr(
        vm_lab,
        "docker_inspect",
        lambda _name: {"Config": {"Labels": labels}, "State": {"Running": True}},
    )
    monkeypatch.setattr(vm_lab, "qmp_command", lambda *args: calls.append(args) or {})
    monkeypatch.setattr(vm_lab_console.time, "sleep", lambda _seconds: None)
    args = SimpleNamespace(name="deb1", state_dir=str(tmp_path), text="aB", text_file=None)
    assert vm_lab.cmd_console_type(vm_lab.GENERIC_PROFILE, args) == 0
    assert [call[1] for call in calls] == ["send-key", "send-key"]
    assert calls[0][2]["keys"] == [{"type": "qcode", "data": "a"}]
    assert calls[1][2]["keys"] == [
        {"type": "qcode", "data": "shift"},
        {"type": "qcode", "data": "b"},
    ]


@pytest.mark.parametrize(
    ("pixels", "expected_frames"),
    [(99, 1), (100, 2), (101, 2)],
)
def test_keep_rule_at_under_at_and_over_threshold(tmp_path, pixels, expected_frames):
    args = record_args(
        tmp_path / "capture",
        diff_threshold=0.01,
        cap=2,
        max_runtime=0.015,
        interval=0.01,
        cursor_w=1,
        cursor_h=1,
    )
    screens = iter([blank(), changed(pixels=pixels), blank()])
    rc, out, _state = run_record(tmp_path, screens, args=args)
    assert rc == 0
    assert len(list((out / "frames").glob("*.png"))) == expected_frames


def test_both_cursor_phases_are_learned_then_suppressed_and_a_new_small_change_is_kept(tmp_path):
    base = blank()
    phase_on = blank()
    phase_on.paste((255, 255, 255), (10, 10, 15, 15))
    genuine = blank()
    genuine.paste((255, 255, 255), (20, 20, 30, 30))
    args = record_args(
        tmp_path / "capture",
        diff_threshold=0.0002,
        cursor_w=20,
        cursor_h=20,
        cap=2,
    )
    genuine_two = blank()
    genuine_two.paste((255, 255, 255), (30, 30, 40, 40))
    screens = iter([base, phase_on, base, phase_on, genuine, genuine_two])
    rc, out, _state = run_record(tmp_path, screens, args=args)
    assert rc == 0
    files = sorted((out / "frames").glob("*.png"))
    assert [path.name for path in files] == ["0001_0s.png", "0002_0s.png"]
    kept = Image.open(files[1]).convert("RGB")
    assert kept.getpixel((25, 25)) == (255, 255, 255)


def test_resume_continues_numbering_uses_original_clock_and_compares_last_frame(tmp_path):
    clock = FakeClock(100)
    args = record_args(tmp_path / "capture", cap=1)
    rc, out, state_dir = run_record(tmp_path, iter([blank()]), clock=clock, args=args)
    assert rc == 0
    first = json.loads((out / "manifest.jsonl").read_text())
    assert first["file"] == "0001_0s.png"
    assert json.loads((out / "state.json").read_text())["t0_epoch"] == 100

    clock.now = 110
    args = record_args(out, cap=1)
    rc, _out, _state = run_record(tmp_path, iter([changed(pixels=100)]), clock=clock, args=args)
    assert rc == 0
    resumed = json.loads((out / "manifest.jsonl").read_text().splitlines()[-1])
    assert (resumed["sequence"], resumed["file"]) == (2, "0002_10s.png")

    clock.now = 120
    args = record_args(out, max_runtime=0.1, cap=10)
    rc, _out, _state = run_record(tmp_path, iter([changed(pixels=100)]), clock=clock, args=args)
    assert rc == 0
    assert len((out / "manifest.jsonl").read_text().splitlines()) == 2
    assert done_reason(out) == "max-runtime"


def test_resume_without_state_json_uses_the_documented_legacy_clock_fallback(tmp_path):
    out = tmp_path / "capture"
    frames = out / "frames"
    frames.mkdir(parents=True)
    Image.new("RGB", (100, 100), "black").save(frames / "0007_10s.png")
    (out / "manifest.jsonl").write_text(
        json.dumps({
            "sequence": 7, "elapsed_s": 10, "file": "0007_10s.png",
            "sha256": "x", "width": 100, "height": 100,
            "fraction_of_pixels_changed": 1.0, "keys_before": [],
        }) + "\n"
    )
    clock = FakeClock(1000)
    args = record_args(out, interval=3.0, cap=1)
    rc, _out, _state = run_record(tmp_path, iter([changed(pixels=100)]), clock=clock, args=args)
    assert rc == 0
    resumed = json.loads((out / "manifest.jsonl").read_text().splitlines()[-1])
    assert resumed["file"] == "0008_13s.png"


def test_done_is_written_for_cap_and_the_temporary_screendump_is_removed(tmp_path):
    rc, out, state_dir = run_record(tmp_path, iter([blank()]), args=record_args(tmp_path / "capture", cap=1))
    assert rc == 0
    assert done_reason(out) == "cap"
    assert not (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).exists()


def test_done_is_written_for_quit_and_gtemp_is_absent(tmp_path):
    out = tmp_path / "capture"
    (out / "inbox").mkdir(parents=True)
    (out / "inbox" / "0001.json").write_text('{"quit": true}')
    rc, out, state_dir = run_record(tmp_path, iter([blank()]), args=record_args(out))
    assert rc == 0
    assert done_reason(out) == "quit-command"
    assert not (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).exists()


def test_done_is_written_for_max_runtime(tmp_path):
    clock = FakeClock()
    rc, out, state_dir = run_record(
        tmp_path,
        iter([blank(), blank(), blank()]),
        clock=clock,
        args=record_args(tmp_path / "capture", max_runtime=0.5, cap=10),
    )
    assert rc == 0
    assert done_reason(out) == "max-runtime"
    assert not (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).exists()


def test_done_is_written_for_an_injected_error_and_gtemp_is_removed(tmp_path):
    rc, out, state_dir = run_record(
        tmp_path,
        iter([RuntimeError("screen source failed")]),
        args=record_args(tmp_path / "capture"),
    )
    assert rc == 1
    assert done_reason(out).startswith("error:")
    assert "screen source failed" in done_reason(out)
    assert not (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).exists()


def test_existing_recorder_temporary_is_refused_and_not_deleted(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    temporary = state_dir / vm_lab_console.RECORDER_TEMP_FILENAME
    temporary.write_bytes(b"someone else's file")
    calls = []
    rc, out, _state = run_record(
        tmp_path,
        iter([blank()]),
        qmp_call=lambda *args: calls.append(args),
        args=record_args(tmp_path / "capture"),
    )
    assert rc == 1
    assert "refusing to overwrite existing recorder screendump" in done_reason(out)
    assert temporary.read_bytes() == b"someone else's file"
    assert calls == []


@pytest.mark.parametrize(
    ("reason", "screen_items"),
    [
        ("cap", [blank()]),
        ("max-runtime", [blank(), blank()]),
        ("error", [RuntimeError("boom")]),
        ("quit-command", [blank()]),
    ],
)
def test_stop_guest_on_exit_except_for_quit(tmp_path, reason, screen_items):
    out = tmp_path / "capture"
    if reason == "quit-command":
        (out / "inbox").mkdir(parents=True)
        (out / "inbox" / "0001.json").write_text('{"quit": true}')
    calls = []
    args = record_args(
        out,
        stop_guest_on_exit=True,
        cap=1 if reason == "cap" else 10,
        max_runtime=0.5,
    )
    rc, out, _state = run_record(
        tmp_path,
        iter(screen_items),
        clock=FakeClock(),
        stop=lambda: calls.append("stop"),
        args=args,
    )
    assert done_reason(out).startswith(reason)
    assert calls == ([] if reason == "quit-command" else ["stop"])
    assert rc == (1 if reason == "error" else 0)


def test_without_stop_guest_on_exit_the_guest_is_never_stopped(tmp_path):
    calls = []
    run_record(
        tmp_path,
        iter([blank()]),
        stop=lambda: calls.append("stop"),
        args=record_args(tmp_path / "capture", cap=1),
    )
    assert calls == []


def parse_ctl(tmp_path: Path, *options):
    return vm_lab.build_parser(vm_lab.GENERIC_PROFILE).parse_args(
        ["console", "ctl", "--out", str(tmp_path / "capture"), *options]
    )


def test_ctl_requires_exactly_one_command(capsys):
    parser = vm_lab.build_parser(vm_lab.GENERIC_PROFILE)
    for options in (["--keys", "ret", "--quit"], ["--type", "abc", "--qmp-json", '{"execute":"close"}']):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args(["console", "ctl", "--out", "capture", *options])
        assert exit_info.value.code == 2
        assert "not allowed with" in capsys.readouterr().err


def test_ctl_rejects_secret_without_type_and_unmappable_text_without_queueing(tmp_path):
    args = parse_ctl(tmp_path, "--keys", "ret", "--secret")
    with pytest.raises(vm_lab.RunnerError, match="--secret is only valid with --type"):
        vm_lab.cmd_console_ctl(vm_lab.GENERIC_PROFILE, args)
    args = parse_ctl(tmp_path, "--type", "é")
    with pytest.raises(vm_lab.RunnerError, match="nothing queued"):
        vm_lab.cmd_console_ctl(vm_lab.GENERIC_PROFILE, args)
    assert not list((tmp_path / "capture" / "inbox").glob("*.json"))


def test_ctl_secret_and_type_file_are_redacted_and_file_text_never_reaches_files(tmp_path):
    out = tmp_path / "capture"
    secret_text = "hunter2"
    file_text = "file-only-secret"
    text_file = tmp_path / "secret.txt"
    text_file.write_text(file_text + "\n")
    vm_lab.cmd_console_ctl(vm_lab.GENERIC_PROFILE, parse_ctl(tmp_path, "--type", secret_text, "--secret"))
    vm_lab.cmd_console_ctl(vm_lab.GENERIC_PROFILE, parse_ctl(tmp_path, "--type-file", str(text_file)))
    assert not any(file_text in path.read_text() for path in (out / "inbox").glob("*.json"))
    assert not any(file_text in path.read_text() for path in (out / "inbox").glob("*.json"))

    calls = []
    clock = FakeClock()
    args = record_args(out, cap=1)
    rc, out, _state = run_record(
        tmp_path,
        iter([blank()]),
        clock=clock,
        qmp_call=lambda *args: calls.append(args) or {},
        args=args,
    )
    assert rc == 0
    log = (out / "keys.jsonl").read_text()
    assert secret_text not in log and file_text not in log
    assert "TYPE:<7 chars redacted>" in log
    assert "TYPE:<16 chars redacted>" in log
    assert all(call[1] == "send-key" for call in calls)


def test_each_recorder_poll_and_queued_command_makes_its_own_qmp_call(tmp_path):
    calls = []
    out = tmp_path / "capture"
    (out / "inbox").mkdir(parents=True)
    (out / "inbox" / "0001.json").write_text('{"keys": [["ret"]]}')
    clock = FakeClock()
    args = record_args(out, max_runtime=3, interval=2)

    def qmp_call(*call_args):
        calls.append(call_args)
        return {}

    rc, out, _state = run_record(
        tmp_path,
        iter([blank(), blank()]),
        clock=clock,
        qmp_call=qmp_call,
        args=args,
    )
    # One send-key from the queued command, then one screendump per poll.
    assert [call[1] for call in calls] == ["send-key", "screendump", "screendump"]
    assert calls[0][0] == tmp_path / "state" / "qmp.sock"


def test_production_screendump_uses_state_directory_and_removes_temporary_file(tmp_path):
    calls = []
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out = tmp_path / "capture"
    args = record_args(out, cap=1)

    def qmp_call(_socket, command, arguments, _timeout):
        calls.append((command, dict(arguments)))
        Image.new("RGB", (20, 20), "black").save(
            state_dir / vm_lab_console.RECORDER_TEMP_FILENAME
        )
        return {}

    rc = vm_lab_console.cmd_console_record(
        args,
        state_dir,
        qmp_call=qmp_call,
        stop_guest=None,
        clock=FakeClock(),
        sleep=lambda _seconds: None,
    )
    assert rc == 0
    assert calls == [(
        "screendump",
        {"filename": "/state/" + vm_lab_console.RECORDER_TEMP_FILENAME, "format": "png"},
    )]
    assert not (state_dir / vm_lab_console.RECORDER_TEMP_FILENAME).exists()


def test_pillow_missing_fails_record_clearly_while_type_still_works(monkeypatch, tmp_path):
    real_import = vm_lab_console.importlib.import_module

    def no_pillow(name):
        if name.startswith("PIL."):
            raise ModuleNotFoundError("No module named 'PIL'")
        return real_import(name)

    monkeypatch.setattr(vm_lab_console.importlib, "import_module", no_pillow)
    rc = vm_lab_console.cmd_console_record(
        record_args(tmp_path / "capture"),
        tmp_path,
        qmp_call=lambda *_args, **_kwargs: {},
        stop_guest=None,
    )
    assert rc == 1
    assert "Pillow is required" in done_reason(tmp_path / "capture")

    calls = []
    labels = {
        vm_lab.GENERIC_PROFILE.vm_label: vm_lab.GENERIC_PROFILE.label_value,
        vm_lab.GENERIC_PROFILE.state_label: vm_lab.state_identity(tmp_path),
    }
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: {"Config": {"Labels": labels}, "State": {"Running": True}})
    monkeypatch.setattr(vm_lab, "qmp_command", lambda *args: calls.append(args))
    monkeypatch.setattr(vm_lab_console.time, "sleep", lambda _seconds: None)
    assert vm_lab.cmd_console_type(
        vm_lab.GENERIC_PROFILE,
        SimpleNamespace(name="deb1", state_dir=str(tmp_path), text="abc", text_file=None),
    ) == 0
    assert len(calls) == 3


def parse_start(*extra):
    return vm_lab.build_parser(vm_lab.GENERIC_PROFILE).parse_args(
        [
            "start", "--name", "deb1", "--base-disk", "base.qcow2",
            "--ovmf-code", "code.fd", "--ovmf-vars", "vars.fd",
            "--state-dir", "state", "--memory-mib", "2048", "--cpus", "2",
            "--forward", "2222:22", *extra,
        ]
    )


def test_generic_start_requires_an_explicit_display_device(capsys):
    with pytest.raises(SystemExit) as exit_info:
        parse_start()
    assert exit_info.value.code == 2
    assert "--display-device" in capsys.readouterr().err


def test_display_device_renders_virtio_and_std_with_std_matching_today():
    std = vm_lab.resolve_profile(vm_lab.GENERIC_PROFILE, parse_start("--display-device", "std"))
    virtio = vm_lab.resolve_profile(vm_lab.GENERIC_PROFILE, parse_start("--display-device", "virtio"))
    std_script = vm_lab.host_command_script(std)
    virtio_script = vm_lab.host_command_script(virtio)
    assert "-device virtio-vga" not in std_script
    assert '-device virtio-vga \\\n  -qmp unix:/state/qmp.sock' in virtio_script
    assert virtio_script.replace("  -device virtio-vga \\\n", "") == std_script


def test_truenas_profile_is_fixed_to_std_and_has_no_display_option():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "truenas" / "tcg_vm.py"
    spec = importlib.util.spec_from_file_location("tcg_vm_console_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PROFILE.display_device == "std"
    parser = module.build_parser()
    base = [
        "start", "--name", "tns", "--base-disk", "base.qcow2",
        "--ovmf-code", "code.fd", "--ovmf-vars", "vars.fd",
        "--state-dir", "state",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--display-device", "virtio"])


def test_generic_resume_identity_records_display_device(monkeypatch, tmp_path):
    base = tmp_path / "base.qcow2"
    code = tmp_path / "code.fd"
    variables = tmp_path / "vars.fd"
    base.write_bytes(b"b")
    code.write_bytes(b"c")
    variables.write_bytes(b"v")
    profile = vm_lab.resolve_profile(vm_lab.GENERIC_PROFILE, parse_start("--display-device", "virtio"))
    manifest = vm_lab.runner_manifest(
        profile,
        name="deb1",
        base_disk=base,
        firmware_code=code,
        firmware_vars=variables,
        image_id="sha256:image",
        network="internal",
        cgroup_parent="dev-background.slice",
        host_ports={"fwd1": 2222},
    )
    assert manifest["display_device"] == "virtio"

#!/usr/bin/env python3
"""QMP console recording and typing for the general vm-lab launcher.

The recorder keeps only changed console frames. QMP connections are made by the
launcher's ``qmp_command`` for one call at a time; this module never holds a
socket between polls. The on-disk recording contract is compatible with the
former pwmcp console-capture recorder.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class ConsoleError(RuntimeError):
    """A fail-closed console recording, typing, or control error."""


SHIFT = {character: key for character, key in zip("!@#$%^&*()", "1234567890")}
PUNCT = {
    " ": "spc",
    "-": "minus",
    "=": "equal",
    ".": "dot",
    ",": "comma",
    "/": "slash",
    ";": "semicolon",
    "'": "apostrophe",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    "`": "grave_accent",
    "\n": "ret",
    "\t": "tab",
}
SHIFTP = {
    "_": "minus",
    "+": "equal",
    ":": "semicolon",
    '"': "apostrophe",
    "<": "comma",
    ">": "dot",
    "?": "slash",
    "{": "bracket_left",
    "}": "bracket_right",
    "|": "backslash",
    "~": "grave_accent",
}

RECORDER_TEMP_FILENAME = ".vm-lab-console-record.png"
LUMA_DIFF_THRESHOLD = 48
QUIT_SENTINEL = object()


def text_to_keys(text: str) -> list[list[str]]:
    """Map US-layout ASCII to the QEMU send-key sequences used by the old recorder."""
    out: list[list[str]] = []
    for character in text:
        if character.isalpha() and character.isascii():
            out.append(["shift", character.lower()] if character.isupper() else [character])
        elif character.isdigit() and character.isascii():
            out.append([character])
        elif character in SHIFT:
            out.append(["shift", SHIFT[character]])
        elif character in SHIFTP:
            out.append(["shift", SHIFTP[character]])
        elif character in PUNCT:
            out.append([PUNCT[character]])
        else:
            raise ConsoleError(f"unmappable character {character!r} (US-layout ASCII only)")
    return out


def diff_stats(a, b, cursor_box: tuple[int, int], image_chops) -> tuple[float, bool]:
    """Return (fraction changed above luma 48, change fits in a cursor-sized box)."""
    if a.size != b.size:
        return 1.0, False
    difference = image_chops.difference(a, b).convert("L").point(
        lambda value: 255 if value > LUMA_DIFF_THRESHOLD else 0
    )
    bbox = difference.getbbox()
    if bbox is None:
        return 0.0, False
    changed = difference.histogram()[255]
    fraction = changed / (a.size[0] * a.size[1])
    small = (bbox[2] - bbox[0]) <= cursor_box[0] and (bbox[3] - bbox[1]) <= cursor_box[1]
    return fraction, small


def _one_control_value(args) -> str:
    given = [
        name
        for name, present in (
            ("--keys", args.keys is not None),
            ("--type", args.type is not None),
            ("--type-file", args.type_file is not None),
            ("--qmp-json", args.qmp_json is not None),
            ("--quit", bool(args.quit)),
        )
        if present
    ]
    if len(given) != 1:
        raise ConsoleError(
            "ctl: give exactly one of --keys, --type, --type-file, --qmp-json, --quit"
        )
    if args.secret and args.type is None:
        raise ConsoleError("ctl: --secret is only valid with --type")
    return given[0]


def queue_control(args) -> Path:
    """Validate and queue one recorder command without contacting the guest."""
    selected = _one_control_value(args)
    inbox = Path(args.out) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if selected == "--keys":
        command: dict[str, Any] = {
            "keys": [key.split("+") for key in args.keys.split(",")]
        }
    elif selected == "--type":
        try:
            text_to_keys(args.type)
        except ConsoleError as exc:
            raise ConsoleError(f"ctl: --type rejected, nothing queued: {exc}") from exc
        command = {"type": args.type, "secret": bool(args.secret)}
    elif selected == "--type-file":
        try:
            text = Path(args.type_file).read_text().rstrip("\n")
            text_to_keys(text)
        except (OSError, ConsoleError) as exc:
            raise ConsoleError(f"ctl: --type-file rejected, nothing queued: {exc}") from exc
        command = {"type_file": str(Path(args.type_file).resolve())}
    elif selected == "--qmp-json":
        try:
            parsed = json.loads(args.qmp_json)
        except json.JSONDecodeError as exc:
            raise ConsoleError(f"ctl: --qmp-json is invalid, nothing queued: {exc}") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("execute"), str):
            raise ConsoleError(
                "ctl: --qmp-json rejected, nothing queued: "
                "expected an object with a string execute field"
            )
        command = {"qmp": parsed}
    else:
        command = {"quit": True}

    target = inbox / f"{time.time_ns()}.json"
    while target.exists() or target.is_symlink():
        target = inbox / f"{time.time_ns()}.json"
    target.write_text(json.dumps(command))
    return target


def send_text(
    socket_path: Path,
    text: str,
    *,
    qmp_call: Callable[..., Any],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Validate and send all text before returning; one unmappable character sends nothing."""
    sequences = text_to_keys(text)
    for combo in sequences:
        qmp_call(
            socket_path,
            "send-key",
            {
                "keys": [{"type": "qcode", "data": key} for key in combo],
                "hold-time": 80,
            },
            timeout,
        )
        sleep(0.15)
    return len(sequences)


def command_executor(
    *,
    socket_path: Path,
    qmp_call: Callable[..., Any],
    timeout: float,
    sleep: Callable[[float], None],
):
    """Build the queued-command executor used by a foreground recorder."""

    def execute(command: Mapping[str, Any], elapsed: float) -> Any:
        if command.get("quit"):
            return QUIT_SENTINEL
        if "keys" in command or "type" in command or "type_file" in command:
            secret = bool(command.get("secret", False))
            if "type_file" in command:
                text = Path(command["type_file"]).read_text().rstrip("\n")
                secret = True
            else:
                text = str(command["type"])
            sequences: Sequence[Sequence[str]]
            if "keys" in command:
                sequences = command["keys"]
            else:
                sequences = text_to_keys(text)
            for combo in sequences:
                qmp_call(
                    socket_path,
                    "send-key",
                    {
                        "keys": [{"type": "qcode", "data": key} for key in combo],
                        "hold-time": 80,
                    },
                    timeout,
                )
                sleep(0.15)
            if "keys" in command:
                label = ["+".join(combo) for combo in sequences]
            elif secret:
                label = f"TYPE:<{len(text)} chars redacted>"
            else:
                label = "TYPE:" + text
            return label

        qmp = command["qmp"]
        result = qmp_call(
            socket_path,
            qmp["execute"],
            qmp.get("arguments", {}),
            timeout,
        )
        return {"qmp": qmp, "result": result}

    return execute


def record_loop(
    args,
    out: Path,
    *,
    next_screen: Callable[[], Any],
    execute: Callable[[Mapping[str, Any], float], Any],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    image_module,
    image_chops,
) -> str:
    """Run the recorder loop until quit, cap, or max-runtime and return the reason."""
    inbox = out / "inbox"
    frames_dir = out / "frames"
    manifest_path = out / "manifest.jsonl"
    state_path = out / "state.json"
    now = clock()
    sequence = 0
    base = None
    t0 = now
    lines = [json.loads(line) for line in manifest_path.read_text().splitlines() if line.strip()]
    if lines:
        sequence = int(lines[-1]["sequence"])
        try:
            base = image_module.open(frames_dir / lines[-1]["file"]).convert("RGB")
            base.load()
        except Exception:
            base = None
        if state_path.exists():
            t0 = float(json.loads(state_path.read_text())["t0_epoch"])
        else:
            # Legacy directories have no state.json; the restart gap is lost.
            t0 = now - float(lines[-1]["elapsed_s"]) - args.interval
    else:
        state_path.write_text(json.dumps({"t0_epoch": t0}))

    phases = [base] if base is not None else []
    pending: tuple[Any, float, list[dict[str, Any]]] | None = None
    recent_keys: list[dict[str, Any]] = []
    cursor_box = (args.cursor_w, args.cursor_h)
    threshold = args.diff_threshold
    print(
        f"threshold fraction>={threshold} (px luma diff>48), "
        f"cursor_box={args.cursor_w}x{args.cursor_h}, interval={args.interval}s, "
        f"cap={args.cap}, max_runtime={args.max_runtime}s, qmp_timeout={args.qmp_timeout}s",
        flush=True,
    )

    with manifest_path.open("a") as manifest, (out / "keys.jsonl").open("a") as keylog:

        def keep(image, fraction: float, keys_before: list[dict[str, Any]], small_vs_old: bool) -> bool:
            nonlocal sequence, base
            old = base
            sequence += 1
            elapsed = int(clock() - t0)
            filename = f"{sequence:04d}_{elapsed}s.png"
            image.save(frames_dir / filename)
            digest = hashlib.sha256((frames_dir / filename).read_bytes()).hexdigest()
            manifest.write(
                json.dumps(
                    {
                        "sequence": sequence,
                        "elapsed_s": elapsed,
                        "file": filename,
                        "sha256": digest,
                        "width": image.size[0],
                        "height": image.size[1],
                        "fraction_of_pixels_changed": round(fraction, 6),
                        "keys_before": keys_before,
                    }
                )
                + "\n"
            )
            manifest.flush()
            base = image
            phases[:] = [image, old] if small_vs_old and old is not None else [image]
            if sequence >= args.cap:
                print("FRAME CAP HIT", flush=True)
                return True
            return False

        def is_phase(image) -> bool:
            return any(
                diff_stats(image, phase, cursor_box, image_chops)[0] < threshold
                for phase in phases
            )

        while True:
            if clock() - t0 > args.max_runtime:
                return "max-runtime"
            for path in sorted(inbox.glob("*.json")):
                command = json.loads(path.read_text())
                path.unlink()
                if command.get("quit"):
                    return "quit-command"
                elapsed = round(clock() - t0, 1)
                sent = execute(command, elapsed)
                keylog.write(json.dumps({"t": elapsed, "sent": sent}) + "\n")
                keylog.flush()
                recent_keys.append({"t": elapsed, "sent": sent})

            image = next_screen()
            stop = False
            if base is None:
                stop = keep(image, 1.0, recent_keys[:], False)
                recent_keys.clear()
            elif pending is not None:
                pending_image, pending_fraction, pending_keys = pending
                pending = None
                if is_phase(image):
                    phases.append(pending_image)
                else:
                    stop = keep(pending_image, pending_fraction, pending_keys, True)
                    fraction, _small = diff_stats(image, base, cursor_box, image_chops)
                    if fraction >= threshold and not stop:
                        pending = (image, fraction, [])
            else:
                fraction, small = diff_stats(image, base, cursor_box, image_chops)
                if fraction >= threshold and not is_phase(image):
                    if small:
                        pending = (image, fraction, recent_keys[:])
                    else:
                        stop = keep(image, fraction, recent_keys[:], False)
                    recent_keys.clear()
            if stop:
                return "cap"
            sleep(args.interval)


def cmd_console_record(
    args,
    state_dir: Path,
    *,
    qmp_call: Callable[..., Any],
    stop_guest: Callable[[], Any] | None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    next_screen: Callable[[], Any] | None = None,
    image_module=None,
    image_chops=None,
) -> int:
    """Run the foreground recorder and always write DONE.

    Pillow is imported only here. Tests may inject the screen source, clock,
    sleep, QMP callable, and image modules to run the exact loop without QEMU
    or real time.
    """
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "inbox").mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    temporary = state_dir / RECORDER_TEMP_FILENAME
    reason = "unknown"
    claimed_temporary = False
    failed = False
    try:
        if temporary.exists() or temporary.is_symlink():
            raise ConsoleError(
                f"refusing to overwrite existing recorder screendump {temporary}"
            )
        claimed_temporary = True
        if image_module is None or image_chops is None:
            try:
                image_module = importlib.import_module("PIL.Image")
                image_chops = importlib.import_module("PIL.ImageChops")
            except ImportError as exc:
                raise ConsoleError(
                    "Pillow is required by `console record`; install the Pillow package"
                ) from exc

        if next_screen is None:
            socket_path = state_dir / "qmp.sock"

            def default_next_screen() -> Any:
                if temporary.exists() or temporary.is_symlink():
                    raise ConsoleError(
                        f"refusing to overwrite existing recorder screendump {temporary}"
                    )
                qmp_call(
                    socket_path,
                    "screendump",
                    {"filename": f"/state/{RECORDER_TEMP_FILENAME}", "format": "png"},
                    args.qmp_timeout,
                )
                try:
                    image = image_module.open(temporary).convert("RGB")
                    image.load()
                    return image
                finally:
                    temporary.unlink(missing_ok=True)

            next_screen = default_next_screen

        execute = command_executor(
            socket_path=state_dir / "qmp.sock",
            qmp_call=qmp_call,
            timeout=args.qmp_timeout,
            sleep=sleep,
        )
        reason = record_loop(
            args,
            out,
            next_screen=next_screen,
            execute=execute,
            clock=clock,
            sleep=sleep,
            image_module=image_module,
            image_chops=image_chops,
        )
    except BaseException as exc:  # noqa: BLE001 - DONE must always record why the recorder stopped
        reason = f"error: {exc!r}"
        failed = True
        print(reason, flush=True)
    finally:
        if claimed_temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                print(
                    f"[record] could not remove temporary screendump {temporary}: {exc}",
                    flush=True,
                )
        (out / "DONE").write_text(reason + "\n")
        if args.stop_guest_on_exit and reason != "quit-command":
            try:
                stop_guest()
            except BaseException as exc:  # noqa: BLE001 - the original DONE reason must survive
                print(f"[record] stop-guest-on-exit failed: {exc}", flush=True)
    return 1 if failed else 0


def cmd_console_type(
    state_dir: Path,
    text: str,
    *,
    qmp_call: Callable[..., Any],
    timeout: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Type validated text into a guest through one short QMP call per key sequence."""
    sent = send_text(
        state_dir / "qmp.sock",
        text,
        qmp_call=qmp_call,
        timeout=timeout,
        sleep=sleep,
    )
    print(f"[type] sent {sent} characters", flush=True)
    return 0

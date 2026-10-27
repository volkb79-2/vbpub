"""The command line: a thin cli-extended shell over the pure core.

Exit codes: 0 ok, 1 other failure, 2 usage, 3 wait-text timeout, 4 no exact text source applies, 5 QMP unreachable/error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from cli_extended import (ArgumentSpec, CliFailure, CliIdentity, CliRegistry, CliRuntime, OptionSpec, RegisteredCli, VerbGroup, VerbSpec,
                          register_skills_verbs)

from . import capture, keys, sources, textmode, waiter
from .errors import NoExactText, QmpError, VmscreenError, WaitTimeout
from .qmp import QmpClient

ClientFactory = Callable[[str, float], QmpClient]


def identity() -> CliIdentity:
    """The version is the installed distribution's; there is no literal fallback (an uninstalled tree fails loudly)."""
    return CliIdentity.resolve(name="vmscreen", long_name="exact console text of local QEMU guests", command="vmscreen",
                               distribution="vmscreen")


def _value(flags: tuple[str, ...], text: str, metavar: str, group: str = "OPTIONS", **kw: Any) -> OptionSpec:
    return OptionSpec(flags, text, group=group, metavar=metavar, parser_kwargs={"default": None, **kw})


def _flag(flags: tuple[str, ...], text: str, group: str = "OPTIONS") -> OptionSpec:
    return OptionSpec(flags, text, group=group, parser_kwargs={"action": "store_true"})


QMP = _value(("--qmp",), "QMP unix socket of the guest (default: $VMSCREEN_QMP). The QEMU command line needs "
             "-qmp unix:SOCK,server=on,wait=off. QMP serves ONE client: stop other pollers first.", "SOCK", "GUEST")
QMP_TIMEOUT = _value(("--qmp-timeout",), "Timeout in seconds for every QMP connect/read.", "SECONDS", "GUEST", type=float, default=10.0)
DUMP_DIR = _value(("--dump-dir",), "A directory BOTH vmscreen and QEMU can read and write (QEMU writes screendump/pmemsave files "
                  "there). Default: the system temp dir, which is only right when QEMU runs in the same container.", "DIR", "GUEST")
SERIAL = _value(("--serial-log",), "The guest's serial log file (QEMU -serial file:PATH); enables the serial source.", "FILE", "SOURCES")
SOURCE = _value(("--source",), "Exact-text source: auto tries vga then serial.", "NAME", "SOURCES", choices=sources.SOURCES, default="auto")
NO_BLINK = _flag(("--no-blink-mode",), "Decode attribute bit 7 as a bright background instead of blink.", "SOURCES")
COMMON = (QMP, QMP_TIMEOUT, DUMP_DIR)
TEXT_OPTS = (SERIAL, SOURCE, NO_BLINK)


def _emit(runtime: CliRuntime, obj: Any, human: str | None = None) -> None:
    if runtime.json_mode or human is None:
        runtime.output.primary(obj if runtime.json_mode else json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))
    else:
        runtime.output.primary(human)


def _fail(exc: VmscreenError, runtime: CliRuntime) -> CliFailure:
    """Exit-coded failure; in --json mode the structured detail still goes to stdout."""
    extra: dict[str, Any] = {"ok": False, "error": str(exc), "exit_code": exc.exit_code}
    if isinstance(exc, NoExactText):
        extra.update(exact=False, tried=exc.tried)
    if isinstance(exc, WaitTimeout):
        extra["last_text"] = exc.last_text
    if runtime.json_mode:
        runtime.output.primary(extra)
    return CliFailure(str(exc), exit_code=exc.exit_code)


def build_cli(cli_identity: CliIdentity | None = None, client_factory: ClientFactory = QmpClient) -> RegisteredCli:
    ident = cli_identity or identity()
    registry = CliRegistry(ident, prog="vmscreen", description="Read the console of a local QEMU guest as exact text, no OCR; "
                           "capture screenshot+text ground-truth pairs.",
                           getting_started=("vmscreen text --qmp /run/vm/qmp.sock", "vmscreen wait-text 'login:' --qmp SOCK --timeout 120",
                                            "vmscreen ocr-pair --qmp SOCK --out corpus/shot-001"),
                           logging_logger="vmscreen", unexpected_exceptions="report", expected_exceptions=(VmscreenError, ValueError))

    def connect(args: Any) -> QmpClient:
        sock = args.qmp or os.environ.get("VMSCREEN_QMP")
        if not sock:
            raise CliFailure("no QMP socket: pass --qmp SOCK or set VMSCREEN_QMP", exit_code=2)
        client = client_factory(sock, args.qmp_timeout)
        client.connect()
        return client

    def reader(client: QmpClient, args: Any, tail: int | None = None) -> Callable[[], sources.TextResult]:
        return lambda: sources.read_text(client, source=args.source, serial_log=args.serial_log, dump_dir=args.dump_dir,
                                         blink_mode=not args.no_blink_mode, tail=tail)

    def guarded(fn: Callable[[Any, CliRuntime], int]) -> Callable[[Any, CliRuntime], int]:
        def run(args: Any, runtime: CliRuntime) -> int:
            try:
                return fn(args, runtime)
            except VmscreenError as exc:
                raise _fail(exc, runtime) from exc
        return run

    @guarded
    def screen(args: Any, runtime: CliRuntime) -> int:
        with connect(args) as client:
            png = sources.screendump_png(client, args.dump_dir)
        Path(args.out).write_bytes(png)
        w, h = textmode.png_size(png)
        _emit(runtime, {"ok": True, "png": args.out, "width": w, "height": h}, f"{args.out} {w}x{h}")
        return 0

    @guarded
    def text(args: Any, runtime: CliRuntime) -> int:
        with connect(args) as client:
            res = reader(client, args, args.tail)()
        lines = res.text.split("\n")
        if args.tail is not None and res.source == "vga-text":
            lines = lines[-args.tail:]
        res.text = "\n".join(lines)
        _emit(runtime, {"ok": True} | res.as_dict(include_cells=args.cells), res.text)
        if not runtime.json_mode:
            runtime.output.info(f"source: {res.source} ({res.scope}, exact)")
        return 0

    @guarded
    def wait_text(args: Any, runtime: CliRuntime) -> int:
        with connect(args) as client:
            out = waiter.wait_for(reader(client, args), args.pattern, args.timeout, args.interval, literal=args.literal)
        _emit(runtime, {"ok": True} | out, f"matched {out['matched']!r} via {out['source']} after {out['elapsed_s']}s")
        return 0

    @guarded
    def send(args: Any, runtime: CliRuntime) -> int:
        if sum(x is not None for x in (args.keys, args.type, args.type_file)) != 1:
            raise CliFailure("give exactly one of --keys, --type, --type-file", exit_code=2)
        try:
            if args.keys is not None:
                chords = keys.parse_keys(args.keys)
            else:
                chords = keys.text_to_keys(args.type if args.type is not None else Path(args.type_file).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CliFailure(str(exc), exit_code=2) from exc  # validated BEFORE connecting: nothing is half-sent
        with connect(args) as client:
            n = keys.send_sequence(client, chords, args.delay, args.hold_ms)
        _emit(runtime, {"ok": True, "sent": n}, f"sent {n} key chord(s)")
        return 0

    @guarded
    def ocr_pair(args: Any, runtime: CliRuntime) -> int:
        with connect(args) as client:
            out = capture.ocr_pair(client, Path(args.out), dump_dir=args.dump_dir, blink_mode=not args.no_blink_mode,
                                   pause=not args.no_pause, allow_inexact=args.allow_inexact)
        _emit(runtime, {"ok": True} | out, f"{out['png']} + {out['json']} (exact={out['exact']})")
        return 0

    @guarded
    def probe(args: Any, runtime: CliRuntime) -> int:
        """Report which sources apply right now; always exit 0 when QMP answers (this is the diagnosis verb)."""
        with connect(args) as client:
            res = None
            try:
                res = reader(client, args)()
                tried = res.tried
            except NoExactText as exc:
                tried = exc.tried
            status = client.execute("query-status").get("status")
        obj = {"ok": True, "vm_status": status, "exact_text_available": res is not None,
               "source": res.source if res else None, "tried": tried}
        _emit(runtime, obj)
        return 0

    registry.register(VerbSpec("screen", None, "Write a PNG of the guest display (QMP screendump).", group=VerbGroup.EXPLORATION.value,
                               examples=("vmscreen screen --qmp /run/vm/qmp.sock --out /tmp/s.png",),
                               options=COMMON + (_value(("--out",), "PNG output file.", "FILE", "OUTPUT", required=True),), handler=screen))
    registry.register(VerbSpec(
        "text", None, "Print the console as exact text from the first applicable source (reported); exit 4 when none applies.",
        group=VerbGroup.EXPLORATION.value, examples=("vmscreen text --qmp SOCK --json", "vmscreen text --qmp SOCK --serial-log serial.log --source serial --tail 20"),
        options=COMMON + TEXT_OPTS + (_value(("--tail",), "Only the last N lines.", "N", "OUTPUT", type=int),
                                       _flag(("--cells",), "With --json: per-cell fg/bg/blink (vga-text only).", "OUTPUT")), handler=text))
    registry.register(VerbSpec(
        "wait-text", None, "Poll until PATTERN (regex) appears in the exact text; exit 3 on timeout.", group=VerbGroup.EXPLORATION.value,
        examples=("vmscreen wait-text 'login:' --qmp SOCK --timeout 120",),
        arguments=(ArgumentSpec("pattern", "Regular expression searched in the whole text (multi-line).", metavar="PATTERN"),),
        options=COMMON + TEXT_OPTS + (_value(("--timeout",), "Give up after this many seconds.", "SECONDS", "WAIT", type=float, default=60.0),
                                       _value(("--interval",), "Seconds between polls.", "SECONDS", "WAIT", type=float, default=0.5),
                                       _flag(("--literal",), "Treat PATTERN as a literal string.", "WAIT")), handler=wait_text))
    registry.register(VerbSpec(
        "send", None, "Send keys (QMP send-key) or type a string into the guest.", group=VerbGroup.MODIFICATION.value, mutating=True,
        include_confirmation=False, examples=("vmscreen send --qmp SOCK --keys ret", "vmscreen send --qmp SOCK --type 'root\\n'"),
        options=COMMON + (_value(("--keys",), "ret | down,down,ret | ctrl+alt+f2 (comma = successive, plus = chord; QMP qcodes).", "KEYS", "KEYS"),
                          _value(("--type",), "Type this text (US-layout ASCII; validated before anything is sent).", "TEXT", "KEYS"),
                          _value(("--type-file",), "Type the contents of FILE (keeps secrets out of argv).", "FILE", "KEYS"),
                          _value(("--delay",), "Seconds between successive chords.", "SECONDS", "KEYS", type=float, default=0.05),
                          _value(("--hold-ms",), "Key hold time in milliseconds.", "MS", "KEYS", type=int)), handler=send))
    registry.register(VerbSpec(
        "ocr-pair", None, "Write OUT.png and OUT.json (exact-text sidecar) captured at the same instant, for OCR corpus building: "
        "the guest is paused (QMP stop) while both are read, then resumed; only the VGA text buffer counts as exact for a pair "
        "(exit 4 on a graphical screen unless --allow-inexact).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        examples=("vmscreen ocr-pair --qmp SOCK --out corpus/bios-001",),
        options=COMMON + (_value(("--out",), "Output prefix; .png and .json are appended.", "PREFIX", "OUTPUT", required=True), NO_BLINK,
                          _flag(("--no-pause",), "Do not pause the guest (pair may straddle a redraw).", "OUTPUT"),
                          _flag(("--allow-inexact",), "On a graphical screen write PNG + sidecar with exact=false instead of exit 4.", "OUTPUT")),
        handler=ocr_pair))
    registry.register(VerbSpec("probe", None, "Report which exact-text sources apply right now and why the others do not.",
                               group=VerbGroup.EXPLORATION.value, options=COMMON + TEXT_OPTS, handler=probe))
    register_skills_verbs(registry, package="vmscreen")
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)

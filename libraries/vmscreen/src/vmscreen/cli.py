"""The command line: a thin cli-extended shell over the pure core.

Exit codes: 0 ok, 1 other failure, 2 usage, 3 wait-text timeout, 4 no exact text source applies, 5 QMP unreachable/error.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from cli_extended import (ArgumentSpec, CliFailure, CliIdentity, CliRegistry, CliRuntime, OptionSpec, RegisteredCli, VerbGroup, VerbSpec,
                          register_skills_verbs)

from . import capture, keys, sources, textmode, waiter
from .errors import NoExactText, VmscreenError, WaitTimeout
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
NO_BLINK = _flag(("--no-blink-mode",), "Decode attribute bit 7 as a bright background instead of blink. QMP cannot read the attribute-controller "
                 "mode bit, so this is a declared assumption (recorded in the output), not a detection.", "SOURCES")
FROM_OFFSET = _value(("--from-offset",), "Serial source: ignore the first N bytes of the log (see `vmscreen mark`).", "BYTES", "SOURCES", type=int)
COMMON = (QMP, QMP_TIMEOUT, DUMP_DIR)
MARK = _value(("--mark",), "Serial source: a token from `vmscreen mark`; like --from-offset, and additionally refuses a log that was "
              "truncated or replaced since (inode and first bytes are checked).", "TOKEN", "SOURCES")
TEXT_OPTS = (SERIAL, SOURCE, NO_BLINK, FROM_OFFSET, MARK)


def _emit(runtime: CliRuntime, obj: Any, human: str | None = None) -> None:
    if runtime.json_mode or human is None:
        runtime.output.primary(obj if runtime.json_mode else json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))
    else:
        runtime.output.primary(human)


def _fail(exc: VmscreenError, runtime: CliRuntime) -> CliFailure:
    """Exit-coded failure; in --json mode the structured detail still goes to stdout."""
    notes = "; ".join(getattr(exc, "__notes__", ()))
    extra: dict[str, Any] = {"ok": False, "error": str(exc) + (f" [{notes}]" if notes else ""), "exit_code": exc.exit_code}
    if isinstance(exc, NoExactText):
        extra.update(exact=False, tried=exc.tried)
    if isinstance(exc, WaitTimeout):
        extra["last_text"] = exc.last_text
    if runtime.json_mode:
        runtime.output.primary(extra)
    return CliFailure(extra["error"], exit_code=exc.exit_code)


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

    def reader(client: QmpClient, args: Any, mark: str | None = None) -> Callable[[], sources.TextResult]:
        mark = mark or args.mark
        if mark is not None and args.from_offset is not None:
            raise CliFailure("--mark (or --since-mark) and --from-offset both set the start: give only one", exit_code=2)
        if args.from_offset is not None and args.from_offset < 0:
            raise CliFailure("--from-offset must be >= 0", exit_code=2)
        if mark is not None:
            sources.parse_mark(mark)  # malformed token: usage error (ValueError -> exit 2)
        return lambda: sources.read_text(client, source=args.source, serial_log=args.serial_log, dump_dir=args.dump_dir,
                                         blink_mode=not args.no_blink_mode, from_offset=args.from_offset or 0, mark=mark)

    def positive(name: str, value: float | None, *, allow_zero: bool = False) -> None:
        if value is not None and (value < 0 or (value == 0 and not allow_zero)):
            raise CliFailure(f"{name} must be {'>= 0' if allow_zero else '> 0'}, got {value:g}", exit_code=2)

    def guarded(fn: Callable[[Any, CliRuntime], int]) -> Callable[[Any, CliRuntime], int]:
        def run(args: Any, runtime: CliRuntime) -> int:
            try:
                return fn(args, runtime)
            except VmscreenError as exc:
                raise _fail(exc, runtime) from exc
            except ValueError as exc:
                raise CliFailure(str(exc), exit_code=2) from exc
            except OSError as exc:  # unwritable --out, unreadable --serial-log directory, ...
                raise CliFailure(f"{type(exc).__name__}: {exc}", exit_code=1) from exc
        return run

    @guarded
    def screen(args: Any, runtime: CliRuntime) -> int:
        positive("--qmp-timeout", args.qmp_timeout)
        with connect(args) as client:
            png = sources.screendump_png(client, args.dump_dir)
        Path(args.out).write_bytes(png)
        w, h = textmode.png_size(png)
        _emit(runtime, {"ok": True, "png": args.out, "width": w, "height": h}, f"{args.out} {w}x{h}")
        return 0

    @guarded
    def text(args: Any, runtime: CliRuntime) -> int:
        positive("--qmp-timeout", args.qmp_timeout)
        if args.tail is not None and args.tail < 1:
            raise CliFailure("--tail must be >= 1", exit_code=2)
        with connect(args) as client:
            res = reader(client, args)()
        if args.tail is not None:  # one definition for every source: the last N lines of the text
            res.text = "\n".join(res.text.split("\n")[-args.tail:])
        _emit(runtime, {"ok": True, "tail": args.tail} | res.as_dict(include_cells=args.cells), res.text)
        if not runtime.json_mode:
            label = {True: "exact", "unverified": f"UNVERIFIED: {res.exact_reason}", "partial": f"PARTIAL: {res.exact_reason}", "stream": "serial stream, not a screen"}.get(res.exact, str(res.exact))
            runtime.output.info(f"source: {res.source} ({res.scope}, {label})")
        return 0

    @guarded
    def wait_text(args: Any, runtime: CliRuntime) -> int:
        try:  # before any connection: a bad pattern is a usage error, not a QMP error
            re.compile(re.escape(args.pattern) if args.literal else args.pattern)
        except re.error as exc:
            raise CliFailure(f"invalid regular expression {args.pattern!r}: {exc}", exit_code=2) from exc
        positive("--qmp-timeout", args.qmp_timeout)
        positive("--interval", args.interval)
        positive("--timeout", args.timeout, allow_zero=True)
        mark = args.mark
        if args.since_mark:
            if not args.serial_log:
                raise CliFailure("--since-mark needs --serial-log", exit_code=2)
            if args.mark is not None or args.from_offset is not None:
                raise CliFailure("--since-mark cannot be combined with --mark or --from-offset", exit_code=2)
            mark = sources.make_mark(args.serial_log)  # only output written from now on can satisfy the wait
        real_offset = sources.parse_mark(mark)[0] if mark else (args.from_offset or 0)
        with connect(args) as client:
            out = waiter.wait_for(reader(client, args, mark), args.pattern, args.timeout, args.interval, literal=args.literal,
                                  accept_unverified=args.accept_unverified, accept_partial=args.accept_partial)
        _emit(runtime, {"ok": True, "mark": mark, "from_offset": real_offset} | out, f"matched {out['matched']!r} via {out['source']} ({out['exact']}) after {out['elapsed_s']}s")
        return 0

    @guarded
    def mark(args: Any, runtime: CliRuntime) -> int:
        token = sources.make_mark(args.serial_log)
        size = sources.parse_mark(token)[0]
        _emit(runtime, {"ok": True, "serial_log": args.serial_log, "offset": size, "mark": token}, token)
        return 0

    @guarded
    def send(args: Any, runtime: CliRuntime) -> int:
        if sum(x is not None for x in (args.keys, args.type, args.type_file)) != 1:
            raise CliFailure("give exactly one of --keys, --type, --type-file", exit_code=2)
        positive("--qmp-timeout", args.qmp_timeout)
        positive("--delay", args.delay, allow_zero=True)
        positive("--hold-ms", args.hold_ms, allow_zero=True)
        try:  # everything is validated BEFORE connecting: nothing is half-sent
            if args.keys is not None:
                chords = keys.parse_keys(args.keys)
                if not args.allow_unknown_keys:
                    keys.validate_keys(chords)
            else:
                chords = keys.text_to_keys(args.type if args.type is not None else Path(args.type_file).read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise CliFailure(str(exc), exit_code=2) from exc
        with connect(args) as client:
            n = keys.send_sequence(client, chords, args.delay, args.hold_ms)
        _emit(runtime, {"ok": True, "sent": n}, f"sent {n} key chord(s)")
        return 0

    @guarded
    def ocr_pair(args: Any, runtime: CliRuntime) -> int:
        positive("--qmp-timeout", args.qmp_timeout)
        with connect(args) as client:
            out = capture.ocr_pair(client, Path(args.out), dump_dir=args.dump_dir, blink_mode=not args.no_blink_mode,
                                   pause=not args.no_pause, allow_inexact=args.allow_inexact, accept_partial=args.accept_partial, force=args.force,
                                   reconnect=lambda: connect(args))
        _emit(runtime, {"ok": True} | out, f"{out['png']} + {out['json']} (exact={out['exact']})")
        return 0

    @guarded
    def probe(args: Any, runtime: CliRuntime) -> int:
        """Report which sources apply right now; always exit 0 when QMP answers (this is the diagnosis verb)."""
        positive("--qmp-timeout", args.qmp_timeout)
        with connect(args) as client:
            res = None
            try:
                res = reader(client, args)()
                tried = res.tried
            except NoExactText as exc:
                tried = exc.tried
            status = client.execute("query-status").get("status")
        obj = {"ok": True, "vm_status": status, "exact_text_available": res is not None and res.exact in (True, "stream"),
               "exact": res.exact if res else False, "exact_reason": res.exact_reason if res else "",
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
                                       _flag(("--literal",), "Treat PATTERN as a literal string.", "WAIT"),
                                       _flag(("--since-mark",), "Serial source: only output written after this command started can match (a previous boot's "
                                             "'login:' cannot satisfy the wait).", "WAIT"),
                                       _flag(("--accept-unverified",), "Also match VGA text whose cross-check against the screendump failed.", "WAIT"),
                                       _flag(("--accept-partial",), "Also match VGA text with uncertain cells (shown as U+FFFD).", "WAIT")),
        handler=wait_text))
    registry.register(VerbSpec("mark", None, "Print a mark of a serial log (OFFSET.INODE.HEADLEN.HEADHASH.TAILLEN.TAILHASH): --mark TOKEN skips everything written so far and rejects a log replaced since.",
                               group=VerbGroup.EXPLORATION.value, examples=("vmscreen mark --serial-log serial.log",),
                               options=(_value(("--serial-log",), "The serial log file.", "FILE", "SOURCES", required=True),), handler=mark))
    registry.register(VerbSpec(
        "send", None, "Send keys (QMP send-key) or type a string into the guest.", group=VerbGroup.MODIFICATION.value, mutating=True,
        include_confirmation=False, examples=("vmscreen send --qmp SOCK --keys ret", "vmscreen send --qmp SOCK --type 'root\\n'"),
        options=COMMON + (_value(("--keys",), "ret | down,down,ret | ctrl+alt+f2 (comma = successive, plus = chord; QMP qcodes).", "KEYS", "KEYS"),
                          _value(("--type",), "Type this text (US-layout ASCII; validated before anything is sent).", "TEXT", "KEYS"),
                          _value(("--type-file",), "Type the contents of FILE (keeps secrets out of argv).", "FILE", "KEYS"),
                          _value(("--delay",), "Seconds between successive chords.", "SECONDS", "KEYS", type=float, default=0.05),
                          _value(("--hold-ms",), "Key hold time in milliseconds.", "MS", "KEYS", type=int),
                          _flag(("--allow-unknown-keys",), "Do not validate --keys names against the known QEMU qcodes.", "KEYS")), handler=send))
    registry.register(VerbSpec(
        "ocr-pair", None, "Write OUT.png and OUT.json (exact-text sidecar) captured at the same instant, for OCR corpus building: "
        "the guest is paused (QMP stop) while both are read, then resumed; only the VGA text buffer counts as exact for a pair "
        "(exit 4 on a graphical screen unless --allow-inexact).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        examples=("vmscreen ocr-pair --qmp SOCK --out corpus/bios-001",),
        options=COMMON + (_value(("--out",), "Output prefix; .png and .json are appended.", "PREFIX", "OUTPUT", required=True), NO_BLINK,
                          _flag(("--no-pause",), "Do not pause the guest (pair may straddle a redraw).", "OUTPUT"),
                          _flag(("--allow-inexact",), "On a graphical or UNVERIFIED screen write PNG + sidecar with the exact flag/reason instead of exit 4.", "OUTPUT"),
                          _flag(("--accept-partial",), "Accept a screen whose cells are all verified except cursor-shaped uncertain cells (listed in the sidecar).", "OUTPUT"),
                          _flag(("--force",), "Overwrite an existing PREFIX.png / PREFIX.json.", "OUTPUT")),
        handler=ocr_pair))
    registry.register(VerbSpec("probe", None, "Report which exact-text sources apply right now and why the others do not.",
                               group=VerbGroup.EXPLORATION.value, options=COMMON + TEXT_OPTS, handler=probe))
    register_skills_verbs(registry, package="vmscreen")
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)

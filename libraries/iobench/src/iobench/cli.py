"""The command line: a thin cli-extended shell over the measurement core (IOB-CLI-01/02/10).

Only this module imports ``cli_extended``; the core stays standard-library only.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
from typing import Any, Callable

from cli_extended import (ArgumentSpec, CliFailure, CliIdentity, CliRegistry, CliRuntime, OptionSpec, RegisteredCli, Requires, VerbGroup,
                          VerbSpec)

from . import calibrate, capability, ceilings, dryrun, guard, matrix, proposals, restore
from . import result as res
from .errors import IobenchError, Refusal
from .host import Host, HostError, RealHost, SEEK_EXTENT_FILESYSTEMS
from .series import SeriesError
from .stages import Config

HostFactory = Callable[[], Host]
PROFILES = ("veryquick", "quick", "standard", "thorough")
KNOBS = ("dirty_bytes", "nr_requests", "max_sectors_kb")
SECTIONS = proposals.SECTIONS


def identity() -> CliIdentity:
    """The version is the installed distribution's; there is no literal fallback (an uninstalled tree fails loudly)."""
    return CliIdentity.resolve(name="iobench", long_name="cache-proof disk benchmark", command="iobench", distribution="iobench")


# -- option sets -------------------------------------------------------------------------------------------
def _flag(flags: tuple[str, ...], text: str, group: str = "OPTIONS", **kw: Any) -> OptionSpec:
    return OptionSpec(flags, text, group=group, parser_kwargs={"action": "store_true", **kw})


def _value(flags: tuple[str, ...], text: str, metavar: str, group: str = "OPTIONS", **kw: Any) -> OptionSpec:
    return OptionSpec(flags, text, group=group, metavar=metavar, parser_kwargs={"default": None, **kw})


def _file_size_arg(value: str) -> int:
    """Parse and validate the file extent before target init can create or preallocate it."""
    try:
        size = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--size must be an integer number of bytes") from None
    mib = 1 << 20
    if size <= 0 or size % mib:
        aligned = max(mib, size // mib * mib)
        raise argparse.ArgumentTypeError(f"--size must be a positive multiple of 1 MiB; use {aligned} bytes or another 1 MiB multiple")
    if size < guard.MIN_TARGET_SIZE_BYTES:
        raise argparse.ArgumentTypeError(f"--size must be at least {guard.MIN_TARGET_SIZE_BYTES} bytes "
                                          "(1 GiB usable plus the reserved 1 MiB tail for calibration)")
    return size


STATE_DIR = _value(("--state-dir",), "Directory of result.json, calibration.json, ceilings.jsonl and capability.jsonl (default: $IOBENCH_DIR, else /var/lib/iobench).", "DIR", "STATE")
GUARDS = (
    _flag(("--confirm-quiet-window",), "Confirm that production work may be disturbed now (required on a live host; unlisted systemd units are not detected).", "LIVE HOST"),
    _value(("--protect-cgroup",), "Cgroup whose io.pressure is watched; a live-host run needs it with --abort-psi (no default).", "CGROUP", "LIVE HOST"),
    _value(("--abort-psi",), "Abort when io.pressure some avg10 of the protected cgroup exceeds this for 3 consecutive samples.", "PERCENT", "LIVE HOST", type=float),
    _flag(("--installer",), "Installer run: waives only the live-host refusals; every target guard still applies.", "LIVE HOST"),
    _value(("--production-pattern",), "Regular expression selecting the running containers that count as production (default: any running container).", "REGEX", "LIVE HOST"),
    _value(("--production-unit",), "A systemd unit that counts as production when active (repeatable).", "UNIT", "LIVE HOST", action="append"),
)
TARGET_KNOBS = (
    _value(("--w-seconds",), "Worst-case sync wait W in seconds (policy default 2).", "SECONDS", "TARGETS", type=float),
    _value(("--p-ms",), "Probe fdatasync p99 acceptance threshold P in milliseconds (policy default 250).", "MS", "TARGETS", type=float),
    _value(("--f-read-ratio",), "Smallest acceptable sequential read ratio F (policy default 0.25).", "RATIO", "TARGETS", type=float),
    _value(("--u-dev-share",), "Dev aggregate share U of the measured basis (policy default 0.70).", "SHARE", "TARGETS", type=float),
)
RUN_OPTIONS = (
    OptionSpec(("--target",), "The prepared target: a device path or a file target (see `target init`).", group="RUN", metavar="TARGET",
               parser_kwargs={"required": True}),
    _value(("--work-dir",), "An existing, empty directory on an existing filesystem; leave max(4 GiB, 3x planned cell volume) free.", "DIR", "RUN"),
    STATE_DIR, *GUARDS, *TARGET_KNOBS,
    _value(("--governor-histogram",), "A governor latency histogram file; recorded as the latency source.", "FILE", "RUN"),
)
CAL_OPTIONS = (
    _value(("--profile",), "Calibration profile (veryquick 3, quick 12, standard 60, thorough 75 minutes).", "PROFILE", "RUN", choices=PROFILES, default="standard"),
    _value(("--budget-minutes",), "Hard wall-clock budget in minutes; overrides the profile.", "MINUTES", "RUN", type=float),
    _value(("--cache-idle-delay-s",), "Idle delay before optional direct read-back evidence (defaults: 30 s standard, 60 s thorough; 0 disables; maximum: 230 s standard, 290 s thorough).", "SECONDS", "RUN", type=float),
    _value(("--metering",), "Typed metering override; dependent values become unproven: operator_override.", "KIND", "RUN", choices=("byte", "iops", "mixed")),
    _value(("--assume-r-est",), "With --dry-run: the write rate (bytes/s) to plan with; otherwise READ from calibration.json.", "BYTES_PER_S", "RUN", type=float),
)
MATRIX_OPTIONS = (
    _value(("--budget-minutes",), "Hard wall-clock budget in minutes; overrides the profile.", "MINUTES", "RUN", type=float),
    _flag(("--full-sweep",), "Eight sweep cells instead of six.", "SWEEP"),
    _value(("--knob",), "Compare one knob by interleaved A B A B cells instead of the default plan.", "NAME", "SWEEP", choices=KNOBS),
    _value(("--candidates",), "Comma list of 2-3 candidate values for --knob.", "A,B[,C]", "SWEEP"),
    _flag(("--repeat",), "Repeat the candidate cells (A B A B) so a decision can be made.", "SWEEP"),
)
MATRIX_CONSTRAINTS = (Requires("--candidates", ("--knob",), "candidates only make sense for a knob comparison"),
                      Requires("--repeat", ("--knob",), "--repeat only makes sense for a knob comparison"),
                      Requires("--knob", ("--candidates",), "a knob comparison needs --candidates"))


def _config(args: Any, version: str, *, profile: str = "standard") -> Config:
    targets = dict(proposals.DEFAULT_TARGETS)
    for key, attr in (("W_s", "w_seconds"), ("P_ms", "p_ms"), ("F_read_ratio", "f_read_ratio"), ("U_dev_share", "u_dev_share")):
        if getattr(args, attr, None) is not None:
            targets[key] = getattr(args, attr)
    candidates: tuple[int, ...] = ()
    if getattr(args, "candidates", None):
        try:
            candidates = tuple(int(x) for x in args.candidates.split(","))
        except ValueError:
            raise Refusal("candidates", f"--candidates {args.candidates!r} is not a comma list of integers", "for example --candidates 64,128,256") from None
    return Config(target=args.target, tool_version=version, work_dir=args.work_dir, profile=getattr(args, "profile", None) or profile,
                  budget_minutes=getattr(args, "budget_minutes", None), cache_idle_delay_s=getattr(args, "cache_idle_delay_s", None),
                  state_dir=args.state_dir, confirm_quiet_window=args.confirm_quiet_window,
                  protect_cgroup=args.protect_cgroup, abort_psi=args.abort_psi, installer=args.installer,
                  production_pattern=args.production_pattern, production_units=tuple(args.production_unit or ()),
                  metering=getattr(args, "metering", None), full_sweep=getattr(args, "full_sweep", False), knob=getattr(args, "knob", None),
                  candidates=candidates, repeat=getattr(args, "repeat", False), governor_histogram=args.governor_histogram,
                  assume_r_est=getattr(args, "assume_r_est", None), targets=targets)


# -- output -------------------------------------------------------------------------------------------------
def _emit(runtime: CliRuntime, obj: Any) -> None:
    """The primary result: compact JSON with --json, indented JSON otherwise (it IS the object)."""
    runtime.output.primary(obj if runtime.json_mode else json.dumps(obj, indent=1, sort_keys=True))


def _outcome(runtime: CliRuntime, out: Any) -> int:
    summary = {"status": out.status, "exit_code": out.exit_code, "message": out.message,
               "run": out.result["run"] if out.result else None}
    if runtime.json_mode:
        runtime.output.primary(summary)
    else:
        runtime.output.info(f"{out.status}: {out.message}" if out.message else out.status)
    return out.exit_code


def _load_failure(exc: res.LoadError) -> CliFailure:
    return CliFailure(f"{exc.reason}: {exc.path}", exit_code=4 if exc.reason == "absent" else 5)


# -- handlers -------------------------------------------------------------------------------------------------
def build_cli(cli_identity: CliIdentity | None = None, host_factory: HostFactory = RealHost) -> RegisteredCli:
    ident = cli_identity or identity()
    registry = CliRegistry(ident, prog="iobench", description="Cache-proof disk benchmark: every proposed value says how far it can be trusted.",
                           getting_started=("iobench target init /dev/vda7 --i-own-this-device --expect-size BYTES --expect-partuuid ID",
                                            "iobench calibrate --target /dev/vda7 --work-dir DIR --dry-run --assume-r-est 1360000000",
                                            "iobench check --json"),
                           logging_logger="iobench", unexpected_exceptions="report",
                           expected_exceptions=(IobenchError, HostError, res.LoadError, SeriesError))

    # target (delegated: init, show)
    target_registry = CliRegistry(ident, prog="iobench target", description="Prepare and inspect targets of at least 1 GiB usable plus the reserved 1 MiB tail.",
                                  unexpected_exceptions="report", expected_exceptions=(IobenchError, HostError, res.LoadError, SeriesError))
    target_registry.register(VerbSpec(
        "init", None, "Write the iobench marker to a dedicated device (or create a file target); raw-device mutation stages are currently refused.",
        group=VerbGroup.MODIFICATION.value, mutating=True, dry_run=True, include_confirmation=False,
        examples=("iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007",
                  "iobench target init --file /var/lib/iobench/target.img --size 6442450944 --i-own-this-file --expect-fs-uuid UUID --confirm-quiet-window"),
        arguments=(ArgumentSpec("device", "Block device or partition (omit with --file).", metavar="DEVICE", parser_kwargs={"nargs": "?"}),),
        options=(_flag(("--i-own-this-device",), "You own this device and accept that iobench overwrites its last MiB marker. Raw-device write/discard stages are currently refused because fio cannot use the same held O_EXCL descriptor.", "DEVICE TARGET"),
                 _value(("--expect-size",), f"The exact device size in bytes; targets need at least {guard.MIN_TARGET_SIZE_BYTES} bytes.", "BYTES", "DEVICE TARGET", type=int),
                 _value(("--expect-partuuid",), "The PARTUUID; must equal the actual one.", "PARTUUID", "DEVICE TARGET"),
                 _value(("--expect-serial",), "The serial, when the device reports one.", "SERIAL", "DEVICE TARGET"),
                 _value(("--file",), "Create a file target at this path on an existing filesystem.", "PATH", "FILE TARGET"),
                 _value(("--size",), f"File target size in bytes (required; upper bound of writes; at least {guard.MIN_TARGET_SIZE_BYTES} bytes and a 1 MiB multiple).", "BYTES", "FILE TARGET", type=_file_size_arg),
                 _flag(("--i-own-this-file",), "You own this path and accept that iobench creates and overwrites it.", "FILE TARGET"),
                 _value(("--expect-fs-uuid",), "The filesystem UUID; must equal the actual one.", "UUID", "FILE TARGET"),
                 _flag(("--confirm-quiet-window",), "Required for a file target on the root filesystem.", "FILE TARGET"),
                 _flag(("--allow-large-root-target",), "Allow --size above one tenth of the free space of the root filesystem.", "FILE TARGET")),
        handler=lambda args, runtime: _target_init(args, runtime, host_factory)))
    target_registry.register(VerbSpec(
        "show", None, "Read the marker and live facts of a prepared target.", group=VerbGroup.EXPLORATION.value,
        arguments=(ArgumentSpec("path", "The device or file target.", metavar="TARGET"),),
        handler=lambda args, runtime: _target_show(args, runtime, host_factory)))
    registry.register(VerbSpec("target", None, "Prepare and inspect the dedicated target (init, show).", group=VerbGroup.MODIFICATION.value,
                               delegate=target_registry.build()))

    registry.register(VerbSpec(
        "calibrate", None, "Measure a host or storage class once: cold-region probe, ladder, time-axis write proof, fresh vs rewrite, cost shape, latency curve.",
        group=VerbGroup.MODIFICATION.value, mutating=True, expensive=True, dry_run=True, include_confirmation=False,
        dry_run_help="print each stage's allotment, fio argv and written-byte bound; write nothing",
        examples=("iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40",),
        options=RUN_OPTIONS + CAL_OPTIONS, handler=lambda args, runtime: _calibrate(args, runtime, ident, host_factory)))
    registry.register(VerbSpec(
        "matrix", None, "Iterate on a current calibration: drain rate, a 6-cell interference sweep, read cost; or compare one knob.",
        group=VerbGroup.MODIFICATION.value, mutating=True, expensive=True, dry_run=True, include_confirmation=False,
        dry_run_help="print each stage's allotment, fio argv and written-byte bound; write nothing",
        examples=("iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40",
                  "iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --knob max_sectors_kb --candidates 64,128,256 --repeat --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40"),
        options=RUN_OPTIONS + MATRIX_OPTIONS, constraints=MATRIX_CONSTRAINTS,
        handler=lambda args, runtime: _matrix(args, runtime, ident, host_factory)))
    registry.register(VerbSpec(
        "run", None, "Calibrate when no current calibration exists, then run the matrix.", group=VerbGroup.MODIFICATION.value, mutating=True,
        expensive=True, dry_run=True, include_confirmation=False, dry_run_help="print the plan of the calibration (or matrix) that would run; write nothing",
        options=RUN_OPTIONS + CAL_OPTIONS + MATRIX_OPTIONS[1:2], handler=lambda args, runtime: _run(args, runtime, ident, host_factory)))
    registry.register(VerbSpec(
        "check", None, "Is the result usable? Exit 0 every requested proposal proven, 3 stale, 4 absent, 5 unreadable, 6 only a failed record, 7 valid but some unproven.",
        group=VerbGroup.EXPLORATION.value,
        examples=("iobench check --json", "iobench check --section caps --json"),
        options=(_value(("--result",), "Check this result file instead of the one in the state directory.", "PATH"),
                 _flag(("--calibration",), "Check calibration.json instead of result.json."),
                 _value(("--section",), "Only this proposal group (repeatable).", "GROUP", choices=SECTIONS, action="append"),
                 _flag(("--require-strong-identity",), "Treat a weak identity (no serial or wwn) as identity_weak on every value."), STATE_DIR),
        handler=lambda args, runtime: _check(args, runtime, host_factory)))
    registry.register(VerbSpec(
        "capability", None, "Read the append-only cache and burst observations for the calibrated disk.",
        group=VerbGroup.EXPLORATION.value, examples=("iobench capability --json", "iobench capability --kind raw --since 2026-11-01T00:00:00Z --json"),
        options=(_value(("--since",), "Include observations at or after this UTC timestamp (YYYY-MM-DDTHH:MM:SSZ).", "TIMESTAMP"),
                 _value(("--kind",), "Show only this evidence kind; by default all kinds are summarised separately.", "KIND", choices=capability.KINDS), STATE_DIR),
        handler=lambda args, runtime: _capability(args, runtime, host_factory)))
    registry.register(VerbSpec(
        "show", None, "Print a stored object exactly (never recomputes, never modifies).", group=VerbGroup.EXPLORATION.value,
        examples=("iobench show --proposals --json", "iobench show --ceilings --json"),
        options=tuple(OptionSpec((f"--{w}",), f"Print the {w} object.", group="WHAT", parser_kwargs={"action": "store_true"},
                                 mutually_exclusive_group="what", mutually_exclusive_required=True) for w in res.SHOW_WHAT)
        + (_value(("--section",), "Only this proposal group (with --proposals).", "GROUP", choices=SECTIONS), STATE_DIR),
        handler=lambda args, runtime: _show(args, runtime, host_factory)))

    ceil = CliRegistry(ident, prog="iobench ceilings", description="The ceiling history.", unexpected_exceptions="report",
                       expected_exceptions=(IobenchError, HostError, res.LoadError, SeriesError))
    ceil.register(VerbSpec(
        "add", None, "Append one plateau observation (governor, probe or benchmark) to the ceiling history.", group=VerbGroup.MODIFICATION.value,
        mutating=True, dry_run=True, include_confirmation=False,
        examples=("iobench ceilings add --dir write --workload seq1m --bps 14680064 --duration-s 600 --source governor",),
        options=(OptionSpec(("--dir",), "Direction.", group="OBSERVATION", metavar="DIR", parser_kwargs={"required": True, "choices": ceilings.DIRS}),
                 OptionSpec(("--workload",), "Workload.", group="OBSERVATION", metavar="WORKLOAD", parser_kwargs={"required": True, "choices": ceilings.WORKLOADS}),
                 OptionSpec(("--bps",), "Sustained plateau in bytes/s.", group="OBSERVATION", metavar="BYTES_PER_S", parser_kwargs={"required": True, "type": float}),
                 OptionSpec(("--duration-s",), "Observed seconds (>= 60).", group="OBSERVATION", metavar="SECONDS", parser_kwargs={"required": True, "type": float}),
                 OptionSpec(("--source",), "Who observed it.", group="OBSERVATION", metavar="SOURCE", parser_kwargs={"required": True, "choices": ceilings.SOURCES}),
                 _value(("--kind",), "What it was measured on: raw (device), file or probe (default: the calibrated target's kind for a benchmark, probe for a probe; "
                        "a governor must say).", "KIND", "OBSERVATION", choices=ceilings.KINDS),
                 STATE_DIR),
        handler=lambda args, runtime: _ceilings_add(args, runtime, host_factory)))
    ceil.register(VerbSpec(
        "summary", None, "Worst, median, best and n per direction and workload over a window (and per evidence class).", group=VerbGroup.EXPLORATION.value,
        options=(_value(("--window-days",), "Window in days.", "DAYS", type=int, default=30),
                 _value(("--kind",), "Summarise only the observations of this class (raw, file, probe), whatever they count for.", "KIND", choices=ceilings.KINDS),
                 STATE_DIR),
        handler=lambda args, runtime: _ceilings_summary(args, runtime, host_factory)))
    registry.register(VerbSpec("ceilings", None, "The append-only ceiling history (add, summary).", group=VerbGroup.MIXED.value, delegate=ceil.build()))

    registry.register(VerbSpec(
        "restore", None, "Restore every runtime knob a dead run left changed (idempotent; a value someone else changed is left alone).",
        group=VerbGroup.MAINTENANCE.value, mutating=True, dry_run=True, include_confirmation=False, options=(STATE_DIR,),
        handler=lambda args, runtime: _restore(args, runtime, host_factory)))
    return registry.build()


def _target_init(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    created = ceilings.iso(host.now())
    if args.file:
        t = guard.init_file(host, args.file, size=args.size, own=args.i_own_this_file, expect_fs_uuid=args.expect_fs_uuid,
                            confirm_quiet_window=args.confirm_quiet_window, allow_large_root_target=args.allow_large_root_target,
                            created=created, dry_run=runtime.dry_run)
    else:
        if args.device is None:
            raise Refusal("device_required", "no device named", "pass DEVICE, or --file PATH --size BYTES for a file target")
        t = guard.init_device(host, args.device, own=args.i_own_this_device, expect_size=args.expect_size, expect_partuuid=args.expect_partuuid,
                              expect_serial=args.expect_serial, created=created, dry_run=runtime.dry_run)
    _emit(runtime, {"target": t.path, "kind": t.kind, "size_bytes": t.size_bytes, "marker_written": not runtime.dry_run,
                    **_target_status(host, t, dry_run=runtime.dry_run),
                    "identity": {"devno": t.devno, "partuuid": t.partuuid, "serial": t.serial, "fs_uuid": t.fs_uuid, "inode": t.inode}})
    return 0


def _target_show(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    t = guard.load_target(host, args.path)
    _emit(runtime, {"target": t.path, "kind": t.kind, "size_bytes": t.size_bytes, **_target_status(host, t),
                    "identity": {"devno": t.devno, "partuuid": t.partuuid, "serial": t.serial, "fs_uuid": t.fs_uuid, "inode": t.inode}})
    return 0


def _target_status(host: Host, target: guard.Target, *, dry_run: bool = False) -> dict[str, int | str | None]:
    """The usable extent and logical data-extent coverage, with an explicit reason when no number is available."""
    if dry_run:
        written, status = None, "not measured: dry run creates no file extent map"
    elif target.kind != "file":
        written, status = None, "unavailable: a raw device has no filesystem extent map"
    elif target.fs_type not in SEEK_EXTENT_FILESYSTEMS:
        supported = ", ".join(sorted(SEEK_EXTENT_FILESYSTEMS))
        written, status = None, f"unavailable: filesystem type {target.fs_type!r} is not verified for SEEK_DATA/SEEK_HOLE; supported types: {supported}"
    else:
        written = host.written_bytes(target.ref, 0, target.usable_bytes)
        status = f"logical SEEK_DATA/SEEK_HOLE data-extent coverage on {target.fs_type}"
    return {"usable_bytes": target.usable_bytes, "written_bytes": written,
            "unwritten_bytes": None if written is None else target.usable_bytes - written,
            "written_bytes_status": status}


def _calibrate(args: Any, runtime: CliRuntime, ident: CliIdentity, factory: HostFactory) -> int:
    host, cfg = factory(), _config(args, ident.version)
    if runtime.dry_run:
        _emit(runtime, dryrun.calibrate_plan(host, cfg))
        return 0
    return _outcome(runtime, calibrate.Calibrator(host, cfg).execute())


def _matrix(args: Any, runtime: CliRuntime, ident: CliIdentity, factory: HostFactory) -> int:
    host, cfg = factory(), _config(args, ident.version)
    if cfg.knob is not None and len(cfg.candidates) not in (2, 3):
        raise Refusal("candidates", "a knob comparison needs two or three --candidates", "for example --candidates 64,128")
    if runtime.dry_run:
        _emit(runtime, dryrun.matrix_plan(host, cfg))
        return 0
    return _outcome(runtime, matrix.Matrixer(host, cfg).execute())


def _run(args: Any, runtime: CliRuntime, ident: CliIdentity, factory: HostFactory) -> int:
    host, cfg = factory(), _config(args, ident.version)
    state = res.state_dir_of(host, cfg.state_dir)
    current = res.check(host, state, calibration=True, now=host.now()).state in ("current", "current_unproven")
    if runtime.dry_run:
        _emit(runtime, dryrun.matrix_plan(host, cfg) if current else dryrun.calibrate_plan(host, cfg))
        return 0
    if not current:
        out = calibrate.Calibrator(host, cfg).execute()
        if out.exit_code != 0:
            return _outcome(runtime, out)
    return _outcome(runtime, matrix.Matrixer(host, cfg).execute())


def _check(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    state = res.state_dir_of(host, args.state_dir)
    result_path = args.result
    out = res.check(host, state, calibration=args.calibration, sections=tuple(args.section) if args.section else None, now=host.now(),
                    require_strong_identity=args.require_strong_identity, result_path=result_path)
    _emit(runtime, out.as_dict())
    return out.exit_code


def _capability(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    state = res.state_dir_of(host, args.state_dir)
    p = res.paths(state)
    warnings: list[str] = []
    try:
        identity = _ceilings_identity(host, state)
        digest = ceilings.identity_digest(identity)
        rows, warnings, skipped_rows = capability.load(host, p["capability"], digest)
    except (res.LoadError, Refusal):
        rows, digest, skipped_rows = [], None, 0
        warnings.append("calibration_identity_unavailable")
    selected = capability.select_since(rows, args.since)
    if args.kind is not None:
        selected = [row for row in selected if row["kind"] == args.kind]
    kinds = (args.kind,) if args.kind is not None else capability.KINDS
    out = {"schema": capability.SCHEMA, "identity_digest": digest, "since": args.since,
           **capability.latest_summary(selected, kinds=kinds, skipped_rows=skipped_rows), "observations": selected}
    if warnings:
        out["warnings"] = warnings
    _emit(runtime, out)
    return 0


def _show(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    what = next(w for w in res.SHOW_WHAT if getattr(args, w))
    try:
        shown = res.show(host, res.state_dir_of(host, args.state_dir), what, section=args.section, now=host.now())
    except res.LoadError as exc:
        raise _load_failure(exc) from exc
    if what == "proposals":
        body = shown["object"]
        annotate = lambda v: proposals.annotate_apply(v) if isinstance(v, dict) else v  # noqa: E731  (notes are a list: nothing to apply)
        shown = {"object": annotate(body) if args.section else {k: annotate(v) for k, v in body.items()}, "stale": shown["stale"]}
    _emit(runtime, shown)
    return 0


def _ceilings_identity(host: Host, state: str) -> dict[str, Any]:
    return res.load(host, res.paths(state)["calibration"], res.REQUIRED_CAL_TOP)["identity"]


def _observation_kind(args: Any, identity: dict[str, Any]) -> str:
    """``--kind``; else a benchmark is what the calibrated target is, a probe is a probe; a governor has to say what it measured on."""
    if args.kind is not None:
        return args.kind
    if args.source == "benchmark":
        return ceilings.TARGET_KIND[identity["target"]["kind"]]
    if args.source == "probe":
        return "probe"
    raise Refusal("ceiling_kind", "a governor observation does not say what it was measured on (its scratch file, or a raw device)",
                  "pass --kind file (a scratch file on the data filesystem) or --kind raw")


def _ceilings_add(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    state = res.state_dir_of(host, args.state_dir)
    try:
        identity = _ceilings_identity(host, state)
    except res.LoadError as exc:
        raise Refusal("no_calibration", f"the history is bound to the calibrated disk identity ({exc.reason})", "run `iobench calibrate` first") from exc
    digest = ceilings.identity_digest(identity)
    obs = {"ts": ceilings.iso(host.now()), "dir": args.dir, "workload": args.workload, "plateau_bps": args.bps, "duration_s": args.duration_s,
           "source": args.source, "kind": _observation_kind(args, identity)}
    ceilings.validate(obs)
    if not runtime.dry_run:
        ceilings.append(host, res.paths(state)["ceilings"], obs, digest)
    _emit(runtime, {"appended": not runtime.dry_run, "observation": obs})
    return 0


def _ceilings_summary(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    state = res.state_dir_of(host, args.state_dir)
    try:
        digest = ceilings.identity_digest(_ceilings_identity(host, state))
    except (res.LoadError, Refusal):
        digest = ""  # no calibration, or one that predates the disk scope: the history is not this disk's
    rows, warnings = ceilings.load(host, res.paths(state)["ceilings"], digest)
    out = ceilings.summary(rows, host.now(), args.window_days, args.kind)
    out["by_kind"] = ceilings.summary_by_kind(rows, host.now(), args.window_days)
    if warnings:
        out["warnings"] = warnings
    _emit(runtime, out)
    return 0


def _restore(args: Any, runtime: CliRuntime, factory: HostFactory) -> int:
    host = factory()
    s = restore.Safety(host, "restore", ceilings.iso(host.now()))
    s.acquire()
    try:
        if runtime.dry_run:
            pending = json.loads(host.read_text(restore.JOURNAL)) if host.exists(restore.JOURNAL) else None
            _emit(runtime, {"journal": pending, "would_restore": bool(pending)})
            return 0
        report = s.recover_previous()
    finally:
        s.release()
    _emit(runtime, {"restored": report.as_dict() if report else None, "warnings": report.warnings if report else []})
    return 0


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)

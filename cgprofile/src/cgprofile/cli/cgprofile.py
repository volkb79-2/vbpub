"""``cgprofile``: the end-user client (CGPROFILECTL-SPEC.md section 2.4).

The local verbs: ``run`` (direct mode, needs cgroupfs), ``mark``, ``report`` (renders a
run directory or an exported daemon session), ``analyze`` and the agent ``skills`` verbs.
Nothing here controls the daemon; that is ``cgprofilectl`` (:mod:`cgprofile.cli.cgprofilectl`).
``report <session>`` only reads an export from the control socket. The report renderer is
imported only inside ``report`` and ``analyze``; ``mark`` imports nothing outside the standard
library and cli-extended (a test pins this).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
    register_skills_verbs,
)

from .. import ctlwire
from ..ctlwire import WireFailure
from ..version import installed_version
from ._common import REPORT_EXTRA_HINT, bad as _bad, emit, guarded, opt as _opt, socket_option, socket_path

DIRECT_MODE_REFUSAL = "direct mode needs cgroupfs; use 'cgprofilectl start' against the host daemon"


def identity() -> CliIdentity:
    return CliIdentity(name="CGPROFILE", version=installed_version(),
                       long_name="cgroup resource profiler", command="cgprofile")


# ── report / analyze ────────────────────────────────────────────────────────

def _load_analysis(run_path: str) -> Any:
    """Analyse a run directory. Needs the ``report`` extra."""
    try:
        from .. import analyze, store
    except ImportError as exc:
        raise WireFailure(f"the report extra is not installed ({exc}); install it with {REPORT_EXTRA_HINT}",
                          exit_code=1, code="report-extra-missing") from exc
    run = store.RunDir(os.path.dirname(run_path), os.path.basename(run_path), create=False)
    return analyze.build(run)


def _render(run_path: str, out_dir: str, html: bool, md: bool) -> List[Dict[str, Any]]:
    """Render a run directory into ``out_dir``. Needs the ``report`` extra."""
    analysis = _load_analysis(run_path)
    from .. import report_html, report_md

    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    if html:
        written.append(report_html.render(analysis, os.path.join(out_dir, "report.html")))
    if md:
        written.append(report_md.render(analysis, out_dir))
    return [{"path": os.path.basename(p), "bytes": os.path.getsize(p)} for p in written]


def _write_parts(parts: Dict[str, bytes], directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for name, body in parts.items():
        with open(os.path.join(directory, os.path.basename(name)), "wb") as handle:
            handle.write(body)


@guarded
def _report(args: argparse.Namespace, runtime: CliRuntime) -> None:
    html, md = not args.md_only, not args.html_only
    if args.run_dir:
        if args.session or args.export_only:
            raise _bad("report --run-dir takes no session id and no --export-only")
        run_path = os.path.realpath(args.run_dir)
        if not os.path.isdir(run_path):
            raise _bad(f"run directory does not exist: {args.run_dir}")
        out_dir = args.out or run_path
        label = os.path.basename(run_path)
        files = _render(run_path, out_dir, html, md)
    else:
        if not args.session:
            raise _bad("report needs a session id or --run-dir DIR")
        label = args.session
        parts = ctlwire.export_parts(socket_path(args), args.session)
        if args.export_only:
            _write_parts(parts, args.export_only)
            out_dir = args.export_only
            files = [{"path": n, "bytes": len(b)} for n, b in sorted(parts.items())]
        else:
            out_dir = args.out or f"./cgprofile-report-{args.session}"
            with tempfile.TemporaryDirectory(prefix="cgprofile-export-") as scratch:
                run_path = os.path.join(scratch, args.session)
                _write_parts(parts, run_path)
                files = _render(run_path, out_dir, html, md)
    emit(runtime, {"session": label, "out_dir": out_dir, "files": files},
         [os.path.join(out_dir, f["path"]) for f in files])


@guarded
def _analyze(args: argparse.Namespace, runtime: CliRuntime) -> None:
    run_path = os.path.realpath(args.run_dir)
    if not os.path.isdir(run_path):
        raise _bad(f"run directory does not exist: {args.run_dir}")
    analysis = _load_analysis(run_path)
    proposals = [p.to_dict() for p in analysis.proposals]
    lines = [f"{p['severity']:<8} {p['id']}  {p['title']} ({p['confidence']})" for p in proposals]
    emit(runtime, {"run_dir": run_path, "proposals": proposals}, lines or ["no proposals"])


# ── run / mark (no daemon) ──────────────────────────────────────────────────

@guarded
def _run(args: argparse.Namespace, runtime: CliRuntime) -> int:
    from .. import access, direct

    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise _bad("nothing to run: put the command after `--`")
    if not access.have_host_cgroup_view():
        raise _bad(DIRECT_MODE_REFUSAL, "direct-mode-unavailable")
    outcome = direct.run_command(command, out_dir=os.path.realpath(args.out_dir), phase=args.phase,
                                 client_dir=os.path.dirname(os.path.realpath(sys.argv[0])))
    emit(runtime, {"run_dir": outcome.run_dir, "exit_status": outcome.exit_status,
                   "duration_seconds": outcome.duration_seconds},
         [outcome.run_dir, f"command exited {outcome.exit_status}"])
    return outcome.exit_status if args.propagate_exit else 0


@guarded
def _mark(args: argparse.Namespace, runtime: CliRuntime) -> None:
    run_path = os.environ.get("CGPROFILE_RUN_DIR")
    if not run_path:
        raise _bad("CGPROFILE_RUN_DIR is not set: run this inside `cgprofile run`", "no-run-dir")
    if not os.path.isdir(run_path):
        raise _bad(f"run directory does not exist: {run_path}", "no-run-dir")
    try:
        meta = json.loads(args.meta) if args.meta else {}
    except json.JSONDecodeError as exc:
        raise _bad(f"--meta must be valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise _bad("--meta must be a JSON object")
    from .. import phases

    mark = phases.emit_mark(run_path, args.name, kind=args.kind, meta=meta)
    emit(runtime, {"name": mark.name, "kind": mark.kind}, [f"mark {mark.kind}:{mark.name}"])


# ── registry ────────────────────────────────────────────────────────────────

def build_cli(cli_identity: Optional[CliIdentity] = None) -> RegisteredCli:
    """Build the registered CLI; import-safe (nothing runs until ``.run()``)."""
    registry = CliRegistry(
        cli_identity or identity(), prog="cgprofile",
        description="Profile commands locally and render cgprofile reports.",
        getting_started=("cgprofile run -- make", "cgprofile report --run-dir ./cgprofile-runs/RUN",
                         "cgprofile analyze ./cgprofile-runs/RUN"),
        unexpected_exceptions="report",
    )
    read = VerbGroup.EXPLORATION.value
    modify = VerbGroup.MODIFICATION.value
    registry.register(VerbSpec(
        "report", description="render a run directory, or export a stored session and render it", group=read,
        expensive=True, include_progress=False,
        examples=("cgprofile report s-20261104T101500Z-9f01", "cgprofile report --run-dir ./runs/run-1 --md-only"),
        arguments=(ArgumentSpec("session", "the session id (omit with --run-dir)", metavar="ID",
                                parser_kwargs={"nargs": "?"}),),
        options=(
            _opt("--out", "output directory (default ./cgprofile-report-<session>/)", group="OUTPUT", metavar="DIR"),
            _opt("--run-dir", "render a standalone run result; needs no daemon", group="SOURCE", metavar="DIR"),
            _opt("--export-only", "write the raw exported parts here and render nothing", group="SOURCE", metavar="DIR"),
            socket_option(),
            _opt("--html-only", "write only report.html", group="OUTPUT", action="store_true",
                 mutually_exclusive_group="format"),
            _opt("--md-only", "write only report.md", group="OUTPUT", action="store_true",
                 mutually_exclusive_group="format"),
        ), handler=_report))
    registry.register(VerbSpec(
        "analyze", description="analyse a run directory and print the proposals", group=read, expensive=True,
        include_progress=False, examples=("cgprofile analyze ./cgprofile-runs/run-1 --json",),
        arguments=(ArgumentSpec("run_dir", "the run directory", metavar="DIR"),), handler=_analyze))
    registry.register(VerbSpec(
        "run", description="profile one command locally (direct mode, needs cgroupfs)", group=modify,
        mutating=True, expensive=True, include_progress=False, confirmation_required=False,
        examples=("cgprofile run --phase build -- make",),
        arguments=(ArgumentSpec("command", "the command to profile, after --", metavar="CMD",
                                parser_kwargs={"nargs": argparse.REMAINDER}),),
        options=(_opt("--phase", "phase name of the whole command (default: its basename)", group="RUN", metavar="NAME"),
                 _opt("--out-dir", "directory receiving the run directory", group="RUN", metavar="DIR",
                      default="./cgprofile-runs"),
                 _opt("--propagate-exit", "exit with the command's own status", group="RUN", action="store_true")),
        handler=_run))
    registry.register(VerbSpec(
        "mark", description="append a phase mark to the current run ($CGPROFILE_RUN_DIR)", group=modify,
        mutating=True, confirmation_required=False, include_progress=False,
        examples=("cgprofile mark restoring-database",),
        arguments=(ArgumentSpec("name", "the mark name", metavar="NAME"),),
        options=(_opt("--kind", "phase or event", group="MARK", metavar="KIND", choices=("phase", "event"), default="phase"),
                 _opt("--meta", "JSON object of extra fields", group="MARK", metavar="JSON")),
        handler=_mark))
    register_skills_verbs(registry, package="cgprofile")
    return registry.build()


def main(argv: Optional[Sequence[str]] = None) -> int:
    return build_cli().run(argv=None if argv is None else list(argv))


if __name__ == "__main__":
    raise SystemExit(main())

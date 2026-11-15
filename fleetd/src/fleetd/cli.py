"""Command line for the fleet facts and planning tools (a registered cli-extended CLI).

The grammar is declared once in :func:`build_cli`; the handlers only implement
product behaviour. The library owns help, version, the shared output controls,
the exception boundary and the JSON-mode result stream, so:

* results are written with ``runtime.output``, which keeps stdout machine-clean
  (``--json`` prints one compact JSON document) and sends diagnostics to stderr;
* ``CliFailure`` carries the exit code. A domain failure is exit 2, as the
  campaign supervisor and the ``campaign-contract`` lane expect; an unexpected
  exception is one ``[ERROR]`` line with exit 1 (``--traceback`` shows the stack);
* the version comes from :func:`identity`, resolved from installed metadata
  (there is no literal fallback).

The documented output of each command is unchanged: with no ``--json`` a
successful command prints the same text or indented, key-sorted JSON document
as before, and every flag keeps its spelling and meaning.
"""

from __future__ import annotations

import argparse
import functools
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
    VersionLookupError,
    register_skills_verbs,
)

from .contracts import ContractError, dump_json, load_json, validate_host_facts
from .facts import DEFAULT_ROOT, collect_host_facts
from .guard import parse_guard_toml
from .plan import plan_campaign, read_lane_demand_table, render_commands
from .campaign import CampaignManager
from .transport import HxTransport, TransportError

PROG = "fleet"
LONG_NAME = "RAM-first fleet facts and campaign plans"
DISTRIBUTION = "fleetd"
COMMAND = PROG

# Every input failure the fleetd contracts and the host transport raise is a
# domain refusal: exit 2, one diagnostic, no traceback. `_domain` (below) is the
# ONE place that maps them, so a new contract error cannot invent its own code.
DOMAIN_FAILURES = (
    ContractError,
    TransportError,
    OSError,
    UnicodeError,
    OverflowError,
)

EXIT_DOMAIN_FAILURE = 2
EXIT_NOT_INSTALLED = 3


def _domain(handler: Callable[[argparse.Namespace, CliRuntime], None]) -> Callable[[argparse.Namespace, CliRuntime], None]:
    """Map a domain refusal to ``CliFailure`` with the CLI's input-error status.

    The library boundary already prints one ``[ERROR]`` line; without this the
    documented exit code of a refused input would be the boundary's generic 1.
    An unexpected exception is deliberately NOT caught here: it stays visible as
    the library's ``unexpected ...`` line (exit 1), with ``--traceback`` for the stack.
    """

    @functools.wraps(handler)
    def wrapper(args: argparse.Namespace, runtime: CliRuntime) -> None:
        try:
            return handler(args, runtime)
        except CliFailure:
            raise
        except DOMAIN_FAILURES as exc:
            raise CliFailure(str(exc), exit_code=EXIT_DOMAIN_FAILURE) from exc

    return wrapper


def identity() -> CliIdentity:
    """The one identity: the installed ``fleetd`` distribution, no literal fallback.

    A source checkout that was never installed therefore refuses to run (exit 3
    with one actionable line) instead of reporting an invented version. The
    project version itself is derived at build time from the ``fleetd-v*`` tag
    (``tool.setuptools_scm``), so a literal in the source could only go stale.
    """

    return CliIdentity.resolve(
        name="FLEET",
        long_name=LONG_NAME,
        command=COMMAND,
        distribution=DISTRIBUTION,
    )


def _default_state_dir() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "fleetd" / "campaigns"


# ---------------------------------------------------------------------------- handlers


def _validate_facts(args: argparse.Namespace, runtime: CliRuntime) -> None:
    validate_host_facts(load_json(args.file))
    runtime.output.primary("valid host-facts/1")


def _collect_facts(args: argparse.Namespace, runtime: CliRuntime) -> None:
    document = collect_host_facts(args.root)
    if runtime.json_mode:
        runtime.output.primary(document)
    else:
        runtime.output.primary(dump_json(document).rstrip("\n"))


def _validate_guard(args: argparse.Namespace, runtime: CliRuntime) -> None:
    parse_guard_toml(args.file.read_text(encoding="utf-8"))
    runtime.output.primary("valid guard.toml")


def _plan_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    result = plan_campaign(
        args.lane,
        load_json(args.candidates),
        load_json(args.hosts),
        read_lane_demand_table(args.demand, args.lane),
        batch_multiplier=args.batch_multiplier,
    )
    if args.commands:
        runtime.output.primary(render_commands(result).rstrip("\n"))
        return
    execution = _execution_context()
    if execution is not None:
        result["execution"] = execution
    if runtime.json_mode:
        runtime.output.primary(result)
        return
    runtime.output.primary(dump_json(result).rstrip("\n"))


def _execution_context() -> dict[str, str] | None:
    """Pin a runnable plan only when the current project is a clean Git tree."""
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True)
        prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], check=False, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], check=False, capture_output=True, text=True)
    except OSError:
        return None
    if any(result.returncode != 0 for result in (root, commit, prefix, status)) or status.stdout.strip():
        return None
    repository = Path(root.stdout.strip())
    project = prefix.stdout.strip().rstrip("/") or "."
    project_path = repository if project == "." else repository / project
    if not (project_path / "run-gate.toml").is_file() and not (project_path / "run-gate.root.toml").is_file():
        return None
    return {"schema": "fleet-plan-execution/1", "commit": commit.stdout.strip(), "project": project}


def _manager(args: argparse.Namespace) -> CampaignManager:
    if args.transport != "hx":
        raise ContractError("transport: only the identity-checked hx wrapper is supported")
    return CampaignManager(args.state_dir, HxTransport())


def _run_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    plan = load_json(args.plan)
    if not isinstance(plan, dict):
        raise ContractError("plan file: expected a JSON object")
    _emit(runtime, _manager(args).run(args.campaign, plan))


def _status_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    _emit(runtime, _manager(args).status(args.campaign))


def _tick_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    _emit(runtime, _manager(args).tick(args.campaign))


def _collect_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    result, _report, json_path, report_path = _manager(args).collect(args.campaign)
    _emit(runtime, {
        "schema": "fleet-campaign-collection/1",
        "campaign": args.campaign,
        "result": str(json_path),
        "report": str(report_path),
        "totals": result["totals"],
    })


def _emit(runtime: CliRuntime, document: object) -> None:
    """Write one campaign document: compact JSON under ``--json``, the documented form otherwise."""

    if runtime.json_mode:
        runtime.output.primary(document)
        return
    runtime.output.primary(dump_json(document).rstrip("\n"))


# ---------------------------------------------------------------------------- registration


def _campaign_options() -> tuple[OptionSpec, ...]:
    return (
        OptionSpec(
            ("--transport",),
            "host transport; only the identity-checked hx wrapper is supported",
            group="TRANSPORT",
            metavar="NAME",
            parser_kwargs={"choices": ("hx",), "default": "hx"},
        ),
        OptionSpec(
            ("--state-dir",),
            "campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns)",
            group="STATE",
            metavar="DIR",
            # A documented path string; argparse converts it with `type=Path`, and
            # the exported surface can enumerate the default.
            parser_kwargs={"type": Path, "default": str(_default_state_dir())},
        ),
    )


def build_facts_cli(cli_identity: CliIdentity | None = None) -> RegisteredCli:
    """The ``fleet facts`` group: read-only host facts collection and validation."""

    registry = CliRegistry(
        cli_identity or identity(),
        prog=f"{PROG} facts",
        description="Collect host-facts/1 from the host, or validate a serialized record.",
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "collect",
        description=(
            "collect host-facts/1 from /proc, /sys, the configured control-plane service "
            "MemoryMin properties and the Geekbench record; --root maps the host's absolute "
            "config and source paths beneath a fixture root"
        ),
        group=VerbGroup.EXPLORATION.value,
        include_json=True,
        include_progress=False,
        examples=(
            "fleet facts collect > host-facts.json",
            "fleet facts collect --root ./host-root --json",
        ),
        options=(OptionSpec(
            ("--root",),
            "prefix for the host's absolute config and fact paths (a fixture root queries no systemd)",
            group="INPUT",
            metavar="DIR",
            # The default is the documented path string: argparse converts it with
            # `type=Path`, and the exported surface can then enumerate the value.
            parser_kwargs={"type": Path, "default": str(DEFAULT_ROOT)},
        ),),
        handler=_domain(_collect_facts),
    ))
    registry.register(VerbSpec(
        "validate",
        description="validate a serialized host-facts/1 JSON file without contacting the host",
        group=VerbGroup.EXPLORATION.value,
        include_json=False,
        include_progress=False,
        examples=("fleet facts validate host-facts.json",),
        arguments=(ArgumentSpec("file", "host-facts/1 JSON file to validate", metavar="FILE", parser_kwargs={"type": Path}),),
        handler=_domain(_validate_facts),
    ))
    return registry.build()


def build_guard_cli(cli_identity: CliIdentity | None = None) -> RegisteredCli:
    """The ``fleet guard`` group: parse and validate host-owned guard rules."""

    registry = CliRegistry(
        cli_identity or identity(),
        prog=f"{PROG} guard",
        description="Validate host-owned guard rules; the rules are parsed, never applied.",
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "validate",
        description="parse and validate one guard TOML file (including its drop-ins)",
        group=VerbGroup.EXPLORATION.value,
        include_json=False,
        include_progress=False,
        examples=("fleet guard validate /etc/cgprofile/guard.toml",),
        arguments=(ArgumentSpec("file", "guard TOML file to validate", metavar="FILE", parser_kwargs={"type": Path}),),
        handler=_domain(_validate_guard),
    ))
    return registry.build()


def build_cli(cli_identity: CliIdentity | None = None) -> RegisteredCli:
    """The registered ``fleet`` CLI; import-safe (nothing runs until ``.run()``)."""

    cli_identity = cli_identity or identity()
    registry = CliRegistry(
        cli_identity,
        prog=PROG,
        description=(
            "Read host facts, create deterministic RAM-first campaign plans, and supervise one "
            "lite campaign through the existing identity-checked runner jobs"
        ),
        getting_started=(
            "fleet facts collect > host-facts.json",
            "fleet plan r2 --candidates candidates.json --hosts hosts.json --demand lane-demand.toml > plan.json",
            "fleet run plan.json --transport hx --campaign r2-week46",
            "fleet status r2-week46",
        ),
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "facts", description="collect or validate host facts",
        group=VerbGroup.EXPLORATION.value, delegate=build_facts_cli(cli_identity),
    ))
    registry.register(VerbSpec(
        "guard", description="validate host-owned guard rules",
        group=VerbGroup.EXPLORATION.value, delegate=build_guard_cli(cli_identity),
    ))
    registry.register(VerbSpec(
        "plan",
        description=(
            "create a deterministic RAM-first campaign plan; from a clean Git project the plan "
            "also pins the commit and project path that `fleet run` requires"
        ),
        group=VerbGroup.EXPLORATION.value,
        include_progress=False,
        examples=(
            "fleet plan r2 --candidates candidates.json --hosts hosts.json --demand lane-demand.toml > plan.json",
            "fleet plan r2 ... --commands",
            "fleet plan r2 ... --batch-multiplier 2.0",
        ),
        arguments=(ArgumentSpec("lane", "Run-Gate lane name", metavar="LANE"),),
        options=(
            OptionSpec(
                ("--candidates",),
                "candidate-table/1 JSON file",
                group="INPUT",
                metavar="FILE",
                parser_kwargs={"type": Path, "required": True},
            ),
            OptionSpec(
                ("--hosts",),
                "fleet-host-set/1 JSON file",
                group="INPUT",
                metavar="FILE",
                parser_kwargs={"type": Path, "required": True},
            ),
            OptionSpec(
                ("--demand",),
                "TOML file containing [lanes.<lane>.demand] (release Run-Gate does not accept "
                "that key yet; see RG-276)",
                group="INPUT",
                metavar="FILE",
                parser_kwargs={"type": Path, "required": True},
            ),
            OptionSpec(
                ("--commands",),
                "print Run-Gate --only-candidates commands instead of plan JSON",
                group="OUTPUT",
                parser_kwargs={"action": "store_true", "default": False},
            ),
            OptionSpec(
                ("--batch-multiplier",),
                "candidate count per batch as a multiple of host worker slots (default: 4)",
                group="PLANNING",
                metavar="FACTOR",
                parser_kwargs={"type": float, "default": 4.0},
            ),
        ),
        handler=_domain(_plan_campaign),
    ))
    registry.register(VerbSpec(
        "run",
        description="start or re-adopt the named jobs in a fleet plan",
        group=VerbGroup.MODIFICATION.value,
        mutating=True,
        confirmation_required=False,
        expensive=True,
        examples=("fleet run plan.json --transport hx --campaign r2-week46",),
        arguments=(ArgumentSpec("plan", "fleet-plan/1 JSON file with an execution object", metavar="PLAN", parser_kwargs={"type": Path}),),
        options=(
            OptionSpec(
                ("--campaign",),
                "campaign ID; the same ID re-adopts existing jobs by their deterministic names",
                group="CAMPAIGN",
                metavar="ID",
                parser_kwargs={"required": True},
            ),
            *_campaign_options(),
        ),
        handler=_domain(_run_campaign),
    ))
    for name, summary, description, handler in (
        (
            "status",
            "probe campaign progress and ETA",
            "probe each planned host, refresh the local state and report per-host progress and ETA",
            _status_campaign,
        ),
        (
            "tick",
            "perform one campaign scheduling step",
            "perform one scheduling step: adopt jobs, detect lost or stalled hosts, re-queue work and return",
            _tick_campaign,
        ),
        (
            "collect",
            "fetch artifacts and write combined campaign results",
            "fetch available progress streams and per-batch verdicts and write "
            "fleet-campaign-result/1 JSON plus a Markdown report",
            _collect_campaign,
        ),
    ):
        registry.register(VerbSpec(
            name,
            description=description,
            group=VerbGroup.MIXED.value if name != "status" else VerbGroup.EXPLORATION.value,
            include_progress=False,
            examples=(f"fleet {name} r2-week46",),
            arguments=(ArgumentSpec("campaign", "campaign ID", metavar="ID"),),
            options=_campaign_options(),
            handler=_domain(handler),
        ))
    # Package data: fleetd/skills/fleetd-campaigns/SKILL.md. The library owns the
    # install/check/list/uninstall verbs, their stamp and their integrity sidecar.
    register_skills_verbs(registry, package="fleetd")
    return registry.build()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; a missing version source is one clean line, exit 3."""

    try:
        cli = build_cli()
    except VersionLookupError as exc:
        print(
            f"fleet: ERROR cannot determine the fleetd version: {exc}. "
            "Install the fleetd wheel (pip install fleetd.whl next to cli-extended.whl).",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_NOT_INSTALLED
    return cli.run(argv=list(argv) if argv is not None else None)

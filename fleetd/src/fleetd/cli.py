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

import re

from .contracts import ContractError, dump_json, load_json, parse_memory_bytes, validate_host_facts
from .facts import DEFAULT_ROOT, collect_host_facts
from .guard import parse_guard_toml
from .measurement import load_measurement
from .merge import load_shard_identity, validate_shard_set
from .plan import plan_campaign, read_lane_demand_table, render_commands
from .scope import (
    DEFAULT_SCOPE_MODE,
    EXIT_NOTHING_TO_JUDGE,
    EXIT_OK,
    EXIT_STALE_CURSOR,
    ROLLING_MODE,
    SCOPE_MODES,
    load_cursor,
    load_scope_file,
    plan_candidates,
    planned_file_blobs,
    refuse_unpinned_checkout,
    release_diff,
    resolve_commit,
    scope_document,
    write_cursor,
)
from .sizing import derive_sizing, sizing_input_from_plan
from .campaign import CampaignManager
from .transport import HxNotConfigured, HxTransport, TransportError

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
    """The documented default, derived at RUN time from the caller's own environment."""

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
    # `--state-dir` has no declarative default on purpose: the documented default
    # ($XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns) belongs to
    # the INVOCATION's environment. A default resolved at registration time would bake
    # one machine's home directory into the exported surface, the generated SPEC region
    # and every reviewed signature, so two hosts would disagree about the same CLI.
    try:
        transport = HxTransport(args.hx)
    except HxNotConfigured as exc:
        raise CliFailure(str(exc), exit_code=EXIT_NOT_INSTALLED) from exc
    return CampaignManager(args.state_dir or _default_state_dir(), transport)


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


def _measurement_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    _emit(runtime, _manager(args).measurement(args.campaign))


def _scope_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    repo = Path(os.path.abspath(os.path.expanduser(str(args.repo))))
    commit = args.commit or resolve_commit(repo)
    refuse_unpinned_checkout(repo, commit)
    scope_file = args.scope_file
    if scope_file is None:
        default = repo / "r2-scope.toml"
        scope_file = default if default.is_file() else None
    declared = load_scope_file(scope_file) if scope_file is not None else {
        "mode": None, "slice_size": None, "cursor": None, "note": None,
        "reserve_percent": None, "path": None,
    }
    if args.mode is not None:
        mode, mode_source = args.mode, "--mode"
    elif declared["mode"] is not None:
        mode, mode_source = declared["mode"], f"scope file {declared['path']}"
    else:
        mode, mode_source = DEFAULT_SCOPE_MODE, "default"
    if mode == DEFAULT_SCOPE_MODE and not args.from_tag:
        raise ContractError(
            "--from-tag: the changed-lines scope needs the previous release tag; "
            "there is nothing to compare against without it"
        )
    slice_size = args.slice_size if args.slice_size is not None else declared["slice_size"]
    cursor_path = args.cursor
    declared_cursor = False
    if cursor_path is None and declared["cursor"] is not None:
        cursor_path = (repo if Path(declared["cursor"]).is_absolute() else repo / declared["cursor"])
        declared_cursor = True
    cursor, legacy_cursor = (
        load_cursor(cursor_path, rebaseline=args.rebaseline, allow_absent=declared_cursor)
        if cursor_path is not None
        else (None, None)
    )
    plan = load_json(args.plan)
    diff_text = release_diff(repo, args.from_tag, commit) if mode == DEFAULT_SCOPE_MODE else ""
    # The rolling sweep keys every candidate to its file's content at the judged
    # commit, so the planned paths' blob ids are an input, not a lookup the
    # producer may skip: a planned path the commit cannot describe is refused.
    blobs = (
        planned_file_blobs(repo, commit, [row.path for row in plan_candidates(plan, require_site_identity=True)])
        if mode == ROLLING_MODE
        else None
    )
    document = scope_document(
        mode=mode,
        mode_source=mode_source,
        lane=args.lane,
        from_tag=args.from_tag or "",
        commit=commit,
        plan=plan,
        diff_text=diff_text,
        slice_size=slice_size,
        cursor=cursor,
        rebaseline=args.rebaseline,
        blobs=blobs,
        legacy_cursor=legacy_cursor,
        reserve_percent=declared["reserve_percent"],
    )
    if args.write_cursor is not None:
        if document["cursor"] is None:
            raise ContractError(
                "--write-cursor: only a rolling scope publishes an advanced cursor; "
                f"this scope is {document['status']}"
            )
        write_cursor(args.write_cursor, document["cursor"])
    if args.out is not None:
        if document["status"] != "ok":
            raise ContractError(
                f"--out: refusing to write an empty candidate file; the scope is "
                f"{document['status']}: {document['reason']}"
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            "".join(f"{candidate_id}\n" for candidate_id in document["candidate_ids"]),
            encoding="ascii",
        )
    _emit(runtime, document)
    # The document is the result even when the scope is refused; the exit status
    # carries the verdict (3 stale cursor, 4 nothing to judge) and the reason.
    if document["status"] == "stale-cursor":
        raise CliFailure(f"stale cursor: {document['reason']}", exit_code=EXIT_STALE_CURSOR)
    if document["status"] == "nothing-to-judge":
        raise CliFailure(f"nothing to judge: {document['reason']}", exit_code=EXIT_NOTHING_TO_JUDGE)


def _sizing_campaign(args: argparse.Namespace, runtime: CliRuntime) -> None:
    plan = load_json(args.plan)
    measurement = load_measurement(args.measurement) if args.measurement is not None else None
    sizing = sizing_input_from_plan(
        plan,
        measurement=measurement,
        per_candidate_seconds=args.per_candidate_seconds,
        per_candidate_memory_bytes=(
            parse_memory_bytes(args.per_candidate_memory, "--per-candidate-memory")
            if args.per_candidate_memory is not None else None
        ),
    )
    limit = None
    allowance = None
    for value, name in (
        (args.minimum_budget, "--minimum-budget"),
        (args.startup_allowance, "--startup-allowance"),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+[smh]", value):
            raise ContractError(f"{name}: expected a duration like 15m or 1h")
        seconds = int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]
        if name == "--minimum-budget":
            limit = seconds
        else:
            allowance = seconds
    _emit(runtime, derive_sizing(
        sizing,
        margin=args.margin,
        minimum_budget_seconds=limit,
        startup_allowance_seconds=allowance,
    ))


def _merge_check(args: argparse.Namespace, runtime: CliRuntime) -> None:
    identities = [load_shard_identity(path) for path in args.identity]
    document = validate_shard_set(identities, require_complete=args.require_complete)
    _emit(runtime, document)
    if document["status"] != "ok":
        raise CliFailure(
            f"the shard set is {document['status']}; see problems in the document",
            exit_code=EXIT_STALE_CURSOR,
        )


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
            ("--hx",),
            "path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source "
            "checkout's scripts/controller/hx.sh; an installed fleetd needs one of them)",
            group="TRANSPORT",
            metavar="PATH",
            parser_kwargs={"type": Path},
        ),
        OptionSpec(
            ("--state-dir",),
            "campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, "
            "or ~/.local/state/fleetd/campaigns when unset)",
            group="STATE",
            metavar="DIR",
            # No declarative default: the default is derived per invocation (see `_manager`).
            parser_kwargs={"type": Path},
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
        "scope",
        description=(
            "produce the R2 candidate-id scope: the candidates on lines changed since the previous "
            "release tag by default, or the next slice of the opt-in rolling whole-target baseline; "
            "exit 3 for a stale cursor, 4 for nothing to judge"
        ),
        group=VerbGroup.EXPLORATION.value,
        include_progress=False,
        examples=(
            "fleet scope r2 --plan plan.json --repo . --from-tag tool-v1.2.0 --out candidates.txt",
            "fleet scope r2 --plan plan.json --mode rolling-whole-target --slice-size 40 --cursor cursor.json --write-cursor cursor.json",
        ),
        arguments=(ArgumentSpec("lane", "Run-Gate lane whose Assay candidates form the whole-target list", metavar="LANE"),),
        options=(
            OptionSpec(("--plan",), "output of `assay plan <lane> --json`", group="INPUT", metavar="FILE",
                       parser_kwargs={"type": Path, "required": True}),
            OptionSpec(("--repo",), "the Git checkout the plan describes", group="INPUT", metavar="DIR",
                       parser_kwargs={"type": Path, "default": "."}),
            OptionSpec(("--from-tag",), "previous release tag (required by the changed-lines scope)", group="SCOPE", metavar="TAG"),
            OptionSpec(("--commit",), "commit to judge (default: HEAD of --repo)", group="SCOPE", metavar="SHA"),
            OptionSpec(("--mode",), f"scope mode (default: {DEFAULT_SCOPE_MODE})", group="SCOPE", metavar="MODE",
                       parser_kwargs={"choices": SCOPE_MODES}),
            OptionSpec(("--scope-file",), "project r2 scope file (default: <repo>/r2-scope.toml when present)",
                       group="SCOPE", metavar="FILE", parser_kwargs={"type": Path}),
            OptionSpec(("--slice-size",), "rolling-whole-target slice size", group="SCOPE", metavar="N",
                       parser_kwargs={"type": int}),
            OptionSpec(("--cursor",), "rolling cursor file to resume from", group="SCOPE", metavar="FILE",
                       parser_kwargs={"type": Path}),
            OptionSpec(("--write-cursor",), "write the advanced rolling cursor here", group="OUTPUT", metavar="FILE",
                       parser_kwargs={"type": Path}),
            OptionSpec(("--rebaseline",), "start one fresh sweep pass from the current file content, discarding the cursor's marks (the explicit migration for an older cursor schema)",
                       group="SCOPE", parser_kwargs={"action": "store_true", "default": False}),
            OptionSpec(("--out",), "write the candidate-id file here (refused for an empty scope)", group="OUTPUT",
                       metavar="FILE", parser_kwargs={"type": Path}),
        ),
        handler=_domain(_scope_campaign),
    ))
    registry.register(VerbSpec(
        "sizing",
        description="derive shards, instances per host and budgets from measured units",
        group=VerbGroup.EXPLORATION.value,
        include_progress=False,
        examples=("fleet sizing plan.json --measurement r2-week46.measurement.json",),
        arguments=(ArgumentSpec("plan", "fleet-plan/1 JSON file", metavar="PLAN", parser_kwargs={"type": Path}),),
        options=(
            OptionSpec(("--measurement",), "fleet-r2-measurement/1 from a finished campaign", group="INPUT",
                       metavar="FILE", parser_kwargs={"type": Path}),
            OptionSpec(("--per-candidate-seconds",), "declared per-candidate wall seconds", group="INPUT",
                       metavar="SECONDS", parser_kwargs={"type": float}),
            OptionSpec(("--per-candidate-memory",), "declared per-candidate memory charge", group="INPUT", metavar="SIZE"),
            OptionSpec(("--margin",), "explicit budget margin over the estimate (default: derived from the measured spread, at least 1.10)",
                       group="BUDGET", metavar="FACTOR", parser_kwargs={"type": float, "default": None}),
            OptionSpec(("--minimum-budget",), "shortest per-instance budget (default: 15m)", group="BUDGET",
                       metavar="DURATION", parser_kwargs={"default": "15m"}),
            OptionSpec(("--startup-allowance",), "outer-bound allowance beside each instance budget (default: 15m)",
                       group="BUDGET", metavar="DURATION", parser_kwargs={"default": "15m"}),
        ),
        handler=_domain(_sizing_campaign),
    ))
    registry.register(VerbSpec(
        "merge-check",
        description="refuse a shard merge whose shards judged different commits, images or plans",
        group=VerbGroup.EXPLORATION.value,
        include_progress=False,
        examples=("fleet merge-check shard-0.identity.json shard-1.identity.json --require-complete",),
        arguments=(ArgumentSpec("identity", "fleet-shard-identity/1 files, one per shard", metavar="IDENTITY",
                                parser_kwargs={"type": Path, "nargs": "+"}),),
        options=(OptionSpec(
            ("--require-complete",),
            "accepted for explicitness; an incomplete shard set is always refused",
            group="CHECK", parser_kwargs={"action": "store_true", "default": False},
        ),),
        handler=_domain(_merge_check),
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
        (
            "measurement",
            "write the per-candidate measurement evidence that sizes the next campaign",
            "write fleet-r2-measurement/1 evidence (per-candidate seconds and memory peaks, medians over "
            "killed and survived candidates) beside the campaign state",
            _measurement_campaign,
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

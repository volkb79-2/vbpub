"""Registered CLI for the guarded applier and endpoint command group."""

from __future__ import annotations

import sys
import warnings
from typing import Any, Sequence

from cli_extended.identity import CliIdentity, VersionLookupError
from cli_extended.parser import ArgumentSpec, CliFailure, CliRegistry, CliRuntime, OptionSpec, VerbSpec

from . import ApplyFailed, Plan, PlanRefused, Problem, StalePlan
from .endpoints import build_endpoints_cli
from .engine import apply, journal, load_request, plan, preview_revert, revert
from .transactions import pending_actions


def _root_option() -> OptionSpec:
    return OptionSpec(
        ("--root",),
        "real or fake host root",
        group="ROOT",
        metavar="DIR",
        parser_kwargs={"default": "/"},
    )


def _emit_plan(planned: Plan, runtime: CliRuntime) -> None:
    if runtime.json_mode:
        runtime.output.primary(planned.as_dict())
    else:
        runtime.output.primary(planned.render().rstrip("\n"))


def _emit_problems(problems: list[Problem], runtime: CliRuntime, *, planned: Plan | None = None) -> int:
    if runtime.json_mode:
        payload: dict[str, Any] = {
            "status": "refused",
            "problems": [problem.as_dict() for problem in problems],
        }
        if planned is not None:
            payload["plan"] = planned.as_dict()
        runtime.output.primary(payload)
    else:
        for problem in problems:
            runtime.output.error(str(problem))
    return 2


def _load_plan(request_path: str, root: str) -> Plan:
    return plan(load_request(request_path), root=root)


def _cli_plan(args: Any, runtime: CliRuntime) -> int:
    try:
        planned = _load_plan(args.request, args.root)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime)
    except (ValueError, OSError) as exc:
        return _emit_problems([Problem("request.invalid", str(exc))], runtime)
    _emit_plan(planned, runtime)
    return 10 if planned.has_changes else 0


def _result_value(result: Any) -> dict[str, Any]:
    if result is None:
        return {"txn": None, "changed": [], "actions": [], "pending": []}
    return {
        "txn": result.txn,
        "changed": list(result.changed),
        "actions": list(result.actions),
        "pending": list(result.pending),
    }


def _cli_apply(args: Any, runtime: CliRuntime) -> int:
    try:
        planned = _load_plan(args.request, args.root)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime)
    except (ValueError, OSError) as exc:
        return _emit_problems([Problem("request.invalid", str(exc))], runtime)
    if not runtime.json_mode:
        _emit_plan(planned, runtime)
    if not planned.has_changes:
        try:
            result = apply(planned)
        except StalePlan as exc:
            return _emit_problems([Problem("plan.stale", str(exc))], runtime, planned=planned)
        except PlanRefused as exc:
            return _emit_problems(exc.problems, runtime, planned=planned)
        if runtime.json_mode:
            runtime.output.primary({"status": "noop", "plan": planned.as_dict(), "result": _result_value(result)})
        return 0
    try:
        confirmed = runtime.confirm("Apply this reviewed Augeaswrap plan?")
    except CliFailure as exc:
        if runtime.json_mode:
            runtime.output.primary({"status": "refused", "plan": planned.as_dict(), "error": exc.message})
            return exc.exit_code
        raise
    if not confirmed:
        if runtime.json_mode:
            runtime.output.primary({"status": "dry_run" if runtime.dry_run else "cancelled", "plan": planned.as_dict()})
        return 0
    try:
        result = apply(planned)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime, planned=planned)
    except StalePlan as exc:
        return _emit_problems([Problem("plan.stale", str(exc))], runtime, planned=planned)
    except ApplyFailed as exc:
        if runtime.json_mode:
            runtime.output.primary(
                {
                    "status": "partial",
                    "txn": exc.txn,
                    "done": exc.done,
                    "failed": exc.failed,
                    "undo": exc.remedy,
                    "plan": planned.as_dict(),
                }
            )
        else:
            runtime.output.error(str(exc))
        return 4
    if runtime.json_mode:
        runtime.output.primary({"status": "applied", "plan": planned.as_dict(), "result": _result_value(result)})
    elif result.txn is None:
        runtime.output.primary("No changes applied.")
    else:
        runtime.output.primary(f"Applied Augeaswrap transaction {result.txn}.")
    return 0


def _cli_revert(args: Any, runtime: CliRuntime) -> int:
    try:
        planned = preview_revert(args.txn, root=args.root)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime)
    if not runtime.json_mode:
        _emit_plan(planned, runtime)
    if not planned.has_changes:
        if runtime.json_mode:
            runtime.output.primary({"status": "noop", "plan": planned.as_dict(), "result": _result_value(None)})
        else:
            runtime.output.primary("Nothing to revert.")
        return 0
    try:
        confirmed = runtime.confirm("Revert this Augeaswrap transaction?")
    except CliFailure as exc:
        if runtime.json_mode:
            runtime.output.primary({"status": "refused", "plan": planned.as_dict(), "error": exc.message})
            return exc.exit_code
        raise
    if not confirmed:
        if runtime.json_mode:
            runtime.output.primary({"status": "dry_run" if runtime.dry_run else "cancelled", "plan": planned.as_dict()})
        return 0
    try:
        result = revert(args.txn, root=args.root)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime, planned=planned)
    except StalePlan as exc:
        return _emit_problems([Problem("plan.stale", str(exc))], runtime, planned=planned)
    except ApplyFailed as exc:
        if runtime.json_mode:
            runtime.output.primary(
                {
                    "status": "partial",
                    "txn": exc.txn,
                    "done": exc.done,
                    "failed": exc.failed,
                    "undo": exc.remedy,
                    "plan": planned.as_dict(),
                }
            )
        else:
            runtime.output.error(str(exc))
        return 4
    if runtime.json_mode:
        runtime.output.primary({"status": "reverted", "plan": planned.as_dict(), "result": _result_value(result)})
    else:
        runtime.output.primary(f"Reverted transaction {args.txn} as {result.txn}.")
    return 0


def _cli_journal(args: Any, runtime: CliRuntime) -> int:
    if args.pending:
        if args.action is not None or args.txn:
            raise CliFailure("journal --pending does not accept list, show, or a transaction id", exit_code=2)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                values = pending_actions(root=args.root)
        except PlanRefused as exc:
            return _emit_problems(exc.problems, runtime)
        for item in caught:
            runtime.output.warn(str(item.message))
        if runtime.json_mode:
            runtime.output.primary(values)
        elif not values:
            runtime.output.primary("No pending unit restarts.")
        else:
            runtime.output.primary(
                "\n".join(
                    f"{item['txn']} {item['unit']} state={item['unit_state']} ({item['action']})"
                    for item in values
                )
            )
        return 0

    action = args.action or "list"
    if action == "show":
        if not args.txn:
            raise CliFailure("journal show requires a transaction id", exit_code=2)
        try:
            value = journal(args.txn, root=args.root)
        except PlanRefused as exc:
            return _emit_problems(exc.problems, runtime)
        assert isinstance(value, dict)
        if runtime.json_mode:
            runtime.output.primary(value)
        else:
            lines = [
                f"{value['id']} {value.get('status', 'unknown')} ({value.get('type', 'apply')})",
                f"  writer: {value.get('writer', '')}",
                f"  created: {value.get('created_at', '')}",
            ]
            for step in value.get("steps", []):
                lines.append(f"  {step['status']:7} {step['name']}")
            runtime.output.primary("\n".join(lines))
        return 0
    if args.txn:
        raise CliFailure("journal list does not accept a transaction id", exit_code=2)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            values = journal(root=args.root)
    except PlanRefused as exc:
        return _emit_problems(exc.problems, runtime)
    for item in caught:
        runtime.output.warn(str(item.message))
    assert isinstance(values, list)
    if runtime.json_mode:
        runtime.output.primary(values)
    elif not values:
        runtime.output.primary("No Augeaswrap transactions.")
    else:
        runtime.output.primary(
            "\n".join(
                f"{value['id']} {value.get('status', 'unknown')} {value.get('writer', '')}"
                for value in values
            )
        )
    return 0


def build_cli(identity: CliIdentity | None = None):
    """Build the Augeaswrap CLI used by its console and module entrypoints."""

    if identity is None:
        identity = CliIdentity.resolve(
            name="AUGEASWRAP",
            long_name="Generic host configuration utility",
            command="augeaswrap",
            distribution="augeaswrap",
        )
    registry = CliRegistry(
        identity,
        prog="augeaswrap",
        description="Plan, apply, inspect and explicitly revert host configuration transactions.",
        getting_started=("augeaswrap plan request.toml", "augeaswrap endpoints check"),
        expected_exceptions=(ValueError, OSError, ApplyFailed, StalePlan),
    )
    request_argument = ArgumentSpec("request", "strict Augeaswrap TOML request", metavar="REQ.toml")
    root_option = _root_option()
    registry.register(
        VerbSpec(
            "plan",
            description="Show guarded host configuration changes without writing host state.",
            group="EXPLORATION",
            arguments=(request_argument,),
            options=(root_option,),
            handler=_cli_plan,
            include_json=True,
            include_progress=False,
        )
    )
    registry.register(
        VerbSpec(
            "apply",
            description="Apply a reviewed plan and keep its transaction journal and pre-images.",
            group="MODIFICATION",
            arguments=(request_argument,),
            options=(root_option,),
            handler=_cli_apply,
            mutating=True,
            dry_run=True,
            confirmation_required=True,
            include_json=True,
            include_progress=False,
        )
    )
    registry.register(
        VerbSpec(
            "revert",
            description="Restore a transaction's pre-images when no later transaction touched them.",
            group="MODIFICATION",
            arguments=(ArgumentSpec("txn", "transaction id to revert", metavar="TXN"),),
            options=(root_option,),
            handler=_cli_revert,
            mutating=True,
            dry_run=True,
            confirmation_required=True,
            include_json=True,
            include_progress=False,
        )
    )
    registry.register(
        VerbSpec(
            "journal",
            description="List retained transactions, inspect a transaction, or query pending restarts.",
            group="EXPLORATION",
            arguments=(
                ArgumentSpec("action", "list journals or show one", parser_kwargs={"nargs": "?", "choices": ("list", "show")}),
                ArgumentSpec("txn", "transaction id when action is show", metavar="TXN", parser_kwargs={"nargs": "?"}),
            ),
            options=(
                root_option,
                OptionSpec(
                    ("--pending",),
                    "list pending unit restarts not yet observed active",
                    parser_kwargs={"action": "store_true"},
                ),
            ),
            handler=_cli_journal,
            include_json=True,
            include_progress=False,
        )
    )
    registry.register(
        VerbSpec(
            "endpoints",
            description="Publish and inspect host endpoint environment facts.",
            group="MAINTENANCE",
            delegate=build_endpoints_cli(identity),
            include_progress=False,
        )
    )
    return registry.build()


def build_surface_cli() -> Any:
    """Build the registered surface without requiring an installed wheel."""

    identity = CliIdentity(
        name="AUGEASWRAP",
        version="surface",
        long_name="Generic host configuration utility",
        command="augeaswrap",
    )
    return build_cli(identity)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Augeaswrap CLI."""

    try:
        app = build_cli()
    except VersionLookupError as exc:
        print(f"[ERROR] augeaswrap: {exc}", file=sys.stderr)
        return 2
    return app.run(argv=argv)


__all__ = ["build_cli", "build_surface_cli", "main"]

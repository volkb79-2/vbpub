"""A fallback-free registry used to prove surface exports stay byte-identical.

``tests/data/surface-baseline-main.json`` was generated from THIS module with
the library source at ``main`` (cli-extended 0.3.0, before LCR-1), by:

    PYTHONPATH=<main checkout>/libraries/cli-extended/src \
        python -c "import sys; sys.path.insert(0, 'tests'); \
        from baseline_app import build_baseline_app; \
        from cli_extended import export_cli_surface, render_cli_surface_json; \
        sys.stdout.write(render_cli_surface_json(export_cli_surface(build_baseline_app())))"

Do not edit this module without regenerating the data file from a library
version that predates the change under test.
"""

from __future__ import annotations

from cli_extended import (
    ArgumentSpec,
    CheckResult,
    CliIdentity,
    CliRegistry,
    DoctorCheck,
    OptionSpec,
    Requires,
    VerbSpec,
    register_doctor,
)


def _ok(args, runtime):
    return 0


def build_baseline_app():
    child = CliRegistry(
        CliIdentity("BASE", "1.0", "Baseline tool", "base"),
        prog="base admission",
        description="Admission group.",
        unexpected_exceptions="report",
    )
    child.register(VerbSpec("show", description="show admission", handler=_ok))
    child.register(
        VerbSpec(
            "set",
            description="set admission",
            handler=_ok,
            mutating=True,
            options=(
                OptionSpec(
                    ("--max-concurrent",),
                    "limit",
                    metavar="N",
                    parser_kwargs={"type": int, "required": True},
                ),
            ),
        )
    )
    registry = CliRegistry(
        CliIdentity("BASE", "1.0", "Baseline tool", "base"),
        prog="base",
        description="Baseline tool.",
        unexpected_exceptions="report",
        global_options=(OptionSpec(("--profile",), "profile name", metavar="NAME"),),
    )
    registry.register(
        VerbSpec(
            "run",
            description="run a lane",
            handler=_ok,
            mutating=True,
            dry_run=True,
            expensive=True,
            arguments=(ArgumentSpec("lane", "lane to run"),),
            options=(
                OptionSpec(("--worktree",), "worktree", metavar="PATH"),
                OptionSpec(("--rejudge",), "rejudge", metavar="ID"),
                OptionSpec(("--rejudge-outcome",), "bucket", metavar="BUCKET"),
            ),
            constraints=(
                Requires("--rejudge-outcome", ("--rejudge",), "needs a rejudge id"),
            ),
        )
    )
    registry.register(
        VerbSpec("list", description="list lanes", handler=_ok, include_json=False)
    )
    registry.register(VerbSpec("admission", description="admission", delegate=child.build()))
    register_doctor(
        registry,
        [DoctorCheck("env", "environment", lambda runtime, args: CheckResult("ok", "fine"))],
    )
    return registry.build()

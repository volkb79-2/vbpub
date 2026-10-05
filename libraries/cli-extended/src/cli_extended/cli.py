"""The ``cli-extended`` console script: surface review workflow and skills."""

from __future__ import annotations

import argparse
import importlib.resources
import io
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import contract
from .audit import STATUSES, AuditItem, run_audit
from .config import ConfigError, CliConfig, load_cli, load_project_config
from .findings import FindingsError, FindingsFile
from .identity import CliIdentity, VersionLookupError
from .parser import (
    CliFailure,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbSpec,
    fixed_help_width,
)
from .review import (
    ReviewCase,
    ReviewCatalog,
    ReviewCatalogError,
    SurfaceSpecError,
    _load_findings_file,
    check_cli_surface,
    load_cli_review_catalog,
    render_cli_review_template,
    sync_cli_surface,
)
from .skills import register_skills_verbs
from .surface import SURFACE_SCHEMA_VERSION, SurfaceError, export_cli_surface

PACK_HELP_COLUMNS = 100
NO_TEMPLATE_ROWS = "No semantic review rows need adding or updating.\n"
RUBRIC_RESOURCE = "review_rubric.md"
_EXPECTED = (
    ConfigError,
    FindingsError,
    ReviewCatalogError,
    SurfaceError,
    SurfaceSpecError,
    ImportError,
    OSError,
)
NONE_LINE = "None."
TASK_TEXT = """\
## Your task

Judge every case above against the rubric and every help block against the
rubric's help checks. Then, following the `cli-extended-review` skill:

1. Edit the review catalog by hand: set each reviewed case's `state`,
   `decision`, `rationale`, `effects` and `test_ids`. Never invent a test ID;
   write the behavioural test first and link its real node ID.
2. Record every problem you find in the findings file by hand, each with a
   concrete `remedy` (a `wontfix` needs a `rationale`).
3. Run `cli-extended surface sync`, then `cli-extended surface check`, then
   `cli-extended surface report`, and stop when `check` passes.

The library never rewrites the catalog or the findings file; only you do.
"""


def _view(
    app: RegisteredCli,
    review_path: Path,
    max_candidates: int | None,
    *,
    tolerate: bool,
) -> tuple[ReviewCatalog, dict[str, Any]]:
    catalog = load_cli_review_catalog(review_path)
    if catalog.cli_id != app.identity.command_name:
        raise ReviewCatalogError(
            f"review catalog cli_id {catalog.cli_id!r} does not match "
            f"registered executable {app.identity.command_name!r}"
        )
    limit = catalog.max_candidates if max_candidates is None else max_candidates
    surface = export_cli_surface(
        app,
        interaction_groups=catalog.interaction_groups,
        max_candidates=limit,
        _tolerate_invalid_interactions=tolerate,
    )
    return catalog, surface


def template_text(
    app: RegisteredCli, review_path: Path, max_candidates: int | None
) -> str:
    """The review-template text for ``app``, or a sentence saying none is needed."""

    catalog, surface = _view(app, review_path, max_candidates, tolerate=False)
    return render_cli_review_template(surface, catalog) or NO_TEMPLATE_ROWS


def _awaiting(candidate: Mapping[str, Any], case: ReviewCase | None) -> str | None:
    if case is None:
        return "new"
    if case.state == "retired":
        return "reappeared"
    if case.state == "pending":
        return "pending"
    if case.reviewed_signature != candidate["signature"]:
        return "changed"
    return None


def _review_state(
    surface: Mapping[str, Any], catalog: ReviewCatalog
) -> tuple[list[tuple[Mapping[str, Any], str, ReviewCase | None]], list[ReviewCase]]:
    cases = catalog.cases_by_id
    awaiting = []
    for candidate in surface["candidates"]:
        case = cases.get(str(candidate["id"]))
        label = _awaiting(candidate, case)
        if label is not None:
            awaiting.append((candidate, label, case))
    candidate_ids = {str(candidate["id"]) for candidate in surface["candidates"]}
    stale = [
        case
        for case in catalog.cases
        if case.case_id not in candidate_ids and case.state != "retired"
    ]
    return awaiting, stale


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _finding_lines(findings: FindingsFile | None) -> list[str]:
    items = findings.open_findings() if findings is not None else ()
    if not items:
        return [NONE_LINE]
    return [
        f"- **{item.severity}** `{item.id}` [{item.category}] "
        f"(route: {item.route or 'none'}): {_one_line(item.summary)} "
        f"Remedy: {_one_line(item.remedy)}"
        for item in items
    ]


def _fence(text: str, info: str) -> list[str]:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", text.rstrip("\n"), fence]


def _case_toml(case: ReviewCase) -> str:
    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [f"id = {encode(case.case_id)}", f"state = {encode(case.state)}"]
    if case.decision is not None:
        lines.append(f"decision = {encode(case.decision)}")
    if case.reviewed_signature is not None:
        lines.append(f"reviewed_signature = {encode(case.reviewed_signature)}")
    if case.rationale:
        lines.append(f"rationale = {encode(case.rationale)}")
    if case.invocation_declared:
        lines.append(f"invocation = {encode(list(case.invocation))}")
    if case.expected_exit_status is not None:
        lines.append(f"expected_exit_status = {case.expected_exit_status}")
    if case.expected_stdout_contains:
        lines.append(f"expected_stdout_contains = {encode(case.expected_stdout_contains)}")
    if case.expected_stderr_contains:
        lines.append(f"expected_stderr_contains = {encode(case.expected_stderr_contains)}")
    if case.effects_declared:
        lines.append(f"effects = {encode(list(case.effects))}")
    if case.test_ids:
        lines.append(f"test_ids = {encode(list(case.test_ids))}")
    if case.retirement_reason:
        lines.append(f"retirement_reason = {encode(case.retirement_reason)}")
    return "\n".join(lines)


def render_report(
    cli_id: str,
    surface: Mapping[str, Any],
    catalog: ReviewCatalog,
    findings: FindingsFile | None,
) -> str:
    """The actionable review list as Markdown."""

    awaiting, stale = _review_state(surface, catalog)
    lines = [f"# CLI review report: {cli_id}", "", "## Open findings", ""]
    lines.extend(_finding_lines(findings))
    lines.extend(("", "## Cases awaiting review", ""))
    lines.extend(
        f"- `{candidate['id']}` ({candidate['kind']}): {label}"
        for candidate, label, _case in awaiting
    )
    if not awaiting:
        lines.append(NONE_LINE)
    lines.extend(("", "## Stale cases", ""))
    lines.extend(f"- `{case.case_id}`: no longer generated; retire or repair" for case in stale)
    if not stale:
        lines.append(NONE_LINE)
    lines.extend(("", "## Incomplete syntax", ""))
    incomplete = list(surface.get("incomplete", ()))
    lines.extend(f"- {_one_line(str(reason))}" for reason in incomplete)
    if not incomplete:
        lines.append(NONE_LINE)
    return "\n".join(lines) + "\n"


def _route_help(app: RegisteredCli, path: Sequence[str]) -> str:
    stdout, stderr = io.StringIO(), io.StringIO()
    with fixed_help_width(PACK_HELP_COLUMNS):
        app.run(argv=[*path[:-1], "help", *path[-1:]], stdout=stdout, stderr=stderr)
    return stdout.getvalue()


def render_pack(
    app: RegisteredCli,
    surface: Mapping[str, Any],
    catalog: ReviewCatalog,
    findings: FindingsFile | None,
) -> str:
    """The deterministic review bundle an agent judges."""

    rubric = importlib.resources.files("cli_extended").joinpath(RUBRIC_RESOURCE)
    lines = [
        rubric.read_text(encoding="utf-8").rstrip("\n"),
        "",
        "## CLI",
        "",
        f"- Identity: {app.identity.headline}",
        f"- Executable: `{app.identity.command_name}`",
        f"- cli-extended contract version: {contract.CONTRACT_VERSION}",
        f"- Surface schema version: {SURFACE_SCHEMA_VERSION}",
        "",
        "## Help",
        "",
        "### root",
        "",
        *_fence(_route_help(app, []), "text"),
    ]
    for route in surface["routes"]:
        path = list(route["path"])
        if path:
            lines.extend(("", f"### `{route['id']}`", ""))
            lines.extend(_fence(_route_help(app, path), "text"))
    lines.extend(("", "## Cases to review", ""))
    awaiting, stale = _review_state(surface, catalog)
    for candidate, label, case in awaiting:
        lines.extend((f"### `{candidate['id']}`", "", f"- kind: {candidate['kind']}"))
        lines.extend((f"- status: {label}", "", "Shape:", ""))
        shape = {
            "route_id": candidate["route_id"],
            "members": candidate.get("members", []),
            "shape": candidate.get("shape", {}),
            "signature": candidate["signature"],
        }
        lines.extend(
            _fence(json.dumps(shape, indent=2, sort_keys=True, ensure_ascii=False), "json")
        )
        lines.extend(("", "Current catalog row:", ""))
        lines.extend(
            _fence(_case_toml(case), "toml") if case is not None else ["(none)"]
        )
        lines.append("")
    for case in stale:
        lines.extend((f"### `{case.case_id}`", "", "- kind: stale"))
        lines.extend(("- status: stale", "", "Current catalog row:", ""))
        lines.extend(_fence(_case_toml(case), "toml"))
        lines.append("")
    if not awaiting and not stale:
        lines.extend((NONE_LINE, ""))
    lines.extend(("## Open findings", ""))
    lines.extend(_finding_lines(findings))
    lines.extend(("", TASK_TEXT.rstrip("\n")))
    return "\n".join(lines) + "\n"


def _context(args: argparse.Namespace) -> tuple[CliConfig, RegisteredCli]:
    config = load_project_config(args.config)
    cli = config.select(args.cli)
    return cli, load_cli(cli)


def _require(cli: CliConfig, verb: str, *names: str) -> list[Path]:
    paths = [getattr(cli, name) for name in names]
    missing = [name for name, path in zip(names, paths) if path is None]
    if missing:
        raise ConfigError(
            f"CLI {cli.id!r} must configure {', '.join(missing)} for 'surface {verb}'"
        )
    return paths


def _guarded(function):
    def handler(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
        try:
            return function(args, runtime)
        except _EXPECTED as exc:
            raise CliFailure(str(exc), exit_code=2) from exc

    return handler


def _sync_or_check(mode: str):
    operation = sync_cli_surface if mode == "sync" else check_cli_surface

    def handler(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
        cli, app = _context(args)
        review, manifest, spec = _require(cli, mode, "review", "manifest", "spec")
        report = operation(
            app,
            review_path=review,
            manifest_path=manifest,
            spec_path=spec,
            max_candidates=args.max_candidates,
            findings_path=cli.findings,
        )
        for finding in report.findings:
            print(f"[REVIEW] {finding}", file=runtime.output.stderr)
        for note in report.notes:
            print(f"[NOTE] {note}", file=runtime.output.stderr)
        if mode == "sync":
            runtime.output.primary("CLI surface files synchronized.")
            return
        if report.passed:
            runtime.output.primary("CLI surface check passed.")
            return
        print("CLI surface check failed.", file=runtime.output.stderr)
        return 1

    return _guarded(handler)


def _template(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
    cli, app = _context(args)
    (review,) = _require(cli, "template", "review")
    runtime.output.stdout.write(template_text(app, review, args.max_candidates))


def _findings(cli: CliConfig, app: RegisteredCli) -> FindingsFile | None:
    return _load_findings_file(app, cli.findings)


def _report(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
    cli, app = _context(args)
    (review,) = _require(cli, "report", "review")
    catalog, surface = _view(app, review, None, tolerate=True)
    runtime.output.stdout.write(render_report(cli.id, surface, catalog, _findings(cli, app)))


def _pack(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
    cli, app = _context(args)
    (review,) = _require(cli, "pack", "review")
    catalog, surface = _view(app, review, None, tolerate=True)
    text = render_pack(app, surface, catalog, _findings(cli, app))
    if args.output is None:
        runtime.output.stdout.write(text)
    else:
        args.output.write_bytes(text.encode("utf-8"))


def _audit_lines(items: Sequence[AuditItem], counts: Mapping[str, int]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(
            f"[{item.status.upper()}] {item.checklist_id} {item.check}: {item.summary}"
        )
        lines.extend(f"    evidence: {line}" for line in item.evidence)
        if item.remedy:
            lines.append(f"    remedy: {item.remedy}")
    lines.append(
        f"audit: {counts['pass']} pass, {counts['warn']} warn, "
        f"{counts['fail']} fail, {counts['manual']} manual"
    )
    return lines


def _audit(args: argparse.Namespace, runtime: CliRuntime) -> int | None:
    project = load_project_config(args.config)
    cli = project.select(args.cli)
    items = run_audit(cli, project)
    counts = {
        status: sum(1 for item in items if item.status == status) for status in STATUSES
    }
    if runtime.json_mode:
        runtime.output.primary(
            {"cli": cli.id, "items": [item.as_dict() for item in items], "summary": counts}
        )
    else:
        for line in _audit_lines(items, counts):
            runtime.output.primary(line)
    return 1 if counts["fail"] else 0


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be an integer of at least 1")
    return number


def _project_options() -> tuple[OptionSpec, ...]:
    return (
        OptionSpec(
            ("--config",),
            "project configuration file (default: found by walking up from the current directory)",
            group="PROJECT",
            metavar="PATH",
            parser_kwargs={"type": Path, "default": None},
        ),
        OptionSpec(
            ("--cli",),
            "which configured CLI to use (required when several are configured)",
            group="PROJECT",
            metavar="ID",
            parser_kwargs={"default": None},
        ),
    )


def _candidate_option() -> OptionSpec:
    return OptionSpec(
        ("--max-candidates",),
        "candidate limit override (default: from the review catalog)",
        group="PROJECT",
        metavar="N",
        parser_kwargs={"type": _positive_int, "default": None},
    )


def _surface_group(registry: CliRegistry) -> RegisteredCli:
    parent = registry.identity
    child = CliRegistry(
        CliIdentity(
            name=parent.name,
            version=parent.version,
            long_name=f"{parent.long_name} — surface review",
            command=parent.command_name,
        ),
        prog=f"{registry.prog} surface",
        description="Generate, check and review a configured CLI's surface contract.",
        logging_logger=registry.logging_logger,
        unexpected_exceptions=registry.unexpected_exceptions,
    )
    plain = {"include_json": False, "include_progress": False}
    child.register(VerbSpec(
        "sync",
        description="write the generated manifest and the marked spec region",
        options=(*_project_options(), _candidate_option()),
        handler=_sync_or_check("sync"),
        **plain,
    ))
    child.register(VerbSpec(
        "check",
        description="exit 1 unless manifest, spec region, catalog and findings are current",
        options=(*_project_options(), _candidate_option()),
        handler=_sync_or_check("check"),
        **plain,
    ))
    child.register(VerbSpec(
        "template",
        description="print review-catalog rows that need adding or updating",
        options=(*_project_options(), _candidate_option()),
        handler=_guarded(_template),
        **plain,
    ))
    child.register(VerbSpec(
        "pack",
        description="write the Markdown review bundle an agent judges",
        options=(
            *_project_options(),
            OptionSpec(
                ("--output",),
                "write the bundle to FILE instead of standard output",
                group="PROJECT",
                metavar="FILE",
                parser_kwargs={"type": Path, "default": None},
            ),
        ),
        handler=_guarded(_pack),
        **plain,
    ))
    child.register(VerbSpec(
        "report",
        description="list open findings, cases awaiting review, stale cases and gaps",
        options=_project_options(),
        handler=_guarded(_report),
        **plain,
    ))
    return child.build()


def build_cli(identity: CliIdentity | None = None) -> RegisteredCli:
    """Build the ``cli-extended`` registry (resolving its identity when omitted)."""

    if identity is None:
        identity = CliIdentity.resolve(
            name="CLI-EXTENDED",
            long_name="shared CLI contract tooling",
            command="cli-extended",
            distribution="cli-extended",
        )
    registry = CliRegistry(
        identity,
        prog="cli-extended",
        description="Review, check and adopt the shared CLI contract.",
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "surface",
        description="sync, check, template, pack or report a CLI's reviewed surface",
        group="REVIEW",
        delegate=_surface_group(registry),
    ))
    registry.register(VerbSpec(
        "audit",
        description="audit a configured CLI against the adoption checklist (read-only)",
        options=_project_options(),
        handler=_guarded(_audit),
        include_progress=False,
    ))
    register_skills_verbs(registry, package="cli_extended")
    return registry.build()


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entrypoint."""

    try:
        app = build_cli()
    except VersionLookupError as exc:
        print(f"[ERROR] cli-extended: {exc}", file=sys.stderr)
        return 2
    return app.run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())

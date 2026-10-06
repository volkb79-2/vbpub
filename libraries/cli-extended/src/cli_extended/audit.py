"""Mechanical adoption audit behind ``cli-extended audit``.

Every check here is mechanical: it inspects the built registry, the project
configuration and (for the checks labelled ``heuristic:``) a plain text scan of
the project sources.  Anything that needs judgement is reported as ``manual``
and handed to the packaged ``cli-extended-adoption`` skill.  The checks map one
to one onto the ``audit:`` rows of ``docs/ADOPTION-CHECKLIST.md``.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .config import CliConfig, ProjectConfig, load_cli
from .findings import FindingsError
from .parser import OptionSpec, RegisteredCli, VerbSpec
from .review import (
    ReviewCatalogError,
    SurfaceSpecError,
    check_cli_surface,
    load_cli_review_catalog,
)
from .skills import SkillError, _load_sources
from .surface import DEFAULT_MAX_CANDIDATES, SurfaceError, export_cli_surface

Status = Literal["pass", "warn", "fail", "manual"]
STATUSES: tuple[Status, ...] = ("pass", "warn", "fail", "manual")

# Check name -> stable checklist id, in checklist (and therefore report) order.
CHECKLIST_IDS: dict[str, str] = {
    "version-source": "AC-01",
    "synopsis-overrides": "AC-04",
    "shadowed-controls": "AC-05",
    "configure-callbacks": "AC-07",
    "hidden-options": "AC-08",
    "exception-policy": "AC-10",
    "mutation-safety": "AC-12",
    "surface-configured": "AC-16",
    "surface-check": "AC-17",
    "surface-complete": "AC-18",
    "skills-packaged": "AC-19",
    "doctor": "AC-20",
    "pytest-plugin": "AC-22",
    "dependency-declared": "AC-24",
    "no-path-hacks": "AC-25",
}

SHADOWED_REPLACEMENTS: dict[str, str] = {
    "--dry-run": "VerbSpec(dry_run=True)",
    "--yes": "VerbSpec(mutating=True) confirmation",
    "--json": "the library --json (VerbSpec include_json)",
    "--traceback": "CliRegistry(unexpected_exceptions='report')",
    "--no-color": "the library colour policy",
    "--color": "the library colour policy",
    "--quiet": "the library verbosity controls",
    "--debug": "the library verbosity controls",
    "--verbose": "the library verbosity controls",
    "--log-level": "the library verbosity controls",
    "--progress": "the library progress control (VerbSpec include_progress)",
    "--debug-raw": "the library verbosity controls",
    "--help": "the library help",
    "--version": "the library version flag",
}

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__",
        ".worktrees", ".tox", ".nox", ".eggs", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "site-packages",
    }
)
EXCLUDED_DIRECTORY_SUFFIX = ".egg-info"
SOURCE_SUFFIXES = (".py", ".toml")
PLUGIN_SUFFIXES = (".py", ".toml", ".ini", ".cfg")
PLUGIN_MODULE = "cli_extended.pytest_plugin"
GATE_FILE_NAME = "run-gate.toml"
# CLI-EXT-28: a test that asserts the library path is ABSENT marks that line
# (or the line after a marker-only line); only marked lines are exempt.
PATH_ASSERTION_MARKER = "# cli-extended: allow-path-assertion"
_REVIEW_ERRORS = (
    ReviewCatalogError,
    SurfaceError,
    SurfaceSpecError,
    FindingsError,
    OSError,
)
_DEPENDENCY = re.compile(r"cli-extended\s*(\[[^\]]*\])?\s*>=", re.IGNORECASE)
_REGEX_USE = re.compile(r"\bre\.")


@dataclass(frozen=True)
class AuditItem:
    """One audit result."""

    check: str
    checklist_id: str
    status: Status
    summary: str
    evidence: tuple[str, ...] = ()
    remedy: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "checklist_id": self.checklist_id,
            "status": self.status,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "remedy": self.remedy,
        }


def _item(
    check: str,
    status: Status,
    summary: str,
    evidence: tuple[str, ...] = (),
    remedy: str | None = None,
) -> AuditItem:
    return AuditItem(check, CHECKLIST_IDS[check], status, summary, evidence, remedy)


def _label(path: tuple[str, ...]) -> str:
    return " ".join(path)


def _apps(
    app: RegisteredCli, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], RegisteredCli]]:
    yield prefix, app
    for verb in app.registered_verbs:
        if verb.delegate is not None:
            yield from _apps(verb.delegate, (*prefix, verb.name))


def _verbs(app: RegisteredCli) -> list[tuple[str, VerbSpec]]:
    return [
        (_label((*prefix, verb.name)), verb)
        for prefix, owner in _apps(app)
        for verb in owner.registered_verbs
    ]


def _options(app: RegisteredCli) -> list[tuple[str, OptionSpec]]:
    found: list[tuple[str, OptionSpec]] = []
    for prefix, owner in _apps(app):
        for option in owner.global_options:
            found.append((_label((*prefix, "(global)")), option))
        for verb in owner.registered_verbs:
            where = _label((*prefix, verb.name))
            found.extend((where, option) for option in verb.options)
    return found


def _scan(
    root: Path, suffixes: tuple[str, ...]
) -> tuple[list[tuple[Path, str]], list[str]]:
    """Read matching text files; return them plus the relative paths that failed."""

    found: list[tuple[Path, str]] = []
    unreadable: list[str] = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(
            name
            for name in names
            if name not in EXCLUDED_DIRECTORIES
            and not name.endswith(EXCLUDED_DIRECTORY_SUFFIX)
        )
        for name in sorted(files):
            if name.endswith(suffixes):
                path = Path(directory) / name
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    unreadable.append(_relative(path, root))
                else:
                    found.append((path, text))
    return found, unreadable


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _verified(
    check: str,
    status: Status,
    summary: str,
    evidence: tuple[str, ...],
    unreadable: list[str],
    remedy: str | None = None,
) -> AuditItem:
    """A scan result: unreadable files are listed and keep a pass from being trusted."""

    evidence = (*evidence, *(f"unreadable: {path}" for path in unreadable))
    if status == "pass" and unreadable:
        return _item(
            check,
            "manual",
            f"{summary}; not fully verified, {len(unreadable)} unreadable file(s)",
            evidence,
            "Make the unreadable files readable, then re-run the audit.",
        )
    return _item(check, status, summary, evidence, remedy)


# --------------------------------------------------------------------- checks


def _version_source(cli: CliConfig, _app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    files, unreadable = _scan(cli.root, SOURCE_SUFFIXES)
    hand_rolled = [
        _relative(path, cli.root)
        for path, text in files
        if "CliIdentity(" in text
        and ("importlib.metadata" in text or (_REGEX_USE.search(text) and "VERSION" in text))
    ]
    if hand_rolled:
        return _verified(
            "version-source",
            "fail",
            "heuristic: a hand-rolled version reader sits next to CliIdentity(...)",
            tuple(hand_rolled),
            unreadable,
            "Replace it with CliIdentity.resolve(name=..., long_name=..., distribution=...).",
        )
    if any("CliIdentity.resolve(" in text for _path, text in files):
        return _verified(
            "version-source", "pass", "heuristic: CliIdentity.resolve( is used", (), unreadable
        )
    return _verified(
        "version-source",
        "manual",
        "heuristic: no CliIdentity.resolve( found; confirm where the version comes from",
        (),
        unreadable,
        "Use CliIdentity.resolve so the version has one source and no fallback.",
    )


def _synopsis_overrides(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    redundant: list[str] = []
    overrides: list[str] = []
    for where, verb in _verbs(app):
        if verb.synopsis is None:
            continue
        derived = replace(verb, synopsis=None).display_synopsis
        if verb.synopsis == derived:
            redundant.append(f"{where}: synopsis {verb.synopsis!r} equals the derived synopsis")
        else:
            overrides.append(
                f"{where}: synopsis {verb.synopsis!r} overrides derived {derived!r}"
            )
    if redundant:
        return _item(
            "synopsis-overrides",
            "warn",
            f"{len(redundant)} redundant synopsis override(s)"
            + (
                f", {len(overrides)} other override(s) need a justification"
                if overrides
                else ""
            ),
            tuple(redundant),
            "Delete the synopsis= argument; the library derives it.",
        )
    if overrides:
        return _item(
            "synopsis-overrides",
            "manual",
            f"{len(overrides)} synopsis override(s) need a justification",
            tuple(overrides),
            "Keep an override only when the derived synopsis misleads.",
        )
    return _item("synopsis-overrides", "pass", "no synopsis overrides")


def _shadowed_controls(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    clashes: list[str] = []
    for where, option in _options(app):
        for flag in option.flags:
            if flag in SHADOWED_REPLACEMENTS:
                clashes.append(
                    f"{where}: {flag} shadows a library control; "
                    f"use {SHADOWED_REPLACEMENTS[flag]}"
                )
    if clashes:
        return _item(
            "shadowed-controls",
            "fail",
            f"{len(clashes)} consumer option(s) shadow library controls",
            tuple(clashes),
            "Remove the hand-rolled option and use the library feature named in the evidence.",
        )
    return _item("shadowed-controls", "pass", "no consumer option shadows a library control")


def _listing(
    check: str,
    app: RegisteredCli,
    selected: Callable[[VerbSpec], bool],
    *,
    ok: str,
    found: str,
    remedy: str,
) -> AuditItem:
    names = tuple(where for where, verb in _verbs(app) if selected(verb))
    if names:
        return _item(check, "manual", f"{len(names)} {found}", names, remedy)
    return _item(check, "pass", ok)


def _mutation_safety(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    return _listing(
        "mutation-safety",
        app,
        lambda verb: verb.mutating and not verb.confirmation_enabled and not verb.dry_run,
        ok="every mutating verb has confirmation or dry-run",
        found="mutating verb(s) have neither confirmation nor dry-run",
        remedy="Judge each: add VerbSpec(dry_run=True) or confirmation, or record why not.",
    )


def _configure_callbacks(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    return _listing(
        "configure-callbacks",
        app,
        lambda verb: verb.configure is not None,
        ok="no verb uses a configure callback",
        found="verb(s) use a configure callback",
        remedy="Judge whether each could be declared with ArgumentSpec/OptionSpec/constraints.",
    )


def _hidden_options(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    names = tuple(
        f"{where}: {'/'.join(option.flags)}"
        for where, option in _options(app)
        if option.hidden
    )
    if names:
        return _item(
            "hidden-options",
            "manual",
            f"{len(names)} hidden option(s) need a justification",
            names,
            "Keep a hidden option only when it is internal or deprecated; record why.",
        )
    return _item("hidden-options", "pass", "no hidden options")


def _exception_policy(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    if app.unexpected_exceptions == "report":
        return _item(
            "exception-policy", "pass", "unexpected_exceptions='report' (library boundary)"
        )
    return _item(
        "exception-policy",
        "warn",
        f"unexpected_exceptions={app.unexpected_exceptions!r}: tracebacks reach users",
        remedy="Pass unexpected_exceptions='report' to CliRegistry; --traceback restores the stack.",
    )


def _surface_configured(cli: CliConfig, _a: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    missing = tuple(
        f"missing: {name}"
        for name in ("review", "manifest", "spec")
        if getattr(cli, name) is None
    )
    if missing:
        return _item(
            "surface-configured",
            "fail",
            "the surface contract is not fully configured",
            missing,
            "Set review, manifest and spec on the [[clis]] entry (see the CONSUMERS guide).",
        )
    return _item("surface-configured", "pass", "review, manifest and spec are configured")


def _surface_check(cli: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    if cli.review is None or cli.manifest is None or cli.spec is None:
        return _item(
            "surface-check", "manual", "surface not configured; nothing to check"
        )
    try:
        report = check_cli_surface(
            app,
            review_path=cli.review,
            manifest_path=cli.manifest,
            spec_path=cli.spec,
            findings_path=cli.findings,
        )
    except _REVIEW_ERRORS as exc:
        return _item(
            "surface-check",
            "fail",
            "the surface check could not run",
            (str(exc),),
            "Fix the reported file, then run `cli-extended surface check`.",
        )
    if report.passed:
        return _item("surface-check", "pass", "the surface check passes")
    return _item(
        "surface-check",
        "fail",
        f"the surface check reports {len(report.findings)} problem(s)",
        report.findings,
        "Run `cli-extended surface sync`, then judge and re-check.",
    )


def _surface_complete(cli: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    try:
        if cli.review is None:
            groups, limit = (), DEFAULT_MAX_CANDIDATES
        else:
            catalog = load_cli_review_catalog(cli.review)
            groups, limit = catalog.interaction_groups, catalog.max_candidates
        surface = export_cli_surface(
            app,
            interaction_groups=groups,
            max_candidates=limit,
            _tolerate_invalid_interactions=True,
        )
    except _REVIEW_ERRORS as exc:
        return _item(
            "surface-complete",
            "fail",
            "the surface could not be exported",
            (str(exc),),
            "Fix the reported problem, then re-run the audit.",
        )
    if surface["syntax_complete"]:
        return _item("surface-complete", "pass", "the exported surface is syntax-complete")
    incomplete = [str(reason) for reason in surface["incomplete"]]
    incomplete.extend(
        f"route {route['id']} is not syntax-complete"
        for route in surface["routes"]
        if not route["syntax_complete"]
    )
    return _item(
        "surface-complete",
        "fail",
        f"the exported surface is incomplete ({len(incomplete)} entries)",
        tuple(sorted(incomplete)),
        "Declare the missing syntax (arguments, options, constraints) on the verbs.",
    )


def _skills_packaged(cli: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    loose = sorted(
        _relative(path, cli.root)
        for path in (cli.root / ".claude" / "skills").glob("*/SKILL.md")
    )
    if loose:
        return _item(
            "skills-packaged",
            "fail",
            "skills live in a .claude/skills source tree",
            tuple(loose),
            "Move them into package data and ship them with register_skills_verbs.",
        )
    if app.skills_package is None:
        return _item(
            "skills-packaged",
            "manual",
            "no packaged skills registered; does this tool need skills?",
            remedy="If agents drive this tool, register_skills_verbs(registry, package=...).",
        )
    try:
        names = sorted(_load_sources(*app.skills_package))
    except SkillError as exc:
        return _item(
            "skills-packaged",
            "fail",
            "a packaged skill does not validate",
            (str(exc),),
            "Fix the named SKILL.md (frontmatter name, description, LF endings).",
        )
    return _item(
        "skills-packaged",
        "pass",
        f"{len(names)} packaged skill(s) validate: {', '.join(names)}",
    )


def _doctor(_c: CliConfig, app: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    if any(verb.name == "doctor" for verb in app.registered_verbs):
        return _item("doctor", "pass", "a doctor verb is registered")
    return _item(
        "doctor",
        "manual",
        "no doctor verb; does this tool have an environment to diagnose?",
        remedy="If it has dependencies or services to verify, register_doctor(registry, checks).",
    )


def _pytest_plugin(cli: CliConfig, _a: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    if cli.review is None:
        return _item("pytest-plugin", "manual", "heuristic: no review catalog configured")
    files, unreadable = _scan(cli.root, PLUGIN_SUFFIXES)
    found = [_relative(path, cli.root) for path, text in files if PLUGIN_MODULE in text]
    if found:
        return _verified(
            "pytest-plugin",
            "pass",
            f"heuristic: {PLUGIN_MODULE} is referenced",
            tuple(found),
            unreadable,
        )
    return _verified(
        "pytest-plugin",
        "fail",
        f"heuristic: a review catalog exists but {PLUGIN_MODULE} is not enabled",
        (),
        unreadable,
        f"Add `-p {PLUGIN_MODULE}` to pytest addopts or `pytest_plugins` in conftest.py.",
    )


def _dependency_declared(cli: CliConfig, _a: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    path = cli.root / "pyproject.toml"
    if not path.is_file():
        return _item(
            "dependency-declared",
            "manual",
            "heuristic: no pyproject.toml; a standalone script",
            remedy="scripts use the installed library (CX-D3)",
        )
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _item(
            "dependency-declared",
            "fail",
            "heuristic: pyproject.toml cannot be read",
            (str(exc),),
            "Fix pyproject.toml.",
        )
    setuptools = table.get("tool", {}).get("setuptools", {})
    mapped = setuptools.get("package-dir", {})
    vendored = sorted(
        f"package-dir {key!r} = {value!r}"
        for key, value in mapped.items()
        if key == "cli_extended" or "cli-extended" in str(value)
    )
    if vendored:
        return _item(
            "dependency-declared",
            "fail",
            "heuristic: package-dir vendors cli_extended",
            tuple(vendored),
            "Depend on the released library: dependencies = ['cli-extended>=X.Y.Z'].",
        )
    dependencies = table.get("project", {}).get("dependencies", [])
    if any(_DEPENDENCY.match(str(entry)) for entry in dependencies):
        return _item(
            "dependency-declared", "pass", "heuristic: pyproject declares cli-extended>="
        )
    return _item(
        "dependency-declared",
        "fail",
        "heuristic: [project].dependencies has no cli-extended>= entry",
        remedy="Add 'cli-extended>=X.Y.Z' with a documented reason for the floor.",
    )


def _without_allowed_assertions(text: str) -> str:
    """Drop lines the author marked as path-absence assertions (CLI-EXT-28)."""

    kept: list[str] = []
    skip_next = False
    for line in text.splitlines():
        stripped = line.strip()
        if skip_next:
            skip_next = False
            continue
        if stripped == PATH_ASSERTION_MARKER:
            skip_next = True
            continue
        if stripped.endswith(PATH_ASSERTION_MARKER):
            continue
        kept.append(line)
    return "\n".join(kept)


def _no_path_hacks(cli: CliConfig, _a: RegisteredCli, _p: ProjectConfig) -> AuditItem:
    files, unreadable = _scan(cli.root, SOURCE_SUFFIXES)
    hits = []
    for path, raw in files:
        text = _without_allowed_assertions(raw)
        if (
            path.name != GATE_FILE_NAME
            and "libraries/cli-extended" in text
            and ("sys.path" in text or "PYTHONPATH" in text)
        ):
            hits.append(_relative(path, cli.root))
    if hits:
        return _verified(
            "no-path-hacks",
            "fail",
            "heuristic: source paths point at the library checkout",
            tuple(hits),
            unreadable,
            "Install cli-extended as a dependency instead of putting its source on a path.",
        )
    return _verified(
        "no-path-hacks",
        "pass",
        "heuristic: no sys.path/PYTHONPATH vendoring found",
        (),
        unreadable,
    )


_CHECKS: dict[str, Callable[[CliConfig, RegisteredCli, ProjectConfig], AuditItem]] = {
    "version-source": _version_source,
    "synopsis-overrides": _synopsis_overrides,
    "shadowed-controls": _shadowed_controls,
    "configure-callbacks": _configure_callbacks,
    "hidden-options": _hidden_options,
    "exception-policy": _exception_policy,
    "mutation-safety": _mutation_safety,
    "surface-configured": _surface_configured,
    "surface-check": _surface_check,
    "surface-complete": _surface_complete,
    "skills-packaged": _skills_packaged,
    "doctor": _doctor,
    "pytest-plugin": _pytest_plugin,
    "dependency-declared": _dependency_declared,
    "no-path-hacks": _no_path_hacks,
}


def run_audit(cli: CliConfig, project: ProjectConfig) -> list[AuditItem]:
    """Run every mechanical check for ``cli`` in checklist order."""

    app = load_cli(cli)
    return [_CHECKS[name](cli, app, project) for name in CHECKLIST_IDS]

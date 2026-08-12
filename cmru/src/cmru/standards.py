"""CMRU project-framework conformance and safe marker updates.

The framework owns only two small, machine-readable adoption markers.  A project
owns every command body, so ``cmru standards --update`` must never rewrite a
bespoke build script merely to claim it is current.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


PROJECT_TEMPLATE_REVISION = 1
RUNNER_TEMPLATE_REVISION = 1
_RUNNER_HEADER_RE = re.compile(r"^# cmru-runner-template-revision:\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ProjectStandardResult:
    name: str
    messages: tuple[str, ...]
    problems: tuple[str, ...]


def _runner_config_path(repo_root: Path, project) -> Path:
    return repo_root / str(project.cwd) / "cmru.build.toml"


def _runner_revision(path: Path) -> int | None:
    if not path.exists():
        return None
    match = _RUNNER_HEADER_RE.search(path.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else None


def assess_projects(
    repo_root: Path,
    projects: Mapping[str, object],
    project_order: Iterable[str],
    selected_names: Iterable[str],
) -> list[ProjectStandardResult]:
    """Assess objective framework requirements after strict config validation."""
    automated = set(project_order)
    results: list[ProjectStandardResult] = []
    for name in selected_names:
        project = projects[name]
        messages = ["strict cmru.toml"]
        problems: list[str] = []
        revision = getattr(project, "template_revision", None)
        if revision != PROJECT_TEMPLATE_REVISION:
            problems.append(
                f"central project template revision is {revision!r}; expected {PROJECT_TEMPLATE_REVISION}"
            )
        else:
            messages.append(f"project template r{revision}")

        if not getattr(project, "changelog", None):
            problems.append("source-first release history is disabled")
        else:
            messages.append(f"history: {project.changelog}")

        steps = getattr(project, "steps", {})
        if name in automated:
            if "run-tests" not in steps:
                problems.append("automated release project has no declared run-tests gate")
            else:
                messages.append("declared release gate")
        else:
            messages.append("not in orchestration.project_order (manual release policy)")

        runner_config = _runner_config_path(repo_root, project)
        runner_revision = _runner_revision(runner_config)
        if runner_revision is None:
            if runner_config.exists():
                problems.append(
                    f"{runner_config.relative_to(repo_root)} lacks cmru runner-template revision header"
                )
            else:
                messages.append("no project-local runner template required")
        elif runner_revision != RUNNER_TEMPLATE_REVISION:
            problems.append(
                f"{runner_config.relative_to(repo_root)} is runner template r{runner_revision}; "
                f"expected r{RUNNER_TEMPLATE_REVISION}"
            )
        else:
            messages.append(f"runner template r{runner_revision}")

        results.append(ProjectStandardResult(name, tuple(messages), tuple(problems)))
    return results


def _atomic_write(path: Path, contents: str) -> None:
    temporary = path.with_name(f".{path.name}.cmru-tmp")
    try:
        temporary.write_text(contents, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _update_project_revisions(config_path: Path, names: Iterable[str]) -> bool:
    contents = config_path.read_text(encoding="utf-8")
    changed = False
    for name in names:
        section = re.compile(
            rf"(?ms)^(\[project\.{re.escape(name)}\]\n)(.*?)(?=^\[|\Z)"
        )
        match = section.search(contents)
        if match is None:
            raise ValueError(f"project.{name} section vanished while updating standards")
        body = match.group(2)
        revision_line = re.compile(r"^template_revision\s*=\s*\d+\s*\n", re.MULTILINE)
        desired = f"template_revision = {PROJECT_TEMPLATE_REVISION}\n"
        if revision_line.search(body):
            updated_body = revision_line.sub(desired, body, count=1)
        else:
            updated_body = desired + body
        if updated_body != body:
            contents = contents[:match.start(2)] + updated_body + contents[match.end(2):]
            changed = True
    if changed:
        _atomic_write(config_path, contents)
    return changed


def _update_runner_header(path: Path) -> bool:
    if not path.exists():
        return False
    contents = path.read_text(encoding="utf-8")
    desired = f"# cmru-runner-template-revision: {RUNNER_TEMPLATE_REVISION}"
    match = _RUNNER_HEADER_RE.search(contents)
    if match:
        updated = contents[:match.start()] + desired + contents[match.end():]
    else:
        updated = "# cmru-runner-template: build-config\n" + desired + "\n" + contents
    if updated == contents:
        return False
    _atomic_write(path, updated)
    return True


def standards_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check CMRU project-framework conformance.  --update changes only CMRU "
            "template revision markers; it never rewrites project-owned commands."
        )
    )
    parser.add_argument("--project", action="append", help="Check one project (repeatable)")
    parser.add_argument("--config", help="Path to cmru.toml")
    parser.add_argument("--update", action="store_true", help="Update stale CMRU-owned revision markers")
    args = parser.parse_args(argv)

    # Import lazily: cli dispatches this verb, and is itself the configuration
    # model used by the report.
    from cmru.cli import _resolve_config, load_config

    config_path = _resolve_config(args.config)
    repo_root, projects, project_order, *_ = load_config(config_path)
    selected = args.project or list(projects)
    unknown = sorted(set(selected) - set(projects))
    if unknown:
        parser.error(f"unknown project(s): {', '.join(unknown)}")

    if args.update:
        changed = _update_project_revisions(config_path, selected)
        for name in selected:
            changed = _update_runner_header(_runner_config_path(repo_root, projects[name])) or changed
        if changed:
            print("[INFO] Updated CMRU-owned template revision marker(s).", flush=True)
        # Re-read to ensure a malformed update can never be reported as conformant.
        repo_root, projects, project_order, *_ = load_config(config_path)

    results = assess_projects(repo_root, projects, project_order, selected)
    problem_count = 0
    for result in results:
        if result.problems:
            problem_count += len(result.problems)
            print(f"[WARN] {result.name}: " + "; ".join(result.problems), flush=True)
        print(f"[INFO] {result.name}: " + "; ".join(result.messages), flush=True)
    if problem_count:
        print(
            f"[ERROR] CMRU standards: {problem_count} issue(s). "
            "Run `cmru standards --update` for safe marker updates, then fix any remaining policy issue.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    print(f"[INFO] CMRU standards: {len(results)} project(s) conform.", flush=True)

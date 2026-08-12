"""CMRU project-framework conformance and safe marker updates.

The project-local ``cmru.toml`` is the complete contract, including runner
controls.  The framework owns one machine-readable revision marker; ``--update``
never rewrites project-owned command bodies merely to claim conformance.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


PROJECT_TEMPLATE_REVISION = 2


@dataclass(frozen=True)
class ProjectStandardResult:
    name: str
    messages: tuple[str, ...]
    problems: tuple[str, ...]


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
                f"project template revision is {revision!r}; expected {PROJECT_TEMPLATE_REVISION}"
            )
        else:
            messages.append(f"project template r{revision}")

        if not getattr(project, "changelog", None):
            problems.append("source-first release history is disabled")
        else:
            messages.append(f"history: {project.changelog}")

        steps = getattr(project, "steps", {})
        runner_steps = getattr(project, "runner_steps", {}) or {}
        noisy_steps = sorted(
            step_name for step_name, step in runner_steps.items()
            if not getattr(step, "quiet", False)
        )
        if noisy_steps:
            problems.append(
                "default orchestration output must be summary-only; set quiet=true for "
                + ", ".join(noisy_steps)
                + " (use --show-run-details for live subprocess output)"
            )
        else:
            messages.append("summary-only default step output")
        if name in automated:
            if "run-tests" not in steps:
                problems.append("automated release project has no declared run-tests gate")
            else:
                messages.append("declared release gate")
        else:
            messages.append("not in orchestration.project_order (manual release policy)")

        messages.append("one project-local release and runner contract")

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


def _update_project_revision(config_path: Path) -> bool:
    contents = config_path.read_text(encoding="utf-8")
    section = re.compile(r"(?ms)^(\[project\]\n)(.*?)(?=^\[|\Z)")
    match = section.search(contents)
    if match is None:
        raise ValueError(f"{config_path}: [project] section vanished while updating standards")
    body = match.group(2)
    revision_line = re.compile(r"^template_revision\s*=\s*\d+\s*\n", re.MULTILINE)
    desired = f"template_revision = {PROJECT_TEMPLATE_REVISION}\n"
    updated_body = revision_line.sub(desired, body, count=1) if revision_line.search(body) else desired + body
    changed = updated_body != body
    if changed:
        contents = contents[:match.start(2)] + updated_body + contents[match.end(2):]
        _atomic_write(config_path, contents)
    return changed


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
        changed = False
        for name in selected:
            project_path = getattr(projects[name], "project_root", None)
            if project_path is None:
                raise ValueError(f"{name}: project-local cmru.toml is required for standards update")
            changed = _update_project_revision(Path(project_path) / "cmru.toml") or changed
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

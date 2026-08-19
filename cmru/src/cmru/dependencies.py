"""Project dependency graph and preflight reporting for CMRU estates.

The orchestration document is authoritative for release ordering. Projects
which stage first-party wheels expose an additional source fact in
"pip/wheels.list"; those inputs must also be represented by the orchestration
project's "depends_on" list. This module compares the two rather than
silently inventing an order.

A project may ALSO declare a "tool" edge (S15, ``[[project.tool_dependencies]]``):
a first-party artifact its own tests/tooling consume, distinct from a "declared"
(release-order) or "artifact" (wheel-input) edge. A tool edge is reported here for
visibility but is DELIBERATELY EXCLUDED from ``project_order`` validation below --
see the comment at its construction site for why routing it through that check would
break the estate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class DependencyEdge:
    provider: str
    consumer: str
    kind: str
    source: str


@dataclass(frozen=True)
class ToolDependencyRef:
    """One project's declared tool-dependency edge, as reported in the graph."""
    provider: str
    version: str
    path: str


@dataclass(frozen=True)
class DependencyReport:
    project_order: tuple[str, ...]
    declared: Mapping[str, tuple[str, ...]]
    artifact_inputs: Mapping[str, tuple[str, ...]]
    edges: tuple[DependencyEdge, ...]
    errors: tuple[str, ...]
    # consumer -> its declared tool dependencies (S15). Defaulted so existing direct
    # DependencyReport(...) construction (tests included) keeps working unchanged.
    tool_dependencies: Mapping[str, tuple[ToolDependencyRef, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "project_order": list(self.project_order),
            "declared": {name: list(values) for name, values in self.declared.items()},
            "artifact_inputs": {
                name: list(values) for name, values in self.artifact_inputs.items()
            },
            "edges": [
                {
                    "provider": edge.provider,
                    "consumer": edge.consumer,
                    "kind": edge.kind,
                    "source": edge.source,
                }
                for edge in self.edges
            ],
            "tool_dependencies": {
                name: [
                    {"provider": ref.provider, "version": ref.version, "path": ref.path}
                    for ref in refs
                ]
                for name, refs in self.tool_dependencies.items()
            },
            "errors": list(self.errors),
            "ok": self.ok,
        }


def _normalise(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def _wheel_inputs(path: Path) -> list[str]:
    """Read the first-party wheel vocabulary from a project's manifest."""
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        values.append(line.split()[0].split("[", 1)[0])
    return values


def build_report(
    *,
    repo_root: Path | None = None,
    project_order: Sequence[str],
    declared: Mapping[str, Sequence[str]],
    projects: Mapping[str, object],
) -> DependencyReport:
    """Build and validate the estate graph from already-parsed project docs."""
    order = tuple(project_order)
    positions = {name: index for index, name in enumerate(order)}
    declared_copy = {name: tuple(values) for name, values in declared.items()}
    errors: list[str] = []
    edges: list[DependencyEdge] = []
    artifact_inputs: dict[str, tuple[str, ...]] = {}

    aliases: dict[str, str] = {}
    for name, project in projects.items():
        for alias in (name, getattr(project, "scm_dist", None)):
            if not alias:
                continue
            key = _normalise(str(alias))
            previous = aliases.get(key)
            if previous is not None and previous != name:
                errors.append(
                    f"projects {previous!r} and {name!r} share first-party package name {alias!r}"
                )
            aliases[key] = name

    for consumer, providers in declared_copy.items():
        for provider in providers:
            if provider not in projects:
                errors.append(
                    f"{consumer!r} declares unknown dependency {provider!r}"
                )
                continue
            if provider == consumer:
                errors.append(f"{consumer!r} depends on itself")
                continue
            edges.append(DependencyEdge(provider, consumer, "declared", "cmru.orchestration.toml"))
            if positions.get(provider, len(order)) >= positions.get(consumer, -1):
                errors.append(
                    f"{consumer!r} depends on {provider!r}, but project_order does not place "
                    "the provider first"
                )

    for consumer, project in projects.items():
        project_root = getattr(project, "project_root", None)
        if project_root is None:
            continue
        wheels_path = Path(project_root) / "pip" / "wheels.list"
        if not wheels_path.is_file():
            continue
        raw_inputs = _wheel_inputs(wheels_path)
        resolved: list[str] = []
        for package in raw_inputs:
            provider = aliases.get(_normalise(package))
            if provider is None:
                errors.append(
                    f"{consumer!r}: {wheels_path.parent.name}/{wheels_path.name} names "
                    f"unknown first-party project/package {package!r}"
                )
                continue
            if provider == consumer:
                errors.append(f"{consumer!r}: first-party wheel list contains itself")
                continue
            if provider not in resolved:
                resolved.append(provider)
            source = str(wheels_path)
            if repo_root is not None:
                try:
                    source = str(wheels_path.relative_to(repo_root))
                except ValueError:
                    pass
            edges.append(DependencyEdge(provider, consumer, "artifact", source))
            if provider not in declared_copy.get(consumer, ()):
                errors.append(
                    f"{consumer!r} consumes the {package!r} wheel from {provider!r}, but "
                    f"orchestration.project.{consumer}.depends_on does not declare it"
                )
        artifact_inputs[consumer] = tuple(resolved)

    # Tool edges (S15): a first-party artifact a project's OWN tests/tooling consume
    # (e.g. cmru's vendored assay zipapp). Reported like the two edge kinds above, but
    # DELIBERATELY NEVER checked against `positions` / project_order: cmru's own tests
    # run tools/assay/*.pyz while `assay` already `depends_on = ["cmru"]` for RELEASE
    # ORDER. Routing this edge through the same ordering check as a "declared" edge
    # would make cmru->assay a cycle against assay->cmru and break the estate -- this
    # edge kind exists specifically because that ordering relationship is unwanted, not
    # because it was left out by omission. Do not "fix" this by adding an ordering
    # check here.
    tool_dependencies: dict[str, tuple[ToolDependencyRef, ...]] = {}
    for consumer, project in projects.items():
        refs: list[ToolDependencyRef] = []
        for dependency in getattr(project, "tool_dependencies", ()) or ():
            provider = getattr(dependency, "project", None)
            if provider not in projects:
                errors.append(
                    f"{consumer!r} declares a tool dependency on unknown project {provider!r}"
                )
                continue
            if provider == consumer:
                errors.append(f"{consumer!r} declares a tool dependency on itself")
                continue
            edges.append(
                DependencyEdge(
                    provider, consumer, "tool",
                    f"{consumer}: project.tool_dependencies[{provider!r}]",
                )
            )
            refs.append(
                ToolDependencyRef(
                    provider=provider,
                    version=getattr(dependency, "version", ""),
                    path=getattr(dependency, "path", ""),
                )
            )
        if refs:
            tool_dependencies[consumer] = tuple(refs)

    unique: dict[tuple[str, str, str], DependencyEdge] = {}
    for edge in edges:
        unique.setdefault((edge.provider, edge.consumer, edge.kind), edge)
    return DependencyReport(
        project_order=order,
        declared=declared_copy,
        artifact_inputs=artifact_inputs,
        edges=tuple(unique.values()),
        errors=tuple(dict.fromkeys(errors)),
        tool_dependencies=tool_dependencies,
    )


def render_text(report: DependencyReport) -> str:
    lines = ["CMRU PROJECT DEPENDENCY GRAPH", ""]
    for consumer in report.project_order:
        providers = list(report.declared.get(consumer, ()))
        artifact = list(report.artifact_inputs.get(consumer, ()))
        tool = list(report.tool_dependencies.get(consumer, ()))
        detail = ", ".join(providers) if providers else "none"
        lines.append(f"  {consumer} <- {detail}  (declared release order)")
        if artifact:
            lines.append(f"    consumes first-party wheels: {', '.join(artifact)}")
        if tool:
            lines.append(
                "    consumes first-party tools (excluded from release ordering): "
                + ", ".join(f"{ref.provider}@{ref.version}" for ref in tool)
            )
    lines.append("")
    if report.errors:
        lines.append("PREFLIGHT: FAIL")
        lines.extend(f"  - {error}" for error in report.errors)
    else:
        lines.append("PREFLIGHT: PASS")
    return "\n".join(lines)


def render_comment_block(report: DependencyReport) -> str:
    """Render the generated TOML comment block kept for human orientation."""
    body = render_text(report).splitlines()
    return "\n".join(
        [
            "# BEGIN CMRU GENERATED DEPENDENCY GRAPH",
            "# Generated by: cmru dependencies --write",
            *[f"# {line}" if line else "#" for line in body],
            "# END CMRU GENERATED DEPENDENCY GRAPH",
        ]
    )


def write_comment_block(path: Path, report: DependencyReport) -> None:
    """Replace or insert only the marked generated comment region."""
    start = "# BEGIN CMRU GENERATED DEPENDENCY GRAPH"
    end = "# END CMRU GENERATED DEPENDENCY GRAPH"
    text = path.read_text(encoding="utf-8")
    block = render_comment_block(report)
    start_index = text.find(start)
    end_index = text.find(end)
    if (start_index >= 0) != (end_index >= 0) or end_index < start_index:
        raise ValueError(f"{path}: malformed generated dependency graph markers")
    if start_index >= 0:
        end_index += len(end)
        updated = text[:start_index] + block + text[end_index:]
    else:
        marker = "[orchestration]"
        marker_index = text.find(marker)
        if marker_index < 0:
            raise ValueError(f"{path}: missing [orchestration] table")
        updated = text[:marker_index] + block + "\n" + text[marker_index:]
    if not updated.endswith("\n"):
        updated += "\n"
    path.write_text(updated, encoding="utf-8")

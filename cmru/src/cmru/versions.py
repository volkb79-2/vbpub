"""Version policy CLI and native artifact writers (FEAT-03).

Resolution is explicit and read-only check never writes. A root target is
resolved once with root policy; a project target (including a project overlay
of a root target) uses project policy and records its state in that project's
cmru.toml.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from cmru import exit_codes
from cmru.config import (
    ForgeConfig,
    InvocationContext,
    effective_versions_for_project,
    load_forge_config,
    resolve_invocation_context,
)
from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cmru.cli_support import CMRUArgumentParser, TargetSelectionError, select_target_names
from cmru.version_config import deep_merge_versions, parse_versions_section
from cmru.version_registry import (
    Candidate,
    RegistryError,
    candidates,
    candidate_for,
    normalized_version,
    validate_constraint,
    version_satisfies,
)


DEFAULT_AGE_WINDOW_DAYS = 14
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*"
    r"((?:(?:===|==|!=|~=|<=|>=|<|>)[^;]*)?)\s*$"
)


class VersionsError(RuntimeError):
    """An invalid version command, policy, or generated output."""


class VersionsPrerequisiteError(VersionsError):
    """A required registry, environment value, or executable is unavailable."""


class VersionsOperationError(VersionsError):
    """A native resolver or writer failed after its prerequisites were found."""


@dataclass(frozen=True)
class TargetResult:
    target_id: str
    version: str
    resolved_at: datetime
    age_cutoff: datetime
    sources: Mapping[str, Candidate]
    override: bool
    reason: str | None
    expires: str | None
    owner: str

    def state(self) -> dict[str, object]:
        return {
            "version": self.version,
            "resolved_at": _timestamp(self.resolved_at),
            "age_cutoff": _timestamp(self.age_cutoff),
            "sources": {
                source_type: {
                    "version": candidate.version,
                    "released_at": _timestamp(candidate.released_at),
                    "age_source": candidate.age_source,
                    **({"tag": candidate.tag} if candidate.tag else {}),
                }
                for source_type, candidate in sorted(self.sources.items())
            },
            **({"provenance": self.reason} if self.reason else {}),
        }


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_document(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            result = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VersionsError(f"could not read {path}: {exc}") from exc
    return result


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _toml_key(value: str) -> str:
    if _BARE_KEY.fullmatch(value):
        return value
    return json.dumps(value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise VersionsError(f"cannot render TOML value of type {type(value).__name__}")


def _render_versions_toml(versions: Mapping[str, object]) -> str:
    lines: list[str] = []

    def render_table(path: list[str], table: Mapping[str, object]) -> None:
        scalars = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
        children = [(key, value) for key, value in table.items() if isinstance(value, dict)]
        if lines:
            lines.append("")
        lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")
        for key, value in scalars:
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
        for key, child in children:
            if child:
                render_table([*path, str(key)], child)

    if versions:
        render_table(["versions"], versions)
    return "\n".join(lines) + ("\n" if lines else "")


def _header_is_versions(line: str) -> bool:
    stripped = line.strip()
    if not (stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[[")):
        return False
    try:
        table = tomllib.loads(stripped + "\n")
    except tomllib.TOMLDecodeError:
        return False
    return "versions" in table


def _replace_versions_region(document: str, versions: Mapping[str, object]) -> str:
    lines = document.splitlines(keepends=True)
    section_indices = [index for index, line in enumerate(lines) if line.lstrip().startswith("[")]
    blocks: list[tuple[int, int, bool]] = []
    for offset, start in enumerate(section_indices):
        end = section_indices[offset + 1] if offset + 1 < len(section_indices) else len(lines)
        blocks.append((start, end, _header_is_versions(lines[start])))
    version_blocks = [(start, end) for start, end, is_versions in blocks if is_versions]
    rendered = _render_versions_toml(versions)
    if not version_blocks:
        base = document.rstrip()
        if not rendered:
            return document
        return (base + "\n\n" if base else "") + rendered

    first = version_blocks[0][0]
    removal = {index for start, end in version_blocks for index in range(start, end)}
    output: list[str] = []
    for index, line in enumerate(lines):
        if index == first:
            if rendered:
                output.append(rendered)
            continue
        if index in removal:
            continue
        output.append(line)
    result = "".join(output)
    return re.sub(r"\n{3,}", "\n\n", result).rstrip() + "\n"


def _update_versions_file(path: Path, mutate) -> None:
    original = path.read_text(encoding="utf-8")
    document = _load_document(path)
    versions = copy.deepcopy(document.get("versions", {}))
    updated = mutate(versions)
    if not isinstance(updated, dict):
        raise VersionsError("internal error: version document update did not return a table")
    try:
        parse_versions_section(updated, f"{path} [versions]", allow_partial=True)
    except ValueError as exc:
        raise VersionsError(str(exc)) from exc
    rendered = _replace_versions_region(original, updated)
    if rendered != original:
        _write_text_atomic(path, rendered)


class _FileTransaction:
    """Restore all declared output files if any native writer fails."""

    def __init__(self) -> None:
        self._before: dict[Path, tuple[bytes, int] | None] = {}

    def track(self, path: Path) -> None:
        path = path.absolute()
        if path.is_symlink():
            raise VersionsError(f"refusing to replace symlink output {path}")
        if path.exists() and not path.is_file():
            raise VersionsError(f"refusing to replace non-file output {path}")
        path = path.resolve(strict=False)
        if path in self._before:
            return
        if path.is_file():
            self._before[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
        else:
            self._before[path] = None

    def rollback(self) -> None:
        for path, data in reversed(list(self._before.items())):
            if data is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                content, mode = data
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.rollback.", dir=path.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(temporary, mode)
                    os.replace(temporary, path)
                except BaseException:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise


def _version_key(candidate: Candidate) -> tuple:
    from cmru.version_registry import _semver_key

    key = _semver_key(candidate.version)
    if key is None:
        raise VersionsError(f"unsupported version spelling {candidate.version!r}")
    return key


def _target_sources(target: Mapping[str, object]) -> dict[str, Mapping[str, str]]:
    return {
        source_type: value
        for source_type, value in target.items()
        if source_type in {"npm", "pypi", "go", "oci"} and isinstance(value, dict)
    }


def _resolve_target(
    target_id: str,
    target: Mapping[str, object],
    *,
    age_window_days: int,
    resolved_at: datetime,
    owner: str,
) -> TargetResult:
    source_tables = _target_sources(target)
    if not source_tables:
        raise VersionsError(f"target {target_id!r} has no configured registry sources")
    constraint = str(target["constraint"])
    cutoff = resolved_at - timedelta(days=age_window_days)
    override = target.get("version")
    selected: dict[str, Candidate] = {}
    try:
        if override is not None:
            version = str(override)
            if not version_satisfies(version, constraint):
                raise VersionsError(
                    f"target {target_id!r} exact override {version!r} violates {constraint!r}"
                )
            for source_type, source in source_tables.items():
                selected[source_type] = candidate_for(source_type, source, version)
                if normalized_version(selected[source_type].version) != normalized_version(version):
                    raise VersionsError(
                        f"target {target_id!r} source {source_type} does not publish exact version {version!r}"
                    )
            too_new = [candidate for candidate in selected.values() if candidate.released_at > cutoff]
            reason = str(target["reason"])
            expires = target.get("expires")
            if expires is not None and str(expires) <= resolved_at.date().isoformat():
                raise VersionsError(
                    f"target {target_id!r} exact override expired on {expires}; update or remove it"
                )
            if too_new and expires is None:
                freshest = max(too_new, key=lambda item: item.released_at)
                raise VersionsError(
                    f"target {target_id!r} override {version!r} bypasses the {age_window_days}-day age window "
                    f"(published {_timestamp(freshest.released_at)}); set a future expires date after review"
                )
            return TargetResult(
                target_id=target_id, version=version, resolved_at=resolved_at,
                age_cutoff=cutoff, sources=selected, override=True, reason=reason,
                expires=str(expires) if expires is not None else None, owner=owner,
            )

        pools: dict[str, dict[str, Candidate]] = {}
        for source_type, source in source_tables.items():
            records = candidates(source_type, source, constraint)
            compatible = {
                key: candidate for key, candidate in records.items()
                if version_satisfies(candidate.version, constraint)
            }
            eligible = {
                key: candidate for key, candidate in compatible.items()
                if candidate.released_at <= cutoff
            }
            if not eligible:
                if not compatible:
                    detail = "registry returned no compatible candidates"
                else:
                    newest_date = max(item.released_at for item in compatible.values())
                    detail = f"newest compatible release is {_timestamp(newest_date)}, newer than the cutoff"
                raise VersionsError(
                    f"target {target_id!r} source {source_type} has no age-eligible version: {detail} "
                    f"(age_window_days={age_window_days}, cutoff={_timestamp(cutoff)})"
                )
            pools[source_type] = eligible

        mode = target["mode"]
        if mode == "single":
            source_type = next(iter(pools))
            chosen = max(pools[source_type].values(), key=_version_key)
            selected[source_type] = chosen
            version = chosen.version
        else:
            common = set.intersection(*(set(pool) for pool in pools.values()))
            if not common:
                raise VersionsError(
                    f"aligned target {target_id!r} has no age-eligible version shared by "
                    f"{', '.join(sorted(pools))}"
                )
            selected_key = max(
                common,
                key=lambda key: max(_version_key(pool[key]) for pool in pools.values()),
            )
            selected = {source_type: pool[selected_key] for source_type, pool in pools.items()}
            version = selected[next(iter(source_tables))].version
            if any(
                normalized_version(candidate.version) != normalized_version(version)
                for candidate in selected.values()
            ):
                raise VersionsError(
                    f"aligned target {target_id!r} matched inconsistent source spellings for {selected_key!r}"
                )
        return TargetResult(
            target_id=target_id, version=version, resolved_at=resolved_at,
            age_cutoff=cutoff, sources=selected, override=False, reason=None,
            expires=None, owner=owner,
        )
    except RegistryError as exc:
        message = str(exc)
        if message.startswith("registry does not publish exact version"):
            raise VersionsError(f"target {target_id!r}: {message}") from exc
        raise VersionsPrerequisiteError(f"target {target_id!r}: {message}") from exc


def _resolve_targets(
    targets: Mapping[str, object],
    *,
    age_window_days: int,
    resolved_at: datetime,
    owner: str,
) -> dict[str, TargetResult]:
    results: dict[str, TargetResult] = {}
    for target_id, target in targets.items():
        if not isinstance(target, dict):
            raise VersionsError(f"target {target_id!r} is not a table")
        results[target_id] = _resolve_target(
            target_id, target, age_window_days=age_window_days,
            resolved_at=resolved_at, owner=owner,
        )
    return results


def _selected_projects(forge: ForgeConfig, context: InvocationContext, target: str | None) -> list[str]:
    if context.config_kind == "project":
        order = list(forge.projects)
    elif forge.orchestration:
        order = list(forge.orchestration.project_order)
    else:
        order = list(forge.projects)
    try:
        return select_target_names(
            target,
            forge.projects,
            order,
            context_project=context.project_name,
            estate_scope=context.scope == "estate",
        )
    except TargetSelectionError as exc:
        raise VersionsError(str(exc)) from exc


def _project_root(forge: ForgeConfig, name: str) -> Path:
    project = forge.projects[name]
    if project.project_root is not None:
        return project.project_root.resolve()
    if forge.orchestration is not None:
        path = forge.orchestration.project_configs.get(name)
        if path is not None:
            return path.parent.resolve()
    return forge.repo_root.resolve()


def _versions_for_project(forge: ForgeConfig, name: str) -> dict[str, object]:
    try:
        return effective_versions_for_project(forge, name)
    except SystemExit as exc:
        raise VersionsError(f"could not construct effective version config for {name}") from exc


def _local_target_overlays(forge: ForgeConfig, name: str) -> dict[str, dict[str, object]]:
    local = forge.projects[name].versions.get("targets", {})
    root = forge.versions.get("targets", {})
    if not isinstance(local, dict) or not isinstance(root, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for target_id, overlay in local.items():
        if not isinstance(overlay, dict):
            continue
        base = root.get(target_id)
        result[target_id] = (
            deep_merge_versions(base, overlay)
            if isinstance(base, dict) else copy.deepcopy(overlay)
        )
    return result


def _merged_outputs(forge: ForgeConfig, name: str) -> dict[str, dict[str, str]]:
    root_outputs = forge.versions.get("outputs", {})
    project_outputs = forge.projects[name].versions.get("outputs", {})
    merged = deep_merge_versions(
        root_outputs if isinstance(root_outputs, dict) else {},
        project_outputs if isinstance(project_outputs, dict) else {},
    )
    return merged


def _report_record(result: TargetResult, declaration: Mapping[str, object], current: object = None) -> dict:
    return {
        "target": result.target_id,
        "owner": result.owner,
        "eligible_version": result.version,
        "recorded_version": current if isinstance(current, str) else None,
        "mode": declaration.get("mode"),
        "constraint": declaration.get("constraint"),
        "override": result.override,
        "reason": result.reason,
        "expires": result.expires,
        "resolved_at": _timestamp(result.resolved_at),
        "age_cutoff": _timestamp(result.age_cutoff),
        "sources": {
            source_type: {
                "version": candidate.version,
                "released_at": _timestamp(candidate.released_at),
                "age_source": candidate.age_source,
                **({"tag": candidate.tag} if candidate.tag else {}),
            }
            for source_type, candidate in sorted(result.sources.items())
        },
        "status": (
            "override" if result.override else
            "matches" if current == result.version else
            "refresh-available" if current is not None else
            "unresolved"
        ),
    }


def _emit_age_evidence_warnings(
    root_results: Mapping[str, TargetResult],
    project_results: Mapping[str, Mapping[str, TargetResult]],
) -> None:
    """Call out timestamp evidence that is weaker than registry publication time."""
    warnings: set[str] = set()
    all_results = [("root", root_results), *sorted(project_results.items())]
    for owner, results in all_results:
        for target_id, result in results.items():
            for source_type, candidate in result.sources.items():
                if candidate.age_source == "go-proxy-info-vcs-commit-time":
                    warnings.add(
                        f"CMRU versions: warning: {owner}:{target_id} uses Go proxy .info Time "
                        "(VCS commit time), not proxy publication time"
                    )
                elif candidate.age_source == "oci-image-created-fallback":
                    warnings.add(
                        f"CMRU versions: warning: {owner}:{target_id} uses publisher-supplied OCI "
                        "image-created time because the registry has no Last-Modified timestamp"
                    )
    for warning in sorted(warnings):
        print(warning, file=sys.stderr)


def _read_recorded(target: Mapping[str, object]) -> object:
    resolved = target.get("resolved")
    return resolved.get("version") if isinstance(resolved, dict) else None


def _target_declarations_for_project(forge: ForgeConfig, name: str) -> dict[str, dict[str, object]]:
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    result = {key: value for key, value in root_targets.items() if isinstance(value, dict)}
    result.update(_local_target_overlays(forge, name))
    return result




def _recorded_versions(
    forge: ForgeConfig,
    root_results: Mapping[str, TargetResult],
    project_results: Mapping[str, Mapping[str, TargetResult]],
    declarations: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> list[dict]:
    records: list[dict] = []
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    for target_id, result in root_results.items():
        declaration = root_targets.get(target_id, {})
        records.append(_report_record(
            result,
            declaration if isinstance(declaration, dict) else {},
            _read_recorded(declaration) if isinstance(declaration, dict) else None,
        ))
    for name, project_targets in project_results.items():
        raw_local = forge.projects[name].versions.get("targets", {})
        if not isinstance(raw_local, dict):
            raw_local = {}
        for target_id, result in project_targets.items():
            declaration = declarations[name].get(target_id, {})
            local = raw_local.get(target_id, {})
            recorded = _read_recorded(local) if isinstance(local, dict) else None
            records.append(_report_record(result, declaration, recorded))
    return records


def _safe_relative_path(root: Path, value: str, *, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise VersionsError(f"{label} must be a relative path within {root}")
    unresolved = root / relative
    current = unresolved
    while current != root and current != current.parent:
        if current.is_symlink():
            raise VersionsError(f"{label} traverses symlink path {current}")
        current = current.parent
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise VersionsError(f"{label} resolves outside project directory: {candidate}") from exc
    return candidate


def _template_artifacts(
    project_root: Path,
    outputs: Mapping[str, Mapping[str, str]],
    results: Mapping[str, TargetResult],
    date_string: str,
) -> list[tuple[Path, str]]:
    if not outputs:
        return []
    try:
        from jinja2 import Environment, StrictUndefined
    except ImportError as exc:
        raise VersionsError(
            "configured [versions.outputs] require Jinja2; install CMRU with its "
            "versions-templates extra"
        ) from exc
    context = {
        "resolved_at": max((_timestamp(item.resolved_at) for item in results.values()), default=None),
        "targets": {
            target_id: {
                "version": result.version,
                "override": result.override,
                "reason": result.reason,
                "expires": result.expires,
                "owner": result.owner,
                "age_cutoff": _timestamp(result.age_cutoff),
                "sources": {
                    source_type: {
                        "version": item.version,
                        "released_at": _timestamp(item.released_at),
                        "age_source": item.age_source,
                        "tag": item.tag,
                    }
                    for source_type, item in sorted(result.sources.items())
                },
            }
            for target_id, result in sorted(results.items())
        },
    }
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    artifacts: list[tuple[Path, str]] = []
    for output_id, config in outputs.items():
        template_path = _safe_relative_path(
            project_root, config["template"], label=f"versions.outputs.{output_id}.template",
        )
        if not template_path.is_file():
            raise VersionsError(f"configured version template does not exist: {template_path}")
        try:
            rendered = environment.from_string(template_path.read_text(encoding="utf-8")).render(**context)
        except Exception as exc:
            raise VersionsError(f"could not render version output {output_id!r}: {exc}") from exc
        stable_path = _safe_relative_path(project_root, config["path"], label=f"versions.outputs.{output_id}.path")
        dated_name = config["dated_path"].replace("{date}", date_string)
        dated_path = _safe_relative_path(
            project_root, dated_name, label=f"versions.outputs.{output_id}.dated_path",
        )
        artifacts.extend(((dated_path, rendered), (stable_path, rendered)))
    return artifacts


def _native_output_files(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
    *,
    now: datetime,
    outputs: Mapping[str, Mapping[str, str]],
    oci_results: Mapping[str, TargetResult] | None = None,
) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    date_string = now.astimezone(timezone.utc).strftime("%Y%m%d")
    oci_items: dict[str, dict[str, object]] = {}

    for target_id, result in (oci_results if oci_results is not None else results).items():
        declaration = declarations.get(target_id, {})
        for source_type, candidate in result.sources.items():
            source = declaration.get(source_type, {})
            if not isinstance(source, dict):
                continue
            if source_type == "oci":
                oci_items[target_id] = {
                    "image": source["image"],
                    "version": candidate.version,
                    "tag": candidate.tag,
                    "released_at": _timestamp(candidate.released_at),
                    "age_source": candidate.age_source,
                    "reason": result.reason,
                }

    if oci_items:
        payload = {
            "schema_version": 1,
            "generated_by": "cmru versions resolve",
            "resolved_at": _timestamp(now),
            "targets": oci_items,
        }
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        oci_paths = (
            _safe_relative_path(project_root, f"versions/oci-images-{date_string}.json", label="generated OCI path"),
            _safe_relative_path(project_root, "versions/oci-images.json", label="generated OCI path"),
        )
        for path in oci_paths:
            if path.exists():
                try:
                    previous = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise VersionsError(f"refusing to overwrite an unrecognized OCI output: {path}") from exc
                if not isinstance(previous, dict) or previous.get("generated_by") != "cmru versions resolve":
                    raise VersionsError(f"refusing to overwrite an OCI output not generated by CMRU: {path}")
        files.extend((path, content) for path in oci_paths)

    files.extend(_template_artifacts(project_root, outputs, results, date_string))
    _assert_unique_output_paths(files)
    return files


def _assert_unique_output_paths(files: list[tuple[Path, str]]) -> None:
    seen: set[Path] = set()
    for path, _content in files:
        canonical = path.resolve(strict=False)
        if canonical in seen:
            raise VersionsError(f"multiple version outputs target the same file: {canonical}")
        seen.add(canonical)


def _compile_python_constraints(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
) -> list[tuple[Path, str]]:
    selected: dict[str, tuple[str, str, TargetResult, Mapping[str, str]]] = {}
    for target_id, result in results.items():
        candidate = result.sources.get("pypi")
        if candidate is None:
            continue
        declaration = declarations.get(target_id, {})
        source = declaration.get("pypi", {})
        if not isinstance(source, dict):
            raise VersionsError(f"PyPI source declaration missing for target {target_id!r}")
        name = re.sub(r"[-_.]+", "-", str(source["name"])).lower()
        previous = selected.get(name)
        if previous and previous[0] != candidate.version:
            raise VersionsError(f"Python package {name!r} has conflicting target versions")
        if previous and previous[1] != target_id:
            raise VersionsError(f"multiple PyPI targets resolve the same Python package {name!r}")
        selected[name] = (candidate.version, target_id, result, source)
    if not selected:
        return []

    registries = {str(item[3]["registry"]).rstrip("/") for item in selected.values()}
    if len(registries) != 1:
        raise VersionsError(
            "all PyPI targets written to one constraints artifact must use the same registry; "
            "split them into separate CMRU projects or use separate native outputs"
        )
    registry = next(iter(registries))
    if registry == "https://pypi.org":
        index_url = "https://pypi.org/simple"
    elif registry.endswith("/simple"):
        index_url = registry
    elif registry.endswith("/pypi"):
        index_url = registry[:-5].rstrip("/") + "/simple"
    else:
        index_url = registry + "/simple"

    global_cutoff = min(item[2].age_cutoff for item in selected.values())
    date_string = max(item[2].resolved_at for item in selected.values()).astimezone(timezone.utc).strftime("%Y%m%d")
    output_paths = [
        _safe_relative_path(
            project_root, f"constraints/constraints-{date_string}.txt", label="generated constraints path",
        ),
        _safe_relative_path(project_root, "constraints/constraints.txt", label="generated constraints path"),
    ]
    for path in output_paths:
        if path.exists():
            try:
                marker = path.read_text(encoding="utf-8").startswith(
                    "# Generated by cmru versions resolve"
                )
            except OSError as exc:
                raise VersionsError(f"could not inspect generated constraints output {path}: {exc}") from exc
            if not marker:
                raise VersionsError(f"refusing to overwrite a non-CMRU constraints file: {path}")
    command = [
        "uv", "pip", "compile", "-",
        "--index", f"cmru={index_url}",
        "--default-index", index_url,
        "--index-strategy", "first-index",
        "--exclude-newer", _timestamp(global_cutoff),
    ]
    for name, (_version, _target_id, result, _source) in sorted(selected.items()):
        candidate = result.sources["pypi"]
        if candidate.released_at > result.age_cutoff:
            command.extend(("--exclude-newer-package", f"{name}=false"))
        elif result.age_cutoff > global_cutoff:
            command.extend(("--exclude-newer-package", f"{name}={_timestamp(result.age_cutoff)}"))

    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for _version, _target_id, _result, source in selected.values()
    }
    if len(auth_refs) != 1:
        raise VersionsPrerequisiteError(
            "PyPI targets using one constraints index must share one credential environment-variable mapping"
        )
    environment = os.environ.copy()
    token_env, username_env, password_env = next(iter(auth_refs))
    if token_env:
        token = os.environ.get(str(token_env), "")
        if not token:
            raise VersionsPrerequisiteError(
                f"required registry token environment variable {token_env} is unset or empty"
            )
        environment["UV_INDEX_CMRU_USERNAME"] = "__token__"
        environment["UV_INDEX_CMRU_PASSWORD"] = token
    elif username_env:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} "
                f"and {password_env} must be set"
            )
        environment["UV_INDEX_CMRU_USERNAME"] = username
        environment["UV_INDEX_CMRU_PASSWORD"] = password
    requirements = "\n".join(
        f"{name}=={version}" for name, (version, _target_id, _result, _source) in sorted(selected.items())
    ) + "\n"
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            input=requirements,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=900,
        )
    except FileNotFoundError as exc:
        raise VersionsPrerequisiteError("Python targets require uv on PATH (install uv before resolving)") from exc
    except subprocess.TimeoutExpired as exc:
        raise VersionsOperationError("uv pip compile did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"uv pip compile failed (exit {completed.returncode}): {detail[-2000:]}")
    if not completed.stdout.strip():
        raise VersionsError("uv pip compile completed without producing constraints output")
    content = (
        "# Generated by cmru versions resolve; edit [versions.targets] instead.\n"
        f"# resolved-at: {_timestamp(max(item[2].resolved_at for item in selected.values()))}\n"
        f"# age-cutoff: {_timestamp(global_cutoff)}\n"
        + completed.stdout.rstrip()
        + "\n"
    )
    return [(path, content) for path in output_paths]


def _age_window(versions: Mapping[str, object]) -> int:
    value = versions.get("age_window_days", DEFAULT_AGE_WINDOW_DAYS)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VersionsError("effective versions.age_window_days must be a positive integer")
    return value


def _apply_resolved_state(path: Path, results: Mapping[str, TargetResult]) -> None:
    states = {target_id: result.state() for target_id, result in results.items()}

    def mutate(versions: dict) -> dict:
        targets = versions.setdefault("targets", {})
        if not isinstance(targets, dict):
            raise VersionsError(f"{path} [versions].targets is not a table")
        for target_id, state in states.items():
            target = targets.get(target_id)
            if not isinstance(target, dict):
                raise VersionsError(
                    f"resolved target {target_id!r} is not declared in {path}; "
                    "refusing to create a generated-only target"
                )
            target["resolved"] = state
        return versions

    _update_versions_file(path, mutate)


def _render_report(records: list[dict]) -> str:
    if not records:
        return "No version targets are configured. Run cmru versions init after declaring project dependencies."
    headers = ("OWNER", "TARGET", "RECORDED", "ELIGIBLE", "STATUS", "AGE SOURCE")
    rows = []
    for record in records:
        age_sources = ",".join(sorted({source["age_source"] for source in record["sources"].values()}))
        rows.append((
            str(record["owner"]), str(record["target"]), str(record["recorded_version"] or "—"),
            str(record["eligible_version"]), str(record["status"]), age_sources,
        ))
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows)
    return "\n".join(lines)


def _resolve_context(config_arg: str | None) -> tuple[InvocationContext, ForgeConfig]:
    config_path = Path(config_arg).expanduser() if config_arg else None
    context = resolve_invocation_context(config_path)
    forge = load_forge_config(context.config_path)
    return context, forge


















def _versions_init(forge: ForgeConfig, projects: list[str], *, dry_run: bool) -> list[str]:
    summaries: list[str] = []
    pending: dict[Path, tuple[str, str]] = {}
    for name in projects:
        project_root = _project_root(forge, name)
        config_path = (
            forge.orchestration.project_configs[name]
            if forge.orchestration is not None
            else project_root / PROJECT_CONFIG_FILENAME
        )
        document = _load_document(config_path)
        versions = document.get("versions", {})
        if not isinstance(versions, dict):
            raise VersionsError(f"{config_path} [versions] must be a table")
        targets = versions.setdefault("targets", {})
        if not isinstance(targets, dict):
            raise VersionsError(f"{config_path} [versions].targets must be a table")
        added = []
        shared = []
        facts, skipped = _init_facts(project_root)
        for family, slug, constraint, source in facts:
            target_id = _target_id(family, slug)
            if target_id in targets:
                continue
            root_targets = forge.versions.get("targets", {})
            root_target = root_targets.get(target_id) if isinstance(root_targets, dict) else None
            if isinstance(root_target, dict):
                root_source = root_target.get(family)
                identity_key = "name" if family in {"npm", "pypi"} else "module"
                if not isinstance(root_source, dict) or not _same_source_identity(
                    family, root_source.get(identity_key), source.get(identity_key),
                ):
                    raise VersionsError(
                        f"root version target {target_id!r} does not match manifest source "
                        f"{family}:{source.get(identity_key)!r} in {project_root}"
                    )
                shared.append(target_id)
                continue
            targets[target_id] = {"mode": "single", "constraint": constraint, family: source}
            added.append(target_id)
        if not added:
            if shared:
                summaries.append(
                    f"{name}: shared root targets already cover {', '.join(shared)}; "
                    "project declarations are unchanged"
                )
            else:
                summaries.append(f"{name}: no new targets derived; declarations are unchanged")
        else:
            try:
                effective = (
                    deep_merge_versions(dict(forge.versions), versions)
                    if forge.orchestration else versions
                )
                parse_versions_section(effective, f"{config_path}: effective [versions]")
            except ValueError as exc:
                raise VersionsError(str(exc)) from exc
            if not dry_run:
                original = config_path.read_text(encoding="utf-8")
                rendered = _replace_versions_region(original, versions)
                pending[config_path] = (original, rendered)
            summaries.append(f"{name}: {'would add' if dry_run else 'added'} {', '.join(added)}")
        for item in skipped:
            summaries.append(f"{name}: skipped manifest dependency {item}")

    if pending and not dry_run:
        try:
            for path, (_original, rendered) in pending.items():
                _write_text_atomic(path, rendered)
            validation_path = (
                forge.repo_root / ORCHESTRATION_CONFIG_FILENAME
                if forge.orchestration is not None
                else next(iter(pending))
            )
            load_forge_config(validation_path)
        except BaseException as exc:
            for path, (original, _rendered) in pending.items():
                _write_text_atomic(path, original)
            if isinstance(exc, SystemExit):
                raise VersionsError("generated version declarations failed CMRU config validation") from exc
            raise
    return summaries


def _init_facts(
    project_root: Path,
) -> tuple[list[tuple[str, str, str, dict[str, str]]], list[str]]:
    """Derive only explicit dependency facts from standard project manifests."""
    findings: list[tuple[str, str, str, dict[str, str]]] = []
    skipped: list[str] = []
    pypi_registry = "https://pypi.org"
    npm_registry = "https://registry.npmjs.org"
    go_proxy = "https://proxy.golang.org"

    requirements = project_root / "requirements.in"
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if not clean:
                continue
            if clean.startswith(("-", "http:", "https:", ".", "/")):
                skipped.append(
                    f"requirements.in line {line.strip()!r}: includes/options/URL/path dependencies "
                    "need a named registry target"
                )
                continue
            match = _REQUIREMENT.fullmatch(clean)
            if not match:
                skipped.append(f"requirements.in line {line.strip()!r}: unsupported requirement syntax")
                continue
            name, constraint = match.groups()
            name = re.sub(r"[-_.]+", "-", name).lower()
            constraint = re.sub(r"\s*,\s*", ",", constraint.strip()) or "*"
            try:
                validate_constraint(constraint)
            except RegistryError as exc:
                skipped.append(f"requirements.in {name}: {exc}")
                continue
            findings.append((
                "pypi", name, constraint,
                {"name": name, "registry": pypi_registry},
            ))

    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as stream:
                metadata = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise VersionsError(f"could not read {pyproject}: {exc}") from exc
        project_metadata = metadata.get("project", {})
        if not isinstance(project_metadata, dict):
            raise VersionsError(f"{pyproject} [project] must be a table")
        if "dependencies" not in project_metadata:
            dependencies = []
        elif isinstance(project_metadata["dependencies"], list):
            dependencies = project_metadata["dependencies"]
        else:
            skipped.append("pyproject.toml project.dependencies: expected an array of requirement strings")
            dependencies = []
        for value in dependencies:
            if not isinstance(value, str):
                skipped.append(f"pyproject.toml dependency {value!r}: dependency must be a string")
                continue
            match = _REQUIREMENT.fullmatch(value)
            if match:
                name, constraint = match.groups()
                name = re.sub(r"[-_.]+", "-", name).lower()
                constraint = re.sub(r"\s*,\s*", ",", constraint.strip()) or "*"
                try:
                    validate_constraint(constraint)
                except RegistryError as exc:
                    skipped.append(f"pyproject.toml {name}: {exc}")
                    continue
                findings.append((
                    "pypi", name, constraint,
                    {"name": name, "registry": pypi_registry},
                ))
            elif value.strip():
                skipped.append(f"pyproject.toml dependency {value!r}: unsupported requirement syntax")

    package_json = project_root / "package.json"
    if package_json.is_file():
        try:
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not read {package_json}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise VersionsError(f"{package_json} must contain a JSON object")
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            if field not in metadata:
                continue
            values = metadata[field]
            if not isinstance(values, dict):
                skipped.append(f"package.json {field}: expected a dependency object")
                continue
            for name, constraint in values.items():
                if not isinstance(constraint, str):
                    skipped.append(f"package.json {name}: dependency constraint must be a string")
                    continue
                if constraint.startswith(("file:", "git+", "http:", "https:", "workspace:")):
                    skipped.append(f"package.json {name}@{constraint}: non-registry dependency")
                    continue
                try:
                    validate_constraint(constraint or "*")
                except RegistryError as exc:
                    skipped.append(f"package.json {name}@{constraint}: {exc}")
                    continue
                slug = name.lower().replace("/", ".").replace("@", "")
                findings.append(("npm", slug, constraint or "*", {"name": name, "registry": npm_registry}))

    go_mod = project_root / "go.mod"
    if go_mod.is_file():
        in_require = False
        for line in go_mod.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require and stripped == ")":
                in_require = False
                continue
            if stripped.startswith("require "):
                parts = stripped.removeprefix("require ").split()
            elif in_require:
                parts = stripped.split()
            else:
                continue
            if not parts or parts[0].startswith("//"):
                continue
            if len(parts) < 2:
                skipped.append(f"go.mod require entry {stripped!r}: expected a module and version")
                continue
            if len(parts) >= 2 and not parts[0].startswith("//") and parts[1].startswith("v"):
                module, version = parts[:2]
                try:
                    validate_constraint(f">={version}")
                except RegistryError as exc:
                    skipped.append(f"go.mod {module}@{version}: {exc}")
                    continue
                slug = re.sub(r"[^A-Za-z0-9_.-]+", ".", module).strip(".")
                findings.append(("go", slug, f">={version}", {"module": module, "proxy": go_proxy}))
            else:
                skipped.append(f"go.mod require entry {stripped!r}: unsupported module version")

    deduplicated: dict[tuple[str, str], tuple[str, str, str, dict[str, str]]] = {}
    for item in findings:
        family, slug, constraint, source = item
        identity = (family, slug)
        previous = deduplicated.get(identity)
        if previous is None:
            deduplicated[identity] = item
        elif previous[3] != source:
            raise VersionsError(f"manifest facts for {family}:{slug} disagree on registry coordinates")
        elif constraint != "*":
            merged = constraint if previous[2] == "*" else f"{previous[2]},{constraint}"
            try:
                validate_constraint(merged)
            except RegistryError as exc:
                raise VersionsError(
                    f"cannot combine constraints for manifest target {family}:{slug}: "
                    f"{previous[2]!r} and {constraint!r}"
                ) from exc
            deduplicated[identity] = (family, slug, merged, source)
    return list(deduplicated.values()), skipped


def _target_id(family: str, slug: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", ".", slug).strip("._-")
    if not cleaned:
        raise VersionsError(f"cannot derive a target identifier for {family}:{slug}")
    return f"{family}.{cleaned}"


def _same_source_identity(family: str, left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if family == "pypi":
        normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
        return normalize(left) == normalize(right)
    if family == "npm":
        return left.casefold() == right.casefold()
    return left == right


def _recorded_target(forge: ForgeConfig, project_name: str, target_id: str, source_type: str) -> str | None:
    local = forge.projects[project_name].versions.get("targets", {})
    target = local.get(target_id, {}) if isinstance(local, dict) else {}
    resolved = target.get("resolved", {}) if isinstance(target, dict) else {}
    sources = resolved.get("sources", {}) if isinstance(resolved, dict) else {}
    source = sources.get(source_type, {}) if isinstance(sources, dict) else {}
    if isinstance(source, dict) and isinstance(source.get("version"), str):
        return source["version"]
    root = forge.versions.get("targets", {})
    root_target = root.get(target_id, {}) if isinstance(root, dict) else {}
    root_resolved = root_target.get("resolved", {}) if isinstance(root_target, dict) else {}
    root_sources = root_resolved.get("sources", {}) if isinstance(root_resolved, dict) else {}
    root_source = root_sources.get(source_type, {}) if isinstance(root_sources, dict) else {}
    return root_source.get("version") if isinstance(root_source, dict) and isinstance(root_source.get("version"), str) else None
































@contextmanager
def _temporary_npm_config(
    registry: str,
    token_env: str | None,
    username_env: str | None,
    password_env: str | None,
):
    if not token_env and not username_env:
        yield None
        return
    parsed = urlsplit(registry)
    if not parsed.hostname:
        raise VersionsError(f"invalid npm registry URL {registry!r}")
    registry_key = f"//{parsed.netloc}{parsed.path.rstrip('/')}/"
    if token_env:
        if not os.environ.get(token_env):
            raise VersionsPrerequisiteError(f"required registry token environment variable {token_env} is unset or empty")
        npm_variable = "$" + "{" + token_env + "}"
        config = f"{registry_key}:_authToken={npm_variable}\n"
    else:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} and {password_env} "
                "must be set"
            )
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        config = f"{registry_key}:_auth={encoded}\n"
    fd, temporary = tempfile.mkstemp(prefix="cmru-npmrc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(config)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        yield Path(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _run_npm(
    project_root: Path,
    package_versions: Mapping[str, tuple[str, str]],
    results: Mapping[str, TargetResult],
    previous: Mapping[str, str | None],
    package_sources: Mapping[str, Mapping[str, str]],
) -> None:
    if not package_versions:
        return
    package_json = project_root / "package.json"
    if not package_json.is_file():
        raise VersionsError(f"npm version targets require {package_json}")
    try:
        document = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionsError(f"could not parse {package_json}: {exc}") from exc
    if not isinstance(document, dict):
        raise VersionsError(f"{package_json} must contain a JSON object")
    lock_path = project_root / (
        "npm-shrinkwrap.json" if (project_root / "npm-shrinkwrap.json").exists() else "package-lock.json"
    )
    lock_doc: dict[str, Any] = {}
    if lock_path.exists():
        try:
            loaded = json.loads(lock_path.read_text(encoding="utf-8"))
            lock_doc = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not parse {lock_path}: {exc}") from exc
    all_declared = set()
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = document.get(field, {})
        if isinstance(values, dict):
            all_declared.update(values)
    lock_packages = lock_doc.get("packages", {})
    lock_names = set()
    if isinstance(lock_packages, dict):
        for package_path, item in lock_packages.items():
            if not package_path or not isinstance(item, dict):
                continue
            package_name = item.get("name")
            if isinstance(package_name, str):
                lock_names.add(package_name)
            elif package_path.startswith("node_modules/"):
                lock_names.add(package_path.rsplit("node_modules/", 1)[-1])
    for name in package_versions:
        if name not in all_declared and name not in lock_names:
            raise VersionsError(
                f"npm target {name!r} is absent from package.json dependencies and package-lock.json; "
                "add the dependency before resolving its override"
            )
    registries = {source["registry"].rstrip("/") for source in package_sources.values()}
    if len(registries) != 1:
        raise VersionsError(
            "all npm targets in one project must use the same registry for native lockfile generation"
        )
    registry = next(iter(registries))
    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for source in package_sources.values()
    }
    if len(auth_refs) != 1:
        raise VersionsError(
            "npm targets using one registry must share one credential environment-variable mapping"
        )
    overrides = document.setdefault("overrides", {})
    if not isinstance(overrides, dict):
        raise VersionsError(f"{package_json}.overrides must be an object")
    for name, (version, target_id) in package_versions.items():
        existing = overrides.get(name)
        old_version = previous.get(target_id)
        if existing is not None and str(existing) not in {version, old_version}:
            raise VersionsError(
                f"{package_json} has an existing npm override for {name!r} that is not owned "
                "by this CMRU target; reconcile it before resolving"
            )
        overrides[name] = version
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            values = document.get(field, {})
            if not isinstance(values, dict):
                continue
            if name in values:
                values[name] = version
    package_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    npm_results = [results[target_id] for _name, (_version, target_id) in package_versions.items()]
    cutoff = min(result.age_cutoff for result in npm_results)
    command = ["npm", "install", "--package-lock-only", "--ignore-scripts", "--before", _timestamp(cutoff)]
    command.extend(("--registry", registry))
    scopes: set[str] = set()
    for name in package_versions:
        if not name.startswith("@"):
            continue
        if "/" not in name:
            continue
        scopes.add(name.split("/", 1)[0])
    command.extend(f"--{scope}:registry={registry}" for scope in sorted(scopes))
    age_exclusions = []
    for name, (_version, target_id) in package_versions.items():
        result = results[target_id]
        candidate = result.sources.get("npm")
        if candidate and (
            candidate.released_at > result.age_cutoff or result.age_cutoff > cutoff
        ):
            age_exclusions.append(name)
    if age_exclusions:
        try:
            version_probe = subprocess.run(
                ["npm", "--version"], cwd=project_root, text=True, capture_output=True,
                check=False, timeout=20,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("npm targets require npm on PATH") from exc
        version_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_probe.stdout.strip())
        if version_probe.returncode or not version_match or tuple(map(int, version_match.groups())) < (11, 5, 0):
            raise VersionsPrerequisiteError(
                "this npm target needs the package-specific age exception flag; "
                "use npm 11.5.0 or newer"
            )
        for name in age_exclusions:
            command.extend(("--min-release-age-exclude", name))
    command.extend(("--audit=false", "--fund=false"))
    token_env, username_env, password_env = next(iter(auth_refs))
    with _temporary_npm_config(registry, token_env, username_env, password_env) as npm_config:
        if npm_config is not None:
            command.extend(("--userconfig", str(npm_config)))
        try:
            completed = subprocess.run(
                command, cwd=project_root, text=True, capture_output=True, check=False, timeout=900,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("npm targets require npm on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise VersionsOperationError("npm package-lock refresh did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"npm lockfile refresh failed (exit {completed.returncode}): {detail[-1600:]}")


@contextmanager
def _temporary_netrc(
    proxy: str,
    token_env: str | None,
    username_env: str | None,
    password_env: str | None,
):
    if not token_env and not username_env:
        yield None
        return
    host = urlsplit(proxy).netloc
    if not host:
        raise VersionsError(f"invalid Go proxy URL {proxy!r}")
    if token_env:
        username = "__token__"
        password = os.environ.get(token_env, "")
        if not password:
            raise VersionsPrerequisiteError(f"required registry token environment variable {token_env} is unset or empty")
    else:
        username = os.environ.get(str(username_env), "")
        password = os.environ.get(str(password_env), "")
        if not username or not password:
            raise VersionsPrerequisiteError(
                f"required registry credential environment variables {username_env} and {password_env} "
                "must be set"
            )
    if any(char.isspace() or char in {'"', "\\", "#"} for char in username + password):
        raise VersionsError("Go proxy credentials cannot contain whitespace, quotes, backslashes, or #")
    fd, temporary = tempfile.mkstemp(prefix="cmru-netrc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"machine {host} login {username} password {password}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        yield Path(temporary)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _go_workspace_files(project_root: Path, environment: Mapping[str, str]) -> list[Path]:
    """Ask Go which workspace it will use and return every mutable workspace file."""
    try:
        completed = subprocess.run(
            ["go", "env", "GOWORK"], cwd=project_root, text=True,
            capture_output=True, check=False, timeout=20, env=dict(environment),
        )
    except FileNotFoundError as exc:
        raise VersionsPrerequisiteError("Go targets require go on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise VersionsOperationError("Go workspace detection did not finish within 20 seconds") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(
            f"could not determine the Go workspace (exit {completed.returncode}): {detail[-1200:]}"
        )
    value = completed.stdout.strip()
    if not value or value == "off":
        return []
    workspace = Path(value)
    if not workspace.is_absolute():
        workspace = project_root / workspace
    return [workspace, Path(str(workspace) + ".sum")]


def _run_go(
    project_root: Path,
    module_versions: Mapping[str, tuple[str, str]],
    module_sources: Mapping[str, Mapping[str, str]],
    transaction: _FileTransaction | None = None,
) -> None:
    if not module_versions:
        return
    go_mod = project_root / "go.mod"
    if not go_mod.is_file():
        raise VersionsError(f"Go version targets require {go_mod}")
    proxies = {source["proxy"].rstrip("/") for source in module_sources.values()}
    if len(proxies) != 1:
        raise VersionsError("all Go targets in one project must use the same proxy for native go.mod updates")
    proxy = next(iter(proxies))
    auth_refs = {
        (source.get("token_env"), source.get("username_env"), source.get("password_env"))
        for source in module_sources.values()
    }
    if len(auth_refs) != 1:
        raise VersionsError(
            "Go targets using one proxy must share one credential environment-variable mapping"
        )
    command = [
        "go", "get",
        *[f"{module}@{version}" for module, (version, _target) in sorted(module_versions.items())],
    ]
    environment = os.environ.copy()
    environment["GOPROXY"] = proxy
    environment["GONOPROXY"] = "none"
    token_env, username_env, password_env = next(iter(auth_refs))
    with _temporary_netrc(proxy, token_env, username_env, password_env) as netrc:
        if netrc is not None:
            environment["NETRC"] = str(netrc)
        if transaction is not None:
            for workspace_path in _go_workspace_files(project_root, environment):
                transaction.track(workspace_path)
        try:
            completed = subprocess.run(
                command, cwd=project_root, text=True, capture_output=True, check=False,
                timeout=900, env=environment,
            )
        except FileNotFoundError as exc:
            raise VersionsPrerequisiteError("Go targets require go on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise VersionsOperationError("Go module update did not finish within 15 minutes") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VersionsOperationError(f"Go module update failed (exit {completed.returncode}): {detail[-1600:]}")




def _project_has_python_manifest(project_root: Path) -> bool:
    return (
        any(project_root.glob("requirements*.in"))
        or any(project_root.glob("requirements*.txt"))
        or (project_root / "pyproject.toml").is_file()
    )


def _npm_declared_names(project_root: Path) -> set[str]:
    package_json = project_root / "package.json"
    if not package_json.is_file():
        return set()
    try:
        document = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionsError(f"could not parse {package_json}: {exc}") from exc
    if not isinstance(document, dict):
        raise VersionsError(f"{package_json} must contain a JSON object")
    names: set[str] = set()
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = document.get(field, {})
        if isinstance(values, dict):
            names.update(key for key in values if isinstance(key, str))
    for lock_name in ("npm-shrinkwrap.json", "package-lock.json"):
        lock_path = project_root / lock_name
        if not lock_path.is_file():
            continue
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionsError(f"could not parse {lock_path}: {exc}") from exc
        if not isinstance(lock, dict):
            raise VersionsError(f"{lock_path} must contain a JSON object")
        packages = lock.get("packages", {})
        if isinstance(packages, dict):
            for package_path, item in packages.items():
                if not package_path or not isinstance(item, dict):
                    continue
                package_name = item.get("name")
                if isinstance(package_name, str):
                    names.add(package_name)
                elif package_path.startswith("node_modules/"):
                    names.add(package_path.rsplit("node_modules/", 1)[-1])
    return names


def _go_required_modules(project_root: Path) -> set[str]:
    go_mod = project_root / "go.mod"
    if not go_mod.is_file():
        return set()
    modules: set[str] = set()
    in_require = False
    for line in go_mod.read_text(encoding="utf-8").splitlines():
        stripped = line.split("//", 1)[0].strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        parts = stripped.removeprefix("require ").split() if stripped.startswith("require ") else (
            stripped.split() if in_require else []
        )
        if len(parts) >= 2 and parts[1].startswith("v"):
            modules.add(parts[0])
    return modules


def _native_results_for_project(
    forge: ForgeConfig,
    project_name: str,
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, dict[str, TargetResult]], dict[str, TargetResult]]:
    """Select source-native writers relevant to this project.

    Root records are shared and resolved once. They update a language's native
    files only where the project has a matching manifest; project-local targets
    are explicit requests and reach their configured writer directly.
    """
    project = forge.projects[project_name]
    local_ids = set(_local_target_overlays(forge, project_name))
    python_present = _project_has_python_manifest(project_root)
    npm_names = _npm_declared_names(project_root)
    go_modules = _go_required_modules(project_root)
    native: dict[str, dict[str, TargetResult]] = {}
    oci: dict[str, TargetResult] = {}
    for target_id, result in results.items():
        declaration = declarations.get(target_id, {})
        is_local = target_id in local_ids
        for source_type, source in _target_sources(declaration).items():
            used = (
                is_local
                or source_type == "pypi" and python_present
                or source_type == "npm" and (project_root / "package.json").is_file()
                and str(source["name"]) in npm_names
                or source_type == "go" and str(source["module"]) in go_modules
            )
            if source_type == "oci":
                if is_local or "oci-image" in project.artifacts or bool(_merged_outputs(forge, project_name)):
                    oci[target_id] = result
            elif used:
                native.setdefault(source_type, {})[target_id] = result
    return native, oci


def _resolve_all_for_command(
    forge: ForgeConfig,
    projects: list[str],
    *,
    resolved_at: datetime,
) -> tuple[dict[str, TargetResult], dict[str, dict[str, TargetResult]], dict[str, dict[str, dict[str, object]]]]:
    root_targets = forge.versions.get("targets", {})
    if not isinstance(root_targets, dict):
        root_targets = {}
    root_results = _resolve_targets(
        root_targets,
        age_window_days=_age_window(forge.versions),
        resolved_at=resolved_at,
        owner="root",
    )
    project_results: dict[str, dict[str, TargetResult]] = {}
    declarations: dict[str, dict[str, dict[str, object]]] = {}
    for name in projects:
        declarations[name] = _target_declarations_for_project(forge, name)
        local_targets = _local_target_overlays(forge, name)
        versions = _versions_for_project(forge, name)
        project_results[name] = _resolve_targets(
            local_targets,
            age_window_days=_age_window(versions),
            resolved_at=resolved_at,
            owner=name,
        )
        _validate_manifest_target_compatibility(
            _project_root(forge, name),
            {**root_results, **project_results[name]},
            declarations[name],
        )
    return root_results, project_results, declarations


def _validate_manifest_target_compatibility(
    project_root: Path,
    results: Mapping[str, TargetResult],
    declarations: Mapping[str, Mapping[str, object]],
) -> None:
    """Refuse a registry target that contradicts this project's declared range."""
    facts, _skipped = _init_facts(project_root)
    for family, _slug, constraint, manifest_source in facts:
        for target_id, result in results.items():
            declaration = declarations.get(target_id, {})
            source = declaration.get(family)
            candidate = result.sources.get(family)
            if not isinstance(source, dict) or candidate is None:
                continue
            identity_key = "name" if family in {"npm", "pypi"} else "module" if family == "go" else "image"
            if identity_key not in source or identity_key not in manifest_source:
                continue
            source_identity = str(source[identity_key])
            manifest_identity = str(manifest_source[identity_key])
            matches = _same_source_identity(family, source_identity, manifest_identity)
            if matches and not version_satisfies(candidate.version, constraint):
                raise VersionsError(
                    f"target {target_id!r} resolves {source_identity}@{candidate.version}, but "
                    f"{project_root} manifest requires {constraint!r}"
                )


def _run_resolve(
    forge: ForgeConfig,
    context: InvocationContext,
    projects: list[str],
    *,
    dry_run: bool,
) -> list[str]:
    resolved_at = datetime.now(timezone.utc).replace(microsecond=0)
    root_results, project_results, declarations = _resolve_all_for_command(
        forge, projects, resolved_at=resolved_at,
    )
    _emit_age_evidence_warnings(root_results, project_results)
    if dry_run:
        records = _recorded_versions(forge, root_results, project_results, declarations)
        return ["Dry run: no files written.", _render_report(records)]

    root_path = context.config_path if context.config_kind == "orchestration" else None
    project_paths = {
        name: (
            forge.orchestration.project_configs[name]
            if forge.orchestration is not None
            else context.config_path
        )
        for name in projects
    }
    results_by_project = {
        name: {**root_results, **project_results[name]}
        for name in projects
    }
    outputs_by_project = {name: _merged_outputs(forge, name) for name in projects}
    native_results_by_project: dict[str, dict[str, dict[str, TargetResult]]] = {}
    oci_results_by_project: dict[str, dict[str, TargetResult]] = {}
    prepared_native: dict[str, list[tuple[Path, str]]] = {}
    for name in projects:
        project_root = _project_root(forge, name)
        native_results_by_project[name], oci_results_by_project[name] = _native_results_for_project(
            forge, name, project_root, results_by_project[name], declarations[name],
        )
        prepared_native[name] = _native_output_files(
            project_root,
            results_by_project[name],
            declarations[name],
            now=resolved_at,
            outputs=outputs_by_project[name],
            oci_results=oci_results_by_project[name],
        )
        prepared_native[name].extend(
            _compile_python_constraints(
                project_root,
                native_results_by_project[name].get("pypi", {}),
                declarations[name],
            )
        )
        _assert_unique_output_paths(prepared_native[name])

    transaction = _FileTransaction()
    if root_path is not None:
        if root_results:
            transaction.track(root_path)
    for name, path in project_paths.items():
        if project_results[name]:
            transaction.track(path)
        project_root = _project_root(forge, name)
        for output_path, _content in prepared_native[name]:
            transaction.track(output_path)
        if native_results_by_project[name].get("npm"):
            transaction.track(project_root / "package.json")
            lock_path = project_root / (
                "npm-shrinkwrap.json" if (project_root / "npm-shrinkwrap.json").exists() else "package-lock.json"
            )
            transaction.track(lock_path)
        if native_results_by_project[name].get("go"):
            transaction.track(project_root / "go.mod")
            transaction.track(project_root / "go.sum")

    try:
        if root_path is not None:
            if root_results:
                _apply_resolved_state(root_path, root_results)
        for name, path in project_paths.items():
            if project_results[name]:
                _apply_resolved_state(path, project_results[name])
        for name in projects:
            project_root = _project_root(forge, name)
            results = results_by_project[name]
            package_versions: dict[str, tuple[str, str]] = {}
            module_versions: dict[str, tuple[str, str]] = {}
            package_sources: dict[str, Mapping[str, str]] = {}
            module_sources: dict[str, Mapping[str, str]] = {}
            previous: dict[str, str | None] = {}
            for target_id, result in native_results_by_project[name].get("npm", {}).items():
                declaration = declarations[name].get(target_id, {})
                source = declaration.get("npm", {})
                candidate = result.sources.get("npm")
                if not isinstance(source, dict):
                    raise VersionsError(f"npm source declaration missing for target {target_id!r}")
                if candidate is None:
                    raise VersionsError(f"npm candidate missing for target {target_id!r}")
                package_name = str(source["name"])
                previous_package = package_versions.get(package_name)
                if previous_package and previous_package[0] != candidate.version:
                    raise VersionsError(f"npm package {package_name!r} has conflicting target versions")
                if previous_package and previous_package[1] != target_id:
                    raise VersionsError(f"multiple npm targets resolve the same package {package_name!r}")
                package_versions[package_name] = (candidate.version, target_id)
                package_sources[package_name] = source
                previous[target_id] = _recorded_target(forge, name, target_id, "npm")
            for target_id, result in native_results_by_project[name].get("go", {}).items():
                declaration = declarations[name].get(target_id, {})
                source = declaration.get("go", {})
                candidate = result.sources.get("go")
                if not isinstance(source, dict):
                    raise VersionsError(f"Go source declaration missing for target {target_id!r}")
                if candidate is None:
                    raise VersionsError(f"Go candidate missing for target {target_id!r}")
                module_name = str(source["module"])
                previous_module = module_versions.get(module_name)
                if previous_module and normalized_version(previous_module[0]) != normalized_version(candidate.version):
                    raise VersionsError(f"Go module {module_name!r} has conflicting target versions")
                if previous_module and previous_module[1] != target_id:
                    raise VersionsError(f"multiple Go targets resolve the same module {module_name!r}")
                module_versions[module_name] = (candidate.version, target_id)
                module_sources[module_name] = source
            if package_versions:
                _run_npm(project_root, package_versions, results, previous, package_sources)
            if module_versions:
                _run_go(project_root, module_versions, module_sources, transaction)
            for path, content in prepared_native[name]:
                _write_text_atomic(path, content)
    except BaseException:
        transaction.rollback()
        raise

    records = _recorded_versions(forge, root_results, project_results, declarations)
    for record in records:
        record["recorded_version"] = record["eligible_version"]
        record["status"] = "override" if record["override"] else "matches"
    return [f"Resolved version targets at {_timestamp(resolved_at)}.", _render_report(records)]


def main(argv: list[str] | None = None) -> int:
    headline = "Resolve declared package versions under a supply-chain age window."
    parser = CMRUArgumentParser(prog="cmru versions", description=headline)
    subparsers = parser.add_subparsers(dest="action", required=True, parser_class=CMRUArgumentParser)
    for action in ("init", "resolve", "check"):
        command = subparsers.add_parser(action, prog=f"cmru versions {action}")
        command.add_argument("target", nargs="?", help="all or one/more comma-separated project ids")
        command.add_argument(
            "--config",
            help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}",
        )
        if action in {"init", "resolve"}:
            command.add_argument("--dry-run", action="store_true", help="show results without writing files")
        if action == "check":
            command.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    try:
        context_path = Path(args.config).expanduser() if args.config else None
        context = resolve_invocation_context(context_path)
        forge = load_forge_config(context.config_path)
        projects = _selected_projects(forge, context, args.target)
        if args.action == "init":
            for line in _versions_init(forge, projects, dry_run=args.dry_run):
                print(line)
            return 0
        if args.action == "check":
            now = datetime.now(timezone.utc).replace(microsecond=0)
            root_results, project_results, declarations = _resolve_all_for_command(
                forge, projects, resolved_at=now,
            )
            _emit_age_evidence_warnings(root_results, project_results)
            records = _recorded_versions(forge, root_results, project_results, declarations)
            if args.json:
                print(json.dumps({"schema_version": 1, "targets": records}, indent=2, sort_keys=True))
            else:
                print(_render_report(records))
            return 0
        for line in _run_resolve(forge, context, projects, dry_run=args.dry_run):
            print(line)
        return 0
    except VersionsPrerequisiteError as exc:
        print(f"CMRU versions: {exc}", file=sys.stderr)
        return exit_codes.PREREQ_MISSING
    except VersionsOperationError as exc:
        print(f"CMRU versions: {exc}", file=sys.stderr)
        return exit_codes.FAILURE
    except RegistryError as exc:
        print(f"CMRU versions: {exc}", file=sys.stderr)
        return exit_codes.PREREQ_MISSING
    except VersionsError as exc:
        print(f"CMRU versions: {exc}", file=sys.stderr)
        return exit_codes.CONFIG_ERROR
    except OSError as exc:
        print(f"CMRU versions: file operation failed: {exc}", file=sys.stderr)
        return exit_codes.FAILURE
    except (SystemExit, ValueError) as exc:
        if isinstance(exc, SystemExit):
            return exc.code if isinstance(exc.code, int) else 2
        print(f"CMRU versions: {exc}", file=sys.stderr)
        return exit_codes.CONFIG_ERROR

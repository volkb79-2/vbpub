"""Deterministic, source-first release-history generation.

This module deliberately knows no project names or product semantics.  A project opts
in with ``[project.<name>.release] changelog = "CHANGES.md"`` and CMRU derives one
new section from the same project-scoped git range that selected the release.  The
result is prepared and gated before the release tag is minted, so a tag always carries
the history entry that describes it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
import subprocess
from typing import Any

from cmru.version import (
    _RELEASE_CONTROL_EXCLUDES,
    _external_version,
    _next_counter_version,
    bump_version,
    detect_changed_projects,
)


_HISTORY_MARKER = "<!-- cmru: release history -->"
_GENERATED_MARKER = "<!-- cmru: generated -->"
_HEADING_RE = re.compile(r"^## \[([^\]]+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE)
_CONVENTIONAL_TYPE_RE = re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:", re.IGNORECASE)


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def _project_release_plan(
    repo_root: Path,
    project: Any,
    *,
    minor: bool = False,
    major: bool = False,
    set_version: str | None = None,
) -> tuple[str, str | None]:
    """Return the pending ``(version, previous_tag)`` for one tag-owning project.

    This mirrors ``cmru.version.release_cmd`` rather than trusting an operator-supplied
    version.  The generator is intentionally unavailable to delegated and registry-only
    projects: those flows do not have a CMRU-owned semver tag to document.
    """
    name = project.name
    changed = {
        candidate_name: (last_tag, bump)
        for candidate_name, _candidate, last_tag, bump in detect_changed_projects(
            repo_root, {name: project}
        )
    }
    if name not in changed:
        raise RuntimeError(
            f"{name}: changelog generation requested but the project has no changes "
            "since its latest release tag"
        )
    last_tag, bump = changed[name]
    prefix = getattr(project, "prefix", None) or f"{name}-v"
    version_cfg = getattr(project, "version", None)
    strategy = getattr(version_cfg, "strategy", "scm") if version_cfg else "scm"
    if strategy == "delegated" or not getattr(project, "mint_tag", True):
        raise RuntimeError(
            f"{name}: release.changelog requires a CMRU-owned tag; "
            f"{strategy!r} / no-tag releases have no stable version to generate"
        )
    if strategy.startswith("external:"):
        variable = strategy.split(":", 1)[1].strip()
        if not variable:
            raise RuntimeError(f"{name}: external version strategy has no variable name")
        version = _external_version(repo_root / (getattr(project, "cwd", None) or name), variable)
    elif set_version:
        version = set_version
    elif major or minor:
        if last_tag:
            version = bump_version(last_tag[len(prefix):], "major" if major else "minor")
        else:
            version = "0.1.0"
    elif strategy == "counter":
        base = getattr(version_cfg, "base_version", "1.0.0") if version_cfg else "1.0.0"
        version = _next_counter_version(repo_root, prefix, base)
    elif last_tag:
        version = bump_version(last_tag[len(prefix):], bump)
    else:
        version = "0.1.0"
    return version, last_tag


def _subject_groups(repo_root: Path, previous_tag: str | None, paths: list[str]) -> dict[str, list[str]]:
    revision_range = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    output = _git(
        repo_root,
        "log", revision_range, "--format=%h%x1f%s", "--", *paths,
        *_RELEASE_CONTROL_EXCLUDES,
    )
    groups: dict[str, list[str]] = {
        "Added": [], "Fixed": [], "Changed": [], "Documentation": [], "Testing": [],
    }
    for line in output.splitlines():
        short_sha, separator, subject = line.partition("\x1f")
        if not separator or not short_sha or not subject:
            raise RuntimeError("cmru changelog: malformed git log record")
        match = _CONVENTIONAL_TYPE_RE.match(subject)
        kind = match.group(1).lower() if match else ""
        section = {
            "feat": "Added",
            "fix": "Fixed",
            "docs": "Documentation",
            "test": "Testing",
        }.get(kind, "Changed")
        groups[section].append(f"{subject} ({short_sha})")
    return {section: items for section, items in groups.items() if items}


def _validate_changelog_path(project: Any, repo_root: Path) -> Path:
    configured = getattr(project, "changelog", None)
    if not configured:
        raise RuntimeError(f"{project.name}: no release.changelog is configured")
    raw_path = Path(configured)
    if raw_path.is_absolute() or ".." in raw_path.parts or raw_path.name in ("", "."):
        raise RuntimeError(
            f"{project.name}: release.changelog must be a non-empty project-relative path, "
            f"got {configured!r}"
        )
    project_root = (repo_root / (getattr(project, "cwd", None) or project.name)).resolve()
    candidate = (project_root / raw_path).resolve()
    if candidate != project_root and project_root not in candidate.parents:
        raise RuntimeError(f"{project.name}: release.changelog escapes the project directory")
    return candidate


def _render_section(version: str, groups: dict[str, list[str]]) -> str:
    date = datetime.now(UTC).date().isoformat()
    lines = [f"## [{version}] - {date}", _GENERATED_MARKER, ""]
    for heading, entries in groups.items():
        lines.extend([f"### {heading}", *[f"- {entry}" for entry in entries], ""])
    if not groups:
        lines.extend(["### Changed", "- Release metadata prepared by CMRU.", ""])
    return "\n".join(lines)


def generate_release_changelog(
    repo_root: Path,
    project: Any,
    *,
    minor: bool = False,
    major: bool = False,
    set_version: str | None = None,
) -> bool:
    """Generate exactly one pending history section; return whether the file changed.

    Existing documents must include the explicit history marker.  This prevents a
    release transaction from guessing where to insert generated prose in a
    hand-authored document.  A retained ``--resume`` worktree sees its already
    generated target-version section and safely does nothing.
    """
    path = _validate_changelog_path(project, repo_root)
    version, previous_tag = _project_release_plan(
        repo_root, project, minor=minor, major=major, set_version=set_version,
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    existing_versions = set(_HEADING_RE.findall(existing))
    if version in existing_versions:
        expected = f"## [{version}]"
        start = existing.index(expected)
        next_heading = existing.find("\n## [", start + len(expected))
        section = existing[start:next_heading if next_heading >= 0 else len(existing)]
        if _GENERATED_MARKER not in section:
            raise RuntimeError(
                f"{project.name}: {path} already has a hand-authored [{version}] section; "
                "CMRU refuses to overwrite it"
            )
        return False

    groups = _subject_groups(
        repo_root, previous_tag,
        list(getattr(project, "paths", None) or [getattr(project, "cwd", None) or project.name]),
    )
    section = _render_section(version, groups)
    if not existing:
        new_content = (
            "# Changelog\n\n"
            "All notable changes to this project are recorded here. "
            "Entries marked `cmru: generated` are produced from the project-scoped "
            "release range before the release gate runs.\n\n"
            f"{_HISTORY_MARKER}\n\n{section}"
        )
    else:
        marker = f"{_HISTORY_MARKER}\n"
        marker_index = existing.find(marker)
        if marker_index < 0:
            raise RuntimeError(
                f"{project.name}: {path} lacks {_HISTORY_MARKER}; add the marker where "
                "CMRU may insert release sections"
            )
        insert_at = marker_index + len(marker)
        new_content = existing[:insert_at] + "\n" + section + existing[insert_at:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")
    return True

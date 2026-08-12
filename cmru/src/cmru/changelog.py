"""Deterministic, source-first release-history generation.

Every CMRU-managed project receives ``CHANGES.md`` by default.  A project may choose a
different project-relative path, or opt out explicitly, but it never has to add a
release script or hand-maintain release bullets.  CMRU derives one section from the
same project-scoped git range that selected the release and commits it before the
gate.  Tagged releases are headed by their pending version.  Image-only, no-tag
releases are headed by the source revision whose changes they describe.
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
_SOURCE_END_MARKER_RE = re.compile(r"<!-- cmru: source-end=([0-9a-f]{40}) -->")
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
) -> tuple[str | None, str | None]:
    """Return the pending ``(version, previous_tag)`` for a release.

    ``version`` is ``None`` for no-tag and delegated releases.  Their source-revision
    heading is selected later, after the previous generated source cursor is
    available.  Tagged flows mirror ``cmru.version.release_cmd`` instead of trusting
    an operator-supplied version.
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
        return None, None
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


def _subject_groups(
    repo_root: Path,
    previous_ref: str | None,
    paths: list[str],
    *,
    exclude_paths: list[str],
    end_ref: str = "HEAD",
) -> dict[str, list[str]]:
    revision_range = f"{previous_ref}..{end_ref}" if previous_ref else end_ref
    exclusions = [f":(exclude){path}" for path in exclude_paths]
    output = _git(
        repo_root,
        "log", revision_range, "--format=%h%x1f%s", "--", *paths,
        *_RELEASE_CONTROL_EXCLUDES, *exclusions,
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


def _last_generated_source_end(existing: str) -> str | None:
    """Return the newest CMRU source cursor from an existing history document."""
    matches = _SOURCE_END_MARKER_RE.findall(existing)
    return matches[0] if matches else None


def _generated_exclusions(project: Any, changelog_path: Path, repo_root: Path) -> list[str]:
    """Return project-relative mechanical paths that must not become source history."""
    project_root = (repo_root / (getattr(project, "cwd", None) or project.name)).resolve()
    relative_changelog = changelog_path.relative_to(project_root).as_posix()
    outputs = [relative_changelog, *getattr(project, "commit_generated", ())]
    cwd = getattr(project, "cwd", None) or project.name
    return [f"{cwd}/{path}" for path in outputs]


def _generated_outputs_changed(repo_root: Path, project: Any) -> bool:
    """Whether prepare changed a declared mechanical output in this transaction.

    A no-tag image may legitimately release updated, private-build provenance even
    when no hand-authored source commit was added since its prior source cursor.  The
    prepare output is not visible to ``git log`` until CMRU commits it, so this is the
    one case where an otherwise-empty no-tag history entry is meaningful.  A retained
    resume is clean and therefore does not append a duplicate.
    """
    outputs = list(getattr(project, "commit_generated", ()))
    if not outputs:
        return False
    cwd = getattr(project, "cwd", None) or project.name
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *[f"{cwd}/{path}" for path in outputs]],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"git status for generated outputs failed ({result.returncode}): {detail}")
    return bool(result.stdout.strip())


def _render_section(
    heading: str,
    groups: dict[str, list[str]],
    *,
    source_end: str,
    date: str | None = None,
    backfilled_tag: str | None = None,
) -> str:
    rendered_date = date or datetime.now(UTC).date().isoformat()
    lines = [
        f"## [{heading}] - {rendered_date}",
        _GENERATED_MARKER,
        f"<!-- cmru: source-end={source_end} -->",
    ]
    if backfilled_tag:
        lines.append(f"<!-- cmru: backfilled-after-release tag={backfilled_tag} -->")
    lines.append("")
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
    source_end = _git(repo_root, "rev-parse", "HEAD").strip()
    previous_ref = previous_tag if version is not None else _last_generated_source_end(existing)
    groups = _subject_groups(
        repo_root,
        previous_ref,
        list(getattr(project, "paths", None) or [getattr(project, "cwd", None) or project.name]),
        exclude_paths=_generated_exclusions(project, path, repo_root),
    )
    # A no-tag release uses the last generated source cursor rather than a tag as
    # its boundary.  On a retained-worktree resume, the only intervening commit is
    # the history commit itself, which is excluded above; generating another empty
    # section would make a resumed transaction non-idempotent.
    if version is None and not groups and not _generated_outputs_changed(repo_root, project):
        return False
    heading = version if version is not None else f"source-{source_end[:12]}"
    existing_versions = set(_HEADING_RE.findall(existing))
    if heading in existing_versions:
        expected = f"## [{heading}]"
        start = existing.index(expected)
        next_heading = existing.find("\n## [", start + len(expected))
        section = existing[start:next_heading if next_heading >= 0 else len(existing)]
        if _GENERATED_MARKER not in section:
            raise RuntimeError(
                f"{project.name}: {path} already has a hand-authored [{version}] section; "
                "CMRU refuses to overwrite it"
            )
        return False

    section = _render_section(heading, groups, source_end=source_end)
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


def _previous_project_tag(repo_root: Path, prefix: str, tag: str) -> str | None:
    """Return the nearest earlier tag in ``tag``'s ancestry, if one exists."""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", f"{prefix}*", f"{tag}^"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    previous = result.stdout.strip()
    return previous or None


def backfill_release_changelog(repo_root: Path, project: Any, tag: str) -> bool:
    """Catalog an already-published CMRU-tagged release without moving its tag.

    This one-time migration helper is intentionally separate from the normal release
    path: it writes a marked historical section in the current source tree, but cannot
    make an immutable tag retroactively contain that file.  Normal ``cmru release``
    remains source-first and never needs this helper.
    """
    path = _validate_changelog_path(project, repo_root)
    prefix = getattr(project, "prefix", None) or f"{project.name}-v"
    if not tag.startswith(prefix):
        raise RuntimeError(
            f"{project.name}: {tag!r} is not a release tag with prefix {prefix!r}"
        )
    source_end = _git(repo_root, "rev-parse", f"{tag}^{{commit}}").strip()
    version = tag[len(prefix):]
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    existing_versions = set(_HEADING_RE.findall(existing))
    if version in existing_versions:
        return False
    previous_tag = _previous_project_tag(repo_root, prefix, tag)
    groups = _subject_groups(
        repo_root,
        previous_tag,
        list(getattr(project, "paths", None) or [getattr(project, "cwd", None) or project.name]),
        exclude_paths=_generated_exclusions(project, path, repo_root),
        end_ref=tag,
    )
    date = _git(repo_root, "show", "-s", "--format=%cs", f"{tag}^{{commit}}").strip()
    section = _render_section(
        version,
        groups,
        source_end=source_end,
        date=date,
        backfilled_tag=tag,
    )
    if not existing:
        new_content = (
            "# Changelog\n\n"
            "All notable changes to this project are recorded here. "
            "Entries marked `cmru: generated` are produced from the project-scoped "
            "release range before the release gate runs. A marked `backfilled-after-release` "
            "entry was generated after its immutable tag already existed.\n\n"
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

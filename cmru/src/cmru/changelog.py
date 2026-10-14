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

from cmru.errors import RefusedBeforeChange, StepUnavailable, UsageRefusal
from cmru.release_notes import (
    CONSUMED_MARKER,
    HISTORY_MARKER,
    ReleaseNotes,
    ReleaseNotesError,
    extract_release_section,
    unreleased_body,
    unreleased_span,
)
from cmru.version import (
    _RELEASE_CONTROL_EXCLUDES,
    _external_version,
    _next_counter_version,
    bump_version,
    detect_changed_projects,
)


_HISTORY_MARKER = HISTORY_MARKER
_GENERATED_MARKER = "<!-- cmru: generated -->"
_SOURCE_END_MARKER_RE = re.compile(r"<!-- cmru: source-end=([0-9a-f]{40}) -->")
_HEADING_RE = re.compile(r"^## \[([^\]]+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE)
# KI-23: a hand-authored pre-release draft is conventionally headed
# `## [X.Y.Z] - UNRELEASED` (renamed to a dated heading and folded at release
# time). _HEADING_RE never matches it (UNRELEASED is not a date), so it was
# invisible to the collision guard below and got silently duplicated instead
# of blocking the release -- recurred on six consecutive ciu releases.
_UNRELEASED_HEADING_RE = re.compile(r"^## \[([^\]]+)\] - UNRELEASED$", re.MULTILINE)
_CONVENTIONAL_TYPE_RE = re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:", re.IGNORECASE)


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def consume_unreleased_notes(markdown: str, version: str) -> str | None:
    """Move the ``[Unreleased]`` notes into ``version``'s section; ``None`` if none.

    The author's text is appended verbatim to the end of the released version's
    section behind :data:`~cmru.release_notes.CONSUMED_MARKER`, and the
    ``[Unreleased]`` body is cleared down to any HTML comments it already held, so
    the file is back to the empty-and-ready state and nothing is published twice.
    Idempotent: with nothing under ``[Unreleased]`` the document is unchanged
    (``None``).
    """
    notes = unreleased_body(markdown)
    span = unreleased_span(markdown) if notes else None
    if span is None:
        return None
    comments = "".join(f"{item}\n\n" for item in _HTML_COMMENT_RE.findall(markdown[span[0]:span[1]]))
    cleared = markdown[:span[0]] + "\n\n" + comments + markdown[span[1]:]
    headings = list(re.finditer(r"^## .*$", cleared, re.MULTILINE))
    target = [
        item for item in headings
        if re.fullmatch(rf"## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}", item.group(0))
    ]
    if len(target) != 1:
        raise ReleaseNotesError(
            f"cannot move the [Unreleased] notes: the changelog has {len(target)} dated "
            f"`## [{version}]` sections (expected exactly one)"
        )
    later = [item.start() for item in headings if item.start() > target[0].start()]
    end = min(later) if later else len(cleared)
    section = cleared[target[0].start():end].rstrip("\n")
    return (
        cleared[:target[0].start()]
        + f"{section}\n\n{CONSUMED_MARKER}\n{notes}\n"
        + ("\n" + cleared[end:] if later else "")
    )


def consume_published_notes(repo_root: Path, project: Any, tag: str) -> bool:
    """Rewrite the changelog to move ``[Unreleased]`` into the published release (KI-78).

    The release transaction calls this only after the publish succeeded and
    before promotion, then commits the file, so a dry run, a failed
    gate/build/publish, or ``--no-build`` leaves the author's notes in place.
    Returns whether the file changed.
    """
    path = _validate_changelog_path(project, repo_root)
    prefix = getattr(project, "prefix", None) or f"{project.name}-v"
    updated = consume_unreleased_notes(path.read_text(encoding="utf-8"), tag[len(prefix):])
    if updated is None:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


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

    ``version`` is ``None`` for no-tag releases.  Their source-revision
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
        raise RefusedBeforeChange(
            f"{name}: changelog generation requested but the project has no changes "
            "since its latest release tag"
        )
    last_tag, bump = changed[name]
    prefix = getattr(project, "prefix", None) or f"{name}-v"
    version_cfg = getattr(project, "version", None)
    strategy = getattr(version_cfg, "strategy", "scm") if version_cfg else "scm"
    if not getattr(project, "git_tag", True):
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


def pending_release_tag(
    repo_root: Path,
    project: Any,
    *,
    minor: bool = False,
    major: bool = False,
    set_version: str | None = None,
) -> str | None:
    """The tag a release of *project* will create, or ``None`` for a no-tag project.

    Same plan the generated history heading uses, so a commit trailer naming this
    tag (REL-08) always agrees with the changelog section.
    """
    version, _previous = _project_release_plan(
        repo_root, project, minor=minor, major=major, set_version=set_version,
    )
    if version is None:
        return None
    return f"{getattr(project, 'prefix', None) or project.name + '-v'}{version}"


def pending_release_notes(
    repo_root: Path,
    project: Any,
    *,
    minor: bool = False,
    major: bool = False,
    set_version: str | None = None,
) -> ReleaseNotes:
    """The curated section a tagged release of *project* will publish (KI-75).

    Reads the changelog from the selected source tree for the SAME pending
    version the history heading and the tag use. Raises
    :class:`~cmru.release_notes.ReleaseNotesError` when the exact section is
    missing, malformed, duplicated or empty, so a caller can refuse before any
    tag, gate or upload. Only tagged projects with a changelog have a body.
    """
    path = _validate_changelog_path(project, repo_root)
    version, _previous = _project_release_plan(
        repo_root, project, minor=minor, major=major, set_version=set_version,
    )
    if version is None:
        raise UsageRefusal(
            f"{project.name}: a no-tag release has no GitHub Release body to preview"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseNotesError(f"cannot read release notes source {path}: {exc}") from exc
    return extract_release_section(text, version, source=str(path))


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
        raise StepUnavailable(f"{project.name}: no release.changelog is configured")
    raw_path = Path(configured)
    if raw_path.is_absolute() or ".." in raw_path.parts or raw_path.name in ("", "."):
        raise UsageRefusal(
            f"{project.name}: release.changelog must be a non-empty project-relative path, "
            f"got {configured!r}"
        )
    project_root = (repo_root / (getattr(project, "cwd", None) or project.name)).resolve()
    candidate = (project_root / raw_path).resolve()
    if candidate != project_root and project_root not in candidate.parents:
        raise UsageRefusal(f"{project.name}: release.changelog escapes the project directory")
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


def _project_commits_after_cursor(
    repo_root: Path, project: Any, changelog_path: Path, section: str,
    version: str | None = None,
) -> bool:
    """Whether project commits exist after a generated section's ``source-end``.

    A section without a readable cursor, or whose cursor is not a commit in this
    repository, is treated as current (kept byte-identical): regenerating cannot
    be justified without evidence.
    """
    cursor = _last_generated_source_end(section)
    if cursor is None:
        return False
    try:
        newer = _subject_groups(
            repo_root,
            cursor,
            list(getattr(project, "paths", None) or [getattr(project, "cwd", None) or project.name]),
            exclude_paths=_generated_exclusions(project, changelog_path, repo_root),
        )
    except RuntimeError:
        return False
    # cmru's own ``file:`` version-bump commit lands AFTER the section is generated
    # and is release machinery, not project work: a resumed release must not count it
    # (it would regenerate the section with a self-referential "chore: bump" entry
    # and re-tag a new commit). KI-59: skip ONLY the exact commit cmru writes -- the
    # subject for THIS prefix and the pending version, touching nothing but the
    # declared version file (and the generated changelog). A look-alike subject on a
    # real code change is project work.
    return any(
        not _is_own_bump_commit(repo_root, project, changelog_path, item, version)
        for items in newer.values()
        for item in items
    )


_ENTRY_HASH_RE = re.compile(r"\s\([0-9a-f]{7,40}\)$")
_MAX_DROPPED_LINES_SHOWN = 20


def _is_own_bump_commit(
    repo_root: Path, project: Any, changelog_path: Path, item: str, version: str | None,
) -> bool:
    """Whether a rendered ``subject (hash)`` entry is cmru's own ``file:`` bump commit."""
    version_cfg = getattr(project, "version", None)
    strategy = getattr(version_cfg, "strategy", "") if version_cfg else ""
    if version is None or not strategy.startswith("file:"):
        return False
    prefix = getattr(project, "prefix", None) or f"{project.name}-v"
    subject_hash = _ENTRY_HASH_RE.search(item)
    if subject_hash is None or item[:subject_hash.start()] != f"chore: bump {prefix} to {version}":
        return False
    short = subject_hash.group(0).strip()[1:-1]
    cwd = getattr(project, "cwd", None) or project.name
    allowed = {
        f"{cwd}/{strategy[len('file:'):]}",
        *_generated_exclusions(project, changelog_path, repo_root),
    }
    try:
        touched = _git(repo_root, "show", "--name-only", "--format=", short).split()
    except RuntimeError:
        return False
    return bool(touched) and set(touched) <= allowed


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


def _comparable_lines(text: str) -> list[str]:
    """Section lines normalised for a generated-vs-hand-written comparison.

    Blank lines, cmru's own HTML-comment markers and the abbreviated trailing
    commit hash (its length depends on the repository size at render time) do not
    count as content.
    """
    lines = []
    for raw in text.splitlines():
        line = _ENTRY_HASH_RE.sub("", raw.rstrip())
        if not line.strip() or line.lstrip().startswith("<!--"):
            continue
        lines.append(line)
    return lines


def _dropped_hand_edits(
    repo_root: Path,
    project: Any,
    changelog_path: Path,
    section: str,
    heading: str,
    previous_ref: str | None,
) -> list[str]:
    """Lines of an old generated section that the generator would not produce (KI-68).

    The generator's own output for the OLD range (``previous_ref`` .. the section's
    recorded ``source-end``) is rendered and compared line by line with the old
    body. What remains are hand edits (including curated release notes, KI-75)
    that regenerating the section would drop. A section without a readable
    cursor, or whose range cannot be listed, yields no evidence and therefore no
    refusal (the same stance as :func:`_project_commits_after_cursor`).
    """
    cursor = _last_generated_source_end(section)
    if cursor is None:
        return []
    try:
        old_groups = _subject_groups(
            repo_root,
            previous_ref,
            list(getattr(project, "paths", None) or [getattr(project, "cwd", None) or project.name]),
            exclude_paths=_generated_exclusions(project, changelog_path, repo_root),
            end_ref=cursor,
        )
    except RuntimeError:
        return []
    produced = set(_comparable_lines(_render_section(heading, old_groups, source_end=cursor)))
    # The `## [x] - date` heading line is the section's own anchor, never content.
    body = section.split("\n", 1)[1] if "\n" in section else ""
    return [line for line in _comparable_lines(body) if line not in produced]


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
    if heading in set(_UNRELEASED_HEADING_RE.findall(existing)):
        raise RefusedBeforeChange(
            f"{project.name}: {path} already has a hand-authored [{heading}] - UNRELEASED "
            "section (KI-23); CMRU refuses to create a second, un-merged heading for the "
            "same version -- fold it into the generated section by hand first, then rename "
            "its heading to match (or remove it)"
        )
    # KI-78: a non-empty `## [Unreleased]` is no longer refused (KI-30). Its text rides
    # the GitHub Release body and moves into the version's history after a
    # successful publish (`consume_published_notes`), for any configured changelog
    # path; change detection excludes that path, so the move cannot schedule a release.
    existing_versions = set(_HEADING_RE.findall(existing))
    if heading in existing_versions:
        expected = f"## [{heading}]"
        # Anchor to a real heading line: prose or a comment that merely MENTIONS
        # `## [X]` mid-line (e.g. inside the [Unreleased] notes) is not the section.
        start = re.search(
            rf"^{re.escape(expected)} - \d{{4}}-\d{{2}}-\d{{2}}$", existing, re.MULTILINE,
        ).start()
        next_heading = existing.find("\n## [", start + len(expected))
        section = existing[start:next_heading if next_heading >= 0 else len(existing)]
        if _GENERATED_MARKER not in section:
            raise RefusedBeforeChange(
                f"{project.name}: {path} already has a hand-authored [{version}] section; "
                "CMRU refuses to overwrite it"
            )
        # REL-02: a generated section for the pending version is reused ONLY
        # while nothing in the project changed after its recorded source cursor
        # (a retained --resume sees just the history commit, which is excluded
        # from the range). If project commits landed after the cursor, the section
        # is stale -- it would ship without them -- so regenerate it in place.
        if not _project_commits_after_cursor(
            repo_root, project, path, section, version,
        ):
            return False
        # KI-68: regenerating replaces the whole body, so hand-written lines the
        # generator would not produce for the OLD range (curated notes among
        # them) would vanish silently. Refuse instead; there is no override.
        dropped = _dropped_hand_edits(
            repo_root, project, path, section, heading, previous_ref,
        )
        if dropped:
            shown = "\n".join(f"  {line}" for line in dropped[:_MAX_DROPPED_LINES_SHOWN])
            more = len(dropped) - _MAX_DROPPED_LINES_SHOWN
            if more > 0:
                shown += f"\n  ... and {more} more"
            raise RefusedBeforeChange(
                f"{project.name}: {path} section [{heading}] was generated before later "
                "project commits landed, so CMRU must regenerate it, but it holds "
                f"{len(dropped)} line(s) the generator would not produce (hand edits) that "
                f"would be dropped:\n{shown}\n"
                "Remedy: advance `<!-- cmru: source-end=... -->` to the final project "
                "commit in the same commit as the hand edit (a section whose cursor is "
                "current is kept byte-identical)."
            )
        regenerated = _render_section(heading, groups, source_end=source_end)
        new_content = (
            existing[:start] + regenerated
            + (existing[next_heading:] if next_heading >= 0 else "")
        )
        path.write_text(new_content, encoding="utf-8")
        return True

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
            raise RefusedBeforeChange(
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


def backfill_release_changelog(
    repo_root: Path, project: Any, tag: str, *, dry_run: bool = False,
) -> bool:
    """Catalog an already-published CMRU-tagged release without moving its tag.

    This one-time migration helper is intentionally separate from the normal release
    path: it writes a marked historical section in the current source tree, but cannot
    make an immutable tag retroactively contain that file.  Normal ``cmru release``
    remains source-first and never needs this helper.
    """
    path = _validate_changelog_path(project, repo_root)
    prefix = getattr(project, "prefix", None) or f"{project.name}-v"
    if not tag.startswith(prefix):
        raise UsageRefusal(
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
            raise RefusedBeforeChange(
                f"{project.name}: {path} lacks {_HISTORY_MARKER}; add the marker where "
                "CMRU may insert release sections"
            )
        insert_at = marker_index + len(marker)
        new_content = existing[:insert_at] + "\n" + section + existing[insert_at:]
    if dry_run:
        import difflib

        print("".join(difflib.unified_diff(
            existing.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=str(path), tofile=f"{path} (planned)",
        )), end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content, encoding="utf-8")
    return True

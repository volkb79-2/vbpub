"""Auto-versioning: change detection, bump, and release trigger (S12).

CLI verbs:
  cmru status  — dry-run: show which projects changed and their next versions
  cmru release — detect → version → tag → (caller does build+publish)

Bump precedence (S12.4):
  1. --major / --minor / --set-version override
  2. Conventional Commits (feat→minor, fix/other→patch, !→major)
  3. patch (default)

Strategies (S12.5):
  scm     — tag HEAD directly; setuptools_scm reads it (no extra commit)
  file    — write file (e.g. VERSION), commit, then tag
  counter — increment the R-suffix: <base>-r<N> (pwmcp pattern)

Dev builds (untagged state, S12.6):
  X.Y.Z.devN+g<hash>  — returned by setuptools_scm when no tag matches;
  publish_versioned() in release.py skips the immutable release for devN builds.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


# Release-control files live inside a project's subtree but are not product source —
# editing them (e.g. repointing release_config during a migration) MUST NOT trigger a
# version bump for that product. Excluded from change detection (S12.2).
_RELEASE_CONTROL_EXCLUDES = (
    ":(exclude,glob)**/build-push.toml",
    ":(exclude,glob)**/cmru.build.toml",
    ":(exclude,glob)**/cmru.vars",
    ":(exclude,glob)**/.release-vars",
)


def _git_log(repo_root: Path, since_ref: str, *paths: str) -> List[str]:
    """Commit messages reachable from HEAD but not from since_ref, touching paths
    (release-control files excluded)."""
    cmd = ["git", "log", f"{since_ref}..HEAD", "--format=%s"]
    if paths:
        cmd += ["--"] + list(paths) + list(_RELEASE_CONTROL_EXCLUDES)
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git_has_changes(repo_root: Path, since_ref: str, *paths: str) -> bool:
    """True if any commits touch the given paths since since_ref."""
    return bool(_git_log(repo_root, since_ref, *paths))


def _latest_tag_for_prefix(repo_root: Path, prefix: str) -> Optional[str]:
    """Return the most recent tag matching prefix* by semver order, or None."""
    result = subprocess.run(
        ["git", "tag", "--list", f"{prefix}*"],
        cwd=repo_root, capture_output=True, text=True,
    )
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    if not tags:
        return None
    from cmru.release import _semver_key
    def _tag_key(tag: str) -> tuple:
        ver = tag[len(prefix):]
        return _semver_key(ver)
    return max(tags, key=_tag_key)


# ---------------------------------------------------------------------------
# Conventional Commits bump detection (S12.4)
# ---------------------------------------------------------------------------

_CC_BREAKING = re.compile(r"^[a-z]+(\([^)]+\))?!:|BREAKING[ -]CHANGE")
_CC_FEAT = re.compile(r"^feat(\([^)]+\))?:")


def _bump_from_commits(messages: List[str]) -> str:
    """Return 'major', 'minor', or 'patch' based on conventional commits."""
    for msg in messages:
        if _CC_BREAKING.search(msg):
            return "major"
    for msg in messages:
        if _CC_FEAT.match(msg):
            return "minor"
    return "patch"


# ---------------------------------------------------------------------------
# Version arithmetic
# ---------------------------------------------------------------------------

def _parse_semver(version: str) -> Tuple[int, int, int, Optional[str]]:
    """Parse a semver string into (major, minor, patch, prerelease)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(-(.*))?$", version)
    if not m:
        raise ValueError(f"Cannot parse version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(5)


def bump_version(current: str, bump: str) -> str:
    """Bump a semver string by the given level (major/minor/patch)."""
    major, minor, patch, _ = _parse_semver(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _next_counter_version(repo_root: Path, prefix: str, base_version: str) -> str:
    """Increment the counter suffix: <prefix><base_version>-r<N> → r<N+1>."""
    result = subprocess.run(
        ["git", "tag", "--list", f"{prefix}{base_version}-r*"],
        cwd=repo_root, capture_output=True, text=True,
    )
    existing = [t for t in result.stdout.splitlines() if t.strip()]
    if not existing:
        return f"{base_version}-r1"
    max_n = 0
    for tag in existing:
        m = re.search(r"-r(\d+)$", tag)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{base_version}-r{max_n + 1}"


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _apply_strategy_scm(
    repo_root: Path,
    prefix: str,
    next_version: str,
    dry_run: bool = False,
) -> str:
    """scm strategy: tag HEAD directly (S12.5.1). setuptools_scm reads the tag."""
    tag = f"{prefix}{next_version}"
    if dry_run:
        print(f"[DRY] Would tag: {tag}")
        return tag
    rc = subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        cwd=repo_root,
    ).returncode
    if rc != 0:
        print(f"[ERROR] git tag {tag} failed (exit {rc})", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] Tagged: {tag}")
    return tag


def _apply_strategy_file(
    repo_root: Path,
    prefix: str,
    next_version: str,
    version_file: str,
    project_cwd: Path,
    dry_run: bool = False,
) -> str:
    """file strategy: write VERSION file, commit, then tag (S12.5.2)."""
    vfile = project_cwd / version_file
    tag = f"{prefix}{next_version}"
    if dry_run:
        print(f"[DRY] Would write {vfile} → {next_version}, commit, tag {tag}")
        return tag
    vfile.write_text(next_version + "\n", encoding="utf-8")
    subprocess.run(["git", "add", str(vfile)], cwd=repo_root, check=True)
    # Only commit if VERSION actually changed; otherwise tag current HEAD. This makes a
    # re-release at the existing version idempotent instead of failing on an empty commit.
    has_staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", str(vfile)], cwd=repo_root
    ).returncode != 0
    if has_staged:
        subprocess.run(
            ["git", "commit", "-m", f"chore: bump {prefix} to {next_version}"],
            cwd=repo_root, check=True,
        )
    else:
        print(f"[INFO] {version_file} already {next_version} — tagging current HEAD.")
    rc = subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        cwd=repo_root,
    ).returncode
    if rc != 0:
        print(f"[ERROR] git tag {tag} failed (exit {rc})", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] Committed version file and tagged: {tag}")
    return tag


def _apply_strategy_counter(
    repo_root: Path,
    prefix: str,
    base_version: str,
    dry_run: bool = False,
) -> str:
    """counter strategy: increment -r<N> suffix (S12.5.3). Used by pwmcp."""
    next_ver = _next_counter_version(repo_root, prefix, base_version)
    tag = f"{prefix}{next_ver}"
    if dry_run:
        print(f"[DRY] Would tag: {tag}")
        return tag
    rc = subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        cwd=repo_root,
    ).returncode
    if rc != 0:
        print(f"[ERROR] git tag {tag} failed (exit {rc})", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] Tagged: {tag}")
    return tag


# ---------------------------------------------------------------------------
# Change detection (S12.2)
# ---------------------------------------------------------------------------

def detect_changed_projects(
    repo_root: Path,
    projects: Dict[str, Any],
) -> List[Tuple[str, Any, Optional[str], str]]:
    """Return [(name, config, last_tag_or_None, bump)] for projects with changes.

    Projects with no prior tag are always included (first release).
    """
    changed = []
    for name, proj in projects.items():
        prefix = getattr(proj, "prefix", None) or f"{name}-v"
        paths = getattr(proj, "paths", None) or [getattr(proj, "cwd", None) or name]
        last_tag = _latest_tag_for_prefix(repo_root, prefix)
        if last_tag:
            messages = _git_log(repo_root, last_tag, *paths)
            if not messages:
                continue  # no changes
        else:
            messages = []  # first release — always eligible

        version_cfg = getattr(proj, "version", None)
        bump_rule = getattr(version_cfg, "bump", "conventional") if version_cfg else "conventional"
        if bump_rule == "conventional" and messages:
            bump = _bump_from_commits(messages)
        else:
            bump = "patch"

        changed.append((name, proj, last_tag, bump))
    return changed


# ---------------------------------------------------------------------------
# Status (dry-run preview) and Release verbs
# ---------------------------------------------------------------------------

def status_cmd(
    repo_root: Path,
    projects: Dict[str, Any],
    *,
    minor: bool = False,
    major: bool = False,
    set_version: Optional[str] = None,
) -> None:
    """Print a table of changed projects and their proposed next versions (S12.7 status)."""
    changed = detect_changed_projects(repo_root, projects)
    if not changed:
        print("[INFO] No projects with changes since last release.")
        return

    bump_override = "major" if major else "minor" if minor else None
    print(f"\n{'Project':<40} {'Last Tag':<30} {'Bump':<8} {'Next Version'}")
    print("-" * 100)
    for name, proj, last_tag, bump in changed:
        prefix = getattr(proj, "prefix", None) or f"{name}-v"
        version_cfg = getattr(proj, "version", None)
        strategy = getattr(version_cfg, "strategy", "scm") if version_cfg else "scm"

        if strategy == "delegated":
            # The project computes its own version during build/publish (e.g. pwmcp's
            # playwright-driven -r<N>). cmru reports "changed" but mints no tag here.
            print(f"  {name:<38} {(last_tag or '(none)'):<30} {'deleg.':<8} (self-versioned at build)")
            continue

        if not getattr(proj, "mint_tag", True):
            # oci-image / version='none': published to a registry (ghcr), no git tag.
            note = "(registry publish — ghcr, no tag)"
            print(f"  {name:<38} {(last_tag or '(none)'):<30} {'image':<8} {note}")
            continue

        if set_version:
            next_ver = set_version
        elif bump_override:
            bump = bump_override
            if last_tag:
                current_ver = last_tag[len(prefix):]
                next_ver = bump_version(current_ver, bump)
            else:
                next_ver = "0.1.0"
        elif strategy == "counter":
            base_ver = getattr(version_cfg, "base_version", "1.0.0") if version_cfg else "1.0.0"
            next_ver = _next_counter_version(repo_root, prefix, base_ver)
        elif last_tag:
            current_ver = last_tag[len(prefix):]
            next_ver = bump_version(current_ver, bump)
        else:
            next_ver = "0.1.0"

        print(f"  {name:<38} {(last_tag or '(none)'):<30} {bump:<8} {prefix}{next_ver}")
    print()


def release_cmd(
    repo_root: Path,
    projects: Dict[str, Any],
    *,
    project_filter: Optional[str] = None,
    minor: bool = False,
    major: bool = False,
    set_version: Optional[str] = None,
    dry_run: bool = False,
) -> List[str]:
    """Tag changed projects; return list of tags created (S12.7 release).

    Requires clean working tree (S12.3). Caller runs build+publish after.
    """
    # Clean tree guard
    dirty = _git(repo_root, "status", "--porcelain")
    if dirty:
        print("[ERROR] Working tree is dirty — commit or stash changes before release.", file=sys.stderr)
        sys.exit(1)

    changed = detect_changed_projects(repo_root, projects)
    if project_filter:
        changed = [(n, p, lt, b) for n, p, lt, b in changed if n == project_filter]

    if not changed:
        print("[INFO] No changed projects; nothing to tag.")
        return []

    bump_override = "major" if major else "minor" if minor else None
    created_tags: List[str] = []

    for name, proj, last_tag, bump in changed:
        prefix = getattr(proj, "prefix", None) or f"{name}-v"
        version_cfg = getattr(proj, "version", None)
        strategy = getattr(version_cfg, "strategy", "scm") if version_cfg else "scm"
        version_file = getattr(version_cfg, "file", "VERSION") if version_cfg else "VERSION"
        project_cwd = repo_root / (getattr(proj, "cwd", None) or name)

        if strategy == "delegated" or not getattr(proj, "mint_tag", True):
            # No cmru tag. delegated → project owns the tag; oci-image/none → published
            # to a registry, never git-tagged. build/publish steps do the work.
            why = "delegated versioning" if strategy == "delegated" else f"{strategy} / registry publish"
            print(f"[INFO] {name}: {why} — cmru mints no tag; build/publish steps own publishing.")
            continue

        if set_version:
            next_ver = set_version
            eff_bump = "set"
        elif bump_override:
            eff_bump = bump_override
            if last_tag:
                current_ver = last_tag[len(prefix):]
                next_ver = bump_version(current_ver, eff_bump)
            else:
                next_ver = "0.1.0"
        elif strategy == "counter":
            base_ver = getattr(version_cfg, "base_version", "1.0.0") if version_cfg else "1.0.0"
            next_ver = _next_counter_version(repo_root, prefix, base_ver)
            eff_bump = "counter"
        elif last_tag:
            eff_bump = bump
            current_ver = last_tag[len(prefix):]
            next_ver = bump_version(current_ver, eff_bump)
        else:
            eff_bump = bump
            next_ver = "0.1.0"

        print(f"[INFO] {name}: {last_tag or '(first release)'} → {prefix}{next_ver} ({eff_bump})")

        if strategy == "scm":
            tag = _apply_strategy_scm(repo_root, prefix, next_ver, dry_run=dry_run)
        elif strategy.startswith("file:"):
            vf = strategy[len("file:"):]
            tag = _apply_strategy_file(repo_root, prefix, next_ver, vf, project_cwd, dry_run=dry_run)
        elif strategy == "counter":
            tag = _apply_strategy_counter(repo_root, prefix, next_ver.rsplit("-r", 1)[0], dry_run=dry_run)
        else:
            print(f"[ERROR] Unknown strategy '{strategy}' for project {name}", file=sys.stderr)
            sys.exit(2)

        created_tags.append(tag)

    return created_tags

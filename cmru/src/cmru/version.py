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
  external:VAR — read a version derived by a prepare step from <cwd>/cmru.vars

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

from cmru.config_names import PROJECT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root,
        capture_output=True, text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout.strip()


# Release-control files live inside a project's subtree but are not product source —
# editing them (e.g. repointing release_config during a migration) MUST NOT trigger a
# version bump for that product. Excluded from change detection (S12.2).
_RELEASE_CONTROL_EXCLUDES = (
    f":(exclude,glob)**/{PROJECT_CONFIG_FILENAME}",
    ":(exclude,glob)**/cmru.vars",
    # CMRU's source-first history is release metadata, not a product change that
    # should by itself schedule the following release.  Custom configured history
    # paths are additionally excluded by changelog.py while rendering entries.
    ":(exclude,glob)**/CHANGES.md",
    ":(exclude,glob)**/CHANGELOG.md",
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


class ReleasePlanRefused(RuntimeError):
    """A release-plan integrity check (S12.2a/S12.2b) refused before any
    project's cycle started: no gate ran, nothing was promoted or tagged.
    A dedicated subclass (still a plain ``RuntimeError`` to callers that
    only ``except RuntimeError`` today) so the isolated release transaction
    can tell "this worktree is unmodified, safe to discard" apart from a
    genuine mid-release failure, which must be retained for inspection."""


def _tag_pushed_to_origin(repo_root: Path, tag: str) -> bool:
    """True if ``tag`` exists on ``origin`` right now AND points at the exact
    same commit locally and remotely (S12.2a / KI-12a).

    Checking only the ref NAME is not enough: a hand-made local tag can share
    a name with a genuinely-published tag while pointing at a different
    commit -- every later decision (S12.2b's tag-vs-HEAD comparison, the
    version this tag implies) must run against the PUBLISHED object, never a
    same-named local one. One ``git ls-remote`` call, querying both the plain
    ref and its peeled ``^{}`` form, covers annotated and lightweight tags
    alike with no extra network round trip: an annotated tag returns both
    lines (the tag object and the commit it targets); a lightweight tag
    returns only the plain line, which already IS the commit.

    Exit code 2 ("no matching refs") means genuinely absent -> False. Any
    other non-zero exit means origin could not even be queried -> raises
    (never silently treated as either pushed or unpushed). A found ref whose
    resolved commit disagrees with the local one also raises immediately,
    naming both SHAs -- that is not "absent", so it must not be reported
    with the "push it" remedy that implies nothing exists there yet.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", "origin",
         f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode == 2:
        return False
    if result.returncode != 0:
        raise ReleasePlanRefused(
            f"cannot verify tag {tag!r} against origin's tags (git ls-remote exited "
            f"{result.returncode}): {result.stderr.strip() or result.stdout.strip()}. "
            "The release baseline must reflect the pushed repository (S12.2a); check "
            "network connectivity/auth to origin and retry."
        )
    plain_sha = peeled_sha = None
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/tags/{tag}^{{}}":
            peeled_sha = sha
        elif ref == f"refs/tags/{tag}":
            plain_sha = sha
    remote_commit = peeled_sha or plain_sha  # lightweight tags have no peeled line
    if remote_commit is None:
        raise ReleasePlanRefused(
            f"git ls-remote found a ref for tag {tag!r} but its output could not be "
            f"parsed: {result.stdout.strip()!r}"
        )
    local_commit = _git(repo_root, "rev-parse", f"{tag}^{{commit}}")
    if remote_commit != local_commit:
        raise ReleasePlanRefused(
            f"tag {tag!r} disagrees with origin: local points at {local_commit} but "
            f"origin's {tag!r} points at {remote_commit}. The release baseline must "
            "reflect the pushed repository (S12.2a) -- this local tag is not the same "
            "object as the published one, almost always a hand-made tag created with "
            "the same name over an existing published release. If the local one is a "
            f"mistake, delete and re-fetch it: git tag -d {tag} && "
            "git fetch --tags --force origin."
        )
    return True


def _highest_remote_tag_for_prefix(repo_root: Path, prefix: str, tag_key) -> Optional[str]:
    """The highest-semver tag NAME matching ``prefix*`` that exists on
    ``origin`` right now, or None (S12.2a / KI-12a).

    Used only to detect a newer origin-only tag this local clone has not
    fetched: a stale local view would otherwise compute a baseline behind
    what is actually published, derive a version that already exists, and
    fail mid-release -- after ``origin/main`` has already been promoted for
    this project, exactly the half-completed state S12.2b now aborts on.
    ``tag_key`` is the caller's own ``_semver_key``-based ordering (over the
    same ``prefix``), so remote and local candidates are compared identically.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{prefix}*"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ReleasePlanRefused(
            f"cannot list origin's tags for prefix {prefix!r} (git ls-remote exited "
            f"{result.returncode}): {result.stderr.strip() or result.stdout.strip()}. "
            "The release baseline must reflect the pushed repository (S12.2a); check "
            "network connectivity/auth to origin and retry."
        )
    names: set[str] = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            name = name[: -len("^{}")]
        names.add(name)
    return max(names, key=tag_key) if names else None


def _latest_tag_for_prefix(
    repo_root: Path, prefix: str, *, require_pushed: bool = False,
) -> Optional[str]:
    """Return the most recent tag matching prefix* by semver order, or None.

    ``require_pushed=True`` (S12.2a / KI-12a) additionally refuses the result
    unless it is a true function of the pushed repository, in two ways:

    1. Origin must not carry a HIGHER matching tag than this local clone
       knows about -- a stale local view (never fetched, or fetched before
       another operator's release landed) would otherwise derive a version
       that already exists on origin, an even more silent variant of the
       original defect. Checked whether or not a local candidate exists at
       all (a "first release" locally can still be stale).
    2. Whatever candidate this local clone does select must be present on
       origin AND point at the exact same commit there (:func:`_tag_pushed_to_origin`)
       -- ``git tag --list`` alone returns local-only refs, and a hand-made,
       never-pushed (or same-named-but-different-object) tag would otherwise
       silently become the baseline every operator's release plan is computed
       from.

    Callers that don't need either guarantee (read-only previews, migrations)
    get today's plain local read by leaving this False.
    """
    result = subprocess.run(
        ["git", "tag", "--list", f"{prefix}*"],
        cwd=repo_root, capture_output=True, text=True,
    )
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    from cmru.release import _semver_key
    def _tag_key(tag: str) -> tuple:
        ver = tag[len(prefix):]
        return _semver_key(ver)
    candidate = max(tags, key=_tag_key) if tags else None

    if require_pushed:
        remote_candidate = _highest_remote_tag_for_prefix(repo_root, prefix, _tag_key)
        if remote_candidate is not None and (
            candidate is None or _tag_key(remote_candidate) > _tag_key(candidate)
        ):
            raise ReleasePlanRefused(
                f"origin has a newer tag for prefix {prefix!r} ({remote_candidate}) than "
                f"this local clone knows about ({candidate or 'none locally'}). The release "
                "baseline must reflect the pushed repository (S12.2a); fetch tags and "
                "re-run: git fetch --tags origin"
            )
        if candidate is not None and not _tag_pushed_to_origin(repo_root, candidate):
            raise ReleasePlanRefused(
                f"latest local tag {candidate!r} (prefix {prefix!r}) is not present on origin. "
                "A release baseline must reflect the pushed repository (S12.2a), not local-only "
                "scratch refs -- cmru owns tag creation for a cmru-managed project, so this usually "
                "means a tag was created by hand. If it is real, push it first "
                f"(git push origin {candidate}); if it was a mistake, delete it "
                f"(git tag -d {candidate})."
            )
    return candidate


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
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown version bump level: {bump!r}; expected major, minor, or patch")


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


def _external_version(project_cwd: Path, variable: str) -> str:
    """Read a prepare-step version from the transaction-local ``cmru.vars`` file."""
    vars_file = project_cwd / "cmru.vars"
    if not vars_file.exists():
        raise RuntimeError(
            f"external version {variable!r} requires {vars_file}; run the project's prepare step"
        )
    for line in vars_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == variable:
            result = value.strip()
            if result:
                return result
    raise RuntimeError(f"{vars_file} does not define required version variable {variable!r}")


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

def _tag_covers_head(repo_root: Path, tag: str) -> bool:
    """True if HEAD is already an ancestor of (or equal to) ``tag``'s commit —
    true for BOTH the benign "already released as of this exact commit" state
    and the anomalous "tag is strictly ahead of this commit" state (S12.2b);
    it alone cannot distinguish them, which is exactly why
    :func:`_tag_head_relationship` additionally compares the resolved commit
    objects. Also true, trivially, for the ordinary "genuinely unchanged"
    case's opposite (tag behind HEAD) being False.
    """
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", tag],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        raise ReleasePlanRefused(
            f"cannot compare HEAD against tag {tag!r} (git merge-base exited "
            f"{result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.returncode == 0


def _tag_head_relationship(repo_root: Path, tag: str) -> str:
    """Classify ``tag`` against HEAD once a path-scoped ``git log`` is already
    known to be empty for it (S12.2b) — three states, not two:

    * ``"equal"``  — the tag's commit IS HEAD: the ordinary, benign state right
      after any successful release (nothing has landed anywhere since). Not an
      error.
    * ``"ahead"``  — the tag's commit is a strict descendant of HEAD: a tag
      that is pushed but not (yet) in this snapshot's history at all, almost
      always because a previous release tagged and pushed this project but
      failed before promoting ``origin/main`` to that commit. A genuine
      anomaly worth aborting on.
    * ``"behind"`` — the tag is a strict ancestor of HEAD: the ordinary
      genuinely-unchanged case (some other project's commits moved HEAD, this
      project's own paths didn't change). Not an error.

    ``git merge-base --is-ancestor`` alone cannot separate "equal" from
    "ahead" (both make HEAD an ancestor-or-equal of the tag), so the commit
    objects are resolved and compared directly first.
    """
    tag_commit = _git(repo_root, "rev-parse", f"{tag}^{{commit}}")
    head_commit = _git(repo_root, "rev-parse", "HEAD")
    if tag_commit == head_commit:
        return "equal"
    if _tag_covers_head(repo_root, tag):
        return "ahead"
    return "behind"


def _tag_ahead_error(repo_root: Path, name: str, prefix: str, tag: str) -> ReleasePlanRefused:
    head = _git(repo_root, "rev-parse", "HEAD")
    return ReleasePlanRefused(
        f"{name}: latest tag {tag} is AHEAD of the snapshot commit {head[:8]} being "
        "released — it points at a commit that is not (yet) in this snapshot's history.\n"
        "        This usually means a previous release tagged and pushed this project but\n"
        "        failed before promoting origin/main to that commit (a half-completed\n"
        "        release) — inspect the prior attempt before proceeding. Compare:\n"
        f"        git log --oneline {head[:8]}..{tag}\n"
        "        Re-run once origin/main includes that commit, or pass\n"
        "        --allow-tag-ahead-of-head to skip this project deliberately."
    )


def _unchanged_reason(repo_root: Path, name: str, last_tag: str, paths: List[str]) -> str:
    """KI-13/S12.2e: the ordinary "nothing changed under this project's own
    paths since its last release" line -- names the exact baseline tag AND
    the commit it resolves to, not a bare project-name list. Package A's
    "already released ... at the snapshot commit" message (the "equal"
    state) already has this shape; this is the "behind" state's equivalent
    (some OTHER project's commits moved HEAD, this one's own paths didn't
    change) -- by far the most common skip reason, and previously the one
    with no message at all. An operator who just committed under one of
    ``paths`` can now immediately tell that apart from a wrong ``paths``
    glob or a misplaced/unpushed tag -- both of which are refused earlier,
    by S12.2a/S12.2b, before this line is ever reached."""
    tag_commit = _git(repo_root, "rev-parse", f"{last_tag}^{{commit}}")
    where = ", ".join(f"{p}/" for p in paths)
    return (
        f"[INFO] Unchanged, skipping: {name} (no commits under {where} "
        f"since {last_tag} @ {tag_commit[:8]})"
    )


def detect_changed_projects(
    repo_root: Path,
    projects: Dict[str, Any],
    *,
    require_pushed_baseline: bool = False,
    check_tag_at_head: bool = False,
    allow_tag_ahead_of_head: bool = False,
) -> List[Tuple[str, Any, Optional[str], str]]:
    """Return [(name, config, last_tag_or_None, bump)] for projects with changes.

    Projects with no prior tag are always included (first release) -- never
    reported as "unchanged", and never printed below (S12.2).

    ``require_pushed_baseline`` (S12.2a) and ``check_tag_at_head`` (S12.2b)
    default to False, preserving today's plain-local-read, skip-silently
    behaviour for read-only previews (``cmru status``) and migrations
    (``cmru changelog``). The isolated release transaction (S-CLI.5) is the
    one caller that turns both on unconditionally: see ``cli.py``'s
    release-plan computation.

    When ``check_tag_at_head`` is True, EVERY unchanged/skipped state below
    prints exactly one informative line naming the project, the baseline tag,
    and the specific reason (KI-13/S12.2e; one shape, shared by all three
    states, not a competing style per state):

    * "equal" (a pushed tag exactly at the snapshot commit, the ordinary
      result right after a completed release) is ALWAYS reported — never an
      error, and never gated by ``allow_tag_ahead_of_head``, which controls
      only the "ahead" state below.
    * "ahead" (S12.2b's genuine anomaly) raises unless
      ``allow_tag_ahead_of_head`` is True (``--allow-tag-ahead-of-head``, née
      ``--allow-tag-at-head``), in which case it too is reported and skipped
      rather than silently folded away.
    * "behind" (the ordinary case: some other project's commits moved HEAD,
      this project's own paths didn't change) is reported via
      :func:`_unchanged_reason`.

    With ``check_tag_at_head`` False, ALL of this is skipped and every state
    folds back into today's plain silent skip -- ``allow_tag_ahead_of_head``
    is meaningless without it, and callers that want the old silent preview
    (``cmru status``, ``cmru changelog``) are unaffected.
    """
    changed = []
    for name, proj in projects.items():
        prefix = getattr(proj, "prefix", None) or f"{name}-v"
        paths = getattr(proj, "paths", None) or [getattr(proj, "cwd", None) or name]
        last_tag = _latest_tag_for_prefix(repo_root, prefix, require_pushed=require_pushed_baseline)
        if last_tag:
            messages = _git_log(repo_root, last_tag, *paths)
            if not messages:
                if check_tag_at_head:
                    relationship = _tag_head_relationship(repo_root, last_tag)
                    if relationship == "equal":
                        print(
                            f"[INFO] Unchanged, skipping: {name} (already released as "
                            f"{last_tag} at the snapshot commit; nothing new since)"
                        )
                    elif relationship == "ahead":
                        if not allow_tag_ahead_of_head:
                            raise _tag_ahead_error(repo_root, name, prefix, last_tag)
                        print(
                            f"[INFO] Unchanged, skipping: {name} (tag {last_tag} is ahead "
                            "of the snapshot commit; skipped via --allow-tag-ahead-of-head)"
                        )
                    else:  # "behind" -- the ordinary, by-far-most-common skip reason
                        print(_unchanged_reason(repo_root, name, last_tag, paths))
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

        if strategy.startswith("external:"):
            variable = strategy.split(":", 1)[1] or "VERSION"
            print(f"  {name:<38} {(last_tag or '(none)'):<30} {'prepare':<8} (derived by {variable})")
            continue

        if not getattr(proj, "git_tag", True):
            # The project owns its no-tag publication convention through explicit
            # build/push commands; CMRU only reports the tag policy.
            note = "(project-owned publication, no git tag)"
            print(f"  {name:<38} {(last_tag or '(none)'):<30} {'no-tag':<8} {note}")
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

        if not getattr(proj, "git_tag", True):
            # No cmru tag. A no-tag project owns its publish convention; CMRU still
            # runs its declared build/push steps through the unified runner.
            why = f"{strategy} / project-owned publish"
            print(f"[INFO] {name}: {why} — cmru mints no tag; build/publish steps own publishing.")
            continue

        if strategy.startswith("external:"):
            variable = strategy.split(":", 1)[1].strip()
            if not variable:
                print(f"[ERROR] external version strategy requires a variable for project {name}", file=sys.stderr)
                sys.exit(2)
            next_ver = _external_version(project_cwd, variable)
            eff_bump = "external"
        elif set_version:
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

        if strategy == "scm" or strategy.startswith("external:"):
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

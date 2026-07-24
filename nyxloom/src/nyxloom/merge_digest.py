"""F018 P2a — merge-digest producer (plan §3.1).

Centralised helper that builds a typed ``MergeDigest`` from
``(cfg, states, task_id, merge_commit)`` so the MANUAL (`cli.cmd_merge`)
and AUTOMATIC (`daemon._execute_auto_merge`) paths produce a
byte-identical digest for the same inputs — deterministically from
(cfg, task_id, merge_commit, project events) + read-only git calls,
never from path-specific local state.

``build_merge_digest`` is the main entry point that populates every field
of the ``MergeDigest`` dataclass (already defined in ``carver_session.py``).
``carver_digest_payload`` is a thin wrapper that returns ``to_dict()`` for
embedding in the ``MERGE_RECORDED`` event payload.

Every git call is read-only and tolerant: a missing commit or path yields a
defined empty / ``None`` value, never a crash.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .carver_session import MergeDigest
from .config import ProjectConfig
from .log import get_logger
from .types import AttemptState, EventType, Role, TaskStateFile

log = get_logger("merge_digest")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_merge_digest(
    cfg: ProjectConfig,
    states: dict[str, TaskStateFile],
    task_id: str,
    merge_commit: str,
    *,
    source_kind: str = "review",
) -> MergeDigest:
    """Build a fully-populated MergeDigest.

    Parameters
    ----------
    cfg:
        Project configuration (provides repo root, spine paths).
    states:
        Current task statefiles (``storage.list_states(project)``).
    task_id:
        The task being merged.
    merge_commit:
        Full SHA of the merge commit.
    source_kind:
        ``"review"`` (default) or ``"auto"`` — included for traceability
        in the digest's ``handoff`` path string but does **not** change
        any computed field (parity mandate).

    Returns
    -------
    MergeDigest
        All fields populated. Tolerates missing commits / paths without
        raising.
    """
    # -- simple scalar fields -----------------------------------------------
    schema_version = 1
    digest_id = f"merge:{cfg.project_id}:{merge_commit}"

    first_parent = _git_rev_parse(cfg.root, f"{merge_commit}^1") or ""

    # -- files (name-status with rename detection) --------------------------
    files = _diff_name_status(cfg.root, merge_commit)

    # -- diffstat -----------------------------------------------------------
    diffstat = _diff_numstat(cfg.root, merge_commit)

    # -- handoff ------------------------------------------------------------
    tsf = states.get(task_id)
    handoff = _build_handoff(cfg, tsf, merge_commit)

    # -- review -------------------------------------------------------------
    review = _build_review(tsf, cfg, merge_commit)

    # -- spine delta --------------------------------------------------------
    spine_delta = _build_spine_delta(cfg, merge_commit, first_parent)

    return MergeDigest(
        schema_version=schema_version,
        digest_id=digest_id,
        task_id=task_id or "",
        merge_commit=merge_commit,
        first_parent=first_parent,
        handoff=handoff,
        files=files,
        diffstat=diffstat,
        review=review,
        spine_delta=spine_delta,
    )


def carver_digest_payload(
    cfg: ProjectConfig,
    states: dict[str, TaskStateFile],
    task_id: str,
    merge_commit: str,
    *,
    source_kind: str = "review",
) -> dict[str, Any]:
    """Build the ``MergeDigest`` and return its ``to_dict()``.

    This is the value to embed in the ``MERGE_RECORDED`` event payload under
    the ``carver_digest`` key.
    """
    md = build_merge_digest(
        cfg, states, task_id, merge_commit, source_kind=source_kind,
    )
    return md.to_dict()


# ---------------------------------------------------------------------------
# Internal helpers — every git call is read-only and tolerant
# ---------------------------------------------------------------------------

def _git_rev_parse(root: Path, ref: str) -> str | None:
    """``git -C <root> rev-parse <ref>`` — returns None on failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", ref],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _git_show(root: Path, commit: str, path: str) -> str | None:
    """``git -C <root> show <commit>:<path>`` — returns None on failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _git_sha256(root: Path, commit: str, path: str) -> str | None:
    """SHA-256 digest of a file *at a specific git revision*, or None."""
    content = _git_show(root, commit, path)
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _repo_root(root: Path) -> str | None:
    """Resolve the true git working-tree root, falling back to *root*."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return str(root)


# ---------------------------------------------------------------------------
# files — git diff-tree --name-status with rename detection
# ---------------------------------------------------------------------------

def _diff_name_status(root: Path, commit: str) -> list[dict[str, str]]:
    """Parse ``git diff-tree --name-status -r -M <commit>^1 <commit>``.

    Compares the commit against its first parent, which works correctly for
    both merge commits (2 parents) and regular commits (1 parent).
    Returns ``[{path, change}]`` sorted by path for determinism.
    Change codes: A, M, D, R (renamed files use the new path).
    """
    rr = _repo_root(root)
    first_parent = _git_rev_parse(Path(rr), f"{commit}^1")
    if first_parent is None:
        return []
    try:
        r = subprocess.run(
            ["git", "-C", rr, "diff-tree", "--no-commit-id",
             "--name-status", "-r", "-M", first_parent, commit],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        return _parse_name_status(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return []


def _parse_name_status(raw: str) -> list[dict[str, str]]:
    """Convert raw ``git diff-tree --name-status`` output to structured list.

    Handles rename lines (``R<score>`` → ``R``) by using the second path
    (the new name).  Ignores unmergeable entries (``U``).
    """
    out: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if not parts:  # pragma: no cover -- unreachable: a non-empty stripped line always splits to >=1 element
            continue
        raw_code = parts[0]
        # Normalise change code: R<score> -> R
        code = raw_code[0] if raw_code else "?"
        if code not in ("A", "M", "D", "R", "C"):
            continue
        # Rename: "R<score>" followed by source<TAB>dest
        if code == "R" and len(parts) >= 3:
            path = parts[2]
        elif len(parts) >= 2:
            path = parts[1]
        else:
            continue
        out.append({"path": path, "change": code})
    out.sort(key=lambda x: x["path"])
    return out


# ---------------------------------------------------------------------------
# diffstat — git diff-tree --numstat
# ---------------------------------------------------------------------------

def _diff_numstat(root: Path, commit: str) -> dict[str, int]:
    """Parse ``git diff-tree --numstat -r <commit>^1 <commit>``.

    Compares the commit against its first parent.  Works for merge commits.
    Returns ``{files_changed, insertions, deletions}``.
    """
    rr = _repo_root(root)
    first_parent = _git_rev_parse(Path(rr), f"{commit}^1")
    if first_parent is None:
        return {"files_changed": 0, "insertions": 0, "deletions": 0}
    try:
        r = subprocess.run(
            ["git", "-C", rr, "diff-tree", "--no-commit-id",
             "--numstat", "-r", first_parent, commit],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return {"files_changed": 0, "insertions": 0, "deletions": 0}
        return _parse_numstat(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return {"files_changed": 0, "insertions": 0, "deletions": 0}


def _parse_numstat(raw: str) -> dict[str, int]:
    """Convert raw ``git diff-tree --numstat`` output.

    ``--numstat`` output: ``<additions>\t<deletions>\t<path>``.
    A ``-`` in either count means binary — treated as 0 for the mechanical
    digest (binary file: known to have changed but line metrics are meaningless).
    """
    files_changed = 0
    insertions = 0
    deletions = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_str, del_str = parts[0], parts[1]
        # Binary files show "-" for both counts
        try:
            ins = int(add_str) if add_str != "-" else 0
        except ValueError:
            ins = 0
        try:
            dels = int(del_str) if del_str != "-" else 0
        except ValueError:
            dels = 0
        insertions += ins
        deletions += dels
        files_changed += 1
    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
    }


# ---------------------------------------------------------------------------
# handoff — recover path/sha256/title/input_revision from the task
# ---------------------------------------------------------------------------

def _build_handoff(
    cfg: ProjectConfig,
    tsf: TaskStateFile | None,
    merge_commit: str,
) -> dict[str, Any]:
    """Build the ``handoff`` sub-dict from the task statefile.

    Deterministic from ``(tsf, merge_commit)`` alone:
      - ``path``: relative handoff path from the statefile.
      - ``sha256``: SHA-256 of the handoff content at the merge commit
        (via ``git show <merge_commit>:<path>``).
      - ``title``: YAML frontmatter title at the merge commit.
      - ``input_revision``: YAML frontmatter ``input_revision`` at the merge
        commit.

    Returns a dict with all four keys (values may be ``None`` when the
    handoff file cannot be read — defined degradation, never a crash).
    """
    result: dict[str, Any] = {
        "path": None,
        "sha256": None,
        "title": None,
        "input_revision": None,
    }
    if tsf is None or not tsf.handoff_path:
        return result

    rel_path = tsf.handoff_path
    # Normalise — strip leading ./ or /
    if rel_path.startswith("./"):
        rel_path = rel_path[2:]
    rel_path = rel_path.lstrip("/")

    result["path"] = rel_path

    # SHA-256
    result["sha256"] = _git_sha256(cfg.root, merge_commit, rel_path)

    # Parse frontmatter for title + input_revision (raw YAML — not schema
    # validated, so a malformed handoff degrades gracefully).
    fm = _parse_frontmatter_raw(cfg.root, merge_commit, rel_path)
    if fm:
        result["title"] = fm.get("title")
        result["input_revision"] = fm.get("input_revision")

    return result


def _parse_frontmatter_raw(
    root: Path, commit: str, path: str,
) -> dict[str, Any] | None:
    """Best-effort YAML frontmatter parsing from a committed file.

    Looks for the first ``---`` … ``---`` block at the top of the file.
    Returns a plain dict, or ``None`` on any parse failure.
    """
    content = _git_show(root, commit, path)
    if content is None:
        return None
    # Find leading --- and the matching closing ---
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return None
    fm_text = "\n".join(lines[1:closing])
    try:
        data = yaml.safe_load(fm_text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    return None


# ---------------------------------------------------------------------------
# review — verdict / attempt_id / report path+hash from the task
# ---------------------------------------------------------------------------

def _build_review(
    tsf: TaskStateFile | None,
    cfg: ProjectConfig,
    merge_commit: str,
) -> dict[str, Any]:
    """Build the ``review`` sub-dict.

    Strategy (deterministic):
      1. From the task's attempts, find the most recent
         ``REVIEW_INDEPENDENT`` attempt with a non-terminal result in its
         receipt (the review that approved/rejected this task).
      2. ``verdict`` = receipt result value (``"done"`` / ``"blocked"`` /
         …).
      3. ``attempt_id`` = the attempt id.
      4. ``report_path`` = the attempt's ``log_path`` if set (the path to
         the review output log).
      5. ``report_sha256`` = SHA-256 of that file at the merge commit (if
         the path is relative to the repo root).

    When no review attempt is found, all fields are ``None`` (defined
    degradation — the merge still records).
    """
    result: dict[str, Any] = {
        "verdict": None,
        "attempt_id": None,
        "report_path": None,
        "report_sha256": None,
    }
    if tsf is None:
        return result

    # Walk attempts in reverse to find the latest REVIEW_INDEPENDENT with
    # a receipt.
    rev_attempt = None
    for att in reversed(tsf.attempts):
        if (att.role == Role.REVIEW_INDEPENDENT
                and att.receipt is not None
                and att.receipt.result is not None):
            rev_attempt = att
            break

    if rev_attempt is None:
        return result

    result["verdict"] = rev_attempt.receipt.result.value
    result["attempt_id"] = rev_attempt.attempt_id

    if rev_attempt.log_path:
        log_path = rev_attempt.log_path
        # If the path is relative, resolve it against the repo root.
        log_sha = _git_sha256(cfg.root, merge_commit, log_path)
        result["report_path"] = log_path
        result["report_sha256"] = log_sha

    return result


# ---------------------------------------------------------------------------
# spine delta — the 4 configured paths, hashed before/after, diff ids
# ---------------------------------------------------------------------------

def _spine_paths(cfg: ProjectConfig) -> list[str]:
    """The 4 direction-spine paths from config (plan §2.5).

    Returns only paths that are actually configured (non-None).
    """
    out: list[str] = []
    for attr in ("north_star", "product_definition", "roadmap", "backlog"):
        val = getattr(cfg, attr, None)
        if val and isinstance(val, str):
            out.append(val)
    return out


def _build_spine_delta(
    cfg: ProjectConfig,
    merge_commit: str,
    first_parent: str,
) -> dict[str, Any]:
    """Build the ``spine_delta`` sub-dict.

    For each of the 4 configured spine paths:
      - compute the SHA-256 at ``first_parent`` and ``merge_commit``.
      - if the hash differs, parse the YAML frontmatter at both revisions
        and extract ``id`` fields from lists of items under known keys
        (``features``, ``milestones``, ``backlog_items``).  The set
        difference of ids feeds ``changed_ids``.

    Returns ``{changed, paths, before_revisions, after_revisions, changed_ids}``.
    """
    paths = _spine_paths(cfg)

    before_revisions: dict[str, str | None] = {}
    after_revisions: dict[str, str | None] = {}
    all_changed_paths: list[str] = []
    changed: bool = False
    all_before_ids: set[str] = set()
    all_after_ids: set[str] = set()

    for sp in paths:
        before_hash = _git_sha256(cfg.root, first_parent, sp) if first_parent else None
        after_hash = _git_sha256(cfg.root, merge_commit, sp)

        before_revisions[sp] = before_hash
        after_revisions[sp] = after_hash

        if before_hash != after_hash:
            changed = True
            all_changed_paths.append(sp)
            # Parse frontmatter ids at both revisions
            before_ids = _extract_ids(cfg.root, first_parent, sp) if first_parent else set()
            after_ids = _extract_ids(cfg.root, merge_commit, sp)
            all_before_ids.update(before_ids)
            all_after_ids.update(after_ids)

    # Set-diff: added = in after but not before; removed = in before but not
    # after.  We report the symmetric difference split by prefix convention.
    added = sorted(all_after_ids - all_before_ids)
    removed = sorted(all_before_ids - all_after_ids)

    changed_ids: dict[str, list[str]] = {
        "features": [i for i in added + removed if _is_feature_id(i)],
        "milestones": [i for i in added + removed if _is_milestone_id(i)],
        "backlog_items": [i for i in added + removed if _is_backlog_id(i)],
    }

    # Remove empty categories for a cleaner payload.
    changed_ids = {k: v for k, v in changed_ids.items() if v}

    return {
        "changed": changed,
        "paths": sorted(all_changed_paths),
        "before_revisions": {k: v or "" for k, v in before_revisions.items()},
        "after_revisions": {k: v or "" for k, v in after_revisions.items()},
        "changed_ids": changed_ids,
    }


def _extract_ids(root: Path, commit: str, path: str) -> set[str]:
    """Extract ``id`` values from YAML lists in a committed file's frontmatter.

    Scans for keys whose values are lists of objects that contain an ``id``
    field.  This is deliberately broad so it works with any spine schema
    that follows the convention ``features: [{id: F1, …}]``.
    """
    fm = _parse_frontmatter_raw(root, commit, path)
    if fm is None:
        return set()
    ids: set[str] = set()
    for val in fm.values():
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and "id" in item and isinstance(item["id"], str):
                    ids.add(item["id"])
    return ids


def _is_feature_id(i: str) -> bool:
    return i.upper().startswith("F") and i[1:].isdigit() if len(i) > 1 else False  # noqa: SIM222


def _is_milestone_id(i: str) -> bool:
    return i.upper().startswith("M") and i[1:].isdigit() if len(i) > 1 else False  # noqa: SIM222


def _is_backlog_id(i: str) -> bool:
    return i.upper().startswith("B") and i[1:].isdigit() if len(i) > 1 else False  # noqa: SIM222

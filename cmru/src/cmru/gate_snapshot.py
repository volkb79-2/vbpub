"""Exact-HEAD workspaces for CMRU's nested release-gate lanes.

The aggregate gate never exposes its mutable caller checkout to tester
containers. It runs each registered lane from a dedicated linked worktree at
the caller's exact commit, and copies back only named gate evidence.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator


_LANE_EVIDENCE_FILES = (
    "cmru/coverage.json",
    "cmru/junit-coverage.xml",
    "cmru/.assay/verdict-cmru.json",
    "cmru/.assay/progress-cmru.jsonl",
    "cmru/.assay/mutation-cmru.json",
    "cmru/.assay/progress-mutation-cmru.jsonl",
    "cmru/.assay/coverage-canary-cmru.json",
)
_MUTATION_RESUME_FILES = (
    "cmru/.assay/mutation-cmru.json",
    "cmru/.assay/progress-mutation-cmru.jsonl",
)
_SNAPSHOT_MANIFEST = "cmru/.assay/release-gate-snapshot.json"
_EVIDENCE_SET = frozenset((*_LANE_EVIDENCE_FILES, _SNAPSHOT_MANIFEST))
_MAX_EVIDENCE_BYTES = 512 * 1024 * 1024
_MAX_STATE_FILE_BYTES = 64 * 1024 * 1024
_LEASE_TTL = timedelta(hours=18)
_CACHE_PARTS = frozenset({
    "__pycache__", ".pytest_cache", ".hypothesis", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "build", "dist",
})


def _linked_git_context(root: Path, *, expected_common: Path | None = None):
    """Read and verify a linked worktree's Git pointers without Git discovery."""
    marker = root / ".git"
    marker_text = _read_pointer_file(marker, label="linked-worktree Git marker")
    lines = marker_text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise RuntimeError(f"linked-worktree Git marker is malformed: {marker}")
    raw_admin = Path(lines[0][len("gitdir: "):])
    unresolved_admin = raw_admin if raw_admin.is_absolute() else root / raw_admin
    try:
        admin_stat = unresolved_admin.lstat()
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect linked-worktree admin dir {unresolved_admin}: {exc}"
        ) from exc
    if not stat.S_ISDIR(admin_stat.st_mode):
        raise RuntimeError(
            f"linked-worktree admin path is not a real directory: {unresolved_admin}"
        )
    admin = unresolved_admin.resolve(strict=True)

    def pointer(name: str) -> str:
        path = admin / name
        value = _read_pointer_file(path, label="linked-worktree pointer").strip()
        if not value or "\n" in value:
            raise RuntimeError(f"linked-worktree pointer is not a regular one-line file: {path}")
        return value

    raw_gitdir = Path(pointer("gitdir"))
    recorded_marker = (raw_gitdir if raw_gitdir.is_absolute() else admin / raw_gitdir).resolve(strict=True)
    if recorded_marker != marker.resolve(strict=True):
        raise RuntimeError(f"linked-worktree gitdir pointer disagrees with {marker}")
    raw_common = Path(pointer("commondir"))
    common = (raw_common if raw_common.is_absolute() else admin / raw_common).resolve(strict=True)
    if expected_common is not None and common != expected_common.resolve(strict=True):
        raise RuntimeError(
            f"linked-worktree commondir {common} disagrees with recorded common dir "
            f"{expected_common}"
        )
    if admin.parent != common / "worktrees":
        raise RuntimeError(f"linked-worktree admin dir is outside its common worktrees dir: {admin}")
    if not common.is_dir():
        raise RuntimeError(f"linked-worktree common Git dir is not a directory: {common}")
    return admin, common


def _read_pointer_file(path: Path, *, label: str) -> str:
    """Read a tiny Git pointer without following symlinks or blocking on FIFOs."""
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"{label} is not a regular file: {path}")
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            raise RuntimeError(f"{label} is too large: {path}")
        return raw.decode("utf-8")
    except RuntimeError:
        raise
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"cannot read {label} {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _process_start_ticks(pid: int) -> str | None:
    """Read Linux process start time so a recycled PID is not treated as live."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 2:].split()  # field 3 (state) is index zero
    return fields[19] if len(fields) > 19 else None


def _stale_gate_lease(lease) -> bool:
    """Recognize a dead local CMRU gate holder; unknown holders stay protected."""
    if lease is None or not lease.holder.startswith("cmru-gate:"):
        return False
    fields = lease.holder.split(":")
    if len(fields) not in (3, 4) or not fields[1].isdecimal():
        return False
    pid = int(fields[1])
    start_ticks = _process_start_ticks(pid)
    if start_ticks is None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            return False
        return False
    return len(fields) == 4 and fields[2] != start_ticks


def _worktree_api():
    try:
        import worktree
        return worktree
    except ModuleNotFoundError:
        # Source execution from a checkout; release wheels carry this same
        # package through CMRU's package-dir mapping.
        source = Path(__file__).resolve().parents[3] / "libraries" / "worktree" / "src"
        if source.is_dir():
            import sys
            sys.path.insert(0, str(source))
            import worktree
            return worktree
        raise


@contextlib.contextmanager
def _host_git_policy() -> Iterator[None]:
    """Run lifecycle Git without system/global config, hooks, or fsmonitor."""
    raw_count = os.environ.get("GIT_CONFIG_COUNT", "0")
    if not raw_count.isdecimal():
        raise RuntimeError("GIT_CONFIG_COUNT is malformed; refusing gate snapshot Git")
    index = int(raw_count)
    updates = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": str(index + 2),
        f"GIT_CONFIG_KEY_{index}": "core.hooksPath",
        f"GIT_CONFIG_VALUE_{index}": os.devnull,
        f"GIT_CONFIG_KEY_{index + 1}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{index + 1}": "false",
    }
    previous = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def host_git_hooks_disabled(root: Path | None = None) -> Iterator[None]:
    """Harden host Git calls and optionally pin them to a verified worktree."""
    raw_count = os.environ.get("GIT_CONFIG_COUNT", "0")
    if not raw_count.isdecimal():
        raise RuntimeError("GIT_CONFIG_COUNT is malformed; refusing release-gate Git")
    index = int(raw_count)
    updates = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": str(index + 2),
        f"GIT_CONFIG_KEY_{index}": "core.hooksPath",
        f"GIT_CONFIG_VALUE_{index}": os.devnull,
        f"GIT_CONFIG_KEY_{index + 1}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{index + 1}": "false",
    }
    previous = {key: os.environ.get(key) for key in updates}
    git_paths = None
    if root is not None:
        admin, _common = _linked_git_context(root)
        git_paths = {
            "GIT_DIR": str(admin),
            "GIT_WORK_TREE": str(root.resolve(strict=True)),
        }
        previous.update({key: os.environ.get(key) for key in git_paths})
    try:
        os.environ.update(updates)
        if git_paths:
            os.environ.update(git_paths)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    admin, _common = _linked_git_context(root)
    with _host_git_policy():
        environment = os.environ.copy()
        environment["GIT_DIR"] = str(admin)
        environment["GIT_WORK_TREE"] = str(root.resolve(strict=True))
        result = subprocess.run(
            ["git", *args], cwd=root, env=environment, capture_output=True,
            text=False, check=False,
        )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"gate snapshot Git command failed ({result.returncode}): "
            f"git {' '.join(args)}: {detail}"
        )
    return result


def _tracked_paths(root: Path, commit: str) -> list[bytes]:
    result = _git(root, "ls-tree", "-rz", "--name-only", commit)
    return [path for path in result.stdout.split(b"\0") if path]


def _refs_sha256(root: Path) -> str:
    refs = _git(
        root, "for-each-ref", "--format=%(refname)%00%(objectname)",
    ).stdout
    return hashlib.sha256(refs).hexdigest()


def _read_snapshot_manifest(source_root: Path) -> dict | None:
    path = source_root / _SNAPSHOT_MANIFEST
    try:
        _safe_regular_file(path)
    except FileNotFoundError:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"release-gate snapshot manifest is invalid: {path}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"release-gate snapshot manifest is not an object: {path}")
    return document


def _verify_source(root: Path) -> tuple[Path, str]:
    root = root.resolve(strict=True)
    _admin, common = _linked_git_context(root)
    marker = root / ".git"
    try:
        marker_stat = marker.lstat()
    except OSError as exc:
        raise RuntimeError(f"cannot inspect release gate source Git marker: {marker}") from exc
    if not stat.S_ISREG(marker_stat.st_mode):
        raise RuntimeError("CMRU release gate requires a linked Git worktree")

    branch = _git(root, "branch", "--show-current").stdout.decode(
        "utf-8", "replace").strip()
    head = _git(root, "rev-parse", "--verify", "HEAD").stdout.decode(
        "ascii", "replace").strip()
    if not branch:
        raise RuntimeError("CMRU release gate source must be attached to a branch")
    if not head or any(char not in "0123456789abcdef" for char in head):
        raise RuntimeError("CMRU release gate source HEAD is not a Git object id")

    status = _git(root, "status", "--porcelain=v1", "--untracked-files=no")
    if status.stdout:
        detail = status.stdout.decode("utf-8", "replace").strip().splitlines()[:8]
        raise RuntimeError(
            "release gate source has tracked changes; commit them before judging "
            f"an exact snapshot: {'; '.join(detail)}"
        )

    index = _git(root, "ls-files", "-v", "-z")
    for entry in index.stdout.split(b"\0"):
        if entry and (entry[:1] == b"S" or entry[:1].islower()):
            raise RuntimeError(
                "release gate source has an assume-unchanged or skip-worktree "
                "index entry; clear it before judging an exact snapshot"
            )

    paths = _tracked_paths(root, head)
    secret_paths = [
        os.fsdecode(path) for path in paths
        if PurePosixPath(os.fsdecode(path)).name == "cmru.secret.toml"
    ]
    if secret_paths:
        raise RuntimeError(
            "release gate source tracks a publisher-secret file; remove it from "
            f"the commit before snapshotting: {', '.join(secret_paths[:5])}"
        )
    tracked_evidence = sorted(
        os.fsdecode(path) for path in paths
        if os.fsdecode(path) in _EVIDENCE_SET
    )
    if tracked_evidence:
        raise RuntimeError(
            "release gate evidence paths must be ignored, untracked outputs; "
            "the source commit tracks: " + ", ".join(tracked_evidence[:8])
        )

    if paths:
        # Keep all path bytes NUL-delimited, including names with whitespace or
        # newlines; a configured filter may execute a command during checkout.
        with _host_git_policy():
            admin, _common = _linked_git_context(root)
            environment = os.environ.copy()
            environment["GIT_DIR"] = str(admin)
            environment["GIT_WORK_TREE"] = str(root.resolve(strict=True))
            attrs = subprocess.run(
                ["git", "check-attr", "-z", "--stdin", "filter"],
                cwd=root, env=environment, input=b"\0".join(paths) + b"\0",
                capture_output=True, check=False,
            )
        if attrs.returncode:
            detail = (attrs.stderr or attrs.stdout).decode("utf-8", "replace").strip()
            raise RuntimeError(f"cannot inspect Git filter attributes: {detail}")
        fields = attrs.stdout.split(b"\0")
        for index in range(0, len(fields) - 2, 3):
            path, attribute, value = fields[index:index + 3]
            if attribute == b"filter" and value not in (b"unspecified", b"unset"):
                raise RuntimeError(
                    "release gate source declares a Git filter that could execute "
                    f"during checkout: {os.fsdecode(path)}"
                )
    return Path(common).resolve(), head


@dataclass
class GateSnapshot:
    source_root: Path
    worktree_path: Path
    commit: str
    branch: str
    context: object
    lease_holder: str
    refs_sha256: str
    lane_exit_codes: dict[str, int | None] = field(default_factory=dict)
    lane_evidence_sha256: dict[str, dict[str, str]] = field(default_factory=dict)
    source_canary: Path | None = None
    source_canary_identity: tuple[int, int] | None = None


def open_gate_snapshot(source_root: Path) -> GateSnapshot:
    """Create or validate the stable linked worktree for one exact source HEAD."""
    api = _worktree_api()
    source_root = source_root.resolve(strict=True)
    common, head = _verify_source(source_root)
    prior_manifest = _read_snapshot_manifest(source_root)
    manifest_refs = None
    if prior_manifest and prior_manifest.get("source_commit") == head:
        candidate = prior_manifest.get("git_refs_sha256")
        if isinstance(candidate, str) and len(candidate) == 64 and all(
            char in "0123456789abcdef" for char in candidate
        ):
            manifest_refs = candidate
    identity = hashlib.sha256(
        f"{source_root}\0cmru-release-gate\0{head}".encode("utf-8")
    ).hexdigest()[:20]
    branch = f"cmru-gate-{identity}"
    target = (source_root.parent / branch).resolve()
    records = api.list_workspaces(common)
    record = next((item for item in records if item.worktree_path == target), None)
    created = record is None
    if created or not prior_manifest or prior_manifest.get("snapshot_branch") != branch:
        manifest_refs = None
    if record is None:
        if target.exists():
            raise RuntimeError(
                f"gate snapshot path exists without a workspace record: {target}; "
                "refusing to adopt or overwrite it"
            )
        with _host_git_policy():
            context = api.create_workspace(
                source_root,
                target,
                branch=branch,
                base=head,
                purpose="cmru-gate",
                labels={"cmru.purpose": "release-gate"},
                metadata={"source_commit": head, "source_root": str(source_root)},
            )
        record = api.find_workspace(common, target)
    else:
        if (
            record.purpose != "cmru-gate"
            or record.source_git_root != source_root
            or record.branch != branch
            or record.base_commit != head
            or record.metadata.get("source_commit") != head
        ):
            raise RuntimeError(
                f"existing gate snapshot identity does not match this source: {target}"
            )
        context = record.context()
    if record is None:
        raise RuntimeError(f"gate snapshot workspace record was not written: {target}")

    current_record = api.read_record(record.record_path)
    if _stale_gate_lease(current_record.lease):
        api.release_lease(current_record)
    start_ticks = _process_start_ticks(os.getpid())
    holder = (
        f"cmru-gate:{os.getpid()}:{start_ticks}:{uuid.uuid4().hex[:10]}"
        if start_ticks is not None else
        f"cmru-gate:{os.getpid()}:{uuid.uuid4().hex[:10]}"
    )
    api.acquire_lease(context, holder=holder, ttl=_LEASE_TTL)
    try:
        refs_sha256 = record.metadata.get("refs_sha256")
        if refs_sha256 is not None and (
            not isinstance(refs_sha256, str)
            or len(refs_sha256) != 64
            or any(char not in "0123456789abcdef" for char in refs_sha256)
        ):
            raise RuntimeError("CMRU gate snapshot has an invalid Git-ref digest")
        if (
            manifest_refs is not None
            and refs_sha256 is not None
            and manifest_refs != refs_sha256
        ):
            raise RuntimeError(
                "CMRU gate snapshot Git-ref digest disagrees with the host manifest"
            )
        if manifest_refs is not None:
            refs_sha256 = manifest_refs
        if created:
            # The workspace lifecycle allocates a branch with --no-checkout;
            # materialize its first tree before applying the retained-state
            # checks used for retries.
            _git(target, "reset", "--hard", head)
            refs_sha256 = _refs_sha256(target)
        else:
            # Validate retained state before trusting or deleting anything
            # left by a prior lane. A crashed or tampered checkout must be
            # inspected, not silently cleaned into a passing-looking retry.
            if refs_sha256 is None:
                # Upgrade a snapshot created before ref-integrity evidence was
                # recorded. Subsequent resumes compare against this digest.
                refs_sha256 = _refs_sha256(target)
            validate_lane_outputs(target, head, expected_refs_sha256=refs_sha256)
        # A failed prior lane may have died after writing permitted evidence.
        # Rescue only those named files before resetting its tree.
        copy_evidence_to_source(target, source_root)
        _git(target, "reset", "--hard", head)
        _git(target, "clean", "-ffdx", "-e", "/.run-gate/")
        copy_evidence_to_snapshot(source_root, target)
        _verify_snapshot(context, head)
        if _refs_sha256(target) != refs_sha256:
            raise RuntimeError("CMRU gate snapshot refs changed during admission")
        if record.metadata.get("refs_sha256") != refs_sha256:
            record = api.read_record(record.record_path)
            record = replace(record, metadata={**record.metadata, "refs_sha256": refs_sha256})
            api.write_record(record)
            context = record.context()
    except BaseException:
        api.release_lease(context)
        raise
    return GateSnapshot(source_root, target, head, branch, context, holder, refs_sha256)


def _verify_snapshot(context, commit: str) -> None:
    api = _worktree_api()
    fresh = api.read_record(context.record_path)
    if (
        fresh.worktree_path != context.worktree_path
        or fresh.git_common_dir != context.git_common_dir
        or fresh.branch != context.branch
        or fresh.base_commit != context.base_commit
        or fresh.purpose != "cmru-gate"
        or fresh.source_git_root != context.source_git_root
    ):
        raise RuntimeError("gate snapshot workspace identity changed during admission")
    admin, common = _linked_git_context(
        fresh.worktree_path, expected_common=fresh.git_common_dir,
    )
    branch = _git(fresh.worktree_path, "branch", "--show-current").stdout.decode(
        "utf-8", "replace").strip()
    head = _git(fresh.worktree_path, "rev-parse", "--verify", "HEAD").stdout.decode(
        "ascii", "replace").strip()
    if admin.parent != common / "worktrees" or branch != fresh.branch or head != commit:
        raise RuntimeError(
            f"gate snapshot is not attached to {fresh.branch} at requested commit {commit}"
        )
    source_head = _git(
        fresh.source_git_root, "rev-parse", "--verify", "HEAD",
    ).stdout.decode("ascii", "replace").strip()
    if source_head != commit:
        raise RuntimeError(
            f"release-gate source HEAD changed during the run: expected {commit}, "
            f"found {source_head}"
        )
    source_tree = _git(fresh.source_git_root, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
    snapshot_tree = _git(fresh.worktree_path, "rev-parse", "HEAD^{tree}").stdout.strip()
    if source_tree != snapshot_tree:
        raise RuntimeError("gate snapshot tree differs from the exact source commit")


def assert_source_head(snapshot: GateSnapshot) -> None:
    """Refuse evidence if the caller checkout moved after snapshot admission."""
    _linked_git_context(
        snapshot.source_root,
        expected_common=snapshot.context.git_common_dir,
    )
    source_head = _git(
        snapshot.source_root, "rev-parse", "--verify", "HEAD",
    ).stdout.decode("ascii", "replace").strip()
    if source_head != snapshot.commit:
        raise RuntimeError(
            f"release-gate source HEAD changed during the run: "
            f"expected {snapshot.commit}, found {source_head}"
        )


def _safe_regular_file(path: Path, *, max_bytes: int = _MAX_EVIDENCE_BYTES) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RuntimeError(f"gate evidence is not a private regular file: {path}")
    if metadata.st_size > max_bytes:
        raise RuntimeError(f"gate evidence exceeds the size limit: {path}")
    return metadata


def _sha256_file(path: Path) -> str:
    _safe_regular_file(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_evidence_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in _LANE_EVIDENCE_FILES:
        path = root / relative
        try:
            hashes[relative] = _sha256_file(path)
        except FileNotFoundError:
            continue
    return hashes


def _ensure_real_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"gate evidence directory is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"gate evidence parent is not a real directory: {path}")


def _copy_file(source: Path, destination: Path) -> None:
    try:
        source_metadata = _safe_regular_file(source)
    except FileNotFoundError:
        return
    _ensure_real_directory(destination.parent, create=True)
    try:
        destination_metadata = _safe_regular_file(destination)
    except FileNotFoundError:
        destination_metadata = None
    if destination_metadata is not None and not stat.S_ISREG(destination_metadata.st_mode):
        raise RuntimeError(f"gate evidence destination is not a regular file: {destination}")

    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    temporary: Path | None = None
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino) != (source_metadata.st_dev, source_metadata.st_ino):
            raise RuntimeError(f"gate evidence changed while being opened: {source}")
        with os.fdopen(source_fd, "rb", closefd=False) as stream:
            data = stream.read(_MAX_EVIDENCE_BYTES + 1)
        if len(data) > _MAX_EVIDENCE_BYTES:
            raise RuntimeError(f"gate evidence exceeds the size limit: {source}")
        descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        temporary = None
    finally:
        os.close(source_fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def copy_evidence_to_source(snapshot_root: Path, source_root: Path) -> None:
    """Copy the fixed CMRU lane evidence set from snapshot into its owner tree."""
    for relative in _LANE_EVIDENCE_FILES:
        _copy_file(snapshot_root / relative, source_root / relative)


def copy_evidence_to_snapshot(
        source_root: Path, snapshot_root: Path, *, lane: str | None = None) -> None:
    """Seed the manifest and, only for R2, the mutation resume inputs."""
    files = (_SNAPSHOT_MANIFEST,)
    if lane == "mutation":
        files += _MUTATION_RESUME_FILES
    for relative in files:
        _copy_file(source_root / relative, snapshot_root / relative)


def _generated_cache_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if any(part in _CACHE_PARTS for part in parts):
        return True
    return any(
        part.endswith((".egg-info", ".dist-info"))
        or part == ".coverage" or part.startswith(".coverage.")
        or part.endswith((".pyc", ".pyo"))
        for part in parts
    )


def _validate_run_gate_state(root: Path) -> None:
    state = root / ".run-gate"
    try:
        metadata = state.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"snapshot Run-Gate state is not a real directory: {state}")
    def failed(error: OSError) -> None:
        raise RuntimeError(f"cannot inspect snapshot Run-Gate state: {error}") from error

    for current, directories, files in os.walk(state, followlinks=False, onerror=failed):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                raise RuntimeError(f"snapshot Run-Gate state contains a symlink: {child}")
        for name in files:
            if name == "cmru.secret.toml":
                raise RuntimeError(
                    "snapshot Run-Gate state contains a publisher secret: "
                    f"{current_path / name}"
                )
            child = current_path / name
            item = _safe_regular_file(child, max_bytes=_MAX_STATE_FILE_BYTES)
            if item.st_size > _MAX_STATE_FILE_BYTES:
                raise RuntimeError(f"snapshot Run-Gate state file is too large: {child}")


def validate_lane_outputs(
        snapshot_root: Path, commit: str, *,
        expected_refs_sha256: str | None = None) -> None:
    """Reject lane modifications and ignored outputs outside the state contract."""
    api = _worktree_api()
    _admin, common = _linked_git_context(snapshot_root)
    record = api.find_workspace(common, snapshot_root)
    if record is None or record.purpose != "cmru-gate":
        raise RuntimeError(f"gate snapshot has no CMRU workspace record: {snapshot_root}")
    _verify_snapshot(record.context(), commit)
    if expected_refs_sha256 is not None and _refs_sha256(snapshot_root) != expected_refs_sha256:
        raise RuntimeError("a release gate lane changed shared Git refs")
    changed = _git(snapshot_root, "status", "--porcelain=v1", "--untracked-files=no")
    if changed.stdout:
        raise RuntimeError("a release gate lane changed tracked snapshot files or HEAD")
    untracked = _git(
        snapshot_root, "ls-files", "--others", "--exclude-standard", "-z",
    ).stdout.split(b"\0")
    ignored = _git(
        snapshot_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
    ).stdout.split(b"\0")
    unexpected: list[str] = []
    for raw in set(untracked) | set(ignored):
        if not raw:
            continue
        relative = os.fsdecode(raw)
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).name == "cmru.secret.toml":
            unexpected.append(relative)
            continue
        if parts and parts[0] == ".run-gate":
            continue
        if relative in _EVIDENCE_SET or _generated_cache_path(relative):
            continue
        unexpected.append(relative)
    _validate_run_gate_state(snapshot_root)
    if unexpected:
        raise RuntimeError(
            "release gate lane left unapproved ignored/untracked files in its "
            f"snapshot: {', '.join(unexpected[:8])}"
        )


def prepare_lane(snapshot: GateSnapshot, lane: str) -> None:
    """Recover evidence, restore exact HEAD, clear lane residue, then reseed."""
    validate_lane_outputs(
        snapshot.worktree_path, snapshot.commit,
        expected_refs_sha256=snapshot.refs_sha256,
    )
    copy_evidence_to_source(snapshot.worktree_path, snapshot.source_root)
    _git(snapshot.worktree_path, "reset", "--hard", snapshot.commit)
    _git(snapshot.worktree_path, "clean", "-ffdx", "-e", "/.run-gate/")
    copy_evidence_to_snapshot(snapshot.source_root, snapshot.worktree_path, lane=lane)
    _verify_snapshot(snapshot.context, snapshot.commit)


def finish_lane(snapshot: GateSnapshot, lane: str) -> None:
    """Retain only Run-Gate state and named evidence between aggregate lanes."""
    validate_lane_outputs(
        snapshot.worktree_path, snapshot.commit,
        expected_refs_sha256=snapshot.refs_sha256,
    )
    copy_evidence_to_source(snapshot.worktree_path, snapshot.source_root)
    snapshot.lane_evidence_sha256[lane] = _existing_evidence_hashes(snapshot.worktree_path)
    _git(snapshot.worktree_path, "clean", "-ffdx", "-e", "/.run-gate/")
    copy_evidence_to_snapshot(snapshot.source_root, snapshot.worktree_path, lane=lane)


def write_snapshot_manifest(snapshot: GateSnapshot, state: str) -> None:
    path = snapshot.source_root / "cmru" / ".assay" / "release-gate-snapshot.json"
    _ensure_real_directory(path.parent, create=True)
    try:
        previous = _safe_regular_file(path)
    except FileNotFoundError:
        previous = None
    del previous
    record = {
        "schema_version": 1,
        "source_commit": snapshot.commit,
        "snapshot_branch": snapshot.branch,
        "snapshot_path": str(snapshot.worktree_path),
        "source_root": str(snapshot.source_root),
        "git_refs_sha256": snapshot.refs_sha256,
        "lane_exit_codes": snapshot.lane_exit_codes,
        "lane_evidence_sha256": snapshot.lane_evidence_sha256,
        "source_canary": (
            str(snapshot.source_canary.relative_to(snapshot.source_root))
            if snapshot.source_canary is not None else None
        ),
        "state": state,
    }
    payload = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=".release-gate-snapshot.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(descriptor)
        os.replace(temporary, path)
        temporary = None
    finally:
        os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def plant_source_canary(snapshot: GateSnapshot) -> None:
    """Plant a random host-only file so a live lane proves it is not mounted."""
    path = snapshot.source_root / f".cmru-gate-canary-{uuid.uuid4().hex}.txt"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = f"CMRU-GATE-SOURCE-CANARY-{uuid.uuid4().hex}\n".encode("ascii")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    snapshot.source_canary = path
    snapshot.source_canary_identity = (metadata.st_dev, metadata.st_ino)


def remove_source_canary(snapshot: GateSnapshot) -> None:
    """Remove only the exact canary inode planted by this gate invocation."""
    if snapshot.source_canary is None:
        return
    try:
        metadata = snapshot.source_canary.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (metadata.st_dev, metadata.st_ino) != snapshot.source_canary_identity
    ):
        raise RuntimeError(
            "release-gate source canary path changed during the live lane probe; "
            f"preserving the replacement at {snapshot.source_canary}"
        )
    snapshot.source_canary.unlink()


def close_gate_snapshot(snapshot: GateSnapshot) -> None:
    """Release the snapshot lease while retaining it for retry or cleanup."""
    _worktree_api().release_lease(snapshot.context)


def _snapshot_branch(source_root: Path, commit: str) -> str:
    identity = hashlib.sha256(
        f"{source_root}\0cmru-release-gate\0{commit}".encode("utf-8")
    ).hexdigest()[:20]
    return f"cmru-gate-{identity}"


def _validate_gate_snapshot_record(record, expected_common: Path) -> None:
    """Prove the record still names the snapshot created for its source HEAD."""
    source_root = record.source_git_root
    expected_branch = _snapshot_branch(source_root, record.base_commit)
    expected_path = (source_root.parent / expected_branch).resolve(strict=False)
    if (
        record.purpose != "cmru-gate"
        or record.git_common_dir != expected_common
        or record.branch != expected_branch
        or record.worktree_path != expected_path
        or len(record.base_commit) not in (40, 64)
        or any(char not in "0123456789abcdef" for char in record.base_commit)
    ):
        raise RuntimeError(
            f"CMRU gate snapshot record does not match its source identity: "
            f"{record.record_path}"
        )
    try:
        metadata = record.worktree_path.lstat()
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect CMRU gate snapshot {record.worktree_path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(
            f"CMRU gate snapshot is not a real directory: {record.worktree_path}"
        )
    admin, common = _linked_git_context(
        record.worktree_path, expected_common=expected_common,
    )
    branch = _git(record.worktree_path, "branch", "--show-current").stdout.decode(
        "utf-8", "replace",
    ).strip()
    head = _git(
        record.worktree_path, "rev-parse", "--verify", "HEAD",
    ).stdout.decode("ascii", "replace").strip()
    if admin.parent != common / "worktrees" or branch != record.branch or head != record.base_commit:
        raise RuntimeError(
            f"CMRU gate snapshot no longer matches its recorded branch and HEAD: "
            f"{record.worktree_path}"
        )


def _snapshot_source_is_stale(record, expected_common: Path) -> bool:
    source_root = record.source_git_root
    try:
        metadata = source_root.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise RuntimeError(f"cannot inspect CMRU gate source {source_root}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(
            f"CMRU gate source is present but not a real directory: {source_root}"
        )
    _linked_git_context(source_root, expected_common=expected_common)
    branch = _git(source_root, "branch", "--show-current").stdout.decode(
        "utf-8", "replace",
    ).strip()
    if not branch:
        return True
    head = _git(source_root, "rev-parse", "--verify", "HEAD").stdout.decode(
        "ascii", "replace",
    ).strip()
    return head != record.base_commit


def _gate_lease_is_live(lease) -> bool:
    if lease is None:
        return False
    if lease.holder.startswith("cmru-gate:"):
        fields = lease.holder.split(":")
        if len(fields) in (3, 4) and fields[1].isdecimal():
            pid = int(fields[1])
            start_ticks = _process_start_ticks(pid)
            if start_ticks is not None:
                return len(fields) == 3 or fields[2] == start_ticks
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except (PermissionError, OSError):
                return True
    expiry = datetime.fromisoformat(
        lease.expires_at_utc[:-1] + "+00:00"
        if lease.expires_at_utc.endswith("Z") else lease.expires_at_utc
    )
    return expiry > datetime.now(timezone.utc)


def _snapshot_assay_state_dir(record) -> Path:
    repo_root = record.git_common_dir.parent.resolve()
    project_root = (record.worktree_path / "cmru").resolve(strict=False)
    state_root = repo_root / ".run-gate"
    try:
        relative = project_root.relative_to(repo_root)
    except ValueError:
        identity = hashlib.sha256(os.fsencode(str(project_root))).hexdigest()
        return state_root / "assay-state-external" / identity
    return state_root / "assay-state" / relative


def _remove_snapshot_assay_state(record) -> None:
    """Remove only the dedicated Run-Gate state tree for this stale snapshot."""
    state_root = record.git_common_dir.parent / ".run-gate"
    state_dir = _snapshot_assay_state_dir(record)
    try:
        relative = state_dir.relative_to(state_root)
    except ValueError as exc:
        raise RuntimeError(f"snapshot Assay state escapes {state_root}: {state_dir}") from exc
    try:
        metadata = state_root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"cannot inspect Run-Gate state root {state_root}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Run-Gate state root is not a real directory: {state_root}")
    current = state_root
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            child = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(f"cannot inspect snapshot Assay state {current}: {exc}") from exc
        if not stat.S_ISDIR(child.st_mode):
            raise RuntimeError(f"snapshot Assay state path is not a real directory: {current}")
        if index == len(relative.parts) - 1:
            shutil.rmtree(current)


def stale_gate_snapshot_records(executor_git_root: Path | str) -> list[object]:
    """List stale, inactive CMRU gate sidecars without changing repository state."""
    api = _worktree_api()
    executor = Path(executor_git_root).resolve(strict=True)
    with _host_git_policy():
        _top, common, _branch, _head = api.discover_git_context(executor)
        stale = []
        for record in api.list_workspaces(common):
            if record.purpose != "cmru-gate":
                continue
            _validate_gate_snapshot_record(record, common)
            if not _snapshot_source_is_stale(record, common):
                continue
            if _gate_lease_is_live(record.lease):
                continue
            stale.append(record)
        return stale


def remove_stale_gate_snapshot(
        record_path: Path | str, *, executor_git_root: Path | str) -> None:
    """Revalidate and remove one confirmed stale CMRU snapshot and its state."""
    api = _worktree_api()
    executor = Path(executor_git_root).resolve(strict=True)
    with _host_git_policy():
        _top, common, _branch, _head = api.discover_git_context(executor)
        record = api.read_record(record_path)
        _validate_gate_snapshot_record(record, common)
        if not _snapshot_source_is_stale(record, common):
            raise RuntimeError(
                f"CMRU gate snapshot source is current; refusing cleanup: {record.source_git_root}"
            )
        if _gate_lease_is_live(record.lease):
            raise RuntimeError(
                f"CMRU gate snapshot has an active lease: {record.worktree_path}"
            )
        if _stale_gate_lease(record.lease):
            record = api.release_lease(record)
        _remove_snapshot_assay_state(record)
        if record.source_git_root.exists():
            api.remove_workspace(record)
        else:
            api.remove_orphan_workspace(record, executor_git_root=executor)


def remove_gate_snapshots_for_source(source_root: Path) -> None:
    """Remove only CMRU gate snapshots owned by this exact source worktree."""
    api = _worktree_api()
    source_root = source_root.resolve(strict=True)
    _admin, common = _linked_git_context(source_root)
    records = api.list_workspaces(common)
    for record in records:
        if record.purpose != "cmru-gate" or record.source_git_root != source_root:
            continue
        if (
            record.worktree_path.parent != source_root.parent
            or not record.worktree_path.name.startswith("cmru-gate-")
            or not record.branch.startswith("cmru-gate-")
        ):
            raise RuntimeError(
                f"CMRU gate workspace record is outside its owned snapshot location: "
                f"{record.record_path}"
            )
        with _host_git_policy():
            _validate_gate_snapshot_record(record, common)
            if _gate_lease_is_live(record.lease):
                raise RuntimeError(
                    f"CMRU gate snapshot has an active lease: {record.worktree_path}"
                )
            if _stale_gate_lease(record.lease):
                record = api.release_lease(record)
            _remove_snapshot_assay_state(record)
            api.remove_workspace(record)

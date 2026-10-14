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
import re
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
# Run-Gate keeps resume state at the judged tree's root AND a per-project store
# (history + locks) beside the project; both are ignored, validated, and kept
# between lanes. Nothing else a lane leaves behind is.
_RUN_GATE_STATE_DIRS = (".run-gate", "cmru/.run-gate")
_CLEAN_KEEP = tuple(
    argument for state in _RUN_GATE_STATE_DIRS for argument in ("-e", f"/{state}/")
)
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
    # The command name (field 2) may contain spaces and parentheses; the last ")"
    # ends it, and field 3 (state) is then index zero.
    fields = raw[raw.rfind(")") + 2:].split()
    return fields[19] if len(fields) > 19 else None


def _local_namespace_identity() -> str | None:
    """Identify this PID namespace and boot so a holder's PID can be interpreted.

    A PID is meaningful only inside the namespace that issued it. The pair
    (PID-namespace inode, boot id) lets a later process decide whether
    "this PID is absent" is evidence about the holder or merely about where it
    is looking.
    """
    try:
        link = os.readlink("/proc/self/ns/pid")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii")
    except OSError:
        return None
    match = re.fullmatch(r"pid:\[(\d+)\]", link)
    boot_id = boot.strip().replace("-", "")
    if match is None or not boot_id.isalnum():
        return None
    return f"{match.group(1)}:{boot_id}"


def _new_lease_holder() -> str:
    """Name this process as a gate holder, with the facts needed to judge it later."""
    nonce = uuid.uuid4().hex[:10]
    start_ticks = _process_start_ticks(os.getpid())
    identity = _local_namespace_identity()
    if start_ticks is None or identity is None:
        # Unprovable holder: every later liveness question is "indeterminate".
        return f"cmru-gate:{os.getpid()}:{nonce}"
    return f"cmru-gate:{os.getpid()}:{start_ticks}:{nonce}:{identity}"


LEASE_LIVE = "live"
LEASE_DEAD = "dead"
LEASE_INDETERMINATE = "indeterminate"


def gate_lease_state(lease, *, now: datetime | None = None) -> str:
    """Classify a snapshot lease as live, dead, or indeterminate.

    ``dead`` needs POSITIVE evidence: no lease, an expired lease whose holder is
    not a CMRU gate, or a CMRU holder from THIS PID namespace whose process is
    gone. Everything else that is not provably alive is ``indeterminate``. An
    unexpired lease is never dead unless its same-namespace process is provably
    gone: the TTL is a floor under every other claim, so a PID that is merely
    absent from this namespace (another container, a host path) cannot release it.
    """
    if lease is None:
        return LEASE_DEAD
    if lease.expires_at_utc is None:
        return LEASE_LIVE  # perpetual: an explicit operator-held lease
    expiry = datetime.fromisoformat(
        lease.expires_at_utc[:-1] + "+00:00"
        if lease.expires_at_utc.endswith("Z") else lease.expires_at_utc
    )
    expired = expiry <= (now or datetime.now(timezone.utc))
    fields = lease.holder.split(":")
    if fields[0] != "cmru-gate":
        return LEASE_DEAD if expired else LEASE_LIVE
    foreign = LEASE_INDETERMINATE if expired else LEASE_LIVE
    if len(fields) != 6 or not fields[1].isdecimal():
        return foreign  # legacy or unprovable holder format
    if f"{fields[4]}:{fields[5]}" != _local_namespace_identity():
        return foreign  # issued in another PID namespace or boot
    pid = int(fields[1])
    start_ticks = _process_start_ticks(pid)
    if start_ticks is not None:
        return LEASE_LIVE if start_ticks == fields[2] else LEASE_DEAD
    if Path(f"/proc/{pid}").exists():
        return LEASE_INDETERMINATE  # present but unreadable: cannot judge
    return LEASE_DEAD


def _worktree_api():
    # Release wheels carry `worktree` through CMRU's package-dir mapping; a
    # source run puts libraries/worktree/src on sys.path (the release gate's
    # `_load_components` does, and so do the registered lanes).
    import worktree
    return worktree


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
def host_git_hooks_disabled(root: Path) -> Iterator[None]:
    """Harden host Git calls and pin them to a verified linked worktree."""
    admin, _common = _linked_git_context(root)
    pins = {"GIT_DIR": str(admin), "GIT_WORK_TREE": str(root.resolve(strict=True))}
    previous = {key: os.environ.get(key) for key in pins}
    with _host_git_policy():
        try:
            os.environ.update(pins)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _git(root: Path, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
    admin, _common = _linked_git_context(root)
    with _host_git_policy():
        environment = os.environ.copy()
        environment["GIT_DIR"] = str(admin)
        environment["GIT_WORK_TREE"] = str(root.resolve(strict=True))
        result = subprocess.run(
            ["git", *args], cwd=root, env=environment, capture_output=True,
            text=False, check=False, input=input,
        )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"gate snapshot Git command failed ({result.returncode}): "
            f"git {' '.join(args)}: {detail}"
        )
    return result


def _tracked_paths(root: Path, commit: str) -> list[bytes]:
    result = _git(root, "ls-tree", "-rz", "--name-only", commit)
    return [path for path in result.stdout.split(b"\0") if path]


def _verify_tracked_files_match_commit(root: Path, commit: str) -> None:
    """Compare working-tree bytes and modes directly with the judged commit.

    ``git status`` is not an independent oracle: an adversarial lane can set
    ``assume-unchanged`` on a file and hide its subsequent edits from status.
    Hash each tracked file as a Git blob and compare it with the commit tree.
    """
    tree = _git(root, "ls-tree", "-rz", "--full-tree", commit).stdout
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        header, separator, raw_path = entry.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("cannot parse the exact release snapshot tree")
        raw_mode, object_type, expected_oid = fields
        relative = os.fsdecode(raw_path)
        parts = PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
            raise RuntimeError(f"release snapshot contains an unsafe tracked path: {relative!r}")
        path = root.joinpath(*parts)
        parent = root
        for component in parts[:-1]:
            parent = parent / component
            try:
                parent_mode = parent.lstat().st_mode
            except OSError as exc:
                raise RuntimeError(
                    f"tracked snapshot path is missing: {relative} ({exc})"
                ) from exc
            if not stat.S_ISDIR(parent_mode):
                raise RuntimeError(
                    f"tracked snapshot path has a non-directory parent: {relative}"
                )
        try:
            file_stat = path.lstat()
        except OSError as exc:
            raise RuntimeError(f"tracked snapshot file is missing: {relative}") from exc

        if raw_mode == b"120000" and object_type == b"blob":
            if not stat.S_ISLNK(file_stat.st_mode):
                raise RuntimeError(f"tracked snapshot symlink changed type: {relative}")
            payload = os.fsencode(os.readlink(path))
            size = len(payload)
            chunks = (payload,)
        elif raw_mode in (b"100644", b"100755") and object_type == b"blob":
            if not stat.S_ISREG(file_stat.st_mode):
                raise RuntimeError(f"tracked snapshot file changed type: {relative}")
            expected_executable = raw_mode == b"100755"
            actual_executable = bool(file_stat.st_mode & 0o111)
            if actual_executable != expected_executable:
                raise RuntimeError(f"tracked snapshot file mode changed: {relative}")
            size = file_stat.st_size

            def file_chunks():
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        yield chunk

            chunks = file_chunks()
        elif raw_mode == b"160000" and object_type == b"commit":
            nested_result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            if nested_result.returncode != 0:
                raise RuntimeError(f"cannot inspect tracked snapshot submodule: {relative}")
            nested = nested_result.stdout.strip()
            if nested.encode("ascii") != expected_oid:
                raise RuntimeError(f"tracked snapshot submodule changed: {relative}")
            continue
        else:
            raise RuntimeError(
                f"unsupported tracked snapshot entry {raw_mode.decode()} "
                f"{object_type.decode()} {relative}"
            )

        algorithm = "sha1" if len(expected_oid) == 40 else "sha256"
        digest = hashlib.new(algorithm)
        digest.update(f"blob {size}\0".encode("ascii"))
        for chunk in chunks:
            digest.update(chunk)
        if digest.hexdigest().encode("ascii") != expected_oid:
            raise RuntimeError(
                f"tracked snapshot file differs from exact commit {commit[:12]}: "
                f"{relative}"
            )


_REFS_SCOPE = "protected-refs/1"


def protected_ref_patterns(snapshot_branch: str, source_branch: str) -> tuple[str, ...]:
    """Name the refs only this gate may legitimately move.

    The shared repository holds hundreds of refs that unrelated agents commit
    to and fetch into while a 14-hour gate runs; digesting all of them reports
    ordinary estate activity as tester tampering. A tester can plausibly
    reach, and the verdict depends on, exactly these: the snapshot's own
    branch, the source branch the gate is judging, and the gate-owned
    ``refs/cmru-gate/<id>/`` namespace that carries the live detector probe.
    """
    identity = snapshot_branch.removeprefix("cmru-gate-")
    return (
        f"refs/heads/{snapshot_branch}",
        f"refs/heads/{source_branch}",
        f"refs/cmru-gate/{identity}",
    )


def _protected_refs_sha256(root: Path, patterns: tuple[str, ...]) -> str:
    refs = _git(
        root, "for-each-ref", "--format=%(refname)%00%(objectname)", *patterns,
    ).stdout
    return hashlib.sha256(refs).hexdigest()


def _anchor_ref_values(root: Path) -> dict[str, str]:
    """Capture pre-existing release tags and main for lane-time integrity checks.

    New tags may be created by an unrelated release while a long gate runs, so
    they are deliberately not part of the expected set. Existing tags are
    immutable. ``main`` may advance normally, but must remain a fast-forward.
    """
    result = _git(
        root, "for-each-ref", "--format=%(refname)%00%(objectname)",
        "refs/heads/main", "refs/tags",
    )
    anchors: dict[str, str] = {}
    for line in result.stdout.splitlines():
        ref_raw, separator, oid_raw = line.partition(b"\0")
        if not separator or not ref_raw or not oid_raw:
            raise RuntimeError("Git returned a malformed main/tag ref record")
        ref = ref_raw.decode("utf-8", "strict")
        oid = oid_raw.decode("ascii", "strict")
        if ref != "refs/heads/main" and not ref.startswith("refs/tags/"):
            raise RuntimeError(f"Git returned an out-of-scope release anchor ref: {ref}")
        if len(oid) not in (40, 64) or any(char not in "0123456789abcdef" for char in oid):
            raise RuntimeError(f"Git returned an invalid object id for release anchor {ref}")
        anchors[ref] = oid
    return dict(sorted(anchors.items()))


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    admin, _common = _linked_git_context(root)
    environment = os.environ.copy()
    environment["GIT_DIR"] = str(admin)
    environment["GIT_WORK_TREE"] = str(root.resolve(strict=True))
    with _host_git_policy():
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
    raise RuntimeError(f"cannot verify main fast-forward ancestry: {detail}")


def _verify_anchor_refs(root: Path, expected: dict[str, str]) -> dict[str, str]:
    current = _anchor_ref_values(root)
    for ref, oid in expected.items():
        actual = current.get(ref)
        if ref == "refs/heads/main":
            if actual is None or not _git_is_ancestor(root, oid, actual):
                raise RuntimeError(
                    "refs/heads/main changed to a non-fast-forward value during "
                    "release-gate execution"
                )
        elif actual != oid:
            raise RuntimeError(
                f"protected release ref {ref} changed during release-gate execution"
            )
    # Make a fast-forward main tip the next checkpoint. Comparing every lane
    # only with the initial tip would miss a lane that rewound main back to that
    # old tip after an earlier lane had advanced it.
    return current


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


def _verify_source(root: Path) -> tuple[Path, str, str]:
    root = root.resolve(strict=True)
    # A main checkout has a `.git` directory, which this refuses: the gate
    # requires a LINKED worktree so its sidecar can attach to the same family.
    _admin, common = _linked_git_context(root)

    branch = _git(root, "branch", "--show-current").stdout.decode(
        "utf-8", "replace").strip()
    head = _git(root, "rev-parse", "--verify", "HEAD").stdout.decode(
        "ascii", "replace").strip()
    if not branch:
        raise RuntimeError("CMRU release gate source must be attached to a branch")

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

    # Keep all path bytes NUL-delimited, including names with whitespace or
    # newlines; a configured filter may execute a command during checkout.
    attrs = _git(
        root, "check-attr", "-z", "--stdin", "filter",
        input=b"".join(path + b"\0" for path in paths),
    )
    fields = attrs.stdout.split(b"\0")
    for index in range(0, len(fields) - 2, 3):
        path, attribute, value = fields[index:index + 3]
        if value not in (b"unspecified", b"unset"):
            raise RuntimeError(
                "release gate source declares a Git filter that could execute "
                f"during checkout: {os.fsdecode(path)}"
            )
    return Path(common).resolve(), head, branch


@dataclass
class GateSnapshot:
    source_root: Path
    worktree_path: Path
    commit: str
    branch: str
    context: object
    lease_holder: str
    refs_sha256: str
    source_branch: str
    anchor_refs: dict[str, str] = field(default_factory=dict)
    run_gate_identity: dict[str, object] = field(default_factory=dict)
    lane_exit_codes: dict[str, int | None] = field(default_factory=dict)
    lane_evidence_sha256: dict[str, dict[str, str]] = field(default_factory=dict)
    source_canary: Path | None = None
    source_canary_identity: tuple[int, int] | None = None

    @property
    def protected_refs(self) -> tuple[str, ...]:
        return protected_ref_patterns(self.branch, self.source_branch)


def open_gate_snapshot(source_root: Path) -> GateSnapshot:
    """Create or validate the stable linked worktree for one exact source HEAD."""
    api = _worktree_api()
    source_root = source_root.resolve(strict=True)
    common, head, source_branch = _verify_source(source_root)
    source_anchors = _anchor_ref_values(source_root)
    if "refs/heads/main" not in source_anchors:
        raise RuntimeError(
            "CMRU release gate requires a local refs/heads/main anchor; "
            "fetch or create the repository's main branch before starting"
        )
    prior_manifest = _read_snapshot_manifest(source_root)
    manifest_refs = None
    if (
        prior_manifest
        and prior_manifest.get("source_commit") == head
        and prior_manifest.get("refs_scope") == _REFS_SCOPE
    ):
        candidate = prior_manifest.get("git_refs_sha256")
        if isinstance(candidate, str) and len(candidate) == 64 and all(
            char in "0123456789abcdef" for char in candidate
        ):
            manifest_refs = candidate
    identity = hashlib.sha256(
        f"{source_root}\0cmru-release-gate\0{head}".encode("utf-8")
    ).hexdigest()[:20]
    branch = f"cmru-gate-{identity}"
    manifest_anchors = None
    manifest_judge: dict[str, object] = {}
    if (
        prior_manifest
        and prior_manifest.get("source_commit") == head
        and prior_manifest.get("snapshot_branch") == branch
    ):
        candidate = prior_manifest.get("anchor_refs")
        if isinstance(candidate, dict) and all(
            isinstance(ref, str)
            and (ref == "refs/heads/main" or ref.startswith("refs/tags/"))
            and isinstance(oid, str)
            and len(oid) in (40, 64)
            and all(char in "0123456789abcdef" for char in oid)
            for ref, oid in candidate.items()
        ) and "refs/heads/main" in candidate:
            manifest_anchors = dict(sorted(candidate.items()))
        elif "anchor_refs" in prior_manifest:
            raise RuntimeError("CMRU gate snapshot has invalid main/tag ref anchors")
        candidate_judge = prior_manifest.get("run_gate_identity")
        candidate_keys = set(candidate_judge) if isinstance(candidate_judge, dict) else set()
        version = candidate_judge.get("version") if isinstance(candidate_judge, dict) else None
        stable_version = (
            isinstance(version, str)
            and re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+tester\.unified)?",
                version,
            ) is not None
        )
        complete_identity = (
            candidate_keys == {
                "version", "executable", "launcher_sha256", "distribution_sha256"
            }
            and isinstance(candidate_judge.get("executable"), str)
            and Path(candidate_judge["executable"]).is_absolute()
            and all(
                isinstance(candidate_judge.get(key), str)
                and re.fullmatch(r"[0-9a-f]{64}", candidate_judge[key]) is not None
                for key in ("launcher_sha256", "distribution_sha256")
            )
        )
        if stable_version and complete_identity:
            manifest_judge = dict(candidate_judge)
        else:
            raise RuntimeError(
                "existing CMRU gate snapshot has no trusted Run-Gate identity; "
                "remove the retained snapshot with `cmru gate-snapshots --remove "
                "--include-current` and start a fresh gate"
            )
    patterns = protected_ref_patterns(branch, source_branch)
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
        record = api.read_record(context.record_path)
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

    current_record = api.read_record(record.record_path)
    if gate_lease_state(current_record.lease) == LEASE_DEAD:
        api.release_lease(current_record)
    holder = _new_lease_holder()
    api.acquire_lease(context, holder=holder, ttl=_LEASE_TTL)
    try:
        if not created and manifest_anchors is None:
            raise RuntimeError(
                "existing CMRU gate snapshot has no trusted main/tag ref baseline; "
                "remove the retained snapshot with `cmru gate-snapshots --remove "
                "--include-current` and start a fresh gate"
            )
        refs_sha256 = (
            record.metadata.get("refs_sha256")
            if record.metadata.get("refs_scope") == _REFS_SCOPE else None
        )
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
            refs_sha256 = _protected_refs_sha256(target, patterns)
            # The source and sidecar share one Git ref store. Keep the baseline
            # read before allocating the sidecar so a missing main ref cannot
            # leave a newly registered orphan snapshot behind.
            anchor_refs = source_anchors
        else:
            # Validate retained state before trusting or deleting anything
            # left by a prior lane. A crashed or tampered checkout must be
            # inspected, not silently cleaned into a passing-looking retry.
            if refs_sha256 is None:
                # Upgrade a snapshot created before ref-integrity evidence was
                # recorded (or under another digest scope). Subsequent resumes
                # compare against this digest.
                refs_sha256 = _protected_refs_sha256(target, patterns)
            validate_lane_outputs(
                target, head, protected_refs=patterns,
                expected_refs_sha256=refs_sha256,
                anchor_refs=manifest_anchors,
            )
            anchor_refs = manifest_anchors
        # A failed prior lane may have died after writing permitted evidence.
        # Rescue only those named files before resetting its tree.
        copy_evidence_to_source(target, source_root)
        _git(target, "reset", "--hard", head)
        _git(target, "clean", "-ffdx", *_CLEAN_KEEP)
        copy_evidence_to_snapshot(source_root, target)
        _verify_snapshot(context, head)
        if (
            record.metadata.get("refs_sha256") != refs_sha256
            or record.metadata.get("refs_scope") != _REFS_SCOPE
        ):
            record = api.read_record(record.record_path)
            record = replace(record, metadata={
                **record.metadata, "refs_sha256": refs_sha256,
                "refs_scope": _REFS_SCOPE,
            })
            api.write_record(record)
            context = record.context()
    except BaseException:
        api.release_lease(context)
        raise
    return GateSnapshot(
        source_root, target, head, branch, context, holder, refs_sha256,
        source_branch, anchor_refs, manifest_judge,
    )


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
    metadata = path.lstat()  # FileNotFoundError is the caller's "absent" signal
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


def _ensure_real_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError(f"gate evidence parent is not a real directory: {path}")


def _copy_file(source: Path, destination: Path) -> None:
    try:
        _safe_regular_file(source)
    except FileNotFoundError:
        return
    _ensure_real_directory(destination.parent)
    with contextlib.suppress(FileNotFoundError):
        _safe_regular_file(destination)  # an existing destination must be private too

    # O_NOFOLLOW makes a link swapped in after the check above an error.
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    temporary: Path | None = None
    try:
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


def _validate_run_gate_state(state: Path) -> None:
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
            _safe_regular_file(current_path / name, max_bytes=_MAX_STATE_FILE_BYTES)


def validate_lane_outputs(
        snapshot_root: Path, commit: str, *,
        protected_refs: tuple[str, ...],
        expected_refs_sha256: str | None = None,
        anchor_refs: dict[str, str] | None = None) -> dict[str, str] | None:
    """Reject lane modifications and ignored outputs outside the state contract."""
    api = _worktree_api()
    _admin, common = _linked_git_context(snapshot_root)
    record = api.find_workspace(common, snapshot_root)
    if record is None or record.purpose != "cmru-gate":
        raise RuntimeError(f"gate snapshot has no CMRU workspace record: {snapshot_root}")
    _verify_snapshot(record.context(), commit)
    if (
        expected_refs_sha256 is not None
        and _protected_refs_sha256(snapshot_root, protected_refs) != expected_refs_sha256
    ):
        raise RuntimeError(
            "protected Git refs changed during release-gate execution "
            f"({', '.join(protected_refs)})"
        )
    index = _git(snapshot_root, "ls-files", "-v", "-z")
    for entry in index.stdout.split(b"\0"):
        if entry and (entry[:1] == b"S" or entry[:1].islower()):
            raise RuntimeError(
                "a release gate lane set an assume-unchanged or skip-worktree "
                "index flag; the judged snapshot must retain ordinary tracking"
            )
    _verify_tracked_files_match_commit(snapshot_root, commit)
    current_anchors = (
        _verify_anchor_refs(snapshot_root, anchor_refs)
        if anchor_refs is not None else None
    )
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
        if PurePosixPath(relative).name == "cmru.secret.toml":
            unexpected.append(relative)
            continue
        if any(relative.startswith(f"{state}/") for state in _RUN_GATE_STATE_DIRS):
            continue
        if relative in _EVIDENCE_SET or _generated_cache_path(relative):
            continue
        unexpected.append(relative)
    for state in _RUN_GATE_STATE_DIRS:
        _validate_run_gate_state(snapshot_root / state)
    if unexpected:
        raise RuntimeError(
            "release gate lane left unapproved ignored/untracked files in its "
            f"snapshot: {', '.join(unexpected[:8])}"
        )
    return current_anchors


def prepare_lane(snapshot: GateSnapshot, lane: str) -> None:
    """Recover evidence, restore exact HEAD, clear lane residue, then reseed."""
    current_anchors = validate_lane_outputs(
        snapshot.worktree_path, snapshot.commit,
        protected_refs=snapshot.protected_refs,
        expected_refs_sha256=snapshot.refs_sha256,
        anchor_refs=snapshot.anchor_refs,
    )
    if current_anchors is not None:
        snapshot.anchor_refs = current_anchors
    copy_evidence_to_source(snapshot.worktree_path, snapshot.source_root)
    _git(snapshot.worktree_path, "reset", "--hard", snapshot.commit)
    _git(snapshot.worktree_path, "clean", "-ffdx", *_CLEAN_KEEP)
    copy_evidence_to_snapshot(snapshot.source_root, snapshot.worktree_path, lane=lane)
    _verify_snapshot(snapshot.context, snapshot.commit)


def finish_lane(snapshot: GateSnapshot, lane: str) -> None:
    """Retain only Run-Gate state and named evidence between aggregate lanes."""
    current_anchors = validate_lane_outputs(
        snapshot.worktree_path, snapshot.commit,
        protected_refs=snapshot.protected_refs,
        expected_refs_sha256=snapshot.refs_sha256,
        anchor_refs=snapshot.anchor_refs,
    )
    if current_anchors is not None:
        snapshot.anchor_refs = current_anchors
    copy_evidence_to_source(snapshot.worktree_path, snapshot.source_root)
    snapshot.lane_evidence_sha256[lane] = _existing_evidence_hashes(snapshot.worktree_path)
    _git(snapshot.worktree_path, "clean", "-ffdx", *_CLEAN_KEEP)
    copy_evidence_to_snapshot(snapshot.source_root, snapshot.worktree_path, lane=lane)


def delete_probe_refs(snapshot: GateSnapshot) -> None:
    """Delete refs under the gate-owned probe namespace (the detector's fixture)."""
    namespace = snapshot.protected_refs[2]
    names = _git(
        snapshot.worktree_path, "for-each-ref", "--format=%(refname)", namespace,
    ).stdout.decode("utf-8").split()
    for name in names:
        _git(snapshot.worktree_path, "update-ref", "-d", name)


def verify_ref_probe_detected(snapshot: GateSnapshot) -> None:
    """Prove the protected-ref check fires on a REAL in-container ref write.

    The ref-probe lane writes a ref under the gate-owned namespace from inside
    the actual tester container. A detector that stayed silent would let any
    other lane move a protected ref unnoticed, so silence is a gate failure.
    The probe ref is always removed so the following lane starts from the
    admitted digest.
    """
    try:
        validate_lane_outputs(
            snapshot.worktree_path, snapshot.commit,
            protected_refs=snapshot.protected_refs,
            expected_refs_sha256=snapshot.refs_sha256,
            anchor_refs=snapshot.anchor_refs,
        )
    except RuntimeError as exc:
        if "changed protected Git refs" not in str(exc):
            raise
    else:
        raise RuntimeError(
            "the ref-probe lane wrote a protected Git ref from inside the tester "
            "container, but the gate did not detect it"
        )
    finally:
        delete_probe_refs(snapshot)


def write_snapshot_manifest(snapshot: GateSnapshot, state: str) -> None:
    path = snapshot.source_root / "cmru" / ".assay" / "release-gate-snapshot.json"
    _ensure_real_directory(path.parent)
    with contextlib.suppress(FileNotFoundError):
        _safe_regular_file(path)  # refuse to replace a non-regular or linked manifest
    record = {
        "schema_version": 3,
        "refreshed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": snapshot.commit,
        "snapshot_branch": snapshot.branch,
        "snapshot_path": str(snapshot.worktree_path),
        "source_root": str(snapshot.source_root),
        "refs_scope": _REFS_SCOPE,
        "protected_refs": list(snapshot.protected_refs),
        "git_refs_sha256": snapshot.refs_sha256,
        "anchor_refs": snapshot.anchor_refs,
        "run_gate_identity": snapshot.run_gate_identity,
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


SOURCE_CURRENT = "current"
SOURCE_STALE = "stale"
SOURCE_UNKNOWN = "unknown"


def _source_disposition(record, expected_common: Path, executor: Path) -> tuple[str, str, bool]:
    """Judge whether a snapshot's source checkout has moved on.

    Returns ``(disposition, reason, source_present)``. A source path this
    process cannot stat is NOT a gone source: it may live in another mount
    namespace (a host path from inside the devcontainer). Absence counts only
    with Git's own evidence: the registry no longer lists the source, or lists
    it prunable and unlocked. Anything else is ``unknown`` and blocks cleanup.
    """
    api = _worktree_api()
    source_root = record.source_git_root
    try:
        metadata = source_root.lstat()
    except FileNotFoundError:
        entry = next(
            (item for item in api.list_git_worktrees(executor)
             if item.path == source_root),
            None,
        )
        if entry is None:
            return SOURCE_STALE, "the source worktree is no longer registered with Git", False
        if entry.is_prunable and not entry.is_locked:
            return SOURCE_STALE, "Git reports the source worktree prunable", False
        return (
            SOURCE_UNKNOWN,
            "the source path is not visible here but Git still registers it as live",
            False,
        )
    except OSError as exc:
        return SOURCE_UNKNOWN, f"cannot inspect the source path {source_root}: {exc}", False
    if not stat.S_ISDIR(metadata.st_mode):
        return SOURCE_UNKNOWN, f"the source path is not a real directory: {source_root}", True
    try:
        _linked_git_context(source_root, expected_common=expected_common)
        branch = _git(source_root, "branch", "--show-current").stdout.decode(
            "utf-8", "replace",
        ).strip()
        head = _git(source_root, "rev-parse", "--verify", "HEAD").stdout.decode(
            "ascii", "replace",
        ).strip()
    except RuntimeError as exc:
        return SOURCE_UNKNOWN, f"cannot read the source worktree's Git state: {exc}", True
    if not branch:
        return SOURCE_STALE, "the source worktree is detached", True
    if head != record.base_commit:
        return SOURCE_STALE, f"the source HEAD moved to {head[:12]}", True
    return SOURCE_CURRENT, "the source is still at the snapshot's commit", True


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
    """Remove only the dedicated Run-Gate state tree for this snapshot.

    The path is derived (never read from the tree). Resolving it must change
    nothing: a symlink anywhere in the chain would redirect the deletion, so it
    refuses instead of following it.
    """
    state_dir = _snapshot_assay_state_dir(record)
    if state_dir.resolve(strict=False) != state_dir:
        raise RuntimeError(
            f"snapshot Assay state path is not a plain directory chain: {state_dir}"
        )
    if state_dir.is_dir():
        shutil.rmtree(state_dir)


REMOVABLE = "removable"
RETAINED = "retained"


@dataclass(frozen=True)
class SnapshotReport:
    """One retained gate snapshot and why cleanup may or may not touch it."""

    record: object
    status: str
    reason: str
    source_present: bool


def _judge_snapshot(record, common: Path, executor: Path, *, include_current: bool) -> SnapshotReport:
    """Decide cleanup eligibility; any indeterminate fact retains the snapshot."""

    def retained(reason: str, present: bool = True) -> SnapshotReport:
        return SnapshotReport(record, RETAINED, reason, present)

    try:
        _validate_gate_snapshot_record(record, common)
    except RuntimeError as exc:
        return retained(str(exc))
    state = gate_lease_state(record.lease)
    if state == LEASE_LIVE:
        return retained(f"a gate lease is live (holder {record.lease.holder})")
    if state == LEASE_INDETERMINATE:
        return retained(
            f"lease liveness cannot be proven from this namespace (holder "
            f"{record.lease.holder}); run cleanup where the gate ran"
        )
    disposition, reason, present = _source_disposition(record, common, executor)
    if disposition == SOURCE_UNKNOWN:
        return retained(reason, present)
    if disposition == SOURCE_CURRENT and not include_current:
        return retained(f"{reason}; a retry can resume it (use --include-current to remove)")
    return SnapshotReport(record, REMOVABLE, reason, present)


def inspect_gate_snapshots(
        executor_git_root: Path | str, *, include_current: bool = False,
) -> list[SnapshotReport]:
    """Report every CMRU gate snapshot in the repository family, read-only."""
    api = _worktree_api()
    executor = Path(executor_git_root).resolve(strict=True)
    with _host_git_policy():
        _top, common, _branch, _head = api.discover_git_context(executor)
        return [
            _judge_snapshot(record, common, executor, include_current=include_current)
            for record in api.list_workspaces(common)
            if record.purpose == "cmru-gate"
        ]


def remove_gate_snapshot(
        record_path: Path | str, *, executor_git_root: Path | str,
        include_current: bool = False) -> None:
    """Re-judge one snapshot from fresh facts and remove it only if removable."""
    api = _worktree_api()
    executor = Path(executor_git_root).resolve(strict=True)
    with _host_git_policy():
        _top, common, _branch, _head = api.discover_git_context(executor)
        record = api.read_record(record_path)
        if record.purpose != "cmru-gate":
            raise RuntimeError(f"not a CMRU gate snapshot record: {record_path}")
        report = _judge_snapshot(record, common, executor, include_current=include_current)
        if report.status != REMOVABLE:
            raise RuntimeError(
                f"CMRU gate snapshot {record.worktree_path} is retained: {report.reason}"
            )
        if record.lease is not None:
            record = api.release_lease(record)  # judged dead above
        _remove_snapshot_assay_state(record)
        if report.source_present:
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
            if gate_lease_state(record.lease) != LEASE_DEAD:
                raise RuntimeError(
                    "CMRU gate snapshot has a lease that is live or cannot be "
                    f"proven dead from this namespace: {record.worktree_path}"
                )
            if record.lease is not None:
                record = api.release_lease(record)
            _remove_snapshot_assay_state(record)
            api.remove_workspace(record)

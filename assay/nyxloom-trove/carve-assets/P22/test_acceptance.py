"""Locked P22 acceptance for bounded committed-object repository snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import socket
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path, PurePosixPath

import pytest
from jsonschema import Draft202012Validator

from assay.errors import AssayError, Outcome, ReasonCode
from assay import verify as verify_module
from assay.isolation import (
    DEFAULT_SNAPSHOT_LIMITS,
    SnapshotLimits,
    SnapshotSpec,
    prepare_snapshot,
)
from assay.verdict import load_schema
from assay.verify import verify_document

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "fixture-manifest.json").read_text(encoding="utf-8"))
GIT = shutil.which("git")
assert GIT is not None, "the real gate must provide git"
SNAPSHOT_TIMEOUT = 600.0

FIXED_IDENTITY = {
    "GIT_AUTHOR_NAME": "Assay Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@assay.invalid",
    "GIT_COMMITTER_NAME": "Assay Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@assay.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _git_env(**extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **FIXED_IDENTITY,
    }
    env.update(extra)
    return env


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> bytes:
    completed = subprocess.run(
        [GIT, "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_env(),
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"fixture git {' '.join(args)} failed ({completed.returncode}): "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


def _write(repo: Path, name: str, data: bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _fixture_repo(tmp_path: Path, *, hostile: bool = True) -> tuple[Path, str, Path]:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    literals = {
        ".gitignore": b"coverage.json\ncache/\n",
        ".gitattributes": b"filtered.txt filter=trap\n",
        "apps/p/main.py": (
            b'from pathlib import Path\nprint(Path("../../shared/input.txt").read_text())\n'
        ),
        "shared/input.txt": b"tracked sibling\n",
        "plain.bin": b"plain\x00bytes\n",
        "run.sh": b"#!/bin/sh\nexit 0\n",
        "filtered.txt": b"raw committed bytes\n",
        "unicod\N{LATIN SMALL LETTER E WITH ACUTE}\nline\\slash.txt": b"odd path\n",
    }
    for name, data in literals.items():
        _write(repo, name, data)
    (repo / "run.sh").chmod(0o755)
    (repo / "links").mkdir()
    os.symlink("../shared/input.txt", repo / "links" / "in")
    base = _commit(repo, "fixture base")
    assert base == MANIFEST["commit"]

    sentinel = tmp_path / "consumer-controlled-program-ran"
    if hostile:
        _write(repo, "shared/input.txt", b"REPLACEMENT REF BYTES\n")
        evil = _commit(repo, "evil replacement")
        _git(repo, "reset", "--hard", base)
        _git(repo, "replace", base, evil)
        _git(repo, "config", "filter.trap.smudge", f"touch {sentinel}")
        _git(repo, "config", "filter.trap.clean", f"touch {sentinel}")
        hooks = repo / ".git" / "hostile-hooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
        hook.chmod(0o755)
        _git(repo, "config", "core.hooksPath", str(hooks))

        # P20's explicit --git-dir/--work-tree pair must remain the sole
        # identity. A convenient `git -C` implementation follows this lie.
        decoy = tmp_path / "decoy-worktree"
        decoy.mkdir()
        _git(repo, "config", "core.worktree", str(decoy))

    _write(repo, "coverage.json", b'{"stale": true}\n')
    _write(repo, "cache/stale.bin", b"ignored stale cache\n")
    os.mkfifo(repo / "cache" / "ignored.fifo")
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(repo / "cache" / "ignored.socket"))
    sock.close()
    return repo, base, sentinel


def _spec(repo: Path, scratch: Path, commit: str, **changes: object) -> SnapshotSpec:
    values: dict[str, object] = {
        "repo_top": repo.resolve(),
        "commit": commit,
        "project_prefix": PurePosixPath("apps/p"),
        "scratch_root": scratch.resolve(),
        "limits": DEFAULT_SNAPSHOT_LIMITS,
    }
    values.update(changes)
    return SnapshotSpec(**values)  # type: ignore[arg-type]


def _object_store_digest(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((repo / ".git" / "objects").rglob("*")):
        if path.is_file() and not path.is_symlink():
            digest.update(path.relative_to(repo / ".git" / "objects").as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _object_inodes(repo: Path) -> set[tuple[int, int]]:
    return {
        (path.stat().st_dev, path.stat().st_ino)
        for path in (repo / ".git" / "objects").rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _snapshot_git(snapshot_root: Path, *args: str) -> str:
    completed = subprocess.run(
        [GIT, "-C", str(snapshot_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_env(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1"),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode("utf-8").strip()


def _assert_snapshot_manifest(root: Path) -> None:
    for name, expected in MANIFEST["entries"].items():
        path = root / name
        if expected["mode"] == "120000":
            assert path.is_symlink()
            target = os.readlink(path)
            assert target == expected["target"]
            raw = os.fsencode(target)
        else:
            assert path.is_file() and not path.is_symlink()
            raw = path.read_bytes()
            assert stat.S_IMODE(path.stat().st_mode) == int(expected["mode"][-3:], 8)
        assert len(raw) == expected["size"]
        assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
    for name in MANIFEST["absent_untracked"]:
        assert not (root / name).exists()
    assert not (root / "cache" / "ignored.socket").exists()


def _assert_pair(exc: AssayError, outcome: Outcome, reason: ReasonCode) -> None:
    assert exc.outcome is outcome
    assert exc.reason_code is reason


def _tight_limits(**changes: int) -> SnapshotLimits:
    values = asdict(DEFAULT_SNAPSHOT_LIMITS)
    values.update(changes)
    return SnapshotLimits(**values)


def _raw_verifier_failures(document: dict) -> list[str]:
    """Call P21's raw-document layer directly, never through the merged
    `verify_document` result that cannot distinguish a raw catch from model
    reconstruction (review disposition SB-P21-R1)."""
    failures: list[str] = []
    outcome = verify_module._check_outcome_and_reason_code(document, failures)
    verify_module._check_exit_code(document, outcome, failures)
    verify_module._check_interval_is_ordered(document, failures)
    verify_module._check_lane_resolved_group(document, failures)
    verify_module._check_claims_cover_declared_rigor(document, failures)
    verify_module._check_evidence_covers_declared_evidence(document, failures)
    verify_module._check_outcome_agrees_with_rollup(document, outcome, failures)
    verify_module._check_judgment_matches_claims(document, failures)
    verify_module._check_mutation_payload_shapes(document, failures)
    return failures


def test_limits_and_spec_refuse_values_a_caller_would_otherwise_invent(
    tmp_path: Path,
) -> None:
    values = asdict(DEFAULT_SNAPSHOT_LIMITS)
    for field in values:
        bad = dict(values)
        bad[field] = True
        with pytest.raises(ValueError, match=field):
            SnapshotLimits(**bad)
        bad[field] = 0
        with pytest.raises(ValueError, match=field):
            SnapshotLimits(**bad)

    repo, commit, _ = _fixture_repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for bad_commit in ("a" * 39, "A" * 40, "g" * 40, "HEAD"):
        with pytest.raises(ValueError, match="commit"):
            _spec(repo, scratch, bad_commit)
    for prefix in (PurePosixPath("/apps/p"), PurePosixPath("../apps/p")):
        with pytest.raises(ValueError, match="project_prefix"):
            _spec(repo, scratch, commit, project_prefix=prefix)
    spec = _spec(repo, scratch, commit)
    for timeout in (True, 0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="timeout"):
            with prepare_snapshot(spec, timeout=timeout):
                pytest.fail("an invalid timeout must not yield")


def test_nested_hostile_repository_is_exact_private_clean_and_independent(
    tmp_path: Path,
) -> None:
    repo, commit, sentinel = _fixture_repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    before = _object_store_digest(repo)
    source_inodes = _object_inodes(repo)

    with prepare_snapshot(
        _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
    ) as prepared:
        assert prepared.spec.commit == commit
        assert prepared.read_regular_file(
            PurePosixPath("shared/input.txt"), timeout=SNAPSHOT_TIMEOUT
        ) == b"tracked sibling\n"
        with prepared.materialize(timeout=SNAPSHOT_TIMEOUT) as first, prepared.materialize(
            timeout=SNAPSHOT_TIMEOUT
        ) as second:
            assert first.commit == second.commit == commit
            assert first.project_root == first.root / "apps" / "p"
            _assert_snapshot_manifest(first.root)
            _assert_snapshot_manifest(second.root)
            assert _snapshot_git(first.root, "rev-parse", "HEAD") == commit
            assert _snapshot_git(first.root, "status", "--porcelain=v1") == ""
            assert _snapshot_git(first.root, "rev-list", "--objects", "HEAD")

            first_objects = _object_inodes(first.root)
            second_objects = _object_inodes(second.root)
            assert first_objects.isdisjoint(source_inodes)
            assert second_objects.isdisjoint(source_inodes)
            assert first_objects.isdisjoint(second_objects)
            assert not (first.root / ".git" / "objects" / "info" / "alternates").exists()
            assert not any((first.root / ".git" / "hooks").glob("*"))
            config = (first.root / ".git" / "config").read_text(encoding="utf-8")
            assert str(repo) not in config
            assert "filter.trap" not in config
            (first.root / "shared" / "input.txt").write_bytes(b"first changed\n")
            assert (second.root / "shared" / "input.txt").read_bytes() == b"tracked sibling\n"

    assert _object_store_digest(repo) == before
    assert not sentinel.exists()
    assert list(scratch.iterdir()) == []


def test_materialization_is_safe_for_parallel_repeated_units(tmp_path: Path) -> None:
    repo, commit, _ = _fixture_repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with prepare_snapshot(
        _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
    ) as prepared:
        def consume(index: int) -> tuple[str, bytes]:
            with prepared.materialize(timeout=SNAPSHOT_TIMEOUT) as snapshot:
                (snapshot.root / f"unit-{index}.tmp").write_bytes(b"private")
                return snapshot.commit, (snapshot.root / "shared/input.txt").read_bytes()

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(consume, range(6)))
        assert results == [(commit, b"tracked sibling\n")] * 6
    assert list(scratch.iterdir()) == []


def test_prepared_seed_never_reopens_the_source_repository(tmp_path: Path) -> None:
    repo, commit, _ = _fixture_repo(tmp_path, hostile=False)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with prepare_snapshot(
        _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
    ) as prepared:
        # This is a disposable fixture repository. Making its Git directory
        # unavailable after preparation proves every later base/replacement
        # materialization is served from the private seed, not from a hidden
        # source alternate or a convenient second source read.
        (repo / ".git").rename(repo / ".git-unavailable")
        with prepared.materialize(timeout=SNAPSHOT_TIMEOUT) as snapshot:
            assert snapshot.commit == commit
            _assert_snapshot_manifest(snapshot.root)
        with prepared.materialize_replacement(
            path=PurePosixPath("shared/input.txt"),
            expected=b"tracked sibling\n",
            replacement=b"changed after source vanished\n",
            timeout=SNAPSHOT_TIMEOUT,
        ) as changed:
            assert changed.commit != commit
            assert (changed.root / "shared/input.txt").read_bytes() == b"changed after source vanished\n"


def test_linked_worktree_gitfile_resolves_the_common_object_store(
    tmp_path: Path,
) -> None:
    repository, commit, _ = _fixture_repo(tmp_path, hostile=False)
    linked = tmp_path / "linked-consumer"
    _git(repository, "worktree", "add", "--detach", str(linked), commit)
    assert (linked / ".git").is_file() and not (linked / ".git").is_symlink()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with prepare_snapshot(
        _spec(linked, scratch, commit), timeout=SNAPSHOT_TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=SNAPSHOT_TIMEOUT) as snapshot:
            assert snapshot.commit == commit
            assert _snapshot_git(snapshot.root, "status", "--porcelain=v1") == ""
            _assert_snapshot_manifest(snapshot.root)


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside"])
def test_absolute_or_escaping_symlink_is_refused_before_yield(
    tmp_path: Path, target: str
) -> None:
    repo, _, _ = _fixture_repo(tmp_path, hostile=False)
    os.unlink(repo / "links" / "in")
    os.symlink(target, repo / "links" / "in")
    commit = _commit(repo, f"unsafe link {target}")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
        ):
            pytest.fail("an unsafe seed must not be yielded")
    _assert_pair(caught.value, Outcome.ERROR, ReasonCode.GIT_FAILED)
    assert list(scratch.iterdir()) == []


def test_gitlink_and_malformed_tree_are_refused_as_git_state(tmp_path: Path) -> None:
    repo, base, _ = _fixture_repo(tmp_path, hostile=False)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},vendor/sub")
    gitlink = _git(repo, "commit", "-q", "-m", "gitlink", check=True)
    assert gitlink == b""
    gitlink_commit = _git(repo, "rev-parse", "HEAD").decode().strip()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, scratch, gitlink_commit), timeout=SNAPSHOT_TIMEOUT
        ):
            pytest.fail("a gitlink seed must not be yielded")
    _assert_pair(caught.value, Outcome.ERROR, ReasonCode.GIT_FAILED)

    blob = _git(repo, "hash-object", "-w", "--stdin", input_bytes=b"x\n").decode().strip()
    malformed = b"100664 bad\x00" + bytes.fromhex(blob)
    tree = _git(
        repo,
        "hash-object",
        "--literally",
        "-w",
        "-t",
        "tree",
        "--stdin",
        input_bytes=malformed,
    ).decode().strip()
    raw_commit = (
        f"tree {tree}\nauthor Assay Fixture <fixture@assay.invalid> 946684800 +0000\n"
        f"committer Assay Fixture <fixture@assay.invalid> 946684800 +0000\n\n"
        "malformed tree\n"
    ).encode()
    malformed_commit = _git(
        repo,
        "hash-object",
        "--literally",
        "-w",
        "-t",
        "commit",
        "--stdin",
        input_bytes=raw_commit,
    ).decode().strip()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, scratch, malformed_commit), timeout=SNAPSHOT_TIMEOUT
        ):
            pytest.fail("an unsupported tree mode must not be yielded")
    _assert_pair(caught.value, Outcome.ERROR, ReasonCode.GIT_FAILED)


@pytest.mark.parametrize(
    "limits",
    [
        pytest.param({"max_entries": 8}, id="entry-count"),
        pytest.param({"max_path_bytes": 8}, id="per-path-bytes"),
        pytest.param({"max_total_path_bytes": 32}, id="total-path-bytes"),
        pytest.param({"max_blob_bytes": 15}, id="blob-bytes"),
        pytest.param(
            {"max_blob_bytes": 100, "max_total_object_bytes": 100},
            id="total-object-bytes",
        ),
        pytest.param({"max_objects": 3}, id="object-count"),
        pytest.param({"max_pack_bytes": 32}, id="pack-bytes"),
    ],
)
def test_each_limit_plus_one_is_a_typed_non_yielding_refusal(
    tmp_path: Path, limits: dict[str, int]
) -> None:
    repo, commit, _ = _fixture_repo(tmp_path, hostile=False)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, scratch, commit, limits=_tight_limits(**limits)),
            timeout=SNAPSHOT_TIMEOUT,
        ):
            pytest.fail("a limit refusal must not expose a partial seed")
    _assert_pair(
        caught.value,
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.SNAPSHOT_LIMIT_EXCEEDED,
    )
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("hazard", ["alternates", "partial", "shallow"])
def test_external_or_incomplete_source_object_topology_is_refused(
    tmp_path: Path, hazard: str
) -> None:
    repo, commit, _ = _fixture_repo(tmp_path, hostile=False)
    if hazard == "alternates":
        alternate = tmp_path / "alternate-objects"
        alternate.mkdir()
        (repo / ".git/objects/info/alternates").write_text(
            f"{alternate}\n", encoding="utf-8"
        )
    elif hazard == "partial":
        _git(repo, "config", "extensions.partialClone", "origin")
        _git(repo, "config", "remote.origin.promisor", "true")
    else:
        (repo / ".git/shallow").write_text(f"{commit}\n", encoding="ascii")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
        ):
            pytest.fail("foreign/incomplete object topology must not be yielded")
    _assert_pair(caught.value, Outcome.ERROR, ReasonCode.GIT_FAILED)
    assert list(scratch.iterdir()) == []


def test_replacement_is_repo_relative_exact_deterministic_and_non_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit, sentinel = _fixture_repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    before = _object_store_digest(repo)
    old = b"tracked sibling\n"
    new = b"mutated sibling\n"
    monkeypatch.setenv("GIT_AUTHOR_NAME", "HOSTILE AMBIENT AUTHOR")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "hostile@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "now")

    with prepare_snapshot(
        _spec(repo, scratch, commit), timeout=SNAPSHOT_TIMEOUT
    ) as prepared:
        assert prepared.read_regular_file(
            PurePosixPath("shared/input.txt"), timeout=SNAPSHOT_TIMEOUT
        ) == old
        with prepared.materialize(timeout=SNAPSHOT_TIMEOUT) as base_snapshot:
            with prepared.materialize_replacement(
                path=PurePosixPath("shared/input.txt"),
                expected=old,
                replacement=new,
                timeout=SNAPSHOT_TIMEOUT,
            ) as changed:
                child = changed.commit
                assert child != commit
                assert (changed.root / "shared/input.txt").read_bytes() == new
                assert _snapshot_git(changed.root, "status", "--porcelain=v1") == ""
                assert _snapshot_git(changed.root, "rev-list", "--parents", "-n", "1", child) == f"{child} {commit}"
                assert (base_snapshot.root / "shared/input.txt").read_bytes() == old
        with prepared.materialize_replacement(
            path=PurePosixPath("shared/input.txt"),
            expected=old,
            replacement=new,
            timeout=SNAPSHOT_TIMEOUT,
        ) as repeated:
            assert repeated.commit == child

        with pytest.raises(AssayError) as caught:
            with prepared.materialize_replacement(
                path=PurePosixPath("shared/input.txt"),
                expected=b"wrong prior bytes\n",
                replacement=new,
                timeout=SNAPSHOT_TIMEOUT,
            ):
                pytest.fail("a stale mutation site must not yield")
        _assert_pair(
            caught.value,
            Outcome.ERROR,
            ReasonCode.MUTATION_DISCOVERY_FAILED,
        )

        with pytest.raises(AssayError):
            # The project lives at apps/p; this proves the replacement path
            # is repo-top-relative, not silently reinterpreted under p.
            with prepared.materialize_replacement(
                path=PurePosixPath("input.txt"),
                expected=old,
                replacement=new,
                timeout=SNAPSHOT_TIMEOUT,
            ):
                pytest.fail("a missing repo-relative path must not yield")

    assert _object_store_digest(repo) == before
    assert (repo / "shared/input.txt").read_bytes() == old
    assert not sentinel.exists()


def test_snapshot_limit_complete_artifact_is_independently_valid() -> None:
    document = json.loads(
        (HERE / "expected/r0-snapshot-limit-v4.json").read_text(encoding="utf-8")
    )
    assert document["outcome"] == "BUDGET_EXCEEDED"
    assert document["reason_code"] == "SNAPSHOT_LIMIT_EXCEEDED"
    assert document["claims"] == [
        {
            "rigor": "R0",
            "source": "computed",
            "status": "BUDGET_EXCEEDED",
            "verified_by_assay": True,
            "reason_code": "SNAPSHOT_LIMIT_EXCEEDED",
        }
    ]
    assert list(Draft202012Validator(load_schema()).iter_errors(document)) == []
    assert _raw_verifier_failures(document) == []
    assert verify_document(document) == []

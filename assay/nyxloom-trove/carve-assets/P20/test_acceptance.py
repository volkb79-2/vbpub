"""Locked, carver-authored P20 behavioral acceptance tests.

This file deliberately lives outside ``tests/`` so main stays green before P20.
The controller runs it explicitly in the P20 worktree and in tester-unified.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from assay import git, runner
from assay.config import CoverageConfig, JudgeConfig, Lane
from assay.errors import AssayError, Outcome, ReasonCode

ASSET_ROOT = Path(__file__).resolve().parent
MAX_COVERAGE_BYTES = 16 * 1024 * 1024
T0 = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 9, 10, 0, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 9, 10, 0, 2, tzinfo=timezone.utc)


def _safeio():
    return importlib.import_module("assay.safeio")


def _clock(*values: datetime):
    remaining = iter(values)
    return lambda: next(remaining)


def _real_git() -> str:
    found = shutil.which("git")
    assert found is not None
    return str(Path(found).resolve(strict=True))


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        [_real_git(), "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _seed_repo(root: Path, name: str = "main") -> tuple[str, str]:
    subprocess.run([_real_git(), "init", "-q", "-b", "main", str(root)], check=True)
    _run_git(root, "config", "user.name", "P20 acceptance")
    _run_git(root, "config", "user.email", "p20@example.invalid")
    (root / "src").mkdir()
    (root / ".gitignore").write_text("cov.json\n", encoding="utf-8")
    (root / "src" / "mod.zzz").write_text("BASE\n", encoding="utf-8")
    (root / "support.txt").write_text(f"{name}\n", encoding="utf-8")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "base")
    base = _run_git(root, "rev-parse", "HEAD").strip()
    (root / "src" / "mod.zzz").write_text("BASE\nLINE2\n", encoding="utf-8")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "head")
    return base, _run_git(root, "rev-parse", "HEAD").strip()


@dataclass(frozen=True)
class PrefixAdapter:
    name: str = "zzz"
    source_globs: tuple[str, ...] = ("*.zzz",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    key_prefix: str = "pkg1/"

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key.removeprefix(self.key_prefix)


ADAPTER = PrefixAdapter()


def _lane(repo: Path, base: str, command: str) -> Lane:
    judge = JudgeConfig(
        language="zzz",
        source_roots=("src",),
        source_root_paths=(repo / "src",),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=None,
        canary=None,
        base=base,
    )
    return Lane(
        name="package",
        scope="S1",
        rigor=("R0", "R1"),
        enforcement="gate",
        argv=("/bin/sh", "-c", command),
        env=MappingProxyType({}),
        env_passthrough=(),
        budget="5m",
        budget_seconds=300.0,
        allow_argv_append=False,
        judge=judge,
        where=None,
    )


def _cov(keys: dict[str, list[int]]) -> str:
    return json.dumps(
        {
            "files": {
                key: {
                    "executed_lines": lines,
                    "missing_lines": [],
                    "excluded_lines": [],
                }
                for key, lines in keys.items()
            }
        }
    )


def _child_env(**updates: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(updates)
    return env


def test_git_identity_survives_hostile_namespace_and_config(tmp_path: Path):
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    _, head_a = _seed_repo(repo_a, "alpha")
    _, head_b = _seed_repo(repo_b, "beta")
    assert head_a != head_b
    _run_git(repo_a, "config", "core.worktree", str(repo_b))
    child = (
        "from pathlib import Path; from assay.git import head_rev, repo_top, dirty_paths; "
        f"root=Path({str(repo_a)!r}); "
        "print(head_rev(root)); print(repo_top(root)); print(dirty_paths(root))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", child],
        env=_child_env(
            GIT_DIR=str(repo_b / ".git"),
            GIT_WORK_TREE=str(repo_b),
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="core.quotePath",
            GIT_CONFIG_VALUE_0="true",
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.splitlines() == [head_a, str(repo_a), "()"]


def test_git_diff_disables_ambient_and_local_external_programs(tmp_path: Path):
    repo = tmp_path / "repo"
    base, head = _seed_repo(repo)
    sentinel = tmp_path / "external-ran"
    external = tmp_path / "external-diff"
    external.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
    external.chmod(0o755)
    _run_git(repo, "config", "diff.external", str(external))
    _run_git(repo, "replace", head, base)
    child = (
        "from pathlib import Path; from assay.git import run; "
        f"print(run(Path({str(repo)!r}), 'diff', '--unified=0', {base!r}, {head!r}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", child],
        env=_child_env(GIT_EXTERNAL_DIFF=str(external)),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "LINE2" in proc.stdout
    assert not sentinel.exists()


def test_git_absence_from_declared_path_fails_without_os_default_search(tmp_path: Path):
    repo = tmp_path / "repo"
    _seed_repo(repo)
    child = (
        "from pathlib import Path; from assay.git import head_rev; "
        "from assay.errors import AssayError; "
        f"\ntry: head_rev(Path({str(repo)!r}))\n"
        "except AssayError as exc: print(exc.outcome.value, exc.reason_code.value)\n"
        "else: print('WRONG_FALLBACK')"
    )
    env = {key: os.environ[key] for key in ("PYTHONPATH", "PYTHONHOME") if key in os.environ}
    proc = subprocess.run(
        [sys.executable, "-c", child], env=env, capture_output=True, text=True, check=True
    )
    assert proc.stdout.strip() == "ERROR GIT_FAILED"


def test_reservation_does_not_unlink_until_armed_and_missing_is_explicit(tmp_path: Path):
    safeio = _safeio()
    old = tmp_path / "cov.json"
    old.write_bytes(b"stale")
    with safeio.reserve_output(tmp_path, "cov.json", limit=MAX_COVERAGE_BYTES) as reserved:
        assert old.read_bytes() == b"stale"
        reserved.arm()
        assert not old.exists()
        assert reserved.consume() is None
        with pytest.raises(RuntimeError):
            reserved.consume()


def test_reservation_rejects_a_swap_before_arm_without_deleting_it(tmp_path: Path):
    safeio = _safeio()
    path = tmp_path / "cov.json"
    path.write_bytes(b"first")
    with safeio.reserve_output(tmp_path, "cov.json", limit=100) as reserved:
        path.unlink()
        path.write_bytes(b"replacement")
        with pytest.raises(AssayError) as caught:
            reserved.arm()
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert path.read_bytes() == b"replacement"


@pytest.mark.parametrize("kind", ["fifo", "symlink", "oversize"])
def test_bounded_read_rejects_special_or_oversize_without_path_reopen(
    tmp_path: Path, kind: str
):
    safeio = _safeio()
    path = tmp_path / "artifact"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"valid")
        path.symlink_to(target)
    else:
        path.write_bytes(b"x" * 33)
    with pytest.raises(AssayError) as caught:
        safeio.read_bounded_file(tmp_path, "artifact", limit=32)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_reservation_reads_original_parent_not_replacement_path(tmp_path: Path):
    safeio = _safeio()
    parent = tmp_path / "build"
    parent.mkdir()
    with safeio.reserve_output(tmp_path, "build/cov.json", limit=100) as reserved:
        reserved.arm()
        parent.rename(tmp_path / "moved")
        parent.mkdir()
        replacement = parent / "cov.json"
        replacement.write_bytes(b"wrong tree")
        assert reserved.consume() is None
    assert replacement.read_bytes() == b"wrong tree"


def test_reservation_rejects_relinked_prior_inode(tmp_path: Path):
    safeio = _safeio()
    prior = tmp_path / "cov.json"
    other_link = tmp_path / "held-link"
    prior.write_bytes(b"stale")
    os.link(prior, other_link)
    with safeio.reserve_output(tmp_path, "cov.json", limit=100) as reserved:
        reserved.arm()
        os.link(other_link, prior)
        with pytest.raises(AssayError) as caught:
            reserved.consume()
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_clean_missing_output_is_no_measurement_empty_coverage(tmp_path: Path):
    repo = tmp_path / "repo"
    base, head = _seed_repo(repo)
    verdict = runner.run_lane(
        _lane(repo, base, "exit 0"),
        commit=head,
        repo=repo,
        project_root=repo,
        adapter=ADAPTER,
        assay_version="p20-acceptance",
        clock=_clock(T0, T1, T2),
    )
    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].reason_code is ReasonCode.EMPTY_COVERAGE


def test_post_command_dirt_matches_hand_authored_complete_artifact(tmp_path: Path):
    repo = tmp_path / "repo"
    base, head = _seed_repo(repo)
    payload = _cov({"src/mod.zzz": [2]})
    command = f"printf '%s' '{payload}' > cov.json; printf dirty >> support.txt"
    verdict = runner.run_lane(
        _lane(repo, base, command),
        commit=head,
        repo=repo,
        project_root=repo,
        adapter=ADAPTER,
        assay_version="p20-acceptance",
        clock=_clock(T0, T1, T2),
    )
    expected = json.loads(
        (ASSET_ROOT / "expected" / "post-dirty-v3.json").read_text(encoding="utf-8")
    )
    expected["commit"] = head
    expected["argv_declared"][2] = command
    expected["argv_effective"][2] = command
    assert json.loads(verdict.to_json()) == expected
    assert "dirty" in (repo / "support.txt").read_text(encoding="utf-8")


def test_normalized_key_collision_becomes_complete_unreadable_artifact(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    base, head = _seed_repo(repo)
    payload = _cov({"pkg1/src/mod.zzz": [2], "src/mod.zzz": []})
    command = f"printf '%s' '{payload}' > cov.json"
    verdict = runner.run_lane(
        _lane(repo, base, command),
        commit=head,
        repo=repo,
        project_root=repo,
        adapter=ADAPTER,
        assay_version="p20-acceptance",
        clock=_clock(T0, T1, T2),
    )
    assert verdict.outcome is Outcome.ERROR
    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].reason_code is ReasonCode.UNREADABLE_ARTIFACT
    document = json.loads(verdict.to_json())
    assert document["commit"] == head
    assert "coverage" not in document["claims"][1]

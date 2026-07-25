"""Unit tests for the shared gate_runner primitive (factory-hardening F).

gate_runner is the ONE implementation of "select the project's verification
gate" + "run a gate against a commit in a clean scratch worktree", shared by the
daemon post-merge path and cli.cmd_merge. cli tests monkeypatch run_gate_at_commit
(to force a verdict), so its BODY is exercised here directly against real git.
"""
from __future__ import annotations

import dataclasses
import subprocess

from nyxloom import gate_runner
from nyxloom.config import GateDef


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _gate(gate_id, argv, phase="post-merge", timeout=10):
    return GateDef(gate_id=gate_id, argv=argv, phase=phase,
                   timeout_seconds=timeout, environment="local")


def test_select_prefers_post_merge_then_impl_then_none(tmp_state, sample_project):
    cfg = sample_project
    # sample_project declares one implementation-phase gate -> impl fallback
    g = gate_runner.select_verification_gate(cfg)
    assert g is not None and g.phase == "implementation"

    # an explicit post-merge-phase gate wins over the implementation gate
    cfg2 = dataclasses.replace(cfg, gates={
        "pm": _gate("pm", ["true"], phase="post-merge"),
        "impl": _gate("impl", ["true"], phase="implementation"),
    })
    assert gate_runner.select_verification_gate(cfg2).gate_id == "pm"

    # no gates declared at all -> None
    assert gate_runner.select_verification_gate(dataclasses.replace(cfg, gates={})) is None


def test_run_gate_at_commit_pass_and_fail_on_real_commit(tmp_state, sample_project):
    cfg = sample_project
    head = _run(cfg.root, "rev-parse", "HEAD").stdout.strip()

    res = gate_runner.run_gate_at_commit(cfg, _gate("ok", ["true"]), head, phase="pre-merge")
    assert res.exit_code == 0
    assert res.phase == "pre-merge"
    assert res.commit == head
    # the scratch worktree is cleaned up
    assert not (cfg.root / ".worktrees" / f"gaterun-{head[:12]}").exists()

    assert gate_runner.run_gate_at_commit(cfg, _gate("bad", ["false"]), head).exit_code != 0


def test_run_gate_at_commit_falls_back_when_commit_unknown(tmp_state, sample_project):
    """A placeholder/unknown commit -> scratch worktree add fails -> the gate
    runs against the live root instead of hard-erroring."""
    cfg = sample_project
    res = gate_runner.run_gate_at_commit(cfg, _gate("ok", ["true"]), "deadbeef" * 5)
    assert res.exit_code == 0  # fell back to the live root; `true` passes


def test_run_gate_at_commit_oserror_yields_127(tmp_state, sample_project):
    """A non-executable gate argv -> OSError -> conventional 127."""
    cfg = sample_project
    head = _run(cfg.root, "rev-parse", "HEAD").stdout.strip()
    res = gate_runner.run_gate_at_commit(cfg, _gate("nobin", ["this-binary-does-not-exist-xyz"]), head)
    assert res.exit_code == 127


def test_run_gate_at_commit_timeout_yields_124(tmp_state, sample_project):
    """A gate that outlives timeout_seconds -> TimeoutExpired -> conventional 124."""
    cfg = sample_project
    head = _run(cfg.root, "rev-parse", "HEAD").stdout.strip()
    res = gate_runner.run_gate_at_commit(cfg, _gate("slow", ["sleep", "5"], timeout=1), head)
    assert res.exit_code == 124


def test_run_gate_at_commit_removes_stale_scratch_worktree(tmp_state, sample_project):
    """A leftover scratch worktree from an interrupted prior run is removed
    before the gate re-adds it -- the run is idempotent."""
    from pathlib import Path

    cfg = sample_project
    head = _run(cfg.root, "rev-parse", "HEAD").stdout.strip()
    repo_root = _run(cfg.root, "rev-parse", "--show-toplevel").stdout.strip()
    stale = Path(repo_root) / ".worktrees" / f"gaterun-{head[:12]}"
    # pre-create the stale scratch worktree out-of-band (simulates an interrupt)
    assert _run(repo_root, "worktree", "add", "--detach", str(stale), head).returncode == 0
    assert stale.exists()

    res = gate_runner.run_gate_at_commit(cfg, _gate("ok", ["true"]), head)
    assert res.exit_code == 0
    assert not stale.exists()  # the stale copy was removed and the run cleaned up after itself

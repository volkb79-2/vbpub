"""CIU-197 / CIU-199: adopt must not refuse its own env-generation child, and a
daemon address-pool exhaustion must say what it is and what to do.

CIU-197 oracle: an ADOPT writes `recovery-required` (its durable no-reset
marker) as its first record state. The full `ciu env generate` child it then
runs verifies the runtime network against that record; before the fix the
verifier accepted only `allocating`, so every adopt of a checkout with a CIU
root ended `recovery-required` and `ensure` hit the same refusal.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree, workspace_env  # noqa: E402
from test_ciu_worktree import _git, fake_generate_env, tmp_repo  # noqa: E402,F401


def _verify(record, **kw):
    return worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
        **kw,
    )


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)


def test_adopted_recovery_required_accepted_during_allocation(
    tmp_repo, fake_generate_env, no_network
):
    """Fails against a verifier that only accepts `allocating`."""
    record = worktree.create(tmp_repo, "adopt-ok")
    adopting = replace(record, state="recovery-required", adopted=True)
    worktree._write_instance_record(adopting)
    assert _verify(record, allow_allocating_instance=True) == adopting


def test_adopted_recovery_required_still_refused_outside_allocation(
    tmp_repo, fake_generate_env, no_network
):
    """The allowance is the allocation caller's only; `ciu up` still refuses."""
    record = worktree.create(tmp_repo, "adopt-up")
    worktree._write_instance_record(
        replace(record, state="recovery-required", adopted=True)
    )
    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="is 'recovery-required'"
    ):
        _verify(record)


@pytest.mark.parametrize("adopted", [None, False])
def test_non_adopted_recovery_required_is_refused_even_during_allocation(
    tmp_repo, fake_generate_env, no_network, adopted
):
    """Fails against a verifier that accepts any `recovery-required` record."""
    record = worktree.create(tmp_repo, f"nonadopt-{adopted}")
    worktree._write_instance_record(
        replace(record, state="recovery-required", adopted=adopted)
    )
    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="is 'recovery-required'"
    ):
        _verify(record, allow_allocating_instance=True)


def test_adopt_completes_when_the_env_child_verifies_its_own_record(
    tmp_repo, fake_generate_env, no_network, monkeypatch, tmp_path
):
    """End to end: the env-generation stand-in verifies like the real child."""
    wt_path = tmp_path / "adopted-wt"
    assert _git(["worktree", "add", "-b", "feat-adopt", str(wt_path)], tmp_repo).returncode == 0

    real_fake = worktree._generate_env_in

    def child_that_verifies(path, *, identity_only=False, allocation_lock_held=False):
        rc = real_fake(path, identity_only=identity_only,
                       allocation_lock_held=allocation_lock_held)
        if rc == 0 and allocation_lock_held:
            record = worktree.read_own_instance_record(Path(path))
            worktree.verify_own_runtime_network(
                Path(path),
                expected_instance_id=record.instance_id,
                expected_network=record.network,
                allow_allocating_instance=True,
                allocation_lock_held=True,
            )
        return rc

    monkeypatch.setattr(worktree, "_generate_env_in", child_that_verifies)
    record = worktree.adopt(tmp_repo, "adopted", str(wt_path))
    assert record.state == "ready"
    assert worktree.read_own_instance_record(wt_path).state == "ready"


# ---------------------------------------------------------------- CIU-199


def _create_failing(monkeypatch, stderr):
    def fake_docker(argv, **_kw):  # pragma: no cover - not used
        raise AssertionError

    calls = []

    def run(argv, **_kw):
        calls.append(argv)
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "No such network")
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.delenv("CIU_TEST_SUITE", raising=False)
    monkeypatch.setattr(workspace_env, "_test_suite_gate_active", lambda: False)
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    monkeypatch.setattr(workspace_env.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("marker", [
    "all predefined address pools have been fully subnetted",
    "Could not find an available, non-overlapping IPv4 address pool among the defaults",
])
def test_pool_exhaustion_message_names_cause_and_remedy(monkeypatch, marker):
    calls = _create_failing(monkeypatch, f"Error response from daemon: {marker}")
    with pytest.raises(workspace_env.WorkspaceEnvError) as err:
        workspace_env._ensure_network_exists("x-network")
    text = str(err.value)
    assert marker.lower() in text.lower()
    assert "[CIU-199]" in text and "default-address-pools" in text
    assert "docker network rm <exact name>" in text
    # never removes anything on its own
    assert not [c for c in calls if c[:3] == ["docker", "network", "rm"]]


def test_other_create_failures_get_no_pool_remedy(monkeypatch):
    _create_failing(monkeypatch, "Error response from daemon: permission denied")
    with pytest.raises(workspace_env.WorkspaceEnvError) as err:
        workspace_env._ensure_network_exists("x-network")
    assert "CIU-199" not in str(err.value)
    assert "permission denied" in str(err.value)

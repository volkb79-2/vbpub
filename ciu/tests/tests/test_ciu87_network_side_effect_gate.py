"""CIU-87 — the test suite must not leak Docker networks into its host.

Every test here is a mutation-style pin on one of the two mechanisms
ciu-P48 added; each names, in its docstring, the wrong implementation it
fails against.

Before ciu-P48 a full suite run created two Docker networks on the shared
devcontainer host and joined the cockpit to both. `ciu clean` then refuses
(by design) to remove a network a container is still attached to, so both the
networks and the memberships leaked permanently, one pair per run, until an
unrelated workload hit the daemon's exhausted address pool.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import conftest as suite_conftest  # noqa: E402  (ciu's own tests/conftest.py)
from ciu import workspace_env  # noqa: E402


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --------------------------------------------------------------------------
# Mechanism 1 — the product-side CIU_TEST_SUITE gate
# --------------------------------------------------------------------------


def test_the_suite_declares_the_gate_for_every_test() -> None:
    """Deleting conftest's CIU_TEST_SUITE assignment fails here.

    The gate is worthless if the suite forgets to raise it, and a test body is
    where a spawned `ciu` subprocess would inherit it from.
    """
    import os

    assert os.environ["CIU_TEST_SUITE"] == "1"
    assert workspace_env._test_suite_gate_active() is True


def test_gate_suppresses_network_create_and_cockpit_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing either `_test_suite_gate_active()` early return fails here.

    This is the leak itself, pinned: under the suite's own gate, the full
    `ensure_workspace_network` path reaches the daemon zero times even though
    ENV_TYPE is a genuine "devcontainer" — which is exactly the situation the
    S1.9 guard alone cannot distinguish from real provisioning.
    """
    monkeypatch.setenv("ENV_TYPE", "devcontainer")
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setattr(
        workspace_env,
        "_docker_available",
        lambda: pytest.fail("gated run must not probe the daemon"),
    )
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("gated run must not shell out to docker"),
    )

    workspace_env.ensure_workspace_network("ciu-p48-must-not-exist-network")


def test_gate_keeps_the_identity_contract_it_does_not_suppress() -> None:
    """Moving the gate ABOVE the empty-name check fails here.

    The gate suppresses the daemon side effect, never the validation that
    names the network — an empty DOCKER_NETWORK_INTERNAL is still a hard error
    inside the suite, so no test can pass on a silently unnamed workspace.
    """
    with pytest.raises(workspace_env.WorkspaceEnvError, match="missing or empty"):
        workspace_env._ensure_network_exists("")


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "TRUE", " 1"])
def test_only_the_exact_value_one_arms_the_gate(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Loosening the comparison to a truthiness test fails here.

    A stray ambient CIU_TEST_SUITE must never silently disarm the real S1.9
    network join in a production devcontainer.
    """
    monkeypatch.setenv("CIU_TEST_SUITE", value)
    assert workspace_env._test_suite_gate_active() is False


def test_gate_absent_restores_the_real_devcontainer_attach(
    real_network_side_effects: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard-disabling the S1.9 attach (rather than gating it) fails here.

    The opt-out fixture is the other half of the contract: production
    behavior is intact, and a test whose subject IS that behavior can still
    reach it. Docker stays a controlled seam — the daemon is never touched.
    """
    monkeypatch.setenv("ENV_TYPE", "devcontainer")
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _result(stdout="somebody-else ")

    monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

    assert workspace_env._test_suite_gate_active() is False
    workspace_env._connect_devcontainer_to_network("ciu-internal")

    assert ["docker", "network", "connect", "ciu-internal", "ciu-cockpit"] in calls


def test_gate_reaches_a_spawned_ciu_subprocess() -> None:
    """Setting the gate only from a fixture (never in os.environ) fails here.

    Several of the tests that used to leak drive ciu through a child process;
    a fixture-only signal would not survive the fork, so conftest sets the
    variable in os.environ at import time as well.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('CIU_TEST_SUITE'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "1"


# --------------------------------------------------------------------------
# Mechanism 2 — the surgical teardown ledger
# --------------------------------------------------------------------------


def _recording_runner(records: list[list[str]], returncode: int = 0):
    def _runner(argv, **_kwargs):
        records.append(list(argv))
        return _result(returncode=returncode)

    return _runner


def test_release_disconnects_the_cockpit_before_removing_the_network() -> None:
    """Reversing the two steps fails here.

    The daemon refuses `network rm` while a container is still joined, and the
    refusal is silent to a best-effort teardown — removal-first would leave the
    leak exactly as it was.
    """
    records: list[list[str]] = []

    issued = suite_conftest.release_test_network(
        "p48-net", "cockpit", remove=True, runner=_recording_runner(records)
    )

    assert issued == [
        ["network", "disconnect", "-f", "p48-net", "cockpit"],
        ["network", "rm", "p48-net"],
    ]
    assert records == [
        ["docker", "network", "disconnect", "-f", "p48-net", "cockpit"],
        ["docker", "network", "rm", "p48-net"],
    ]


def test_release_never_removes_a_network_the_test_did_not_create() -> None:
    """Making `remove` unconditional fails here — that is the shared-host
    hazard CIU-87 was itself first (wrongly) suspected of being."""
    records: list[list[str]] = []

    issued = suite_conftest.release_test_network(
        "someone-elses-net", "cockpit", remove=False, runner=_recording_runner(records)
    )

    assert issued == [["network", "disconnect", "-f", "someone-elses-net", "cockpit"]]


def test_release_tolerates_an_already_gone_network() -> None:
    """Raising on a non-zero docker exit fails here: a teardown that explodes
    on "already gone" turns an unrelated test failure into an error cascade."""
    suite_conftest.release_test_network(
        "gone", "cockpit", remove=True, runner=_recording_runner([], returncode=1)
    )


def test_release_skips_disconnect_when_no_cockpit_is_detectable() -> None:
    """Passing an empty container name through fails here — `docker network
    disconnect NET ""` is an argument error, not a no-op."""
    records: list[list[str]] = []

    issued = suite_conftest.release_test_network(
        "p48-net", "", remove=True, runner=_recording_runner(records)
    )

    assert issued == [["network", "rm", "p48-net"]]


def test_run_docker_survives_a_missing_docker_binary() -> None:
    """Letting OSError escape fails here: teardown runs after the test body,
    where an exception would mask the test's own verdict."""

    def _explode(*_args, **_kwargs):
        raise OSError("docker: no such file")

    assert suite_conftest.run_docker(["network", "ls"], runner=_explode).returncode == 1


def test_network_exists_reads_the_inspect_exit_status() -> None:
    """Inverting or ignoring the exit status fails here — this predicate is
    what separates "we created it" from "it was already there"."""
    assert suite_conftest.network_exists("n", runner=_recording_runner([], 0)) is True
    assert suite_conftest.network_exists("n", runner=_recording_runner([], 1)) is False


def _tracker(exists_answers: list[bool], *, suppressed: bool = False):
    answers = iter(exists_answers)
    return suite_conftest.NetworkSideEffectTracker(
        exists=lambda _name: next(answers),
        suppressed=lambda: suppressed,
    )


def test_tracker_registers_a_network_this_test_brought_into_existence() -> None:
    """Dropping the before/after comparison fails here."""
    tracker = _tracker([False, True])

    tracker.wrap_ensure(lambda _name: None)("fresh-net")

    assert tracker.created == ["fresh-net"]


def test_tracker_ignores_a_network_that_already_existed() -> None:
    """Registering on "exists afterwards" alone fails here: on this shared
    host a pre-existing network is another tenant's live work."""
    tracker = _tracker([True, True])

    tracker.wrap_ensure(lambda _name: None)("someone-elses-net")

    assert tracker.created == []


def test_tracker_registers_a_network_whose_creation_then_failed_only_if_real() -> None:
    """Registering unconditionally on entry fails here — a creation that
    raised leaves nothing to remove."""
    tracker = _tracker([False, False])

    with pytest.raises(RuntimeError):
        tracker.wrap_ensure(lambda _name: (_ for _ in ()).throw(RuntimeError("boom")))(
            "never-created"
        )

    assert tracker.created == []


def test_tracker_records_the_join_even_when_the_product_call_raises() -> None:
    """Recording after the call instead of in a `finally` fails here: a
    membership created before an exception is still a membership."""
    tracker = _tracker([])

    with pytest.raises(RuntimeError):
        tracker.wrap_connect(lambda _name: (_ for _ in ()).throw(RuntimeError("boom")))(
            "joined-net"
        )

    assert tracker.joined == ["joined-net"]


def test_tracker_is_a_pass_through_while_the_gate_is_active() -> None:
    """Probing the daemon regardless of the gate fails here — the exists
    callback would be consumed and raise StopIteration."""
    tracker = _tracker([], suppressed=True)
    seen: list[str] = []

    tracker.wrap_ensure(seen.append)("n")
    tracker.wrap_connect(seen.append)("n")

    assert seen == ["n", "n"]
    assert (tracker.created, tracker.joined) == ([], [])


def test_release_removes_only_created_networks_and_deduplicates() -> None:
    """Removing every tracked name fails here; so does releasing a network
    twice when it was both created and joined in one test."""
    tracker = _tracker([False, True])
    tracker.wrap_ensure(lambda _name: None)("mine")
    tracker.joined.extend(["mine", "theirs"])
    calls: list[tuple[str, str, bool]] = []

    names = tracker.release(
        "cockpit",
        releaser=lambda name, container, *, remove: calls.append(
            (name, container, remove)
        ),
    )

    assert names == ["mine", "theirs"]
    assert calls == [("mine", "cockpit", True), ("theirs", "cockpit", False)]


def test_the_teardown_net_is_armed_for_every_test(request: pytest.FixtureRequest) -> None:
    """Making `_track_real_docker_networks` opt-in fails here.

    The gate is the primary fix; this fixture is the net under it, and a net
    only counts if it is under every test — including the ones that opt the
    gate off.
    """
    assert "_track_real_docker_networks" in request.fixturenames
    assert workspace_env._ensure_network_exists.__name__ == "_ensure"
    assert workspace_env._connect_devcontainer_to_network.__name__ == "_connect"

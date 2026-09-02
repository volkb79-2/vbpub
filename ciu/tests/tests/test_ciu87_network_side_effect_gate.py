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
    """Removing the `_ciu_test_suite_gate` autouse fixture's `setenv` fails
    here — as does any conftest that stops raising the gate by both routes.

    Scope note (ciu-P48 review B2): this pins that SOME mechanism has the gate
    up during a test body. It does NOT distinguish the two — conftest's
    import-time assignment and the autouse fixture both write `os.environ`, so
    deleting either one alone leaves this green. The import-time half is
    pinned separately, in a child process, by
    `test_conftest_raises_the_gate_at_import_not_only_per_test`.
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
    """Signalling the gate by anything a child process cannot see — a pytest
    marker, a module global, a `sys` attribute — fails here.

    Several of the tests that used to leak drive ciu through a child process,
    so the signal has to live in the process ENVIRONMENT, not merely in the
    interpreter that runs the test. (Scope note, ciu-P48 review B2: this says
    nothing about WHICH conftest mechanism put it there — the autouse
    fixture's `setenv` writes `os.environ` too, so a child inherits it either
    way. See `test_conftest_raises_the_gate_at_import_not_only_per_test`.)
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('CIU_TEST_SUITE'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "1"


def test_conftest_raises_the_gate_at_import_not_only_per_test() -> None:
    """Deleting conftest's import-time `os.environ[...] = "1"` fails here, and
    fails ONLY here.

    Import time is the half no fixture can cover: collection-time module
    bodies run before any fixture, and a `ciu` subprocess spawned from one
    would inherit an ungated environment. Every in-process assertion about
    this is masked by the autouse fixture, which writes the same variable —
    so the check has to happen in a child interpreter that imports conftest
    with the variable deliberately removed and nothing else running.
    """
    tests_root = str(Path(__file__).resolve().parents[1])
    probe = (
        "import os, sys\n"
        "os.environ.pop('CIU_TEST_SUITE', None)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import conftest\n"
        "print(os.environ.get('CIU_TEST_SUITE'))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, tests_root],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "1", (
        "importing tests/conftest.py must itself raise CIU_TEST_SUITE; "
        f"child reported {proc.stdout.strip()!r}"
    )


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


def _tracker(
    exists_answers: list[bool],
    *,
    attached_answers: list[bool] | None = None,
    suppressed: bool = False,
):
    exists = iter(exists_answers)
    attached = iter(attached_answers or [])
    return suite_conftest.NetworkSideEffectTracker(
        exists=lambda _name: next(exists),
        attached=lambda _name: next(attached),
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
    membership that really appeared before an exception is still a
    membership, and still has to be undone."""
    tracker = _tracker([], attached_answers=[False, True])

    with pytest.raises(RuntimeError):
        tracker.wrap_connect(lambda _name: (_ for _ in ()).throw(RuntimeError("boom")))(
            "joined-net"
        )

    assert tracker.joined == ["joined-net"]


def test_tracker_registers_a_join_only_when_a_membership_really_appeared() -> None:
    """ciu-P48 review B1. Recording every name the product was ASKED to
    connect — the original `finally: self.joined.append(...)` — fails here.

    A boundary test that mocks `subprocess.run` makes no attachment at all, so
    nothing may be registered, so teardown may issue no `docker network
    disconnect -f`. The bug this pins was live: teardown really did disconnect
    the cockpit from a network a mocked test merely NAMED.
    """
    tracker = _tracker([], attached_answers=[False, False])

    tracker.wrap_connect(lambda _name: None)("mocked-seam-never-attached")

    assert tracker.joined == []


def test_tracker_ignores_a_membership_that_predates_the_call() -> None:
    """Registering on "attached afterwards" alone fails here: a cockpit that
    was ALREADY on the network was put there by someone else, and forcibly
    disconnecting it is the shared-host harm, not the fix."""
    tracker = _tracker([], attached_answers=[True, True])

    tracker.wrap_connect(lambda _name: None)("someone-elses-live-network")

    assert tracker.joined == []


def test_a_mocked_seam_join_leaves_the_teardown_ledger_empty(
    real_network_side_effects: None,
    _track_real_docker_networks: "suite_conftest.NetworkSideEffectTracker",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ciu-P48 review B1, end to end through the real fixture.

    This is the reviewer's reproduction, inverted into a regression test: the
    gate is off, the product's whole Docker seam is mocked, and
    `_connect_devcontainer_to_network` is driven against a network name this
    test does not own. Nothing may reach the ledger — because everything in
    the ledger becomes a real `docker network disconnect -f`/`rm` against the
    live shared daemon at teardown.
    """
    monkeypatch.setenv("ENV_TYPE", "devcontainer")
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_a, **_k: _result(stdout=""),
    )

    workspace_env._connect_devcontainer_to_network("ciu-p48-not-ours-network")

    assert _track_real_docker_networks.joined == []
    assert _track_real_docker_networks.created == []


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

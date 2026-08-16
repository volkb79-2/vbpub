"""Behavioral witnesses for the agent ABC contracts.

The abstract methods deliberately have an executable failure body.  A
compliant subclass can call that body while developing an implementation,
and the contract fails loudly instead of becoming an automatically excluded
coverage line.
"""

from pathlib import Path

import pytest

from cmru.agent.adapter import ProjectAdapter
from cmru.agent.backend import (
    DesiredStateBackend,
    EnrollmentSeed,
    LockHandle,
)
from cmru.agent.consul_backend import ConsulBackend


class ContractAdapter(ProjectAdapter):
    """Concrete probe that reaches each base contract explicitly."""

    def validate(self, desired, installed_release):
        return super().validate(desired, installed_release)

    def prepare(self, desired, release_root):
        return super().prepare(desired, release_root)

    def apply_step(self, step):
        return super().apply_step(step)

    def health(self, step):
        return super().health(step)

    def rollback(self, previous):
        return super().rollback(previous)


def test_each_adapter_contract_fails_loudly_when_reached():
    adapter = ContractAdapter()
    calls = (
        ("validate", lambda: adapter.validate({}, Path("/release"))),
        ("prepare", lambda: adapter.prepare({}, Path("/release"))),
        ("apply_step", lambda: adapter.apply_step({})),
        ("health", lambda: adapter.health({})),
        ("rollback", lambda: adapter.rollback({})),
    )

    for method_name, call in calls:
        with pytest.raises(NotImplementedError, match=method_name):
            call()


class ContractBackend(DesiredStateBackend):
    """Concrete probe that reaches each transport contract explicitly."""

    def enroll(self, seed):
        return super().enroll(seed)

    def watch_desired(self, node_id, landscape, index, wait):
        return super().watch_desired(node_id, landscape, index, wait)

    def acquire_lock(self, node_id, landscape, generation):
        return super().acquire_lock(node_id, landscape, generation)

    def release_lock(self, lock):
        return super().release_lock(lock)

    def publish_observed(self, node_id, landscape, observed_json):
        return super().publish_observed(node_id, landscape, observed_json)

    def register_service(self, node_id):
        return super().register_service(node_id)

    def pass_health_check(self, node_id):
        return super().pass_health_check(node_id)

    def read_observed(self, node_id, landscape):
        return super().read_observed(node_id, landscape)


def test_each_backend_contract_fails_loudly_when_reached():
    backend = ContractBackend()
    calls = (
        (
            "enroll",
            lambda: backend.enroll(
                EnrollmentSeed("node", "landscape", "token", "pubkey")
            ),
        ),
        (
            "watch_desired",
            lambda: backend.watch_desired("node", "landscape", 0, "1s"),
        ),
        (
            "acquire_lock",
            lambda: backend.acquire_lock("node", "landscape", 1),
        ),
        (
            "release_lock",
            lambda: backend.release_lock(LockHandle("session", "key", True)),
        ),
        (
            "publish_observed",
            lambda: backend.publish_observed("node", "landscape", "{}"),
        ),
        ("register_service", lambda: backend.register_service("node")),
        ("pass_health_check", lambda: backend.pass_health_check("node")),
        (
            "read_observed",
            lambda: backend.read_observed("node", "landscape"),
        ),
    )

    for method_name, call in calls:
        with pytest.raises(NotImplementedError, match=method_name):
            call()


def test_consul_backend_is_a_complete_concrete_backend():
    assert issubclass(ConsulBackend, DesiredStateBackend)
    assert not ConsulBackend.__abstractmethods__
    assert isinstance(ConsulBackend(consul_addr="http://consul"), DesiredStateBackend)

"""Fail-closed witnesses for Consul read response decoding."""
from __future__ import annotations

import base64
import json

import pytest

from cmru.agent.consul_backend import ConsulBackend


@pytest.mark.parametrize(
    "payload",
    [
        b'{"not": "a-list"}',
        json.dumps(["not-an-entry"]).encode(),
        json.dumps([{"Value": "@@@"}]).encode(),
        json.dumps([{"Value": base64.b64encode(b"\xff").decode()}]).encode(),
    ],
)
def test_read_observed_refuses_malformed_consul_values(payload):
    backend = ConsulBackend()
    backend._get = lambda *args, **kwargs: (200, payload, {})

    assert backend.read_observed("node", "landscape") is None


@pytest.mark.parametrize(
    "payload",
    [b'{"not": "a-list"}', json.dumps(["not-an-entry"]).encode()],
)
def test_read_desired_sig_refuses_malformed_consul_shapes(payload):
    backend = ConsulBackend()
    backend._get = lambda *args, **kwargs: (200, payload, {})

    assert backend.read_desired_sig("node", "landscape") is None

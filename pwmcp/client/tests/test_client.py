from __future__ import annotations

import json

from pwmcp_client.contract import load_contract
from pwmcp_client.session import _major_minor


def test_load_contract(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "release": "1.61.0-r5",
        "playwright": {"python": "1.61.0", "protocol": "1.61"},
        "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
        "run_server": {"limits": {"default_lease_seconds": 10, "max_lease_seconds": 20, "max_clients": 2, "idle_recycle_seconds": 30}},
    }))
    contract = load_contract(path)
    assert contract.release == "1.61.0-r5"
    assert contract.max_clients == 2


def test_major_minor_ignores_patch():
    assert _major_minor("1.61.1") == _major_minor("1.61.0")

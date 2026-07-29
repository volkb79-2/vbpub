import pytest

import ciu.transport_ssh as transport_ssh


def test_ssh_sync_rejects_missing_ssh_host_before_transport(monkeypatch, tmp_path):
    def fail_if_invoked(*args, **kwargs):
        raise AssertionError("subprocess must not be invoked")

    monkeypatch.setattr(transport_ssh.subprocess, "run", fail_if_invoked)

    with pytest.raises(ValueError, match=r"\[SPEC J\] ssh_host not configured"):
        transport_ssh.ssh_sync(
            {"ssh_user": "deploy", "ssh_key": "/fake/key"},
            "/local",
            "/remote",
            config={},
            repo_root=tmp_path,
        )

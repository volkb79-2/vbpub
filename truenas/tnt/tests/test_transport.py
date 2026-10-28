import pytest

from tnt import adapters, transport
from tnt.errors import Refused, TransportError, UnsupportedVersion
from tnt.profile import Profile, Secret
from tnt.session import open_session
from tnt.testing import FakeServer


def _open(server, **kw):
    return open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, **kw)


def test_round_trip_logs_in_with_login_ex_never_the_deprecated_method(server):
    session = _open(server)
    assert session.backend.generation == "v27"
    assert "auth.login_ex" in server.called()
    assert "auth.login_with_api_key" not in server.called()
    assert server.logins == 1
    session.close()


def test_ws_is_refused_without_the_loopback_test_hook(server):
    with pytest.raises(TransportError, match="ws:// is refused") as err:
        open_session(server.profile(), secret=server.secret())
    assert err.value.kind == "refused" and server.logins == 0


def test_ws_to_a_non_loopback_host_is_refused_even_with_the_hook():
    p = Profile("x", "ws://nas.example.invalid/api/current", "tnt", auth="plain")
    with pytest.raises(TransportError, match="ws:// is refused"):
        transport.connect(p, Secret("1-k"), allow_loopback_ws=True)


def test_unsupported_scheme():
    p = Profile("x", "http://127.0.0.1/api/current", "tnt")
    with pytest.raises(TransportError, match="unsupported URL scheme"):
        transport.connect(p, Secret("1-k"))


def test_insecure_profile_needs_the_explicit_acknowledgement():
    p = Profile("t", "wss://vm.example.invalid/api/current", "tnt", tls="insecure")
    with pytest.raises(TransportError, match="pass --insecure"):
        transport.connect(p, Secret("1-k"))


def test_verify_flag_follows_tls_mode_and_ack():
    seen = []

    class Client:
        def __init__(self, uri, verify):
            seen.append(verify)

        def login_with_api_key(self, *a, **k):
            pass

        def close(self):
            pass
    secure = Profile("a", "wss://h.example.invalid/api/current", "u")
    insecure = Profile("b", "wss://h.example.invalid/api/current", "u", tls="insecure")
    transport.connect(secure, Secret("1-k"), client_factory=Client)
    transport.connect(secure, Secret("1-k"), insecure_ack=True, client_factory=Client)  # ack alone never lowers verification
    transport.connect(insecure, Secret("1-k"), insecure_ack=True, client_factory=Client)
    assert seen == [True, True, False]


def test_scram_login_uses_channel_binding_over_wss():
    calls = []

    class Client:
        def __init__(self, uri, verify):
            pass

        def login_with_api_key(self, user, key, mech, *, channel_binding):
            calls.append((user, mech.value, channel_binding))

    transport.connect(Profile("a", "wss://h.example.invalid/api/current", "u", auth="scram"), Secret("1-k"), client_factory=Client)
    assert calls == [("u", "SCRAM", True)]


def test_auth_failure_is_fatal_scrubbed_and_not_retried():
    server = FakeServer(api_key="1-therealone")
    with server:
        with pytest.raises(TransportError, match="login to 127.0.0.1") as err:
            open_session(server.profile(), secret=Secret("1-wrongkeyxyz"), allow_loopback_ws=True)
        assert err.value.kind == "auth" and "wrongkeyxyz" not in str(err.value)
        assert server.logins == 1


def test_connect_failure_is_a_transport_error_and_scrubbed():
    def boom(uri, verify):
        raise OSError("refused for 1-leaky")
    with pytest.raises(TransportError, match="cannot connect") as err:
        transport.connect(Profile("a", "wss://h.example.invalid/x", "u"), Secret("1-leaky"), client_factory=boom)
    assert err.value.kind == "connect" and "1-leaky" not in str(err.value)


def test_close_failure_during_failed_login_is_swallowed():
    class Client:
        def __init__(self, uri, verify):
            pass

        def login_with_api_key(self, *a, **k):
            raise ValueError("bad 1-k")

        def close(self):
            raise OSError("already dead")
    with pytest.raises(TransportError, match="login") as err:
        transport.connect(Profile("a", "wss://h.example.invalid/x", "u"), Secret("1-k"), client_factory=Client)
    assert "1-k" not in str(err.value)


def test_default_client_factory_builds_a_real_client_without_connecting():
    # constructing the real client against a closed loopback port fails with a transport error, not a crash
    with pytest.raises(TransportError, match="cannot connect"):
        transport.connect(Profile("a", "wss://127.0.0.1:1/api/current", "u", tls="insecure"), Secret("1-k"), insecure_ack=True)


def test_read_only_guard_allows_reads_and_refuses_writes(server):
    session = _open(server)
    assert session.rpc.call("system.version").startswith("TrueNAS")
    for method in ("pool.create", "disk.wipe", "system.reboot", "pool.dataset.delete"):
        with pytest.raises(Refused, match="read-only"):
            session.rpc.call(method)
    assert all(not m.startswith(("pool.", "system.reboot")) for m in server.called())
    session.close()


def test_write_session_is_default_deny_outside_a_plan(server):
    session = _open(server, allow_write=True)
    assert "WRITE ENABLED" in session.banner()
    with pytest.raises(Refused, match="no-plan"):
        session.rpc.call("pool.create", {})
    assert "pool.create" not in server.called()
    session.close()


def test_call_errors_are_wrapped_and_scrubbed():
    server = FakeServer(fail={"disk.query": "boom 1-fakefakefakefakefakefakefakefake"})
    with server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        with pytest.raises(TransportError, match="disk.query failed") as err:
            session.disks()
        assert "fakefakefake" not in str(err.value) and err.value.kind == "call"
        session.close()


def test_is_read_method_shapes():
    for m in ("disk.query", "disk.details", "system.info", "boot.get_state", "pool.import_find", "auth.me"):
        assert transport.is_read_method(m)
    for m in ("pool.create", "disk.wipe", "boot.attach", "auth.login_ex"):
        assert not transport.is_read_method(m)


def test_job_flag_is_forwarded_and_close_is_quiet():
    class Inner:
        def call(self, method, *params, job=False):
            return (method, params, job)

        def close(self):
            raise OSError("x")
    g = transport.GuardedRpc(Inner(), read_only=False)
    with g.authorized([("pool.scrub", ("tank",), True)], None):
        assert g.call("pool.scrub", "tank", job=True) == ("pool.scrub", ("tank",), True)
    g.close()


def test_guard_without_secret_keeps_the_message():
    class Inner:
        def call(self, method, *params):
            raise RuntimeError("plain failure")
    with pytest.raises(TransportError, match="plain failure"):
        transport.GuardedRpc(Inner()).call("system.info")


def test_session_closes_the_client_when_detection_fails():
    server = FakeServer(version="TrueNAS-24.10.2")
    with server:
        with pytest.raises(UnsupportedVersion):
            open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)


def test_adapter_selection():
    assert adapters.select("TrueNAS-27.0.0-RC.1").generation == "v27"
    assert adapters.select("28.04-MASTER-20261014-1").generation == "v28"
    assert adapters.parse_major("TrueNAS-SCALE-25.10.3") == 25
    with pytest.raises(UnsupportedVersion, match="no adapter"):
        adapters.select("TrueNAS-25.10.3")
    with pytest.raises(UnsupportedVersion, match="cannot read"):
        adapters.select("unknown")


def test_manifest_diff_against_live_method_list(server):
    session = _open(server)
    available = session.rpc.call("core.get_methods")
    assert adapters.missing_methods(session.backend, available) == {}
    trimmed = {m: {} for m in available if m != "disk.details"}
    assert adapters.missing_methods(session.backend, trimmed) == {"disks": ["disk.details"]}
    session.close()


def test_manifests_declare_how_they_were_verified_and_v27_has_no_virt_namespace():
    from tnt.adapters.v27 import V27
    from tnt.adapters.v28 import V28
    assert V27.verified == "reference" and V28.verified == "copied"
    methods = {m for ms in V27.manifest.values() for m in ms}
    assert not any(m.startswith("virt.") for m in methods) and "container.query" in methods

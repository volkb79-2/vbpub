import pytest

from tnt import adapters, transport
from tnt.errors import ProfileError, Refused, TransportError, UnsupportedVersion
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


def test_session_opening_requires_a_secret_loaded_from_the_paired_file(monkeypatch):
    called = []
    monkeypatch.setattr(transport, "connect", lambda *args, **kwargs: called.append((args, kwargs)))
    profile = Profile("lab", "wss://lab.example.invalid/api/current", "operator")
    with pytest.raises(ProfileError, match="paired tnt.secrets.toml"):
        open_session(profile)
    with pytest.raises(ProfileError, match="paired tnt.secrets.toml"):
        open_session(profile, secret="plain-text-is-not-a-Secret")
    assert called == []


def test_ws_to_a_non_loopback_host_is_refused_even_with_the_hook():
    p = Profile("x", "ws://nas.example.invalid/api/current", "tnt", auth="api_key")
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

    transport.connect(Profile("a", "wss://h.example.invalid/api/current", "u", auth="api_key"), Secret("1-k"), client_factory=Client)
    assert calls == [("u", "SCRAM", True)]


def test_password_login_uses_pinned_client_mechanism_and_fake_server_once(server):
    session = open_session(server.profile(auth="password"), secret=server.password_secret(), allow_loopback_ws=True)
    login = next(request for request in server.requests if request["method"] == "auth.login_ex")
    assert login["params"][0] == {
        "mechanism": "PASSWORD_PLAIN",
        "username": "tnt",
        "password": "fake-password-material",
    }
    assert server.logins == 1
    session.close()


def test_password_login_failure_is_fatal_scrubbed_and_not_retried(server):
    profile = server.profile(auth="password")
    with pytest.raises(TransportError, match="login to 127.0.0.1") as err:
        open_session(profile, secret=Secret("wrong-password-material"), allow_loopback_ws=True)
    assert err.value.kind == "auth"
    assert "wrong-password-material" not in str(err.value)
    assert server.logins == 1


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
    from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan

    action = Action("T-JOB", Kind.MUTATE, "start app", (RpcCall("app.start", ("example-app",), True),),
                    (RpcCall("app.query", ([], {})),))
    plan = make_plan(action, g)
    assert apply_plan(plan, g, allow_write=True) == [("app.start", ("example-app",), True)]
    g.close()


def test_public_authorized_context_cannot_create_a_write_grant():
    g = transport.GuardedRpc(type("Inner", (), {"call": lambda self, *a, **k: "done", "close": lambda self: None})(),
                             read_only=False)
    with pytest.raises(Refused, match="apply_plan"):
        with g.authorized([("user.delete", (8, {}), False)], None):
            g.call("user.delete", 8, {})


def test_open_session_records_live_v27_capabilities_and_blocks_missing_calls():
    from tnt.adapters.v27 import V27

    methods = [method for method in V27.supported_methods() if method != "disk.details"]
    server = FakeServer(methods=methods)
    with server:
        session = _open(server)
        assert session.backend.verified == "reference"
        assert session.missing_methods == {"disks": ["disk.details"]}
        assert "disk.details" in session.banner()
        with pytest.raises(Refused, match="not reported by the live v27 capability list"):
            session.rpc.call("disk.details", {"type": "BOTH"})
        assert "disk.details" not in [request["method"] for request in server.requests]
        session.close()


def test_open_session_rejects_non_string_capability_names_and_closes_client(monkeypatch):
    class Client:
        closed = False

        def call(self, method, *params, job=False):
            if method == "system.version":
                return "TrueNAS-SCALE-27.0"
            if method == "core.get_methods":
                return {27: {}}
            raise AssertionError(method)

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(transport, "connect", lambda *args, **kwargs: client)
    profile = Profile("fake", "wss://nas.example.invalid/api/current", "tnt")
    with pytest.raises(TransportError, match="invalid v27 WebSocket method list"):
        open_session(profile, secret=Secret("fake-key"))
    assert client.closed


def test_guarded_rpc_capability_set_is_single_use_and_requires_mapping():
    inner = type("Inner", (), {"call": lambda self, *a, **k: "done", "close": lambda self: None})()
    rpc = transport.GuardedRpc(inner)
    with pytest.raises(Refused, match="metadata object"):
        rpc._set_live_capabilities([])
    rpc._set_live_capabilities({"system.info": {}})
    with pytest.raises(Refused, match="already set"):
        rpc._set_live_capabilities({"system.version": {}})


def test_only_the_reviewed_container_metrics_event_can_be_subscribed():
    class Inner:
        def __init__(self):
            self.events = []

        def subscribe(self, name, callback, payload=None, sync=False):
            self.events.append(("subscribe", name, sync))
            return "event-1"

        def unsubscribe(self, subscription_id):
            self.events.append(("unsubscribe", subscription_id))

    inner = Inner()
    rpc = transport.GuardedRpc(inner)
    with pytest.raises(Refused, match="only the container.metrics"):
        rpc.subscribe_event("system.health", lambda *_: None)
    subscription = rpc.subscribe_event("container.metrics", lambda *_args, **_kwargs: None)
    assert subscription == "event-1"
    with pytest.raises(Refused, match="does not own"):
        rpc.unsubscribe_event("unowned")
    rpc.unsubscribe_event(subscription)
    assert inner.events == [("subscribe", "container.metrics", True), ("unsubscribe", "event-1")]


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


def test_manifest_declares_v27_and_has_no_virt_namespace():
    from tnt.adapters.v27 import V27
    assert V27.verified == "reference"
    methods = set(V27.supported_methods())
    assert not any(m.startswith("virt.") for m in methods) and "container.query" in methods


def test_event_subscription_capability_and_lifecycle_failures_are_explicit():
    class NoEvents:
        def call(self, method, *params, job=False):
            return None
        def close(self):
            pass

    rpc = transport.GuardedRpc(NoEvents())
    with pytest.raises(TransportError, match="does not support event subscriptions"):
        rpc.subscribe_event("container.metrics", lambda *_args, **_kwargs: None)
    with pytest.raises(Refused, match="only the container.metrics"):
        rpc.subscribe_event("container.metrics", None)

    class BrokenSubscribe:
        def subscribe(self, *args, **kwargs):
            raise OSError("subscribe failed")
    rpc = transport.GuardedRpc(BrokenSubscribe(), secret=Secret("fake-event-secret"))
    with pytest.raises(TransportError, match="cannot subscribe") as error:
        rpc.subscribe_event("container.metrics", lambda *_args, **_kwargs: None)
    assert "fake-event-secret" not in str(error.value)

    class EmptyId:
        def subscribe(self, *args, **kwargs):
            return ""
    with pytest.raises(TransportError, match="invalid subscription id"):
        transport.GuardedRpc(EmptyId()).subscribe_event("container.metrics", lambda *_args, **_kwargs: None)

    class NoUnsubscribe:
        def subscribe(self, *args, **kwargs):
            return "owned"
    rpc = transport.GuardedRpc(NoUnsubscribe())
    subscription = rpc.subscribe_event("container.metrics", lambda *_args, **_kwargs: None)
    with pytest.raises(TransportError, match="cannot cancel"):
        rpc.unsubscribe_event(subscription)

    class BrokenUnsubscribe:
        def subscribe(self, *args, **kwargs):
            return "owned"
        def unsubscribe(self, subscription_id):
            raise OSError("unsubscribe failed")
    rpc = transport.GuardedRpc(BrokenUnsubscribe())
    subscription = rpc.subscribe_event("container.metrics", lambda *_args, **_kwargs: None)
    with pytest.raises(TransportError, match="cannot unsubscribe"):
        rpc.unsubscribe_event(subscription)


def test_unexpected_auth_mode_and_read_refusal_are_wrapped(monkeypatch):
    class Client:
        closed = False
        def __init__(self, uri, verify):
            pass
        def close(self):
            self.closed = True

    client = Client("", True)
    profile = Profile("odd", "wss://host.example.invalid/api/current", "u", auth="future-auth")
    with pytest.raises(TransportError, match="unsupported authentication mode"):
        transport.connect(profile, Secret("fake"), client_factory=lambda uri, verify: client)
    assert client.closed

    real = transport.refusal_for
    monkeypatch.setattr(transport, "refusal_for", lambda method, params, state: "synthetic read refusal")
    try:
        with pytest.raises(Refused, match="synthetic read refusal"):
            transport.GuardedRpc(type("Inner", (), {"call": lambda self, *a, **k: None})()).call("system.info")
    finally:
        monkeypatch.setattr(transport, "refusal_for", real)

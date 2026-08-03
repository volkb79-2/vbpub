"""CR-15 (RISK-005): control-plane authentication and operator identity.

The invariant the whole product rests on is "the human owns direction" --
`POST /api/decision/reply` is the mechanism it runs on, and before this
package anything that could open a TCP connection to the daemon's port could
supply the human's answer. These oracles assert the ARTIFACT of every
refusal, never just its status code: a 401 that still mutated, or that leaked
which decision ids exist, would be worthless.

Scope map (siblings that must not regress):
- test_config_ui.py owns the CSRF refusals themselves (Content-Type / Origin);
  here they are only re-asserted as still-present ahead of authentication.
- test_daemon.py / test_intake_ui.py / test_carver.py own each endpoint's own
  validation contract; here every endpoint is exercised only through the
  credential gate.

Determinism: the HTTP server is started synchronously via `_start_http()` and
stopped via `_stop_http()`, so no test in this file waits on a thread, polls a
deadline, or sleeps -- there is no wall-clock budget anywhere that could flip
a verdict on a slow or loaded machine (STANDING.md L20). The urllib timeouts
are deliberately generous failsafes against hanging the suite, never oracles.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import socket
import stat
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import structlog.contextvars

from nyxloom import (
    cli, config, control_auth, daemon, decisions, lint, log, paths, reconcile, render, storage,
)
from nyxloom.types import ActorKind, EventType


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Same process-global logging safety net as test_config_ui.py's copy:
    this file drives a live daemon whose handlers log, and a leaked handler
    lands in a sibling test under xdist, not in this one."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py)

ROUTES_TOML = """\
revision = "test-rev"

[tiers.flash-high]
routes = ["fake-cli"]

[tiers.frontier-review]
routes = ["fake-cli"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
"""

# One VALID body per mutating path. Valid on purpose: a refusal must be
# distinguishable from a 400, so these bodies would be accepted (200/404 on
# the target's own merits) if the credential were present.
MUTATIONS: list[tuple[str, dict]] = [
    ("/api/config/policy", {"project": "demo", "key": "max_active_tasks", "value": 5}),
    ("/api/config/pause", {"project": "demo", "mode": "drain-agents"}),
    ("/api/config/tier", {"tier": "flash-high", "routes": ["fake-cli"]}),
    ("/api/decision/reply", {"decision_id": "D-001", "text": "option b"}),
    ("/api/intake", {"project": "demo", "text": "add a dark mode toggle"}),
    ("/api/finding/promote", {"project": "demo", "finding_id": "fnd-000000000001"}),
    ("/api/config/log-level", {"level": "debug"}),
]

UNAUTHENTICATED_BODY = b'{"error":"mutation authentication required"}'
STORE_UNAVAILABLE_BODY = b'{"error":"mutation authentication unavailable"}'


def _set_ephemeral_http_port(cfg) -> None:
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


@pytest.fixture()
def served(tmp_state, sample_project, monkeypatch):
    """A bound, serving Daemon HTTP surface -- with NO reconcile loop.

    `_start_http()` returns only once the socket is bound and the credential
    store bootstrapped, so a request issued immediately after cannot race the
    server. `run()` is deliberately not used: it would add a reconcile thread
    this file has nothing to say about, and its readiness would have to be
    polled."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    paths.routes_path().write_text(ROUTES_TOML, encoding="utf-8")
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    d._start_http()
    try:
        yield d
    finally:
        d._stop_http()


def _base(d) -> str:
    return f"http://127.0.0.1:{d.http_port}"


def _store() -> control_auth.CredentialStore:
    return control_auth.CredentialStore(paths.daemon_dir())


def _bearer() -> dict[str, str]:
    return control_auth.authorization_header(_store().load())


def _json_headers(**extra: str) -> dict[str, str]:
    return {"Content-Type": "application/json", **extra}


def _post_raw(d, path: str, body: dict,
              headers: dict[str, str]) -> tuple[int, bytes]:
    """Status + RAW body bytes.

    Indistinguishability is a byte-level property: two refusals that differ
    only in whitespace or key order are still an oracle for an attacker, so
    these tests compare the bytes, not a parsed dict. The timeout is a
    generous failsafe against hanging the suite, never a measured budget."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{d and _base(d)}{path}", data=data,
                                 method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(d, path: str, body: dict, **extra_headers: str) -> tuple[int, bytes]:
    """Authenticated POST (the happy path every endpoint now requires)."""
    return _post_raw(d, path, body, _json_headers(**_bearer(), **extra_headers))


def _ledger() -> list:
    return list(storage.iter_events(control_auth.CONTROL_LEDGER_PROJECT))


def _refusals() -> list:
    return [e for e in _ledger() if e.type is EventType.CONTROL_MUTATION_REFUSED]


def _capture_errors(monkeypatch) -> list[tuple[str, dict]]:
    """Capture `control_auth`'s ERROR records without configuring logging.

    Patches the module attribute that OWNS the logger (not the shared
    structlog root), so teardown restores exactly what it found and a sibling
    test on the same xdist worker cannot inherit the capture."""
    errors: list[tuple[str, dict]] = []
    real_log = control_auth.log

    class _CapturingLog:
        def error(self, msg, **kw):
            errors.append((msg, dict(kw)))
            return real_log.error(msg, **kw)

        def warning(self, msg, **kw):
            errors.append((msg, dict(kw)))
            return real_log.warning(msg, **kw)

        def __getattr__(self, name):
            return getattr(real_log, name)

    monkeypatch.setattr(control_auth, "log", _CapturingLog())
    return errors


class _Snapshot:
    """Everything a refused mutation must leave untouched.

    Compared as bytes/dicts rather than "no exception raised": the point of
    the package is that a refusal has no effect, so the oracle is the state of
    the world, not the shape of the response."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.project_toml = (cfg.root / ".nyxloom" / "project.toml").read_bytes()
        self.inbox = (cfg.root / cfg.decisions_inbox).read_bytes()
        self.routes = paths.routes_path().read_bytes()
        self.events = [e.to_dict() for e in storage.iter_events("demo")]
        self.paused = paths.pause_flag("demo").exists()
        self.log_level_override = paths.daemon_log_level_path().exists()

    def assert_unchanged(self, why: str = "") -> None:
        cfg = self.cfg
        assert (cfg.root / ".nyxloom" / "project.toml").read_bytes() == self.project_toml, why
        assert (cfg.root / cfg.decisions_inbox).read_bytes() == self.inbox, why
        assert paths.routes_path().read_bytes() == self.routes, why
        assert [e.to_dict() for e in storage.iter_events("demo")] == self.events, why
        assert paths.pause_flag("demo").exists() == self.paused, why
        assert paths.daemon_log_level_path().exists() == self.log_level_override, why


# ==========================================================================
# Every mutating endpoint requires a credential -- enumerated from the
# daemon's OWN routing table, so a new endpoint cannot quietly opt out.
# ==========================================================================

def test_mutation_path_table_matches_the_oracles_in_this_file():
    """The guard that keeps the parametrized oracles below honest.

    Every test in this file that says "every mutating endpoint" iterates
    MUTATIONS. If someone adds a route to `_CONFIG_POST_PATHS` without adding
    it here, that route would silently escape all of them -- so assert the two
    sets are equal, and fail with the missing path named."""
    assert {path for path, _body in MUTATIONS} == set(daemon._CONFIG_POST_PATHS)


@pytest.mark.parametrize("path,body", MUTATIONS, ids=[p for p, _ in MUTATIONS])
def test_unauthenticated_mutation_is_refused_audited_and_has_no_effect(
        served, sample_project, path, body):
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, path, body, _json_headers())

    assert status == 401
    assert raw == UNAUTHENTICATED_BODY
    snapshot.assert_unchanged(f"{path} mutated despite refusal")

    # Exactly ONE audited refusal, naming the path and the reason and nothing
    # else -- no body, no target id, no header value.
    refusals = _refusals()
    assert len(refusals) == 1
    assert refusals[0].payload == {"path": path,
                                   "reason": "invalid-or-missing-credential"}
    assert refusals[0].actor.kind is ActorKind.OPERATOR
    assert refusals[0].actor.id == control_auth.UNAUTHENTICATED_ACTOR_ID
    # The refusal actor is unforgeable: no real operator id can equal it.
    with pytest.raises(control_auth.CredentialStoreError):
        _store().rotate(control_auth.UNAUTHENTICATED_ACTOR_ID)


@pytest.mark.parametrize("path,body", MUTATIONS, ids=[p for p, _ in MUTATIONS])
def test_wrong_credential_is_refused_identically_to_a_missing_one(
        served, sample_project, path, body):
    """A caller must not learn from the response whether it guessed a
    well-formed credential -- the refusal is byte-identical either way."""
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, path, body, _json_headers(
        Authorization="Bearer " + "x" * 43))

    assert status == 401
    assert raw == UNAUTHENTICATED_BODY
    snapshot.assert_unchanged(f"{path} mutated on a wrong credential")
    assert len(_refusals()) == 1


@pytest.mark.parametrize("header,why", [
    ("", "empty header value"),
    ("Bearer", "scheme with no token"),
    ("Bearer ", "scheme with empty token"),
    ("Basic {credential}", "wrong scheme carrying the right value"),
    ("{credential}", "raw value with no scheme"),
    ("Bearer {credential}extra", "right value with a suffix"),
    ("Bearer x{credential}", "right value with a prefix"),
])
def test_malformed_authorization_headers_are_refused(served, sample_project,
                                                     header, why):
    credential = _store().load().credential
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            _json_headers(Authorization=header.format(
                                credential=credential)))

    assert status == 401, why
    assert raw == UNAUTHENTICATED_BODY, why
    snapshot.assert_unchanged(why)


def test_duplicate_authorization_headers_are_refused(served, sample_project):
    """Two Authorization headers are ambiguous -- refuse rather than pick one,
    which is how a proxy-injected second header becomes an escalation.
    urllib collapses duplicates, so send the request on a raw connection."""
    import http.client

    snapshot = _Snapshot(sample_project)
    credential = _store().load().credential
    conn = http.client.HTTPConnection("127.0.0.1", served.http_port, timeout=60)
    try:
        body = json.dumps({"project": "demo", "mode": "drain-agents"}).encode("utf-8")
        conn.putrequest("POST", "/api/config/pause")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.putheader("Authorization", f"Bearer {credential}")
        conn.putheader("Authorization", f"Bearer {credential}")
        conn.endheaders()
        conn.send(body)
        resp = conn.getresponse()
        assert resp.status == 401
        assert resp.read() == UNAUTHENTICATED_BODY
    finally:
        conn.close()
    snapshot.assert_unchanged("duplicate Authorization headers mutated state")


def test_bearer_scheme_is_case_insensitive(served, sample_project):
    """RFC 7235 makes the scheme token case-insensitive; a client that sends
    "bearer" must not be locked out of the emergency brake."""
    credential = _store().load().credential
    status, _raw = _post_raw(served, "/api/config/pause",
                             {"project": "demo", "mode": "drain-agents"},
                             _json_headers(Authorization=f"bearer {credential}"))
    assert status == 200
    assert paths.pause_flag("demo").exists()


# ==========================================================================
# The authenticated path: a NAMED operator reaches the endpoint, and that
# identity lands in the resulting domain events.
# ==========================================================================

def test_authenticated_pause_names_the_operator_in_the_event(served):
    """The acceptance criterion the audit trail exists for: not "ui" (an
    interface name) but the operator the credential is bound to."""
    record = _store().rotate("alice")
    status, _raw = _post_raw(
        served, "/api/config/pause", {"project": "demo", "mode": "drain-agents"},
        _json_headers(**control_auth.authorization_header(record)))
    assert status == 200

    set_events = [e for e in storage.iter_events("demo")
                  if e.type is EventType.PAUSE_SET]
    assert len(set_events) == 1
    assert set_events[0].actor.kind is ActorKind.OPERATOR
    assert set_events[0].actor.id == "alice"
    assert not _refusals()


def test_authenticated_policy_and_tier_changes_name_the_operator(served, sample_project):
    record = _store().rotate("bob")
    headers = _json_headers(**control_auth.authorization_header(record))

    assert _post_raw(served, "/api/config/policy",
                     {"project": "demo", "key": "max_active_tasks", "value": 5},
                     headers)[0] == 200
    assert _post_raw(served, "/api/config/tier",
                     {"tier": "flash-high", "routes": ["fake-cli"]},
                     headers)[0] == 200

    changed = [e for e in storage.iter_events("demo")
               if e.type is EventType.CONFIG_CHANGED]
    assert [e.payload["scope"] for e in changed] == ["policy", "routes"]
    assert {e.actor.id for e in changed} == {"bob"}
    assert {e.actor.kind for e in changed} == {ActorKind.OPERATOR}


def test_authenticated_decision_reply_names_the_operator_and_audits_the_turn(
        served, sample_project, monkeypatch):
    """The sharpest edge of RISK-005. The chat turn itself is stubbed (its
    mechanics are test_decision_chat.py's), but the ACTOR the endpoint hands
    it must be the authenticated operator, and the HTTP turn must leave a
    durable marker even when the turn resolves nothing."""
    from nyxloom import decision_chat

    decision_id = decisions.open_decision(sample_project, "Ship it?", "resume")
    seen = []
    monkeypatch.setattr(decision_chat, "advance_chat",
                        lambda cfg, project, did, text, actor=None:
                        seen.append((project, did, text, actor)) or "ok")

    record = _store().rotate("carol")
    status, _raw = _post_raw(
        served, "/api/decision/reply", {"decision_id": decision_id, "text": "option b"},
        _json_headers(**control_auth.authorization_header(record)))
    assert status == 200

    assert len(seen) == 1
    assert seen[0][3].id == "carol"
    assert seen[0][3].kind is ActorKind.OPERATOR

    recorded = [e for e in storage.iter_events("demo")
                if e.type is EventType.DECISION_REPLY_RECORDED]
    assert len(recorded) == 1
    assert recorded[0].decision_id == decision_id
    assert recorded[0].actor.id == "carol"


def test_decision_resolution_event_carries_the_operator_identity(sample_project, monkeypatch):
    """`advance_chat` unstubbed, one layer down: when a reply RESOLVES the
    decision, the DECISION_RESOLVED event -- the durable answer to "who
    answered this" -- must name the operator, not the decision agent."""
    from nyxloom import decision_chat

    paths.routes_path().write_text(ROUTES_TOML, encoding="utf-8")
    decision_id = decisions.open_decision(sample_project, "Ship it?", "resume")
    # The agent turn is a subprocess seam, not this package's subject: canned
    # here so the reply deterministically carries a DECISION: line.
    monkeypatch.setattr(decision_chat, "_run_subprocess_turn",
                        lambda *a, **kw: ("DECISION: option b\nrationale", "sess-1"))
    monkeypatch.setattr(decision_chat, "_post_feedback", lambda *a, **kw: None)

    operator = control_auth.OperatorCredential("dave", "x" * 43, 1)
    decision_chat.advance_chat(sample_project, "demo", decision_id, "go with b",
                               actor=operator.actor)

    resolved = [e for e in storage.iter_events("demo")
                if e.type is EventType.DECISION_RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].actor.kind is ActorKind.OPERATOR
    assert resolved[0].actor.id == "dave"
    # The DECISION: line was authored by the agent; the AUTHORITY is still the
    # operator whose message drove the turn.
    assert resolved[0].actor.kind is not ActorKind.FRONTIER_SESSION

    # And there is no way to reach this code path unattributed: `actor` is a
    # required keyword argument, so no call site can produce an anonymous
    # answer to a human decision.
    other = decisions.open_decision(sample_project, "And this?", "resume")
    with pytest.raises(TypeError):
        decision_chat.advance_chat(sample_project, "demo", other, "yes")
    assert not [e for e in storage.iter_events("demo")
                if e.type is EventType.DECISION_RESOLVED and e.decision_id == other]


def test_log_level_flip_audits_the_operator_without_touching_project_history(served):
    """D-L4 refined, not repealed: the flip is an authenticated control-plane
    mutation, so it names its operator in the CONTROL ledger -- while the
    project's replayable event log stays byte-identical."""
    record = _store().rotate("erin")
    before = [e.to_dict() for e in storage.iter_events("demo")]

    status, _raw = _post_raw(served, "/api/config/log-level", {"level": "warning"},
                            _json_headers(**control_auth.authorization_header(record)))
    assert status == 200
    assert [e.to_dict() for e in storage.iter_events("demo")] == before

    changed = [e for e in _ledger() if e.type is EventType.CONFIG_CHANGED]
    assert len(changed) == 1
    assert changed[0].actor.id == "erin"
    assert changed[0].payload == {"scope": "daemon", "key": "log-level",
                                  "old": "info", "new": "warning"}


# ==========================================================================
# No target leakage: a refusal cannot be used to probe what exists.
# ==========================================================================

def test_refused_decision_reply_cannot_distinguish_a_real_id_from_a_fake_one(
        served, sample_project):
    """The probe an attacker actually wants: which decisions are open? The
    refusal happens before the body is read, so a real id and a fabricated one
    produce identical bytes AND identical audit records."""
    real_id = decisions.open_decision(sample_project, "Ship it?", "resume")
    snapshot = _Snapshot(sample_project)

    real = _post_raw(served, "/api/decision/reply",
                     {"decision_id": real_id, "text": "option b"}, _json_headers())
    fake = _post_raw(served, "/api/decision/reply",
                     {"decision_id": "D-999", "text": "option b"}, _json_headers())

    assert real == fake == (401, UNAUTHENTICATED_BODY)
    snapshot.assert_unchanged("a refused decision reply changed state")

    payloads = [e.payload for e in _refusals()]
    assert payloads == [{"path": "/api/decision/reply",
                         "reason": "invalid-or-missing-credential"}] * 2
    # Nothing anywhere in the ledger records which id was probed.
    ledger_text = json.dumps([e.to_dict() for e in _ledger()])
    assert real_id not in ledger_text
    assert "D-999" not in ledger_text


def test_refused_config_post_cannot_distinguish_a_real_project_from_a_ghost(
        served, sample_project):
    """Same property one endpoint over -- and the contrast that proves the
    test is not hollow: WITH a credential the endpoint really does distinguish
    them (200 vs 404), so the identical 401s are the gate working, not the
    endpoint being blind."""
    real = _post_raw(served, "/api/config/policy",
                     {"project": "demo", "key": "max_active_tasks", "value": 5},
                     _json_headers())
    ghost = _post_raw(served, "/api/config/policy",
                      {"project": "ghost", "key": "max_active_tasks", "value": 5},
                      _json_headers())
    assert real == ghost == (401, UNAUTHENTICATED_BODY)

    authed_real = _post(served, "/api/config/policy",
                        {"project": "demo", "key": "max_active_tasks", "value": 5})
    authed_ghost = _post(served, "/api/config/policy",
                         {"project": "ghost", "key": "max_active_tasks", "value": 5})
    assert authed_real[0] == 200
    assert authed_ghost[0] == 404


# ==========================================================================
# Rotation
# ==========================================================================

def test_rotation_invalidates_the_prior_credential_immediately(served, sample_project):
    """The acceptance criterion is "within one reconcile pass"; the store is
    re-read per request, so the old value is dead on the very next one -- with
    no restart and no cache to expire."""
    old_headers = _json_headers(**_bearer())
    old_credential = _store().load().credential

    assert _post_raw(served, "/api/config/pause",
                     {"project": "demo", "mode": "drain-agents"}, old_headers)[0] == 200
    paths.pause_flag("demo").unlink()

    rotated = _store().rotate()
    assert rotated.generation == 2
    assert rotated.credential != old_credential

    snapshot = _Snapshot(sample_project)
    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"}, old_headers)
    assert (status, raw) == (401, UNAUTHENTICATED_BODY)
    snapshot.assert_unchanged("the rotated-out credential still mutated state")

    assert _post_raw(served, "/api/config/pause",
                     {"project": "demo", "mode": "drain-agents"},
                     _json_headers(**control_auth.authorization_header(rotated)))[0] == 200


def test_rotation_is_atomic_and_leaves_a_0600_store_without_the_old_value():
    store = _store()
    first = store.ensure("alice")
    second = store.rotate()

    assert second.operator_id == "alice"        # identity carries forward
    assert second.generation == first.generation + 1
    assert stat.S_IMODE(store.path.lstat().st_mode) == 0o600
    raw = store.path.read_bytes()
    assert first.credential.encode() not in raw
    assert second.credential.encode() in raw
    # No temp file survives a successful rotation.
    assert [p.name for p in store.path.parent.iterdir()
            if p.name.startswith(f".{store.path.name}")] == []


def test_rotation_survives_a_directory_that_cannot_be_fsynced(tmp_state):
    """Honest semantics for the one failure that happens AFTER the swap.

    Once `os.replace` lands, the new credential is what authenticates and the
    old one is dead. Reporting "rotation failed" for the parent-directory
    fsync would leave the operator holding a value that no longer works and no
    copy of the one that does -- so the rotation stands, the returned record
    is the live one, and only the durability is downgraded to a warning."""
    store = _store()
    store.ensure("alice")
    real_fsync = os.fsync

    def _fail_on_directories(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(22, "Invalid argument")
        return real_fsync(fd)

    errors: list[tuple[str, dict]] = []
    real_log = control_auth.log

    class _CapturingLog:
        def warning(self, msg, **kw):
            errors.append((msg, dict(kw)))

        def __getattr__(self, name):
            return getattr(real_log, name)

    original_log = control_auth.log
    original_fsync = os.fsync
    try:
        control_auth.log = _CapturingLog()
        os.fsync = _fail_on_directories
        rotated = store.rotate("alice")
    finally:
        os.fsync = original_fsync
        control_auth.log = original_log

    # The rotation HAPPENED: the returned value is the one on disk and the one
    # that now authenticates.
    assert store.load().credential == rotated.credential
    assert rotated.generation == 2
    assert any("durable" in msg for msg, _kw in errors), errors


def test_a_store_that_grows_under_the_read_is_refused_not_parsed_as_a_prefix(
        tmp_state):
    """The size fstat decides how many bytes are consumed, so an append
    between the stat and the read would leave a valid PREFIX parsed and the
    rest silently ignored -- a store whose real content nobody validated. The
    growth is injected at the syscall seam: deterministic, no threads."""
    store = _store()
    legitimate = store.ensure("alice")
    real_fstat = os.fstat
    grown: list[int] = []

    def _grow_after_first_stat(fd):
        info = real_fstat(fd)
        if stat.S_ISREG(info.st_mode) and not grown:
            grown.append(fd)
            with open(store.path, "ab") as stream:
                stream.write(b'{"appended":"by someone else"}\n')
        return info

    original_fstat = os.fstat
    try:
        os.fstat = _grow_after_first_stat
        with pytest.raises(control_auth.CredentialStoreError):
            store.load()
    finally:
        os.fstat = original_fstat

    assert grown, "the append was never injected -- load did not fstat the store"
    # ...and the loader never returned the prefix it could have parsed.
    assert legitimate.credential.encode() in store.path.read_bytes()


def test_an_io_fault_reading_the_store_fails_closed(tmp_state):
    """A read that faults mid-way is a store that cannot be trusted, not a
    reason to authenticate against whatever came back."""
    store = _store()
    store.ensure("alice")
    original_read = os.read

    def _eio(fd, size):
        raise OSError(5, "Input/output error")

    try:
        os.read = _eio
        with pytest.raises(control_auth.CredentialStoreError):
            store.load()
    finally:
        os.read = original_read


def test_hard_linked_store_is_refused(tmp_state):
    """A second name for the credential inode is a second, differently-
    permissioned door to the secret -- and `rotate`'s os.replace never makes
    one, so its presence means somebody else did."""
    store = _store()
    store.ensure("alice")
    os.link(store.path, store.path.with_name("second-name.json"))

    with pytest.raises(control_auth.CredentialStoreError):
        store.load()


def test_load_validates_and_reads_one_file_description(tmp_state):
    """The TOCTOU claim in the module docstring, exercised rather than read.

    A writer of the state directory replaces the store the instant after the
    loader opens it. A path-based `lstat`-then-read would validate the honest
    file and then read the attacker's; validating with `fstat` on the SAME
    descriptor cannot, so the legitimate credential comes back. The race is
    SIMULATED at the syscall seam -- no threads, no timing, deterministic."""
    store = _store()
    legitimate = store.ensure("alice")

    attacker = store.path.with_name("attacker.json")
    attacker.write_text(json.dumps({
        "schema_version": control_auth.SCHEMA_VERSION,
        "operator_id": "mallory", "credential": "m" * 43, "generation": 99,
    }), encoding="utf-8")
    attacker.chmod(0o600)

    real_open = os.open
    swapped: list[str] = []

    def _swap_after_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(path) == str(store.path) and not swapped:
            swapped.append(str(path))
            os.replace(attacker, store.path)     # the attacker wins the race
        return fd

    original_open = os.open
    try:
        os.open = _swap_after_open
        record = store.load()
    finally:
        os.open = original_open

    assert swapped, "the race was never simulated -- load did not open the store"
    assert record.credential == legitimate.credential
    assert record.operator_id == "alice"


def test_rotation_can_rename_the_operator(tmp_state):
    store = _store()
    store.ensure("alice")
    assert store.rotate("bob").operator_id == "bob"
    assert store.load().operator_id == "bob"


# ==========================================================================
# Fail closed: an untrustworthy store refuses mutations, it never falls back
# ==========================================================================

def _chmod_group_readable(path) -> None:
    path.chmod(0o644)


def _truncate(path) -> None:
    path.write_bytes(b"")


def _garbage(path) -> None:
    path.write_bytes(b"not json at all")


def _partial_write(path) -> None:
    path.write_bytes(b'{"schema_version":1,"operator_id":"alice","credenti')


def _drop_a_key(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    del value["generation"]
    path.write_text(json.dumps(value), encoding="utf-8")


def _extra_key(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["role"] = "admin"
    path.write_text(json.dumps(value), encoding="utf-8")


def _future_schema(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema_version"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")


def _short_credential(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["credential"] = "short"
    path.write_text(json.dumps(value), encoding="utf-8")


def _bad_operator_id(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["operator_id"] = "../../etc/passwd"
    path.write_text(json.dumps(value), encoding="utf-8")


def _bool_generation(path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["generation"] = True
    path.write_text(json.dumps(value), encoding="utf-8")


def _delete(path) -> None:
    path.unlink()


def _replace_with_symlink(path) -> None:
    target = path.with_name("elsewhere.json")
    target.write_bytes(path.read_bytes())
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)


def _replace_with_directory(path) -> None:
    path.unlink()
    path.mkdir()


BROKEN_STORES = [
    (_chmod_group_readable, "mode widened to 0644"),
    (_truncate, "zero-length file"),
    (_garbage, "not JSON"),
    (_partial_write, "truncated mid-write"),
    (_drop_a_key, "missing key"),
    (_extra_key, "unexpected key"),
    (_future_schema, "unsupported schema_version"),
    (_short_credential, "credential too short"),
    (_bad_operator_id, "operator id fails validation"),
    (_bool_generation, "bool masquerading as int generation"),
    (_delete, "store deleted under the running daemon"),
    (_replace_with_symlink, "symlink swap"),
    (_replace_with_directory, "directory in place of the file"),
]


@pytest.mark.parametrize("mutate,why", BROKEN_STORES,
                         ids=[why for _fn, why in BROKEN_STORES])
def test_untrustworthy_store_refuses_every_mutation_and_audits_it(
        served, sample_project, mutate, why):
    """A trust root that cannot be read is not a reason to fall back to a
    previous value, and not a reason to keep serving mutations: 503, audited,
    with the project untouched."""
    store = _store()
    mutate(store.path)
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            _json_headers(Authorization="Bearer " + "x" * 43))

    assert status == 503, why
    assert raw == STORE_UNAVAILABLE_BODY, why
    snapshot.assert_unchanged(why)

    refusals = _refusals()
    assert len(refusals) == 1, why
    assert refusals[0].payload == {"path": "/api/config/pause",
                                   "reason": "credential-store-unavailable"}


@pytest.mark.parametrize("mutate,why", BROKEN_STORES,
                         ids=[why for _fn, why in BROKEN_STORES])
def test_untrustworthy_store_raises_rather_than_returning_a_credential(
        tmp_state, mutate, why):
    """The unit-level half: `load` never returns a value it could not fully
    validate, so no caller can accidentally authenticate against one."""
    store = _store()
    store.ensure("alice")
    mutate(store.path)
    with pytest.raises(control_auth.CredentialStoreError):
        store.load()
    with pytest.raises(control_auth.CredentialStoreError):
        store.authenticate({"Authorization": "Bearer x"})


def test_ensure_does_not_overwrite_or_repair_an_untrustworthy_store(tmp_state):
    """`ensure` bootstraps ONLY when the store is absent. Silently replacing a
    file it cannot read would let a mode-widened store be swapped for a fresh
    credential behind the operator's back -- and would make the daemon issue a
    new secret nobody asked for on every boot."""
    store = _store()
    first = store.ensure("alice")
    store.path.chmod(0o644)
    with pytest.raises(control_auth.CredentialStoreError):
        store.ensure("alice")
    assert first.credential.encode() in store.path.read_bytes()


def test_forced_rotation_is_the_documented_recovery_path(tmp_state):
    store = _store()
    store.ensure("alice")
    store.path.chmod(0o644)

    with pytest.raises(control_auth.CredentialStoreError):
        store.rotate()

    recovered = store.rotate(force=True)
    # Neither the identity nor the generation can be read out of a store the
    # loader refuses, so a forced rotation is honest about that: default
    # identity, generation restarted -- NOT a fabricated carry-forward.
    assert recovered.operator_id == control_auth.default_operator_id()
    assert recovered.generation == 1
    assert stat.S_IMODE(store.path.lstat().st_mode) == 0o600
    assert store.load().credential == recovered.credential

    # An identity can still be named explicitly on the way back.
    store.path.chmod(0o644)
    assert store.rotate("alice", force=True).operator_id == "alice"


def test_bootstrap_losing_the_creation_race_adopts_the_winner_never_a_second_secret(
        tmp_state):
    """Two daemons (or a daemon and `nyxloom auth bootstrap`) can reach an
    absent store at once. The O_EXCL create is what makes that safe: the loser
    must adopt the winner's credential, NOT mint a second one -- an operator
    holding the value printed by the first would otherwise be silently locked
    out. The race is simulated at the syscall seam: the winner's file appears
    between the existence check and the exclusive create."""
    store = _store()
    winner = control_auth.CredentialStore(paths.daemon_dir())
    real_open = os.open
    raced: list[str] = []

    def _create_the_winner_first(path, flags, *args, **kwargs):
        if str(path) == str(store.path) and not raced:
            raced.append(str(path))
            winner.ensure("alice")               # the other process gets there
        return real_open(path, flags, *args, **kwargs)

    original_open = os.open
    try:
        os.open = _create_the_winner_first
        loser = store.ensure("bob")
    finally:
        os.open = original_open

    assert raced, "the race was never simulated"
    assert loser.credential == winner.load().credential
    assert loser.operator_id == "alice"          # the winner's identity stands
    assert store.load().credential == loser.credential


def test_a_failed_bootstrap_write_leaves_no_half_written_store(tmp_state):
    """A partially written store would fail the loader's checks forever and
    could not be told apart from tampering, so a write failure must remove it
    and fail closed -- leaving the directory clean enough that the next
    bootstrap can succeed."""
    store = _store()
    real_fsync = os.fsync

    def _fail_on_the_new_store(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(28, "No space left on device")
        return real_fsync(fd)

    original_fsync = os.fsync
    try:
        os.fsync = _fail_on_the_new_store
        with pytest.raises(control_auth.CredentialStoreError):
            store.ensure("alice")
    finally:
        os.fsync = original_fsync

    assert not store.path.exists(), "a half-written credential survived"
    # ...and the daemon can still bootstrap once the fault clears.
    assert store.ensure("alice").operator_id == "alice"


def test_bootstrap_into_an_unwritable_state_directory_fails_closed(tmp_state):
    """Every errno reaches the caller as CredentialStoreError, so the daemon's
    single `except` cannot be bypassed by one nobody thought about. A
    read-only state directory is the realistic one (a mount gone read-only);
    the daemon then serves reads and refuses every mutation."""
    store = _store()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = stat.S_IMODE(store.path.parent.lstat().st_mode)
    store.path.parent.chmod(0o500)
    try:
        with pytest.raises(control_auth.CredentialStoreError) as exc:
            store.ensure("alice")
    finally:
        store.path.parent.chmod(original_mode)

    assert "could not be created" in str(exc.value)
    assert not store.path.exists()


def test_a_store_owned_by_another_user_is_refused(tmp_state):
    """Ownership is the check that makes a group- or world-writable parent
    directory survivable: another user can create a file there, but not one
    this loader accepts. Simulated by moving the euid the check compares
    against, since a test cannot chown to a foreign uid."""
    store = _store()
    store.ensure("alice")
    real_geteuid = os.geteuid
    original_geteuid = os.geteuid
    try:
        os.geteuid = lambda: real_geteuid() + 1
        with pytest.raises(control_auth.CredentialStoreError) as exc:
            store.load()
    finally:
        os.geteuid = original_geteuid

    assert "unexpected owner" in str(exc.value)
    # The store itself is untouched -- refusing is not repairing.
    assert store.load().operator_id == "alice"


def test_store_io_failures_fail_closed_as_credential_store_errors(tmp_state):
    """Every failure mode reaches callers as CredentialStoreError, so the
    daemon's single `except` and the CLI's error path cannot be bypassed by an
    errno nobody thought about (a directory where the file belongs, here)."""
    store = _store()
    store.path.mkdir(parents=True)
    with pytest.raises(control_auth.CredentialStoreError):
        store.ensure("alice")
    with pytest.raises(control_auth.CredentialStoreError):
        store.rotate("alice", force=True)
    # ...and nothing partial is left lying around under the daemon dir.
    assert [p.name for p in store.path.parent.iterdir()
            if p.name.startswith(f".{store.path.name}")] == []


def test_an_explicitly_empty_operator_identity_is_a_usage_error(tmp_state):
    """`--operator ""` must not be read as "use the default" -- an unnamed
    operator is exactly the "ui" non-identity CR-15 exists to remove."""
    store = _store()
    with pytest.raises(control_auth.CredentialStoreError):
        store.ensure("")
    assert not store.path.exists()
    store.ensure()                                   # the default still works
    with pytest.raises(control_auth.CredentialStoreError):
        store.rotate("")


def test_a_broken_store_does_not_stop_the_daemon_from_starting(
        tmp_state, sample_project, monkeypatch):
    """Availability half of failing closed: a bad credential file must not
    take the factory down with it. Reads and reconcile keep working; only
    mutations refuse, so `nyxloom auth rotate --force` can fix it live."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    _set_ephemeral_http_port(sample_project)
    store = _store()
    store.ensure("alice")
    store.path.chmod(0o644)

    d = daemon.Daemon({"demo": sample_project.root})
    d._start_http()                            # must NOT raise
    try:
        assert d.http_port != 0
        read = urllib.request.urlopen(f"{_base(d)}/api/projects", timeout=60)
        assert read.status == 200               # the read surface is alive
        assert _post_raw(d, "/api/config/pause",
                         {"project": "demo", "mode": "drain-agents"},
                         _json_headers(Authorization="Bearer x"))[1] == \
            STORE_UNAVAILABLE_BODY

        # ...and a forced rotation restores control WITHOUT a restart.
        recovered = store.rotate(force=True)
        assert _post_raw(d, "/api/config/pause",
                         {"project": "demo", "mode": "drain-agents"},
                         _json_headers(**control_auth.authorization_header(
                             recovered)))[0] == 200
    finally:
        d._stop_http()


# ==========================================================================
# Audit failure must not become an authentication bypass
# ==========================================================================

def test_refusal_survives_an_unwritable_audit_ledger(served, sample_project, monkeypatch):
    """If the ledger cannot be appended, the mutation is STILL refused, the
    response is still the constant 401 (not a 500 carrying a storage error --
    which would itself be a distinguishable signal), and the failure is
    logged at ERROR rather than swallowed."""
    def _boom(*a, **kw):
        raise OSError("ledger is unwritable")

    monkeypatch.setattr(storage, "append_event", _boom)

    errors = _capture_errors(monkeypatch)
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            _json_headers(Authorization="Bearer wrong"))

    assert (status, raw) == (401, UNAUTHENTICATED_BODY)
    snapshot.assert_unchanged("a mutation slipped through while auditing failed")
    assert any("could not be audited" in msg for msg, _kw in errors), errors


def test_an_unauditable_decision_reply_is_refused_before_the_turn_runs(
        served, sample_project, monkeypatch):
    """The other half of "audit failure must not silently permit a mutation".

    A refusal that cannot be audited is still a refusal (above). A SUCCESS
    that cannot be audited is a mutation with no record of who made it -- so
    on this endpoint the record is written first and an unwritable ledger
    refuses the turn outright, rather than answering a human decision
    anonymously."""
    from nyxloom import decision_chat

    decision_id = decisions.open_decision(sample_project, "Ship it?", "resume")
    monkeypatch.setattr(decision_chat, "advance_chat",
                        lambda *a, **kw: pytest.fail("the turn ran unaudited"))
    monkeypatch.setattr(storage, "append_event",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))

    status, raw = _post(served, "/api/decision/reply",
                        {"decision_id": decision_id, "text": "option b"})

    assert status == 503
    assert json.loads(raw)["error"] == "mutation audit unavailable"


@pytest.mark.parametrize("path,body", [
    ("/api/intake", {"project": "demo", "text": "add a dark mode toggle"}),
    ("/api/finding/promote",
     {"project": "demo", "finding_id": "fnd-000000000001"}),
])
def test_an_unauditable_mutation_is_never_reported_as_success(
        served, sample_project, monkeypatch, path, body):
    """Endpoints whose audit record names an id minted DURING the operation
    cannot audit first. They must still never answer 200 for a mutation whose
    operator was not recorded -- the caller has to learn that the trail is
    broken."""
    monkeypatch.setattr(storage, "append_event",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))

    status, _raw = _post(served, path, body)

    assert status != 200, f"{path} reported success for an unaudited mutation"


def test_cross_site_refusal_survives_an_unwritable_audit_ledger(
        served, sample_project, monkeypatch):
    """Same property for the CSRF half of the gate, which audits through the
    same helper: an audit outage must not turn a 403 into a 500."""
    monkeypatch.setattr(storage, "append_event",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            _json_headers(Origin="http://evil.example",
                                          **_bearer()))
    assert status == 403
    assert json.loads(raw)["error"] == "cross-site origin"
    snapshot.assert_unchanged("a cross-site request mutated state")


# ==========================================================================
# The CSRF checks (2026-08-02 amendment) must not regress, and must not
# become an oracle for credential validity.
# ==========================================================================

@pytest.mark.parametrize("headers,expected", [
    ({}, "content-type must be application/json"),
    ({"Content-Type": "text/plain"}, "content-type must be application/json"),
    ({"Content-Type": "application/json",
      "Origin": "http://evil.example"}, "cross-site origin"),
])
def test_csrf_refusals_still_apply_to_an_authenticated_caller(
        served, sample_project, headers, expected):
    snapshot = _Snapshot(sample_project)
    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            {**_bearer(), **headers})
    assert status == 403
    assert json.loads(raw)["error"] == expected
    snapshot.assert_unchanged(expected)
    assert [e.payload["reason"] for e in _refusals()] == [expected]


def test_cross_site_refusal_does_not_reveal_credential_validity(served):
    """CSRF is checked BEFORE authentication, so a cross-site probe learns
    nothing about the credential it guessed: identical bytes with a valid
    credential, an invalid one, and none at all."""
    body = {"project": "demo", "mode": "drain-agents"}
    origin = {"Content-Type": "application/json", "Origin": "http://evil.example"}
    with_valid = _post_raw(served, "/api/config/pause", body,
                           {**origin, **_bearer()})
    with_invalid = _post_raw(served, "/api/config/pause", body,
                             {**origin, "Authorization": "Bearer " + "x" * 43})
    with_none = _post_raw(served, "/api/config/pause", body, origin)
    assert with_valid == with_invalid == with_none
    assert with_valid[0] == 403


def test_oversized_body_is_refused_after_authentication_without_being_read(
        served, sample_project):
    """The body read used to be reachable from any unauthenticated socket with
    an arbitrary declared length. Now it is authenticated AND capped, and the
    cap is enforced from the header -- no byte of the body is read."""
    import http.client

    snapshot = _Snapshot(sample_project)
    conn = http.client.HTTPConnection("127.0.0.1", served.http_port, timeout=60)
    try:
        conn.putrequest("POST", "/api/config/pause")
        conn.putheader("Content-Type", "application/json")
        # Declare far more than we will send: the refusal must not wait on it.
        conn.putheader("Content-Length", str((1 << 20) + 1))
        for key, value in _bearer().items():
            conn.putheader(key, value)
        conn.endheaders()
        conn.send(b'{"project":"demo","mode":"drain-agents"}')
        resp = conn.getresponse()
        assert resp.status == 413
        assert json.loads(resp.read())["error"] == "request body too large"
    finally:
        conn.close()
    snapshot.assert_unchanged("an oversized body mutated state")

    # An UNAUTHENTICATED oversized body is still just a 401: the cap must not
    # become a way to probe the surface without a credential.
    conn2 = http.client.HTTPConnection("127.0.0.1", served.http_port, timeout=60)
    try:
        conn2.putrequest("POST", "/api/config/pause")
        conn2.putheader("Content-Type", "application/json")
        conn2.putheader("Content-Length", str((1 << 20) + 1))
        conn2.endheaders()
        conn2.send(b'{"project":"demo","mode":"drain-agents"}')
        resp2 = conn2.getresponse()
        assert resp2.status == 401
        assert resp2.read() == UNAUTHENTICATED_BODY
    finally:
        conn2.close()


def test_unknown_post_path_is_404_before_authentication_and_is_not_audited(served):
    """An unrouted path is decided from a static table, so a scanner cannot
    fill the audit ledger through it -- and the 404 leaks nothing the
    dashboard's own JavaScript does not already name."""
    status, _raw = _post_raw(served, "/api/config/nope", {}, _json_headers())
    assert status == 404
    assert not _refusals()


# ==========================================================================
# Request framing: a refusal decided before the body is read must not leave
# that body on the socket for the next request line to be parsed out of.
# ==========================================================================

def _raw_exchange(port: int, request: bytes) -> bytes:
    """Send raw bytes, read until the peer closes or stops sending.

    Deliberately below http.client: these oracles are about what is on the
    WIRE (how many responses, whether the connection was dropped), which a
    client library normalizes away. The timeout is a generous failsafe against
    hanging the suite, never a measured budget -- the server either closes the
    connection or it does not, and both outcomes are reached immediately."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=60)
    try:
        sock.sendall(request)
        chunks = []
        # A server that neither answers nor closes raises socket.timeout out of
        # recv after 60s and fails the test with that as its message -- the
        # failsafe is the socket's own timeout, so there is no branch here to
        # exclude from coverage.
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        sock.close()


def _smuggled_pause(credential: str) -> bytes:
    """A complete, VALID pause request -- as a request body."""
    body = b'{"project":"demo","mode":"drain-agents"}'
    return (b"POST /api/config/pause HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Authorization: Bearer " + credential.encode("utf-8") + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"\r\n" + body)


@pytest.mark.parametrize("first_line,extra_headers,why", [
    (b"POST /api/config/nope HTTP/1.1", b"", "unknown path (404, before auth)"),
    (b"POST /api/config/pause HTTP/1.1", b"Origin: http://evil.example\r\n",
     "cross-site refusal (403, before auth)"),
])
def test_a_refusal_before_the_body_does_not_smuggle_the_next_request(
        served, sample_project, first_line, extra_headers, why):
    """The framing hazard behind "refuse before reading the body".

    On an HTTP/1.1 keep-alive connection the unread body stays queued, so the
    server's next read parses it as a fresh request line -- letting an
    attacker hide a fully authenticated mutation inside a request that was
    refused. The oracle is the wire: exactly ONE response, the connection
    dropped, and the smuggled pause never executed."""
    credential = _store().load().credential
    smuggled = _smuggled_pause(credential)
    request = (first_line + b"\r\nHost: 127.0.0.1\r\n"
               b"Content-Type: application/json\r\n" + extra_headers +
               b"Content-Length: " + str(len(smuggled)).encode("ascii") +
               b"\r\n\r\n" + smuggled)

    wire = _raw_exchange(served.http_port, request)

    assert wire.count(b"HTTP/1.1 ") == 1, f"{why}: smuggled request was answered"
    assert b"connection: close" in wire.lower(), why
    assert not paths.pause_flag("demo").exists(), f"{why}: smuggled pause ran"


@pytest.mark.parametrize("framing,why", [
    (b"Content-Length: 40\r\nContent-Length: 5\r\n", "two Content-Length headers"),
    (b"Transfer-Encoding: chunked\r\n", "chunked body http.server cannot de-chunk"),
    (b"Transfer-Encoding:\r\nContent-Length: 40\r\n",
     "Transfer-Encoding present but empty -- `.get` reports it falsy"),
    (b"Content-Length: +40\r\n", "sign-prefixed length int() would accept"),
    (b"Content-Length: 4_0\r\n", "underscore-separated length int() would accept"),
    # Leading OWS is not part of a field value and the header parser has
    # already removed it before `_declared_body_length` sees anything; trailing
    # OWS survives, so that is the case worth pinning.
    (b"Content-Length: 40 \r\n", "trailing whitespace around the length"),
    (b"Content-Length: 40\t\r\n", "tab-padded length"),
])
def test_ambiguously_framed_bodies_are_refused_and_the_connection_dropped(
        served, sample_project, framing, why):
    """A body length IS the message framing. `int()` accepts "+40" and "4_0";
    HTTP does not, and `.get` would silently take the first of two
    Content-Lengths -- the classic desync. All of them are refused, the
    connection is dropped rather than left mid-message, and nothing mutates."""
    snapshot = _Snapshot(sample_project)
    body = b'{"project":"demo","mode":"drain-agents"}'
    credential = _store().load().credential
    request = (b"POST /api/config/pause HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               b"Content-Type: application/json\r\n"
               b"Authorization: Bearer " + credential.encode("utf-8") + b"\r\n" +
               framing + b"\r\n" + body)

    wire = _raw_exchange(served.http_port, request)

    assert wire.startswith(b"HTTP/1.1 400 "), why
    assert b"ambiguous request framing" in wire, why
    assert wire.count(b"HTTP/1.1 ") == 1, why
    assert b"connection: close" in wire.lower(), why
    snapshot.assert_unchanged(why)


def test_a_request_with_no_content_length_is_framed_as_an_empty_body(
        served, sample_project):
    """A POST with no Content-Length at all is unambiguous, not malformed: it
    frames a zero-length body. It must reach the endpoint's OWN validation
    rather than being refused as unframeable -- and it must still change
    nothing.

    The target is /api/decision/reply because its empty-body verdict is the
    one that DISCRIMINATES: "missing decision_id" is emitted by nothing else
    on this server, whereas the pause endpoint answers a missing project with
    the same 404 `not found` an unrouted path gets before framing is ever
    reached -- an oracle that would pass without the body having been framed
    at all. It is also the endpoint the "human owns direction" invariant runs
    on, so an empty body reaching it and doing nothing is the case worth
    pinning."""
    import http.client

    snapshot = _Snapshot(sample_project)
    # http.client, not the raw helper: this response deliberately does NOT
    # close the connection (nothing was left unread), so reading to EOF would
    # block on a healthy keep-alive socket.
    conn = http.client.HTTPConnection("127.0.0.1", served.http_port, timeout=60)
    try:
        conn.putrequest("POST", "/api/decision/reply")
        conn.putheader("Content-Type", "application/json")
        for key, value in _bearer().items():
            conn.putheader(key, value)
        conn.endheaders()                       # no body, no Content-Length
        resp = conn.getresponse()
        assert resp.status == 400
        error = json.loads(resp.read())["error"]
    finally:
        conn.close()

    assert error == "missing decision_id"       # the endpoint's own validation
    assert error != "ambiguous request framing"
    snapshot.assert_unchanged("an empty-bodied POST mutated state")


def test_a_sign_prefixed_length_cannot_smuggle_a_body_past_the_cap(
        served, sample_project):
    """The cap and the read must agree on ONE parse of Content-Length. When
    the cap check used `str.isdigit` and the read used `int`, "+1048577" fell
    between them: over the cap, but read anyway."""
    snapshot = _Snapshot(sample_project)
    credential = _store().load().credential
    request = (b"POST /api/config/pause HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               b"Content-Type: application/json\r\n"
               b"Authorization: Bearer " + credential.encode("utf-8") + b"\r\n"
               b"Content-Length: +" + str((1 << 20) + 1).encode("ascii") + b"\r\n"
               b"\r\n" + b'{"project":"demo","mode":"drain-agents"}')

    wire = _raw_exchange(served.http_port, request)

    assert wire.startswith(b"HTTP/1.1 400 ")
    assert b"ambiguous request framing" in wire
    snapshot.assert_unchanged("an unframeable oversized body mutated state")


# ==========================================================================
# Structural census: the auth boundary cannot be routed around
# ==========================================================================

def _daemon_ast() -> ast.Module:
    return ast.parse(Path(daemon.__file__).read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(fn: ast.FunctionDef) -> list[tuple[int, str, ast.Call]]:
    return [(node.lineno, ast.unparse(node.func), node)
            for node in ast.walk(fn) if isinstance(node, ast.Call)]


def test_mutation_route_census_equals_the_production_dispatch():
    """The census the oracles in this file rest on, read from daemon.py itself.

    `_CONFIG_POST_PATHS` is the authentication surface; the `if path == ...`
    chain is the dispatch. If they disagree, one of two unguarded shapes has
    shipped: a dispatch branch for a path the table does not authenticate, or
    a tabled path with no handler. Either fails here, naming the path."""
    dispatch = _function(_daemon_ast(), "_handle_post")
    routed = {
        node.comparators[0].value
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name) and node.left.id == "path"
        and isinstance(node.ops[0], ast.Eq)
        and isinstance(node.comparators[0], ast.Constant)
    }
    assert routed == set(daemon._CONFIG_POST_PATHS)
    assert routed == {path for path, _body in MUTATIONS}


def test_every_mutating_route_crosses_the_auth_boundary_before_body_or_target():
    """The ORDER, asserted structurally rather than only per-endpoint.

    Authentication must happen exactly once, before the body is read and
    before any handler (which is where a project/decision/intake/finding id is
    resolved) runs -- and every handler must receive the authenticated actor,
    so none can invent its own. A future route added below the body read, or
    dispatched without `actor`, fails here even if it is in the table."""
    dispatch = _function(_daemon_ast(), "_handle_post")
    calls = _calls(dispatch)

    auth = [line for line, name, _ in calls
            if name.endswith("_authenticate_control_mutation")]
    body = [line for line, name, _ in calls if name.endswith("_read_json_body")]
    handlers = [(line, name, node) for line, name, node in calls
                if "._post_" in name]

    assert len(auth) == 1, "authentication must have exactly one call site"
    assert body and min(body) > auth[0], "the body is read before authentication"
    assert handlers, "no route handlers found -- the census would be vacuous"
    assert min(line for line, _n, _c in handlers) > auth[0]
    assert len(handlers) == len(daemon._CONFIG_POST_PATHS)
    for _line, name, node in handlers:
        supplied = ({a.id for a in node.args if isinstance(a, ast.Name)} |
                    {kw.arg for kw in node.keywords})
        assert "actor" in supplied, f"{name} is dispatched without the operator"


def test_do_post_has_no_second_entry_point_and_reads_stay_read_only():
    """Two structural negatives that keep the boundary the ONLY way in:
    `do_POST` delegates to `_handle_post` and to nothing else on the daemon,
    and the GET surface -- which is deliberately unauthenticated -- appends no
    events and calls no mutating handler."""
    tree = _daemon_ast()
    do_post = _function(tree, "do_POST")
    delegated = {name for _line, name, _node in _calls(do_post)
                 if name.startswith("daemon.")}
    assert delegated == {"daemon._handle_post"}

    handle_get = _function(tree, "_handle_get")
    for _line, name, _node in _calls(handle_get):
        assert "._post_" not in name, name
        assert not name.endswith(("_append_ui_event", "storage.append_event",
                                  "storage.append_and_apply")), name


def test_a_tabled_path_without_a_handler_authenticates_then_fails_loudly(
        served, monkeypatch):
    """Membership in `_CONFIG_POST_PATHS` is what makes a route
    authenticated, so a future endpoint cannot forget the credential check --
    and if it forgets its DISPATCH branch instead, it must answer 500 rather
    than hang the client on a response that never comes."""
    monkeypatch.setattr(daemon, "_CONFIG_POST_PATHS",
                        frozenset(daemon._CONFIG_POST_PATHS | {"/api/config/future"}))

    unauthenticated = _post_raw(served, "/api/config/future", {}, _json_headers())
    assert unauthenticated == (401, UNAUTHENTICATED_BODY)
    assert [e.payload["path"] for e in _refusals()] == ["/api/config/future"]

    status, raw = _post(served, "/api/config/future", {})
    assert status == 500
    assert json.loads(raw)["error"] == "unrouted control path"


# ==========================================================================
# The read surface stays open by design (plan item 4)
# ==========================================================================

@pytest.mark.parametrize("path", [
    "/api/projects", "/api/tasks?project=demo", "/api/logs/level",
    "/api/events?project=demo", "/www/index.html", "/www/config.html",
])
def test_read_endpoints_need_no_credential(served, sample_project, path):
    """Mutation authority and read access are deliberately separable so a
    trusted network can serve the dashboard read-only."""
    render.render_all({"demo": sample_project.root})       # the /www/* pages
    resp = urllib.request.urlopen(f"{_base(served)}{path}", timeout=60)
    assert resp.status == 200


def test_get_on_a_mutating_path_is_still_405_not_401(served):
    """Method routing is decided before authentication, so a GET on a POST
    path keeps its existing contract instead of turning into a credential
    prompt."""
    for path in sorted(daemon._CONFIG_POST_PATHS):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{_base(served)}{path}", timeout=60)
        assert exc.value.code == 405, path


# ==========================================================================
# Dashboard credential flow: prompted at runtime, never rendered
# ==========================================================================

MUTATING_PAGES = ["config.html", "decisions.html", "findings.html", "intake.html"]


def test_every_mutating_page_ships_the_credential_header_helper(sample_project):
    render.render_all({"demo": sample_project.root})
    for name in MUTATING_PAGES:
        page = (paths.www_dir() / name).read_text(encoding="utf-8")
        assert "function nyxloomMutationFetch" in page, name
        assert "nyxloomMutationFetch(" in page, name
        assert "'Authorization': 'Bearer ' + credential" in page, name
        # A 401 drops the stored value so the next action re-prompts rather
        # than silently retrying a credential that was rotated out.
        assert "if (resp.status === 401) { nyxloomForgetCredential(); }" in page, name


def test_no_rendered_page_posts_outside_the_credential_helper(sample_project):
    """The structural guard against a new dashboard button that forgets the
    header: on any page, the ONLY POST call site is the helper itself."""
    render.render_all({"demo": sample_project.root})
    for page_path in sorted(paths.www_dir().rglob("*.html")):
        text = page_path.read_text(encoding="utf-8")
        expected = 1 if "function nyxloomMutationFetch" in text else 0
        assert text.count("method: 'POST'") == expected, page_path
        assert text.count('method: "POST"') == 0, page_path


def test_no_rendered_page_or_log_or_event_contains_the_credential(
        served, sample_project, tmp_state):
    """The credential is typed into the dashboard at runtime; it must exist
    nowhere durable except its own 0600 store. This walks the whole state root
    after a full lifecycle -- authenticated mutation, refused mutation,
    rotation -- and the rendered dashboard."""
    log.configure(level=log.DEBUG, log_dir=paths.logs_dir(), console=False)
    first = _store().load()

    assert _post(served, "/api/config/pause",
                 {"project": "demo", "mode": "drain-agents"})[0] == 200
    _post_raw(served, "/api/config/pause", {"project": "demo", "mode": "run"},
              _json_headers(Authorization=f"Bearer {first.credential}x"))
    rotated = _store().rotate()
    assert _post_raw(served, "/api/config/pause", {"project": "demo", "mode": "run"},
                     _json_headers(**control_auth.authorization_header(rotated)))[0] == 200
    render.render_all({"demo": sample_project.root})

    secrets_seen = [first.credential, rotated.credential]
    store_path = _store().path
    for path in sorted(tmp_state.rglob("*")):
        if not path.is_file() or path == store_path:
            continue
        blob = path.read_bytes()
        for secret in secrets_seen:
            assert secret.encode("utf-8") not in blob, f"{path} leaked a credential"


# ==========================================================================
# Store semantics and identity validation (unit level)
# ==========================================================================

def test_ensure_is_idempotent_and_creates_a_0600_regular_file(tmp_state):
    store = _store()
    first = store.ensure("alice")
    assert store.ensure().credential == first.credential
    assert store.ensure("someone-else").operator_id == "alice"
    info = store.path.lstat()
    assert stat.S_ISREG(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.geteuid()


def test_credential_has_real_entropy_and_is_not_reused(tmp_state):
    """256 bits, freshly drawn each time -- the package's stated reason for
    having no rate limit. Asserted on the DECODED byte count, because a
    urlsafe-base64 length check on its own would pass a 24-byte token: 32
    characters is not 32 bytes of entropy."""
    import base64

    store = _store()
    seen = {store.ensure().credential}
    for _ in range(5):
        seen.add(store.rotate().credential)
    assert len(seen) == 6
    for credential in seen:
        padded = credential + "=" * (-len(credential) % 4)
        assert len(base64.urlsafe_b64decode(padded)) >= 32, credential


def test_rotation_is_atomic_under_a_concurrent_reader(tmp_state):
    """The atomicity claim on `os.replace`, exercised against a real reader.

    A reader racing a rotation must see the whole OLD record or the whole NEW
    one -- never a partial file, and never a refusal caused merely by the swap
    being in progress. Both threads run a fixed number of iterations and are
    joined; nothing here waits on a clock or asserts on how far either got, so
    a slower machine changes the interleaving, never the verdict."""
    store = _store()
    store.ensure("alice")
    rounds = 40
    failures: list[str] = []
    generations: list[int] = []
    reader_done = threading.Event()

    def _rotate() -> None:
        # No except clause: a rotation that raises kills this thread, the
        # `finally` still releases the reader, and the generation assertion at
        # the end fails with the store's real state. Catching it here would
        # only add an unreachable branch.
        try:
            for _ in range(rounds):
                store.rotate("alice")
        finally:
            reader_done.set()

    def _read() -> None:
        try:
            while not reader_done.is_set():
                record = store.load()
                generations.append(record.generation)
                if record.operator_id != "alice" or len(record.credential) < 32:
                    failures.append(f"torn record: {record!r}")
        except Exception as exc:
            failures.append(f"load raised {exc!r} during a rotation")

    writer = threading.Thread(target=_rotate)
    reader = threading.Thread(target=_read)
    writer.start()
    reader.start()
    writer.join(timeout=60)
    reader.join(timeout=60)

    assert not writer.is_alive() and not reader.is_alive()
    assert failures == []
    assert generations, "the reader never observed the store"
    assert store.load().generation == rounds + 1


@pytest.mark.parametrize("bad", [
    "", " ", "-leading-dash", ".dot", "with space", "tab\t", "new\nline",
    "../traversal", "a" * 129,
    "unicode" + chr(233),      # built, not typed: this source stays ASCII
    control_auth.UNAUTHENTICATED_ACTOR_ID,
])
def test_operator_identity_validation_rejects_unusable_names(tmp_state, bad):
    """The operator id reaches an event payload and a log field, and is
    compared against the refusal sentinel, so it is constrained at every
    entry point rather than sanitized downstream."""
    store = _store()
    with pytest.raises(control_auth.CredentialStoreError):
        store.ensure(bad)
    assert not store.path.exists()


def test_default_operator_id_prefers_the_env_var_and_falls_back_safely(monkeypatch):
    monkeypatch.setenv("NYXLOOM_OPERATOR_ID", "release-bot")
    assert control_auth.default_operator_id() == "release-bot"

    # An unusable value must not propagate as an identity, and must not raise
    # during a daemon boot -- it degrades to a fixed, valid name.
    monkeypatch.setenv("NYXLOOM_OPERATOR_ID", "not a valid id")
    fallback = control_auth.default_operator_id()
    assert fallback == "local-operator"
    assert control_auth._OPERATOR_RE.fullmatch(fallback)


@pytest.mark.parametrize("failure", [
    OSError("no passwd entry for this uid"),
    KeyError("getpwuid(): uid not found"),
    ImportError("pwd is unavailable on this platform"),
])
def test_default_operator_id_survives_a_host_with_no_resolvable_user(
        monkeypatch, failure):
    """The gate container runs as a uid whose passwd entry may be missing --
    the identity failure DOCTRINE names as producing 108 spurious failures in
    another project. `getpass.getuser()` raises there, and a daemon boot must
    still yield a usable operator name rather than propagating the error.

    The env var is cleared so the lookup is actually reached: with
    NYXLOOM_OPERATOR_ID set to anything non-empty the `try` never runs, which
    is how this path went untested while looking tested."""
    monkeypatch.delenv("NYXLOOM_OPERATOR_ID", raising=False)
    monkeypatch.setattr(control_auth.getpass, "getuser",
                        lambda: (_ for _ in ()).throw(failure))

    assert control_auth.default_operator_id() == "local-operator"


def test_authenticate_accepts_a_plain_mapping_and_a_message_object(tmp_state):
    """The daemon passes an email.message.Message (get_all); tests and other
    callers pass a dict. Both must behave identically."""
    import email.message

    store = _store()
    record = store.ensure("alice")
    assert store.authenticate(
        {"Authorization": f"Bearer {record.credential}"}) == record.actor
    assert store.authenticate({}) is None
    assert store.authenticate({"Authorization": "Bearer nope"}) is None

    message = email.message.Message()
    message["Authorization"] = f"Bearer {record.credential}"
    assert store.authenticate(message) == record.actor


def test_authorization_header_is_a_bearer_header(tmp_state):
    record = _store().ensure("alice")
    assert control_auth.authorization_header(record) == {
        "Authorization": f"Bearer {record.credential}"}


# ==========================================================================
# The audit ledger is a real, readable, replay-safe event stream
# ==========================================================================

def test_control_ledger_is_readable_with_the_events_command(served, capsys):
    """An audit trail nobody can read is not an audit trail. The refusals land
    in the ordinary event store, so the ordinary reader tool finds them."""
    _post_raw(served, "/api/config/pause", {"project": "demo", "mode": "drain-agents"},
              _json_headers())
    capsys.readouterr()

    assert cli.main(["events", control_auth.CONTROL_LEDGER_PROJECT]) == 0
    out = capsys.readouterr().out
    records = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert [r["type"] for r in records] == ["CONTROL_MUTATION_REFUSED"]
    assert records[0]["actor"] == {"kind": "operator",
                                   "id": control_auth.UNAUTHENTICATED_ACTOR_ID}
    assert records[0]["payload"] == {"path": "/api/config/pause",
                                     "reason": "invalid-or-missing-credential"}


def test_control_ledger_is_not_a_registered_project(served, sample_project):
    """The synthetic ledger id must stay invisible to everything that walks
    the registry, or the dashboard would grow a phantom project."""
    _post_raw(served, "/api/config/pause", {"project": "demo", "mode": "drain-agents"},
              _json_headers())
    assert _refusals()                                   # the ledger exists...
    assert control_auth.CONTROL_LEDGER_PROJECT not in config.load_registry()
    assert control_auth.CONTROL_LEDGER_PROJECT not in served.registry

    render.render_all({"demo": sample_project.root})
    index = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert control_auth.CONTROL_LEDGER_PROJECT not in index


@pytest.mark.parametrize("ev_type", [
    EventType.CONTROL_MUTATION_REFUSED,
    EventType.CONTROL_CREDENTIAL_ROTATED,
    EventType.DECISION_REPLY_RECORDED,
    EventType.INTAKE_REPLY_RECORDED,
    EventType.FINDING_PROMOTED,
])
def test_new_event_types_round_trip_and_replay_without_projection(tmp_state, ev_type):
    """Serde + replay completeness for the types this package adds: they
    round-trip through the store byte-for-byte and are true no-ops for the
    task projection (they are audit records, not state transitions)."""
    appended = storage.append_event(
        control_auth.CONTROL_LEDGER_PROJECT,
        actor=control_auth.unauthenticated_actor(), type=ev_type,
        payload={"path": "/api/config/pause", "reason": "test"},
    )
    (read_back,) = list(storage.iter_events(control_auth.CONTROL_LEDGER_PROJECT))
    assert read_back.to_dict() == appended.to_dict()
    assert read_back.type is ev_type

    states: dict = {}
    assert storage.apply_event(states, read_back) == []
    assert states == {}
    assert storage.replay(control_auth.CONTROL_LEDGER_PROJECT) == {}


# ==========================================================================
# CLI credential flow
# ==========================================================================

def test_cli_bootstrap_show_and_rotate_round_trip(served, capsys):
    """The operator-facing flow end to end: bootstrap/show print a credential
    that actually authenticates, and rotate prints one that replaces it."""
    capsys.readouterr()
    assert cli.main(["auth", "show"]) == 0
    shown = capsys.readouterr().out
    credential = _credential_from(shown)
    assert f"operator: {_store().load().operator_id}" in shown
    assert "generation: 1" in shown
    assert f"header: Authorization: Bearer {credential}" in shown

    assert _post_raw(served, "/api/config/pause",
                     {"project": "demo", "mode": "drain-agents"},
                     _json_headers(Authorization=f"Bearer {credential}"))[0] == 200
    paths.pause_flag("demo").unlink()

    assert cli.main(["auth", "rotate", "--operator", "alice"]) == 0
    rotated = _credential_from(capsys.readouterr().out)
    assert rotated != credential

    assert _post_raw(served, "/api/config/pause",
                     {"project": "demo", "mode": "drain-agents"},
                     _json_headers(Authorization=f"Bearer {credential}"))[0] == 401
    assert _post_raw(served, "/api/config/pause",
                     {"project": "demo", "mode": "drain-agents"},
                     _json_headers(Authorization=f"Bearer {rotated}"))[0] == 200

    set_events = [e for e in storage.iter_events("demo")
                  if e.type is EventType.PAUSE_SET]
    assert set_events[-1].actor.id == "alice"

    rotations = [e for e in _ledger()
                 if e.type is EventType.CONTROL_CREDENTIAL_ROTATED]
    assert len(rotations) == 1
    assert rotations[0].actor.id == "alice"
    assert rotations[0].payload == {"generation": 2, "forced": False}
    # The event records WHO and WHICH generation -- never the value.
    assert rotated not in json.dumps(rotations[0].to_dict())


def _credential_from(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("credential: "):
            return line.split(": ", 1)[1].strip()
    raise AssertionError(f"no credential line in output: {stdout!r}")


def test_cli_bootstrap_creates_the_store_when_absent(tmp_state, capsys):
    assert not _store().path.exists()
    assert cli.main(["auth", "bootstrap", "--operator", "alice"]) == 0
    printed = _credential_from(capsys.readouterr().out)
    record = _store().load()
    assert record.operator_id == "alice"
    assert record.credential == printed
    assert stat.S_IMODE(_store().path.lstat().st_mode) == 0o600


def test_cli_reports_an_untrustworthy_store_instead_of_raising(tmp_state, capsys):
    store = _store()
    store.ensure("alice")
    store.path.chmod(0o644)
    capsys.readouterr()

    assert cli.main(["auth", "show"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""                     # no partial secret on stdout
    assert "0600" in captured.err

    assert cli.main(["auth", "rotate"]) == 1
    assert "--force" in capsys.readouterr().err

    assert cli.main(["auth", "rotate", "--force"]) == 0
    out = capsys.readouterr().out
    assert _credential_from(out) == store.load().credential
    assert "generation: 1" in out
    forced = [e for e in _ledger()
              if e.type is EventType.CONTROL_CREDENTIAL_ROTATED]
    assert forced[-1].payload == {"generation": 1, "forced": True}


def test_cli_auth_without_a_subcommand_is_a_usage_error(tmp_state, capsys):
    assert cli.main(["auth"]) == 2
    assert not _store().path.exists()


def test_cli_rotation_prints_the_credential_even_if_the_audit_append_fails(
        tmp_state, capsys, monkeypatch):
    """Ordering matters: the credential on disk has ALREADY changed, so an
    unavailable event store must not cost the operator the only copy of the
    value that now works. Report the audit failure, keep the value."""
    store = _store()
    store.ensure("alice")
    monkeypatch.setattr(storage, "append_event",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))
    capsys.readouterr()

    assert cli.main(["auth", "rotate"]) == 1
    captured = capsys.readouterr()
    assert _credential_from(captured.out) == store.load().credential
    assert "could not be audited" in captured.err

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

import json
import logging
import os
import stat
import urllib.error
import urllib.request

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

    # Regression guard for the pre-CR-15 default: an unattributed call (the
    # ntfy feedback router, which has no operator identity) still records the
    # decision agent rather than inventing an operator.
    other = decisions.open_decision(sample_project, "And this?", "resume")
    decision_chat.advance_chat(sample_project, "demo", other, "yes")
    resolved2 = [e for e in storage.iter_events("demo")
                 if e.type is EventType.DECISION_RESOLVED and e.decision_id == other]
    assert len(resolved2) == 1
    assert resolved2[0].actor.kind is ActorKind.FRONTIER_SESSION


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

    errors: list[tuple[str, dict]] = []
    real_log = daemon.log

    class _CapturingLog:
        def error(self, msg, **kw):
            errors.append((msg, dict(kw)))
            return real_log.error(msg, **kw)

        def __getattr__(self, name):
            return getattr(real_log, name)

    monkeypatch.setattr(daemon, "log", _CapturingLog())
    snapshot = _Snapshot(sample_project)

    status, raw = _post_raw(served, "/api/config/pause",
                            {"project": "demo", "mode": "drain-agents"},
                            _json_headers(Authorization="Bearer wrong"))

    assert (status, raw) == (401, UNAUTHENTICATED_BODY)
    snapshot.assert_unchanged("a mutation slipped through while auditing failed")
    assert any("could not be audited" in msg for msg, _kw in errors), errors


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
    store = _store()
    seen = {store.ensure().credential}
    for _ in range(5):
        seen.add(store.rotate().credential)
    assert len(seen) == 6
    assert all(len(c) >= 32 for c in seen)


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
    monkeypatch.setattr(control_auth.getpass, "getuser",
                        lambda: (_ for _ in ()).throw(OSError("no passwd entry")))
    fallback = control_auth.default_operator_id()
    assert fallback == "local-operator"
    assert control_auth._OPERATOR_RE.fullmatch(fallback)


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

"""Tests for nyxloom.intake_bridge (B9 / nyxloom-P109).

Every fixture payload below is a VERBATIM capture from a real Mattermost
11.10.1 instance, not a hand-written approximation, because the whole class
of bug this module can have is "the wire shape is not what the parser
assumed" -- which is what P107's `Incoming:` prefix incident was, and what
`_json_rows` exists for. The captures that matter:

* `mmctl --json post list` prints one printer row PER POST, so the container
  is EMPTY STDOUT for zero, a BARE OBJECT for one and an ARRAY for many. A
  parser that assumes "array" reads `len()` over a single post's dict keys.
  (Observed: a one-post channel reported as 21 messages.)
* mmctl's `--since` accepts ONLY `...+00:00`; a trailing `Z` is rejected.
* The REST endpoint returns `order` NEWEST-first while mmctl returns
  oldest-first, and REST's `since` is exclusive on `update_at` while mmctl's
  is inclusive on `create_at`.
* Webhook-authored posts carry `props.from_webhook == "true"` -- the loop
  guard, server-attributed rather than self-declared.

The transport seam is exercised through a fake `MessageReader` rather than a
patched `subprocess`, EXCEPT where the point of the test is the argv or the
parse; those two go through the real `MmctlReader` against a recorded
subprocess result.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog.contextvars

from nyxloom import config, control_auth, intake_bridge, intake_chat, log, storage
from nyxloom.config import IntakeBridgeConfig
from nyxloom.intake_bridge import BridgeError, InboundMessage
from nyxloom.types import EventType


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


# --------------------------------------------------------------------------
# verbatim 11.10.1 captures

#: `mmctl --local --json post list nyxloom:alerts --number 3` -- THREE posts,
#: array container, oldest-first, with a webhook post's `from_webhook` prop.
MMCTL_THREE_POSTS = json.dumps([
    {"id": "dfgm4959otd3tcedqohrxafp8o", "create_at": 1788908337461,
     "update_at": 1788908337461, "delete_at": 0,
     "user_id": "1645jrsniibymkhgq818gezoer",
     "channel_id": "cjg1wnnryff3iq14udyiakb4dh",
     "message": "nyxloom-daemon joined the channel.",
     "type": "system_join_channel", "props": {"username": "nyxloom-daemon"}},
    {"id": "kh775pyd7fymbq15pj5e885pic", "create_at": 1788908337730,
     "update_at": 1788908337730, "delete_at": 0,
     "user_id": "m9zfp5zwop88xxx7i5bz1cy9da",
     "channel_id": "cjg1wnnryff3iq14udyiakb4dh",
     "message": "operator: build me a thing", "type": "", "props": {}},
    {"id": "56t8ykyjhbn7te31dhejkqp91a", "create_at": 1788908489705,
     "update_at": 1788908489705, "delete_at": 0,
     "user_id": "1645jrsniibymkhgq818gezoer",
     "channel_id": "cjg1wnnryff3iq14udyiakb4dh",
     "message": ":warning: **nyxloom-P107 cutover**", "type": "",
     "props": {"from_webhook": "true", "override_username": "nyxloom-daemon"}},
])

#: THE TRAP: `post list --since <t>` matching exactly ONE post prints a BARE
#: OBJECT, not a one-element array. Captured verbatim.
MMCTL_ONE_POST = json.dumps(
    {"id": "1rwhzrizpjff9g1dgdrnqx8gnc", "create_at": 1788947692381,
     "update_at": 1788947692381, "delete_at": 0,
     "user_id": "yq376jj6e3f6jk1z1uprj8fhhe",
     "channel_id": "8wmfeg1wzbdhieyuitqktdetwa",
     "message": "operator: build me a thing", "type": "", "props": {}},
)

#: The REST `GET /api/v4/channels/{id}/posts` envelope -- `order` is
#: NEWEST-first, `posts` is a map.
REST_PAGE = {
    "order": ["1rwhzrizpjff9g1dgdrnqx8gnc", "q9hfqsc8zfn83mngsjnsscoicc"],
    "posts": {
        "1rwhzrizpjff9g1dgdrnqx8gnc": {
            "id": "1rwhzrizpjff9g1dgdrnqx8gnc", "create_at": 1788947692381,
            "update_at": 1788947692381, "delete_at": 0, "user_id": "yq376j",
            "message": "operator: build me a thing", "type": "", "props": {}},
        "q9hfqsc8zfn83mngsjnsscoicc": {
            "id": "q9hfqsc8zfn83mngsjnsscoicc", "create_at": 1788947691140,
            "update_at": 1788947691140, "delete_at": 0, "user_id": "yq376j",
            "message": "**bot reply** turn 1", "type": "",
            "props": {"from_webhook": "true"}},
    },
    "next_post_id": "", "prev_post_id": "",
}


# --------------------------------------------------------------------------
# local helpers


class _FakeReader(intake_bridge.MessageReader):
    """Records the `since_ms` it was asked for and returns canned posts."""

    name = "fake"

    def __init__(self, messages: list[InboundMessage]):
        self.messages = messages
        self.since_calls: list[int] = []

    def is_configured(self, bc: IntakeBridgeConfig) -> bool:
        return True

    def fetch(self, bc: IntakeBridgeConfig, *, since_ms: int) -> list[InboundMessage]:
        self.since_calls.append(since_ms)
        return list(self.messages)


def _msg(post_id: str, create_at: int, text: str, *, program=False,
         system=False, deleted=False) -> InboundMessage:
    return InboundMessage(post_id=post_id, create_at_ms=create_at,
                          author_id="u1", text=text, is_program=program,
                          is_system=system, is_deleted=deleted)


def _bridge_cfg(**overrides) -> IntakeBridgeConfig:
    base = dict(transport="mmctl", team="nyxloom", channel="intake",
                container="nyxloom-prod-mattermost",
                webhook_url="http://mm:8065/hooks/abc", bootstrap_messages=0)
    base.update(overrides)
    return IntakeBridgeConfig(**base)


@pytest.fixture()
def wired(sample_project, monkeypatch):
    """A registered project with the bridge configured, a named channel
    operator, a stubbed intake turn and a captured outbound send."""
    monkeypatch.setenv(control_auth.CHANNEL_OPERATOR_ENV, "operator@example")
    cfg = sample_project
    cfg.intake_bridge = _bridge_cfg()

    turns: list[tuple[str, str]] = []
    sent: list[tuple[object, dict]] = []

    def _fake_advance(_cfg, _project, intake_id, text):
        turns.append((intake_id, text))
        return f"reply to: {text[:40]}"

    monkeypatch.setattr(intake_chat, "advance_intake", _fake_advance)
    monkeypatch.setattr(intake_chat, "load_chat", lambda *_a, **_k: None)
    monkeypatch.setattr(intake_bridge.notify, "send",
                        lambda nc, note: (sent.append((nc, note)), (True, "ok"))[1])
    return cfg, turns, sent


# --------------------------------------------------------------------------
# registry / selector parity


def test_reader_registry_matches_the_schema_side_tuple():
    assert sorted(intake_bridge._READERS) == sorted(config.INTAKE_TRANSPORTS)


def test_unknown_transport_is_refused_at_construction():
    with pytest.raises(ValueError, match="transport must be one of"):
        IntakeBridgeConfig(transport="carrier-pigeon")


def test_resolve_reader_never_falls_back_to_another_transport():
    """A `rest` config with no token must resolve to NOTHING, not to mmctl:
    a fallback would silently re-route a token-scoped read onto the
    unrestricted local-mode admin socket -- an escalation, not a
    degradation."""
    bc = IntakeBridgeConfig(transport="rest", team="nyxloom", channel="intake",
                            base_url="http://mm:8065", container="c")
    assert intake_bridge.resolve_reader(bc) is None


def test_resolve_reader_returns_none_for_an_unset_transport():
    assert intake_bridge.resolve_reader(IntakeBridgeConfig()) is None


# --------------------------------------------------------------------------
# the mmctl JSON shape trap


def test_json_rows_normalises_a_bare_object_to_one_row():
    """The trap itself: one post prints as an OBJECT. Reading it as a list
    yields the post's KEY COUNT as a message count."""
    rows = intake_bridge._json_rows(MMCTL_ONE_POST, what="post list")
    assert len(rows) == 1
    assert rows[0]["id"] == "1rwhzrizpjff9g1dgdrnqx8gnc"


def test_json_rows_handles_the_array_and_the_two_empty_spellings():
    assert len(intake_bridge._json_rows(MMCTL_THREE_POSTS, what="post list")) == 3
    assert intake_bridge._json_rows("", what="post list") == []
    assert intake_bridge._json_rows("   \n", what="post list") == []
    assert intake_bridge._json_rows("null", what="token list") == []


@pytest.mark.parametrize("payload", ["not json at all", "[1, 2, 3]", '"a string"', "42"])
def test_json_rows_refuses_rather_than_degrading_to_empty(payload):
    """P107's lesson: an unrecognised list read as 'nothing exists' is what
    repeats a mutation. Every unknown shape must RAISE."""
    with pytest.raises(BridgeError):
        intake_bridge._json_rows(payload, what="post list")


def test_mmctl_since_uses_the_only_layout_the_server_accepts():
    """`--since ...Z` is REJECTED by 11.10.1 (`invalid since time`); only a
    numeric offset parses. Second-truncation is safe because mmctl's window
    is inclusive, so it can only ever over-fetch."""
    out = intake_bridge._mmctl_since(1788947692381)
    assert out == "2026-09-09T09:54:52+00:00"
    assert not out.endswith("Z")


# --------------------------------------------------------------------------
# MmctlReader argv + parse (the real reader, a recorded subprocess)


def _recorded_run(stdout: str, rc: int = 0, stderr: str = ""):
    calls: list[list[str]] = []

    class _Proc:
        returncode = rc
        def __init__(self):
            self.stdout = stdout
            self.stderr = stderr

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        return _Proc()

    return calls, _run


def test_mmctl_reader_uses_number_when_there_is_no_cursor(monkeypatch):
    calls, run = _recorded_run(MMCTL_THREE_POSTS)
    monkeypatch.setattr(intake_bridge.subprocess, "run", run)
    intake_bridge.MmctlReader().fetch(_bridge_cfg(bootstrap_messages=2), since_ms=0)
    assert "--number" in calls[0] and "--since" not in calls[0]
    assert calls[0][calls[0].index("--number") + 1] == "3"


def test_mmctl_reader_uses_since_once_a_cursor_exists(monkeypatch):
    calls, run = _recorded_run(MMCTL_ONE_POST)
    monkeypatch.setattr(intake_bridge.subprocess, "run", run)
    msgs = intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1788947692381)
    assert "--since" in calls[0] and "--number" not in calls[0]
    assert calls[0][calls[0].index("--since") + 1] == "2026-09-09T09:54:52+00:00"
    assert [m.post_id for m in msgs] == ["1rwhzrizpjff9g1dgdrnqx8gnc"]


def test_mmctl_reader_marks_webhook_and_system_posts(monkeypatch):
    _calls, run = _recorded_run(MMCTL_THREE_POSTS)
    monkeypatch.setattr(intake_bridge.subprocess, "run", run)
    msgs = intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1)
    by_id = {m.post_id: m for m in msgs}
    assert by_id["dfgm4959otd3tcedqohrxafp8o"].is_system
    assert by_id["56t8ykyjhbn7te31dhejkqp91a"].is_program
    assert by_id["kh775pyd7fymbq15pj5e885pic"].is_human_turn


def test_mmctl_reader_raises_on_a_failed_read_instead_of_reporting_empty(monkeypatch):
    _calls, run = _recorded_run("", rc=1, stderr="Error: channel not found")
    monkeypatch.setattr(intake_bridge.subprocess, "run", run)
    with pytest.raises(BridgeError, match="exited 1"):
        intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1)


def test_mmctl_reader_never_echoes_stdout_in_its_error(monkeypatch):
    """A failed `post list` still prints; stdout may hold post text, so the
    refusal must name the verb and rc only."""
    _calls, run = _recorded_run("SECRET-POST-BODY", rc=1, stderr="boom")
    monkeypatch.setattr(intake_bridge.subprocess, "run", run)
    with pytest.raises(BridgeError) as excinfo:
        intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1)
    assert "SECRET-POST-BODY" not in str(excinfo.value)


# --------------------------------------------------------------------------
# RestReader


def test_rest_reader_windows_one_ms_below_the_cursor(monkeypatch):
    """REST's `since` is EXCLUSIVE and matches `update_at`; `-1` is what
    keeps a post created at exactly the cursor inside the window, so the
    shared id filter (not the server) decides whether it is new."""
    seen: list[str] = []
    reader = intake_bridge.RestReader()

    def _get(_self, _bc, path):
        seen.append(path)
        return {"id": "chan1"} if "/channels/name/" in path else REST_PAGE

    monkeypatch.setattr(intake_bridge.RestReader, "_get", _get)
    msgs = reader.fetch(_bridge_cfg(transport="rest", base_url="http://mm:8065",
                                    token="t"), since_ms=1788947692381)
    assert "since=1788947692380" in seen[1]
    assert {m.post_id for m in msgs} == set(REST_PAGE["posts"])


def test_rest_reader_reads_the_posts_map_not_the_order_list(monkeypatch):
    """`order` is newest-first here and oldest-first under mmctl; the bridge
    sorts by (create_at, id) itself so neither order can leak into the
    cursor."""
    monkeypatch.setattr(
        intake_bridge.RestReader, "_get",
        lambda _s, _b, path: {"id": "c"} if "/channels/name/" in path else REST_PAGE)
    msgs = intake_bridge.RestReader().fetch(
        _bridge_cfg(transport="rest", base_url="http://mm:8065", token="t"), since_ms=1)
    assert {m.post_id for m in msgs} == set(REST_PAGE["posts"])
    assert any(m.is_program for m in msgs)


def test_rest_reader_refuses_a_payload_without_a_posts_map(monkeypatch):
    monkeypatch.setattr(
        intake_bridge.RestReader, "_get",
        lambda _s, _b, path: {"id": "c"} if "/channels/name/" in path else {"order": []})
    with pytest.raises(BridgeError, match="no 'posts' map"):
        intake_bridge.RestReader().fetch(
            _bridge_cfg(transport="rest", base_url="http://mm:8065", token="t"), since_ms=1)


# --------------------------------------------------------------------------
# the reader ABC + degraded transport paths


def test_the_reader_base_class_refuses_to_pretend_it_works():
    """A subclass that forgets a method must fail loudly, not silently
    report an empty channel (the Protocol-stub trap, LESSONS L25)."""
    base = intake_bridge.MessageReader()
    with pytest.raises(NotImplementedError):
        base.is_configured(_bridge_cfg())
    with pytest.raises(NotImplementedError):
        base.fetch(_bridge_cfg(), since_ms=0)


def test_a_transport_mutated_past_construction_validation_resolves_to_none():
    """`__post_init__` refuses an unknown transport at construction, but a
    caller can still mutate the field afterwards; `resolve_reader` must not
    KeyError, and must not silently pick the other transport."""
    bc = _bridge_cfg()
    bc.transport = "carrier-pigeon"
    assert intake_bridge.resolve_reader(bc) is None


def test_a_post_missing_its_id_or_timestamp_is_dropped_not_crashed_on():
    assert intake_bridge._message_from_post({"create_at": 1, "message": "x"}) is None
    assert intake_bridge._message_from_post({"id": "p", "message": "x"}) is None
    assert intake_bridge._message_from_post({"id": "p", "create_at": "not-an-int"}) is None
    assert intake_bridge._message_from_post({"id": "p", "create_at": 1}) is not None


def test_mmctl_reader_translates_a_subprocess_fault_into_a_bridge_error(monkeypatch):
    def _boom(_argv, **_kwargs):
        raise subprocess_timeout()

    def subprocess_timeout():
        return intake_bridge.subprocess.TimeoutExpired(cmd="mmctl", timeout=30)

    monkeypatch.setattr(intake_bridge.subprocess, "run", _boom)
    with pytest.raises(BridgeError, match="mmctl post list failed: TimeoutExpired"):
        intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1)


def test_mmctl_reader_translates_a_missing_docker_binary(monkeypatch):
    monkeypatch.setattr(
        intake_bridge.subprocess, "run",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("docker")))
    with pytest.raises(BridgeError, match="FileNotFoundError"):
        intake_bridge.MmctlReader().fetch(_bridge_cfg(), since_ms=1)


# --- RestReader's real HTTP path (urlopen stubbed, _get NOT stubbed) -------


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _rest_cfg(**overrides):
    return _bridge_cfg(transport="rest", base_url="http://mm:8065/", token="t",
                       **overrides)


def test_rest_get_sends_the_bearer_token_and_parses_the_body(monkeypatch):
    seen = {}

    def _urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        if "/channels/name/" in req.full_url:
            return _Resp({"id": "chan1"})
        return _Resp(REST_PAGE)

    monkeypatch.setattr(intake_bridge.urllib.request, "urlopen", _urlopen)
    msgs = intake_bridge.RestReader().fetch(_rest_cfg(), since_ms=1788947692381)
    assert seen["auth"] == "Bearer t"
    assert "//mm:8065/api/v4/" in seen["url"], "the trailing / on base_url must not double"
    assert {m.post_id for m in msgs} == set(REST_PAGE["posts"])


def test_rest_bootstrap_asks_for_a_page_not_a_since_window(monkeypatch):
    urls = []

    def _urlopen(req, timeout=None):
        urls.append(req.full_url)
        return _Resp({"id": "c"} if "/channels/name/" in req.full_url else REST_PAGE)

    monkeypatch.setattr(intake_bridge.urllib.request, "urlopen", _urlopen)
    intake_bridge.RestReader().fetch(_rest_cfg(bootstrap_messages=3), since_ms=0)
    assert "per_page=4" in urls[1] and "since=" not in urls[1]


def test_rest_reports_the_http_status_and_never_the_response_body(monkeypatch):
    """403 = not a member of the channel, 401 = PAT revoked. The body is
    server prose on a request that carried a bearer token, so it is not
    echoed."""
    def _urlopen(req, timeout=None):
        raise intake_bridge.urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, io.BytesIO(b"SECRET-BODY-PROSE"))

    monkeypatch.setattr(intake_bridge.urllib.request, "urlopen", _urlopen)
    with pytest.raises(BridgeError) as excinfo:
        intake_bridge.RestReader().fetch(_rest_cfg(), since_ms=1)
    assert "HTTP 403" in str(excinfo.value)
    assert "SECRET-BODY-PROSE" not in str(excinfo.value)


def test_rest_translates_a_connection_fault(monkeypatch):
    monkeypatch.setattr(
        intake_bridge.urllib.request, "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(
            intake_bridge.urllib.error.URLError("refused")))
    with pytest.raises(BridgeError, match="REST read failed: URLError"):
        intake_bridge.RestReader().fetch(_rest_cfg(), since_ms=1)


def test_rest_refuses_a_channel_lookup_with_no_id(monkeypatch):
    monkeypatch.setattr(intake_bridge.RestReader, "_get",
                        lambda _s, _b, _p: {"not_an_id": True})
    with pytest.raises(BridgeError, match="channel lookup returned no id"):
        intake_bridge.RestReader().fetch(_rest_cfg(), since_ms=1)


# --------------------------------------------------------------------------
# state / cursor


def test_channel_key_refuses_a_name_that_would_escape_the_state_dir():
    with pytest.raises(BridgeError, match="refusing rather than sanitising"):
        intake_bridge._channel_key(_bridge_cfg(channel="../../etc"))


def test_a_corrupt_cursor_refuses_instead_of_replaying_the_channel(sample_project):
    """Degrading a bad cursor to 0 would re-run finished interviews."""
    path = intake_bridge._state_path("demo", "nyxloom:intake")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BridgeError, match="unreadable"):
        intake_bridge.load_state("demo", "nyxloom:intake")


def test_cursor_records_ties_at_the_boundary_millisecond():
    state = intake_bridge.BridgeState(channel_key="nyxloom:intake")
    intake_bridge._advance_cursor(state, [_msg("a", 100, "x"), _msg("b", 100, "y"),
                                          _msg("c", 90, "z")])
    assert state.last_create_at_ms == 100
    assert sorted(state.boundary_ids) == ["a", "b"]


def test_advancing_a_cursor_over_nothing_leaves_it_alone():
    state = intake_bridge.BridgeState(channel_key="k", last_create_at_ms=100,
                                      boundary_ids=["a"])
    intake_bridge._advance_cursor(state, [])
    assert (state.last_create_at_ms, state.boundary_ids) == (100, ["a"])


def test_cursor_accumulates_boundary_ids_when_nothing_is_newer():
    state = intake_bridge.BridgeState(channel_key="k", last_create_at_ms=100,
                                      boundary_ids=["a"])
    intake_bridge._advance_cursor(state, [_msg("b", 100, "y")])
    assert state.last_create_at_ms == 100
    assert sorted(state.boundary_ids) == ["a", "b"]


# --------------------------------------------------------------------------
# poll_once -- gates


def test_poll_is_unconfigured_without_a_transport(sample_project):
    sample_project.intake_bridge = IntakeBridgeConfig()
    result = intake_bridge.poll_once(sample_project, "demo")
    assert result.status == "unconfigured"


def test_poll_fails_closed_without_its_own_reply_webhook(sample_project, monkeypatch):
    """Falling back to cfg.notify's webhook would answer an intake question
    in `alerts` -- the notification channel, bound to a different account."""
    monkeypatch.setenv(control_auth.CHANNEL_OPERATOR_ENV, "operator@example")
    sample_project.intake_bridge = _bridge_cfg(webhook_url=None)
    result = intake_bridge.poll_once(sample_project, "demo")
    assert result.status == "unconfigured"
    assert "reply webhook" in result.detail


def test_poll_refuses_without_a_named_channel_operator(sample_project, monkeypatch):
    monkeypatch.delenv(control_auth.CHANNEL_OPERATOR_ENV, raising=False)
    sample_project.intake_bridge = _bridge_cfg()
    reader = _FakeReader([_msg("p1", 100, "hello")])
    result = intake_bridge.poll_once(sample_project, "demo", reader=reader)
    assert result.status == "refused"
    assert reader.since_calls == [], "state must not be read before the operator is resolved"


def test_a_refused_poll_is_audited_on_the_control_ledger(sample_project, monkeypatch):
    monkeypatch.delenv(control_auth.CHANNEL_OPERATOR_ENV, raising=False)
    sample_project.intake_bridge = _bridge_cfg()
    intake_bridge.poll_once(sample_project, "demo", reader=_FakeReader([]))
    refusals = [e for e in storage.iter_events(control_auth.CONTROL_LEDGER_PROJECT, since=0)
                if e.type is EventType.CONTROL_MUTATION_REFUSED]
    assert refusals, "a refused channel mutation must leave an audit record"


# --------------------------------------------------------------------------
# poll_once -- behaviour


def test_first_poll_adopts_the_head_and_ingests_nothing(wired):
    cfg, turns, sent = wired
    reader = _FakeReader([_msg("p1", 100, "old"), _msg("p2", 200, "older still")])
    result = intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert result.status == "bootstrapped"
    assert result.cursor_ms == 200
    assert turns == [], "a first poll must not replay the channel into the interview"
    assert sent == []


def test_bootstrap_messages_adopts_that_many_from_the_head(wired):
    """The opt-in to picking up a little history: adopt the newest N as a
    real first turn instead of only recording where 'now' is."""
    cfg, turns, _sent = wired
    cfg.intake_bridge = _bridge_cfg(bootstrap_messages=2)
    reader = _FakeReader([_msg("a", 100, "oldest"), _msg("b", 200, "middle"),
                          _msg("c", 300, "newest")])
    result = intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert result.status == "ok"
    assert turns[0][1] == "middle\nnewest", "only the newest two are adopted"
    assert intake_bridge.load_state("demo", "nyxloom:intake").last_create_at_ms == 300


def test_bootstrap_adopting_the_whole_short_channel_still_ingests_it(wired):
    """bootstrap_messages >= the channel length leaves nothing to park the
    cursor on -- the adopted window must still reach the turn engine."""
    cfg, turns, _sent = wired
    cfg.intake_bridge = _bridge_cfg(bootstrap_messages=5)
    reader = _FakeReader([_msg("a", 100, "only one")])
    intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert turns[0][1] == "only one"


def test_bootstrap_does_not_ingest_what_it_parked_the_cursor_over(wired):
    """The messages OLDER than the adopted window are decided, never
    ingested, and never come back on the next poll."""
    cfg, turns, _sent = wired
    cfg.intake_bridge = _bridge_cfg(bootstrap_messages=1)
    batch = [_msg("a", 100, "ancient"), _msg("b", 200, "newest")]
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader(batch))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader(batch))
    assert [t[1] for t in turns] == ["newest"]


def test_a_poll_over_an_unchanged_channel_reports_no_new_messages(wired):
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    reader = _FakeReader([_msg("p0", 50, "seed")])
    result = intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert result.detail == "no new messages"
    assert turns == []


def test_a_second_poll_ingests_only_what_is_new(wired):
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p1", 100, "old")]))
    reader = _FakeReader([_msg("p1", 100, "old"), _msg("p2", 200, "brand new")])
    result = intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert reader.since_calls == [100]
    assert result.ingested == 1
    assert [t[1] for t in turns] == ["brand new"]


def test_the_bots_own_reply_is_never_ingested(wired):
    """The loop guard. `props.from_webhook` is server-attributed, so this
    holds even for a reply whose TEXT looks like a human turn."""
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    reader = _FakeReader([_msg("bot", 100, "reply to: hello", program=True)])
    result = intake_bridge.poll_once(cfg, "demo", reader=reader)
    assert turns == []
    assert result.detail == "nothing but program/system posts"


def test_skipped_posts_still_advance_the_cursor(wired):
    """Otherwise the bot's own replies are re-fetched forever and the poll
    window grows without bound."""
    cfg, _turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo",
                            reader=_FakeReader([_msg("bot", 100, "x", program=True)]))
    state = intake_bridge.load_state("demo", "nyxloom:intake")
    assert state.last_create_at_ms == 100


def test_system_and_deleted_posts_are_not_turns(wired):
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([
        _msg("j", 100, "x joined the channel.", system=True),
        _msg("d", 110, "retracted", deleted=True),
    ]))
    assert turns == []


def test_one_poll_costs_exactly_one_turn_however_many_messages_arrived(wired):
    """Three lines typed between two polls are ONE utterance: the poll
    interval is the utterance boundary, and a poll must never fan out into
    N model calls."""
    cfg, turns, sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([
        _msg("a", 100, "add a widget"), _msg("b", 110, "it must be blue"),
        _msg("c", 120, "priority 2"),
    ]))
    assert len(turns) == 1
    assert turns[0][1] == "add a widget\nit must be blue\npriority 2"
    assert len(sent) == 1


def test_messages_beyond_the_per_poll_cap_are_deferred_not_dropped(wired):
    cfg, turns, _sent = wired
    cfg.intake_bridge = _bridge_cfg(max_messages_per_poll=2)
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    batch = [_msg("a", 100, "one"), _msg("b", 110, "two"), _msg("c", 120, "three")]
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader(batch))
    assert turns[0][1] == "one\ntwo"
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader(batch))
    assert turns[1][1] == "three"


def test_the_turn_text_is_capped_on_the_way_in(wired):
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader(
        [_msg("a", 100, "x" * (intake_bridge.MAX_TURN_CHARS + 500))]))
    assert len(turns[0][1]) == intake_bridge.MAX_TURN_CHARS


def test_the_same_intake_id_carries_across_polls(wired):
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("b", 200, "second")]))
    assert turns[0][0] == turns[1][0]


def test_a_finalised_interview_closes_the_conversation(wired, monkeypatch):
    """Once intake_chat has filed the brief the chat can produce nothing
    more, so the next message must open a NEW one rather than talk into it."""
    cfg, turns, _sent = wired
    finished = intake_chat.IntakeChat(intake_id="i1", project="demo", brief_id="B-9")
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    monkeypatch.setattr(intake_chat, "load_chat", lambda *_a, **_k: finished)
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("b", 200, "second")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("c", 300, "third")]))
    assert turns[1][0] != turns[2][0]


def test_new_intake_resets_the_conversation_without_a_model_turn(wired):
    cfg, turns, sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    before = len(turns)
    result = intake_bridge.poll_once(cfg, "demo",
                                     reader=_FakeReader([_msg("b", 200, "New Intake")]))
    assert result.detail == "conversation reset"
    assert len(turns) == before
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("c", 300, "again")]))
    assert turns[-1][0] != turns[before - 1][0]


def test_an_ingested_turn_is_audited_under_the_named_operator(wired):
    cfg, _turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    replies = [e for e in storage.iter_events("demo", since=0)
               if e.type is EventType.INTAKE_REPLY_RECORDED]
    assert len(replies) == 1
    assert replies[0].actor.id == "operator@example"


# --------------------------------------------------------------------------
# the reply channel


def test_the_reply_never_inherits_the_alerts_channel_override(wired):
    """cfg.notify.mattermost_channel is 'alerts' on the live deployment, and
    Mattermost honours a payload `channel` as an OVERRIDE -- inheriting it
    would post every intake reply into the notification channel."""
    cfg, _turns, sent = wired
    cfg.notify.mattermost_channel = "alerts"
    cfg.notify.webhook_url = "http://mm:8065/hooks/ALERTS-HOOK"
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    nc, note = sent[0]
    assert nc.mattermost_channel is None
    assert nc.webhook_url == "http://mm:8065/hooks/abc"
    assert nc.backend == "mattermost"
    assert note["body"].startswith("reply to:")


def test_the_click_target_is_a_fragment_the_page_actually_has(wired):
    """`intake.html` reads NO query parameters but does emit
    `id="transcript-<intake_id>"` per open card, so `?intake=` would be a
    link that only looks like it deep-links."""
    cfg, _turns, sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "first")]))
    _nc, note = sent[0]
    intake_id = note["title"].split()[-1]
    assert note["click"] == f"{intake_bridge.INTAKE_UI_URL}#transcript-{intake_id}"
    assert "?" not in note["click"]


@pytest.mark.parametrize("intake_id", ["-", "../../evil", "", "a b", "x" * 200])
def test_a_click_target_never_interpolates_an_unsafe_id(intake_id):
    """The reset notice has no id at all, so the guard lives here rather
    than being assumed of every caller."""
    assert intake_bridge.intake_chat_url(intake_id) == intake_bridge.INTAKE_UI_URL


def test_a_failed_reply_does_not_lose_the_turn(wired, monkeypatch):
    """The turn already happened and intake_chat already persisted it;
    raising here would strand a real conversation behind a transport
    hiccup."""
    cfg, turns, _sent = wired
    intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("p0", 50, "seed")]))
    monkeypatch.setattr(intake_bridge.notify, "send",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    result = intake_bridge.poll_once(cfg, "demo", reader=_FakeReader([_msg("a", 100, "hi")]))
    assert result.reply_posted is False
    assert len(turns) == 1
    assert intake_bridge.load_state("demo", "nyxloom:intake").last_create_at_ms == 100


# --------------------------------------------------------------------------
# config resolution ([intake_bridge] + the two env-wins credentials)


def _reload_with_bridge_table(sample_project, table: str):
    """Append an `[intake_bridge]` table to the sample project's toml and
    reload it through the REAL ProjectConfig.load."""
    toml_path = sample_project.root / ".nyxloom" / "project.toml"
    toml_path.write_text(toml_path.read_text() + "\n" + table, encoding="utf-8")
    return config.ProjectConfig.load(sample_project.root)


def test_an_absent_table_leaves_the_bridge_off(sample_project):
    assert sample_project.intake_bridge.transport is None
    assert intake_bridge.resolve_reader(sample_project.intake_bridge) is None


def test_the_table_is_parsed_off_the_project_toml(sample_project):
    cfg = _reload_with_bridge_table(sample_project, """
[intake_bridge]
transport = "mmctl"
team = "nyxloom"
channel = "intake"
container = "nyxloom-prod-mattermost"
base_url = "http://mm:8065"
max_messages_per_poll = 5
bootstrap_messages = 2
""")
    bc = cfg.intake_bridge
    assert (bc.transport, bc.team, bc.channel) == ("mmctl", "nyxloom", "intake")
    assert bc.container == "nyxloom-prod-mattermost"
    assert (bc.max_messages_per_poll, bc.bootstrap_messages) == (5, 2)


def test_both_credentials_come_from_the_environment(sample_project, monkeypatch):
    """Neither the PAT nor the reply webhook URL may live in the committed,
    bind-mounted nyxloom.toml -- both are bearer secrets."""
    monkeypatch.setenv("NYXLOOM_INTAKE_MM_TOKEN", "pat-from-env")
    monkeypatch.setenv("NYXLOOM_INTAKE_WEBHOOK_URL", "http://mm:8065/hooks/env")
    cfg = _reload_with_bridge_table(sample_project, """
[intake_bridge]
transport = "rest"
team = "nyxloom"
channel = "intake"
base_url = "http://mm:8065"
""")
    assert cfg.intake_bridge.token == "pat-from-env"
    assert cfg.intake_bridge.webhook_url == "http://mm:8065/hooks/env"
    assert intake_bridge.resolve_reader(cfg.intake_bridge).name == "rest"


def test_the_credential_var_names_are_renameable_per_project(sample_project, monkeypatch):
    monkeypatch.delenv("NYXLOOM_INTAKE_MM_TOKEN", raising=False)
    monkeypatch.setenv("OTHER_TOKEN_VAR", "pat-from-other")
    monkeypatch.setenv("OTHER_HOOK_VAR", "http://mm:8065/hooks/other")
    cfg = _reload_with_bridge_table(sample_project, """
[intake_bridge]
transport = "mmctl"
token_env = "OTHER_TOKEN_VAR"
webhook_url_env = "OTHER_HOOK_VAR"
""")
    assert cfg.intake_bridge.token == "pat-from-other"
    assert cfg.intake_bridge.webhook_url == "http://mm:8065/hooks/other"


def test_an_unset_credential_var_leaves_the_field_none(sample_project, monkeypatch):
    monkeypatch.delenv("NYXLOOM_INTAKE_MM_TOKEN", raising=False)
    monkeypatch.delenv("NYXLOOM_INTAKE_WEBHOOK_URL", raising=False)
    cfg = _reload_with_bridge_table(sample_project, """
[intake_bridge]
transport = "mmctl"
""")
    assert cfg.intake_bridge.token is None
    assert cfg.intake_bridge.webhook_url is None


def test_a_bad_transport_in_the_toml_fails_the_whole_config_load(sample_project):
    """Loudly at load, like NotifyConfig.backend -- not silently ignored into
    a bridge that reads from a channel nobody named."""
    with pytest.raises(ValueError, match="transport must be one of"):
        _reload_with_bridge_table(sample_project, """
[intake_bridge]
transport = "smoke-signal"
""")


# --------------------------------------------------------------------------
# CLI verb


def test_cli_poll_reports_and_exits_zero_when_unconfigured(sample_project, capsys):
    from nyxloom import cli

    assert cli.main(["intake-bridge", "poll", "demo"]) == 0
    assert "status=unconfigured" in capsys.readouterr().out


def test_cli_poll_exits_one_only_on_a_refusal(sample_project, monkeypatch, capsys):
    from nyxloom import cli

    monkeypatch.delenv(control_auth.CHANNEL_OPERATOR_ENV, raising=False)
    monkeypatch.setattr(intake_bridge, "resolve_reader", lambda _bc: _FakeReader([]))
    monkeypatch.setattr(
        config.ProjectConfig, "load",
        classmethod(lambda _cls, _root: _with_bridge(sample_project)))
    assert cli.main(["intake-bridge", "poll", "demo"]) == 1
    assert "status=refused" in capsys.readouterr().out


def _with_bridge(cfg):
    cfg.intake_bridge = _bridge_cfg()
    return cfg


def test_cli_transport_override_still_goes_through_is_configured(sample_project, capsys):
    """`--transport rest` over a project with no PAT must report
    'unconfigured', NOT silently fall through to the mmctl socket."""
    from nyxloom import cli

    toml_path = sample_project.root / ".nyxloom" / "project.toml"
    toml_path.write_text(toml_path.read_text() + """
[intake_bridge]
transport = "mmctl"
team = "nyxloom"
channel = "intake"
container = "nyxloom-prod-mattermost"
""", encoding="utf-8")
    assert cli.main(["intake-bridge", "poll", "demo", "--transport", "rest"]) == 0
    out = capsys.readouterr().out
    assert "status=unconfigured" in out


def test_cli_rejects_an_unknown_transport_override(sample_project):
    from nyxloom import cli

    assert cli.main(["intake-bridge", "poll", "demo", "--transport", "carrier"]) == 2


def test_cli_bare_verb_group_prints_usage(sample_project):
    from nyxloom import cli

    assert cli.main(["intake-bridge"]) == 2

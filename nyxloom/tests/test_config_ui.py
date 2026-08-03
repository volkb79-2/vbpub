"""Tests for the P15 UI config surface (handoff/P15-ui-config.md):
audited HTTP config-mutation endpoints (policy / pause-mode / tier remap),
config.html rendering, and the last-activity render column.

Cross-package note: the daemon's HTTP server and config.py's two surgical
TOML-edit functions are this package's own additions; reconcile.py's
pause-mode planner semantics (oracle 7) are covered in test_reconcile.py,
and the ntfy verb surface (oracle 7) in test_commands.py -- both minimal
additions to those files per the P15 handoff. The CLI `pause` verb
(src/nyxloom/cli.py, PACKAGE P10) is explicitly OUT OF SCOPE for this
package (not in its owned-files list) and is therefore not exercised here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
import structlog.contextvars

from nyxloom import config, control_auth, daemon, lint, log, paths, reconcile, render, storage
from nyxloom.types import (
    ActorKind, Attempt, AttemptState, EventType, Role, Route, TaskState,
    TaskStateFile,
)


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI oracle,
    docs/plan-logging.md P05c). config.py's update_project_policy/
    update_routes now carry log.info/log.warning calls reachable through
    this file's live daemon HTTP fixture."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py)

TIER_ROUTES_TOML = """\
revision = "test-rev"

[tiers.flash-high]
routes = ["fake-cli"]

[tiers.frontier-review]
routes = ["opus-cli"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
status = "primary"

[routes.opus-cli]
cli = "fake"
model = "opus-model"
probe = ["true"]
usage_source = "none"

[routes.claude-sonnet5-high]
cli = "fake"
model = "sonnet5-model"
probe = ["true"]
usage_source = "none"
"""


def _set_ephemeral_http_port(cfg):
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


def _add_policy_comment(cfg):
    """A stray comment line inside [policy] -- proves a policy POST is a
    surgical single-line edit, never a whole-file reserialize (oracle 1)."""
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    text = text.replace("[policy]\n", "[policy]\n# ops note: keep this comment intact\n", 1)
    ptoml.write_text(text, encoding="utf-8")


@pytest.fixture()
def cfg_daemon(tmp_state, sample_project, monkeypatch):
    """A live Daemon HTTP server over the 'demo' project, with a richer
    routes.toml (two tiers, three route defs) and a policy-section comment
    to assert against. plan_project is stubbed to a no-op (this suite tests
    the HTTP config surface, not the reconcile pass itself)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    paths.routes_path().write_text(TIER_ROUTES_TOML, encoding="utf-8")
    _add_policy_comment(sample_project)
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.http_port != 0
    try:
        yield d
    finally:
        d.stop()
        t.join(timeout=5)
        assert not t.is_alive(), "cfg_daemon fixture's daemon thread outlived teardown and may pollute global logging"


def _operator() -> control_auth.OperatorCredential:
    """The daemon's own operator record (CR-15). `ensure()` rather than
    `load()` so a test that never started the HTTP server still gets the same
    store the daemon would have bootstrapped."""
    return control_auth.CredentialStore(paths.daemon_dir()).ensure()


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json",
                 **control_auth.authorization_header(_operator())},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _project_toml_text(cfg) -> str:
    return (cfg.root / ".nyxloom" / "project.toml").read_text(encoding="utf-8")


# ==========================================================================
# Oracle 1: policy POST -> surgical edit (comment intact), CONFIG_CHANGED
# with old/new, next run_pass uses the new cap.
# ==========================================================================

def test_policy_update_full_flow(cfg_daemon, sample_project, monkeypatch):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    status, _resp = _post(base, "/api/config/policy",
                           {"project": "demo", "key": "max_active_tasks", "value": 5})
    assert status == 200

    text = _project_toml_text(sample_project)
    assert "max_active_tasks = 5" in text
    assert "max_active_tasks = 2" not in text
    assert "ready_queue_target = 3" in text  # neighbouring line untouched
    assert "# ops note: keep this comment intact" in text  # comment survives

    changed = [e for e in storage.iter_events("demo") if e.type is EventType.CONFIG_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload == {"scope": "policy", "key": "max_active_tasks", "old": 2, "new": 5}
    assert changed[0].actor.kind is ActorKind.OPERATOR
    # CR-15: the authenticated operator, not the interface name "ui".
    assert changed[0].actor.id == _operator().operator_id

    captured = []
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: (captured.append(inp), [])[1])
    d.run_pass("demo")
    # NB: assert the CONTRACT ("a reconcile pass after the config change sees
    # the new cap"), not an exact call count. The cfg_daemon fixture runs a
    # LIVE Daemon.run() reconcile loop in the background, which shares this same
    # monkeypatched plan_project and can slip in its own "demo" pass between the
    # setattr above and this assertion -- a real ~5%-under-xdist race that
    # `== 1` turned into an intermittent gate failure (2026-07-27). Every
    # captured input reflects the post-change config, so check the last one.
    assert captured, "run_pass did not invoke plan_project"
    assert captured[-1].cfg.policy.max_active_tasks == 5


# ==========================================================================
# Oracle 2: bounds -- 0 or 999 -> 400, file untouched, no event.
# ==========================================================================

def test_policy_bounds_rejects_zero_and_too_large(cfg_daemon, sample_project):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    original = _project_toml_text(sample_project)

    for bad_value in (0, 999):
        status, resp = _post(base, "/api/config/policy",
                              {"project": "demo", "key": "max_active_tasks", "value": bad_value})
        assert status == 400
        assert "error" in resp

    assert _project_toml_text(sample_project) == original
    assert not any(e.type is EventType.CONFIG_CHANGED for e in storage.iter_events("demo"))


def test_policy_unknown_key_rejected(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, resp = _post(base, "/api/config/policy",
                          {"project": "demo", "key": "not_a_real_key", "value": 3})
    assert status == 400
    assert "error" in resp


# ==========================================================================
# Oracle 3: tier remap -- rewrites only that tier's routes= line; unknown
# route id -> 400, no write.
# ==========================================================================

def test_tier_remap_rewrites_only_that_tier(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    status, _resp = _post(base, "/api/config/tier",
                           {"tier": "flash-high", "routes": ["claude-sonnet5-high"]})
    assert status == 200

    text = paths.routes_path().read_text(encoding="utf-8")
    assert 'routes = ["claude-sonnet5-high"]' in text
    assert 'routes = ["opus-cli"]' in text  # frontier-review line untouched

    changed = [e for e in storage.iter_events("demo") if e.type is EventType.CONFIG_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload["scope"] == "routes"
    assert changed[0].payload["key"] == "flash-high"
    assert changed[0].payload["old"] == ["fake-cli"]
    assert changed[0].payload["new"] == ["claude-sonnet5-high"]


def test_tier_remap_unknown_route_id_400_no_write(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    original = paths.routes_path().read_text(encoding="utf-8")

    status, resp = _post(base, "/api/config/tier",
                          {"tier": "flash-high", "routes": ["no-such-route"]})
    assert status == 400
    assert "error" in resp
    assert paths.routes_path().read_text(encoding="utf-8") == original


def test_tier_remap_unknown_tier_404(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, _resp = _post(base, "/api/config/tier",
                           {"tier": "no-such-tier", "routes": ["fake-cli"]})
    assert status == 404


# ==========================================================================
# Oracle 4 / 7 (UI surface): pause via UI -> flag + event; resume reverses.
# ==========================================================================

def test_pause_via_ui_then_resume(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    flag = paths.pause_flag("demo")

    status, _resp = _post(base, "/api/config/pause", {"project": "demo", "mode": "drain-agents"})
    assert status == 200
    assert flag.exists()
    assert flag.read_text(encoding="utf-8") == "drain-agents"

    set_evs = [e for e in storage.iter_events("demo") if e.type is EventType.PAUSE_SET]
    assert len(set_evs) == 1
    assert set_evs[0].payload == {"mode": "drain-agents"}
    assert set_evs[0].actor.id == _operator().operator_id
    assert set_evs[0].actor.kind is ActorKind.OPERATOR

    status2, _resp2 = _post(base, "/api/config/pause", {"project": "demo", "mode": "run"})
    assert status2 == 200
    assert not flag.exists()
    cleared = [e for e in storage.iter_events("demo") if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].actor.id == _operator().operator_id


def test_pause_unknown_mode_rejected(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, resp = _post(base, "/api/config/pause", {"project": "demo", "mode": "bogus"})
    assert status == 400
    assert not paths.pause_flag("demo").exists()


# ==========================================================================
# Oracle 5: config.html renders current policy + tier table; no secrets;
# no innerHTML.
# ==========================================================================

def test_config_html_renders_policy_and_tiers_no_secrets_no_innerhtml(sample_project, tmp_state):
    credential = control_auth.CredentialStore(paths.daemon_dir()).ensure("alice")
    paths.routes_path().write_text(TIER_ROUTES_TOML, encoding="utf-8")
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    content = (paths.www_dir() / "config.html").read_text(encoding="utf-8")
    assert "max_active_tasks" in content
    assert "flash-high" in content
    assert "fake-cli" in content
    assert "opus-cli" in content
    assert "innerHTML" not in content

    # The no-secrets marker scan, kept from the original oracle. CR-15 drops
    # only "authorization" from the marker list -- the header NAME is now
    # legitimately in the page -- and replaces it with the stronger check
    # below: the credential VALUE appears nowhere, and the header is built
    # from a runtime-supplied variable rather than a baked-in literal.
    lowered = content.lower()
    for marker in ("token", "secret", "password"):
        assert marker not in lowered
    assert "'Authorization': 'Bearer ' + credential" in content

    # And the live credential itself is never rendered, under any spelling.
    assert credential.credential not in content
    for page in sorted(paths.www_dir().rglob("*.html")):
        assert credential.credential not in page.read_text(encoding="utf-8"), page

    index_content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'href="config.html"' in index_content


# ==========================================================================
# Oracle 6: traversal/method safety -- GET on POST endpoints -> 405;
# unknown project -> 404.
# ==========================================================================

def test_get_on_config_endpoints_is_405(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    for path in ("/api/config/policy", "/api/config/pause", "/api/config/tier"):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}{path}", timeout=5)
        assert exc_info.value.code == 405


def test_post_config_unknown_project_404(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, _resp = _post(base, "/api/config/policy",
                           {"project": "ghost", "key": "max_active_tasks", "value": 3})
    assert status == 404
    status2, _resp2 = _post(base, "/api/config/pause", {"project": "ghost", "mode": "run"})
    assert status2 == 404


def test_post_unknown_path_404(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, _resp = _post(base, "/api/config/does-not-exist", {})
    assert status == 404


# ==========================================================================
# Oracle 8: last-activity -- seeded attempt log with known mtime renders
# the expected age string in both the index and task-page tables.
# ==========================================================================

def test_last_activity_column_index_and_task_page(sample_project, tmp_state):
    project_id = "demo"
    started = datetime.now(timezone.utc) - timedelta(minutes=10)
    attempt_id = "att-la"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model")
    att = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
                  route=route, started=started)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P30-active", project=project_id,
        state=TaskState.ACTIVE, since=started, attempts=[att],
    ))

    log_dir = paths.attempt_dir(project_id, attempt_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "attempt.log"
    log_file.write_text("hello\n", encoding="utf-8")
    three_min_ago = time.time() - 180
    os.utime(log_file, (three_min_ago, three_min_ago))

    render.render_all({"demo": sample_project.root})

    index_content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert "3m" in index_content

    task_content = (paths.www_dir() / "task" / "demo" / "demo-P30-active.html").read_text(
        encoding="utf-8")
    assert "3m" in task_content


def test_last_activity_dash_when_no_log(sample_project, tmp_state):
    project_id = "demo"
    started = datetime.now(timezone.utc)
    route = Route(route_id="fake-cli", cli="fake", model="fake-model")
    att = Attempt(attempt_id="att-nolog", role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
                  route=route, started=started)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P31-nolog", project=project_id,
        state=TaskState.ACTIVE, since=started, attempts=[att],
    ))

    render.render_all({"demo": sample_project.root})

    task_content = (paths.www_dir() / "task" / "demo" / "demo-P31-nolog.html").read_text(
        encoding="utf-8")
    # _format_age(None) == "-" (plain ASCII hyphen -- distinct from the
    # em-dash '—' this page uses elsewhere for other missing fields).
    assert "<td>-</td>" in task_content


# ==========================================================================
# 2026-08-02 review amendment (RISK-005): the mutating HTTP surface is
# UNAUTHENTICATED and the deployed bind is 0.0.0.0 on the ciu bridge, so a
# browser that can reach the dashboard could previously be made to pause a
# project, rewrite policy, or answer a human decision from any other site.
# daemon.Daemon._reject_cross_site closes the drive-by half of that.
#
# Every oracle below asserts the ARTIFACT, not just the status code: a
# refused request must leave project.toml byte-identical and append no
# CONFIG_CHANGED event. A 403 that still mutated would be worthless.
# ==========================================================================

def _post_headers(base: str, path: str, body: dict,
                  headers: dict[str, str]) -> tuple[int, dict]:
    """_post's sibling with caller-controlled headers (no implicit
    Content-Type), for exercising the cross-site refusals."""
    data = json.dumps(body).encode("utf-8")
    # Authenticated by default so these oracles isolate the CSRF refusals from
    # CR-15's credential refusal; the caller's headers still win on collision.
    request_headers = {**control_auth.authorization_header(_operator()), **headers}
    req = urllib.request.Request(f"{base}{path}", data=data, method="POST",
                                 headers=request_headers)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _config_changed_count(project: str = "demo") -> int:
    return sum(1 for e in storage.iter_events(project)
               if e.type is EventType.CONFIG_CHANGED)


@pytest.mark.parametrize("headers,why", [
    ({}, "no content-type at all"),
    # The form-CSRF vector: a cross-site <form> can only produce these three
    # encodings, so none of them may reach a mutation handler.
    ({"Content-Type": "text/plain"}, "text/plain form post"),
    ({"Content-Type": "application/x-www-form-urlencoded"}, "urlencoded form post"),
    ({"Content-Type": "multipart/form-data; boundary=x"}, "multipart form post"),
])
def test_mutation_refused_without_json_content_type(cfg_daemon, sample_project,
                                                    headers, why):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    before_toml = _project_toml_text(sample_project)
    before_events = _config_changed_count()

    status, resp = _post_headers(
        base, "/api/config/policy",
        {"project": "demo", "key": "max_active_tasks", "value": 5}, headers)

    assert status == 403, why
    assert "content-type" in resp["error"]
    assert _project_toml_text(sample_project) == before_toml, why
    assert _config_changed_count() == before_events, why


def test_mutation_refused_from_cross_site_origin(cfg_daemon, sample_project):
    """Correct Content-Type but a foreign Origin -- the fetch()-from-another-
    site vector. Browsers set Origin on cross-site POSTs; the server compares
    it against Host."""
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    before_toml = _project_toml_text(sample_project)
    before_events = _config_changed_count()

    status, resp = _post_headers(
        base, "/api/config/policy",
        {"project": "demo", "key": "max_active_tasks", "value": 5},
        {"Content-Type": "application/json", "Origin": "http://evil.example"})

    assert status == 403
    assert resp["error"] == "cross-site origin"
    assert _project_toml_text(sample_project) == before_toml
    assert _config_changed_count() == before_events


def test_decision_reply_refused_from_cross_site_origin(cfg_daemon):
    """The sharpest edge of RISK-005: /api/decision/reply is how a HUMAN
    answers an escalation. A cross-site caller must not reach it -- and must
    be refused BEFORE any lookup, so an attacker cannot even probe which
    decision ids exist (a valid id 404s, an invalid one would too)."""
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    status, resp = _post_headers(
        base, "/api/decision/reply", {"decision_id": "dec-1", "text": "approved"},
        {"Content-Type": "application/json", "Origin": "http://evil.example"})

    assert status == 403
    assert resp["error"] == "cross-site origin"


def test_same_origin_browser_post_still_mutates(cfg_daemon, sample_project):
    """The guard must not break the dashboard itself: render.py's fetch()
    calls send Content-Type: application/json, and the browser adds a
    same-origin Origin. That combination still applies the edit."""
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    host = f"127.0.0.1:{d.http_port}"

    status, _resp = _post_headers(
        base, "/api/config/policy",
        {"project": "demo", "key": "max_active_tasks", "value": 5},
        {"Content-Type": "application/json", "Origin": f"http://{host}"})

    assert status == 200
    assert "max_active_tasks = 5" in _project_toml_text(sample_project)


def test_non_browser_client_without_origin_still_mutates(cfg_daemon, sample_project):
    """curl and the CLI send no Origin header at all. Absent Origin is not
    evidence of a cross-site request, so it must not be refused -- otherwise
    this guard would silently break every scripted operator workflow."""
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    status, _resp = _post_headers(
        base, "/api/config/policy",
        {"project": "demo", "key": "ready_queue_target", "value": 7},
        {"Content-Type": "application/json"})

    assert status == 200
    assert "ready_queue_target = 7" in _project_toml_text(sample_project)

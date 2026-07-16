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
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from nyxloom import config, daemon, lint, paths, reconcile, render, storage
from nyxloom.types import (
    ActorKind, Attempt, AttemptState, EventType, Role, Route, TaskState,
    TaskStateFile,
)


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


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
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
    assert changed[0].actor.id == "ui"

    captured = []
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: (captured.append(inp), [])[1])
    d.run_pass("demo")
    assert len(captured) == 1
    assert captured[0].cfg.policy.max_active_tasks == 5


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
# Oracle 4 / 7 (UI surface): pause via UI -> flag + event; unpause reverses.
# ==========================================================================

def test_pause_via_ui_then_unpause(cfg_daemon):
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
    assert set_evs[0].actor.id == "ui"
    assert set_evs[0].actor.kind is ActorKind.OPERATOR

    status2, _resp2 = _post(base, "/api/config/pause", {"project": "demo", "mode": "run"})
    assert status2 == 200
    assert not flag.exists()
    cleared = [e for e in storage.iter_events("demo") if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].actor.id == "ui"


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
    paths.routes_path().write_text(TIER_ROUTES_TOML, encoding="utf-8")
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    content = (paths.www_dir() / "config.html").read_text(encoding="utf-8")
    assert "max_active_tasks" in content
    assert "flash-high" in content
    assert "fake-cli" in content
    assert "opus-cli" in content
    assert "innerHTML" not in content

    lowered = content.lower()
    for marker in ("token", "secret", "password", "authorization"):
        assert marker not in lowered

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

"""Tests for P39 ntfy URL single-source resolution (config.NotifyConfig).

The NTFY_URL env var is authoritative over the TOML source: the ntfy server
owns its own URL (a deployment fact), so no project's nyxloom.toml has to
re-hardcode it. Resolution happens at config load, so a caller constructing
NotifyConfig(...) directly still keeps the url it passes.
"""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
from pathlib import Path

import pytest
import structlog.contextvars

from nyxloom import log
from nyxloom.config import CarveStageConfig, NotifyConfig, ProjectConfig, Prices

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI oracle,
    docs/plan-logging.md P05c). ProjectConfig.load now carries the P05c
    config-resolved DEBUG (oracle 2 below drives it explicitly with its own
    log.configure(); every OTHER test in this file must not have that call
    leak through structlog's pre-configure PrintLogger default)."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()

_PROJECT_TOML = """\
[project]
id = "p39proj"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]

[policy]

[notify]
{notify_lines}
"""


def _write_project(root: Path, notify_lines: str = "") -> Path:
    """Write a minimal nyxloom-trove/nyxloom.toml and return the repo root."""
    trove = root / "nyxloom-trove"
    trove.mkdir(parents=True, exist_ok=True)
    (trove / "nyxloom.toml").write_text(
        _PROJECT_TOML.format(notify_lines=notify_lines), encoding="utf-8"
    )
    return root


# =========================================================================
# Oracle O1: NTFY_URL env overrides a project's [notify] ntfy_url in toml
# =========================================================================

def test_ntfy_url_env_overrides_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://env-wins.example")
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://env-wins.example"


# =========================================================================
# Oracle O2: fallback chain env -> toml -> None
# =========================================================================

def test_ntfy_url_falls_back_to_toml_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://toml-value.example"


def test_ntfy_url_none_without_env_or_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    root = _write_project(tmp_path)

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url is None


def test_empty_ntfy_url_env_does_not_shadow_toml(tmp_path, monkeypatch):
    """An empty/blank NTFY_URL is treated as unset, not as a url that
    disables notifications by pointing them at ''."""
    monkeypatch.setenv("NTFY_URL", "")
    root = _write_project(tmp_path, 'ntfy_url = "https://toml-value.example"\n')

    cfg = ProjectConfig.load(root)

    assert cfg.notify.ntfy_url == "https://toml-value.example"


# =========================================================================
# The env is authoritative over the TOML source ONLY. Direct construction
# keeps the url the caller passes -- otherwise NotifyConfig(ntfy_url=None)
# could not express "notifications disabled", and callers aiming at a
# specific endpoint (a local stub, a closed port) would be silently
# retargeted at the deployment server whenever NTFY_URL happened to be set.
# =========================================================================

def test_direct_construction_is_not_overridden_by_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")

    nc = NotifyConfig(ntfy_url="http://127.0.0.1:8099")

    assert nc.ntfy_url == "http://127.0.0.1:8099"


def test_direct_construction_can_express_disabled_under_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")

    nc = NotifyConfig(ntfy_url=None)

    assert nc.ntfy_url is None


# =========================================================================
# Oracle O3: nyxloom's own nyxloom-trove/nyxloom.toml no longer hardcodes
# ntfy_url; config still loads and NTFY_URL resolves the URL.
# =========================================================================

def test_repo_own_config_has_no_toml_ntfy_url():
    import tomllib

    toml_path = REPO_ROOT / "nyxloom-trove" / "nyxloom.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert "ntfy_url" not in data.get("notify", {})


def test_repo_own_config_loads_and_resolves_ntfy_url_from_env(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://deployment.example")
    cfg = ProjectConfig.load(REPO_ROOT)
    assert cfg.notify.ntfy_url == "https://deployment.example"


def test_repo_own_config_notifications_disabled_without_env(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    cfg = ProjectConfig.load(REPO_ROOT)
    assert cfg.notify.ntfy_url is None


# =========================================================================
# PACKAGE F1 (docs/spine-documents-spec.md): north_star/product_definition/
# roadmap/backlog are optional trove-relative-path config keys on
# ProjectConfig. Cross-doc validator behavior (S1-S4) is tests/test_spine.py;
# this covers only that ProjectConfig.load reads (or defaults) them.
# =========================================================================

def test_spine_keys_default_to_none_when_unset(tmp_path):
    root = _write_project(tmp_path)
    cfg = ProjectConfig.load(root)

    assert cfg.north_star is None
    assert cfg.product_definition is None
    assert cfg.roadmap is None
    assert cfg.backlog is None


def test_spine_keys_load_when_set(tmp_path):
    # _write_project's template only exposes one templated slot (under
    # [notify]) and TOML forbids redeclaring [project] -- write the toml
    # directly here instead of routing the new keys through that helper.
    trove = tmp_path / "nyxloom-trove"
    trove.mkdir(parents=True)
    (trove / "nyxloom.toml").write_text(
        '[project]\n'
        'id = "spineproj"\n'
        'default_branch = "main"\n'
        'handoff_globs = ["handoff/*.md"]\n'
        'north_star = "nyxloom-trove/1-north-star.md"\n'
        'product_definition = "nyxloom-trove/2-product-definition.md"\n'
        'roadmap = "nyxloom-trove/3-roadmap.md"\n'
        'backlog = "nyxloom-trove/4-backlog.md"\n',
        encoding="utf-8",
    )

    cfg = ProjectConfig.load(tmp_path)

    assert cfg.north_star == "nyxloom-trove/1-north-star.md"
    assert cfg.product_definition == "nyxloom-trove/2-product-definition.md"
    assert cfg.roadmap == "nyxloom-trove/3-roadmap.md"
    assert cfg.backlog == "nyxloom-trove/4-backlog.md"
    assert cfg.notify.ntfy_url is None


# ==========================================================================
# http_bind is INFRA-sourced (NYXLOOM_HTTP_BIND), never a toml [policy] key
# (2026-07-20). Mirrors the NTFY_URL env-authority pattern above: a per-target
# deployment fact the shared, bind-mounted toml structurally cannot carry --
# and here also a security boundary (the bind guards an unauthenticated control
# plane), so a hand-edited toml must never be able to widen it.
# ==========================================================================

_POLICY_TOML = """\
[project]
id = "bindproj"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]

[policy]
{policy_lines}

[notify]
"""


def _write_policy_project(root: Path, policy_lines: str = "") -> Path:
    trove = root / "nyxloom-trove"
    trove.mkdir(parents=True, exist_ok=True)
    (trove / "nyxloom.toml").write_text(
        _POLICY_TOML.format(policy_lines=policy_lines), encoding="utf-8")
    return root


def test_http_bind_defaults_to_loopback_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "127.0.0.1"


def test_http_bind_env_is_the_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "0.0.0.0"


def test_toml_http_bind_is_ignored_THE_DISCRIMINATOR(tmp_path, monkeypatch):
    """The whole point: a hand-edited toml http_bind must NOT reach the running
    bind, even with no env set. If toml were still a source, this would be
    "0.0.0.0" and a shared, host-mounted toml could silently expose the
    unauthenticated control plane on the host's LAN. It must stay loopback."""
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    root = _write_policy_project(tmp_path, policy_lines='http_bind = "0.0.0.0"')
    cfg = ProjectConfig.load(root)  # must still LOAD, not raise
    assert cfg.policy.http_bind == "127.0.0.1"


def test_env_wins_over_toml_http_bind_toml_is_not_even_a_fallback(tmp_path, monkeypatch):
    """Paired with the discriminator: toml is not a source AT ALL, not merely a
    lower-priority one. With both set, the env value wins and the toml value
    ("0.0.0.0") never appears -- so toml can neither widen NOR narrow the bind."""
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "10.1.2.3")
    root = _write_policy_project(tmp_path, policy_lines='http_bind = "0.0.0.0"')
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "10.1.2.3"


def test_empty_http_bind_env_does_not_shadow_the_default(tmp_path, monkeypatch):
    """Mirrors test_empty_ntfy_url_env_does_not_shadow_toml: an empty env var
    is falsy, so the safe loopback default stands rather than an empty bind."""
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "")
    root = _write_policy_project(tmp_path)
    cfg = ProjectConfig.load(root)
    assert cfg.policy.http_bind == "127.0.0.1"


# ==========================================================================
# PACKAGE P05c (docs/plan-logging.md, logging sweep): config.py oracles.
# ==========================================================================

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    import json
    return [json.loads(ln) for ln in lines]


class TestConfigLoadLogging:
    """Oracle 2: a config load/resolve logs a DEBUG record."""

    def test_config_load_logs_debug_on_resolve(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NTFY_URL", raising=False)
        log.configure(level=log.DEBUG, log_dir=tmp_path, console=False)
        root = _write_project(tmp_path)

        ProjectConfig.load(root)

        records = _read_jsonl(tmp_path / "nyxloom.jsonl")
        resolved = [r for r in records if r.get("msg") == "config resolved"]
        assert len(resolved) == 1
        assert resolved[0]["level"] == "debug"
        assert resolved[0]["project_id"] == "p39proj"

    def test_config_load_never_logs_secret_values(self, tmp_path, monkeypatch):
        """Oracle 3: a token/secret VALUE reachable from config load never
        appears in any emitted record. config.py only ever reads/logs the
        env var NAME a secret lives under (NotifyConfig.token_env) -- the
        value itself is never parsed out of the environment by config.py,
        so planting one in NTFY_TOKEN and asserting it never appears in the
        JSONL output pins that invariant directly."""
        secret = "sk-should-never-appear-in-any-log-record-000111"
        monkeypatch.setenv("NTFY_TOKEN", secret)
        monkeypatch.delenv("NTFY_URL", raising=False)
        log.configure(level=log.DEBUG, log_dir=tmp_path, console=False)
        root = _write_project(tmp_path, 'ntfy_url = "https://example.test"\n')

        ProjectConfig.load(root)

        raw = (tmp_path / "nyxloom.jsonl").read_text(encoding="utf-8")
        assert secret not in raw
        # ... while the env-var NAME (never the value) is expected to appear.
        records = _read_jsonl(tmp_path / "nyxloom.jsonl")
        resolved = [r for r in records if r.get("msg") == "config resolved"]
        assert resolved[0]["token_env"] == "NTFY_TOKEN"


class TestUpdateProjectPolicy:
    """update_project_policy: a surgical single-line [policy] editor
    (P15). Direct unit coverage -- previously exercised only indirectly
    (and incompletely) via the P15 UI HTTP surface in test_config_ui.py,
    which pre-validates keys/tiers before ever reaching this function's own
    not-found branch."""

    def _write(self, root: Path, policy_body: str) -> None:
        trove = root / "nyxloom-trove"
        trove.mkdir(parents=True, exist_ok=True)
        (trove / "nyxloom.toml").write_text(
            '[project]\nid = "polproj"\ndefault_branch = "main"\n'
            'handoff_globs = ["handoff/*.md"]\n\n'
            f'[policy]\n{policy_body}\n[notify]\n',
            encoding="utf-8",
        )

    def test_update_existing_key_rewrites_value(self, tmp_path):
        from nyxloom.config import update_project_policy

        self._write(tmp_path, "max_active_tasks = 2\n")
        update_project_policy(tmp_path, {"max_active_tasks": 9})

        cfg = ProjectConfig.load(tmp_path)
        assert cfg.policy.max_active_tasks == 9

    def test_update_missing_key_raises_no_write(self, tmp_path):
        from nyxloom.config import update_project_policy

        self._write(tmp_path, "max_active_tasks = 2\n")
        before = (tmp_path / "nyxloom-trove" / "nyxloom.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="not found"):
            update_project_policy(tmp_path, {"ready_queue_target": 9})

        after = (tmp_path / "nyxloom-trove" / "nyxloom.toml").read_text(encoding="utf-8")
        assert after == before


class TestUpdateRoutes:
    """update_routes: a surgical `routes = [...]` line editor per tier."""

    def test_update_existing_tier_rewrites_routes_line(self, tmp_state):
        from nyxloom import paths
        from nyxloom.config import update_routes

        paths.routes_path().write_text(
            'revision = "r1"\n\n[tiers.flash-high]\nroutes = ["a"]\n',
            encoding="utf-8",
        )

        update_routes({"flash-high": ["a", "b"]})

        text = paths.routes_path().read_text(encoding="utf-8")
        assert 'routes = ["a", "b"]' in text

    def test_update_missing_tier_raises_no_write(self, tmp_state):
        from nyxloom import paths
        from nyxloom.config import update_routes

        original = 'revision = "r1"\n\n[tiers.flash-high]\nroutes = ["a"]\n'
        paths.routes_path().write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="not found"):
            update_routes({"no-such-tier": ["a"]})

        assert paths.routes_path().read_text(encoding="utf-8") == original


class TestRoutesForTierDanglingRoute:
    """CR-08: a tier listing a route id `[routes.*]` no longer declares must
    refuse the dangling id, not KeyError the whole caller -- the live-path
    half of the fix route_doctor's `tier-dangling-route` finding already
    diagnosed offline (tests/test_route_doctor.py)."""

    def test_for_tier_drops_the_dangling_id_and_keeps_the_rest(self, tmp_path):
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.implement-1]\n'
            'routes = ["claude-haiku", "ghost", "claude-sonnet"]\n\n'
            '[routes.claude-haiku]\n'
            'cli = "claude"\n'
            'model = "haiku"\n\n'
            '[routes.claude-sonnet]\n'
            'cli = "claude"\n'
            'model = "sonnet"\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        result = routes.for_tier("implement-1")
        assert [r.route_id for r in result] == ["claude-haiku", "claude-sonnet"]

    def test_for_tier_on_an_entirely_dangling_tier_returns_empty_not_raises(
            self, tmp_path):
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.implement-1]\n'
            'routes = ["ghost"]\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        assert routes.for_tier("implement-1") == []

    def test_for_tier_and_for_role_never_log(self):
        """D-L5 (docs/plan-logging.md): reconcile.plan_project is PURE -- no
        clock, no I/O, no logger import -- and Routes.for_tier/for_role are
        called from BOTH that pure core (reconcile.dispatch_eligible,
        planning.PlanContext.derive) AND impure effect-boundary code, so a
        log call inside either method would make every pure caller impure
        too. The AST-based purity oracle (test_planning.py's
        test_no_rule_can_reach_an_impure_capability) cannot catch this: it
        does not resolve method calls on instance attributes like
        `inp.routes.for_tier(...)`, only module-level/module-alias calls --
        confirmed a real blind spot by independent review, not a
        hypothetical. This test closes it directly: no `log.` attribute
        access anywhere in either method's own body."""
        from nyxloom.config import Routes

        for name in ("for_tier", "for_role"):
            tree = ast.parse(textwrap.dedent(
                inspect.getsource(getattr(Routes, name))))
            log_calls = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Attribute)
                         and isinstance(node.value, ast.Name)
                         and node.value.id == "log"]
            assert not log_calls, f"Routes.{name} must not call the logger"

    def test_a_dangling_id_produces_no_log_output(self, tmp_path):
        """Behavioural partner of the structural test above: prove it, not
        just declare it. A real dangling-id lookup through the real
        structlog pipeline emits nothing."""
        import structlog
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.implement-1]\n'
            'routes = ["ghost"]\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        captured: list = []
        old_config = structlog.get_config()
        try:
            structlog.configure(processors=[
                lambda logger, name, event_dict: captured.append(event_dict)
                or event_dict])
            assert routes.for_tier("implement-1") == []
        finally:
            structlog.configure(**old_config)
        assert captured == []

    def test_for_role_inherits_the_refusal_via_its_tier_fallback(self, tmp_path):
        """for_role's tier-named-after-role fallback calls for_tier directly
        -- confirm the refusal propagates through that path too, not just
        the explicit for_tier call."""
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.frontier-review]\n'
            'routes = ["ghost", "fake-review"]\n\n'
            '[routes.fake-review]\n'
            'cli = "claude"\n'
            'model = "opus"\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        matches = routes.for_role("review-independent")
        assert [r.route_id for r in matches] == ["fake-review"]


class TestRoutesForRole:
    """Routes.for_role: the B16/D-R1 decoupling of call sites from tier
    names -- prefers route(s) carrying a matching RouteDef.role_default, and
    falls back to a tier named after the role (back-compat with tier-named
    configs, so the migration is non-breaking)."""

    def test_for_role_returns_the_route_default_match(self, tmp_path):
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.review-3]\n'
            'routes = ["claude-opus-high"]\n\n'
            '[routes.claude-opus-high]\n'
            'cli = "claude"\n'
            'model = "opus"\n'
            'role_default = "review-independent"\n\n'
            '[routes.claude-haiku]\n'
            'cli = "claude"\n'
            'model = "haiku"\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        matches = routes.for_role("review-independent")
        assert [r.route_id for r in matches] == ["claude-opus-high"]

    def test_for_role_returns_empty_when_no_route_defaults_to_it(self, tmp_path):
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.implement-1]\n'
            'routes = ["claude-haiku"]\n\n'
            '[routes.claude-haiku]\n'
            'cli = "claude"\n'
            'model = "haiku"\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        assert routes.for_role("review-independent") == []
        assert routes.for_role("nonexistent") == []

    def test_for_role_falls_back_to_tier_named_after_role(self, tmp_path):
        """Back-compat: with no role_default anywhere but a tier whose name
        equals the old role value, for_role resolves via that tier (so pre-migration
        tier-named configs keep working, e.g. the many test fixtures that
        declare [tiers.frontier-review])."""
        from nyxloom.config import Routes

        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "test"\n\n'
            '[tiers.frontier-review]\n'
            'routes = ["fake-review"]\n\n'
            '[routes.fake-review]\n'
            'cli = "claude"\n'
            'model = "opus"\n',
            encoding="utf-8",
        )
        routes = Routes.load(path=p)

        matches = routes.for_role("review-independent")
        assert [r.route_id for r in matches] == ["fake-review"]


class TestHealthyRoutes:
    """CR-08 Slice 2a: the ONE place "is a route healthy" is decided, in
    place of four independently hand-written copies of the same
    `provider_ok.get(route_id, False)` filter."""

    def test_filters_to_healthy_candidates_preserving_order(self):
        from nyxloom.config import RouteDef, healthy_routes

        a = RouteDef(route_id="a", cli="claude", model="haiku")
        b = RouteDef(route_id="b", cli="claude", model="sonnet")
        c = RouteDef(route_id="c", cli="claude", model="opus")
        provider_ok = {"a": True, "b": False, "c": True}

        assert healthy_routes([a, b, c], provider_ok) == [a, c]

    def test_a_route_absent_from_provider_ok_is_treated_as_unhealthy(self):
        """`.get(route_id, False)`'s default -- a route provider_ok never
        probed (not `False`, simply MISSING) must not be treated as healthy
        by omission."""
        from nyxloom.config import RouteDef, healthy_routes

        a = RouteDef(route_id="a", cli="claude", model="haiku")
        assert healthy_routes([a], {}) == []

    def test_no_healthy_candidates_returns_empty_not_none(self):
        from nyxloom.config import RouteDef, healthy_routes

        a = RouteDef(route_id="a", cli="claude", model="haiku")
        assert healthy_routes([a], {"a": False}) == []

    def test_empty_candidates_returns_empty(self):
        from nyxloom.config import healthy_routes

        assert healthy_routes([], {"anything": True}) == []


class TestPricesLoad:
    """Prices.load: absent-file vs. present-file resolution (§5 config
    rubric: "a config load/resolve -> DEBUG")."""

    def test_absent_file_returns_empty(self, tmp_path):
        prices = Prices.load(path=tmp_path / "does-not-exist.toml")
        assert prices.revision == "absent"
        assert prices.models == {}

    def test_present_file_parses_models(self, tmp_path):
        p = tmp_path / "prices.toml"
        p.write_text(
            'revision = "2026-07"\n\n[models.demo-model]\ninput = 1.0\noutput = 2.0\n',
            encoding="utf-8",
        )
        prices = Prices.load(path=p)
        assert prices.revision == "2026-07"
        assert "demo-model" in prices.models


# =========================================================================
# F018 P1: carve stage config (plan-long-running-carver.md §9)
# =========================================================================

class TestCarveStageConfigDefaults:
    """CarveStageConfig has safe defaults that mean 'feature off'."""

    def test_defaults_are_fresh_session(self):
        cfg = CarveStageConfig()
        assert cfg.session == "fresh"
        assert cfg.compact_context_ratio == 0.70
        assert cfg.compact_after_turns == 24
        assert cfg.compact_hard_after_turns == 32
        assert cfg.retain_merge_digests == 10
        assert cfg.max_resume_failures == 2
        assert cfg.max_proposal_repairs == 2

    def test_custom_session(self):
        cfg = CarveStageConfig(session="project-persistent")
        assert cfg.session == "project-persistent"

    def test_compat_context_ratio_bounds(self):
        CarveStageConfig(compact_context_ratio=0.0)
        CarveStageConfig(compact_context_ratio=1.0)

    def test_zero_compact_after_turns_disabled(self):
        cfg = CarveStageConfig(compact_after_turns=0)
        assert cfg.compact_after_turns == 0


class TestProjectConfigCarveDefaults:
    """A project TOML with no [stage.carve] loads clean and carve is off."""

    def _write_minimal(self, root: Path) -> Path:
        trove = root / "nyxloom-trove"
        trove.mkdir(parents=True, exist_ok=True)
        (trove / "nyxloom.toml").write_text(
            '[project]\n'
            'id = "carvecfg"\n'
            'default_branch = "main"\n'
            'worktree_root = ".worktrees"\n'
            'handoff_globs = ["handoff/*.md"]\n',
            encoding="utf-8",
        )
        return root

    def test_default_carve_is_fresh(self, tmp_path):
        root = self._write_minimal(tmp_path)
        cfg = ProjectConfig.load(root)
        assert cfg.carve.session == "fresh"

    def test_carve_keys_loaded_from_toml(self, tmp_path):
        root = tmp_path
        trove = root / "nyxloom-trove"
        trove.mkdir(parents=True, exist_ok=True)
        (trove / "nyxloom.toml").write_text(
            '[project]\n'
            'id = "carvecfg"\n'
            'default_branch = "main"\n'
            'worktree_root = ".worktrees"\n'
            'handoff_globs = ["handoff/*.md"]\n'
            '\n'
            '[stage.carve]\n'
            'session = "project-persistent"\n'
            'compact_context_ratio = 0.50\n'
            'compact_after_turns = 12\n'
            'compact_hard_after_turns = 20\n'
            'retain_merge_digests = 5\n'
            'max_resume_failures = 3\n'
            'max_proposal_repairs = 1\n',
            encoding="utf-8",
        )
        cfg = ProjectConfig.load(root)
        assert cfg.carve.session == "project-persistent"
        assert cfg.carve.compact_context_ratio == 0.50
        assert cfg.carve.compact_after_turns == 12
        assert cfg.carve.compact_hard_after_turns == 20
        assert cfg.carve.retain_merge_digests == 5
        assert cfg.carve.max_resume_failures == 3
        assert cfg.carve.max_proposal_repairs == 1

    def test_no_stage_section_keeps_defaults(self, tmp_path):
        root = self._write_minimal(tmp_path)
        cfg = ProjectConfig.load(root)
        assert cfg.carve.session == "fresh"
        assert cfg.carve.compact_context_ratio == 0.70

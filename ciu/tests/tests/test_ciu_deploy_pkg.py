"""
Tests for src/ciu/deploy_pkg/ — CIU v3 orchestration helpers.

Spec IDs covered:
  S7.1   phase naming + numeric sort
  S7.2   service_enabled flag semantics
  S7.4   profile table
  S7.5   CIU_SERVICES_PROFILE (Seam 4), CIU_HOST_PROFILE retired, groups rejection
  S7.5a  topology_overrides deep-merge (host-B case)
  Seam4  multi-profile union/dedup/conflict rules
  S7.7   health gate (classify + evaluate_gate + wait_for_gate)
  S7.8   anchored_name_filter
  S7.9   check_registry_auth (config.json parsing)

All filesystem fixtures use tmp_path; env vars use monkeypatch.
No subprocess, no network in this module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import (  # noqa: E402
    PHASE_KEY_RE,
    Profile,
    anchored_name_filter,
    check_registry_auth,
    classify,
    dedupe_keep_order,
    evaluate_gate,
    http_get_json,
    iter_enabled_services,
    ordered_phases,
    parse_env_overrides,
    reject_groups,
    resolve_profile,
    resolve_profiles,
    service_enabled,
    service_health_enabled,
    wait_for_gate,
)
from ciu.deploy_pkg.health import _parse_cmd_shell_tools, probe_image_tools  # noqa: E402


class TestServiceHealthEnabled:
    def test_absent_defaults_true(self):
        assert service_health_enabled({"path": "infra/api"}) is True

    def test_false_excludes_one_shot(self):
        assert service_health_enabled({"health": False}) is False

    @pytest.mark.parametrize("bad", ["false", 0, [], {"enabled": False}])
    def test_non_bool_aborts(self, bad):
        with pytest.raises(ValueError, match=r"\[S7.2\].*health.*bool"):
            service_health_enabled({"health": bad})

    def test_false_still_participates_in_deployment_selection(self):
        configured = {
            "phase_1": {
                "services": [
                    {"path": "jobs/schema-init", "health": False, "enabled": True}
                ]
            }
        }
        selected = list(iter_enabled_services(configured, control={}))
        assert [service["path"] for _, _, service in selected] == ["jobs/schema-init"]

    def test_invalid_value_fails_during_general_selection(self):
        configured = {
            "phase_1": {
                "services": [
                    {"path": "jobs/schema-init", "health": "false", "enabled": True}
                ]
            }
        }
        with pytest.raises(ValueError, match=r"\[S7.2\].*health.*bool"):
            list(iter_enabled_services(configured, control={}))


# ===========================================================================
# health._parse_cmd_shell_tools — image healthcheck preflight
# ===========================================================================

@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("wget -qO- http://127.0.0.1:8080/health || exit 1", ["wget"]),
        ("curl -fsS http://localhost/health | grep -q ok", ["curl", "grep"]),
        (
            "python3 -c \"import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:9000/health').read()\"",
            ["python3"],
        ),
        ("if wget -qO- http://localhost; then exit 0; else exit 1; fi", ["wget"]),
        ("exec /usr/local/bin/otelcol-contrib --config=/etc/otel.yaml", ["otelcol-contrib"]),
    ],
)
def test_healthcheck_tool_parser_reports_only_executables(command, expected):
    assert _parse_cmd_shell_tools(command) == expected


def test_healthcheck_probe_accepts_distroless_declared_entrypoint(monkeypatch):
    import subprocess
    import ciu.deploy_pkg.health as health

    def fake_run(argv, **kwargs):
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout='{"Entrypoint":["/otelcol-contrib"],"Cmd":["--config","/etc/otel.yaml"]}',
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 127, stdout=b"", stderr=b"")

    monkeypatch.setattr(health._subprocess, "run", fake_run)
    assert probe_image_tools("example/distroless", ["otelcol-contrib", "wget"]) == {
        "otelcol-contrib": True,
        "wget": False,
    }


# ===========================================================================
# http_util.http_get_json
# ===========================================================================

class TestHttpGetJson:
    """Unit-test the urllib wrapper with monkeypatching (no real network)."""

    def _mock_urlopen(self, monkeypatch, body: bytes, status: int = 200):
        """Patch urllib.request.urlopen to return a fake response."""
        import io
        import ciu.deploy_pkg.http_util as mod

        class _FakeResp:
            def __init__(self):
                self._body = body
                self.status = status

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: _FakeResp())

    def test_success_returns_parsed_dict(self, monkeypatch):
        self._mock_urlopen(monkeypatch, b'{"status": "ok", "version": "1.2"}')
        ok, data = http_get_json("http://localhost/health")
        assert ok is True
        assert data["status"] == "ok"

    def test_non_json_returns_false(self, monkeypatch):
        self._mock_urlopen(monkeypatch, b"not json at all")
        ok, msg = http_get_json("http://localhost/health")
        assert ok is False
        assert "Non-JSON" in msg

    def test_http_error_returns_false(self, monkeypatch):
        import urllib.error
        import ciu.deploy_pkg.http_util as mod

        def _raise(*a, **kw):
            raise urllib.error.HTTPError(
                url="http://x", code=503, msg="Service Unavailable",
                hdrs=None, fp=None
            )

        monkeypatch.setattr(mod.urllib.request, "urlopen", _raise)
        ok, msg = http_get_json("http://localhost/health")
        assert ok is False
        assert "503" in msg

    def test_url_error_returns_false(self, monkeypatch):
        import urllib.error
        import ciu.deploy_pkg.http_util as mod

        def _raise(*a, **kw):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(mod.urllib.request, "urlopen", _raise)
        ok, msg = http_get_json("http://localhost/health")
        assert ok is False
        assert "URLError" in msg

    def test_timeout_returns_false(self, monkeypatch):
        import ciu.deploy_pkg.http_util as mod

        def _raise(*a, **kw):
            raise TimeoutError("timed out")

        monkeypatch.setattr(mod.urllib.request, "urlopen", _raise)
        ok, msg = http_get_json("http://localhost/health", timeout=1)
        assert ok is False
        assert "Timeout" in msg


# ===========================================================================
# phases.PHASE_KEY_RE
# ===========================================================================

class TestPhaseKeyRe:
    def test_matches_phase_1(self):
        assert PHASE_KEY_RE.match("phase_1")

    def test_matches_phase_10(self):
        assert PHASE_KEY_RE.match("phase_10")

    def test_does_not_match_bare_int(self):
        assert PHASE_KEY_RE.match("1") is None

    def test_does_not_match_phase_without_underscore(self):
        assert PHASE_KEY_RE.match("phase1") is None

    def test_does_not_match_arbitrary_key(self):
        assert PHASE_KEY_RE.match("group_1") is None


# ===========================================================================
# phases.ordered_phases — S7.1
# ===========================================================================

class TestOrderedPhases:
    def test_numeric_order_phase_2_before_phase_10(self):
        """S7.1: sort is numeric, so phase_2 < phase_10 (fixes v1 lex sort)."""
        cfg = {
            "phase_10": {"services": []},
            "phase_2": {"services": []},
            "phase_1": {"services": []},
        }
        result = ordered_phases(cfg)
        nums = [t[0] for t in result]
        assert nums == [1, 2, 10]
        assert result[1][1] == "phase_2"
        assert result[2][1] == "phase_10"

    def test_returns_tuples_with_correct_shape(self):
        cfg = {"phase_3": {"name": "three", "services": []}}
        result = ordered_phases(cfg)
        assert len(result) == 1
        num, key, data = result[0]
        assert num == 3
        assert key == "phase_3"
        assert data == {"name": "three", "services": []}

    def test_bad_phase_key_raises_s7_1(self):
        """S7.1: non-phase_ key → ValueError naming [S7.1]."""
        cfg = {"group_1": {}}
        with pytest.raises(ValueError, match=r"\[S7\.1\]"):
            ordered_phases(cfg)

    def test_bad_phase_key_names_offending_key(self):
        cfg = {"bad_key": {}}
        with pytest.raises(ValueError, match="bad_key"):
            ordered_phases(cfg)

    def test_int_key_raises_s7_1(self):
        """S7.1: non-string keys (e.g. integer) must raise [S7.1] (A8 fix)."""
        cfg = {1: {}}
        with pytest.raises(ValueError, match=r"\[S7\.1\]"):
            ordered_phases(cfg)

    def test_empty_config_returns_empty_list(self):
        assert ordered_phases({}) == []

    def test_required_format_mentioned_in_error(self):
        """Error message must mention the required format."""
        cfg = {"phase_x": {}}
        with pytest.raises(ValueError, match="phase_<uint>|phase_1"):
            ordered_phases(cfg)


# ===========================================================================
# phases.service_enabled — S7.2
# ===========================================================================

class TestServiceEnabled:
    def test_absent_defaults_to_true(self):
        assert service_enabled({}, {}) is True

    def test_bool_true(self):
        assert service_enabled({"enabled": True}, {}) is True

    def test_bool_false(self):
        assert service_enabled({"enabled": False}, {}) is False

    def test_flag_name_true(self):
        control = {"deploy_redis": True}
        assert service_enabled({"enabled": "deploy_redis"}, control) is True

    def test_flag_name_false(self):
        control = {"deploy_redis": False}
        assert service_enabled({"enabled": "deploy_redis"}, control) is False

    def test_unknown_flag_raises_s7_2(self):
        """S7.2: unknown flag name → ValueError [S7.2]."""
        with pytest.raises(ValueError, match=r"\[S7\.2\]"):
            service_enabled({"enabled": "unknown_flag"}, {})

    def test_unknown_flag_names_the_flag(self):
        with pytest.raises(ValueError, match="missing_flag"):
            service_enabled({"enabled": "missing_flag"}, {"other": True})

    def test_non_bool_control_value_raises_s7_2(self):
        """S7.2: control flag with non-bool value → ValueError [S7.2]."""
        control = {"my_flag": "yes"}  # string, not bool
        with pytest.raises(ValueError, match=r"\[S7\.2\]"):
            service_enabled({"enabled": "my_flag"}, control)

    def test_int_enabled_raises_s7_2(self):
        """S7.2: int 'enabled' (expressions forbidden) → ValueError [S7.2]."""
        with pytest.raises(ValueError, match=r"\[S7\.2\]"):
            service_enabled({"enabled": 1}, {})

    def test_list_enabled_raises_s7_2(self):
        with pytest.raises(ValueError, match=r"\[S7\.2\]"):
            service_enabled({"enabled": [True]}, {})


# ===========================================================================
# phases.iter_enabled_services
# ===========================================================================

class TestIterEnabledServices:
    def _make_svc(self, path, name="svc", enabled=True):
        return {"path": path, "name": name, "enabled": enabled}

    def test_yields_enabled_services_in_numeric_order(self):
        phases = {
            "phase_10": {"services": [self._make_svc("infra/b")]},
            "phase_2": {"services": [self._make_svc("infra/a")]},
        }
        results = list(iter_enabled_services(phases, {}))
        paths = [r[2]["path"] for r in results]
        assert paths == ["infra/a", "infra/b"]

    def test_skips_disabled_services(self):
        phases = {
            "phase_1": {"services": [
                self._make_svc("infra/on", enabled=True),
                self._make_svc("infra/off", enabled=False),
            ]},
        }
        results = list(iter_enabled_services(phases, {}))
        paths = [r[2]["path"] for r in results]
        assert paths == ["infra/on"]

    def test_skips_services_with_empty_path(self):
        phases = {
            "phase_1": {"services": [
                {"path": "", "name": "no-path", "enabled": True},
                self._make_svc("infra/real"),
            ]},
        }
        results = list(iter_enabled_services(phases, {}))
        assert len(results) == 1
        assert results[0][2]["path"] == "infra/real"

    def test_skips_services_with_missing_path_key(self):
        phases = {
            "phase_1": {"services": [
                {"name": "no-path-key", "enabled": True},
                self._make_svc("infra/present"),
            ]},
        }
        results = list(iter_enabled_services(phases, {}))
        assert len(results) == 1

    def test_phase_filter_restricts_output(self):
        phases = {
            "phase_1": {"services": [self._make_svc("infra/a")]},
            "phase_2": {"services": [self._make_svc("infra/b")]},
        }
        results = list(iter_enabled_services(phases, {}, phase_filter={"phase_1"}))
        paths = [r[2]["path"] for r in results]
        assert paths == ["infra/a"]

    def test_control_flag_respected(self):
        control = {"include_worker": False}
        phases = {
            "phase_1": {"services": [
                {"path": "apps/worker", "name": "worker", "enabled": "include_worker"},
                self._make_svc("apps/api"),
            ]},
        }
        results = list(iter_enabled_services(phases, control))
        paths = [r[2]["path"] for r in results]
        assert paths == ["apps/api"]

    def test_yields_phase_num_and_key(self):
        phases = {"phase_5": {"services": [self._make_svc("apps/x")]}}
        results = list(iter_enabled_services(phases, {}))
        assert len(results) == 1
        phase_num, phase_key, svc = results[0]
        assert phase_num == 5
        assert phase_key == "phase_5"


# ===========================================================================
# phases.parse_env_overrides
# ===========================================================================

class TestParseEnvOverrides:
    def test_simple_key_value(self):
        assert parse_env_overrides(["FOO=bar"]) == {"FOO": "bar"}

    def test_value_with_equals_preserved(self):
        """'=' inside a value must not be split away."""
        result = parse_env_overrides(["URL=http://host:8080/path?a=1"])
        assert result["URL"] == "http://host:8080/path?a=1"

    def test_empty_value_allowed(self):
        assert parse_env_overrides(["EMPTY="]) == {"EMPTY": ""}

    def test_multiple_entries(self):
        result = parse_env_overrides(["A=1", "B=2"])
        assert result == {"A": "1", "B": "2"}

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="="):
            parse_env_overrides(["NO_EQUALS_HERE"])

    def test_empty_list_returns_empty_dict(self):
        assert parse_env_overrides([]) == {}


# ===========================================================================
# profiles.resolve_profile — S7.4, S7.5, S7.5a
# ===========================================================================

def _global_cfg(profiles=None, topology=None, groups=None):
    """Build a minimal global config dict for profile tests."""
    deploy: dict = {}
    if profiles is not None:
        deploy["profiles"] = profiles
    if groups is not None:
        deploy["groups"] = groups
    cfg: dict = {"deploy": deploy}
    if topology is not None:
        cfg["topology"] = topology
    return cfg


class TestResolveProfile:
    """Tests for the single-name shim (resolve_profile delegates to resolve_profiles)."""

    def test_no_name_no_env_returns_default_profile(self, monkeypatch):
        """No name, no CIU_SERVICES_PROFILE → default Profile (all phases)."""
        monkeypatch.delenv("CIU_HOST_PROFILE", raising=False)
        cfg = _global_cfg()
        p = resolve_profile(cfg, None, env={})
        assert p.name is None
        assert p.phase_keys is None
        assert p.extra_stacks == []

    def test_explicit_name_resolves_profile(self):
        profiles = {
            "explicit": {"phases": ["phase_2"]},
        }
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "explicit", env={})
        assert p.name == "explicit"
        assert p.phase_keys == {"phase_2"}

    def test_unknown_profile_raises_listing_available(self):
        profiles = {"alpha": {}, "beta": {}}
        cfg = _global_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="alpha|beta"):
            resolve_profile(cfg, "gamma", env={})

    def test_unknown_profile_message_names_requested(self):
        profiles = {"alpha": {}}
        cfg = _global_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="gamma"):
            resolve_profile(cfg, "gamma", env={})

    def test_profile_phase_keys_resolved(self):
        profiles = {"worker": {"phases": ["phase_1", "phase_3"]}}
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "worker", env={})
        assert p.phase_keys == {"phase_1", "phase_3"}

    def test_profile_stacks_resolved(self):
        profiles = {"infra": {"stacks": ["infra/redis-core", "infra/postgres"]}}
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "infra", env={})
        assert p.extra_stacks == ["infra/redis-core", "infra/postgres"]

    def test_profile_compose_profiles(self):
        profiles = {"dev": {"compose_profiles": ["debug", "mock"]}}
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "dev", env={})
        assert p.compose_profiles == ["debug", "mock"]

    def test_profile_env_overrides(self):
        profiles = {"staging": {"env_overrides": {"LOG_LEVEL": "DEBUG"}}}
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "staging", env={})
        assert p.env_overrides == {"LOG_LEVEL": "DEBUG"}

    def test_invalid_phase_key_in_profile_raises_s7_1(self):
        """Phase key in profile must match phase_<uint>; invalid → [S7.1]."""
        profiles = {"bad": {"phases": ["not_a_phase"]}}
        cfg = _global_cfg(profiles=profiles)
        with pytest.raises(ValueError, match=r"\[S7\.1\]"):
            resolve_profile(cfg, "bad", env={})

    def test_topology_overrides_deep_merged_s7_5a(self):
        """S7.5a: topology_overrides deep-merges into config['topology'].

        Scenario: host B's profile points vault at host A's external address.
        The original internal_host must be replaced by the override value.
        """
        original_topology = {
            "services": {
                "vault": {
                    "internal_host": "vault.internal",
                    "port": 8200,
                }
            }
        }
        profiles = {
            "host_b": {
                "topology_overrides": {
                    "services": {
                        "vault": {
                            "internal_host": "192.0.2.1",  # host A's external IP
                        }
                    }
                }
            }
        }
        cfg = _global_cfg(profiles=profiles, topology=original_topology)
        p = resolve_profile(cfg, "host_b", env={})

        vault_cfg = p.config["topology"]["services"]["vault"]
        # internal_host swapped to external address
        assert vault_cfg["internal_host"] == "192.0.2.1"
        # port preserved (deep merge, not replace)
        assert vault_cfg["port"] == 8200

    def test_topology_overrides_do_not_mutate_input(self):
        """Input global_cfg must never be mutated (deep copy required)."""
        original_topology = {"services": {"vault": {"internal_host": "original"}}}
        profiles = {
            "mutate_test": {
                "topology_overrides": {"services": {"vault": {"internal_host": "changed"}}}
            }
        }
        cfg = _global_cfg(profiles=profiles, topology=original_topology)
        resolve_profile(cfg, "mutate_test", env={})

        # Original config untouched
        assert cfg["topology"]["services"]["vault"]["internal_host"] == "original"

    def test_empty_profile_returns_all_phases(self):
        """Profile without 'phases' key → phase_keys is None (all phases)."""
        profiles = {"minimal": {}}
        cfg = _global_cfg(profiles=profiles)
        p = resolve_profile(cfg, "minimal", env={})
        assert p.phase_keys is None

    def test_config_is_set_on_default_profile(self):
        """Default profile (no name) should carry the original config."""
        cfg = _global_cfg(topology={"services": {}})
        p = resolve_profile(cfg, None, env={})
        assert p.config is cfg  # no copy needed for default


# ===========================================================================
# profiles.reject_groups — S7.5
# ===========================================================================

class TestRejectGroups:
    def test_groups_present_raises_s7_5(self):
        """[deploy.groups] present → ValueError [S7.5]."""
        cfg = _global_cfg(groups={"infra": ["phase_1"]})
        with pytest.raises(ValueError, match=r"\[S7\.5\]"):
            reject_groups(cfg)

    def test_groups_message_mentions_profiles(self):
        cfg = _global_cfg(groups={"infra": ["phase_1"]})
        with pytest.raises(ValueError, match="profiles"):
            reject_groups(cfg)

    def test_no_groups_passes_silently(self):
        cfg = _global_cfg(profiles={"host_a": {}})
        reject_groups(cfg)  # must not raise

    def test_no_deploy_key_passes_silently(self):
        reject_groups({})  # must not raise


# ===========================================================================
# health.classify — S7.7
# ===========================================================================

class TestClassify:
    def test_none_returns_not_found(self):
        assert classify(None) == "not-found"

    def test_healthy(self):
        state = {"Health": {"Status": "healthy"}}
        assert classify(state) == "healthy"

    def test_unhealthy(self):
        state = {"Health": {"Status": "unhealthy"}}
        assert classify(state) == "unhealthy"

    def test_starting(self):
        """'starting' must return 'starting' (S7.7: pending, not passed)."""
        state = {"Health": {"Status": "starting"}}
        assert classify(state) == "starting"

    def test_no_health_key_returns_no_healthcheck(self):
        """Container without Health key → no-healthcheck (S7.7 warning)."""
        state = {"Status": "running"}  # no 'Health' key
        assert classify(state) == "no-healthcheck"

    def test_unknown_status_returns_unhealthy(self):
        state = {"Health": {"Status": "weird_status"}}
        assert classify(state) == "unhealthy"


# ===========================================================================
# health.evaluate_gate — S7.7
# ===========================================================================

class TestEvaluateGate:
    def test_all_healthy_passes(self):
        passed, summary = evaluate_gate({"a": "healthy", "b": "healthy"})
        assert passed is True
        assert summary["healthy"] == ["a", "b"]

    def test_no_healthcheck_does_not_fail_gate(self):
        passed, summary = evaluate_gate({"a": "healthy", "b": "no-healthcheck"})
        assert passed is True
        assert "b" in summary["no_healthcheck"]

    def test_starting_fails_gate(self):
        """S7.7: 'starting' goes to pending and FAILS the gate."""
        passed, summary = evaluate_gate({"a": "starting"})
        assert passed is False
        assert "a" in summary["pending"]

    def test_unhealthy_fails_gate(self):
        passed, summary = evaluate_gate({"a": "unhealthy"})
        assert passed is False
        assert "a" in summary["unhealthy"]

    def test_not_found_fails_gate(self):
        passed, summary = evaluate_gate({"a": "not-found"})
        assert passed is False
        assert "a" in summary["not_found"]

    def test_mixed_healthy_and_starting_fails(self):
        passed, summary = evaluate_gate({"a": "healthy", "b": "starting"})
        assert passed is False

    def test_empty_statuses_passes(self):
        passed, _ = evaluate_gate({})
        assert passed is True

    def test_all_no_healthcheck_passes(self):
        passed, summary = evaluate_gate({"a": "no-healthcheck", "b": "no-healthcheck"})
        assert passed is True

    def test_summary_has_all_buckets(self):
        _, summary = evaluate_gate({})
        for key in ("healthy", "pending", "unhealthy", "no_healthcheck", "not_found"):
            assert key in summary


# ===========================================================================
# health.wait_for_gate — S7.7 (fake clock + fake sleep)
# ===========================================================================

class TestWaitForGate:
    def test_passes_immediately_if_already_healthy(self):
        def check():
            return {"svc": "healthy"}

        passed, _ = wait_for_gate(
            check,
            timeout_s=10.0,
            sleep_fn=lambda s: None,
            clock=iter([0.0, 1.0]).__next__,
        )
        assert passed is True

    def test_passes_on_second_poll_with_fake_clock(self):
        """Fake clock advances; second call to check_fn returns healthy."""
        calls: list[int] = []

        def check():
            calls.append(1)
            if len(calls) < 2:
                return {"svc": "starting"}
            return {"svc": "healthy"}

        times = iter([0.0, 1.0, 2.0, 3.0])

        def fake_clock():
            return next(times)

        slept: list[float] = []

        passed, summary = wait_for_gate(
            check,
            timeout_s=10.0,
            interval_s=1.0,
            sleep_fn=slept.append,
            clock=fake_clock,
        )
        assert passed is True
        assert len(calls) == 2
        assert len(slept) == 1

    def test_returns_false_on_timeout(self):
        def check():
            return {"svc": "starting"}

        # clock always returns a value beyond timeout_s
        passed, summary = wait_for_gate(
            check,
            timeout_s=5.0,
            interval_s=1.0,
            sleep_fn=lambda s: None,
            clock=iter([0.0, 10.0]).__next__,
        )
        assert passed is False
        assert "svc" in summary["pending"]


# ===========================================================================
# health.anchored_name_filter — S7.8
# ===========================================================================

class TestAnchoredNameFilter:
    def test_format(self):
        result = anchored_name_filter("myproj", "prod", "redis")
        assert result == "^myproj-prod-redis$"

    def test_anchors_both_ends(self):
        result = anchored_name_filter("p", "e", "n")
        assert result.startswith("^")
        assert result.endswith("$")

    def test_components_joined_with_dashes(self):
        result = anchored_name_filter("project", "dev", "postgres")
        assert "-" in result
        assert "project" in result
        assert "dev" in result
        assert "postgres" in result


# ===========================================================================
# registry.check_registry_auth — S7.9
# ===========================================================================

class TestCheckRegistryAuth:
    def _write_config(self, tmp_path, data: dict) -> Path:
        p = tmp_path / "docker_config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_auths_entry_with_auth_returns_true(self, tmp_path):
        """auths[host].auth non-empty → True."""
        cfg = {"auths": {"registry.example.com": {"auth": "dXNlcjpwYXNz"}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("registry.example.com", p) is True

    def test_auths_entry_with_identitytoken_returns_true(self, tmp_path):
        cfg = {"auths": {"registry.example.com": {"identitytoken": "tok123"}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("registry.example.com", p) is True

    def test_auths_entry_empty_auth_returns_false(self, tmp_path):
        """auths[host] present but auth is empty → no credential stored."""
        cfg = {"auths": {"registry.example.com": {"auth": ""}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("registry.example.com", p) is False

    def test_cred_helpers_entry_returns_true(self, tmp_path):
        """credHelpers[host] present → True."""
        cfg = {"credHelpers": {"registry.example.com": "ecr-login"}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("registry.example.com", p) is True

    def test_global_creds_store_returns_true(self, tmp_path):
        """Global credsStore → True (catches keychain-backed setups)."""
        cfg = {"credsStore": "osxkeychain"}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("any.registry.io", p) is True

    def test_empty_config_returns_false(self, tmp_path):
        p = self._write_config(tmp_path, {})
        assert check_registry_auth("registry.example.com", p) is False

    def test_missing_config_file_returns_false(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        assert check_registry_auth("registry.example.com", missing) is False

    def test_strips_https_scheme(self, tmp_path):
        """URL with https:// scheme → host extracted correctly."""
        cfg = {"auths": {"registry.example.com": {"auth": "abc"}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("https://registry.example.com", p) is True

    def test_docker_io_shorthand_maps_to_index_key(self, tmp_path):
        """docker.io maps to the canonical https://index.docker.io/v1/ key."""
        cfg = {"auths": {"https://index.docker.io/v1/": {"auth": "abc"}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("docker.io", p) is True

    def test_invalid_json_returns_false(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{", encoding="utf-8")
        assert check_registry_auth("registry.example.com", bad) is False

    def test_url_with_path_stripped(self, tmp_path):
        """Host+path URL → only host used as key."""
        cfg = {"auths": {"registry.example.com": {"auth": "abc"}}}
        p = self._write_config(tmp_path, cfg)
        assert check_registry_auth("https://registry.example.com/v2/", p) is True


# ===========================================================================
# Seam 4 — resolve_profiles (multi-profile: §8 acceptance criteria)
# ===========================================================================

def _mp_cfg(profiles=None, topology=None):
    """Build a global config dict for multi-profile tests."""
    deploy: dict = {}
    if profiles is not None:
        deploy["profiles"] = profiles
    cfg: dict = {"deploy": deploy}
    if topology is not None:
        cfg["topology"] = topology
    return cfg


class TestDedupeKeepOrder:
    """dedupe_keep_order helper."""

    def test_removes_duplicates_preserving_first_seen(self):
        result = dedupe_keep_order(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_empty_list(self):
        assert dedupe_keep_order([]) == []

    def test_no_duplicates_unchanged(self):
        assert dedupe_keep_order(["x", "y", "z"]) == ["x", "y", "z"]

    def test_all_same(self):
        assert dedupe_keep_order(["a", "a", "a"]) == ["a"]


class TestResolveProfilesOrderedUnionDedup:
    """§8 AC#1: ordered union + dedup across profiles."""

    def _profiles(self):
        return {
            "core": {
                "phases": ["phase_1", "phase_2"],
                "compose_profiles": ["base"],
                "stacks": ["infra/s1", "infra/s2"],
            },
            "db": {
                "phases": ["phase_2"],         # dup phase_2
                "compose_profiles": ["base", "postgres"],  # dup "base"
                "stacks": ["infra/s2", "infra/s3"],        # dup "infra/s2"
            },
            "worker-io": {
                "phases": ["phase_4"],
                "compose_profiles": ["workers"],
                "stacks": ["infra/s4"],
            },
        }

    def test_phases_deduped_and_unioned(self):
        cfg = _mp_cfg(profiles=self._profiles())
        p = resolve_profiles(cfg, ["core", "db", "worker-io"])
        # phase_1, phase_2 (from core), phase_2 (dup from db, deduped), phase_4
        assert p.phase_keys == {"phase_1", "phase_2", "phase_4"}

    def test_compose_profiles_order_preserving_dedup(self):
        cfg = _mp_cfg(profiles=self._profiles())
        p = resolve_profiles(cfg, ["core", "db", "worker-io"])
        # core: ["base"], db: ["base"(dup), "postgres"], worker-io: ["workers"]
        assert p.compose_profiles == ["base", "postgres", "workers"]

    def test_stacks_order_preserving_dedup(self):
        cfg = _mp_cfg(profiles=self._profiles())
        p = resolve_profiles(cfg, ["core", "db", "worker-io"])
        # core: [s1, s2], db: [s2(dup), s3], worker-io: [s4]
        assert p.extra_stacks == ["infra/s1", "infra/s2", "infra/s3", "infra/s4"]

    def test_name_is_comma_joined(self):
        cfg = _mp_cfg(profiles=self._profiles())
        p = resolve_profiles(cfg, ["core", "db"])
        assert p.name == "core,db"


class TestResolveProfilesConflictRejection:
    """§8 AC#2: conflict rejection before any render."""

    def test_topology_conflict_raises_valueerror(self):
        profiles = {
            "conflict_a": {
                "topology_overrides": {"services": {"redis": {"internal_host": "host-a"}}}
            },
            "conflict_b": {
                "topology_overrides": {"services": {"redis": {"internal_host": "host-b"}}}
            },
        }
        cfg = _mp_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="Conflict"):
            resolve_profiles(cfg, ["conflict_a", "conflict_b"])

    def test_topology_conflict_message_names_key(self):
        profiles = {
            "pa": {"topology_overrides": {"services": {"redis": {"internal_host": "a"}}}},
            "pb": {"topology_overrides": {"services": {"redis": {"internal_host": "b"}}}},
        }
        cfg = _mp_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="internal_host"):
            resolve_profiles(cfg, ["pa", "pb"])

    def test_topology_conflict_message_names_both_profiles(self):
        profiles = {
            "pa": {"topology_overrides": {"services": {"redis": {"internal_host": "a"}}}},
            "pb": {"topology_overrides": {"services": {"redis": {"internal_host": "b"}}}},
        }
        cfg = _mp_cfg(profiles=profiles)
        with pytest.raises(ValueError) as exc_info:
            resolve_profiles(cfg, ["pa", "pb"])
        msg = str(exc_info.value)
        assert "pa" in msg and "pb" in msg

    def test_env_override_conflict_raises_valueerror(self):
        profiles = {
            "a": {"env_overrides": {"SHARED_KEY": "value-a"}},
            "b": {"env_overrides": {"SHARED_KEY": "value-b"}},
        }
        cfg = _mp_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="Conflict|SHARED_KEY"):
            resolve_profiles(cfg, ["a", "b"])


class TestResolveProfilesEqualRepeatedValues:
    """§8 AC#3: equal repeated values accepted silently."""

    def test_equal_topology_values_no_error(self):
        profiles = {
            "a": {"topology_overrides": {"services": {"vault": {"internal_host": "same"}}}},
            "b": {"topology_overrides": {"services": {"vault": {"internal_host": "same"}}}},
        }
        cfg = _mp_cfg(profiles=profiles)
        p = resolve_profiles(cfg, ["a", "b"])  # must not raise
        assert p.config["topology"]["services"]["vault"]["internal_host"] == "same"

    def test_equal_env_values_no_error(self):
        profiles = {
            "a": {"env_overrides": {"SHARED": "same-val"}},
            "b": {"env_overrides": {"SHARED": "same-val"}},
        }
        cfg = _mp_cfg(profiles=profiles)
        p = resolve_profiles(cfg, ["a", "b"])  # must not raise
        assert p.env_overrides["SHARED"] == "same-val"


class TestResolveProfilesCLIPrecedence:
    """§8 AC#4: CLI list fully overrides env list."""

    def test_cli_names_override_env_list(self):
        profiles = {
            "a": {"phases": ["phase_1"]},
            "b": {"phases": ["phase_2"]},
            "c": {"phases": ["phase_3"]},
        }
        cfg = _mp_cfg(profiles=profiles)
        # CLI gives ["c"]; env would give "a,b" — CLI should win
        env = {"CIU_SERVICES_PROFILE": "a,b"}
        p = resolve_profiles(cfg, ["c"], env=env)
        assert p.phase_keys == {"phase_3"}
        assert "phase_1" not in (p.phase_keys or set())
        assert "phase_2" not in (p.phase_keys or set())

    def test_empty_cli_falls_through_to_env(self):
        profiles = {
            "from_env": {"phases": ["phase_2"]},
        }
        cfg = _mp_cfg(profiles=profiles)
        env = {"CIU_SERVICES_PROFILE": "from_env"}
        p = resolve_profiles(cfg, None, env=env)
        assert p.name == "from_env"
        assert p.phase_keys == {"phase_2"}


class TestResolveProfilesEnvParsing:
    """§8 AC#5: env var parsing handles spaces."""

    def test_env_with_spaces_parsed_correctly(self):
        profiles = {
            "core": {"phases": ["phase_1"]},
            "db": {"phases": ["phase_2"]},
            "worker-io": {"phases": ["phase_4"]},
        }
        cfg = _mp_cfg(profiles=profiles)
        env = {"CIU_SERVICES_PROFILE": "core, db ,worker-io"}
        p = resolve_profiles(cfg, None, env=env)
        assert p.phase_keys == {"phase_1", "phase_2", "phase_4"}

    def test_empty_env_var_returns_default(self):
        cfg = _mp_cfg()
        p = resolve_profiles(cfg, None, env={"CIU_SERVICES_PROFILE": ""})
        assert p.name is None
        assert p.phase_keys is None


class TestCIUHostProfileRetired:
    """§8 AC#6: CIU_HOST_PROFILE is retired — raises/exits 2, never used."""

    def test_ciu_host_profile_raises_valueerror(self):
        cfg = _mp_cfg()
        env = {"CIU_HOST_PROFILE": "some_profile"}
        with pytest.raises(ValueError, match="CIU_HOST_PROFILE"):
            resolve_profiles(cfg, None, env=env)

    def test_ciu_host_profile_error_mentions_replacement(self):
        cfg = _mp_cfg()
        env = {"CIU_HOST_PROFILE": "some_profile"}
        with pytest.raises(ValueError, match="CIU_SERVICES_PROFILE"):
            resolve_profiles(cfg, None, env=env)

    def test_ciu_host_profile_not_used_as_fallback(self):
        """Even if CIU_HOST_PROFILE names a valid profile, it must raise."""
        profiles = {"some_profile": {"phases": ["phase_1"]}}
        cfg = _mp_cfg(profiles=profiles)
        env = {"CIU_HOST_PROFILE": "some_profile"}
        with pytest.raises(ValueError):
            resolve_profiles(cfg, None, env=env)

    def test_ciu_host_profile_with_cli_names_still_raises(self):
        """CIU_HOST_PROFILE in env raises even when CLI names are given."""
        profiles = {"valid": {"phases": ["phase_1"]}}
        cfg = _mp_cfg(profiles=profiles)
        env = {"CIU_HOST_PROFILE": "stale_profile"}
        with pytest.raises(ValueError, match="CIU_HOST_PROFILE"):
            resolve_profiles(cfg, ["valid"], env=env)


class TestResolveProfilesCommaForm:
    """§8 AC#7: comma CLI form equals repeatable form."""

    def test_comma_form_same_as_repeatable(self):
        """resolve_profiles(["core,db"]) equivalent to resolve_profiles(["core","db"]).

        Note: the comma splitting happens in deploy.py's CLI processing, not in
        resolve_profiles itself. Here we test that if a caller passes
        ["core", "db"] the result is the same as iterating both. The comma
        splitting is tested at the deploy.py layer.
        """
        profiles = {
            "core": {"phases": ["phase_1", "phase_2"]},
            "db": {"phases": ["phase_2", "phase_3"]},
        }
        cfg = _mp_cfg(profiles=profiles)
        p1 = resolve_profiles(cfg, ["core", "db"], env={})
        # Verify correct union
        assert p1.phase_keys == {"phase_1", "phase_2", "phase_3"}

    def test_unknown_profile_in_list_raises_with_available(self):
        """§8 AC#9: unknown profile still errors with the available-profiles list."""
        profiles = {"alpha": {}, "beta": {}}
        cfg = _mp_cfg(profiles=profiles)
        with pytest.raises(ValueError, match="alpha|beta"):
            resolve_profiles(cfg, ["alpha", "unknown"], env={})


class TestResolveProfilesAllPhasesAbsorb:
    """Profile with no phases means 'all phases' → absorbs into None."""

    def test_none_phases_profile_absorbs_to_all(self):
        profiles = {
            "specific": {"phases": ["phase_1"]},
            "all_phases": {},  # no 'phases' key → None
        }
        cfg = _mp_cfg(profiles=profiles)
        p = resolve_profiles(cfg, ["specific", "all_phases"], env={})
        assert p.phase_keys is None  # None = all phases

    def test_none_phases_profile_first_also_absorbs(self):
        profiles = {
            "all_phases": {},
            "specific": {"phases": ["phase_2"]},
        }
        cfg = _mp_cfg(profiles=profiles)
        p = resolve_profiles(cfg, ["all_phases", "specific"], env={})
        assert p.phase_keys is None

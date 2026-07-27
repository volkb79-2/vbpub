"""Tests for route_doctor.py (BACKLOG B1: `nyxloom route doctor` --
schema-validate routes.toml + live-probe each route). ALL live probing is
mocked -- the authoritative gate has no network and no CLI binaries
installed; `adapters.probe` itself already has its own unmocked-subprocess
contract tests in test_adapters.py, which this module reuses verbatim
(never reimplements)."""

from __future__ import annotations

import logging

import pytest
import structlog.contextvars
from unittest.mock import patch

from nyxloom import cli, log, paths, route_doctor
from nyxloom.config import RouteDef, Routes


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Same rationale as test_cli.py's copy (docs/plan-logging.md P05c) --
    load-bearing for the CLI-table tests below, which assert on exact
    capsys stdout content."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def _routes(tiers=None, routes=None) -> Routes:
    return Routes(revision="test", tiers=tiers or {}, routes=routes or {})


# ---------------------------------------------------------------------------
# check_schema

class TestCheckSchema:
    def test_clean_config_has_no_findings(self):
        routes = _routes(
            tiers={"implement-1": ["r1"]},
            routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1",
                                    probe=["fake", "--version"])},
        )
        assert route_doctor.check_schema(routes) == []

    def test_unknown_cli_is_critical(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="carrier-pigeon", model="m1"),
        })
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "route-unknown-cli"
        assert f.severity == "critical"
        assert f.refs == ["r1"]
        assert "carrier-pigeon" in f.message

    def test_missing_model_is_critical(self):
        routes = _routes(routes={"r1": RouteDef(route_id="r1", cli="fake", model="")})
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        assert findings[0].kind == "route-missing-model"
        assert findings[0].severity == "critical"
        assert findings[0].refs == ["r1"]

    def test_probe_named_builtin_is_valid(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe="one-token-ping"),
        })
        assert route_doctor.check_schema(routes) == []

    def test_probe_second_named_builtin_is_valid(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe="session-limit-check"),
        })
        assert route_doctor.check_schema(routes) == []

    def test_probe_argv_list_is_valid(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe=["fake", "--version"]),
        })
        assert route_doctor.check_schema(routes) == []

    def test_probe_none_is_valid(self):
        routes = _routes(routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1", probe=None)})
        assert route_doctor.check_schema(routes) == []

    def test_probe_empty_list_is_malformed(self):
        routes = _routes(routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1", probe=[])})
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        assert findings[0].kind == "route-malformed-probe"
        assert findings[0].severity == "error"

    def test_probe_bare_non_builtin_string_is_malformed(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe="not-a-builtin"),
        })
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        assert findings[0].kind == "route-malformed-probe"
        assert "not-a-builtin" in findings[0].message

    def test_probe_list_with_non_string_element_is_malformed(self):
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe=["fake", 7]),
        })
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        assert findings[0].kind == "route-malformed-probe"

    def test_tier_dangling_route_is_critical(self):
        routes = _routes(
            tiers={"implement-1": ["r1", "ghost"]},
            routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1")},
        )
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "tier-dangling-route"
        assert f.severity == "critical"
        assert f.refs == ["implement-1", "ghost"]

    def test_tier_dangling_route_matches_a_real_for_tier_keyerror(self):
        """The finding exists precisely BECAUSE Routes.for_tier has no
        guard against a dangling entry -- prove the KeyError it warns about
        is real, not a hypothetical."""
        routes = _routes(tiers={"implement-1": ["ghost"]}, routes={})
        findings = route_doctor.check_schema(routes)
        assert findings[0].kind == "tier-dangling-route"
        with pytest.raises(KeyError):
            routes.for_tier("implement-1")

    def test_empty_tier_is_warning(self):
        routes = _routes(tiers={"implement-1": []}, routes={})
        findings = route_doctor.check_schema(routes)
        assert len(findings) == 1
        assert findings[0].kind == "tier-empty"
        assert findings[0].severity == "warning"
        assert findings[0].refs == ["implement-1"]

    def test_multiple_routes_each_get_their_own_finding(self):
        routes = _routes(routes={
            "bad-cli": RouteDef(route_id="bad-cli", cli="nope", model="m"),
            "bad-model": RouteDef(route_id="bad-model", cli="fake", model=""),
            "ok": RouteDef(route_id="ok", cli="fake", model="m"),
        })
        findings = route_doctor.check_schema(routes)
        kinds_by_ref = {f.refs[0]: f.kind for f in findings}
        assert kinds_by_ref == {
            "bad-cli": "route-unknown-cli",
            "bad-model": "route-missing-model",
        }


# ---------------------------------------------------------------------------
# check_live

class TestCheckLive:
    def test_healthy_route_produces_no_finding(self):
        routes = _routes(routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1")})
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")) as mock_probe:
            findings = route_doctor.check_live(routes)
        mock_probe.assert_called_once()
        assert findings == []

    def test_failing_route_produces_warning_finding(self):
        routes = _routes(routes={"r1": RouteDef(route_id="r1", cli="fake", model="m1")})
        with patch("nyxloom.adapters.probe", return_value=(False, "command not found: fake")):
            findings = route_doctor.check_live(routes)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "route-probe-failed"
        assert f.severity == "warning"
        assert f.refs == ["r1"]
        assert "command not found: fake" in f.message

    def test_only_failing_routes_are_flagged(self):
        routes = _routes(routes={
            "good": RouteDef(route_id="good", cli="fake", model="m1"),
            "bad": RouteDef(route_id="bad", cli="fake", model="m1"),
        })

        def fake_probe(route):
            return (False, "boom") if route.route_id == "bad" else (True, "ok")

        with patch("nyxloom.adapters.probe", side_effect=fake_probe):
            findings = route_doctor.check_live(routes)
        assert [f.refs[0] for f in findings] == ["bad"]

    def test_reuses_the_real_adapters_probe_unmocked(self):
        """No mock here -- proves check_live really calls the SAME
        adapters.probe the daemon's _provider_ok memoizes, not a
        reimplementation: a probe=None route always resolves (True,
        'no-probe') per adapters.probe's own contract, with zero subprocess
        calls."""
        routes = _routes(routes={
            "r1": RouteDef(route_id="r1", cli="fake", model="m1", probe=None),
        })
        with patch("subprocess.run") as mock_run:
            findings = route_doctor.check_live(routes)
        mock_run.assert_not_called()
        assert findings == []


# ---------------------------------------------------------------------------
# doctor_routes

class TestDoctorRoutes:
    def test_load_failure_degrades_to_single_critical_finding(self, tmp_path):
        bad = tmp_path / "routes.toml"
        bad.write_text("[unterminated", encoding="utf-8")
        routes, findings = route_doctor.doctor_routes(path=bad, live_probe=False)
        assert routes is None
        assert len(findings) == 1
        assert findings[0].kind == "routes-load-failed"
        assert findings[0].severity == "critical"

    def test_dangling_inherit_parent_degrades_to_load_failed(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "t"\n\n[routes.r1]\ninherit = "ghost"\n', encoding="utf-8")
        routes, findings = route_doctor.doctor_routes(path=p, live_probe=False)
        assert routes is None
        assert findings[0].kind == "routes-load-failed"

    def test_live_probe_false_never_calls_adapters_probe(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "t"\n\n[routes.r1]\ncli = "fake"\nmodel = "m1"\n',
                      encoding="utf-8")
        with patch("nyxloom.adapters.probe") as mock_probe:
            routes, findings = route_doctor.doctor_routes(path=p, live_probe=False)
        mock_probe.assert_not_called()
        assert routes is not None
        assert findings == []

    def test_live_probe_true_combines_schema_and_live_findings(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "t"\n\n'
            '[routes.bad-cli]\ncli = "nope"\nmodel = "m1"\n\n'
            '[routes.unreachable]\ncli = "fake"\nmodel = "m2"\n',
            encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe", return_value=(False, "timeout after 60s")):
            routes, findings = route_doctor.doctor_routes(path=p, live_probe=True)
        assert routes is not None
        assert sorted(f.kind for f in findings) == [
            "route-probe-failed", "route-probe-failed", "route-unknown-cli",
        ]

    def test_default_path_uses_paths_routes_path(self, tmp_state):
        paths.routes_path().write_text(
            'revision = "t"\n\n[routes.r1]\ncli = "fake"\nmodel = "m1"\n', encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")):
            routes, findings = route_doctor.doctor_routes()
        assert routes is not None
        assert routes.routes["r1"].model == "m1"
        assert findings == []


# ---------------------------------------------------------------------------
# CLI: `nyxloom route doctor`

class TestCliRouteDoctor:
    def test_no_subcommand_prints_help_and_exits_2(self, capsys):
        """Mirrors the same unknown-subcommand handling as `gate`/
        `free-models`/`capability-map`/`finding` (see test_cli.py's
        test_gate_no_subcommand_prints_help_and_exits_2)."""
        exit_code = cli.main(["route"])
        assert exit_code == 2

    def test_clean_config_exits_0_and_prints_ok_row(self, tmp_state, capsys):
        paths.routes_path().write_text(
            'revision = "t"\n\n[routes.r1]\ncli = "fake"\nmodel = "m1"\n', encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")):
            exit_code = cli.main(["route", "doctor"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "r1" in out
        assert "OK" in out
        assert "problem" not in out

    def test_critical_finding_exits_1_and_marks_route_problem(self, tmp_state, capsys):
        paths.routes_path().write_text(
            'revision = "t"\n\n[routes.r1]\ncli = "nope"\nmodel = "m1"\n', encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")):
            exit_code = cli.main(["route", "doctor"])
        assert exit_code == 1
        out = capsys.readouterr().out
        assert "problem" in out
        assert "route-unknown-cli" in out

    def test_no_probe_flag_skips_live_probing(self, tmp_state, capsys):
        paths.routes_path().write_text(
            'revision = "t"\n\n[routes.r1]\ncli = "fake"\nmodel = "m1"\n', encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe") as mock_probe:
            exit_code = cli.main(["route", "doctor", "--no-probe"])
        mock_probe.assert_not_called()
        assert exit_code == 0

    def test_warning_only_finding_still_exits_0(self, tmp_state, capsys):
        """An empty tier is a warning, not critical/error -- must not fail
        the exit code (mirrors cmd_doctor's own severity threshold)."""
        paths.routes_path().write_text(
            'revision = "t"\n\n[tiers.dead]\nroutes = []\n\n'
            '[routes.r1]\ncli = "fake"\nmodel = "m1"\n',
            encoding="utf-8",
        )
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")):
            exit_code = cli.main(["route", "doctor"])
        assert exit_code == 0
        assert "tier-empty" in capsys.readouterr().out

    def test_load_failure_exits_1_without_a_per_route_table(self, tmp_state, capsys):
        paths.routes_path().write_text("[unterminated", encoding="utf-8")
        exit_code = cli.main(["route", "doctor"])
        assert exit_code == 1
        out = capsys.readouterr().out
        assert "routes-load-failed" in out
        assert "status" not in out  # the per-route table header never printed

    def test_read_only_file_is_byte_identical_after_a_clean_run(self, tmp_state, capsys):
        content = ('revision = "t"\n\n[routes.r1]\ncli = "fake"\nmodel = "m1"\n'
                   'probe = ["fake", "--version"]\n')
        paths.routes_path().write_text(content, encoding="utf-8")
        with patch("nyxloom.adapters.probe", return_value=(True, "ok")):
            exit_code = cli.main(["route", "doctor"])
        assert exit_code == 0
        assert paths.routes_path().read_text(encoding="utf-8") == content

    def test_read_only_file_is_byte_identical_even_with_critical_findings(self, tmp_state, capsys):
        """Doctor never writes routes.toml -- unlike free_models/
        capability_map's managed-block writers, it has no writer at all."""
        content = 'revision = "t"\n\n[routes.r1]\ncli = "nope"\nmodel = "m1"\n'
        paths.routes_path().write_text(content, encoding="utf-8")
        exit_code = cli.main(["route", "doctor", "--no-probe"])
        assert exit_code == 1
        assert paths.routes_path().read_text(encoding="utf-8") == content

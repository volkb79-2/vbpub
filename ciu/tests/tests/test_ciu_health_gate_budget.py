"""S7.7 — CIU-67 (the gate's budget) and CIU-68 (the gate running at all).

Both were live-reproduced together on a fresh `ciu clean && ciu up`, and
they compound, so they are tested together.

Oracles:
- CIU-67. `[deploy.health].timeout` is ONE probe attempt's duration. The
  inter-phase gate's OVERALL budget for a container to reach `healthy` is a
  different question with a different answer, and it is DERIVED from that
  container's own declared healthcheck — never from `timeout`. The
  controlled reproduction below is the live dstdns case verbatim: a correct,
  deliberate `timeout = "5s"` next to a service whose own healthcheck
  declares `start_period = 240s`. Before the fix that container got a
  five-second budget and failed the gate on every fresh deploy while
  converging normally.
- CIU-67. `[deploy.health].gate_timeout` is the new global lever for the
  budget; `[deploy.phases.*.services].health_timeout` remains the
  per-service escape hatch and still wins over both.
- CIU-68(a). Declaring a `stack:*:healthy|completed` requirement turns the
  gate on by itself. Self-selecting: a selection with no such ref is
  unchanged, and pays nothing.
- CIU-68(b). A requirement reported `starting` is on track, not broken: the
  preflight polls it to a bounded budget. A requirement that will never
  satisfy (container absent, unhealthy) still fails PROMPTLY — the poll must
  not convert every failure into a long wait.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, procutil, provisioning  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


# ---------------------------------------------------------------------------
# CIU-67 — derive_gate_budget_s / resolve_gate_timeout_s
# ---------------------------------------------------------------------------


class TestDeriveGateBudget:
    def test_start_period_plus_retries_times_interval(self):
        """Docker's own worst case for a container still converging: the full
        grace period, plus the retries a post-grace probe sequence needs to
        become conclusive."""
        definition = {
            "healthcheck": {"start_period": "240s", "interval": "10s", "retries": 3}
        }
        assert deploy.derive_gate_budget_s(definition) == 240 + 3 * 10

    def test_omitted_fields_use_dockers_documented_defaults(self):
        """A declared healthcheck with no timings behaves per the daemon's
        own defaults (0s grace, 3 retries, 30s interval) — a READ fact about
        what Docker will do, not an invented number."""
        assert deploy.derive_gate_budget_s({"healthcheck": {"test": ["CMD", "true"]}}) == 90.0
        assert deploy.DEFAULT_GATE_BUDGET_S == 90.0

    @pytest.mark.parametrize("definition", [{}, {"healthcheck": None}, {"healthcheck": "nope"}])
    def test_no_usable_healthcheck_falls_back_to_the_docker_default(self, definition):
        """Never actually waited on: such a container classifies
        `no-healthcheck`, a READY status that resolves on the first poll."""
        assert deploy.derive_gate_budget_s(definition) == deploy.DEFAULT_GATE_BUDGET_S

    def test_unparseable_retries_warns_and_uses_the_default(self, capsys):
        budget = deploy.derive_gate_budget_s(
            {"healthcheck": {"retries": "many", "interval": "10s"}}
        )
        assert budget == 3 * 10
        assert "could not parse healthcheck retries" in capsys.readouterr().out

    def test_nonsensical_retries_count_uses_the_default(self):
        """A zero or negative retry count would derive a budget of just the
        grace period; Docker would still be probing after it."""
        assert deploy.derive_gate_budget_s(
            {"healthcheck": {"retries": 0, "interval": "10s", "start_period": "5s"}}
        ) == 5 + 3 * 10

    def test_unparseable_durations_fall_back_per_field(self, capsys):
        budget = deploy.derive_gate_budget_s(
            {"healthcheck": {"start_period": "banana", "interval": "10s", "retries": 1}}
        )
        assert budget == 0 + 1 * 10
        assert "could not parse duration" in capsys.readouterr().out


class TestResolveGateTimeout:
    def test_absent_key_is_none_not_zero(self):
        """An absent key is not a value: None means "derive per container"."""
        assert deploy.resolve_gate_timeout_s({}) is None
        assert deploy.resolve_gate_timeout_s({"deploy": {"health": {"timeout": "5s"}}}) is None

    def test_declared_gate_timeout_is_parsed(self):
        config = {"deploy": {"health": {"gate_timeout": "600s", "timeout": "5s"}}}
        assert deploy.resolve_gate_timeout_s(config) == 600.0

    def test_unparseable_gate_timeout_falls_back_to_the_docker_default(self, capsys):
        config = {"deploy": {"health": {"gate_timeout": "soon"}}}
        assert deploy.resolve_gate_timeout_s(config) == deploy.DEFAULT_GATE_BUDGET_S
        assert "could not parse duration" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CIU-67 — the live reproduction, through resolve_selection_health_containers
# ---------------------------------------------------------------------------


_SLOW_COMPOSE = """\
services:
  pgadmin:
    image: dpage/pgadmin4
    container_name: proj-dev-pgadmin
    healthcheck:
      test: ["CMD", "wget", "-q", "http://localhost/misc/ping"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 240s
"""


def _slow_stack(repo_root: Path, rel: str = "apps/pgadmin") -> None:
    stack_dir = repo_root / rel
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "ciu.compose.yml").write_text(_SLOW_COMPOSE, encoding="utf-8")


def _profile(health: dict | None = None) -> Profile:
    deploy_cfg: dict = {"project_name": "proj", "environment_tag": "dev"}
    if health is not None:
        deploy_cfg["health"] = health
    return Profile(name=None, phase_keys=None, config={"deploy": deploy_cfg})


def _selection(rel: str = "apps/pgadmin", **service) -> list[dict]:
    svc = {"name": "pgadmin", "health": True}
    svc.update(service)
    return [{"path": rel, "service": svc, "phase": 1}]


class TestGateBudgetResolution:
    def test_a_five_second_probe_timeout_no_longer_becomes_a_five_second_gate(
        self, tmp_path
    ):
        """The live dstdns reproduction (D-212). `timeout = "5s"` is a
        correct, deliberate PER-PROBE value. Before CIU-67 it silently became
        this container's overall gate budget, failing it 5s after phase start
        despite a declared 240s grace period."""
        _slow_stack(tmp_path)
        targets = deploy.resolve_selection_health_containers(
            tmp_path, _profile({"timeout": "5s"}), _selection(),
            default_timeout_s=deploy.resolve_gate_timeout_s(
                _profile({"timeout": "5s"}).config
            ),
        )
        assert targets == {"proj-dev-pgadmin": 270.0}, "5s must not reach the gate"

    def test_declared_gate_timeout_applies_to_every_container(self, tmp_path):
        _slow_stack(tmp_path)
        config_health = {"timeout": "5s", "gate_timeout": "600s"}
        targets = deploy.resolve_selection_health_containers(
            tmp_path, _profile(config_health), _selection(),
            default_timeout_s=deploy.resolve_gate_timeout_s(_profile(config_health).config),
        )
        assert targets == {"proj-dev-pgadmin": 600.0}

    def test_per_service_health_timeout_still_wins_over_both(self, tmp_path):
        """The existing S7.7/CIU-QOL-8 escape hatch is untouched and remains
        the most specific answer."""
        _slow_stack(tmp_path)
        config_health = {"timeout": "5s", "gate_timeout": "600s"}
        targets = deploy.resolve_selection_health_containers(
            tmp_path, _profile(config_health), _selection(health_timeout="90s"),
            default_timeout_s=deploy.resolve_gate_timeout_s(_profile(config_health).config),
        )
        assert targets == {"proj-dev-pgadmin": 90.0}


# ---------------------------------------------------------------------------
# CIU-68(a) — the gate turns itself on for the refs that need it
# ---------------------------------------------------------------------------


class TestSelectionStackHealthRequirement:
    @pytest.mark.parametrize(
        "ref", ["stack:infra/vault:healthy", "stack:db-init:completed"]
    )
    def test_a_stack_health_ref_is_found(self, ref):
        rendered = {"apps/api": {"api": {"requires": [ref, "vault:secret/x"]}}}
        found = deploy.selection_stack_health_requirement([{"path": "apps/api"}], rendered)
        assert found == ref

    def test_other_ref_kinds_do_not_arm_the_gate(self):
        """Self-selecting: a run with no such ref pays nothing."""
        rendered = {"apps/api": {"api": {"requires": ["vault:secret/x", "pg:role/api"]}}}
        assert deploy.selection_stack_health_requirement(
            [{"path": "apps/api"}], rendered
        ) is None

    @pytest.mark.parametrize("rendered", [None, {}])
    def test_no_rendered_selection_is_not_an_error(self, rendered):
        assert deploy.selection_stack_health_requirement([{"path": "a"}], rendered) is None

    def test_unrendered_or_malformed_entries_are_skipped_not_raised(self):
        """A shape error belongs to provisioning_preflight's own
        validate_stack_shape call moments later, not to the question "should
        the gate run?"."""
        rendered = {
            "apps/one": "not a mapping",
            "apps/two": {"a": {}, "b": {}},          # ambiguous root key
            "apps/three": {"three": {"requires": "not a list"}},
            "apps/four": {"four": {"requires": [7, "stack:x:healthy"]}},
        }
        selection = [{"path": p} for p in ("apps/nope", "apps/one", "apps/two",
                                           "apps/three", "apps/four")]
        assert deploy.selection_stack_health_requirement(selection, rendered) == "stack:x:healthy"


class TestHealthAfterPhaseDefault:
    """End-to-end through `deploy.main` — the flag an operator could not
    discover is no longer the only way to get the gate."""

    @pytest.fixture
    def up(self, monkeypatch, tmp_path):
        seen: list[bool] = []
        profile = _profile()

        def harness(requires: list[str], *argv: str) -> list[bool]:
            rendered = {"apps/api": {"api": {"requires": requires}}}
            selection = [{"path": "apps/api", "service": {"name": "api"}, "phase": 1}]
            monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
            monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _c: None)
            monkeypatch.setattr(deploy, "resolve_repo_root", lambda _r: tmp_path)
            monkeypatch.setattr(deploy, "load_global_config", lambda _r: profile.config)
            monkeypatch.setattr(deploy, "resolve_profiles", lambda _c, _n: profile)
            monkeypatch.setattr(deploy, "build_selection", lambda *_a: selection)
            monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_a, **_k: rendered)
            for name in (
                "check_preflight", "vault_preflight", "producer_preflight",
                "provisioning_preflight", "registry_preflight",
                "governance_slice_preflight", "ensure_workspace_network",
            ):
                monkeypatch.setattr(deploy, name, lambda *_a, **_k: None)
            monkeypatch.setattr(deploy, "action_healthcheck", lambda *_a, **_k: 0)
            monkeypatch.setattr(
                deploy, "action_deploy",
                lambda *_a, **kw: seen.append(kw["health_after_phase"]) or 0,
            )
            assert deploy.main(["--deploy", *argv]) == 0
            return seen

        return harness

    def test_a_stack_healthy_requirement_enables_the_gate_on_a_bare_up(self, up, capsys):
        assert up(["stack:infra/vault:healthy"]) == [True]
        out = capsys.readouterr().out
        assert "health gate enabled for this run" in out
        assert "stack:infra/vault:healthy" in out

    def test_no_such_requirement_leaves_the_gate_off(self, up, capsys):
        assert up(["vault:secret/x"]) == [False]
        assert "health gate enabled for this run" not in capsys.readouterr().out

    def test_explicit_deploy_plus_healthcheck_still_enables_it(self, up, capsys):
        """The pre-CIU-68 invocation keeps working, and does not re-announce
        an auto-enable it did not need."""
        assert up(["vault:secret/x"], "--healthcheck") == [True]
        assert "health gate enabled for this run" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CIU-68(b) — `starting` is on track, and only `starting` gets to wait
# ---------------------------------------------------------------------------


def _docker_state(state: str | None):
    if state is None:
        return lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="")
    return lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=state)


class TestProbeRetryability:
    def test_starting_is_retryable_and_says_why(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            procutil, "docker",
            _docker_state('{"Health": {"Status": "starting"}}'),
        )
        result = provisioning.probe_ref("stack:vault:healthy", {}, tmp_path)
        assert (result.satisfied, result.retryable) == (False, True)
        assert "within its start_period" in result.reason

    def test_a_running_one_shot_is_retryable_too(self, monkeypatch, tmp_path):
        """`:completed` had the identical shape: a job that has not finished
        YET is not a job that failed."""
        monkeypatch.setattr(
            procutil, "docker",
            _docker_state('{"Running": true, "ExitCode": 0}'),
        )
        result = provisioning.probe_ref("stack:db-init:completed", {}, tmp_path)
        assert (result.satisfied, result.retryable) == (False, True)

    @pytest.mark.parametrize(
        "ref, state",
        [
            ("stack:vault:healthy", None),                                  # absent
            ("stack:vault:healthy", '{"Health": {"Status": "unhealthy"}}'),  # broken
            ("stack:db-init:completed", '{"Running": false, "ExitCode": 2}'),
        ],
    )
    def test_conditions_that_will_never_resolve_are_not_retryable(
        self, monkeypatch, tmp_path, ref, state
    ):
        """The other half of the distinction. If everything were retryable,
        the poll would just make every genuine failure slow."""
        monkeypatch.setattr(procutil, "docker", _docker_state(state))
        result = provisioning.probe_ref(ref, {}, tmp_path)
        assert (result.satisfied, result.retryable) == (False, False)


class _FakeClock:
    """Deterministic monotonic clock + sleep for the bounded poll."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _preflight_rendered() -> dict:
    return {"apps/api": {"api": {"requires": ["stack:vault:healthy"], "provides": []}}}


class TestBoundedRequirementPoll:
    @pytest.fixture
    def run_preflight(self, monkeypatch, tmp_path):
        clock = _FakeClock()
        monkeypatch.setattr(deploy, "time", clock)

        def run(results: list[provisioning.ProbeResult], config: dict | None = None):
            pending = list(results)
            calls: list[str] = []
            graphs: list[object] = []
            run.graphs = graphs  # type: ignore[attr-defined]  # readable even if the call raises

            def fake_probe(ref, _config, _root, *, stacks=None):
                # CIU-70 × CIU-68(b): `stacks` is the resolution graph the
                # probe resolves its target container from. It is recorded,
                # not ignored — the merge of these two changes is the one
                # place where dropping it on the RE-probe would silently
                # regress CIU-70 only on the retry path, which no CIU-70 test
                # exercises (nothing there retries).
                calls.append(ref)
                graphs.append(stacks)
                return pending.pop(0) if len(pending) > 1 else pending[0]

            from ciu import provisioning as provisioning_pkg
            monkeypatch.setattr(provisioning_pkg, "probe_ref", fake_probe)
            profile = Profile(
                name=None, phase_keys=None,
                config=config or {"deploy": {"project_name": "p", "environment_tag": "e"}},
            )
            deploy.provisioning_preflight(
                tmp_path, profile, [{"path": "apps/api"}], _preflight_rendered(),
                lint=False, probe=True,
            )
            return calls, clock

        run.clock = clock  # type: ignore[attr-defined]
        return run

    def test_a_starting_dependency_is_waited_out_not_failed(self, run_preflight, capsys):
        """The CIU-68 reproduction: `stack:infra/vault:healthy` reported
        `starting` on a genuinely fresh deploy and the one-shot probe failed
        the whole phase; vault reported healthy moments later, unobserved."""
        starting = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=False, retryable=True,
            reason="Stack 'vault' health status: starting",
        )
        healthy = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=True, reason="healthy",
        )
        calls, clock = run_preflight([starting, starting, healthy])

        assert len(calls) == 3, "the probe must be retried, not called once"
        assert clock.slept == [5.0, 5.0]
        out = capsys.readouterr().out
        assert "waiting up to 90s" in out
        assert "satisfied while waiting" in out

    def test_the_resolution_graph_reaches_every_reprobe_not_just_the_first(
        self, run_preflight
    ):
        """CIU-70 × CIU-68(b), the merge point — this is the ONE assertion
        that catches the resolution these two changes' textual conflict
        invites getting wrong.

        CIU-70 passes `stacks=probe_graph` so a probe resolves its target
        container from the stack that PROVIDES the ref; without it a `pg:` /
        `minio:` ref fails closed. CIU-68(b) wraps that call in a bounded
        retry. Threading the graph through the FIRST call and dropping it on
        the RE-probe would look correct in every CIU-70 test (none of them
        retry) and in every CIU-68 test that only counts calls — and would
        fail live, on the retry path only, exactly where the original
        `starting` failure lived.
        """
        starting = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=False, retryable=True,
            reason="starting",
        )
        healthy = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=True, reason="healthy",
        )
        calls, _clock = run_preflight([starting, starting, healthy])
        graphs = run_preflight.graphs

        assert len(graphs) == len(calls) == 3
        expected = deploy.provisioning_graph(_preflight_rendered())
        assert graphs == [expected] * 3, (
            "every probe — initial AND each re-probe — must receive the same "
            "CIU-70 resolution graph"
        )
        assert expected, "the fixture must actually produce a non-empty graph"

    def test_a_never_converging_dependency_fails_after_the_budget(self, run_preflight):
        stuck = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=False, retryable=True,
            reason="Stack 'vault' health status: starting",
        )
        with pytest.raises(ValueError, match="Provisioning preflight failed"):
            run_preflight([stuck])

    def test_gate_timeout_sets_the_poll_budget(self, run_preflight, capsys):
        stuck = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=False, retryable=True, reason="starting",
        )
        config = {
            "deploy": {
                "project_name": "p", "environment_tag": "e",
                "health": {"timeout": "5s", "gate_timeout": "20s"},
            }
        }
        with pytest.raises(ValueError):
            run_preflight([stuck], config)
        # 20s budget at a 5s cadence, not the 5s per-probe timeout.
        assert "waiting up to 20s" in capsys.readouterr().out
        assert run_preflight.clock.slept == [5.0, 5.0, 5.0, 5.0]

    def test_a_non_retryable_failure_still_fails_promptly(self, run_preflight):
        """A poll that waited on everything would turn every real
        misconfiguration into a long, silent stall."""
        absent = provisioning.ProbeResult(
            ref="stack:vault:healthy", satisfied=False,
            reason="Container 'p-e-vault' not found",
        )
        with pytest.raises(ValueError, match="not found"):
            run_preflight([absent])
        assert run_preflight.clock.slept == [], "no waiting on a dependency that is absent"

    def test_resolve_requirement_poll_budget_prefers_gate_timeout(self):
        assert deploy.resolve_requirement_poll_budget_s({}) == deploy.DEFAULT_GATE_BUDGET_S
        assert deploy.resolve_requirement_poll_budget_s(
            {"deploy": {"health": {"gate_timeout": "12s"}}}
        ) == 12.0

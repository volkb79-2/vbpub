"""CR-05b: review dispatch and the committed-report readers.

The report readers get the most attention here. They are what is LEFT of
prose in this pipeline after the typed-result package removed prose from the
merge decision, so the property that matters is that they stay routing hints
-- readable, best-effort, and never able to turn into authority.
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from nyxloom import effects, effects_review, reconcile, storage
from nyxloom.config import MutexDef
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Event, EventType, Role, Route,
    TaskState, TaskStateFile, utc_now,
)


class _Git:
    """A git port that serves scripted `show` / `ls-tree` answers."""

    def __init__(self, show=None, ls_tree=None) -> None:
        self.show_map = show or {}
        self.ls_tree_out = ls_tree
        self.calls: list[tuple] = []

    def show(self, root, ref_path):
        self.calls.append(("show", ref_path))
        answer = self.show_map.get(ref_path)
        if answer is None:
            return subprocess.CompletedProcess([], 128, "", "does not exist")
        return subprocess.CompletedProcess([], 0, answer, "")

    def ls_tree_names(self, root, ref, path):
        self.calls.append(("ls_tree", ref, path))
        if self.ls_tree_out is None:
            return subprocess.CompletedProcess([], 128, "", "no such tree")
        return subprocess.CompletedProcess([], 0, self.ls_tree_out, "")

    def resolve_verify(self, root, rev):
        self.calls.append(("resolve_verify", rev))
        return "sha-of-" + rev


def _events(*rows) -> list[Event]:
    return [Event(schema_version=storage.SCHEMA_VERSION, sequence=i,
                  timestamp=utc_now(), project="demo",
                  actor=Actor(ActorKind.TICK, "t"), type=kind,
                  payload=payload, task_id=task_id)
            for i, (kind, payload, task_id) in enumerate(rows)]


def _gate_finished(task_id, phase, exit_code, tail=""):
    return (EventType.GATE_FINISHED,
            {"gate_result": {"phase": phase, "exit_code": exit_code,
                             "output_tail": tail}}, task_id)


class TestReviewReportText:

    def test_the_documented_path_is_read_first(self, sample_project):
        rel = f"feat/t1:./{sample_project.reports_dir}/t1-REVIEW.md"
        git = _Git(show={rel: "VERDICT: APPROVED\n"})
        assert effects_review.review_report_text(git, sample_project, "t1") == (
            "VERDICT: APPROVED\n")
        assert git.calls == [("show", rel)], "no broadened search when the documented path answers"

    def test_an_empty_report_at_the_documented_path_falls_through(self, sample_project):
        """A file that exists but is empty is the same as no report: the
        broadened search still runs, so a reviewer that wrote to the wrong
        name is still found."""
        rel = f"feat/t1:./{sample_project.reports_dir}/t1-REVIEW.md"
        other = f"feat/t1:./{sample_project.reports_dir}/t1-CODEREVIEW.md"
        git = _Git(show={rel: "   \n", other: "REJECT_CLASS: fixable\n"},
                   ls_tree=f"{sample_project.reports_dir}/t1-CODEREVIEW.md\n")
        assert "fixable" in effects_review.review_report_text(git, sample_project, "t1")

    def test_a_report_found_only_by_content_reference_is_accepted(self, sample_project):
        """A live incident had a reviewer write its report to a filename that
        did not carry the task id. A report that exists but cannot be found
        reads exactly like a reviewer that never ran."""
        misnamed = f"feat/t1:./{sample_project.reports_dir}/REVIEW.md"
        git = _Git(show={misnamed: "review of t1 here\nREJECT_CLASS: product\n"},
                   ls_tree=f"{sample_project.reports_dir}/REVIEW.md\n")
        assert effects_review.parse_reject_class(git, sample_project, "t1") == "product"

    def test_blank_and_non_review_entries_are_skipped(self, sample_project):
        """The broadened arm must not read whatever else happens to be in the
        reports directory."""
        git = _Git(show={}, ls_tree="\n"
                   f"{sample_project.reports_dir}/NOTES.txt\n"
                   f"{sample_project.reports_dir}/t1-REPORT.md\n")
        assert effects_review.review_report_text(git, sample_project, "t1") is None

    def test_a_candidate_whose_show_fails_is_skipped_not_fatal(self, sample_project):
        git = _Git(show={}, ls_tree=f"{sample_project.reports_dir}/other-REVIEW.md\n")
        assert effects_review.review_report_text(git, sample_project, "t1") is None

    def test_a_candidate_that_mentions_another_task_is_not_this_task_s_report(
            self, sample_project):
        other = f"feat/t1:./{sample_project.reports_dir}/t9-REVIEW.md"
        git = _Git(show={other: "this is the review of t9\n"},
                   ls_tree=f"{sample_project.reports_dir}/t9-REVIEW.md\n")
        assert effects_review.review_report_text(git, sample_project, "t1") is None

    def test_an_unreadable_reports_tree_yields_no_report(self, sample_project):
        assert effects_review.review_report_text(_Git(), sample_project, "t1") is None


class TestParseRejectClass:

    @pytest.mark.parametrize("value", ["fixable", "architectural", "incapable",
                                       "product", "transient"])
    def test_every_declared_class_is_recognised(self, sample_project, value):
        rel = f"feat/t1:./{sample_project.reports_dir}/t1-REVIEW.md"
        git = _Git(show={rel: f"VERDICT: REJECTED\nREJECT_CLASS: {value.upper()}\n"})
        assert effects_review.parse_reject_class(git, sample_project, "t1") == value

    def test_a_class_outside_the_vocabulary_reads_as_unclassified(self, sample_project):
        """Inventing a class is not a way to route somewhere new: an
        unrecognised value leaves the task unclassified and falls back to the
        mechanical attempt budget."""
        rel = f"feat/t1:./{sample_project.reports_dir}/t1-REVIEW.md"
        git = _Git(show={rel: "REJECT_CLASS: whatever-i-felt-like\n"})
        assert effects_review.parse_reject_class(git, sample_project, "t1") is None

    def test_no_report_means_no_class(self, sample_project):
        assert effects_review.parse_reject_class(_Git(), sample_project, "t1") is None


class TestLatestGateFailure:

    def test_the_most_recent_failure_wins_and_passes_are_ignored(self):
        events = _events(_gate_finished("t1", "pre-merge", 1, "first failure"),
                         _gate_finished("t1", "pre-merge", 0, "a pass"),
                         _gate_finished("t1", "mutation", 1, "SURVIVED mutant"))
        assert effects_review.latest_gate_failure(events, "t1") == (
            "SURVIVED mutant", "mutation")

    def test_a_post_merge_failure_is_not_a_reviewer_s_to_classify(self):
        """Its remedy is a revert or a block, decided mechanically."""
        events = _events(_gate_finished("t1", "post-merge", 1, "post-merge tail"))
        assert effects_review.latest_gate_failure(events, "t1") == ("", "")

    def test_another_task_s_failure_does_not_leak(self):
        events = _events(_gate_finished("t2", "pre-merge", 1, "not mine"))
        assert effects_review.latest_gate_failure(events, "t1") == ("", "")

    def test_no_failure_recorded_degrades_gracefully(self):
        assert effects_review.latest_gate_failure([], "t1") == ("", "")


class TestIncapableEscalationNote:

    def test_no_frontmatter_short_circuits_before_any_io(self, sample_project):
        git = _Git()
        assert effects_review.incapable_escalation_note(git, sample_project, None) == ""
        assert git.calls == []

    def test_a_handoff_with_no_source_ref_produces_nothing(self, sample_project):
        class _FM:
            source = None
        assert effects_review.incapable_escalation_note(_Git(), sample_project, _FM()) == ""

    def test_a_source_ref_that_is_empty_produces_nothing(self, sample_project):
        class _FM:
            source = type("S", (), {"ref": ""})()
        assert effects_review.incapable_escalation_note(_Git(), sample_project, _FM()) == ""


class TestWarmestReviewSession:

    def _attempt(self, handle, state, started, role=Role.REVIEW_INDEPENDENT):
        return Attempt(attempt_id=f"a-{handle or 'none'}", role=role,
                       state=state, started=started,
                       route=Route(route_id="r", cli="fake", model="m"),
                       session_handle=handle)

    def test_no_prior_review_session_is_a_cold_launch(self):
        assert effects_review.warmest_review_session({}) is None

    def test_the_most_recently_started_exited_session_wins(self):
        early = utc_now()
        late = early.replace(year=early.year + 1)
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="t1", project="demo",
            state=TaskState.AWAITING_REVIEW, since=early,
            attempts=[self._attempt("old", AttemptState.EXITED, early),
                      self._attempt("new", AttemptState.EXITED, late)])
        assert effects_review.warmest_review_session({"t1": tsf}) == "new"

    def test_a_running_session_is_not_reusable(self):
        """Resuming a live session would interleave two prompts in one
        context."""
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="t1", project="demo",
            state=TaskState.AWAITING_REVIEW, since=utc_now(),
            attempts=[self._attempt("live", AttemptState.RUNNING, utc_now())])
        assert effects_review.warmest_review_session({"t1": tsf}) is None

    def test_an_implementer_session_is_never_reused_for_review(self):
        """The role contract prefix is what the warm resume is reusing, and
        an implementer session does not have it."""
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id="t1", project="demo",
            state=TaskState.AWAITING_REVIEW, since=utc_now(),
            attempts=[self._attempt("impl", AttemptState.EXITED, utc_now(),
                                    role=Role.IMPLEMENTER)])
        assert effects_review.warmest_review_session({"t1": tsf}) is None

    def _tie(self, handle, when):
        return TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=f"t-{handle}",
            project="demo", state=TaskState.AWAITING_REVIEW, since=when,
            attempts=[self._attempt(handle, AttemptState.EXITED, when)])

    def test_a_started_tie_resolves_the_same_way_whatever_the_map_order(self):
        """CR-05g. `max` returns the FIRST maximal element, so before the
        tie-break this followed `states` iteration order -- and the daemon
        builds that mapping by scanning a directory, so the chosen warm
        session genuinely varied between runs of identical state.

        Both orders are asserted, not one: a single order would pass just as
        happily against the old implementation, which is what let the same
        defect survive in the planner's copy until CR-06b.
        """
        when = utc_now()
        forward = {"t-h1": self._tie("h1", when), "t-h2": self._tie("h2", when)}
        backward = {"t-h2": forward["t-h2"], "t-h1": forward["t-h1"]}

        assert effects_review.warmest_review_session(forward) == "h2"
        assert effects_review.warmest_review_session(backward) == "h2"

    def test_the_tie_break_never_reaches_past_a_tie_to_an_older_session(self):
        """The safety property the repair rests on, asserted rather than
        argued: `(started, attempt_id)` compares `started` FIRST, so the
        winner is always drawn from the `started`-maximal set. Here the older
        session sorts LAST by attempt id, so a tie-break that ignored
        `started` would pick it."""
        late = utc_now()
        early = late.replace(year=late.year - 1)
        states = {
            "t-zz": self._tie("zz", early),     # newest id, oldest session
            "t-aa": self._tie("aa", late),      # oldest id, newest session
        }
        assert effects_review.warmest_review_session(states) == "aa"


class TestWaveLeaseUnion:

    def test_a_wave_holds_the_union_of_its_members_leases_deduplicated(
            self, tmp_state, sample_project, monkeypatch):
        """A concurrent carve or dispatch must not touch a task while it is
        under review, so the ONE review attempt holds every member's leases.
        Deduplicated by name: two members naming the same mutex must not make
        the wave request it twice, which would over-count capacity.

        Drives the real handler -- reimplementing the union here would assert
        the test's own arithmetic rather than the effector's.
        """
        from nyxloom import effects_dispatch, wrapper

        cfg = dataclasses.replace(sample_project, mutexes={
            "stack": MutexDef(name="stack", scope="project", capacity=1),
            "docs": MutexDef(name="docs", scope="project", capacity=2)})

        class _FM:
            tier = None
            source = None

            def __init__(self, names):
                self._names = names
                self.scope = type("S", (), {"touch": []})()

            def effective_mutexes(self):
                return self._names

        by_task = {"t-a": _FM(["stack", "docs"]), "t-b": _FM(["stack"])}
        monkeypatch.setattr(effects_dispatch, "frontmatter_for",
                            lambda c, tsf: by_task.get(tsf.task_id) if tsf else None)

        specs: list = []
        monkeypatch.setattr(wrapper, "launch_detached",
                            lambda spec: (specs.append(spec), 4242)[1])
        monkeypatch.setattr(
            effects_review.adapters, "build_dispatch",
            lambda route, **kw: (["fake-cli"], "prompt"))
        monkeypatch.setattr(effects_review.config.Routes, "load",
                            classmethod(lambda cls: _routes()))

        states = {t: TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                   task_id=t, project="demo",
                                   state=TaskState.AWAITING_REVIEW,
                                   since=utc_now())
                  for t in ("t-a", "t-b")}
        ports = effects.EffectPorts.system()
        effector = effects_review.ReviewEffector(ports)
        ctx = effects.EffectContext(project="demo", cfg=cfg, states=states,
                                    ports=ports)
        effector.launch_review(
            ctx, reconcile.LaunchReview(wave_id="w1", task_ids=["t-a", "t-b"]))

        assert len(specs) == 1, "one review attempt covers the whole wave"
        names = [ls["name"] for ls in specs[0].leases]
        assert len(names) == len(set(names)), f"duplicate lease requested: {names}"
        assert set(names) == {
            MutexDef(name="stack", scope="project", capacity=1).lease_name("demo"),
            MutexDef(name="docs", scope="project", capacity=2).lease_name("demo"),
        }


class TestReviewDepthReachesTheRealCaller:
    """nyxloom-P101 O5: the caller edit that drops the stale `tier=` kwarg
    (WI5) is proven end-to-end through the REAL `ReviewEffector.launch_review`
    handler, mirroring TestWaveLeaseUnion's full-path harness above.

    Both directions are asserted: a caller wired to a hardcoded list (e.g.
    `scope_touch=["a","b","c","d","e","f"]` instead of
    `first_fm.scope.touch`) would pass a one-direction "6 paths -> fires"
    check just as happily as the real caller -- the 1-path direction is what
    proves the frontmatter's REAL `scope.touch` is what reaches the
    directive, not a stand-in.
    """

    class _FM:
        tier = None
        source = None

        def __init__(self, touch):
            self.scope = type("S", (), {"touch": touch})()

        def effective_mutexes(self):
            return []

    def _launch_and_capture_review_depth(self, sample_project, monkeypatch, touch):
        from nyxloom import effects_dispatch, wrapper

        fm = self._FM(touch)
        monkeypatch.setattr(effects_dispatch, "frontmatter_for",
                            lambda c, tsf: fm if tsf else None)
        monkeypatch.setattr(wrapper, "launch_detached", lambda spec: 4242)

        captured: dict = {}

        def _capturing_build_dispatch(route, **kw):
            captured.update(kw)
            return (["fake-cli"], "prompt")

        monkeypatch.setattr(effects_review.adapters, "build_dispatch",
                            _capturing_build_dispatch)
        monkeypatch.setattr(effects_review.config.Routes, "load",
                            classmethod(lambda cls: _routes()))

        states = {"t-a": TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                       task_id="t-a", project="demo",
                                       state=TaskState.AWAITING_REVIEW,
                                       since=utc_now())}
        ports = effects.EffectPorts.system()
        effector = effects_review.ReviewEffector(ports)
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states, ports=ports)
        effector.launch_review(
            ctx, reconcile.LaunchReview(wave_id="w1", task_ids=["t-a"]))

        return captured["review_depth"]

    def test_scope_touch_size_drives_review_depth_through_the_real_caller(
            self, tmp_state, sample_project, monkeypatch):
        large = self._launch_and_capture_review_depth(
            sample_project, monkeypatch, [f"src/file{i}.py" for i in range(6)])
        assert "high-complexity" in large

        small = self._launch_and_capture_review_depth(
            sample_project, monkeypatch, ["src/one_file.py"])
        assert "high-complexity" not in small


def _routes():
    from nyxloom.config import RouteDef, Routes
    return Routes(
        revision="test-rev",
        tiers={"frontier-review": ["review-route"]},
        # CR-13a review: `trust` is REQUIRED, not defaulted -- a local fake
        # script is the operator's own. Without it this route requires
        # containment, `launch_review`'s admission gate refuses (no image is
        # configured in a test environment), no spec is ever built, and the
        # wave-lease oracle below fails for a reason that has nothing to do
        # with leases. CR-13a's own fixture-posture pass missed this one.
        routes={"review-route": RouteDef(route_id="review-route", cli="fake",
                                         model="review-model",
                                         trust="operator",
                                         role_default="review-independent")},
    )


class TestAReviewWithNoRouteRefusesRatherThanCrashing:
    """CR-13a review: the role-indexed twin of the vanished-route defect.

    Both review legs resolved their route with `for_role(...)[0]` and no
    guard, so a deployment with no review-independent route raised IndexError
    -- which run_pass's outer net turns into a TICK_ERROR that ends the whole
    pass. Same class as the dispatch KeyError, same fix: refuse the launch and
    let every other task in the pass proceed.

    `effects_carve` already had this discipline and said why ("routes.toml
    could change between planning and execution within the same pass. Never
    mint a synthetic task / worktree we cannot actually dispatch into"); the
    review legs did not inherit it.
    """

    def _no_review_routes(self):
        from nyxloom.config import Routes
        return Routes(revision="test-rev", tiers={}, routes={})

    def test_launch_review_refuses_when_no_route_serves_the_role(
            self, tmp_state, sample_project, monkeypatch):
        from nyxloom import wrapper
        monkeypatch.setattr(effects_review.config.Routes, "load",
                            classmethod(lambda cls: self._no_review_routes()))
        launched: list = []
        monkeypatch.setattr(wrapper, "launch_detached",
                            lambda spec: (launched.append(spec), 4242)[1])

        effector = effects_review.ReviewEffector(effects.EffectPorts.system())
        states = {"t-a": TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                       task_id="t-a", project="demo",
                                       state=TaskState.AWAITING_REVIEW,
                                       since=utc_now())}
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states,
                                    ports=effects.EffectPorts.system())
        try:
            events = effector.launch_review(
                ctx, reconcile.LaunchReview(wave_id="w1", task_ids=["t-a"]))
        except Exception as exc:                      # noqa: BLE001 -- the oracle
            pytest.fail(f"an unroutable review raised {exc!r} instead of refusing")

        assert events == []
        assert launched == [], "nothing may launch without a resolved route"
        assert states["t-a"].state is TaskState.AWAITING_REVIEW, (
            "a refused review must leave the task reviewable next pass")


def _uncontainable_review_routes():
    """A `review-independent` route that resolves but cannot be contained --
    no `trust = "operator"` declaration, so containment.requires_containment
    is True for it. Paired with NYXLOOM_CONTAINMENT_IMAGE left unset, this
    makes `admissible`'s containment half refuse deterministically without
    ever asking docker (see test_effects_dispatch.py's own
    TestAdmissionRefusesAnUncontainableRoute, which established the
    technique)."""
    from nyxloom.config import RouteDef, Routes
    return Routes(
        revision="test-rev",
        tiers={"frontier-review": ["review-route"]},
        routes={"review-route": RouteDef(route_id="review-route", cli="fake",
                                         model="review-model",
                                         role_default="review-independent")},
    )


class TestAGateDiagnosisRouteRefusesRatherThanCrashing:
    """CR-13a review: `launch_gate_diagnosis` resolves its route with
    `effects_dispatch.route_for_role` (never a bare `for_role(...)[0]`) and
    re-asks admission once that route is known -- the SAME two-part
    discipline `TestAReviewWithNoRouteRefusesRatherThanCrashing` covers for
    `launch_review` above. No prior test drove `launch_gate_diagnosis`
    itself far enough to reach either refusal.
    """

    def test_refuses_when_no_route_serves_the_review_independent_role(
            self, tmp_state, sample_project):
        """sample_project's own routes.toml (conftest.SAMPLE_ROUTES_TOML)
        declares no review-independent role/tier, so `route_for_role`
        returns None -- the role-indexed twin of the vanished route-id case,
        for this leg."""
        effector = effects_review.ReviewEffector(effects.EffectPorts.system())
        states = {"t1": TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                      task_id="t1", project="demo",
                                      state=TaskState.REVIEW_REJECTED,
                                      since=utc_now())}
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states,
                                    ports=effects.EffectPorts.system())

        try:
            events = effector.launch_gate_diagnosis(
                ctx, reconcile.LaunchGateDiagnosis(task_id="t1"))
        except Exception as exc:                      # noqa: BLE001 -- the oracle
            pytest.fail(f"an unroutable gate diagnosis raised {exc!r} instead of refusing")

        assert events == []
        assert states["t1"].state is TaskState.REVIEW_REJECTED, (
            "a refused diagnosis must leave the task retried next pass")

    def test_refuses_when_the_resolved_route_cannot_be_contained(
            self, tmp_state, sample_project, monkeypatch):
        """The SECOND admission check -- asked again once the role-resolved
        route is known, since containment is a property of the route and
        cannot be answered before `route_for_role` resolves it."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        monkeypatch.setattr(effects_review.config.Routes, "load",
                            classmethod(lambda cls: _uncontainable_review_routes()))
        effector = effects_review.ReviewEffector(effects.EffectPorts.system())
        states = {"t1": TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                      task_id="t1", project="demo",
                                      state=TaskState.REVIEW_REJECTED,
                                      since=utc_now())}
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states,
                                    ports=effects.EffectPorts.system())

        try:
            events = effector.launch_gate_diagnosis(
                ctx, reconcile.LaunchGateDiagnosis(task_id="t1"))
        except Exception as exc:                      # noqa: BLE001 -- the oracle
            pytest.fail(f"an uncontainable route raised {exc!r} instead of refusing")

        assert events == []
        assert states["t1"].state is TaskState.REVIEW_REJECTED


class TestLaunchReviewRefusesWhenItsResolvedRouteCannotBeContained:
    """CR-13a review: `launch_review`'s OWN second admission check --
    distinct from `TestAReviewWithNoRouteRefusesRatherThanCrashing` above,
    which covers the FIRST refusal (route_for_role returns None). This one
    is only reachable once a REAL route has resolved."""

    def test_launch_review_refuses_when_the_resolved_route_cannot_be_contained(
            self, tmp_state, sample_project, monkeypatch):
        from nyxloom import wrapper
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        monkeypatch.setattr(effects_review.config.Routes, "load",
                            classmethod(lambda cls: _uncontainable_review_routes()))
        launched: list = []
        monkeypatch.setattr(wrapper, "launch_detached",
                            lambda spec: (launched.append(spec), 4242)[1])

        effector = effects_review.ReviewEffector(effects.EffectPorts.system())
        states = {"t-a": TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                                       task_id="t-a", project="demo",
                                       state=TaskState.AWAITING_REVIEW,
                                       since=utc_now())}
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states,
                                    ports=effects.EffectPorts.system())
        try:
            events = effector.launch_review(
                ctx, reconcile.LaunchReview(wave_id="w1", task_ids=["t-a"]))
        except Exception as exc:                      # noqa: BLE001 -- the oracle
            pytest.fail(f"an uncontainable review route raised {exc!r} instead of refusing")

        assert events == []
        assert launched == [], "nothing may launch without contained admission"
        assert states["t-a"].state is TaskState.AWAITING_REVIEW, (
            "a refused review must leave the task reviewable next pass")

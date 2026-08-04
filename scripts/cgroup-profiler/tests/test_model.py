"""Tests for lib.model: the shared dataclasses and their to_dict/from_dict
round trip. This is the interface between the collector and the analyser —
they may be separated by a container boundary and by hours — so every type
must survive a JSON round trip exactly, and ``_clean`` must never leave a
stray ``None`` in the compact on-disk form.
"""

from __future__ import annotations

from lib.model import Analysis, Event, Mark, Phase, Proposal, Series, _clean


class TestClean:
    def test_drops_none_values_from_dict(self):
        assert _clean({"a": 1, "b": None, "c": 0}) == {"a": 1, "c": 0}

    def test_recurses_into_nested_dicts(self):
        assert _clean({"a": {"b": None, "c": 1}}) == {"a": {"c": 1}}

    def test_recurses_into_lists(self):
        # A mark's meta commonly carries a list (argv), and _clean must walk
        # into it rather than treating it as an opaque leaf.
        assert _clean({"a": [{"b": None, "c": 1}, {"d": 2}]}) == {"a": [{"c": 1}, {"d": 2}]}

    def test_passes_scalars_through_unchanged(self):
        assert _clean(5) == 5
        assert _clean("x") == "x"
        assert _clean(None) is None


class TestMark:
    def test_to_dict_round_trips_with_a_list_valued_meta(self):
        # cmd_run's wrapper mark carries meta={"argv": [...]}; that list must
        # survive _clean's recursion into a dict-and-list mix.
        mark = Mark(t=1.0, name="job-a", kind="phase", mono=2.5,
                    meta={"argv": ["ls", "-la"]}, source="wrapper")
        data = mark.to_dict()
        assert data == {
            "t": 1.0, "name": "job-a", "kind": "phase", "mono": 2.5,
            "meta": {"argv": ["ls", "-la"]}, "source": "wrapper",
        }
        back = Mark.from_dict(data)
        assert back == mark

    def test_to_dict_drops_none_mono(self):
        # emit_mark deliberately leaves mono unset; the on-disk record must
        # not carry a literal null for it.
        mark = Mark(t=1.0, name="startup")
        assert "mono" not in mark.to_dict()

    def test_from_dict_defaults_missing_optional_fields(self):
        mark = Mark.from_dict({"t": "3.5", "name": "x"})
        assert mark.kind == "phase"
        assert mark.mono is None
        assert mark.meta == {}
        assert mark.source == "mark"
        assert mark.t == 3.5

    def test_from_dict_meta_none_becomes_empty_dict(self):
        # A writer that explicitly wrote `"meta": null` must not crash a
        # reader that expects a dict to update.
        mark = Mark.from_dict({"t": 1.0, "name": "x", "meta": None})
        assert mark.meta == {}


class TestPhase:
    def test_duration_is_t1_minus_t0(self):
        phase = Phase(name="job-a", t0=10.0, t1=25.0)
        assert phase.duration == 15.0

    def test_duration_never_negative(self):
        # A phase whose boundaries were resolved out of order must not
        # report a negative span to a chart.
        phase = Phase(name="broken", t0=25.0, t1=10.0)
        assert phase.duration == 0.0

    def test_to_dict_from_dict_round_trip(self):
        phase = Phase(name="job-a", t0=1.0, t1=2.0, source="auto", meta={"n": 3})
        data = phase.to_dict()
        assert data == {"name": "job-a", "t0": 1.0, "t1": 2.0, "source": "auto", "meta": {"n": 3}}
        assert Phase.from_dict(data) == phase

    def test_from_dict_defaults(self):
        phase = Phase.from_dict({"name": "run", "t0": 0, "t1": 10})
        assert phase.source == "mark"
        assert phase.meta == {}

    def test_from_dict_meta_none_becomes_empty_dict(self):
        phase = Phase.from_dict({"name": "run", "t0": 0, "t1": 10, "meta": None})
        assert phase.meta == {}


class TestEvent:
    def test_to_dict_from_dict_round_trip(self):
        event = Event(t=1.0, mono=2.0, kind="oom_kill", severity="critical",
                      target="/dev.slice", message="OOM killed 1", data={"delta": 1})
        data = event.to_dict()
        assert data == {
            "t": 1.0, "mono": 2.0, "kind": "oom_kill", "severity": "critical",
            "target": "/dev.slice", "message": "OOM killed 1", "data": {"delta": 1},
        }
        assert Event.from_dict(data) == event

    def test_to_dict_drops_empty_data_key_is_kept_when_nonempty_and_dropped_when_falsy(self):
        # An empty dict is not None, so _clean must keep it rather than treat
        # "empty" and "absent" the same way it treats None.
        event = Event(t=1.0, mono=2.0, kind="cgroup_appeared", severity="info",
                      target="/x", message="x appeared")
        assert event.to_dict()["data"] == {}

    def test_from_dict_defaults_severity_and_data(self):
        event = Event.from_dict({"t": 1.0, "kind": "psi_spike", "target": "/x"})
        assert event.severity == "info"
        assert event.mono == 0.0
        assert event.message == ""
        assert event.data == {}

    def test_from_dict_data_none_becomes_empty_dict(self):
        event = Event.from_dict({"t": 1.0, "kind": "x", "data": None})
        assert event.data == {}


class TestSeries:
    def test_points_zips_t_and_v(self):
        series = Series(key="mem", target="a", label="a mem", unit="bytes",
                        group="memory", t=[0.0, 1.0], v=[100, None])
        assert series.points() == [(0.0, 100), (1.0, None)]

    def test_finite_drops_none(self):
        series = Series(key="mem", target="a", label="a", unit="bytes", group="memory",
                        t=[0.0, 1.0, 2.0], v=[1.0, None, 3.0])
        assert series.finite() == [1.0, 3.0]

    def test_finite_of_all_none_is_empty(self):
        series = Series(key="mem", target="a", label="a", unit="bytes", group="memory",
                        t=[0.0], v=[None])
        assert series.finite() == []

    def test_to_dict_from_dict_round_trip(self):
        series = Series(key="mem.current", target="gate", label="gate mem", unit="bytes",
                        group="memory", t=[0.0, 1.0], v=[1.0, None], role="observer",
                        meta={"note": "x"})
        data = series.to_dict()
        assert data == {
            "key": "mem.current", "target": "gate", "label": "gate mem", "unit": "bytes",
            "group": "memory", "role": "observer", "t": [0.0, 1.0], "v": [1.0, None],
            "meta": {"note": "x"},
        }
        assert Series.from_dict(data) == series

    def test_from_dict_defaults(self):
        series = Series.from_dict({"key": "k"})
        assert series.target == ""
        assert series.label == "k"
        assert series.unit == "count"
        assert series.group == "memory"
        assert series.t == []
        assert series.v == []
        assert series.role == "subject"
        assert series.meta == {}

    def test_from_dict_null_t_v_meta_become_empty(self):
        series = Series.from_dict({"key": "k", "t": None, "v": None, "meta": None})
        assert series.t == []
        assert series.v == []
        assert series.meta == {}


class TestProposal:
    def test_to_dict_from_dict_round_trip(self):
        proposal = Proposal(
            id="dev-slice-uncapped", severity="serious", title="t", rationale="r",
            evidence=["e1"], change=["c1"], confidence="observed",
        )
        data = proposal.to_dict()
        assert data == {
            "id": "dev-slice-uncapped", "severity": "serious", "title": "t",
            "rationale": "r", "evidence": ["e1"], "change": ["c1"], "confidence": "observed",
        }
        assert Proposal.from_dict(data) == proposal

    def test_from_dict_defaults(self):
        proposal = Proposal.from_dict({"id": "x", "title": "t"})
        assert proposal.severity == "info"
        assert proposal.rationale == ""
        assert proposal.evidence == []
        assert proposal.change == []
        assert proposal.confidence == "inferred"

    def test_from_dict_null_lists_become_empty(self):
        proposal = Proposal.from_dict({"id": "x", "title": "t", "evidence": None, "change": None})
        assert proposal.evidence == []
        assert proposal.change == []

    def test_to_dict_keeps_none_fields_unlike_the_other_types(self):
        # Proposal.to_dict uses plain asdict(), not _clean — so unlike Mark/
        # Phase/Event it is not expected to drop None. Pin that down so a
        # future "helpful" switch to _clean doesn't silently change the shape
        # every proposal in a report.json is serialised with.
        proposal = Proposal(id="x", severity="info", title="t", rationale="")
        assert proposal.to_dict()["rationale"] == ""


class TestAnalysis:
    def test_series_in_filters_by_group(self):
        a = Analysis(series=[
            Series(key="a", target="t", label="a", unit="bytes", group="memory"),
            Series(key="b", target="t", label="b", unit="pct", group="pressure"),
        ])
        assert [s.key for s in a.series_in("memory")] == ["a"]
        assert a.series_in("nonexistent") == []

    def test_groups_orders_by_panels_then_extras_sorted(self):
        a = Analysis(series=[
            Series(key="a", target="t", label="a", unit="bytes", group="io"),
            Series(key="b", target="t", label="b", unit="pct", group="memory"),
            Series(key="c", target="t", label="c", unit="count", group="zzz-custom"),
            Series(key="d", target="t", label="d", unit="count", group="aaa-custom"),
        ])
        # PANELS order is fixed (memory, swap, cpu, io, ...); only groups that
        # are actually present appear, in that fixed order, then any group
        # outside PANELS sorted alphabetically.
        assert a.groups() == ["memory", "io", "aaa-custom", "zzz-custom"]

    def test_groups_of_no_series_is_empty(self):
        assert Analysis().groups() == []

    def test_to_dict_from_dict_round_trip(self):
        a = Analysis(
            manifest={"run_id": "r1"},
            targets=[{"key": "gate"}],
            phases=[Phase(name="run", t0=0.0, t1=1.0)],
            events=[Event(t=1.0, mono=1.0, kind="k", severity="info", target="/x", message="m")],
            series=[Series(key="k", target="t", label="l", unit="bytes", group="memory")],
            per_phase=[{"phase": "run"}],
            per_target=[{"target": "gate"}],
            correlations=[{"r": 0.5}],
            proposals=[Proposal(id="p1", severity="info", title="t", rationale="r")],
            host={"MemTotal": 1},
            limits={"cg": {}},
            damon={"n": 1},
            notes=["note"],
        )
        data = a.to_dict()
        back = Analysis.from_dict(data)
        assert back.manifest == a.manifest
        assert back.targets == a.targets
        assert [p.name for p in back.phases] == ["run"]
        assert [e.kind for e in back.events] == ["k"]
        assert [s.key for s in back.series] == ["k"]
        assert back.per_phase == a.per_phase
        assert back.per_target == a.per_target
        assert back.correlations == a.correlations
        assert [p.id for p in back.proposals] == ["p1"]
        assert back.host == a.host
        assert back.limits == a.limits
        assert back.damon == a.damon
        assert back.notes == a.notes

    def test_from_dict_defaults_every_field_for_an_empty_object(self):
        a = Analysis.from_dict({})
        assert a.manifest == {}
        assert a.targets == []
        assert a.phases == []
        assert a.events == []
        assert a.series == []
        assert a.per_phase == []
        assert a.per_target == []
        assert a.correlations == []
        assert a.proposals == []
        assert a.host == {}
        assert a.limits == {}
        assert a.damon == {}
        assert a.notes == []

    def test_from_dict_null_fields_become_the_same_defaults(self):
        # A tool that round-trips through jq or similar may write explicit
        # nulls rather than omitting keys entirely; `or {}`/`or []` in
        # from_dict must handle both the same way.
        a = Analysis.from_dict({
            "manifest": None, "targets": None, "phases": None, "events": None,
            "series": None, "per_phase": None, "per_target": None,
            "correlations": None, "proposals": None, "host": None, "limits": None,
            "damon": None, "notes": None,
        })
        assert a.manifest == {}
        assert a.targets == []
        assert a.phases == []

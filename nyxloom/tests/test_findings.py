"""FN-1: system->user findings channel (option C)."""

from __future__ import annotations

import pytest

from nyxloom import findings, storage
from nyxloom.findings import FINDING_KINDS, Finding, FindingError, promote_seed_text, record_finding, load_findings
from nyxloom.types import Actor, ActorKind, EventType, utc_now


# --------------------------------------------------------------------------
# registry invariants

def test_registry_push_template_present_iff_pushable():
    """A pushable kind must carry a fixed template; a non-pushable one must
    not (that template is the SPEC-13-safe push body)."""
    for spec in FINDING_KINDS.values():
        assert (spec.push_template is not None) == spec.pushable
    assert FINDING_KINDS["generic"].pushable is False
    assert FINDING_KINDS["cost_crossover"].pushable is True
    assert FINDING_KINDS["model_near_equivalent"].pushable is True


# --------------------------------------------------------------------------
# record_finding — happy paths

def test_record_generic_finding_roundtrips(tmp_state):
    ev = record_finding("demo", "generic", title="deepseek-flash is cheap",
                        body="Observed ~1/10th cost on coding packages.")
    assert ev.type is EventType.FINDING_RECORDED
    assert ev.payload["kind"] == "generic"
    findings_out = load_findings("demo")
    assert len(findings_out) == 1
    f = findings_out[0]
    assert f.finding_id == f"F-demo-{ev.sequence}"
    assert f.kind == "generic"
    assert f.title == "deepseek-flash is cheap"
    assert f.body.startswith("Observed")
    assert f.pushable is False
    assert f.task_id is None


def test_record_pushable_finding_with_all_fields(tmp_state):
    ev = record_finding(
        "demo", "cost_crossover",
        title="flash matches pro on coding",
        fields={"cheap_model": "deepseek-flash", "ref_model": "gpt-pro",
                "metric": "swe-bench", "ratio": 0.1},
        severity="important", task_id="demo-P01",
    )
    f = load_findings("demo")[0]
    assert f.pushable is True
    assert f.severity == "important"
    assert f.task_id == "demo-P01"
    assert f.fields["cheap_model"] == "deepseek-flash"
    # the event carries the task link for B26 drilldown
    assert ev.task_id == "demo-P01"


def test_record_with_explicit_actor_and_timestamp(tmp_state):
    ts = utc_now()
    ev = record_finding("demo", "generic", title="tick-sourced",
                        actor=Actor(ActorKind.TICK, "reconcile"), now=ts)
    assert ev.actor.kind is ActorKind.TICK
    f = load_findings("demo")[0]
    assert f.created == ts.isoformat()


# --------------------------------------------------------------------------
# record_finding — validation failures

def test_unknown_kind_raises(tmp_state):
    with pytest.raises(FindingError, match="unknown finding kind"):
        record_finding("demo", "no_such_kind", title="x")


def test_unknown_severity_raises(tmp_state):
    with pytest.raises(FindingError, match="unknown severity"):
        record_finding("demo", "generic", title="x", severity="urgent")


def test_blank_title_raises(tmp_state):
    with pytest.raises(FindingError, match="non-empty"):
        record_finding("demo", "generic", title="   ")


def test_pushable_missing_required_field_raises(tmp_state):
    with pytest.raises(FindingError, match="missing required field"):
        record_finding("demo", "cost_crossover", title="incomplete",
                       fields={"cheap_model": "x"})  # ref_model/metric/ratio absent


# --------------------------------------------------------------------------
# load_findings — ordering & filtering

def test_load_findings_newest_first(tmp_state):
    record_finding("demo", "generic", title="first")
    record_finding("demo", "generic", title="second")
    record_finding("demo", "generic", title="third")
    titles = [f.title for f in load_findings("demo")]
    assert titles == ["third", "second", "first"]


def test_load_findings_ignores_non_finding_events(tmp_state):
    # a non-finding event in the same log must be excluded
    storage.append_event("demo", actor=Actor(ActorKind.TICK, "t"),
                         type=EventType.ARTIFACT_REGISTERED, payload={"x": 1})
    record_finding("demo", "generic", title="only me")
    out = load_findings("demo")
    assert len(out) == 1
    assert out[0].title == "only me"


def test_load_findings_empty_project(tmp_state):
    record_finding("demo", "generic", title="present")
    assert load_findings("other-empty") == []


# --------------------------------------------------------------------------
# Finding.pushable defensive path

def test_finding_pushable_false_for_unregistered_kind():
    f = Finding(finding_id="F-x-1", sequence=1, created="2026-07-24T00:00:00+00:00",
                project="x", kind="dropped_kind", title="t", body="")
    assert f.pushable is False


# --------------------------------------------------------------------------
# FN-6 promote_seed_text

def test_promote_seed_text_full_finding():
    """Build seed from a Finding with body, fields, and task_id."""
    f = Finding(
        finding_id="F-demo-42", sequence=42, created="2026-07-24T00:00:00+00:00",
        project="demo", kind="generic", title="test finding",
        body="observed something interesting",
        fields={"score": 0.95, "model": "deepseek"},
        task_id="demo-P01",
    )
    seed = promote_seed_text(f)
    assert f.finding_id in seed
    assert f.kind in seed
    assert f.title in seed
    assert "score=0.95" in seed
    assert "model=deepseek" in seed
    assert f.task_id in seed


def test_promote_seed_text_minimal_finding():
    """Build seed from a bare Finding with no optional fields."""
    f = Finding(
        finding_id="F-demo-1", sequence=1, created="2026-07-24T00:00:00+00:00",
        project="demo", kind="generic", title="minimal", body="",
    )
    seed = promote_seed_text(f)
    assert "Finding F-demo-1" in seed
    assert "[generic]" in seed
    assert "minimal" in seed
    # optional branches skipped: no body, fields, task_id
    # the seed is just two lines (header + finding line)
    assert seed.count("\n") == 1

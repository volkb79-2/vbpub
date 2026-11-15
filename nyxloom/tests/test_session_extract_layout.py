"""The readback LAYOUT: goal anchor + recent prose tail + delta (operator
decision 2026-11-15).

Pinned here: the three parts and their order, the anchor's exclusion of
harness framing and slash-command wrappers, `--goal-file`, the tail's
newest-first complete records from BEFORE the cut, the no-duplication property
across two consecutive read-backs, the truncation order under a budget
(the tail shrinks first, then the delta, the anchor never), the end-of-document
marker line, and redaction of the anchor text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import extract, layout, presets, select
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.config import ExtractConfig

FIX = Path(__file__).parent / "fixtures" / "readback"
LAYOUT = FIX / "layout.jsonl"


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _layout_config(**kw):
    base = dict(
        budget_tokens=30_000, agent_comms="off", goal_prompt=True,
        tail_prose_tokens=10_000, show_timestamps="none", gap_marker_mode="none",
        tool_calls="intent",
    )
    base.update(kw)
    return ExtractConfig(**base)


def _render(**kw):
    return extract(LAYOUT, _layout_config(**kw)).render()


# --- goal anchor -------------------------------------------------------------

def test_goal_anchor_is_the_first_genuine_prompt_without_harness_framing():
    out = _render()
    assert layout.GOAL_HEADER in out
    assert "OPERATOR: Run the whole controller job to the end" in out
    # The system-reminder block inside the same record is NOT the goal.
    assert "Harness reminder: this is injected framing" not in out


def test_slash_command_wrappers_are_never_the_goal(tmp_path):
    records = [
        {"isSidechain": False, "sessionId": "s", "parentUuid": None, "cwd": "/w",
         "type": "user", "uuid": "x0", "timestamp": "2026-11-15T09:00:00Z",
         "message": {"role": "user",
                     "content": "<command-name>/loop</command-name> check the queue"}},
        {"isSidechain": False, "sessionId": "s", "parentUuid": "x0", "cwd": "/w",
         "type": "user", "uuid": "x1", "timestamp": "2026-11-15T09:01:00Z",
         "message": {"role": "user", "content": "The real goal is the gate report."}},
    ]
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    result = extract(source, _layout_config())
    assert "OPERATOR: The real goal is the gate report." in result.render()
    command_event = next(ev for ev in result.all_events if ev.marker == "x0")
    assert command_event.meta["slash_command"] == "loop"
    assert layout.goal_candidate(result.all_events)[0].marker == "x1"


def test_goal_file_pins_the_anchor_and_a_missing_goal_is_stated(tmp_path, capsys):
    goal = tmp_path / "goal.md"
    goal.write_text("Hold the release line until R2 reports.\n", encoding="utf-8")
    code, out, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--goal-file", goal,
        "--budget-tokens", "30000", "--no-color",
    )
    assert code == 0, err
    assert "OPERATOR: Hold the release line until R2 reports." in out
    assert "goal=file" in out

    with_goal = layout.build(
        full_events=[], tail_region=[], config=_layout_config(), last_marker="m",
    )
    assert "goal=none" in with_goal.footer_lines[0]
    assert layout.GOAL_MISSING in with_goal.sections[0]

    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        extract(LAYOUT, _layout_config(goal_file=str(empty)))


def test_goal_anchor_is_redacted_like_every_kept_paragraph():
    out = _render(redact_patterns=("Run the whole controller job",))
    assert "OPERATOR: [redacted paragraph -- matched --redact-pattern" in out
    assert "Run the whole controller job to the end" not in out


# --- recent prose tail -------------------------------------------------------

def test_tail_keeps_the_newest_prose_before_the_cut_as_complete_records():
    out = _render(since_marker="g6", tail_prose_tokens=100)
    tail_start = out.index("## Recent prose before the cut")
    tail_end = out.index("## Delta since the previous read-back")
    tail = out[tail_start:tail_end]
    assert "This is the newest prose BEFORE the cut and belongs in the recent-prose tail." in tail
    assert "This middle prose sits between the goal anchor and the recent tail" in tail
    assert "Second half: the layout is now the rule" not in tail
    # A 100-token tail cannot also carry the whole older prose.
    result = extract(LAYOUT, _layout_config(since_marker="g6", tail_prose_tokens=20))
    assert result._readback_layout.tail.kept == 1
    assert result._readback_layout.tail.dropped >= 1
    # Under pressure the NEWEST record survives, never the oldest.
    squeezed = result.render()
    assert "This is the newest prose BEFORE the cut" in squeezed
    assert "This middle prose sits between the goal anchor" not in squeezed


def test_tail_yields_to_the_protected_operator_material_of_the_delta(tmp_path):
    """Truncation order: the tail shrinks BEFORE the delta, and the delta's
    operator turns are never dropped, so a large operator turn after the cut
    must squeeze the tail rather than let the document overshoot the budget
    by a whole tail (review finding)."""
    records = []
    previous = None

    def add(kind, uuid, text):
        nonlocal previous
        body = (
            {"role": "user", "content": text} if kind == "user"
            else {"role": "assistant", "id": "m" + uuid,
                  "content": [{"type": "text", "text": text}]}
        )
        records.append({
            "isSidechain": False, "sessionId": "s", "parentUuid": previous, "cwd": "/w",
            "type": kind, "uuid": uuid,
            "timestamp": f"2026-11-15T09:{len(records):02d}:00Z", "message": body,
        })
        previous = uuid

    for index in range(30):
        add("assistant", f"p{index}", f"PRIOR{index} " + "word " * 80)
    add("user", "cut", "the cut record")
    add("user", "big", "OPERATORBIG " + "instruction " * 300)
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    config = _layout_config(since_marker="cut", goal_prompt=False, budget_tokens=1800)
    result = extract(source, config)
    plan = result._readback_layout
    protected = select.protected_readback_tokens(
        [ev for ev in result.all_events if ev.marker == "big"], config, set(),
    )
    assert protected > 700
    out = result.render()
    assert "OPERATORBIG" in out                     # never dropped
    assert plan.tail.kept_tokens <= max(0, 1800 - 512 - protected)
    assert select.estimate_tokens(out) <= 1800      # the whole document fits
    # With the protected turn absent the same budget keeps a real tail.
    short = tmp_path / "short.jsonl"
    short.write_text(
        "\n".join(json.dumps(r) for r in records[:-1]) + "\n", encoding="utf-8",
    )
    assert extract(short, config)._readback_layout.tail.kept_tokens > plan.tail.kept_tokens


def test_redact_pattern_also_hides_the_tail_prose_and_is_counted(capsys):
    """The tail is rendered before the delta's own redaction step, so it must
    apply --redact-pattern itself (review finding: it used to leak what the
    delta hid)."""
    pattern = "newest prose BEFORE"
    result = extract(
        LAYOUT, _layout_config(since_marker="g6", redact_patterns=(pattern,)),
    )
    out = result.render()
    tail = out[out.index("## Recent prose before the cut"):out.index(layout.DELTA_HEADER)]
    assert "belongs in the recent-prose tail" not in out
    assert "[redacted paragraph -- matched --redact-pattern" in tail
    # The unmatched tail record survives, and the count includes the tail.
    assert "This middle prose sits between the goal anchor and the recent tail" in tail
    assert result.redacted_paragraphs >= 1

    code, cli_out, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--since", "g6",
        "--redact-pattern", pattern, "--no-color",
    )
    assert code == 0
    assert "belongs in the recent-prose tail" not in cli_out
    assert "redacted 1 paragraph(s)" in err


def test_the_preset_omits_the_anchor_unless_asked(capsys):
    """A controller session's anchor is its plan file (AGENTS.md), so the
    shipped preset must not prepend the first prompt; a spawned/targeted
    session opts in."""
    code, out, err = _run(capsys, LAYOUT, "--preset", "readback",
                          "--budget-tokens", "30000", "--no-color")
    assert code == 0, err
    assert layout.GOAL_HEADER not in out
    assert "goal=none" in out
    assert "## Recent prose before the cut" in out
    code, out, err = _run(capsys, LAYOUT, "--preset", "readback", "--goal-prompt",
                          "--budget-tokens", "30000", "--no-color")
    assert code == 0, err
    assert layout.GOAL_HEADER in out
    assert "goal=session" in out


def test_tail_is_empty_without_a_cut_and_says_why():
    out = _render()
    assert "[readback tail: no cut was given" in out
    assert "[no user/assistant prose record exists before the cut" in out


def test_tail_never_carries_tool_output():
    out = _render(since_marker="g6")
    tail_start = out.index("## Recent prose before the cut")
    tail_end = out.index("## Delta since the previous read-back")
    tail = out[tail_start:tail_end]
    for tool_text in ("tool call", "List the repository root", "/work/repo/plan.yaml"):
        assert tool_text not in tail


# --- no duplication across two consecutive read-backs ------------------------

def test_two_consecutive_readbacks_never_repeat_the_previous_one(tmp_path, capsys):
    code, first, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--budget-tokens", "30000",
        "--since", "g6", "--no-color",
    )
    assert code == 0, err
    assert "[readback marker: h2]" in first
    marker = first.split("[readback marker: ")[1].split("]")[0]
    code, second, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--budget-tokens", "30000",
        "--since", marker, "--no-color",
    )
    assert code == 0, err
    # The delta of the second read-back repeats NOTHING from the first one's
    # delta. (This run asks for the goal anchor, so the anchor is the one
    # deliberate repetition.)
    for record in (
        "Second half: the layout is now the rule",
        "The delta for this read-back starts here and must not repeat the first half.",
    ):
        assert first.count(record) == 1
        assert record not in second.split("## Delta since the previous read-back")[1]
        assert second.count(record) <= 1


def test_since_file_reads_the_previous_readbacks_marker(tmp_path, capsys):
    code, first, err = _run(capsys, LAYOUT, "--preset", "readback",
                            "--budget-tokens", "30000", "--since", "g6", "--no-color")
    assert code == 0, err
    saved = tmp_path / "readback-1.md"
    saved.write_text(first, encoding="utf-8")
    code, second, err = _run(capsys, LAYOUT, "--preset", "readback",
                             "--budget-tokens", "30000", "--since-file", saved,
                             "--no-color")
    assert code == 0, err
    assert "## Delta since the previous read-back (strictly after marker h2)" in second


# --- budget: the whole document, in the documented truncation order ----------

def test_output_never_exceeds_the_budget_above_the_anchor_floor():
    for budget in (800, 1_500, 3_000, 10_000, 30_000):
        out = _render(since_marker="g6", budget_tokens=budget)
        assert select.estimate_tokens(out) <= budget, (budget, select.estimate_tokens(out))


def test_below_the_anchor_floor_the_anchor_survives_and_the_overrun_is_named():
    out = _render(since_marker="g6", budget_tokens=100)
    assert "OPERATOR: Run the whole controller job to the end" in out
    assert "[readback warning: anchor plus fixed scaffolding exceed the limit" in out
    # Nothing budgetable survives: the protected operator turn is still emitted
    # in full (it is never budget-dropped), and every lower-priority record is
    # gone.
    since_delta = out.split("## Delta since the previous read-back")[1]
    assert "The delta for this read-back starts here" not in since_delta


def test_tail_shrinks_first_then_the_delta_while_the_anchor_never_changes():
    full = _render(since_marker="g6", budget_tokens=30_000)
    tight = _render(since_marker="g6", budget_tokens=1_100)
    anchor_line = "OPERATOR: Run the whole controller job to the end and keep the state files current."
    assert anchor_line in full and anchor_line in tight
    assert full.count(anchor_line) == 1 and tight.count(anchor_line) == 1
    full_tail = layout.build(
        full_events=extract(LAYOUT, _layout_config()).all_events, tail_region=[],
        config=_layout_config(), last_marker="h2",
    )
    assert full_tail.goal_source == "session"
    tight_result = extract(LAYOUT, _layout_config(since_marker="g6", budget_tokens=1_100))
    assert tight_result._readback_layout.tail.kept_tokens <= (
        extract(LAYOUT, _layout_config(since_marker="g6"))
        ._readback_layout.tail.kept_tokens
    )
    assert tight_result.events  # the delta still has its protected operator turn


def test_estimate_and_marker_line_end_the_document():
    out = _render(since_marker="g6")
    body_lines = [line for line in out.splitlines() if line.strip()]
    marker_lines = [line for line in body_lines if line.startswith("[readback marker:")]
    assert marker_lines == ["[readback marker: h2]"]
    tail_after_marker = body_lines[body_lines.index(marker_lines[0]) + 1:]
    assert all(line.startswith("<!-- nyxloom-extract:") for line in tail_after_marker)
    assert out.index("[readback layout: goal=session") < out.index("[readback marker: h2]")


# --- configuration and CLI contract ------------------------------------------

def test_layout_is_only_available_through_the_readback_preset(capsys):
    code, out, err = _run(capsys, LAYOUT, "--preset", "readback",
                          "--goal-prompt", "--no-color")
    assert code == 0, err
    assert layout.GOAL_HEADER in out
    # The shipped preset turns the layout (recent prose) on, but NOT the goal
    # anchor: a controller session's anchor is its plan file (AGENTS.md), so
    # only a spawned/targeted session asks for --goal-prompt/--goal-file.
    assert presets.effective(_args(preset="readback"), "goal_prompt") is None
    assert layout.enabled(_layout_config())
    assert not layout.enabled(ExtractConfig(budget_tokens=30000))


def _args(**kw):
    class _Args:
        preset = None
        goal_prompt = None
        goal_file = None
        tail_prose_tokens = None

        def __getattr__(self, name):
            return None

    holder = _Args()
    holder.__dict__.update(kw)
    return holder


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("--goal-prompt",), "require --preset readback"),
        (("--goal-file", "goal.md"), "require --preset readback"),
        (("--tail-prose-tokens", "100"), "require --preset readback"),
        (("--preset", "readback", "--tail-prose-tokens", "-1"), "non-negative"),
    ],
)
def test_layout_cli_refusals(capsys, argv, message):
    code, _, err = _run(capsys, LAYOUT, *argv)
    assert code == 2
    assert message in err


def test_config_rejects_a_negative_tail_and_an_empty_goal_file_path():
    with pytest.raises(ValueError):
        ExtractConfig(tail_prose_tokens=-1)
    with pytest.raises(ValueError):
        ExtractConfig(goal_file="   ")


# --- the layout helpers' own edge branches ----------------------------------

def _event(seq, kind, text, *, marker=None, timestamp="", meta=None):
    return NormalizedEvent(
        seq, marker or f"m{seq}", timestamp, kind, text, meta=meta or {},
    )


def test_goal_candidate_skips_records_that_are_not_operator_intent():
    events = [
        _event(1, EventKind.ASSISTANT_TEXT, "an assistant message comes first"),
        _event(2, EventKind.TOOL_CALL, "a tool call"),
        _event(3, EventKind.OPERATOR_TEXT, "check the queue", meta={"slash_command": "loop"}),
        _event(4, EventKind.OPERATOR_TEXT, "<system-reminder>framing only</system-reminder>"),
        _event(5, EventKind.OPERATOR_TEXT, "The real goal."),
    ]
    found = layout.goal_candidate(events)
    assert found is not None
    assert found[0].marker == "m5" and found[1] == "The real goal."
    # A session with no operator intent at all has no anchor.
    assert layout.goal_candidate(events[:2]) is None


def test_prose_events_keeps_operator_and_qa_prose_and_drops_noise():
    events = [
        _event(1, EventKind.OPERATOR_TEXT, "operator"),
        _event(2, EventKind.QA_PAIR, "interview"),
        _event(3, EventKind.ASSISTANT_TEXT, "assistant"),
        _event(4, EventKind.TOOL_CALL, "a tool call"),
        _event(5, EventKind.ASSISTANT_TEXT, "[API ERROR: overloaded_error]"),
    ]
    shown = layout.prose_events(events, _layout_config())
    assert [ev.text for ev in shown] == ["operator", "interview", "assistant"]
    # hide_api_errors=False keeps the transport noise (the delta honours the
    # same switch, so the tail must too).
    assert len(layout.prose_events(events, _layout_config(hide_api_errors=False))) == 4


def test_prose_before_time_complements_the_time_cut_exactly():
    events = [
        _event(1, EventKind.ASSISTANT_TEXT, "before", timestamp="2026-11-15T09:00:00Z"),
        _event(2, EventKind.ASSISTANT_TEXT, "at the cut", timestamp="2026-11-15T09:05:00Z"),
        _event(3, EventKind.ASSISTANT_TEXT, "after", timestamp="2026-11-15T09:10:00Z"),
        _event(4, EventKind.ASSISTANT_TEXT, "undated"),
    ]
    # Strictly after the stamp goes in the delta; the stamp itself does NOT
    # (filter_since_time is exclusive by default), so the tail keeps it -- a
    # record is carried by exactly one of the two sections.
    strict = layout.prose_before_time(
        events, _layout_config(since_time="2026-11-15T09:05:00Z"),
    )
    assert [ev.text for ev in strict] == ["before", "at the cut"]
    # With --since-time-inclusive the stamp belongs to the DELTA, so the
    # exact complement drops it from the tail.
    inclusive = layout.prose_before_time(
        events,
        _layout_config(since_time="2026-11-15T09:05:00Z", since_time_inclusive=True),
    )
    assert [ev.text for ev in inclusive] == ["before"]


def test_build_requires_a_budget_and_skips_the_tail_when_it_is_zero():
    with pytest.raises(ValueError, match="--budget-tokens"):
        layout.build(
            full_events=[], tail_region=[], config=ExtractConfig(), last_marker=None,
        )
    plan = layout.build(
        full_events=[], tail_region=[],
        config=_layout_config(tail_prose_tokens=0, goal_prompt=False),
        last_marker="abc",
    )
    assert plan.tail is None
    assert plan.footer_lines[-1] == "[readback marker: abc]"
    assert all(layout.GOAL_HEADER not in section for section in plan.sections)


def test_cut_header_names_the_cut_in_force():
    assert layout.cut_header(_layout_config(since_marker="abc")).endswith(
        "(strictly after marker abc)"
    )
    assert layout.cut_header(_layout_config(since_time="2026-11-15T09:05:00Z")).endswith(
        "(after 2026-11-15T09:05:00Z)"
    )
    assert "no previous read-back marker was given" in layout.cut_header(_layout_config())


def test_readback_without_the_layout_uses_the_plain_selection(capsys):
    """--tail-prose-tokens 0 turns the layout OFF, so the run keeps the
    pre-layout selection path (no anchor, no tail, no marker line)."""
    code, out, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--tail-prose-tokens", "0",
        "--budget-tokens", "30000", "--no-color",
    )
    assert code == 0, err
    assert layout.GOAL_HEADER not in out
    assert "## Recent prose before the cut" not in out
    assert "[readback marker:" not in out
    assert "[readback budget: limit=30000" in out


def test_cut_index_resolves_the_marker_and_reports_absence():
    result = extract(LAYOUT, _layout_config())
    markers = [(ev.marker, ev.seq) for ev in result.all_events]
    assert layout.cut_index(result.all_events, "g6") == dict(markers)["g6"]
    assert layout.cut_index(result.all_events, "no-such-marker") is None

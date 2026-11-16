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
        full_events=[], delta_events=[], config=_layout_config(), last_marker="m",
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

TAIL = "## Recent prose"
NEWEST = "The delta for this read-back starts here and must not repeat the first half."
SECOND = "Second half: the layout is now the rule"
MIDDLE = "This middle prose sits between the goal anchor and the recent tail"
PRE_CUT = "This is the newest prose BEFORE the cut"


def _sections(out):
    """(tail section, delta section) of one rendered readback."""
    tail_start = out.index(TAIL)
    delta_start = out.index(layout.DELTA_HEADER)
    assert tail_start < delta_start
    return out[tail_start:delta_start], out[delta_start:]


def test_tail_is_the_newest_prose_of_the_session_end_not_prose_before_the_marker():
    """Operator ruling 2026-11-15: the tail is the newest prose at the moment the
    readback is built (the END of the transcript), limited to what is after the
    previous marker."""
    out = _render(since_marker="g3", tail_prose_tokens=30)
    tail, delta = _sections(out)
    assert NEWEST in tail                      # the very last prose record
    assert SECOND not in tail and MIDDLE not in tail
    # The records the tail does not carry are in the delta, once; the tail's
    # own record is NOT repeated there.
    assert SECOND in delta and MIDDLE in delta and PRE_CUT in delta
    assert NEWEST not in delta
    # A wider tail takes the next-newest record too (newest-first, complete).
    out = _render(since_marker="g3", tail_prose_tokens=50)
    tail, delta = _sections(out)
    assert NEWEST in tail and SECOND in tail and MIDDLE not in tail
    assert SECOND not in delta and NEWEST not in delta
    # Prose BEFORE the marker is never in the tail (it belongs to the previous
    # readback): with the marker at g6 only post-marker prose can be there.
    out = _render(since_marker="g6", tail_prose_tokens=10_000)
    assert MIDDLE not in out and PRE_CUT not in out


def test_tail_squeezed_by_the_budget_keeps_the_newest_record():
    result = extract(LAYOUT, _layout_config(since_marker="g3", tail_prose_tokens=30))
    assert result._readback_layout.tail.kept == 1
    assert result._readback_layout.tail.dropped >= 1
    out = result.render()
    assert NEWEST in out and SECOND in out
    assert out.count(NEWEST) == 1 and out.count(SECOND) == 1


def test_no_record_is_emitted_twice_inside_one_readback():
    for tail_tokens in (0, 15, 30, 50, 100, 10_000):
        for marker in (None, "g1", "g3", "g6"):
            kw = {} if marker is None else {"since_marker": marker}
            out = _render(tail_prose_tokens=tail_tokens, goal_prompt=False, **kw)
            for text in (NEWEST, SECOND, MIDDLE, PRE_CUT):
                assert out.count(text) <= 1, (tail_tokens, marker, text)


def test_a_tail_that_covers_the_whole_delta_says_so():
    out = _render(since_marker="g6", tail_prose_tokens=10_000)
    tail, delta = _sections(out)
    assert SECOND in tail and NEWEST in tail
    assert layout.DELTA_COVERED_NOTE in delta
    # With the tail disabled the same cut has a real delta and no note.
    plain = _render(since_marker="g6", tail_prose_tokens=0)
    assert layout.DELTA_COVERED_NOTE not in plain
    # Nothing after the marker at all: the delta is empty, not "covered".
    empty = _render(since_marker="h2", tail_prose_tokens=10_000)
    assert layout.DELTA_EMPTY_NOTE in empty
    assert layout.TAIL_EMPTY_NOTE in empty
    assert "[readback marker: none]" in empty


def test_first_readback_has_the_tail_plus_the_session_before_it():
    """No marker: tail = newest prose of the session, delta = the part before
    it, bounded by the budget; the marker at the end is the end of the session."""
    out = _render(tail_prose_tokens=30, goal_prompt=False)
    tail, delta = _sections(out)
    assert NEWEST in tail
    assert "no previous read-back marker was given" in delta
    for earlier in (SECOND, MIDDLE, PRE_CUT, "Before you compact"):
        assert earlier in delta
    assert NEWEST not in delta
    assert "[readback marker: h2]" in out
    # A tight budget drops the tail first, then the delta's droppable classes,
    # never the protected operator turns.
    tight = _render(tail_prose_tokens=10_000, goal_prompt=False, budget_tokens=900)
    assert "Before you compact" in tight and SECOND in tight
    assert select.estimate_tokens(tight) <= 900


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

    add("user", "cut", "the cut record")
    add("user", "big", "OPERATORBIG " + "instruction " * 300)
    for index in range(30):
        add("assistant", f"p{index}", f"PRIOR{index} " + "word " * 80)
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
        "\n".join(json.dumps(r) for r in records if r["uuid"] != "big") + "\n",
        encoding="utf-8",
    )
    assert extract(short, config)._readback_layout.tail.kept_tokens > plan.tail.kept_tokens


def test_redact_pattern_also_hides_the_tail_prose_and_is_counted(capsys):
    """The tail is rendered before the delta's own redaction step, so it must
    apply --redact-pattern itself (review finding: it used to leak what the
    delta hid)."""
    pattern = "must not repeat"
    result = extract(
        LAYOUT, _layout_config(since_marker="g3", redact_patterns=(pattern,),
                               tail_prose_tokens=50),
    )
    out = result.render()
    tail, delta = _sections(out)
    assert "starts here" not in out
    assert "[redacted paragraph -- matched --redact-pattern" in tail
    # The unmatched tail record survives, and the count includes the tail.
    assert SECOND in tail
    assert result.redacted_paragraphs >= 1

    code, cli_out, err = _run(
        capsys, LAYOUT, "--preset", "readback", "--since", "g3",
        "--redact-pattern", pattern, "--no-color",
    )
    assert code == 0
    assert "starts here" not in cli_out
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
    assert TAIL in out
    code, out, err = _run(capsys, LAYOUT, "--preset", "readback", "--goal-prompt",
                          "--budget-tokens", "30000", "--no-color")
    assert code == 0, err
    assert layout.GOAL_HEADER in out
    assert "goal=session" in out


def test_the_tail_is_empty_when_there_is_no_prose_after_the_cut():
    out = _render(since_marker="h2")
    assert layout.TAIL_EMPTY_NOTE in out


def test_tail_never_carries_tool_output():
    out = _render(since_marker="g3", tail_prose_tokens=10_000)
    tail, _ = _sections(out)
    for tool_text in ("tool call", "List the repository root", "/work/repo/plan.yaml"):
        assert tool_text not in tail


# --- no duplication across two consecutive read-backs ------------------------

def test_two_consecutive_readbacks_never_repeat_the_previous_one(tmp_path, capsys):
    """The session grows between two read-backs (the real use): readback 2 is
    built from the same file after more records were appended, with readback
    1's marker."""
    lines = LAYOUT.read_text(encoding="utf-8").splitlines()
    grown = tmp_path / "session.jsonl"
    grown.write_text("\n".join(lines[:7]) + "\n", encoding="utf-8")   # g0..g6
    code, first, err = _run(
        capsys, grown, "--preset", "readback", "--budget-tokens", "30000", "--no-color",
    )
    assert code == 0, err
    assert "[readback marker: g6]" in first
    assert PRE_CUT in first and MIDDLE in first       # first readback: tail + the part before
    grown.write_text("\n".join(lines) + "\n", encoding="utf-8")        # + h1, h2
    code, second, err = _run(
        capsys, grown, "--preset", "readback", "--budget-tokens", "30000",
        "--since", "g6", "--no-color",
    )
    assert code == 0, err
    assert "[readback marker: h2]" in second
    # The second read-back carries ONLY what happened after the first one.
    for record in (PRE_CUT, MIDDLE, "Before you compact"):
        assert record not in second
    for record in (SECOND, NEWEST):
        assert second.count(record) == 1 and record not in first
    # ... and each is the exact complement of the other, so together nothing is
    # emitted twice and nothing is lost.
    for record in (PRE_CUT, MIDDLE, SECOND, NEWEST):
        assert (record in first) != (record in second)


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
    anchor_line = "OPERATOR: Run the whole controller job to the end and keep the state files current."
    full_result = extract(LAYOUT, _layout_config(since_marker="g1"))
    tight_result = extract(LAYOUT, _layout_config(since_marker="g1", budget_tokens=800))
    full, tight = full_result.render(), tight_result.render()
    assert anchor_line in full and anchor_line in tight
    assert full.count(anchor_line) == 1 and tight.count(anchor_line) == 1
    assert full_result._readback_layout.goal_source == "session"
    # Order: the tail loses records first ...
    full_plan, tight_plan = full_result._readback_layout, tight_result._readback_layout
    assert tight_plan.tail.kept_tokens < full_plan.tail.kept_tokens
    # ... and the delta's protected operator turns survive the squeeze.
    assert "Before you compact" in tight and SECOND in tight
    assert select.estimate_tokens(tight) <= 800
    # A budget with room for the whole tail leaves the tail whole; below it the
    # delta's droppable material goes (limit 0) before any protected turn.
    roomy = extract(LAYOUT, _layout_config(since_marker="g1", budget_tokens=900))
    assert roomy._readback_layout.tail.kept_tokens == full_plan.tail.kept_tokens
    assert select.estimate_tokens(roomy.render()) <= 900
    assert tight_plan.tail.kept < full_plan.tail.kept
    assert extract(LAYOUT, _layout_config(since_marker="g1", budget_tokens=700)
                   )._readback_layout.tail.kept == 0


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


def test_a_time_cut_takes_the_tail_from_the_records_after_the_cut():
    # since_time narrows the delta region exactly like --since marker does, and
    # the tail comes from that region only.
    out = _render(since_time="2026-11-15T09:10:00Z", tail_prose_tokens=10_000,
                  goal_prompt=False)
    tail, delta = _sections(out)
    assert SECOND in tail and NEWEST in tail
    assert MIDDLE not in out and PRE_CUT not in out
    assert "(after 2026-11-15T09:10:00Z)" in delta


def test_build_requires_a_budget_and_skips_the_tail_when_it_is_zero():
    with pytest.raises(ValueError, match="--budget-tokens"):
        layout.build(
            full_events=[], delta_events=[], config=ExtractConfig(), last_marker=None,
        )
    plan = layout.build(
        full_events=[], delta_events=[],
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
    assert TAIL not in out
    assert "[readback marker:" not in out
    assert "[readback budget: limit=30000" in out


def test_a_goal_only_run_still_builds_the_layout_without_a_tail():
    out = _render(tail_prose_tokens=0, since_marker="g3")
    assert layout.GOAL_HEADER in out and TAIL not in out
    assert "[readback marker: h2]" in out


def test_the_goal_prompt_is_found_even_when_it_precedes_the_cut():
    """The anchor needs the whole session; the tail and delta do not."""
    out = _render(since_marker="g3", tail_prose_tokens=30)
    assert "OPERATOR: Run the whole controller job to the end" in out
    tail, delta = _sections(out)
    assert "Run the whole controller job" not in tail + delta


# --- the STATE section (operator decision 2026-11-16) -------------------------

STATE_BODY = (
    "## Done\n- Synthetic item one landed.\n\n"
    "## Decisions\n- Synthetic decision because it is cheaper.\n\n"
    "## Next\n- Synthetic next step.\n\n"
    "## Open\n- Synthetic open question."
)


def _state_file(tmp_path, body=STATE_BODY, name="state.md"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_the_state_section_sits_after_the_anchor_and_before_the_recent_prose(tmp_path):
    state = _state_file(tmp_path)
    out = _render(state_file=str(state))
    assert layout.STATE_HEADER in out
    assert f"[state file: {state}]" in out
    assert STATE_BODY.strip() in out                      # VERBATIM, not summarized
    assert out.index(layout.GOAL_HEADER) < out.index(layout.STATE_HEADER)
    assert out.index(layout.STATE_HEADER) < out.index(TAIL)
    assert "state=file" in out and "state_tokens=" in out


def test_the_state_section_is_opt_in_and_absent_by_default(tmp_path):
    assert layout.STATE_HEADER not in _render()
    assert "state=none" in _render()
    # It is its own reason for the layout to run, without any goal or tail.
    out = _render(goal_prompt=False, tail_prose_tokens=0, state_file=str(_state_file(tmp_path)))
    assert layout.STATE_HEADER in out and TAIL not in out


def test_the_state_section_is_bounded_and_truncated_at_a_paragraph_boundary(tmp_path):
    paragraphs = [f"## Step {index}\n" + "synthetic instruction " * 12 for index in range(40)]
    body = "\n\n".join(paragraphs)
    state = _state_file(tmp_path, body)
    result = extract(LAYOUT, _layout_config(
        goal_prompt=False, tail_prose_tokens=0, budget_tokens=900,
        state_file=str(state),
    ))
    out = result.render()
    assert result._readback_layout.state_truncated is True
    assert select.estimate_tokens(out) <= 900
    assert "[state truncated:" in out and str(state) in out.split("[state truncated:")[1]
    kept_paragraphs = [line for line in out.splitlines() if line.startswith("## Step ")]
    expected_paragraphs = [line for line in body.splitlines() if line.startswith("## Step ")]
    assert kept_paragraphs and kept_paragraphs == expected_paragraphs[: len(kept_paragraphs)]
    # The cut never splits a paragraph: the text before the marker ends with a
    # complete paragraph, so a successor cannot act on half an instruction.
    before_marker = out.split("[state truncated:")[0].rstrip()
    assert before_marker.endswith("synthetic instruction")
    # A budget too small for even one paragraph keeps the marker, not half of it.
    tiny = extract(LAYOUT, _layout_config(
        goal_prompt=False, tail_prose_tokens=0, budget_tokens=700,
        state_file=str(state),
    )).render()
    assert "[state truncated:" in tiny
    assert "synthetic instruction" not in tiny.split(layout.STATE_HEADER)[1].split(
        "[state truncated:"
    )[0]


def test_the_state_section_takes_priority_over_the_delta_and_spares_the_tail(tmp_path):
    state = _state_file(tmp_path, body="\n\n".join(
        f"## Step {index}\n" + "synthetic instruction " * 12 for index in range(20)
    ))
    result = extract(LAYOUT, _layout_config(
        goal_prompt=False, tail_prose_tokens=10_000, budget_tokens=1_500,
        state_file=str(state),
    ))
    plan = result._readback_layout
    out = result.render()
    assert plan.tail.kept >= 1                        # the tail kept its minimum share
    assert plan.state_tokens > 0
    assert plan.content_limit_tokens <= max(0, 1_500 - 512 - plan.state_tokens)
    assert "synthetic instruction" in out and NEWEST in out


def test_the_state_section_is_redacted_like_every_kept_paragraph(tmp_path):
    body = "## Done\n- Synthetic secret marker one.\n\n## Next\n- Synthetic next step."
    state = _state_file(tmp_path, body)
    result = extract(LAYOUT, _layout_config(
        goal_prompt=False, tail_prose_tokens=0, state_file=str(state),
        redact_patterns=("synthetic secret marker",),
    ))
    out = result.render()
    assert "Synthetic secret marker one." not in out
    assert "[redacted paragraph -- matched --redact-pattern" in out
    assert "Synthetic next step." in out              # unrelated paragraph survives
    assert result.redacted_paragraphs >= 1


def test_an_empty_or_missing_state_file_is_refused(tmp_path):
    empty = _state_file(tmp_path, "   \n", name="empty.md")
    with pytest.raises(ValueError, match="--state-file"):
        extract(LAYOUT, _layout_config(state_file=str(empty)))
    with pytest.raises(ValueError, match="must be a non-empty path"):
        ExtractConfig(state_file="   ")


@pytest.mark.parametrize(("argv", "message"), [
    (("--state-file", "state.md"), "require --preset readback"),
    (("--preset", "readback", "--state-file", "missing.md"), "--state-file"),
])
def test_state_file_cli_contract(capsys, tmp_path, argv, message):
    code, _, err = _run(capsys, LAYOUT, *argv)
    assert code != 0
    assert message in err


def test_paragraph_cut_keeps_whole_paragraphs_and_refuses_a_partial_first_one():
    from nyxloom.session_extract.layout import paragraph_cut

    text = "first paragraph\n\nsecond paragraph\n\nthird"
    # The whole text fits: the cut is its own length, not a paragraph prefix.
    assert paragraph_cut(text, len(text)) == len(text)
    assert paragraph_cut(text, len(text) - 1) == len("first paragraph\n\nsecond paragraph")
    # Not even the first paragraph fits -> 0, so the caller discloses instead
    # of emitting half an instruction.
    assert paragraph_cut(text, 5) == 0


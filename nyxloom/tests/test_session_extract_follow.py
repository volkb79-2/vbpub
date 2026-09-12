"""follow.py: the incremental tailing engine, the checkpoint-scoring
lookahead buffer, each attention signal, and delivery.

`Follower.tick()` is one poll cycle, so every test here drives real polls
against a real growing file (or a real SQLite store) -- no mocked clock, no
sleeping, and no infinite loop.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from nyxloom.session_extract import classifier
from nyxloom.session_extract.adapters import claude_code
from nyxloom.session_extract.config import ExtractConfig
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.follow import (
    FollowConfig,
    Follower,
    FollowSelector,
    JsonlSource,
    JsonlTailer,
    OpencodeSource,
)
from nyxloom.session_extract.select import select

_TS = "2026-01-01T00:00:00Z"


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return base


def _user(uuid, text):
    return _rec(type="user", uuid=uuid, timestamp=_TS, message={"role": "user", "content": text})


def _assistant(uuid, text):
    return _rec(type="assistant", uuid=uuid, timestamp=_TS,
                 message={"role": "assistant", "content": [{"type": "text", "text": text}]})


def _append(fp: Path, *records) -> int:
    payload = "".join(json.dumps(r) + "\n" for r in records)
    with fp.open("a", encoding="utf-8") as f:
        f.write(payload)
    return len(payload.encode("utf-8"))


# --------------------------------------------------------------------------
# JsonlTailer -- the actual tail -f mechanics
# --------------------------------------------------------------------------


def test_tailer_reads_only_the_appended_bytes_never_the_whole_file(tmp_path):
    # THE regression test for this feature's first-draft bug: calling a
    # whole-file dump per poll tick rescanned the entire growing file from
    # byte 0 every second. bytes_read exists precisely to witness that.
    fp = tmp_path / "session.jsonl"
    initial = _append(fp, *[_user(f"u{i}", "x" * 500) for i in range(40)])
    assert initial > 20_000

    tailer = JsonlTailer(fp, offset=fp.stat().st_size)
    assert tailer.poll() == []
    assert tailer.bytes_read == 0  # unchanged file -> no read AT ALL

    appended = _append(fp, _user("u99", "the new line"))
    lines = tailer.poll()
    assert len(lines) == 1
    assert "the new line" in lines[0]
    assert tailer.bytes_read == appended
    assert tailer.bytes_read < initial  # i.e. nowhere near a full rescan

    appended += _append(fp, _user("u100", "another"))
    assert len(tailer.poll()) == 1
    assert tailer.bytes_read == appended
    tailer.close()


def test_tailer_holds_back_a_partial_line_until_it_is_complete(tmp_path):
    # A writer caught mid-flush must never yield a truncated record.
    fp = tmp_path / "session.jsonl"
    fp.write_text("", encoding="utf-8")
    tailer = JsonlTailer(fp, offset=0)

    whole = json.dumps(_user("u1", "complete record"))
    with fp.open("a", encoding="utf-8") as f:
        f.write(whole[:20])
    assert tailer.poll() == []

    with fp.open("a", encoding="utf-8") as f:
        f.write(whole[20:] + "\n")
    lines = tailer.poll()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"]["content"] == "complete record"
    tailer.close()


def test_tailer_reopens_from_zero_when_it_observes_the_file_shrink(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u1", "first"))
    tailer = JsonlTailer(fp, offset=fp.stat().st_size)

    fp.write_text("", encoding="utf-8")  # truncation, observed while empty
    assert tailer.poll() == []
    _append(fp, _user("u2", "after truncation"))
    lines = tailer.poll()
    assert len(lines) == 1
    assert "after truncation" in lines[0]
    tailer.close()


def test_tailer_detects_a_rewrite_that_regrew_past_its_own_offset(tmp_path):
    # The case no size comparison can see: truncated AND regrown beyond where
    # we were, between two ticks. Caught by the committed-offset-follows-a-
    # newline invariant instead (which is why the offset only ever advances
    # past complete lines).
    fp = tmp_path / "session.jsonl"
    fp.write_text("", encoding="utf-8")
    tailer = JsonlTailer(fp, offset=0)
    _append(fp, _user("u1", "first"))
    assert len(tailer.poll()) == 1  # the offset is now this tailer's own

    fp.write_text("", encoding="utf-8")
    _append(fp, _user("u2", "x" * 900))  # rewritten, now LONGER than before
    lines = tailer.poll()
    assert len(lines) == 1
    assert json.loads(lines[0])["uuid"] == "u2"
    tailer.close()


def test_a_caller_supplied_mid_line_anchor_is_not_second_guessed(tmp_path):
    # The starting offset is a plain file size taken while a LIVE session may
    # be mid-write, so it can land mid-line. Validating it would mean
    # re-streaming the entire file as "new" on a perfectly normal start, so
    # it is trusted: the leftover fragment simply fails to parse and is
    # dropped, and the stream is correct from the next complete record on.
    fp = tmp_path / "session.jsonl"
    half = json.dumps(_user("u1", "half-written record"))
    fp.write_text(half[:30], encoding="utf-8")
    tailer = JsonlTailer(fp, offset=fp.stat().st_size)

    with fp.open("a", encoding="utf-8") as f:
        f.write(half[30:] + "\n")
    _append(fp, _user("u2", "the next complete record"))

    source = JsonlSource(fp, "claude-code", fp.stat().st_size, ExtractConfig(), False)
    source.tailer = tailer
    arrivals = source.poll()
    assert [e.text for a in arrivals for e in a.events] == ["the next complete record"]
    tailer.close()


def test_tailer_tolerates_a_file_that_does_not_exist_yet(tmp_path):
    tailer = JsonlTailer(tmp_path / "not-created-yet.jsonl", offset=0)
    assert tailer.poll() == []


# --------------------------------------------------------------------------
# FollowSelector -- select()'s per-event rule, applied forward
# --------------------------------------------------------------------------


def _feed_all(events, config):
    selector = FollowSelector(config)
    emitted, checkpoints = [], []
    for ev in events:
        result = selector.feed(ev)
        emitted += result.emitted
        checkpoints += result.checkpoints
    return emitted, checkpoints


def test_follow_selection_reproduces_the_backward_walk_on_the_same_events():
    # The whole point of the select.decide() refactor: streaming these
    # forward must keep exactly what the backward walk keeps (with the
    # aggregate stop conditions off, since those are span-bounded and
    # meaningless on a stream). The single trailing event whose verdict still
    # depends on an unseen lookahead is the documented exception.
    events = [
        NormalizedEvent(0, "op0", _TS, EventKind.OPERATOR_TEXT, "do the thing"),
        NormalizedEvent(1, "a1", _TS, EventKind.ASSISTANT_TEXT, "Let me check."),
        NormalizedEvent(2, "a2", _TS, EventKind.ASSISTANT_TEXT,
                         "## Status\n\nDone -- everything landed, main clean."),
        NormalizedEvent(3, "a3", _TS, EventKind.ASSISTANT_TEXT, "x" * 400),
        NormalizedEvent(4, "a4", _TS, EventKind.ASSISTANT_TEXT, "Good catch"),
        NormalizedEvent(5, "op5", _TS, EventKind.OPERATOR_TEXT, "yes, that one"),
        NormalizedEvent(6, "a6", _TS, EventKind.ASSISTANT_TEXT,
                         "Found it -- a pgrep pattern bug in `detect.py`."),
        NormalizedEvent(7, "qa7", _TS, EventKind.QA_PAIR, "INTERVIEW: ship?\n\nOPERATOR: yes"),
        NormalizedEvent(8, "lc8", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]"),
    ]
    config = ExtractConfig(max_checkpoints=-1, max_words=-1, max_lifecycle_markers=-1)

    walk_events = [
        NormalizedEvent(e.seq, e.marker, e.timestamp, e.kind, e.text) for e in events
    ]
    classifier.score_events(walk_events)
    expected = [e.marker for e in select(walk_events, config)]

    emitted, _ = _feed_all(events, config)
    assert [e.marker for e in emitted] == expected


def test_a_marginal_short_line_waits_for_the_lookahead_then_is_kept():
    # "Good catch" scores 1.0 on shape alone -- below the 3.0 threshold, and
    # short with no finding signal, so whether it survives at all depends
    # entirely on the +2.0 "followed by a pause" bonus. It must NOT appear
    # until the pause actually arrives.
    config = ExtractConfig()
    selector = FollowSelector(config)
    marginal = NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT, "Good catch")
    assert selector.feed(marginal).emitted == []

    result = selector.feed(NormalizedEvent(1, "op1", _TS, EventKind.OPERATOR_TEXT, "right"))
    assert [e.marker for e in result.emitted] == ["a0", "op1"]
    assert [e.marker for e in result.checkpoints] == ["a0"]


def test_a_marginal_short_line_is_dropped_when_no_pause_follows():
    config = ExtractConfig()
    selector = FollowSelector(config)
    selector.feed(NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT, "Good catch"))
    result = selector.feed(NormalizedEvent(1, "a1", _TS, EventKind.ASSISTANT_TEXT, "x" * 400))
    assert [e.marker for e in result.emitted] == ["a1"]  # a0 dropped, exactly as select() would
    assert result.checkpoints == []


def test_an_obvious_checkpoint_is_not_delayed_at_all():
    # A header alone scores 3.0, clearing the threshold; the pause bonus can
    # only ADD, so the verdict is already final -- waiting would hide the one
    # thing this feature most exists to surface.
    config = ExtractConfig()
    result = FollowSelector(config).feed(
        NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT, "## Where things stand\n\nall good")
    )
    assert [e.marker for e in result.emitted] == ["a0"]
    assert [e.marker for e in result.checkpoints] == ["a0"]


def test_a_long_block_prints_immediately_and_its_checkpoint_fires_late():
    # Kept regardless of score (length alone), so it prints at once; if the
    # pause bonus later lifts it over the threshold, the ATTENTION signal
    # fires then -- a notification arriving a beat late costs nothing, a
    # delayed line on screen does.
    config = ExtractConfig()
    selector = FollowSelector(config)
    long_closure = "Landed. " + "x" * 300
    first = selector.feed(NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT, long_closure))
    assert [e.marker for e in first.emitted] == ["a0"]
    assert first.checkpoints == []  # 2.0 on shape alone, below the threshold

    later = selector.feed(NormalizedEvent(1, "op1", _TS, EventKind.OPERATOR_TEXT, "thanks"))
    assert [e.marker for e in later.checkpoints] == ["a0"]
    assert [e.marker for e in later.emitted] == ["op1"]  # a0 is NOT re-emitted


def test_thinking_behind_a_still_pending_event_keeps_stream_order():
    config = ExtractConfig(include_thinking=True)
    selector = FollowSelector(config)
    assert selector.feed(
        NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT, "Good catch")
    ).emitted == []
    # THINKING is not decisive for the pending score (score_events' own pause
    # scan skips it), so it must be held behind, not printed ahead.
    assert selector.feed(
        NormalizedEvent(1, "th1", _TS, EventKind.THINKING, "y" * 400)
    ).emitted == []
    result = selector.feed(NormalizedEvent(2, "op2", _TS, EventKind.OPERATOR_TEXT, "ok"))
    assert [e.marker for e in result.emitted] == ["a0", "th1", "op2"]


def test_an_api_error_is_dropped_immediately_not_buffered():
    config = ExtractConfig()
    selector = FollowSelector(config)
    api = NormalizedEvent(0, "a0", _TS, EventKind.ASSISTANT_TEXT,
                           "[API ERROR: rate_limit, HTTP 429] You've hit your session limit")
    assert selector.feed(api).emitted == []
    # nothing is pending, so the next event resolves on its own
    result = selector.feed(NormalizedEvent(1, "op1", _TS, EventKind.OPERATOR_TEXT, "hm"))
    assert [e.marker for e in result.emitted] == ["op1"]


# --------------------------------------------------------------------------
# Follower -- end to end against a growing file
# --------------------------------------------------------------------------


def _follower(fp, out, lossless_mode=False, follow_config=None, config=None, fmt="claude-code"):
    config = config or ExtractConfig()
    source = JsonlSource(fp, fmt, fp.stat().st_size, config, lossless_mode)
    return Follower(
        source, harness=fmt, session_path=str(fp), config=config,
        follow_config=follow_config or FollowConfig(), out=out,
        lossless_mode=lossless_mode, printed_any=False,
    )


def test_extract_follow_streams_only_checkpoint_worthy_new_content(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out)

    _append(
        fp,
        _assistant("a1", "Let me check."),
        _assistant("a2", "## Live status\n\nDone -- landed, main clean."),
        _user("u1", "next please"),
    )
    follower.tick()
    text = out.getvalue()
    assert "## Live status" in text
    assert "OPERATOR: next please" in text
    assert "Let me check." not in text  # short, procedural -- dropped, as one-shot would
    follower.close()


def test_lossless_follow_keeps_everything_including_what_extract_drops(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out, lossless_mode=True)

    _append(fp, _assistant("a1", "Let me check."))
    follower.tick()
    text = out.getvalue()
    assert "Let me check." in text
    assert "===[a1 |" in text  # the dump's own block header, same as one-shot
    follower.close()


def test_nothing_is_emitted_when_the_file_has_not_grown(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out)
    assert follower.tick() == 0
    assert out.getvalue() == ""
    follower.close()


def test_blocks_are_separated_the_same_way_the_one_shot_render_separates_them(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out)
    _append(fp, _user("u1", "first"), _user("u2", "second"))
    follower.tick()
    assert out.getvalue() == "OPERATOR: first\n\n---\n\nOPERATOR: second"
    follower.close()


def test_a_subagent_transcript_is_followed_rather_than_filtered_to_nothing(tmp_path):
    # Every record in a dispatched sub-agent's own file is isSidechain=true;
    # follow.py has to be told the file has no primary thread, since one
    # tailed line cannot reveal that.
    fp = tmp_path / "agent-a36c6ff1d3cc69767.jsonl"
    _append(fp, _rec(type="user", uuid="s0", isSidechain=True, timestamp=_TS,
                      message={"role": "user", "content": "delegated task"}))
    config = ExtractConfig()
    out = io.StringIO()
    source = JsonlSource(fp, "claude-code", fp.stat().st_size, config, False,
                          has_primary_thread=claude_code.has_primary_thread(fp))
    follower = Follower(source, harness="claude-code", session_path=str(fp), config=config,
                         follow_config=FollowConfig(), out=out, lossless_mode=False,
                         printed_any=False)
    _append(fp, _rec(type="assistant", uuid="s1", isSidechain=True, timestamp=_TS,
                      message={"role": "assistant", "content": [
                          {"type": "text", "text": "## Done\n\nthe delegated work landed"}]}))
    follower.tick()
    assert "the delegated work landed" in out.getvalue()
    follower.close()


# --------------------------------------------------------------------------
# Attention signals + delivery
# --------------------------------------------------------------------------


def test_interview_pending_fires_on_an_unanswered_question_and_clears_on_the_answer():
    pending: dict[str, str] = {}
    question = _rec(type="assistant", uuid="a1", timestamp=_TS, message={
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "Ship it?", "options": [{"label": "yes"}]}]}},
        ]})
    assert claude_code.update_interview_pending(question, pending) == "Ship it?"
    assert pending == {"tu1": "Ship it?"}

    answer = _rec(type="user", uuid="u1", timestamp=_TS, message={
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": '"Ship it?"="yes"'},
        ]})
    assert claude_code.update_interview_pending(answer, pending) is None
    assert pending == {}


def test_an_ordinary_tool_call_is_not_an_interview_signal():
    pending: dict[str, str] = {}
    rec = _rec(type="assistant", uuid="a1", timestamp=_TS, message={
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
        ]})
    assert claude_code.update_interview_pending(rec, pending) is None
    assert pending == {}


def test_bell_writes_the_bell_byte_on_a_fired_signal(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out, follow_config=FollowConfig(bell=True))
    _append(fp, _assistant("a1", "## Status\n\nDone -- landed."))
    follower.tick()
    assert "\a" in out.getvalue()
    follower.close()


def test_no_bell_when_nothing_fires(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out, follow_config=FollowConfig(bell=True))
    _append(fp, _user("u1", "just an operator turn"))
    follower.tick()
    assert "\a" not in out.getvalue()
    follower.close()


def test_on_attention_runs_the_command_with_the_documented_env_vars(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    sink = tmp_path / "fired.txt"
    script = (
        f'printf "%s\\n%s\\n%s\\n%s\\n" "$NYXLOOM_ATTENTION_REASON" '
        f'"$NYXLOOM_ATTENTION_HARNESS" "$NYXLOOM_ATTENTION_SESSION_PATH" '
        f'"$NYXLOOM_ATTENTION_EXCERPT" >> {sink}'
    )
    out = io.StringIO()
    follower = _follower(fp, out, follow_config=FollowConfig(on_attention=script))

    _append(fp, _rec(type="assistant", uuid="a1", timestamp=_TS, message={
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "Which approach?"}]}},
        ]}))
    follower.tick()
    follower.close()

    reason, harness, session_path, excerpt = sink.read_text().splitlines()[:4]
    assert reason == "interview_pending"
    assert harness == "claude-code"
    assert session_path == str(fp)
    assert excerpt == "Which approach?"


def test_the_excerpt_is_capped(tmp_path):
    from nyxloom.session_extract.follow import EXCERPT_CHARS

    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    sink = tmp_path / "fired.txt"
    out = io.StringIO()
    follower = _follower(
        fp, out,
        follow_config=FollowConfig(on_attention=f'printf "%s" "$NYXLOOM_ATTENTION_EXCERPT" > {sink}',
                                    attention_min_chars=50),
    )
    _append(fp, _assistant("a1", "z" * 500))
    follower.tick()
    follower.close()
    assert len(sink.read_text()) == EXCERPT_CHARS


def test_long_block_signal_is_off_until_attention_min_chars_is_given(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))

    out = io.StringIO()
    quiet = _follower(fp, out, follow_config=FollowConfig(bell=True))
    _append(fp, _assistant("a1", "z" * 500))  # long, but no checkpoint shape
    quiet.tick()
    assert "\a" not in out.getvalue()
    quiet.close()

    out2 = io.StringIO()
    loud = _follower(fp, out2, follow_config=FollowConfig(bell=True, attention_min_chars=100))
    _append(fp, _assistant("a2", "z" * 500))
    loud.tick()
    assert "\a" in out2.getvalue()
    loud.close()


def test_lossless_follow_falls_back_to_shape_scoring_for_checkpoints(tmp_path):
    # No scoring exists in that verb by design, so the signal is
    # classifier.shape_score alone -- weaker, and documented as such.
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out, lossless_mode=True, follow_config=FollowConfig(bell=True))
    _append(fp, _assistant("a1", "## A header block\n\nprose"))
    follower.tick()
    assert "\a" in out.getvalue()
    follower.close()


def test_a_failing_on_attention_command_does_not_stop_the_stream(tmp_path):
    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    follower = _follower(fp, out, follow_config=FollowConfig(on_attention="exit 3"))
    _append(fp, _assistant("a1", "## Status\n\nDone -- landed."))
    assert follower.tick() == 1
    assert "## Status" in out.getvalue()
    follower.close()


# --------------------------------------------------------------------------
# render modes on the live stream
# --------------------------------------------------------------------------


def test_highlight_preserves_every_markdown_character_in_the_stream(tmp_path):
    import re

    from nyxloom.session_extract.highlight import highlight_markdown

    fp = tmp_path / "session.jsonl"
    _append(fp, _user("u0", "start"))
    out = io.StringIO()
    source = JsonlSource(fp, "claude-code", fp.stat().st_size, ExtractConfig(), False)
    follower = Follower(source, harness="claude-code", session_path=str(fp),
                         config=ExtractConfig(), follow_config=FollowConfig(), out=out,
                         lossless_mode=False, printed_any=False,
                         block_render=lambda t: highlight_markdown(t, color=True))
    prose = "## Status\n\nDone -- **bold** and `code` landed."
    _append(fp, _assistant("a1", prose))
    follower.tick()
    follower.close()

    streamed = out.getvalue()
    assert "\x1b[" in streamed  # colored...
    assert re.sub(r"\x1b\[[0-9;]*m", "", streamed) == prose  # ...but byte-identical underneath


# --------------------------------------------------------------------------
# opencode: an indexed cursor, plus the settle rule
# --------------------------------------------------------------------------


def _opencode_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT);"
    )
    conn.execute("INSERT INTO session VALUES ('ses_04bd4e9b4ffeBJm48T6v130DS6', 1)")
    conn.commit()
    conn.close()
    return path


def _opencode_message(db: Path, msg_id: str, t: int, role: str, text: str | None) -> None:
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO message VALUES (?, 'ses_04bd4e9b4ffeBJm48T6v130DS6', ?, ?, ?)",
                  (msg_id, t, t, json.dumps({"role": role})))
    if text is not None:
        conn.execute(
            "INSERT INTO part VALUES (?, ?, 'ses_04bd4e9b4ffeBJm48T6v130DS6', ?, ?, ?)",
            (f"p-{msg_id}", msg_id, t, t, json.dumps({"type": "text", "text": text})),
        )
    conn.commit()
    conn.close()


def test_opencode_source_holds_back_the_newest_row_until_a_newer_one_lands(tmp_path):
    # A message row exists before its `part` rows finish streaming, so the
    # newest row is never committed: it is re-read next tick instead. Without
    # this, a row read the instant it appeared would emit empty (or partial)
    # prose and never be revisited.
    db = _opencode_db(tmp_path / "opencode.db")
    sid = "ses_04bd4e9b4ffeBJm48T6v130DS6"
    source = OpencodeSource(db, sid, ExtractConfig(), lossless_mode=False, cursor=(-1, ""))

    _opencode_message(db, "m1", 10, "user", None)  # row created, parts not written yet
    assert source.poll() == []  # held back, nothing lost

    # its text arrives, and a newer row proves m1 is finished
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO part VALUES ('p-m1', 'm1', ?, 10, 10, ?)",
                  (sid, json.dumps({"type": "text", "text": "the full prompt text"})))
    conn.commit()
    conn.close()
    _opencode_message(db, "m2", 20, "assistant", "## Status\n\nDone -- landed.")

    arrivals = source.poll()
    assert [e.text for a in arrivals for e in a.events] == ["the full prompt text"]
    # m2 is now the newest and stays pending in turn
    assert source.poll() == []

    _opencode_message(db, "m3", 30, "user", "next")
    arrivals = source.poll()
    assert [e.text for a in arrivals for e in a.events] == ["## Status\n\nDone -- landed."]
    source.close()


def test_opencode_source_starts_from_the_given_cursor(tmp_path):
    db = _opencode_db(tmp_path / "opencode.db")
    sid = "ses_04bd4e9b4ffeBJm48T6v130DS6"
    _opencode_message(db, "m1", 10, "user", "already in the one-shot brief")
    _opencode_message(db, "m2", 20, "assistant", "also already printed")

    source = OpencodeSource(db, sid, ExtractConfig(), lossless_mode=False, cursor=(20, "m2"))
    _opencode_message(db, "m3", 30, "user", "brand new")
    _opencode_message(db, "m4", 40, "assistant", "newer still")
    arrivals = source.poll()
    assert [e.text for a in arrivals for e in a.events] == ["brand new"]
    source.close()


def test_opencode_lossless_follow_yields_the_dumps_own_blocks(tmp_path):
    db = _opencode_db(tmp_path / "opencode.db")
    sid = "ses_04bd4e9b4ffeBJm48T6v130DS6"
    source = OpencodeSource(db, sid, ExtractConfig(), lossless_mode=True, cursor=(-1, ""))
    _opencode_message(db, "m1", 10, "user", "short procedural line")
    _opencode_message(db, "m2", 20, "assistant", "and more")
    blocks = [b for a in source.poll() for b in a.blocks]
    assert [b.text for b in blocks] == ["short procedural line"]
    assert blocks[0].header.startswith("===[m1 |")
    source.close()

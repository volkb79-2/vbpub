"""SUCCESSOR-2 round 3: the four `--preset` bundles (watch, successor, review,
ledger): pinned expansions, every member a real grouped option, explicit
options overriding each preset, and the watch renderer (text, colour, JSON
lines, follow)."""

from __future__ import annotations

import io
import json

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.cli_registry import EXTRACT_GROUPS, harness_cli
from nyxloom.session_extract import presets
from nyxloom.session_extract.config import PROFILES
from nyxloom.session_extract.follow import FollowConfig, Follower, JsonlSource
from nyxloom.session_extract.watch import WatchFormatter, agent_id

T0 = "2026-01-01T10:00:00Z"
ESC = "\x1b["


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _rec(**kw):
    base = {"isSidechain": True, "sessionId": "s", "parentUuid": None, "cwd": "/w/repo"}
    base.update(kw)
    return base


def _user(uid, ts, content):
    return _rec(type="user", uuid=uid, timestamp=ts, message={"role": "user", "content": content})


def _assistant(uid, ts, blocks, msg="m1"):
    return _rec(type="assistant", uuid=uid, timestamp=ts,
                message={"role": "assistant", "id": msg, "content": blocks})


def _text(text):
    return {"type": "text", "text": text}


def _use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _result(tid, text, error=False):
    return {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}


def _session(tmp_path, name="agent-abc123.jsonl"):
    records = [
        _user("u1", T0, "please push the branch"),
        _assistant("a1", "2026-01-01T10:00:05Z", [_text("Starting on the push now.")], "m1"),
        _assistant("a2", "2026-01-01T10:00:10Z",
                   [_use("t1", "Bash", command="git push origin feat", description="Push the branch")], "m2"),
        _user("r1", "2026-01-01T10:00:11Z", [_result("t1", "Exit code 1\nremote rejected", True)]),
        _assistant("a3", "2026-01-01T10:00:20Z",
                   [_use("t2", "Edit", file_path="/w/repo/a.py", old_string="a", new_string="b")], "m3"),
        _assistant("a4", "2026-01-01T10:00:30Z", [_text("The push was rejected; I fixed a.py.")], "m4"),
        _user("u2", "2026-01-01T10:00:40Z", "ok, try again"),
    ]
    fp = tmp_path / name
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


# --- pinned expansions -------------------------------------------------------

EXPANSIONS = {
    "watch": "--profile all --timestamps all --prose-only",
    "successor": (
        "--profile all --tool-calls intent-or-call --tool-errors show --edit-calls collapse "
        "--read-calls collapse --effect-calls always --timestamps gaps --path-aliases auto "
        "--strip-cd-prefix --ledger --stop-state"
    ),
    "review": (
        "--profile all --tool-calls intent-or-call --tool-errors show --edit-calls show "
        "--read-calls show --effect-calls always --timestamps all --ledger"
    ),
    "ledger": "--no-prose --ledger --stop-state",
}


def test_there_are_exactly_four_presets_with_the_pinned_expansions():
    assert list(presets.PRESETS) == ["watch", "successor", "review", "ledger"]
    assert {name: p.expansion for name, p in presets.PRESETS.items()} == EXPANSIONS


def test_every_preset_option_is_a_real_grouped_option_and_a_legal_value():
    parser = harness_cli().command_parsers["extract"]
    by_flag = {}
    for group in parser._action_groups:
        for action in group._group_actions:
            for spelling in action.option_strings:
                by_flag[spelling] = (group.title, action)
    for preset in presets.PRESETS.values():
        members = [(presets.option_of(a), v) for a, v in preset.values]
        members += [(presets.option_of(a), None) for a in preset.flags]
        for flag, value in members:
            assert flag in by_flag, f"{preset.name}: {flag} is not an extract option"
            title, action = by_flag[flag]
            assert title in EXTRACT_GROUPS, f"{preset.name}: {flag} is ungrouped ({title})"
            if value is None:
                assert action.nargs == 0, f"{preset.name}: {flag} should be a boolean flag"
            elif action.choices:
                assert value in action.choices, f"{preset.name}: {flag} {value} not a choice"


def test_every_cancelling_option_is_a_real_option():
    parser = harness_cli().command_parsers["extract"]
    flags = {s for a in parser._actions for s in a.option_strings}
    for attr, others in presets.CANCELLED_BY.items():
        assert presets.option_of(attr) in flags
        for other in others:
            assert presets.option_of(other) in flags


def test_help_defines_every_preset_with_one_example_each():
    text = harness_cli().command_parsers["extract"].format_help()
    flat = " ".join(text.split()).replace("- ", "-")
    for preset in presets.PRESETS.values():
        assert preset.expansion in flat
    assert len(presets.example_lines()) == 4
    for name in presets.PRESETS:
        assert sum(f"extract SESSION_LOG --preset {name}" in line for line in text.splitlines()) == 1


# --- resolve(): explicit options win ------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.preset = None
        self.successor_brief = False
        self.show_tool_calls = False
        self.__dict__.update(kw)

    def __getattr__(self, name):  # unset options read as "not passed"
        return None


@pytest.mark.parametrize("name", list(presets.PRESETS))
def test_resolve_applies_the_whole_expansion_when_nothing_is_explicit(name):
    changes = presets.resolve(_Args(preset=name))
    preset = presets.PRESETS[name]
    assert changes == {**dict(preset.values), **{a: True for a in preset.flags}}


@pytest.mark.parametrize("name", list(presets.PRESETS))
def test_resolve_never_replaces_an_explicit_value(name):
    for attr, _ in presets.PRESETS[name].values:
        assert attr not in presets.resolve(_Args(preset=name, **{attr: "explicit"}))


def test_resolve_boolean_members_yield_to_their_contradicting_flag():
    assert "ledger" not in presets.resolve(_Args(preset="review", no_ledger=True))
    assert "stop_state" not in presets.resolve(_Args(preset="ledger", no_stop_state=True))
    assert "strip_cd_prefix" not in presets.resolve(_Args(preset="successor", no_strip_cd_prefix=True))
    assert "prose_only" not in presets.resolve(_Args(preset="watch", no_prose=True))
    assert "no_prose" not in presets.resolve(_Args(preset="ledger", prose_only=True))


def test_the_deprecated_tool_call_alias_counts_as_explicit():
    assert "tool_calls" not in presets.resolve(_Args(preset="review", show_tool_calls=True))


def test_successor_brief_implies_the_successor_preset():
    assert presets.preset_name(_Args(successor_brief=True)) == "successor"
    assert presets.resolve(_Args(successor_brief=True))["stop_state"] is True
    assert presets.preset_name(_Args()) is None and presets.resolve(_Args()) == {}


# --- watch ---------------------------------------------------------------------

def test_watch_shows_only_operator_and_assistant_prose(tmp_path, capsys):
    code, out, err = _run(capsys, _session(tmp_path), "--preset", "watch")
    assert code == 0, err
    assert "[10:00:00] OPERATOR: please push the branch" in out
    assert "[10:00:05] Starting on the push now." in out
    assert "[10:00:40] OPERATOR: ok, try again" in out
    for noise in ("git push", "Edit", "tool", "rejected\n", "Exit code", "nyxloom-extract", "---", "[gap"):
        assert noise not in out.replace("The push was rejected; I fixed a.py.", ""), noise
    assert ESC not in out  # not a TTY: colour is off


def test_watch_colour_is_automatic_only_on_a_tty_and_forced_by_color(tmp_path, capsys):
    fp = _session(tmp_path)
    _, plain, _ = _run(capsys, fp, "--preset", "watch")
    _, forced, _ = _run(capsys, fp, "--preset", "watch", "--color")
    _, never, _ = _run(capsys, fp, "--preset", "watch", "--no-color")
    assert ESC not in plain and ESC not in never
    assert f"{ESC}2m[10:00:00]{ESC}0m" in forced                    # dim timestamp
    assert f"{ESC}1;36mOPERATOR: please push the branch{ESC}0m" in forced  # operator highlighted
    assert "Starting on the push now." in forced
    assert f"{ESC}1;36mStarting" not in forced                       # assistant text is plain


def test_watch_jsonl_lines(tmp_path, capsys):
    fp = _session(tmp_path)
    code, out, err = _run(capsys, fp, "--preset", "watch", "--jsonl")
    assert code == 0, err
    rows = [json.loads(line) for line in out.splitlines()]
    assert [r["role"] for r in rows] == ["operator", "assistant", "assistant", "operator"]
    assert rows[0] == {"ts": T0, "role": "operator", "text": "please push the branch", "agent": "abc123"}
    assert all(set(r) == {"ts", "role", "text", "agent"} for r in rows)
    assert ESC not in out


def test_watch_jsonl_omits_agent_for_a_main_session_file(tmp_path, capsys):
    fp = _session(tmp_path, name="main-session.jsonl")
    # Not a subagent file: the adapter treats isSidechain records as noise, so
    # rewrite them as primary-thread records.
    records = [json.loads(line) for line in fp.read_text().splitlines()]
    for r in records:
        r["isSidechain"] = False
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    _, out, _ = _run(capsys, fp, "--preset", "watch", "--jsonl")
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows and all("agent" not in r for r in rows)


def test_agent_id_only_for_agent_files(tmp_path):
    assert agent_id(tmp_path / "agent-ad3baa0ae09e050a0.jsonl") == "ad3baa0ae09e050a0"
    assert agent_id(tmp_path / "384f276e.jsonl") is None


def test_watch_overrides(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--preset", "watch", "--timestamps", "none")
    assert "[10:00" not in out and "OPERATOR: please push the branch" in out
    _, out, _ = _run(capsys, fp, "--preset", "watch", "--timestamp-format", "%H:%M")
    assert "10:00 OPERATOR: please push" in out
    _, out, _ = _run(capsys, fp, "--preset", "watch", "--show-timestamps", "none")
    assert "[10:00" not in out
    _, out, _ = _run(capsys, fp, "--preset", "watch", "--no-prose", "--ledger")
    assert "OPERATOR" not in out and "[session ledger" in out        # --no-prose wins over the preset's flag


@pytest.mark.parametrize("extra", [
    ["--ledger"], ["--stop-state"], ["--tool-calls", "call"], ["--edit-calls", "show"],
    ["--successor-brief"], ["--task", "x"], ["--json"],
])
def test_watch_refuses_options_that_contradict_prose_only(tmp_path, capsys, extra):
    code, _, err = _run(capsys, _session(tmp_path), "--preset", "watch", *extra)
    assert code == 2


def test_jsonl_needs_prose_only(tmp_path, capsys):
    code, _, err = _run(capsys, _session(tmp_path), "--jsonl")
    assert code == 2 and "--prose-only" in err


def test_watch_follow_streams_prose_only_formatted_lines(tmp_path):
    fp = tmp_path / "agent-f00d.jsonl"
    fp.write_text(json.dumps(_user("u0", T0, "first")) + "\n", encoding="utf-8")
    config = PROFILES["all"]  # what --preset watch selects
    out = io.StringIO()
    formatter = WatchFormatter(jsonl=True, agent=agent_id(fp))
    source = JsonlSource(fp, "claude-code", fp.stat().st_size, config, False, has_primary_thread=False)
    follower = Follower(source, harness="claude-code", session_path=str(fp), config=config,
                        follow_config=FollowConfig(), out=out, lossless_mode=False,
                        printed_any=False, watch=formatter)
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_user("u1", "2026-01-01T10:01:00Z", "live operator message")) + "\n")
        f.write(json.dumps(_assistant("a1", "2026-01-01T10:01:05Z",
                                      [_use("t1", "Bash", command="ls")], "m1")) + "\n")
        f.write(json.dumps(_assistant("a2", "2026-01-01T10:01:09Z", [_text("live reply")], "m2")) + "\n")
    assert follower.tick() == 2
    follower.close()
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(r["role"], r["text"]) for r in rows] == [("operator", "live operator message"),
                                                       ("assistant", "live reply")]
    assert all(r["agent"] == "f00d" for r in rows)
    assert "nyxloom-extract" not in out.getvalue()


def test_watch_formatter_text_follow_mode_has_no_separators_or_cursors(tmp_path):
    fp = tmp_path / "agent-f00d.jsonl"
    fp.write_text(json.dumps(_user("u0", T0, "first")) + "\n", encoding="utf-8")
    config = PROFILES["all"]
    out = io.StringIO()
    source = JsonlSource(fp, "claude-code", fp.stat().st_size, config, False, has_primary_thread=False)
    follower = Follower(source, harness="claude-code", session_path=str(fp), config=config,
                        follow_config=FollowConfig(), out=out, lossless_mode=False,
                        printed_any=True, watch=WatchFormatter(color=False))
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_assistant("a1", "2026-01-01T10:01:09Z", [_text("hello")], "m1")) + "\n")
    follower.tick()
    assert out.getvalue() == "[10:01:09] hello\n\n"


# --- review ----------------------------------------------------------------------

def test_review_renders_every_call_errors_with_calls_and_the_ledger(tmp_path, capsys):
    code, out, err = _run(capsys, _session(tmp_path), "--preset", "review")
    assert code == 0, err
    assert "Push the branch" in out                    # intent when present
    assert "git push origin feat" in out               # effect call always shown
    assert "remote rejected" in out                    # the error, with its call
    assert "[tool call: Edit]" in out                  # edits not collapsed
    assert "[edited" not in out
    assert "[10:00:00]" in out and "[10:00:40]" in out  # every timestamp
    assert "[session ledger" in out and "external effects" in out


def test_review_overrides(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--preset", "review", "--edit-calls", "collapse")
    assert "[edited" in out
    _, out, _ = _run(capsys, fp, "--preset", "review", "--no-ledger")
    assert "[session ledger" not in out
    _, out, _ = _run(capsys, fp, "--preset", "review", "--tool-calls", "intent", "--effect-calls", "mode",
                     "--tool-errors", "hide")
    assert "git push origin feat" not in out.split("[session ledger")[0]
    assert "Push the branch" in out
    _, out, _ = _run(capsys, fp, "--preset", "review", "--timestamps", "none")
    assert "[10:00:00]" not in out.split("[session ledger")[0]
    _, out, _ = _run(capsys, fp, "--preset", "review", "--tool-errors", "hide")
    assert "remote rejected" not in out


@pytest.mark.parametrize("flag", ["--json", "--follow"])
def test_review_and_ledger_refuse_json_and_follow(tmp_path, capsys, flag):
    for name in ("review", "ledger"):
        code, _, _ = _run(capsys, _session(tmp_path), "--preset", name, flag)
        assert code == 2


# --- ledger ----------------------------------------------------------------------

def test_ledger_preset_prints_only_effects_files_and_stop_state(tmp_path, capsys):
    code, out, err = _run(capsys, _session(tmp_path), "--preset", "ledger")
    assert code == 0, err
    assert "[session ledger" in out and "git push origin feat" in out
    assert "a.py" in out                                  # touched file
    assert "Stop state" in out or "stop state" in out.lower()
    for prose in ("please push the branch", "Starting on the push", "ok, try again", "[tool call"):
        assert prose not in out


def test_ledger_overrides(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--preset", "ledger", "--no-stop-state")
    assert "stop state" not in out.lower() and "[session ledger" in out
    # --prose-only cancels the preset's --no-prose; the preset's ledger and stop
    # state must then be switched off too (prose-only prints nothing else).
    _, out, _ = _run(capsys, fp, "--preset", "ledger", "--prose-only", "--no-ledger", "--no-stop-state")
    assert "OPERATOR: please push the branch" in out and "[session ledger" not in out
    code, _, _ = _run(capsys, fp, "--preset", "ledger", "--prose-only")
    assert code == 2
    _, out, _ = _run(capsys, fp, "--preset", "ledger", "--effect-pattern", "Edit",
                     "--no-default-effect-patterns")
    assert "git push origin feat" not in out.split("[session ledger")[1].split("external effects")[-1]


def test_no_prose_alone_prints_nothing_so_it_is_refused(tmp_path, capsys):
    code, _, err = _run(capsys, _session(tmp_path), "--no-prose")
    assert code == 2 and "--ledger" in err


# --- successor -------------------------------------------------------------------

def test_successor_preset_now_includes_the_stop_state_and_overrides_still_win(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--preset", "successor")
    assert "stop state" in out.lower()
    _, out, _ = _run(capsys, fp, "--preset", "successor", "--no-stop-state", "--timestamps", "all")
    assert "stop state" not in out.lower()
    assert "[10:00:30]" in out


def test_successor_brief_conflicts_with_a_different_preset(tmp_path, capsys):
    code, _, _ = _run(capsys, _session(tmp_path), "--successor-brief", "--preset", "review")
    assert code == 2

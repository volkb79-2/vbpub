"""SUCCESSOR-2 round 2: `--preset successor`, the harness-version warning and the
agent-control ledger rule."""

from __future__ import annotations

import json

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import harness, ledger

T0 = "2026-01-01T10:00:00Z"


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _rec(**kw):
    base = {"isSidechain": True, "sessionId": "s", "parentUuid": None, "cwd": "/w/repo"}
    base.update(kw)
    return base


def _user(uid, ts, text, **kw):
    return _rec(type="user", uuid=uid, timestamp=ts, message={"role": "user", "content": text}, **kw)


def _assistant(uid, ts, blocks, msg="m1", **kw):
    return _rec(type="assistant", uuid=uid, timestamp=ts,
                message={"role": "assistant", "id": msg, "content": blocks}, **kw)


def _use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _write(tmp_path, records, name="agent-s.jsonl"):
    fp = tmp_path / name
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def _session(tmp_path, version=None):
    extra = {"version": version} if version else {}
    return _write(tmp_path, [
        _user("u1", T0, "please run the thing", **extra),
        _assistant("a1", "2026-01-01T10:01:00Z",
                   [_use("t1", "Bash", command="cat /w/repo/a.py", description="Read it")], **extra),
        _assistant("a2", "2026-01-01T10:02:00Z",
                   [_use("t2", "Edit", file_path="/w/repo/a.py", old_string="a", new_string="b")], "m2", **extra),
    ])


# --- the preset ------------------------------------------------------------

def test_preset_turns_the_successor_defaults_on_without_the_brief(tmp_path, capsys):
    fp = _session(tmp_path)
    code, out, _ = _run(capsys, fp, "--preset", "successor")
    assert code == 0
    assert "[oriented: 1 read]" in out              # --read-calls collapse
    assert "[edited $REPO/a.py" in out              # --edit-calls collapse + --path-aliases auto
    assert "[session ledger" in out                 # --ledger
    assert "# Successor brief" not in out           # not the brief document


def test_explicit_flags_override_the_preset(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--preset", "successor", "--read-calls", "show",
                     "--edit-calls", "show", "--tool-calls", "call")
    assert "[tool call: Bash] $ cat $REPO/a.py" in out
    assert "[tool call: Edit]" in out and "[oriented" not in out


def test_deprecated_tool_call_alias_counts_as_explicit(tmp_path, capsys):
    fp = _session(tmp_path)
    code, out, err = _run(capsys, fp, "--preset", "successor", "--show-tool-calls")
    assert code == 2 or "deprecated" in err
    if code == 0:
        assert "[tool call: Bash]" not in out.replace("[tool call: Bash] Read it", "")


def test_no_strip_cd_prefix_and_preset(tmp_path, capsys):
    recs = [_user("u1", T0, "go"),
            _assistant("a1", "2026-01-01T10:01:00Z", [_use("t1", "Bash", command="cd /w/repo && make")])]
    fp = _write(tmp_path, recs)
    _, out, _ = _run(capsys, fp, "--preset", "successor", "--read-calls", "show")
    assert "$ make" in out and "cd /w/repo" not in out
    _, out, _ = _run(capsys, fp, "--preset", "successor", "--read-calls", "show", "--no-strip-cd-prefix")
    assert "$ cd $REPO && make" in out


@pytest.mark.parametrize("flag", ["--json", "--follow"])
def test_preset_refuses_json_and_follow(tmp_path, capsys, flag):
    code, _, err = _run(capsys, _session(tmp_path), "--preset", "successor", flag)
    assert code == 2


def test_preset_refuses_unknown_name_and_non_claude_format(tmp_path, capsys):
    fp = _session(tmp_path)
    assert harness_main(["extract", str(fp), "--preset", "nope"]) != 0
    assert "nope" in capsys.readouterr().err
    codex = tmp_path / "rollout.jsonl"
    codex.write_text(json.dumps({"type": "session_meta", "payload": {"id": "x"}}) + "\n", encoding="utf-8")
    code, _, err = _run(capsys, codex, "--preset", "successor", "--format", "codex")
    assert code == 1 and "--preset successor" in err


def test_preset_values_are_the_documented_expansion():
    from nyxloom import cli

    assert cli.SUCCESSOR_PRESET_TEXT == (
        "--profile all --tool-calls intent-or-call --tool-errors show --edit-calls collapse "
        "--read-calls collapse --effect-calls always --timestamps gaps --path-aliases auto "
        "--strip-cd-prefix --ledger"
    )


# --- harness version warning ------------------------------------------------

def test_verified_versions_contain_the_fixture_version():
    assert "2.1.289" in harness.VERIFIED_HARNESS_VERSIONS


def test_no_recorded_version_means_no_warning(tmp_path):
    assert harness.version_warning(_session(tmp_path)) is None


def test_verified_version_means_no_warning(tmp_path):
    assert harness.version_warning(_session(tmp_path, "2.1.289")) is None


def test_unverified_version_warns_once_and_lists_every_version(tmp_path, capsys):
    fp = _session(tmp_path, "9.9.9")
    warning = harness.version_warning(fp)
    assert warning.startswith("[warning: harness v9.9.9 not verified for interrupt/denial detection")
    assert "may be incomplete" in warning
    _, out, _ = _run(capsys, fp, "--profile", "all")
    assert out.count("not verified for interrupt/denial detection") == 1
    assert out.startswith("[warning: harness v9.9.9")
    _, out, _ = _run(capsys, fp, "--successor-brief")
    assert out.count("not verified for interrupt/denial detection") == 1
    assert "> [warning: harness v9.9.9" in out.split("\n## ")[0]
    mixed = _write(tmp_path, [_user("u1", T0, "x", version="2.1.289"),
                              _user("u2", T0, "y", version="3.0.0")], "mixed.jsonl")
    assert "v3.0.0" in harness.version_warning(mixed) and "v2.1.289 " not in harness.version_warning(mixed)


def test_json_output_gets_the_warning_on_stderr_only(tmp_path, capsys):
    fp = _session(tmp_path, "9.9.9")
    code, out, err = _run(capsys, fp, "--json")
    assert code == 0 and "not verified" in err
    json.loads(out)


def test_interrupt_record_stays_a_stop_even_for_an_unverified_version(tmp_path, capsys):
    fp = _write(tmp_path, [
        _user("u1", T0, "go", version="9.9.9"),
        _user("u2", "2026-01-01T10:01:00Z", [{"type": "text", "text": "[Request interrupted by user for tool use]"}],
              version="9.9.9"),
    ])
    _, out, _ = _run(capsys, fp, "--profile", "all")
    assert "[STOP: harness interrupt record, not an operator message" in out


# --- agent-control ledger rule ---------------------------------------------

def test_agent_control_calls_are_ledger_effects(tmp_path, capsys):
    fp = _write(tmp_path, [
        _user("u1", T0, "orchestrate"),
        _assistant("a1", "2026-01-01T10:01:00Z", [_use("t1", "Agent", description="Survey the repo",
                                                       subagent_type="Explore", prompt="long prompt")]),
        _assistant("a2", "2026-01-01T10:02:00Z", [_use("t2", "SendMessage", to="abc123", summary="fix round 1",
                                                       message="do the fixes")], "m2"),
        _assistant("a3", "2026-01-01T10:03:00Z", [_use("t3", "TaskStop", task_id="abc123")], "m3"),
        _assistant("a4", "2026-01-01T10:04:00Z", [_use("t4", "TaskStop", task_id="gone")], "m4"),
        _user("r4", "2026-01-01T10:04:05Z", [{"type": "tool_result", "tool_use_id": "t4", "is_error": True,
                                              "content": "<tool_use_error>No task found with ID: gone</tool_use_error>"}]),
    ])
    _, out, _ = _run(capsys, fp, "--ledger")
    assert "agent launch [Explore]: Survey the repo" in out
    assert "agent message to abc123: fix round 1" in out
    assert "agent stop: abc123" in out
    assert "agent stop: gone [FAILED]" in out
    assert "external effects (4)" in out


def test_agent_control_line_shapes():
    assert ledger.agent_control_line("Agent", {"prompt": "p"}) == "agent launch [general-purpose]: p"
    assert ledger.agent_control_line("SendMessage", {"message": {"k": 1}}) == 'agent message to ?: {"k": 1}'
    assert ledger.agent_control_line("TaskStop", {}) == "agent stop: ?"
    assert ledger.agent_control_line("TaskStop", {"agent_id": "z"}) == "agent stop: z"
    assert ledger.agent_control_line("SendMessage", {"to": "q", "content": "c"}) == "agent message to q: c"

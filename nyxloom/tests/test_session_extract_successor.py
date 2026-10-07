"""nyxloom-SUCCESSOR (2026-10-06): `--tool-calls`, `--tool-errors`, the
whole-session ledger with external effects, stop-state detection and
`--successor-brief`. Fixtures under tests/fixtures/successor/ are minimal,
anonymised slices of real Agent-tool subagent transcripts:
  - agent-p5stopped000000  user-stopped probe (+ .meta.json stoppedByUser)
  - agent-described0000    Bash calls that carry a `description`
  - agent-lane0000000      a single-brief live-test lane: Bash calls WITHOUT
                           descriptions, a snapshot create, a failing ssh
                           loop, a heredoc that must not count, a timeout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import ExtractConfig, ledger, read_since_marker, toolresult
from nyxloom.session_extract.stopstate import build_stop_state

FIX = Path(__file__).parent / "fixtures" / "successor"
P5 = FIX / "agent-p5stopped000000.jsonl"
DESCRIBED = FIX / "agent-described0000.jsonl"
LANE = FIX / "agent-lane0000000.jsonl"


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _copy(tmp_path: Path, src: Path, name: str = "agent-copy.jsonl", meta: dict | None = None) -> Path:
    dst = tmp_path / name
    shutil.copy(src, dst)
    if meta is not None:
        dst.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return dst


def _write(tmp_path: Path, records: list[dict], name: str = "agent-t.jsonl") -> Path:
    fp = tmp_path / name
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def _rec(**kw):
    base = {"isSidechain": True, "sessionId": "s", "parentUuid": None}
    base.update(kw)
    return base


def _brief(text="do the thing"):
    return _rec(type="user", uuid="b1", timestamp="2026-01-01T00:00:00Z",
                message={"role": "user", "content": text})


def _call(uid, tid, name, ts, **inp):
    return _rec(type="assistant", uuid=uid, timestamp=ts, message={"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]})


def _result(uid, tid, ts, content, **extra):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    block.update({k: v for k, v in extra.items() if k == "is_error"})
    rec = _rec(type="user", uuid=uid, timestamp=ts, message={"role": "user", "content": [block]})
    rec.update({k: v for k, v in extra.items() if k != "is_error"})
    return rec


# --- --tool-calls -------------------------------------------------------

def test_tool_calls_default_none_renders_no_tool_events(capsys):
    code, out, _ = _run(capsys, DESCRIBED, "--profile", "all")
    assert code == 0
    assert "[tool call:" not in out


def test_tool_calls_intent_renders_only_the_calls_own_description(capsys):
    code, out, _ = _run(capsys, DESCRIBED, "--profile", "all", "--tool-calls", "intent")
    assert code == 0
    assert "[tool call: Bash] Check CPU pressure before doing anything on the shared host" in out
    assert "[tool call: Bash] Show last 3 commits and working tree status of lt-apt worktree" in out
    # the Read call has no intent -> no event at all (intent mode never falls back)
    assert "[tool call: Read]" not in out
    assert "$ head" not in out


def test_tool_calls_intent_or_call_falls_back_to_the_truncated_call(capsys):
    code, out, _ = _run(capsys, DESCRIBED, "--profile", "all", "--tool-calls", "intent-or-call")
    assert code == 0
    # intent present -> intent, not the command
    assert "[tool call: Bash] Check CPU pressure before doing anything on the shared host" in out
    assert "$ head -1 /proc/pressure/cpu" not in out
    # intent absent -> the call itself
    assert "[tool call: Read] /work/repo/notes.md" in out

    code, out, _ = _run(capsys, LANE, "--profile", "all", "--tool-calls", "intent-or-call")
    assert "[tool call: Bash] $ cd /work/repo/scratch; export NC_LANE=r1002; python3 nc.py scp-api" in out


def test_tool_calls_call_is_one_truncated_line_and_never_the_result(capsys):
    code, out, _ = _run(capsys, DESCRIBED, "--profile", "all", "--tool-calls", "call")
    assert code == 0
    assert "[tool call: Bash] $ head -1 /proc/pressure/cpu" in out
    assert "Check CPU pressure" not in out
    assert "some avg10=5.39 avg60" not in out.split("PSI and state")[0]  # result text never rendered
    call_lines = [ln for ln in out.splitlines() if "[tool call: Bash] $ cd /work/repo/.worktrees" in ln]
    assert len(call_lines) == 1


def test_call_summary_truncates_to_one_line_with_ellipsis():
    long = "echo " + "x" * 400
    s = toolresult.summarize_call("Bash", {"command": long + "\nsecond line"})
    assert "\n" not in s and len(s) == 160 and s.endswith("...")
    assert toolresult.summarize_call("Grep", {"pattern": "foo"}) == "pattern=foo"
    assert toolresult.summarize_call("Write", {"file_path": "/a/b.py", "content": "zzz"}) == "/a/b.py"
    assert toolresult.tool_intent({"intent": "  do   it "}) == "do it"
    assert toolresult.tool_intent("not-a-dict") == ""


def test_legacy_flags_keep_their_exact_output_and_warn(capsys):
    code, out, err = _run(capsys, DESCRIBED, "--profile", "all", "--show-tool-calls")
    assert code == 0
    assert "[tool call: Bash]\n" in out and "Check CPU" not in out
    assert "deprecated" in err
    code, out, _ = _run(capsys, DESCRIBED, "--profile", "all", "--show-tool-calls", "--show-tool-call-intent")
    assert "[tool call: Bash] Check CPU pressure before doing anything on the shared host" in out
    assert "[tool call: Read]\n" in out  # legacy: a label even without intent


def test_new_and_legacy_tool_call_spellings_are_mutually_exclusive(capsys):
    code, _, err = _run(capsys, DESCRIBED, "--tool-calls", "call", "--show-tool-calls")
    assert code == 2
    assert "pass only one spelling" in err


def test_tool_calls_rejected_for_non_claude_format(tmp_path, capsys):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                              "payload": {"id": "c1", "cwd": "/x"}}) + "\n", encoding="utf-8")
    code, _, err = _run(capsys, fp, "--format", "codex", "--tool-calls", "call")
    assert code == 1 and "--tool-calls is not supported for 'codex'" in err
    code, _, err = _run(capsys, fp, "--format", "codex", "--tool-errors", "hide")
    assert code == 1 and "--tool-errors is not supported for 'codex'" in err
    code, _, err = _run(capsys, fp, "--format", "codex", "--stop-state")
    assert code == 1 and "--stop-state does not support 'codex'" in err
    code, _, err = _run(capsys, fp, "--format", "codex", "--successor-brief")
    assert code == 1 and "--successor-brief does not support 'codex'" in err


def test_extract_config_validates_modes():
    with pytest.raises(ValueError):
        ExtractConfig(tool_calls="everything")
    with pytest.raises(ValueError):
        ExtractConfig(tool_errors="maybe")
    assert ExtractConfig().tool_call_mode == "none"
    assert ExtractConfig(show_tool_calls=True).tool_call_mode == "label"
    assert ExtractConfig(show_tool_calls=True, show_tool_call_intent=True).tool_call_mode == "label-intent"
    assert ExtractConfig(tool_calls="call", show_tool_calls=True).tool_call_mode == "call"


# --- --tool-errors ------------------------------------------------------

def test_tool_errors_default_show_renders_failed_results_without_tool_calls(capsys):
    code, out, _ = _run(capsys, LANE, "--profile", "all")
    assert code == 0
    assert "[tool call:" not in out  # --tool-calls is none ...
    # ... yet both failures are rendered, truncated, naming the tool
    # the failed call comes first (cleaned, one line), then the truncated error
    assert ("[tool error: Bash] $ cd ~/.ssh; for k in key1 key2; do echo == $k; ssh -F /dev/null "
            "-i $k -o BatchMode=yes root@203.0.113.7 'uname -r'; done => Exit code 255 "
            "Permission denied (publickey).") in out
    assert "=> Exit code 124 15:43:53 15:44:16 15:44:39" in out
    # successful results are never shown
    assert "brief text" not in out and "env.sh\n" not in out.replace("cat > env.sh", "")


def test_tool_errors_hide_drops_them(capsys):
    code, out, _ = _run(capsys, LANE, "--profile", "all", "--tool-errors", "hide")
    assert code == 0
    assert "[tool error" not in out


def test_tool_errors_are_truncated_to_240_chars(tmp_path, capsys):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="make"),
        _result("r1", "t1", "2026-01-01T00:00:02Z", "Exit code 2\n" + "boom " * 200, is_error=True),
    ])
    _, out, _ = _run(capsys, fp, "--profile", "all")
    line = next(ln for ln in out.splitlines() if "[tool error" in ln)
    assert line.endswith("...") and len(line) < 300


def test_failure_detection_variants():
    ok = {"type": "tool_result", "content": "Exit code 0\nfine"}
    assert not toolresult.is_failed(ok)
    assert toolresult.is_failed({"type": "tool_result", "content": "Exit code 3\nx"})  # text only, no is_error
    assert toolresult.is_failed({"type": "tool_result", "content": "<tool_use_error>nope</tool_use_error>"})
    assert toolresult.is_failed({"type": "tool_result", "content": "x", "is_error": True})
    assert toolresult.is_failed(ok, {"toolUseResult": {"interrupted": True}})
    assert toolresult.result_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"


def test_error_events_appear_in_json_output(capsys):
    code, out, _ = _run(capsys, LANE, "--profile", "all", "--json")
    assert code == 0
    texts = [e["text"] for e in json.loads(out)["events"] if e["kind"] == "tool_call"]
    assert any(t.startswith("[tool error: Bash] $ cd ~/.ssh") and "=> Exit code 255" in t for t in texts)


# --- stop markers: never an OPERATOR turn ----------------------------------

def test_interrupt_records_render_as_stop_not_operator(capsys):
    code, out, _ = _run(capsys, P5, "--profile", "all")
    assert code == 0
    operator_lines = [ln for ln in out.splitlines() if ln.startswith("OPERATOR:")]
    assert len(operator_lines) == 1 and "harness experiment" in operator_lines[0]
    assert "Request interrupted" not in "\n".join(operator_lines)
    assert "[STOP: harness interrupt record, not an operator message -- [Request interrupted by user for tool use]]" in out
    assert "[STOP: tool call rejected by the harness, not an operator message -- tool]" in out or \
        "[STOP: tool call rejected by the harness, not an operator message -- Bash:" in out


def test_stop_markers_survive_tool_errors_hide(capsys):
    _, out, _ = _run(capsys, P5, "--profile", "all", "--tool-errors", "hide")
    assert "[STOP:" in out


def test_stop_marker_names_the_rejected_call_when_calls_are_shown(capsys):
    _, out, _ = _run(capsys, P5, "--profile", "all", "--tool-calls", "call")
    assert "[tool call: Bash] $ python3 -c" in out
    assert "rejected by the harness, not an operator message -- Bash: $ python3 -c" in out


def test_stop_marker_is_not_a_ledger_boundary(capsys):
    _, out, _ = _run(capsys, P5, "--profile", "all", "--ledger")
    assert out.count("[session ledger") == 1


# --- whole-session ledger + external effects -------------------------------

def test_ledger_renders_whole_session_for_a_single_brief_agent(capsys):
    code, out, _ = _run(capsys, LANE, "--profile", "all", "--ledger")
    assert code == 0
    assert "[session ledger -- whole session]" in out
    assert "files read (1): " in out and "LT-LANE-BRIEF.md" in out
    assert "external effects (1) -- already done; verify by state, never repeat:" in out
    assert "python3 nc.py scp-api snapshots 799611 create --name lt-pre-r1002" in out
    # D2: the read-only `ssh ... uname -r` probe is NOT an effect; neither is the heredoc body `ssh -V`
    ledger_part = out.split("[session ledger")[1]
    assert "ssh -F" not in ledger_part and "ssh -V" not in ledger_part
    # the whole-session block precedes the closing cursor comment
    assert out.rstrip().splitlines()[-1].startswith("<!-- nyxloom-extract:")
    assert out.index("[session ledger") < out.rindex("<!-- nyxloom-extract:")


def test_ledger_whole_session_even_when_no_operator_boundary_is_kept(capsys):
    # --since the brief: the brief (the only OPERATOR turn) is not kept at all
    code, out, _ = _run(capsys, LANE, "--profile", "all", "--ledger", "--since", "l-u1")
    assert code == 0
    assert "OPERATOR:" not in out
    assert "[session ledger -- whole session]" in out
    assert "nc.py scp-api snapshots 799611 create" in out


def test_ledger_with_nothing_to_report_still_says_so(capsys):
    code, out, _ = _run(capsys, P5, "--profile", "all", "--ledger")
    assert code == 0
    assert "[session ledger -- whole session]\n(no files, commits, branches, tests or external effects recorded)" in out


def test_ledger_cursor_still_readable_for_since_file(tmp_path, capsys):
    _, out, _ = _run(capsys, LANE, "--profile", "all", "--ledger", "--stop-state")
    saved = tmp_path / "snap.md"
    saved.write_text(out, encoding="utf-8")
    assert read_since_marker(saved) == ("claude-code", "l-a6")


def test_rejected_effect_is_annotated_not_executed(tmp_path, capsys):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="git push origin main"),
        _result("r1", "t1", "2026-01-01T00:00:02Z", toolresult.DENIAL_PREFIX + ". rejected", is_error=True,
                toolDenialKind="user-rejected"),
    ])
    _, out, _ = _run(capsys, fp, "--profile", "all", "--ledger")
    assert "[00:00:01] git push origin main [REJECTED by harness: not executed]" in out


@pytest.mark.parametrize("command", [
    "git push origin main", "git -C /repo push --force", "git merge --no-ff feature", "git rebase main",
    "git tag v1.2.3", "scp a b:/c", "curl -X POST https://x/y",
    "curl --request DELETE https://x/y", "curl -s -d '{}' https://x/y",
    "python3 nc.py scp-api snapshots 1 create --name n", "python3 nc.py scp-api snapshots 1 delete id",
    "NC_LANE=r1002 python3 nc.py install-host --yes", "python3 nc.py scp-api server-details 1 power off",
    "python3 nc.py attach-iso 1 x.iso", "python3 nc.py boot-order set 1 cd",
    "docker rm -f c1", "docker stop c1", "docker run --rm img", "systemctl restart nginx",
    "apt-get install -y x", "sudo apt install y", "cd /x && git push", "nice -n 19 git push",
    # D2 wrapper / form coverage: m2 (VAR=x prefix), env, abs path, command, --no-pager, subshell, bash -c
    "GIT_X=1 git push", "env X=1 git push", "/usr/bin/git push", "command git push",
    "git --no-pager push", "(git push)", "bash -c 'git push'", "git reset --hard HEAD~1",
    "docker container rm c1", "docker volume rm v1", "docker network rm n1", "docker kill c1",
    "ls | xargs -r docker stop", "systemctl --user stop x", "dpkg -i x.deb", "dpkg --purge x",
    "apt-get dist-upgrade", "curl -X PUT https://x/y",
    # ssh/scp: only a MUTATING remote command (recursed into the quoted remote command)
    "ssh root@host 'systemctl restart x'", "ssh -p 22 h \"cd /x; git push\"", "ssh h docker rm c1",
    "scp -P 22 a b:/c",
])
def test_default_effect_patterns_match(command):
    assert ledger.external_effect(command), command


@pytest.mark.parametrize("command", [
    "git status", "git log --oneline -3", "git tag -l", "git tag --list 'v*'", "git diff --stat",
    "ssh-keygen -lf key.pub", "curl -s https://x/y", "curl -X GET https://x/y",
    "python3 nc.py scp-api snapshots 1", "python3 nc.py scp-api tasks --server-id 1",
    "docker ps", "docker logs c1", "echo git push", "grep -n 'git push' f.md",
    "cat > env.sh <<'EOF'\nssh -V\nsystemctl stop x\nEOF\nls", "ls -la", "",
    # D2: read-only forms
    "ssh root@host uname -r", "ssh h 'systemctl status x'", "scp h:/tmp/f .", "systemctl status x",
    "systemctl is-active x", "apt list --installed", "apt update", "apt-cache policy x",
    "dpkg -l", "git merge-base --is-ancestor a b", "git merge-base a b", "docker container ls",
    "docker image ls", "git remote -v",
    # quote-awareness: separators and verbs INSIDE quoted arguments are not commands
    "grep \"x; git push\" f", "echo 'a && docker rm c'", "git commit -m \"fix ssh && curl -X POST\"",
    "grep -n 'nc.py x snapshots 1 create' docs", "cat nc.py snapshots create",
    "ssh h \"grep 'a; git push' f\"", "bash -c 'git status'",
    # a stray custom flag is not a verb
    "python3 nc.py scp-api snapshots 1 --name lt-create-x",
])
def test_default_effect_patterns_do_not_match(command):
    assert not ledger.external_effect(command), command


def test_effect_pattern_is_configurable(tmp_path, capsys):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="terraform apply -auto-approve"),
        _result("r1", "t1", "2026-01-01T00:00:02Z", "ok"),
        _call("a2", "t2", "Bash", "2026-01-01T00:00:03Z", command="git push origin main"),
        _result("r2", "t2", "2026-01-01T00:00:04Z", "ok"),
    ])
    _, out, _ = _run(capsys, fp, "--profile", "all", "--ledger")
    assert "git push origin main" in out and "terraform" not in out.split("[session ledger")[1]
    _, out, _ = _run(capsys, fp, "--profile", "all", "--ledger", "--effect-pattern", r"^terraform\s+apply")
    tail = out.split("[session ledger")[1]
    assert "terraform apply -auto-approve" in tail and "git push origin main" in tail
    _, out, _ = _run(capsys, fp, "--profile", "all", "--ledger", "--no-default-effect-patterns",
                     "--effect-pattern", r"^terraform\s+apply")
    tail = out.split("[session ledger")[1]
    assert "terraform apply" in tail and "git push" not in tail


def test_effect_pattern_validation(tmp_path, capsys):
    code, _, err = _run(capsys, LANE, "--ledger", "--effect-pattern", "(")
    assert code == 1 and "--effect-pattern '(': invalid regex" in err
    code, _, err = _run(capsys, LANE, "--effect-pattern", "x")
    assert code == 2
    assert "only apply with --ledger or --successor-brief" in err


# --- stop state ------------------------------------------------------------

def test_stop_state_user_stopped_from_meta(capsys):
    state = build_stop_state(P5)
    assert state.stopped_by_user is True
    assert "stopped by the USER" in state.cause and "SendMessage" in state.cause
    assert state.in_flight == 'Bash: $ python3 -c "import time; time.sleep(3000)" # resume-exp-P5'
    assert "rejected by the harness" in state.in_flight_outcome
    code, out, _ = _run(capsys, P5, "--profile", "all", "--stop-state")
    assert code == 0
    assert "[stop state]" in out and "cause: stopped by the USER" in out
    assert "in-flight call: Bash: $ python3 -c" in out
    assert "last assistant text: (none)" in out


def test_stop_state_without_stopped_flag_does_not_claim_user(tmp_path):
    fp = _copy(tmp_path, P5, meta={"agentType": "general-purpose", "model": "sonnet"})
    state = build_stop_state(fp)
    assert state.stopped_by_user is None
    assert "stopped by the USER" not in state.cause
    assert "does not set stoppedByUser" in state.cause and "controller TaskStop" in state.cause

    fp2 = _copy(tmp_path, P5, name="agent-nometa.jsonl")
    assert "no `.meta.json` is available" in build_stop_state(fp2).cause

    fp3 = _copy(tmp_path, P5, name="agent-false.jsonl", meta={"stoppedByUser": False})
    s3 = build_stop_state(fp3)
    assert s3.stopped_by_user is False and "stopped by the USER" not in s3.cause


def test_stop_state_corrupt_meta_is_tolerated(tmp_path):
    fp = _copy(tmp_path, P5, name="agent-bad.jsonl")
    fp.with_suffix(".meta.json").write_text("{not json", encoding="utf-8")
    assert "no `.meta.json` is available" in build_stop_state(fp).cause


def test_stop_state_in_flight_call_without_result(tmp_path):
    fp = _write(tmp_path, [
        _brief(),
        _rec(type="assistant", uuid="a0", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "Writing the file, then stopping."}]}),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:02Z", command="sleep 3000"),
    ])
    state = build_stop_state(fp)
    assert state.in_flight == "Bash: $ sleep 3000"
    assert "no result recorded" in state.in_flight_outcome
    assert "tool call in flight" in state.cause
    assert state.last_text == "Writing the file, then stopping."


def test_stop_state_normal_end(capsys):
    state = build_stop_state(LANE)
    assert state.in_flight is None
    assert state.cause.startswith("ended normally")
    assert state.last_text.startswith("Lane r1002 stopped at the pre-flight kernel stop condition")
    assert state.last_text_ts == "2026-10-06T15:46:42.000Z"


def _denied_sleep_records(tail: list[dict]) -> list[dict]:
    denial = "The user doesn't want to proceed with this tool use. The tool use was rejected."
    return [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="sleep 100"),
        _result("u1", "t1", "2026-01-01T00:00:02Z", denial, is_error=True),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:03Z", message={
            "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}),
        *tail,
    ]


def test_stop_state_rejected_call_followed_by_operator_text_and_summary_is_not_in_flight(tmp_path):
    """A rejection counts as the tail only while nothing follows it."""
    fp = _write(tmp_path, _denied_sleep_records([
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:04Z",
             message={"role": "user", "content": "ok just summarise where you are"}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:05Z", message={
            "role": "assistant", "content": [{"type": "text", "text": "Summary: all done."}]}),
    ]))
    state = build_stop_state(fp)
    assert state.in_flight is None and state.in_flight_outcome is None
    assert state.cause.startswith("ended normally")
    assert "interrupted" not in state.cause and "sleep 100" not in state.render()


def test_stop_state_operator_text_after_the_interrupt_clears_the_stop_tail(tmp_path):
    fp = _write(tmp_path, _denied_sleep_records([
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:04Z",
             message={"role": "user", "content": [{"type": "text", "text": "carry on please"}]}),
    ]))
    state = build_stop_state(fp)
    assert state.in_flight is None
    assert "interrupted" not in state.cause and "last record kind: user_text" in state.cause
    # string-content operator message: same
    fp2 = _write(tmp_path, _denied_sleep_records([
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:04Z",
             message={"role": "user", "content": "carry on please"}),
    ]), name="agent-s.jsonl")
    assert "interrupted" not in build_stop_state(fp2).cause


def test_stop_state_rejected_call_as_the_tail_still_reads_interrupted(tmp_path):
    state = build_stop_state(_write(tmp_path, _denied_sleep_records([])))
    assert state.in_flight == "Bash: $ sleep 100"
    assert state.cause.startswith("interrupted")


def test_stop_state_rejects_json(capsys):
    code, _, err = _run(capsys, P5, "--stop-state", "--json")
    assert code == 2
    assert "--stop-state" in err


# --- --successor-brief ------------------------------------------------------

def _first_user_text(path: Path) -> str:
    return json.loads(path.read_text().splitlines()[0])["message"]["content"]


def test_successor_brief_section_order_and_content(tmp_path, capsys):
    order = tmp_path / "ORDER.md"
    order.write_text("Verify the snapshot by state; run LT-02 only.\n", encoding="utf-8")
    code, out, err = _run(capsys, LANE, "--successor-brief", "--order", f"@{order}")
    assert code == 0, err
    heads = [ln for ln in out.splitlines() if ln.startswith("## ")]
    assert heads == [
        "## Original brief (verbatim)",
        "## Extract of the predecessor's work",
        "## Ledger",
        "## Stop state",
        "## Your order",
    ]
    brief = _first_user_text(LANE)
    assert brief in out  # verbatim, byte-exact (multi-line, markdown, fence-safe)
    assert f"sha256 `{hashlib.sha256(brief.encode()).hexdigest()}`" in out
    assert out.count("NEVER touch nano1") == 1  # the extract does NOT repeat the brief
    # successor defaults: intent-or-call + errors + ledger incl. external effects
    # --effect-calls always: the outside-effect call is printed even though the mode is intent-or-call
    assert "[tool call: Bash] $ python3 nc.py scp-api snapshots 799611 create --name lt-pre-r1002" in out
    assert "=> Exit code 124" in out
    assert "external effects (1) -- already done; verify by state, never repeat:" in out
    assert "- cwd: /work/repo" in out and "- gitBranch: main" in out
    assert "cause: ended normally" in out
    assert out.rstrip().endswith("Verify the snapshot by state; run LT-02 only.")
    # the transcript is a subagent file with no meta: header says so by omission, but names the file
    assert str(LANE.absolute()) in out


def test_successor_brief_for_a_user_stopped_agent_includes_meta_and_stop_state(capsys):
    code, out, _ = _run(capsys, P5, "--successor-brief", "--order", "Do not retry the sleep.")
    assert code == 0
    assert "- stoppedByUser: true" in out and "- description: resume-exp P5 user-stop" in out
    assert "cause: stopped by the USER" in out
    assert "in-flight call: Bash: $ python3 -c" in out
    assert "[STOP:" in out
    assert not any(ln.startswith("OPERATOR:") and "interrupted" in ln for ln in out.splitlines())
    assert "## Your order\nDo not retry the sleep." in out


def test_successor_brief_without_order_has_no_order_section(capsys):
    _, out, _ = _run(capsys, LANE, "--successor-brief")
    assert "## Your order" not in out


def test_successor_brief_long_brief_is_referenced_by_path_and_sha256(capsys):
    code, out, _ = _run(capsys, LANE, "--successor-brief", "--brief-max-chars", "50")
    assert code == 0
    brief = _first_user_text(LANE)
    assert "## Original brief (not inlined: longer than 50 chars)" in out
    assert f"sha256 `{hashlib.sha256(brief.encode()).hexdigest()}`" in out
    assert str(LANE.absolute()) in out and "FIRST user record" in out
    assert "NEVER touch nano1" not in out


def test_successor_brief_fence_survives_backticks_in_brief(tmp_path, capsys):
    text = "Run ```bash\nls\n``` then ````inner```` carefully."
    fp = _write(tmp_path, [_brief(text), _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="ls"),
                           _result("r1", "t1", "2026-01-01T00:00:02Z", "x")])
    _, out, _ = _run(capsys, fp, "--successor-brief")
    section = out.split("## Original brief (verbatim)")[1].split("## Extract")[0]
    assert "`````text\n" + text + "\n`````" in section


def test_successor_brief_explicit_flags_override_defaults(capsys):
    _, out, _ = _run(capsys, LANE, "--successor-brief", "--tool-calls", "none", "--tool-errors", "hide")
    assert "[tool call:" not in out and "[tool error" not in out


def test_successor_brief_explicit_profile_and_legacy_call_flag_win(capsys):
    code, out, err = _run(capsys, DESCRIBED, "--successor-brief", "--profile", "operator-review",
                          "--show-tool-calls")
    assert code == 0, err
    assert "deprecated" in err
    assert "# Successor" in out or "Original brief" in out


def test_brief_max_chars_must_be_non_negative(capsys):
    code, _, err = _run(capsys, LANE, "--successor-brief", "--brief-max-chars", "-1")
    assert code == 2 and "non-negative" in err


def test_successor_brief_task_banner_wording(capsys):
    _, out, _ = _run(capsys, DESCRIBED, "--task", "Continue the lane.")
    assert "TASK FOR THIS SESSION" in out
    assert "authored by the operator" not in out
    assert "supplied by the requester of this extract" in out and "Continue the lane." in out


@pytest.mark.parametrize("extra", [["--json"], ["--task", "x"], ["--stop-state"], ["--follow"]])
def test_successor_brief_incompatible_flags(extra, capsys):
    code, _, err = _run(capsys, LANE, "--successor-brief", *extra)
    assert code == 2
    assert "--successor-brief" in err


def test_order_requires_successor_brief(capsys):
    code, _, err = _run(capsys, LANE, "--order", "x")
    assert code == 2
    assert "--order only applies with --successor-brief" in err


def test_order_file_missing_is_a_clean_error(tmp_path, capsys):
    code, _, err = _run(capsys, LANE, "--successor-brief", "--order", f"@{tmp_path / 'nope.md'}")
    assert code == 1 and "--order:" in err


def test_successor_brief_without_any_user_record(tmp_path, capsys):
    fp = _write(tmp_path, [_call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="ls")])
    code, _, err = _run(capsys, fp, "--successor-brief")
    assert code == 1 and "no user record" in err


def test_successor_brief_locates_a_bare_agent_id(tmp_path, capsys, monkeypatch):
    sess = tmp_path / ".claude" / "projects" / "-x" / "uuid-1" / "subagents"
    sess.mkdir(parents=True)
    agent = "a" + "0123456789abcdef"  # 17 hex chars
    shutil.copy(P5, sess / f"agent-{agent}.jsonl")
    shutil.copy(P5.with_suffix(".meta.json"), sess / f"agent-{agent}.meta.json")
    monkeypatch.setenv("HOME", str(tmp_path))
    code, out, err = _run(capsys, agent, "--successor-brief")
    assert code == 0, err
    assert "stopped by the USER" in out


# --- edge branches (coverage of the new modules) -------------------------

def _text_rec(kind, uid, ts, content):
    return _rec(type=kind, uuid=uid, timestamp=ts, message={"role": kind, "content": content})


def test_stop_state_tolerates_junk_lines_and_odd_blocks(tmp_path):
    fp = tmp_path / "agent-junk.jsonl"
    lines = [
        "",
        "{not json",
        json.dumps(_brief()),
        json.dumps(_text_rec("assistant", "a1", "2026-01-01T00:00:01Z",
                             ["junk", {"type": "text", "text": "  "}, {"type": "tool_use", "name": "Bash",
                                                                       "input": {"command": "ls"}}])),
        json.dumps(_text_rec("user", "u1", "2026-01-01T00:00:02Z", ["junk", {"type": "image"}])),
    ]
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = build_stop_state(fp)
    # the call has no id -> anonymous id; no result was recorded for it
    assert state.in_flight == "Bash: $ ls"
    assert state.last_text == ""
    assert "last assistant text: (none)" in state.render()


def test_stop_state_failed_result_is_not_in_flight_and_user_text_is_not_a_stop(tmp_path):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="false"),
        _result("u1", "t1", "2026-01-01T00:00:02Z", "Exit code 1\nboom"),
        _text_rec("user", "u2", "2026-01-01T00:00:03Z", [{"type": "text", "text": "carry on please"}]),
    ])
    state = build_stop_state(fp)
    assert state.in_flight is None
    assert "last record kind: user_text" in state.cause
    assert "in-flight call: none" in state.render()


def test_stop_state_plain_string_user_records(tmp_path):
    base = [_brief(), _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="ls"),
            _result("u1", "t1", "2026-01-01T00:00:02Z", "ok")]
    plain = _write(tmp_path, base + [_text_rec("user", "u2", "2026-01-01T00:00:03Z", "hello again")],
                   name="agent-plain.jsonl")
    assert "last record kind: user_text" in build_stop_state(plain).cause
    stopped = _write(tmp_path, base + [_text_rec("user", "u2", "2026-01-01T00:00:03Z",
                                                  "[Request interrupted by user]")],
                     name="agent-intr.jsonl")
    assert "no `.meta.json` is available" in build_stop_state(stopped).cause
    assert "interrupted" in build_stop_state(stopped).cause


def test_stop_state_ignores_other_record_types_and_callless_sessions(tmp_path):
    fp = _write(tmp_path, [
        _brief(),
        _rec(type="system", uuid="s1", timestamp="2026-01-01T00:00:01Z", content="note"),
        _text_rec("user", "u1", "2026-01-01T00:00:02Z", None),
        _text_rec("assistant", "a1", "2026-01-01T00:00:03Z", [{"type": "text", "text": "All done."}]),
    ])
    state = build_stop_state(fp)
    assert state.in_flight is None
    assert state.cause.startswith("ended normally")
    assert state.last_text == "All done."


def test_ledger_effect_from_a_call_without_an_id(tmp_path, capsys):
    block = {"type": "tool_use", "name": "Bash", "input": {"command": "git push origin main"}}
    fp = _write(tmp_path, [
        _brief(),
        _text_rec("assistant", "a1", "2026-01-01T00:00:01Z", [block]),
    ])
    code, out, err = _run(capsys, fp, "--ledger", "--profile", "all")
    assert code == 0, err
    assert "git push origin main" in out


def test_stop_state_ended_after_tool_result_only(tmp_path):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="ls"),
        _result("u1", "t1", "2026-01-01T00:00:02Z", "fine"),
    ])
    assert "last record kind: user_result" in build_stop_state(fp).cause


def test_first_user_record_list_content_and_skips(tmp_path):
    from nyxloom.session_extract.successor import first_user_record

    fp = tmp_path / "agent-lists.jsonl"
    lines = [
        "", "{bad",
        json.dumps(_rec(type="assistant", uuid="x", message={"content": "no"})),
        json.dumps(_text_rec("user", "e1", "t", 42)),                      # unusable content type
        json.dumps(_text_rec("user", "e2", "t", [{"type": "text", "text": "   "}])),  # blank text
        json.dumps(_text_rec("user", "b1", "t", ["junk", {"type": "text", "text": "part one "},
                                                  {"type": "text", "text": "part two"}])),
    ]
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert first_user_record(fp) == ("b1", "part one part two", 6)  # the real physical line
    empty = tmp_path / "agent-none.jsonl"
    empty.write_text("", encoding="utf-8")
    assert first_user_record(empty) is None


def test_toolresult_odd_shapes():
    assert toolresult.result_text({"content": [{"type": "image"}]}) == json.dumps([{"type": "image"}])
    assert toolresult.result_text({}) == ""
    assert toolresult.result_text({"content": {"a": 1}}) == '{"a": 1}'
    assert toolresult.summarize_call("Task", {"x": {1, 2}}) == ""        # unserialisable input
    assert toolresult.summarize_call("Task", {"x": 1}) == '{"x": 1}'
    assert toolresult.summarize_call("Grep", {"pattern": "foo"}) == "pattern=foo"
    assert toolresult.summarize_call("Read", {"file_path": "/a/b"}) == "/a/b"
    assert toolresult.summarize_call("Bash", "ls") == "ls"
    assert toolresult.summarize_call("Bash", 7) == ""


def test_session_ledger_renders_branches_and_tests(tmp_path, capsys):
    fp = _write(tmp_path, [
        _brief(),
        _call("a1", "t1", "Bash", "2026-01-01T00:00:01Z", command="git checkout -b feat/x"),
        _result("u1", "t1", "2026-01-01T00:00:02Z", "Switched to a new branch"),
        _call("a2", "t2", "Bash", "2026-01-01T00:00:03Z", command="pytest -q"),
        _result("u2", "t2", "2026-01-01T00:00:04Z", "12 passed in 1.2s"),
        _call("a3", "t3", "Bash", "2026-01-01T00:00:05Z", command=["not", "a", "string"]),
    ])
    code, out, err = _run(capsys, fp, "--ledger", "--profile", "all")
    assert code == 0, err
    assert "branches involved: feat/x" in out
    assert "tests: " in out and "passed" in out

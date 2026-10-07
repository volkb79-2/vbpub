"""nyxloom SUCCESSOR-2 (2026-10): the compression/selection OPTIONS
(`--strip-cd-prefix`, `--path-aliases`, `--edit-calls`, `--read-calls`,
`--effect-calls`, `--timestamps`), Edit/Write `Intent:` pairing, call-first
error lines, the exact interrupt match, and the quote-aware shell analysis
(shellcmd.py) behind the effect/read classification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import compress, ledger, shellcmd, toolresult
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.successor import (
    assemble, brief_section, first_user_record, transcript_context,
)

T0 = "2026-01-01T10:00:00Z"


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _rec(**kw):
    base = {"isSidechain": True, "sessionId": "s", "parentUuid": None, "cwd": "/w/repo/.worktrees/wt1/sub"}
    base.update(kw)
    return base


def _ts(minute, sec=0):
    return f"2026-01-01T10:{minute:02d}:{sec:02d}Z"


def _brief(text="do the thing"):
    return _rec(type="user", uuid="b1", timestamp=T0, message={"role": "user", "content": text})


def _blocks(uid, ts, blocks, msg="m1"):
    return _rec(type="assistant", uuid=uid, timestamp=ts,
                message={"role": "assistant", "id": msg, "content": blocks})


def _use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _text(t):
    return {"type": "text", "text": t}


def _result(uid, tid, ts, content, **block_extra):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content, **block_extra}
    return _rec(type="user", uuid=uid, timestamp=ts, message={"role": "user", "content": [block]})


def _write(tmp_path, records, name="agent-c.jsonl"):
    fp = tmp_path / name
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


# --- interrupt classification (blocker 1) ----------------------------------

@pytest.mark.parametrize("text", [
    "[Request interrupted by user]", "[Request interrupted by user for tool use]",
    "  [Request interrupted by user for tool use]\n",
])
def test_interrupt_text_exact_synthetic_strings(text):
    assert toolresult.is_interrupt_text(text)


@pytest.mark.parametrize("text", [
    "[Request interrupted by user for tool use] is what I keep seeing. Please continue.",
    "[Request interrupted by user] and more", "see [Request interrupted by user]",
    "[Request interrupted by user for", "",
])
def test_interrupt_text_not_a_prefix_match(text):
    assert not toolresult.is_interrupt_text(text)


def test_operator_message_starting_with_interrupt_text_stays_operator(tmp_path, capsys):
    msg = "[Request interrupted by user for tool use] is what I keep seeing. Do NOT touch the prod db."
    fp = _write(tmp_path, [_brief(), _rec(type="user", uuid="u2", timestamp=_ts(1),
                                          message={"role": "user", "content": msg})])
    code, out, _ = _run(capsys, fp, "--profile", "all")
    assert code == 0
    assert f"OPERATOR: [00:01] {msg}".replace("[00:01]", "[10:01:00]") in out
    assert "[STOP" not in out


def test_exact_interrupt_record_is_a_stop_event(tmp_path, capsys):
    fp = _write(tmp_path, [_brief(), _rec(type="user", uuid="u2", timestamp=_ts(1), message={
        "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]})])
    _, out, _ = _run(capsys, fp, "--profile", "all")
    assert "[STOP: harness interrupt record, not an operator message" in out


# --- is_failed anchoring (finding 7) ---------------------------------------

def test_tool_use_error_must_lead_the_result():
    assert toolresult.is_failed({"content": "<tool_use_error>File not found</tool_use_error>"})
    assert toolresult.is_failed({"content": "  \n<tool_use_error>x"})
    assert not toolresult.is_failed({"content": "source: x = '<tool_use_error>' in a Read of code"})


# --- toolresult helpers ----------------------------------------------------

def test_one_line_exact_length_is_not_truncated():
    assert toolresult.one_line("abcde", 5) == "abcde"
    assert toolresult.one_line("abcdef", 5) == "ab..."
    assert toolresult.one_line("abcdef", 0) == "abcdef"


def test_is_denial_needs_the_rec_or_the_prefix():
    assert toolresult.is_denial("x", {"toolDenialKind": "k"})
    assert not toolresult.is_denial("x", {})
    assert not toolresult.is_denial("x", None)
    assert toolresult.is_denial(toolresult.DENIAL_PREFIX + " ...")


def test_strip_cd_variants():
    assert toolresult.strip_cd("cd /a/b && ls") == "ls"
    assert toolresult.strip_cd("cd /a/b; ls") == "ls"
    assert toolresult.strip_cd('cd "/a b" && cd /c && ls') == "ls"
    assert toolresult.strip_cd("echo cd /x && ls") == "echo cd /x && ls"
    assert toolresult.strip_cd("cd /a/b") == "cd /a/b"


def test_apply_aliases_longest_first_and_boundary():
    aliases = (("REPO", "/w/repo"), ("WT", "/w/repo/.worktrees/wt1"))
    assert toolresult.apply_aliases("ls /w/repo/.worktrees/wt1/x /w/repo/y", aliases) == "ls $WT/x $REPO/y"
    assert toolresult.apply_aliases("/w/repository", aliases) == "/w/repository"
    assert toolresult.apply_aliases("a/w/repo", aliases) == "a/w/repo"
    assert toolresult.apply_aliases("/w/repo", aliases) == "$REPO"
    assert toolresult.apply_aliases("/w/repo/", (("R", "/w/repo/"),)) == "$R/"
    assert toolresult.apply_aliases("/x", (("E", ""),)) == "/x"


def test_summarize_call_cleans_before_truncating():
    call = {"command": "cd /w/repo && " + "x" * 50}
    plain = toolresult.summarize_call("Bash", call, limit=20)
    cleaned = toolresult.summarize_call("Bash", call, limit=20, strip_cd_prefix=True)
    assert plain == "$ cd /w/repo && x..."
    assert cleaned == "$ " + "x" * 15 + "..."
    assert toolresult.summarize_call("Read", {"file_path": "/w/repo/a"}, aliases=(("REPO", "/w/repo"),)) == "$REPO/a"
    assert toolresult.summarize_call("Grep", {"pattern": "/w/repo/a"}, aliases=(("REPO", "/w/repo"),)) == "pattern=$REPO/a"
    assert toolresult.summarize_call("Task", {"x": "/w/repo"}, aliases=(("REPO", "/w/repo"),)) == '{"x": "$REPO"}'


def test_tool_intent_aliases_before_truncation_and_falls_back_to_intent_key():
    assert toolresult.tool_intent({"description": "see /w/repo/a"}, (("REPO", "/w/repo"),)) == "see $REPO/a"
    assert toolresult.tool_intent({"description": "x" * 300}) == "x" * 240
    assert toolresult.tool_intent({"description": "", "intent": "why"}) == "why"
    assert toolresult.tool_intent({}) == ""


def test_paired_intent_takes_the_last_line_only():
    assert toolresult.paired_intent("Intent: change a  and b") == ("change a and b", "")
    assert toolresult.paired_intent("Some prose.\nIntent: do it\n") == ("do it", "Some prose.")
    assert toolresult.paired_intent("Intent: first\nthen prose") == ("", "Intent: first\nthen prose")
    assert toolresult.paired_intent("no intent here") == ("", "no intent here")
    assert toolresult.paired_intent("Intent:") == ("", "Intent:")


def test_tool_kind_classes():
    pats = shellcmd.DEFAULT_EFFECT_PATTERNS
    assert toolresult.tool_kind("Edit", {}, pats) == "edit"
    assert toolresult.tool_kind("MultiEdit", {}, pats) == "edit"
    assert toolresult.tool_kind("Read", {}, pats) == "read"
    assert toolresult.tool_kind("Grep", {}, pats) == "read"
    assert toolresult.tool_kind("Bash", {"command": "git push"}, pats) == "effect"
    assert toolresult.tool_kind("Bash", {"command": "ls -la"}, pats) == "read"
    assert toolresult.tool_kind("Bash", {"command": "make build"}, pats) == "other"
    assert toolresult.tool_kind("Bash", {"command": 7}, pats) == "other"
    assert toolresult.tool_kind("Agent", {"prompt": "x"}, pats) == "other"
    assert toolresult.tool_kind("Bash", {"command": "scp a h:/x"}, pats, scp_uploads=False) == "other"


# --- shellcmd --------------------------------------------------------------

@pytest.mark.parametrize("command, expected", [
    ("ls; git push", ["ls", "git push"]),
    ("a && b || c | d", ["a", "b", "c", "d"]),
    ("grep 'a; b' f && ls", ["grep 'a; b' f", "ls"]),
    ('echo "x && y"', ['echo "x && y"']),
    ('echo "esc \\" ; still quoted" ; ls', ['echo "esc \\" ; still quoted"', "ls"]),
    ("echo a\\;b", ["echo a\\;b"]),
    ("sleep 1 & ls", ["sleep 1", "ls"]),
    ("cmd 2>&1 | tee x", ["cmd 2>&1", "tee x"]),
    ("cmd &> /dev/null", ["cmd &> /dev/null"]),
    ("(a; b)", ["a", "b"]),
    ("echo `date` x", ["echo", "date", "x"]),
    ("a\nb", ["a", "b"]),
    ("cat <<EOF\ngit push\nEOF\nls", ["cat", "ls"]),
])
def test_split_segments(command, expected):
    assert shellcmd.split_segments(command) == expected


@pytest.mark.parametrize("seg, expected", [
    ("GIT_X=1 git push", "git push"),
    ('X="a b" git push', "git push"),
    ("sudo -n git push", "git push"),
    ("nice -n 19 ionice -c 3 git push", "git push"),
    ("timeout 30 git push", "git push"),
    ("time nohup exec command git push", "git push"),
    ("env -i -u A X=1 git push", "git push"),
    ("/usr/bin/git push", "git push"),
    ("./nc.py x", "nc.py x"),
    ("xargs -r docker stop", "docker stop"),
    ("if git push", "git push"),
    ("docker stop a/b", "docker stop a/b"),
])
def test_strip_wrappers(seg, expected):
    assert shellcmd.strip_wrappers(seg) == expected


def test_normalized_segments_recurse_into_bash_c_with_a_depth_bound():
    assert shellcmd.normalized_segments("bash -c 'cd x; git push'") == ["cd x", "git push"]
    assert shellcmd.normalized_segments('sh -lc "ls"') == ["ls"]
    deep = "bash -c \"bash -c 'bash -c \\\"bash -c ls\\\"'\""
    assert shellcmd.normalized_segments(deep)  # terminates; depth-bounded
    assert shellcmd.normalized_segments("bash -c") == ["bash -c"]


def test_ssh_and_scp_helpers():
    assert shellcmd.ssh_remote_command("ssh -p 22 -i k h 'git push'") == "git push"
    assert shellcmd.ssh_remote_command("ssh h git push origin") == "git push origin"
    assert shellcmd.ssh_remote_command("ssh h") is None
    assert shellcmd.ssh_remote_command("ssh h 'unterminated") is None
    assert shellcmd.ssh_remote_command("ssh -v h ls") == "ls"
    assert shellcmd.is_scp_upload("scp a h:/p")
    assert shellcmd.is_scp_upload("scp -P 22 -i k a b u@h:p")
    assert not shellcmd.is_scp_upload("scp h:/p .")
    assert not shellcmd.is_scp_upload("scp a /local/b:c")
    assert not shellcmd.is_scp_upload("scp a")
    assert not shellcmd.is_scp_upload("scp 'a")


def test_effect_segments_report_the_segment_and_scp_flag():
    assert shellcmd.effect_segments("cd x && git push origin main") == ["git push origin main"]
    assert shellcmd.effect_segments("scp a h:/p", scp_uploads=False) == []
    assert shellcmd.effect_segments("git status", patterns=[r"^git"]) == ["git status"]
    assert shellcmd.effect_segments("ssh h uname", patterns=[r"^ssh"]) == ["ssh h uname"]
    deep = "ssh a 'ssh b \"ssh c \\\"ssh d git push\\\"\"'"
    assert shellcmd.effect_segments(deep) in ([], [deep])  # bounded recursion, no crash


@pytest.mark.parametrize("command, read_only", [
    ("ls -la", True), ("cd /x && cat f | grep y | wc -l", True), ("git status", True),
    ("git branch --list", True), ("git branch -D x", False), ("git push", False),
    ("sed -n 1,5p f", True), ("sed -i s/a/b/ f", False), ("find . -name x", True),
    ("find . -delete", False), ("find . -exec rm {} ;", False), ("echo hi > f", False),
    ("echo hi 2>&1 > /dev/null", True), ("ls 2>/dev/null", True), ("make", False),
    ("docker ps", True), ("docker rm x", False), ("systemctl status x", True),
    ("systemctl restart x", False), ("ssh h uname -a", True), ("ssh h 'rm -rf /x'", False),
    ("ssh h", False), ("dpkg -l", True), ("apt list", True), ("pip list", True), ("", False),
    ("ls && make", False), ("ssh h 'ls; cat f'", True),
])
def test_is_read_only(command, read_only):
    assert shellcmd.is_read_only(command) is read_only


# --- collapse stage --------------------------------------------------------

def _ev(i, text, kind=EventKind.TOOL_CALL, **meta):
    return NormalizedEvent(i, f"m{i}", f"2026-01-01T10:0{i}:00Z", kind, text, meta={k: v for k, v in meta.items()})


def test_collapse_noop_and_edit_runs():
    edits = [_ev(1, "e1", tool_kind="edit", tool_file="/a", tool_intent="one", gap_after="2"),
             _ev(2, "e2", tool_kind="edit", tool_file="/a", tool_intent="two"),
             _ev(3, "e3", tool_kind="edit", tool_file="/a", tool_intent="one", gap_after="4")]
    assert compress.collapse_tool_calls(edits, "show", "show") == edits
    out = compress.collapse_tool_calls(edits, "collapse", "show")
    assert [e.text for e in out] == ["[edited /a x3: one; two]"]
    assert out[0].meta["gap_after"] == "4" and out[0].meta["collapsed"] == "3" and out[0].marker == "m1"
    two_files = compress.collapse_tool_calls(
        [_ev(1, "x", tool_kind="edit", tool_file="/a"), _ev(2, "y", tool_kind="edit", tool_file="/b")],
        "collapse", "show")
    assert [e.text for e in two_files] == ["[edited /a]", "[edited /b]"]
    unknown = compress.collapse_tool_calls([_ev(1, "x", tool_kind="edit")], "collapse", "show")
    assert unknown[0].text == "[edited (unknown file)]"
    no_gap = compress.collapse_tool_calls(
        [_ev(1, "x", tool_kind="edit", tool_file="/a", gap_after="3"), _ev(2, "y", tool_kind="edit", tool_file="/a")],
        "collapse", "show")
    assert "gap_after" not in no_gap[0].meta


def test_collapse_reads_and_run_breakers():
    r = lambda i: _ev(i, "r", tool_kind="read")
    out = compress.collapse_tool_calls(
        [r(1), r(2), _ev(3, "prose", EventKind.ASSISTANT_TEXT), r(4),
         _ev(5, "err", tool_kind="read", tool_error="true"), r(6), _ev(7, "eff", tool_kind="effect")],
        "show", "collapse")
    assert [e.text for e in out] == [
        "[oriented: 2 reads]", "prose", "[oriented: 1 read]", "err", "[oriented: 1 read]", "eff"]
    assert compress.collapse_tool_calls([r(1)], "collapse", "show")[0].text == "r"  # reads untouched
    # an edit run followed by a read run: each collapses on its own
    mixed = compress.collapse_tool_calls(
        [_ev(1, "e", tool_kind="edit", tool_file="/a"), r(2)], "collapse", "collapse")
    assert [e.text for e in mixed] == ["[edited /a]", "[oriented: 1 read]"]


# --- alias detection / resolution ------------------------------------------

def test_detect_and_resolve_aliases(tmp_path):
    scratch = "/tmp/claude-1003/-w-repo/abc-123/scratchpad"
    fp = _write(tmp_path, [_brief(f"notes in {scratch}/n.md and {scratch}/m.md"), _rec(type="user", uuid="x")])
    found = compress.detect_aliases(fp)
    assert found == {"REPO": "/w/repo", "WT": "/w/repo/.worktrees/wt1", "SCRATCH": scratch}
    plain = _write(tmp_path, [{"type": "user", "cwd": "/srv/app", "uuid": "u"}], "agent-p.jsonl")
    assert compress.detect_aliases(plain) == {"REPO": "/srv/app"}
    assert compress.resolve_aliases(None, fp) == () and compress.resolve_aliases("none", fp) == ()
    assert dict(compress.resolve_aliases("auto,WT=/other", fp))["WT"] == "/other"
    assert dict(compress.resolve_aliases("auto, $X=/x", fp))["X"] == "/x"
    assert compress.resolve_aliases("auto,none", fp) == ()
    for bad in ("nonsense", "1x=/y", "A="):
        with pytest.raises(ValueError):
            compress.resolve_aliases(bad, fp)


# --- end to end through the CLI -------------------------------------------

def _session(tmp_path):
    recs = [
        _brief(),
        _blocks("a1", _ts(1), [_use("t1", "Bash", command="cd /w/repo/.worktrees/wt1/sub && ls", description="List it")], "m1"),
        _result("r1", "t1", _ts(1, 5), "ok"),
        _blocks("a2", _ts(2), [_use("t2", "Read", file_path="/w/repo/.worktrees/wt1/a.py")], "m2"),
        _result("r2", "t2", _ts(2, 5), "x"),
        _blocks("a3", _ts(3), [_text("Intent: switch the default to 7 because it is the cap")], "m3"),
        _blocks("a4", _ts(3, 1), [_use("t3", "Edit", file_path="/w/repo/.worktrees/wt1/a.py", old_string="a", new_string="b")], "m3"),
        _result("r3", "t3", _ts(3, 5), "edited"),
        _blocks("a5", _ts(4), [_text("Intent: and the doc too")], "m4"),
        _blocks("a6", _ts(4, 1), [_use("t4", "Edit", file_path="/w/repo/.worktrees/wt1/a.py", old_string="c", new_string="d")], "m4"),
        _result("r4", "t4", _ts(4, 5), "edited"),
        _blocks("a7", _ts(30), [_use("t5", "Bash", command="git push origin main", description="Publish")], "m5"),
        _result("r5", "t5", _ts(30, 5), "Exit code 1\nrejected", is_error=True),
    ]
    return _write(tmp_path, recs)


def test_default_call_event_texts_without_options(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent-or-call")
    assert "[tool call: Bash] List it" in out
    assert "[tool call: Read] /w/repo/.worktrees/wt1/a.py" in out
    assert "[tool call: Edit] switch the default to 7 because it is the cap" in out
    assert "[tool call: Edit] and the doc too" in out
    assert "Intent:" not in out  # the paired Intent line is shown on its call, once
    assert "[tool call: Bash] Publish" in out and "$ git push" not in out.split("[tool error")[0]


def test_intent_lines_pair_only_within_one_message_and_to_edit_tools(tmp_path, capsys):
    recs = [
        _brief(),
        _blocks("a1", _ts(1), [_text("Intent: for another message")], "mA"),
        _blocks("a2", _ts(1, 1), [_use("t1", "Edit", file_path="/a", old_string="x", new_string="y")], "mB"),
        _blocks("a3", _ts(2), [_text("Intent: for a bash")], "mC"),
        _blocks("a4", _ts(2, 1), [_use("t2", "Bash", command="make")], "mC"),
        _blocks("a5", _ts(3), [_text("Intent: first"), _use("t3", "Edit", file_path="/b", old_string="x", new_string="y"),
                               _use("t4", "Edit", file_path="/c", old_string="x", new_string="y")], "mD"),
        _blocks("a6", _ts(4), [_text("Intent: stale"), ], "mE"),
        _rec(type="user", uuid="u9", timestamp=_ts(4, 2), message={"role": "user", "content": "ok"}),
        _blocks("a7", _ts(5), [_use("t5", "Edit", file_path="/d", old_string="x", new_string="y")], "mE"),
        _blocks("a8", _ts(6), [_text("Some prose\nIntent: with prose first"),
                               _use("t6", "Write", file_path="/e", content="z")], "mF"),
    ]
    fp = _write(tmp_path, recs)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent-or-call")
    assert "[tool call: Edit] /a" in out            # different message: not paired
    assert "[tool call: Bash] $ make" in out        # Bash keeps its own (absent) intent
    assert "[tool call: Edit] first" in out and "[tool call: Edit] /c" in out  # only the first Edit pairs
    assert "[tool call: Edit] /d" in out            # a user record clears the pending intent
    assert "[tool call: Write] with prose first" in out and "Some prose" in out
    _, out2, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent")
    assert "[tool call: Edit] /a" not in out2 and "[tool call: Edit] first" in out2
    _, out3, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call")
    assert "Intent: first" in out3  # `call` mode does not show intent: the text stays as prose


def test_unpaired_intent_lines_stay_in_the_prose(tmp_path, capsys):
    """Round-3 blocker (ruling D5): only an Intent line actually paired to an
    Edit/Write call is removed from the prose."""
    recs = [
        _brief(),
        # (1) Intent followed by a Bash call (same message): not paired
        _blocks("a1", _ts(1), [_text("Plan is set.\nIntent: run the destructive migration on staging")], "m1"),
        _blocks("a2", _ts(1, 1), [_use("t1", "Bash", command="./migrate.sh --apply")], "m1"),
        _result("r1", "t1", _ts(1, 2), "done"),
        # (2) Intent as the last text of a message, nothing after it
        _blocks("a3", _ts(2), [_text("Finished the first part.\nIntent: next I will drop the old table")], "m2"),
    ]
    fp = _write(tmp_path, recs)
    for mode in ("intent", "intent-or-call"):
        _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", mode)
        assert "Intent: run the destructive migration on staging" in out, mode
        assert "Intent: next I will drop the old table" in out, mode
    # (3) an Edit whose call is omitted renders no intent, so the line stays
    omitted = [
        _brief(),
        _blocks("a1", _ts(1), [_text("Intent: rewrite the config"),
                               _use("t1", "Edit", file_path="/a", old_string="x", new_string="y")], "m1"),
    ]
    fp2 = _write(tmp_path, omitted, "agent-o.jsonl")
    _, out2, _ = _run(capsys, fp2, "--profile", "all", "--tool-calls", "intent-or-call", "--edit-calls", "omit")
    assert "Intent: rewrite the config" in out2 and "[tool call: Edit]" not in out2
    # ...and with the call shown the same line is NOT duplicated: exactly once
    for extra in ((), ("--edit-calls", "collapse")):
        _, out3, _ = _run(capsys, fp2, "--profile", "all", "--tool-calls", "intent-or-call", *extra)
        assert out3.count("rewrite the config") == 1, extra
        assert "Intent:" not in out3


def test_paired_intent_with_prose_before_it_renders_prose_and_intent_once(tmp_path, capsys):
    recs = [
        _brief(),
        _blocks("a1", _ts(1), [_text("Some prose first.\nIntent: change the default"),
                               _use("t1", "Write", file_path="/e", content="z")], "m1"),
    ]
    _, out, _ = _run(capsys, _write(tmp_path, recs), "--profile", "all", "--tool-calls", "intent-or-call")
    assert "Some prose first." in out and "Intent:" not in out
    assert out.count("change the default") == 1


def test_edit_calls_collapse_and_omit(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent", "--edit-calls", "collapse")
    assert "[edited /w/repo/.worktrees/wt1/a.py x2: switch the default to 7 because it is the cap; and the doc too]" in out
    assert "[tool call: Edit]" not in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--edit-calls", "omit")
    assert "Edit" not in out.replace("[tool error", "") and "[tool call: Bash]" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--edit-calls", "show")
    assert out.count("[tool call: Edit]") == 2


def test_read_calls_collapse_counts_reads_even_in_intent_mode(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent", "--read-calls", "collapse")
    assert "[oriented: 2 reads]" in out  # the described `ls` and the intent-less Read
    assert "[tool call: Read]" not in out and "List it" not in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent", "--read-calls", "show")
    assert "[tool call: Read]" not in out and "[tool call: Bash] List it" in out


def test_effect_calls_always_prints_the_call_in_intent_mode(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent", "--effect-calls", "always")
    assert "[tool call: Bash] $ git push origin main -- Publish" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent", "--effect-calls", "mode")
    assert "[tool call: Bash] Publish" in out and "$ git push origin main -- Publish" not in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "intent-or-call", "--effect-calls", "always",
                     "--strip-cd-prefix", "--path-aliases", "WT=/w/repo/.worktrees/wt1")
    assert "[tool call: Bash] $ git push origin main -- Publish" in out


def test_strip_cd_prefix_and_path_aliases_apply_before_truncation(tmp_path, capsys):
    recs = [_brief(), _blocks("a1", _ts(1), [_use("t1", "Bash", command="cd /w/repo/.worktrees/wt1/sub && cat /w/repo/.worktrees/wt1/a.py")]),
            _result("r1", "t1", _ts(1, 5), "boom", is_error=True)]
    fp = _write(tmp_path, recs)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call")
    assert "$ cd /w/repo/.worktrees/wt1/sub && cat /w/repo/.worktrees/wt1/a.py" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--strip-cd-prefix")
    assert "[tool call: Bash] $ cat /w/repo/.worktrees/wt1/a.py" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--strip-cd-prefix", "--path-aliases", "auto")
    assert "[tool call: Bash] $ cat $WT/a.py" in out
    assert "[tool error: Bash] $ cat $WT/a.py => boom" in out  # the error line carries the cleaned call
    code, _, err = _run(capsys, fp, "--profile", "all", "--path-aliases", "bogus")
    assert code == 1 and "--path-aliases" in err


def test_error_lines_carry_the_call_in_every_mode(tmp_path, capsys):
    fp = _session(tmp_path)
    for mode in ("none", "intent", "intent-or-call", "call"):
        _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", mode)
        assert "[tool error: Bash] $ git push origin main => Exit code 1 rejected" in out, mode


def test_timestamps_modes(tmp_path, capsys):
    fp = _session(tmp_path)
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--timestamps", "all")
    assert "[10:01:00]" in out and "[10:03:01]" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--timestamps", "none")
    assert "[10:" not in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--timestamps", "gaps")
    assert "[10:00:00] " in out            # the first event
    assert "[10:03:01]" not in out         # a close call: no stamp
    assert "[10:30:00]" in out             # 26 minutes after the previous event
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--timestamps", "gaps",
                     "--timestamp-gap-minutes", "0")
    assert "[10:03:01]" in out
    _, out, _ = _run(capsys, fp, "--profile", "all", "--tool-calls", "call", "--timestamps", "gaps",
                     "--timestamp-gap-minutes", "27")
    assert "[10:30:00]" not in out


def test_option_validation(tmp_path, capsys):
    fp = _session(tmp_path)
    for argv in (("--edit-calls", "omit"), ("--read-calls", "collapse"), ("--effect-calls", "always"),
                 ("--no-strip-cd-prefix",), ("--timestamp-gap-minutes", "3"),
                 ("--tool-calls", "call", "--follow", "--timestamps", "gaps"),
                 ("--tool-calls", "call", "--follow", "--edit-calls", "collapse"),
                 ("--tool-calls", "call", "--follow", "--read-calls", "collapse"),
                 ("--successor-brief", "--strip-cd-prefix", "--no-strip-cd-prefix"),
                 ("--timestamps", "gaps", "--timestamp-gap-minutes", "-1"),
                 ("--json", "--timestamps", "none")):
        code, _, err = _run(capsys, fp, *argv)
        assert code == 2, argv


# --- successor brief: header, line number, exact cap ---------------------

def test_brief_header_cwd_branch_and_lowercase_bool(tmp_path):
    fp = _write(tmp_path, [{"type": "attachment"}, _brief("x"), _rec(type="assistant", uuid="a", gitBranch="feat",
                message={"role": "assistant", "content": [_text("done")]})], "agent-h.jsonl")
    fp.with_suffix(".meta.json").write_text(json.dumps({"stoppedByUser": True, "description": "d"}), encoding="utf-8")
    assert transcript_context(fp) == {"cwd": "/w/repo/.worktrees/wt1/sub", "gitBranch": "feat"}
    from nyxloom.session_extract.ledger import Ledger
    from nyxloom.session_extract.stopstate import build_stop_state
    doc = assemble(fp, "x", "EXTRACT", Ledger(), build_stop_state(fp), None)
    assert "- stoppedByUser: true" in doc and "- cwd: /w/repo/.worktrees/wt1/sub" in doc and "- gitBranch: feat" in doc
    assert first_user_record(fp)[2] == 2


def test_brief_cap_is_inclusive_and_pointer_names_the_real_line(tmp_path):
    fp = tmp_path / "agent-b.jsonl"
    fp.write_text("", encoding="utf-8")
    text = "x" * 40
    inline = brief_section(fp, text, 40, 5)
    assert "(verbatim)" in inline and "line" not in inline.split("\n", 1)[1].split("sha256")[0]
    over = brief_section(fp, text, 39, 5)
    assert "(line 5)" in over and "not inlined" in over


def test_effect_ledger_lines_use_aliases_and_the_scp_flag(tmp_path, capsys):
    recs = [_brief(), _blocks("a1", _ts(1), [_use("t1", "Read", file_path="/w/repo/.worktrees/wt1/a.py"),
                                              _use("t2", "Bash", command="scp /w/repo/f h:/p")]),
            _result("r2", "t2", _ts(1, 2), "ok")]
    fp = _write(tmp_path, recs)
    _, out, _ = _run(capsys, fp, "--ledger", "--path-aliases", "auto")
    assert "files read (1): $WT/a.py" in out and "scp $REPO/f h:/p" in out
    _, out, _ = _run(capsys, fp, "--ledger", "--no-default-effect-patterns", "--path-aliases", "none")
    assert "external effects: none detected" in out
    code = ledger.external_effect("scp /a h:/p", scp_uploads=False)
    assert code is False

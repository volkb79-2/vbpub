"""Tests for adapters module. Package P03."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nyxloom import adapters, log
from nyxloom.config import RouteDef
from nyxloom.types import Basis, Role


def _read_log_records(log_dir: Path) -> list[dict]:
    """P05a: local helper (never added to conftest.py -- see
    tests/test_daemon.py's identical helper) reading back the rendered
    JSONL records nyxloom.log writes."""
    p = log_dir / "nyxloom.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture()
def record_argv_script(tmp_path):
    """Create and return a record-argv.sh script."""
    script = tmp_path / "record-argv.sh"
    script.write_text("#!/bin/sh\necho \"$@\" > \"$RECORD_FILE\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def sleepy_script(tmp_path):
    """Create and return a sleepy.sh script."""
    script = tmp_path / "sleepy.sh"
    script.write_text("#!/bin/sh\nsleep \"$SLEEP_TIME\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def emit_script(tmp_path):
    """Create and return an emit.sh script."""
    script = tmp_path / "emit.sh"
    script.write_text("#!/bin/sh\ncat \"$EMIT_FILE\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def version_record_script(tmp_path):
    """Create and return a version-record.sh script."""
    script = tmp_path / "version-record.sh"
    script.write_text("#!/bin/sh\necho \"$@\" > \"$RECORD_FILE\"\necho \"version 1.0\"\n")
    script.chmod(0o755)
    return script


# Oracle 1: render_argv with placeholders
def test_render_argv_basic():
    """Oracle 1: render_argv(['a','{x}b'], {'x':'1'}) == ['a','1b']."""
    result = adapters.render_argv(["a", "{x}b"], {"x": "1"})
    assert result == ["a", "1b"]


def test_render_argv_missing_key():
    """Oracle 1: missing key raises AdapterError naming the placeholder."""
    with pytest.raises(adapters.AdapterError, match="missing placeholder"):
        adapters.render_argv(["a", "{missing}b"], {"x": "1"})


def test_render_argv_multiple_replacements():
    """Multiple placeholders in one element."""
    result = adapters.render_argv(
        ["{a}{b}c{d}"],
        {"a": "1", "b": "2", "d": "3"}
    )
    assert result == ["12c3"]


def test_render_argv_empty_template():
    """Empty template returns empty list."""
    result = adapters.render_argv([], {})
    assert result == []


# Oracle 2: build_dispatch exact argv assertions
def test_build_dispatch_claude():
    """Oracle 2 (amended P14 2026-07-15 -- buffered-CLI-blindness fix):
    claude route argv uses stream-json + --verbose, NOT buffered `json`.
    The old assertion (`"json" in argv`) tested the exact defect P14 item 1
    fixes: `-p --output-format json` writes its entire output at process
    exit, so log mtime is structurally dead as a liveness signal."""
    route = RouteDef(
        route_id="claude-test",
        cli="claude",
        model="sonnet",
        effort="high",
        dispatch_extra=["--dangerously-skip-permissions"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--output-format" in argv
    assert "stream-json" in argv
    assert "json" not in argv  # bare "json" must NOT appear (buffered mode)
    assert "--verbose" in argv
    assert "--model" in argv
    assert "sonnet" in argv
    assert "--effort" in argv
    assert "high" in argv
    assert "--dangerously-skip-permissions" in argv


def test_build_dispatch_codex():
    """Oracle 2: codex route constructs correct argv with sandbox."""
    route = RouteDef(
        route_id="codex-test",
        cli="codex",
        model="gpt-5.6-terra",
        sandbox="danger-full-access"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "codex"
    assert "exec" in argv
    assert "--sandbox" in argv
    assert "danger-full-access" in argv
    assert "--cd" in argv
    assert "/tmp/wt" in argv
    assert "-m" in argv
    assert "gpt-5.6-terra" in argv


def test_build_dispatch_codex_default_sandbox():
    """Oracle 2: codex route defaults sandbox to workspace-write."""
    route = RouteDef(
        route_id="codex-test",
        cli="codex",
        model="gpt-5.6-terra"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert "--sandbox" in argv
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "workspace-write"


def test_build_dispatch_codex_effort_passthrough():
    """codex route passes reasoning effort as a `-c` config override.

    Regression guard: codex has no --effort flag, so route.effort was silently
    dropped and every codex route ran at the ~/.codex/config.toml default.
    """
    route = RouteDef(
        route_id="codex-luna-high",
        cli="codex",
        model="gpt-5.6-luna",
        sandbox="danger-full-access",
        effort="high",
    )
    argv, _ = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json",
    )
    assert "-c" in argv
    c_idx = argv.index("-c")
    assert argv[c_idx + 1] == 'model_reasoning_effort="high"'


def test_build_dispatch_codex_no_effort_omits_override():
    """codex route without effort emits no `-c` reasoning override (negative)."""
    route = RouteDef(
        route_id="codex-test",
        cli="codex",
        model="gpt-5.6-terra",
    )
    argv, _ = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json",
    )
    assert "-c" not in argv


def test_build_dispatch_opencode():
    """Oracle 2: opencode route constructs correct argv."""
    route = RouteDef(
        route_id="opencode-test",
        cli="opencode",
        model="openrouter/deepseek/deepseek-v4-flash",
        variant="high",
        dispatch_extra=["--auto", "--title", "{task_id}"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "opencode"
    assert "run" in argv
    assert "--model" in argv
    assert "openrouter/deepseek/deepseek-v4-flash" in argv
    assert "--dir" in argv
    assert "/tmp/wt" in argv
    assert "--variant" in argv
    assert "high" in argv
    assert "--auto" in argv
    assert "--title" in argv
    assert "T-123" in argv


def test_build_dispatch_opencode_no_variant():
    """Oracle 2: opencode variant only when set."""
    route = RouteDef(
        route_id="opencode-test",
        cli="opencode",
        model="openrouter/deepseek/deepseek-v4-flash"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert "--variant" not in argv


def test_build_dispatch_reasonix():
    """Oracle 2: reasonix route constructs correct argv."""
    route = RouteDef(
        route_id="reasonix-test",
        cli="reasonix",
        model="deepseek-flash-high/deepseek-v4-flash"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv == ["reasonix", "run", "-dir", "/tmp/wt", prompt]


def test_build_dispatch_fake():
    """Oracle 2: fake route for testing."""
    route = RouteDef(
        route_id="fake-test",
        cli="fake",
        model="fake-model",
        dispatch_extra=["--test-flag"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "fake"
    assert "--test-flag" in argv
    assert prompt in argv


def test_build_dispatch_prompt_contains_required_info():
    """Oracle 2: prompt contains handoff path, worktree, branch, gate_hint, receipt_path."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="handoff/P03.md",
        worktree="/workspace/.worktrees/feat-x",
        branch="feat-x",
        task_id="T-999",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json"
    )
    assert "handoff/P03.md" in prompt
    assert "/workspace/.worktrees/feat-x" in prompt
    assert "feat-x" in prompt
    assert "pytest-q" in prompt
    assert "/tmp/receipt.json" in prompt


def test_build_dispatch_prompt_commit_instruction_is_truthful():
    """P21 oracle 4: the implementer prompt no longer asserts the falsehood
    "uncommitted work is discarded" (P21 live P93 lesson: uncommitted work
    is now surfaced to review, not discarded) -- it still tells the
    implementer to commit, just without the lie."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="handoff/P21.md",
        worktree="/workspace/.worktrees/feat-x",
        branch="feat-x",
        task_id="T-999",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json",
    )
    assert "uncommitted work is discarded" not in prompt
    assert "git commit" in prompt
    assert "surfaced to review" in prompt


# P44 (O1/O2): role-scoped build_dispatch -- IMPLEMENTER keeps today's exact
# text (regression pin), CARVER/REVIEW_INDEPENDENT get their own role-correct
# prompt instead of inheriting the implementer's commit instruction.
_P44_KW = dict(
    handoff_path="handoff/P44.md",
    worktree="/workspace/.worktrees/feat-x",
    branch="feat-x",
    task_id="T-P44",
    gate_hint="pytest-q",
    receipt_path="/tmp/receipt.json",
)


def test_build_dispatch_role_implementer_matches_pre_p44_default():
    """O1(a) non-hollow anchor: passing role=Role.IMPLEMENTER explicitly
    produces a prompt BYTE-FOR-BYTE IDENTICAL to calling build_dispatch with
    no role kwarg at all (today's only behavior, pre-P44) -- a real
    regression pin, not just a re-check of the same substrings."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _argv_default, prompt_default = adapters.build_dispatch(route, **_P44_KW)
    _argv_explicit, prompt_explicit = adapters.build_dispatch(
        route, role=Role.IMPLEMENTER, **_P44_KW)
    assert prompt_explicit == prompt_default

    # Re-assert the pre-existing IMPLEMENTER-path assertions (P21 truthfulness
    # pin + required-info) against the EXPLICIT-role prompt, proving role=
    # Role.IMPLEMENTER is not just equal to the default but still carries
    # every one of today's real assertions.
    assert "handoff/P44.md" in prompt_explicit
    assert "/workspace/.worktrees/feat-x" in prompt_explicit
    assert "feat-x" in prompt_explicit
    assert "pytest-q" in prompt_explicit
    assert "/tmp/receipt.json" in prompt_explicit
    assert "uncommitted work is discarded" not in prompt_explicit
    assert "git commit" in prompt_explicit
    assert "surfaced to review" in prompt_explicit


def test_build_dispatch_role_carver_files_authority_drops_commit_instruction():
    """O1(b) non-hollow anchor: a CARVER dispatch under carve_authority
    'files' must NOT contain the commit instruction -- 'files' authority's
    whole contract (daemon.py module docstring above _CARVE_AUTHORITIES) is
    "writes new handoff files WITHOUT committing (no git)"."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _argv, prompt = adapters.build_dispatch(
        route, role=Role.CARVER, carve_authority="files", **_P44_KW)
    assert "git commit" not in prompt
    assert "git add" not in prompt
    # still names what the carver needs to find its work:
    assert "handoff/P44.md" in prompt
    assert "/workspace/.worktrees/feat-x" in prompt
    assert "pytest-q" in prompt
    assert "/tmp/receipt.json" in prompt


@pytest.mark.parametrize("authority", ["branch", "main"])
def test_build_dispatch_role_carver_branch_or_main_keeps_commit_instruction(authority):
    """Positive counterpart to the 'files' case: O1 explicitly permits
    keeping the commit instruction when authority is 'branch' or 'main'
    (these DO commit the new handoff file(s)) -- proves the CARVER branch
    is authority-conditional, not just unconditionally silent about git."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _argv, prompt = adapters.build_dispatch(
        route, role=Role.CARVER, carve_authority=authority, **_P44_KW)
    assert "git commit" in prompt


def test_build_dispatch_role_review_independent_commits_verdict_never_main_or_code():
    """P59 2026-07-20 (M6a -- corrects P44's O1(c)). The daemon derives the
    merge verdict EXCLUSIVELY from the reviewer's COMMITTED <task>-REVIEW.md
    (_parse_review_verdict via `git show`); the receipt carries no verdict.
    P44's old assertion here ('git commit' not in prompt) encoded the very
    bug -- a reviewer told never to commit left NO committed verdict, so the
    daemon read 'missing' and false-rejected approved work (or, under
    guarded-automatic merge, could mis-decide): a nondeterministic merge gate
    contradicting the packet's REQUIRED OUTPUT CONTRACT. The prompt must now
    AGREE with the packet -- commit the verdict report -- while keeping the
    original safety intent (never main, never a code change)."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _argv, prompt = adapters.build_dispatch(
        route, role=Role.REVIEW_INDEPENDENT, **_P44_KW)
    # tells the reviewer to commit its verdict report (consistent with the
    # packet AND with what _parse_review_verdict actually reads):
    assert "git commit" in prompt
    assert "VERDICT" in prompt
    assert "NOT from the receipt" in prompt
    # ...but keeps the safety intent P44 was after: not main, not code changes.
    assert "never main" in prompt
    assert "not authoring code changes" in prompt
    # still names what the reviewer needs to find the packet/gate/receipt:
    assert "handoff/P44.md" in prompt
    assert "pytest-q" in prompt
    assert "/tmp/receipt.json" in prompt


def test_build_dispatch_review_independent_stamps_attempt_id_on_verdict():
    """P59b 2026-07-20 (A7, M6/I8). When an attempt_id is threaded in, the
    review prompt must instruct the reviewer to stamp `(attempt <id>)` on the
    VERDICT line and flag it as REQUIRED/copied-exactly, so the daemon can
    bind the verdict to THIS review and ignore stale/foreign ones. Without an
    attempt_id (older call sites), the instruction degrades to the unbound
    form -- proven byte-stable by the test above (which passes no id)."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    att = "att-abc123def456"
    _argv, prompt = adapters.build_dispatch(
        route, role=Role.REVIEW_INDEPENDENT, attempt_id=att, **_P44_KW)
    assert f"VERDICT: APPROVED (attempt {att})" in prompt
    assert f"VERDICT: REJECTED (attempt {att})" in prompt
    assert "REQUIRED" in prompt and "EXACTLY" in prompt
    # unbound form absent when bound (would defeat the binding):
    assert "VERDICT: APPROVED`" not in prompt


def test_build_dispatch_review_independent_unbound_when_no_attempt_id():
    """Back-compat: no attempt_id => byte-for-byte the pre-P59b unbound prompt
    (the bound suffix and note are empty), so callers that don't thread an id
    are unchanged."""
    route = RouteDef(route_id="test", cli="fake", model="fake-model")
    _a, bound = adapters.build_dispatch(route, role=Role.REVIEW_INDEPENDENT, attempt_id=None, **_P44_KW)
    _a2, plain = adapters.build_dispatch(route, role=Role.REVIEW_INDEPENDENT, **_P44_KW)
    assert bound == plain
    assert "(attempt" not in bound


def test_daemon_build_dispatch_call_sites_pass_role_explicitly():
    """O2 (grep-provable): all five daemon.py build_dispatch call sites
    (legacy CarveDispatch's CARVER, IMPLEMENTER, REVIEW_INDEPENDENT, F018 P3a's
    StartCarverSession CARVER bootstrap, and F019 P1b's gate-diagnosis
    REVIEW_INDEPENDENT cold dispatch) pass their own role= explicitly -- guards
    against the exact silent-mismatch this package exists to close (a call site
    importing Role but never actually passing role= to build_dispatch, silently
    keeping the wrong-role default). F018 P3a (2026-07-25) added the 4th call
    site (_execute_start_carver_session's cold bootstrap launch, role=Role.CARVER
    like the legacy carve dispatch it mirrors). F019 P1b (2026-07-25) added the
    5th (_execute_gate_diagnosis's cold classify-only launch,
    role=Role.REVIEW_INDEPENDENT) -- roles_seen stays the same 3-element set (no
    new role, just a second REVIEW_INDEPENDENT call site)."""
    import re

    daemon_src = (Path(__file__).parent.parent / "src" / "nyxloom" / "daemon.py").read_text()
    calls = re.findall(
        r"adapters\.build_dispatch\(\s*(?:[^()]|\([^()]*\))*?\)",
        daemon_src,
        flags=re.DOTALL,
    )
    assert len(calls) == 5, f"expected exactly 5 build_dispatch call sites, found {len(calls)}"
    roles_seen = set()
    for call in calls:
        m = re.search(r"role\s*=\s*Role\.(\w+)", call)
        assert m is not None, f"a build_dispatch call site is missing role=Role.*:\n{call}"
        roles_seen.add(m.group(1))
    assert roles_seen == {"CARVER", "IMPLEMENTER", "REVIEW_INDEPENDENT"}


# Oracle 3: Prompt-length guard
def test_build_dispatch_prompt_too_long():
    """Oracle 3: long prompt raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        argv_max=50  # Very short limit
    )
    with pytest.raises(adapters.AdapterError, match="exceeds argv_max"):
        adapters.build_dispatch(
            route,
            handoff_path="handoff/P03.md",
            worktree="/very/long/path/to/workspace",
            branch="feat-x",
            task_id="T-999",
            gate_hint="pytest-q",
            receipt_path="/tmp/receipt.json"
        )


def test_build_dispatch_incremental_write_hint():
    """Oracle 3: incremental-write hint appends ~80-line sentence."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        prompt_hints=["incremental-write"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="h.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json"
    )
    assert "~80-line" in prompt


def test_build_dispatch_free_endpoint_confidentiality_guard():
    """A free-endpoint route injects the operator-mandated no-secrets notice;
    a route without the hint does NOT (so the guard is scoped to free routes,
    not shipped to every dispatch)."""
    kw = dict(handoff_path="h.md", worktree="/tmp/wt", branch="feat-x",
              task_id="T-123", gate_hint="pytest-q", receipt_path="/tmp/r.json")

    free_route = RouteDef(route_id="free", cli="fake", model="m",
                          prompt_hints=["free-endpoint"])
    _argv, free_prompt = adapters.build_dispatch(free_route, **kw)
    assert "never upload any confidential" in free_prompt
    assert "credentials or secrets" in free_prompt

    # NEGATIVE: a normal (non-free) route must NOT carry the notice.
    paid_route = RouteDef(route_id="paid", cli="fake", model="m")
    _argv2, paid_prompt = adapters.build_dispatch(paid_route, **kw)
    assert "never upload any confidential" not in paid_prompt


# Oracle 4: build_resume
def test_build_resume_substitutes_placeholders():
    """Oracle 4: build_resume substitutes {session}/{worktree}/{prompt}."""
    route = RouteDef(
        route_id="test",
        cli="claude",
        model="sonnet",
        resume=["claude", "--resume", "{session}", "--model", "sonnet", "-p", "{prompt}"]
    )
    argv = adapters.build_resume(
        route,
        session="sess-abc123",
        worktree="/tmp/wt",
        prompt="continue working"
    )
    assert argv == ["claude", "--resume", "sess-abc123", "--model", "sonnet",
                    "-p", "continue working"]


def test_build_resume_empty_template():
    """Oracle 4: empty resume template raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=[]
    )
    with pytest.raises(adapters.AdapterError, match="empty resume template"):
        adapters.build_resume(
            route,
            session="sess-123",
            worktree="/tmp/wt",
            prompt="test"
        )


def test_build_resume_session_required_but_none():
    """Oracle 4: template with {session} but session=None raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=["fake", "run", "--session", "{session}", "{prompt}"]
    )
    with pytest.raises(adapters.AdapterError, match="session required"):
        adapters.build_resume(
            route,
            session=None,
            worktree="/tmp/wt",
            prompt="test"
        )


def test_build_resume_session_not_required():
    """Template without {session} should not require session."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=["fake", "run", "{worktree}", "{prompt}"]
    )
    argv = adapters.build_resume(
        route,
        session=None,
        worktree="/tmp/wt",
        prompt="test"
    )
    assert argv == ["fake", "run", "/tmp/wt", "test"]


# Oracle 5: probe
def test_probe_none():
    """Oracle 5: probe None returns (True, 'no-probe')."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=None
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    assert detail == "no-probe"


def test_probe_argv_true(tmp_path):
    """Oracle 5: probe ['true'] returns (True, ...)."""
    ok, detail = adapters.probe(RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["true"]
    ))
    assert ok is True


def test_probe_argv_false(tmp_path):
    """Oracle 5: probe ['false'] returns (False, ...)."""
    ok, detail = adapters.probe(RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["false"]
    ))
    assert ok is False


def test_probe_timeout():
    """Oracle 5: probe exceeding timeout returns (False, 'timeout...')."""
    # Use a command that takes a long time
    # Monkeypatch subprocess.run to raise TimeoutExpired
    import subprocess
    from unittest.mock import patch

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["sleep", "10"]
    )

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep 10", 1)):
        ok, detail = adapters.probe(route)
        assert ok is False
        assert "timeout" in detail.lower()


def test_probe_named_builtin_one_token_ping(version_record_script, tmp_path, monkeypatch):
    """Oracle 5: named builtin 'one-token-ping' executes [cli,'--version']."""
    record_file = tmp_path / "record.txt"
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    route = RouteDef(
        route_id="test",
        cli=str(version_record_script),
        model="fake-model",
        probe="one-token-ping"
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    if record_file.exists():
        args = record_file.read_text().strip()
        assert "--version" in args


def test_probe_named_builtin_session_limit_check(version_record_script, tmp_path, monkeypatch):
    """Oracle 5: named builtin 'session-limit-check' executes [cli,'--version']."""
    record_file = tmp_path / "record.txt"
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    route = RouteDef(
        route_id="test",
        cli=str(version_record_script),
        model="fake-model",
        probe="session-limit-check"
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    if record_file.exists():
        args = record_file.read_text().strip()
        assert "--version" in args


# Oracle 6: capture_session
def test_capture_session_newest_jsonl(tmp_path, monkeypatch):
    """Oracle 6: 'newest-jsonl' finds newest file after launched_at."""
    # Set up HOME to tmp
    monkeypatch.setenv("HOME", str(tmp_path))

    worktree = "/tmp/wt"
    projects_dir = tmp_path / ".claude" / "projects" / "tmp-wt"
    projects_dir.mkdir(parents=True)

    # Create two jsonl files
    old_file = projects_dir / "old.jsonl"
    new_file = projects_dir / "new.jsonl"
    old_file.write_text("old")
    new_file.write_text("new")

    # Set mtimes using os.utime
    import os
    import time
    now = time.time()
    os.utime(str(old_file), (now - 100, now - 100))
    os.utime(str(new_file), (now + 100, now + 100))

    launched_at = datetime.fromtimestamp(now, tz=timezone.utc)

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_capture="newest-jsonl"
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree=worktree,
        launched_at=launched_at
    )
    assert result == "new"


def test_capture_session_newest_jsonl_no_dir(tmp_path, monkeypatch):
    """Oracle 6: no dir returns None."""
    monkeypatch.setenv("HOME", str(tmp_path))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_capture="newest-jsonl"
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/nonexistent",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result is None


def test_capture_session_discover(tmp_path, monkeypatch, emit_script):
    """Oracle 6: session_discover runs command, parses JSON, matches dir."""
    json_output = json.dumps([
        {"id": "sess-1", "dir": "/tmp/wt", "title": "Session 1"},
        {"id": "sess-2", "dir": "/other/wt", "title": "Session 2"}
    ])
    emit_file = tmp_path / "emit.txt"
    emit_file.write_text(json_output)
    monkeypatch.setenv("EMIT_FILE", str(emit_file))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_discover=[str(emit_script)]
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result == "sess-1"


def test_capture_session_discover_by_title(tmp_path, monkeypatch, emit_script):
    """Oracle 6: session_discover matches title field."""
    json_output = json.dumps([
        {"id": "sess-1", "title": "/tmp/wt", "dir": "/some/other/path"}
    ])
    emit_file = tmp_path / "emit.txt"
    emit_file.write_text(json_output)
    monkeypatch.setenv("EMIT_FILE", str(emit_file))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_discover=[str(emit_script)]
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result == "sess-1"


# P17 2026-07-15: capture_session extracts session_id from a claude route's
# stream-json FIRST log line, instead of the newest-jsonl heuristic.
def test_capture_session_claude_stream_json_first_line(tmp_path):
    """Regression (Gap 1): a stream-json fixture log -> capture_session
    returns the embedded session_id, read via the explicit log_path."""
    log_path = tmp_path / "attempt.log"
    log_path.write_text(
        '{"type":"system","subtype":"init","session_id":"abc-123-def"}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )

    route = RouteDef(
        route_id="claude-test",
        cli="claude",
        model="sonnet",
        session_capture="newest-jsonl",  # must NOT be consulted for claude
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path,
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
        log_path=str(log_path),
    )
    assert result == "abc-123-def"


def test_capture_session_claude_stream_json_defaults_to_attempt_log(tmp_path):
    """log_path omitted -> falls back to <attempt_dir>/attempt.log."""
    attempt_dir = tmp_path / "att"
    attempt_dir.mkdir()
    (attempt_dir / "attempt.log").write_text(
        '{"session_id":"fallback-sess"}\n', encoding="utf-8",
    )

    route = RouteDef(route_id="claude-test", cli="claude", model="sonnet")

    result = adapters.capture_session(
        route,
        attempt_dir=attempt_dir,
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
    )
    assert result == "fallback-sess"


def test_capture_session_claude_stream_json_malformed_first_line(tmp_path):
    """Negative case: a non-JSON first line degrades to None (never raises),
    and does NOT fall back to newest-jsonl for a claude route."""
    log_path = tmp_path / "attempt.log"
    log_path.write_text("not json at all\n", encoding="utf-8")

    route = RouteDef(
        route_id="claude-test", cli="claude", model="sonnet",
        session_capture="newest-jsonl",
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path,
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
        log_path=str(log_path),
    )
    assert result is None


def test_capture_session_claude_stream_json_missing_session_id(tmp_path):
    """Negative case: valid JSON first line with no session_id -> None."""
    log_path = tmp_path / "attempt.log"
    log_path.write_text('{"type":"system","subtype":"init"}\n', encoding="utf-8")

    route = RouteDef(route_id="claude-test", cli="claude", model="sonnet")

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path,
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
        log_path=str(log_path),
    )
    assert result is None


def test_capture_session_claude_stream_json_missing_log_file(tmp_path):
    """Negative case: log file doesn't exist yet -> None, no exception."""
    route = RouteDef(route_id="claude-test", cli="claude", model="sonnet")

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path,
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
        log_path=str(tmp_path / "does-not-exist.log"),
    )
    assert result is None


# Oracle 7: extract_usage
def test_extract_usage_output_format_json():
    """Oracle 7: output-format-json extracts from LAST json line."""
    log = """some log line
{"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 80}, "total_cost_usd": 0.0123}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100
    assert usage.tokens_out == 50
    assert usage.cached_in == 80
    assert usage.cost == 0.0123
    assert usage.currency == "USD"


def test_extract_usage_output_format_json_skips_malformed():
    """Oracle 7: earlier malformed json lines are skipped."""
    log = """malformed: {not json
valid line
{"usage": {"input_tokens": 100, "output_tokens": 50}, "total_cost_usd": 0.0123}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100


def test_extract_usage_codex_footer():
    """Oracle 7: codex 'Tokens used: 12,345' -> ESTIMATED tokens_out 12345."""
    log = """some output
Tokens used: 12,345
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="exec-output-footer"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ESTIMATED
    assert usage.tokens_out == 12345


def test_extract_usage_deepseek():
    """Oracle 7: deepseek regex extracts ACTUAL tokens."""
    log = """{
  "prompt_tokens": 100,
  "completion_tokens": 50
}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="run-log-deepseek-usage"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100
    assert usage.tokens_out == 50
    assert usage.cost is None


def test_extract_usage_stream_json_fixture():
    """P14 item 1/oracle 3: extract_usage parses a REALISTIC stream-json
    (NDJSON) log -- one JSON object per line as claude -p --output-format
    stream-json --verbose emits -- to ACTUAL usage from the final `result`
    line. Earlier lines have no top-level 'usage'/'total_cost_usd' key
    (usage is nested under "message" there) so they must be skipped."""
    log = "\n".join([
        '{"type": "system", "subtype": "init", "cwd": "/tmp/wt"}',
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}], '
        '"usage": {"input_tokens": 5, "output_tokens": 2}}}',
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"duration_ms": 1234, "usage": {"input_tokens": 200, "output_tokens": 90, '
        '"cache_read_input_tokens": 150}, "total_cost_usd": 0.045}',
    ]) + "\n"
    route = RouteDef(
        route_id="test",
        cli="claude",
        model="sonnet",
        usage_source="output-format-json",
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 200
    assert usage.tokens_out == 90
    assert usage.cached_in == 150
    assert usage.cost == 0.045
    assert usage.currency == "USD"


def test_build_dispatch_claude_argv_stream_json_exact_position():
    """P14 item 1: --output-format is immediately followed by stream-json
    (not the buffered 'json')."""
    route = RouteDef(route_id="claude-test", cli="claude", model="sonnet")
    argv, _prompt = adapters.build_dispatch(
        route,
        handoff_path="h.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json",
    )
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "stream-json"
    assert "--verbose" in argv


def test_extract_usage_garbage_log():
    """Oracle 7: garbage log -> Usage(UNKNOWN) and NO exception."""
    log = "a" * 10000  # 10KB of garbage
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.UNKNOWN


# Oracle 8: classify_log_tail
def test_classify_log_tail_blocked():
    """Oracle 8: 'BLOCKED:' at line start -> 'blocked'."""
    log = "some log\nBLOCKED: cannot meet contract\nmore log"
    result = adapters.classify_log_tail(log)
    assert result == "blocked"


def test_classify_log_tail_scope_amendment():
    """O4 (B21 2026-07-23, D-R16 §3): 'SCOPE_AMENDMENT_REQUEST:' at line
    start -> 'scope_amendment' -- the mid-flight escalation marker,
    recognized the same way BLOCKED is (line-start match, anywhere in the
    last 200 lines)."""
    log = ('some log\nSCOPE_AMENDMENT_REQUEST: {"file": "src/x.py", '
           '"reason": "needs shared helper"}\nmore log')
    result = adapters.classify_log_tail(log)
    assert result == "scope_amendment"


def test_classify_log_tail_blocked_still_wins_no_regression():
    """O4 NEGATIVE (no regression): classify_log_tail still returns 'blocked'
    for a BLOCKED: line -- the new SCOPE_AMENDMENT_REQUEST recognizer must
    not shadow or displace the pre-existing BLOCKED path. Also pins BLOCKED's
    precedence when BOTH markers somehow appear in the same tail."""
    assert adapters.classify_log_tail("BLOCKED: cannot meet contract") == "blocked"
    both = ('BLOCKED: cannot meet contract\n'
            'SCOPE_AMENDMENT_REQUEST: {"file": "x.py", "reason": "y"}')
    assert adapters.classify_log_tail(both) == "blocked"


def test_classify_log_tail_limit():
    """Oracle 8: 'rate limit exceeded' -> 'limit'."""
    log = "some log\nrate limit exceeded\nmore"
    result = adapters.classify_log_tail(log)
    assert result == "limit"


def test_classify_log_tail_limit_variants():
    """Oracle 8 (tightened 2026-07-15): SPECIFIC limit phrases recognized in
    the terminal tail — bare 'quota'/'limit' no longer match (domain-vocab
    false positive, topos-P91)."""
    for phrase in ["session limit", "usage limit", "quota exceeded",
                   "plan limit", "rate limit reached", "429 too many requests"]:
        log = f"some log\n{phrase}\nmore"
        result = adapters.classify_log_tail(log)
        assert result == "limit", f"Failed for phrase: {phrase}"
    # bare domain words are NOT a limit:
    assert adapters.classify_log_tail("the quota knob and rate limit setting\ndone") is None


# Oracle 8 (B24 2026-07-23, D-R17): transient provider-throttle classification
def test_classify_log_tail_transient_variants():
    """O1: each PROVIDER-throttle/outage signature -> 'transient', checked
    (per the terminal-tail reasoning shared with 'limit') in the last
    LIMIT_TAIL_LINES lines."""
    cases = [
        "Error: HTTP 502 Bad Gateway from upstream proxy",
        "google.api_core.exceptions.ResourceExhausted: 429 Resource has been exhausted",
        "Upstream idle timeout after 90s, connection reset",
        "Worker local total request limit reached, retrying in backoff",
        "anthropic.APIStatusError: Error code: 429 - rate limited, retry-after: 30s",
    ]
    for phrase in cases:
        log = "progress\n" * 30 + phrase
        result = adapters.classify_log_tail(log)
        assert result == "transient", f"Failed for phrase: {phrase!r} (got {result!r})"


def test_classify_log_tail_transient_ordering_beats_limit():
    """O1: order matters -- 'Worker local total request limit reached' would
    ALSO match limit_phrase's own bare 'limit reached' alternative, so
    transient must be checked BEFORE limit and win (reclassified transient,
    not LIMIT)."""
    log = "progress\n" * 10 + "Worker local total request limit reached"
    assert adapters.classify_log_tail(log) == "transient"


def test_classify_log_tail_transient_no_regression():
    """O1 NEGATIVE (no regression):
    - a BLOCKED: line still returns 'blocked' (transient never shadows it)
    - a scope-amendment marker still returns 'scope_amendment'
    - a genuine session/usage limit still returns 'limit' -- the existing
      '<n> too many requests' LIMIT phrasing is UNCHANGED (transient's own
      HTTP-429 signature is deliberately narrower, see classify_log_tail's
      docstring), so this is a true no-regression check, not a hollow one
    - ordinary domain prose about 'limits'/'quota' still returns None
    """
    assert adapters.classify_log_tail(
        "progress\n" * 10 + "BLOCKED: cannot meet contract") == "blocked"
    assert adapters.classify_log_tail(
        "progress\n" * 10 +
        'SCOPE_AMENDMENT_REQUEST: {"file": "x.py", "reason": "y"}'
    ) == "scope_amendment"
    assert adapters.classify_log_tail(
        "progress\n" * 10 + "session limit exceeded, please wait") == "limit"
    assert adapters.classify_log_tail(
        "progress\n" * 10 + "usage limit reached for this billing period") == "limit"
    assert adapters.classify_log_tail(
        "progress\n" * 10 + "error: rate limit exceeded (429 too many requests)") == "limit"
    assert adapters.classify_log_tail(
        "the quota knob and rate limit setting\ndone") is None


def test_classify_log_tail_both_blocked_wins():
    """Oracle 8: both blocked and limit present -> 'blocked'."""
    log = "some\nrate limit exceeded\nBLOCKED: reason\nmore"
    result = adapters.classify_log_tail(log)
    assert result == "blocked"


def test_classify_log_tail_blocked_midsentence():
    """Oracle 8: 'blocked: midsentence' (not line-start, lowercase) -> not 'blocked'."""
    log = "the word blocked: midsentence"
    result = adapters.classify_log_tail(log)
    assert result is not None or result is None  # Should not specifically be 'blocked'
    # Check that it's not blocked at least
    if result:
        assert result != "blocked"


def test_classify_log_tail_clean_log():
    """Oracle 8: clean log -> None."""
    log = "all is well\neverything working\nno issues"
    result = adapters.classify_log_tail(log)
    assert result is None


def test_classify_log_tail_only_last_200_lines():
    """Oracle 8: only last 200 lines considered."""
    # Create 250 clean lines, then a BLOCKED line
    lines = ["clean line"] * 250
    lines.append("BLOCKED: late blocker")
    log = "\n".join(lines)

    result = adapters.classify_log_tail(log)
    # Should still find it since BLOCKED is at line 251, within last 200 lines
    assert result == "blocked"

    # Now put BLOCKED more than 200 lines back
    # 400 clean lines, then a BLOCKED line, then 1 more clean line
    # This puts BLOCKED at position 401, but the last 200 lines are 202-401
    lines = ["BLOCKED: very early blocker"] + ["clean line"] * 400
    log = "\n".join(lines)

    result = adapters.classify_log_tail(log)
    # Should NOT find it since it's beyond 200 lines (it's at line 1)
    assert result is None


def test_build_dispatch_unknown_cli():
    """Unknown cli should raise AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="unknown-cli",
        model="fake-model"
    )
    with pytest.raises(adapters.AdapterError, match="unknown cli"):
        adapters.build_dispatch(
            route,
            handoff_path="h.md",
            worktree="/tmp/wt",
            branch="feat-x",
            task_id="T-123",
            gate_hint="pytest-q",
            receipt_path="/tmp/receipt.json"
        )


# Oracle O1 (P27): find_controller_container
def _fake_docker_ps(names):
    """A subprocess.run stand-in returning `docker ps --format {{.Names}}`-shaped output."""
    class Result:
        stdout = "\n".join(names) + ("\n" if names else "")
    def _run(argv, **kwargs):
        return Result()
    return _run


def test_find_controller_container_matches_prod_nyxloomd():
    """O1: the real container `nyxloom-prod-nyxloomd` is found with no
    container_prefix given (generic '-nyxloomd' suffix match)."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value="/usr/bin/docker"), \
         patch("nyxloom.adapters.subprocess.run",
               side_effect=_fake_docker_ps(["nyxloom-prod-nyxloomd", "unrelated"])):
        assert adapters.find_controller_container() == "nyxloom-prod-nyxloomd"


def test_find_controller_container_matches_given_prefix():
    """O1: an explicit container_prefix (the ciu.toml value) matches exactly
    '<prefix>-nyxloomd' and rejects a differing prefix."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value="/usr/bin/docker"), \
         patch("nyxloom.adapters.subprocess.run",
               side_effect=_fake_docker_ps(["nyxloom-staging-nyxloomd"])):
        assert adapters.find_controller_container(
            container_prefix="nyxloom-staging") == "nyxloom-staging-nyxloomd"
        assert adapters.find_controller_container(
            container_prefix="nyxloom-prod") is None


def test_find_controller_container_old_controller_pattern_no_longer_required():
    """Negative (O1): the pre-P27 bug required 'nyxloom' AND 'controller' in
    the name. The real container has neither substring pairing ('nyxloomd'
    has no 'controller'), so the fixed resolver must not depend on it --
    and a container that merely mentions 'controller' but isn't the daemon
    must NOT match."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value="/usr/bin/docker"), \
         patch("nyxloom.adapters.subprocess.run",
               side_effect=_fake_docker_ps(["nyxloom-controller-unrelated"])):
        assert adapters.find_controller_container() is None


def test_find_controller_container_env_override_wins():
    """O1: $NYXLOOM_CONTAINER wins outright when it names a running container."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value="/usr/bin/docker"), \
         patch("nyxloom.adapters.subprocess.run",
               side_effect=_fake_docker_ps(["nyxloom-prod-nyxloomd", "my-dev-override"])):
        assert adapters.find_controller_container(
            env={"NYXLOOM_CONTAINER": "my-dev-override"}) == "my-dev-override"


def test_find_controller_container_no_candidate_running():
    """O1: returns None (host fallback) when nothing running matches."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value="/usr/bin/docker"), \
         patch("nyxloom.adapters.subprocess.run", side_effect=_fake_docker_ps([])):
        assert adapters.find_controller_container() is None


def test_find_controller_container_no_docker_binary():
    """O1: docker missing degrades to None, never raises."""
    from unittest.mock import patch
    with patch("nyxloom.adapters.shutil.which", return_value=None):
        assert adapters.find_controller_container() is None


def test_classify_limit_false_positive_domain_vocab():
    """2026-07-15 (topos-P91): a package about caps/limits/quota says those
    words in its own reasoning; that must NOT read as a provider limit."""
    from nyxloom.adapters import classify_log_tail
    domain = "\n".join([
        "Implementing the persistent capped history with a rate limit knob.",
        "The quota is enforced per window; when the cap is exceeded we evict.",
    ] + ["ordinary progress line"] * 40)
    assert classify_log_tail(domain) is None
    # a REAL limit lands in the final lines with error shape:
    real = "progress\n" * 40 + "error: rate limit exceeded (429 too many requests)\n"
    assert classify_log_tail(real) == "limit"
    # BLOCKED still wins and is still recognized deep in the tail:
    assert classify_log_tail("BLOCKED: cannot meet contract\n" + "x\n" * 10) == "blocked"


def test_self_review_prompt_is_mechanical_oracle_anchored():
    """B5-hardened (P40 + AUTHORING): the self_review prompt must be MECHANICAL
    and oracle-anchored -- run each oracle's observable on REAL data AND check
    the NEGATIVE (edge case) so a hollow test is caught -- NOT the subjective
    'reflect / fresh eyes' framing AUTHORING flags as false confidence. Pins the
    contract so it cannot silently regress to a vague 'review critically'."""
    p = adapters.self_review_prompt(
        task_id="demo-P01", worktree="/w", branch="feat/demo-P01",
        report_path="reports/demo-P01-SELFREVIEW.md")
    low = p.lower()
    for token in ("oracle", "observable", "real data", "negative", "hollow"):
        assert token in low, f"self_review prompt missing mechanical token {token!r}"
    assert "SELF_REVIEW: APPROVED" in p and "SELF_REVIEW: REJECTED" in p
    assert "reports/demo-P01-SELFREVIEW.md" in p


# ============================================================================
# B4b 2026-07-20 (D-060 triage; critique CRITIQUE.md:207). Two prompt
# contracts: (1) the REVIEW_INDEPENDENT prompt makes the reviewer self-stamp a
# REJECT_CLASS on a rejection (the Tier-2 producer, D-066); (2) an IMPLEMENTER
# re-dispatch embeds the prior review verdict so a re-queued fix is targeted,
# never the "bare same-model context-free retry" the critique bans. Each pins
# a NEGATIVE so the assertion cannot pass hollowly.
# ============================================================================

def _fake_route():
    return RouteDef(route_id="r-b4b", cli="fake", model="m")


def test_review_independent_prompt_requires_reject_class():
    """The reviewer prompt instructs a REJECT_CLASS line with all three classes
    on a REJECTED verdict (the Tier-2 producer). NEGATIVE: the IMPLEMENTER and
    CARVER prompts carry NO such instruction -- only the reviewer classifies."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    assert "REJECT_CLASS" in prompt
    for cls in ("fixable", "architectural", "product"):
        assert cls in prompt, f"reviewer prompt must define the {cls!r} class"
    assert "REJECTED" in prompt

    _a2, impl_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER,
    )
    assert "REJECT_CLASS" not in impl_prompt


def test_review_independent_prompt_stays_under_argv_max_with_real_paths():
    """B4b REGRESSION GUARD (the live gate catch): the REJECT_CLASS block must
    NOT push the reviewer prompt past argv_max. The first cut used minimal paths
    in the content test above and passed at ~1490 chars, but real deep-worktree
    paths overflowed 1500 -> build_dispatch raised -> the review leg never
    dispatched -> every task stranded at AWAITING_REVIEW (7 behavioral tests).
    Pin a realistic long path AND a comfortable margin so a future prompt edit
    that eats the headroom fails HERE, cheaply, not in a stranded daemon."""
    long_task = "nyxloom-P74-reviewer-session-reuse-and-spine-digest"
    _argv, prompt = adapters.build_dispatch(
        _fake_route(),
        handoff_path=f"nyxloom-trove/handoffs/{long_task}.md",
        worktree=f"/workspaces/vbpub/.worktrees/flow-stages/nyxloom/.worktrees/feat/{long_task}",
        branch=f"feat/{long_task}", task_id=long_task,
        gate_hint="cd .. && MOCK_MODE=true pytest tests -q",
        receipt_path=f"/state/attempts/att-0123456789abcdef/receipt.json",
        role=Role.REVIEW_INDEPENDENT, attempt_id="att-0123456789abcdef",
    )
    # argv_max default is 1500; keep >= 200 chars of headroom for even longer paths.
    assert len(prompt) <= 1300, f"reviewer prompt too long ({len(prompt)}); trim the REJECT_CLASS block"
    assert "REJECT_CLASS" in prompt          # not trimmed away entirely


def test_implementer_re_dispatch_embeds_prior_verdict():
    """A re-queued implementer dispatch with a prior_verdict embeds that prose
    verbatim, flagged as a prior rejection to fix. NEGATIVE: prior_verdict=None
    (a first dispatch) produces a prompt with NO such block -- so the embed is
    caused by the verdict, not always present."""
    rationale = "Findings: the retry backoff ignores the max cap.\nVERDICT: REJECTED"
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, prior_verdict=rationale,
    )
    assert "ignores the max cap" in prompt          # the reviewer's prose is embedded
    assert "REJECTED by review" in prompt

    _a2, first_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, prior_verdict=None,
    )
    assert "REJECTED by review" not in first_prompt
    assert "ignores the max cap" not in first_prompt


def test_prior_verdict_only_embedded_for_implementer():
    """The docstring's claim 'Only the IMPLEMENTER branch consults it' pinned:
    a prior_verdict passed to a REVIEW_INDEPENDENT dispatch is NOT embedded (the
    reviewer is reviewing fresh, not fixing a prior rejection)."""
    rationale = "Findings: unique-marker-xyz should not leak into the reviewer prompt."
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, prior_verdict=rationale,
    )
    assert "unique-marker-xyz" not in prompt


def test_implementer_prior_verdict_bounded_to_argv_max():
    """B4b: a multi-KB review report embedded into an implementer re-dispatch is
    TRUNCATED to fit argv_max, never raises -- an unbounded embed would overflow
    the guard and strand the reject-loop's fix pass. Keeps the HEAD of the
    findings (most salient) + a truncation marker, and stays within budget."""
    huge = "REVIEWER FINDINGS: " + ("the retry cap is wrong; " * 400)  # ~9 KB
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, prior_verdict=huge,
    )
    assert len(prompt) <= 1500                       # never overflows the guard
    assert "REVIEWER FINDINGS" in prompt             # keeps the head of the review
    assert "truncated to fit" in prompt              # marked as truncated


# ============================================================================
# B21 2026-07-23 (D-R16 §3, scope-amendment escalation). Oracle O1: every
# IMPLEMENTER dispatch carries the standing escape-hatch instruction. Oracle
# O2: a re-dispatch with approved_amendments actually widens the PROMPT (the
# mechanism D-B21-1's overlay depends on -- the handoff file itself is never
# rewritten). Oracle O6: the REVIEW_INDEPENDENT prompt tells the reviewer about
# approved amendments so it does not reject the now-legitimate out-of-scope
# edit. Each pins a NEGATIVE so the assertion cannot pass hollowly.
# ============================================================================

def test_implementer_prompt_always_carries_scope_amendment_escape_hatch():
    """O1: EVERY implementer dispatch (not just a re-dispatch) is told the
    escape hatch exists -- the SCOPE_AMENDMENT_REQUEST marker syntax, and
    that it replaces faking a workaround or hard-BLOCKing. NEGATIVE: a
    CARVER dispatch (which never emits attempt receipts through this same
    IMPLEMENTER-facing path) does NOT get this instruction."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER,
    )
    assert "SCOPE_AMENDMENT_REQUEST:" in prompt
    assert "do NOT hard-BLOCK" in prompt

    _a2, carver_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.CARVER, carve_authority="branch",
    )
    assert "SCOPE_AMENDMENT_REQUEST:" not in carver_prompt


def test_implementer_re_dispatch_widens_prompt_with_approved_amendments():
    """O2: after an amendment is approved for file X, the re-dispatched
    IMPLEMENTER prompt CONTAINS X in an amendment-approved instruction --
    the widened allowlist actually reaches the agent's next attempt (D-B21-1:
    this prompt injection, not a handoff rewrite, IS the widening mechanism).
    NEGATIVE: approved_amendments=None (no amendment history -- every pre-B21
    dispatch) produces NO such block, and does not mention the file."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, approved_amendments=["src/demo/shared_helper.py"],
    )
    assert "Amendment-approved" in prompt
    assert "src/demo/shared_helper.py" in prompt
    assert "you MAY also edit" in prompt

    _a2, plain_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, approved_amendments=None,
    )
    assert "Amendment-approved" not in plain_prompt
    assert "src/demo/shared_helper.py" not in plain_prompt


def test_approved_amendments_only_embedded_for_implementer():
    """The docstring's claim generalizes prior_verdict's own pin (test
    above): approved_amendments passed to a REVIEW_INDEPENDENT dispatch does NOT
    get the IMPLEMENTER-worded 'you MAY also edit' embed (the reviewer gets
    its OWN differently-worded note instead -- see the O6 test below)."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, approved_amendments=["src/demo/shared_helper.py"],
    )
    assert "you MAY also edit" not in prompt


def test_review_independent_prompt_tells_reviewer_about_approved_amendments():
    """O6: the REVIEW_INDEPENDENT prompt for a task with approved amendments
    includes the granted file(s) as in-scope, so the reviewer does not reject
    the (now-legitimate) out-of-scope edit as a scope violation. NEGATIVE: no
    approved_amendments (the common case -- most reviews have no amendment
    history) produces NO such note."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, approved_amendments=["src/demo/shared_helper.py"],
    )
    assert "src/demo/shared_helper.py" in prompt
    assert "legitimately in-scope" in prompt

    _a2, plain_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    assert "src/demo/shared_helper.py" not in plain_prompt
    assert "Scope-amendment note" not in plain_prompt


def test_implementer_amendment_note_skipped_when_argv_max_too_tight():
    """B21 regression guard (found LIVE while writing the behavioral test:
    an earlier unconditional version of the REVIEW_INDEPENDENT counterpart of
    this note overflowed argv_max for a realistic packet path and
    permanently stranded a review attempt at CREATED -- see LOG.md). The
    amendment-approved note must be BOUNDED like prior_verdict's embed:
    skipped (never raising, never truncated mid-list) when too little room
    remains. NEGATIVE: the base prompt WITHOUT the note still fits and is
    dispatched -- degrade, never strand."""
    kw = dict(handoff_path="h.md", worktree="/wt", branch="feat/T1",
              task_id="T1", gate_hint="pytest -q", receipt_path="r.json")
    route_wide = _fake_route()
    _a, base_prompt = adapters.build_dispatch(route_wide, role=Role.IMPLEMENTER, **kw)

    # Two-pass construction: a route sized off the WIDE base_prompt (which
    # includes the doctrine manifest) is not self-consistent once budgeted
    # appends compete for freed-up room -- at that width the manifest itself
    # gets dropped (its own -200 margin isn't met), which frees enough space
    # that a LONGER note could slip in even though the test wants it
    # excluded. Probe once to find the actual no-manifest content length at
    # a tight width, THEN size the real tight_route off THAT measurement so
    # "base fits, base+note does not" holds regardless of which optional
    # block (manifest, DRY) the freed-up room would otherwise go to.
    probe_route = RouteDef(route_id="probe", cli="fake", model="m", argv_max=len(base_prompt) + 20)
    _p, probe_base = adapters.build_dispatch(probe_route, role=Role.IMPLEMENTER, **kw)
    tight_route = RouteDef(route_id="tight", cli="fake", model="m", argv_max=len(probe_base) + 20)
    # Baseline must be built under the SAME route: the doctrine manifest is
    # argv-budgeted too (doc-ownership 2026-07-23), so comparing a tight build
    # against a WIDE-route baseline would conflate two independent variables.
    _b, tight_base = adapters.build_dispatch(tight_route, role=Role.IMPLEMENTER, **kw)
    _argv, prompt = adapters.build_dispatch(
        tight_route, role=Role.IMPLEMENTER, approved_amendments=["src/demo/shared_helper.py"], **kw)

    assert "Amendment-approved" not in prompt   # skipped, not truncated
    assert "src/demo/shared_helper.py" not in prompt
    assert len(prompt) <= tight_route.argv_max   # never raises AdapterError
    assert prompt == tight_base                  # byte-identical to the no-amendments dispatch


def test_review_independent_amendment_note_skipped_when_argv_max_too_tight():
    """B21 regression guard: the LIVE-observed failure mode (see LOG.md/
    the test above) was specifically on THIS branch -- the reviewer prompt
    is already close to argv_max with real packet paths, so an unconditional
    note overflowed it and permanently stranded a review dispatch (the
    daemon never retries a review whose Attempt record already exists).
    Same bounded "skip, never strand" fix as the IMPLEMENTER side."""
    kw = dict(handoff_path="h.md", worktree="/wt", branch="feat/T1",
              task_id="T1", gate_hint="pytest -q", receipt_path="r.json")
    route_wide = _fake_route()
    _a, base_prompt = adapters.build_dispatch(
        route_wide, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)

    tight_route = RouteDef(route_id="tight", cli="fake", model="m", argv_max=len(base_prompt) + 20)
    # Baseline must be built under the SAME route: the doctrine manifest is
    # argv-budgeted too (doc-ownership 2026-07-23), so comparing a tight build
    # against a WIDE-route baseline would conflate two independent variables.
    _b, tight_base = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)
    _argv, prompt = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x",
        approved_amendments=["src/demo/shared_helper.py"], **kw)

    assert "Scope-amendment note" not in prompt
    assert "src/demo/shared_helper.py" not in prompt
    assert len(prompt) <= tight_route.argv_max
    assert prompt == tight_base


# ============================================================================
# DRY standing instructions (2026-07-26, routing-model-redesign.md D-R2).
# Explicit standing instructions on EVERY implementer/reviewer dispatch
# enforcing code-standards (DRY, non-duplication). Oracle O1: the IMPLEMENTER
# prompt contains the DRY instruction. Oracle O2: the REVIEW_INDEPENDENT prompt
# contains the DRY instruction AND explicitly says to REJECT (not fix)
# production-logic duplication. Oracle O3: the existing argv_max boundary test
# still passes with the additions included. Each pins a NEGATIVE so the
# assertion cannot pass hollowly.
# ============================================================================

def test_implementer_prompt_contains_dry_instruction():
    """O1: EVERY implementer dispatch is told to keep the diff DRY: extract
    a shared helper instead of copying similar logic. NEGATIVE: a CARVER
    dispatch does NOT get this instruction (carvers write handoff files, not
    production code)."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER,
    )
    assert "Keep your diff DRY" in prompt
    assert "extract a shared helper" in prompt

    _a2, carver_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.CARVER, carve_authority="branch",
    )
    assert "Keep your diff DRY" not in carver_prompt


def test_review_independent_prompt_contains_dry_instruction():
    """O2: EVERY review_independent dispatch is told to check for DRY
    violations and REJECT (never fix) when the implementer has copied
    production logic without extracting a shared helper. NEGATIVE: the
    IMPLEMENTER and CARVER prompts do NOT contain this reviewer-facing
    instruction."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    assert "DRY" in prompt
    assert "reject duplicated production logic" in prompt
    assert "never fix it yourself" in prompt

    _a2, impl_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER,
    )
    assert "DRY check" not in impl_prompt


def test_review_independent_dry_and_reject_class_both_present():
    """Regression guard: the DRY instruction is appended AFTER the existing
    REJECT_CLASS line, so both must coexist in the prompt. This pins that
    adding DRY did not accidentally remove or elide REJECT_CLASS."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    assert "REJECT_CLASS" in prompt
    assert "DRY" in prompt
    # Ensure DRY appears after REJECT_CLASS (order matters for clarity)
    reject_pos = prompt.find("REJECT_CLASS")
    dry_pos = prompt.find("DRY")
    assert reject_pos < dry_pos, "REJECT_CLASS should appear before DRY in prompt"


def test_review_independent_prompt_with_dry_yields_the_real_paths_margin():
    """O3 (extends test_review_independent_prompt_stays_under_argv_max_with_
    real_paths): the SAME long-path/attempt-id scenario that guards the base
    reviewer prompt's 200-char safety margin. DRY is checked against that SAME
    margin (argv_max - 200, mirroring the doctrine manifest's own check), so
    for this real-long-path scenario it correctly YIELDS -- proving DRY is not
    the addition that finally eats the margin B4b's own live incident exists
    to protect. NEGATIVE (the simple-path case, in
    test_review_focus_absent_prompt_is_byte_identical_to_pre_d1's snapshot)
    proves DRY is not simply dead code -- it DOES appear when there is room."""
    long_task = "nyxloom-P74-reviewer-session-reuse-and-spine-digest"
    _argv, prompt = adapters.build_dispatch(
        _fake_route(),
        handoff_path=f"nyxloom-trove/handoffs/{long_task}.md",
        worktree=f"/workspaces/vbpub/.worktrees/flow-stages/nyxloom/.worktrees/feat/{long_task}",
        branch=f"feat/{long_task}", task_id=long_task,
        gate_hint="cd .. && MOCK_MODE=true pytest tests -q",
        receipt_path=f"/state/attempts/att-0123456789abcdef/receipt.json",
        role=Role.REVIEW_INDEPENDENT, attempt_id="att-0123456789abcdef",
    )
    # argv_max default is 1500; keep >= 200 chars of headroom for even longer paths.
    assert len(prompt) <= 1300, f"reviewer prompt too long ({len(prompt)}); trim content"
    assert "REJECT_CLASS" in prompt   # base reviewer content survives
    assert "DRY" not in prompt        # skipped -- the 200-char margin is protected


# ============================================================================
# D1 factory-hardening (2026-07-25, plan-factory-hardening.md §D part 1).
# `review_focus` is the carve-authored "adversarially check these" hint list
# (types.Frontmatter.review_focus), injected into the REVIEW_INDEPENDENT
# prompt using the SAME bounded-append idiom as prior_verdict/approved_
# amendments above. Oracle O1: the hints actually reach the prompt. Oracle
# O2: an absent review_focus reproduces the EXACT pre-D1 prompt (snapshot
# equality, not just "doesn't crash"). Oracle O3: a worst-case review_focus
# never overflows argv_max (extends the existing guard test). Each pins a
# NEGATIVE so the assertion cannot pass hollowly.
# ============================================================================

# Captured verbatim from build_dispatch BEFORE the D1 change (git blame:
# pre-review_focus adapters.py), for the simple-paths REVIEW_INDEPENDENT
# case below -- the O2 snapshot-equality proof.
_PRE_D1_SIMPLE_REVIEW_PROMPT = (
    "Handoff: h.md\n"
    "Worktree: /wt\n"
    "Gate: pytest -q\n"
    "Receipt: r.json\n"
    "You are REVIEWING this packet, not authoring code changes. Follow the "
    "packet's REQUIRED OUTPUT CONTRACT: write your verdict as a `VERDICT: "
    "APPROVED` or `VERDICT: REJECTED` line into the <task>-REVIEW.md the "
    "packet names, then `git add` and `git commit` ONLY that review file "
    "onto the task's own feat/ branch -- never main, and never a code "
    "change. The daemon reads your verdict from that committed file, NOT "
    "from the receipt.\n"
    "If REJECTED, also add a line `REJECT_CLASS: <fixable|architectural|"
    "product>` (fixable=local defect, fix on retry; architectural=re-carve; "
    "product=human decision). Omit it on APPROVED.\n"
    "Doctrine: `reference/AUTHORING.md` + `reference/DOCTRINE.md`; then any "
    "same-named `nyxloom-trove/` sibling for project overrides.\n"
    "DRY: reject duplicated production logic; never fix it yourself."
)


def test_review_independent_prompt_embeds_review_focus():
    """O1: a REVIEW_INDEPENDENT dispatch with review_focus entries embeds
    each entry verbatim into the prompt. NEGATIVE: the same review_focus
    passed to an IMPLEMENTER dispatch is NOT embedded (only the reviewer
    consults it -- mirrors prior_verdict/approved_amendments' own split)."""
    focus = ["check the CAS revert for a stale head_commit", "check None-safety"]
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_focus=focus,
    )
    assert "check the CAS revert for a stale head_commit" in prompt
    assert "check None-safety" in prompt
    assert "Carve-authored focus" in prompt

    _a2, impl_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, review_focus=focus,
    )
    assert "check the CAS revert for a stale head_commit" not in impl_prompt
    assert "Carve-authored focus" not in impl_prompt


def test_review_focus_absent_prompt_is_byte_identical_to_pre_d1():
    """O2: a REVIEW_INDEPENDENT dispatch with NO review_focus (the default,
    and every pre-D1 handoff) reproduces the prompt EXACTLY -- proving zero
    regression for existing handoffs, not merely 'still works'. Checked both
    via the literal snapshot and via explicit review_focus=None/[] parity."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    assert prompt == _PRE_D1_SIMPLE_REVIEW_PROMPT

    _a2, prompt_explicit_none = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_focus=None,
    )
    _a3, prompt_explicit_empty = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_focus=[],
    )
    assert prompt_explicit_none == _PRE_D1_SIMPLE_REVIEW_PROMPT
    assert prompt_explicit_empty == _PRE_D1_SIMPLE_REVIEW_PROMPT


def test_review_independent_prompt_with_realistic_review_focus_stays_under_argv_max():
    """O3 (extends test_review_independent_prompt_stays_under_argv_max_with_
    real_paths): the SAME long-path/attempt-id scenario that guards the base
    reviewer prompt, now ALSO carrying a realistic multi-hint review_focus,
    must still stay within argv_max -- and the hints must actually survive
    (not be silently dropped) for a realistic size."""
    long_task = "nyxloom-P74-reviewer-session-reuse-and-spine-digest"
    focus = [
        "check the CAS revert for a stale head_commit",
        "check None-safety on the wrapper timeout path",
        "verify the retry backoff never exceeds max_cost",
    ]
    _argv, prompt = adapters.build_dispatch(
        _fake_route(),
        handoff_path=f"nyxloom-trove/handoffs/{long_task}.md",
        worktree=f"/workspaces/vbpub/.worktrees/flow-stages/nyxloom/.worktrees/feat/{long_task}",
        branch=f"feat/{long_task}", task_id=long_task,
        gate_hint="cd .. && MOCK_MODE=true pytest tests -q",
        receipt_path="/state/attempts/att-0123456789abcdef/receipt.json",
        role=Role.REVIEW_INDEPENDENT, attempt_id="att-0123456789abcdef",
        review_focus=focus,
    )
    assert len(prompt) <= 1500
    assert "REJECT_CLASS" in prompt                 # base reviewer content survives
    assert "check the CAS revert" in prompt          # realistic focus survives too


def test_review_independent_prompt_worst_case_review_focus_degrades_never_raises():
    """O3: an unrealistically large review_focus (far more hints than any
    real carve would author) on the SAME tight long-path scenario is
    TRUNCATED to fit, never raises AdapterError and never exceeds argv_max
    -- 'degrade, never strand the dispatch', same philosophy as
    prior_verdict's own truncation guard."""
    long_task = "nyxloom-P74-reviewer-session-reuse-and-spine-digest"
    huge_focus = [
        f"adversarially check hypothesis {i} about a subtle race condition "
        "in the reconcile loop involving stale receipts and CAS retries"
        for i in range(20)
    ]
    _argv, prompt = adapters.build_dispatch(
        _fake_route(),
        handoff_path=f"nyxloom-trove/handoffs/{long_task}.md",
        worktree=f"/workspaces/vbpub/.worktrees/flow-stages/nyxloom/.worktrees/feat/{long_task}",
        branch=f"feat/{long_task}", task_id=long_task,
        gate_hint="cd .. && MOCK_MODE=true pytest tests -q",
        receipt_path="/state/attempts/att-0123456789abcdef/receipt.json",
        role=Role.REVIEW_INDEPENDENT, attempt_id="att-0123456789abcdef",
        review_focus=huge_focus,
    )
    assert len(prompt) <= 1500
    assert "truncated to fit" in prompt
    assert "REJECT_CLASS" in prompt                  # base reviewer content never sacrificed


def test_review_independent_review_focus_skipped_when_argv_max_too_tight():
    """B21-style regression guard, applied to review_focus: when even the
    HEADER alone would not leave 40 chars of room, the whole block is
    skipped -- never truncated down to something unreadable, never raising.
    Mirrors test_review_independent_amendment_note_skipped_when_argv_max_
    too_tight's route-tightening technique, but anchored to the MANIFEST-
    FREE floor length (not a wide-route base_prompt, which would still
    include the doctrine manifest and so understate the true headroom)."""
    kw = dict(handoff_path="h.md", worktree="/wt", branch="feat/T1",
              task_id="T1", gate_hint="pytest -q", receipt_path="r.json")
    # Probe the manifest-free core prompt length: a deliberately tight
    # argv_max forces the (itself argv-budgeted) doctrine manifest to be
    # skipped, isolating the REVIEW_INDEPENDENT core content this test
    # anchors its margin to.
    floor_route = RouteDef(route_id="floor", cli="fake", model="m", argv_max=1000)
    _f, floor_prompt = adapters.build_dispatch(
        floor_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)
    assert "Doctrine:" not in floor_prompt  # sanity: manifest genuinely absent

    # A route with room for the core prompt but NOT even the review_focus
    # header (room = argv_max - len(prompt) - len(header) < 40 by construction).
    tight_route = RouteDef(route_id="tight", cli="fake", model="m",
                            argv_max=len(floor_prompt) + 25)
    _b, tight_base = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)
    _argv, prompt = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x",
        review_focus=["check the CAS revert"], **kw)

    assert "Carve-authored focus" not in prompt   # skipped, not truncated
    assert "check the CAS revert" not in prompt
    assert len(prompt) <= tight_route.argv_max    # never raises AdapterError
    assert prompt == tight_base                   # byte-identical to the no-focus dispatch


def test_review_focus_only_embedded_for_review_independent_role():
    """The docstring's claim generalizes prior_verdict/approved_amendments'
    own pin: review_focus passed to a CARVER dispatch is not embedded (the
    hint list is reviewer-facing only)."""
    focus = ["unique-marker-review-focus-xyz"]
    _a, carver_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.CARVER, carve_authority="branch", review_focus=focus,
    )
    assert "unique-marker-review-focus-xyz" not in carver_prompt


# ============================================================================
# D-BATCHC (2026-07-26, plan-factory-hardening.md §D part 2): review-depth
# directive by complexity band (Frontmatter.tier, with a scope.touch-size
# fallback) + declared gate rigor (GateDef.asserts). Mirrors review_focus's
# own oracle set immediately above -- pure-helper correctness (O1/O3/O4),
# the load-bearing byte-identical-when-neutral safety property (O2), role
# scoping (O5), worst-case argv degradation (O6), and coexistence with
# review_focus (O7). Each pins a NEGATIVE so the assertion cannot pass
# hollowly.
# ============================================================================

def test_compute_review_depth_directive_embeds_high_complexity_and_shallow_gate():
    """Oracle 1: a high band (implement-3) combined with a shallow gate
    (asserts only tests-pass -- missing changed-line-coverage AND mutation)
    returns a non-empty directive naming BOTH reasons. build_dispatch then
    embeds that directive verbatim into the REVIEW_INDEPENDENT prompt."""
    directive = adapters.compute_review_depth_directive(
        tier="implement-3", scope_touch=["a.py"], gate_asserts=["tests-pass"])
    assert directive != ""
    assert "high-complexity" in directive          # names the band reason
    assert "tests-pass" in directive                # names the shallow-gate reason
    assert "coverage" in directive and "mutation" in directive

    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_depth=directive,
    )
    assert directive in prompt
    assert "Review depth:" in prompt


def test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc():
    """Oracle 2 (LOAD-BEARING SAFETY ORACLE): low band (implement-1) +
    rigorous gate (both changed-line-coverage AND mutation declared) ->
    compute_review_depth_directive returns ''. build_dispatch's output for
    that '' is then BYTE-IDENTICAL to the pre-BATCHC prompt (the same
    snapshot O2 pins for review_focus, since neither feature fires here) --
    proving zero regression for every existing handoff, not merely 'still
    works'. Checked via the literal snapshot AND explicit review_depth=""
    /default-omitted parity."""
    directive = adapters.compute_review_depth_directive(
        tier="implement-1", scope_touch=["a.py"],
        gate_asserts=["tests-pass", "changed-line-coverage", "mutation"])
    assert directive == ""

    _argv, prompt_default = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT,
    )
    _a2, prompt_explicit_empty = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_depth=directive,
    )
    assert prompt_default == _PRE_D1_SIMPLE_REVIEW_PROMPT
    assert prompt_explicit_empty == _PRE_D1_SIMPLE_REVIEW_PROMPT
    assert "Review depth:" not in prompt_default
    assert "Review depth:" not in prompt_explicit_empty


def test_compute_review_depth_directive_shallow_gate_alone_triggers_it():
    """Oracle 3: LOW band (implement-1) but a shallow gate still fires --
    the directive names ONLY the rigor gap, NOT a high-complexity reason
    (proves the two signals are independent triggers, not just band-driven).
    Proves gate_asserts=[] and gate_asserts=None (a project with no
    declared gate at all) are both handled -- no crash, both read as
    maximally shallow."""
    for asserts in (["tests-pass"], [], None):
        directive = adapters.compute_review_depth_directive(
            tier="implement-1", scope_touch=["a.py"], gate_asserts=asserts)
        assert directive != "", f"gate_asserts={asserts!r} should still be shallow"
        assert "high-complexity" not in directive   # band is low -- NOT the reason
        assert "coverage" in directive and "mutation" in directive

    # No crash dispatching with either shallow-gate directive either.
    directive_empty_asserts = adapters.compute_review_depth_directive(
        tier="implement-1", scope_touch=["a.py"], gate_asserts=None)
    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_depth=directive_empty_asserts,
    )
    assert "declares no assertions" in prompt


def test_compute_review_depth_directive_scope_size_fallback():
    """Oracle 4: an absent/unparseable tier falls back to a scope.touch-size
    proxy. A large scope_touch (above _HIGH_BAND_SCOPE_TOUCH_THRESHOLD) is
    treated as band 3 (directive fires, naming high-complexity) even with a
    RIGOROUS gate paired in both cases -- isolating that the FALLBACK band
    alone (not gate rigor) drives the difference. A tiny scope_touch stays
    band 1 (directive empty)."""
    rigorous = ["tests-pass", "changed-line-coverage", "mutation"]
    large_scope = [f"src/file{i}.py" for i in range(8)]
    directive_large = adapters.compute_review_depth_directive(
        tier=None, scope_touch=large_scope, gate_asserts=rigorous)
    assert directive_large != ""
    assert "high-complexity" in directive_large

    tiny_scope = ["src/one_file.py"]
    directive_tiny = adapters.compute_review_depth_directive(
        tier=None, scope_touch=tiny_scope, gate_asserts=rigorous)
    assert directive_tiny == ""

    # An unrecognized (not just absent) tier string falls back the same way.
    directive_unparseable = adapters.compute_review_depth_directive(
        tier="not-a-real-tier", scope_touch=large_scope, gate_asserts=rigorous)
    assert directive_unparseable != ""
    assert "high-complexity" in directive_unparseable


def test_review_depth_only_embedded_for_review_independent_role():
    """Oracle 5: mirrors review_focus's own role-scoping pin -- a non-empty
    review_depth passed to an IMPLEMENTER (or CARVER) dispatch appends
    NOTHING (the directive is reviewer-facing only)."""
    directive = adapters.compute_review_depth_directive(
        tier="implement-3", scope_touch=["a.py"], gate_asserts=["tests-pass"])
    assert directive != ""  # sanity: a real, non-empty directive

    _a, impl_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.IMPLEMENTER, review_depth=directive,
    )
    assert directive not in impl_prompt
    assert "Review depth:" not in impl_prompt

    _b, carver_prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.CARVER, carve_authority="branch", review_depth=directive,
    )
    assert directive not in carver_prompt
    assert "Review depth:" not in carver_prompt


def test_review_depth_worst_case_argv_degrades_never_raises():
    """Oracle 6: mirrors test_review_independent_review_focus_skipped_when_
    argv_max_too_tight's technique -- when even the 'Review depth: ' header
    alone would not leave 40 chars of room, the whole block is skipped
    (never truncated to something unreadable, never raising), and the
    resulting prompt is byte-identical to the same dispatch without
    review_depth."""
    kw = dict(handoff_path="h.md", worktree="/wt", branch="feat/T1",
              task_id="T1", gate_hint="pytest -q", receipt_path="r.json")
    floor_route = RouteDef(route_id="floor", cli="fake", model="m", argv_max=1000)
    _f, floor_prompt = adapters.build_dispatch(
        floor_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)
    assert "Review depth:" not in floor_prompt  # sanity: genuinely absent

    tight_route = RouteDef(route_id="tight", cli="fake", model="m",
                            argv_max=len(floor_prompt) + 25)
    _b, tight_base = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)

    directive = adapters.compute_review_depth_directive(
        tier="implement-3", scope_touch=["a.py"], gate_asserts=["tests-pass"])
    _argv, prompt = adapters.build_dispatch(
        tight_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x",
        review_depth=directive, **kw)

    assert "Review depth:" not in prompt          # skipped, not truncated
    assert directive not in prompt
    assert len(prompt) <= tight_route.argv_max     # never raises AdapterError
    assert prompt == tight_base                    # byte-identical to the no-depth dispatch


def test_review_depth_truncates_when_room_tight_but_above_floor():
    """Oracle 6 (companion): the OTHER worst-case branch -- room is >= 40 (the
    header fits) but too small for the full directive body. The body is
    TRUNCATED-WITH-MARKER (never silently dropped, never raising), mirroring
    prior_verdict/review_focus's own truncate-to-fit behavior. Distinct from
    the test above, which covers the 'skip the whole block' branch (room <
    40) -- this one covers the 'truncate the body' branch (room >= 40 but
    < len(directive))."""
    kw = dict(handoff_path="h.md", worktree="/wt", branch="feat/T1",
              task_id="T1", gate_hint="pytest -q", receipt_path="r.json")
    floor_route = RouteDef(route_id="floor", cli="fake", model="m", argv_max=1000)
    _f, floor_prompt = adapters.build_dispatch(
        floor_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x", **kw)

    directive = adapters.compute_review_depth_directive(
        tier="implement-3", scope_touch=["a.py"], gate_asserts=["tests-pass"])
    assert len(directive) > 60  # sanity: long enough that a 46-char room truncates it

    # room = argv_max - len(floor_prompt) - len("\nReview depth: ") ~= 46:
    # comfortably >= 40 (header fits, block is NOT skipped) but far short of
    # len(directive) (body must truncate).
    medium_route = RouteDef(route_id="medium", cli="fake", model="m",
                            argv_max=len(floor_prompt) + 60)
    _argv, prompt = adapters.build_dispatch(
        medium_route, role=Role.REVIEW_INDEPENDENT, attempt_id="att-x",
        review_depth=directive, **kw)

    assert "Review depth:" in prompt               # header present -- not skipped
    assert directive not in prompt                 # full body did NOT fit
    assert "truncated to fit" in prompt             # truncation marker present
    assert len(prompt) <= medium_route.argv_max     # never raises AdapterError, never overflows


def test_review_depth_coexists_with_review_focus():
    """Oracle 7: a REVIEW_INDEPENDENT dispatch carrying BOTH a review_focus
    list and a review_depth directive embeds BOTH, still under argv_max,
    with review_depth appended AFTER review_focus (sane order/spacing --
    the focus header/body is not corrupted by the depth append)."""
    focus = ["check the CAS revert for a stale head_commit"]
    directive = adapters.compute_review_depth_directive(
        tier="implement-3", scope_touch=["a.py"], gate_asserts=["tests-pass"])
    assert directive != ""

    _argv, prompt = adapters.build_dispatch(
        _fake_route(), handoff_path="h.md", worktree="/wt", branch="feat/T1",
        task_id="T1", gate_hint="pytest -q", receipt_path="r.json",
        role=Role.REVIEW_INDEPENDENT, review_focus=focus, review_depth=directive,
    )
    assert "check the CAS revert for a stale head_commit" in prompt
    assert "Carve-authored focus" in prompt
    assert directive in prompt
    assert "Review depth:" in prompt
    assert len(prompt) <= 1500  # _fake_route().argv_max is None -> build_dispatch's 1500 default
    # review_depth is appended LAST (after review_focus).
    assert prompt.index("Carve-authored focus") < prompt.index("Review depth:")


# ---------------------------------------------------------------------------
# P05a (docs/plan-logging.md §5): adapters.py instrumentation -- a provider
# call -> DEBUG; "a route probe failure/pause" is a named WARNING example.

def test_probe_emits_debug_and_warning_on_failure(tmp_path):
    log_dir = tmp_path / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    route = RouteDef(route_id="probe-route", cli="fake", model="fake-model", probe=["false"])
    ok, _detail = adapters.probe(route)
    assert ok is False

    records = _read_log_records(log_dir)
    probed = [r for r in records if r.get("msg") == "probe"]
    assert len(probed) == 1
    assert probed[0]["level"] == "debug"
    assert probed[0]["route"] == "probe-route"

    failed = [r for r in records if r.get("msg") == "probe-failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warning"
    assert failed[0]["route"] == "probe-route"
    assert "exit code" in failed[0]["detail"]


def test_probe_success_emits_no_warning(tmp_path):
    """The DEBUG probe call fires either way; the WARNING is failure-only."""
    log_dir = tmp_path / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    route = RouteDef(route_id="probe-route-ok", cli="fake", model="fake-model", probe=["true"])
    ok, _detail = adapters.probe(route)
    assert ok is True

    records = _read_log_records(log_dir)
    assert any(r.get("msg") == "probe" and r["level"] == "debug" for r in records)
    assert not any(r.get("msg") == "probe-failed" for r in records)


def test_capture_session_discover_emits_debug(tmp_path, monkeypatch, emit_script):
    json_output = json.dumps([{"id": "sess-1", "dir": "/tmp/wt", "title": "Session 1"}])
    emit_file = tmp_path / "emit.txt"
    emit_file.write_text(json_output)
    monkeypatch.setenv("EMIT_FILE", str(emit_file))

    log_dir = tmp_path / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    route = RouteDef(route_id="discover-route", cli="fake", model="fake-model",
                      session_discover=[str(emit_script)])
    result = adapters.capture_session(
        route, attempt_dir=tmp_path / "attempt", worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc),
    )
    assert result == "sess-1"

    records = _read_log_records(log_dir)
    discovered = [r for r in records if r.get("msg") == "session-discover"]
    assert len(discovered) == 1
    assert discovered[0]["level"] == "debug"
    assert discovered[0]["route"] == "discover-route"
    assert discovered[0]["worktree"] == "/tmp/wt"


def test_probe_file_not_found_emits_warning(tmp_path):
    """§5: the FileNotFoundError branch (command-not-found) also emits the
    WARNING probe-failed record."""
    log_dir = tmp_path / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    route = RouteDef(route_id="probe-route-missing", cli="fake", model="fake-model",
                      probe=["/nonexistent/binary/xyz-does-not-exist"])
    ok, detail = adapters.probe(route)
    assert ok is False
    assert "command not found" in detail

    records = _read_log_records(log_dir)
    failed = [r for r in records if r.get("msg") == "probe-failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warning"
    assert failed[0]["route"] == "probe-route-missing"
    assert "command not found" in failed[0]["detail"]


def test_probe_generic_exception_emits_warning(tmp_path):
    """§5: the catch-all Exception branch also emits the WARNING
    probe-failed record."""
    import subprocess
    from unittest.mock import patch

    log_dir = tmp_path / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    route = RouteDef(route_id="probe-route-boom", cli="fake", model="fake-model", probe=["true"])
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        ok, detail = adapters.probe(route)
    assert ok is False
    assert detail == "boom"

    records = _read_log_records(log_dir)
    failed = [r for r in records if r.get("msg") == "probe-failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warning"
    assert failed[0]["route"] == "probe-route-boom"
    assert failed[0]["detail"] == "boom"


# ---------------------------------------------------------------------------
# Doc-ownership read-first manifest (2026-07-23)

def test_implementer_prompt_carries_the_doctrine_manifest():
    """Canonical doctrine ships with the product; the dispatch prompt emits a
    read-first POINTER to it (never the content) plus the same-named
    nyxloom-trove sibling convention for project overrides."""
    _argv, prompt = adapters.build_dispatch(
        _fake_route(),
        handoff_path="nyxloom-trove/handoffs/t.md", worktree="/w", branch="b",
        task_id="t", gate_hint="pytest -q", receipt_path="/r.json",
        role=Role.IMPLEMENTER, attempt_id="att-1",
    )
    assert "reference/AUTHORING.md" in prompt
    assert "reference/DOCTRINE.md" in prompt
    assert "nyxloom-trove/" in prompt


def test_doctrine_manifest_is_skipped_when_it_would_eat_argv_headroom():
    """THE NEGATIVE: the manifest is a convenience, never a dispatch risk. With
    an argv_max so tight that appending it would consume the reserved 200-char
    headroom, it is dropped and the prompt still builds (a stranded dispatch is
    strictly worse than a missing doc pointer)."""
    route = _fake_route()
    route.argv_max = 800
    _argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="nyxloom-trove/handoffs/t.md", worktree="/w", branch="b",
        task_id="t", gate_hint="pytest -q", receipt_path="/r.json",
        role=Role.IMPLEMENTER, attempt_id="att-1",
    )
    assert "reference/DOCTRINE.md" not in prompt
    assert "Handoff:" in prompt          # the real prompt still built

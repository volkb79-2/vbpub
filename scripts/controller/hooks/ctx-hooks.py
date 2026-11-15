#!/usr/bin/env python3
"""Controller context hooks: pressure notes, readback building, recovery notes.

Usage: ctx-hooks.py <statusline|post-tool-use|pre-compact|session-start|stop> [options]

Reads the harness hook JSON from stdin and NEVER raises: a hook failure must
not disturb the session (every error path logs one line, prints nothing that
could block a turn, and exits 0). A hard 20 s cap guards the readback build, so
a slow extract can never hold a compaction.

Subcommands (the Claude Code hook events they serve):

  statusline     status line text: local time and the context fill.
  post-tool-use  the pressure ladder: a note every 50k estimated tokens from
                 100k on, delivered as PostToolUse `additionalContext`
                 (below 200k: finish the current unit of work). From 200k the
                 wording depends on the session kind: an interactive
                 CONTROLLER is told to persist its state at the next semantic
                 boundary and CONTINUE (it cannot terminate itself and must
                 never wait for a compaction); a SPAWNED session is told to
                 stop at the boundary with a brief.
  pre-compact    builds the readback into `<state dir>/readback-N.md` and
                 prints a short note for the SUMMARISER: keep the session's
                 goal and the in-flight list, then the read order is
                 republished after compaction by `session-start`.
  session-start  after a compaction (`source` compact), a resume or a fresh
                 startup: the read order of the controller state files, the
                 newest readback's path and size, and -- when the build
                 failed -- the exact manual command to build it.
  stop           a user-visible scrollback marker (time and context fill).

Configuration (flags or environment; nothing is hard-coded):

  --state-dir / CTX_STATE_DIR      controller state directory. Default:
                                   `<hook cwd>/.controller-state` (derived from
                                   the hook input, never a fixed checkout path).
  --extract / CTX_EXTRACT_CMD      the extract command. Default:
                                   `nyxloom-harness` resolved on PATH.
  --budget-tokens / CTX_READBACK_BUDGET_TOKENS
                                   30000 (the controller profile).
  --tail-prose-tokens / CTX_READBACK_TAIL_PROSE_TOKENS
                                   10000.
  --goal-file / CTX_READBACK_GOAL_FILE
                                   pin the readback's goal anchor from this file.
  --session-kind / CTX_SESSION_KIND
                                   controller | spawned. Default: a hook fired
                                   inside an Agent-tool subagent (`agent_id` in
                                   the input) is spawned, the main session is
                                   the controller.
  --timeout / CTX_READBACK_TIMEOUT 15 seconds (capped at 20).
  --log / CTX_HOOK_LOG             default `<state dir>/hooks/hook-log.jsonl`.

The controller points its own settings at this file; the repository ships the
script and the docs, never a controller's settings.local.json or state files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STEP = 50_000          # the ladder's cadence
ARM_AT = 100_000       # first note from this fill on
STOP_AT = 200_000      # from here the wording says stop at the next boundary
HARD_TIMEOUT = 20.0    # a compaction must never wait longer than this
READ_ORDER_LIMIT = 9_000  # hook stdout/additionalContext cap is 10,000 chars
ACTIVE_LOG: Path | None = None  # the resolved log, so main() can log an unexpected crash

# The read order of the controller's own state files, in order. `{state}` is
# the resolved state directory (never a fixed checkout path).
READ_ORDER = """CONTROLLER RESUME (printed by the SessionStart[compact] hook).
Read in this order and nothing else first:
 (0) plan.yaml (the RESUME item, then PROGRESS-last, PROCESS-*, DEC-MATRIX, OPEN-OPERATOR, PEER-1)
 (1) {state}/operator-decisions.md (verbatim operator turns; the last two sections)
 (2) the newest {state}/readback-N.md IN FULL (read it in chunks of about 450 lines)
 (3) {state}/HANDOFF.md (section 'RESUME HERE #N', the last one)
Then check reality before acting: running jobs without a .done marker, `git worktree list`, host campaigns, and re-arm ONE Monitor."""

MANUAL = (
    "BUILD IT BY HAND (the automatic build did not produce a readback):\n"
    "  {extract} extract {session} --preset readback --budget-tokens {budget} "
    "--tail-prose-tokens {tail}{since}{goal} > {state}/readback-N.md"
)

MARKER_RE = re.compile(r"^\[readback marker: (?P<marker>[^\]]*)\]$", re.MULTILINE)
READBACK_FILE_RE = re.compile(r"^readback-(\d+)\.md$")


# ---------------------------------------------------------------- plumbing


def log(path: Path | None, record: dict) -> None:
    """One JSON object per event; a logging failure is itself never fatal."""
    if path is None:
        return
    try:
        record = dict(record)
        record["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def options(argv: list[str]) -> tuple[str, argparse.Namespace]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", default="")
    parser.add_argument("--state-dir")
    parser.add_argument("--extract")
    parser.add_argument("--budget-tokens", type=int)
    parser.add_argument("--tail-prose-tokens", type=int)
    parser.add_argument("--goal-file")
    parser.add_argument("--session-kind")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--log")
    args = parser.parse_args(argv)
    return args.mode, args


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_state_dir(args: argparse.Namespace, hook: dict) -> Path | None:
    """The state directory: flag, then environment, then the hook's own cwd."""
    explicit = args.state_dir or os.environ.get("CTX_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    cwd = hook.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).expanduser() / ".controller-state"
    return None


def resolve_extract(args: argparse.Namespace) -> list[str]:
    """The extract command: flag/env may name an executable or a command line."""
    raw = args.extract or os.environ.get("CTX_EXTRACT_CMD") or "nyxloom-harness"
    return raw.split()


def resolve_log(args: argparse.Namespace, state: Path | None) -> Path | None:
    raw = args.log or os.environ.get("CTX_HOOK_LOG")
    if raw:
        return Path(raw).expanduser()
    return None if state is None else state / "hooks" / "hook-log.jsonl"


def transcript_of(hook: dict) -> str:
    """The transcript this event concerns.

    Measured 2026-11-14 (live controller session): INSIDE A SUBAGENT
    `transcript_path` is the PARENT's transcript; the subagent's own file is
    `<parent dir>/<session_id>/subagents/agent-<agent_id>.jsonl`. Reading the
    parent's size as the subagent's caused a false "past 200k" note that made a
    subagent stop early, so the subagent's own file is used whenever it exists.
    """
    path = hook.get("transcript_path") or ""
    if not path or not isinstance(path, str):
        return ""
    agent_id = hook.get("agent_id")
    if agent_id:
        own = Path(path).parent / str(hook.get("session_id") or "") / "subagents" / (
            f"agent-{agent_id}.jsonl"
        )
        return str(own) if own.exists() else ""
    return str(path)


def ctx_size(path: str) -> int | None:
    """Estimated context fill: the last assistant message's input + cache read
    + cache creation tokens (measured in E4), or None when it cannot be read."""
    if not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 400_000))
            tail = handle.read().decode("utf-8", "replace").splitlines()
        for line in reversed(tail):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            usage = (rec.get("message") or {}).get("usage")
            if not usage:
                continue
            return (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            )
    except Exception:
        return None
    return None


def read_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_state(path: Path, payload: dict) -> None:
    """Write via a temporary sibling and a rename, so a reader never sees half
    a state file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------- modes


def session_kind(args: argparse.Namespace, hook: dict) -> str:
    """`controller` (an interactive session: persist and continue) or
    `spawned` (a worker: self-terminate at a boundary). Explicit flag/env
    first; otherwise a hook fired inside an Agent-tool subagent (`agent_id`
    present) is spawned and the main session is the controller."""
    explicit = (args.session_kind or os.environ.get("CTX_SESSION_KIND") or "").strip().lower()
    if explicit in ("controller", "spawned"):
        return explicit
    return "spawned" if hook.get("agent_id") else "controller"


def pressure_note(
    fill: int | None, state: Path | None, who: str, kind: str = "controller",
) -> str | None:
    """The pressure ladder: note every STEP from ARM_AT on, wording by band
    and by session kind (the same fill means persist-and-continue to an
    interactive controller and stop-at-a-boundary to a spawned session)."""
    if fill is None:
        return None
    step = fill // STEP
    key = state / f"pressure-{who}.json" if state is not None else None
    stored = read_state(key) if key is not None else {}
    try:
        recorded = int(stored.get("step", 0))
    except Exception:
        recorded = 0
    if step < recorded:
        # The context shrank (a compaction happened): re-arm the ladder.
        if key is not None:
            write_state(key, {"step": step, "at": fill})
        return None
    if step <= recorded or step < ARM_AT // STEP:
        return None
    if key is not None:
        write_state(key, {"step": step, "at": fill})
    k = fill // 1000
    controller = kind != "spawned"
    if fill >= STOP_AT and controller:
        # An interactive controller cannot terminate itself and must not stand
        # still waiting for a compaction (AGENTS.md, "Interactive controller
        # session"): persist and CONTINUE.
        return (
            f"[context {k}k] Past {STOP_AT // 1000}k: at the next semantic boundary (a unit of "
            "work is finished and its outcome is durably recorded; nothing half-edited) persist "
            "your working state (plan, decision log, handoff) and then CONTINUE working. Do not "
            "stop and do not wait for a compaction: it happens when the harness or the operator "
            "triggers it, and the recovery files plus the readback carry you across it."
        )
    if fill >= STOP_AT:
        return (
            f"[context {k}k] Past {STOP_AT // 1000}k: stop at the next semantic boundary (a unit of "
            "work is finished and its outcome is durably recorded; nothing half-edited), write "
            "your continuation brief, then end."
        )
    after = (
        f"from {STOP_AT // 1000}k on, persist your working state at the next semantic boundary "
        "and continue." if controller else
        f"from {STOP_AT // 1000}k on, stop at the next semantic boundary."
    )
    return (
        f"[context {k}k] Context pressure is rising: prefer to finish the current unit of work "
        f"and record its outcome before starting a new one; {after}"
    )


def readback_number(state: Path, recorded: int) -> int:
    """The next readback number: one past the highest state file on disk, so a
    state file lost to a crash cannot re-use a number."""
    highest = recorded
    try:
        for entry in state.iterdir():
            match = READBACK_FILE_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    except Exception:
        pass
    return highest + 1


def readback_command(
    extract: list[str],
    transcript: str,
    budget: int,
    tail: int,
    marker: str | None,
    goal_file: str | None,
) -> list[str]:
    command = [
        *extract, "extract", transcript,
        "--preset", "readback",
        "--budget-tokens", str(budget),
        "--tail-prose-tokens", str(tail),
        "--no-color",
    ]
    if marker:
        command += ["--since", marker]
    if goal_file:
        command += ["--goal-file", goal_file]
    return command


def build_readback(
    extract: list[str],
    transcript: str,
    state: Path,
    budget: int,
    tail: int,
    timeout: float,
    log_path: Path | None,
    goal_file: str | None,
) -> dict:
    """Build `<state>/readback-N.md` and record its marker. Never raises."""
    stored = read_state(state / "readback-state.json")
    marker = stored.get("marker") or None
    if isinstance(marker, str) and marker in ("", "none"):
        marker = None
    number = readback_number(state, int(stored.get("n") or 0))
    target = state / f"readback-{number}.md"
    command = readback_command(extract, transcript, budget, tail, marker, goal_file)
    result = {
        "ok": False, "n": number, "path": str(target), "marker": marker,
        "command": command, "reason": "",
    }
    if not os.path.isfile(transcript):
        result["reason"] = f"no readable transcript at {transcript}"
        return _failed(state, log_path, result, None)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        result["reason"] = f"timeout after {timeout:g}s"
        return _failed(state, log_path, result, None)
    except Exception as exc:
        result["reason"] = f"could not run: {exc!r}"[:200]
        return _failed(state, log_path, result, None)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        result["reason"] = (
            f"extract exited {completed.returncode}: "
            f"{detail[-1][:300] if detail else 'no output'}"
        )
        return _failed(state, log_path, result, completed.returncode)
    text = completed.stdout or ""
    if not text.strip():
        result["reason"] = "extract printed nothing"
        return _failed(state, log_path, result, 0)
    new_marker = marker
    match = MARKER_RE.search(text)
    if match:
        value = match.group("marker").strip()
        if value and not value.startswith("none"):
            new_marker = value
    try:
        state.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except Exception as exc:
        result["reason"] = f"could not write {target}: {exc!r}"[:200]
        return _failed(state, log_path, result, 0)
    result.update(ok=True, marker=new_marker, bytes=len(text),
                  estimated_tokens=max(1, len(text) // 4))
    write_state(state / "readback-state.json", {
        "n": number, "path": str(target), "marker": new_marker,
        "bytes": len(text), "ok": True, "command": command,
    })
    log(log_path, {"ev": "readback", **result, "rc": 0})
    return result


def _failed(state: Path, log_path: Path | None, result: dict, rc: int | None) -> dict:
    """Record a failed build: the next session must be told to build it by
    hand, so the failure is durable state, not only a log line."""
    log(log_path, {"ev": "readback", **result, "rc": rc})
    write_state(state / "readback-state.json", {
        "n": int(read_state(state / "readback-state.json").get("n") or 0),
        "path": result["path"], "marker": result.get("marker"), "ok": False,
        "reason": result.get("reason", ""), "command": result.get("command", []),
    })
    return result


def summariser_note(result: dict, state: Path) -> str:
    """The short instruction PreCompact prints into the summariser prompt."""
    if result.get("ok"):
        anchor = (
            f"The readback for the continuation is {result['path']} "
            f"({result.get('estimated_tokens', 0)}k estimated tokens); it is read AFTER the "
            "state files, not instead of them."
        )
    else:
        anchor = (
            "The readback could not be built automatically "
            f"({result.get('reason') or 'unknown reason'}); the next session must build it "
            "by hand (the SessionStart hook prints the exact command)."
        )
    return (
        "READBACK FOR THE CONTINUATION: keep (a) the session's ORIGINAL goal -- what the work "
        "is for, in the words it began with -- and (b) the IN-FLIGHT list: every running job, "
        "worktree, host campaign and pending decision, each with the identifier a later reader "
        "needs. Do not summarise them away in favour of narrative. " + anchor
    )


def session_start_text(
    state: Path, log_path: Path | None, extract: list[str], transcript: str,
    budget: int, tail: int, goal_file: str | None, session_name: str,
) -> str:
    """The read order, the newest readback, and -- on failure -- the manual
    command. Capped so the harness's 10,000-character hook cap never truncates
    the read order itself."""
    stored = read_state(state / "readback-state.json")
    order = READ_ORDER.format(state=state)
    lines = [order]
    newest = None
    try:
        numbers = sorted(
            int(match.group(1))
            for entry in state.iterdir()
            if (match := READBACK_FILE_RE.match(entry.name))
        )
        if numbers:
            newest = state / f"readback-{numbers[-1]}.md"
    except Exception:
        newest = None
    if newest is not None and newest.exists():
        size = newest.stat().st_size
        lines.append(
            f"NEWEST READBACK: {newest} ({size} bytes, about {max(1, size // 4)} estimated "
            "tokens; marker "
            f"{stored.get('marker') or 'unknown'}). Read it IN FULL as step (2)."
        )
    else:
        since = ""
        if stored.get("marker"):
            since = f" --since {stored['marker']}"
        manual = MANUAL.format(
            extract=" ".join(extract), session=transcript or "<session.jsonl>",
            budget=budget, tail=tail, since=since, state=state,
            goal=f" --goal-file {goal_file}" if goal_file else "",
        )
        reason = stored.get("ok") is False and stored.get("reason")
        if reason:
            lines.append(f"LAST BUILD FAILED: {reason}.")
        lines.append(manual)
    text = "\n".join(lines)
    log(log_path, {
        "ev": "SessionStart", "source": session_name, "readback": str(newest) if newest else "",
        "failed": newest is None,
    })
    if len(text) > READ_ORDER_LIMIT:
        return text[:READ_ORDER_LIMIT]
    return text


def must_keep_note(hook: dict, state: Path | None, mode: str, log_path: Path | None) -> None:
    """Log one event's shape (never its content: no secrets, no env values)."""
    log(log_path, {
        "ev": mode, "keys": sorted(k for k in hook if k), "state": str(state or ""),
    })


# ---------------------------------------------------------------- entrypoint


def run(mode: str, args: argparse.Namespace) -> int:
    try:
        hook = json.load(sys.stdin)
        if not isinstance(hook, dict):
            hook = {}
    except Exception:
        hook = {}
    state = resolve_state_dir(args, hook)
    log_path = resolve_log(args, state)
    global ACTIVE_LOG
    ACTIVE_LOG = log_path
    transcript = transcript_of(hook)
    fill = ctx_size(transcript)
    budget = args.budget_tokens or env_int("CTX_READBACK_BUDGET_TOKENS", 30_000)
    tail = args.tail_prose_tokens
    if tail is None:
        tail = env_int("CTX_READBACK_TAIL_PROSE_TOKENS", 10_000)
    goal_file = args.goal_file or os.environ.get("CTX_READBACK_GOAL_FILE") or None
    timeout = args.timeout
    if timeout is None:
        timeout = float(env_int("CTX_READBACK_TIMEOUT", 15)) if os.environ.get(
            "CTX_READBACK_TIMEOUT"
        ) else 15.0
    timeout = max(1.0, min(float(timeout), HARD_TIMEOUT))
    extract = resolve_extract(args)
    now = time.strftime("%H:%MZ", time.gmtime())

    if mode == "statusline":
        print(f"{now} ctx {fill // 1000}k" if fill is not None else f"{now} ctx ?")
        return 0

    if mode == "stop":
        must_keep_note(hook, state, "Stop", log_path)
        note = f"[{now} ctx {fill // 1000}k]" if fill is not None else f"[{now} ctx ?]"
        print(json.dumps({"systemMessage": note}))
        return 0

    if mode == "post-tool-use":
        who = str(hook.get("agent_id") or "main")
        must_keep_note(hook, state, "PostToolUse", log_path)
        note = pressure_note(fill, state, who, session_kind(args, hook))
        if note is None:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": note,
            },
        }))
        return 0

    if mode == "session-start":
        source = str(hook.get("source") or "startup")
        if state is None:
            log(log_path, {"ev": "SessionStart", "source": source, "error": "no state dir"})
            return 0
        if source == "compact":
            print(session_start_text(
                state, log_path, extract, transcript, budget, tail, goal_file, source,
            ))
        else:
            # resume/startup: the same read order, because a resumed session
            # (a manual compaction, an external one, or a different harness)
            # needs the artifact just as much as an automatic compaction does.
            print(session_start_text(
                state, log_path, extract, transcript, budget, tail, goal_file, source,
            ))
        return 0

    if mode == "pre-compact":
        must_keep_note(hook, state, "PreCompact", log_path)
        if state is None or not transcript:
            result = {"ok": False, "path": "", "reason": "no transcript or state directory",
                      "command": [], "n": 0, "estimated_tokens": 0}
            log(log_path, {"ev": "readback", **result, "trigger": hook.get("trigger")})
            print(summariser_note(result, state or Path(".")))
            return 0
        print(summariser_note(
            build_readback(extract, transcript, state, budget, tail, timeout, log_path, goal_file),
            state,
        ))
        return 0

    # Unknown or missing mode: log and stay silent.
    log(log_path, {"ev": "unknown-mode", "mode": mode})
    return 0


def main(argv: list[str]) -> int:
    try:
        mode, args = options(argv)
    except SystemExit:
        return 0  # a bad flag must not fail the session
    except Exception:
        return 0
    try:
        return run(mode, args)
    except Exception as exc:  # never disturb the session
        try:
            log(ACTIVE_LOG, {"ev": "hook-error", "mode": mode, "err": repr(exc)[:200]})
            if mode == "session-start":
                # The recovery read must never go silent: say how to build it.
                print(
                    "The controller hook failed unexpectedly; build the readback by hand: "
                    "nyxloom-harness extract <session.jsonl> --preset readback "
                    "--budget-tokens 30000 --tail-prose-tokens 10000 > readback-N.md"
                )
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

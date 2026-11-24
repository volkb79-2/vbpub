#!/usr/bin/env python3
"""Controller context hooks: pressure notes, readback building, recovery notes.

Usage: ctx-hooks.py <statusline|post-tool-use|pre-tool-use|pre-compact|session-start|stop> [options]

Reads the harness hook JSON from stdin and NEVER raises: a hook failure must
not disturb the session (every error path logs one line, prints nothing that
could block a turn, and exits 0). A hard 20 s cap guards the readback build, so
a slow extract can never hold a compaction.

Subcommands (the Claude Code hook events they serve). The mode words of the
retired untracked `.controller-state/hooks/ctx.py` are kept, so re-pointing
`.claude/settings.local.json` is a path change, plus one new PreToolUse entry:

  statusline     status line text: local time and the context fill.
  post-tool-use  the pressure ladder: a note every 50k estimated tokens from
                 100k on, delivered as PostToolUse `additionalContext`: a free
                 marker (fill, growth since the last marker, calls left to the
                 forced yield) plus the wording of the session's ROLE:
                   controller below 200k: persist at the next semantic boundary
                     and CONTINUE;
                   controller 200k (INFORM) and 250k (URGE): reach a voluntary
                     stop for compaction at the next semantic boundary: write
                     the handover (controller-handover skill) and launch
                     scripts/controller/compact-resume.sh DETACHED on its own
                     pane; 250k says it more urgently;
                   controller from 300k (FORCE): see pre-tool-use; every tool
                     call prints how many grace calls are left;
                   agent (an Agent-tool subagent or a spawned session) from its
                     soft threshold (CTX_SOFT_AGENT, 200k): at the next
                     semantic boundary finish or return PARTIAL with a handoff
                     report; below it, the marker only.
                 A controller past 200k whose HANDOFF.md is older than 15
                 minutes also gets a stale-state warning.
  pre-tool-use   the forced yield (controller kind only; never in a subagent):
                 from FORCE_AT (300k) the first FORCE_GRACE (5) tool calls pass;
                 after that every call is DENIED (PreToolUse permissionDecision
                 deny) with the instruction to write the handover, launch
                 compact-resume.sh detached on the session's own pane and end
                 the turn. ALLOW-LIST (always passes, not counted): Write/Edit/
                 MultiEdit of a file under the state directory or of `plan.yaml`
                 at the repo root, and a Bash call that is ONLY the launch of
                 scripts/controller/compact-resume.sh or clear-resume.sh (optional
                 `setsid`/`nohup`/`bash` prefix, redirects and one trailing `&`;
                 any `;` `|` `&&` backtick `$(` or newline refuses the match).
                 Everything else is denied until the fill is below FORCE_AT again.
                 Unreadable fill or state: fail open (nothing is denied).
  pre-compact    builds the readback into `<state dir>/readback-<session_id>-N.md`
                 (state in `<state dir>/hooks/readback-state-<session_id>.json`:
                 every session has its own files and `--since` marker), prunes
                 (the newest 3 readbacks per session stay; pressure, force and
                 readback-state files untouched for 14 days go) and prints a note
                 for the SUMMARISER: keep the session's goal and the in-flight
                 list, the age of HANDOFF.md, and the merges to origin/main since
                 it was written. A subagent, or a session with CTX_HOOK_ROLE=agent,
                 builds nothing unless a session kind is named explicitly.
  session-start  after a compaction (`source` compact), a resume or a fresh
                 startup: the role rule with the ONE first command
                 (`scripts/controller/resume-read.sh <role>`), the main
                 controller's handover list TAKEN FROM the newest `RESUME HERE`
                 block of HANDOFF.md (its FIRST ACTION and READ FIRST lines),
                 THIS session's readback path and size when its own last build
                 succeeded, otherwise the failure and the exact manual command.
  stop           a user-visible scrollback marker (time and context fill).

Thresholds (operator 2026-11-24; the 500k/650k ceilings are gone): controller
INFORM_AT 200000, URGE_AT 250000, FORCE_AT 300000 with FORCE_GRACE 5 tool calls
(env CTX_INFORM_AT, CTX_URGE_AT, CTX_FORCE_AT, CTX_FORCE_GRACE); agent soft
threshold CTX_SOFT_AGENT, default 200000.

Configuration (flags or environment; nothing is hard-coded):

  --state-dir / CTX_STATE_DIR      controller state directory. Default: the
                                   nearest `.controller-state` directory at or
                                   above the hook input's `cwd` (so a `cd` into
                                   a worktree keeps the controller's state), else
                                   the `.controller-state` of the repo holding
                                   this script, else none (nothing is written).
                                   Never a fixed checkout path.
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
                                   the controller. CTX_HOOK_ROLE=agent|controller
                                   is the same override under the live hook's
                                   name (the flag and CTX_SESSION_KIND win). A
                                   pre-compact fired inside a subagent (and no
                                   explicit kind) builds nothing.
  --repo-root / CTX_REPO_ROOT      the checkout `git log` runs in for the
                                   "merges since" list. Default: the state
                                   directory's parent.
  --handoff-file / CTX_HANDOFF_FILE
                                   the controller's handover file. Default:
                                   `<state dir>/HANDOFF.md`.
  --goal-prompt / CTX_READBACK_GOAL_PROMPT
                                   add the session's first prompt as the goal
                                   anchor. Implied by session kind `spawned`;
                                   a controller (anchor: plan.yaml) never gets it
                                   unless asked. --goal-file wins over it.
  --redact-pattern / CTX_READBACK_REDACT_PATTERNS
                                   regex forwarded to the extract as
                                   `--redact-pattern` (flag: repeatable; env: one
                                   per line). Matching paragraphs are replaced in
                                   the whole readback. Patterns are never logged,
                                   stored or printed (a pattern can be a secret);
                                   an invalid one makes the extract refuse, so
                                   the build fails closed.
  --timeout / CTX_READBACK_TIMEOUT 15 seconds (capped at 20).
  --log / CTX_HOOK_LOG             default `<state dir>/hooks/hook-log.jsonl`
                                   (the per-session pressure files live there too).
  CTX_MAIN_CONTROLLER              the main controller's session name in the
                                   session-start role rule (vbpub-main-controller).

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
INFORM_AT = 200_000    # controller: reach a voluntary stop for compaction (operator 2026-11-24)
URGE_AT = 250_000      # the same reminder, stronger urge
FORCE_AT = 300_000     # forced yield: after FORCE_GRACE more tool calls every call is denied
FORCE_GRACE = 5        # tool calls that still pass once FORCE_AT is reached
AGENT_SOFT_DEFAULT = 200_000
KEEP_READBACKS = 3     # newest readbacks kept per session
PRUNE_DAYS = 14        # pressure/force/readback-state files untouched this long are removed
STALE_FROM = 200_000   # a controller past this fill is warned about a stale HANDOFF.md
STALE_MIN = 15         # minutes after which HANDOFF.md counts as stale
HARD_TIMEOUT = 20.0    # a compaction must never wait longer than this
READ_ORDER_LIMIT = 9_000  # hook stdout/additionalContext cap is 10,000 chars
HANDOFF_TAIL_BYTES = 400_000  # the newest handover block is read from the file's tail
ACTIVE_LOG: Path | None = None  # the resolved log, so main() can log an unexpected crash

# Printed after the handover-derived read order. Nothing here names a plan item
# or a file of the controller: the READ FIRST list of the handover does that.
REALITY_CHECK = (
    "Then check reality before acting (git log and worktrees of your scope, running jobs without a .done marker, "
    "your runs' .done files, host campaigns, ListAgents), re-arm ONE Monitor, continue, and write your state file "
    "at the next boundary."
)

MANUAL = (
    "BUILD IT BY HAND (the automatic build did not produce a readback):\n"
    "  {extract} extract {transcript} --preset readback --budget-tokens {budget} "
    "--tail-prose-tokens {tail}{since}{goal} > {state}/readback-{sid}-N.md"
)

RESUME_RE = re.compile(r"^## RESUME HERE #\d+", re.MULTILINE)
# A line that starts a new labelled section of a handover block ("AFTER A RESTART ...:").
SECTION_RE = re.compile(r"^(?:[A-Z][A-Z0-9 ()/,'-]{2,}:|\(#\d+|## )")
MARKER_RE = re.compile(r"^\[readback marker: (?P<marker>[^\]]*)\]$", re.MULTILINE)

MAIN_CONTROLLER_DEFAULT = "vbpub-main-controller"

# The role rule printed in front of the read order: every top-level session of the
# repo compacts on its own, and only the main controller reads the handover list
# that follows. The ONE first command is resume-read.sh <role>; the files it prints
# are named too (they are what the older hand-edited live text listed), in case the
# script is missing in the checkout.
ROLE_RULE = (
    "Find your role from your session name (/rename, --name) or from your own last messages. Your FIRST COMMAND is ONE "
    "tool call, which prints exactly your slices (each under a header with its size and cap):\n"
    " MAIN CONTROLLER ({main}): `{rr} main` = PROGRAM-*/PRIME-main-controller.md; the newest 'RESUME HERE #N' block "
    "of {state}/HANDOFF.md + its (#N) update lines; PROGRAM.md section 8 (last 40 lines); the last 3 sections of "
    "operator-decisions.md; git log -8 origin/main; the worktree count.\n"
    " SUB-CONTROLLER vbpub-<topic> (platform, core, assay, hostobs, edge, rollback): `{rr} <topic>` = (1) "
    "{state}/<topic>/STATE.md (your living state; for platform and assay also {state}/<topic>/HANDOFF.md newest "
    "block + update lines); (2) {state}/handovers/vbpub-<topic>-successor.md if newer than STATE.md; (3) "
    "{state}/PROGRAM-*/PRIME-<topic>.md (rules); (4) PROGRAM.md section 1-3 headings and the last 40 lines of section 8. "
    "Your main controller is {main} (SendMessage). The handover list below is NOT yours.\n"
    " ANY OTHER SESSION (implementer/reviewer): your brief and your own run records; do not act as a controller and do "
    "not run it.\n"
    "If the command fails, read the files named above by hand."
)

# The sentence every pressure note for a role carries (AGENTS.md "Context pressure ladder").
WEIGH = (
    "Weigh the REMAINING EFFORT (how many units are left, and whether they fit), what already lives in tools and files "
    "(commits, state files, logs, gate output) versus only in this conversation's prose, and how well the work would "
    "resume from a prose readback alone."
)


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
    parser.add_argument("--goal-prompt", action="store_true")
    parser.add_argument("--redact-pattern", action="append", default=[])
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--log")
    parser.add_argument("--repo-root")
    parser.add_argument("--handoff-file")
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


def safe_id(value: object) -> str:
    """A session id usable in a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "x"))[:80] or "x"


def readback_re(session: str) -> "re.Pattern[str]":
    return re.compile(rf"^readback-{re.escape(session)}-(\d+)\.md$")


def readback_state_path(state: Path, session: str) -> Path:
    return state / "hooks" / f"readback-state-{session}.json"


def load_readback_state(state: Path, session: str) -> dict:
    """This session's readback state; a file written by the pre-22a hook (in the
    state directory root) is still read when the new location has none."""
    stored = read_state(readback_state_path(state, session))
    return stored or read_state(state / f"readback-state-{session}.json")


def resolve_state_dir(args: argparse.Namespace, hook: dict) -> Path | None:
    """The state directory: flag, then environment, then the nearest
    `.controller-state` at or above the hook's cwd (a `cd` into a worktree must
    not move the controller's state), then the `.controller-state` of the repo that
    holds this script, else None (nothing is written anywhere)."""
    explicit = args.state_dir or os.environ.get("CTX_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    cwd = hook.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        base = Path(cwd).expanduser()
        for parent in (base, *base.parents):
            if (parent / ".controller-state").is_dir():
                return parent / ".controller-state"
    try:
        own = Path(__file__).resolve().parents[3] / ".controller-state"
        return own if own.is_dir() else None
    except Exception:
        return None


def resolve_repo_root(args: argparse.Namespace, state: Path | None) -> Path | None:
    explicit = args.repo_root or os.environ.get("CTX_REPO_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    return None if state is None else state.parent


def resolve_handoff(args: argparse.Namespace, state: Path | None) -> Path | None:
    explicit = args.handoff_file or os.environ.get("CTX_HANDOFF_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return None if state is None else state / "HANDOFF.md"


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


def last_assistant(path: str) -> tuple[int | None, str | None]:
    """(fill, model) of the last assistant message that carries usage, read from
    the transcript's tail; (None, None) when it cannot be read. The fill is the
    estimated context size: input + cache read + cache creation tokens
    (measured in E4)."""
    if not path:
        return None, None
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
            message = rec.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue
            model = message.get("model")
            return (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            ), (model if isinstance(model, str) and model else None)
    except Exception:
        return None, None
    return None, None


def ctx_size(path: str) -> int | None:
    """Estimated context fill of a transcript, or None."""
    return last_assistant(path)[0]


def soft_threshold() -> int:
    """The agent's soft threshold: CTX_SOFT_AGENT, default 200000."""
    value = env_int("CTX_SOFT_AGENT", AGENT_SOFT_DEFAULT)
    return value if value > 0 else AGENT_SOFT_DEFAULT


def controller_limits() -> tuple[int, int, int, int]:
    """(inform, urge, force, grace) of the controller policy, each from its
    CTX_* variable; a non-positive or non-numeric value keeps the default."""
    def positive(name: str, default: int) -> int:
        value = env_int(name, default)
        return value if value > 0 else default
    return (positive("CTX_INFORM_AT", INFORM_AT), positive("CTX_URGE_AT", URGE_AT),
            positive("CTX_FORCE_AT", FORCE_AT), max(0, env_int("CTX_FORCE_GRACE", FORCE_GRACE)))


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
    role = (os.environ.get("CTX_HOOK_ROLE") or "").strip().lower()
    if role in ("controller", "agent"):
        return "spawned" if role == "agent" else "controller"
    return "spawned" if hook.get("agent_id") else "controller"


def handoff_age(path: Path | None) -> tuple[str, int] | None:
    """(HH:MMZ of the last write of the handover file, minutes since) or None.
    Operator 2026-11-18: there is always a previous state on disk; the question
    is whether it is the current one."""
    if path is None:
        return None
    try:
        written = path.stat().st_mtime
        return time.strftime("%H:%MZ", time.gmtime(written)), int((time.time() - written) // 60)
    except Exception:
        return None


def merges_since(repo: Path | None, minutes: int) -> list[str]:
    """Merge subjects first-parented on origin/main in the last `minutes`
    minutes (mechanical, no model involved)."""
    if repo is None:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--first-parent", "origin/main",
             f"--since={minutes + 1} minutes ago", "--format=%h %s"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.splitlines()
        return [line[:140] for line in out]
    except Exception:
        return []


def growth_marker(fill: int, stored: dict, calls: int, target: int, label: str) -> str:
    """The free context marker: size, growth since the last marker and the
    extrapolated number of tool calls left to `target` (named `label`)."""
    k = fill // 1000
    try:
        prev = int(stored.get("at") or 0)
    except Exception:
        prev = 0
    if not prev:
        return f"[ctx {k}k]"
    per_call = (fill - prev) / calls if calls else 0
    text = f"[ctx {k}k | +{(fill - prev) // 1000}k over {calls} tool calls since the last marker"
    if per_call > 0 and fill < target:
        text += (f" (~{int(per_call)} tokens/call) | ~{int((target - fill) / per_call)} "
                 f"calls to the {target // 1000}k {label} at this rate")
    return text + "]"


def state_key(state: Path | None, prefix: str, session: str, who: str) -> Path | None:
    """`<state>/hooks/<prefix>-<session>-<who>.json`, or None without a state dir."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{session}-{who}")
    return None if state is None else state / "hooks" / f"{prefix}-{safe}.json"


def controller_wording(fill: int, marker: str, limits: tuple[int, int, int, int]) -> str:
    """The controller's note: persist-and-continue below INFORM_AT, then the
    voluntary-stop reminder (INFORM, stronger from URGE), then the forced yield."""
    inform, urge, force, grace = limits
    launch = ("launch scripts/controller/compact-resume.sh DETACHED on your own pane (`setsid nohup ... >log 2>&1 &`, "
              "never Bash run_in_background) with a guidance file and a resume prompt, then end the turn")
    if fill < inform:
        return (
            f"{marker} [controller] Context pressure is rising: prefer to finish the current unit of work and record "
            f"its outcome in the state files before starting a new one; from {inform // 1000}k on, plan a voluntary "
            f"stop for compaction at a good compaction point. {WEIGH}"
        )
    if fill < force:
        urgent = fill >= urge
        return (
            f"{marker} [controller] Past {(urge if urgent else inform) // 1000}k"
            + (f" (second reminder, more urgent): at the next semantic boundary STOP voluntarily and compact. "
               f"At {force // 1000}k a yield is FORCED. " if urgent else
               ": reach a voluntary stop for compaction at the next semantic boundary, a good compaction point "
               "(a unit of work finished, its outcome durably recorded, nothing half-edited). ")
            + f"There write the handover (controller-handover skill) and sync the plan, then {launch}. {WEIGH}"
        )
    return (
        f"{marker} [controller] FORCED YIELD: past {force // 1000}k. {grace} more tool calls pass, then every tool "
        "call is DENIED except the handover writes (Write/Edit under the state directory, plan.yaml) and the "
        f"compact-resume launch. Finish your unit of work now, write the handover, then {launch}. {WEIGH}"
    )


def force_count(state: Path | None, session: str, who: str) -> int:
    """Tool calls already counted against the grace in this fill band."""
    key = state_key(state, "force", session, who)
    try:
        return int(read_state(key).get("used", 0)) if key is not None else 0
    except Exception:
        return 0


def pressure_note(
    fill: int | None, state: Path | None, who: str, kind: str = "controller", *,
    soft: int | None = None, handoff: Path | None = None, session: str = "x",
    limits: tuple[int, int, int, int] | None = None,
) -> str | None:
    """The pressure ladder: a note every STEP from ARM_AT on; the marker is the
    same for every role, the wording follows the role (`kind`): the controller's
    by `limits` (inform, urge, force, grace; default `controller_limits()`), the
    agent's by its soft threshold (`soft`, default CTX_SOFT_AGENT)."""
    if fill is None:
        return None
    step = fill // STEP
    limits = limits or controller_limits()
    key = state_key(state, "pressure", session, who)
    stored = read_state(key) if key is not None else {}
    try:
        recorded = int(stored.get("step", 0))
        calls = int(stored.get("calls", 0)) + 1
    except Exception:
        recorded, calls = 0, 1
    if step < recorded:
        # The context shrank (a compaction happened): re-arm the ladder.
        if key is not None:
            write_state(key, {"step": step, "at": fill, "calls": 0})
        return None
    if step <= recorded or step < ARM_AT // STEP:
        if key is not None:  # tool calls since the last marker are counted for free
            write_state(key, {**stored, "calls": calls})
        return None
    if key is not None:
        write_state(key, {"step": step, "at": fill, "calls": 0})
    controller = kind != "spawned"
    soft = soft if soft else soft_threshold()
    marker = growth_marker(fill, stored, calls, limits[2] if controller else soft,
                           "forced yield" if controller else "wrap-up threshold")
    if not controller:
        if fill < soft:
            return marker
        return (
            f"{marker} [agent] Past {soft // 1000}k: at the next semantic boundary (a unit of work "
            "is finished, its outcome is durably recorded, nothing half-edited) finish or return "
            "PARTIAL with a handoff report: what is done and verified, what is half-known, the exact "
            "next step, and which facts exist only in this conversation. If your dispatch brief sets "
            f"a context rule, that rule wins. {WEIGH} The controller decides who continues."
        )
    note = controller_wording(fill, marker, limits)
    age = handoff_age(handoff) if fill >= STALE_FROM else None
    if age and age[1] >= STALE_MIN:
        note += (
            f" STALE STATE: HANDOFF.md was last written at {age[0]} ({age[1]} min ago). Compaction "
            "can land at any tool call and no model turn is given before it: append the update now "
            "(operator turns, merges, runs in flight with names, open branches with heads, next "
            "steps), then continue."
        )
    return note


def readback_number(state: Path, recorded: int, session: str) -> int:
    """The next readback number of this session: one past the highest of its files
    on disk, so a state file lost to a crash cannot re-use a number."""
    highest = recorded
    pattern = readback_re(session)
    try:
        for entry in state.iterdir():
            match = pattern.match(entry.name)
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
    flags: list[str],
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
    return command + flags


def extra_flags(
    goal_file: str | None, goal_prompt: bool, patterns: list[str],
) -> tuple[list[str], list[str]]:
    """The optional extract flags: (real, shown). `shown` is what is logged,
    stored and printed -- a redaction pattern can itself be a secret (a literal
    token), so only a placeholder ever leaves this process."""
    real: list[str] = []
    shown: list[str] = []
    if goal_file:
        real += ["--goal-file", goal_file]
        shown += ["--goal-file", goal_file]
    elif goal_prompt:
        real.append("--goal-prompt")
        shown.append("--goal-prompt")
    for pattern in patterns:
        real += ["--redact-pattern", pattern]
        shown += ["--redact-pattern", "<REGEX>"]
    return real, shown


def truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def build_readback(
    extract: list[str],
    transcript: str,
    state: Path,
    budget: int,
    tail: int,
    timeout: float,
    log_path: Path | None,
    flags: tuple[list[str], list[str]],
    session: str = "x",
) -> dict:
    """Build `<state>/readback-<session>-N.md` and record its marker in this
    session's own state file. Never raises. `flags` is `extra_flags(...)`: the
    real optional flags and their shown form."""
    stored = load_readback_state(state, session)
    marker = stored.get("marker") or None
    if isinstance(marker, str) and marker in ("", "none"):
        marker = None
    number = readback_number(state, int(stored.get("n") or 0), session)
    target = state / f"readback-{session}-{number}.md"
    command = readback_command(extract, transcript, budget, tail, marker, flags[0])
    shown = readback_command(extract, transcript, budget, tail, marker, flags[1])
    result = {
        "ok": False, "n": number, "path": str(target), "marker": marker,
        "command": shown, "reason": "",
    }
    if not os.path.isfile(transcript):
        result["reason"] = f"no readable transcript at {transcript}"
        return _failed(state, log_path, result, None, session)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        result["reason"] = f"timeout after {timeout:g}s"
        return _failed(state, log_path, result, None, session)
    except Exception as exc:
        result["reason"] = f"could not run: {exc!r}"[:200]
        return _failed(state, log_path, result, None, session)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        result["reason"] = (
            f"extract exited {completed.returncode}: "
            f"{detail[-1][:300] if detail else 'no output'}"
        )
        return _failed(state, log_path, result, completed.returncode, session)
    text = completed.stdout or ""
    if not text.strip():
        result["reason"] = "extract printed nothing"
        return _failed(state, log_path, result, 0, session)
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
        return _failed(state, log_path, result, 0, session)
    result.update(ok=True, marker=new_marker, bytes=len(text),
                  estimated_tokens=max(1, len(text) // 4))
    write_state(readback_state_path(state, session), {
        "n": number, "path": str(target), "marker": new_marker,
        "bytes": len(text), "ok": True, "command": shown,
    })
    log(log_path, {"ev": "readback", **result, "rc": 0})
    return result


def _failed(state: Path, log_path: Path | None, result: dict, rc: int | None, session: str = "x") -> dict:
    """Record a failed build: the next session must be told to build it by
    hand, so the failure is durable state, not only a log line."""
    log(log_path, {"ev": "readback", **result, "rc": rc})
    write_state(readback_state_path(state, session), {
        "n": int(load_readback_state(state, session).get("n") or 0),
        "path": result["path"], "marker": result.get("marker"), "ok": False,
        "reason": result.get("reason", ""), "command": result.get("command", []),
    })
    return result


def summariser_note(result: dict, state: Path, handoff_note: str = "") -> str:
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
        "needs. Do not summarise them away in favour of narrative. " + anchor + handoff_note
    )


def handoff_note(handoff: Path | None, repo: Path | None) -> str:
    """For the summariser: how old the handover file is and what merged since,
    because everything after its last write exists only in the conversation."""
    age = handoff_age(handoff)
    if not age:
        return ""
    merged = merges_since(repo, age[1])
    return (
        f" The controller's HANDOFF.md was last written at {age[0]} ({age[1]} min before this "
        "compaction); everything after that exists only in this conversation, so the summary must "
        "carry it explicitly: operator turns, decisions, runs started or finished, open branches "
        "with heads, and the exact next steps."
        + (f" Merges to origin/main since then: {' | '.join(merged)}." if merged else "")
    )


def read_order(handoff: Path | None, state: Path, repo: Path | None = None) -> str:
    """The role rule with the one first command (resume-read.sh <role>), then
    the read order taken from the newest `## RESUME HERE #N` block of the
    handover file (its FIRST ACTION and READ FIRST lines); nothing about the
    controller's plan items is hard-coded here. A file without a READ FIRST
    list gets a pointer, said to be a fallback."""
    block = ""
    try:
        if handoff is not None:
            size = handoff.stat().st_size
            with handoff.open("rb") as handle:
                handle.seek(max(0, size - HANDOFF_TAIL_BYTES))
                text = handle.read().decode("utf-8", "replace")
            heads = list(RESUME_RE.finditer(text))
            block = text[heads[-1].start():] if heads else ""
    except Exception:
        block = ""
    lines = block.splitlines()
    header = lines[0][:200] if lines else ""
    first: list[str] = []
    for line in lines[1:]:
        if first and (not line.strip() or SECTION_RE.match(line)):
            break
        if first or line.startswith("READ FIRST"):
            first.append(line)
    main = os.environ.get("CTX_MAIN_CONTROLLER") or MAIN_CONTROLLER_DEFAULT
    resume_read = (repo or state.parent) / "scripts" / "controller" / "resume-read.sh"
    out = [
        "CONTROLLER RESUME (printed by the SessionStart hook).",
        ROLE_RULE.format(main=main, state=state, rr=resume_read),
        f"HANDOVER LIST OF THE MAIN CONTROLLER ({main}) ONLY:",
    ]
    action = next((line for line in lines[1:] if line.startswith("FIRST ACTION")), "")
    if action:
        out.append(action[:600])
    if first:
        out.append(f"Newest handover: {handoff} -- {header}")
        out.append("It supersedes older blocks; later '(#N HH:MMZ)' lines extend it. Read in this order and nothing else first:")
        out.append("\n".join(first)[:3000])
    else:
        out.append(
            f"FALLBACK (no READ FIRST list found in {handoff}): read the newest 'RESUME HERE #N' block "
            f"of {state / 'HANDOFF.md'} (the last one) first, then plan.yaml and "
            f"{state / 'operator-decisions.md'}."
        )
    return "\n".join(out + [REALITY_CHECK])


def session_start_text(
    state: Path, log_path: Path | None, extract: list[str], transcript: str,
    budget: int, tail: int, flags: tuple[list[str], list[str]], session_name: str,
    handoff: Path | None = None, session: str = "x", repo: Path | None = None,
) -> str:
    """The read order, THIS session's readback (only when its own last build
    succeeded) and -- otherwise -- the failure and the manual command. Capped so
    the harness's 10,000-character hook cap never truncates the read order."""
    stored = load_readback_state(state, session)
    lines = [read_order(handoff, state, repo)]
    newest = None
    if stored.get("ok") is True:
        candidate = Path(str(stored.get("path") or ""))
        newest = candidate if candidate.is_file() else None
    if newest is not None:
        size = newest.stat().st_size
        lines.append(
            f"NEWEST READBACK: {newest} ({size} bytes, about {max(1, size // 4)} estimated "
            "tokens; marker "
            f"{stored.get('marker') or 'unknown'}). Read it IN FULL after the list above."
        )
    else:
        since = ""
        if stored.get("marker"):
            since = f" --since {stored['marker']}"
        manual = MANUAL.format(
            extract=" ".join(extract), transcript=transcript or "<session.jsonl>", sid=session,
            budget=budget, tail=tail, since=since, state=state,
            goal=(" " + " ".join(flags[1])) if flags[1] else "",
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


def prune(state: Path, session: str) -> None:
    """Bound the files the hook accumulates: the newest KEEP_READBACKS readbacks of
    this session, and pressure/force/readback-state files untouched for PRUNE_DAYS.
    Never raises."""
    try:
        pattern = readback_re(session)
        found = sorted(
            (int(m.group(1)), entry) for entry in state.iterdir() if (m := pattern.match(entry.name))
        )
        for _number, entry in found[:-KEEP_READBACKS] if KEEP_READBACKS > 0 else found:
            entry.unlink(missing_ok=True)
        cutoff = time.time() - PRUNE_DAYS * 86400
        for prefix in ("pressure", "force", "readback-state"):
            for entry in (state / "hooks").glob(f"{prefix}-*.json"):
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
    except Exception:
        pass


# Bash that is ONLY the launch of the successor helpers: optional setsid/nohup/bash words, the script,
# its arguments, redirects and one trailing `&`. Anything that chains or substitutes is refused.
RESUME_LAUNCH_RE = re.compile(
    r"^(?:(?:setsid|nohup|bash)\s+)*(?:\S*/)?scripts/controller/(?:compact|clear)-resume\.sh(?:\s|$)"
)
CHAIN_RE = re.compile(r"[;|`\n\r]|&&|\$\(|<\(|>\(")


def is_handover_call(hook: dict, state: Path, repo: Path | None) -> bool:
    """True for the calls the forced-yield deny must let through (documented in
    the module docstring): a Write/Edit under the state directory or of plan.yaml,
    or the detached launch of compact-resume.sh / clear-resume.sh."""
    tool = hook.get("tool_name")
    data = hook.get("tool_input")
    if not isinstance(data, dict):
        return False
    if tool == "Bash":
        command = data.get("command")
        return (isinstance(command, str) and not CHAIN_RE.search(command)
                and bool(RESUME_LAUNCH_RE.match(command.strip())))
    if tool in ("Write", "Edit", "MultiEdit"):
        raw = data.get("file_path")
        if not isinstance(raw, str) or not raw:
            return False
        base = hook.get("cwd") if isinstance(hook.get("cwd"), str) else os.getcwd()
        target = Path(os.path.realpath(os.path.join(base, raw)))
        roots = [Path(os.path.realpath(state))]
        if repo is not None:
            if target == Path(os.path.realpath(repo / "plan.yaml")):
                return True
        return any(target == root or root in target.parents for root in roots)
    return False


def force_decision(
    hook: dict, state: Path | None, repo: Path | None, session: str, who: str, fill: int | None,
    limits: tuple[int, int, int, int],
) -> str | None:
    """pre-tool-use of a controller: the deny message, or None to let the call pass.
    Below FORCE_AT the counter is cleared; at or above it the first `grace`
    calls that are not handover calls pass and count, the rest are denied."""
    _inform, _urge, force, grace = limits
    key = state_key(state, "force", session, who)
    if key is None or fill is None:
        return None
    if fill < force:
        try:
            key.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    if state is None or is_handover_call(hook, state, repo):
        return None
    used = force_count(state, session, who) + 1
    write_state(key, {"used": used, "at": fill})
    if used <= grace:
        return None
    return (
        f"FORCED YIELD: the context is {fill // 1000}k (limit {force // 1000}k) and the {grace}-call grace is used, so "
        "this tool call is denied. Do this now, nothing else: (1) write the handover (controller-handover skill: "
        f"HANDOFF.md block and plan.yaml; Write/Edit under {state} and plan.yaml stay allowed); (2) launch "
        "scripts/controller/compact-resume.sh <your tmux pane id> <guidance-file> <resume-prompt-file> <log-file> "
        "DETACHED (`setsid nohup ... >log 2>&1 &`, never run_in_background; only that launch is allowed in Bash); "
        "(3) end the turn. All other tool calls are denied until the context is below the limit again."
    )


def must_keep_note(
    hook: dict, state: Path | None, mode: str, log_path: Path | None, extra: dict | None = None,
) -> None:
    """Log one event's shape (never its content: no secrets, no env values)."""
    log(log_path, {
        "ev": mode, "keys": sorted(k for k in hook if k), "state": str(state or ""), **(extra or {}),
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
    fill = last_assistant(transcript)[0]
    repo = resolve_repo_root(args, state)
    handoff = resolve_handoff(args, state)
    budget = args.budget_tokens or env_int("CTX_READBACK_BUDGET_TOKENS", 30_000)
    tail = args.tail_prose_tokens
    if tail is None:
        tail = env_int("CTX_READBACK_TAIL_PROSE_TOKENS", 10_000)
    goal_file = args.goal_file or os.environ.get("CTX_READBACK_GOAL_FILE") or None
    kind = session_kind(args, hook)
    # The goal anchor (the session's first prompt) is for SPAWNED/targeted
    # sessions or an explicit flag; a controller's anchor is its plan.yaml.
    goal_prompt = (
        args.goal_prompt or truthy(os.environ.get("CTX_READBACK_GOAL_PROMPT"))
        or kind == "spawned"
    )
    patterns = [p for p in (
        *args.redact_pattern,
        *(os.environ.get("CTX_READBACK_REDACT_PATTERNS") or "").splitlines(),
    ) if p.strip()]
    flags = extra_flags(goal_file, goal_prompt, patterns)
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
        kind = session_kind(args, hook)
        session = str(hook.get("session_id") or "x")
        limits = controller_limits()
        soft = soft_threshold()
        must_keep_note(hook, state, "PostToolUse", log_path, {
            "ctx": fill, "kind": kind, "soft": soft, "inform": limits[0], "urge": limits[1], "force": limits[2],
        })
        note = pressure_note(fill, state, who, kind, soft=soft, handoff=handoff, session=session, limits=limits)
        if note is None and kind != "spawned" and fill is not None and fill >= limits[2] and state is not None:
            # Between the 50k notes: how many grace calls are left before the denies start.
            left = max(0, limits[3] - force_count(state, session, who))
            note = (f"[ctx {fill // 1000}k] FORCED YIELD: {left} grace tool calls left, then every call except the "
                    "handover writes and the compact-resume launch is denied. Write the handover and launch "
                    "compact-resume.sh now.")
        if note is None:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": note,
            },
        }))
        return 0

    if mode == "pre-tool-use":
        # The forced yield. Fail open: any trouble reading the fill or the state denies nothing.
        if session_kind(args, hook) == "spawned":
            return 0
        reason = force_decision(hook, state, repo, str(hook.get("session_id") or "x"),
                                str(hook.get("agent_id") or "main"), fill, controller_limits())
        if reason is None:
            return 0
        log(log_path, {"ev": "force-deny", "tool": hook.get("tool_name"), "ctx": fill})
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason,
        }}))
        return 0

    if mode == "session-start":
        source = str(hook.get("source") or "startup")
        if state is None:
            log(log_path, {"ev": "SessionStart", "source": source, "error": "no state dir"})
            return 0
        # Every source (compact, resume, startup) gets the same text: a resumed
        # session needs the artifact as much as an automatically compacted one.
        # The settings matcher (`compact`) decides which events fire at all.
        print(session_start_text(
            state, log_path, extract, transcript, budget, tail, flags, source, handoff,
            safe_id(hook.get("session_id")), repo,
        ))
        return 0

    if mode == "pre-compact":
        must_keep_note(hook, state, "PreCompact", log_path)
        explicit = args.session_kind or os.environ.get("CTX_SESSION_KIND")
        role_env = (os.environ.get("CTX_HOOK_ROLE") or "").strip().lower()
        if (hook.get("agent_id") or role_env == "agent") and not explicit:
            # A compaction inside an Agent-tool subagent, or in a spawned
            # top-level session (CTX_HOOK_ROLE=agent), builds no readback; an
            # explicit CTX_SESSION_KIND / --session-kind still does.
            log(log_path, {"ev": "readback", "skipped": "subagent compaction",
                           "agent_id": str(hook.get("agent_id"))})
            return 0
        if state is None or not transcript:
            result = {"ok": False, "path": "", "reason": "no transcript or state directory",
                      "command": [], "n": 0, "estimated_tokens": 0}
            log(log_path, {"ev": "readback", **result, "trigger": hook.get("trigger")})
            print(summariser_note(result, state or Path("."), handoff_note(handoff, repo)))
            return 0
        session = safe_id(hook.get("session_id"))
        result = build_readback(extract, transcript, state, budget, tail, timeout, log_path, flags, session)
        prune(state, session)
        print(summariser_note(result, state, handoff_note(handoff, repo)))
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

# Context economy of the controller sessions (P-plat-22c)

Status: design note and measurement, 2026-11-24. Binding input: `.controller-state/operator-decisions.md`
"## 2026-11-24 ~10:50Z". Items A/B (resume-read.sh, 200/250/300 hook policy) are P-plat-22a; item C (hygiene
notifier) is P-plat-22b and takes its thresholds from section 5. This note ships no code; the scripts in the appendix
are throwaway measurement tools kept for repeatability.

## 1. Measurement (F): what a restart costs

### Method

- Sessions: the seven controller panes of PROGRAM-2026-11-24, found by `customTitle`/`agentName` in
  `~/.claude/projects/-workspaces-vbpub/*.jsonl` (newest file per name; `/clear` starts a new file, `/compact` writes a
  `system/compact_boundary` record with `compactMetadata` into the same file).
- Restart measured = the LAST `compact_boundary` that already has at least 10 requests after it, else the file start
  (a `/clear` restart). Three of the seven had no compaction in their current file at measurement time (10:57Z).
- A request = one assistant `message.id` (Claude Code writes one JSONL line per content block, all with the same
  usage; `jsonl-metrics.py curve` counts lines, so its call counts are 1.5-2x the request count here). Context size of
  a request = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
- Split of the first request: the measured `cache_read` is the cached prefix (system prompt + tool schemas; it is
  26,577 in all seven, i.e. one shared prefix). The rest (`cache_creation + input`) is apportioned by character share
  of the records written between the restart and the first response: the `isCompactSummary` message, `attachment`
  records of type `file`/`compact_file_reference` (re-attached files), `instructions` (CLAUDE.md + MEMORY.md),
  listings (`skill_listing`, `deferred_tools_delta`, `agent_listing_delta`, `mcp_instructions_delta`), hook output,
  prompts/preserved tail, other. Cross-check: the system+tools share by characters would be 29.6-31.7k against the
  measured 26.6k, so the char-share split is good to about +-15%.
- Orientation = requests 1-10. Read set = tool name plus file paths from the tool input (`extract_bash_paths` from
  `nyxloom/tools/jsonl-metrics.py`, imported unchanged, with a path-token fallback); size = tool-result chars/4 (the
  `jsonl-metrics.py orient` estimate). Growth after orientation = median/mean of per-request context deltas up to the
  next restart, plus tokens per hour.
- Commands (read-only; scripts in the appendix):
  `python3 -I measure.py <jsonl>...` and `python3 -I gaps.py <jsonl>...`, run in `~/.claude/projects/-workspaces-vbpub/`.

### Results (tokens; k = 1000)

| Session (file) | Restart (UTC) | req 1 total | cached sys+tools | summary | re-attached files | instructions | listings | other | req 10 | growth req 1-10 | after: median / mean per req, per hour |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main-controller (b0e61dd5) | /compact 10:53, 264.7k -> 13.8k, 28 s | 60.7k | 26.6k | 2.7k | 3.2k (5 files) | 8.0k | 12.8k | 7.5k | 79.7k | 2.1k | 0.86k / 1.21k, 171k/h (09:51 window, 142 req) |
| platform (adef3ef1) | /compact 09:23, 442.4k -> 20.6k, 42 s | 73.5k | 26.6k | 3.7k | 9.5k (5 files) | 9.9k | 13.3k | 10.5k | 86.4k | 1.4k | 0.93k / 1.15k, 114k/h (146 req) |
| core (8a5b6d1c) | /compact 09:46, 584.5k -> 15.9k, 30 s | 66.5k | 26.6k | 3.0k | 5.9k (5 files) | 9.8k | 13.2k | 8.0k | 82.3k | 1.8k | 0.96k / 1.46k, 143k/h (109 req) |
| hostobs (5932c288) | /compact 10:52, 338.6k -> 15.1k, 31 s | 62.7k | 26.6k | 3.3k | 4.8k (5 files) | 8.8k | 14.1k | 5.1k | 73.6k | 1.2k | 0.91k / 1.20k, 65k/h (06:40 window, 223 req) |
| assay (0b7364ec) | /clear 07:23 | 53.6k | 26.6k | - | - | 10.5k | 14.8k | 1.7k | 66.8k | 1.5k | 0.79k / 1.04k, 72k/h (244 req) |
| edge (f61c3dd5) | /clear 08:35 | 53.9k | 26.6k | - | - | 10.9k | 14.8k | 1.6k | 87.6k | 3.7k | 0.94k / 1.30k, 115k/h (204 req) |
| rollback (172665b9) | /clear 07:36 | 53.8k | 26.6k | - | - | 10.7k | 14.8k | 1.7k | 96.9k | 4.8k | 1.01k / 1.45k, 90k/h (204 req) |

"other" after a compaction is mostly a `deferred_tools_record` attachment (15.0k chars, about 5k tokens, the record of
tools discovered before the compaction), plus `invoked_skills` (platform, 5.1k chars) and `task_status`. The main
controller's earlier compaction (09:51, 466.8k -> 13.6k) measured 62.6k at request 1 and 92.3k at request 10.

What the startup listings and instructions consist of (rollback at 07:36, identical layout in all seven): MEMORY.md
18.1k chars (now 14.8k after the main controller's pruning), `~/.claude/CLAUDE.md` 4.8k, `vbpub/CLAUDE.md` 2.5k;
skill listing 21.8k chars for 42 skills (62% of the listings); 100 deferred tool names (3.4k chars), 72 of them
`mcp__claude_ai_*` connector tools; MCP server instructions for 4 servers (Claude Docs 1.9k, Higgsfield 0.6k,
platform docs 0.6k, easel 0.1k chars); agent-type listing 4.6k chars.

What each loaded in orientation (requests 1-10; tool-result size in tokens):

- main (10:53): one Bash over `.controller-state/HANDOFF.md`, `PRIME-main-controller.md`, `PROGRAM.md` and git log
  (3.1k). At 09:51: HANDOFF.md + PROGRAM.md + a compact-resume log (5.8k), ListAgents, 6 SendMessage, git checks.
- platform: three background-task output files + `platform/HANDOFF.md` + a suite log (1.5k), reports and run logs
  (0.1-0.7k each), `platform/STATE.md` (0.3k), `operator-decisions.md` (0.2k); orientation +12.8k.
- core: run-gate state and `.run-gate` listings (0.2-1.6k), `PROGRAM.md` + `c5/HANDOFF.md` (0.4k),
  `run-gate.toml`, `assay.toml`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`; +15.8k.
- hostobs (10:52): `handovers/vbpub-hostobs-successor.md` + `hostobs/STATE.md` + a gate log, ListAgents; +11.0k.
- assay: `handovers/vbpub-assay-successor.md` (1.8k), `assay/STATE.md` (1.9k), `~/controller/remote-gates` (1.0k),
  `assay/run-gate.toml`; +13.2k.
- edge: `edge/HANDOVER.md` (1.8k), `PLAN-edge.md` + `PRIME-edge.md` + `edge/STATE.md` (0.6k), then a full Read of a
  harness-persisted tool-result file `tool-results/b6ipkgywq.txt` (8.8k, request delta 15.5k); +33.7k.
- rollback: `handovers/vbpub-rollback-successor.md` (3.6k), `rollback/STATE.md` + runs (6.7k),
  `scripts/controller/remote-gate.sh` (4.5k), the successor file again + `PLAN-rollback.md` + a prompt +
  `DESIGN-CORE-SPLIT.md` (3.9k); +43.1k.

Idle gaps (gaps.py): no gap over 55 min in the seven current files. In the previous week's controller files every
gap over an hour was a full cache miss: after 282-7017 min idle the next request re-created 117k-335k tokens
(`2bd3b531`: 265k, 255k, 270k; `56311814`: 117k; `a887211a`: 141k, 335k), each at the 1-hour write price.

### Findings

1. The floor is fixed: a `/clear` restart starts at 53.6-53.9k, a `/compact` restart at 60.7-73.5k. Half of it is the
   shared cached prefix (26.6k, cheap at 0.1x and not under our control); the controllable part is instructions
   (8-11k, dominated by MEMORY.md) and listings (13-15k, dominated by skills and connector tools).
2. The compaction summary itself is small (2.7-3.7k). Claude Code adds up to five recently read files (3.2-9.5k)
   chosen by recency, not relevance: core and hostobs got their own STATE/successor files back (useful), main got
   `controller.local.env` and `compact-resume.sh` (noise). Hypothesis, untested: reading one's own state file last
   before `/compact` turns the re-attachment into useful content.
3. Orientation is the largest variable cost: +11k to +43k in ten requests. Whole-file reads of successor files,
   state files, scripts and a persisted tool output cost edge and rollback 34-43k; main at 10:53 oriented with one
   sliced call (3.1k result). `resume-read.sh <role>` (22a) with per-slice caps is the right lever; a 15k orientation
   budget would have saved 19-28k in edge and rollback.
4. After orientation every controller grows by about 0.8-1.0k per request (median; mean 1.0-1.5k), 65-171k per hour.
   From about 80k a busy controller reaches 200k in one to two hours, so the 200/250/300 policy will fire several
   times a day per controller and the restart size is paid each time.
5. Cost shape (Anthropic pricing page: cache read 0.1x, 1-hour cache write 2x base input): a request at context C
   costs about 0.1*C plus 2x its new tokens. Every 10k saved at restart saves 1k base-input-equivalents on every later
   request until the next restart, about 150-250 requests: 150-250k per 10k saved, per restart.
6. Disabling the unused connectors (operator item) removes about 72 deferred tool names and 2-3 MCP instruction
   blocks, roughly 1.5-2k tokens per session; trimming unused skills from the listing is worth up to 8k. Both enter
   every restart of all seven sessions.

## 2. Keep-alive watcher (D)

Operator: no AGENTS.md rule; a mechanical watcher. Why the rule failed and the watcher does not: a `ScheduleWakeup`
is armed by the model at the end of a turn, at an interval the model picks (285 s and 59-60 min were both seen) and it
is not cancelled when the session becomes active again, so wakeups fired on top of later work and each one was a full
model turn decided by the model. The watcher instead reads the real anchor of the cache TTL (the time of the last API
request, from the JSONL), fires only after measured idleness, is cancelled by construction (any request moves the
anchor), and has its interval and cap as constants in code.

Design (first cut, `scripts/controller/ctl-keepalive.sh`, not written yet):

- Where: ONE watcher for all panes, started detached (`setsid nohup`) by `ctl-doctor.sh` or the operator, singleton by
  `flock` on `~/controller/locks/keepalive.lock`, pidfile + log under `~/controller/`. One process is simpler to check
  (ctl-doctor reports "keepalive: running, N panes watched") than seven per-pane loops, and it survives every
  controller restart because no controller owns it.
- Registry: `tmux list-panes -a -F '#{pane_id} #{pane_title} #{pane_current_command}'`, filtered to panes running
  `claude`; pane -> JSONL by session name (customTitle/agentName) = newest file with that name. A name that maps to two
  live files is skipped and logged (seen today: two `vbpub-edge` files).
- Loop every 60 s per pane:
  1. Cheap gate: JSONL mtime younger than 50 min -> active, reset the pane's ping counter, skip.
  2. Anchor: timestamp of the newest `api-request`/assistant record (tail read, as ctx.py's `ctx_size` does). The
     mtime alone is not enough: Claude Code rewrites metadata records (custom-title, last-prompt, mode) without a
     request.
  3. Ping when 55 min <= now - anchor < 59 min (TTL is 1 h from the last request; 4 min of margin for the poll and
     the send). Past 60 min do nothing: the cache is gone and the next real turn pays the rebuild anyway.
  4. Preconditions (resume-lib.sh `pane_state`, stricter): not busy, input row PRESENT and empty (resume-lib treats
     "no input row" as empty; a permission dialog has none, and the watcher must never type into a dialog); no
     compact-resume/clear-resume running for the pane (shared per-pane lock `~/controller/locks/<pane>.lock`, taken
     non-blocking by all three scripts); context below the 300k force threshold (a session at the yield point should
     compact, not be kept warm).
  5. Count: at most KEEPALIVE_MAX=3 pings per idle stretch (covers about 3 h 45 min; 4 is one setting away). The
     counter resets only on activity that is not the ping's own reply: the ping produces exactly one request, so a
     second request after the ping, or a newer user prompt record, means the session is active again.
- Ping text: `[keepalive N/3, ctl-keepalive: cache refresh only. Reply "ok", no tools, nothing else.]` It is cheap
  because the request reads the whole context from cache (0.1*C), writes about 100 new tokens at 2x and produces a
  one-word answer; the context grows by about 100 tokens per ping. At C = 250k: a ping costs about 25k
  base-equivalents, a miss costs about 500k (re-creation at 2x), so one ping pays for itself if the session resumes
  within the hour with probability above 5%; three pings (75k) are cheaper than one miss. The measured misses of last
  week (117-335k re-created) would each have been prevented by one to three pings.
- Failure modes: watcher dead (no pings: only cost, no harm; ctl-doctor shows it); operator typing (input row not
  empty: skip this poll, try again within the window); dialog or unknown footer (no input row: skip); wrong pane ->
  JSONL mapping (ambiguous names skipped; the ping text names its source so a wrong target is visible); usage limit
  or API error on the ping (counted, stops at the cap); ping crossing a compaction or a resume script (per-pane lock);
  footer markers changing with a Claude Code release (same markers as resume-lib.sh, one place to adjust; a
  fixture test like test-compact-resume.sh); clock skew (all times UTC, the anchor comes from the JSONL, not the pane).
- Not covered: Agent-tool subagents (no pane; their parent's activity keeps the parent warm), and pi/Codex panes
  (section 3: pi warms itself, Codex/DeepSeek caching is provider-side and not TTL-steerable).

## 3. pi and Codex in the controller role (E)

Both run DeepSeek here: pi via the `opendesign_*` providers (`deepseek-v4.1-flash`, `-eco`: context window 328,000,
max output 128,000, no `promptCache` declared, `~/.pi/agent/models.json`), Codex via `~/.codex-opendesign-*` profiles
(`deepseek-v4.1-flash-eco`, catalog `context_window` 328,000, `effective_context_window_percent` 95 = 311.6k) and the
openrouter profile. The 300k force point is 91% of a 328k window, so on these routes the policy sits just below the
harness's own auto-compaction; the controller should decide whether to keep absolute numbers or scale them to the
window (for example 60/75/85%).

pi 1.6.0 (sources: package docs `compaction.md`, `settings.md`, `extensions.md`, `models.md` under
`/opt/mdt/npm/lib/node_modules/@earendil-works/pi-coding-agent/docs/`; `pi --help`; AGENTS.md harness matrix, X5):

- Compaction: `/compact [instructions]`, rpc `compact` with `customInstructions`, extension `ctx.compact({
  customInstructions })`. Auto: `compaction.enabled` (true locally) at `contextTokens > contextWindow -
  reserveTokens` (default 16384, so 311.6k), `keepRecentTokens` 20000; per-model `compaction.modelOverrides`.
- Hooks: an extension, not shell hooks. `tool_result` can modify a result (the PostToolUse-additionalContext
  equivalent for the 200k and 250k notices, with `ctx.getContextUsage()`); `tool_call` can block (`{block, reason}`;
  the 300k yield with an allow-list like 22a's); `session_before_compact` can supply the handover file as the summary
  with zero model calls (measured X5); `session_compact` / `session_start` can inject the first instruction.
- Successor: in-process `ctx.newSession()` + `sendUserMessage("scripts/controller/resume-read.sh <role>")`, or a fresh
  `pi --name vbpub-<topic>` with that first prompt; `--session <id>` / `--continue` / `--fork` to reopen. No pane
  typing is needed, so compact-resume.sh (Claude Code footer markers) does not apply.
- Keep-alive: built in (`cacheWarming: "idle"`, set locally, warms up to 30 min between runs) but only for models that
  declare `promptCache`; the DeepSeek models declare none, so nothing is warmed and the D watcher is not needed there.

Codex 0.171.0 (sources: `codex --help`, `codex resume --help`, `codex queue --help`, `codex features list`, the
installed binary's config-key and hook-event strings, AGENTS.md harness matrix X3/X6):

- Compaction: TUI `/compact` and app-server `thread/compact/start` work (X6); `exec resume <id> "/compact"` is not a
  trigger (sent as a plain prompt). Steering only through `compact_prompt` (`-c` at launch or per thread on
  `thread/resume`); the user messages survive verbatim, assistant messages are dropped (X6).
- Auto: `model_auto_compact_token_limit` (+ `_scope`, and `model_post_turn_compact_threshold_percent` in the 0.171
  binary): X3 saw no automatic compaction under four settings; UNTESTED on 0.171.
- Hooks: feature `hooks` is stable/on, and the binary carries `hooks.json`, SessionStart, PreToolUse, PostToolUse,
  UserPromptSubmit, Stop, PreCompact, PostCompact and `hookSpecificOutput`/`additionalContext`, i.e. very likely a
  Claude-Code-shaped hook surface. UNTESTED (no `hooks.json` in any of the five CODEX_HOMEs). If it holds, ctx-hooks.py
  ports with a transcript-size function for the rollout's token-count events.
- Without hooks: notices by an external watcher over the rollout file, delivered with `codex queue --thread <id>
  --message <text>` (no pane typing; UNTESTED as a delivery path), and `model_auto_compact_token_limit` + a
  `compact_prompt` that ends with "first run scripts/controller/resume-read.sh <role>" as the backstop.
- Successor: `codex resume <id|name> "<prompt>"` or `codex exec resume <thread> "<prompt>"` with the
  resume-read.sh line as the prompt; `codex fork` for a copy.
- Keep-alive: provider-side automatic caching, no TTL control in Codex; nothing to do.

## 4. Open questions for the controller

- Absolute or window-relative 200/250/300 for the 328k DeepSeek routes (section 3).
- Ping cap 3 or 4 (section 2); whether the watcher should also skip a session whose operator marked it "parked".
- Test the re-attachment hypothesis (finding 2) on the next planned `/compact`.

## 5. Hygiene notifier thresholds (input for P-plat-22b, report-only)

Measured now: MEMORY.md 80 lines, 14,844 B (was 18,141 B at 07:36), 1 "IN PROGRESS" line (dated 2026-11-05);
`.controller-state/HANDOFF.md` 29.5 kB with 3 `## RESUME HERE` blocks; `platform/HANDOFF.md` 51.9 kB with 7;
STATE.md files 6.6-22.3 kB; `handovers/` 87 kB in 7 files.

- MEMORY.md: warn above 12 kB or 70 lines (every byte is paid by all seven sessions at each restart, finding 1); list
  each "IN PROGRESS" or "CURRENT" line whose date is older than 3 days and each line whose linked topic file is
  missing; Claude Code loads only the first 200 lines of the index, so warn hard at 150.
- HANDOFF.md / STATE.md: keep the newest 2 `## RESUME HERE` blocks (with their update lines); report when there are
  more than 2 or the file exceeds 24 kB; the archive target is `.controller-state/archive/<topic>-<date>/`, never
  deletion.
- Successor files in `handovers/`: report one older than its topic's STATE.md by more than 24 h (stale, it would be
  skipped by resume-read.sh anyway).
- Done backlog items: report a `KNOWN_ISSUES_TODO_BACKLOG.md` whose DONE/closed entries exceed 20 or 30% of the file.
- Output: one line per finding (`path: rule, value, threshold`), exit 0 always; run from ctl-doctor and at each
  controller restart. The notifier moves nothing.

## Appendix A: measure.py (scratchpad, verbatim)

```python
#!/usr/bin/env python3
"""measure.py <jsonl>... : first request after the LAST restart (compact_boundary, else file start = /clear),
its input split by char share, orientation reads in requests 1..10, growth. Prints only names, paths, sizes, tokens."""
import importlib.util, json, re, statistics, sys
PATH_RE = re.compile(r"(?:~|\.{0,2})?/?[\w.\-]+(?:/[\w.\-]+)+|[\w.\-]+\.(?:md|ya?ml|sh|py|json|jsonl|txt|toml|env)\b")
from datetime import datetime

spec = importlib.util.spec_from_file_location("jm", "/workspaces/vbpub/nyxloom/tools/jsonl-metrics.py")
jm = importlib.util.module_from_spec(spec); sys.modules["jm"] = jm; spec.loader.exec_module(jm)  # reuse extract_bash_paths, no change to it

LISTING = {"skill_listing", "deferred_tools_delta", "agent_listing_delta", "mcp_instructions_delta"}
FILES = {"file", "compact_file_reference", "plan_file_reference", "edited_text_file", "nested_memory"}
ORIENT = 10


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def measure(path):
    recs = []
    for l in open(path, errors="replace"):
        try:
            recs.append(json.loads(l))
        except Exception:
            recs.append({})
    # the LAST restart that already has >= ORIENT requests after it (a newer one still orienting is reported, not measured)
    bounds = [(0, None)] + [(i, e.get("compactMetadata") or {}) for i, e in enumerate(recs) if e.get("subtype") == "compact_boundary"]
    def nreq_after(b):
        return len({(r.get("message") or {}).get("id") for r in recs[b:] if r.get("type") == "assistant" and (r.get("message") or {}).get("usage")})
    pending = []
    while len(bounds) > 1 and nreq_after(bounds[-1][0]) < ORIENT:
        pending.append(bounds.pop())
    start, meta = bounds[-1]
    shapes = {e.get("shapeHash"): e for e in recs if e.get("type") == "api-request-shape"}
    # requests after the restart, deduped by message.id (one request = one id; content blocks share usage)
    reqs, seen = [], {}
    stop = pending[-1][0] if pending else len(recs)
    for i in range(start, stop):
        e = recs[i]
        if e.get("type") != "assistant":
            continue
        m = e.get("message") or {}
        u = m.get("usage")
        if not u:
            continue
        mid = m.get("id") or f"line{i}"
        if mid not in seen:
            seen[mid] = len(reqs)
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            reqs.append(dict(line=i, ts=e.get("timestamp"), ctx=ctx, inp=u.get("input_tokens") or 0,
                             cr=u.get("cache_read_input_tokens") or 0, cc=u.get("cache_creation_input_tokens") or 0,
                             out=u.get("output_tokens") or 0, tools=[]))
        r = reqs[seen[mid]]
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                r["tools"].append((b.get("id"), b.get("name"), b.get("input") or {}))
    if not reqs:
        return None
    first = reqs[0]
    # char shares of what the first request carried, from the records between the restart and the first response
    cat = dict(system_tools=0, summary=0, files=0, instructions=0, listings=0, hook=0, prompt_preserved=0, other=0)
    files = []
    other_types = {}
    snap, shape = None, None
    for i in range(start, first["line"]):
        e = recs[i]
        t = e.get("type")
        if t == "user" and not e.get("isMeta"):
            c = (e.get("message") or {}).get("content")
            n = len(c) if isinstance(c, str) else len(json.dumps(c))
            cat["summary" if e.get("isCompactSummary") else "prompt_preserved"] += n
        elif t == "attachment":
            a = e.get("attachment") or {}
            at = a.get("type")
            if at == "prompt_snapshot":
                snap = a
                continue
            if at in FILES:
                fc = ((a.get("content") or {}).get("file") or {}).get("content") or ""
                n = len(fc) if fc else len(json.dumps(a))
                cat["files"] += n
                files.append((a.get("filename") or a.get("displayPath"), at, n))
            elif at == "instructions":
                cat["instructions"] += sum(len(f.get("content") or "") for f in a.get("files") or [])
            elif at in LISTING:
                cat["listings"] += (len(a.get("content") or "") + len("\n".join(a.get("addedLines") or []))
                                    + len("".join(a.get("addedBlocks") or [])) + len(json.dumps(a.get("surfacedDefinitions") or [])))
            elif at and at.startswith("hook"):
                cat["hook"] += len(a.get("content") or a.get("stdout") or "")
            else:
                cat["other"] += len(json.dumps(a))
                other_types[at] = other_types.get(at, 0) + len(json.dumps(a))
        elif t == "api-request" and shape is None:
            shape = shapes.get(e.get("shapeHash"))
    if snap is None:  # the snapshot is written once and reused: take the newest one before the first response
        for i in range(first["line"], -1, -1):
            a = recs[i].get("attachment") or {}
            if a.get("type") == "prompt_snapshot":
                snap = a; break
    sp = sum(len(x) for x in (snap or {}).get("systemPrompt") or [])
    tl = len(json.dumps(((shape or {}).get("shape") or {}).get("tools") or []))
    cat["system_tools"] = sp + tl
    total_chars = sum(cat.values()) or 1
    rest_chars = (total_chars - cat["system_tools"]) or 1
    split = {"system_tools(cache_read)": first["cr"]}
    split.update({k: round((first["ctx"] - first["cr"]) * v / rest_chars) for k, v in cat.items() if k != "system_tools"})
    est_sys = round(first["ctx"] * cat["system_tools"] / total_chars)
    # orientation: tool calls in requests 1..ORIENT with their result sizes
    results = {}
    for e in recs[start:]:
        if e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = len(json.dumps(b.get("content")))
    reads = []
    for k, r in enumerate(reqs[:ORIENT]):
        delta = (reqs[k + 1]["ctx"] - r["ctx"]) if k + 1 < len(reqs) else None
        for tid, name, inp in r["tools"]:
            if name == "Read":
                ps = [inp.get("file_path")]
            elif name == "Bash":
                cmd = inp.get("command") or ""
                ps = [p for p, _ in jm.extract_bash_paths(cmd)] or sorted(set(PATH_RE.findall(cmd)))[:6] or ["(no path)"]
            elif name in ("Grep", "Glob"):
                ps = [inp.get("path") or inp.get("pattern")]
            else:
                ps = ["-"]
            reads.append((k + 1, name, ps, results.get(tid, 0) // 4, delta))
    n = min(ORIENT, len(reqs))
    o = reqs[n - 1]
    growth_orient = (o["ctx"] - first["ctx"]) / max(1, n - 1)
    later = [reqs[j + 1]["ctx"] - reqs[j]["ctx"] for j in range(n - 1, len(reqs) - 1)]
    later_pos = [d for d in later if d > -5000]  # drop shrink steps (none expected without a compaction)
    hours = (ts(reqs[-1]["ts"]) - ts(o["ts"])).total_seconds() / 3600 if len(reqs) > n else 0
    return dict(file=path.split("/")[-1][:8], restart="compact L%d %s pre=%s post=%s dur=%ss" % (
        start, meta.get("trigger"), meta.get("preTokens"), meta.get("postTokens"), (meta.get("durationMs") or 0) // 1000) if meta else "file start (/clear)",
        first=first, split=split, est_sys=est_sys, other_types=other_types, chars=cat, files=files, reads=reads, n=n, orient_ctx=o["ctx"], orient_ts=o["ts"],
        growth_orient=growth_orient, later_median=statistics.median(later_pos) if later_pos else None,
        later_mean=statistics.mean(later_pos) if later_pos else None, nreq=len(reqs), end_ctx=reqs[-1]["ctx"],
        pending=["L%d pre=%s post=%s" % (b, m.get("preTokens"), m.get("postTokens")) for b, m in pending],
        end_ts=reqs[-1]["ts"], per_hour=((reqs[-1]["ctx"] - o["ctx"]) / hours) if hours else None)


for p in sys.argv[1:]:
    d = measure(p)
    if not d:
        print(p, "no requests"); continue
    f = d["first"]
    print(f"=== {d['file']} restart: {d['restart']}" + (f"  (newer, still orienting: {d['pending']})" if d['pending'] else ""))
    print(f"  req1 {f['ts']} ctx={f['ctx']} (input={f['inp']} cache_read={f['cr']} cache_create={f['cc']}) out={f['out']}")
    print("  split by char share (tokens): " + " ".join(f"{k}={v}" for k, v in d["split"].items()))
    print("  chars: " + " ".join(f"{k}={v}" for k, v in d["chars"].items()) + f"  (system_tools by char share would be {d['est_sys']})")
    print("  other by type (chars): " + " ".join(f"{k}={v}" for k, v in sorted(d["other_types"].items(), key=lambda x: -x[1])))
    for fn, at, n in d["files"]:
        print(f"    re-attached {at}: {fn} ~{n // 4} tok")
    print(f"  req{d['n']} {d['orient_ts']} ctx={d['orient_ctx']}  growth req1..{d['n']}: {d['growth_orient']:.0f} tok/req")
    print(f"  after orientation: {d['nreq'] - d['n']} requests, median +{d['later_median']} mean +{d['later_mean'] and round(d['later_mean'])} tok/req, "
          f"end ctx={d['end_ctx']} at {d['end_ts']}, {d['per_hour'] and round(d['per_hour'])} tok/h")
    for k, name, ps, tok, delta in d["reads"]:
        print(f"    req{k:<2} {name:<10} ~{tok:>6} tok result, req delta {delta}  {' '.join(str(x) for x in ps)}")
```

## Appendix B: gaps.py (scratchpad, verbatim)

```python
# gaps.py <jsonl>...: request gaps > 55 min and whether the next request hit the cache (cache_read share); tokens only.
import json, sys
from datetime import datetime
for p in sys.argv[1:]:
    reqs, seen = [], set()
    for l in open(p, errors="replace"):
        try: e = json.loads(l)
        except Exception: continue
        m = e.get("message") or {}
        u = m.get("usage")
        if e.get("type") != "assistant" or not u or m.get("id") in seen: continue
        seen.add(m.get("id"))
        reqs.append((datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")), u.get("cache_read_input_tokens") or 0,
                     u.get("cache_creation_input_tokens") or 0, u.get("input_tokens") or 0))
    out = []
    for a, b in zip(reqs, reqs[1:]):
        gap = (b[0] - a[0]).total_seconds() / 60
        if gap > 55:
            out.append(f"{a[0]:%m-%d %H:%M} gap {gap:.0f}m next cr={b[1]} cc={b[2]}")
    print(p.split("/")[-1][:8], len(reqs), "requests;", "; ".join(out) or "no gap > 55 min")
```

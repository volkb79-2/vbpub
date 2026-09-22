#!/usr/bin/env python3
"""jsonl-metrics.py — context-growth curves, checkpoint-restart simulation,
and reviewer read-set extraction for Claude Code agent transcript JSONLs.

Built for nyxloom context-lifecycle experiment E-006. Designed to be reused
for later experiments in the same program: every subcommand takes one or
more transcript JSONL paths and prints ONLY small aggregate tables — it
never dumps raw transcript content, because these files are multi-MB and
must never be `cat`/`Read` directly into an agent's own context.

Transcript line schema (Claude Code subagent/session JSONL):
  - one JSON object per line; `type` in {user, assistant, attachment, ...}
  - assistant lines: message.usage = {input_tokens, cache_read_input_tokens,
    cache_creation_input_tokens, output_tokens, ...}; message.content is a
    list of blocks, tool_use blocks carry {type:"tool_use", name, input}
  - top-level `timestamp` is ISO-8601 UTC ("...Z") on every line

Subcommands:
  curve     per-agent context-growth summary + percentile growth table
  simulate  checkpoint-restart cost simulation at N%/of-calls checkpoints
  simulate-multi  multi-checkpoint simulation: uniform N-schedules (N=1..max)
            + optimal schedule via dynamic programming over placements
  readset   extract the deduplicated file read-set from Read/Bash tool calls
  overlap   compare a transcript's read-set against an orientation pack
            (read-list.txt + slice headers parsed from pack.md)
  boundaries        (E-008) detect COHERENT checkpoint boundaries from
            transcript CONTENT: gate runs (+green/red verdict), git commits,
            edit-cluster ends, LOG/REPORT writes
  simulate-boundary (E-008) the multi-checkpoint DP restricted to those
            boundaries, side by side with the unconstrained optimum and the
            uniform schedule, plus the "first gate-green/commit after X%" rule
  threshold         (E-008) where the FIRST checkpoint belongs, in context
            size and in calls: marginal-cost crossover, DP first placement,
            boundary-constrained first placement, with median/IQR across the
            given transcripts

Cost model (normalized, input-token-equivalent units):
  input_tokens × 1.0, cache_read_input_tokens × 0.1,
  cache_creation_input_tokens × 1.25, output_tokens × 5.0
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

W_INPUT = 1.0
W_CACHE_READ = 0.1
W_CACHE_CREATE = 1.25
W_OUTPUT = 5.0

DEFAULT_BRIEF_TOKENS = 25_000
DEFAULT_CHECKPOINTS_PCT = (25, 50, 75)
GROWTH_PCTS = (10, 25, 50, 75, 100)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

@dataclass
class Call:
    idx: int
    ts: datetime
    input_tokens: int
    cache_read: int
    cache_creation: int
    output_tokens: int

    @property
    def context(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_creation

    @property
    def cost(self) -> float:
        return (
            self.input_tokens * W_INPUT
            + self.cache_read * W_CACHE_READ
            + self.cache_creation * W_CACHE_CREATE
            + self.output_tokens * W_OUTPUT
        )


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ")


def load_calls(path: Path) -> list[Call]:
    calls: list[Call] = []
    idx = 0
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            ts_raw = obj.get("timestamp")
            try:
                ts = _parse_ts(ts_raw) if ts_raw else None
            except ValueError:
                ts = None
            if ts is None:
                continue
            calls.append(
                Call(
                    idx=idx,
                    ts=ts,
                    input_tokens=int(usage.get("input_tokens") or 0),
                    cache_read=int(usage.get("cache_read_input_tokens") or 0),
                    cache_creation=int(usage.get("cache_creation_input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                )
            )
            idx += 1
    return calls


def iter_tool_uses(path: Path):
    """Yield (call_idx, tool_name, tool_input) for every tool_use block,
    in the same idx space as load_calls (sequential assistant-message order)."""
    idx = 0
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield idx, block.get("name"), (block.get("input") or {})
            idx += 1


# --------------------------------------------------------------------------
# curve
# --------------------------------------------------------------------------

def agent_summary(calls: list[Call]) -> dict:
    n = len(calls)
    if n == 0:
        return {}
    wall_min = (calls[-1].ts - calls[0].ts).total_seconds() / 60.0
    tot_input = sum(c.input_tokens for c in calls)
    tot_read = sum(c.cache_read for c in calls)
    tot_create = sum(c.cache_creation for c in calls)
    tot_output = sum(c.output_tokens for c in calls)
    denom = tot_read + tot_create + tot_input
    hit_ratio = tot_read / denom if denom else 0.0
    norm_cost = sum(c.cost for c in calls)
    return dict(
        calls=n,
        wall_min=round(wall_min, 1),
        final_context=calls[-1].context,
        total_input=tot_input,
        total_cache_read=tot_read,
        total_cache_creation=tot_create,
        total_output=tot_output,
        cache_hit_ratio=round(hit_ratio, 4),
        normalized_cost=round(norm_cost, 1),
    )


def growth_table(calls: list[Call]) -> dict:
    n = len(calls)
    final_ctx = calls[-1].context if n else 0
    rows = []
    for pct in GROWTH_PCTS:
        i = max(0, min(n - 1, round(pct / 100 * n) - 1))
        ctx = calls[i].context
        frac = ctx / final_ctx if final_ctx else 0.0
        rows.append(dict(pct=pct, call_idx=i, context=ctx, frac_of_final=round(frac, 3)))
    # knee: call with the largest single-step context delta
    knee_idx, knee_delta = 0, -1
    for i in range(1, n):
        d = calls[i].context - calls[i - 1].context
        if d > knee_delta:
            knee_delta = d
            knee_idx = i
    knee_pct = round(100 * knee_idx / n, 1) if n else 0.0
    # shape classification (heuristic, numbers are exact — label is judgment)
    frac25 = rows[1]["frac_of_final"]
    frac75 = rows[3]["frac_of_final"]
    if frac25 >= 0.5:
        shape = "front-loaded"
    elif frac75 <= 0.6:
        shape = "tail-heavy"
    else:
        shape = "roughly linear"
    return dict(
        rows=rows,
        knee_call_idx=knee_idx,
        knee_pct_of_run=knee_pct,
        knee_delta_tokens=knee_delta,
        shape=shape,
    )


def cmd_curve(args):
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        calls = load_calls(path)
        summ = agent_summary(calls)
        growth = growth_table(calls) if calls else {}
        out[path.name] = dict(summary=summ, growth=growth)

    if args.json:
        print(json.dumps(out, indent=2))
        return

    for name, d in out.items():
        s = d["summary"]
        g = d["growth"]
        print(f"=== {name} ===")
        if not s:
            print("  (no assistant/usage lines found)")
            continue
        print(
            f"  calls={s['calls']}  wall_min={s['wall_min']}  "
            f"final_context={s['final_context']:,}  cache_hit_ratio={s['cache_hit_ratio']}"
        )
        print(
            f"  totals: input={s['total_input']:,}  cache_read={s['total_cache_read']:,}  "
            f"cache_creation={s['total_cache_creation']:,}  output={s['total_output']:,}"
        )
        print(f"  normalized_cost={s['normalized_cost']:,}")
        print(f"  growth shape: {g['shape']}  "
              f"(knee at call {g['knee_call_idx']} = {g['knee_pct_of_run']}% of run, "
              f"+{g['knee_delta_tokens']:,} tok jump)")
        print("  pct_of_calls  call_idx  context      frac_of_final")
        for r in g["rows"]:
            print(f"  {r['pct']:>10}%  {r['call_idx']:>8}  {r['context']:>10,}  {r['frac_of_final']:>13}")
        print()


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------

def simulate_checkpoint(calls: list[Call], n_checkpoint: int, brief_tokens: int) -> dict:
    """Cost of (actual run through call n_checkpoint-1) + (restart: one-time
    brief cache_creation of brief_tokens, then remaining calls re-priced with
    context rebuilt from brief_tokens, growing by the SAME per-call context
    deltas observed in the actual run)."""
    n = len(calls)
    if n_checkpoint <= 0 or n_checkpoint >= n:
        return {}

    actual_pre = sum(c.cost for c in calls[:n_checkpoint])
    actual_post = sum(c.cost for c in calls[n_checkpoint:])
    actual_total = actual_pre + actual_post

    brief_cost = brief_tokens * W_CACHE_CREATE
    rebuilt_ctx = brief_tokens
    restart_post = 0.0
    prev_ctx = calls[n_checkpoint - 1].context
    for c in calls[n_checkpoint:]:
        delta = c.context - prev_ctx
        prev_ctx = c.context
        cache_creation_sim = max(0, delta)
        cache_read_sim = rebuilt_ctx
        rebuilt_ctx = rebuilt_ctx + cache_creation_sim
        restart_post += (
            c.input_tokens * W_INPUT
            + cache_read_sim * W_CACHE_READ
            + cache_creation_sim * W_CACHE_CREATE
            + c.output_tokens * W_OUTPUT
        )
    restart_total = actual_pre + brief_cost + restart_post

    savings_pct = 100.0 * (actual_total - restart_total) / actual_total if actual_total else 0.0
    return dict(
        n_checkpoint=n_checkpoint,
        checkpoint_context=calls[n_checkpoint - 1].context,
        actual_total_cost=round(actual_total, 1),
        restart_total_cost=round(restart_total, 1),
        brief_cost=round(brief_cost, 1),
        savings_pct=round(savings_pct, 2),
    )


def find_breakeven_context(calls: list[Call], brief_tokens: int) -> dict | None:
    """Scan every call index as a candidate checkpoint; return the smallest
    checkpoint context size at which restart savings_pct first turns positive."""
    n = len(calls)
    for i in range(1, n):
        r = simulate_checkpoint(calls, i, brief_tokens)
        if r and r["savings_pct"] > 0:
            return dict(call_idx=i, context=r["checkpoint_context"], savings_pct=r["savings_pct"])
    return None


def cmd_simulate(args):
    checkpoints_pct = tuple(int(x) for x in args.checkpoints.split(","))
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        calls = load_calls(path)
        n = len(calls)
        if n < 4:
            out[path.name] = dict(note="too few calls to simulate")
            continue
        rows = []
        for pct in checkpoints_pct:
            i = max(1, min(n - 1, round(pct / 100 * n)))
            r = simulate_checkpoint(calls, i, args.brief_tokens)
            if r:
                r["pct_of_calls"] = pct
                rows.append(r)
        breakeven = find_breakeven_context(calls, args.brief_tokens)
        out[path.name] = dict(checkpoints=rows, breakeven=breakeven, total_calls=n,
                               final_context=calls[-1].context)

    if args.json:
        print(json.dumps(out, indent=2))
        return

    for name, d in out.items():
        print(f"=== {name} (brief={args.brief_tokens:,} tok) ===")
        if "note" in d:
            print(f"  {d['note']}")
            continue
        print("  pct  call_idx  ckpt_context   actual_cost   restart_cost   savings%")
        for r in d["checkpoints"]:
            print(
                f"  {r['pct_of_calls']:>3}%  {r['n_checkpoint']:>8}  "
                f"{r['checkpoint_context']:>12,}  {r['actual_total_cost']:>12,}  "
                f"{r['restart_total_cost']:>12,}  {r['savings_pct']:>7}"
            )
        be = d["breakeven"]
        if be:
            print(
                f"  break-even: checkpointing at call {be['call_idx']} "
                f"(context={be['context']:,}) is the first point restart beats continuing "
                f"(savings {be['savings_pct']}%)"
            )
        else:
            print("  break-even: restart never beats continuing within this transcript")
        print()


# --------------------------------------------------------------------------
# simulate-multi
# --------------------------------------------------------------------------

def _actual_prefix_costs(calls: list[Call]) -> list[float]:
    """A[j] = actual cost of calls 0..j-1 (A[0]=0, A[n]=full actual cost)."""
    A = [0.0]
    for c in calls:
        A.append(A[-1] + c.cost)
    return A


def _segment_cost_rows(calls: list[Call], brief_tokens: int) -> list[list[float]]:
    """S[j][m-j-1] = cost of calls j..m-1 replayed after a restart at call j
    (context reset to brief_tokens, then growing by the real per-call context
    deltas), for j in 1..n-1, m in j+1..n. Same replay model as
    simulate_checkpoint. Brief cache_creation cost is NOT included here."""
    n = len(calls)
    rows: list[list[float]] = [[] for _ in range(n)]
    for j in range(1, n):
        rebuilt = brief_tokens
        prev_real = calls[j - 1].context
        acc = 0.0
        row = rows[j]
        for k in range(j, n):
            c = calls[k]
            delta = c.context - prev_real
            prev_real = c.context
            creation = delta if delta > 0 else 0
            acc += (
                c.input_tokens * W_INPUT
                + rebuilt * W_CACHE_READ
                + creation * W_CACHE_CREATE
                + c.output_tokens * W_OUTPUT
            )
            rebuilt += creation
            row.append(acc)  # row[k-j] = S(j, k+1)
    return rows


def _schedule_cost(calls: list[Call], A: list[float], S: list[list[float]],
                   brief_cost: float, placements: list[int]) -> float:
    """Total cost of a run with checkpoints placed before each call index in
    `placements` (sorted, each in 1..n-1)."""
    n = len(calls)
    if not placements:
        return A[n]
    total = A[placements[0]]
    for t, c in enumerate(placements):
        nxt = placements[t + 1] if t + 1 < len(placements) else n
        total += brief_cost + S[c][nxt - c - 1]
    return total


def simulate_multi(calls: list[Call], brief_tokens: int, max_n: int) -> dict:
    """Uniform schedules for N=1..max_n, per-N optimal via DP, and the
    unrestricted-N optimal schedule."""
    n = len(calls)
    A = _actual_prefix_costs(calls)
    S = _segment_cost_rows(calls, brief_tokens)
    brief_cost = brief_tokens * W_CACHE_CREATE
    actual_total = A[n]
    INF = float("inf")

    def sav(cost: float) -> float:
        return round(100.0 * (actual_total - cost) / actual_total, 2) if actual_total else 0.0

    # --- uniform schedules ---
    uniform: dict[int, dict] = {}
    for N in range(1, max_n + 1):
        placements = sorted({max(1, min(n - 1, round(t * n / (N + 1)))) for t in range(1, N + 1)})
        cost = _schedule_cost(calls, A, S, brief_cost, placements)
        uniform[N] = dict(cost=round(cost, 1), savings_pct=sav(cost),
                          placements=placements,
                          distinct=len(placements))

    # --- per-N optimal via DP ---
    # D_k[j] = min cost of the tail starting with a restart at call j, using
    # exactly k checkpoints total in the tail (incl. the one at j):
    #   D_1[j] = brief + S(j, n)
    #   D_k[j] = brief + min_{m>j} ( S(j, m) + D_{k-1}[m] )
    # opt(k)  = min_j  A[j] + D_k[j]
    per_n_opt: dict[int, dict] = {}
    args_by_k: dict[int, list] = {}   # args_by_k[k][j] = next checkpoint after j (or None)
    D_prev = [INF] * n
    for k in range(1, max_n + 1):
        D_cur = [INF] * n
        arg_cur: list = [None] * n
        for j in range(1, n):
            row = S[j]
            if k == 1:
                D_cur[j] = brief_cost + row[n - j - 1]
            else:
                best, barg = INF, None
                for m in range(j + 1, n):
                    if D_prev[m] == INF:
                        continue
                    v = row[m - j - 1] + D_prev[m]
                    if v < best:
                        best, barg = v, m
                if best < INF:
                    D_cur[j] = brief_cost + best
                    arg_cur[j] = barg
        args_by_k[k] = arg_cur
        best_total, best_first = INF, None
        for j in range(1, n):
            if D_cur[j] == INF:
                continue
            v = A[j] + D_cur[j]
            if v < best_total:
                best_total, best_first = v, j
        placements: list[int] = []
        if best_first is not None:
            j, kk = best_first, k
            while j is not None:
                placements.append(j)
                j = args_by_k[kk][j]
                kk -= 1
        per_n_opt[k] = dict(cost=round(best_total, 1) if best_total < INF else None,
                            savings_pct=sav(best_total) if best_total < INF else None,
                            placements=placements)
        D_prev = D_cur

    # --- unrestricted optimal ---
    # G[j] = min cost of the tail with a restart at j and ANY number of
    # further checkpoints. Computed backwards.
    G = [INF] * (n + 1)
    argG: list[int | None] = [None] * (n + 1)
    for j in range(n - 1, 0, -1):
        row = S[j]
        best = row[n - j - 1]        # run to the end, no further checkpoint
        barg = None
        for m in range(j + 1, n):
            if G[m] == INF:
                continue
            v = row[m - j - 1] + G[m]
            if v < best:
                best, barg = v, m
        G[j] = brief_cost + best
        argG[j] = barg
    best_total, best_first = actual_total, None
    for j in range(1, n):
        v = A[j] + G[j]
        if v < best_total:
            best_total, best_first = v, j
    placements = []
    j = best_first
    while j is not None:
        placements.append(j)
        j = argG[j]
    unrestricted = dict(
        n_checkpoints=len(placements),
        cost=round(best_total, 1),
        savings_pct=sav(best_total),
        placements=placements,
        placements_pct=[round(100 * p / n, 1) for p in placements],
    )

    return dict(
        total_calls=n,
        actual_total_cost=round(actual_total, 1),
        brief_tokens=brief_tokens,
        uniform=uniform,
        per_n_optimal=per_n_opt,
        unrestricted_optimal=unrestricted,
    )


def cmd_simulate_multi(args):
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        calls = load_calls(path)
        if len(calls) < 4:
            out[path.name] = dict(note="too few calls to simulate")
            continue
        out[path.name] = simulate_multi(calls, args.brief_tokens, args.max_n)

    if args.json:
        # placements lists can be long in unrestricted mode; keep them
        print(json.dumps(out, indent=2))
        return

    for name, d in out.items():
        print(f"=== {name} (brief={args.brief_tokens:,} tok, max_n={args.max_n}) ===")
        if "note" in d:
            print(f"  {d['note']}")
            continue
        print(f"  calls={d['total_calls']}  actual_total_cost={d['actual_total_cost']:,}")
        print("    N   uniform_sav%   optimal_sav%   optimal placements (% of calls)")
        n = d["total_calls"]
        for N in sorted(d["uniform"]):
            u = d["uniform"][N]
            o = d["per_n_optimal"][N]
            opct = ",".join(f"{100 * p / n:.0f}" for p in sorted(o["placements"]))
            note = "" if u["distinct"] == N else f"  (uniform collapsed to {u['distinct']})"
            print(f"  {N:>3}   {u['savings_pct']:>11}   {o['savings_pct']:>11}   [{opct}]{note}")
        ur = d["unrestricted_optimal"]
        pct = ",".join(f"{p:.0f}" for p in ur["placements_pct"])
        print(f"  unrestricted optimum: N={ur['n_checkpoints']}  savings={ur['savings_pct']}%  "
              f"placements=[{pct}]% of calls")
        print()


# --------------------------------------------------------------------------
# readset
# --------------------------------------------------------------------------

REPO_ROOT_PREFIXES = ("/workspaces/dstdns/",)
REPO_TOP_DIRS = (
    "libs/", "applications/", "docs/", "tests/", "infra/", "nyxloom/",
    "nyxloom-trove/", "scripts/", "common/", "ciu.", "assay.toml",
)

BASH_FILE_TOOLS = {"cat", "head", "tail", "sed", "grep"}
SPLIT_RE = re.compile(r"&&|\|\||;|\|(?!\|)")

GATE_ARTIFACT_RE = re.compile(
    r"(LOG\.md$|REPORT\.md$|\.log$|test-runner|pytest|/archive/handoff/|"
    r"coverage|assay.*report|junit|\.xml$)",
    re.IGNORECASE,
)


def _looks_like_repo_path(tok: str) -> bool:
    if tok.startswith(REPO_ROOT_PREFIXES):
        return True
    if tok.startswith(REPO_TOP_DIRS):
        return True
    return False


def _normalize_path(tok: str) -> str:
    for prefix in REPO_ROOT_PREFIXES:
        if tok.startswith(prefix):
            return tok[len(prefix):]
    return tok


def extract_bash_paths(command: str) -> list[tuple[str, str]]:
    """Return list of (path, subtool) tuples found in a Bash command string."""
    found = []
    for sub in SPLIT_RE.split(command):
        sub = sub.strip()
        if not sub:
            continue
        try:
            tokens = shlex.split(sub)
        except ValueError:
            tokens = sub.split()
        if not tokens:
            continue
        prog = Path(tokens[0]).name
        if prog == "sed" and len(tokens) >= 2 and tokens[1] not in ("-n",):
            # e.g. `sed -n '1,5p' file` handled below; bare sed without -n skip
            pass
        if prog not in BASH_FILE_TOOLS:
            continue
        # trailing non-flag args are candidate paths (grep pattern is the
        # first non-flag arg and is excluded)
        rest = tokens[1:]
        non_flags = [t for t in rest if not t.startswith("-")]
        if prog == "grep" and non_flags:
            non_flags = non_flags[1:]  # drop the search pattern
        for t in non_flags:
            if _looks_like_repo_path(t) and "." in Path(t).name:
                found.append((_normalize_path(t), prog))
    return found


def compute_readset(path: Path) -> tuple[Counter, dict]:
    """Returns (Counter[normalized_path] -> access count, provenance dict
    normalized_path -> set of tool names that accessed it)."""
    counts: Counter = Counter()
    provenance: dict[str, set] = {}
    for _idx, name, tool_input in iter_tool_uses(path):
        if name == "Read":
            fp = tool_input.get("file_path")
            if fp:
                norm = _normalize_path(fp)
                counts[norm] += 1
                provenance.setdefault(norm, set()).add("Read")
        elif name == "Bash":
            cmd = tool_input.get("command") or ""
            for norm, subtool in extract_bash_paths(cmd):
                counts[norm] += 1
                provenance.setdefault(norm, set()).add(f"Bash:{subtool}")
    return counts, provenance


def cmd_readset(args):
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        counts, provenance = compute_readset(path)
        out[path.name] = dict(
            unique_files=len(counts),
            total_accesses=sum(counts.values()),
            files=[
                dict(path=p, count=c, via=sorted(provenance.get(p, [])))
                for p, c in counts.most_common()
            ],
        )
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for name, d in out.items():
        print(f"=== {name} ===")
        print(f"  unique_files={d['unique_files']}  total_accesses={d['total_accesses']}")
        for row in d["files"][:30]:
            print(f"    {row['count']:>3}x  {row['path']}  [{','.join(row['via'])}]")
        if len(d["files"]) > 30:
            print(f"    ... ({len(d['files']) - 30} more)")
        print()


# --------------------------------------------------------------------------
# overlap
# --------------------------------------------------------------------------

SLICE_HEADER_RE = re.compile(r"^=== (.*?) ===\s*$")
# grabs a path-looking token: has a '/' or a dot-extension, not pure prose
PATH_TOKEN_RE = re.compile(r"([\w][\w./\-]*\.[A-Za-z0-9_]+)")


def parse_readlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # read-list rows are `<path>[:<a>-<b>]  <kind>  <why>` (two-space columns,
        # pack.py/E-002 format); older one-column lists are plain paths. Keep ONLY
        # the path column and strip a `:a-b` slice suffix — before 2026-08-20 the
        # whole row was kept as a "path", which inflated the pack file-set in the
        # E-006 Task B overlap tables (pack.py `score` is the superseding measure).
        token = line.split("  ")[0].split()[0]
        token = token.split(":", 1)[0] if re.search(r":\d+-\d+$", token) else token
        out.append(token)
    return out


def parse_pack_slice_headers(path: Path, basename_map: dict) -> tuple[set, list]:
    """Returns (resolved_file_set, unresolved_header_lines)."""
    resolved = set()
    unresolved = []
    if not path.exists():
        return resolved, unresolved
    for line in path.read_text().splitlines():
        m = SLICE_HEADER_RE.match(line)
        if not m:
            continue
        header = m.group(1)
        # strip trailing "@<hash>" markers
        header_wo_hash = re.sub(r"\s*@[0-9a-f]{6,}\s*$", "", header)
        tokmatch = PATH_TOKEN_RE.search(header_wo_hash)
        if not tokmatch:
            unresolved.append(line)
            continue
        tok = tokmatch.group(1).rstrip(":,")
        # SLICE headers embed "path:LINE-RANGE" — strip a trailing :digits-digits
        tok = re.sub(r":\d+(-\d+)?$", "", tok)
        if "/" in tok:
            resolved.add(tok)
        else:
            # bare filename — resolve via basename map from read-list.txt
            full = basename_map.get(tok)
            if full:
                resolved.add(full)
            else:
                resolved.add(tok)  # best-effort, unresolved directory
    return resolved, unresolved


def build_pack_fileset(pack_dir: Path) -> dict:
    readlist = parse_readlist(pack_dir / "read-list.txt")
    basename_map = {Path(p).name: p for p in readlist}
    pack_md = pack_dir / "pack.md"
    slice_files, unresolved = parse_pack_slice_headers(pack_md, basename_map)
    fileset = set(readlist) | slice_files
    return dict(fileset=fileset, readlist=set(readlist), slice_files=slice_files,
                unresolved_headers=unresolved)


def classify_extra(path: str, via: list[str]) -> str:
    if GATE_ARTIFACT_RE.search(path):
        return "gate/test artifact"
    if any(v.startswith("Bash:grep") for v in via):
        return "sweep/probe (grep)"
    if any(v.startswith("Bash:") for v in via):
        return "sweep/probe (cat/head/tail/sed)"
    return "sweep/probe (Read)"


def cmd_overlap(args):
    pack = build_pack_fileset(Path(args.pack_dir))
    pack_dir_norm = str(args.pack_dir).rstrip("/")
    meta_files = {f"{pack_dir_norm}/pack.md", f"{pack_dir_norm}/read-list.txt",
                  f"{pack_dir_norm}/pack-delta.md"}
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        counts, provenance = compute_readset(path)
        read_set = set(counts.keys()) - meta_files
        inter = read_set & pack["fileset"]
        extra = read_set - pack["fileset"]
        dead = pack["fileset"] - read_set

        extra_rows = []
        for p in extra:
            via = sorted(provenance.get(p, []))
            extra_rows.append(dict(path=p, count=counts[p], via=via, category=classify_extra(p, via)))
        extra_rows.sort(key=lambda r: -r["count"])

        cat_totals = Counter(r["category"] for r in extra_rows)

        out[path.name] = dict(
            pack_dir=str(args.pack_dir),
            pack_fileset_size=len(pack["fileset"]),
            pack_unresolved_headers=len(pack["unresolved_headers"]),
            meta_pack_doc_reads_excluded=len(set(counts.keys()) & meta_files),
            read_set_size=len(read_set),
            intersection_size=len(inter),
            extra_size=len(extra),
            dead_size=len(dead),
            extra_by_category=dict(cat_totals),
            top_extra=extra_rows[:20],
            dead_weight=sorted(dead)[:40],
        )
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for name, d in out.items():
        print(f"=== {name}  vs pack {d['pack_dir']} ===")
        print(f"  pack_fileset={d['pack_fileset_size']} (unresolved slice headers: {d['pack_unresolved_headers']}, "
              f"meta pack-doc self-reads excluded: {d['meta_pack_doc_reads_excluded']})")
        print(f"  reviewer_read_set={d['read_set_size']}  intersection={d['intersection_size']}  "
              f"read_set\\pack={d['extra_size']}  pack\\read_set(dead weight)={d['dead_size']}")
        print(f"  extra-by-category: {d['extra_by_category']}")
        print("  top read_set\\pack files:")
        for r in d["top_extra"][:15]:
            print(f"    {r['count']:>3}x  {r['path']}  [{r['category']}]")
        print("  dead-weight (pack files reviewer never touched), first 40:")
        for p in d["dead_weight"]:
            print(f"    {p}")
        print()


# --------------------------------------------------------------------------
# boundaries (E-008) — coherent checkpoint-boundary detection from CONTENT
# --------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SEARCH_PROGS = {"grep", "rg", "ag", "find", "ls", "cat", "head", "tail", "sed", "awk", "wc"}

# Bash-mediated edits: this repo's agents write files far more often through
# Bash (heredoc redirect, `sed -i`, a python heredoc calling write_text) than
# through the Edit/Write tools — the P110 implementer made 333 Bash calls and
# only 5 Write calls — so edit-cluster detection MUST cover them or it sees
# almost no clusters on exactly the runs that matter most.
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
REDIRECT_RE = re.compile(r"(?<![0-9<>&])>>?\s*(['\"]?)([^\s'\"|&;<>]+)\1")
PY_WRITE_RE = re.compile(r"write_text\s*\(|open\s*\([^)]*['\"][wa]\+?['\"]")
PY_PATH_RE = re.compile(r"Path\s*\(\s*['\"]([^'\"]+)['\"]\s*\)|open\s*\(\s*['\"]([^'\"]+)['\"]")

GATE_CMD_RE = re.compile(r"testing-exec\.sh|schema-gate\.sh|\bpytest\b")
COMMIT_CMD_RE = re.compile(r"\bgit\s+commit\b")
LOGREPORT_RE = re.compile(r"(LOG|REPORT)\.md$", re.IGNORECASE)

# result-text limits: transcripts are multi-MB, so only a bounded window of
# each tool_result is ever held (head+tail), enough for a pass/fail verdict.
RESULT_HEAD = 4000
RESULT_TAIL = 2000

BOUNDARY_KINDS = ("gate_green", "gate_red", "gate_unknown", "commit",
                  "edit_cluster_end", "log_report_write")


@dataclass
class Boundary:
    call_idx: int
    kind: str
    detail: str


def iter_tool_uses_full(path: Path):
    """Like iter_tool_uses but also yields the tool_use_id:
    (call_idx, tool_use_id, tool_name, tool_input)."""
    idx = 0
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            if not msg.get("usage"):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield idx, block.get("id"), block.get("name"), (block.get("input") or {})
            idx += 1


def _tool_result_lengths(path: Path) -> dict[str, int]:
    """tool_use_id -> character length of its result, WITHOUT retaining the
    body (measure-then-discard, same discipline as load_tool_results)."""
    out: dict[str, int] = {}
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "user":
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                raw = block.get("content")
                if isinstance(raw, list):
                    n = sum(len(b.get("text", "")) for b in raw
                            if isinstance(b, dict) and b.get("type") == "text")
                else:
                    n = len(str(raw or ""))
                tid = block.get("tool_use_id")
                if tid:
                    out[tid] = out.get(tid, 0) + n
    return out


_MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit"}
_ORIENT_TOOLS = {"Read", "Grep", "Glob", "Bash"}


def compute_orientation(path: Path) -> dict:
    """Everything a session did BEFORE its first file mutation (Edit/Write/
    NotebookEdit) — the self-directed orientation phase pack.py's derivation
    rules exist to replace. Returns a dict ready for the CLI or for feeding
    back into pack curation review; never retains file content, only sizes.

    Boundary choice: the first mutating call is unambiguous and mechanically
    detectable, unlike trying to classify which Bash calls are read-only —
    that heuristic is fragile enough to not be worth it for a calibration
    tool. Everything (including Bash) before that first mutation counts as
    orientation; nothing after it does, even a later exploratory Read."""
    lengths = _tool_result_lengths(path)
    reads: list[dict] = []      # ordered per-call orientation events
    per_file: dict[str, dict] = {}
    boundary_idx = None
    total_before_boundary = 0
    for idx, tid, name, tin in iter_tool_uses_full(path):
        if name in _MUTATING_TOOLS:
            boundary_idx = idx
            break
        if name not in _ORIENT_TOOLS:
            continue
        total_before_boundary += 1
        chars = lengths.get(tid, 0)
        if name == "Read":
            fp = _normalize_path(tin.get("file_path") or "?")
            sliced = ("offset" in tin) or ("limit" in tin)
            reads.append(dict(idx=idx, tool="Read", path=fp,
                               kind="slice" if sliced else "full", chars=chars))
            d = per_file.setdefault(fp, dict(count=0, chars=0, kind="full", first_idx=idx))
            d["count"] += 1
            d["chars"] += chars
            if sliced and d["kind"] == "full" and d["count"] > 1:
                pass  # a later full read still wins; first-read kind is informative enough
            elif not sliced:
                d["kind"] = "full"
            elif d["count"] == 1:
                d["kind"] = "slice"
        elif name == "Grep":
            reads.append(dict(idx=idx, tool="Grep", path=tin.get("path", "."),
                               kind=tin.get("pattern", ""), chars=chars))
        elif name == "Glob":
            reads.append(dict(idx=idx, tool="Glob", path=tin.get("pattern", ""),
                               kind=None, chars=chars))
        elif name == "Bash":
            cmd = tin.get("command") or ""
            bpaths = extract_bash_paths(cmd)
            if bpaths:
                for p, subtool in bpaths:
                    fp = _normalize_path(p)
                    reads.append(dict(idx=idx, tool=f"Bash:{subtool}", path=fp,
                                       kind=None, chars=chars))
                    d = per_file.setdefault(fp, dict(count=0, chars=0, kind="cmd", first_idx=idx))
                    d["count"] += 1
                    d["chars"] += chars
            else:
                reads.append(dict(idx=idx, tool="Bash", path=cmd[:80],
                                   kind=None, chars=chars))
    return dict(
        boundary_idx=boundary_idx,
        orientation_calls=total_before_boundary,
        events=reads,
        per_file=per_file,
    )


def _handoff_touch_paths(text: str) -> set[str]:
    """`scope.touch` entries only — never regex the whole file, `escalate_if`
    is the same `- "..."` YAML-list shape and would pollute the set. Frontmatter
    extraction must be LINE-anchored (`line.strip() == "---"`), not a bare
    substring split — a `# --- section header ---` comment inside scope.touch
    itself contains the delimiter string and truncates a naive split after the
    first entry (measured: 875 of ~12k chars, cutting off after `touch:` alone)."""
    lines = text.splitlines()
    fm_start, fm_end = None, None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if fm_start is None:
                fm_start = i
            else:
                fm_end = i
                break
    lines = lines[fm_start + 1:fm_end] if fm_start is not None and fm_end else lines
    out: list[str] = []
    in_touch = False
    touch_indent = None
    for line in lines:
        stripped = line.strip()
        if re.match(r"^touch\s*:\s*$", stripped):
            in_touch = True
            touch_indent = len(line) - len(line.lstrip())
            continue
        if in_touch:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= touch_indent:
                break
            m = re.match(r'^-\s*"([^"]+)"', stripped)
            if m:
                out.append(m.group(1))
    return set(out)


def cmd_orient(args):
    handoff_scope: set[str] | None = None
    if args.handoff:
        text = Path(args.handoff).read_text()
        handoff_scope = _handoff_touch_paths(text)
    for path_str in args.files:
        path = Path(path_str)
        d = compute_orientation(path)
        per_file = d["per_file"]
        total_chars = sum(v["chars"] for v in per_file.values())
        print(f"=== {path.name} ===")
        print(f"  orientation ends at call #{d['boundary_idx']} "
              f"(first Edit/Write/NotebookEdit); {d['orientation_calls']} read-tool "
              f"calls before it, {len(per_file)} distinct files/paths, "
              f"~{total_chars // 4:,} tokens read (chars/4 estimate)")
        print(f"\n  IN ORDER FIRST-SEEN (kind, calls, ~tokens, path):")
        seen = set()
        for ev in d["events"]:
            fp = ev["path"]
            if fp in seen or ev["tool"] not in ("Read",) and not ev["tool"].startswith("Bash:"):
                continue
            seen.add(fp)
            fd = per_file.get(fp, {})
            flag = ""
            if handoff_scope is not None:
                flag = "  [in scope.touch]" if fp in handoff_scope else "  [NOT in scope.touch]"
            print(f"    {fd.get('kind', '?'):<5}  {fd.get('count', 1):>2}x  "
                  f"~{fd.get('chars', ev['chars']) // 4:>6,}tok  {fp}{flag}")
        if handoff_scope is not None:
            derived = set(per_file)
            missing_from_scope = sorted(derived - handoff_scope)
            unread_in_scope = sorted(handoff_scope - derived)
            print(f"\n  READ BUT NOT IN scope.touch ({len(missing_from_scope)}) — "
                  f"candidates pack.py's derivation is missing:")
            for fp in missing_from_scope:
                print(f"    {fp}")
            print(f"\n  IN scope.touch BUT NEVER READ during orientation "
                  f"({len(unread_in_scope)}):")
            for fp in unread_in_scope:
                print(f"    {fp}")
        if args.json:
            print(json.dumps(d, indent=2, default=str))
        print()


def load_tool_results(path: Path) -> dict:
    """tool_use_id -> dict(text=<bounded head+tail snippet>, is_error=bool).
    Never retains a full result body."""
    out: dict[str, dict] = {}
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "user":
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                raw = block.get("content")
                if isinstance(raw, list):
                    parts = [b.get("text", "") for b in raw
                             if isinstance(b, dict) and b.get("type") == "text"]
                    text = "\n".join(parts)
                else:
                    text = str(raw or "")
                if len(text) > RESULT_HEAD + RESULT_TAIL:
                    text = text[:RESULT_HEAD] + "\n...\n" + text[-RESULT_TAIL:]
                out[block.get("tool_use_id")] = dict(
                    text=text, is_error=bool(block.get("is_error"))
                )
    return out


def strip_heredoc_bodies(command: str) -> str:
    """Remove heredoc BODIES, keeping the command lines themselves.

    Without this, any `python3 - <<'PY' ... PY` whose body merely *mentions*
    pytest (a comment being written into a test file, say) is misread as a
    gate run. Verified against the P110 implementer: 2 of its 18 apparent
    pytest runs were heredoc text, not executions."""
    lines = command.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = HEREDOC_RE.search(line)
        if m:
            delim = m.group(2)
            i += 1
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
        i += 1
    return "\n".join(out)


def _is_write_path(tok: str) -> bool:
    if not tok or tok.startswith("/dev/") or tok.startswith("&"):
        return False
    if _looks_like_repo_path(tok):
        return True
    return "/" in tok and "." in Path(tok).name


def extract_bash_writes(command: str) -> list[str]:
    """Normalized paths this Bash command WRITES (heredoc/redirect, `sed -i`,
    `tee`, `cp`/`mv` destination, or a python heredoc calling write_text/open-w).
    Read-only commands return []."""
    targets: list[str] = []
    stripped = strip_heredoc_bodies(command)
    for _pre, tok in REDIRECT_RE.findall(stripped):
        if _is_write_path(tok):
            targets.append(_normalize_path(tok))
    for tokens in _bash_segments(stripped):
        prog = Path(tokens[0]).name
        rest = tokens[1:]
        non_flags = [t for t in rest if not t.startswith("-")]
        if prog == "sed" and any(t.startswith("-i") or t == "--in-place" for t in rest):
            for t in non_flags[1:] if len(non_flags) > 1 else []:
                if _is_write_path(t):
                    targets.append(_normalize_path(t))
        elif prog == "tee":
            for t in non_flags:
                if _is_write_path(t):
                    targets.append(_normalize_path(t))
        elif prog in ("cp", "mv") and len(non_flags) >= 2:
            t = non_flags[-1]
            if _is_write_path(t):
                targets.append(_normalize_path(t))
    if PY_WRITE_RE.search(command):
        for a, b in PY_PATH_RE.findall(command):
            t = a or b
            if _is_write_path(t):
                targets.append(_normalize_path(t))
    seen, uniq = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


BASH_OPERATORS = {"&&", "||", ";", "|", "&"}


def _bash_segments(command: str) -> list[list[str]]:
    """Split a Bash command into per-program token segments, QUOTE-AWARE.

    Distinct from `extract_bash_paths`'s regex split (kept byte-identical for
    E-006/E-007 reproducibility): splitting the raw string on `|` cuts through
    quoted grep alternations like "^def \\|^@pytest\\|...", orphaning a
    fragment whose first token then looks like a program name — which is how
    a plain grep got counted as a gate run. shlex tokenizes first, so a quoted
    pattern stays inside its own program's segment."""
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        return [t.split() for t in (x.strip() for x in SPLIT_RE.split(command)) if t.strip()]
    segs: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok in BASH_OPERATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def is_gate_command(command: str) -> bool:
    """True when the command RUNS a gate, not when it merely mentions one.
    Heredoc bodies are stripped first, and a segment whose program is a
    search/inspection tool (grep, cat, ...) is ignored even if the gate token
    appears in its arguments."""
    command = strip_heredoc_bodies(command)
    if not GATE_CMD_RE.search(command):
        return False
    for tokens in _bash_segments(command):
        prog = Path(tokens[0]).name
        if prog in SEARCH_PROGS:
            continue
        if GATE_CMD_RE.search(" ".join(tokens)):
            return True
    return False


def gate_verdict(text: str, is_error: bool) -> str:
    """green / red / unknown, from the tool_result body of a gate run."""
    if is_error:
        return "red"
    if not text:
        return "unknown"
    if re.search(r"Command running in background", text):
        return "unknown"
    fails = sum(int(m) for m in re.findall(r"(\d+)\s+failed", text))
    errs = sum(int(m) for m in re.findall(r"(\d+)\s+errors?\b", text))
    passes = sum(int(m) for m in re.findall(r"(\d+)\s+passed", text))
    if fails or errs:
        return "red"
    # Gate output reaching the transcript is often a filtered excerpt with no
    # summary line (measured: 4 of the P110 implementer's pytest results), so
    # fall back to pytest's own failure markers.
    if re.search(r"^E {3,}\S", text, re.MULTILINE) or \
            re.search(r"^FAILED \S|^ERROR \S", text, re.MULTILINE) or \
            re.search(r"^_{5,} .* _{5,}$", text, re.MULTILINE) or \
            re.search(r"Traceback \(most recent", text):
        return "red"
    if passes:
        return "green"
    m = re.search(r"exit(?:\s+|_)?code[:= ]+(\d+)", text, re.IGNORECASE)
    if m:
        return "green" if m.group(1) == "0" else "red"
    if re.search(r"\bPASSED\b|\ball tests pass", text, re.IGNORECASE):
        return "green"
    return "unknown"


def _edit_target(tool_input: dict) -> str | None:
    fp = tool_input.get("file_path") or tool_input.get("notebook_path")
    return _normalize_path(fp) if fp else None


def detect_boundaries(path: Path, cluster_mode: str = "file",
                      min_cluster: int = 1) -> list[Boundary]:
    """Candidate COHERENT checkpoint boundaries, by call index:
      gate_green / gate_red / gate_unknown — a Bash gate run + its verdict
      commit                              — a Bash `git commit`
      edit_cluster_end                    — last edit of a maximal run of
                                            edit ops (Edit/Write/MultiEdit
                                            tools AND Bash-mediated writes)
                                            on ONE
                                            file (cluster_mode=file) or one
                                            directory (cluster_mode=dir),
                                            ended by an edit op on a
                                            different target
      log_report_write                    — an edit of a *LOG.md / *REPORT.md
    """
    results = load_tool_results(path)
    bounds: list[Boundary] = []
    cur_key: str | None = None
    cur_len = 0
    cur_last_idx = -1

    def close_cluster():
        nonlocal cur_key, cur_len, cur_last_idx
        if cur_key is not None and cur_len >= min_cluster and cur_last_idx >= 0:
            bounds.append(Boundary(cur_last_idx, "edit_cluster_end",
                                   f"{cur_key} ({cur_len} edit{'s' if cur_len > 1 else ''})"))
        cur_key, cur_len, cur_last_idx = None, 0, -1

    for idx, tuid, name, tin in iter_tool_uses_full(path):
        if name in EDIT_TOOLS:
            target = _edit_target(tin)
            if target is None:
                continue
            key = str(Path(target).parent) if cluster_mode == "dir" else target
            if key != cur_key:
                close_cluster()
                cur_key, cur_len, cur_last_idx = key, 0, -1
            cur_len += 1
            cur_last_idx = idx
            if LOGREPORT_RE.search(target):
                bounds.append(Boundary(idx, "log_report_write", target))
            continue
        if name == "Bash":
            cmd = tin.get("command") or ""
            writes = extract_bash_writes(cmd)
            if writes:
                target = writes[0]
                key = str(Path(target).parent) if cluster_mode == "dir" else target
                if key != cur_key:
                    close_cluster()
                    cur_key, cur_len, cur_last_idx = key, 0, -1
                cur_len += 1
                cur_last_idx = idx
                for w in writes:
                    if LOGREPORT_RE.search(w):
                        bounds.append(Boundary(idx, "log_report_write", w))
                        break
            if COMMIT_CMD_RE.search(strip_heredoc_bodies(cmd)):
                bounds.append(Boundary(idx, "commit", "git commit"))
            if is_gate_command(cmd):
                r = results.get(tuid) or {}
                v = gate_verdict(r.get("text", ""), r.get("is_error", False))
                token = ("testing-exec.sh" if "testing-exec.sh" in cmd
                         else "schema-gate.sh" if "schema-gate.sh" in cmd else "pytest")
                bounds.append(Boundary(idx, f"gate_{v}", token))
    close_cluster()

    seen = set()
    uniq = []
    for b in sorted(bounds, key=lambda b: (b.call_idx, b.kind)):
        k = (b.call_idx, b.kind)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(b)
    return uniq


def cmd_boundaries(args):
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        calls = load_calls(path)
        n = len(calls)
        bl = detect_boundaries(path, args.cluster_mode, args.min_cluster)
        rows = []
        for b in bl:
            i = min(b.call_idx, n - 1) if n else 0
            rows.append(dict(call_idx=b.call_idx, kind=b.kind, detail=b.detail,
                             context=calls[i].context if n else 0,
                             pct_of_run=round(100 * b.call_idx / n, 1) if n else 0.0))
        out[path.name] = dict(total_calls=n, boundaries=rows,
                              by_kind=dict(Counter(r["kind"] for r in rows)))
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for name, d in out.items():
        print(f"=== {name} (calls={d['total_calls']}) ===")
        print(f"  by kind: {d['by_kind']}  total={len(d['boundaries'])}")
        print("   call_idx  %run   context      kind               detail")
        shown = d["boundaries"] if args.all else [
            r for r in d["boundaries"] if r["kind"] != "edit_cluster_end"]
        for r in shown:
            print(f"  {r['call_idx']:>8}  {r['pct_of_run']:>5}  {r['context']:>10,}  "
                  f"{r['kind']:<17}  {r['detail'][:60]}")
        if not args.all:
            hid = len(d["boundaries"]) - len(shown)
            if hid:
                print(f"  ({hid} edit_cluster_end rows hidden; --all to show)")
        print()


# --------------------------------------------------------------------------
# simulate-boundary (E-008) — DP constrained to detected boundaries
# --------------------------------------------------------------------------

def _dp_constrained(calls, A, S, brief_cost, allowed: list[int], max_n: int) -> dict:
    """Per-N optimal placement restricted to `allowed` call indices (each in
    1..n-1). Same recurrence as simulate_multi's DP, over the allowed set."""
    n = len(calls)
    INF = float("inf")
    actual_total = A[n]
    allowed = sorted({j for j in allowed if 1 <= j <= n - 1})
    per_n: dict[int, dict] = {}
    if not allowed:
        return per_n
    pos = {j: k for k, j in enumerate(allowed)}
    D_prev = {j: INF for j in allowed}
    args_by_k: dict[int, dict] = {}
    for k in range(1, max_n + 1):
        D_cur = {j: INF for j in allowed}
        arg_cur: dict[int, int | None] = {j: None for j in allowed}
        for j in allowed:
            row = S[j]
            if k == 1:
                D_cur[j] = brief_cost + row[n - j - 1]
            else:
                best, barg = INF, None
                for m in allowed[pos[j] + 1:]:
                    if D_prev[m] == INF:
                        continue
                    v = row[m - j - 1] + D_prev[m]
                    if v < best:
                        best, barg = v, m
                if best < INF:
                    D_cur[j] = brief_cost + best
                    arg_cur[j] = barg
        args_by_k[k] = arg_cur
        best_total, best_first = INF, None
        for j in allowed:
            if D_cur[j] == INF:
                continue
            v = A[j] + D_cur[j]
            if v < best_total:
                best_total, best_first = v, j
        placements = []
        if best_first is not None:
            j, kk = best_first, k
            while j is not None:
                placements.append(j)
                j = args_by_k[kk][j]
                kk -= 1
        per_n[k] = dict(
            cost=round(best_total, 1) if best_total < INF else None,
            savings_pct=(round(100.0 * (actual_total - best_total) / actual_total, 2)
                         if best_total < INF and actual_total else None),
            placements=sorted(placements),
        )
        D_prev = D_cur
    return per_n


def first_boundary_after(bounds: list[Boundary], n: int, pct: int,
                         kinds: tuple) -> Boundary | None:
    cut = pct / 100.0 * n
    for b in bounds:
        if b.call_idx >= cut and b.kind in kinds:
            return b
    return None


def first_boundary_at_context(bounds: list[Boundary], calls, ctx: int,
                              kinds: tuple) -> Boundary | None:
    """The operational rule stated in CONTEXT SIZE: the first coherent
    boundary of an allowed kind at or above `ctx` carried tokens."""
    for b in bounds:
        if b.kind in kinds and calls[min(b.call_idx, len(calls) - 1)].context >= ctx:
            return b
    return None


def simulate_boundary(calls, path: Path, brief_tokens: int, max_n: int,
                      kinds: tuple, cluster_mode: str, min_cluster: int,
                      ctx_rule_list: tuple = ()) -> dict:
    n = len(calls)
    A = _actual_prefix_costs(calls)
    S = _segment_cost_rows(calls, brief_tokens)
    brief_cost = brief_tokens * W_CACHE_CREATE
    actual_total = A[n]
    bounds = detect_boundaries(path, cluster_mode, min_cluster)
    sel = [b for b in bounds if b.kind in kinds]
    allowed = sorted({min(max(b.call_idx + 1, 1), n - 1) for b in sel})
    constrained = _dp_constrained(calls, A, S, brief_cost, allowed, max_n)
    unconstrained = simulate_multi(calls, brief_tokens, max_n)

    rule_kinds = ("gate_green", "commit")
    rules = {}
    ctx_rules = {}
    for C in ctx_rule_list:
        b = first_boundary_at_context(bounds, calls, C, kinds)
        if b is None:
            ctx_rules[C] = None
            continue
        j = min(max(b.call_idx + 1, 1), n - 1)
        r = simulate_checkpoint(calls, j, brief_tokens)
        ctx_rules[C] = dict(call_idx=b.call_idx, kind=b.kind,
                            pct_of_run=round(100 * b.call_idx / n, 1),
                            context=calls[b.call_idx].context,
                            savings_pct=r["savings_pct"] if r else None)
    for pct in (25, 33, 50):
        b = first_boundary_after(bounds, n, pct, rule_kinds)
        if b is None:
            rules[pct] = None
            continue
        j = min(max(b.call_idx + 1, 1), n - 1)
        r = simulate_checkpoint(calls, j, brief_tokens)
        rules[pct] = dict(call_idx=b.call_idx, kind=b.kind, detail=b.detail,
                          pct_of_run=round(100 * b.call_idx / n, 1),
                          context=calls[b.call_idx].context,
                          savings_pct=r["savings_pct"] if r else None)
    return dict(total_calls=n, actual_total_cost=round(actual_total, 1),
                brief_tokens=brief_tokens,
                n_boundaries=len(sel), n_allowed=len(allowed),
                by_kind=dict(Counter(b.kind for b in sel)),
                constrained=constrained,
                unconstrained=unconstrained["per_n_optimal"],
                uniform=unconstrained["uniform"],
                rules=rules, ctx_rules=ctx_rules)


def cmd_simulate_boundary(args):
    kinds = tuple(args.kinds.split(",")) if args.kinds else BOUNDARY_KINDS
    out = {}
    for path_str in args.files:
        path = Path(path_str)
        calls = load_calls(path)
        if len(calls) < 4:
            out[path.name] = dict(note="too few calls to simulate")
            continue
        ctx_rule_list = tuple(int(x) for x in args.ctx_rules.split(",")) if args.ctx_rules else ()
        out[path.name] = simulate_boundary(calls, path, args.brief_tokens,
                                           args.max_n, kinds, args.cluster_mode,
                                           args.min_cluster, ctx_rule_list)
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for name, d in out.items():
        print(f"=== {name} (brief={args.brief_tokens:,} tok, kinds={','.join(kinds)}) ===")
        if "note" in d:
            print(f"  {d['note']}")
            continue
        n = d["total_calls"]
        print(f"  calls={n}  boundaries={d['n_boundaries']} "
              f"(distinct placements={d['n_allowed']})  by_kind={d['by_kind']}")
        print("    N   bounded_sav%   optimal_sav%   uniform_sav%   gap(opt-bnd)   bounded placements (% of calls)")
        for N in range(1, args.max_n + 1):
            c = d["constrained"].get(N)
            u = d["unconstrained"].get(N)
            un = d["uniform"].get(N)
            if not c or c["savings_pct"] is None:
                print(f"  {N:>3}   {'n/a':>12}   {u['savings_pct']:>12}   {un['savings_pct']:>12}   {'—':>12}")
                continue
            gap = round(u["savings_pct"] - c["savings_pct"], 2)
            ppct = ",".join(f"{100 * p / n:.0f}" for p in c["placements"])
            print(f"  {N:>3}   {c['savings_pct']:>12}   {u['savings_pct']:>12}   "
                  f"{un['savings_pct']:>12}   {gap:>12}   [{ppct}]")
        print("  rule 'first gate_green|commit after X% of run':")
        for pct, r in d["rules"].items():
            if r is None:
                print(f"    X={pct}%: no such boundary")
            else:
                print(f"    X={pct}%: call {r['call_idx']} ({r['pct_of_run']}% of run, "
                      f"{r['kind']}, context={r['context']:,}) -> 1-ckpt savings {r['savings_pct']}%")
        if d.get("ctx_rules"):
            print("  rule 'first coherent boundary at context >= C':")
            for C, r in d["ctx_rules"].items():
                if r is None:
                    print(f"    C={C:,}: never reached")
                else:
                    print(f"    C={C:,}: call {r['call_idx']} ({r['pct_of_run']}% of run, "
                          f"{r['kind']}, context={r['context']:,}) -> 1-ckpt savings {r['savings_pct']}%")
        print()


# --------------------------------------------------------------------------
# threshold (E-008) — where does the FIRST checkpoint belong?
# --------------------------------------------------------------------------

def _quartiles(xs: list[float]) -> dict:
    if not xs:
        return dict(n=0)
    s = sorted(xs)

    def q(p):
        if len(s) == 1:
            return s[0]
        pos = p * (len(s) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return dict(n=len(s), min=s[0], q1=q(0.25), median=q(0.5), q3=q(0.75), max=s[-1])


def marginal_threshold(calls, brief_tokens: int) -> dict | None:
    """First call whose per-call CARRYING cost (cache_read x W_CACHE_READ)
    exceeds what the same call would cost after a restart: the post-restart
    carrying cost (brief x W_CACHE_READ) plus the one-time brief write
    amortized over the remaining calls (brief x W_CACHE_CREATE / remaining).
    Returns that call index and its context size."""
    n = len(calls)
    for i, c in enumerate(calls):
        remaining = n - i
        if remaining <= 0:
            break
        carry = c.cache_read * W_CACHE_READ
        amortized = brief_tokens * W_CACHE_READ + brief_tokens * W_CACHE_CREATE / remaining
        if carry > amortized:
            return dict(call_idx=i, pct_of_run=round(100 * i / n, 1),
                        context=c.context,
                        carry_cost=round(carry, 1),
                        amortized_restart_cost=round(amortized, 1))
    return None


def threshold_row(path: Path, brief_list: tuple, max_n: int,
                  cluster_mode: str, min_cluster: int, kinds: tuple) -> dict:
    calls = load_calls(path)
    n = len(calls)
    A = _actual_prefix_costs(calls)
    row = dict(calls=n, final_context=calls[-1].context if n else 0,
               marginal={}, dp_first={}, bounded_first={})
    bounds = detect_boundaries(path, cluster_mode, min_cluster)
    sel = [b for b in bounds if b.kind in kinds]
    allowed = sorted({min(max(b.call_idx + 1, 1), n - 1) for b in sel})
    for B in brief_list:
        row["marginal"][B] = marginal_threshold(calls, B)
        S = _segment_cost_rows(calls, B)
        brief_cost = B * W_CACHE_CREATE
        m = simulate_multi(calls, B, max_n)
        ur = m["unrestricted_optimal"]["placements"]
        if ur:
            j = min(ur)
            row["dp_first"][B] = dict(call_idx=j, pct_of_run=round(100 * j / n, 1),
                                      context=calls[j - 1].context,
                                      n_checkpoints=len(ur))
        else:
            row["dp_first"][B] = None
        cons = _dp_constrained(calls, A, S, brief_cost, allowed, max_n)
        best_N, best_sav = None, None
        for N, d in cons.items():
            if d["savings_pct"] is None:
                continue
            if best_sav is None or d["savings_pct"] > best_sav:
                best_N, best_sav = N, d["savings_pct"]
        if best_N is not None:
            j = min(cons[best_N]["placements"])
            row["bounded_first"][B] = dict(call_idx=j, pct_of_run=round(100 * j / n, 1),
                                           context=calls[j - 1].context,
                                           best_n=best_N, best_savings_pct=best_sav)
        else:
            row["bounded_first"][B] = None
    return row


def cmd_threshold(args):
    brief_list = tuple(int(x) for x in args.briefs.split(","))
    kinds = tuple(args.kinds.split(",")) if args.kinds else BOUNDARY_KINDS
    rows = {}
    for path_str in args.files:
        path = Path(path_str)
        if len(load_calls(path)) < 4:
            continue
        rows[path.name] = threshold_row(path, brief_list, args.max_n,
                                        args.cluster_mode, args.min_cluster, kinds)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    for B in brief_list:
        print(f"=== brief={B:,} tok ===")
        print("  transcript                          calls   marg_call  marg_ctx    dpfirst_call  dpfirst_ctx   bnd_call  bnd_ctx    bnd_bestN")
        for name, r in rows.items():
            mg = r["marginal"][B]
            dp = r["dp_first"][B]
            bd = r["bounded_first"][B]
            print(
                f"  {name[:34]:<34}  {r['calls']:>5}   "
                f"{(mg['call_idx'] if mg else -1):>9}  {(mg['context'] if mg else 0):>9,}    "
                f"{(dp['call_idx'] if dp else -1):>11}  {(dp['context'] if dp else 0):>10,}   "
                f"{(bd['call_idx'] if bd else -1):>8}  {(bd['context'] if bd else 0):>8,}   "
                f"{(bd['best_n'] if bd else 0):>8}"
            )
        for key, label in (("marginal", "marginal-threshold context"),
                           ("dp_first", "DP first-checkpoint context"),
                           ("bounded_first", "boundary-constrained first-checkpoint context")):
            ctxs = [r[key][B]["context"] for r in rows.values() if r[key][B]]
            calls_ = [r[key][B]["call_idx"] for r in rows.values() if r[key][B]]
            qc = _quartiles([float(x) for x in ctxs])
            qk = _quartiles([float(x) for x in calls_])
            if qc.get("n"):
                print(f"  {label}: n={qc['n']}  median={qc['median']:,.0f} tok "
                      f"(IQR {qc['q1']:,.0f}-{qc['q3']:,.0f}, range {qc['min']:,.0f}-{qc['max']:,.0f})"
                      f"  |  calls median={qk['median']:.0f} (IQR {qk['q1']:.0f}-{qk['q3']:.0f})")
        print()

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_curve = sub.add_parser("curve", help="per-agent context-growth summary")
    p_curve.add_argument("files", nargs="+")
    p_curve.add_argument("--json", action="store_true")
    p_curve.set_defaults(func=cmd_curve)

    p_sim = sub.add_parser("simulate", help="checkpoint-restart cost simulation")
    p_sim.add_argument("files", nargs="+")
    p_sim.add_argument("--checkpoints", default=",".join(str(x) for x in DEFAULT_CHECKPOINTS_PCT),
                        help="comma-separated percentages of call count, e.g. 25,50,75")
    p_sim.add_argument("--brief-tokens", type=int, default=DEFAULT_BRIEF_TOKENS)
    p_sim.add_argument("--json", action="store_true")
    p_sim.set_defaults(func=cmd_simulate)

    p_sm = sub.add_parser("simulate-multi",
                           help="multi-checkpoint simulation: uniform N-schedules + optimal DP")
    p_sm.add_argument("files", nargs="+")
    p_sm.add_argument("--brief-tokens", type=int, default=DEFAULT_BRIEF_TOKENS)
    p_sm.add_argument("--max-n", type=int, default=12,
                       help="largest checkpoint count for the uniform/per-N tables")
    p_sm.add_argument("--json", action="store_true")
    p_sm.set_defaults(func=cmd_simulate_multi)

    p_rs = sub.add_parser("readset", help="extract deduplicated file read-set")
    p_rs.add_argument("files", nargs="+")
    p_rs.add_argument("--json", action="store_true")
    p_rs.set_defaults(func=cmd_readset)

    p_ov = sub.add_parser("overlap", help="compare read-set against an orientation pack")
    p_ov.add_argument("files", nargs="+")
    p_ov.add_argument("--pack-dir", required=True)
    p_ov.add_argument("--json", action="store_true")
    p_ov.set_defaults(func=cmd_overlap)

    p_or = sub.add_parser("orient", help="derive what a pack SHOULD contain from a "
                           "vanilla (no pre-built pack) agent's own self-directed "
                           "orientation reads, up to its first file mutation")
    p_or.add_argument("files", nargs="+")
    p_or.add_argument("--handoff", help="a handoff .md to diff the derived read-set "
                       "against its scope.touch (shows curation gaps both directions)")
    p_or.add_argument("--json", action="store_true")
    p_or.set_defaults(func=cmd_orient)

    p_b = sub.add_parser("boundaries",
                          help="detect coherent checkpoint boundaries from transcript content")
    p_b.add_argument("files", nargs="+")
    p_b.add_argument("--cluster-mode", choices=("file", "dir"), default="file",
                      help="group edit clusters by exact file (default) or by directory")
    p_b.add_argument("--min-cluster", type=int, default=1,
                      help="minimum edits in a cluster for its end to count as a boundary")
    p_b.add_argument("--all", action="store_true",
                      help="print edit_cluster_end rows too (hidden by default)")
    p_b.add_argument("--json", action="store_true")
    p_b.set_defaults(func=cmd_boundaries)

    p_sb = sub.add_parser("simulate-boundary",
                           help="multi-checkpoint DP constrained to detected boundaries")
    p_sb.add_argument("files", nargs="+")
    p_sb.add_argument("--brief-tokens", type=int, default=DEFAULT_BRIEF_TOKENS)
    p_sb.add_argument("--max-n", type=int, default=10)
    p_sb.add_argument("--kinds", default="",
                       help="comma-separated boundary kinds to allow as placements "
                            f"(default: all of {','.join(BOUNDARY_KINDS)})")
    p_sb.add_argument("--cluster-mode", choices=("file", "dir"), default="file")
    p_sb.add_argument("--min-cluster", type=int, default=1)
    p_sb.add_argument("--ctx-rules", default="",
                       help="comma-separated context sizes: report the first coherent "
                            "boundary at or above each, and its single-checkpoint savings")
    p_sb.add_argument("--json", action="store_true")
    p_sb.set_defaults(func=cmd_simulate_boundary)

    p_th = sub.add_parser("threshold",
                           help="first-checkpoint threshold analysis (context size and calls)")
    p_th.add_argument("files", nargs="+")
    p_th.add_argument("--briefs", default="25000,50000,100000",
                       help="comma-separated brief sizes in tokens")
    p_th.add_argument("--max-n", type=int, default=10)
    p_th.add_argument("--kinds", default="",
                       help="boundary kinds allowed for the constrained DP")
    p_th.add_argument("--cluster-mode", choices=("file", "dir"), default="file")
    p_th.add_argument("--min-cluster", type=int, default=1)
    p_th.add_argument("--json", action="store_true")
    p_th.set_defaults(func=cmd_threshold)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

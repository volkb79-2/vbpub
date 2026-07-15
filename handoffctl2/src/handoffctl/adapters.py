"""Route adapters: dispatch argv, probes, resume, usage extraction. PACKAGE P03.

Everything volatile about the four CLIs, table-driven from RouteDef
(config.Routes). NO free-text shell: argv lists only, rendered from trusted
templates. This module performs NO event writes.

INTERFACE CONTRACT (frozen):

- Placeholders in RouteDef list templates: {session}, {worktree}, {prompt},
  {task_id}, {handoff}, {model}. render_argv() substitutes them INSIDE list
  elements (str.format_map with missing keys -> AdapterError).
- build_dispatch composes the CLI-specific base invocation:
    claude:   [cli, '-p', prompt, '--output-format', 'stream-json',
               '--verbose', '--model', model] (+ ['--effort', effort] if set)
               (+ dispatch_extra)
               (P14 2026-07-15: `-p --output-format json` writes its ENTIRE
               output at process exit -- the log's mtime is structurally
               dead as a liveness signal until the CLI is already done.
               stream-json emits incremental JSONL lines as the turn
               progresses, so the log's mtime IS a real heartbeat and a
               live dashboard tail works. extract_usage below still finds
               the final `result` line by the same "last json-parsing
               brace-line with usage/total_cost_usd" rule.)
    codex:    [cli, 'exec', '--sandbox', sandbox or 'workspace-write',
               '--cd', worktree, prompt] (+ model via ['-m', model])
    opencode: [cli, 'run', '--model', model, '--dir', worktree]
              (+ ['--variant', variant] if set) (+ dispatch_extra) + [prompt]
    reasonix: [cli, 'run', '-dir', worktree, prompt]
    fake:     [cli] + dispatch_extra + [prompt]        (test route)
  The PROMPT is short (<= route.argv_max or 1500 chars; AdapterError if the
  rendered prompt exceeds it): it names the handoff path, worktree, branch,
  gate command hint and the receipt requirement; substance stays in the
  handoff (v2: argv wedge). If 'incremental-write' in route.prompt_hints,
  append the fixed sentence about ~80-line write batches.
- probe(route) runs route.probe argv (subprocess, timeout 60s, captured):
  returns (ok: bool, detail: str). probe == 'session-limit-check' or
  'one-token-ping' are named builtins: for the pilot both execute
  [route.cli, '--version'] as a cheap liveness proxy (documented limitation).
  probe None -> (True, 'no-probe').
- build_resume(route, session, worktree, prompt) -> argv from route.resume
  template; AdapterError if template empty or session required but None
  ('{session}' present in template).
- capture_session(route, attempt_dir, worktree, launched_at, log_path=None)
  -> str | None:
    P17 2026-07-15: route.cli == 'claude' -- the FIRST line the CLI ever
      writes under --output-format stream-json (P14) is a JSON object
      carrying `session_id` (json.loads(line).get('session_id')). Read it
      straight from the attempt log (log_path if the caller passes it --
      the wrapper always knows the exact log file for the CURRENT run, both
      on first dispatch and on resume; else <attempt_dir>/attempt.log) --
      no subprocess, no directory scan. This REPLACES newest-jsonl for
      claude routes: scanning ~/.claude/projects/<slug>/ for the newest
      file modified after launched_at is now both unnecessary (the id is
      already in hand) and unreliable (concurrent claude processes / a
      missing or lagging project dir raced None in production). A missing
      log file, empty first line, non-JSON first line, or a first line
      lacking a non-empty string `session_id` all degrade to None (never
      raises) -- no newest-jsonl fallback for claude routes.
    session_capture == 'newest-jsonl' (non-claude routes only, now): newest
      *.jsonl under ~/.claude/projects/<slug(worktree)>/ modified after
      launched_at, where slug replaces '/' with '-' (leading '-' kept:
      /a/b -> -a-b).
    session_discover argv set: run it (timeout 30), parse JSON list, return
      the id field of the entry whose title/dir matches worktree.
    else None.
- extract_usage(route, attempt_dir, log_text) -> types.Usage:
    usage_source 'output-format-json': the LAST '{'-starting line of
      log_text that json-parses and has 'usage' or 'total_cost_usd' ->
      Usage(basis=ACTUAL, tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens, cached_in=
            usage.cache_read_input_tokens, cost=total_cost_usd,
            currency='USD').
    'exec-output-footer' (codex): regex r'tokens used[:\\s]+([\\d,]+)'
      (case-insens.) -> Usage(basis=ESTIMATED, tokens_out=<n>) (codex does
      not split in/out; documented).
    'session-json' / 'run-log-deepseek-usage': regex
      r'"(prompt|input)_tokens"\\s*:\\s*(\\d+)' and
      r'"(completion|output)_tokens"\\s*:\\s*(\\d+)' over log_text ->
      Usage(basis=ACTUAL tokens, cost=None).
    anything else/no match -> Usage(basis=UNKNOWN).
  Never raises on malformed logs; degrade to UNKNOWN.
- classify_log_tail(text) -> 'blocked' | 'limit' | None:
  'BLOCKED:' at a line start (last 200 lines) -> 'blocked';
  v2 §5.2 limit phrases (case-insens.: 'session limit', 'usage limit',
  'rate limit exceeded', 'quota', 'plan limit') -> 'limit'; else None.
  'blocked' beats 'limit' when both appear.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import RouteDef
from .types import Basis, Usage


class AdapterError(Exception):
    pass


def render_argv(template: list[str], mapping: dict[str, str]) -> list[str]:
    """Substitute placeholders in list elements; missing keys raise AdapterError."""
    result = []
    for elem in template:
        try:
            result.append(elem.format_map(mapping))
        except KeyError as e:
            raise AdapterError(f"missing placeholder {e}")
    return result


def build_dispatch(route: RouteDef, *, handoff_path: str, worktree: str,
                   branch: str, task_id: str, gate_hint: str,
                   receipt_path: str) -> tuple[list[str], str]:
    """Returns (argv, prompt). See module contract for per-CLI shapes."""
    # Construct the prompt (short, names handoff, worktree, branch, gate, receipt)
    prompt = (
        f"Handoff: {handoff_path}\n"
        f"Worktree: {worktree}\n"
        f"Branch: {branch}\n"
        f"Gate: {gate_hint}\n"
        f"Receipt: {receipt_path}\n"
        # 2026-07-15 (live P64 lesson): the first real dispatch produced a
        # full implementation but never committed it, and the review packet
        # diffs main...HEAD — uncommitted work is invisible to review.
        "You MUST `git add` and `git commit` ALL your work on the branch "
        "before finishing; uncommitted work is discarded."
    )

    # Append incremental-write hint if present
    if "incremental-write" in route.prompt_hints:
        prompt += "\nFor large writes, batch in ~80-line chunks."

    # Check prompt length
    argv_max = route.argv_max or 1500
    if len(prompt) > argv_max:
        raise AdapterError(
            f"rendered prompt exceeds argv_max ({len(prompt)} > {argv_max})"
        )

    # Mapping for placeholder substitution in dispatch_extra
    mapping = {
        "session": "",
        "worktree": worktree,
        "prompt": prompt,
        "task_id": task_id,
        "handoff": handoff_path,
        "model": route.model,
    }

    # Build CLI-specific argv
    if route.cli == "claude":
        # P14 2026-07-15: stream-json + --verbose replaces the buffered
        # `json` format so the log gets incremental JSONL writes (a real
        # heartbeat) instead of one giant write at process exit.
        argv = [route.cli, "-p", prompt, "--output-format", "stream-json",
                "--verbose", "--model", route.model]
        if route.effort:
            argv.extend(["--effort", route.effort])
        argv.extend(render_argv(route.dispatch_extra, mapping))
    elif route.cli == "codex":
        sandbox = route.sandbox or "workspace-write"
        argv = [route.cli, "exec", "--sandbox", sandbox,
                "--cd", worktree, prompt, "-m", route.model]
    elif route.cli == "opencode":
        argv = [route.cli, "run", "--model", route.model, "--dir", worktree]
        if route.variant:
            argv.extend(["--variant", route.variant])
        argv.extend(render_argv(route.dispatch_extra, mapping))
        argv.append(prompt)
    elif route.cli == "reasonix":
        argv = [route.cli, "run", "-dir", worktree, prompt]
    elif route.cli == "fake":
        argv = [route.cli]
        argv.extend(render_argv(route.dispatch_extra, mapping))
        argv.append(prompt)
    else:
        raise AdapterError(f"unknown cli: {route.cli}")

    return (argv, prompt)


def build_resume(route: RouteDef, *, session: str | None, worktree: str,
                 prompt: str) -> list[str]:
    """Build resume command from template."""
    if not route.resume:
        raise AdapterError("empty resume template")

    # Check if session is required but missing
    template_str = "".join(route.resume)
    if "{session}" in template_str and session is None:
        raise AdapterError("session required but not provided")

    # Render the template
    mapping = {"session": session or "", "worktree": worktree, "prompt": prompt}
    return render_argv(route.resume, mapping)


def probe(route: RouteDef) -> tuple[bool, str]:
    """Test route liveness via probe."""
    if route.probe is None:
        return (True, "no-probe")

    # Handle named builtins
    if route.probe == "one-token-ping" or route.probe == "session-limit-check":
        probe_argv = [route.cli, "--version"]
    else:
        probe_argv = route.probe

    try:
        result = subprocess.run(probe_argv, capture_output=True, text=True,
                              timeout=60)
        if result.returncode == 0:
            return (True, "ok")
        else:
            return (False, f"exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        return (False, "timeout after 60s")
    except FileNotFoundError:
        return (False, f"command not found: {probe_argv[0]}")
    except Exception as e:
        return (False, str(e))


def _stream_json_session_id(log_path: Path) -> str | None:
    """Read the FIRST line of a stream-json attempt log and pull out its
    `session_id` field. Degrades to None on any I/O or parse problem --
    this is a best-effort early capture, never a hard requirement."""
    try:
        with log_path.open("r", encoding="utf-8") as f:
            first_line = f.readline()
    except OSError:
        return None
    first_line = first_line.strip()
    if not first_line:
        return None
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def capture_session(route: RouteDef, *, attempt_dir: Path, worktree: str,
                    launched_at: datetime, log_path: str | Path | None = None) -> str | None:
    """Capture session ID (see module contract)."""
    if route.cli == "claude":
        # P17 2026-07-15: stream-json's first line carries session_id --
        # read it directly instead of guessing via the newest-jsonl scan
        # (unreliable for claude routes, see module contract). `log_path`
        # names the CURRENT run's exact log file (the wrapper always knows
        # it); fall back to the conventional first-dispatch path only when
        # the caller omits it.
        lp = Path(log_path) if log_path is not None else Path(attempt_dir) / "attempt.log"
        return _stream_json_session_id(lp)

    if route.session_capture == "newest-jsonl":
        # Build the slug: replace '/' with '-', keep leading '-'
        slug = worktree.replace("/", "-")
        if not slug.startswith("-"):
            slug = "-" + slug

        projects_dir = Path.home() / ".claude" / "projects" / slug.lstrip("-")
        if not projects_dir.exists():
            return None

        # Find newest .jsonl modified after launched_at
        jsonl_files = sorted(
            projects_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for jf in jsonl_files:
            mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
            if mtime > launched_at:
                return jf.stem

        return None

    elif route.session_discover:
        # Run session discovery command
        try:
            result = subprocess.run(route.session_discover, capture_output=True,
                                  text=True, timeout=30)
            if result.returncode != 0:
                return None

            sessions = json.loads(result.stdout)
            if not isinstance(sessions, list):
                return None

            for session in sessions:
                if session.get("dir") == worktree or session.get("title") == worktree:
                    return session.get("id")

            return None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            return None

    return None


def extract_usage(route: RouteDef, attempt_dir: Path, log_text: str) -> Usage:
    """Extract usage from logs based on usage_source."""
    if route.usage_source == "output-format-json":
        # Find LAST '{'-starting line (trimmed) that parses as JSON
        lines = log_text.split("\n")
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    data = json.loads(stripped)
                    if "usage" in data or "total_cost_usd" in data:
                        usage_obj = data.get("usage", {})
                        return Usage(
                            basis=Basis.ACTUAL,
                            tokens_in=usage_obj.get("input_tokens"),
                            tokens_out=usage_obj.get("output_tokens"),
                            cached_in=usage_obj.get("cache_read_input_tokens"),
                            cost=data.get("total_cost_usd"),
                            currency="USD"
                        )
                except (json.JSONDecodeError, ValueError):
                    continue
        # Fallback (2026-07-15): newer claude CLI -p --output-format json
        # emits a result ARRAY whose summary carries modelUsage{...costUSD}.
        # Regex the tail rather than parsing the (possibly huge) array.
        tail = log_text[-8192:]
        m_cost = re.findall(r'"costUSD":\s*([0-9.]+)', tail)
        m_in = re.findall(r'"inputTokens":\s*(\d+)', tail)
        m_out = re.findall(r'"outputTokens":\s*(\d+)', tail)
        m_cache = re.findall(r'"cacheReadInputTokens":\s*(\d+)', tail)
        if m_cost:
            return Usage(
                basis=Basis.ACTUAL,
                tokens_in=int(m_in[-1]) if m_in else None,
                tokens_out=int(m_out[-1]) if m_out else None,
                cached_in=int(m_cache[-1]) if m_cache else None,
                cost=round(sum(float(c) for c in m_cost), 6),
                currency="USD",
            )
        return Usage(basis=Basis.UNKNOWN)

    elif route.usage_source == "exec-output-footer":
        # Regex: tokens used
        match = re.search(r"tokens\s+used[:\s]+([0-9,]+)", log_text,
                         re.IGNORECASE)
        if match:
            tokens = int(match.group(1).replace(",", ""))
            return Usage(basis=Basis.ESTIMATED, tokens_out=tokens)
        return Usage(basis=Basis.UNKNOWN)

    elif route.usage_source == "session-json" or route.usage_source == "run-log-deepseek-usage":
        # Regex for input and output tokens
        tokens_in_match = re.search(
            r'"(?:prompt|input)_tokens"\s*:\s*(\d+)',
            log_text
        )
        tokens_out_match = re.search(
            r'"(?:completion|output)_tokens"\s*:\s*(\d+)',
            log_text
        )

        if tokens_in_match and tokens_out_match:
            return Usage(
                basis=Basis.ACTUAL,
                tokens_in=int(tokens_in_match.group(1)),
                tokens_out=int(tokens_out_match.group(1))
            )
        return Usage(basis=Basis.UNKNOWN)

    return Usage(basis=Basis.UNKNOWN)


def classify_log_tail(text: str) -> str | None:
    """Classify the log tail for blocked/limit indicators.

    2026-07-15 false-positive fix (groop-P91 "persistent capped history"):
    a real provider limit TERMINATES the process, so its phrase lands in the
    final lines — whereas a package about rate/quota/caps says "limit",
    "quota", "capped" throughout its own reasoning and tests. Matching
    limit phrases over the last 200 lines misread P91's domain vocabulary as
    a rate-limit hit and looped it back to re-dispatch (v2 §5.2: never infer
    a limit from free text — require a limit-SHAPED terminal signal).

    - BLOCKED: still recognized anywhere in the last 200 lines (the agent
      writes it as a deliberate final marker; a line-start match is
      unambiguous).
    - limit phrases only count in the last LIMIT_TAIL_LINES lines AND only
      when the line looks like a CLI/error surface (starts with a known
      error prefix or contains 'error'/'exceeded'/HTTP 429), not arbitrary
      prose. 'blocked' still beats 'limit'.
    """
    lines = text.split("\n")
    tail200 = lines[-200:] if len(lines) > 200 else lines
    tail_lim = lines[-LIMIT_TAIL_LINES:] if len(lines) > LIMIT_TAIL_LINES else lines

    if any(line.startswith("BLOCKED:") for line in tail200):
        return "blocked"

    # Specific limit phrases only (never bare 'limit'/'quota'/'capped' — that
    # is domain vocabulary), and only in the terminal tail where a real
    # provider limit that ended the process would land.
    limit_phrase = re.compile(
        r"(?i)(session limit|usage limit|rate limit (exceeded|reached|hit)|"
        r"quota (exceeded|reached)|plan limit|429 too many requests|"
        r"too many requests|limit reached)")
    if any(limit_phrase.search(line) for line in tail_lim):
        return "limit"

    return None


LIMIT_TAIL_LINES = 25

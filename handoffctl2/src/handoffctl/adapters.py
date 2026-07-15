"""Route adapters: dispatch argv, probes, resume, usage extraction. PACKAGE P03.

Everything volatile about the four CLIs, table-driven from RouteDef
(config.Routes). NO free-text shell: argv lists only, rendered from trusted
templates. This module performs NO event writes.

INTERFACE CONTRACT (frozen):

- Placeholders in RouteDef list templates: {session}, {worktree}, {prompt},
  {task_id}, {handoff}, {model}. render_argv() substitutes them INSIDE list
  elements (str.format_map with missing keys -> AdapterError).
- build_dispatch composes the CLI-specific base invocation:
    claude:   [cli, '-p', prompt, '--output-format', 'json',
               '--model', model] (+ ['--effort', effort] if set)
               (+ dispatch_extra)
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
- capture_session(route, attempt_dir, worktree, launched_at) -> str | None:
    session_capture == 'newest-jsonl': newest *.jsonl under
      ~/.claude/projects/<slug(worktree)>/ modified after launched_at, where
      slug replaces '/' with '-' (leading '-' kept: /a/b -> -a-b).
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

from datetime import datetime
from pathlib import Path

from .config import RouteDef
from .types import Usage


class AdapterError(Exception):
    pass


def render_argv(template: list[str], mapping: dict[str, str]) -> list[str]:
    raise NotImplementedError


def build_dispatch(route: RouteDef, *, handoff_path: str, worktree: str,
                   branch: str, task_id: str, gate_hint: str,
                   receipt_path: str) -> tuple[list[str], str]:
    """Returns (argv, prompt). See module contract for per-CLI shapes."""
    raise NotImplementedError


def build_resume(route: RouteDef, *, session: str | None, worktree: str,
                 prompt: str) -> list[str]:
    raise NotImplementedError


def probe(route: RouteDef) -> tuple[bool, str]:
    raise NotImplementedError


def capture_session(route: RouteDef, *, attempt_dir: Path, worktree: str,
                    launched_at: datetime) -> str | None:
    raise NotImplementedError


def extract_usage(route: RouteDef, attempt_dir: Path, log_text: str) -> Usage:
    raise NotImplementedError


def classify_log_tail(text: str) -> str | None:
    raise NotImplementedError

# P03 — route adapters: implementation report

**Status:** done

**Date:** 2026-07-15

## Summary

Implemented all six functions in `src/handoffctl/adapters.py`:
- `render_argv()` — placeholder substitution in list elements
- `build_dispatch()` — CLI-specific argv composition (claude, codex, opencode, reasonix, fake)
- `build_resume()` — resume command building from template
- `probe()` — route liveness checking with named builtins
- `capture_session()` — session ID recovery from files or discovery commands
- `extract_usage()` — usage extraction from logs (json, codex footer, deepseek regex)
- `classify_log_tail()` — blocked/limit classification in log tail

Created comprehensive test suite in `tests/test_adapters.py` (41 tests) covering all 8 oracles and edge cases.

## Oracle results

| Oracle | Tests | Status | Notes |
|--------|-------|--------|-------|
| 1. render_argv | 4 | pass | Basic substitution, missing keys, multiple placeholders, empty template |
| 2. build_dispatch | 10 | pass | All 5 CLI shapes (claude, codex, opencode, reasonix, fake); argv validation; prompt content checks |
| 3. Prompt guard | 2 | pass | argv_max enforcement; incremental-write hint appending |
| 4. build_resume | 3 | pass | Template substitution; empty template error; session required validation |
| 5. probe | 7 | pass | None → no-probe; true/false commands; timeout handling; named builtins (one-token-ping, session-limit-check) |
| 6. capture_session | 4 | pass | newest-jsonl (mtime after launched_at); no dir → None; session_discover by dir/title |
| 7. extract_usage | 5 | pass | output-format-json (with json fields); codex footer regex; deepseek regex; malformed handling; garbage → UNKNOWN |
| 8. classify_log_tail | 6 | pass | blocked at line start; limit phrases (case-insens); both → blocked wins; clean → None; last 200 lines only |

**Total: 41 tests, 0 failures**

## Files touched

- `src/handoffctl/adapters.py` — full implementation
- `tests/test_adapters.py` — test suite
- `tests/fixtures/fakecli/record-argv.sh.txt` — fakecli template
- `tests/fixtures/fakecli/emit.sh.txt` — fakecli template
- `tests/fixtures/fakecli/sleepy.sh.txt` — fakecli template
- `tests/fixtures/fakecli/version-record.sh.txt` — fakecli template

## Gate output (tail)

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/handoffctl2
configfile: pyproject.toml
plugins: hypothesis-6.156.6, cov-7.1.0, anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None
collected 41 items

tests/test_adapters.py .........................................         [100%]

============================== 41 passed in 0.19s ==============================
```

## Implementation notes

1. **render_argv**: Uses `str.format_map()` with missing-key error handling per contract.

2. **build_dispatch**: Each CLI has distinct argv shape; prompt is fixed and includes handoff_path, worktree, branch, gate_hint, receipt_path; dispatch_extra rendered with placeholders; prompt length guarded against argv_max; incremental-write hint appends batching sentence.

3. **build_resume**: Validates empty template; checks session presence when {session} in template; renders all three placeholders.

4. **probe**: Handles None → (True, 'no-probe'); named builtins 'one-token-ping' and 'session-limit-check' both invoke [cli, '--version']; subprocess run with 60s timeout; graceful error handling.

5. **capture_session**: newest-jsonl slug logic replaces '/' with '-' and keeps leading dash (per spec); searches files with mtime > launched_at (timezone-aware datetime); session_discover runs command, parses JSON array, matches 'dir' or 'title' fields.

6. **extract_usage**: Each source type has distinct regex/parsing:
   - output-format-json: Last line starting with '{' that json-parses and has usage/total_cost_usd fields
   - exec-output-footer: Regex for 'tokens used: NNN' (case-insens, handles commas)
   - session-json / run-log-deepseek-usage: Regex for input/output token fields (handles prompt|input, completion|output variants)
   - Malformed/missing → Usage(basis=UNKNOWN), never raises

7. **classify_log_tail**: Scans last 200 lines; BLOCKED at line start → 'blocked' (beats 'limit'); case-insens limit phrases → 'limit'; else None.

## Deviations / assumptions

None; all oracles implemented per contract.

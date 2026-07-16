# P03 — route adapters (dispatch argv, probes, resume, usage extraction)

> Tier: haiku · Depends-on: none · Read first: handoff/STANDING.md,
> src/nyxloom/adapters.py (docstring = normative), src/nyxloom/config.py
> (RouteDef), schemas/routes.example.toml (the shapes being encoded).

## Owned files
- `src/nyxloom/adapters.py`
- `tests/test_adapters.py`
- `tests/fixtures/fakecli/` (small executable `#!/bin/sh` scripts you create;
  chmod +x them in a test fixture, not at import time)

## Oracles
1. `render_argv(['a','{x}b'], {'x':'1'})` == `['a','1b']`; missing key →
   AdapterError naming the placeholder.
2. `build_dispatch` exact-argv assertions for each CLI. Construct RouteDefs
   inline and assert the FULL argv list equals the docstring shape, e.g.
   claude: `['claude','-p',prompt,'--output-format','json','--model','sonnet','--effort','high', *dispatch_extra]`;
   codex sandbox default 'workspace-write' when route.sandbox None; opencode
   variant flag only when set; reasonix `-dir`; fake. The returned prompt
   must contain: handoff path, worktree, branch, gate_hint, receipt_path.
3. Prompt-length guard: route.argv_max=120 and long paths → AdapterError.
   `prompt_hints=['incremental-write']` → prompt ends with the fixed
   batching sentence (assert a stable substring: '~80-line').
4. `build_resume` substitutes {session}/{worktree}/{prompt}; template
   containing '{session}' with session=None → AdapterError; empty resume
   template → AdapterError.
5. `probe`: argv ['true'] → (True, ...); ['false'] → (False, ...); a
   fakecli script sleeping past a monkeypatched 1s timeout → (False,
   'timeout...'); probe None → (True,'no-probe'); named builtins
   'one-token-ping'/'session-limit-check' execute `[cli,'--version']` —
   point route.cli at a fakecli script that records its argv to a file and
   assert.
6. `capture_session` 'newest-jsonl': monkeypatch HOME to tmp; create
   `~/.claude/projects/-tmp-wt/{old,new}.jsonl` with mtimes straddling
   launched_at; worktree '/tmp/wt' → returns 'new'. No dir → None.
   `session_discover`: fakecli printing a JSON list with two sessions →
   returns the id whose 'dir' matches worktree.
7. `extract_usage` per source with realistic sample logs (write them
   inline): 'output-format-json' log whose LAST json line has
   usage{input_tokens:100, output_tokens:50, cache_read_input_tokens:80}
   and total_cost_usd 0.0123 → Usage(ACTUAL, 100, 50, 80, 0.0123, 'USD');
   earlier malformed json lines must be skipped. codex footer
   'Tokens used: 12,345' → ESTIMATED tokens_out 12345. deepseek regex
   pair → ACTUAL tokens, cost None. Garbage log → Usage(UNKNOWN) and NO
   exception (negative: feed 10KB of random bytes decoded latin-1).
8. `classify_log_tail`: 'BLOCKED: cannot meet contract 3' at line start →
   'blocked'; 'rate limit exceeded' → 'limit'; both present → 'blocked';
   'the word blocked: midsentence' (not line-start, lowercase) → not
   'blocked'; clean log → None; only last 200 lines considered (seed an
   early BLOCKED: line then 300 clean lines → None).

## Guidance
- subprocess.run with capture_output, text=True, explicit timeout; never
  shell=True anywhere (security boundary).
- Keep per-CLI shapes in a dict of builder functions keyed by route.cli;
  unknown cli → AdapterError.
- fakecli scripts: `record-argv.sh` (echo "$@" > "$RECORD_FILE"),
  `emit.sh` (cat a named file), `sleepy.sh`. Make them via a fixture
  writing into tmp_path so no repo-committed +x bits are needed —
  tests/fixtures/fakecli/ may hold the TEMPLATES as .txt the fixture
  copies+chmods.

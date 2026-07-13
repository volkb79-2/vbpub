# P75 - Frontier review (pass #2)

Reviewer: Opus 4.8, fresh session, wave P67/P75/P76 · 2026-07-13
Verdict: **MERGED** (529ec02) after substantial review-fix. Two blockers.

## Headline

P75 is the package whose entire job is to catch breakage against a live daemon.
**Its central check could not fail.** Not "was weakly asserted" -- structurally
incapable of failing, on the exact condition it claimed to verify.

## Blockers (both fixed before merge)

### B1. Check 3 tested `isError`, which is never true for a groop error

`flagged-by-pass-1: no`

`tool_calls_ok &= not result.isError`. But the MCP SDK sets `isError=True` only
via `_make_error_result`, which fires when a tool **raises**
(`mcp/server/lowlevel/server.py:473-480`). groop's tools *return* their typed
failures as an ordinary `{"error": {"code": ...}}` payload (`mcp/server.py:81`
`_tool_error`). So `isError` is `False` for **every** typed groop error, and the
leg reported *"All 4 tools returned successful results"* even if the daemon
rejected every single call. `groop_overview` was additionally never folded into
`tool_calls_ok` at all.

Telling detail: checks 5 and 6 in the same file parse the payload for
`error.code` correctly. Only the central check did not.

### B2. Tool call order came from iterating a `set`

`flagged-by-pass-1: no`

`tools_to_call = list(_MCP_SMOKE_TOOLS)` where `_MCP_SMOKE_TOOLS` is a set
literal. Iteration order varies with `PYTHONHASHSEED`. I sampled six fresh
interpreters: in **four of six**, `groop_entity`/`groop_history` ran *before*
`groop_overview`, so they were called with an empty selector and got a typed
`invalid-selector` error -- which B1 then swallowed as a pass.

The handoff's contract that entity/history be "driven by an entity key taken from
the live `groop_overview` response" was being satisfied only by luck, and the two
bugs composed to hide each other.

## Also fixed

| # | Finding | pass-1? |
| --- | --- | --- |
| 3 | `server_alive = True  # session is still connected` was a hardcoded literal -- on the contract that losing the daemon must not take the MCP server down. It now re-drives the session; a dead server cannot answer `list_tools()`. | no |
| 4 | Readiness polled for the socket *path*. A unix socket exists on disk after `bind()` and before `listen()`, so a fast poll could hand a not-yet-listening socket to the session. Now connects. Also reports a daemon that died at startup instead of burning the full 30s. | no |
| 5 | A session that failed to start escaped as a **traceback**, leaving `--json` consumers with unparseable output on the single most likely live failure. Now a typed failing check. | no |
| 6 | Teardown unlinked the socket unconditionally: `mcp-smoke --socket /run/groop/groop.sock` would **delete the packaged system daemon's socket**. Only a socket this process created is removed now. The tmpdir was created unconditionally but removed only on the default path, leaking a directory per `--socket` run. | no |
| 7 | The handoff's **required** teardown test ("a check that raises mid-run still terminates both child processes ... it gets a real test that fails if the `finally` path is removed") was never written. | **yes** (SELFREVIEW F1/F2, but dismissed as "Low / mitigated" -- it is a stated Required-Test deliverable, not a nit) |
| 8 | `assert result.extra_installed is True or result.extra_installed is False` -- a tautology. | no |

## Verified as genuinely met (production path, not test seam)

- CLI shape, exit codes, and the rootless `mkdtemp` default socket.
- Skip-on-missing-extra: a real production branch, not a mock.
- Daemon teardown *does* run in a `finally` on every exit path (the contract was
  right; only its test was missing).
- The MCP server child is reaped by `stdio_client`'s own `finally`.
- The `invalid-selector` and `daemon-unavailable` checks genuinely parse the live
  payload and would fail on a broken resolver.

## Mutation evidence

The fixes are pinned, and I checked that they are:

- Delete the `finally` teardown -> the new teardown test fails.
- Revert Check 3 to the `isError` form -> **all 54 tests still passed.** The B1 fix
  had no oracle. I added a direct test of the payload-vs-`isError` semantics; the
  mutation now fails. A fix nothing can catch is not a fix.

## Pass-1 overlap

**1 of 8** (~13%). The self-review (78aed25) fixed only doc-accuracy issues (a
future-dated LOG entry, a placeholder venv path) and *did* raise the teardown
test-coverage gap -- but classified it "Low / mitigated" rather than as the
unmet Required-Test contract it was. It found neither blocker.

## Gates (re-run from `main`, package venv)

- Full suite from `main` after merge: **1328 passed, 1 failed** (the pre-existing
  `test_zst_without_zstandard_exits_2`, which also fails on unmodified `main`;
  carved as P82).
- ASCII clean; `git diff --check` clean.

## Note for the controller

This package merged, but it is the strongest evidence yet for §6's thesis. A
same-tier self-review re-read its own reasoning and confirmed it. The two blockers
were both of the form "the assertion cannot fail" -- which is invisible from
inside the session that wrote it, and obvious the moment someone mutates the code
and watches the suite stay green.

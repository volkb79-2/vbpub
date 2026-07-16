# P12 — ntfy inbound command listener (operator chat-ops) — REPORT

**Result: done**

Date: 2026-07-15.

## Gate output (verbatim tail)

Command: `cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_commands.py -q`

```
................                                                          [100%]
16 passed in 1.67s
```

Command (full suite, oracle 7): `cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest tests/ -q`

```
........................................................................ [ 23%]
........................................................................ [ 47%]
........................................................................ [ 70%]
........................................................................ [ 94%]
.................                                                        [100%]
305 passed in 55.59s
```

Also re-ran `tests/test_daemon.py` alone (18 passed) to confirm the daemon.py
wiring change doesn't disturb existing daemon lifecycle tests (none of them
set `cfg.notify.cmd_topic`, so `_start_cmd_listener()` is a no-op find-loop
for all of them).

## Per-oracle results

| # | Oracle | Test(s) | Result |
|---|--------|---------|--------|
| 1 | help lists all 5 verbs; garbage/shell-metachar commands rejected | `test_help_lists_all_five_verbs`, `test_garbage_command_is_rejected`, `test_shell_metacharacters_rejected_by_strict_regex` | pass (3/3) |
| 2 | pause/unpause: flag + PAUSE_SET/PAUSE_CLEARED event, actor `ntfy-cmd`, reply confirms state | `test_unpause_clears_flag_and_appends_cleared_event`, `test_pause_sets_flag_and_appends_set_event` | pass (2/2) |
| 3 | status reflects seeded statefiles (+ paused suffix, unknown/missing project) | `test_status_reflects_seeded_statefiles`, `test_status_reflects_paused_flag`, `test_status_unknown_project`, `test_status_missing_project_arg` | pass (4/4) |
| 4 | `nyxloomd-reply` tag -> `None` (loop guard) | `test_nyxloomd_reply_tag_is_ignored` | pass |
| 5 | Transport: reply POST (publisher token, `nyxloomd-reply` tag) + reconnect carrying `since` after connection ends | `test_transport_reply_and_reconnect_carries_since` | pass |
| 6 | Injection: prose after a valid verb -> unknown-command, hostile text absent from reply | `test_hostile_prose_after_verb_is_rejected`, `test_hostile_prose_as_project_arg_is_rejected` | pass (2/2) |
| 7 | Full suite green | `tests/ -q` (see above) | pass (305/305) |

Extra (not separately numbered, but part of the owned interface / help
contract): `test_digest_verb_uses_notify_digest`,
`test_digest_verb_truncates_to_max_chars`,
`test_digest_verb_empty_digest_has_fixed_reply` — 3/3 pass.

**Totals: 16 tests in test_commands.py, 16 pass, 0 fail.**

## Files touched

- `src/nyxloom/commands.py` (new) — `CommandListener` class: pure verb
  dispatch (`handle_message`) plus the long-poll transport
  (`start`/`stop`/`_run`/`_listen_once`/`_send_reply`).
- `tests/test_commands.py` (new) — 16 tests incl. a local fake-ntfy-server
  fixture class (`_FakeNtfyServer`, `ThreadingHTTPServer`-based) for the
  transport oracle.
- `src/nyxloom/daemon.py` — narrow ~18-line exception per the handoff:
  - import line: added `commands` to the existing `from . import ...` line.
  - `Daemon.__init__`: added `self._cmd_listener: commands.CommandListener | None = None`.
  - `Daemon.run()`: added `self._start_cmd_listener()` call after
    `self._start_http()`, and `self._stop_cmd_listener()` in the inner
    `finally` alongside `self._stop_http()`.
  - `Daemon.stop()`: added `self._stop_cmd_listener()`.
  - Two new small methods: `_start_cmd_listener` (scans `self.registry`,
    starts a `CommandListener(self.registry)` on the first project whose
    `cfg.notify.cmd_topic` is set and whose `cmd_token_env` is present in
    `os.environ`) and `_stop_cmd_listener` (stops + clears it).
  - Nothing else in daemon.py changed.

## Security model (implemented as specified)

- Read side uses `cfg.notify.cmd_token_env` (default `NTFY_CMD_TOKEN`) only;
  the reply side reuses `notify.send()` with `cfg.notify.token_env` (the
  normal write-only publisher token) against a `NotifyConfig` pointed at
  the SAME `cmd_topic` -- verified in the transport test (`Authorization:
  Bearer write-tok` on the POST vs `Bearer read-tok` on the GET).
- Verb parsing is the single anchored regex
  `^(help|status|pause|unpause|digest)( [a-z][a-z0-9-]{0,30})?$` (no
  `re.IGNORECASE`, no shell, no eval). Anything not fully matching falls
  through to the fixed `UNKNOWN_REPLY` constant.
- All reply text is built from: the fixed `HELP_TEXT`/`UNKNOWN_REPLY`
  templates, the regex-validated `[a-z0-9-]` project token, task-state
  enum values + counts (`storage.list_states`), or `notify.digest()`
  output (already typed-fields-only per its own P06 contract, capped to
  1500 chars here). No handoff prose, log text, or raw ntfy message
  content is ever echoed back.
- Every executed pause/unpause appends via `storage.append_event` (not
  `append_and_apply`, matching cli.py's own project-level pause/unpause
  branch, which has no per-task statefile to project onto) with
  `Actor(ActorKind.OPERATOR, "ntfy-cmd")`.

## Deviations / assumptions (for the reviewer)

1. **`status` output format** is my own design choice (not itself frozen):
   `"<project>: <n> QUEUED, <n> ACTIVE[, <n> OTHER_STATE ...] [(paused)]"`
   -- QUEUED and ACTIVE are always shown (even at 0) as the two
   operationally interesting buckets; any other state is appended only if
   its count is non-zero. The handoff's example (`"groop: 11 QUEUED, 0
   ACTIVE (paused)"`) is consistent with this but doesn't fully pin down
   the format for states beyond those two, or what "active-attempt count"
   (as opposed to the ACTIVE task-state count) was meant to mean --
   I read those as the same number (count of tasks in `TaskState.ACTIVE`)
   rather than a separate non-terminal-attempt tally, since the given
   example has only two numbers total. Flag this if a different
   status shape is wanted.
2. **Missing-project-argument reply**: the verb regex allows a bare verb
   with no project (`"status"`, `"pause"`, etc., since the trailing group
   is optional) for symmetry with `"help"`. Not explicitly covered by an
   oracle; I added a fixed `"missing project: send '<verb> <project>'"`
   reply (verb is regex-validated, so this stays within the
   typed-data-only rule) rather than treating a bare `"pause"` as
   `UNKNOWN_REPLY`. Worth confirming this is the desired UX.
3. **Test project id**: I used the `sample_project` fixture's `"demo"`
   project (matching this repo's existing test convention) rather than
   the handoff's illustrative `"groop"` name; the transport test registers
   its own throwaway `"cmdproj"` project directly (via a local
   `_register_cmd_project` helper) since it needs a real `[notify]`
   section with `ntfy_url` pointed at the fake server's ephemeral port,
   which the shared `sample_project` fixture's `project.toml` doesn't have.
4. **Reconnect backoff**: implemented as a doubling backoff from 1.0s to a
   60.0s cap (`CommandListener.BACKOFF_INITIAL`/`BACKOFF_MAX`), reset to
   1.0s after any iteration of `_listen_once` that returns without raising
   (including a clean long-poll end, i.e. the server just closing the
   connection normally -- which is what the transport test actually
   exercises, since forcing a truly abrupt mid-read socket error felt
   unnecessarily brittle for an equivalent externally-observable outcome:
   a second GET arrives with `since` carried over). No sleep/wait in
   `_run` or in the test exceeds ~1-5s and none use a single blocking call
   over 2s.
5. Did not modify `notify.py`, `cli.py`, or any frozen file. Read-only
   references were made to `notify.send`/`notify.digest` (P06) and to the
   pause/unpause flag + event semantics in `cli.py` (P10) purely to mirror
   their exact behavior, per the handoff's instruction to "reuse the CLI's
   exact semantics."

## Suggestions for the reviewer (not acted on)

- Consider whether `status`'s exact text format should be pinned down as
  part of a future frozen contract (e.g. in `config.py` or a shared
  helper) if other surfaces (web dashboard, CLI `status`) are expected to
  match it verbatim.
- The daemon only ever starts **one** `CommandListener` for the *first*
  project (by dict iteration order) with `cmd_topic` configured, per the
  handoff's "any registered project" wording -- if multi-project
  chat-ops with distinct cmd topics per project is ever wanted, this will
  need revisiting (current design assumes one shared ntfy command topic
  across all projects, consistent with the "operator sends `unpause
  groop`" phrasing in the handoff).

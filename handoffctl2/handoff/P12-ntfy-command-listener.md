# P12 — ntfy inbound command listener (operator chat-ops)

> Tier: sonnet · Date: 2026-07-15 · Requested by user ("could I send just
> `unpause` in the channel? do we have help?"). Read handoff/STANDING.md
> first — its rules bind you (STANDING's "frozen files" list still applies;
> daemon.py gets a narrow exception below).

## Objective

A daemon-side listener on an ntfy command topic so the operator can send
`unpause groop` (etc.) from the phone app. Outbound notifications stay
untouched. Security model is non-negotiable:

- The listener reads the topic via the READ-ONLY identity: token from env
  `cfg.notify.cmd_token_env` (default NTFY_CMD_TOKEN; already provisioned:
  ntfy user `cmd-reader`, read-only on `handoffctl-cmd`).
- Replies publish via the existing WRITE-ONLY publisher path
  (notify.send with the normal token env) to the SAME cmd topic, always
  tagged `handoffd-reply` — and the listener MUST ignore any message
  carrying that tag (loop prevention; ntfy does not expose sender identity).
- Verb allowlist, strict parse: `^(help|status|pause|unpause|digest)`
  `( [a-z][a-z0-9-]{0,30})?$` on the trimmed message body. Anything else ->
  reply "unknown command — send: help". NO other execution path; NO shell;
  NO free-text interpolation into replies (typed data + fixed templates
  only — the injection boundary applies to replies too).
- Every executed verb appends an audited event via storage.append_event
  (actor Actor(OPERATOR, "ntfy-cmd")): PAUSE_SET/PAUSE_CLEARED for
  pause/unpause (reuse the CLI's exact semantics — flag file + event),
  NEEDS_OPERATOR payload {"detail": "cmd-<verb>"} is NOT needed; for
  status/digest/help no state changes, no events.

## Owned files

- `src/handoffctl/commands.py` (new module — the listener + verb handlers)
- `tests/test_commands.py` (new)
- `src/handoffctl/daemon.py` — NARROW EXCEPTION to the frozen rule: you may
  add at most ~20 lines total: start a `commands.CommandListener` thread in
  run() when any registered project has cfg.notify.cmd_topic set AND the
  cmd token env is present; stop it in stop(). Nothing else in daemon.py
  may change.

## Interface (implement exactly)

```python
class CommandListener:
    def __init__(self, registry: dict[str, Path], poll_timeout: int = 60): ...
    def start(self) -> None: ...   # daemon thread
    def stop(self) -> None: ...
    def handle_message(self, text: str, tags: list[str]) -> str | None:
        """Pure verb dispatch: returns the reply text (or None for
        handoffd-reply-tagged input). Separated from transport for tests."""
```

Transport: long-poll `GET {ntfy_url}/{cmd_topic}/json?poll=0&since=<last-id>`
(streaming JSON lines; each line a message event; keep `since` = last seen
message id; `keepalive` events are skipped). urllib with the read token as
Authorization Bearer; reconnect with capped backoff (1s..60s) on any error;
never raises out of the thread.

Verbs (project arg required where shown; unknown project -> reply
"unknown project: <name>" using only the validated `[a-z0-9-]` string):
- `help` -> fixed multi-line list of verbs with one-line descriptions.
- `status <project>` -> per-state counts + active-attempt count (from
  storage.list_states; e.g. "groop: 11 QUEUED, 0 ACTIVE (paused)").
- `pause <project>` / `unpause <project>` -> same effect as the CLI verbs
  (flag file + PAUSE_SET/PAUSE_CLEARED event, actor ntfy-cmd), reply
  confirms new state.
- `digest <project>` -> notify.digest(cfg, project, 0) capped to 1500 chars.

## Oracles (each a test; fake ntfy server = local http.server streaming
prepared JSON lines, then hanging until closed)

1. handle_message('help', []) returns text listing all five verbs;
   handle_message('rm -rf /', []) returns the unknown-command reply;
   handle_message('unpause; rm x', []) -> unknown-command (strict regex).
2. handle_message('unpause groop', []) on a registered tmp project with the
   pause flag set: flag removed, PAUSE_CLEARED event with actor id
   'ntfy-cmd', reply contains 'unpaused'. pause symmetric.
3. handle_message('status groop', []) reflects seeded statefiles.
4. handle_message(anything, ['handoffd-reply']) -> None (loop guard).
5. Transport: fake server streams one command message then blocks; listener
   thread issues the reply POST to the cmd topic (capture it: assert
   Authorization uses the PUBLISHER token env, tag handoffd-reply present)
   and advances `since`; server connection drop -> reconnect (second
   request arrives with backoff, assert since carried over).
6. Injection: a command message whose body embeds hostile prose after a
   valid verb ('help EVILPROSE') -> parsed as INVALID (regex has no such
   form) -> unknown-command reply; assert 'EVILPROSE' absent from reply.
7. Full suite still green: run the complete gate
   (`.../python -m pytest tests/ -q` from handoffctl2) and report the tail.

## Rules

STANDING.md deliverables apply: REPORT at handoff/reports/P12-REPORT.md,
receipt-only final message. Do not commit. Do not touch notify.py, cli.py,
or any frozen file. If the daemon wiring cannot stay within ~20 lines,
BLOCKED — do not restructure daemon.py.

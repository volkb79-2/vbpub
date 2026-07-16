# P20 — Daemon attach mode for non-root clients

**Cut:** v1.5/v2 foundation. **Depends:** P16. Branch:
`feat/topos-p20-daemon-attach`. Follow `topos/README.md` workflow protocol.

## Goal

Let a non-root `topos` client consume frames from the read-only daemon broker
instead of collecting directly from cgroup/proc/sysfs. This is the next practical
step toward "full reads for non-root users" without adding mutation APIs or BPF.

## Scope — in

1. Add a daemon client module, preferably `topos/src/topos/daemon/client.py`,
   that:
   - connects to a Unix socket;
   - sends one JSON request per connection using the P16 protocol;
   - parses JSONL responses;
   - validates `type=frame` payloads with `frame_from_jsonable`;
   - raises clear client errors for socket failure, malformed JSON, protocol
     errors, and missing `end` responses.
2. Add top-level CLI support:
   - `topos --attach SOCKET` starts the existing TUI over daemon frames;
   - `topos --attach SOCKET --once --json` prints one daemon frame as canonical
     JSON for deterministic tests and shell use;
   - `--record FILE` may record attached frames if this fits cleanly, but it is
     optional for this slice;
   - do not combine `--attach` with `--replay` or direct live collection flags in
     ambiguous ways.
3. Preserve the same UI model as live/replay:
   - attached frames must flow through the same `Frame` contract;
   - `--ui-smoke` should work with `--attach`;
   - no Textual imports outside `src/topos/ui/`.
4. Add focused tests:
   - daemon client current/stream parsing using a local fixture socket;
   - CLI `--attach --once --json`;
   - malformed/error response handling;
   - no file-read/command/mutation protocol expansion.
5. Update docs:
   - `docs/DAEMON.md` with attach usage and current limitations;
   - `README.md` quickstart/canonical work package status if needed;
   - `docs/STATUS.md` and/or `docs/ROADMAP.md` after implementation.

## Scope — out

- Production systemd unit/package installation.
- Authentication beyond Unix socket permissions.
- Any daemon request that selects arbitrary host paths, commands, PIDs, Docker
  actions, systemd actions, BPF state, or DAMON mutations.
- Exact BPF provider work.
- Web UI.

## Design notes

- Keep the daemon protocol narrow: `{"op":"current"}` and
  `{"op":"stream","limit":N}` only.
- For a continuous attached TUI, it is acceptable for the client generator to
  repeatedly request bounded `stream` batches. Keep defaults conservative and
  document that P16 is still a broker spike, not a production service.
- Use existing frame serialization/deserialization helpers. Do not introduce a
  second frame schema.
- Error messages should be actionable for non-root users, e.g. "cannot connect
  to /run/topos/topos.sock" or "daemon returned unsupported operation".

## Acceptance

- `topos --attach SOCKET --once --json` returns a canonical frame from a test
  broker socket.
- `topos --attach SOCKET --ui-smoke` renders the same UI smoke path over daemon
  frames.
- Full tests pass.
- `py_compile` passes for new/changed Python files.
- No live/default behavior changes when `--attach` is absent.
- No protocol expansion beyond read-only frame retrieval.

## Resumability

Create and keep current:

- `topos/handoff/reports/P20-LOG.md`
- `topos/handoff/reports/P20-REPORT.md`

Use `topos/handoff/AGENT-LOG-TEMPLATE.md`. Record the actual worktree path,
branch, changed files, commands, validation output, decisions, blockers, and
known gaps.

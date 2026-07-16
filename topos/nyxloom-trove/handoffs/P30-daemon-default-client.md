# P30 - Daemon Default Client UX

**Cut:** v1.5/v2 daemon usability. **Depends:** P16, P20, P22, P25.
Branch: `feat/topos-p30-daemon-default-client`. Follow `topos/README.md`
workflow protocol exactly.

## Goal

Make the root-daemon/non-root-client path easier to use without adding any new
privileged behavior. Operators should be able to use the packaged default socket
without repeating `/run/topos/topos.sock`, and scripts should have a clear
read-only command for retrieving one daemon frame.

## Required Context

- `topos/README.md` workflow protocol.
- `topos/docs/DAEMON.md`, `topos/docs/STATUS.md`, `topos/docs/ROADMAP.md`.
- `topos/src/topos/cli.py`.
- `topos/src/topos/daemon/client.py`, `topos/src/topos/daemon/deploy.py`.
- `topos/tests/test_attach_cli.py`, `topos/tests/test_daemon_client.py`,
  `topos/tests/test_daemon_deploy.py`.
- `topos/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Let `--attach` use the packaged default daemon socket when no explicit path is
   supplied:

```bash
topos --attach
topos --attach --once --json
topos --attach --ui-smoke
```

   Keep existing `topos --attach /custom/path.sock ...` behavior.
2. Add a read-only daemon subcommand for one-frame retrieval, for example:

```bash
topos daemon current --json
topos daemon current --socket /custom/path.sock --pretty-json
```

   It should use `DEFAULT_DAEMON_SOCKET` by default and print canonical frame
   JSON. It must not fall back to live collection if the socket is missing.
3. Preserve all existing attach validation:
   - attach still rejects `--replay`, `--cgroup-root`, replay pacing flags, and
     unsupported record combinations;
   - attach JSON still requires `--once`;
   - daemon errors should be printed to stderr with exit code 2.
4. Add focused tests using the fixture broker:
   - argparse/default behavior for `--attach` with no value;
   - `--attach /custom.sock --once --json` still works;
   - `topos daemon current --socket TMP --json` returns canonical frame JSON;
   - pretty JSON works;
   - missing socket or protocol errors return non-zero and do not collect live
     data;
   - existing ambiguous-combination tests still pass.
5. Update docs:
   - `README.md` P30 row should become Done after implementation;
   - `docs/DAEMON.md` should document default-socket attach and
     `topos daemon current`;
   - `docs/STATUS.md` and `docs/ROADMAP.md` should reflect the improved daemon
     client UX while keeping production install execution and live BPF lifecycle
     as future work.

## Scope - Out

- No daemon install execution.
- No systemd start/stop/restart.
- No socket discovery beyond `DEFAULT_DAEMON_SOCKET`.
- No protocol changes.
- No daemon-side authorization changes.
- No file/log inspection integration.
- No BPF, DAMON, Docker, or systemd mutation.

## Design Notes

- This should be a CLI usability layer over existing `DaemonClient` and
  `DEFAULT_DAEMON_SOCKET`.
- Prefer a narrow helper if it removes duplicated "current frame then JSON"
  logic between attach and `daemon current`.
- Keep output canonical and stable by reusing `frame_to_jsonable()` and the
  existing JSON printer helper.
- Be careful with `argparse`: `--attach --once --json` must parse as "attach to
  default socket", not as a missing-value error.

## Acceptance

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_attach_cli.py topos/tests/test_daemon_client.py topos/tests/test_daemon_deploy.py -q
PYTHONPATH=topos/src python3 -m pytest topos/tests -q
PYTHONPATH=topos/src python3 -m py_compile topos/src/topos/cli.py
```

Also run one CLI smoke against a fixture broker for `topos daemon current`.

## Handoff Requirements

- Keep `topos/handoff/reports/P30-LOG.md` current using
  `topos/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P30-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract changes.
- Commit the feature branch with a focused message.

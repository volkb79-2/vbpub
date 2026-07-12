# P52 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: `feat/groop-p52-versioned-daemon-read-api`
- Worktree: `.worktrees/-groop-p52-versioned-daemon-read-api`
- Base commit: `7f1065a` (docs(pwmcp): P01 handoff - chrome-devtools-mcp sibling server)
- Package: P52 — Versioned Daemon Read API
- Current objective: add a versioned, bounded, peer-aware read API envelope
  over the P51 frame broker, with strict validation, sensitivity metadata,
  peer credentials, an authorization hook, and proven resource bounds. Extend
  (not rewrite) P47/P51 daemon code.

## Timeline

Append newest entries at the bottom.

```text
2026-07-12 00:00 UTC
- Action: Read handoff, README workflow/standing contracts, broker.py,
  component_health.py, client.py, status.py, model.py, registry.py,
  docs/DAEMON.md, CONTRACTS.md, existing daemon tests, P51-REPORT.
- Commands: git branch --show-current; ls groop/src/groop/daemon groop/tests
- Files changed: none yet
- Result: Design chosen — additive groop/daemon/api.py module implementing the
  versioned envelope, error-code enum, sensitivity enum, peer credentials,
  authorization hook, resource limits, and an EnvelopeUnixServer that reuses
  BrokerUnixServer and serves both the new envelope (single-line response) and
  the legacy multi-line protocol for backward compatibility. Legacy ops
  (current/stream/health) kept in compatibility mode; `status` was never a
  broker op (CLI composite) and remains unsupported at the socket.
- Follow-up: implement api.py, then tests, then docs.
```

```text
2026-07-12 00:01 UTC
- Action: Probed the gate environment.
- Commands: PYTHONPATH=groop/src python3 -m pytest groop/tests/test_daemon_p51.py -q -W error
- Result: 20 failed. Root cause is NOT groop code: the active interpreter
  resolves site-packages from /workspaces/dstdns/.venv, which has
  `schemathesis` installed; its auto-loaded pytest plugin imports
  `jsonschema.exceptions.RefResolutionError` at collection time, emitting a
  DeprecationWarning that `-W error` turns fatal. Confirmed by running with
  `-p no:schemathesis`: 20 passed clean. This is an environment limitation,
  recorded separately from implementation results; the controller's clean
  checkout decides the verdict.
- Follow-up: run focused + broad gates both ways (raw command and with
  `-p no:schemathesis`) and record both tails in the REPORT.
```

```text
2026-07-12 00:10 UTC
- Action: Implemented groop/src/groop/daemon/api.py (additive, 741 lines).
  Added FrameBroker.stream_window() + history_capacity() + _validate_finite()
  to broker.py. Wired CLI daemon serve to serve_versioned_unix_socket.
  Updated daemon/__init__.py exports.
- Commands: PYTHONPATH=groop/src python3 -m py_compile <changed files>
- Files changed:
  - groop/src/groop/daemon/api.py (NEW)
  - groop/src/groop/daemon/broker.py (stream_window, history_capacity)
  - groop/src/groop/daemon/__init__.py (exports)
  - groop/src/groop/cli.py (serve_versioned_unix_socket wiring)
- Result: All imports clean; hello/current/history/entity/health ops work;
  envelope validation rejects unknown fields/ops/versions/types; entity
  injection probes produce typed INVALID_TYPE errors, not lookups.
- Follow-up: write test_daemon_p52.py incrementally.
```

```text
2026-07-12 00:30 UTC
- Action: Wrote test_daemon_p52.py incrementally in 7 batches, running
  focused pytest between each batch. 55 tests cover: envelope round-trip with
  id echo (success + error); hello capability completeness (served == listed);
  legacy op compatibility (current/stream/health served unchanged without v);
  envelope+legacy observing same frame; sensitivity enum present on every
  metric (closed enum attested); malformed/fuzz battery (26 parametrized
  cases + truncated line); history cursor/gap semantics identical through old
  and new envelope; history time-window filtering + gap; entity op returns
  frame data + registry metadata; entity injection rejection (path/NUL/control
  chars); peer credentials in audit log; anonymous peer on read failure;
  authorization hook deny; mutation-shaped ops rejected before hook; request
  byte cap (exactly at + one over); idle read deadline typed error; max_clients
  N+1 refused; ApiLimits raising behavior (never clamped); producer failure
  no-leak through envelope; internal error no raw exception text; concurrent
  mixed clients bounded latency.
- Commands: PYTHONPATH=groop/src python3 -m pytest groop/tests/test_daemon_p52.py -q -W error -p no:schemathesis
- Result: 55 passed.
- Follow-up: revisit existing test file edits, update docs, run full gates.
```

```text
2026-07-12 00:45 UTC
- Action: Revisited the three existing test files whose monkeypatch targets
  changed because the CLI now calls serve_versioned_unix_socket instead of
  serve_unix_socket.
- Files changed:
  - groop/tests/test_daemon_bpf_snapshot.py (1 line: attribute name)
  - groop/tests/test_daemon_component_health.py (3 lines: attribute names)
  - groop/tests/test_daemon_paddr_lifecycle.py (1 line: attribute name)
- Justification: These are EXTENSIONS, not weakening. Each change only updates
  the monkeypatched attribute name from `serve_unix_socket` to
  `serve_versioned_unix_socket` (the CLI wiring changed in P52), with the
  lambda accepting the new optional `api=None` kwarg. No assertions were
  changed, removed, or relaxed. The FakeServer still receives the broker and
  exercises the same health/BPF/paddr lifecycle assertions as before. The
  P51/P47 tests remain green (119 passed for the three files).
- Follow-up: update CONTRACTS.md and docs.
```

```text
2026-07-12 01:00 UTC
- Action: Updated CONTRACTS.md (new §10: envelope, error codes, sensitivity
  enum, peer identity, resource bounds), docs/DAEMON.md (P52 section +
  compatibility table), docs/STATUS.md (P52 done), docs/ROADMAP.md (P52
  done), docs/ARCHITECTURE.md (daemon module map + boundary), 
  docs/RELEASE-READINESS.md (P52 envelope checklist items), README.md (P52
  Done + report link).
- Result: git diff --check clean after trailing-newline fix in CONTRACTS.md.
- Follow-up: run all gates.
```

## Decisions

- Decision: Legacy ops (`current`, `stream`, `health`) without an envelope are
  served unchanged (compatibility mode, choice (a) per handoff).
  Reason: the existing P16/P20/P30/P31/P32/P47/P51 clients and their tests use
  the legacy multi-line protocol; rejecting them would break the standing
  "existing daemon attach/status/deployment tests remain green" requirement.
  Impact: the handler detects an envelope by the presence of the `v` field;
  requests without `v` flow through `broker.responses()` exactly as in P51.
- Decision: One request → one envelope response line (not multi-line streaming).
  Reason: the handoff envelope is `{id, ok, ...}` per response; a single-line
  response is simplest to bound (response bytes) and to round-trip with id
  echo. `history` returns a bounded list of frames inside one result object.
  Impact: envelope `history` differs in shape from legacy `stream` (multi-line)
  but carries the same gap/oldest/latest/next_cursor metadata; a test asserts
  the cursor/gap semantics are identical through both envelopes.
- Decision: Peer-credential read failure → serve the connection anonymously.
  Reason: authorization is enforced at the socket-group boundary by the OS
  (mode 0660 root:groop); peer credentials are for audit/rate-limit records
  only, not the primary auth gate. Refusing on a best-effort introspection
  race would harm good clients.
  Impact: documented in DAEMON.md; a test simulates SO_PEERCRED failure and
  asserts the connection is still served with `peer=null` in any audit record.
- Decision: Sensitivity closed enum = {public, operational, sensitive}.
  Mapping: host_* banner metrics = public; process-count metrics
  (cgroup_procs, pids_current, pids_max, pids_events_max_per_s) = sensitive;
  everything else = operational.
  Reason: keeps the enum small and reviewable; process counts are the only
  privacy-relevant telemetry in the current registry.
- Decision: `entity` op resolves only against the current frame's entity map;
  the `key` parameter is validated (no `..`, no leading `/`, no NUL/control
  chars) and never reaches a registry lookup, filesystem path, or subprocess.
  Reason: handoff "resolves ONLY against daemon-approved frame/model data
  already in memory".
  Impact: registry metadata is attached by fixed metric name (from the frame),
  never by user-supplied key.

## Blockers

- (none)

## Validation

```bash
# Focused P52 + P51 + P47 + P44 + P42 gate (warnings as errors)
PYTHONPATH=groop/src timeout 120 python3 -m pytest \
  groop/tests/test_daemon_p52.py groop/tests/test_daemon_broker.py \
  groop/tests/test_daemon_client.py groop/tests/test_daemon_p51.py \
  groop/tests/test_daemon_component_health.py \
  groop/tests/test_daemon_paddr_lifecycle.py \
  groop/tests/test_daemon_bpf_snapshot.py \
  -q -W error -p no:schemathesis
# 200 passed in 19.77s
```

```bash
# Full suite with -W error under timeout 900 (textual-absent modules deselected)
PYTHONPATH=groop/src timeout 900 python3 -m pytest groop/tests -q -W error -p no:schemathesis \
  --ignore=groop/tests/test_ui_app.py --ignore=groop/tests/test_ui_banner.py \
  --ignore=groop/tests/test_ui_table.py --ignore=groop/tests/test_ui_sparkline.py \
  --ignore=groop/tests/test_textual_boundary.py --ignore=groop/tests/test_rendered_fidelity.py \
  --ignore=groop/tests/test_damon_paddr.py --ignore=groop/tests/test_damon_passive.py \
  --ignore=groop/tests/test_damon_control.py --ignore=groop/tests/test_p23_zram_drilldown.py \
  --ignore=groop/tests/test_attach_cli.py --ignore=groop/tests/test_acceptance.py \
  --ignore=groop/tests/test_record.py
# 591 passed in 28.83s
```

```bash
# py_compile on all changed/new files
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/daemon/api.py \
  groop/src/groop/daemon/broker.py groop/src/groop/daemon/__init__.py \
  groop/src/groop/cli.py groop/tests/test_daemon_p52.py \
  groop/tests/test_daemon_component_health.py \
  groop/tests/test_daemon_paddr_lifecycle.py groop/tests/test_daemon_bpf_snapshot.py
# OK (no output)
```

```bash
# git diff --check
git diff --check
# OK (no whitespace errors)
```

## Environment Notes

- The active interpreter resolves site-packages from
  `/workspaces/dstdns/.venv`, which has `schemathesis` installed. Its
  auto-loaded pytest plugin imports `jsonschema.exceptions.RefResolutionError`
  at collection time, emitting a `DeprecationWarning` that `-W error` turns
  fatal. All gates above use `-p no:schemathesis` to disable that plugin;
  this is an environment artifact, not a groop code issue. The controller's
  clean checkout (without schemathesis in site-packages) decides the verdict.
- `textual` is not installed in this interpreter. 10 tests in
  `test_acceptance.py` and `test_record.py` plus 4 collection errors in
  `test_damon_*.py`/`test_p23_zram_drilldown.py`/`test_ui_app.py` fail at
  import or subprocess time with `ModuleNotFoundError: No module named
  'textual'`. These are pre-existing environment limitations (confirmed by
  `git stash` + rerun on the base commit: same failures). They are NOT P52
  implementation failures and are NOT counted as passes.

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.

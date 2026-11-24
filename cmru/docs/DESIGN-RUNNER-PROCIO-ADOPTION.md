# Design: CMRU adopts `cli_extended.procio` (P7)

Scope: KI-178 items 3 and 4 in CMRU, KI-180, and the routing part of KI-189. One package, one
merge. Other consumers adopt the same released helper with their owners: CIU-220,
MDT-104, NL-83.

## Decisions

1. **P7 is one package and one merge.** The former P7.1-P7.3 split existed only to stage
   prerequisites, and its atomicity rule already forced a single merge.
2. **No Run-Gate lane-cap fact, no `capped_container` allowlist (P7.0a and RG-293 dropped).**
   Containment is a local fact. `estate_test_policy`'s `requires_contained_cgroup` (TP-13) checks
   it without coupling cmru to lane names, Assay declarations or image paths.
3. **The image prerequisite is a pin bump plus an import smoke (P-core-05).** `fetch-cli-extended.py`
   already enforces the wheel SHA-256, so no further evidence chain is needed.
4. **Order: release cli-extended 0.8.0, build the tester image with it, then P7.** The cmru floor
   `cli-extended>=0.8.0` must never name a wheel the gate image lacks.
5. **runner.py's private procio copy (~`runner.py:384-620`) is deleted.** Steps call
   `procio.run_streaming(..., progress_paths=())`: one implementation, and no progress path,
   because the step log is written only from pipe bytes, which already count as progress.
6. **`[runner] no_progress_timeout_seconds`: default 1800, finite, no off value.** A step that
   is silent for ever must fail loudly. The value is an inactivity bound, not a runtime limit.
7. **Template revision stays 5.** The keys are optional; a revision bump would force
   estate-wide marker churn through `cmru standards`.
8. **D-178: cleanup keeps its own contract and gains only the watchdog.** Passing the full
   parsed clean config would newly honour controls that cleanup has always ignored.
9. **D-179: the child's exit status is authoritative; malformed log bytes never fail a step.**
   Streamed output is diagnostic text. Failing a completed build over undecodable bytes is less
   reliable than writing `U+FFFD`.
10. **`run_process(capture=True)` delegates to `procio.run_capture` and gains a `stdin`
    parameter.** Captured probes get a bounded drain and group-reaping timeouts. `stdin=DEVNULL`
    becomes expressible (KI-186's P6b gap).
11. **`tester_gate.py` is frozen.** It only switches its import so that it keeps importing (to
    `cli_extended.procio.terminate_as_exit` or a thin wrapper). It is a retirement candidate
    (KI-191).
12. **P7 does not edit `cmru/assay.toml`.** No lane env keys are needed once the lane-cap fact
    is gone, so KI-141 part 7 no longer constrains the order. If an edit proves unavoidable,
    ask T3 first.
13. **P7 closes KI-180, KI-178 items 3-4, KI-186 as re-scoped and KI-189's routing part.**
    KI-180 is fixed by procio: its signal scope defers signals across fork/`Popen` and closes
    the pipes in a `finally`.

## Contract

### Configuration and precedence

```toml
[runner]
no_progress_timeout_seconds = 1800

[steps.build]
no_progress_timeout_seconds = 3600
```

- The `[runner]` table and the per-step key are both optional. An absent project default
  resolves to 1800 s. A step override wins when present and otherwise inherits the resolved
  project default.
- The strict grammar rejects unknown `[runner]` keys. Both values must be numeric, not bool,
  finite and greater than 0. Any other value exits 2 with one of these messages (`repr` of the
  value):
  - `runner.no_progress_timeout_seconds must be a finite number of seconds greater than 0; got <value>`
  - `steps.<step>.no_progress_timeout_seconds must be a finite number of seconds greater than 0; got <value>`
- `StepConfig` carries the resolved value and its winning source key, which is exactly one of
  `steps.<step>.no_progress_timeout_seconds`, `runner.no_progress_timeout_seconds` or
  `default runner.no_progress_timeout_seconds`. Every parsed step resolves the same way,
  whichever cmru verb runs it.
- Direct `run_command` calls without parsed step metadata use one exported CMRU constant,
  1800 s. `_execute_step` always passes the parsed value and its source.

### Watchdog expiry

procio captures the process tree before any signal, then sends TERM, and KILL after
`GROUP_TERM_GRACE_SECONDS`. CMRU prints and logs the diagnostic even in quiet mode, naming the
winning source key and showing the pre-signal tree. The step fails through the existing
`StepFailed(returncode=1)` path; a negative signal status is never the CLI exit code.

### Drain and holder WARN

The output read ends `OUTPUT_DRAIN_SECONDS` after the child exits, even when a descendant
still holds the pipe, and the child's own exit code is kept. procio returns the warning as an
unprefixed body; CMRU renders it exactly once to stdout as `[WARN] {body}` and exactly once to
the step log as `[cmru] {body}`. It is printed in quiet mode too, never written to the
aggregate mirror, and a visible holder PID appears in the body.

### Required behavior comparison

Preserved:
- argv, cwd and the environment are derived by CMRU and passed through unchanged. This covers
  bake arguments, the scoped project/step/protected environment and `PYTHONUNBUFFERED=1`.
- stdout and stderr are merged. The step log, the aggregate mirror, quiet excerpts and success
  evidence behave as before.
- A nonzero exit maps to the existing `CalledProcessError`/`StepFailed` path. A missing
  executable still raises the plain spawn error.
- Each child gets its own session and group. Caller SIGINT, SIGTERM and SIGHUP reap the whole
  group.

Changed: decoding (D-179), the new watchdog, and the bounded drain in place of the EOF wait.

### D-178: preserve cleanup's existing behavior

- `load_config` parses `steps.clean` with the same `[runner]` default and step override as
  every other step.
- `cleanup_project_step` builds the existing `_build_step_config("clean", ...)`. It then copies
  only the resolved timeout and its source key from `project.runner_steps["clean"]`.
- Everything else stays cleanup-owned:
  - the command list and the original absolute cwd;
  - `quiet=True`;
  - no bake, login, `env_command`, `required_env`, `step_env`, `clean_dirs` or registries;
  - `repo_root/logs` as the log root;
  - `CMRU_VERSION`, the selected publisher token and the protected cleanup environment.
- A `steps.clean` override reaches the watchdog, and its diagnostic names
  `steps.clean.no_progress_timeout_seconds`.

### D-179: replacement decoding for streamed output

- Streamed step output uses procio's locale decoding with universal newlines and
  `errors="replace"`.
- Every line goes to the step log and the aggregate mirror, including `U+FFFD` and an
  unterminated tail. No strict streaming mode is added.
- Captured output (`run_capture`, and with it `run_process(capture=True)`) stays strict, which
  is the `subprocess.run(text=True)` contract.

### procio contract P7 relies on

- procio returns warnings as data and never prints.
- The watchdog has no off value and no library default; the caller always passes it.
- `reap_process_group` skips only a known-empty group after its leader is reaped. A PGID-reuse
  race between that check and `killpg` is not prevented.
- A descendant that escapes with `setsid` before the snapshot may be missed. Escaped PIDs are
  reported when discoverable and are not killed. CMRU prints the report as returned.
- procio is Linux-only (`/proc`), as CMRU's gate environment already is.

### Dependency, tests, docs

- Both `cli-extended` requirements in `cmru/pyproject.toml`, including the `interactive`
  extra, rise to `>=0.8.0`.
- The floor oracles rise with them: `tools/installed_wheel_smoke.py`,
  `tests/test_packaging_cli_extended.py`, `tests/test_release_gate_interpreter.py`, and any
  other assertion of the supported floor.
- Process-signalling tests carry `@pytest.mark.requires_contained_cgroup` (TP-13). TP-13 means
  a finite effective `memory.max` and `pids.max` and a private PID namespace. The tests use
  pipe or pidfd handshakes and a fake clock, never sleeps. cmru's pytest ini sets
  `estate_policy = fail` and `required_plugins = estate-test-policy`, with the plugin loaded
  from source.
- The public documentation states the default, precedence, finite-only rule, source
  diagnostics, D-178, D-179 and revision-5 compatibility. That covers SPEC S3.2 and S3.4a,
  README, DESIGN-GUIDE, CONSUMERS, and the repository's own `cmru.toml` `[runner]` example.
  The examples parse with the shipped loader.

## Acceptance (behavioural)

These run under the marker, in the gate container:
- A fake-clock watchdog expiry gives exit 1.
- A pipe-holding grandchild returns within the drain period with a holder WARN.
- Malformed UTF-8 never fails a step whose child exited 0 (D-179).
- `cleanup --policy` honours the per-step override.
- A captured probe with a lingering grandchild times out and reaps its group.

## Not in this package

- Run-Gate lane facts, an allowlist, `cmru/assay.toml` edits, a template revision bump, or a
  `[runner]` block in the scaffold templates.
- `tester-gate` changes beyond keeping it importable (retirement is KI-191).
- The remaining `subprocess.run` sites and the private `_git` helpers (KI-189 rest): on
  touch. P6c/P6d are retired.
- An explicit env mapping instead of the `os.environ` mutation (KI-200).
- The ciu, mdt-cockpit and nyxloom adoptions (CIU-220, MDT-104, NL-83).

# Design: CMRU adopts `cli_extended.procio`

Status: **closed implementation plan** after the Codex Sol xhigh exact-head review of
`47e11f872751299056a4f1b854f62c6e81ec60ca`. This note covers KI-178 items 3 and 4 in CMRU
only. It supersedes the prior closures through
`36992832cd36d07698a2788ec488ad5159f6fbcc` and the original closure at
`c36f25cddccf6c417eee48763fa0f3bd003836e0`. It does not implement P7.0-P7.3, and it does
not assign adoption work in CIU, mdt-cockpit, or nyxloom.

## Decision

CMRU will route every declared project-step command through
`cli_extended.procio.run_streaming`. CMRU retains its logging, quiet-mode, error-excerpt,
evidence, and ordinary step-failure contracts in the `on_line` adapter. For an abandoned
output pipe, CMRU keeps its exact presentation: procio's unprefixed warning body is printed
once to stdout as `[WARN] {body}\n`, written once to the step log as `[cmru] {body}\n`, never
written to the aggregate mirror, and printed even when `quiet=True`. Every command receives a
finite no-progress limit resolved from project configuration; the default is 1,800 seconds.
Watchdog expiry is a runtime step failure with exit 1.

There is one deliberate output change under D-179: streamed step output uses
`run_streaming`'s locale-based universal-newline decoding with replacement characters for
undecodable bytes. Malformed output therefore no longer raises `UnicodeDecodeError` or
overrides an ordinarily completed child's exit status. A nonzero child exit remains the
existing step failure, and captured probes keep their strict decoding because they are
outside this migration.

CMRU will not migrate short captured probes, interactive commands, registry login, or other
non-step subprocess calls in this package. The other KI-178 consumers remain with their
owners: `mdt-cockpit/build-push.py` (MDT-104), `ciu/src/ciu/engine.py` (CIU-220), and nyxloom
(NL-83). They adopt the same released helper independently.

## Review closure

All ten confirmed findings from the original design review retain their settled
disposition:

| # | Confirmed finding | Closure in this plan |
|---|---|---|
| 1 | `steps.clean` is a direct `execute_step` path outside `run_project_step`. | P7.1 includes `cli.py`, parses clean through the shared `[runner]` grammar, and explicitly builds D-178's cleanup-specific execution config below. |
| 2 | P7.2 could import procio while cmru metadata still allowed an older cli-extended wheel. | The first procio import and the floor increase to `cli-extended>=0.7.0` ship in the same P7.2 package, after P7.0b's selected-image proof. |
| 3 | `tester_gate.py` still imports CMRU's private `runner._terminate_as_exit`. | P7.2 imports public `cli_extended.procio.terminate_as_exit`; no parallel CMRU signal implementation remains. |
| 4 | A template revision bump has several owners and an estate-wide migration cost. | Revision 5 stays. No marker, scaffold, template, or `standards.py` revision change is needed for optional controls. |
| 5 | Dependency-floor assertions exist beyond the two package requirements. | P7.2 updates both requirements and every floor-specific smoke/test oracle named below; only deliberately old generic-parser fixtures may retain 0.3.0. |
| 6 | The fake-clock watchdog choreography did not account for procio reporting progress on the next pump tick. | The deterministic sequence below arms on the ready line, consumes `progressed=True` on the next tick, and advances past the limit on the following tick. |
| 7 | `capped_container` was proposed without collection-time enforcement. | P7.2 registers it and validates the exact Run-Gate fact, allowed lane/environment pair, interpreter, and effective finite cgroup-v2 limits before setup or spawn. |
| 8 | `reap_process_group` cannot promise PGID-reuse safety across its check and `killpg`. | The claim is narrowed to skipping a known-empty group after the leader is reaped; the check-to-signal reuse race remains documented. |
| 9 | The selected tester image source pins cli-extended 0.5.0 and its old digest. | P7.0b updates the wheel URL, digest, minimum version, CMRU pin oracle, image selection, and installed-image proof for 0.7.0. |
| 10 | Direct `run_command` callers do not provide the optional `log_path`. | `run_command` uses the finite 1,800-second CMRU policy default; `_execute_step` passes the resolved parsed value and source. Progress paths are `()` without a log and `(log_path,)` with one. |

The exact-head review then produced six findings. Each has one closure below:

| # | Exact-head finding | Closure in this plan |
|---|---|---|
| 1 | The preservation promise conflicts with CMRU's strict streamed decoder. | D-179 accepts replacement decoding, its malformed-byte oracle is assigned to P7.2, and P7.3 documents the intentional change, including SPEC S3.4a. |
| 2 | P7.1 owns a live cleanup watchdog/process-spawning oracle before the P7.2 adapter and marker exist. | P7.1 owns only parsed-value/source and cleanup-field propagation with stubs; P7.2 owns the live capped `cmru cleanup --policy` oracle. |
| 3 | A public `[runner]` key could land without its required public documentation. | P7.1-P7.3 are one atomic merge unit with no intermediate main merge or release; P7.3 owns the parseable public examples, docs, and anchor checks. |
| 4 | The brief requires a behavior comparison and hand-mutation targets. | "Required behavior comparison" and "Controlled hand-mutation targets" below cover every behavior and every implementation package. |
| 5 | D-178 was cited but absent from the formal decision ledger. | `cmru/nyxloom-trove/decisions.md` records D-178 and D-179 with authority, rationale, binding scope, and oracles. D-001 remains unchanged. |
| 6 | The final image cannot be probed for a wheel archive that the Dockerfile deletes. | P7.0b defines a build-time-wheel-hash to selected-immutable-image proof chain based on installed metadata and module bytes compared with the pinned archive's contents. |

The subsequent exact-head review of
`47e11f872751299056a4f1b854f62c6e81ec60ca` found one remaining presentation issue:

| Finding | Closure in this plan |
|---|---|
| Procio returns an unprefixed holder-warning body while CMRU renders it differently on stdout and in the step log. | P7.2 owns one explicit warning formatter and a holder-warning presentation oracle covering both prefixes, one occurrence per abandoned pipe, quiet mode, the visible holder PID, and an unchanged aggregate mirror. |

## Required behavior comparison

The implementation preserves the behavior marked "preserved" and deliberately changes only
the decoding row under D-179. Captured probes remain outside this table's streamed-step
migration.

| Behavior | Current CMRU | `cli_extended.procio` | P7 target | Owner | Observable oracle |
|---|---|---|---|---|---|
| argv, cwd, environment | Builds the effective argv (including configured bake arguments), resolves an explicit per-command cwd, and runs under CMRU's scoped project/step/protected environment and `PYTHONUNBUFFERED=1`. | Accepts caller-supplied argv, cwd, and environment; it neither derives configuration nor mutates the caller's process environment. | Preserved: CMRU continues to derive those inputs; the adapter passes them unchanged to `run_streaming`. | P7.2 | Adapter fixtures capture the exact effective argv/cwd/env; cleanup's live case proves `CMRU_VERSION`, the selected token, original absolute cwd, and protected environment reach the child while ignored cleanup controls do not. |
| Output streams and logging | Merges stdout/stderr and writes ordinary lines to the step log and optional aggregate mirror. When an output pipe remains open after bounded drain, `_warn_pipe_holders` prints one unprefixed-body warning to stdout as `[WARN] {body}\n` and writes it to the step log as `[cmru] {body}\n`; the warning is printed in quiet mode and is not written to the aggregate mirror. | Merges stdout/stderr and calls `on_line`; `StreamResult.warning` carries the unprefixed one-line body and reports the visible holder PID when available, but procio does not render prefixes or own CMRU's files. | Preserved: ordinary lines keep their existing step-log/aggregate-mirror behavior; the P7.2 formatter renders procio's body exactly once as CMRU's stdout and step-log lines, never mirrors it, and never suppresses it in quiet mode. | P7.2 | The holder-warning presentation oracle runs one abandoned pipe with a visible holder PID and asserts exactly one `[WARN] {body}\n` on stdout, exactly one `[cmru] {body}\n` in the step log, the PID in both, an unchanged aggregate mirror, and the same warning under `quiet=True`. |
| Decoding and newlines | Locale incremental decoding with universal newlines and strict error handling: malformed bytes raise `UnicodeDecodeError`. | Locale incremental decoding with universal newlines and `errors="replace"`: malformed bytes become `U+FFFD`; `run_capture(text=True)` remains strict. | Deliberately changed by D-179 for streamed step output only; captured probes are not migrated. | P7.2 | In the registered capped lane with UTF-8, a child writing `b"ok \xff end\n"` and exiting 0 succeeds; both the step log and aggregate mirror contain `ok \ufffd end\n`, and no `UnicodeDecodeError` escapes. A nonzero child exit remains a step failure. |
| Exit and error mapping | Nonzero child status becomes `CalledProcessError` and then the existing `StepFailed`; failure excerpts are written before raising. Missing executables propagate the spawn error. | Returns `StreamResult(returncode, outcome, warning, watchdog)` or raises the spawn/termination/on-line exception. | Preserved for ordinary exits: the adapter maps nonzero to the existing failure path and never turns replacement decoding into success/failure. Watchdog expiry is the new exit-1 runtime failure. | P7.2 | Child exits 0, 7, and signal-terminated cases retain their existing observable failure semantics; a missing executable retains the plain spawn exception; watchdog maps to `StepFailed(returncode=1)`, never a negative signal status. |
| Watchdog and progress | Has no inactivity watchdog. | Requires a finite positive `no_progress_seconds`; bytes or configured path size/mtime changes count as progress, and expiry captures the tree before TERM/KILL. | Added: finite 1,800-second CMRU default, `[runner]` default, per-step override, source diagnostic, and path-sensitive progress. | P7.1/P7.2 | Config precedence and invalid-value tests in P7.1; fake-clock cases in P7.2 prove progress consumption, expiry diagnostic/source, pre-signal tree, and exit 1. |
| Process groups and signals | Starts each child in a new session, turns caller SIGTERM/SIGHUP into cleanup, and TERM-then-KILLs the child's group. | Provides the same supervision scope and group reaping, including explicit grace handling; it skips only a known-empty group after leader reaping. | Preserved; `tester_gate.py` moves to the public `procio.terminate_as_exit` rather than retaining the private runner helper as the control layer. | P7.2 | Pipe/pidfd handshakes prove whole-group cleanup and SIGINT/SIGTERM behavior without sleeps; the PGID oracle asserts only the known-empty case and documents the reuse race. |
| Cleanup | `cleanup_project_step` uses a cleanup-specific config and ignores ordinary step controls; it forces quiet output and uses absolute command cwd plus `repo_root/logs`. | Executes any supplied `StepConfig` under the generic supervision contract. | Preserved by D-178 except that cleanup gains the shared finite watchdog. Construct the existing cleanup config first, then copy only timeout/source metadata. | P7.1/P7.2 | P7.1 stub tests prove parsed timeout/source and field preservation; P7.2's live capped test proves the override reaches the watchdog, its diagnostic names `steps.clean.no_progress_timeout_seconds`, and ignored controls stay inert. |
| Direct calls | `run_command` accepts calls without parsed step metadata and with `log_path=None`. | Requires a positive no-progress limit and rejects `None` path elements; no path means `progress_paths=()`. | Explicit policy: direct calls use one CMRU 1,800-second constant; parsed execution always passes its resolved value/source; no log passes `()`, a log passes `(log_path,)`. | P7.2 | Adapter tests prove the finite direct default, parsed override, and exact progress-path shapes; neither path forwards `None`. |

## D-178: preserve cleanup's existing behavior

`steps.clean` historically receives only a cleanup-specific execution contract. Adopting its
full parsed step would newly honor controls that cleanup has ignored, changing existing
projects. D-178 therefore decides that cleanup preserves its existing environment,
quiet-output policy, command/cwd mapping, and `repo_root/logs` behavior while gaining only the
shared watchdog timeout.

In P7.1, `load_config` parses `steps.clean` with the same project `[runner]` default and step
override as every other step. `cleanup_project_step` starts from the existing
`_build_step_config("clean", project.steps["clean"])`, then copies only the resolved finite
timeout and its winning source key from `project.runner_steps["clean"]`. This preserves
D-178 by construction and avoids accidentally activating controls as `StepConfig` grows.
The resulting cleanup-owned fields remain:

- the command list and original absolute command cwd mapping from
  `project.steps["clean"]`;
- `bake_set_prefix=None`, `bake_set_vars=[]`, `no_cache_env=None`, `clean_dirs=[]`,
  `required_env=[]`, `login=None`, `step_env={}`, `env_command=None`, and `registries=None`;
- `quiet=True`; and
- the parsed finite no-progress timeout and its winning source key.

The execution environment remains the existing cleanup environment: `CMRU_VERSION`, the
selected publisher token, project environment, the protected cleanup environment
(`CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` cleared, selected `GITHUB_PUSH_PAT` applied),
the original absolute command cwd, and `repo_root/logs` as the log root. The full parsed
clean config must not be passed through, because that would accidentally activate ignored
step controls.

P7.1's stub oracle captures the `StepConfig`, `extra_env`, `protected_env`, log root, and
quiet output that `cleanup_project_step` would send to `execute_step`. It uses a project
`[runner]` default and a `steps.clean` override and asserts that the override/source win, all
cleanup-owned fields above survive, and inert controls remain inert. It spawns no process.

P7.2 owns the live behavioral oracle, marked `capped_container`: with stubbed remote cleanup
inputs, invoke `cmru cleanup --policy` in the registered capped lane using the real adapter.
Assert that the override reaches the watchdog, its diagnostic names
`steps.clean.no_progress_timeout_seconds`, `CMRU_VERSION` and the selected token reach the
child, the original command cwd and `repo_root/logs` are used, output stays quiet, and
`env_command` and login do not run.

## D-179: replacement decoding for streamed output

D-179 accepts procio's current decoding contract for streamed step output. Output is
diagnostic text: CMRU logs it, mirrors it, extracts quiet-mode context/evidence, and leaves
ordinary step success to the child's exit status. Failing a completed child merely because
its diagnostic bytes are malformed is less reliable than rendering undecodable bytes as
`U+FFFD`. This intentionally changes CMRU's prior strict decoder and requires no new procio
API or release. Captured probes, including `run_capture(text=True)`, stay strict and outside
this migration.

P7.2 must prove in the registered capped lane with UTF-8 selected that a step child writing
`b"ok \xff end\n"` and exiting zero succeeds, writes `ok \ufffd end\n` to both the step log
and aggregate mirror, and raises no `UnicodeDecodeError`. A nonzero child exit must still
produce the existing step failure. Watchdog expiry remains the separate exit-1 runtime
failure.

## P7.0 prerequisites

Neither prerequisite is code in this documentation-only branch. P7.0a and P7.0b must both be
complete before P7.2's registered CMRU gate.

### P7.0a: Run-Gate publishes the lane-cap fact

Run-Gate owns and publishes exactly one reserved fact for an ephemeral container lane with a
positive declared memory cap and positive declared PID cap:

```text
RUN_GATE_LANE_CAPS_V1={"environment":"tester-unified","lane":"assay-r0-r1","memory_max_bytes":2147483648,"pids_max":8192}
```

The value is compact JSON with exactly four keys and no extras:

- `environment`: the parsed Run-Gate environment declaration name;
- `lane`: the parsed Run-Gate lane name;
- `memory_max_bytes`: a positive JSON integer derived through Run-Gate's existing size
  parser and passed as the same memory cap to Docker; and
- `pids_max`: the positive declared JSON integer PID cap.

Run-Gate constructs the fact after processing caller-forwarded values. It reserves the name
and refuses a collision with `forward_env`, `required_env`, `image_env`, `cgroup_slice_env`,
or any other caller-controlled environment input. A caller-supplied value is never promoted
to a lane fact. Bare-host and ineligible paths clear an inherited value; ineligible
container lanes explicitly shadow an ambient or image value so no usable fact survives.

P7.0a includes the producer, collision/refusal tests, bare-host and ineligible-path tests,
the documented contract in `run-gate-project/SPEC.md`, and release/install of the new
Run-Gate wheel. For the CMRU-assay path it also proves that the outer lane container receives
the derived value and that Assay can forward it only when the lane declares both
`env_required` and `env_passthrough`.

### P7.0b: select a tester-unified image containing 0.7.0

The release-availability question is resolved: cli-extended 0.7.0 is published as
[`cli_extended-0.7.0-py3-none-any.whl`](https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.7.0/cli_extended-0.7.0-py3-none-any.whl)
with SHA-256
`39d330e3501da48eba7fc51359e26c02b3dc0340f6389b82afefe774cb76560d`.
The [GitHub release](https://github.com/volkb79-2/vbpub/releases/tag/cli-extended-v0.7.0) is
the release coordinate.

The selected tester-unified source still pins
`cli_extended-0.5.0-py3-none-any.whl` with SHA-256
`755c6f4919b587bbada9f9988a7bd677428513e93a040b4d3561fd76316ac951`, and its CMRU pin oracle
fixes that digest. P7.0b must:

1. update `tester-unified/Dockerfile`'s wheel URL, digest, and minimum version;
2. update CMRU's tester-image pin oracle;
3. hash the fetched wheel during the build and refuse bytes that do not match the pinned
   0.7.0 SHA-256;
4. bind the pinned wheel hash and the installed `cli_extended/procio.py` digest into image
   build evidence and OCI labels (the wheel archive itself is deleted after installation
   and need not remain in the final image);
5. rebuild and select the immutable image ID; and
6. prove that selected image, not a mutable local tag, release page, Git tag, or source
   checkout: read `/opt/tester-venv`'s installed cli-extended version, read the installed
   `cli_extended/procio.py` bytes and compare them with the corresponding path inside the
   pinned 0.7.0 wheel archive, and import `cli_extended.procio` from that image.

The local Git tag `cli-extended-v0.7.0` and the presence of `procio.py` in source do not prove
the published wheel or selected image contains those bytes. Only the published-wheel digest
and selected-image evidence chain satisfy this prerequisite.

## P7.1: configuration and cleanup propagation

P7.1, P7.2, and P7.3 are one atomic merge unit. No package in this unit may merge to `main`
or be released between packages. The branch may carry sequential package commits and use
their package gates, but the public `[runner]` key becomes mergeable only in the same main
merge that ships P7.2's implementation and P7.3's complete public documentation.

Add an optional project-level `[runner]` default and the same optional per-step key:

```toml
[runner]
no_progress_timeout_seconds = 1800

[steps.build]
quiet = true
no_progress_timeout_seconds = 3600
```

`no_progress_timeout_seconds` is a duration in seconds. The `[runner]` table is optional; an
absent project default resolves to the finite 1,800-second default. A step override inherits
the resolved project default when absent and wins when present. There is no disable value:
unknown runner keys, booleans, zero, negative values, NaN, and infinity are invalid because
the helper requires a finite positive duration.

Extend `config.py`'s strict project grammar to accept the top-level `runner` table and the
step key, and reject unknowns. Both configured values must be numeric, not bool, finite, and
greater than zero. Configuration errors exit 2 with the exact messages below, rendering the
invalid value with `repr`:

- `runner.no_progress_timeout_seconds must be a finite number of seconds greater than 0; got <value>`
- `steps.<step>.no_progress_timeout_seconds must be a finite number of seconds greater than 0; got <value>`

Thread the validated `[runner]` table into the `parse_step` source used by
`load_config` when it builds `ProjectConfig.runner_steps`. Store both the resolved finite
timeout and the winning source key in `StepConfig`. Include `cli.py` in P7.1 because
`cleanup_project_step` is the direct execution path that D-178 covers.

The diagnostic source keys are exactly:

- `steps.<step>.no_progress_timeout_seconds`;
- `runner.no_progress_timeout_seconds`; or
- `default runner.no_progress_timeout_seconds`.

The P7.1 oracles cover the default, inheritance, override, unknown keys, bool/zero/negative/
NaN/infinite rejection, and exit-2 wording. Its D-178 stub oracle proves timeout/source
propagation and cleanup-field preservation without executing `cmru cleanup`. Every parsed
step must receive the same resolved finite value independent of the higher-level CMRU verb.

## P7.2: adapter, dependency, signals, and capped process oracles

The first `cli_extended.procio` import and the dependency-floor increase ship in P7.2. Update
both cmru requirements in `cmru/pyproject.toml` to `cli-extended>=0.7.0`, including the
`interactive` extra, and update every floor-specific oracle with the same package:

- `cmru/tools/installed_wheel_smoke.py`;
- `cmru/tests/test_packaging_cli_extended.py`;
- `cmru/tests/test_release_gate_interpreter.py`; and
- the interactive-extra assertion in `cmru/tests/test_w2_integ.py`.

Older generic-parser fixtures may retain 0.3.0 only where they intentionally test the old
literal; they must not remain a declaration of CMRU's supported floor.

### D-179 streamed decoding

The adapter accepts procio's replacement decoding for streamed step diagnostics. It must not
add a strict streaming mode or infer failure from malformed text. Every returned line,
including replacement characters and an unterminated final tail, is written to the step log
and aggregate mirror under the existing output policy. The child's exit status remains
authoritative for ordinary success and failure. Captured probes are not migrated and retain
their existing strict behavior.

### Holder-warning presentation

`procio.StreamResult.warning` is an unprefixed one-line body. When non-`None`, it represents
exactly one abandoned-pipe event; `StreamResult.abandoned_holders` carries the visible holder
PIDs or `None` when the `/proc` scan failed. The P7.2 adapter owns one explicit formatter and
must preserve CMRU's current observable presentation exactly:

- stdout receives exactly one `[WARN] {body}\n`, regardless of `quiet`;
- the step log receives exactly one `[cmru] {body}\n`;
- the aggregate mirror receives no warning bytes and remains otherwise unchanged; and
- when a holder PID is visible, its decimal value appears in the body and therefore in both
  prefixed renderings.

The formatter consumes the unprefixed body as-is: it must not add a second prefix, emit both
renderings more than once, suppress either rendering in quiet mode, omit the holder PID, or
route the warning through the ordinary streamed-line path that also writes the aggregate
mirror. Ordinary streamed output retains its existing step-log and aggregate-mirror behavior.

The P7.2 holder-warning presentation oracle, named
`cmru/tests/test_runner_pipe_holders.py::test_abandoned_pipe_warning_preserves_cmru_renderings`,
uses one abandoned pipe whose holder is visible and parameterizes `quiet` while asserting the
exact stdout text, step-log text, occurrence counts, holder PID, and unchanged aggregate mirror
above.

`run_command` remains the compatibility adapter around `_run_command`, which calls
`procio.run_streaming` and preserves CMRU's elapsed/evidence, quiet excerpts, mirror handle,
holder-warning body ownership and formatting, and `CalledProcessError` behavior. Pass
`drain_seconds=OUTPUT_DRAIN_SECONDS`, `term_grace_seconds=GROUP_TERM_GRACE_SECONDS`, and the
resolved `no_progress_seconds`.

Direct `run_command` calls without parsed step configuration use one exported CMRU policy
constant, 1,800 seconds. `_execute_step` always passes the parsed finite value and its source
key explicitly. Pass `progress_paths=()` when `log_path is None`; otherwise pass
`(log_path,)`. Never pass `None` as a path element. Oracles prove that a direct no-log call
uses the finite policy default, a parsed step passes its override, and neither path sends
`None` to `run_streaming`.

The normal adapter keeps CMRU's logging protocol: every returned line is written to the step
log and optional aggregate mirror, success evidence and quiet tails are retained, and
watchdog diagnostics are printed and logged even in quiet mode. Watchdog expiry renders the
winning source key and the pre-signal process tree, then maps to the existing
`StepFailed(returncode=1)` path; a negative signal return code from procio is never exposed as
the CMRU CLI status.

`tester_gate.py` switches from `runner._terminate_as_exit` to public
`cli_extended.procio.terminate_as_exit`. Keep its existing exact-container cleanup behavior
and tests; do not add a second local signal implementation.

### Capped test contract

Register the `capped_container` pytest marker in `cmru/tests/conftest.py`. Apply it to every
process-spawning or signal-delivery case whose correctness requires containment. At
collection, before fixture setup or child spawn, the guard validates:

1. `RUN_GATE_LANE_CAPS_V1` parses as a JSON object with exactly `environment`, `lane`,
   `memory_max_bytes`, and `pids_max`; both names are non-empty strings, and both caps are
   positive JSON integers, not booleans.
2. The `(Run-Gate lane, environment)` pair is on an explicit allowlist for a full-suite CMRU
   test path.
3. The test interpreter is `/opt/tester-venv/bin/python` (the selected tester-unified
   interpreter).
4. The process's effective cgroup-v2 `memory.max` and `pids.max`, derived through the readable
   visible cgroup ancestry, are finite and positive and do not exceed `memory_max_bytes` and
   `pids_max`. Missing identity, malformed schema, a wrong pair, an unreadable component, no
   finite effective cap, or an observed over-cap value refuses before setup/spawn.

The current allowed full-suite mapping is:

| Run-Gate path | Assay lane | Environment |
|---|---|---|
| `assay` | `cmru` | `tester-unified` |
| `assay-r0-r1` | `cmru-r0-r1` | `tester-unified` |
| `assay-first-release` | `cmru-first-release` | `tester-unified` |
| `assay-first-release-r2` | `cmru-first-release-r2` | `tester-unified` |
| `mutation` | command lane `mutation` | `cmru-mutation` |
| `r2` | command lane `r2` | `cmru-mutation` |

Add `RUN_GATE_LANE_CAPS_V1` to both `env_required` and `env_passthrough` on the four full-suite
Assay declarations in `cmru/assay.toml`: `cmru`, `cmru-r0-r1`, `cmru-first-release`, and
`cmru-first-release-r2`. Assay copies only declared passthrough names into pytest's
environment, and its `env_required` check refuses an absent fact instead of silently running
the marked tests without one. Run-Gate injects the fact directly into the two command lanes,
`mutation` and `r2`, whose environment is the separately declared `cmru-mutation`. The
Capped guard's allowlist includes those current paths, especially R2, and fails closed for
everything else. `release-preconditions` needs inclusion only if its command collects a
marked test.

The guard test matrix covers direct cockpit refusal, malformed/extra/wrong-type facts, an
unlisted lane, a wrong interpreter, unreadable or unbounded effective limits, observed limits
above the declared facts, and acceptance in a registered capped full-suite lane. This fact is
operational containment evidence, not an authentication credential against a caller that
deliberately forges its own environment and process state.

### Deterministic process and clock oracles

All process and signal tests use pipes/pidfds for synchronization, never fixed sleeps or
wall-clock verdicts. Selector polling is set to zero while the fake clock is frozen.

- Drain: the child sends its pid over an output pipe and exits while a holder retains the
  output pipe; the test opens the pidfd, waits for readiness, advances the fake clock past the
  drain interval, and asserts the child's exit code, holder warning, and holder survival.
- Watchdog: the child emits a ready line and then stays silent. The `on_line` callback only
  arms the fake clock. On the next pump iteration the guard receives `progressed=True` and
  returns baseline time, so the guard consumes the ready line and resets `last_progress`.
  The following pump tick advances past the idle threshold, causing the pre-signal tree
  snapshot and watchdog result.
- TERM-ignoring escalation: the child's TERM handler acknowledges through a control pipe.
  Advance the fake clock past the grace period only after the reaper has read its deadline;
  pidfd readiness occurs after SIGKILL and is therefore too late to be the trigger. Assert
  the pre-signal tree, exit-1 mapping, and eventual SIGKILL.
- SIGINT/SIGTERM driver cases: the child reports the shell and grandchild pids over a ready
  pipe; deliver the signal only after that handshake and block on pidfds until both are gone.

`reap_process_group` skips a **known-empty** process group after its leader is reaped. It does
not pin group identity between the emptiness check and `killpg`, so this design promises no
protection against a PGID-reuse race.

## P7.3: user documentation at revision 5

Keep the project template revision at 5. The new settings are optional and existing
revision-5 projects remain valid. A revision bump would impose an unnecessary estate-wide
migration because `cmru standards` checks the marker globally. Do not change `standards.py`,
either scaffold template, `scaffold.py`'s revision replacement, or existing project markers.
Do not add the optional `[runner]` block to scaffold templates in this plan.

Update the normative runner grammar, including SPEC S3.2 and the S2/S3.4a behavior, plus
`README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, and user-facing examples/tests for:
the 1,800-second finite default; project/step precedence and source-naming diagnostics;
finite-only validation; cleanup's preserved D-178 behavior; revision-5 compatibility; and
D-179's replacement-decoding change for streamed output while captured probes remain strict.

P7.3 must include every public example and its checks in this same work item:

- every config example in README, DESIGN-GUIDE, and CONSUMERS parses with the shipped loader
  and declares the applicable current schema/version marker (including the current template
  revision in CMRU project configs);
- the repository's own `cmru.toml` carries the brief-required example of the public
  `[runner]` default;
- every public runner key, finite-only rule, precedence outcome, source diagnostic, and
  cleanup compatibility fact appears in at least one public document;
- cross-document links and section anchors resolve; and
- SPEC S3.4a and user documentation describe replacement decoding rather than the retired
  strict streamed-decoder claim.

The compatibility oracle is that an unchanged revision-5 project still loads, passes
`cmru standards`, and resolves the finite 1,800-second default.

## Package order

1. **P7.0a** - Run-Gate lane-fact producer, collision refusal, tests/spec, and release/install.
2. **P7.0b** - tester-unified 0.7.0 wheel pin/digest, image build/selection, and exact
   installed-image proof.
3. **P7.1** - configuration parsing, resolved step metadata, cleanup propagation, and
   D-178 stub oracles.
4. **P7.2** - procio adapter, dependency floor/oracles, public signal import, capped guard,
   D-179 decoding, deterministic process tests, and the live cleanup oracle.
5. **P7.3** - normative/user documentation and examples, without a revision bump.

P7.0a and P7.0b must both be complete before P7.2's registered CMRU gate. If the installed
Run-Gate wheel or selected tester-unified image cannot prove the exact producer/image
contract, pause P7.2's procio import, dependency-floor, and capped-gate work; P7.1 may
proceed independently. The 0.7.0 wheel itself is already published and verified, so wheel
availability alone is not a pause trigger.

P7.1 may be implemented on the branch before the prerequisites, but P7.1-P7.3 do not merge
separately: P7.2's dependency/adapter release work and P7.3's public docs remain in the same
atomic merge unit, so P7.2's pause also pauses that merge.

## Controlled hand-mutation targets

These are deliberate hand edits used during implementation review, not predicted mutation
score targets. Each must be restored after its named check proves it red.

| Package | Controlled mutation | Required killer |
|---|---|---|
| P7.0a | Omit the lane fact, add a fifth key, emit a malformed/boolean cap, accept a caller collision, or persist it on a bare-host/ineligible path. | Run-Gate producer, exact-schema, collision-refusal, bare-host, ineligible-path, and Assay-forwarding tests. |
| P7.0b | Change the wheel URL/digest/minimum, or replace the evidence chain with a mutable tag alone, an installed-version-only check, or no installed-module-byte comparison. | Tester-unified Dockerfile/pin tests and the selected-image evidence check must fail. |
| P7.1 | Let `[runner]` beat a step override, drop the winning source, accept bool/zero/negative/NaN/infinity, or let cleanup inherit ignored step controls from the full parsed config. | Config precedence/validation tests and the D-178 cleanup `StepConfig` stub test. |
| P7.2 | Ignore `U+FFFD` replacement, turn malformed output into failure over exit 0, lose nonzero/signal failure mapping, forward `None` or a bare path instead of the required progress-path shapes, skip watchdog exit-1/tree reporting, omit whole-group cleanup, or regress holder-warning presentation. | D-179 malformed-byte/exit tests, direct-call/log-path adapter tests, fake-clock watchdog tests, pipe/pidfd process-group tests, and `cmru/tests/test_runner_pipe_holders.py::test_abandoned_pipe_warning_preserves_cmru_renderings`. |
| P7.3 | Remove or invalidate a public config example, omit the runner key/default/precedence/oracle, break a cross-document anchor, or restore the old strict-decoding claim in S3.4a/user docs. | Shipped-loader example parsing, public-key vocabulary checks, anchor checks, and documentation regression tests. |

The exact killer for each P7.2 holder-warning mutation is
`cmru/tests/test_runner_pipe_holders.py::test_abandoned_pipe_warning_preserves_cmru_renderings`:

| Controlled mutation | Required rejection |
|---|---|
| Drop the stdout `[WARN] ` prefix. | The stdout exact-text and occurrence assertion fails. |
| Drop the step-log `[cmru] ` prefix. | The step-log exact-text and occurrence assertion fails. |
| Duplicate the warning or retain/add a second prefix. | The exactly-one stdout/step-log assertions fail. |
| Suppress the warning when `quiet=True`. | The parameterized quiet case sees no required warning and fails. |
| Omit the visible holder PID from either rendering. | The body/PID assertions on stdout and in the step log fail. |
| Invent an aggregate-mirror copy of the warning. | The unchanged-mirror assertion fails while ordinary streamed output remains separately checked. |

## Risks and source limits

- Procio is Linux-specific because `/proc` is part of its contract. CMRU already relies on
  Linux in its supported gate environment; this change does not add non-Linux portability.
- A command that is active but silent for more than 1,800 seconds now fails unless its
  project or step declares a larger finite threshold. There is deliberately no off/infinite
  setting. The watchdog is an inactivity bound, not a total-runtime timeout.
- Procio snapshots descendants through parent links. A process that escapes with `setsid` and
  is reparented before the snapshot may not appear in the tree; escaped pids are reported
  when discoverable but are not killed. CMRU must print the report as returned and make no
  stronger reaping claim.
- The PGID check narrows only the known-empty case after leader reaping. A reuse race between
  checking and `killpg` remains possible and is not claimed to be prevented.
- The capped guard establishes the current operational lane envelope. It is not an
  authentication boundary against deliberate environment or process-state forgery.

Source inventory: `cmru/src/cmru/runner.py`, `cmru/src/cmru/config.py`,
`cmru/src/cmru/cli.py`, `cmru/src/cmru/tester_gate.py`, `cmru/tests/conftest.py`,
`cmru/assay.toml`, `cmru/run-gate.toml`, `cmru/pyproject.toml`,
`cmru/tools/installed_wheel_smoke.py`,
`cmru/tests/test_packaging_cli_extended.py`,
`cmru/tests/test_release_gate_interpreter.py`, `cmru/tests/test_w2_integ.py`,
`cmru/tests/test_tester_unified_image.py`, `tester-unified/Dockerfile`,
`run-gate-project/run_gate.py`, `run-gate-project/SPEC.md`,
`assay/src/assay/runner.py`, `cmru/src/cmru/standards.py`,
`cmru/src/cmru/templates/project-wheel.toml`, `cmru/templates/cmru.toml.tmpl`,
`cmru/docs/SPEC.md`, `libraries/cli-extended/src/cli_extended/procio.py`,
`/workspaces/vbpub/AGENTS.md`, and `scripts/controller/rules.md`.

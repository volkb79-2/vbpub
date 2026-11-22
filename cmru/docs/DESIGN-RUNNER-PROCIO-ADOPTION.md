# Design: CMRU adopts `cli_extended.procio`

Status: **closed implementation plan** after the Codex Sol xhigh review of
`c36f25cddccf6c417eee48763fa0f3bd003836e0`. This note covers KI-178 items 3 and 4 in CMRU
only. It does not implement P7.0-P7.3, and it does not assign adoption work in CIU,
mdt-cockpit, or nyxloom.

## Decision

CMRU will route every declared project-step command through
`cli_extended.procio.run_streaming`. CMRU retains its logging, quiet-mode, error-excerpt,
evidence, and step-failure contracts in the `on_line` adapter. The adapter reports procio's
bounded-drain warning and watchdog report through CMRU's existing output and log paths.
Every command receives a finite no-progress limit resolved from project configuration; the
default is 1,800 seconds. Watchdog expiry is a runtime step failure with exit 1.

CMRU will not migrate short captured probes, interactive commands, registry login, or other
non-step subprocess calls in this package. The other KI-178 consumers remain with their
owners: `mdt-cockpit/build-push.py` (MDT-104), `ciu/src/ciu/engine.py` (CIU-220), and nyxloom
(NL-83). They adopt the same released helper independently.

## Review closure

All ten confirmed findings from the design review have a settled disposition:

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

## D-178: preserve cleanup's existing behavior

`steps.clean` historically receives only a cleanup-specific execution contract. Adopting its
full parsed step would newly honor controls that cleanup has ignored, changing existing
projects. D-178 therefore decides that cleanup preserves its existing environment,
quiet-output policy, command/cwd mapping, and `repo_root/logs` behavior while gaining only the
shared watchdog timeout.

In P7.1, `load_config` parses `steps.clean` with the same project `[runner]` default and step
override as every other step. `cleanup_project_step` then copies
`project.runner_steps["clean"]` and explicitly replaces all cleanup-owned fields:

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

Behavioral oracle: stub the remote cleanup inputs and invoke `cmru cleanup --policy` with a
project `[runner]` default plus a `steps.clean` override. Assert that the override reaches
the watchdog and its diagnostic names
`steps.clean.no_progress_timeout_seconds`; prove `CMRU_VERSION`, the selected token, command
cwd, log root, and quiet output are preserved; and prove `env_command` and `login` do not run.
The process-spawning cleanup case has the `capped_container` marker.

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
3. rebuild and select the image; and
4. verify the selected immutable image ID itself, not a local tag, release page, Git tag, or
   source checkout: read `/opt/tester-venv`'s installed cli-extended metadata, verify the
   installed wheel digest, and import `cli_extended.procio` from that image.

The local Git tag `cli-extended-v0.7.0` and the presence of `procio.py` in source do not prove
the published wheel or selected image contains those bytes. Only the published-wheel digest
and selected-image inspection satisfy this prerequisite.

## P7.1: configuration and cleanup propagation

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
NaN/infinite rejection, exit-2 wording, and D-178. Every parsed step must receive the same
resolved finite value independent of the higher-level CMRU verb.

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

`run_command` remains the compatibility adapter around `_run_command`, which calls
`procio.run_streaming` and preserves CMRU's elapsed/evidence, quiet excerpts, mirror handle,
warning text, and `CalledProcessError` behavior. Pass
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
finite-only validation; cleanup's preserved D-178 behavior; and revision-5 compatibility.

The compatibility oracle is that an unchanged revision-5 project still loads, passes
`cmru standards`, and resolves the finite 1,800-second default.

## Package order

1. **P7.0a** - Run-Gate lane-fact producer, collision refusal, tests/spec, and release/install.
2. **P7.0b** - tester-unified 0.7.0 wheel pin/digest, image build/selection, and exact
   installed-image proof.
3. **P7.1** - configuration parsing, resolved step metadata, cleanup propagation, and
   D-178 oracles.
4. **P7.2** - procio adapter, dependency floor/oracles, public signal import, capped guard,
   and deterministic process tests.
5. **P7.3** - normative/user documentation and examples, without a revision bump.

P7.0a and P7.0b must both be complete before P7.2's registered CMRU gate. If the installed
Run-Gate wheel or selected tester-unified image cannot prove the exact producer/image
contract, pause P7.2's procio import, dependency-floor, and capped-gate work; P7.1 may
proceed independently. The 0.7.0 wheel itself is already published and verified, so wheel
availability alone is not a pause trigger.

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

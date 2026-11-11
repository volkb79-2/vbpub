# run-gate — writing lanes worth running

Sibling of **`CONSUMERS.md`** (how to adopt run-gate: the mechanics, the lane
schema, the recipes per project type) and of **`REMOTE-LANES-BUILDKITE.md`**
(how a lane leaves this host). This file answers a different question: given
the mechanics, **what makes a lane good**, which tools belong where, and what
to consider before declaring one. Normative rules stay in `SPEC.md`; assay's
judgment model stays in `assay/docs/DESIGN-GUIDE.md`. Written 2026-09-02 from
an operator discussion; the measured estate facts in §2 are dated to that day.

The one sentence to keep: **run-gate decides WHERE and HOW a command runs,
assay decides WHAT the run proves, and most tools people call "testing tools"
are neither's judgment.**

---

## 1. Where each tool class belongs

| Tool class | What it gives you | Python | Go | React / TypeScript | Home |
|---|---|---|---|---|---|
| Lint / static analysis | unused names, dead imports, obvious bugs, before any test runs | pyflakes, ruff | gofmt, go vet, staticcheck | `tsc --noEmit`, eslint | run-gate `kind = "command"` lane |
| Type checking | contract checking without running code | mypy, pyright | the compiler | tsc | run-gate `kind = "command"` lane |
| Property-based testing | generated inputs that kill the mutants example tests miss | hypothesis | pgregory.net/rapid, testing/quick | fast-check | inside the suite the R0 command runs |
| API fuzzing | schema-driven requests against a RUNNING server | schemathesis | schemathesis | schemathesis | a lane that needs a deployed instance (ciu) |
| Coverage | R1 evidence | coverage.py | `go test -coverprofile` | vitest + istanbul (lcov) | assay R1 |
| Mutation | R2 evidence | assay's python adapter | assay's go adapter | Stryker report, ingested by assay | assay R2 |
| Canary | R3 evidence: the test fails when the code it guards is broken | assay | assay | assay | assay R3 |
| Long fuzzing | crashes found over hours, elsewhere | atheris, hypothesis fuzz | `go test -fuzz` | fast-check | assay Tier 3 (attested), async lane |
| Security / dependency scans | commit-bound findings with a declared threshold | bandit, pip-audit | gosec, govulncheck | npm audit | assay Tier 2 (adjudicated) — deferred, A-O10 |

Why the split lands this way:

- **assay judges TEST RIGOR.** Its rigor ladder R0 → R1 → R2 → R3 is about
  what a test run proves about the tests. A linter or a type checker proves
  nothing about the tests; it proves something about the code. Putting it
  under assay would make assay a policy engine, which its design guide names
  as the first way a testing tool rots (`assay/docs/DESIGN-GUIDE.md` §3).
- **assay's Tier 2 exists for tools whose OUTPUT you want bound to the commit
  as evidence** — a scanner with a declared threshold whose verdict travels
  with the verdict artifact. That tier is deliberately unbuilt until a real
  integration needs it (assay A-078, A-O10). A lint result does not need
  commit binding: the gate's exit code is the whole story.
- **Property-based and fuzz tools are test-AUTHORING techniques**, not
  lanes. Their value shows up inside assay's numbers: a property test kills
  mutants an example test lets survive, so it raises the R2 kill rate, and a
  fuzzer's findings become new example tests. See §4 for the one real
  interaction, which is budget.
- **API fuzzing and integration suites need a deployed stack.** That is
  ciu's job (an `exec`/instance lane in ciu v8, or a command lane run
  against an instance today), never a bare `test-runner` lane pretending the
  server is there.

---

## 2. What the estate does today (measured 2026-09-02)

- **assay's own registered gate** lints its source with **pyflakes** as a
  phase after the suite, from its own hash-bound wheelhouse in a third venv
  (Wave D, B024/DA-R7). **ruff was dropped there**: a platform wheel of about
  10 MB that adds nothing over pyflakes for the F rules. That is a fact about
  assay's closure discipline, not a verdict on ruff for other projects.
- **srdm's Go gate** (`shared-ramdisk-depot-manager/tools/gate.sh`) already
  runs `gofmt -l` and `go vet ./...` before its tests.
- **nyxloom** carries a real Hypothesis suite (`tests/test_properties.py`);
  **dstdns** floors `hypothesis` and `schemathesis` in its root
  `requirements.txt`, runs mypy in its controller, and its React app's
  `lint` script is `tsc --noEmit` with vitest and istanbul coverage.
- **assay adapters**: python, go and sql are native (R1/R2); JavaScript is
  reached by ingesting Stryker mutation reports (R2) and istanbul/lcov
  coverage (R1, consumer wave in progress, A-O03).
- **Nobody declares a lint lane in `run-gate.toml` yet.** Where linting
  happens it is a phase inside a project's own gate script. §3 says how to
  declare it as a lane instead.

---

## 3. How to structure a project's lanes

**One concern per lane.** A lane is a unit that can pass or fail on its own,
be re-run on its own, and be timed on its own (RG-27 history is per lane).
The typical set:

| lane | tier | kind | what it proves | budget shape |
|---|---|---|---|---|
| `focused` | T0 | assay or command | a bounded R0 selection for local feedback | seconds to a few minutes |
| `lint` | T1/T2 | command | the code parses and has no obvious dead names; types check | seconds to a minute |
| `r0-r1` | T1; R0/R1 part of T2 | assay | the suite passes and covers the changed lines or the whole-project floor | the suite's own bound |
| `r3` | T2 | assay or command | the applicable guarded checks reject broken code | per attempt, bounded |
| `integration` | mapped by the release step | command against an instance | the service behaves behind its real boundaries | the stack's bound |
| `gate` | T2 | sequence | the release-safe gate: `r0-r1`, `r3`, and live checks | the sum |
| `r2` | T3 | assay or command | the suite kills the mutants in the judged scope | generous, with `budget_per_candidate` and (23.4.0) `stall_timeout` |
| `fuzz` | T3 | command | a bounded resumable campaign finds crashes or invariant violations | budget plus target or iteration bound |

Rules of thumb:

1. **Fast lanes first in the conjunction.** `lint` before `unit` before
   `mutation`; a conjunction stops at the first failure (`&&`), so the cheap
   refusal saves the expensive run. See `CONSUMERS.md` "Gate-conjunction
   lanes" for the shape and the `{worktree}` / `{base}` tokens.
2. **Lint and types are `kind = "command"` lanes on the same environment as
   the unit lane.** Do not put them under assay, and do not put them inside
   the unit command either: a lint failure that surfaces as a pytest error
   is a lint failure you cannot re-run alone.
3. **Anything that tests production code is an assay lane.** A bare command
   lane running pytest proves only that pytest exited 0; the same command as
   an assay R0 lane produces a verdict artifact bound to the commit, and the
   R1 floor is one key away. `CONSUMERS.md` "Worked example — run-gate ×
   assay, end to end" shows both files.
4. **Mutation is its own T3 lane, never folded into the release gate.** It has
   a different budget and event, and it is the lane where `--resume` and
   `--progress` do real work (R-38 passes both to every assay lane; they are
   no-ops without R2). Use the `r2` preset unless an existing name is mapped
   explicitly.
5. **`budget` is the command's own bound, stated once.** On run-gate it is
   advisory and printed; on assay it is enforced (`LANE_TIMEOUT`). The
   consumer's `assay.toml` owns the mutation lane's number; a `budget` key
   under a run-gate pin table is refused from 23.4.0 (RG-32). Pair the two
   deliberately: assay's bound must fit inside whatever the outer caller
   allows (`CONSUMERS.md` "Consumer timeouts must not cut lanes short").
6. **Integration lanes name the instance they need.** Today that is a
   command lane whose argv reaches a running stack; under ciu v8 it becomes
   a ciu gate lane with an `exec` environment. Either way the lane declares
   the dependency; it never assumes the server is up.

### Tier contract and preset names

The tier names, required claims and event boundaries are normative in
[`CONSUMERS.md`](CONSUMERS.md#normative-test-tiers-t0-t3). The lane author's
mapping is:

| Preset (lane name) | Tier | Lane shape |
|---|---|---|
| `focused` | T0 | A bounded R0 selection, normally with `accepts_args = true`. |
| `r0-r1` | T1, and the R0/R1 part of T2 | One canonical suite run feeding R0 and changed-line or whole-project R1. |
| `r3` | T2 | The applicable canaries. A changed-lines lane that needs both the import-break and uncovered-line mechanisms uses two Assay lanes until AS-2 exists. |
| `gate` | T2 | A native `kind = "sequence"` containing `r0-r1`, `r3`, and the project's live release checks. |
| `r2` | T3 | A standalone R2 lane on the latest release, runnable without an aggregate lane preparing its inputs. |
| `fuzz` | T3 | A bounded resumable fuzz campaign with tracked corpus and ignored output. |

A project may keep an established lane name (`selftest`, `tester-unified`,
`ciu-gate`) when renaming adds only churn. It documents that name's preset
mapping in its adoption notes.

**`gate` means T2: no R2.** A `gate` lane is the release-safe path and must
not contain R2, fuzzing, or any other T3 member transitively. It is a native
sequence, cheap members first and `stop_on = "FAIL"`, so each member remains
independently selectable and timed. An existing aggregate that includes R2
is split: rename the release-safe sequence to `gate` and move the campaign to
`r2`; do not preserve a release-blocking `gate` alias.

**Mutation becomes `r2` at T3.** Every project that claims R2 declares an
always-available `r2` lane that runs alone, resumes, and is queued by each
release for that tool's latest tag. Its changed-line base is the newest tag
with a recorded R2 PASS for that lane, or the lane's declared floor tag when
none exists. R2 never blocks an ordinary release unless the project has an
explicit version-boundary precondition. A finding opens a backlog item; a
missing or failed T3 result is attention, not a release refusal.

With Assay 12.1.1, set the native mutation lane's `jobs_max` before the release
tag. Assay folds the lane declaration into the judge identity, so changing it
changes the judge hash and invalidates finished-candidate state; keep the same
value through every resume. For a four-core host, cap at three to reserve a
core for the host and sampler, then fit the lane's memory budget to measured per-candidate RSS p95 (or
an evidence-based `candidate_ram` estimate when history is absent). Use
`jobs = "auto"` when Run-Gate should select the count under those committed limits.

The queue entry and result ledger are the stable interface (CONSUMERS.md,
"T3 trigger, queue and ledger"). The current host queue, campaign supervisor
and `remote-gate` are replaceable machinery; lane declarations and release
steps consume only the entry and ledger contracts.

### 3.1 Per-language recipes

The `run-gate.toml` and `assay.toml` fragments below are shapes, not
complete files; the full grammars are in `CONSUMERS.md` (lane schema) and
`assay/docs/CONSUMERS.md`.

**Python**

```toml
# run-gate.toml
[lanes.lint]
kind = "command"
environment = "tester-unified"
description = "pyflakes + mypy over src/ — parses, no dead names, types check"
argv = ["bash", "-c", "python -m pyflakes src && python -m mypy src"]
budget = "3m"

[lanes.unit]
kind = "assay"
environment = "tester-unified"
assay_lane = "unit"                  # -> assay.toml [lanes.unit], rigor R0 + R1
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<ver>.pyz"]
[lanes.unit.pins.assay]
version = "<ver>"
sha256 = "tools/assay/assay-<ver>.pyz.sha256"

[lanes.r2]
kind = "assay"
environment = "tester-unified"
assay_lane = "r2"                    # T3 rigor R2; assay.toml owns the budget
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<ver>.pyz"]
# stall_timeout = "15m"              # from run-gate 23.4.0: silence, never elapsed time
[lanes.r2.pins.assay]
version = "<ver>"
sha256 = "tools/assay/assay-<ver>.pyz.sha256"
```

**Go** — `gofmt -l` and `go vet` in the lint lane (srdm's gate is the
template), `staticcheck` when the module vendors it; assay's go adapter for
R1 (statement-granular since 4.1.0) and R2. Property tests with
`pgregory.net/rapid`; native fuzzing (`go test -fuzz`) is an async lane, not
part of the gate (§5).

**React / TypeScript** — lint lane `tsc --noEmit && eslint .`; unit lane runs
vitest with istanbul coverage and the lane declares the lcov artifact for
assay R1 (A-O03); Stryker writes the mutation report an ingested R2 lane
reads. Property tests with `fast-check`.

**SQL** — assay's sql adapter at R2 over the migration's own tests (dstdns's
`cw2b_schema` lane is the shape: `judge.language = "sql"`, R0 + R2). Lint
with sqlfluff in a command lane if the project wants it.

---

## 4. What to consider before declaring a lane

**Determinism.** A lane must give the same answer for the same commit.

- **Declare the network boundary.** Docker's default network is available
  unless the effective policy opts out; ephemeral environments and their lanes
  can set `network = "none"` or `network = "default"`. The lane wins over the
  environment. `"none"` asks Run-Gate to start that lane container with
  `--network none`; `"default"` leaves Docker's default in effect. A
  declared key is rejected for `exec` and `host` modes because they do not
  create the disposable lane container. A test that needs the internet is an
  integration lane or a bug; it must not silently inherit the default for a
  hermetic test.

  ```toml
  [lanes.assay-r1]
  kind = "assay"
  environment = "tester-unified"
  assay_lane = "r1"
  network = "none"
  ```

  The cgprofile observer remains available to such a lane: its endpoint is a
  bind-mounted Unix socket. Run-Gate's admission and observer probes keep
  their own network settings. See the [consumer contract](CONSUMERS.md#lane-network-mode)
  and [design rationale](docs/DESIGN-GUIDE.md#per-lane-network-boundaries-belong-on-the-ephemeral-lane-container).
- Seeded or derandomised randomness. For Hypothesis, register profiles in
  `conftest.py` and select by environment:

  ```python
  from hypothesis import settings, HealthCheck
  settings.register_profile("gate", derandomize=True, deadline=None,
                            max_examples=200,
                            suppress_health_check=[HealthCheck.too_slow])
  settings.register_profile("nightly", deadline=None, max_examples=5000)
  settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "gate"))
  ```

  `deadline=None` matters on a shared host: the gate runs under
  `nice -n 19`, and Hypothesis's default deadline turns load into flakes.
  The nightly profile belongs on a remote lane (§5), selected by the assay
  lane's `env`.
- **Under R2 every candidate re-runs the suite**, so a property test's
  example count multiplies into the mutation budget. Keep the gate profile
  small and let the nightly profile be large; that is the whole interaction
  between property testing and assay.
- No test-order dependence, no shared mutable fixtures across modules, no
  `pytest-randomly` unless seeded.
- **No xdist on a shared host.** `-n` on 8 cores shared with a production
  workload is how the load reached 85 on 2026-09-02; serial under
  `nice -n 19 ionice -c 3` is the standing rule (`AGENTS.md`). Where xdist
  is needed, declare `workers`, `workers_max` and `xdist_safe = true` with
  the `{workers}` token (README "xdist workers for command lanes"): run-gate
  then requires enforced caps, sizes `auto` from measured per-worker numbers,
  re-runs an N>1-only failure serially (`XDIST_ISOLATION`), and
  `--xdist-parity`/`--scaling` produce the evidence. Never hard-code `-n`.

**Exit-code truth.** The command fails on the failure it exists for.

- No `|| true`, no `set +e`, no `pytest ... -k` filters written into the lane
  argv (a filter is a per-run choice, not a lane).
- A lane that "skips" is not green: a skip that hides a missing tool is a
  false pass. Make the tool a `required_env`/toolchain fact run-gate checks
  (`doctor`, `--check-env`) or fail.
- Print evidence on failure to stdout/stderr; run-gate preserves it (R-26)
  and RG-27 records the outcome. A lane that fails silently costs a second
  run to find out why.
- `pytest tests/` green is not the gate green (`assay-gate-vs-pytest-gap` in
  the estate memory; assay's Wave D found 20 gate-only harnesses invisible
  to a local run). The lane IS the definition; run the lane.

**Hermeticity and provenance.**

- Run from a clean tree (`clean_tree = true` is the default and the refusal
  is a feature); a `false` needs a written reason in the lane.
- Internal vbpub lanes use the shared source-backed judge: omit both
  `assay_command` and `pins`, and run-gate installs the selected worktree's
  `assay/` source while recording its runtime version and source commit. A
  consumer outside this repository has no shared source tree, so it uses an
  explicit immutable command plus `pins.assay` digest/version; refresh that
  external/copy artifact deliberately when it adopts a new Assay release.
- Environment facts (image, slice, mounts, `state_root`) come from the
  applicable `run-gate.toml` — DERIVE or READ or FAIL, never a silent default
  (`AGENTS §4.2a`). Assay environments must have a durable, writable state
  root; see the [consumer contract](CONSUMERS.md#resume-progress-and-durable-assay-state).

**Evidence over assertion.**

- Prefer the ladder: R0 first, then an R1 floor measured before it is set
  (never a round number chosen in a meeting), then R2 on the scope that
  matters with `budget_per_candidate`, then R3 on the tests that guard the
  critical paths. Measure before floors; the 0/0-is-100% trap is real
  (assay A-026/A-035).
- Every wire field a consumer reads by name is a contract; dstdns reads
  `outcome`, `status`, `coverage.pct`, `coverage.missing_lines` and
  `coverage.missing_branch_lines`. Name what you read, in the lane's
  description, so a schema cut can find you.

**Resources and the shared host.**

- Declare `memory` (hard cap + admission, RG-20) on anything that can grow;
  a mutation lane running out of RAM is a `crashed` candidate, not a kill.
  `resources.memory_max` is an optional daemon placement ceiling for exec and
  bare-host lanes; it is not a second Docker limit and is ignored for
  ephemeral lanes. The client derives a ceiling from the verified gates slice
  when the lane does not declare one. For exec/bare-host lanes with a placement
  request, an explicit daemon refusal or invalid read-back prevents the lane
  body from running and returns an infrastructure refusal, not a candidate
  verdict; daemon/transport unavailability before a placement result retains
  the disclosed R-36h fallback.
- From 23.4.0 a mutation lane gets `stall_timeout`: it stops the lane only
  when the container is still running AND the progress file has been silent
  that long, never on elapsed time. That plus a generous `budget` plus
  `budget_per_candidate` is the whole bounding story for R2 (RW-6).
  **Every R2 (mutation) lane sets `judge.mutation.budget_per_candidate`
  in `assay.toml` (RW-28) — this is MANDATORY, not merely advisory:**
  a mutant that hangs the test process at interpreter exit (a
  `threading.Thread(daemon=True)` flipped to non-daemon by the mutation
  itself, seen live in this wave) is not a slow candidate `budget`/
  `stall_timeout` will ever catch — nothing else bounds a single candidate,
  and assay waits on it forever with no default.
- Gate containers share the verified gates-slice admission object. The host
  policy permits the configured concurrent mutation slots; every launched
  container is still capped immediately (`docker update --cpus=3` while the
  host is shared). A daemon-backed lane waits for gates-slice headroom before
  starting and then proceeds after its bounded `admission_wait` with one
  warning; host load never becomes a lane verdict.
- The project-level `[profile]` table configures profiling policy and
  `admission_wait`; it cannot choose a socket path. Run-Gate reads
  `CGPROFILE_CONTROL_SOCKET` from its process environment when profiling is
  enabled, and observer lanes also require `CGPROFILE_OBSERVER_SOCKET` for
  their version preflight and read-only mount. Missing endpoints fail by
  name; `RUN_GATE_PROFILE=off` is the explicit profiling opt-out. Keep the
  environment endpoint setup and `required-env:ASSAY_RESOURCE_OBSERVER_SOCKET`
  forwarding documented for any Assay lane that uses the observer.
  The daemon watch is one reader per session; if it is
  unavailable, the in-process assay watch bounds the R2 baseline by Assay's
  heartbeat (`300s` minimum; run-gate requests a 60 s heartbeat, so an assay
  lane's `stall_timeout` below `120s` is refused at load) and then uses the producer's calibrated
  cadence and pauses its idle clock during high host memory PSI. For an
  exec or bare-host lane without a placement request, daemon stall policy
  is `report`; a shared container is never a kill target.
- Declare `resources.cpus` (RG-48) on any container lane whose argv spawns
  workers by NAME (`-n auto`, `--workers auto`) — otherwise the worker
  count is decided by the CONTAINER's visible CPU count (which nothing
  capped) while the actual budget is decided by whatever else is running
  on the shared host; `doctor` names the gap when it sees one.
- Resource policy and consumer facts share `[resources]` on member lanes;
  sequence lanes have no runner of their own and reject a `resources` table.
  Use
  `io_sync = "durable"` for any lane that judges fsync, crash consistency,
  or storage durability; it is lane-only and rejects every `--io-sync`
  override, including `strict`. Use
  `io_sync = "relaxed"` only for ephemeral performance lanes that accept the
  eatmydata shim. `[resources.cowtree] mode` selects `auto` or `off`; with
  `auto`, an ephemeral lane passes `MDT_COW_ROOT` through at its unchanged
  same-path bind. CoW and repository bind paths cannot contain commas, colons,
  or newlines. See the [resource settings consumer contract](CONSUMERS.md#combined-resource-settings)
  for precedence and examples. `ram_target` and lane-only `candidate_ram`
  are parsed resource metadata and do not currently select workers or change
  container capacity.
- **Let `run-gate.footprint.json` set the budget, not a guess (RG-55).**
  Every lane kind is profiled now, bare-host included (RG-57) — a
  bare-host lane without the cgprofile daemon reachable gets a
  coarser `os.wait4()`-based number (`method: "rusage"`, peak memory is
  that lane's own child's peak RSS, not a cgroup read, not a sum across
  children, and never borrowed from another child; `footprint`/`doctor`
  disclose that caveat next to the number). A very light lane's reported
  peak also has a FLOOR: it can never read below run-gate's own resident
  memory at the moment it forked the lane's child (fork/COW accounting —
  RW-46b). When `memory.peak_bytes <= memory.floor_bytes`,
  `memory.peak_at_floor` is `true` and every surface above prints
  "(peak <= floor)" next to it — that lane's true footprint is smaller
  than or comparable to run-gate's own process, not necessarily the exact
  number shown; budget it as SMALL rather than reading the number
  literally.
  Once a lane has a handful of profiled runs, `run-gate footprint
  --write` distills a COMMITTED peak-memory/CPU/stall number for it —
  size `resources.memory`/`resources.cpus` off THAT (with headroom for the
  outlier `max`, not just the `median`), not off a number picked in a
  meeting. Re-run `footprint --write` when a lane's shape changes
  materially (more tests, a bigger fixture); `doctor` warns when the
  committed number has drifted from the live history or gone stale
  (`[footprint] tolerance_pct`/`max_age_days`), so a budget that quietly
  stopped matching reality does not stay silent.

**Anti-patterns, seen in the estate.**

- Lint under assay (a policy engine in the making); assay under a lint
  budget (a mutation lane with a 3-minute bound).
- `budget` under a pin table (inert for months, RG-32) and `clean_tree`
  under a pin table (inert, flips the lane's real setting when "cleaned up"
  by deletion — move it, do not delete it).
- A relative script path in a container command lane's `argv[0]` without
  `{worktree}` (exit 127 under a worktree-only mount, RG-34; `doctor` names
  it from 23.4.0).
- A conjunction lane that duplicates its sub-lanes' internals in a
  consumer config instead of pointing at `run-gate <lane>`.
- A directory of tests called "the gate" with no lane declaring it.

---

## 5. Remote and asynchronous lanes

Some lanes do not belong on the developer's host at all: nightly property
profiles, mutation campaigns over a whole module, fuzzing, and anything that
takes hours. T3 campaigns use the release-triggered host queue and result
ledger described above. Buildkite remains the scale-out path for additional
hosts or a browsable build history; it calls the same `run-gate <lane>` and
writes the same ledger rows. `REMOTE-LANES-BUILDKITE.md` carries the dated
supersession, enrollment manual and integration seams.

What a remote lane needs from its author is exactly what a local one needs
plus two things: **artifacts declared AND kept under `.assay/`** (the
pipeline generator cannot read a lane's `artifacts` — `--list` does not
carry them, by the no-second-parser rule — so its upload globs are fixed:
`<project>/.assay/**/*`, `<project>/.assay/*` and
`<project>/.run-gate/history.json`; a declared artifact anywhere else, such
as `run-gate-project/selftest`'s `coverage.json`, does not travel back until
RG-45 exposes `artifacts` through `--list --json`), and **a budget that
assumes nobody is watching** — `stall_timeout` for silence, `budget` for the
honest maximum, and assay's `--resume` (already passed by R-38) so a re-run
picks up where the last one stopped.

---

## 6. Best-practice checklist

Copy this into the lane's description or the PR that adds it; every line is
a yes/no.

- [ ] One concern; re-runnable alone; named for what it proves.
- [ ] Fast lanes first in the conjunction; the conjunction points at
      `run-gate <sub-lane>`, never at internals.
- [ ] Lint and types as command lanes; production code under an assay lane.
- [ ] Deterministic: offline, seeded, no order dependence, no xdist on the
      shared host, Hypothesis on the `gate` profile.
- [ ] Exit-code truth: no `|| true`, no argv filters, no silent skips;
      evidence printed on failure.
- [ ] Clean tree; judge pinned by digest; environment facts from the central
      config.
- [ ] Floors measured before set; R2 has `budget`, `budget_per_candidate`
      and (23.4.0) `stall_timeout`; `memory` declared, with `memory_max`
      where daemon placement of an exec/bare-host lane needs a ceiling.
- [ ] If profiling is configured, `[profile]` policy and `admission_wait` are
      documented for the adopter; endpoint environment setup and the explicit
      `RUN_GATE_PROFILE=off` opt-out are documented and exercised by `doctor`
      or `--dry-run`; no consumer starts or stops the daemon.
- [ ] Observer Assay lanes pass `ASSAY_RESOURCE_OBSERVER_SOCKET` with
      `required-env:` so an unset host fact fails before the judge runs.
- [ ] Artifacts declared if the lane may run remotely.
- [ ] `run-gate doctor` clean; the lane runs once from a clean checkout with
      the Run-Gate wheel installed.

---

## 7. TODOs (the open items this guide depends on)

Filed items carry their id; unfiled ones say where they would go.

| item | where | state |
|---|---|---|
| RG-55/P5 transport/watch/placement/admission seam | run-gate `KNOWN_ISSUES_TODO_BACKLOG.md` | P5 implementation and documentation in review; RG-56 atomic admission remains open |
| `stall_timeout` for container COMMAND lanes, judged from log-stream silence | run-gate RG-40 | OPEN, E-3 candidate (23.5.0) |
| `coverage_gate.py` line offsets under `--allow-dirty` | run-gate RG-39 | OPEN, first item of E-3 |
| Re-attach / follow a running lane after the client dies | run-gate RG-35 → 23.4.0 | in review |
| Timestamps and outcome buckets in assay's progress events | assay B065 | OPEN, E-2 (assay 5.1.0) |
| Durable state directory for resume across worktrees | assay B066 → run-gate RG-38 | OPEN, E-2 then E-3 |
| `budget = "unbounded"` when every unit is bounded | assay B067 | OPEN, E-4 |
| JS coverage via istanbul/lcov at R1 | assay A-O03 | half shipped, consumer wave |
| Security scanners as assay Tier 2 with a threshold vocabulary | assay A-O10 | deferred until a real integration |
| Declare `lint` lanes in each vbpub project's `run-gate.toml` (assay, srdm, nyxloom, ciu, cmru) and in dstdns | per project | not started; assay and srdm lint inside their gate scripts today |
| Hypothesis `gate` / `nightly` profiles in nyxloom and dstdns conftests | per project | not started |
| Schemathesis lane against a ciu instance for dstdns | dstdns, ciu v8 exec lane | waits for ciu v8 |
| Remote lanes: artifacts contract, pipeline generator from `--list`, image provenance on remote hosts, a collector keyed by commit | `REMOTE-LANES-BUILDKITE.md` §6 | design written, nothing implemented |

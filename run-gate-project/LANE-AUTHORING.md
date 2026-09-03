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

| lane | kind | what it proves | budget shape |
|---|---|---|---|
| `lint` | command | the code parses and has no obvious dead names; types check | seconds to a minute |
| `unit` | assay, R0 + R1 | the suite passes and covers the changed lines to the floor | the suite's own bound |
| `mutation` | assay, R2 | the suite kills the mutants in the judged scope | generous, with `budget_per_candidate` and (23.4.0) `stall_timeout` |
| `canary` | assay, R3 | the guarded test fails when its target is broken | per attempt, bounded |
| `integration` | command against an instance | the service behaves behind its real boundaries | the stack's bound |
| `gate` | command, conjunction | the whole implementation gate, in order | the sum |

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
4. **Mutation is its own lane, never folded into `unit`.** Different budget
   class, different frequency (per merge or nightly, not per edit), and the
   only lane where `--resume` and `--progress` do real work (R-38 passes both
   to every assay lane; they are no-ops without R2).
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

[lanes.mutation]
kind = "assay"
environment = "tester-unified"
assay_lane = "mutation"              # rigor R2; assay.toml owns the budget
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<ver>.pyz"]
# stall_timeout = "15m"              # from run-gate 23.4.0: silence, never elapsed time
[lanes.mutation.pins.assay]
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

- No network. The container environments are offline by construction; a
  test that needs the internet is an integration lane or a bug.
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
  is needed for a dedicated host, keep coverage xdist-safe and declare it
  in the lane's description.

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
- Pin the judge by digest (`pins.assay`); never "latest". Staleness is the
  cost and `cmru tool-deps --refresh assay` is the remedy at each assay
  release.
- Environment facts (image, slice, mounts) come from the central
  `run-gate.toml` — DERIVE or READ or FAIL, never a silent default (`AGENTS
  §4.2a`).

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
- From 23.4.0 a mutation lane gets `stall_timeout`: it stops the lane only
  when the container is still running AND the progress file has been silent
  that long, never on elapsed time. That plus a generous `budget` plus
  `budget_per_candidate` is the whole bounding story for R2 (RW-6).
- One gate container at a time across every agent on the host; cap it
  (`docker update --cpus=3`) right after launch while the host is shared.

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
  consumer config instead of pointing at `./run-gate.py <lane>`.
- A directory of tests called "the gate" with no lane declaring it.

---

## 5. Remote and asynchronous lanes

Some lanes do not belong on the developer's host at all: nightly property
profiles, mutation campaigns over a whole module, fuzzing, and anything that
takes hours. The estate's decision for those is **D-110.4 (dstdns decisions,
2026-08-20)**: additional lanes with large budgets, run by **Buildkite agents
on the remote hosts**, calling the SAME `./run-gate.py <lane>` — nyxloom, the
operator and a controller session are equal triggers. `CONSUMERS.md`
"Partner integration notes" carries the forward note; the enrollment manual
and the integration seams are in **`REMOTE-LANES-BUILDKITE.md`**.

What a remote lane needs from its author is exactly what a local one needs
plus two things: **artifacts declared AND kept under `.assay/`** (the
pipeline generator cannot read a lane's `artifacts` — `--list` does not
carry them, by the no-second-parser rule — so its upload globs are fixed:
`<project>/.assay/**`, `<project>/.assay/*` and
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
      `./run-gate.py <sub-lane>`, never at internals.
- [ ] Lint and types as command lanes; production code under an assay lane.
- [ ] Deterministic: offline, seeded, no order dependence, no xdist on the
      shared host, Hypothesis on the `gate` profile.
- [ ] Exit-code truth: no `|| true`, no argv filters, no silent skips;
      evidence printed on failure.
- [ ] Clean tree; judge pinned by digest; environment facts from the central
      config.
- [ ] Floors measured before set; R2 has `budget`, `budget_per_candidate`
      and (23.4.0) `stall_timeout`; `memory` declared.
- [ ] Artifacts declared if the lane may run remotely.
- [ ] `./run-gate.py doctor` clean; the lane run once from a fresh clone.

---

## 7. TODOs (the open items this guide depends on)

Filed items carry their id; unfiled ones say where they would go.

| item | where | state |
|---|---|---|
| This guide's backlog row (docs, no code) | run-gate `KNOWN_ISSUES_TODO_BACKLOG.md`, next free RG id after the 23.4.0 wave merges (RG-41 expected) | to file after the merge, to avoid an append conflict with RG-39/RG-40 |
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

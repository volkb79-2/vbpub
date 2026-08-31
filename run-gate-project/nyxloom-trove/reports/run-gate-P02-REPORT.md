# run-gate-P02 — REPORT

**Package:** RG-24, RG-23, RG-21, RG-25, RG-26 — five backlog entries, all in
`run-gate.py`, bundled to avoid five worktrees fighting over one file.
**Worktree:** `/workspaces/vbpub/.worktrees/run-gate-P02-checkpoint3-bundle`
**Branch:** `fix/run-gate-P02-checkpoint3-bundle`, based on `main` @ `858766d1`
**Revisions:** `__revision__` 23 → **28** (one bump per item)
**Chronology:** [`run-gate-P02-LOG.md`](run-gate-P02-LOG.md)

---

## READ THIS FIRST — the gate is red, and it was red before this package

`./run-gate.py selftest` exits **1** at `main` @ `858766d1`, untouched, and
still exits 1 at the tip of this branch. The failure is one test and is not
mine:

```
run-gate: DEFECT steps.run-tests.commands[0].argv[argv]: loading ../cmru/run-gate.toml: lane '[lanes.assay]': pin 'assay' sidecar tools/assay/assay-2.2.0.pyz.sha256 does not exist in this project (../cmru) — vendor it or shadow the lane
run-gate: validate-pointers FAILED: 1 defect(s) across 0 invocation(s) in ../cmru/cmru.toml
```

`cmru/tools/assay/` vendors `assay-2.3.0.pyz` + `.sha256`; `cmru/run-gate.toml`
still declares the **2.2.0** sidecar. Reproduced identically from the primary
checkout `/workspaces/vbpub` and from this fresh worktree, and `git ls-tree -r
HEAD -- cmru/tools/assay` lists only the 2.3.0 pair. **Filed as RG-29** and
deliberately NOT fixed: `cmru/` is outside this package's stated scope.

Because the selftest lane's argv is `pytest … && coverage_gate`, that red
pytest **short-circuits the diff-coverage floor entirely** — the gate can
never reach its coverage step while RG-29 stands. So every item below was
ALSO measured with exactly that one test deselected, which is the only way to
obtain a real diff-coverage verdict today. Both verdicts are pasted below,
per item, verbatim.

> A note on one number: in the per-commit sweep script I wrapped the gate in
> `|| true`, which means the `EXIT=` line in those raw logs is the wrapper's
> status, not the gate's — the AGENTS "read the exit status from the job,
> never from the wrapper" trap, walked into and then caught. The authoritative
> per-commit status is the tool's own `run-gate: lane 'selftest' exit N` line,
> quoted below. The branch tip was re-run without the wrapper to get an honest
> `GATE_EXIT`, also quoted.

---

## Per-item results, with verbatim gate verdicts

Each of the five commits was checked out detached on a clean tree and
re-gated, so every verdict below is a real run of that exact commit.

### Item 1 — RG-24 · `bd1a3f85` · rev 24

`resolve_container_name()` derived an exec-mode container's name from `repo`
— which `resolve_repo_and_worktree()` defines as the checkout owning the
shared `.git`, i.e. the MAIN checkout for any linked worktree. A
multi-instance ("Mode-B") worktree with its own rendered `ciu.global.toml`,
own network and own runner therefore had its lane exec'd into the main
landscape's container.

Precedence is now `declared container_name` > `<worktree>/ciu.global.toml` >
`<repo>/ciu.global.toml` — additive, as the entry requires: a worktree that
is not itself an adopted instance keeps repo-relative resolution
byte-for-byte. The resolution source gained a scope label (`judged worktree:`
/ `repo:` + path) and now appears in the pre-execution `container …`
disclosure, not only in the not-running refusal; a missing-config refusal
names both candidate paths when they differ.

The entry's disclosed workaround (a hand-rolled `docker exec` into the
correct instance) is **not** reproduced anywhere in the tool — it was
evidence-gathering, not a design.

Docs: SPEC `R-14a`; CONSUMERS "Python app estate with its own runner"
(worked disclosure line + an explicit "do not pin `container_name` as a
workaround"); CHANGES; backlog RG-24 → FIXED.
Tests: `TestWorktreeScopedContainerName` ×10.

```
run-gate: rev 24 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 222 passed, 2 skipped, 2 warnings in 36.53s
run-gate: lane 'selftest' exit 1
```

```
PYTEST_EXIT=0
diff-coverage OK: 9/9 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

### Item 2 — RG-23 · `c55f5748` · rev 25

Half 1 of the entry (this repo). The dstdns half stays open in its own repo.

The breaking change is now stated with its migration (SPEC `R-24a`,
CONSUMERS "BREAKING CHANGE — migrate if you use `mode = "exec"`", README),
and `--check-env` was **extended** rather than documented-away: an AST pass
that also sees a literal handed to the project's own env-reader helper — the
`_env_flag_enabled("RUN_LIVE_TESTS")` shape that hid the flag from the old
line regex — plus `setdefault`/`pop`/`"X" in os.environ`, with bound-method
parameter offsets accounted for. It stays advisory (exit 0); an unparseable
file is named and falls back to the regex.

Estate audit performed and kept as a test: **no** vbpub project declares
`mode = "exec"`, none declares `forward_env`, none references
`MOCK_MODE`/`RUN_LIVE_TESTS` in any `run-gate.toml`. Confirmed blast radius:
dstdns alone.

Tests: `TestEnvReferenceScan` ×9 + `TestEstateExecForwardEnvAudit`.

```
run-gate: rev 25 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 232 passed, 2 skipped, 2 warnings in 32.11s
run-gate: lane 'selftest' exit 1
```

```
PYTEST_EXIT=0
diff-coverage OK: 68/68 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

### Item 3 — RG-21 · `9adf11fc` · rev 26

Directions 2 and 3 of the entry. `doctor` emits ONE `[WARN]` when the project
declares a host lane AND the judged tree is a linked worktree whose gitdir
lies outside it, naming worktree, gitdir, the exact symptom and three
remedies; `[OK]` on a plain checkout with a host lane; nothing at all for a
container-only project. It never moves doctor's exit code.

Docs: SPEC `R-30a`; CONSUMERS "Host lanes that delegate to a
host-path-mounting harness (RG-21)" — the real srdm error verbatim, the
doctor line, and three pasteable harness-side fixes plus the
`SRDM_HOST_REPO_ROOT` note.
Tests: `TestLinkedWorktreeHostLaneWarning` ×7.

```
run-gate: rev 26 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 239 passed, 2 skipped, 2 warnings in 34.64s
run-gate: lane 'selftest' exit 1
```

```
PYTEST_EXIT=0
diff-coverage OK: 87/87 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

### Item 4 — RG-25 · `9a403da3` · rev 27

`doctor` and `--check-env` now ask the JUDGE what each `kind = "assay"` lane
needs (`<assay_command> lanes --json --file assay.toml`, assay ≥ 3.2.0 /
B044) inside the lane's environment, then check that environment for it.
run-gate still parses no `assay.toml`.

`build_env_probe_argv()` is the single in-environment probe builder; a test
asserts by source inspection that the only functions constructing a `docker
run`/`docker exec` argv are `run_container_lane`, `run_exec_lane` and
`build_env_probe_argv`. Ephemeral probes carry `--cgroup-parent` like any
container this tool starts, and SKIP rather than run unconfined.

`[FAIL]` names lane, tool AND environment (or an `assay_lane` the judge does
not declare, naming what it does). Every "could not determine this" is
`[SKIP]` with its reason, so an assay older than B044 can never turn a
healthy project red. Doctor's summary counts SKIPs; `--check-env` exits 2 on
a toolchain FAIL while its env-drift half stays advisory.

Docs: SPEC `R-34` (+ `R-01`, `R-30` amendments); CONSUMERS `kind = "assay"`
section (the stale "Once RG-25 lands…" sentence is replaced by the real
behaviour).
Tests: `TestAssayToolchainFitness` ×18.

```
run-gate: rev 27 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 257 passed, 2 skipped, 2 warnings in 34.39s
run-gate: lane 'selftest' exit 1
```

```
PYTEST_EXIT=0
diff-coverage OK: 182/182 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

### Item 5 — RG-26 · `7b30bc49` · rev 28

`run-gate <lane> --base REF` reaches a delegating assay lane as
`--request-base REF`, making assay's B019 (`judge.base_source = "request"`)
usable from the gate for the first time. Delegation is DERIVED from
`assay lanes --json` — **no new `run-gate.toml` key**, so the fact keeps one
spelling. Default ref is the judged worktree's `git merge-base HEAD
@{upstream}`; no upstream refuses rather than guessing. Conjunction lanes
propagate through a `{base}` token (RG-1/`R-25`'s mechanism). Every
non-delegating case refuses by name.

Also filed and fixed here: **RG-28** — `run_host_lane` raised
`KeyError('argv')` for `kind = "assay"` on `environment = "host"`, a config
the validator accepts (`R-04` calls a traceback for a legal config a defect).
Also filed OPEN: **RG-29** (the cmru pin above).

Docs: SPEC `R-35` + `R-19` amendment; CONSUMERS "Lanes that take their
comparison base from the gate"; README invocation list + a
derive-never-restate paragraph on the orchestration/judgment split; CHANGES.
Tests: `TestComparisonBasePassthrough` ×16.

```
run-gate: rev 28 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 273 passed, 2 skipped, 2 warnings in 34.70s
run-gate: lane 'selftest' exit 1
```

```
PYTEST_EXIT=0
diff-coverage OK: 243/243 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

### Branch tip, re-run without the `|| true` wrapper

```
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 273 passed, 2 skipped, 2 warnings in 33.68s
run-gate: artifact: /workspaces/vbpub/.worktrees/run-gate-P02-checkpoint3-bundle/run-gate-project/coverage.json
run-gate: lane 'selftest' exit 1
GATE_EXIT=1
```

Baseline for comparison, `main` @ `858766d1`, untouched:

```
run-gate: rev 23 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 212 passed, 2 skipped, 2 warnings in 38.45s
run-gate: lane 'selftest' exit 1
EXIT=1
```

**Net:** 212 → 273 passing, same single pre-existing failure, no new
failures, diff coverage 100% at every commit.

---

## Design decisions — where an entry offered a choice

### D1 (RG-24) — what the workaround told me, and what I refused to build

The entry discloses a workaround: a hand-rolled `docker exec -w <worktree>
<correct-container>`. It is evidence-gathering, not a design, and it is not
reproduced anywhere. The entry also names the tempting non-fix — pinning
`container_name` in the tracked `run-gate.toml` — and explains why it is a
shadowing default: correct for exactly one running instance, wrong for the
next worktree created. CONSUMERS now says that in the operator's own words so
the next person under time pressure does not "fix" it that way.

### D2 (RG-24) — the disclosure change nobody asked for

Strictly, the entry asks only for the precedence. I also moved the resolution
source into the live disclosure line and added a scope label. Reason: the
defect's real cost was not that the wrong config was read, it was that
**which config had been read was never printed**, so a wrong container looked
exactly like a right one. A precedence fix without visibility leaves the next
occurrence just as silent.

Test consequence worth stating: the regression oracle keeps BOTH containers
present and RUNNING in the fake `docker ps`. With only the correct one
present, the pre-fix code fails by a "not running" refusal — which is a
different assertion, and would have passed a wrong implementation that
resolved correctly by accident.

### D3 (RG-23) — EXTEND `--check-env`, not "document the limitation"

The entry offers both. I chose EXTEND, and the argument is the estate's own:
the sweep had already certified a clean bill of health over the exact
variable whose absence turned an all-skipped pytest run green. A check whose
comparison is narrower than its message issues a false certification, which
AGENTS calls worse than having no check at all. Documenting that away would
have left the false certificate in place.

I did **not** restore the hardcoded allowlist. That would re-create the
shadowing default the declarative key removed; the entry itself frames the
mechanism change as correct and the missing migration as the defect.

Two smaller calls inside D3: the sweep deliberately does **not** flag
ordinary dict reads (`cfg["NOT_AN_ENV_VAR"]`) — a superset refusal trains its
consumers to ignore it — and bound-method `self`/`cls` offsets are handled,
because getting that wrong does not merely miss a read, it reports a
confident name taken from the wrong argument position.

### D4 (RG-25) — **DEVIATION, needs controller review before merge**

The entry says: "`command -v` each of its `external_tools` and its `argv0`
inside the environment."

I implemented `external_tools` ∪ `argv0` ∪ a small `language` toolchain table
(`javascript` → node, npm; `go` → go). Why:

- assay 3.2.0's own `docs/CONSUMERS.md` states that `external_tools` is `()`
  for **every shipped adapter** (python, sql, go, javascript) and says
  explicitly: *"a gate consumer should not build a `MISSING_EXTERNAL_TOOL`
  preflight around this field expecting it to name node/npm for a javascript
  lane — that check today has to come from `language` itself."*
- Following the letter alone therefore ships a check that reports `[OK]` for
  a JavaScript lane in an environment with no Node — precisely the gap the
  entry was filed to close, and precisely the false-certification class
  AGENTS forbids.
- The entry's **own** example output, `[OK] lane 'ui-unit' toolchain: node,
  npm`, is unreachable from `external_tools` today and reachable only via
  `language`. The two readings agree on the intended outcome.

Guard rails on the table, because a literal standing in for a fact with an
authoritative source is exactly the hazard AGENTS names: it holds only the
two languages assay documents, its comment cites that source, and an
**unmapped** language attaches a caveat to the report line
(`language 'rust' has no toolchain fact run-gate knows — only
argv0/external_tools were verified`) instead of being silently read as
"nothing needed". The right long-term fix is assay populating
`external_tools` per adapter, at which point this table shrinks to nothing.

### D5 (RG-25) — "one probe builder" read as one way to REACH an environment

The acceptance asks that grep prove ONE in-environment probe builder shared
with the pin probe. The pin `--version` check is not a separate probe path at
all — it is a shell fragment inside the lane's own inner command. So the
honest reading is: create ONE builder and never a second, reusing the
existing reach-an-environment primitives. `build_env_probe_argv()` calls
`resolve_container_name()` (exec) and `physical_path()`/`dual_mount_flags()`
(ephemeral) rather than restating them. A source-inspection test pins that
only `run_container_lane`, `run_exec_lane` and `build_env_probe_argv`
construct a `docker run`/`docker exec` argv.

The probe is attached and captured where a judged lane is detached (`-d` →
`wait` → `logs`, `R-17`, which exists so a lane's status cannot be forged
over a lying transport). That difference is safe **here and only here**,
because a probe's result becomes a preflight line and never a verdict — this
is stated in the function's docstring so it is not silently copied.

### D6 (RG-25) — SKIP vs FAIL, and what `doctor` may start

FAIL is reserved for facts the inventory established. Everything meaning "I
could not determine this" is SKIP with its reason: older judge, non-JSON,
`inventory_schema != 1` (with the value), host environment, docker absent, no
slice derivable. This is the entry's own rule ("NEVER a FAIL for an older
judge — the pin declares the version, run-gate must not require a floor it
never declared") applied uniformly.

Two consequences I made visible rather than hid:
- doctor's summary now reports a SKIP count. An uncounted status is the
  "absence for emptiness" anti-pattern one level up: silence would read as
  health.
- `R-30` said doctor "runs nothing". It now may start at most one short-lived
  read-only probe container per assay environment. I amended `R-30` rather
  than letting the spec quietly become false.

### D7 (RG-25 vs RG-26) — a deliberate asymmetry on `host` environments

RG-25 SKIPs toolchain fitness for a `host`-environment assay lane; RG-26
probes it. Not an oversight:

- RG-25 asks *"is this declared ENVIRONMENT fit for the lane"* — a question
  about a container image or runner the operator cannot easily inspect. On
  `host` the answer is "look at your own `PATH`", and the lane's own run says
  so immediately.
- RG-26 asks *"what does this LANE declare about its comparison base"* — a
  question about the lane, not the environment, so it must be answerable
  everywhere, including on the host.

`build_env_probe_argv()` supports both; only the callers differ.

### D8 (RG-26) — the probe runs on every assay invocation, and that is a cost

Delegation must be known even when `--base` is absent, because a delegating
lane invoked bare still needs the merge-base default. So an assay lane
invocation now issues one short read-only inventory probe before the judged
run. I did not hide this: SPEC `R-35` and CONSUMERS both state it, and the
CHANGES entry names it as the price of not adding a `run-gate.toml` key.

### D9 (RG-26) — resolving the entry's internal tension about conjunctions

The entry asks for two things that collide: *"conjunction lanes propagate
`--base` to every sub-invocation"* and *"a lane that does NOT delegate,
invoked with `--base` → exit 2"*. A conjunction IS a command lane that does
not itself delegate.

Resolved with RG-1's own mechanism rather than a new one: a `{base}` token in
the conjunction lane's argv means "this conjunction propagates a base", and
is substituted into every sub-invocation. A command lane **without** the
token, given `--base`, refuses — the ref could otherwise only be silently
dropped, which is `R-25`'s hazard class verbatim. A `{base}`-carrying lane
resolves its ref by the same policy as a delegating assay lane, so on an
upstream-less tree it refuses instead of substituting an empty string.

### D10 (RG-26) — the incidental RG-28 fix, and why it is in this commit

RG-26's contract covers every `kind = "assay"` lane. On
`environment = "host"` such a lane died with `KeyError('argv')` before this
work and after it — `_validate_lane` accepts the combination, so this was a
traceback for a legal config, which `R-04` names a defect outright. Fixing it
is three lines reusing `build_assay_inner`. I filed it as RG-28 and marked it
FIXED in the same change rather than leaving a known traceback behind a
feature that now reaches it. A reviewer who wants it split out can do so
cleanly: it is one hunk in `run_host_lane` plus the `R-19` amendment.

---

## Things a reviewer should look at first

1. **RG-29 / the red gate.** Nothing in this package can make
   `./run-gate.py selftest` green. Confirm the baseline for yourself
   (`git stash` nothing needed — run it on `858766d1`) before attributing the
   red to this branch. And note the second-order effect: the diff-coverage
   floor never executes while it stands.
2. **D4, the RG-25 deviation.** It is the one place I did not follow an
   entry's literal instruction. The argument is above; the mitigation is the
   caveat line and a two-entry table.
3. **The probe cost (D8)** — one extra short container per assay lane run.
   Acceptable to me given the alternative is a duplicated config key; a
   controller may weigh it differently.
4. **`doctor` now starts containers (D6).** `R-30` was amended. If the
   estate's position is that `doctor` must be strictly non-executing, RG-25's
   contract needs renegotiating with the entry's author, not silently
   narrowing here.
5. **Four pre-existing tests were asserting against the wrong docker call**
   after RG-26 added the probe, and one of them (`test_exec_assay_lane_
   judges_selected_worktree`) still *passed* while doing so. `lane_runs()` /
   `lane_execs()` were introduced and that test strengthened. Worth a look:
   if there are other places in the suite where "the first docker call" is
   assumed to be the lane, they are latent.

## Not done, deliberately

- **RG-23's dstdns half** (`forward_env` + `required_env` in
  `/workspaces/dstdns/run-gate.toml`): another repo, cross-repo pointer in
  the entry, out of scope.
- **RG-21's direction 1** (mount the common gitdir / hand over `GIT_DIR`):
  harness-side, in `shared-ramdisk-depot-manager/tools/gate.sh`. Building it
  here would mean run-gate reaching into a consumer's own container
  construction — the inversion the one-parser design (D-110) exists to
  prevent. Three pasteable fixes are documented for the harness author.
- **RG-29** (cmru pin): outside scope, filed.
- **`rigor` vs `rigor_reachable`** (RG-25 adjacent): the inventory also
  exposes rigor levels a lane declares that this assay build cannot reach for
  its language — a second preflightable mid-run refusal. Not in the entry's
  contract; noted in the backlog for a future entry rather than smuggled in.

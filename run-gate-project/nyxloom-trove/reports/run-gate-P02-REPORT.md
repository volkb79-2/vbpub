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

## Review round 2 — B1, B2, B3 + S3, S8, N1 (rev 28 → **29**)

Adversarial review returned ACCEPT-conditional: three doc/text blockers, no
code redesign, and three decision-asks the controller ratified (**D4** the
`language`-table deviation, **D6** doctor starting probe containers, **D8**
the probe running on every assay invocation rather than lazily). All three
ratifications are recorded above unchanged; nothing was re-litigated.

### B2 — the cost claim was quantitatively false; batched, not reworded

`R-30` promised "at most one short-lived, read-only probe container per assay
environment". The reviewer measured **4 containers for 3 lanes on one
environment**. Root cause: the inventory probe was cached per (environment,
`assay_command`), but `probe_missing_tools` ran once per LANE, each building
its own `docker run --rm`.

Taken the better of the two offered fixes: **batched**, not reworded.
`assay_toolchain_findings()` is now two passes — pass 1 asks the judge and
decides per lane; pass 2 runs ONE `command -v` probe per environment over the
UNION of every lane's tools. What is on a `PATH` is a property of the
environment, not of the lane asking. Each lane is still judged against its
own tool list, so batching cannot smear one lane's missing tool onto another
— which is its own oracle
(`test_batched_tool_probe_still_names_only_each_lane_own_missing_tool`: three
lanes, one needing `node`/`npm`, exactly one FAILs). The count itself is now
a test
(`test_probe_cost_is_one_inventory_plus_one_tool_probe_per_environment`),
because a cost stated in a spec and not measured is a cost that drifts —
which is exactly how this one drifted.

One deliberate omission: pass 2 has **no** `except GateError` guard, with a
comment saying why. An environment only reaches pass 2 after pass 1 already
built a probe argv for it through the same `_probe_slice`/`dual_mount_flags`
path with the same inputs, so a defensive except would be a branch no test
could ever redden — and an unreddenable branch is the thing the diff-coverage
floor exists to keep out.

### B1 — `--dry-run` starts a container, and three places said otherwise

Correct and now fixed in all three: SPEC `R-28`, `usage()`'s `--dry-run`
block, and the argparse `--dry-run` help. All three now state that an assay
lane's read-only inventory probe runs — it is *what resolves the base the
printed plan must show*, so it cannot be skipped — and that `--dry-run`'s
real promise is that **no judged lane starts**. `R-28` says outright that
"nothing runs" was true before `R-35` and is not true now, rather than
quietly softening.

**One correction to the review, stated because the REPORT's value is that it
is accurate.** The relayed finding says
`test_assay_lane_dry_run_discloses_no_verdict` "is misnamed/wrong — its
`EXEC_LANE` fixture is `kind="command"`, so it never actually exercised an
assay lane's dry-run". I checked the source rather than acting on it: that
test builds its config from `SIMPLE_LANE` with `kind = "command"` replaced by
`kind = "assay"` and the argv swapped for `assay_lane`/`assay_command` — it
*is* an assay lane, and its name is accurate for what it asserts (no verdict
artifact disclosed). `EXEC_LANE` belongs to the neighbouring
`test_exec_lane_dry_run_rehearses_runner_preflight`, which is a command lane
by design and correctly named. **The substantive half of the finding is
entirely right and is fixed:** its only structural assertion was
`lane_runs(log) == []`, which filters on `-d` and therefore *cannot see the
probe at all*, so no test pinned what a dry run actually executes. Added
`test_assay_lane_dry_run_runs_the_probe_and_no_judged_container`, which pins
the exact call set — exactly one `docker run`, carrying `--rm` and not `-d`,
whose script is `lanes --json`; zero `docker exec`; zero judged containers.

### B3 — "doctor starts containers" now said everywhere, not just in SPEC

Added to `CONSUMERS.md` (a call-out box with the bounded cost and the "if you
run `doctor` where starting a container is unacceptable, this is the check to
know about" line), `README.md`, the `CHANGES.md` RG-25 entry, and `usage()`'s
`doctor` block. `cmd_doctor`'s docstring no longer claims "pure
recomposition — every check here already exists on the run path": it now
names check 5 as the exception, says fitness *cannot be read, only observed*,
and states the bounded probe count.

### S3, S8, N1 — taken

- **S3** — RG-28 (the host-assay `KeyError('argv')` fix) had no dedicated
  oracle. Added `test_host_assay_lane_actually_builds_the_assay_inner`: a
  judge shim that echoes its own cwd and argv, asserting the executed inner
  is the real assay inner (`run ui_unit --file assay.toml`,
  `--verdict-json .assay/verdict-ui_unit.json`, `--request-base`), that cwd
  is the effective project dir, and that `mkdir -p .assay` actually ran.
  Gutting the branch back to `lane["argv"]` reddens it with `KeyError`;
  replacing the inner with `"true"` reddens every assertion.
- **S8** — `ASSAY_LANGUAGE_TOOLCHAIN["go"]` was executed but never asserted,
  so gutting it to `()` reddened nothing. Added `test_go_language_derives_
  the_go_toolchain` plus `test_language_toolchain_is_unioned_not_replaced`,
  which pins the composition order (language → `external_tools` → `argv0`)
  and de-duplication that the OK line's text depends on.
- **N1** — my own RG-29 filing under-prescribed its fix. Checked
  `cmru/run-gate.toml` directly: the vanished filename appears **four**
  times, not two — the pin `sha256`, the pin `version` (verified in-lane per
  RG-4, so a stale value fails loudly), `[lanes.assay] assay_command` (:21),
  and `[lanes.mutation] argv`'s `--assay-zipapp` (:55). The last is inside a
  free-form shell string that `validate-pointers` deliberately never stats
  (`R-22`: argv strings are shell text, not declared paths), so **nothing
  will tell a fixer about it**. All four are named, and the acceptance now
  includes `grep -n 'assay-2\.2\.0' cmru/run-gate.toml` returning nothing.

### Logged, not chased (per instruction)

S1 (probe-failure misdiagnosis in exec mode), S2 (host-assay writes a fixed
shared `/tmp` gitconfig path), S4 (the env-forward audit test would not catch
dstdns's actual pattern), S5 (AST helper-name resolution false-positive
class, zero live blast radius), S6 (`rigor_reachable` note buried in a FIXED
entry, wants its own RG-30), S7 (the probe now runs before the clean-tree
refusal), S9 (python/sql lanes will show a caveat). None was folded in: each
needs a judgement call I would rather have made deliberately than in the
margin of a fix round. S2 and S7 look to me like the two worth filing next —
S2 because a fixed shared path under `/tmp` is a real multi-tenant hazard,
S7 because "probe ran, then the gate refused for a dirty tree" is a wasted
container and a confusing order of output.

### Verdicts after the review round

```
run-gate: rev 29 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 279 passed, 2 skipped, 2 warnings in 40.20s
run-gate: lane 'selftest' exit 1
GATE_EXIT=1
```

```
PYTEST_EXIT=0
diff-coverage OK: 258/258 changed executable lines covered (100.0% ≥ 100.0% floor)
COVGATE_EXIT=0
```

273 → 279 passing: six new tests — two B2 oracles, the B1 dry-run call-set
test, the S3 RG-28 oracle, two S8 toolchain oracles. Changed executable lines
243 → 258 rather than 243 + n, because B2's rewrite REPLACED part of the
earlier RG-25 diff rather than only adding to it.

---

## For merge verification — the exact procedure, since the gate will still be red

RG-29 stays open, so `./run-gate.py selftest` will exit 1 at merge time for a
reason unrelated to this branch, and — because the lane argv is
`pytest … && coverage_gate` — the `&&` means **the coverage step never runs**,
so the gate can produce no coverage verdict at all while that stands. Run
these three steps, from `run-gate-project/`, reading each verdict in a
separate step (never a pipe tail):

```bash
# 1. The real gate, unwrapped. Expect exit 1 with EXACTLY ONE failure,
#    test_cmru_release_step_names_a_real_lane. Any other FAILED line is a
#    genuine regression in this branch.
./run-gate.py selftest > /tmp/verify-gate.log 2>&1
echo "GATE_EXIT=$?" >> /tmp/verify-gate.log
grep -E '^FAILED|passed|^run-gate: lane|GATE_EXIT' /tmp/verify-gate.log

# 2. Confirm the one failure is RG-29 and not something new.
python3 run-gate.py validate-pointers ../cmru/cmru.toml   # expect exit 2, assay-2.2.0.pyz.sha256

# 3. The diff-coverage floor the red gate short-circuits, measured directly
#    with EXACTLY that one test deselected — the same argv the lane would
#    have run, nothing else changed.
python3 -m pytest tests -q --cov=. --cov-branch --cov-report=json:coverage.json \
  --deselect tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane \
  > /tmp/verify-cov.log 2>&1
echo "PYTEST_EXIT=$?" >> /tmp/verify-cov.log
python3 tools/coverage_gate.py --repo . --base main --coverage-json coverage.json \
  --source run-gate.py >> /tmp/verify-cov.log 2>&1
echo "COVGATE_EXIT=$?" >> /tmp/verify-cov.log
grep -E 'PYTEST_EXIT|COVGATE_EXIT|diff-coverage' /tmp/verify-cov.log
```

Expected at this branch's tip: step 1 `GATE_EXIT=1` with one FAILED line;
step 2 exit 2 naming `tools/assay/assay-2.2.0.pyz.sha256`; step 3
`PYTEST_EXIT=0` and `diff-coverage OK: …100.0% ≥ 100.0% floor`,
`COVGATE_EXIT=0`.

Two traps, both walked into during this package and both worth avoiding:
**never wrap the gate in `|| true`** (the `EXIT=` you then read is the
wrapper's, not the gate's — the raw per-commit sweep logs in the scratchpad
have exactly that defect, which is why the authoritative status quoted above
is the tool's own `run-gate: lane 'selftest' exit N` line); and **do not run
the coverage gate against an uncommitted tree** — it maps `git diff main
HEAD` line numbers onto working-tree coverage, so any uncommitted edit skews
the mapping and reports phantom uncovered lines. Commit first, then measure.

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

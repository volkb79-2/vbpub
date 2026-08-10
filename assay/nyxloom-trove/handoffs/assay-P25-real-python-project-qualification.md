---
schema_version: 1
id: assay-P25-real-python-project-qualification
project: assay
title: "Installed Assay agrees with a pinned real Topos R1 evaluator"
tier: implement-2
input_revision: "9f522a72d37b9cb5beb1939ceca1978c9fc4ef23"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P24-versioned-wheel-contract]
session: fresh
scope:
  touch: ["assay.toml", "gate/python/**", "tools/tester-unified-gate.sh", "nyxloom-trove/nyxloom.toml", "tests/test_python_qualification.py", "nyxloom-trove/reports/assay-P25-real-python-project-qualification-LOG.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay", "pyproject.toml", "nyxloom-trove/carve-assets/P20", "nyxloom-trove/carve-assets/P21", "nyxloom-trove/carve-assets/P22", "nyxloom-trove/carve-assets/P23", "nyxloom-trove/carve-assets/P24", "nyxloom-trove/carve-assets/P25"]
oracles:
  - id: O1
    observable: "The gate's current installed Assay wheel runs all 2,923 pinned Topos tests plus the probe in a committed snapshot, emits the locked complete v4 PASS artifact, and agrees exactly with the copied Topos evaluator and hand line manifest"
    negative: "A targeted-only/hello-world fixture, source import, omitted tracked fixture, alternate wheel, or universal-PASS producer cannot satisfy the full artifact and both independent witnesses"
    gate: tester-unified
  - id: O2
    observable: "A separately installed clean-tagged 1.2.5 wheel is hash-bound through P24's verifier and pip, while PASS, one missing line, excluded-policy asymmetry, and comment-only 0/0 produce the frozen Assay/Topos/manifest matrix"
    negative: "A wrong root, 0/0 assumption, exclusion collapse, check/use gap, or producer-authored expected result changes at least one complete comparison"
    gate: tester-unified
  - id: O3
    observable: "The exact pinned Topos tree, root ignore policy, three explicit absolute-symlink adoption deletions, five retained relative symlinks, 965-entry baseline index, command plan, wheel identity, and consumer cleanliness are checked before and after every scenario"
    negative: "Ordinary git-add silently omits tracked Docker fixtures, an unsafe symlink is filtered without a disposition, a real checkout is written, or a container bypasses the registered verified cgroup/network boundary"
    gate: tester-unified
  - id: O4
    observable: "Missing/stale profile, dirty consumer, base-is-HEAD, command-created dirt, command-created HEAD move, wrong source root, installed-source exposure, changed input OID, and universal-PASS artifact mutation each fail with their frozen terminal or at the independent complete-artifact boundary"
    negative: "Any corruption remains green because evidence was copied from Assay output, only the terminal string was compared, or a compatibility/default route remained reachable"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the pinned tester-unified image cannot run the exact full Topos suite and frozen command environment offline"
  - "the exact three-symlink disposable compatibility patch no longer preserves the full Topos test answer"
  - "a correct implementation requires Assay production code, a P24/P25 locked-asset edit, or a real Topos edit"
  - "Assay and the copied Topos evaluator disagree outside the explicitly frozen exclusion-capability asymmetry"
mutexes: [merge-lane]
---

# P25 — real Python-project qualification

The claim to attack is deliberately narrower and more truthful than the
provisional wording: **the current installed Assay product and a separately
hash-installed clean release wheel give the same R1 answers as Topos's copied
independent evaluator on a pinned real Topos source/suite, after one explicit
consumer-compatibility patch.** This is qualification, not Topos adoption.

## Dispatch contract

- Contract class: **2d — constrained implementation**.
- Required roles: **Sonnet xhigh implementer → fresh Opus xhigh independent reviewer**.
- Readiness: **READY only from the Sol freeze commit containing
  `nyxloom-trove/reports/assay-P25-JIT-CARVE.md` and the locked P25 packet.**
- Degrees of freedom: private helper names, dataclass decomposition, and
  equivalent local test decomposition only. Pins, paths, command/env, deletion
  set, line manifests, complete artifact templates, scenario terminals,
  installed-wheel ownership, marker order, and no-adoption boundary are fixed.

## Worktree and branch

Work only in
`/workspaces/vbpub/.worktrees/assay-P25-real-python-project-qualification` on
branch `feat/assay-P25-real-python-project-qualification`, created from the
Sol freeze commit named by the controller.

## Context to read first

Read exactly these, in order:

1. `nyxloom-trove/carve-assets/P25/README.md`,
   `topos-input-manifest.json`, `qualification-manifest.json`, and
   `skeleton/qualify_topos.py` — normative construction and pins.
2. `nyxloom-trove/carve-assets/P25/test_acceptance.py` and both
   `expected/*-v4-template.json` files — independent quick acceptance and
   complete artifact shapes. Do not edit any P25 asset.
3. `nyxloom-trove/reports/assay-P25-JIT-CARVE.md` §§Result, Resolved blockers,
   Frozen implementation packet, and Adversarial review.
4. `gate/distribution/release_wheel.py`, `assay.toml`,
   `tests/test_self_lane.py::test_lane_budget_agrees_with_the_gate_timeout`,
   and the build/install functions in `tools/tester-unified-gate.sh` — landed
   P24 helper, the coupled lane/registered-gate budget, current wheel/run venv,
   tester-site `.pth`, outer Docker/cgroup/network, and marker ownership.
5. At pinned revision `9f522a72d37b9cb5beb1939ceca1978c9fc4ef23` only:
   `.gitignore`, `topos/pyproject.toml`, `topos/tests/conftest.py`,
   `topos/tools/coverage_gate.py`, and `topos/tests/test_config.py`.
   The manifest already names every other Topos fact; do not re-orient through
   966 files.
6. Canonical `nyxloom/reference/DOCTRINE.md` §§1–4 and
   `nyxloom/reference/AUTHORING.md` §3b.

## Environment setup

The implementer runs only the quick locked suite and focused tests. The
controller owns the authoritative registered gate, whose declared argv is
`bash {worktree}/assay/tools/tester-unified-gate.sh {worktree}`:

```text
python -m pytest nyxloom-trove/carve-assets/P25/test_acceptance.py -q -p no:randomly
```

The live proof runs only when `tools/tester-unified-gate.sh` is already inside
the same uid-complete, validated-background-cgroup, `--network=none`
`tester-unified:local` container P24 established. P25 starts no Docker process
and never invokes Topos's stale outer gate argv.

P25's additional full Topos phase widens the registered gate's declared
`timeout_seconds` from 1,800 to exactly 3,600. `assay.toml` is a coupled
configuration owner: change its existing R0 self-lane `budget` from `30m` to
exactly `60m`. Do not weaken or bypass
`test_lane_budget_agrees_with_the_gate_timeout`; it is the anti-drift oracle
that requires those two independently loaded declarations to agree (A-207).

## Implementation packet (normative)

### 1. Owned production interface

Copy `nyxloom-trove/carve-assets/P25/skeleton/qualify_topos.py` to
`gate/python/qualify_topos.py` and complete its TODO bodies without changing
these signatures:

```python
verify_pinned_inputs(source_repo: Path) -> None
install_locked_release(*, source_repo: Path, scratch: Path) -> tuple[Path, str]
materialize_scenario(*, source_repo: Path, scratch: Path, spec: ScenarioSpec) -> tuple[Path, Path, Path, str, str]
run_scenario(*, source_repo: Path, scratch: Path, assay_executable: Path, assay_version: str, spec: ScenarioSpec) -> ScenarioResult
normalize_artifact(document, *, assay_version, base_oid, head_oid, witness, pytest_log) -> dict[str, Any]
compare_complete_artifact(*, actual, template, assay_version, base_oid, head_oid, witness, pytest_log) -> None
qualify(*, source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> tuple[ScenarioResult, ...]
```

CLI grammar is exact:

```text
qualify_topos.py --source-repo ABS --scratch ABSENT_ABS
                 --current-assay ABS --current-version VERSION
```

Success prints exactly `ASSAY_P25_TOPOS_QUALIFIED=1` after every comparison.
Any failed premise/comparison is non-zero and prints no success marker.

### 2. Explicit input topology and adoption precondition

The source is the Git object at:

```text
vbpub input commit       9f522a72d37b9cb5beb1939ceca1978c9fc4ef23
topos tree               1bc8a51296b74e536bf60b534efb2fc938dcc389
topos tracked entries    966
root .gitignore blob     b0a3fe4a8cabd6ebdebc4fc4aa4a4a8623bd8dbe
Topos comparator blob    6b96e8711b6c0a2e20456e74626804cd502cf9b7
```

Export only pinned `.gitignore` and `topos/`. Before Git init, enumerate all
eight symlinks. Retain the five relative contained links and require exactly
the following three absolute links to target `/etc/passwd`, then delete exactly
them from the disposable baseline:

```text
topos/tests/fixtures/inspect_files/_danger/passwd_link
topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape
topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current
```

This deletion is not an Assay workaround: A-186 correctly refuses absolute
links in a whole-topology higher-rigor snapshot. It is an explicit prospective
Topos-owned adoption precondition. The P25 REPORT must say that the unmodified
current Topos tree is **not directly adoptable**.

Create tracked root `.assay/.gitignore` with exact bytes `*\n!.gitignore\n`.
Force-add the exact exported set (`git add -f`), never ordinary `git add`:
ordinary add reinterprets the carried root ignore policy and silently omits
four already-tracked Docker-log fixtures, making 13 full-suite tests fail while
the targeted qualification stays green. Require exactly 965 indexed baseline
paths (966 − 3 + root `.gitignore` + `.assay/.gitignore`). Commit with the
skeleton's fixed identity/time.

HEAD adds exactly: one frozen source probe, one test, byte-identical
`coverage-witness.py`, and complete `assay.toml`. No Topos production source is
changed. The real vbpub/Topos checkout is hash/status checked before and after.

### 3. Two installed Assay owners; no alternate selection

1. **Current owner:** the already built `scratch/run-venv/bin/assay` and exact
   P24-discovered version passed by the gate. It runs the primary full-suite
   proof and all integrity negatives, so future Assay source changes remain
   externally qualified.
2. **Release owner:** copy the locked P25 wheel and manifest byte-for-byte into
   `gate/python/release/P25/`. Run P24's `release_wheel.py verify`, write its
   sole stdout line to a new requirements file, then use offline pip
   `--no-index --require-hashes -r`. Attach tester-unified site-packages with
   the same `.pth` pattern as P24. Require metadata version ==
   `assay.__version__` == `1.2.5`, import below that venv and outside
   `assay/src`. It runs the targeted release smoke.

Never glob/select a wheel, rebuild one at runtime, copy P24's fixture, accept a
source import, or turn the fixed release fixture into the ongoing current-head
proof.

### 4. Exact command and environment

Promote every file under P25 `fixtures/` byte-for-byte to
`gate/python/fixtures/P25/`. The disposable wrapper produces one relative
artifact for Assay, then copies those exact bytes once to the externally named
comparator witness. It never parses Assay output or coverage JSON.

Primary argv:

```text
/opt/tester-venv/bin/python topos/tools/assay_p25_coverage.py
topos/tests -q -n auto
```

Targeted argv replaces the final list with:

```text
topos/tests/test_config.py topos/tests/test_assay_probe.py -q -n 2
```

The wrapper appends exactly:

```text
--cov=topos/src/topos --cov-branch
--cov-report=json:.assay/topos-coverage.json
```

Lane environment is a closed map—`env_passthrough=[]`—containing exactly the
dynamic absolute `ASSAY_P25_WITNESS`/`ASSAY_P25_LOG` plus:

```text
PYTHONPATH=topos/src:topos
PATH=/opt/tester-venv/bin:/usr/local/bin:/usr/bin:/bin
HOME=/home/tester
XDG_CACHE_HOME=/home/tester/.cache
XDG_CONFIG_HOME=/home/tester/.config
XDG_STATE_HOME=/home/tester/.local/state
LANG=C.UTF-8
LC_ALL=C.UTF-8
```

These are verified image facts, not defaults. Removing the identity/PATH map
makes the full suite fail while the 19-test targeted lane remains green.

### 5. Three independent witnesses and complete artifacts

For every common-semantics scenario:

1. installed Assay emits v4;
2. the hand manifest fixes line identity and the complete normalized artifact;
3. the unmodified pinned `topos/tools/coverage_gate.py` parses the same copied
   profile bytes and base→HEAD diff.

Only `assay_version`, `commit`, `started`, `ended`, resolved base, witness path,
and pytest-log path are normalized, after their real values are checked. Every
other field compares exactly with a locked template. Never construct an
expected field from Assay's output.

Topos cannot express exclusion provenance. With `allow_excluded=true`, require
common parity. With `false`, Assay must return `FAIL/EXCLUDED_LINES`; Topos's
PASS is recorded as the expected capability asymmetry, not compared as a
terminal.

### 6. Scenario/terminal table

| scenario | Assay | copied Topos | hand source |
|---|---|---|---|
| current full suite + pass probe | exact v4 PASS; 5/5; excluded line 11 recorded | PASS 5/5 | pass template + literal lines |
| release targeted smoke | same PASS under 1.2.5 installed via hashes | PASS 5/5 | release manifest + pass template |
| missing branch | FAIL/UNCOVERED_LINES; missing line 7; 4/5 | FAIL 4/5, line 7 | missing template |
| excluded allowed | PASS; exclusion recorded | PASS; line absent | hand manifest |
| excluded forbidden | FAIL/EXCLUDED_LINES | expected PASS, not terminal-compared | hand manifest |
| imported comment-only module | PASS 0/0, considered=1 | PASS 0/0 | literal comment file |
| absent/stale copied profile | NO_MEASUREMENT/EMPTY_COVERAGE | not compared | exclusive witness state |
| consumer dirty before run | NO_MEASUREMENT/DIRTY_TREE; no command | not compared | seeded status |
| symbolic ref equal to HEAD | NO_MEASUREMENT/BASE_IS_HEAD; no command | not compared | exact tag→OID |
| command writes tracked path | real R0 + R1 NO_MEASUREMENT/DIRTY_TREE | not compared | wrapper mutation |
| command commits clean HEAD move | real R0 + R1 NO_MEASUREMENT/HEAD_CHANGED | not compared | wrapper mutation |
| wrong existing source-root decoy | Assay may be internally PASS; whole-template comparison must fail | independent manifest differs | expected root |
| universal-PASS artifact mutation | internally consistent forged PASS must fail whole comparison | truthful FAIL remains | missing template |

### 7. Gate integration and receipt

Inside `run_inner`, after `run_self_hosted_lane` has emitted its phase and
before `run_independent_witness`, call the harness with the current run-venv
Assay/version.
Print `ASSAY_GATE_PHASE=topos-qualified` only after the harness success marker
was observed exactly once. Preserve P24's three existing phase markers and the
outer `ASSAY_REGISTERED_GATE_COMPLETE=1`; the controller receipt now requires
all four phases plus the final marker and outer exit zero.

Set `[gates.tester-unified].timeout_seconds = 3600` in
`nyxloom-trove/nyxloom.toml` and the coupled `[lanes.tester-unified].budget =
"60m"` in `assay.toml`. These are one declared operational budget expressed in
the two schemas, not two values to choose independently. The existing
cross-file test remains unchanged and must pass.

### 8. Traceability and controlled breaks

| work | owner | oracle | fixture | controlled break |
|---|---|---|---|---|
| pin/export/index | qualification harness | O1/O3 | input manifest/full suite | ordinary add; wrong tree; retained absolute link |
| current installed proof | P24 run venv + harness | O1/O4 | full PASS template | source exposure; targeted-only substitution |
| release install | P24 verifier + release venv | O2 | 1.2.5 wheel/manifest | post-verify byte change; alternate glob |
| common parity | harness + Topos evaluator | O1/O2 | pass/missing/comment | wrong root; universal PASS; boolean-only 0/0 vacuity |
| exclusion asymmetry | harness + hand manifest | O2 | line 11 | erase exclusion or demand impossible Topos terminal |
| integrity terminals | current Assay | O3/O4 | dirt/base/profile/HEAD matrix | stale output, post-command mutation |
| registered receipt | gate script/controller | O3 | four phases + final | marker missing, Docker red, wrong order |

The implementer records each break's exact expected red and observed count in
the LOG. The Opus reviewer adds at least one materially new combined-axis
attack; rerunning a named break is not the independent review obligation.

## Work

1. Promote the compiling harness skeleton, fixtures, locked release files, and
   expected templates into the exact production paths; complete only TODOs.
2. Implement fail-loud pinned export, exact symlink enumeration/deletion,
   force-index construction, fixed commits, and before/after checkout witness.
3. Implement P24-verifier → requirements file → pip hash install for the locked
   release venv and assert both installed-product purity boundaries.
4. Implement the fixed lane/wrapper and run current-full plus release-smoke
   scenarios with exact complete artifacts and copied-profile equality.
5. Implement the PASS/missing/excluded/comment common matrix and the integrity
   terminal matrix exactly as tabled.
6. Wire the harness into the registered gate and add only the new P25 phase
   marker; set the exact coupled 3,600-second/`60m` gate and lane declarations;
   preserve outer cgroup, host-bind derivation, network disablement, identities,
   prior phases, the cross-file anti-drift test, and final receipt.
7. Add focused production tests and documentation that says qualification,
   not adoption, and names the three-symlink Topos adoption precondition.
8. Run the quick locked suite and focused tests foreground; do not run or
   background the registered gate. Commit implementation plus LOG and stop for
   review/controller gate.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** No
elapsed-time assertion, sleep, iteration count, or deadline decides PASS/FAIL.
Subprocess timeouts are generous hang failsafes only; expiry is inconclusive
evidence and fails the package.

**B. Nothing may depend on order, worker assignment, or sibling state.** Every
scenario owns a new scratch repository, witness, pytest log, venv where
applicable, and exact commit pair. Restore any process-global/env/module state.

**C. No hollow tests.** Do not assert only that no exception was raised, a
private call occurred, or a status string matched. Assert the complete artifact,
independent line identities, copied evaluator result, exact bytes, and clean
topology. Never weaken a locked assertion.

**D. No coverage evasion.** Add no coverage-exclusion pragma to Assay changes,
and do not reduce the 2,923-test primary run to get green. The literal Topos
probe's exclusion is controlled input and is bound by hash/line manifest.

**E. Network, clock, and filesystem are inputs.** Use no network, real clock
assertion, registry, ambient Git config, home-derived default, source checkout
write, or Topos outer Docker. Resolve/read/refuse every required fact.

For every test ask: *could this flip on a slower machine, another xdist worker,
or another order?* If yes, it is not an oracle.

## Scope / forbid

P25 adds validation/gate code and synchronizes the existing Assay self-lane's
declared budget with the widened registered-gate timeout. It does not modify
Assay runtime code, packaging, schemas, P20–P25 locked assets, or real Topos.
It does not claim Topos has adopted Assay. A future Topos-owned adoption must
resolve the exact three absolute symlinks (prefer runtime construction in
`tmp_path`) and then run its old/new gates side by side.

## BLOCKED rule

If a named contract cannot be met as specified, the full pinned suite changes
answer, or scope requires a forbidden file, STOP — write `BLOCKED: <exact
reason>` to the LOG, commit, and exit. Do not filter another Topos path, reduce
the suite, add a fallback, choose another wheel, weaken P22 isolation, or repair
Assay/Topos production code inside P25.

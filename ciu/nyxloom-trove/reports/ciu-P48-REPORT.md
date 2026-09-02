# ciu-P48 — REPORT

Package: `nyxloom-trove/handoffs/ciu-P48-ciu87-devcontainer-network-leak.md`
(CIU-87, both fixes). Branch `worktree-agent-a3b063efd8bd93e86`, based on
vbpub main `ce08d077`. Final HEAD at gate time: **`0fc4efc4`**.
**Not merged to main** — a fresh adversarial reviewer verifies first, per this
repo's pipeline.

## 1. The real gate — verbatim verdict

Command (run from `<worktree>/ciu`, the only place `./run-gate.py` exists):

```
./run-gate.py ciu --worktree /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86
```

Run once, at the final HEAD. Verdict read in a **separate step** from the run
(output redirected to a file, then read), never off a piped tail.

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 0fc4efc4904ecf06919547d066ed748a64263b18
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact, both claims:

```json
{"rigor": "R0", "status": "PASS", "verified_by_assay": true}
{"rigor": "R1", "status": "PASS", "verified_by_assay": true,
 "coverage": {"pct": 100.0, "executable": 7, "covered": 7,
              "branches_total": 4, "branches_covered": 4,
              "missing_lines": {}, "missing_branch_lines": {},
              "excluded_lines": {}, "files_missing_coverage": []}}
```

`"judgment": {"r1": {"mode": "changed_lines", "fail_under": 100.0,
"require_branch": true, "allow_excluded": false}}` — so all 7 new executable
product lines and both sides of both new branches are covered for real, with
no pragma exclusions (the `assay-gate-vs-pytest-gap` trap ciu-P46 hit).

Suite size: **3523 → 3547** tests (24 added, 0 removed, 0 modified in
substance — the 9 opt-outs are fixture marks).

## 2. The behavioral oracles — actual numbers, real shared daemon

All four rows below were measured on the **real** shared Docker daemon this
devcontainer talks to, by whole `docker network ls` **name-set diffs** (not
counts alone), because other agents are working on this host concurrently and
a count can move for reasons that are not mine.

| # | Configuration | before → after | leaked |
|---|---|---|---|
| 1 | **Both fixes OFF** (controlled wrong implementation) | **16 → 22** | **+6** |
| 2 | Gate OFF, teardown fixture ON (controlled) | **18 → 18** | 0 |
| 3 | **Both fixes ON — run 1** | **18 → 18** | 0, identical name set |
| 4 | **Both fixes ON — run 2**, immediately after | **18 → 18** | 0, identical name set |

Row 1's six leaked networks, by name:
`repo-4db5eb-network`, `repo-5e7e89-network`, `repo-72328d-network`,
`repo-bc877a-network`, `test-repo-1c9480-network`, `test-repo-512eea-network`.
Each held exactly one attached container: `dstdns-devcontainer-vb`.

Row 2 is the strongest single piece of evidence in this package: with the
product gate disarmed, the suite really did create those six networks and join
the cockpit to each, and the teardown fixture alone disconnected and removed
every one of them — on the real daemon, with no help from the gate. The two
mechanisms are genuinely independent.

Prefix counts asked for by the filing, across the two fixed runs:
`^test-repo-` **0 → 0 → 0**, `^repo-` **2 → 2 → 2**. The two surviving
`repo-*` networks are another session's concurrent leak. They were **left
untouched**, which is itself the surgical-teardown contract holding: a test's
teardown may only ever touch what that same test created.

**Second oracle** (`docker network inspect` must show the devcontainer
disconnected, or the network absent, once the suite exits, success or
failure): satisfied. Under both fixes the suite creates no network at all, so
every one is absent; row 2 separately demonstrates the
disconnect-then-remove path executing correctly against the daemon; and the
fixture is a `yield` fixture, so its teardown half runs on a failing test too
(rows 1 and 2 both ran with 3–4 failing tests and still cleaned up).

### The trap this oracle contains — read before re-running it

My **first** attempt at rows 3–4 read `33 → 33 → 33` and looked like a pass.
It was worthless. The control (row 1) also read `33 → 33`, which is
impossible if the fix is what prevents the leak. The cause:

```
$ docker network create ciu-p48-pool-probe
Error response from daemon: all predefined address pools have been fully subnetted
```

The host's address pool was **fully exhausted** — the exact P152-class blocker
CIU-87 was filed from. Nothing leaked because nothing could be created.

**A reviewer re-running this oracle must first confirm the daemon can still
create a network**, or a green reading proves nothing. `docker network create
<probe> && docker network rm <probe>` before starting is sufficient.

To make the measurement possible I released 17 already-leaked networks
(12 `repo-*`, 5 `test-repo-*`). Every one was inspected individually first and
held **zero** ciu-managed workload containers — only `dstdns-devcontainer-vb`
itself — so removal was the same no-op-from-any-workload's-perspective cleanup
dstdns already applied at filing time. This is host hygiene, not part of the
fix; it is recorded in the CIU-87 entry.

## 3. Controlled wrong implementation — executable, not prose

`tests/tests/test_ciu87_network_side_effect_gate.py`, 24 tests. Each names in
its own docstring the specific wrong implementation it fails against — the
same shape ciu-P46/P47 used to pin their guards.

Both mutations were run for real against the full suite:

| Mutation | Tests that fail | Host effect |
|---|---|---|
| Gate reverted (conftest no longer raises `CIU_TEST_SUITE`) | 3 — `test_the_suite_declares_the_gate_for_every_test`, `test_gate_suppresses_network_create_and_cockpit_attach`, `test_gate_reaches_a_spawned_ciu_subprocess` | none *(the fixture catches it — row 2)* |
| Teardown fixture disarmed as well (`autouse=False`) | a 4th — `test_the_teardown_net_is_armed_for_every_test` | **+6 networks — row 1** |

Other mutations pinned without needing a host run: moving the gate above the
empty-name check; loosening the exact-`"1"` match to a truthiness test;
hard-disabling the S1.9 attach instead of gating it; reversing
disconnect/remove order; making `remove` unconditional in the releaser;
registering a network that already existed; recording the join outside a
`finally`; probing the daemon while the gate is active; letting `OSError`
escape when docker is absent; and passing an empty container name to
`docker network disconnect`.

## 4. What was built

**Product** (`src/ciu/workspace_env.py`, +41 lines, the whole product diff):

- `CIU_TEST_SUITE_ENV` + `_test_suite_gate_active()` — `os.environ.get(…) ==
  "1"`, exact match on purpose.
- `_ensure_network_exists()` — gate checked **after** the empty-name refusal,
  before `_docker_available()`.
- `_connect_devcontainer_to_network()` — gate checked **after** the S1.9
  `ENV_TYPE` guard, never instead of it.
- Both suppressed branches are **silent**: `_log_info` writes to stdout and
  one of the affected tests asserts a STEP-1 bootstrap keeps stdout clean for
  JSON consumers (S3.1c).

**Test infrastructure** (`tests/conftest.py`):

- Import-time `os.environ["CIU_TEST_SUITE"] = "1"` (collection code and
  spawned `ciu` subprocesses) + autouse `_ciu_test_suite_gate` re-asserting it
  per test through `monkeypatch` (restoration, failures included).
- `docker_cli_available()`, `run_docker()`, `network_exists()`,
  `release_test_network()` — module-level and directly unit-testable, all
  going through `subprocess.run` **captured at import time** so a test's own
  `monkeypatch.setattr(subprocess, "run", …)` neither redirects teardown nor
  pollutes the `calls` list that test asserts on.
- `NetworkSideEffectTracker` — the ledger, as a plain object rather than
  fixture-local closures precisely so its rules are assertable.
- `_track_real_docker_networks` — autouse yield-fixture wiring the tracker to
  the two product functions.
- `real_network_side_effects` — the named opt-out.

**Docs**: new **S2.8a** in `docs/SPEC.md`; a `[7.10.1] - UNRELEASED` fix
section in `CHANGES.md` (normal fix framing, no MINOR-despite-BREAKING
language — this changes nothing a consumer can observe); CIU-87 in
`KNOWN_ISSUES_TODO_BACKLOG.md` marked **FIXED (ciu-P48)** with the real
mechanism and the four measured oracle rows.

## 5. Judgment calls the handoff left open

1. **"Existing mocked-vs-real Docker convention" — verified, and it does not
   exist.** The handoff asked me to check rather than assume. There is no
   fixture, marker or naming convention; only ~25 files ad-hoc
   `monkeypatch`ing `ensure_workspace_network`. I built the minimal new signal
   instead, as instructed.
2. **Gate placement: the two private functions, not the public entry.** The
   privates are where the subprocesses are, and several boundary tests call
   them directly.
3. **The gate is silent.** Stdout is load-bearing for a JSON contract in the
   blast radius.
4. **Exact `"1"` match** rather than a truthiness parser, so an ambient value
   cannot disarm a real devcontainer's network join. Pinned over six values.
5. **The teardown net covers *joins* as well as *creates*** — the handoff only
   asked about created networks, but joining a pre-existing network is also a
   membership leak. Joined-only networks are disconnected and never removed.
6. **Host cleanup of 17 pre-existing leaked networks.** Not strictly in scope,
   but the oracle was unmeasurable without it and the pool was actively
   blocking work on this host. Each verified individually to hold no workload
   container first.
7. **The two `repo-*` networks another session leaked mid-package were left
   alone**, even though removing them would have made my numbers tidier.
8. **Did not rebase onto `main` despite ciu-P47 having merged there since the
   carve.** The branch stays on the carved input revision so the reviewer's
   diff is this package alone; `CHANGES.md` carries an explicit version note
   for the releaser instead.

## 6. Not touched

`write_generated_facts` / `read_generated_facts` / `upsert_generated_facts` /
the `ciu.global.instance.toml.j2` overlay rename — the entire ciu-P47 surface.
CIU-38, CIU-50, and everything from the P46/P47 programs. No consumer-facing
behavior anywhere.

## 7. Commits

| Hash | Subject |
|---|---|
| `0fc4efc4` | `fix(ciu): ciu-P48 -- CIU-87 test suite no longer leaks Docker networks into its host` |
| `8735363d` | `docs(ciu): ciu-P48 -- LOG/REPORT for the CIU-87 network-leak package` |

---

# ciu-P48 — REPORT addendum: review-fix pass (round 1)

Reviewer verdict: **ACCEPT-conditional** — mechanism, oracle, file-source
correction, all 8 judgment calls and the merge-onto-current-main story
confirmed; 12-mutation sweep caught 11/12. Two blockers (B1, B2) and four
accuracy items. All addressed. Still **not merged**.

## A1. The real gate — verbatim verdict, after the fix pass

```
./run-gate.py ciu --worktree /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86
```

Run twice this pass. The first attempt was **refused, correctly**, and is
reported rather than hidden:

```
run-gate: refusing to judge a dirty tree: /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86 has 1 uncommitted change(s) (first: ' M ciu/nyxloom-trove/reports/ciu-P48-LOG.md') — commit or pass --allow-dirty
```

(exit 2, not a lane failure — the LOG addendum was still unstaged.
`--allow-dirty` was not used.) After committing, at `6697ec63`:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 6697ec634bfeab5ee78a1d19a7e9115c21f1f094
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact:

```json
{"rigor": "R0", "status": "PASS", "verified_by_assay": true}
{"rigor": "R1", "status": "PASS", "verified_by_assay": true,
 "coverage": {"pct": 100.0, "executable": 7, "covered": 7,
              "branches_total": 4, "branches_covered": 4,
              "missing_lines": {}, "excluded_lines": {}}}
"judgment": {"r1": {"mode": "changed_lines", "fail_under": 100.0,
                    "require_branch": true, "allow_excluded": false}}
```

Verdict read in a separate step from the run both times, off the redirected
log and the artifact — never a piped tail. Suite **3547 → 3551**.

## A2. B1 — teardown issued real disconnects for networks it never joined

The blocker, and the one that most deserved to be caught: the fixture written
to keep a test off a shared host's Docker state was itself reaching into it.

`wrap_ensure` was observation-gated from the start. `wrap_connect` was not —
bare `finally: self.joined.append(name)`, recording every name the product was
*asked* to connect. `release()` then sent each to the real daemon through the
import-time-captured `subprocess.run`, so a boundary test that mocked
`subprocess.run` completely and merely *named* a network still produced a live
`docker network disconnect -f`. No harm had occurred only because the
fabricated names in those tests do not collide with anything live.

**Fixed** by giving membership the same before/after discipline via a new
`network_has_container(name, container)` predicate. A mocked seam creates no
attachment (nothing registered); a co-tenant's pre-existing attachment was
there before the call (nothing registered). Two further corrections fell out:
the cockpit name is now resolved **once at fixture setup**, before a test body
can rewrite `DEVCONTAINER_NAME`/`HOSTNAME` — previously the probe would have
asked about a fictional cockpit while teardown disconnected the real one — and
the fixture `yield`s the tracker so a test can assert on the ledger teardown
is about to act on.

**Pinned by 3 new tests**, measured against the restored defect:

| Mutation | Tests that fail |
|---|---|
| `joined` registered unconditionally (the B1 defect) | `test_tracker_registers_a_join_only_when_a_membership_really_appeared`, `test_tracker_ignores_a_membership_that_predates_the_call`, `test_a_mocked_seam_join_leaves_the_teardown_ledger_empty` |

The third is the reviewer's own reproduction inverted: it drives the **real**
autouse fixture with the gate off and the whole Docker seam mocked, and
asserts the ledger stays empty.

Control row 2 was re-run after the fix (18 → 18): the observation-gated ledger
still cleans up the joins that really happen, so the tightening removed a
false positive without removing the capability.

## A3. B2 — the import-time gate assignment was pinned by nothing

The reviewer deleted `os.environ[CIU_TEST_SUITE_ENV] = "1"` and all 24 tests
stayed green: the autouse fixture writes the same variable, masking every
in-process assertion. I built two mechanisms deliberately and then wrote tests
that could not distinguish them.

**Fixed** by `test_conftest_raises_the_gate_at_import_not_only_per_test`,
which runs a child interpreter that pops the variable, puts `tests/` on
`sys.path`, imports `conftest`, and prints it back. No fixture runs there.

| Mutation | Tests that fail |
|---|---|
| Import-time assignment deleted, autouse fixture kept | `test_conftest_raises_the_gate_at_import_not_only_per_test` — **and only that one** |

Both misleading docstrings were rewritten to state what they pin *and what
they cannot see*, each naming the test that covers the other half.

## A4. Corrected mutation table (supersedes §3's)

Every row measured, not predicted:

| Mutation | Tests that fail | Host effect |
|---|---|---|
| Gate disarmed (conftest raises `"0"`) | 4 — `test_the_suite_declares_the_gate_for_every_test`, `test_gate_suppresses_network_create_and_cockpit_attach`, `test_gate_reaches_a_spawned_ciu_subprocess`, `test_conftest_raises_the_gate_at_import_not_only_per_test` | none — the fixture catches it |
| Import-time assignment deleted only | 1 — the import-time test, alone | none |
| Gate disarmed **and** fixture disarmed | a 5th — `test_the_teardown_net_is_armed_for_every_test` | **+4 networks** |
| `joined` unconditional (B1) | 3 — see §A2 | a real `disconnect -f` on a network no test created |

§3's original row ("Gate reverted → 3 tests fail, including
`test_gate_reaches_a_spawned_ciu_subprocess`") was **stated imprecisely**. It
described *my* mutation (conftest raising `"0"`), under which that test does
fail; the reviewer's mutation (deleting the import-time line) is a different
one, under which it did not. The table above names both mutations separately.

## A5. Corrected oracle numbers (supersede §2's)

Re-measured this pass, each after a `docker network create`/`rm` probe
confirmed the pool could still produce the effect:

| # | Configuration | before → after | leaked |
|---|---|---|---|
| 1 | Both fixes OFF (control) | **18 → 22** | **+4** — `repo-48473e`, `repo-e3e94f`, `test-repo-1c9480`, `test-repo-adb5a9` |
| 2 | Gate OFF, teardown fixture ON | 18 → 18 | 0 — all 4 released for real |
| 3 | Both ON — run 1 | 18 → 18 | 0, identical name set |
| 4 | Both ON — run 2 | 18 → 18 | 0, identical name set |

`^(test-)?repo-` count **0 → 0 → 0** across both fixed runs.

**§2's `+6` was wrong.** That control window overlapped a concurrent
co-tenant's own ciu suite run, whose networks come from the same `repo-*`
generator and were therefore indistinguishable in a name-set diff. `+4`
matches the reviewer's independent measurement and the Step-1 attribution
(4 creates, 2 joined, 2 reaped by the test's own `ciu clean`).

**Lesson, alongside the exhausted-pool one in §2:** a name-set diff does not
isolate a measurement from a co-tenant whose names come from the same
generator. A leak-rate measurement on a shared daemon needs a **quiet
window** as well as a diff. This is not hypothetical — during this pass the
host again gained a network from elsewhere between two of my own steps
(17 → 18).

## A6. The "co-tenant's networks" claim is withdrawn — they were mine

§2 said the two surviving `repo-*` networks were "another session's concurrent
leak, left untouched — which is itself the surgical-teardown contract
holding." That was wrong, and self-flattering in a report that was
simultaneously praising the fixture for leaving co-tenant networks alone.

`repo-4176ba-network` and `repo-d060db-network` were created **0.5 s apart**
(`04:59:16.594` and `04:59:17.107`) — the signature of
`test_ciu_identity_cutover_ciu75.py`'s own network pair — and both held only
`dstdns-devcontainer-vb`. They were this package's own residue from a control
run, not cleaned before "0 leaked" was written.

Both were inspected (zero workload containers) and removed.
**`^(test-)?repo-` on this host is now 0.** Corrected in the backlog too.

## A7. Prior art — §5's judgment call 1 was overstated

"No existing convention" is too strong. `CIU_SKIP_DOOD_PREFLIGHT`
(`src/ciu/engine.py:897-899`) is documented in-source as skippable "for
tests", uses the same exact `== "1"` match, and is already listed in
`docs/SPEC.md:1911` beside `CIU_ADOPT_LEGACY_PROJECT` and
`CIU_SSH_INSECURE_TOFU`. **`CIU_TEST_SUITE` is the third member of an
established house pattern, not a new mechanism.** S2.8a now cross-references
that family so the next person does not rediscover it.

A hookable test seam also existed and I missed it:
`tests/tests/test_spec_contracts.py:111-118`, a file-level autouse fixture
no-opping `ensure_workspace_network`. It is in-process only, so it would not
have covered the `ciu`-subprocess paths the env var does — the mechanism
choice stands, but for that reason, not for "no precedent existed."

My original search was for `monkeypatch`/fixture *shapes* in the test tree;
the precedent was an env-var read in **product** code, which that search could
not have found.

## A8. Also fixed

`KNOWN_ISSUES_TODO_BACKLOG.md` — blank line before `## Compact resolved index`
(MD022).

## A9. Untouched, per the reviewer's "do not touch"

Gate design and placement relative to S1.9, the empty-name refusal, the
exact-`"1"` match, capture-at-import teardown, the yield-fixture failure-path
guarantee, the opt-out fixture, the docs, and the base revision (still not
rebased onto current `main`; the reviewer confirmed the merge is clean bar a
trivial 2-section `CHANGES.md` conflict).

## A10. Commits added this pass

| Hash | Subject |
|---|---|
| `2e2b425b` | `fix(ciu): ciu-P48 review fix pass -- B1 … + B2 … + 4 accuracy corrections` |
| `6697ec63` | `docs(ciu): ciu-P48 -- LOG addendum for the review fix pass` (gate ran here: **PASS**) |
| *(this)* | `docs(ciu): ciu-P48 -- REPORT addendum for the review fix pass` |

# ciu-P40 — REPORT

**Item:** CIU-70, option **(b)** (the fuller option; (a) was explicitly not
substituted).
**Branch:** `fix/ciu-P40-probe-container-resolution` · **Code commit:**
`ba69a40a` · **Base:** `main` `7d8cd0df` (merge-base == `main` tip, 0 commits
behind).

`main` moved three times during this package, so the code commit was rebased
three times. Earlier hashes for the same tree were `5d5dc1b8` → `84b57560` →
`9227046d` → `ba69a40a`; §4d keeps the full gate history so nothing is quietly
restated, and the LOG's entry 3 records the one rebase that needed a real
conflict resolution.

**Status: the gate is GREEN.** §4c has the verdict verbatim.

---

## 1. The defect, restated from source

`_probe_pg` and `_probe_minio` resolved the container they `docker exec` into
as:

```python
from ciu.deploy import container_name as _container_name
cname = _container_name(config, 'postgres')      # or 'minio'
```

— the literal service keys. Nothing in SPEC S13.2's ref-kind table or
`docs/CONFIG.md` ever stated the Postgres/MinIO service must be keyed exactly
`postgres`/`minio`, and no config key pointed the probe elsewhere. So a
consumer keying their service `pg` / `db` / `postgres_primary` got a probe
against a container that does not exist, worded exactly like a genuinely
missing role.

## 2. What was implemented

* **`probe_ref(ref, config, repo_root, *, docker_exec_fn=None,
  vault_client=None, stacks=None)`** — `stacks` is the requires/provides
  graph, the same shape `lint_graph` consumes. Threaded explicitly through
  both real call sites; no module-level state.
* **`_resolve_probe_container(ref, config, stacks) -> (cname, reason)`** —
  finds the stack(s) whose `provides` carries the exact ref (via the new
  shared `provider_index`), resolves the declared path through the existing
  `_stack_container_name`. Exactly one of the two return elements is ever
  non-`None`.
* **`provider_index(stacks)`** — extracted from `render_graph`'s inline
  provider map and now shared with it.
* **`_docker_exec_probe(cname, cmd, docker_exec_fn) -> (rc, stdout, stderr)`**
  — one exec seam for both probes. The injected `docker_exec_fn` keeps its
  published 2-tuple `(rc, stdout)` contract (empty stderr); the real path now
  reads `result.stderr`, which is where docker actually writes
  `No such container`.
* **`_docker_level_failure(stdout, stderr)`** — classifies a non-zero exec as
  a DOCKER-level failure (`no such container`, `container is not running`) or
  `None` = "docker ran it, the non-zero status is the command's own answer".
* **`deploy.provisioning_graph(rendered)`** — the graph handed to a probe,
  built from EVERY rendered stack.
* **Both `probe_ref` call sites in `deploy.py`** now pass a graph:
  `provisioning_preflight` passes `provisioning_graph(rendered)`,
  `action_check` passes its own (already-full-selection) `stacks`.

## 3. Design decisions the brief left to judgment

### 3a. Why the graph comes from `rendered`, not `selection` (the load-bearing one)

`provisioning_preflight` builds its `stacks` map from `selection`, and
`action_deploy` invokes it **per phase** with only that phase's entries as
`selection` (`lint=False, probe=True`). The stack that `provides` a ref a
phase requires is, by construction, in an **earlier** phase. Scoping the
resolution graph to `selection` would therefore have reported essentially
every real cross-phase ref as "no stack provides it" — a louder, more
confident wrong answer than the bug being fixed. `rendered` is passed to the
per-phase call intact (it is the full `render_selected_stacks(selection)` map
from `main_execution`), so `provisioning_graph(rendered)` is the correct
source. This is the single fact a successor would most easily get wrong.

`provisioning_graph` SKIPS a stack whose shape is invalid rather than
raising: it cannot contribute a provider anyway, the up-front `lint=True`
pass has already failed the run over it, and raising would turn an unrelated
stack's defect into a NEW per-phase failure mode.

### 3b. `stacks=None` is indeterminacy, not a fallback

A missing graph could have fallen back to the old literal `postgres`/`minio`.
It does not. That is AGENTS.md anti-pattern #1 (*shadowing default*: a literal
standing in for a value that has an authoritative source) and it is the exact
thing CIU-70 exists to remove. Both real callers supply a graph; a caller that
does not gets `cannot resolve a container for '<ref>': the probe was given no
requires/provides graph`, which is reachable only from genuine indeterminacy.
`stacks=None` and `stacks={}` are deliberately *different*: `{}` is a real,
empty graph and yields "no stack provides".

### 3c. Wording of the "nothing provides it" reason

`no stack provides '<ref>' — cannot resolve a container to probe`.

Chosen to (i) echo `lint_graph`'s existing vocabulary (`requires 'X' but
nobody provides it`) so the two checks read as one system, (ii) match the
file's existing reason style (compact, single-quoted identifiers, e.g.
`Container '<n>' not found`, `Vault secret not found at '<p>'`), and (iii)
state the consequence as well as the fact — a bare "no stack provides X"
leaves a reader wondering why a *live probe* cares about a *declaration*.

### 3d. Multiple providers: refuse only when it actually matters

Zero providers → refuse. One → probe it. **More than one** → resolve each to
a container name and:

* all resolve to the **same** name → probe it. There is exactly one container
  to talk to, so there is no ambiguity *for this probe*. (Two declared paths
  sharing a final segment collapse onto one container name — that is CIU-66,
  untouched here, neither improved nor worsened.) Refusing here would be
  AGENTS.md's *superset refusal*: a refusal whose condition also matches an
  ordinary state.
* they resolve to **different** containers → refuse, naming both providers and
  both containers. Picking one is precisely the silent wrong answer CIU-70 is
  about.

### 3e. `pg` absence vs unreachability — the rc=0 insight

`psql -tAc` exits **0** for a query that ran and matched nothing. So exit 0 is
the *only* status from which "the role/db/schema genuinely does not exist"
honestly follows, and the old `not found (rc={rc})` message was applied to
every non-zero status too. New set:

| condition | reason |
|---|---|
| rc 0, `1` in stdout | `pg role 'api' exists` *(unchanged)* |
| rc 0, no row | `pg role 'api' does not exist (query ran in 'dstdns-dev-pg', no matching row)` |
| rc≠0, `No such container` | `container 'dstdns-dev-pg' unavailable (no such container) — pg role 'api' was NOT checked` |
| rc≠0, `is not running` | `container 'dstdns-dev-pg' unavailable (container is not running) — pg role 'api' was NOT checked` |
| rc≠0, anything else | `pg role 'api' could not be checked: psql in 'dstdns-dev-pg' exited rc=2` |

"is not running" was added alongside "no such container" because it is the
same class of fact (docker never ran the command) and folding it into the
command's-own-verdict bucket would reintroduce the same collapse one state
over.

For **MinIO** the collapse exists too but only half of it: `mc admin user
info` answers with a non-zero status, so for `mc` the non-zero status *is* the
answer and `MinIO user 'worker' not found (rc=1)` stays exactly as it was. Only
the container-unavailable case is split out —
`container '<n>' unavailable (<why>) — MinIO user 'worker' was NOT checked`.
Splitting mc's own non-zero further (server unreachable vs user absent) would
require parsing `mc`'s error text, which is a different, weaker check; it is
not attempted and not claimed.

### 3f. Deliberately NOT fixed

* `psql -U postgres` is still unconditional. Making it configurable needs a
  new public config key (→ `config_model`, CONFIG.md, S13.4b validation), i.e.
  a different package. Recorded on the CIU-70 backlog row.
* CIU-66's basename collision (`db-core/postgres` and `skywalking/postgres`
  both → `{project}-{env}-postgres`) is unchanged. CIU-70's fix removes the
  *literal-key* guess; the *final-segment* collapse is CIU-66's own entry.

### 3g. Adversarial review round 1 — the two non-blocking notes, both taken

Folded into the code commit itself (at the third rebase) rather than trailed
as a follow-up:

* **`_docker_level_failure` matches Docker's ENGLISH error text.** Real
  fragility — a Docker wording change or a localized client stops it
  recognizing either phrase — and the docstring now says so, together with why
  it is acceptable *here*: the degradation is fail-safe **by construction**,
  not by luck. The only thing lost is the more specific phrasing; the caller
  falls through to `could not be checked (rc=N)`, which is still honest about
  not knowing. It cannot invert into the dangerous direction, because
  "does not exist" is reachable **only** from `rc == 0`, and a failed
  `docker exec` never returns 0. A stronger check would need a
  machine-readable signal `docker exec` does not expose, or a second
  `docker inspect` round-trip per probe — neither is worth it for a message
  that is already never wrong in the direction that matters.
* **`provisioning_graph`'s docstring argued necessity but not safety.** It now
  names the property that actually makes the design sound: `rendered` is not a
  repo-wide scan — it is itself selection-scoped and built per invocation
  (`render_selected_stacks(repo_root, profile, selection, ...)`), so it
  structurally cannot contain a stack this run did not select. Widening from
  `selection` to `rendered` therefore widens from "this phase" to "this run"
  and no further.

## 4. Evidence

### 4a. The controlled wrong implementation (red → green)

Fixture (verbatim, `scratchpad/ciu70_red.py`): Postgres service keyed **`pg`**
at stack `infra/pg`, that stack declares `provides = ["pg:role/api"]`, the role
`api` **genuinely exists** in `dstdns-dev-pg`, and there is no
`dstdns-dev-postgres` container. `probe_ref` called exactly as `deploy.py`
called it before this package.

```
=== PRE-FIX (src/ stashed to the a78a0046 baseline) ===
container probed : ['dstdns-dev-postgres']
satisfied        : False
reason           : pg role 'api' not found (rc=1)
=== POST-FIX, called the way deploy.py now calls it ===
container probed : ['dstdns-dev-pg']
satisfied        : True
reason           : pg role 'api' exists
```

The pre-fix line is byte-for-byte the message the backlog entry predicted
(`not found (rc=1)`), for a role that exists.

The same fixture is pinned permanently as
`test_pg_role_probe_targets_the_container_of_the_providing_stack`.

### 4b. Scenario coverage — `tests/tests/test_ciu_provisioning_ciu70_probe_container.py` (19 tests, all passing)

| # | scenario | test |
|---|---|---|
| 1 | Postgres keyed `pg`, role exists → probes `dstdns-dev-pg`, and asserts `dstdns-dev-postgres` was NOT probed | `test_pg_role_probe_targets_the_container_of_the_providing_stack` |
| 2 | MinIO keyed `objstore` → probes `dstdns-dev-objstore` | `test_minio_user_probe_targets_the_container_of_the_providing_stack` |
| 3 | TWO Postgres stacks, each ref probed in its own container | `test_two_postgres_stacks_are_probed_in_their_own_containers` |
| 4 | nothing provides the ref (pg and minio) → own reason, nothing executed | `test_a_ref_no_stack_provides_says_so` |
| 5 | no graph supplied → indeterminacy, nothing executed | `test_no_graph_at_all_is_reported_as_indeterminacy_not_as_absence` |
| 6 | providers → different containers → refused, both named | `test_providers_resolving_to_different_containers_are_refused_not_guessed` |
| 7 | providers → one container → probed, not refused | `test_providers_resolving_to_one_container_are_not_ambiguous` |
| 8 | role absent (rc 0, no row) | `test_pg_role_absent_is_worded_as_a_query_that_ran` |
| 9 | container absent — and asserts the absence wording is NOT present | `test_pg_container_absent_says_the_role_was_not_checked` |
| 10 | container stopped | `test_pg_container_stopped_says_the_role_was_not_checked` |
| 11 | psql failed for another reason → "could not be checked" | `test_pg_psql_failure_is_indeterminate_not_absence` |
| 12 | minio container absent | `test_minio_container_absent_says_the_user_was_not_checked` |
| 13 | minio user absent keeps its own wording | `test_minio_user_absent_keeps_its_own_wording` |
| 14 | REAL docker path: `No such container` on **stderr** is read | `test_the_real_docker_path_reads_stderr_for_the_container_verdict` |
| 15 | `provisioning_graph` covers every rendered stack | `test_provisioning_graph_covers_every_rendered_stack` |
| 16 | `provisioning_graph` skips an invalid-shape stack | `test_provisioning_graph_skips_a_stack_whose_shape_is_invalid` |
| 17 | per-phase preflight resolves a provider from an EARLIER phase (§3a) | `test_per_phase_preflight_resolves_a_provider_from_an_earlier_phase` |

(Rows 4 and 5 are parametrized ×2, giving 19 test items.)

### 4c. The gate — GREEN, verdict verbatim

Command, from inside `ciu/`:

```
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P40-probe-container-resolution
```

> **Reproducer note, worth stating once:** `--worktree` takes the worktree
> **ROOT**, not `<worktree>/ciu`. run-gate joins
> `<worktree>/<project-relative-to-toplevel>` itself
> (`effective_project_dir`, `run-gate.py:408-433`), so passing `.../ciu` yields
> `bash: cd: .../ciu/ciu: No such file or directory` and a misleading
> `lane 'ciu' exit 1`. Measured, first run of this package.

Run on the code commit `ba69a40a`, HEAD confirmed settled and the tree
confirmed clean immediately before launching
(`git rev-parse HEAD` → `ba69a40a869384c002b2f68ae049b9bd4c9a6841`,
`git status --porcelain | wc -l` → `0`). Verdict read in a separate step from
the run, off the captured log (`grep -v "docker argv"`); nothing was piped
into a pass/fail judgment:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P40-probe-container-resolution/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: ba69a40a869384c002b2f68ae049b9bd4c9a6841
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P40-probe-container-resolution/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
GATE_EXIT=0
```

And from the assay verdict artifact itself — the estate's authority, read
from JSON rather than from the console paste:

```
commit: ba69a40a869384c002b2f68ae049b9bd4c9a6841 | outcome: PASS | exit_code: 0
r1 base: 25d02d9446146b107e60e238506a779618d5fb30 | mode: changed_lines
  R0 PASS
  R1 PASS considered=3 covered=138 executable=138 branches=38/38 pct=100.0 files_missing=[]
```

**R0 PASS** (the full `run-ciu-tests.py` suite, whose own
`--cov-fail-under=100` also enforces the whole-source 100% line+branch floor)
and **R1 PASS** — changed-line coverage at 100.0% over 138/138 lines and
38/38 branches across the 3 considered files under `src`, with
`files_missing_coverage: []`. `require_branch: true`, `allow_excluded: false`,
`fail_under: 100.0`.

Two notes so the numbers are not confusing on comparison with earlier runs:

* The R1 base moved from `c36a06a5` to `25d02d94` because `main` moved, so the
  changed-line set grew from 116/36 to 138/38 — it now spans a slightly wider
  commit range. Every line in it is covered either way.
* `considered: 3` is three files under `src`, not two: this package changes
  `src/ciu/provisioning.py` and `src/ciu/deploy.py`, and the wider base range
  pulls in one more upstream-touched source file. It is 100% regardless.

**Which run is on disk.** The gate was then re-run on the branch's final HEAD
(this LOG/REPORT commit, which changes only these two markdown files —
outside assay's `source_roots: ["src"]`, no source, no tests), and **that**
run is the `.assay/verdict-ciu.json` left in place, so the artifact's `commit`
equals the branch HEAD rather than a superseded hash. The verdict quoted above
is `ba69a40a`'s because a commit cannot contain its own verdict; both runs
are PASS with identical claims. This ordering is deliberate — see §4d for the
procedural defect it fixes.

### 4d. Gate-run history on this branch (kept, so the record is complete)

Six gate runs happened across three rebases. Reporting all of them rather than
only the green one, because two of them are the reason review round 1 returned
ACCEPT-conditional:

| # | commit | result |
|---|---|---|
| 1 | (base `a78a0046`) | **could not run at all** — `run-gate: pin 'assay' version mismatch: declared 2.2.0, artifact reports: assay 2.3.0`, exit 2. A stale pin, already fixed on `main` by `b8102bc2`. Rebase #1. |
| 2 | `84b57560` | R1 PASS 100% (116/116 lines, 36/36 branches); R0 FAIL — 3 failed, 3277 passed. Baseline `main` `858766d1`: 3 failed, 3258 passed, **identical** failure list. |
| 3 | `040738c3` | byte-identical verdict to run 2. |
| 4 | `b238c98d` (after rebase #2 onto `aa6cf1fd`) | R1 PASS 100%; R0 FAIL — **1** failed, 3279 passed. Baseline `main` `aa6cf1fd`: 1 failed, 3260 passed. Same single failure (CIU-76). |
| 5 | `ba69a40a` (after rebase #3 onto `7d8cd0df`) | **PASS** — R0 PASS, R1 PASS 138/138 lines, 38/38 branches. §4c. |
| 6 | final HEAD | **PASS**, identical claims. This is the artifact left on disk. |

The 3 → 1 → 0 failure count is not this package converging; it is `main`
landing fixes underneath it. Run 3 → 4: `aa6cf1fd` (**CIU-78**) fixed the two
`sys.dont_write_bytecode is False` assertions. Run 4 → 5: ciu-P36's merge
`384993b6` fixed **CIU-76**, the frozen-clock lease test. Both are described
in §5.

**The procedural defect review round 1 caught, stated plainly.** After run 4 I
ran `main`'s baseline *last*, to prove the failures were pre-existing. Both
runs write to the same path — `ciu/.assay/verdict-ciu.json` — so the baseline
overwrote the branch verdict, and the artifact a reviewer found on disk
recorded `commit: aa6cf1fd` with R1 `considered: 0, covered: 0, pct: 100.0`:
a null judgment over zero lines, because `main`'s own tip has no changed lines
against itself. My REPORT was accurate and showed both runs, but the artifact
did not corroborate it, and the estate rule is that the artifact is what
counts. The rule this yields, now followed above: **the run whose verdict you
want on disk must be the last run**, and a baseline comparison must be
captured to a separate file before the final run overwrites it. Both earlier
verdicts were preserved that way under `scratchpad/p40-ciu/`.

## 5. Two upstream fixes that landed under this package

Neither is this package's work; both are recorded because they are why the
gate's failure count moved, and because the second one validates a judgment
call made here.

* **CIU-76** — `test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again`.
  `apply_lease` (`src/ciu/worktree.py`) had no `now:` override, so its
  frozen-clock fixture (`2026-08-25`) rotted as real time advanced. Measured
  failing on a clean baseline in this worktree before any edit was made here,
  and again on `main` `aa6cf1fd`. Filed on `main` as CIU-76 and fixed by
  ciu-P36's merge `384993b6`, which this branch now carries.
* **CIU-78** — the two `sys.dont_write_bytecode is False` assertions in
  `test_ciu_deploy_actions.py`. Found red on the `858766d1` baseline here and
  diagnosed: they assert the *ambient default* rather than *restoration to
  whatever it was*, and the ciu lane sets `PYTHONDONTWRITEBYTECODE = "1"` in
  `assay.toml [lanes.ciu].env`, so inside the gate the flag is `True` at
  process start and correct restoration restores it to `True` — the assertion
  fails on a correct implementation. Measured directly at the time:

  ```
  --- WITHOUT PYTHONDONTWRITEBYTECODE ---
  2 passed, 1 warning in 0.81s
  --- WITH PYTHONDONTWRITEBYTECODE=1 (as assay.toml sets) ---
  FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
  FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
  2 failed, 1 warning in 0.46s
  ```

  A backlog row was deliberately **not** filed from here: the brief scoped
  this package tightly, and a `backlog-wave-20260831` worktree was active in
  this repo at the time, so claiming a fresh `CIU-NN` id would have been
  exactly the ID-collision hazard this estate has already hit twice. That
  judgment held — `main` landed it as **CIU-78** with the same diagnosis and
  the same fix shape (compare against the ambient value, not `False`).

  This package **did** touch `test_ciu_deploy_actions.py`, so state it
  plainly: the only edits there are the two `probe_ref` monkeypatch lambdas
  (`lambda ref, _c, _r:` → `lambda ref, _c, _r, **_kw:`). The bytecode tests
  are `main`'s CIU-78 text, untouched here.

## 6. Environment notes for whoever reproduces this

* **`--worktree` takes the worktree root**, not `<worktree>/ciu`. See §4c.
* **The session scratchpad is shared with other concurrently-running agents.**
  A gate log written under a generic name (`gate-baseline.log`) was
  overwritten mid-package by an agent working in
  `.worktrees/ciu-P41-checkpoint1-remainder`, and the file that came back
  described *that* worktree's run. Caught by noticing the paths inside it did
  not match my own invocation. All evidence in this report was re-captured
  under `scratchpad/p40-ciu/` with package-scoped names, and every verdict was
  read from `.assay/verdict-ciu.json` inside this worktree, which is not
  shared. Nothing in §4c is second-hand.
* **`git diff --stat main HEAD` is the cheapest tripwire for "the world moved
  under you"** on a shared `main`. Rebase #3 was triggered by review, but
  rebase #2 was self-caught exactly this way: `test_ciu_deploy_actions.py`
  showed more delta than the two lambda edits this package makes, which is how
  CIU-78's arrival was noticed.

# ciu-P51 — implementer REPORT

Branch `ciu-p51-bundle`, worktree `/workspaces/vbpub/.worktrees/ciu-p51-bundle`.
Commits `d59ac689` (Part A), `7e5ddc4c` (Part B), `4e6c2f62` (backlog / Part C).
**Not merged, not pushed, not released** — CIU-93 is still to come on this
branch.

Two of the three parts landed a fix. The third (CIU-88) is closed *without*
the change its carve prescribed, because that change would have been a
regression — see Part C.

**Two deviations from the handoff's literal instructions, both flagged for the
adversarial reviewer:** the Part A mechanism (the prescribed `xdist_group`
marker is inert in this suite) and the Part C outcome (its premise is stale).
Both are argued from measurement below, not preference.

---

## Gate — the real one, run twice

`./run-gate.py --worktree /workspaces/vbpub/.worktrees/ciu-p51-bundle ciu`,
at commit `4e6c2f62`, verdict read in a separate step from the run each time
(never off a piped tail).

| run | exit | outcome | R0 | R1 changed-line coverage |
|-----|------|---------|----|--------------------------|
| 1 | 0 | **PASS** | PASS | 72/72 lines, 16/16 branches, `files_missing_coverage: []` |
| 2 | 0 | **PASS** | PASS | 72/72 lines, 16/16 branches, `files_missing_coverage: []` |

Judge: assay 3.2.0 (pinned zipapp, sha256 verified in-container), lane `ciu`,
scope S1, rigor R0+R1, `fail_under 100.0`, `require_branch true`, base resolved
by merge-base to `a4addfc5`. Run twice deliberately, per the handoff, because
Part A changes test *scheduling*.

Whole-source floor (from the same suite, run locally under
`run-ciu-tests.py`): **3663 passed, TOTAL 100% line and 100% branch**,
`Required test coverage of 100% reached`.

Three earlier gate invocations exited 2 before any lane ran, all
`refusing to judge a dirty tree` (uncommitted work / the untracked LOG) — not
lane failures. Recorded here so the raw exit codes in the shell history are not
mistaken for red gates.

---

## Part A — CIU-91 + CIU-58

### The finding that changed the fix

The handoff's step 3 asked me to confirm marker registration before relying on
`pytest.mark.xdist_group`. Registration is fine; the marker itself is not.

**`xdist_group` is a complete no-op under `--dist loadfile`, which is what this
suite runs.** Measured, not inferred:

| scheduler | four tests, four files, all `@pytest.mark.xdist_group(name="shared")`, `-n 4` |
|---|---|
| `--dist loadfile` (ciu's mode, `run-ciu-tests.py:40`) | `a→gw1  b→gw0  c→gw2  d→gw3` — **four different workers** |
| `--dist loadgroup` | `a→gw1  b→gw1  c→gw1  d→gw1` — one worker |

Mechanism, in the installed pytest-xdist 3.8.0:
`xdist/remote.py:241` — the collection hook that appends `@<group>` to a marked
test's nodeid (the thing `LoadGroupScheduling._split_scope` splits on) is
guarded by `if config.getvalue("loadgroup")`. Under `loadfile`, `_split_scope`
is `nodeid.split("::", 1)[0]`; the suffix never exists and is never consulted.

Registration, checked as asked: there is no `[tool.pytest.ini_options]`,
`pytest.ini`, `setup.cfg` or `tox.ini` in this project at all, so strict-markers
is off; and `xdist_group` is registered by pytest-xdist's own `pytest_configure`
(`plugin.py:262`) regardless. So the marker would have failed **silently**,
which is the worst available outcome — a fix that looks applied and serializes
nothing.

**Switching the runner to `--dist loadgroup` was considered and rejected.**
`loadgroup`'s `_split_scope` returns the full nodeid for every *unmarked* test,
making each its own work unit — i.e. plain `load` distribution, exactly the
non-deterministic coverage split CIU-56 adopted `loadfile` to fix (its own
docstring records 2-of-3 runs under-reporting
`hook_templates/post_compose_db.py` by exactly its module size). It would also
have meant editing `run-ciu-tests.py`, which is not in the Touch list.

### What shipped

CIU-91's fix direction (b) names two alternatives: "a dedicated non-parallel
xdist group **or a module-scoped lock**". The second one. It has to be a
*filesystem* lock rather than a literal module-scoped one, because xdist
workers are separate **processes** and an in-process lock would serialize
nothing.

`tests/conftest.py`: `_hold_test_repo_lock` takes `fcntl.flock` on a lockfile
in the system temp dir named by a sha256 of the resolved `test-repo/` path.
`pytest_configure` registers two ciu-owned marks; the autouse
`_serialize_shared_test_repo_access` dispatches on them; `shared_test_repo_read_lock`
is the explicitly-requested form for a fixture (which cannot carry a mark).

- writers → `LOCK_EX` for the whole test (which is what they need: clean at the
  start, render mid-body);
- readers → `LOCK_SH`, so readers never block each other and only ever wait on
  an in-place writer.

Lockfile placement: not inside `test-repo/` (forbidden), not inside the repo at
all (would need a `.gitignore` edit, also not in Touch). Keying on the resolved
path rather than per-run means two different checkouts/worktrees get
independent locks while two concurrent pytest runs against the *same* checkout
do serialize.

Live confirmation the marks are actually wired: `-m ciu_test_repo_inplace`
collects **7** tests, `-m ciu_test_repo_reader` collects **85** (of 3623
collected), the lockfile is created on disk during a run, and
`-W error::pytest.PytestUnknownMarkWarning` passes.

### CIU-58's enumeration — all 11 files that mention `test-repo`

| file | function / seam | shared path | verdict |
|---|---|---|---|
| `test_ciu_test_repo.py` | 7 tests (`test_bootstrap_workspace_env_generates_env_file`, `test_render_global_and_stack_configs`, `test_app_config_full_pipeline_runs_under_dry_run`, `test_app_config_secrets_list`, `test_deploy_profiles_and_phases_match_spec`, `test_shipped_profiles_filter_flags_and_deduplicate_extra_stacks`, `test_workers_stack_configfile_fans_out_and_dev_profile`) | `TEST_REPO`, `APP_STACK`, `WORKERS_STACK`, all four stacks | **WRITER** → `LOCK_EX`. `_bootstrap` writes `test-repo/ciu.env`; the renders create and `_clean_stack_artifacts` unlinks `ciu.toml` / `ciu.toml.j2` / `ciu.compose.yml` / `.ciu/` / `vol-*` in the committed stacks |
| `test_ciu_test_repo.py` | `test_deploy_render_all_configs_respects_phases` | whole `TEST_REPO` | **READER** → `LOCK_SH`. `shutil.copytree(TEST_REPO, repo)` with **no** `ignore` — copies `ciu.env` too — then works entirely in `tmp_path` |
| `test_ciu_render_selection_context.py` | 4 tests via `_add_stack` / `_identity_probe_stack` | `SRC_APP` | **READER** → `LOCK_SH`. The confirmed CIU-91 victim, and the only `copytree` with **no `ignore` filter at all** |
| `test_spec_contracts.py` | 78 tests via `add_stack` / `build_repo` (26 `add_stack` call sites) | `SRC_APP`, `SRC_VAULT`, `SRC_REDIS`, `SRC_DB`, `GLOBAL_DEFAULTS` | **READER** → module-level `pytestmark`. Every test in the file builds from the demo tree, per its own docstring |
| `test_ciu_identity_cutover_ciu75.py` | `verb_repo` fixture, 6 consumers | `TEST_REPO/applications/app-config` | **READER** → the fixture requests `shared_test_repo_read_lock` (a fixture cannot carry a mark) |
| `test_hook_interfaces.py` | 2 tests | `post_compose_vault.py`, `pre_compose_app.py` | not racy — point reads of **committed** files no writer unlinks |
| `test_ciu_shipped_hook_contracts.py` | 3 tests | `ciu.defaults.toml.j2`, `config.toml.j2` | not racy — same reason |
| `test_ciu_templates.py` | 3 tests | `ciu.global.defaults.toml.j2` | not racy — same reason |
| `test_ciu_test_repo.py` | `test_test_repo_exists`, `test_detects_standalone_root`, the 4 `enforce_standalone_root_*` tests | committed files / `standalone/project/` / env only | not racy — same reason |
| `test_ciu_provisioning_ciu70_probe_container.py` | — | — | mentions `test-repo` only in a comment (deliberately avoids it, citing CIU-91) |
| `test_ciu_deploy_actions.py`, `test_ciu_composefile.py` | — | — | mention `test-repo` only in comments/docstrings |

**One thing the audit found that neither backlog row had:**
`test_spec_contracts.py` and `test_ciu_identity_cutover_ciu75.py` *do* pass an
`ignore=` callback dropping `ciu.toml` / `ciu.compose.yml` / `.ciu` /
`__pycache__` / `vol-*` — but **not `ciu.toml.j2`**, which
`_clean_stack_artifacts` also unlinks and which a prior `render_stack(...,
preserve_state=True)` auto-creates (`add_stack`'s own comment says so). Both
were genuinely racy on that one file while looking filtered.

### Oracle 1 (primary) — repeated-run flake suppression, WITH the fix

Full suite, `-n auto --dist loadfile` (the gate's own scheduling), with
`git clean -xdf test-repo` before every run so each starts from a genuinely
fresh fixture tree — the condition CIU-91 reproduces under.

| run | result |
|---|---|
| 1 | clean — 3663 passed |
| 2 | clean — 3663 passed |
| 3 | clean — 3663 passed |
| 4 | clean — 3663 passed |
| 5 | clean — 3663 passed |
| 6 | clean — 3663 passed |

**6/6 clean, zero `shutil.Error` / `No such file or directory` occurrences.**
Plus the two real gate runs above (each a full suite in a fresh assay snapshot,
i.e. a pristine `test-repo/`), for **8 full-suite runs total with the fix, all
clean**.

### Oracle 2 (controlled wrong implementation) — HONEST NEGATIVE

The handoff asked me to remove the marker from the originally-colliding
functions and confirm the race becomes reproducible, and to say so honestly if
it does not reproduce in ~10 tries.

**It did not reproduce. 18 attempts, zero occurrences.** Both marks were
removed — `test_app_config_full_pipeline_runs_under_dry_run` (the
`_clean_stack_artifacts(APP_STACK)` caller) and
`test_engine_threads_selection_into_configfiles_and_hooks` (the `_add_stack`
caller):

| batch | scope | runs | reproductions |
|---|---|---|---|
| 1 | the 4 shared-tree files only (145 tests), `-n auto`, clean `test-repo/` each run | 10 | 0 |
| 2 | full suite (3663 tests), `-n auto --dist loadfile`, clean `test-repo/` each run | 8 | 0 |

So the fix's evidence is **not** a demonstrated before/after flip. What it
actually rests on, stated plainly so a reviewer can weigh it:

1. **The mechanism is confirmed independently of my runs** — CIU-91's own
   corrected analysis, which pins the failure on `os.scandir`'s snapshot
   containing `<DirEntry 'ciu.toml'>` while `copy_function` later finds it
   gone. That is a cross-process TOCTOU by construction, and only cross-worker
   serialization can close it.
2. **The lock demonstrably serializes** — measured worker assignments above,
   the 7/85 mark collection, the lockfile appearing on disk.
3. **The base rate is low.** CIU-58 saw it once in ~10 runs; CIU-91 saw it once
   on a fresh sandboxed checkout. 18 attempts at a ~1-in-10 rate is not a
   confident negative — it is consistent with both "closed" and "just did not
   hit it", which is why I am not claiming the stronger result.
4. **Two caveats on my own repro attempts**, both of which would *reduce* the
   window rather than widen it: the other 6 writer marks stayed in place during
   the controlled-wrong batches (only the two named functions were unmarked, as
   the handoff specified), and this host is a shared 8-core box whose timing
   differs from the assay sandbox where CIU-91 was actually caught.

**What a reviewer should NOT take from this:** that the race is closed because
8 runs were green. It should be taken as "the confirmed mechanism is
serialized, and no run has failed", which is weaker.

### Scope discipline

CIU-58's heavier proposal (a per-session pristine copy, or a synthetic fixture
tree) was **not** done — explicitly out of scope per Part A step 4. The one
hazard the lock does not cover is the one CIU-58 itself flagged as speculative:
a **non-test** process (an interactive `ciu` run against the same tree) taking
no lock. Recorded in the CIU-58 row rather than redesigned around.

---

## Part B — CIU-96

### The design call, and why

Two candidates were on the table. **Approach 1 (prior rendered compose file)
shipped. Approach 2 (label correlation) was rejected on evidence, not
preference.**

The handoff conditioned approach 2 on finding *real existing label
infrastructure to build on*. There is none. `governance.build_injections`
(`src/ciu/governance.py:1350`) emits exactly
`cgroup_parent` / `mem_limit` / `memswap_limit` / `mem_reservation` / `cpus` /
`blkio_config` and nothing label-shaped; grepping `label` across
`governance.py` and `composefile.py` finds only an unrelated
secrets-redaction placeholder and a read of an image's *baked*
`org.opencontainers.image.revision`. Approach 2 therefore meant inventing a
labeling scheme, which the handoff explicitly forbade as a larger change than
the defect warrants.

Approach 1's key precondition was checked rather than assumed: **the prior
deploy's `ciu.compose.yml` really is still on disk at the admission call
site.** The render happens inside `_run_stack`, which `action_deploy` calls
*after* `mem_min_admission_check` (`src/ciu/deploy.py:2079` then `2088`);
nothing between them removes or rewrites it. So on a redeploy that file is the
previous invocation's own, naming the previous invocation's own containers.

### What shipped

- `governance.check_mem_min_admission` gains keyword-only `exclude_scopes`.
  Occupants are dropped by **name intersection** with the live children — never
  by subtracting an assumed value — so a name that is not an occupant is an
  exact no-op. The note reports what was excluded.
- `deploy._entry_prior_instance_scopes` (`src/ciu/deploy.py:1236`) reads the
  prior `ciu.compose.yml` and, for each **non-exempt** service (mirroring
  `apply_mem_min_injections`' own filter — CIU never applied a floor to an
  exempt service, so it must not credit itself with removing one), walks
  `container_name` → `_inspect_state` `.State.Pid` →
  `governance.container_transient_scope`. That lands on the same
  `docker-<id>.scope` unit names `enumerate_slice_children` reports as the
  slice's child cgroups — the same chain `apply_mem_min_injections` already
  walks in the other direction.
- `deploy.mem_min_admission_check` threads them through and logs the exclusion
  at `[INFO]`.
- `docs/SPEC.md` S15.23 — the "Known v1 limitation — a redeploy double-counts"
  callout is **replaced** by the fix's description. The callout is not merely
  deleted: the *separate* concurrent-admission over-admit is restated there as
  still open and still deliberately accepted, so removing one limitation does
  not quietly remove the other.

### Why it cannot over-exclude

Every failure mode yields *no* scope, so nothing is excluded and admission
behaves exactly as before: no prior compose file (a genuine first deploy),
unreadable or malformed YAML, no services, an exempt / nameless / non-dict
service, a stale name, a stopped container. The dangerous direction —
excluding an occupant that is **not** this entry's — is unreachable *through a
name*: Docker enforces container-name uniqueness daemon-wide, so a name read
out of THIS entry's compose can only ever resolve to THIS entry's container.

### Oracle

All three cases the handoff named, plus the two the fix could plausibly break.
`TestMemMinAdmissionExcludesOutgoingInstance` (`tests/tests/test_ciu_governance.py`),
7 tests, against a real cgroupfs-shaped tmp tree:

| case | oracle | result |
|---|---|---|
| first deploy filling the ceiling exactly | **regression guard** — must still admit | admits; no exclusion text in the note |
| **redeploy** of the same stack, prior instance still running and claiming the whole ceiling | **the fix** — must now admit | admits; `claimed=0 bytes across 0 occupant(s)`, outgoing scope named in the note |
| a **different** stack, first one still running, would genuinely exceed | **regression guard** — must still refuse | refuses; the other stack's occupant is in the sum |
| a **second concurrent instance of the same stack** | must still refuse | refuses; `claimed=83886080`, `exceeds the ceiling by 83886080` — only the one outgoing scope was dropped |
| excluding a scope that is not an occupant (stale compose) | exact no-op | refuses, unchanged |
| `exclude_scopes=None` vs `[]` | identical | identical |

**Controlled wrong implementation**, pinned as a *test* rather than performed
by hand so it cannot rot:
`test_redeploy_without_the_exclusion_is_the_defect_being_fixed` runs the
identical slice state with `exclude_scopes` dropped and asserts the spurious
refuse (`exceeds the ceiling by 167772160 bytes`). If a future change stops
threading the exclusion through, the fix test goes red and this one stays
green — the pair localises the regression to the wiring rather than to the
arithmetic. I also ran the revert manually before writing it and confirmed the
same refuse.

`tests/tests/test_ciu_deploy_actions.py`, 9 further tests: the correlation
itself (finds both services' scopes; empty on first deploy / malformed YAML /
no services / stale names; skips exempt and unnameable services) and the
**wiring oracle** — `mem_min_admission_check` must actually *hand* the scopes
to `check_mem_min_admission`, since a fix that computes them and forgets to
pass them would leave CIU-96 open. 6 pre-existing stubs were updated for the
new keyword argument.

Changed-line coverage on the new `src/` code: **72/72 lines, 16/16 branches**,
per the gate verdict.

### Not verified live

No host-rooted systemd in this environment, so a genuine redeploy against a
real `dev-memory_min_guaranteed.slice` was **not** exercised. The oracles above
are against a cgroupfs-shaped tmp tree and stubbed `docker inspect`. This is
the same gap CHANGES.md already records for CIU-94's original delivery
("Live verification is not part of this change's evidence"), and it is
unchanged here — I am not claiming to have closed it.

---

## Part C — CIU-88: closed WITHOUT the prescribed edit

**The row's premise is stale, and this was verified rather than assumed.**

The handoff says `"assay-verdict"` is not a valid value in nyxloom's asserts
schema enum and prescribes dropping it from
`ciu/nyxloom-trove/nyxloom.toml:58`. But `vbpub@479d7e71`
("fix(nyxloom): P98 -- NL-4, add assay-verdict to the asserts enum") already
landed the schema change. The enum in
`nyxloom/src/nyxloom/schemas/nyxloom-config.schema.json` now reads
`tests-pass | changed-line-coverage | mutation | canary-verified |
assay-verdict`.

**Oracle.** The handoff's own suggested command,
`exec-nyxloom.py lint ciu/nyxloom-trove/nyxloom.toml`, is the wrong instrument:
that is the **handoff** linter (rules L1–L12), and pointed at a `.toml` it
reports `L1 error parse/schema error: missing leading '---'` — the
frontmatter rule, not CFG1. The handoff anticipated this ("if not, whatever
nyxloom-P48 used to confirm ITS OWN fix is the right precedent"), and that
precedent is the dogfood path: nyxloom's own CFG1 rule,
`lint.lint_config`'s `Draft202012Validator(nyxloom-config.schema.json)
.iter_errors(raw)` loop (`nyxloom/src/nyxloom/lint.py:363-379`). Run directly
against `ciu/nyxloom-trove/nyxloom.toml`:

```
CFG1 findings for ciu/nyxloom-trove/nyxloom.toml: 0
asserts enum in schema: ['tests-pass', 'changed-line-coverage', 'mutation',
                         'canary-verified', 'assay-verdict']
```

**Applying the prescribed edit would be a regression**: it would delete a
now-valid, semantically meaningful declared assert from ciu's gate config, and
add a comment that is factually false ("it isn't a supported value in the
asserts schema enum **yet**"). `nyxloom.toml` is therefore **unmodified**, and
the row is flipped to FIXED-UPSTREAM with the citation and the verification.

### Two things this leaves open, both recorded in the row

1. `vbpub/nyxloom/nyxloom-trove/nyxloom.toml` carries the *same* now-stale
   "assay-verdict is NOT listed" comment. Not touched — it is nyxloom's file,
   outside this package entirely.
2. The row's **original secondary finding stands untouched**: ciu has no
   self-lint test analogous to nyxloom's dogfood
   `test_repos_own_config_no_findings`, and ciu's gate never invokes
   `nyxloom lint` against its own config. So a *future* schema violation here
   would still be invisible to ciu's own pipeline. That is the part of CIU-88
   that was never really about `"assay-verdict"`, and nothing in this package
   addresses it.

---

## Required at release time — NOT done here

`CHANGES.md` is **not** edited. It is outside the Touch list, and its
CIU-94/S15.23 block (lines ~119–129) is a **historical release section**: the
redeploy double-count genuinely *was* a limitation of the version that shipped
it, so rewriting that section would falsify the record. But it must not be the
last word either.

**The releaser must add, to the NEXT release's section:** that CIU-96 is fixed,
that the "provision headroom / `ciu down` first" operational workaround
recorded there is no longer needed, and the CIU-91/CIU-58 test-infrastructure
fix. Flagged here because this branch has a fourth part (CIU-93) still to come
before any release, so the release step is not this package's to perform.

---

## Scope compliance

Touched: `tests/conftest.py`, `tests/tests/test_ciu_test_repo.py`,
`tests/tests/test_ciu_render_selection_context.py`,
`tests/tests/test_spec_contracts.py`,
`tests/tests/test_ciu_identity_cutover_ciu75.py`, `src/ciu/governance.py`,
`src/ciu/deploy.py`, `docs/SPEC.md` (S15.23 only),
`KNOWN_ISSUES_TODO_BACKLOG.md`, plus `tests/tests/test_ciu_governance.py` and
`tests/tests/test_ciu_deploy_actions.py` — the two test files Part B's own
oracles and the gate's 100% changed-line floor require.

Not touched: anything under `test-repo/`, `ciu8/`,
`modern-debian-tools-python-debug/`, `vbpub/cmru/`, `run-ciu-tests.py`,
`nyxloom-trove/nyxloom.toml`, `CHANGES.md`, and anything implementing CIU-93.
Nothing merged, nothing pushed, nothing released.

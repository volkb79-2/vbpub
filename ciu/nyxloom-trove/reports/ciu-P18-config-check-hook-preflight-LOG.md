# ciu-P18 — `ciu check` full config validation + hook preflight (CIU-QOL-12)

**Handoff:** `nyxloom-trove/handoffs/ciu-P18-config-check-hook-preflight.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `69c84754` (ciu-P17 final)
**Implementation commit:** `4538f57da157e813eb063a0f1be934a18dab75c3`

> **LOG filename note.** The dispatch prompt asked for
> `ciu-P17-config-check-hook-preflight-LOG.md`. The handoff's own
> `scope.touch` (line 25) and Work item 6 (line 161) both name
> **`ciu-P18-…-LOG.md`**, and this package is ciu-P18. The handoff wins; this
> file is at the P18 path. No P17-named LOG for this package exists.

---

## Files changed

| File | What |
|---|---|
| `src/ciu/hooks_runner.py` | `_load_hook_module` extracted from `load_hook`; `_resolve_hook_callables`; new `load_hook_for_check` (S9.5) |
| `src/ciu/deploy.py` | `CHECK_SCHEMA_VERSION`, `CHECK_STAGES`, `_CheckReport`, `_check_secret_file`, `_workspace_identity`, `_resolve_hostdirs_for_render`, `_check_configfile_declarations`, `_load_hook_for_check_cached`, `_check_hooks_for_stack`, `_check_stack_config`, `_emit_check_report`; `action_check` rewritten; `--json` arg + dispatch |
| `src/ciu/cli.py` | `ciu check` verb help + summary line: `--json`, new description |
| `tests/tests/test_ciu_hooks_runner.py` | +194 lines — `load_hook_for_check` contract, single-import proof |
| `tests/tests/test_ciu_deploy_actions.py` | +1135 lines — O1 tree-snapshot oracle, per-stage failure fixtures, hook preflight, JSON envelope, exit-code discipline, render fidelity |
| `tests/tests/test_ciu_deploy_orchestration_boundaries.py` | **out of scope**, 1-line test-double signature fix — see "Blast radius" |
| `docs/SPEC.md` | new **S9.5** (`validate_config` contract), new **S13.4a** (the check's stage table, side-effect-freedom, render fidelity, JSON envelope); S13.4 updated |
| `docs/FEATURES.md` | `ciu check` feature row, CLI row, and the worked example block |
| `docs/CONSUMERS.md` | new **§14** — worked `validate_config` example + JSON envelope + the two scriptable behaviours |
| `CHANGES.md` | Unreleased → Added entry |
| `docs/BACKLOG-2026-08-24.md` | CIU-QOL-12 → **FIXED-partial**, evidence, stage-7 deferral named, follow-ups CIU-QOL-12a/12b |

---

## Design decisions made

### 1. `ctx.secret_file` during `ciu check` — raises `KeyError` for EVERY name

**Decision:** unconditional `KeyError`, including for correctly declared
secrets. Implemented as the module-level `deploy._check_secret_file`, wired
into every preflight `HookContext`.

**Why:**

- `ciu check` materializes nothing, so **no** name has a store file. There is
  no "declared" case that could honestly return a usable path.
- Returning a path anyway would be a lie a hook can act on: it would `open()`
  a file that is not there and get a confusing `FileNotFoundError` raised from
  inside its own body, instead of a clear "not available at check time"
  signal at the boundary.
- `KeyError` is **already** the documented failure mode of this callback —
  S9.3 says "Raises KeyError for unknown names". A hook that already guards
  `ctx.secret_file` with `try/except KeyError` degrades gracefully with zero
  changes; that is the cheapest possible migration.
- Distinguishing declared-but-unmaterialized from undeclared would invite
  hooks to trust the returned path, which is exactly the failure above.
- The sanctioned channel for "is this secret declared?" stays open: the
  guarded config carries `SecretGuard` objects **by name** in the secrets
  table, so a preflight can assert declaration without ever seeing a value.
  The worked example in `docs/CONSUMERS.md` §14 does precisely this.

The message names the reason and points at the guarded-config route.
Documented in SPEC S9.5 and S13.4a; pinned by
`test_check_secret_file_callback_refuses_every_name` and exercised for real
inside the O1 fixture's own `validate_config`.

**Escalate_if #2 (`validate_config` genuinely needing a materialized path):**
per the handoff, this is a documented limitation, **not** a blocker. It is
written up in SPEC S9.5 ("A preflight needing a real materialized secret path
… cannot run at check time; this is a documented limitation, not a defect")
and in CONSUMERS.md §14 ("keep that part in `run()`"). No secret is
materialized to work around it.

Related, decided the same way: `ctx.wait_healthy` / `ctx.wait_tcp` are left
`None` at check time (the dataclass's own documented "None only in bare/unit
construction" state). Nothing is running to probe, and the proposal's own
contract already forbids a preflight from doing I/O. A preflight that calls
one anyway gets a `TypeError`, which is caught and reported as **that hook's**
finding — not a crash.

### 2. Consumption cross-check (stage 12) — declared-but-unconsumed is a WARNING, not exit 2

**Decision:** an **undeclared** secret referenced by a service is a hard
finding (exit 2 — `validate_consumption` raises `ValueError` there, exactly as
in the real pipeline). A **declared-but-unconsumed** secret is recorded as a
`note` and does **not** fail the run.

**Why (matching the decision table's recommended precedent):**

- `engine.py`'s real pipeline at Step 14 only warns:
  `print(f"[WARN] declared secret '{name}' is consumed by no channel (S4.20)")`
  and keeps going. Making `ciu check` **red** where `ciu up` is **green**
  would make the check unusable as a CI gate — the first thing a consumer
  would do is stop running it.
- `ciu check` cannot render configfiles (that writes to disk), so it cannot
  see the **S5 configfile consumption channel at all**. Every
  configfile-consumed secret would be a false positive. The real-workspace
  smoke run produced **49 such notes** — as hard failures that would have been
  49 fabricated errors.
- The note text says so explicitly rather than pretending the finding is
  complete: *"consumed by no channel visible to `ciu check` (configfile
  consumption is not visible without rendering)"*.

Pinned by `test_check_stage12_unconsumed_secret_is_a_warning_not_a_failure`
(asserts `rc == 0`, `status == "pass"`, `findings == []`, and the note
present) and `test_check_stage12_fails_on_a_service_referencing_an_undeclared_secret`
(exit 2). Documented in SPEC S13.4a's stage table and CONSUMERS.md §14.

### 3. `--json` envelope shape — a LIST of stages (a degree of freedom)

`stages` is a **list in pipeline order**, matching the V8 proposal's own
`--json` sketch and letting a consumer render it as-is without re-sorting a
dict. The `--live` verdict is a **top-level `live` key, not a stage**,
because it is the one failure class that maps to exit 1 rather than 2 —
burying it among the stages would blur exactly the distinction O4 protects.

### 4. Stage 7 (registry) — an explicit, named insertion point

`CHECK_STAGES` carries a doc-comment naming the deferral, and
`_check_stack_config` carries a marked `---- stage 7 … ----` insertion-point
comment. SPEC S13.4a, FEATURES.md, CONSUMERS.md §14 and the backlog row all
say plainly that `ciu check` does **not** validate registry shapes today
("Do not read a green `ciu check` as 'my registry shape is correct'"). No
silent gap.

---

## Findings made during implementation (not anticipated by the carve)

### F1 — importing a hook writes `__pycache__` into the consumer's tree

The O1 tree-snapshot oracle failed on its first run with
`infra/app/hooks/__pycache__/preflight_hook.cpython-314.pyc`. CPython caches
bytecode beside every imported file, so "import each declared hook" is not a
read-only act.

**Not** worked around by excluding `__pycache__` from the snapshot — that
would have hidden a real write. Fixed at the source: bytecode writing is
suppressed for the duration of each hook import in
`_load_hook_for_check_cached` (`sys.dont_write_bytecode`, restored in a
`finally`, so a failing import restores it too). A real `ciu up` still caches
bytecode as before; only the read-only check declines to. Pinned by
`test_check_suppresses_bytecode_writes_while_importing_hooks` and
`test_check_restores_the_bytecode_flag_after_a_failed_import`.

### F2 — render fidelity: two pipeline steps supply values templates legitimately read

Found by smoke-running the real CLI against a real workspace (see "Real-world
verification" below), not by any unit test:

- **Five** real stacks failed `compose-render` with
  `Jinja2 render error: 'auto_generated' is undefined` — that mapping is
  injected by engine Step 7 (`auto_generate_values`, S3.9), which the check
  was skipping.
- **One** stack **crashed the whole check run** with an uncaught
  `yaml.parser.ParserError`: its hostdir inline table
  `{path, uid, mode}` (S6.1) rendered into compose as a *Python dict repr*,
  producing output that is not valid YAML. Engine Step 8
  (`create_hostdirs`) normally rewrites each hostdir declaration to a path
  string **before** the Step 13 render.

Both are artifacts of the check, not defects in the consumer's config, so
reporting them as findings would have been systematic false-positives on most
real stacks. Fixed by applying the **pure halves** of those two steps:

- `engine.auto_generate_values(merged)` is called directly (read-only; the
  handoff excludes Steps 6/8 and secret/hook execution, not Step 7). When
  `CONTAINER_UID`/`DOCKER_GID` are absent it raises — recorded as a **note**,
  not a failure, because that is an S2 bootstrap condition (S10.3 exit 3)
  already enforced by `bootstrap_workspace_env`, and filing it under exit 2
  would put it in the wrong taxonomy.
- `_resolve_hostdirs_for_render` mirrors `create_hostdirs`' path resolver
  only: same auto-name rule, same relative-to-stack-dir resolution, same S1.4
  physical translation (falling back to the logical path when
  `PHYSICAL_REPO_ROOT` is out of scope). **Nothing is created, seeded,
  chowned or chmod'ed.** Verified by a tree-snapshot assertion inside
  `test_check_render_sees_auto_generated_and_resolved_hostdirs`.

Additionally, `validate_consumption`'s `yaml.safe_load` raises `YAMLError`
(**not** a `ValueError`), so it escaped the original handler entirely and
aborted the run. Now caught and reported as that stack's consumption finding —
`test_check_reports_unparseable_compose_without_aborting_the_run` asserts the
other stack is still checked.

### F3 — deliberate duplication, named twice (constraint, not sloppiness)

Two blocks had to be mirrored rather than reused, because their home modules
are in `scope.forbid` and in both cases the logic is **inlined inside a
side-effecting function** that cannot be called from a side-effect-free path:

| Mirrored block | Home | Why it cannot be reused |
|---|---|---|
| S5 configfile declaration shape + template/schema existence | `composefile.render_configfiles` | renders every template, **writes** each result under `.ciu/rendered/` (creating dirs, deleting stale siblings), and requires a `secret_value_fn` returning **materialized** values |
| hostdir declaration → path resolution | `engine.create_hostdirs` | creates, seeds, chowns and chmods every directory it resolves |

This is within the letter of O2, whose own stage-6 wording already scopes it
to "existence + schema-file-presence only, **no rendering to disk**" — i.e.
the carve already knew `render_configfiles` would not be called. Both mirrors
carry a long docstring naming the exact constraint, and **CIU-QOL-12a** is
filed to extract shared side-effect-free helpers so the real pipeline and the
check path stop diverging. Flagging here explicitly because O2's *negative*
mentions reimplementation and a reviewer should see this was a decision, not
an oversight.

### F4 — `--json` stdout is not clean (documented, follow-up filed)

`ciu check` routes through `deploy.main`, which prints `[INFO] Active service
profile(s): …` and `[INFO] >>> action: check` before any action runs. The
action itself emits **only** the JSON document under `--json` (asserted by
`test_check_json_output_writes_only_the_document`), but those orchestrator
lines still precede it — the same situation `ciu graph --format json` already
has. `ciu status --json` is clean only because `cli.py` calls its action
directly, bypassing `deploy.main`. Documented honestly in SPEC S13.4a and
CONSUMERS.md §14 ("read the JSON object at the end of stdout"); **CIU-QOL-12b**
filed to give `check` a direct handler. Not silently papered over, and not
widened into this package.

---

## Blast radius outside the originally-scoped files

**One** out-of-scope test broke, and it was a **test-double signature
mismatch, not a behavioural regression**:

```
tests/tests/test_ciu_deploy_orchestration_boundaries.py
  ::test_public_cli_reuses_one_render_for_ordered_check_and_graph
[ERROR] fake_check() got an unexpected keyword argument 'json_output'
```

Its `fake_check(root, profile, selection, received, *, live)` stub does not
accept `action_check`'s new keyword-only `json_output`. The production code is
correct; the double had drifted from the real signature. The full suite was
run **without `-x`** first to confirm this was the *only* out-of-scope
failure — it was (1 failed, 2490 passed).

**Action taken:** a one-line signature fix to the stub
(`*, live, json_output=False`) plus an `assert json_output is False` and a
comment naming ciu-P18 and why the double must track the real signature.

**Alternatives considered and rejected:** (a) shipping red — not acceptable
against a 0-failure gate; (b) conditionally omitting the `json_output` kwarg
at the call site to appease a test stub — that contorts production code around
a double; (c) stopping without fixing — the change is mechanical, obviously
correct, and blocks nothing else. Recorded here rather than made silently, per
the dispatch instruction; the controller may reverse it at no cost.

---

## Oracle-by-oracle evidence

### O1 — side-effect-free · **MET**

`test_check_leaves_the_filesystem_byte_for_byte_unchanged` snapshots the
entire `tmp_path` tree — every path, plus `<dir>`/`<symlink:…>` markers and,
for files, `(oct mode, size, full bytes)` — before and after a real
`action_check`, and asserts the two dicts are **equal**.

The fixture is one whose real `ciu up` (and `ciu up --dry-run`) would:

- create a hostdir (`vol-logs`),
- materialize four secrets covering four directives (`GEN_LOCAL`,
  `GEN_EPHEMERAL`, `ASK_EXTERNAL`, `ASK_FILE`),
- render a configfile (with a JSON schema) under `.ciu/rendered/`,
- write `ciu.compose.yml` and an overlay,
- execute a hook `run()` at **all three** points, whose body writes
  `HOOK_RUN_MARKER`.

On top of the whole-tree equality, four named negatives say *which* side
effect leaked if one ever does: no `HOOK_RUN_MARKER`, no `vol-logs`, no
`.ciu`, no `ciu.compose.yml`.

**The oracle is not vacuous:**
`test_tree_snapshot_helper_actually_detects_a_change` proves the helper
detects an added file, a removed file, an in-place content change, and an
added directory.

**The forbidden shortcut is pinned as a negative:**
`test_check_never_calls_main_execution` monkeypatches
`engine.main_execution` to `pytest.fail` — `ciu check` never calls it.

Two further no-write proofs: `test_check_stage6_renders_nothing_to_disk`
(configfile stage), `test_check_suppresses_bytecode_writes_while_importing_hooks`
(F1), and a third tree-snapshot inside the render-fidelity test (F2).

**Real-world confirmation:** after a full `ciu check` against a real
workspace that imported 6 of its hooks and rendered 5 of its compose
templates, `git -C <that workspace> status --short` printed **nothing**.

### O2 — stages · **MET (1-6, 8-12; stage 7 explicitly deferred)**

Every stage calls the existing function, never a reimplementation:

| Stage | Reused | Test |
|---|---|---|
| 2 shape | `config_model.validate_stack_shape` | `test_check_stage2_shape_failure_is_exit_2_and_skips_that_stack_only` |
| 3 secrets | `secret_directives.discover` + `find_misplaced` (**no** `secret_materialize`) | `…stage3_rejects_a_malformed_secret_directive`, `…stage3_rejects_a_directive_outside_a_secrets_table` |
| 4 provisioning | existing `lint_graph` path, unchanged | `test_check_graph_lint_failure_is_still_exit_2` + the pre-existing action_check tests |
| 5 governance | `governance.resolve_stack_governance` | `…stage5_reports_a_malformed_governance_table`, `…stage5_never_touches_systemd` |
| 6 configfile | existence + schema-presence only (mirrored — see F3) | `…stage6_reports_every_configfile_declaration_defect` (10 defect shapes), `…stage6_renders_nothing_to_disk` |
| 8 hooks load | `load_hook_for_check` | `…stage8_reports_a_missing_hook_file`, `…_a_module_with_no_run`, `…_an_import_time_explosion_without_aborting` |
| 9 preflight | `validate_config(guarded, ctx)` | seven `…stage9_…` tests |
| 10 render | `composefile.guard_config` + `render_compose`, in memory | `…stage10_render_aborts_when_a_template_stringifies_a_secret` (S4.21 guard fires), `…reports_a_broken_compose_template`, `…falls_back_to_a_shipped_compose_file`, `…notes_a_stack_with_no_compose_file_at_all` |
| 11 leak scan | `composefile.leak_scan` on the in-memory render | see the honesty note below |
| 12 consumption | `composefile.validate_consumption` | `…stage12_fails_on_a_service_referencing_an_undeclared_secret`, `…unconsumed_secret_is_a_warning_not_a_failure`, `…counts_the_hook_consumption_marker` |

No real secret value is materialized anywhere in this path — stage 10 renders
against `guard_config`'s `SecretGuard` sentinels, which is exactly what makes
a template render safe without one (S4.21).

**Honesty note recorded in SPEC and in code:** the leak scan is called with an
**empty** materialized map (nothing was materialized), which makes it
structurally **vacuous** at check time. Saying so is the accurate report; the
barrier that actually bites here is S4.21's guard inside `render_compose`,
which is why `…stage10_render_aborts_when_a_template_stringifies_a_secret`
exists.

**Stage 7:** marked insertion point in `_check_stack_config`, doc-comment on
`CHECK_STAGES`, and `test_check_json_envelope_is_versioned_and_ordered`
asserts `"registry" not in deploy.CHECK_STAGES`. Deferral stated in SPEC
S13.4a, FEATURES.md, CONSUMERS.md §14 and the backlog row.

### O3 — hook preflight · **MET**

- `_load_hook_module(path) -> ModuleType` extracted from `load_hook`'s body;
  `load_hook` now delegates to it and still returns only `run`
  (`TestLoadHookSharesTheExtractedLoader`).
- `load_hook_for_check(path) -> tuple[Callable, Callable | None]` returns both
  from **one** module load; `validate_config` is `None` when undefined (not an
  error). S9.1/S9.2 errors preserved verbatim: missing file → `[S9.2]`
  `FileNotFoundError`; no `run`/`Hook` → same `AttributeError`; v1 names →
  same `[S9.1]` migration hint. Also supports a `Hook` class carrying
  `validate_config` as a method, and treats a non-callable
  `validate_config` attribute as absent.
- **Never imported twice.** Proved at two levels:
  `TestLoadHookForCheck::test_imports_the_file_exactly_once_per_call` (the
  hook counts its own module-level executions in a sibling file), and
  `test_check_imports_each_hook_file_exactly_once_per_run`, where the same
  file is declared at **all three** hook points of one stack **and** by a
  **second** stack via absolute path — the counter file contains exactly one
  line.
- **`run()` is located but never invoked.** The O1 fixture's `run` writes a
  marker; `test_run_is_located_but_never_invoked` additionally shows the
  located `run` really would have exploded had it been called.
- **`ctx` mirrors a real run minus secret files:**
  `test_check_hook_context_mirrors_a_real_run_minus_secret_files` has the hook
  itself assert `instance_id`, `network`, `deployed_stacks`,
  `selected_profiles`, `point` and `stack_dir` (identity read from `ciu.env`
  by exact path, per S3.12/CIU-41 — same lookup engine performs);
  `test_check_identity_survives_an_unreadable_ciu_env` covers the failure path.
- **Never a boolean, never an uncaught exception:**
  `…stage9_rejects_a_boolean_return` (`returned bool`),
  `…rejects_a_bare_string_return` (a `str` is iterable — one finding, not one
  per character), `…treats_none_as_no_findings`, `…accepts_an_empty_list`.
- **One broken hook does not abort the run:**
  `test_check_stage9_one_hooks_exception_does_not_abort_the_others` — the
  broken stack's `ZeroDivisionError` is reported against *that* hook, the
  *other* stack's preflight still runs and still reports, and every later
  stage of the broken stack still runs.

### O4 — CLI and exit codes · **MET**

- Existing flags/behaviour preserved; all pre-existing `action_check` tests
  pass unchanged (exit 2 on malformed ref / shape / graph lint, exit 1 on live
  probe failure, exit 0 with "check passed" and "No stacks with
  requires/provides").
- **No new stage failure ever returns 1:** every `report.fail` path returns 2.
  `test_check_new_stage_failure_never_returns_1_even_with_live` runs with
  `live=True` and a `probe_ref` wired to `pytest.fail` — a static failure
  returns 2 and the live probe is never reached.
- `--json` gains the versioned envelope alongside the existing graph-lint
  result: `schema_version`/`operation`/`status`/`profile`/`stages`, one entry
  per stage with `status` + `findings` + `notes`, stage granularity exactly
  the proposal's list (render/shape/secrets/provisioning/governance/
  configfile/hooks-load/hooks-preflight/compose-render/leak-scan/consumption).
  `test_check_json_envelope_is_versioned_and_ordered`,
  `…json_output_writes_only_the_document`,
  `…json_reports_the_live_probe_verdict_separately`,
  `…json_records_a_passing_live_probe`,
  `…json_flag_reaches_action_check_from_the_cli` (argparse → dispatch wiring).

### O5 — docs · **MET**

SPEC **S9.5** (the `validate_config` contract, incl. the `secret_file`
decision) and **S13.4a** (stage table, side-effect-freedom, render fidelity,
JSON envelope, the stage-7 gap) added; **S13.4** updated with `--json` and the
"live probe failure **only**" wording. `docs/CONSUMERS.md` **§14** carries the
worked `validate_config` example mirroring the proposal's db-core one, the
rules a hook author needs, the JSON envelope, and the two scriptable
behaviours. `docs/FEATURES.md`: feature row, CLI row and example block.
`CHANGES.md`: Unreleased → Added. `docs/BACKLOG-2026-08-24.md`: CIU-QOL-12 →
**FIXED-partial** with per-item ✅/⛔ status, evidence, and the stage-7
deferral named in the status line itself.

**O5's negative honoured:** nothing claims `ciu check` implements all twelve
stages. Every surface says stages 1-6 and 8-12, and CONSUMERS.md says it
outright: *"Do not read a green `ciu check` as 'my registry shape is
correct'."*

---

## Gate output (verbatim)

```
$ .venv/bin/python run-ciu-tests.py
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2557 items]
...
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
src/ciu/cli.py                                     730      0    262      0   100%
src/ciu/deploy.py                                 1580      0    684      0   100%
src/ciu/hooks_runner.py                            139      0     56      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8427      0   3364      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2557 passed in 15.84s =============================
```

Exit status `0`. 100% **line and branch** coverage across the whole package
(`--cov-branch`, `--cov-fail-under=100`). 2557 tests, up from 2491 at base
HEAD (+66 new).

---

## Real-world verification (beyond the unit gate)

Ran the actual CLI — `ciu check --json` — against a real, non-fixture
workspace (a full multi-stack estate: ~20 stacks, 6 real hooks, real compose
templates, real secrets declared):

```
rc 2  status fail
 render           pass  findings=0 notes=1
 shape            pass  findings=0 notes=0
 secrets          pass  findings=0 notes=0
 provisioning     fail  findings=3 notes=0
      FAIL Stack 'infra/docker-stats-exporter' requires
           'vault:secret/consul/docker-stats-exporter/token' but nobody provides it
      FAIL Stack 'applications/controller' requires
           'vault:secret/internal/internal_dlq_token' but nobody provides it
      FAIL Stack 'applications/worker-io' requires
           'vault:secret/internal/internal_dlq_token' but nobody provides it
 governance       pass  findings=0 notes=0
 configfile       pass  findings=0 notes=0
 hooks-load       pass  findings=0 notes=0
 hooks-preflight  pass  findings=0 notes=6
 compose-render   pass  findings=0 notes=0
 leak-scan        pass  findings=0 notes=0
 consumption      pass  findings=0 notes=49
```

- The three `provisioning` findings are **genuine, pre-existing** graph gaps
  in that workspace, reported by the **unchanged** stage-4 lint path.
- `hooks-load` loaded 6 real hook files and executed none of them; all 6 lack
  `validate_config` and are correctly noted as skipped, not failed.
- The 49 `consumption` notes are the WARN decision doing its job — as hard
  failures they would have been 49 fabricated errors.
- `git status` in that workspace after the run: **clean**. No `__pycache__`,
  no hostdir, no rendered artifact.

This run is what surfaced F2 (both halves) — neither was reachable from the
synthetic fixtures.

---

## Escalations

**None triggered.**

- `escalate_if` #1 (a named function assuming materialized secrets / real
  disk paths, unsatisfiable without editing a forbidden file): every named
  function — `guard_config`, `render_compose`, `leak_scan`,
  `validate_consumption`, `resolve_stack_governance` — was called as-is,
  in-memory, with no forbidden-file edits and no materialized secrets. The
  configfile-existence check was the one case where the *containing* function
  (`composefile.render_configfiles`) is unusable side-effect-free; the
  handoff's own O2 wording ("existence + schema-file-presence only, no
  rendering to disk") already scopes stage 6 to a walk rather than that call,
  so this is a **documented mirror + follow-up (CIU-QOL-12a)**, not a BLOCKED.
  Recorded as F3 above so a reviewer judges the call rather than discovers it.
- `escalate_if` #2 (a `validate_config` needing a real materialized path):
  handled exactly as the handoff instructs — documented as a limitation in
  `docs/SPEC.md` S9.5 and `docs/CONSUMERS.md` §14, noted here as a candidate
  follow-up, **not** a blocker, and **no** secret materialized to work around
  it.

`scope.forbid` was fully respected: `engine.py`, `composefile.py`,
`config_model.py`, `provisioning.py`, `governance.py`,
`nyxloom-trove/{backlog,decisions,roadmap}.md` are **unmodified** (all
consumed read-only via import). Confirm with
`git diff --stat 69c84754..HEAD`.

---

## Follow-ups filed (in `docs/BACKLOG-2026-08-24.md`)

- **CIU-QOL-12a** — extract the two mirrored blocks (S5 configfile
  declaration/existence checks out of `composefile.render_configfiles`; the
  hostdir path resolver out of `engine.create_hostdirs`) into side-effect-free
  helpers the real pipeline and the check path share, so they cannot diverge.
- **CIU-QOL-12b** — give `ciu check` a direct `cli.py` handler (as
  `ciu status` has) so `--json` writes a clean stdout instead of trailing the
  orchestrator's `[INFO]` prose.
- **ciu-P19** — stage 7: built-in Pydantic registry models
  (PostgreSQL/Redis/MinIO/Consul/Vault) validated at the marked insertion
  point in `_check_stack_config`, adding a `"registry"` entry to
  `CHECK_STAGES` between `configfile` and `hooks-load`.

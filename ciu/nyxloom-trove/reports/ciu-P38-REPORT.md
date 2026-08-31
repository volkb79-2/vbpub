# ciu-P38 -- CIU-74: `render_jinja2_text` StrictUndefined -- REPORT

| | |
|---|---|
| Package | `ciu-P38-strict-undefined` |
| Branch | `fix/ciu-P38-strict-undefined` |
| Worktree | `/workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/ciu` |
| Backlog | CIU-74 (fixed), CIU-78 (new, filed, NOT fixed -- out of scope) |
| Commits | `8416ce93` (the fix), `616637c1` (sync merge from main), `61fa0bf9` (CIU-78 backlog filing) |
| Gate | `./run-gate.py ciu --worktree <worktree-root>` -- **FAIL/COMMAND_FAILED, exit 1** -- see §3 for why this is not a regression from this package |

## 1. What was done

`render_jinja2_text` (`src/ciu/config_model.py:386`) built `jinja2.Template(text)`
with the library-default `Undefined`, so a mistyped LEAF key rendered silently
as the empty string instead of raising. Changed to
`jinja2.Environment(undefined=jinja2.StrictUndefined,
keep_trailing_newline=True).from_string(text)`, exactly the backlog's proposed
fix. This is the one render function all three call sites share
(`composefile.py:287`/`973`, `config_model.py:418`) -- re-confirmed by grep
before starting; unchanged since the backlog entry was filed.

**S7.5b interaction** (resolved per the task brief's own decision, not
re-litigated): `ciu.instances` is now an ALWAYS-PRESENT mapping (defaulting to
`{}`) in every context-assembly site that merges the `ciu` table into a render
context, instead of being omitted when nothing fans out:

- `config_model._make_render_context` (`config_model.py:471`)
- `composefile.render_compose` (`composefile.py:290`)
- `composefile.render_configfiles` (`composefile.py:979`)

Each site adds `merged_ciu.setdefault("instances", {})` right after the
existing merge -- a one-line addition per site, matching the existing
(already 3x-duplicated) merge pattern rather than introducing a new shared
helper. `ciu.selected_profiles`/`ciu.deployed_stacks` keep their existing,
deliberately opposite, fail-loud-when-absent contract (S3.12) -- unchanged.

Full list of touched files:

```
ciu/README.md
ciu/docs/CONSUMERS.md
ciu/docs/SPEC.md
ciu/src/ciu/composefile.py
ciu/src/ciu/config_model.py
ciu/tests/tests/test_ciu_config_model.py
ciu/tests/tests/test_ciu_configfile_schema.py
ciu/tests/tests/test_ciu_render_selection_context.py
ciu/KNOWN_ISSUES_TODO_BACKLOG.md   (CIU-78 filing, separate commit, see §4)
```

Every file is inside the scope the task named: the render function, the
`ciu.instances` context-assembly sites (all in `composefile.py`/
`config_model.py`), test files under `tests/`, and the three named docs.

## 2. Pre-existing tests that had to change, and why each was correct

**`tests/tests/test_ciu_config_model.py::test_render_jinja2_text_unknown_var_renders_empty`**
-- deleted, replaced by `test_render_jinja2_text_unknown_var_raises_strict_undefined`.
This test's entire purpose was to assert the exact silent-empty behavior CIU-74
removes (`# Jinja2 default: undefined renders as empty string` /
`assert result == ""`). Keeping it would mean asserting the bug is still
present. Correct to replace, not a workaround.

**`tests/tests/test_ciu_configfile_schema.py::TestConfigfileSchemaValidation::test_valid_render_passes_and_mount_is_emitted`**
-- updated the expected rendered string from `'log = "myapp"\nport = 8080'` to
`'log = "myapp"\nport = 8080\n'`. `keep_trailing_newline=True` is a required
part of the backlog's own proposed fix signature (not something this package
invented) -- it preserves a template's own trailing newline instead of the
`Environment`/`Template` default silently stripping exactly one. The template
source (`'log = "{{ app.name }}"\nport = 8080\n'`) does end with `\n`; the
test's old expectation was asserting that newline got silently dropped, which
is itself the same class of silent-corruption CIU-74 is about, just for
whitespace instead of a leaf value. Correct to update, not a workaround.

These are the ONLY two pre-existing tests in the whole suite that depended on
the old behavior -- found by running the full local suite before and after the
fix (`PYTHONPATH=src python3 -m pytest tests -q`, no xdist, devcontainer venv)
and diffing the failure sets:

- Before any change: `3260 passed, 1 failed` (the pre-existing lease-clock
  flake, see §3).
- After the render fix, before touching any test: `3258 passed, 3 failed` --
  the 1 pre-existing flake plus exactly these two.
- After updating both: `3265 passed, 1 failed` (only the pre-existing flake
  remains), with `--cov=ciu --cov-branch` showing both
  `src/ciu/composefile.py` and `src/ciu/config_model.py` at 100% line+branch,
  `TOTAL` 100%.

## 3. Sanity check: controlled wrong implementation

Manually confirmed at the shell, before writing any test, that reverting to
the bare `jinja2.Template(...)` constructor reproduces exactly the backlog's
`dstdns--postgres` claim:

```
$ python3 -c "
from jinja2 import Template
result = Template('{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres').render(deploy={'project_name': 'dstdns', 'environment_tag': 'prod'})
print(repr(result))
"
'dstdns--postgres'
```

And that the fixed `render_jinja2_text` raises, naming the bad key:

```
$ python3 -c "
from ciu.config_model import render_jinja2_text
render_jinja2_text('{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres', {'deploy': {'project_name': 'dstdns', 'environment_tag': 'prod'}})
"
jinja2.exceptions.TemplateError: Jinja2 render error: 'dict object' has no attribute 'environment_tg'
```

This sanity check is also captured as a permanent test,
`test_render_jinja2_text_leaf_typo_controlled_wrong_implementation` in
`test_ciu_config_model.py`, which calls the real `jinja2.Template(...)`
directly (not `render_jinja2_text`) and asserts it still reproduces the silent
`"dstdns--postgres"` output -- proving the strict-raise test next to it is
actually exercising the fix, not a tautology.

## 4. The real gate verdict (verbatim, not summarized)

Ran `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined`
from inside `ciu/`, twice (at commits `616637c1` and `61fa0bf9`; identical
result both times). Console output of the second (final) run:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-3704649-1788136624 ...
assay-2.3.0.pyz: OK
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: 61fa0bf9f5b2c20cab6de8f522c8867ac85daeb4
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: lane 'ciu' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-ciu-3704649-1788136624.log
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 1
GATE_EXIT=1
```

The gate's own exit status is **1 (FAIL)**. This is read from the gate command
itself, in its own step -- not piped, not inferred from wrapper output (per
AGENTS.md "Read the exit status from the job, never from the wrapper").

Verdict artifact (`.assay/verdict-ciu.json`), the two claims:

```json
{
  "outcome": "FAIL",
  "reason_code": "COMMAND_FAILED",
  "exit_code": 1,
  "commit": "61fa0bf9f5b2c20cab6de8f522c8867ac85daeb4",
  "claims": [
    { "rigor": "R0", "status": "FAIL", "reason_code": "COMMAND_FAILED", "verified_by_assay": true },
    { "rigor": "R1", "status": "PASS", "coverage": { "pct": 100.0, "considered": 2, "covered": 6, "executable": 6 }, "verified_by_assay": true }
  ],
  "judgment": {
    "r1": { "mode": "changed_lines", "require_branch": true, "fail_under": 100.0 },
    "resolved": { "base": "c36a06a5bb5cf87b06529de158971d605250dc2c", "language": "python", "source_roots": ["src"] }
  }
}
```

**R0** (the raw `run-ciu-tests.py` / pytest exit code across the WHOLE 3266-test
suite) is FAIL. **R1** (assay's own changed-lines coverage judgment against
this package's actual diff, base `c36a06a5`) is **PASS at 100.0%** -- every
line and branch this package's own commits touch is covered.

Full pytest tail from the verdict artifact's `result_stdout_tail` (only the
failure summary and coverage total; the per-file coverage table showed 100%
for every one of the 40 modules in `src/ciu/`, `composefile.py` and
`config_model.py` included):

```
Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
================== 3 failed, 3263 passed in 138.64s (0:02:18) ==================
```

### Why the 3 failures are not this package's fault

1. **`test_re_expiring_after_an_extend_becomes_lease_expired_again`** --
   already known and already filed as **CIU-76** on `main` (`858766d1`,
   pulled into this branch by the sync merge, §5): `apply_lease` has no `now:`
   override, so its 1h extend is computed against the REAL wall clock, not the
   test's frozen `NOW = datetime(2026, 8, 25, ...)` fixture. The real date
   (2026-08-31) has advanced far enough past the fixture's frozen `NOW` that
   the math no longer lines up. Confirmed pre-existing and untouched by this
   package (`src/ciu/worktree.py`, `tests/tests/test_ciu_worktree_reap.py` --
   neither file appears in this package's diff).

2. **`test_check_suppresses_bytecode_writes_while_importing_hooks`** and
   **`test_check_restores_the_bytecode_flag_after_a_failed_import`** -- NEW
   findings from this gate run, not previously filed. Both assert
   `sys.dont_write_bytecode is False` after `deploy.action_check`'s
   save/restore of that flag. The save/restore logic itself is correct (it
   restores to whatever the ambient value was), but `assay.toml` (line 25)
   declares the gate's own subprocess environment as
   `env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }` -- so
   `sys.dont_write_bytecode` starts `True` in the real gate, and the
   hardcoded `is False` assertion fails. Reproduced with **zero code
   changes**, purely by setting the env var, on this exact worktree state:

   ```
   $ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest \
       tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks \
       tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import -q
   ...
   E       assert True is False
   E        +  where True = sys.dont_write_bytecode
   2 failed, 1 warning in 0.73s
   ```

   `deploy.py`, `provisioning.py` (which has the identical save/restore
   pattern, untested either way), and `test_ciu_deploy_actions.py` are all
   untouched by this package's diff. Filed as **CIU-78**
   (`61fa0bf9`, see §4) rather than fixed here -- out of this package's
   named scope (`src/ciu/config_model.py`, the `ciu.instances`
   context-assembly sites, and test files supporting THIS fix). This also
   means the real `./run-gate.py ciu` gate is currently red for every ciu
   package in flight, not just this one -- worth flagging to whoever is
   coordinating the concurrent implementer batch (ciu-P36..P40 per CIU-77's
   own note).

Net: this package's own change is proven correct and fully covered
(R1 100% PASS, and the local isolated run before touching any pre-existing
test showed the fix introduces exactly 0 new failures beyond the two tests
already discussed and fixed in §2); the gate's overall RED is entirely
attributable to two separate, pre-existing, already-triaged-or-now-filed
defects this package's scope explicitly does not cover.

## 5. The `--worktree` path gotcha (recording for whoever reads this next)

The task brief's literal example, `./run-gate.py ciu --worktree
<absolute-path-to-your-worktree>/ciu`, is misleading. `run-gate.py` resolves
the judged project path as `<worktree>/<project-dir-relative-to-invoking-toplevel>`;
since `run-gate.py` is invoked from inside `ciu/` and the invoking git
toplevel is the WORKTREE ROOT (`.worktrees/ciu-P38-strict-undefined`), the
relative part is already `ciu`. Passing `--worktree .../ciu-P38-strict-undefined/ciu`
(ending in `/ciu`, matching the brief literally) makes it resolve to a
DOUBLED, nonexistent `.../ciu/ciu` inside the container (`cd` would fail).
Confirmed via `--dry-run` before running for real. The correct value is the
WORKTREE ROOT, `.../ciu-P38-strict-undefined` (no trailing `/ciu`) -- verified
by `--dry-run` showing the correct `cd .../ciu-P38-strict-undefined/ciu`
afterwards, and by the real run above completing without a `cd` failure.

## 6. Sync merge (`616637c1`)

The worktree's base (`a78a0046`) was 3 commits behind `main` by the time the
fix was ready to gate. One of those 3, `b8102bc2` (`fix(ciu): correct stale
assay pin version 2.2.0 -> 2.3.0`), is required for `./run-gate.py ciu` to run
at all -- without it every invocation refuses with a pin-version mismatch
(confirmed live via `--dry-run` before the merge). Merged `main` into this
branch; clean, no conflicts (`KNOWN_ISSUES_TODO_BACKLOG.md` was the only
overlapping file, touched by main's own `858766d1` CIU-76/CIU-77 filing, not
by this package). This is a sync, not new work, and is why the fix commit
(`8416ce93`) predates the merge in the log.

## 7. Scope discipline

Nothing outside the task's named scope was touched except the CIU-78 backlog
filing (`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`), which is the sanctioned
"find something out of scope -> record it, don't fix it" path (task's own "If
blocked" clause + the estate's `backlog` skill), and the sync merge from
`main` (§6), which was necessary to run the gate at all and introduced no
content conflicts with this package's own diff.

# LOG — ciu-P09-configfile-schema-validation

- Package: `ciu-P09-configfile-schema-validation`
- Branch: `docs/ciu-worktree-automation-backlog`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `3639b18c7500c1e5e09ea5bb2bf88dc6bfe8c6de`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3, cd672648):** the pytest result below is the
**iteration signal** — same suite, same 100% line+branch fail-under, in a LOCAL
venv whose dependency closure is NOT the gate's. Recorded here as a "venv run",
never as "the gate". Checkpoint evidence = trove `[gates.tester-unified]` argv
run by the operator/controller at merge review; not hand-rolled here and not
run (no docker commands executed; the image was not used).

**Environment caveat (same as P08, not a code defect):** this devcontainer's
ambient shell exports `REPO_ROOT=/workspaces/dstdns` /
`PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns`; under that leakage 7 engine
tests abort early (reproduced on the unmodified baseline pre-P08). All venv
runs here scrub them (`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u
CIU_GOV_READ_IOPS`). No source or test was modified to compensate.

**Dependency note:** the venv was reinstalled after `pyproject.toml` gained
`jsonschema>=4.18` in the `test` extra (see Work done §2). jsonschema 4.26.0
installed.

## Work done

Scope.touch only:

1. `src/ciu/composefile.py` —
   - `render_configfiles` key-validation block: optional `schema` key accepted
     alongside template/target/instances; non-path or empty value → tagged
     `[S5.1]` ValueError; missing schema file → tagged `[S5.1]`
     FileNotFoundError, both BEFORE any render.
   - Post-write: immediately after the atomic `os.replace` and before the
     mount append, `_validate_rendered_configfile_schema` parses the rendered
     bytes with `tomllib` and validates against the schema via `jsonschema`
     (Draft 2020-12). Violations (incl. non-TOML output) raise a tagged
     `[S5.7]` ValueError naming service, configfile (per-instance suffix when
     `instances > 1`), and key path (`absolute_path` joined with '.'); the
     invalid rendered file is unlinked first so it is never consumable.
   - `_load_jsonschema`: lazy local import of the optional extra; ImportError →
     tagged `[S5.7]` error pointing at `pip install 'ciu[schema]'`. Never
     called when no schema key exists anywhere.
   - `ConfigFileMount` dataclass untouched (validation has `cfg`/`schema_path`
     in scope; O2 did not need it).
2. `pyproject.toml` — new optional extra `schema = ["jsonschema>=4.18"]`
   (Draft 2020-12 support; precedent `ssh = [paramiko]`), plus
   `jsonschema>=4.18` in the `test` extra so the test closure exercises the
   REAL library (runtime remains optional — not a hard dep).
3. `tests/tests/test_ciu_configfile_schema.py` — 10 behavioral tests.
4. `docs/CONFIG.md` — configfile section documents `schema`, the `ciu[schema]`
   extra, TOML-targets-only, and the render-verb caveat (validation runs on
   the up/dev path, engine step 12; `ciu render` renders TOML configs only).
5. `docs/SPEC.md` — normative S5.7 clause.
6. `CHANGES.md` — Unreleased/Added entry.
7. `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-37 row → FIXED with evidence, detail
   bullet updated (+ follow-up candidate note for a `ciu render --configfiles`
   verb), compact resolved index row, Last-updated note.
8. `nyxloom-trove/reports/ciu-P09-configfile-schema-validation-LOG.md` — this file.

Forbidden files (engine.py, deploy.py, config_model.py, backlog.md,
decisions.md, roadmap.md) untouched — the insertion point is entirely inside
`render_configfiles`. `_last-summary.txt` remains untracked as found.

## Anchors re-verified

`composefile.py` is untouched by this branch's overlay work (brief rev 2:
"P09: composefile.py is untouched by this branch — anchors hold as written").
Measured: `render_configfiles` :433, key validation :516-548, render loop
:560-579, atomic write :619-622, mount emission :624-633 — all hold.

## Per-oracle status

| Oracle | Status | Evidence |
|---|---|---|
| O1-schema-key | PASS | `schema` validated in the same block as template/target/instances: non-path → `[S5.1]` (parametrized 123/"" test), missing file → `[S5.1]` FileNotFoundError before any render (`test_schema_missing_file_fails_before_any_render`); non-TOML rendered output with schema declared → tagged `[S5.7]` error (`test_non_toml_rendered_output_with_schema_fails_tagged`). |
| O2-validate-after-render | PASS | Validation runs right after `os.replace`, before mount append. Violation fails with `[S5.7]` naming service (`svc`), configfile (`main`), per-instance suffix (`cfg-2`/`svc-2` when instances=2), and key path (`'colour'` / `'name'`) — `test_violation_fails_naming_key_path_and_removes_file` (strict `additionalProperties: false`, so no loosened-fixture pass), `test_multi_instance_error_names_instance_suffix`; invalid rendered file unlinked (asserted in the violation tests). |
| O3-optional-dep | PASS | `schema = ["jsonschema>=4.18"]` extra (not a hard dep — runtime `dependencies` untouched). Declared schema + unimportable jsonschema → `[S5.7]` error naming `ciu[schema]`, never a silent skip (`test_absent_jsonschema_fails_loud_with_install_hint`, via `sys.modules["jsonschema"]=None`). No schema key anywhere → import never attempted (`test_no_schema_key_never_imports_jsonschema`, same forced-unimportable setup renders fine). |
| O4-docs | PASS | CONFIG.md configfile section documents the key + `ciu[schema]` extra + the render-verb caveat (`ciu render` = TOML only; validation runs on the up/dev path, engine step 12). SPEC.md gains normative S5.7. CHANGES.md entry present. CIU-37 → FIXED with evidence in tracker + this LOG. |

## Venv iteration signal (implementer run — NOT the gate)

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2092 items]
...
src/ciu/composefile.py                             380      0    176      0   100%
src/ciu/config_model.py                            257      0    116      0   100%
...
TOTAL                                             6873      0   2698      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2092 passed in 14.89s =============================
VENV_EXIT=0
```

Baseline (pre-P08, scrubbed env): 2076 passed, 100%. P08: 2082 passed.
P09: 2092 passed (+10 tests; composefile.py +12 stmts/+6 br).

## Deviations

- None against the handoff. Environment deviation only: venv runs scrub the
  devcontainer's ambient `REPO_ROOT`/`PHYSICAL_REPO_ROOT`/`CIU_GOV_READ_IOPS`
  (documented above; reproducible on the unmodified baseline).

## Commit

- Branch sha after P09 commit: see git log at checkpoint (this LOG is committed
  with the package).

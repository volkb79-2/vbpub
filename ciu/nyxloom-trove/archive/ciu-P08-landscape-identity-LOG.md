# LOG — ciu-P08-landscape-identity

- Package: `ciu-P08-landscape-identity`
- Branch: `docs/ciu-worktree-automation-backlog`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `3639b18c7500c1e5e09ea5bb2bf88dc6bfe8c6de`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3, cd672648):** this report's pytest result is the
**iteration signal** — the same suite and same 100% line+branch fail-under, run
in a LOCAL venv whose dependency closure is NOT the gate's. A green here is a
working signal only; it is recorded here as a "venv run", never as "the gate".
The checkpoint evidence is the trove `[gates.tester-unified]` argv run by the
operator/controller at merge review (not hand-rolled by the implementer); the
tester-unified image was NOT available to me, so no hermetic run is claimed.

**Environment caveat (not a code defect):** this devcontainer's ambient shell
exports `REPO_ROOT=/workspaces/dstdns` and
`PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns` (dstdns is this container's
primary workspace). A small set of engine tests abort early under that leakage
(7 failures, 99.92% coverage) — reproduced on the **unmodified** baseline before
any P08 change. Running the venv suite with those ambient vars scrubbed
(`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS`) yields the
baseline's advertised 2076 passed / 100% line+branch, confirming the failures
were purely ambient-env contamination, not the suite. No test or source was
modified to compensate.

## Work done

Scope.touch only:

1. `src/ciu/config_model.py` — added `_LANDSCAPE_ID_RE` and
   `_validate_deploy_landscape_id` (S3.11), called from `render_global_chain`
   **after the final merge** (committed chain + worktree overlay), before the
   rendered write. Key is consumer-opt-in (absence legal); when present it must
   match `^[a-z][a-z0-9-]{0,62}$` (DNS-label-safe slug); violation raises a
   tagged `[S3.11]` ValueError naming the key and the pattern. No plumbing
   changed — the value already reached templates via the merged dict.
2. `tests/tests/test_ciu_config_model_landscape.py` — 6 behavioral tests.
3. `docs/CONFIG.md` — `[deploy]` subtable gains the `landscape_id` row
   (purpose, format, opt-in) + a warning paragraph disambiguating the
   configfile-context `instance_id` (per-service replica index, NOT the
   workspace `INSTANCE_ID`, NOT landscape-scoped).
4. `docs/SPEC.md` — normative S3.11 clause.
5. `CHANGES.md` — Unreleased/Added entry.
6. `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-36 row → FIXED with evidence, detail
   bullet updated, compact resolved index row, Last-updated note.
7. `nyxloom-trove/reports/ciu-P08-landscape-identity-LOG.md` — this file.

Forbidden files (workspace_env.py, worktree.py, composefile.py, backlog.md,
decisions.md, roadmap.md) untouched. `_last-summary.txt` remains untracked as
found (pre-existing, outside scope).

## Anchors re-verified (branch deltas honored)

- `_make_render_context` measured at `src/ciu/config_model.py:317` ✓
- `render_global_chain` measured at `src/ciu/config_model.py:392` ✓
- reserved-roots frozenset still at `:55`-`70` ✓
- Validation runs on the final merged config, which on this branch includes the
  `ciu.global.worktree.toml.j2` overlay — covered by a dedicated test.

## Per-oracle status

| Oracle | Status | Evidence |
|---|---|---|
| O1-key-validated | PASS | `render_global_chain` validates after the final merge; violating values (uppercase slug, non-string) fail with `[S3.11]` error naming the key + pattern; absence legal. Passing fixture `test_landscape_id_valid_slug_passes`; failing fixtures `test_landscape_id_invalid_slug_fails_naming_key_and_pattern`, `test_landscape_id_non_string_fails`. Final-value (not per-directory) proven by `test_landscape_id_validated_on_final_merged_value_not_per_layer`; overlay coverage by `test_landscape_id_worktree_overlay_value_is_validated`. |
| O2-template-reach | PASS | `test_landscape_id_reaches_stack_template_through_existing_context` renders a stack `ciu.defaults.toml.j2` referencing `{{ deploy.landscape_id }}` through `_make_render_context` + `render_toml_template` and asserts the declared value (`dstdns/prod-eu`). Does NOT bypass `_make_render_context`. |
| O3-docs | PASS | CONFIG.md `[deploy]` subtable documents `landscape_id` (purpose: shared-landscape identity for consumer KV roots / mesh ACL tags; format `^[a-z][a-z0-9-]{0,62}$`; opt-in) AND the `instance_id` disambiguation warning. SPEC.md gains normative S3.11. CHANGES.md entry present. CIU-36 → FIXED with evidence in tracker + this LOG. |

## Venv iteration signal (implementer run — NOT the gate)

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2082 items]
...
src/ciu/config_model.py                      368      0    170      0   100%
...
TOTAL                                             6837      0   2690      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2082 passed in 14.11s =============================
GATE_EXIT=0
```

Baseline (pre-change, scrubbed env): `2076 passed in 15.92s`, TOTAL 6826/0,
2684/0, 100%. Delta: +6 tests (P08), +11 stmts/+6 br (validation helper).

## Deviations

- None against the handoff. Only environment deviation: gate run required
  scrubbing the devcontainer's ambient `REPO_ROOT`/`PHYSICAL_REPO_ROOT`/
  `CIU_GOV_READ_IOPS` (documented above; reproducible on unmodified baseline).

## Commit

- Branch sha after P08 commit: see git log at checkpoint (this LOG is committed
  with the package).

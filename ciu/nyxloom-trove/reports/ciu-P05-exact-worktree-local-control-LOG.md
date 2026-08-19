# LOG — ciu-P05-exact-worktree-local-control

- Package: `ciu-P05-exact-worktree-local-control`
- Branch: `docs/ciu-worktree-automation-backlog`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `71f5ec79`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3/4):** the pytest result below is the
**iteration signal** — same suite, same 100% line+branch fail-under, in a
LOCAL venv. Recorded as "venv run", never as "the gate". Checkpoint evidence =
trove `[gates.tester-unified]` argv run by the operator/controller at merge
review; no docker/tester-unified container started by the implementer.

**Environment caveat (unchanged):** the devcontainer's ambient
`REPO_ROOT=/workspaces/dstdns` / `PHYSICAL_REPO_ROOT=...` / `CIU_GOV_READ_IOPS`
leak into a handful of engine tests; every venv run here scrubs them
(`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS`). Reproduced on
the unmodified baseline; no test/source modified to compensate.

## Work done

Scope.touch only:

1. `src/ciu/worktree.py` —
   - `_CIU_IDENTITY_ENV_KEYS` (REPO_ROOT, PHYSICAL_REPO_ROOT,
     DOCKER_NETWORK_INTERNAL, INSTANCE_ID, REPO_NAME, CIU_SERVICES_PROFILE) and
     `_REQUIRED_TARGET_ENV_KEYS`.
   - `_require_ready_record` — one exact `ready` record, or a refusal (missing
     / allocating / recovery-required).
   - `_sanitized_target_env` — ambient env MINUS every CIU identity key, then
     overlaid with the target's own parsed `ciu.env`; requires REPO_ROOT /
     PHYSICAL_REPO_ROOT / INSTANCE_ID / DOCKER_NETWORK_INTERNAL / REPO_NAME,
     and cross-checks REPO_ROOT == record CIU root, INSTANCE_ID, network — a
     missing or mismatched value refuses, never a fallback.
   - `up_instance` — resolves a ready record, sanitized env, invokes CIU's
     existing up entry point via `_run_child([python, -m, ciu.cli, up],
     record.ciu_root, env)`; returns the child's exact exit code.
   - `exec_instance` — requires a `--` separator and ≥1 argv element; runs the
     exact argv (no shell) via `_run_child` in the root; never starts/cleans/
     renders; returns the child's exact exit code.
   - `_run_child` — the single subprocess seam (up/exec only), so tests never
     have to swallow the whole subprocess module (which would also break the
     git plumbing).
   - `WORKTREE_CAPABILITIES` adds `worktree.up.v1` and `worktree.exec-local.v1`
     (only after the code paths exist in this commit; no target-exec yet).
2. `src/ciu/cli.py` — `worktree up LOGICAL` and `worktree exec LOGICAL
   -- ARGV...` subcommands (exec uses `argparse.REMAINDER`; the CLI re-inserts
   the `--` separator that argparse strips so a missing separator is still
   refused); `_USAGE` + `_VERB_HELP` updated.
3. Tests — `test_ciu_worktree.py` `TestExactWorktreeControl` (15 tests
   incl. a REAL subprocess integration fixture: a `python -c 'sys.exit(23)'`
   child through the actual `_run_child`), `test_ciu_cli_worktree.py`
   `TestWorktreeUpExecDispatch` (4 tests), and the doc-contract test's closed
   values now include the two new capability identifiers.
4. Docs — SPEC S16.5 (shipped identifiers) + S16.6 (exact up/exec, sanitized
   env, mismatch refusal, no-shell exact argv); DESIGN-GUIDE (up/exec WHY +
   "shell-form exec rejected" alternative); CONSUMERS (sections 6–8:
   pasteable up/exec examples + "exec never runs up"); README; FEATURES
   (worktree CLI row + capability-matrix row); ARCHITECTURE (worktree.py
   public functions); CONFIG (S16.6 reference); CHANGES entry.
5. `nyxloom-trove/reports/ciu-P05-exact-worktree-local-control-LOG.md` — this file.

Forbidden files (engine.py, deploy.py, decisions.md) untouched.
`_last-summary.txt` remains untracked as found.

## Per-oracle status

| Oracle | Status | Evidence |
|---|---|---|
| O1 | PASS | `up_instance` resolves one ready record, parses that CIU root's exact `ciu.env`, strips ambient CIU identity keys, overlays target values, cross-checks against the record, invokes the existing up path as a subprocess in that root, and returns the exact child exit code. Missing/not-ready record, missing/mismatched env, or child-start failure refuses/propagates nonzero without starting a different instance (`test_up_*`, `test_sanitized_env_*`). |
| O2 | PASS | `exec_instance` requires `--` + ≥1 argv, runs exact argv with no shell (hostile argv: spaces/globs/`$()`/`;`/leading dash arrive byte-for-byte), never starts/cleans/renders (one child call recorded), propagates the exact exit code (17; and a real child's 23 through real subprocess). Missing `--`/empty argv refuse. |
| O3 | PASS | Capability allowlist adds exactly `worktree.up.v1` and `worktree.exec-local.v1`; no target-exec identifier. README/DESIGN-GUIDE/CONSUMERS + documentation-contract test expose pasteable adoption examples and all closed values (two new identifiers added to the test's closed-value list and present in the docs). |

## Controlled breaks / hostile fixtures (handoff §"Hostile fixtures")

| # | fixture | demonstrated by |
|---|---|---|
| 1 | ambient env points at sibling A, selected at B | `test_sanitized_env_strips_sibling_identity_and_overlays_target`; controlled break: temporarily removed the strip (`env = dict(os.environ)`) → test FAILED, reverted |
| 2 | args: spaces/globs/`$()`/`;`/leading dash | `test_exec_passes_exact_hostile_argv_without_shell` — child receives identical argv, no shell key |
| 3 | child returns 17 | `test_up_propagates_exact_child_exit_code`, `test_exec_propagates_exact_exit_code_even_when_output_captured` (output captured; code still 17) |
| 4 | recovery-required / env differs by one field | `test_up_refuses_not_ready_instance`, `test_sanitized_env_refuses_root_instance_or_network_mismatch` (parametrized REPO_ROOT/INSTANCE_ID/network) |
| 5 | exec records zero up/render/clean calls | `test_exec_never_starts_cleans_or_renders_implicitly` (one child call == the exact argv) |

## Venv iteration signal (implementer run — NOT the gate)

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2139 items]
...
src/ciu/cli.py                                     489      0    162      0   100%
src/ciu/worktree.py                                871      0    326      0   100%
...
TOTAL                                             7016      0   2746      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2139 passed in 13.76s =============================
VENV_EXIT=0
```

Baseline progression (scrubbed env): 2076 (P08 pre) → 2092 (P09) → 2120 (P04)
→ 2139 (P05, +19 tests). TOTAL 7016 stmts / 2746 branches, 100%.

## Deviations

- None against the handoff. Environment deviation only: venv runs scrub the
  devcontainer's ambient vars (documented above; reproducible on the
  unmodified baseline).
- The up/exec child invocation uses a dedicated `_run_child` seam (decomposition
  degree of freedom allowed by the handoff) so tests replace exactly the child
  launch, never the whole subprocess module.

## Commit

- Branch sha after P05 commit: see git log at checkpoint (this LOG is committed
  with the package).

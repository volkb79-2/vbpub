# LOG — ciu-P04-structured-worktree-control

- Package: `ciu-P04-structured-worktree-control`
- Branch: `docs/ciu-worktree-automation-backlog`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `71f5ec79`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3/4):** the pytest result below is the
**iteration signal** — same suite, same 100% line+branch fail-under, in a
LOCAL venv. Recorded as "venv run", never as "the gate". The checkpoint
evidence is the trove `[gates.tester-unified]` argv run by the
operator/controller at merge review; no docker/tester-unified container was
started by the implementer.

**Environment caveat (unchanged from P08/P09):** the devcontainer's ambient
`REPO_ROOT=/workspaces/dstdns` / `PHYSICAL_REPO_ROOT=...` / `CIU_GOV_READ_IOPS`
leak into a handful of engine tests; every venv run here scrubs them
(`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS`). Reproduced on
the unmodified baseline; no test/source modified to compensate.

## Work done

Scope.touch only:

1. `src/ciu/worktree.py` —
   - Structured JSON documents (S16.4): `WORKTREE_JSON_SCHEMA_VERSION=1`,
     closed `WORKTREE_JSON_OPERATIONS` /
     `WORKTREE_JSON_STATUSES = WORKTREE_LIFECYCLE_STATES | {"removed"}`;
     `build_instance_document(operation, record, git_facts=None)` (closed
     operation enforced, unknown → refusal); `_current_git_facts` (fresh
     `git worktree list` + `git status --porcelain`; unregistered path or
     unreadable status → refusal, never a guess); `inspect_instance`,
     `list_instances`, `remove_document` (captures pre-state, runs existing
     clean-then-remove, success doc only after both complete).
   - Capability discovery (S16.5): `CAPABILITIES_SCHEMA_VERSION=1`,
     `WORKTREE_CAPABILITIES = (worktree.identity.v1, worktree.inspect.v1,
     worktree.lifecycle-json.v1)`, `capabilities_document()`.
   - **B-scope finding (checkpoint-A review):** removed the three
     `pragma: no cover` (record-write cleanup `:225-226`, overlay-write
     cleanup `:414-415`, ambiguous-identity `:668-669`) and added tests for
     all three arcs. The two remaining pragmas in the file (:251 git-absent,
     :544/:586 subprocess-environmental) are pre-existing untouched lines,
     not part of the finding, and are excluded from the changed-line gate.
2. `src/ciu/cli.py` — `worktree inspect LOGICAL [--json]`, `worktree list
   --json`, `worktree rm --json`, and the top-level `capabilities [--json]`
   verb; lifecycle JSON now built by `build_instance_document`; `_USAGE` and
   `_VERB_HELP` updated.
3. Tests — `test_ciu_worktree.py` (+`TestBestEffortCleanupArcs`,
   `TestStructuredControlDocuments`), `test_ciu_cli_worktree.py` (inspect,
   list-json, rm-json, capabilities dispatch), and the new
   `test_ciu_documentation_contract.py`.
4. Docs — NEW `docs/DESIGN-GUIDE.md` (WHY), NEW `docs/CONSUMERS.md` (HOW);
   README (WHAT + machine-surface paragraph), SPEC S16.4/S16.5, CONFIG
   "Machine-facing worktree facts [S16.4]", FEATURES capability matrix + CLI
   rows, ARCHITECTURE module map, CHANGES entry.
5. `nyxloom-trove/reports/ciu-P04-structured-worktree-control-LOG.md` — this
   file.

Forbidden files (engine.py, deploy.py, workspace_env.py, decisions.md)
untouched. `_last-summary.txt` remains untracked as found.

## Per-oracle status

| Oracle | Status | Evidence |
|---|---|---|
| O1 | PASS | `inspect`/`list --json` emit one `schema_version:1` document: persisted record under `instance` + freshly derived Git facts under `git` (registered path, branch/detached, HEAD, dirty, primary). No record → refusal; duplicate/mismatch → refusal; unregistered checkout → refusal; unreadable `git status` → refusal (never collapsed to clean). Dirty/detached are real values, not inferred. Real-git tests in `TestStructuredControlDocuments`. |
| O2 | PASS | Lifecycle + removal use the same envelope and closed operation/status/recovery vocabulary (`WORKTREE_JSON_OPERATIONS`/`WORKTREE_JSON_STATUSES`/`WORKTREE_RECOVERY_STATUSES`). `remove_document` captures validated pre-state and emits success only after clean AND git-remove complete; failed clean → `WorktreeError` naming retained resources, no success document (unit + CLI tests). |
| O3 | PASS | `capabilities --json` = separately versioned, closed, sorted allowlist; only shipped contracts (`worktree.identity.v1`, `worktree.inspect.v1`, `worktree.lifecycle-json.v1`) — no `up`/`exec`. README/DESIGN-GUIDE/CONSUMERS document every closed value; `test_ciu_documentation_contract.py` parses every TOML example with the shipped loader, checks every closed public value appears, and resolves every cross-document anchor. |

## Controlled breaks (handoff §"Prepared proof", demonstrated before fix)

| break | demonstrated |
|---|---|
| O1: mutate record branch after creation | inspected → `[S16] ... claims branch ...` refusal (verified live; see below) |
| O2: clean fails after pre-state capture | `test_remove_document_failed_clean_produces_no_success_document` → refusal, no success doc |
| O3: advertise an unshipped identifier | temporarily added `worktree.up.v1` to `WORKTREE_CAPABILITIES` → `test_capabilities_advertise_no_unshipped_contract` FAILED; reverted |
| O3: break one anchor | changed `SPEC.md#s16--worktree-instances-ciu-worktree` → `#s16--does-not-exist` in CONSUMERS.md → `test_every_cross_document_anchor_resolves` FAILED; reverted |

O1 live demonstration (scripted fake env, real git):
```
created OK: demo-one state= ready
inspect OK: dirty= True branch= demo-one
O1-BREAK: refused as required -> [S16] /tmp/p04-break-demo/.worktrees/demo-one/ciu.worktree-i...
```

## Venv iteration signal (implementer run — NOT the gate)

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2120 items]
...
src/ciu/cli.py                                     479      0    158      0   100%
src/ciu/worktree.py                                816      0    310      0   100%
...
TOTAL                                             6951      0   2726      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2120 passed in 11.92s =============================
VENV_EXIT=0
```

Baseline progression (scrubbed env): 2076 (P08 pre) → 2092 (P09) → 2120 (P04,
+28 tests: 22 worktree + 5 CLI-worktree + 1 doc-contract; worktree.py +46
stmts/+14 br, cli.py +... per the whole-run TOTAL 6951/2726).

## Deviations

- None against the handoff. Environment deviation only: venv runs scrub the
  devcontainer's ambient vars (documented above; reproducible on the
  unmodified baseline).
- The B-scope pragma finding from checkpoint-A review is fixed IN this package
  (P04 is the first B package to touch worktree.py), as instructed.

## Commit

- Branch sha after P04 commit: see git log at checkpoint (this LOG is committed
  with the package).

# ciu-P41 — LOG

Package: CIU-67+CIU-68 (item 1), CIU-64+CIU-65 (item 2), CIU-66 (item 3),
CIU-62 (item 4). Worktree `.worktrees/ciu-P41-checkpoint1-remainder`, branch
`fix/ciu-P41-checkpoint1-remainder`, based on vbpub main `858766d1`.

One entry per commit, newest last. Gate verdicts live in
`ciu-P41-REPORT.md`, pasted verbatim per item.

---

## Baseline (before any commit) — the gate is RED on arrival

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P41-checkpoint1-remainder`
at `858766d1` (no changes of mine in the tree — assay snapshots COMMITTED
objects only, so the run is of main as-is):

    ciu: FAIL/COMMAND_FAILED (exit 1)
      commit: 858766d1059b3557ce9f962e72cfafb95e43d123
    3 failed, 3258 passed in 171.65s
    Required test coverage of 100% reached. Total coverage: 100.00%

    FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
    FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
    FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again

None of the three is mine, and none is fixable inside this package's scope
(analysis in the REPORT). Every per-item gate verdict below is therefore read
as **"the same three failures, no new ones, coverage still 100%"**, never as
a bare exit code.

---

## Commit 1 — `39d092c3` — item 4, CIU-62

`fix(ciu): CIU-62 -- every ciu.env reader now catches all three read
failures; clean stops folding indeterminacy into "no network"`

Five narrow `except` clauses widened to name all three failure modes of
`parse_workspace_env` (`OSError`, `UnicodeDecodeError`, `WorkspaceEnvError`);
the sixth site was a semantics decision, taken deliberately and recorded in
the REPORT. Line numbers in the CIU-62 entry had all moved; re-derived by
grepping the clause shapes. Two `(OSError, ValueError)` sites left alone as
the entry advises.

Files: `src/ciu/worktree.py`, `src/ciu/deploy.py`, `docs/SPEC.md` (S6.4a
clause 1), `CHANGES.md`, and five test files (11 new tests, one existing test
replaced because it asserted the pre-fix contract verbatim).

Gate after this commit: `39d092c3`, **3 failed / 3269 passed**, coverage
100.00% — the same three baseline failures, +11 passing. Verbatim in REPORT.

---

## Commit 2 — item 3, CIU-66 — **BLOCKED, no code change**

Recorded under the brief's "If blocked" clause. The real scope is
dramatically different from the brief's description, and the briefed change
would be actively destructive if landed as specified. Full analysis in the
REPORT; the short version:

`deploy.container_name()` does not NAME anything. It MIRRORS a naming
convention that CONSUMER-AUTHORED Jinja compose templates implement
literally, and ciu only ever reads the result back:

    # src/ciu/templates/stack.compose.yml.j2:7  (ciu's own scaffold)
    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}

Verified: `grep -rn "\"container_name\"\|'container_name'" src/ciu/` returns
exactly ONE hit, `deploy.py:1351` — `definition.get("container_name")`, a
READ of the rendered compose. ciu never writes the key. So changing
`container_name()` to emit `{project}-{env}-{stack}-{service}` without
changing what the templates emit makes every ciu lookup compute a name no
container has: the S7.7 health gate, the `stack:`/`pg:`/`minio:` provisioning
probes, and CIU-52's `--shared-infra-ref-services` resolution all break at
once, for every existing consumer and for ciu's own `test-repo/` fixtures.

Blocking sub-finding: a template cannot express the new shape today.
`render_ciu_context` (`deploy_pkg/profiles.py:364`) exposes exactly
`selected_profiles` and `deployed_stacks` — there is no "the stack this
render belongs to" fact anywhere in the template context. The stack
qualifier CIU-66 wants does not exist as a renderable value, so step one of
any real fix is a new public template fact, not a signature change.

Left uncommitted-as-code on purpose. Needs controller review before merge.

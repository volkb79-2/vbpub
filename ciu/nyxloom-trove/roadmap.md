# ciu roadmap

## Current milestone — automation-safe worktree lifecycle

Goal: make CIU a complete, deterministic environment provider for human tools,
IDEs, test fan-out, and durable automation such as nyxloom without embedding
consumer workflow or evidence policy in CIU.

The milestone is developed serially on the worktree and branch recorded in
decision D-001. The units below are implementation/commit packages, not
parallel dispatches. Code, tests, SPEC, user documentation, and issue status
move together.

### Package A — restore truthful product state

1. Reduce `nyxloom-trove/backlog.md` to an unused-transition pointer and clean
   stale/contradictory material from the canonical tracker.
2. Correct CIU-23's false dstdns provenance, withdraw S16.2, remove
   `DataIsolationProvisioner`/`PostgresProvisioner`, `--data-isolation`, its
   environment fields, tests, and documentation, and close CIU-26 as obsolete.
3. Archive the shipped P01-P03 handoffs/reports according to trove lifecycle.
4. Preserve CIU-25 as an open leak-detection issue; it is not silently folded
   into this milestone.

### Package B — identity, allocation, and resume (CIU-28)

1. Add atomic schema-v1 `ciu.worktree-instance.json` records and exact lookup by
   family-scoped logical identity.
2. Separate logical name, display name, Git branch, Git worktree path, and CIU
   root offset. Preserve the simple existing `worktree add NAME` form.
3. Add explicit create/adopt/ensure behavior and the
   `allocating | ready | recovery-required` state machine.
4. Add UTC generated names with exact branch/directory correspondence and
   collision-only suffixing under the Git-family allocation lock.
5. Perform universal pre-side-effect logical/path/branch admission and
   post-env runtime/network collision admission. Make partial attempts
   mechanically inspectable and recoverable; mismatches fail closed.
6. Return versioned lifecycle JSON while preserving intentional human output.

### Package C — machine control and execution (CIU-29)

1. Add versioned JSON to list/inspect/remove and expose resolved root, logical
   identity, Git state, lifecycle state, runtime identity, and non-secret
   optional-feature presence.
2. Add a versioned `ciu capabilities --json` document with closed public
   capability identifiers.
3. Add `ciu worktree up <id>` as the explicit selected-instance control-plane
   operation. It parses that checkout's `ciu.env` by exact path and replaces
   conflicting inherited CIU identity values.
4. Add local `worktree exec` for non-container consumers and alias-only target
   execution for declared containers. Neither mode implicitly starts anything.
5. Resolve container aliases to one exact worktree Compose project/service/
   network, verify the selected checkout mount unless explicitly disabled, use
   no shell, and propagate the child's exact exit code.

### Package D — gate, documentation, and qualification

1. Add CIU's real `assay.toml` lane(s) and a gate launcher pinned to the
   released Assay 1.0.0 artifact.
2. Resolve and verify `$CGROUP_PARENT_DEV_BACKGROUND`; refuse to start a test
   container when it is absent or not a loaded slice.
3. Run focused regression tests throughout, then the complete
   Assay-backed `tester-unified` gate against the final branch.
4. Update SPEC, CONFIG, FEATURES, README, CLI help, migration notes, and issue
   status in lockstep with the public behavior.

### Final qualification and merge

Perform one adversarial review across requirements, code, structured schemas,
failure/partial states, namespace translations, tests, and documentation. Add
combined-axis attacks not anticipated by the implementation tests, repair all
accepted findings, rerun the full gate, and merge the branch to `main` once.

## Later

- CIU-25: grounded stale worktree/stack detection and explicit reap semantics.
- A general external data-slot provision/drop hook only after a real consumer
  supplies requirements; do not resurrect the withdrawn PostgreSQL default.
- Move active issues into nyxloom's per-entry backlog format once that schema is
  implemented and adopted by CIU.

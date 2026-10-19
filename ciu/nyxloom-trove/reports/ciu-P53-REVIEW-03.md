# CIU P53 — Final broad design review

Review target: `8af65ea127009e9de6141694698a2f8c8db2e081` (candidate versus
`main`). The review was read-only and did not run tests or gates. The
implementation and regression tests addressing these findings are in the
follow-on candidate commit; an independent confirmation review is still
required before merge.

## Findings

### High

1. **Nested-root allocations become invisible to follow-on worktree commands.** [cli.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/cli.py:1976) passes the selected CIU root to `create`, `ensure`, and `adopt`, but `create --up` and the `up`, `inspect`, `lease`, and `rm` paths still pass the Git root ([cli.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/cli.py:2009)). In a family with only nested CIU roots, `create NAME --profile core --up` writes the record beneath that root, then fails to find it for startup. Later worktree commands have the same lookup problem. This breaks the documented nested-root lifecycle. Use one consistent record-root selector for every worktree verb, and add a lifecycle test that creates from a nested root, then inspects, starts, and removes the result. The current [nested-root test](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/tests/tests/test_ciu_cli_worktree.py:494) checks only argument forwarding.

2. **A moving `--base` can produce a ready overlay under a root absent from the checkout.** `create` proves the selected marker at one commit ([worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:4936)), then resets to the symbolic `base_ref` again ([worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:4647)). If `main` advances between those steps to a commit that removes the selected marker, record writing recreates the directory, the overlay is published there, and allocation can report `ready` without preparing that root. This violates the committed-target-root requirement and the claim that invalid requests fail before allocation effects. Bind marker validation, workspace creation, and reset to one resolved commit; add an oracle that moves `main` at this seam.

3. **A concurrent lease write can restore pending overlay recovery after readiness.** `apply_lease` reads a record and writes the whole modified snapshot without the allocation lock ([worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:2306)). If it reads while creation is `allocating`, creation then publishes `ready`, and the lease writes last, the stored record returns to `allocating` with `initial_overlay_pending=true`. A subsequent `ensure` can recreate an overlay the operator deleted after creation. This violates the one-time overlay ownership boundary; the same stale write can erase a newly saved `network_id`. Serialize and re-read all record mutations under a common lock, including the own-lease paths, and test this interleaving.

4. **Startup bypasses the new network ownership proof.** [up_instance](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:3768) requires only a ready record; the runtime network helper accepts any existing network of the configured name ([workspace_env.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/workspace_env.py:921)). After CIU records network object A, remove it and create an unlabelled object B with the same name, then call `ciu worktree up` without `ensure`: startup can use B. The ownership check added to `ensure` therefore does not protect the command that uses the network. Verify the object ID or owner label immediately before managed startup and refuse before launching the child; add a replacement-network startup oracle.

### Medium

5. **The “one-time” legacy network claim can overwrite existing ownership evidence.** [worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:4254) accepts `--adopt-existing-network` for an unlabelled object even when `network_id` already records a different object. Claim legacy object A, replace it with foreign same-name object B, then repeat `ensure --adopt-existing-network`: B becomes the new recorded owner. That defeats the documented [one-time claim](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/docs/CONSUMERS.md:126) and same-name replacement detection. Permit the explicit claim only when no object ID has yet been recorded; test replacement with the flag present.

6. **A Docker execution error is treated as network absence.** [worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:4161) catches every `OSError` from `docker network ls` and returns “network absent.” For example, `PermissionError` on the Docker executable lets a ready `ensure` return without checking a potentially present replacement. The contract permits absence, not indeterminate ownership. Treat only a missing executable as Docker absence and propagate other launch errors; add an oracle distinguishing those cases.

7. **Record renames are atomic but not durable across machine power loss.** [worktree.py](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/src/ciu/worktree.py:753) fsyncs the temporary file and replaces the record without fsyncing its parent directory. A power loss after the first allocation record is reported written can lose that directory entry, leaving a Git checkout without the overlay-intent or reset checkpoint needed for recovery. The schema-v5 contract calls those checkpoints durable. Fsync the containing directory after replacement, and cover the required syscall ordering with a focused writer oracle.

### Low

8. **The normative schema descriptions disagree with the shipped record.** [SPEC.md](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/docs/SPEC.md:288) says initial payloads are checkpointed in a schema-v4 record, and its JSON section lists only v1–v3 ([SPEC.md](/workspaces/vbpub/.worktrees/ciu-overlay-intent-20261013/ciu/docs/SPEC.md:4654)); new allocations emit v5. Update both descriptions so readers of the configuration and JSON contracts see the current fields.

This was read-only source and document inspection of `main...HEAD`. I did not run tests or gates.

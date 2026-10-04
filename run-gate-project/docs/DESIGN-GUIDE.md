# run-gate design guide

## A worktree is the complete judgment boundary

`--worktree W` selects the source tree whose commit is judged. The lane policy
must come from that same tree: a worktree can add a lane, change an Assay pin,
or update a shared root config before any of those changes reach the invoking
checkout. Reading the invoking checkout's config would make the run look
successful while judging a different declared gate.

run-gate discovers the invoking project to learn its position inside the Git
toplevel, then maps that relative path into W. It reads the mapped project's
`run-gate.toml` and the nearest `run-gate.root.toml` beneath W. The mapped
project config is required. The config path is printed before a lane starts,
and the run record stores the config path and SHA-256 for the exact bytes
parsed. It also records the path and SHA-256 of the inherited root config when
one supplied shared policy.

This rule preserves nested projects in a monorepo: a project at
`packages/service` in the invoking checkout maps to
`W/packages/service`, while the worktree root stays the Git judgment root.

## The runner belongs to the judged worktree

An exec lane uses a persistent runner managed by that project's deployment
tool. In a multi-instance workflow, the runner's name, network, and rendered
environment belong to the selected worktree. run-gate derives the name only
from that worktree's rendered `ciu.global.toml`. If the file is missing, the
worktree has no known runner identity, so run-gate refuses and directs the
operator to start that worktree's test-runner with CIU. Before `docker exec`,
run-gate checks `docker ps`; a stopped runner receives the same worktree-local
remedy.

An environment may declare `container_name` when its deployment authority
provides that explicit stable name. CIU-derived names use the worktree-local
rendered file and report its path in the execution header.

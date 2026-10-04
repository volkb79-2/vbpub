# run-gate design guide

## A worktree is the complete judgment boundary

`--worktree W` selects the source tree whose commit is judged. The lane policy
must come from that same tree: a worktree can add a lane, change an Assay pin,
or update a shared root config before any of those changes reach the invoking
checkout. Reading the invoking checkout's config would make the run look
successful while judging a different declared gate.

run-gate discovers the invoking project to learn its position inside the Git
toplevel, then maps that relative path into W. It reads the mapped project's
`run-gate.toml` and the nearest `run-gate.root.toml` inside W's Git toplevel.
Central lookup stops at that boundary, so a nested standalone repository
cannot inherit a parent's gate policy. The mapped project config is required.
The config path is printed before a lane starts,
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

## Runner modes name the runtime contract

Environment names cannot safely select execution behavior. The former
implicit `host` name meant a container, while `bare-host` meant a subprocess;
the same config line could therefore change meaning when built-in resolution
changed. Each environment now declares `mode` directly:

| mode | execution | required facts |
|---|---|---|
| `ephemeral` | a fresh container for this lane | `image` |
| `exec` | a persistent externally managed runner | `image` and a declared or judged-worktree-derived identity |
| `host` | subprocess on the invoking host | no image |

This makes the name ordinary data. `[environments.host] mode = "ephemeral"`
is a container; any other name with `mode = "host"` is a host subprocess.
Missing `mode` is an error instead of an implicit choice. `migrate-modes`
inserts the old implicit choice as literal config text, preserving comments
and requiring the operator to review the resulting diff. It migrates one
file per call so a project config and an ancestor `run-gate.root.toml` can be
checked independently.

## Source-backed Assay identifies code as source

Internal vbpub lanes install Assay from the selected worktree with an editable
install. That source tree is not a built wheel or zipapp, so an artifact digest
would claim evidence that does not exist. Run-gate instead checks that the
imported `assay` module resolves to the selected worktree's
`assay/src/assay/__init__.py`, and that the verdict has a non-empty
`assay_version` plus a full Git commit matching the commit captured in the run
record. Both checks matter: the path binds Python to the chosen source tree,
while the commit binds the verdict to the tree run-gate sampled. A source-mode
verdict without that identity or with a different commit is ERROR. The same
Python interpreter both performs the import check and executes Assay, so a
different `assay` script on `PATH` cannot replace the checked package.

External consumers use immutable artifacts and retain the full
`judge_provenance` check. The two modes use evidence appropriate to their
input: source location and selected commit for an editable source tree, or an
artifact digest for a built distribution. Run-gate does not convert one form
into the other.

The same closed-contract principle applies to process status. A raw command
status is preserved in `LaneResult`; run-gate returns only PASS 0, FAIL 1,
ERROR 2, NOT_RUN 3, or BUDGET_EXCEEDED 4. This prevents a command's code 2 or
3 from being mistaken for a run-gate refusal. ERROR means the gate could not
produce a judge result; NOT_RUN names a precondition that prevented the judge
from starting; a hard lane budget has its own status. `--json` and the human
summary carry the verdict and raw code so callers need not infer them from
stderr text.

See [the normative mapping](../SPEC.md#2-cli-contract) and [migration
instructions](../CONSUMERS.md#closed-results-and-runner-mode-migration).

## Sequences own the whole composite

A shell `&&` chain returns the wrapper command's status. That flattens a
nested gate's result and makes wrappers reimplement stop policy, worktree
forwarding, base selection, and history. A native `kind = "sequence"` keeps
the members and order in run-gate's lane model. It runs members serially,
records each result, and carries one admission ticket across the composite.

The sequence resolves its request base once. `[project].trunk` only supplies
a base when HEAD is a merge commit on that declared local branch; the first
parent is the base. A non-merge trunk HEAD has no post-merge change set, so it
is NOT_RUN with `no-base`. The resolved commit is passed only to member lanes
that request it. This avoids comparing different members against different
moving refs during one composite.

## Selective requests stay with the selected lane

Assay's `--reuse-from`, `--rejudge`, and `--rejudge-outcome` describe one
specific R2 rerun. run-gate accepts them only on a single assay lane and
forwards them only to that judge invocation; a sequence cannot accidentally
apply one candidate selection to every member. Command argv is closed by
default. A command must declare `accepts_args = true` before arguments after
`--` are appended, and composites refuse those arguments because their member
argv has separate ownership. The [consumer examples](../CONSUMERS.md#selective-lane-requests)
show both entry points.

## Docker names order daemon-wide admission

An OS lock file coordinates only processes that share its filesystem. A host
shell, a devcontainer, and CI can all reach one Docker daemon while seeing
different filesystems, so run-gate allocates a ticket by attempting
`docker create --name ciu-res-gates-N`. Docker's name uniqueness is the shared
compare-and-swap. A conflict causes a fresh listing and retry; the ticket
number is the highest visible ticket or tombstone plus one.

Releasing the highest ticket starts its `/bin/true` container and waits for
it to exit, leaving a tombstone so a later gate cannot reuse the number. A
lower ticket can be removed once a higher number is visible. The published
count is a separate `ciu-admission-N` object, replaced by generation with
same-daemon `docker create --name` allocation. This mode is opt-in and count
only; resource governance stays independent.

Docker may ask systemd to auto-create a misspelled slice, which then reports
`LoadState=loaded` despite having no installed unit file. Where run-gate can
reach host systemd, it also requires a non-empty `FragmentPath`; containerized
gate clients rely on their outer launcher to verify the host unit before
launching.

An owner tuple includes boot id, pid, start ticks, and PID namespace inode.
A reader may prove an owner dead only when host, boot id, and PID namespace
match its own view. A different namespace is unknown and remains live until
the wait or run deadline; this avoids reclaiming a live ticket by mistaking
an invisible pid for a dead process. The cross-tool label contract is pinned
in the shared `tests/fixtures/admission-label-grammar.json` fixture so the
later CIU port can compare the same bytes.

Each lane's elapsed budget starts after the runner locks are held and count
admission succeeds. Waiting for another gate to release its ticket therefore
does not consume the lane's own execution budget.

## Durable mutation state survives short-lived worktrees

Assay keeps verdict and progress beside the judged project for this run, but
mutation resume state must outlive a CIU-managed worktree. run-gate therefore
passes `--state-dir` under the checkout that owns the shared Git directory,
keyed by the project's path there. This keeps retries resumable after the
worktree is removed. The separate RG-49 repair for root-owned parents in
partial-bind containers remains open; see the [consumer contract](../CONSUMERS.md#resume-progress-and-durable-assay-state).

## Keep failed assay evidence outside short-lived worktrees

Assay writes its current verdict and progress under the judged project. A
retry intentionally overwrites those paths, and a CIU-managed worktree may
then be deleted. On a failed assay result, run-gate copies the current files
to the checkout's `.run-gate/failed/<lane>/<run_id>/` archive before the next
run can replace them. The archive is git-ignored, owner-only, and bounded to
ten entries per lane. A short digest is printed at the point of failure so
the operator can see the first failing node ids without opening the full
transcript.

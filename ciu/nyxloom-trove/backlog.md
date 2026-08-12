# ciu dev backlog — un-carved ideas

## Historical carve record: CIU-22 and CIU-24 are shipped

`ciu-P01` originally packaged CIU-20..24. The early review correctly split
CIU-22 and CIU-24 until their observable contracts were designed. Those designs
are now implemented and released: CIU-22 is S16.1's shared-infra join and
CIU-24 is S16.3's primary-config concurrency budget. Their historical reasoning
remains below because it explains why the shipped mechanism is imperative
post-up joining and a repo-level policy, rather than the inert alternatives
initially proposed. The current status board is
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md).

### CIU-22 — shared-infra join for `ciu worktree` (historical design record)

**Why it is not carvable as written.** The round-2 handoff proposed "`ciu.env`
gains a list of extra networks to join." That mechanism is **inert**: ciu writes
**no `networks:` key anywhere** in `src/ciu/` (verified — zero matches; the only
`networks` mention in `src/` is a comment at deploy.py:1597). Service→network
attachment is entirely **stack-author-owned** (the author's own compose declares
the per-service and top-level `networks:` blocks). Nothing in ciu reads such a
list and nothing can attach a service to a second network through the overlay.

**The real shape (candidate, not yet decided).** The workable mechanism is
imperative `docker network connect <ref-network> <container>` run AFTER the new
instance is up — precedent: `_connect_devcontainer_to_network`
(workspace_env.py:608), which already attaches a container to a network outside
compose. Connectivity therefore spans **two verbs**: `worktree add` records the
ref-network intent in the new `ciu.env`, and `ciu up` (in the new worktree's own
process, after `docker compose up`) performs the join. Open design questions
before carving: which tier joins (only the diverging tier, not the whole
instance — moving `DOCKER_NETWORK_INTERNAL` onto the ref network would destroy
S16's cross-instance isolation), idempotency of the connect, how a
ref-not-running / unresolvable-ref failure surfaces, and how connectivity is
proven in a gate that has **no docker socket**.

### CIU-24 — worktree instance concurrency budget (historical design record)

**Why it is not carvable as written.** The proposed key
`governance.max_concurrent_worktrees` lives in a **per-stack**
`[<root>.governance]` table, but `worktree.add()` (worktree.py:172) **loads no
stack config at all**, and a repo with several stacks has several such tables
with no single one to read. The ambient override `CIU_MAX_CONCURRENT_WORKTREES`
is reachable; the config-FILE key is not.

**The real shape (candidate, not yet decided).** Decide where the cap lives: a
**repo-level** location (it governs host capacity, an instance-count concern,
not a single stack) that `add()` can read, versus a stack-resolution rule for a
multi-stack repo — and reconcile with **CIU-13**'s established global
`[governance]` / per-stack merge (which level wins). Only after that is the
count semantics (primary counts; only registered-AND-deployed worktrees count;
unset at both levels = NO cap) carvable.

## O4 deferred proof (CIU-26 remains open)

CIU-23's injectable data-isolation provisioner is tested in-gate against a
**fake** because the package gate does not supply a live Postgres. The real
Postgres integration proof is filed as **CIU-26** and remains open until an
explicit external integration lane proves `PostgresProvisioner.provision/drop`.

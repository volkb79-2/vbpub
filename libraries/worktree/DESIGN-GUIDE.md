# Worktree library design guide

The library is intentionally neutral so CIU is not a hidden dependency of
CMRU. Git-family discovery, identity, records, leases, and cleanup have one
implementation and one set of failure categories. Product adapters retain only
policy that the neutral layer cannot prove.

The physical path is the default identity input because a logical container path
can be the same for several host checkouts. Path identities are lexically
canonical absolute strings: `.` and `..` are normalized, but symlinks are not
resolved and no filesystem query is made. A container cannot safely resolve a
daemon-host path with its own kernel. A caller constructing a visible name from
the identity may provide an explicit canonical identity path, which is
persisted and rechecked rather than hidden in an adapter-only calculation. A
missing identity key means the physical path; a present malformed value is a
refusal, not a signal to fall back. The six-character base-36 value is short
enough for resource names but collision admission is mandatory: a short name
is never evidence that two paths are the same.

The record lives beside the Git object/ref store rather than inside the linked
checkout. That keeps lifecycle evidence available when a checkout is damaged
and prevents ordinary project files from becoming an implicit state database.
The family lock is separate from CIU's root lock because allocating two
worktrees in one Git family must serialize, while two nested CIU roots may
prepare independently.

Before invoking the cleanup callback, removal re-reads the durable record and
checks the actual checkout top level, branch, Git common directory, and source
checkout. A changed or missing checkout refuses before adapter side effects;
this prevents a damaged record from directing cleanup at an unrelated branch.
After preflight, the cleanup callback runs before Git removal. This is the
recovery boundary: Docker/Compose cleanup can refuse while the checkout and
its exact identity remain available for a retry. Removing Git state first
would erase the only safe ownership evidence.

Inventory and inspection distinguish absence from indeterminacy. Only a
genuinely absent record directory is empty; unreadable state is a refusal, and
an inaccessible checkout is not reported as missing.

Native Git-worktree inventory is centralized too. `list_git_worktrees()` parses
`git worktree list --porcelain -z`, so a path containing spaces or newlines is
not reparsed as presentation text. Its `GitWorktree` records carry Git's
primary, detached, bare, locked, and prunable facts. The shared layer never
stats those literal paths: a consumer may be running in a different namespace.
The `prunable` bit is Git's registration state, not a path-visibility test, so
consumers preserve HEAD independently and do not report visibility from that
bit. CIU adapts these records for its CLI; CMRU filters them by its transaction
branch policy and takes the reported HEAD rather than running a second parser
or per-worktree `rev-parse`.

Shared-record lookup is centralized as `find_workspace()`: it compares an
exact absolute stored worktree path and never canonicalizes or probes the
checkout. `list_workspaces()` rejects duplicate ownership before returning its
inventory, and the lookup retains the same guard for alternate listing
implementations. This preserves the namespace boundary and treats ambiguous
ownership as malformed state instead of choosing whichever record sorts first.

CIU and CMRU therefore share mechanics but not policy. CIU maps one workspace
to one or more committed CIU roots and adds root identity to runtime names.
CMRU allocates one context per Git family and coordinates those contexts in
release order; it never pretends independent repositories share one atomic Git
commit.

The substrate is qualified independently as well as through both consumers.
That is deliberately a test boundary, not a third release boundary: its
tester-unified `worktree` lane runs the complete library suite through R0-R3,
including branch coverage, serial mutation, and import-break rejection. This
catches a shared-core regression even when both adapters happen to exercise the
same changed path in a way that masks the defect.

Legacy product records that predate the shared record use
`remove_unrecorded_workspace`; that operation first adopts the registered
checkout through the neutral record path and then uses the same removal
implementation. It exists for migration only, not as a second lifecycle
algorithm.

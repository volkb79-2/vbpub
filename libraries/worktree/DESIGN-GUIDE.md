# Worktree library design guide

The library is intentionally neutral so CIU is not a hidden dependency of
CMRU. Git-family discovery, identity, records, leases, and cleanup have one
implementation and one set of failure categories. Product adapters retain only
policy that the neutral layer cannot prove.

The physical path is the default identity input because a logical container path
can be the same for several host checkouts. A caller constructing a visible name
from the identity may provide an explicit canonical identity path, which is
persisted and rechecked rather than hidden in an adapter-only calculation. The
six-character base-36 value is short
enough for resource names but collision admission is mandatory: a short name
is never evidence that two paths are the same.

The record lives beside the Git object/ref store rather than inside the linked
checkout. That keeps lifecycle evidence available when a checkout is damaged
and prevents ordinary project files from becoming an implicit state database.
The family lock is separate from CIU's root lock because allocating two
worktrees in one Git family must serialize, while two nested CIU roots may
prepare independently.

The cleanup callback runs before Git removal. This is the recovery boundary:
Docker/Compose cleanup can refuse while the checkout and its exact identity
remain available for a retry. Removing Git state first would erase the only
safe ownership evidence.

CIU and CMRU therefore share mechanics but not policy. CIU maps one workspace
to one or more committed CIU roots and adds root identity to runtime names.
CMRU allocates one context per Git family and coordinates those contexts in
release order; it never pretends independent repositories share one atomic Git
commit.

Legacy product records that predate the shared record use
`remove_unrecorded_workspace`; that operation first adopts the registered
checkout through the neutral record path and then uses the same removal
implementation. It exists for migration only, not as a second lifecycle
algorithm.

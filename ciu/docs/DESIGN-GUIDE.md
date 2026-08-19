# CIU — Design guide: structured worktree control (WHY)

For adopters and reviewers who want the reasoning behind the machine-readable
surface, not just how to use it. The authoritative normative contract is
[SPEC.md](SPEC.md) (§S16.4, §S16.5); the adoption walkthrough is
[CONSUMERS.md](CONSUMERS.md); the capability list is `ciu capabilities`.

## Why a versioned, closed JSON surface at all

CIU's worktree lifecycle is an *environment provider* for human tools, IDEs,
test fan-out, and durable automation (nyxloom). Before this milestone an
automation consumer could only:

- parse CIU's **prose** output (fragile: phrasing changes freely);
- infer what CIU can do from its **version number** (SemVer says nothing about
  which capabilities a given build shipped);
- trust a **name or stale record** as if it were a current Git fact (a
  worktree can move, be removed, or sit on a branch it was not created on).

All three are the same failure: an automation boundary that treats *presentation
or inference* as *identity*. The decision D-009 (see `nyxloom-trove/decisions.md`)
replaces them with two explicit contracts:

1. **Structured JSON documents** (`schema_version: 1`) with a closed
   `operation` and closed `status` vocabulary. A consumer can validate the
   shape against the shipped vocabulary instead of string-matching.
2. **A versioned capability allowlist** (`ciu capabilities --json`). A consumer
   allowlists the identifiers of the contracts it actually depends on; a CIU
   that does not advertise an identifier cannot be silently trusted to provide
   the feature, and a CIU that does is pinned to the code path that shipped
   with that identifier in the same release.

## Why Git facts are freshly derived, never frozen or guessed

The durable instance record (`ciu.worktree-instance.json`) deliberately stores
**no current HEAD** and no derived state that Git already owns (decision
D-003). It stores only identity facts CIU created: logical name, display name,
branch, Git path, CIU-root offset, allocation time, base reference, lifecycle
state, and runtime identity. Inspection (`ciu worktree inspect`) therefore
re-derives current Git facts from the live `git worktree list` and
`git status --porcelain` every time.

This split has one rule: **a mismatch is a refusal, never a repair.** If the
record says branch `feature-x` but Git registers `main`, or the record's
checkout is no longer a registered worktree, CIU refuses with the discrepancy
instead of quietly reporting either value. The alternative — "repairing" the
record or reporting an inferred value — is precisely the silent-wrong-answer
anti-pattern the estate doctrine forbids: an automation consumer that reads a
"branch" from a stale record would run the wrong checkout's tests with a
convincing-looking label on the result.

The same refusal applies to `git status` being unreadable: an unknown state is
reported as a refusal (`[S16] could not read git status`), never collapsed into
"clean". And `(detached)` is its own boolean, never folded into "attached" or
"unknown".

## Why one envelope and one closed vocabulary for every document

`create`/`ensure`/`adopt`/`add`, `inspect`, `list`, and `remove` all speak the
same envelope: `schema_version`, `operation`, `status`, `instance` (the
persisted record), plus `git` where fresh facts apply. There is exactly one
place a consumer must learn the shape, and the closed vocabulary is a single
constant in code (`WORKTREE_JSON_OPERATIONS` / `WORKTREE_JSON_STATUSES`), so a
new value cannot be introduced accidentally.

Removal keeps the same contract under partial failure: CIU captures the
validated pre-state, runs `ciu clean` **then** `git worktree remove` (the
normative order — see SPEC §S16), and only then emits `"status": "removed"`.
A failed clean or failed git removal raises the existing `WorktreeError`,
which names the retained resources; there is never a success document for a
removal that did not happen. This matters because the failure mode is
unrecoverable-by-CIU: once the checkout is gone, so is the rendered config
that told CIU what to clean, and root-owned `vol-*` directories can then only
be removed by hand.

## Why `capabilities` is a separate, sorted, closed allowlist

`worktree.identity.v1`, `worktree.inspect.v1`, and
`worktree.lifecycle-json.v1` are the identifiers advertised in this release.
Each maps to a shipped code path:

- `worktree.identity.v1` — schema-v1 instance records and the create/adopt/
  ensure/add lifecycle;
- `worktree.inspect.v1` — the inspect document and the managed list document;
- `worktree.lifecycle-json.v1` — the lifecycle JSON envelopes.

`up` and `exec` are deliberately **not** advertised: their packages (P05/P06)
have not shipped, and advertising a contract before its code path exists is
the exact "SemVer inference" failure this document exists to prevent. An
identifier is added to `WORKTREE_CAPABILITIES` in the same commit as the code
path it names, or not at all.

## Rejected alternatives

- **SemVer-based feature inference** — rejected: a version bump carries no
  per-capability signal, and consumers would parse version strings (the same
  string-matching fragility as prose). Capability identifiers are the
  allowlist primitive instead (D-009).
- **Freezing HEAD in the instance record** — rejected (D-003): the record is
  durable identity, not a cache; a frozen HEAD goes stale the moment the
  branch moves and would then be a *wrong* answer wearing a *durable* label.
- **A single unbounded JSON blob with whatever keys happen to exist** —
  rejected: closed vocabulary + a fixed schema version is what lets a
  consumer fail fast on an unexpected shape instead of silently ignoring
  unknown fields.

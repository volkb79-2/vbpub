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

`worktree.identity.v1`, `worktree.inspect.v1`, `worktree.lifecycle-json.v1`,
`worktree.up.v1`, `worktree.exec-local.v1`, and `worktree.exec-target.v1` are
the identifiers advertised in this release. Each maps to a shipped code path:

- `worktree.identity.v1` — schema-v1 instance records and the create/adopt/
  ensure/add lifecycle;
- `worktree.inspect.v1` — the inspect document and the managed list document;
- `worktree.lifecycle-json.v1` — the lifecycle JSON envelopes;
- `worktree.up.v1` — exact selected-worktree `up` (S16.6);
- `worktree.exec-local.v1` — exact local `exec` (S16.6);
- `worktree.exec-target.v1` — declared container-target `exec` (S16.7).

An identifier is added to `WORKTREE_CAPABILITIES` in the same commit as the
code path it names, or not at all.

## Why `up` and `exec` take one exact selected instance

A worktree family has one primary checkout and many linked ones; each linked
checkout is a distinct CIU instance with its own `INSTANCE_ID`, network, and
`REPO_ROOT` (a hash of the *physical* path, S2). Running a command "in the
worktree" from the wrong process environment is therefore the same failure as
the one inspection guards against: the ambient `REPO_ROOT` describes the
PRIMARY checkout, so a naive `cd <worktree> && ciu up` would argue about which
instance is real, and could act on the wrong one.

Both `worktree up` and `worktree exec` therefore build the child environment
from the SELECTED instance's own `ciu.env`, by exact path, after stripping
every CIU identity key from the ambient environment. The selected value must
agree with the durable record (`REPO_ROOT` = the record's CIU root, and the
record's `INSTANCE_ID`/network) — a mismatch refuses rather than running with
a mixed identity. This is the same "derive or read, never invent" rule as
inspection, applied to where a command executes.

`exec` deliberately never starts anything: it is the *execute-in-this-exact-
place* primitive, not a shortcut for `up`. It requires a `--` separator and
passes argv to `subprocess.run` as a list with no shell, so spaces, globs,
`$()`, semicolons, and leading dashes arrive byte-for-byte at the child and
cannot be interpreted by a shell or misparsed as CIU flags. The child's exit
code is returned exactly — an automation lane that runs a gate command through
`exec` needs that code, not a wrapper's guess.

## Why container targets are declared aliases, not arbitrary services

`exec --target` lets an automation lane run a command *inside* the stack's
tester container. The dangerous alternative is "pick any service by name" —
an arbitrary service-selection escape hatch where a consumer could reach a
container that was never meant to be an execution boundary. CIU therefore
requires the target to be **declared** in the instance's own global config
(`[ciu.worktree.exec_targets.<alias>]`) with exactly four keys (`stack`,
`service`, `workdir`, `requires_worktree_mount`) — no arbitrary selection, no
invented defaults. The alias is a Git-safe single component, and an unknown
key or malformed value refuses before Docker is ever touched.

Selection is by the **exact** compose project/service/network identity of the
selected instance (derived with the existing naming rule and the instance's
own `DOCKER_NETWORK_INTERNAL`), and exactly one already-running container must
match. `up` is never started implicitly: a missing container is a refusal, not
an invitation to create one.

## Why the worktree-mount proof exists, and why it reads Docker's output

A container that looks like the right project/service could still be the
WRONG checkout — the primary worktree mounted while a linked worktree is
selected, or a sibling's container on a different network. By default
(`requires_worktree_mount = true`), CIU proves the container has a bind mount
whose host source is the selected Git worktree's **physical** path at a path
containing the declared `workdir` before running anything.

The proof reads only Docker's own `inspect` output, never a local filesystem
predicate on a path belonging to the other namespace. The host-side `Source`
is compared against the physical translation of the record's Git path (via
`to_physical_path` with the target's own REPO_ROOT/PHYSICAL_REPO_ROOT), and
the container-side `Destination` against the declared container `workdir` —
each value is compared in the namespace it belongs to. This is the estate's
namespace-translation rule applied to containers: an `is_file()` on a
container→host translation would ask the wrong kernel, exactly the CIU-15 /
dstdns class of incident the doctrine records. `requires_worktree_mount =
false` is the explicit opt-out for a deliberate non-source utility container;
it never weakens project/service/network uniqueness.

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
- **Shell-form exec (`sh -c "..."`)** — rejected: a shell rewrites argv
  (spaces, globs, `$()`, `;`), so an automation lane could not trust that
  what it asked to run is what ran. A list argv with no shell and a mandatory
  `--` is the only faithful form.
- **Arbitrary service-name target selection** — rejected: a consumer could
  reach a container never intended as an execution boundary. Declared aliases
  with a closed key set are the only surface (D-007).

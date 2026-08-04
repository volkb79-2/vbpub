---
schema_version: 1
id: srdm-P10-overlay-rw
project: srdm
title: "access: rw via an overlay upper layer, and the holder recognizer it requires"
tier: sonnet5-high
input_revision: "1142e39c"
depends_on: [D-027, D-028]
session: fresh
source: {kind: roadmap, ref: nyxloom-trove/roadmap.md}
scope:
  touch:
    - "internal/expose/**"
    - "internal/consumer/**"
    - "internal/harvest/**"
    - "internal/config/**"
    - "cmd/srdm/**"
    - "tools/canary.sh"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/reports/srdm-P10-LOG.md"
  forbid:
    - "internal/store"
    - "internal/publish/publish.go"
oracles:
  - id: O25
    observable: "A generation exposed rw through an overlay is byte-identical to its release afterwards: every class tree still verifies against the manifest once the consumer has written through the merged view."
    negative: "The lower generation is modified, which is the whole failure the overlay exists to prevent — it would put us back at dirty_capable with extra machinery."
    gate: privileged-e2e
  - id: O26
    observable: "Two servers may hold rw exposures of the SAME generation at once; each sees only its own writes, and neither sees the other's."
    negative: "The single-consumer limit survives the change, or worse, one server's writes are visible to the other."
    gate: privileged-e2e
  - id: O27
    observable: "consumer.Resolve reports a holder when a process in a SECOND mount namespace holds an overlay whose lowerdir is under an srdm root, and teardown is refused naming it."
    negative: "D-028's silent leak: the unmount succeeds, frees nothing, the content stays readable through the overlay, and the guard said nothing was holding it."
    gate: privileged-e2e
  - id: O28
    observable: "harvest reads the merged view of a named server's overlay and produces a release whose manifest equals a from-scratch stage of the same resulting content; a file DELETED through the overlay is absent from the harvest."
    negative: "Whiteouts are read as real files, or the harvest silently takes the lower's copy of a file the update replaced."
    gate: privileged-e2e
gates: [unit, privileged-e2e, coverage, canary]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
  - "the overlay upperdir cannot live on the state filesystem"
---

# P10 — `access: rw` via an overlay upper layer

## Working setup

Worktree `/workspaces/vbpub/.worktrees/srdm-p10-overlay-rw`, branch
`feat/srdm-P10-overlay-rw`, cut from `main`. Never merge `main` into the
branch before gating — rebase — or the coverage gate measures the wrong
delta.

## Context to read first

Paths from the **vbpub repo root**, one level above this project.

1. `shared-ramdisk-depot-manager/nyxloom-trove/PLAN.md` — authoritative.
   §The measured ground and §Direction 2 above all. The old master plan in
   `wings-cgroups/` is superseded history; do not cite it.
2. `shared-ramdisk-depot-manager/nyxloom-trove/decisions.md` — **D-027** and
   **D-028** are this package's premises, and D-012/D-018/D-019/D-020/D-022
   are the reasoning they build on.
3. `shared-ramdisk-depot-manager/internal/expose/` — `expose.go` for `plan()`
   and `sourceBase()`, `hostbind.go` for the preconditions and `bind()`.
4. `shared-ramdisk-depot-manager/tools/overlay-copyup-probe.sh` and
   `overlay-holder-probe.sh` — the two measurements this package rests on.
   Re-run them if anything surprises you rather than reasoning about it.

## Why

Today `rw` unseals the shared generation **in place** and `lchown`s it to the
game's uid (D-020, D-022). The consequences are all real and all recorded:
the generation is marked `dirty_capable`, cannot be shared with a second
consumer, cannot be used as a source until re-verified, and the only repairs
are republish (discard) or harvest (keep).

D-027 measured the alternative and adopted it. With the sealed generation as
`lowerdir` and a per-server `upperdir`, the updater's writes land in the
upper — bounded by what it actually touches — while the lower stays pristine,
sealed and **shared with every other consumer**. "Write causes copy-up" is the
feature here, not the cost.

This unblocks M2: P09 rehearses a two-server migration, and rw as it stands
cannot serve two servers.

## What to build

1. **`expose`: an overlay mode for `rw`.** Replace the in-place unseal rather
   than adding a second mode — two modes doubles the surface and the old one
   is worse on every axis. Per binding (the existing `Binding` maps 1:1):
   `lowerdir` = the sealed `ExposePath` subpath, `upperdir`/`workdir` under
   `cfg.StateDir`, `target` = the volume path as today.
   - Upper and work **must be on the same filesystem as each other**, and
     that filesystem **must not itself be an overlay** — measured, an overlay
     is refused as an upperdir outright. `StateDir` satisfies both.
   - Upper is **persistent**, deliberately: an in-place update that survived
     a crash but not a reboot would be worse than one that never started.
   - `unseal()` and its `chown` injection go away with the in-place mode, and
     with them `PreconditionWriteOwner` — an overlay writes to a directory
     srdm creates and owns, so there is nothing to hand over.

2. **`consumer`: the overlay recognizer (D-028).** This is not optional and
   not a follow-up. Measured: an overlay reports **its own** device and the
   lower's `major:minor` appears nowhere; unmounting the generation beneath it
   **succeeds and frees nothing** while the content stays readable. Superblock
   matching is blind to it. Add a second recognizer: an `overlay` mount whose
   `lowerdir=` (any element of the colon-separated list) resolves under an
   srdm root is a holder of that generation. Binds are matched by *device*
   because their path is unpredictable; overlays by *path* because their
   device is uninformative and `lowerdir` is chosen by the mounter.

3. **`harvest`: read the merged view, and name whose.** Multiple servers may
   now hold `rw` on one generation and diverge, so harvest can no longer infer
   its source. Add `--from-server <uuid>`, defaulted when exactly one server
   holds an `rw` exposure and **refused when more than one does** — that
   refusal is the honest replacement for the single-consumer limit, moved
   from where it cost sharing to where it actually matters.
   - Whiteouts must be handled: a file deleted through the overlay is absent
     from the merged view, and the harvest must agree. Do not walk upper and
     lower separately and merge in Go — walk the **merged mount**, which is
     the kernel's own answer and cannot disagree with what the server saw.

4. **`dirty_capable` becomes vestigial for overlay exposures** — the lower is
   never written, so nothing is dirty. Do not delete the flag: a record
   written by an older srdm can still carry it, and `doctor`'s drift check
   still earns its keep. State in the LOG which of D-020/D-022's conclusions
   survive and which the overlay retires.

## Out of scope

Anything under `internal/publish/publish.go` — publication is unchanged, and
this package binds what it produces. `access: ro` is untouched: D-027
**rejected** overlays there, and re-opening it needs a decision, not a diff.

## Watch for

- **A refused mount is not a refused exposure.** If the overlay mount fails,
  the server must not start with a partially-bound tree. `bind()`'s existing
  unwind is the model.
- **`plan()` is what oracle 19 asserts against.** Managed content still has
  to appear at exactly the declared class paths — the overlay changes the
  mount *type*, not where anything lands.
- Every canary touching `rw` in `tools/canary.sh` will need repointing, and
  a canary reporting "the mutation matched nothing" is a failure rather than
  a skip. Add one per new oracle.

## Gate

`tools/gate.sh <worktree> unit`, then `privileged-e2e`, then `coverage`
(against `main`, on a committed tree — exit 3 is NO MEASUREMENT, not a
pass), then `tools/canary-run.sh`. All four must be green, and the canary
run must report 0 survived.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a
forbidden file, **STOP** — write `BLOCKED: <reason>` to the LOG, commit, and
exit. Do not improvise a workaround. A BLOCKED exit is a success mode.

Product gaps are **decisions**: file a `D-<NNN>` and keep working.

## Decisions this package is expected to produce

- Whether the overlay `upperdir` is retained after `unexpose`, or discarded.
  Retaining it makes an interrupted update resumable; discarding it makes
  `unexpose` mean what it says. Both are defensible and the LOG must say
  which and why.
- What survives of D-020 and D-022. The overlay retires the in-place unseal
  those two decided; say explicitly which of their conclusions still hold so
  the next reader is not left reconciling three documents.

# srdm-P03 — LOG

Package: publication mount topology
Roadmap: `../roadmap.md`, Wave 1
Date: 2026-08-03

---

## What was built

The publication sequence, exactly as the master plan orders it:

```
mkdir 0700 -> mount tmpfs (size, mode, nodev, nosuid[, noexec])
  -> populate from the store -> VERIFY every file against the manifest
  -> chmod -R a-w -> mount --bind -> remount,ro,bind
  -> fsync the published-state record
```

The invariant the order exists to hold: **the visible path appears only as a
read-only bind of an already-verified tree.** Nothing is renamed into
visibility, nothing visible was ever writable, and no consumer can observe a
half-populated class. A publication interrupted anywhere leaves mounts under
the operation-private root and nothing under the visible one.

| | |
|---|---|
| `internal/mountinfo` | promoted out of `doctor`, which had a private copy. Full entry parsing, propagation classification (`rslave` shows up as `master:N` — P06 needs this), `Under`/`At` helpers. |
| `internal/publish` | sizing, the publication sequence, sealing, teardown, and reconciliation against the live mount table. |
| `store.Manifest.VerifyClass` | per-class content verification, and `ClassEntries`/`ClassBytes`. |
| `config` | the `.op` / expose / published-record layout. |

Two roots under `RunDir`, and the split is deliberate: operation-private work
happens under `.op`, where a half-populated tree is nobody's business; a
generation becomes visible only as a read-only bind of a verified tree.

## Gated at both levels

**Unit** — the mount sequence is asserted call-by-call against an injected
mounter, so a "tmpfs" is just the directory that was already there and the
topology logic is exercisable without privilege.

**Privileged (6 new oracles)** — because no fake can tell you what the kernel
does:

- a published exposure refuses writes with **EROFS**, not EACCES. The
  distinction is the point: EACCES is what a chmod-only seal would give, and
  it would not hold against root;
- the op tmpfs stays **writable** while only the bind is read-only, so the
  seal is a real remount rather than a permission trick;
- data classes carry `noexec`, and every class carries `nosuid` and `nodev`;
- teardown leaves nothing mounted;
- a class whose tmpfs cannot hold its content is **refused** — the
  2026-07-29 corruption shape — and leaves nothing mounted and no record;
- reconciliation classifies healthy / needs-republish / orphan correctly
  against the live mount table.

Five new canaries, one per contract: no-seal, writable-exposure,
teardown-order, publishes-excluded, no-orphans. **13 canaries, 0 survived.**

## Three bugs the tests caught, and one of them was in the design

**A nil deref in the failure path** — the classic Go named-return trap. The
deferred cleanup read the named return value `rec`, which any
`return nil, err` had already set to nil, so the cleanup panicked precisely
when it mattered most. The record now builds into a local that the defer
closes over.

**A substring match on "ro" that found it inside "root".** My own test. The
fake mounter now records structured calls and the assertion inspects the
`MS_RDONLY` flag, so there is no string surgery to get wrong.

**And the one that was not a test bug.** The teardown oracle failed
intermittently with `EBUSY`. `Type=exec` marks a unit active as soon as the
process has been **exec'd**, not when it has populated anything — so the
oracle, which waited for the charge to reach the written size within a
tolerance and then unmounted, was racing the worker's still-open file
descriptor. That is D-012's mechanism arriving from the other direction.

The fix is **D-013**: the hold unit is `Type=notify` with
`NotifyAccess=main`, and the worker signals ready only after population
completes, so "active" means "populated". Widening the tolerance or sleeping
before the unmount would have made a real race pass on a fast machine —
which is the anti-pattern the authoring guide names first. The oracle was
correct; the unit shape was not. `HoldBaseProperties` now returns that shape
and a unit test pins it, so the decision cannot decay into a comment.

## Decisions filed

`D-013` the hold unit is `Type=notify`, refining D-011 · `D-014` publication
verifies content, not modes (sealing changes them by design; the
extra-files check is kept, because dropping it alongside would leave content
smugglable into a published class).

## Gaps

- **Population is inline.** Pages are charged to the daemon's cgroup, which
  is wrong for production and fine for proving topology. P04 relocates it,
  and that is a rewrite of `publishClass`'s populate step, not an addition.
- **Reconciliation reports; it does not repair.** `NeedsRepublish` and
  `NotReadOnly` are surfaced but nothing acts on them yet — republish
  belongs with the boot path (P08).
- **No consumer resolution.** Teardown does not check whether anything is
  still holding a bind. That is P05, and it runs before teardown, not
  inside it.
- **`/run/srdm` propagation is untested.** The master plan requires it to be
  the host default so mounts reach an `rslave` Wings view. Asserting that
  needs a second container, which is P06.

## Verification

```
tools/gate.sh . unit    → gofmt, build, vet, all oracles green
tools/gate.sh . e2e     → 11 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh     → 13 canaries rejected, 0 survived
```

Three consecutive e2e runs, deliberately: the race D-013 fixed was
intermittent, and one green run would not have distinguished a fix from
luck.

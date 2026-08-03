# srdm-P05 — LOG

Package: consumer resolution and teardown safety
Roadmap: `../roadmap.md`, Wave 2
Date: 2026-08-03

---

## What was built

`internal/consumer`, and a refusal in `Teardown` that uses it.

Resolution matches on the **superblock**. A consumer's bind is at a path srdm
has never seen and cannot predict — `/home/container/paks`, chosen by Wings —
while the major:minor is the same number on both sides of a bind. So srdm
reads `/proc/<pid>/mountinfo` for every mount namespace except its own, looks
for its own generation's device, and names the container from the holding
process's cgroup path plus the Docker socket (a ~90-line HTTP client over the
unix socket, because the whole surface needed is one GET and the SDK would be
a larger dependency tree than srdm).

**There is no stored registry**, and that is the design rather than an
omission. Nothing tells srdm who mounted what in the host-bind shape: Wings
creates the container, Docker resolves the bind source in the host namespace,
srdm is not in the conversation. A table srdm maintained could only ever be a
second opinion about the kernel's, and the kernel is what holds the pages.
The registry is the resolution, done fresh at every ask. That is what the
master plan means by oracle 24 being "oracle 15 without the protocol's help —
the mode that has to get it right by inspection".

## Why the check is inside teardown (D-018)

P03 said teardown "does NOT decide whether it is safe to run". That was
wrong, and specifically so.

Every step of a teardown run while a consumer holds the content **succeeds**.
The unmounts return 0. The hold units stop. The slice goes. The record is
removed. And not one page comes back, because the consumer's namespace still
holds the superblock. There is no error afterwards, no state that looks
wrong, and nothing in the journal that reads differently from a healthy
teardown. The only instant at which the difference exists is before the first
unmount — so the check cannot be something a caller is trusted to run.

`Publisher` now takes a `Guard` the way it takes a `Holder`. `Holders` is
exported for `activate` and `rollback`, which have the same problem and
arrive with P08.

## The measurement that changed publication (D-019)

Matching on the superblock has an immediate consequence. On a systemd host
`/run` is shared, so a mount created beneath it is **delivered** to every
mount namespace that is a slave of the root — every service with
`PrivateTmp`, `ProtectSystem` or its own `unshare`. Every one of those copies
matches, so every one reads as a consumer, and teardown would be refused
forever by services that want nothing from srdm.

The obvious filter: such a copy carries `master:<srdm's peer group>`, so it
is downstream and srdm's unmount will take it away again. That was written.
Then it was measured, and it does not hold — a copy carrying exactly that tag
**survived** the host unmount with the content still readable, when the
namespace also held its own bind of the same superblock. In other
arrangements the same tag is removed as expected. Nothing in `mountinfo`
tells the cases apart.

The asymmetry decides it: over-filtering is a silent leak, which is the
failure this check exists to prevent; under-filtering is a refusal an
operator can see and act on. So srdm does not filter — and instead does not
hand the copies out. Publication binds its operation root onto itself and
marks it `MS_PRIVATE` before mounting anything beneath it. Marking a mount
private *afterwards* is too late; also measured, the copy has already gone.

## Four things the tests caught

1. **A filter that was wrong in the safe-looking direction.** The
   propagation rule above passed its own unit tests — written from the same
   wrong premise — and was only caught by putting it in front of a real
   kernel. Both are now pinned by an oracle that reads the surviving mount
   and the surviving content.
2. **The isolation mount became an orphan.** Reconciliation swept it up as
   an unrecorded mount under the srdm root and tore it down, which would have
   removed the isolation from under every live generation. It is
   infrastructure, alongside `RunDir`, and is excluded by name.
3. **The e2e consumer was a bad model.** `unshare --propagation private`
   holds an independent copy of every mount it inherited, so it pins every
   superblock on the host — the first version of the oracle passed for a
   reason production never has. It now runs `--propagation slave`, and the
   assertions name the consumer's own bind rather than pinning a count.
4. **A canary went stale.** `P03-no-orphans` reported "the mutation matched
   nothing" after the orphan check was rewritten around it — the code had
   moved and that canary was silently testing an unmodified tree. Which is
   what the script exists to say.

## Decisions filed

- **D-018** — the consumer check is inside teardown, not a precondition on
  it; and there is no stored registry, deliberately.
- **D-019** — publication mounts into a private root, because propagation
  cannot be filtered soundly.

## Gaps

- **`activate` and `rollback` are not refused, because they do not exist.**
  `Holders` is exported and is what they will call; the verbs arrive with
  P08. Until then the only guarded operation is teardown.
- **Isolation does not help against a namespace created after publication.**
  `unshare` copies the whole mount table whatever its propagation, so a
  service that restarts while a generation is published holds it. srdm will
  refuse the teardown and srdm will be right — the memory really would not
  come back — but the operator is told about a process that has nothing to do
  with the game. Whether that wants a narrower answer belongs with P06, which
  owns propagation properly.
- **The Docker half is unit-tested only.** The gate container has no Docker
  socket, so the client is exercised against a stub over a real unix socket
  and the e2e path always runs degraded. Naming a real container end to end
  needs the P06 harness.
- **The "any labeled container in any state" rule is not implemented.**
  Teardown safety asks about *running* containers, which is the narrower and
  correct question for memory. The stricter rule governs generation GC, where
  a stopped definition still pins what it will need on its next start — P08.

## Verification

```
tools/gate.sh . unit      → gofmt, build, vet, all oracles green
tools/gate.sh . e2e       → 22 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh       → 32 canaries rejected, 0 survived
tools/gate.sh . coverage  → see below
nyxloom lint              → clean
```

The coverage base is passed as an explicit SHA, never `HEAD~N`: background
commits from other projects land between this package's own, and a relative
base silently drifts onto them.

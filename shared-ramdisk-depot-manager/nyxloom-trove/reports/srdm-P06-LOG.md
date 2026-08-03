# srdm-P06 — LOG

Package: the `host-bind` exposure driver, `ro`/`rw`, and doctor's Wings
preconditions
Roadmap: `../roadmap.md`, Wave 2
Date: 2026-08-03

---

## What was built

`internal/expose` is the fork the whole pipeline has been building toward: an
interface and, for v1, one driver. `host-bind` mounts a generation's per-class
content onto the matching paths under a server's volume — which is what the
legacy `soulmask_tmpfs` does, now with verified immutable generations, a
journal, teardown safety and a doctor behind it.

`internal/wings` reads the two facts about the node srdm neither owns nor
configures: how the volume tree propagates, and whether Wings will run its
pre-boot chown walk.

Both preconditions **refuse rather than warn**, and a test asserts that no
refusal ever ships without a fix attached — a refusal with no remedy is an
outage with extra steps.

## The waiver, bounded

host-bind waives invariant 14 deliberately (master-plan decision 10): managed
content DOES appear under the server's host volume path, where Wings' disk
accounting, backups, SFTP and pre-boot walk all traverse it. That is the price
of needing no Wings patch.

What keeps the waiver bounded is one binding per **declared class path**, not
one per class. `code` is `Engine` plus `WS/Binaries`; each lands at its own
place in the volume. Binding the class root instead would put content where
the game does not look and shadow everything under that ancestor — and the
oracle asserts exactly this: content at the declared paths, nothing anywhere
else, `world.db` unchanged across expose and unexpose, and per-instance state
still writable underneath the mounts.

`Plan` refuses a profile whose class path would shadow an excluded one, and
that check runs in both directions. Binding AT `WS/Saved` is the obvious
mistake; binding at `WS` is the dangerous one, because the mount **hides**
what is beneath it — the saves are not overwritten, they become invisible,
and the damage is found when somebody goes looking for a world that is still
on disk underneath.

## What the tests found

**`rw` could not work as built, and the ephemerality oracle said so on its
first run** (D-020). An `rw` exposure was a bind of the published path — which
is itself a read-only bind — and a bind inherits its source's per-mount flags.
The write returned `EROFS` before it could prove anything about ephemerality.
The two modes now bind different mount points of the same superblock: `ro` the
published exposure, `rw` the operation tmpfs's own content root. Same pages,
same hold unit, same charge.

The half of that which is still open is stated rather than papered over.
Publication seals a class tree `chmod -R a-w`, and P06 does not unseal it, so
`rw` permits writes at the mount level while the modes still refuse them: root
can write through, a game container cannot. Closing it needs an ownership
model, and `harvest` is the only consumer of that decision — so P07 owns it.

**A test expectation that was wrong about unwinding.** The failed-bind case
expected the failing mount to be unwound too. It is not, and should not be: a
mount that failed never happened, and unmounting it would either error or
remove something that was already there.

## Decisions filed

- **D-020** — `access: rw` binds the writable side of the superblock; who may
  write through it is deliberately open until P07.
- **D-021** — Wings' node config is scanned for one key rather than parsed
  (srdm still has no YAML dependency, per D-005), and F1 is asserted by
  configuration because a patch in a Go binary cannot be detected. Both fail
  closed: anything unreadable is treated as the unsafe value, so ambiguity
  refuses rather than proceeds.

## Gaps

- **The container half of precondition 1 is never exercised against Docker.**
  The gate has no socket and no Wings, so `BindPropagation` is unit-tested
  against a stub and injected in e2e. → backlog, "a gate harness with
  containers in it".
- **Oracle 20's last clause is only half asserted.** "Neither failure is
  allowed to surface as a server start error" is shown by srdm refusing
  first; it is not shown against a server that actually starts, because
  nothing here starts one. → same backlog entry.
- **`rw` ownership is unresolved** (D-020). → roadmap, P07.
- **`activate` and `rollback` still do not exist**, so the "affected
  consumers stopped" precondition is enforced for teardown (P05) and for
  sharing, but not for the verbs that swap content underneath a running
  server. → roadmap, P08.
- **Nothing drives exposure from the CLI.** Like publication and hold, it is
  a library; the operator entry point is P08.

## Verification

```
tools/gate.sh . unit      → gofmt, build, vet, all oracles green
tools/gate.sh . e2e       → 31 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh       → 44 canaries rejected, 0 survived
tools/gate.sh . coverage  → see the commit
nyxloom lint              → clean
```

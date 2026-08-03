# srdm-P08 — LOG

Package: the operator surface — assignments, `activate`/`rollback`,
retention/GC, and the CLI verbs
Roadmap: `../roadmap.md`, Wave 3
Date: 2026-08-03

---

## The carve, first

P08 was carved as one package covering retention, the daemon, the admin
socket, boot restore, adoption and quarantine, doctor online, and every CLI
verb the master plan names. It is two, and the seam is not size.

`srdm-restore.service` republishes "assigned generations". **Nothing in
P01–P07 records an assignment.** `expose` takes a server id and remembers
nothing; a published record names a release and no consumers. So the boot
path had no input at all until somebody wrote down what an operator asked
for — and that same record is what `activate` re-points and what `gc` must
refuse to collect. It is the first thing, not a detail of the last.

P08 is therefore the operator surface. P08b is the boot path and everything
that makes it survive a crash.

## What was built

**`internal/assign`** is the record that did not exist: one document per
profile, naming the active release, the release before it, and the servers.
Fsync'd, because it is what a boot reads to decide what to bring back.

**`internal/opctl`** is the order, and the order is the entire content of the
package. Everything under it already worked; none of it knew about the
others.

```
publish the new generation -> move each server onto it ->
record the assignment -> tear the old generation down
```

The record goes in after the exposures and before the teardown, which is the
only position with no bad crash in it. Written first, a crash leaves an
assignment naming a release nothing has mounted. Written last, a crash after
the teardown leaves one naming a generation that no longer exists while the
servers are already on the new one. In between, every crash leaves the
servers reading the release the record names and at worst an old generation
still holding memory — which reconciliation can see, and which costs pages
rather than correctness.

Attach and detach use the **opposite order from each other**, and the
asymmetry is the same argument twice. Attach ADDS a consumer, so the
dangerous crash is a record claiming a server is attached when nothing is
bound: a boot would believe it had restored something it never mounted. So
attach exposes first. Detach REMOVES one, where the dangerous crash is a
record saying the server is gone while its mounts are still there, because
nothing would ever go looking for them again. So detach unexposes first.

Servers move **one at a time** rather than all-unexpose-then-all-expose: a
failure halfway through the second loop would otherwise leave the earlier
servers with nothing bound at all, which is worse than leaving them where
they were.

**The CLI**, finally. Every verb the v1 pipeline needs —
`activate, rollback, attach, detach, teardown, harvest, gc, status` — each a
one-shot root process under an `flock`. Until this package, `cmd/srdm` said
by name that none of it was reachable.

## Decisions filed

- **D-002 — closed.** Retention is 3, from configuration, and it is a floor
  on what is KEPT rather than a cap on what exists. Four pins come first —
  assigned, rollback target, published, channel target — and the number only
  chooses among what is left. `Validate` refuses a retention below 1, because
  "keep nothing beyond what is pinned" is what an unset field would mean.
- **D-024 — an assignment is declared intent, and it is not the registry
  D-018 refused.** They answer different questions: who is HOLDING a
  generation is a fact about the kernel, which is why it is resolved fresh
  every time; which servers SHOULD be reading a profile's content is a fact
  about what an operator asked for, which nothing else knows and no mount
  table can supply. Intent is recorded, reality is measured, reconciliation
  is the comparison — and a system with those the wrong way round is both
  unable to restore itself and confidently wrong about what it holds.
- **D-025 — v1 has no daemon** (proposed, confirm before P08b). Taking the
  master plan's daemon apart purpose by purpose: the provider socket and
  per-start lease resolution are v2, boot restore is a `oneshot` unit the
  plan itself specifies as a unit, and what is left is serialization. An
  `flock` buys that with no long-lived process and no "refuses when the
  daemon is down" mode — a mode which, on a node whose daemon has crashed,
  refuses precisely the operations needed to fix it.

## What the tests found

**A `gc` reason precedence I had backwards.** The published pin was
overwriting the assigned one, so the active release reported "published: a
live generation was built from it" — true, and the wrong answer. `published`
is a *consequence* of `assigned` for the active release, so reporting it
there names the symptom. The reasons are now tried most-declarative-first, on
the rule that the useful answer to "why is this kept" is the one the operator
can change. It still reports `published` for a release nobody assigned, which
is the rolled-back-but-not-torn-down case and the one where it is genuinely
surprising.

**Unit-name collisions across e2e tests**, again — the same failure harvest
hit in P07. A release id fixes the generation id, which fixes the transient
hold unit names, and a unit outlives the test that made it for as long as
systemd takes to reap it. Two tests sharing `rel-1` race for
`srdm-hold-<g8>-code.service`. The harness now prefixes every release id with
the test name. Worth stating as a property of the harness rather than a bug
found twice: **any e2e case that publishes must own its release ids.**

**The order oracles had to read the durable file from inside the fakes.** A
sequencing contract between a file write and a mount is not observable from
outside the sequence — by the time the operation returns, everything has
happened. The fake publisher's `Teardown` and the fake driver's `Expose`
therefore record what the assignment on disk said at the instant they ran,
which is what makes "recorded after the exposures and before the teardown"
an assertion rather than a comment.

## Gaps

- **Boot restore does not exist.** The record it needs now does. → roadmap,
  P08b.
- **Reconciliation still reports and does not repair.** → roadmap, P08b.
- **`doctor` has no online half**, and still reports drift that nothing acts
  on. → roadmap, P08b.
- **GC's "no labeled container in any state" term is not implemented.**
  `consumer.DockerLister` offers `RunningContainers` alone, and P05 chose
  that deliberately for teardown safety. Latent in v1 — labels are v2, and gc
  collects releases while a container references a generation — but the boot
  path is where a stopped-but-configured consumer first matters. → roadmap,
  P08b.
- **The lock is per node, not per profile**, so two profiles cannot be
  operated on concurrently. Free on a node with one game; the fix is a
  per-profile lock file, not a daemon. → roadmap, P08b, and stated as D-025's
  cost.
- **`daemon`, `stage` and `operation` have no verb.** Each says by name what
  it is waiting for rather than being quietly absent.

## Verification

```
tools/gate.sh <worktree> unit      → gofmt, build, vet, all oracles green
tools/gate.sh <worktree> e2e       → 39 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh                → 67 canaries rejected, 0 survived
tools/gate.sh <worktree> coverage  → see below
nyxloom lint                       → clean
```

39 is every `func Test` in a `//go:build e2e` file, less the `TestMain`
dispatchers:

```
consumer 6 · expose 5 · harvest 4 · opctl 6 · publish 13 · systemdx 5
```

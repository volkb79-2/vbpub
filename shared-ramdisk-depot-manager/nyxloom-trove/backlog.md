# srdm backlog

Un-carved ideas. Nothing here is committed to.

**Where a gap goes when a package ends.** Every package LOG closes with a
"Gaps" section, and that section is the narrative — it is not the tracking.
Each gap it names must also land in exactly one of three places, or it exists
only in a file nobody re-reads:

- the **roadmap**, when a named later package already owns it (say so in that
  package's entry, so whoever carves it inherits the obligation);
- **here**, when it is real work that no package owns yet;
- a **`D-<NNN>`**, when it is a product call rather than work.

Retire an entry when it ships; a backlog that still proposes something the
tree already has is worse than an empty one.

---

- **Measure the parked hold worker's own footprint** (P04). The worker is
  charged to the class cgroup alongside the content it holds, so its
  resident size comes straight off the class floor.
  `debug.FreeOSMemory()` returns the copy buffers before it parks, but a Go
  runtime is not nothing. Worth a number before the floors are calibrated
  against a real payload in P09 — and worth knowing whether a smaller
  worker would be worth writing.
- **Exercise the Docker resolver against a real daemon** (P05). The gate
  container has no Docker socket, so `internal/consumer`'s Docker half is
  tested against a stub over a unix socket and every e2e run reports the
  check as degraded. Naming a real container end to end needs a harness with
  the socket mounted — P06 builds the first harness that has containers in
  it at all.
- **A gate harness with containers in it** (P06). The container half of the
  propagation precondition — does the Wings container really bind the volume
  tree `rslave`? — is unit-tested against a stub and injected in e2e, because
  the gate container has no Docker socket and no Wings. The same harness
  would close the P05 entry above, and would let oracle 20's "neither
  failure surfaces as a server start error" be asserted against a server
  that actually starts.
- **A durability oracle.** D-008: process kill proves ordering, not
  durability. `dm-flakey` or a VM snapshot in the privileged harness would
  close it.
- **A YAML front-end for profile documents** (D-005), once the profile
  engine lands and a parser dependency is worth its weight.
- **`srdm store gc`** — retention (D-002), once there is something that
  pins a release.
- **Manifest streaming.** `BuildManifest` holds every entry in memory, and
  `harvest.assemble` holds two maps keyed on every path it copies. Fine for a
  ~2.4 GB game tree; worth revisiting together if a profile ever covers a
  tree with millions of entries.
- **Parallel hashing.** The manifest walk is single-threaded. Measure before
  optimizing: on the case-study node the store lives on the same device the
  io.max caps govern, so more concurrency may buy nothing.
- **Harvest reads the tree twice** (P07). It copies the class trees into a
  transaction and then `store.Promote` hashes the copy — two full passes over
  a multi-gigabyte tree. Hashing the copy rather than the source is the
  correct half and must stay: a release's manifest has to describe what
  actually landed on disk. Hashing WHILE copying would collapse the two and
  keep that property, at the cost of a second manifest builder to keep in
  step with the first. Measure the copy against the hash before deciding it
  is worth two implementations.
- **The containerized SteamCMD acquisition driver** (`internal/source/steam`,
  currently a doc-only stub). Wanted for MVP after all: "drop in the appid
  and it prepares the filesystem". Master plan §SteamCMD driver has the
  shape — unprivileged uid inside the egg's own runner image, `app_info_print`
  for build identity, unconditional `validate`, identity from
  `appmanifest_<appid>.acf`, stage jobs confined to `srdm-stage.slice`, and
  one fsync'd typed result record handed back so the daemon never parses
  foreign data. Note it needs the **build-identity recording** entry below,
  which is the same piece of work for the staged path.
- **No privileged e2e oracle for adoption/quarantine or reconciliation
  repair** (P08b). `AdoptOrQuarantine`, `IsComplete`, `RepairReadOnly` and
  `ClearForRepublish` are gated at the unit level against fakes for a
  mounter, a holder and a mount table. The fact still unmeasured against the
  real kernel: a genuinely killed process's leftover plan, mounts and hold
  unit, told apart from a genuinely finished operation, by real systemd and
  real `/proc/self/mountinfo` rather than by a fake standing in for either —
  the same category of fact D-011/D-013 exist because a fake would have
  guessed wrong about. Wants a `kill -9` of the daemon mid-`Publish` in the
  privileged harness, then `AdoptOrQuarantine` against what that really
  leaves behind.
- **An unparsable operation plan cannot stop its own hold unit** (P08b). If
  `internal/publish`'s durable operation plan (`OpPlanFile`) is found
  corrupted — practically only disk damage, since srdm alone ever writes it
  — `AdoptOrQuarantine` falls back to sweeping the operation's mounts by path
  and cannot recover the class names needed to `Forget` the hold unit that
  goes with them, so it is left running with nothing mounted underneath it
  (stated in `sweepOpDir`'s own comment and journaled). Closing it needs
  either a second, independent record of which units an operation started
  (defeating some of the plan's own simplicity) or a host-wide sweep of
  `srdm-hold-*.service` units against every op directory that still exists,
  which is closer to a systemd unit-listing capability `internal/hold` does
  not have yet than to a bug fix.
- **`publish.MarkDirtyCapable` is unreachable code** (P10). D-029 replaced
  in-place unsealing with an overlay, and no `rw` exposure this project
  builds anymore marks a generation dirty-capable — the `Marker` interface,
  `expose.WithMarker` and the `cmd/srdm` wiring that called it are all gone.
  Nothing calls `MarkDirtyCapable` now. It is intentionally left in
  `internal/publish/publish.go` rather than deleted: that file was out of
  P10's scope (another package was concurrently working in it), and the
  READ side of `Record.DirtyCapable` stays genuinely load-bearing — a
  record written by an older srdm, or a future non-overlay driver, can
  still carry the flag, and `doctor`'s drift check earns its keep on those.
  Only the writer is dead. Remove `MarkDirtyCapable` (and reconsider whether
  `Marker` should even be a package-level export anymore) once nothing
  plausibly still calls it.
- **Build identity is not recorded** (P07). The master plan's harvest step 4
  is "run the profile's probes; record build identity where discoverable" —
  the probes run, and there is nowhere to record a build identity, because no
  profile has a way to express one. It would want a probe kind that captures
  a value (a version file, a manifest header) rather than asserting a
  predicate, and a field in COMPLETE to hold it. Wanted by the same thing
  that wants it for staged releases, so it is one piece of work for both
  paths, not a harvest rider.

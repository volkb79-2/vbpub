# srdm-P07 — LOG

Package: `harvest` — adopt an in-place-updated generation as a release, and
close the rw ownership model
Roadmap: `../roadmap.md`, Wave 3
Date: 2026-08-03

---

## What was built

`internal/harvest` turns a published generation's live content back into a
first-class release. It is today's manual procedure — ROLE=main updates on
disk, then repopulate — automated, verified and rollback-able, and it is what
makes the game's own updater a legitimate acquisition source (master-plan
decision 12).

Everything from classification down is `store.Promote`, unchanged and shared
with the staged path. That is the design rather than a shortcut: a harvested
release has to be indistinguishable from a staged one, and the cheapest way to
guarantee it is for both to be made by the same code. The oracle compares the
two manifests' content digests and they are equal, which is what "byte for
byte" means when the manifest already covers every entry's type, path, mode,
class and payload.

What harvest adds is the two things staging never has to think about.

**The content is on a tmpfs somebody may still be writing to.** So the guard
is asked before the copy and again after it — and the second ask is not
belt-and-braces. It is the only thing that can see a container that started
while harvest was reading, and a tree read across such a start produces a
release assembled from two different states, which hashes cleanly, verifies
cleanly, and is a build that never existed.

**The content is on N tmpfs mounts, not one tree.** Publication gives every
class its own, so the release tree is ASSEMBLED, and assembly is the one step
with decisions in it.

## Assembly, and the check that is not promotion's

Every path is re-classified before it is copied, and the question is
different from the one `store.Promote` asks. Promotion asks *does any rule
match this path*. Assembly asks *does the rule that matches it still name the
tmpfs it came off*. The first catches new content; the second catches a
**profile that changed under a live generation**, where nothing about the tree
moved, every path still classifies, and harvesting anyway would republish the
content into a different class with a different tmpfs and a different memory
floor, reported by nothing.

That is worth stating precisely because of what it implies about the other
rule. Under host-bind an exposure binds only the declared class paths, so
every write that reaches a class tmpfs lands inside that class and classifies.
**A profile change is the only way a harvest meets an unclassifiable path.**
The rule still holds and still refuses with `*profile.UnclassifiedError` —
the same refusal the staged path produces — but a gate that pretended a
write could produce one would be testing a route that does not exist.

The same reasoning bounds the collision check. Disjoint class prefixes make a
classified path unreachable from two trees — it would trip the class check
first — so what actually reaches the collision test is a **structural** path
that is a directory in one tree and something else in another. Picking a
winner would make the release depend on the order the classes were walked in.

## D-022 — the rw ownership model, measured rather than argued

P06 left `access: rw` permitting writes at the MOUNT level while
publication's seal still refused them at the MODE level: root could write
through, and the game container — the entire reason `rw` exists — could not.
An `AUTO_UPDATE=1` run would report success and change nothing.

`rw` now **unseals** each bound class tree: owner write restored, and the tree
`lchown`ed to a declared `wings.write_owner`. Never re-sealed — that would
restore the modes of a tree whose content is no longer any release's, which
is the appearance of a sealed generation without the property. The two ways
out of a written-through generation are republish (discard the writes, oracle
22) and harvest (keep them), and P07 is what makes the second one exist.

**Undeclared refuses.** srdm does not guess the uid. Wings' own
`system.user.uid` lives a level deeper in the YAML than the single key
`wings.ReadChownWalk` scans for, and srdm has no parser (D-005, D-021); more
to the point, a wrong guess fails exactly as no unsealing at all does, so
there is nothing to be gained by making one. The refusal names where to find
the number.

The oracle performs the write **as uid 65534**, through both modes, and the
two failures it tells apart are the whole decision: `EROFS` would mean rw
bound the read-only side (D-020's measured half), `EACCES` would mean the
tree was never unsealed (this one). Root writing through proves neither —
which is precisely what P06 was able to observe, and why it deferred.

## What the tests found

**Two mode bugs, both invisible until harvest made a published tree
comparable with the release it came from.** `hold.Seal` and `fsx.CopyTree`
both chmod'd through Go's `Perm()`, which is blind to setuid, setgid and
sticky — the same blindness `store.unixMode` was written to avoid, arriving
twice more. Nothing in publication compares modes again (D-014), so a setgid
directory lost its bit at publication and again at every stage, silently, and
the first thing that could ever have noticed was a round trip. Both fixed,
both pinned by canaries.

**A copy cannot be built inside its own sealed directories.** Publication
leaves a class tree 0555, and `CopyEntry` carrying that mode across failed on
the very next entry it wrote into that directory — for anyone who is not
root, which includes the gate. Directories are now created at least
owner-writable; the store's ownership phase sets the release's modes anyway,
so nothing that survives is lost.

**A canary survived, and the oracle was the thing that was wrong.** With a
consumer present for the whole harvest, dropping the *first* guard check
changes nothing observable: the second one refuses identically, discards
identically, and the test could not tell them apart — having first copied the
entire tree it was always going to throw away. The fix was in the oracle, not
the canary: the guard now records whether any transaction content existed at
each ask, so "the refusal costs nothing" is asserted rather than assumed.

**The exposure is itself a hold.** srdm's volume binds propagate into the
Wings container's namespace by design — that is what precondition 1 is for —
so a live exposure holds the generation's superblock and harvest is refused
until it is removed. This is correct and it makes the procedure an order:
stop the server → unexpose → harvest. Harvest reads the generation's own
tmpfs and needs no exposure to do it, so the refusal names that as the
remedy rather than leaving an operator to work it out.

## Decisions filed

- **D-022** — `access: rw` unseals the class tree and hands it to a declared
  owner; never re-sealed. Closes the half of D-020 P06 left open.
- **D-023** — a harvested release carries no per-instance state, because
  publication never carried any. That is the absolute state rule holding, not
  a gap, and it is what oracle 23's "byte for byte" is measured against.

## Gaps

- **`harvest` has no CLI verb.** Like publication, hold and exposure, it is a
  library. → roadmap, P08, which also records the extra input it needs (the
  release id the harvest becomes).
- **Nothing enforces the procedure's order.** A live exposure is a hold, so
  harvest refuses until it is removed; the refusal names the next step, but
  no verb or loop sequences it. → roadmap, P08.
- **Build identity is not recorded.** The master plan's harvest step 4 says
  "record build identity where discoverable" — there is nowhere to record
  one, because no profile can express one. It is the same missing piece for
  staged releases. → backlog.
- **Harvest reads the tree twice** — copy, then hash the copy. Hashing the
  copy is the correct half and must stay. → backlog.
- **Doctor reports drift and nothing acts on it.** Harvest re-hashes from the
  tree and never trusts a `DirtyCapable` record's manifest, which is the
  P06-inherited obligation discharged; the reporting side is still
  unactioned. → roadmap, P08.

## Verification

```
tools/gate.sh <worktree> unit      → gofmt, build, vet, all oracles green
tools/gate.sh <worktree> e2e       → 33 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh                → 58 canaries rejected, 0 survived
tools/gate.sh <worktree> coverage  → 322/380 changed lines (84.7% >= 75.0%)
nyxloom lint                       → clean
```

33 is every `func Test` in a `//go:build e2e` file, less the three `TestMain`
dispatchers:

```
consumer 6 · expose 5 · harvest 4 · publish 13 · systemdx 5
```

P07 added four. P06's LOG records 31 where that count gives 29, so the
project total moved by two less than the difference between the two LOGs
suggests; the number above is the one the command produces.

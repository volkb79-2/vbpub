# Consuming Assay from inside vbpub (estate-internal)

[`CONSUMERS.md`](CONSUMERS.md) is for a genuinely separate repository (dstdns,
or anything outside this monorepo) — it correctly tells that reader to pin an
immutable release and verify its sha256, because that reader has no other
relationship to assay's source than the artifact they downloaded.

A project living *inside* vbpub (cmru, ciu, or any of the mini-projects under
`scripts/`) is not that reader. It shares this repo's git history with
`assay/` itself. Pinning a versioned zipapp per consumer — copying
`tools/assay/assay-<version>.pyz` into each project and bumping it by hand —
is the wrong tool for that relationship: every consumer silently drifts
behind the moment nobody remembers to re-copy it (this was true in practice:
before this doc, `ciu/tools/assay/` and `cmru/tools/assay/` were both pinned
to 2.3.0 while 2.4.2 had already shipped, with two more `assay-v*` tags never
copied anywhere at all).

## The mechanism

No artifact, no pin, no cache:

```toml
argv = ["bash", "-c",
  "cd {worktree} && /opt/tester-venv/bin/pip install -q -e assay && " +
  "cd {worktree}/<project> && /opt/tester-venv/bin/assay run --lane <lane> ..."]
```

- `{worktree}` is already bind-mounted into every `tester-unified` lane —
  `run-gate.py`'s own `resolve_repo_and_worktree` / mountinfo-based host-path
  resolution does this for every project already; nothing new to configure.
- `pip install -e assay` against that mount installs from whatever commit is
  currently checked out in *this* worktree. `assay` has zero third-party
  dependencies (confirmed: `pip show assay` → `Requires:` empty), so this is
  a local, network-free, dependency-resolution-free operation — fast enough
  to run at the start of every single lane invocation with no caching layer.
  Each lane gets its own ephemeral container, so there is no cross-lane
  state to worry about either.
- No zipapp. The zipapp's packaging (`gate/distribution/build_release.py`:
  stripped install metadata, a hand-written `__main__.py` so a verdict's
  exit code isn't swallowed, normalized mtimes for byte-reproducibility,
  single-file portability) exists to solve *external* distribution — no
  shared source tree, no guaranteed pip/network access at the consumer end.
  None of that applies inside a container that already has the live source
  mounted and a working pip; building one here would be solving a problem
  this consumption path doesn't have.

## Why this is not the "ambient image version" failure CONSUMERS.md warns about

CONSUMERS.md's CMRU section already rejects one specific shape: baking a
version of assay into the shared `tester-unified` **image**. That's correctly
rejected — rebuilding that one image for an unrelated reason (an OS package
bump, say) would silently drift *every* consumer's assay version at once,
with no commit attached to the change at all.

Installing from the bind-mounted worktree is a different shape. It is scoped
to *this worktree's own git tree*, not the image. That means:

- Reproducibility is exactly as strong as it is for everything else in this
  repo: given a specific commit, the assay behavior at that commit is fixed.
  It is not "floating forever," it is "tracks HEAD like every other
  in-repo dependency does."
- A different worktree (a different branch, a different checkout) sees
  whatever `assay/` looks like *there* — a bad commit on `main` does not
  retroactively reach into an unrelated feature branch's worktree.

## The tradeoff, stated plainly

Within **one worktree's own linear history**, there is no staged rollout and
no blast-radius containment: the moment a commit lands in `assay/`, every
project's gate lanes that run in that worktree afterward pick it up
immediately — including a regression, if one lands. Per-consumer pinning
existed to prevent exactly that (a consumer could keep running a known-good
pinned copy indefinitely, choosing when to re-pin). This design trades that
protection away, deliberately, in exchange for: zero staleness (nothing to
remember to re-copy), zero per-consumer maintenance, and one less place a
"which version is this actually running" question can go stale and be
believed.

**Why we chose this anyway:** assay and its estate-internal consumers live in
the same repository, under the same review/merge discipline, on the same
commit history — a regression in `assay/` is exactly as reviewable, exactly
as revertible, and exactly as visible in `git log` as a regression in any
consumer's own code would be. Pinning duplicates a promise git already
makes. It is the right tool for a consumer outside that trust boundary
(CONSUMERS.md's audience); it is redundant, staleness-prone overhead for a
consumer inside it.

If a specific project genuinely needs to hold back from a landed `assay/`
change (rare — e.g. bisecting a suspected assay regression), that is a
deliberate, visible, local decision: pin *that one project's* lane to a
specific commit/tag as a documented exception, the same way you would pin
any other in-repo dependency you needed to temporarily hold back — don't
revert to copying zipapps by default.

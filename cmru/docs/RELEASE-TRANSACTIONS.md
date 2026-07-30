# Isolated release transactions

`cmru release` is intentionally safe to start from a busy developer checkout.
It does not build, tag, or publish from that checkout. Instead it takes a
committed snapshot of `origin/main` and performs all release work in a private
`cmru/release/<id>` worktree — one project **at a time**, each project's own
cycle running to completion before the next project starts (see "Transaction
order" below).

## Operator contract

Use the ordinary entry point:

```bash
./cmru.release.sh --project <project>
```

cmru obtains a local release lock and checks two things about the caller's own
checkout before it creates the release worktree:

1. **Local `main` must not be ahead of `origin/main`.** Those committed
   changes are easy to mistake for published release inputs, and the
   fetched-`origin/main` snapshot would silently omit them. A local `main`
   that is *behind* is reported but safe — the fetched remote commit is
   authoritative regardless.
2. **No uncommitted change may touch a project's own path** for any project in
   this run's scope (`--project <name>`, or every orchestrated project
   otherwise) — whether or not that project would otherwise show as "changed".
   The release source is the fetched `origin/main` commit, never the caller's
   working tree, so an uncommitted edit under, say, `ciu/` would be silently
   left out of the release with no other signal. Commit (and push) it first,
   or pass `--allow-uncommitted` to proceed and knowingly leave it out. Skipped
   entirely for `--dry-run`: a preview has no publish step to protect, and
   having local edits you haven't committed yet is exactly when you'd run one.

Both are fail-closed by default; `--allow-uncommitted` only overrides the
second one; there is no override for the first (push your commits instead).

cmru then creates the release worktree and streams the child process output
back to your terminal. It copies the optional `cmru.secret.toml` overlay into
that worktree with mode `0600`; the copy is removed with a successful
worktree and is never staged.

The re-execed child inherits the parent transaction lock; it does not try to
acquire a second lock against its own release.

Failure retains the worktree and prints its path and branch. Inspect and commit
the correction there, then resume it explicitly:

```bash
./cmru.release.sh --resume /path/reported/by/cmru --project <project>
```

Do not copy generated files back into the caller's dirty checkout. A successful
transaction removes the ephemeral branch/worktree.

## Transaction order

Every changed project releases **one after another** — its own prepare, gate,
promote, tag, build, and publish all finish before the next project's cycle
begins. This is what lets a later project resolve an earlier project's
brand-new release within the *same* `cmru release` run (an OCI image project
like `modern-debian-tools-python-debug` picking up a wheel project's
just-published version, for example) instead of always trailing one run behind.

```text
caller checkout
    │  lock + reject uncommitted release-path edits + reject local-only main commits
    │  fetch origin/main → immutable snapshot base
    ▼
cmru/release/<id> worktree, one project at a time (project_order, changed only):
    ┌─────────────────────────────────────────────────────────────────────┐
    │  optional prepare → commit its declared mechanical outputs          │
    │  required tester-unified gate                                      │
    │  fast-forward origin/main to current HEAD (or fail on a concurrent  │
    │    remote update)                                                  │
    │  explicit tag (if versioned) → build → publish/push                │
    │  checkpoint: record this project's HEAD as the last full success   │
    └─────────────────────────────────────────────────────────────────────┘
    → repeat for the next project, or stop and report on failure
    ▼
every changed project's immutable public artifact, each linked to its own
source commit
```

There is no in-place release mode. A local lock prevents two releases on one
clone; each project's fast-forward push protects that slice of the source
integration from a concurrent remote update. Publication for a project cannot
begin until its own promotion has landed.

### Failure and revert

If project *N* fails, cmru stops — it does not attempt project *N+1* onward.
What gets reverted on `origin/main` depends on how far project *N* got:

- **Before its own promote** (its prepare step failed, or its gate failed):
  nothing new was pushed for it, so there is nothing to revert. Every earlier
  project's release stands untouched.
- **After its own promote, but before publish finished** (tag/build/push
  failed): cmru pushes a revert commit on `origin/main` that undoes *only*
  project *N*'s promoted commit — never an earlier project's. This is tracked
  via a small checkpoint file, written after each project's complete success
  and read back by the failure handler instead of the transaction's original
  starting point. The checkpoint is seeded to *this run's own* starting point
  before the loop begins, so a `--resume` (which reuses the same branch/token
  as the attempt it's continuing) never reads a stale checkpoint left over
  from an earlier, different attempt on that same token.
- **The very first project in the run fails before its own promote**: nothing
  in the whole transaction was ever pushed, so this degrades to the classic
  "abort, nothing published" case — there's still nothing to revert, and the
  worktree is retained exactly as before this feature existed.

Note the checkpoint tracks *source-tree commits only* — a project with no
`prepare` step (most wheel projects, e.g. ciu/nyxloom below) commits nothing
of its own, so the checkpoint can still equal the run's starting point even
after that project's tag and published artifact are real and live; those are
never touched by any of this regardless, since tags/GitHub Releases/ghcr
pushes aren't reverted by a source-tree `git revert`. cmru always reports
"attempting automatic revert" and then whether anything actually needed
reverting — it does not try to guess from the checkpoint alone whether an
earlier project in the same run succeeded (tags/artifacts are the source of
truth for that, not the checkpoint).

In every case the worktree/branch is retained for inspection and `--resume`.
Re-running (the default `--abandon all-previous` behavior) is also safe:
`detect_changed_projects` is tag-based, so any project that already fully
released in the failed attempt shows as unchanged on the next run and is
skipped — only the failed project (now reverted) and anything after it in
`project_order` are attempted again.

### Worked example: releasing ciu, nyxloom, and modern-debian-tools-python-debug together

Suppose only these three have real changes this run (`project_order` puts
`ciu` and `nyxloom` — both wheel projects — ahead of the OCI image project
`modern-debian-tools-python-debug`, which resolves their wheels at build time).

**Happy path**

```
[INFO] Release plan: 3/7 project(s) changed — releasing in order: ciu, nyxloom, modern-debian-tools-python-debug
[INFO] Unchanged, skipping: cmru, pwmcp, tls-edge, topos

=== ciu: releasing ===
[INFO] ciu: running required release gate
[INFO] ciu: promoted to origin/main            # origin/main → <sha A> (no prepare step; nothing new to commit)
[INFO] ciu: ciu-v4.8.1 → ciu-v4.9.0 (minor)
[INFO] Tagged: ciu-v4.9.0
[INFO] Pushing tags to origin: ciu-v4.9.0
[INFO] Building + publishing ciu (ciu-v4.9.0)   # wheel built + GitHub Release ciu-v4.9.0 published

=== nyxloom: releasing ===
[INFO] nyxloom: running required release gate
[INFO] nyxloom: promoted to origin/main         # origin/main → <sha B>
[INFO] nyxloom: nyxloom-v0.1.0 → nyxloom-v0.2.0 (minor)
[INFO] Tagged: nyxloom-v0.2.0
[INFO] Building + publishing nyxloom (nyxloom-v0.2.0)   # wheel + 2 OCI images published

=== modern-debian-tools-python-debug: releasing ===
[INFO] modern-debian-tools-python-debug: preparing release inputs   # build-push.py --build:
                                                                     #   resolves ciu-v4.9.0 / nyxloom-v0.2.0
                                                                     #   LIVE — they're already published
[INFO] modern-debian-tools-python-debug: committed prepared release inputs   # package-manifests-versioned/ diff
[INFO] modern-debian-tools-python-debug: running required release gate
[INFO] modern-debian-tools-python-debug: promoted to origin/main   # origin/main → <sha C> (includes the manifest commit)
[INFO] Building + pushing modern-debian-tools-python-debug (oci-image — registry, no tag)   # image pushed to ghcr

[INFO] Released: ciu (ciu-v4.9.0), nyxloom (nyxloom-v0.2.0), modern-debian-tools-python-debug (image)
```

End state: `origin/main` is at `<sha C>`; `ciu-v4.9.0` and `nyxloom-v0.2.0` are
real GitHub Releases with wheels attached; the mdt image on ghcr was built
against those exact wheel versions. The `cmru/release/<id>` branch/worktree
and its origin backup are removed; your local `main` is synced to `<sha C>`.

**Partial failure — before that project's own promote (nothing to revert)**

nyxloom's gate fails. ciu already fully succeeded (checkpoint at `<sha A>`);
nyxloom's gate runs *before* its promote, so nothing new was ever pushed for
it:

```
=== nyxloom: releasing ===
[INFO] nyxloom: running required release gate
[ERROR] ... test failure ...
[ERROR] Release failed after origin/main was already promoted; attempting
        automatic revert of the in-flight project's changes...
[INFO] Nothing to revert on origin/main — the in-flight project never got as
       far as its own promotion.
```

`origin/main` is still exactly at ciu's checkpoint (nyxloom's gate ran
*before* its own promote, so nothing new was ever pushed for it) — there was
nothing to revert. `ciu-v4.9.0` stands. mdt is never attempted. Fix nyxloom's
test, then re-run: ciu shows unchanged (already tagged) and is skipped;
nyxloom and mdt are attempted again.

**Partial failure — after that project's own promote (scoped revert)**

mdt's `prepare` succeeds and promotes its manifest commit to `origin/main`,
but its subsequent `push` (uploading the image to ghcr) fails:

```
=== modern-debian-tools-python-debug: releasing ===
[INFO] modern-debian-tools-python-debug: committed prepared release inputs
[INFO] modern-debian-tools-python-debug: promoted to origin/main   # origin/main → <sha C>
[INFO] Building + pushing modern-debian-tools-python-debug (oci-image — registry, no tag)
[ERROR] ... ghcr push failed ...
[ERROR] Release failed after origin/main was already promoted; attempting
        automatic revert of the in-flight project's changes...
[INFO] origin/main reverted to its last-known-good state.
[ERROR] Release transaction failed; retained .../cmru-release-<id> on branch
        cmru/release/<id> for inspection/resume.
```

`origin/main` is now a *new* revert commit on top of `<sha B>` (nyxloom's
checkpoint) that undoes mdt's manifest commit — `ciu-v4.9.0` and
`nyxloom-v0.2.0` are completely untouched, still published. The worktree
itself still has mdt's un-reverted manifest commit on its own branch (the
revert only added a commit to `origin/main`, it never rewrites history), so
`--resume` can pick up the investigation. Re-running fresh instead: ciu and
nyxloom show unchanged and are skipped; mdt is attempted again from scratch.

**Full abort — the first project fails before promoting**

If ciu's own gate had failed instead (the very first project in the run,
before anything was ever promoted), this degrades to the original all-or-
nothing case: `origin/main` never moved, `promotion_landed` reports false, no
revert is attempted (there is nothing to revert), and the worktree is
retained for inspection exactly as it always was.

## Project author contract

Every releasable project must declare a meaningful `steps.run-tests` command.
It must invoke the project’s real gate in `tester-unified`, not the developer
container. Use `cmru tester-gate --cwd <project> -- <command>` in the command
declaration: it resolves the cockpit bind mount to Docker's host-visible path,
mounts only the isolated release worktree at `/worktree`, and executes without
a shell. cmru refuses to tag or publish a changed project with no such step.

Use `steps.prepare` only for mechanical, deterministic release input changes.
List every tracked output in `release.commit_generated`; cmru rejects an
undeclared write. A prepare step that derives a version writes it to
`<project>/cmru.vars`, and the project declares `version.strategy =
"external:VAR"`. cmru then creates the annotated tag after the prepared source
is gated and integrated. Projects do not create release tags through a build
script or an implicit GitHub Release API side effect.

OCI projects must not push while gathering generated provenance. Build privately
first, commit/promote declared provenance, then run the separate registry push.

**Known sharp edge (build-all-projects-after-another):** the clean-tree guards
above (`release_cmd`'s dirty-tree check, `commit_generated`'s undeclared-write
check) are repo-wide, not scoped to the current project. Since projects now
build sequentially in the same worktree, a build/push step that leaves behind
any stray non-`.gitignore`d file *anywhere* in the repo will be blamed on
whichever project runs next, with a confusing "working tree is dirty" error
pointing at files that project never touched. Not currently triggered — every
existing step's output already lands under an ignored path (`build/`,
`logs/`, etc.) — but keep any new step's outputs ignored or declared, since
nothing currently enforces per-project scoping here.

## vbpub gate adoption

The following release gates are now declared through `cmru tester-gate`:

| Project | Current state | Required follow-up |
|---|---|---|
| ciu | Full pytest coverage floor | `run-ciu-tests.py` |
| cmru | Full unit/contract suite | `pytest tests -q` |
| nyxloom | Full unit/contract suite | `pytest tests -q` |
| MDT | Source-first release-flow and OCI-staging contracts | focused `unittest` modules |
| pwmcp | Resolver and builder contracts | `pytest tests -q` |
| tls-edge | Hermetic standalone render and config validation | `render_standalone.py --check --defaults-only` |

The release engine still rejects any future project with no gate before remote
main, tags, or public artifacts change: a missing gate is not a passing gate.

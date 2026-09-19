# Isolated release transactions

`cmru release` is intentionally safe to start from a busy developer checkout.
It does not build, tag, or publish from that checkout. Instead it takes a
committed snapshot of `origin/main` and performs all release work in a private
`cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<uuid8>` worktree (SPEC S-CLI.5b; flat,
so the branch name and its `.worktrees/` directory name are identical) — one
project **at a time**, each project's own cycle running to completion before
the next project starts (see "Transaction order" below).

## Operator contract

Use the ordinary entry point. It creates/overwrites `cmru.release.log`; do not
wrap it in `tee` yourself:

```bash
cmru release <project>
```

cmru obtains a local release lock and checks two things about the caller's own
checkout before it creates the release worktree:

1. **Local `main` must not be ahead of `origin/main`.** Those committed
   changes are easy to mistake for published release inputs, and the
   fetched-`origin/main` snapshot would silently omit them. A local `main`
   that is *behind* is reported but safe — the fetched remote commit is
   authoritative regardless.
2. **No uncommitted change may touch a project's own path** for any project in
   this run's scope (`<name>`, or every orchestrated project
   otherwise) — whether or not that project would otherwise show as "changed".
   The release source is the fetched `origin/main` commit, never the caller's
   working tree, so an uncommitted edit under, say, `ciu/` would be silently
   left out of the release with no other signal. Commit (and push) it first,
   or pass `--allow-uncommitted` to proceed and knowingly leave it out. Skipped
   entirely for `--dry-run`: a preview has no publish step to protect, and
   having local edits you haven't committed yet is exactly when you'd run one.

Both are fail-closed by default; `--allow-uncommitted` only overrides the
second one; there is no override for the first (push your commits instead).

cmru then creates the release worktree and reports concise orchestration progress
to your terminal. Full project subprocess output is line-flushed into the root
audit log and transaction-local `<project>/logs/cmru/<step>.log` files; use
`--show-run-details` to stream raw child output to the terminal too. It copies the
repository-root `cmru.secret.toml` and each selected project's explicit secret overlay
into that worktree with mode `0600`; they are removed with a successful worktree and are
never staged.

Inside the worktree, before touching any project, cmru also validates the release
*plan* itself against `origin` two ways a purely local `git tag --list` read cannot (SPEC
S12.2a): the local clone's chosen `<prefix>-v*` tag must resolve to the exact same commit
on `origin` under the exact same name (never a local-only tag, and never a same-named
local tag hand-made over an already-published one at a different commit), and `origin`
must not carry a newer matching tag this local clone never fetched (a stale local view
would otherwise derive a version that already exists and fail mid-release). Either always
aborts, naming the offending tag(s)/commit(s) and a remedy. Given a verified tag, cmru
then compares its commit against the snapshot commit (SPEC S12.2b): *equal* is the
ordinary state right after a completed release (nothing has landed anywhere since) and
is reported as an informative skip, never an error; only *ahead* — a tag pushed to a
commit not yet in this snapshot's history, almost always a previous release that tagged
and pushed but failed before promoting `origin/main` — aborts, because folding that into
an ordinary "unchanged" skip would make an empty release look successful.
`--allow-tag-ahead-of-head` downgrades only that "ahead" abort — for the deliberate case
(e.g. re-running before `origin/main` has caught up) — back to a normal skip
(`--allow-tag-at-head` is a deprecated alias). Any plan-time refusal here is a clean,
typed failure: no project's cycle has started yet, so cmru discards the just-created
worktree exactly like a success would, instead of retaining it the way a genuine
mid-release failure is retained for inspection.

At the same point in the plan — for exactly the projects this run will actually
release — cmru also verifies every declared `[[project.tool_dependencies]]` (SPEC
S15): a first-party artifact a project's own tests consume that was vendored, rather
than release-ordered, specifically to avoid a dependency cycle. Internal vbpub
Assay consumption is source-backed and has no S15 declaration; this check applies
to an explicitly declared external/copy artifact. Integrity (do the
vendored bytes match the recorded hash?), authenticity (does that hash match the
PUBLISHED release asset's bytes — never just the filename or version string?), and
freshness (is the pin the highest published version?) are three distinct checks; a
stale or mismatched pin refuses the release the same way a bad tag state does
(`--allow-stale-tool-deps` overrides staleness only — never an integrity or
authenticity failure). A fresh clone with nothing published yet, or an unreachable
network, is its own explicit "unresolved" outcome, never a pass and never a failure.
This check makes zero network calls when nothing in this run declares a tool
dependency, or when nothing changed. See `cmru tool-deps` for the standalone verb.

Every project this plan skips (equal, ahead-and-allowed, or the ordinary "behind" case) prints
its own `[INFO] Unchanged, skipping: <name> (…)` line naming the exact baseline tag and reason —
never a bare list of names (SPEC S12.2e); see the worked example below. This computation runs
exactly once, before `--dry-run` is ever considered, so a preview and a real run report
identical plan/baseline/reason diagnostics — a dry run only adds the `[DRY] Would …` prefix on
what a real run instead performs for real (SPEC S-CLI.5c).

The re-execed child inherits the parent transaction lock; it does not try to
acquire a second lock against its own release.

Failure retains the worktree and prints its path and branch. A **pre-tag** failure
can be inspected, deliberately corrected, re-gated, and resumed there:

```bash
cmru release <project> --resume /path/reported/by/cmru
```

Do not copy generated files back into the caller's dirty checkout. A successful
transaction removes the ephemeral branch/worktree, but retains its project logs
and artifacts by default first: logs move into
`<project>/logs/cmru-release/<immutable-id>/`, and any explicitly declared
artifact directories move into `<project>/artifacts/<immutable-id>/` with a
`release.json` SHA-256 inventory. Pass `--discard-logs-on-release` and/or
`--discard-artifacts-on-release` to opt out of either half. A project that
declares no `project.release.artifact_dirs` simply has nothing to retain and is
skipped for the artifact half, not an error.

### Caller-main cleanup

After a successful release, a plan refusal, or a child failure, cmru attempts the same
caller-main cleanup; the primary release result is not replaced by a cleanup warning. If the
caller is currently on `main`, cmru fetches `origin/main` and checks the caller worktree before
rebasing. Any tracked or untracked change, including ignored files and directories, makes
cleanup return false before either `git rebase` or `git rebase --abort` is run. The dirty files
and local `main` ref stay exactly where they were, and the terminal reports that the caller
checkout is dirty rather than claiming a rebase conflict. The warning tells the operator to
commit or stash all changes (use `git stash -a` when ignored files must be included), then run
`git rebase origin/main` from the clean checkout.

`--allow-uncommitted` applies only to the preflight that keeps caller edits out of the immutable
remote release snapshot. It does not authorize cleanup to rebase, stash, or otherwise consume
those edits. A clean current `main` still fast-forwards or rebases as before; a real rebase
conflict is aborted and reported distinctly, while another clean-rebase failure is reported as
undetermined rather than guessed to be a conflict. When `main` is not checked out, the existing
safe behavior remains: a clean checkout may fast-forward its local `main`, while a diverged
local `main` is left untouched rather than force-moved. A false cleanup result is reported on
the success, plan-refusal, and child-failure paths, so no path claims that the caller was synced.

`cmru build` uses the same fetched snapshot but stops before every release action. A successful
build copies logs into `<project>/logs/<commit-date>_<full-commit>/` and declared artifact
directories into `<project>/artifacts/<commit-date>_<full-commit>/`, writes a `build.json`
SHA-256 inventory marked `publication: forbidden`, then removes its
`cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<uuid8>` worktree. It is local consumption evidence, not a candidate that `publish` may consume. A build
or retention failure keeps that worktree and prints its path; `cmru worktrees` discovers it and
`cmru cleanup --discard-build-worktree <path> --yes` removes it after inspection. Rebuilding the
same commit requires explicit deletion of the existing output record with
`cmru cleanup <name> --delete-build-output <id> --yes`.

> **Current recovery limit:** a post-tag publication failure is not an automatic retry.
> Preserve the worktree, the stable logs, and generated provenance; do not assume a plain
> resume will publish an existing tag. The deliberately scoped follow-up is
> [KI-06](../KNOWN_ISSUES_TODO_BACKLOG.md#ki-06--durable-post-tag-publication-resume--open-scoped-deliberately).

## Transaction order

Every changed project releases **one after another** — its own prepare, gate,
tag, build, publish, and promote all finish before the next project's cycle
begins. This is what lets a later project resolve an earlier project's
brand-new release within the *same* `cmru release` run (an OCI image project
like `modern-debian-tools-python-debug` picking up a wheel project's
just-published version, for example) instead of always trailing one run behind.

```text
caller checkout
    │  lock + reject uncommitted release-path edits + reject local-only main commits
    │  fetch origin/main → immutable snapshot base
    ▼
cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<uuid8> worktree, one project at a time (project_order, changed only):
    ┌─────────────────────────────────────────────────────────────────────┐
    │  optional prepare → generate CHANGES.md → commit declared outputs  │
    │  required tester-unified gate (again if versioning adds a commit)  │
    │  refresh durable candidate branch                                  │
    │  explicit tag (if versioned) → build → publish/push                │
    │  fast-forward origin/main from this exact candidate (or fail closed │
    │    on a concurrent remote update)                                  │
    │  checkpoint: record this project's HEAD as the last full success   │
    └─────────────────────────────────────────────────────────────────────┘
    → repeat for the next project, or stop and report on failure
    ▼
every changed project's immutable public artifact, each linked to its own
source commit
```

There is no in-place release mode. A local lock prevents two releases on one
clone; each project's final fast-forward push integrates the exact source commit
that produced its public artifact. The durable candidate branch is refreshed
before publication and retained if the final promotion fails. Publication for a
project therefore does not silently turn into a different source commit through
an automatic rebase.

### Failure and retained candidate

If project *N* fails, cmru stops — it does not attempt project *N+1* onward.
The failed project's candidate is never promoted: promotion is the final step,
after its tag/build/publish work. `origin/main` therefore remains at the last
fully completed project, while the candidate branch and worktree retain the exact
source SHA, logs, and any generated release output. cmru does not push a source
revert commit and does not rebase the candidate after publication. If an artifact
was already published when a concurrent remote update rejected promotion, the
artifact and candidate branch are retained as an explicit post-publication state
for operator resolution; the release engine does not claim that source history
can undo an external publication.

In every case the worktree/branch is retained for inspection and `--resume`.
Starting a fresh release after an explicit `--abandon all-previous` is also safe:
`detect_changed_projects` is tag-based, so any project that already fully
released in the failed attempt shows as unchanged on the next run and is
skipped — the retained candidate must be handled explicitly before a new
publication attempt.

### Worked example: releasing ciu, nyxloom, and modern-debian-tools-python-debug together

Suppose only these three have real changes this run (`project_order` puts
`ciu` and `nyxloom` — both wheel projects — ahead of the OCI image project
`modern-debian-tools-python-debug`, which resolves their wheels at build time).

**Happy path**

```
[INFO] Release plan: 3/7 project(s) changed — releasing in order: ciu, nyxloom, modern-debian-tools-python-debug
[INFO] Unchanged, skipping: cmru (no commits under cmru/ since cmru-v2.0.0 @ 1a2b3c4d)
[INFO] Unchanged, skipping: pwmcp (no commits under pwmcp/ since pwmcp-v1.4.2 @ 2b3c4d5e)
[INFO] Unchanged, skipping: tls-edge (no commits under tls-edge/ since tls-edge-v0.3.1 @ 3c4d5e6f)
[INFO] Unchanged, skipping: topos (no commits under topos/ since topos-v0.9.0 @ 4d5e6f70)

=== ciu: releasing ===
[INFO] ciu: running required release gate
[INFO] ciu: ciu-v4.8.1 → ciu-v4.9.0 (minor)
[INFO] Tagged: ciu-v4.9.0
[INFO] Pushing tags to origin: ciu-v4.9.0
[INFO] Building + publishing ciu (ciu-v4.9.0)   # wheel built + GitHub Release ciu-v4.9.0 published
[INFO] ciu: promoted release candidate to origin/main  # exact <sha A>

=== nyxloom: releasing ===
[INFO] nyxloom: running required release gate
[INFO] nyxloom: nyxloom-v0.1.0 → nyxloom-v0.2.0 (minor)
[INFO] Tagged: nyxloom-v0.2.0
[INFO] Building + publishing nyxloom (nyxloom-v0.2.0)   # wheel + 2 OCI images published
[INFO] nyxloom: promoted release candidate to origin/main  # exact <sha B>

=== modern-debian-tools-python-debug: releasing ===
[INFO] modern-debian-tools-python-debug: preparing release inputs   # build-push.py --build:
                                                                     #   resolves ciu-v4.9.0 / nyxloom-v0.2.0
                                                                     #   LIVE — they're already published
[INFO] modern-debian-tools-python-debug: committed prepared release inputs   # package-manifests-versioned/ diff
[INFO] modern-debian-tools-python-debug: running required release gate
[INFO] Building + pushing modern-debian-tools-python-debug (oci-image — registry, no tag)   # image pushed to ghcr
[INFO] modern-debian-tools-python-debug: promoted release candidate to origin/main  # exact <sha C>

[INFO] Released: ciu (ciu-v4.9.0), nyxloom (nyxloom-v0.2.0), modern-debian-tools-python-debug (image)
```

End state: `origin/main` is at `<sha C>`; `ciu-v4.9.0` and `nyxloom-v0.2.0` are
real GitHub Releases with wheels attached; the mdt image on ghcr was built
against those exact wheel versions. The `cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<uuid8>`
branch/worktree and its origin candidate branch are removed; your local `main` is synced to `<sha C>`.
That removal is real here because this run actually pushed the backup once at least one
project changed; a run that never pushes one (a dry run, "nothing to release", or a refused
plan) has nothing to remove and prints nothing about it either (SPEC S-CLI.5d).

**Partial failure — before that project's own promotion**

nyxloom's gate fails. ciu already fully succeeded (checkpoint at `<sha A>`);
nyxloom's gate runs *before* its promote, so nothing new was ever pushed for
it:

```
=== nyxloom: releasing ===
[INFO] nyxloom: running required release gate
[ERROR] ... test failure ...
[ERROR] Release candidate was not promoted; origin/main was left at the last
        fully completed project. The durable candidate branch was retained.
```

`origin/main` is still exactly at ciu's checkpoint (nyxloom's gate ran
before its own promotion, so nothing new was integrated for it). There is
nothing to revert. `ciu-v4.9.0` stands. mdt is never attempted. Fix nyxloom's
test, then re-run: ciu shows unchanged (already tagged) and is skipped;
nyxloom and mdt are attempted again.

**Publication succeeds but final promotion loses a race**

mdt's `prepare` succeeds, its image is built and pushed, but the final candidate
promotion loses a concurrent fast-forward race:

```
=== modern-debian-tools-python-debug: releasing ===
[INFO] modern-debian-tools-python-debug: committed prepared release inputs
[INFO] Building + pushing modern-debian-tools-python-debug (oci-image — registry, no tag)
[INFO] ... ghcr push succeeded ...
[ERROR] release candidate was not promoted to origin/main; the candidate branch was retained
[ERROR] Release transaction failed; retained .../cmru-release-20260818_195012-all-a3ae580d on branch
        cmru-release-20260818_195012-all-a3ae580d for inspection/resume.
```

`origin/main` remains at `<sha B>` (nyxloom's completed commit); `ciu-v4.9.0`
and `nyxloom-v0.2.0` are untouched, and the mdt candidate remains on the durable
branch at `<sha C>`. The artifact is intentionally retained for operator
resolution. Re-running fresh: ciu and nyxloom show unchanged and are skipped;
mdt is attempted again from a new snapshot after the retained state is handled.

**Full abort — the first project fails before promotion**

If ciu's own gate had failed instead, `origin/main` never moved and the durable
candidate branch contains the prepared candidate for inspection. No revert is
needed because no failed candidate was promoted.

## Project author contract

Every releasable project must declare a meaningful `steps.run-tests` command.
It must invoke the project’s real gate in `tester-unified`, not the developer
container. Set `CMRU_TESTER_UNIFIED_IMAGE` explicitly in the estate's
`[orchestration.defaults.env]` (or in the project's `[env]` when it owns a
different requirement), then use
`cmru tester-gate --cwd <project> -- <command>` in the command
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

CMRU also generates `CHANGES.md` for every project unless it explicitly configures
`release.changelog = false`. It writes the history after `steps.prepare`, before the
gate, and commits it with the declared mechanical outputs. Do not list the default
history file in `commit_generated`; it is already an allowed generated output. Tagged
releases carry a version heading. No-tag image flows carry a source-revision
heading and resume from the source cursor recorded in the prior generated entry.

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

# Isolated release transactions

`cmru release` is intentionally safe to start from a busy developer checkout.
It does not build, tag, or publish from that checkout. Instead it takes a
committed snapshot of `origin/main` and performs all release work in a private
`cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree (SPEC S-CLI.5b; flat,
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

## Bootstrap when the release engine changes in the same wave

When an earlier tool release needs CMRU behavior that is present on the
merged `origin/main` but not in the currently released CMRU wheel (for
example, folding `[Unreleased]` notes under KI-78), bootstrap from the exact
merged commit before releasing that tool. Build CMRU from the committed
`cmru/` source with the declared wheel-builder workflow, install that wheel as
a non-editable package in the estate venv, and record the source commit and
wheel SHA-256 in the release log. Verify the active `cmru` launcher and its
distribution both resolve from the estate venv before starting releases. This
bootstrap wheel is a local release controller, not a published CMRU release.
An actual `cmru release` now refuses a source-tree or editable controller and
records the wheel and installed-file digests in both the launcher and
transaction logs.

Use the bootstrapped controller for dependent releases such as CGProfile and
Run-Gate. After Run-Gate is published, rebuild `tester-unified` from the exact
published Run-Gate wheel and run the CMRU release gate with that image before
releasing CMRU itself. This keeps the dependency order usable while ensuring
the CMRU release is judged with the public Run-Gate artifact. The gate hashes
the published wheel bytes, checks the installed wheel's recorded source digest,
and verifies the selected image's version and wheel-digest labels before each
lane. It pins the lane to the inspected image ID so a mutable tag cannot select
a different image. Never run the
bootstrap through an editable checkout: uncommitted files and whichever source
tree happens to be mounted are not release evidence.

### Run-Gate compatibility bootstrap before CGProfile

The current root `run-gate.root.toml` declares `image_env`, which is supported
by the Run-Gate source in this wave but is rejected by the previously published
Run-Gate wheel. A CGProfile-first release therefore needs a temporary local
Run-Gate bootstrap after the reviewed source commit has reached
`origin/main` and the CMRU bootstrap above is installed. This bootstrap is not
a publication; it only lets CGProfile's registered Run-Gate lanes use the
configuration declared by the same commit.

From a checkout at the exact reviewed `origin/main` commit:

```bash
git rev-parse HEAD
git rev-parse origin/main
cmru handler wheel-build --cwd run-gate-project
python3 tester-unified/run-gate-wheel-version.py run-gate-project/dist --json
tester-unified/build-image.sh \
  --wheel-dir run-gate-project/dist \
  --tag tester-unified:run-gate-bootstrap
docker image inspect --format '{{.Id}}' tester-unified:run-gate-bootstrap
```

Install the one wheel identified by that output into the estate venv, verify
`run-gate --version`, and set `RUN_GATE_TESTER_UNIFIED_IMAGE` to the inspected
image ID for the CGProfile release. Record the source commit, wheel SHA-256,
and image ID in the release log. The image ID makes Run-Gate's lane selection
independent of later movement of the temporary tag. Release CGProfile with
`cmru release cgroup-profiler --set-version 1.0.0`, then release Run-Gate as its
own project. Run-Gate's own CMRU release gate prepares and tests the exact wheel
it will publish; after publication, use that public wheel and a newly built
tester image for the CMRU release described above.

cmru then creates the release worktree and reports concise orchestration progress
to your terminal. Full project subprocess output is line-flushed into the root
audit log and transaction-local `<project>/logs/cmru/<step>.log` files; use
`--show-run-details` to stream raw child output to the terminal too. No credential
file is ever copied into (or read from) that worktree. The launcher resolves the
publisher credential on the host from your checkout (the invocation environment
token, else the repository-root `cmru.secret.toml` merged with each selected project's
explicit overlay) and hands the resolved root and per-project tokens to the child over
a private inherited pipe: never argv, never a file. A resumed child (`--resume`) gets
its credential the same way. A child that received no handoff fails closed.

The re-execed child reads project configs from the isolated snapshot. A central
orchestration file in the snapshot already names project configs relative to
that checkout; an external central file maps them from the source Git root.
Either way, a selected project config must resolve inside the isolated worktree.

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
The old `--allow-tag-at-head` spelling was removed; there is no alias or compatibility window.
Any plan-time refusal here is a clean,
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
never a bare list of names (SPEC S12.2e); see the worked example below. After a dry run has
performed any selected external-version prepare in its disposable candidate, this computation
runs exactly once before the preview branch, so a preview and a real run report
identical plan/baseline/reason diagnostics — a dry run only adds the `[DRY] Would …` prefix on
what a real run instead performs for real (SPEC S-CLI.5c).

The re-execed child inherits the parent transaction lock; it does not try to
acquire a second lock against its own release.

Failure retains the worktree and prints its path and branch. A **pre-tag** failure
can be inspected and corrected on that branch. A failed **build** after the tag was pushed
(REL-05) rolls the freshly pushed tag back automatically, locally and on `origin`, each
deletion pinned to the exact object cmru pushed, and records an absence proof, so the
candidate is resumable exactly like a pre-tag failure and the message prints the
`--resume` command. If the rollback itself cannot be verified, the tag is kept and the
exact manual commands are printed. A failure once the **publish** (`push`) step has begun
never touches the tag; see [Post-publication recovery](#post-publication-recovery). Commit the fixes there before
resuming; CMRU refuses a dirty candidate so the final tag and artifacts cannot
silently omit the correction. Resume reruns prepare and the required gate against
the corrected branch tip, then ships that candidate commit:

```bash
cmru release <project> --resume /path/reported/by/cmru
```

Do not copy generated files back into the caller's dirty checkout. A successful
transaction removes the ephemeral branch/worktree, but retains its project logs, declared
artifacts, and declared gate evidence by default first: logs move into
`<project>/logs/cmru-release/<immutable-id>/`, explicitly declared artifact directories move
into `<project>/artifacts/<immutable-id>/` with the existing `release.json` SHA-256 inventory,
and `evidence_paths` move into `<project>/evidence/cmru-release/<immutable-id>/` with an
`evidence.json` source-commit/hash inventory. Pass `release --discard logs`,
`--discard artifacts`, and/or `--discard evidence` to opt out of each
half. A project that declares no `project.release.artifact_dirs` or no
`project.release.evidence_paths` simply has nothing to retain for that half and is skipped,
not an error. Evidence declarations name only project-relative files/directories produced by
the release gate; missing, escaping, or symlinked paths fail retention and preserve the
worktree for inspection.

### Caller-main cleanup

After a successful release, a plan refusal, or a child failure, cmru attempts the same
caller-main cleanup; the primary release result is not replaced by a cleanup warning. If the
caller is currently on `main`, cmru fetches `origin/main` and checks the caller worktree before
rebasing. Any tracked or untracked (non-ignored) change makes cleanup return false before
either `git rebase` or `git rebase --abort` is run. Ordinary ignored build output (REL-13) does
not block the sync; an ignored file blocks it only when `origin/main` would create a tracked
file at that exact path, because a checkout would silently overwrite it (the warning then says
so; move the file away or `git stash -a`). The dirty files
and local `main` ref stay exactly where they were, and the terminal reports that the caller
checkout is dirty rather than claiming a rebase conflict. The warning tells the operator to
commit or stash the changes, then run `git rebase origin/main` from the clean checkout.
A failed `git fetch` during this best-effort sync (REL-06) is likewise only a warning with the
same recovery advice: it never turns a completed release into a failure, and on a failed
release the retained-candidate path is printed before the sync is attempted.

`--allow-uncommitted` applies only to the preflight that keeps caller edits out of the immutable
remote release snapshot. It does not authorize cleanup to rebase, stash, or otherwise consume
those edits. A clean current `main` still fast-forwards or rebases as before; a real rebase
conflict is aborted and reported distinctly, while another clean-rebase failure is reported as
undetermined rather than guessed to be a conflict. When `main` is not checked out, the existing
safe behavior remains: a clean checkout may fast-forward its local `main`, while a diverged
local `main` is left untouched rather than force-moved. A false cleanup result is reported on
the success, plan-refusal, and child-failure paths, so no path claims that the caller was synced.

`cmru build` uses the same fetched snapshot but stops before source release actions. A successful
build copies logs into `<project>/logs/<commit-date>_<full-commit>/` and declared artifact
directories into `<project>/artifacts/<commit-date>_<full-commit>/`, writes a `build.json`
SHA-256 inventory, then removes its
`cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree. `cmru publish --build-output ID`
revalidates the record and publishes those exact retained bytes through the declared push step;
it refuses any record with tracked or untracked source-tree changes, including during dry-run. A
project must ignore expected untracked generated build outputs while leaving source paths visible
to Git; ignore rules do not hide modified tracked files.
It does not rebuild, create Release records/Git refs, or promote a source branch. Built-in
publishers require existing versioned and `<prefix>-latest` Release/tag targets, verify that a
stable version tag points at the recorded source commit, and update assets in place. Generated
sidecars and `latest.json` use temporary copies, so the retained record stays valid. A custom
publisher must consume files beneath `CMRU_BUILD_OUTPUT_ROOT` and must not create or move Git
refs. A build or retention failure keeps that
worktree and prints its path; `cmru worktrees` discovers it and
`cmru abandon <absolute-path> --yes` removes it after inspection. Rebuilding the
same commit requires explicit deletion of the existing output record with
`cmru cleanup <name> --delete-build-output <id> --yes`.

> **Current recovery limit:** a post-tag publication failure is not an automatic retry.
> Preserve the worktree, the stable logs, and generated provenance; do not assume a plain
> resume will publish an existing tag (`--resume` refuses it). See
> [Post-publication recovery](#post-publication-recovery). The deliberately scoped follow-up is
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
cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id> worktree, one project at a time (project_order, changed only):
    ┌─────────────────────────────────────────────────────────────────────┐
    │  optional prepare → generate CHANGES.md → commit declared outputs  │
    │  required tester-unified gate (again if versioning adds a commit)  │
    │  refresh durable candidate branch                                  │
    │  explicit tag (if versioned) → build → publish/push                │
    │  push this exact candidate to origin/main; if main advanced, merge  │
    │    origin/main INTO the candidate (bounded, never force/rebase)    │
    │  checkpoint: record this project's HEAD as the last full success   │
    └─────────────────────────────────────────────────────────────────────┘
    → repeat for the next project, or stop and report on failure
    ▼
every changed project's immutable public artifact, each linked to its own
source commit
```

There is no in-place release mode. A local lock prevents two releases on one
clone; each project's final push integrates the exact source commit
that produced its public artifact. The durable candidate branch is refreshed
before publication and retained if the final promotion fails. Publication for a
project therefore does not silently turn into a different source commit through
an automatic rebase.

### Promotion when `origin/main` advanced (REL-04)

The gate can take a long time, so `origin/main` may move while it runs. The promotion push
is then rejected as a non-fast-forward. Because the candidate is already built and published,
cmru never rebases it and never force-pushes. Instead it:

1. fetches `origin/main`;
2. checks that the new `origin/main` commits did **not** touch the released project's own
   paths since the merge-base (the gated and published content must be exactly what lands);
3. merges `origin/main` into the candidate with a `--no-ff` merge commit titled
   `Merge origin/main into release candidate <tag>`, and pushes again.

This is retried at most three times, in case main keeps moving. Authentication, hook and
network failures are not a lost race and fail immediately without merging. Promotion stops
with an error (tag and published state kept, candidate retained) when:

- `origin/main` advanced AND changed the released project's own paths: review those changes,
  and merge by hand if they are acceptable;
- the merge conflicts (the merge is aborted, the candidate is left unchanged);
- the project's paths are unknown, or `origin/main` kept advancing for all three attempts.

The error prints the manual recovery: merge `origin/main` into the candidate branch yourself,
then `git push origin HEAD:refs/heads/main` from the retained worktree.

> **Never merge a candidate branch into `main` by hand** (REL-08). The candidate branch
> carries the generated release-inputs commit (marked with a `Cmru-Release-Candidate: <tag>`
> trailer) and any file-strategy version commit; promotion lands those through the push
> described above. Merging the branch separately duplicates generated history and was the root
> cause of a stale generated changelog section (REL-02). Merge `origin/main` into the
> candidate, never the other way round.

### Release history behaviour changes (REL-02, REL-10)

- **A non-empty plain `## [Unreleased]` section is folded into every tagged candidate before
  the gate and tag (KI-78).** For a CMRU GitHub Release-body handler, the text is appended
  verbatim inside the generated dated version section, then the exact tagged section is used
  in the GitHub Release body (`--dry-run` prints that same body). The release tag and body
  therefore describe the same notes. `--no-build`, and an image push without
  `oci-image-release`, still carry the notes in the tagged changelog but publish no GitHub
  Release body (see "Image-only release bodies" below). Failed gates,
  builds, publishes, and promotions leave source `main`'s `[Unreleased]` unchanged. No
  post-publish changelog commit is needed. The project's configured
  changelog path remains excluded from change detection. This replaces the KI-30 refusal;
  a comment-only body is still just an empty section.
- **Regenerating a stale generated section overwrites hand edits inside it.** When project
  commits landed after a generated section's `source-end` cursor (for example on `--resume`
  of a candidate that was prepared earlier), the section is rebuilt in place from the project
  commit range. Anything edited by hand inside that section is lost; keep hand-written prose
  outside `<!-- cmru: generated -->` sections. A current section stays byte-identical. cmru's
  own `file:` version-bump commit (`chore: bump <prefix> to <version>`) is not project work
  and never makes a section stale, so a resumed `file:` release re-tags the same commit.
  **Hand-curating a pending generated section** (text inside it, such as a summary block or a
  reworded entry) is only safe if the SAME final commit also advances that section's
  `<!-- cmru: source-end=<sha> -->` marker to the project's current head, so cmru sees the section
  as current and keeps it byte-identical. Otherwise any project commit landing after the old
  `source-end` makes cmru need to regenerate the section from commit subjects. It used to drop
  the edit silently (seen in run-gate 23.10.0, whose release lost hand-curated text that two of
  its tests pin); since KI-68 it **refuses** instead (exit 4, before any gate or tag), listing
  up to 20 lines the generator would not produce, with no override flag. Curated release
  notes (KI-75) live in that section, so advancing `source-end` in the same commit is how they
  survive. Text that is not release notes can still be curated AFTER the release (a
  post-release commit, keeping the markers intact).

### Image-only release bodies (`oci-image-release`, KI-78)

For a project whose artifact is an OCI image pushed by its own `push` commands, the
GitHub Release body is published by `cmru handler oci-image-release --prefix <prefix>
--image <registry/repository> --cwd <dir>`, which must be the LAST command of the same
`push` step, after the image push. It runs at the ordinary publication point of the order
above, so its preconditions are facts CMRU has already established by then.

**Checked before any side effect.** In the release child, right after the plan names the
projects to ship and before the tool-dependency check, any `prepare` step, any gate, any
tag or any image push, CMRU validates the configured command of EVERY project it will ship
with one function (`_validated_oci_release_image`); `cmru release --dry-run` calls it too
and refuses identically, leaving the changelog bytes unchanged, and each project's
preparation repeats it before running its `prepare` step. An invalid command is a typed
refusal (exit 4) that marks the plan refused, so no candidate is retained. It refuses a
malformed, duplicate, missing or unexpected `--prefix`/`--image`/`--cwd`; a prefix other
than the project's release prefix without `-v`; an `--image` with a tag or digest; a
`--cwd` that, resolved from the command's declared `cwd` against the project root derived
from the candidate, is not the project root holding the changelog (an absolute path is
refused); any command after it in the push step; an untagged project; a push step whose
`bake_set_*`/`no_cache_env` appends runner arguments; and another body handler in the
step. These checks keep a bad configuration out of a release. They do not make the
publication atomic.

At the handler, the remaining preconditions are:

1. The release tag is already on `origin` at the gated candidate commit (the explicit tag
   step pushed it before build and publish), and the protected `CMRU_RELEASE_TAG`,
   `CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_NOTES_SOURCE` are set for the step. The handler
   derives the image tag from `CMRU_RELEASE_TAG` and reads the changelog section from
   `CMRU_RELEASE_COMMIT`, never the working tree.
2. Before any release mutation it checks that the remote tag exists and equals that
   commit, and that an existing release is for that tag and does not target another commit.
   Any absent, malformed or mismatched value refuses; it never creates, moves or deletes a
   Git ref. A missing Release is created under that tag and commit; an existing one has
   only its name and body rewritten, after it is re-fetched and its exact release ID, tag
   identity and remote tag target are verified again immediately before the PATCH (a change
   there sends nothing and refuses). No asset is listed, deleted or uploaded. After the
   write it reads the release (for an update) and the remote tag back. GitHub offers no
   conditional update and cannot make the tag check atomic with a create, so a change seen
   only in that read-back is reported as `was written, but ...`: the release may stay in
   place with the new body, nothing is rolled back, and it must be inspected before any
   retry.
3. It is publication: once the `push` step has begun, a failure follows
   [Post-publication recovery](#post-publication-recovery) and never rolls the tag back.
   The handler itself is idempotent: running it again for the same tag and commit only
   rewrites the body of the same release and leaves every asset alone.

The step cannot also hold `wheel-publish`, `tarball-publish` or a second
`oci-image-release` (the later body would overwrite the earlier one); that is refused by
the same up-front validation, before any side effect. `--no-build` runs no `push` step, so
it creates no body (its configuration is still validated).

### Post-publication recovery

Once the publish (`push`) step has begun, cmru does not roll the tag back: public artifacts
may already exist. The failure message prints the tag, its object id and these commands, to
run from the source checkout:

```sh
git push --force-with-lease=refs/tags/<tag>:<object-id> origin :refs/tags/<tag>
git tag -d <tag>
cmru abandon <candidate-branch> --yes
```

then start a fresh release. Assets already published under the tag are removed with
`cmru cleanup`. `--resume` refuses a candidate whose release tag survived.

### Failure and retained candidate

If project *N* fails, cmru stops — it does not attempt project *N+1* onward.
The failed project's candidate is never promoted: promotion is the final step,
after its tag/build/publish work. `origin/main` therefore remains at the last
fully completed project, while the candidate branch and worktree retain the exact
source SHA, logs, and any generated release output. cmru does not push a source
revert commit and does not rebase the candidate after publication. If an artifact
was already published when promotion could not be completed (see the merge-promote stop
conditions above), the
artifact and candidate branch are retained as an explicit post-publication state
for operator resolution; the release engine does not claim that source history
can undo an external publication.

In every case the worktree/branch is retained for inspection. `--resume` works only when
no release tag was left behind (a pre-tag failure, or a build failure whose tag was rolled
back); after publication began, abandon and re-release.
Use the separate local transaction command after reviewing its exact plan:

```sh
cmru abandon --dry-run
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --dry-run
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --yes
```

The branch argument is an exact managed branch name. Without it, CMRU displays
the complete retained release set and `--yes` confirms exactly that set. The
command refuses any transaction with publication/promotion evidence or
uncertain metadata and never removes a public Release, tag, or GHCR version.
It removes the candidate worktree and its in-worktree logs/artifacts as well as
the private candidate ref and sidecars. Run `cmru cleanup --dry-run` separately
to inspect configured remote asset pruning; `cleanup` does not remove retained
release worktrees.

### Retiring an origin-only candidate (KI-35)

A candidate branch can outlive its worktree: the transaction succeeded (or was removed with
raw Git) and only the `cmru-release-*` ref on origin and orphan sidecars under the shared Git
directory's `cmru-release-scopes/` remain. Pass its exact name to `cmru abandon`:

```sh
cmru abandon cmru-release-20260917_004929-all-2d088b95 --dry-run
cmru abandon cmru-release-20260917_004929-all-2d088b95 --yes
```

When no local worktree matches, `abandon` looks the branch up on origin and **retires it only
if every commit on it is already on `origin/main`**, so the delete cannot lose source. The
delete is a lease (`--force-with-lease=<ref>:<oid>`) read back from origin, then the
transaction's sidecars are removed. `origin/main`, release tags and published assets are never
touched. A candidate holding commits that `origin/main` lacks is withheld (exit 4) with the
`git log` command to inspect them and the manual `git push origin --delete` that would remove
it. A candidate whose tip is not available locally is reported with the `git fetch` to run.
A local branch of the same name must be removed first. For a retained worktree whose
published or ambiguous state `abandon` refuses, the supported path is: confirm the release
(tag or image) exists, remove the worktree and local branch with `git worktree remove` and
`git branch -D`, then retire the
remaining origin ref with this command.

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
against those exact wheel versions. The `cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>`
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

**Publication succeeds but final promotion cannot complete**

If `origin/main` merely advanced during the gate without touching the project's paths,
promotion merges it into the candidate and succeeds (REL-04). The case below is the one that
stops: the concurrent update touched the project's own paths (or conflicted, or kept moving).
mdt's `prepare` succeeds, its image is built and pushed, but the final candidate
promotion stops:

```
=== modern-debian-tools-python-debug: releasing ===
[INFO] modern-debian-tools-python-debug: committed prepared release inputs
[INFO] Building + pushing modern-debian-tools-python-debug (oci-image — registry, no tag)
[INFO] ... ghcr push succeeded ...
[ERROR] release candidate was not promoted to origin/main; the candidate branch was retained
[ERROR] Release transaction failed; retained .../cmru-release-20260818_195012-all-abc123 on branch
        cmru-release-20260818_195012-all-abc123 for inspection.
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
is gated and integrated. The source-first release transaction creates its tag
explicitly before publication. The retained-build publication path only updates
existing Release/tag targets and refuses a missing target.

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
| cmru | Contract-2 resource observation on every ephemeral gate lane; current 6.2.0 candidate uses the KI-79 provisional R2 deferral | `run-gate gate` for full R2; `run-gate gate-provisional` records an approved deferral only |
| nyxloom | Full unit/contract suite | `pytest tests -q` |
| MDT | Source-first release-flow and OCI-staging contracts | focused `unittest` modules |
| pwmcp | Resolver and builder contracts | `pytest tests -q` |
| tls-edge | Hermetic standalone render and config validation | `render_standalone.py --check --defaults-only` |

The release engine still rejects any future project with no gate before remote
main, tags, or public artifacts change: a missing gate is not a passing gate.

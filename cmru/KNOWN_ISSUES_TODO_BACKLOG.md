# CMRU — Known Issues, TODO & Backlog

> **This is the canonical CMRU issue tracker.** File CMRU bugs and enhancements **here**, in
> the CMRU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CMRU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs** in lockstep.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited below.

---

## Landed

### FIX-01 — Containerized wheel build lost git history in a release worktree — *shipped*
**Status:** landed (code + tests + spec in lockstep). **Root cause of the original
`ciu-v2.0.0`/`cmru-v0.2.0` orphan-tag mystery** (see git history around 2026-07-28/29) —
superseding the earlier "concurrent stale process" theory, which was wrong.
**SPEC:** `S9.3a`.
**Why:** `cmd_wheel_build` (`cmru/src/cmru/handlers.py`), when `CMRU_WHEEL_BUILDER_IMAGE`
is set, bind-mounted only the project's worktree subtree into the builder container. A
release worktree's `.git` is a *file* pointing to an absolute path OUTSIDE that subtree
(`gitdir: <repo_root>/.git/worktrees/<name>`) — reproduced directly:
`git rev-parse --show-toplevel` inside such a container fails with `fatal: not a git
repository ... Stopping at filesystem boundary`. `setuptools_scm` does not error on
this — it silently falls back to `pyproject.toml`'s `fallback_version` (`"2.0.0"` for
ciu, `"0.2.0"` for cmru — exactly the orphan tag versions found earlier), and that wrong,
static version gets baked into the wheel and published as if it were real.
**Fix:** `_wheel_builder_git_mount_args()` additionally bind-mounts the checkout's real
git common directory (`git rev-parse --git-common-dir`, translated to its host path the
same way the existing subtree mount already is) at its own absolute path inside the
container — a no-op for an ordinary non-worktree checkout, where that directory is
already covered by the existing mount. Verified empirically: `git describe --tags`
inside the container went from failing closed-ish (silent `fallback_version`) to
correctly reporting `ciu-v4.9.0-156-gb57a4fc1`.

### FEAT-02 — mdt `load` flow: single-build, digest-verified OCI publish — *shipped*
**Status:** landed (code + tests + spec doc in lockstep). This is a project-owned
MDT release command, not an implicit CMRU OCI profile; see `KI-02`.
**Why:** `RELEASE_IMAGE_FLOW=load` (mdt's default) built the image privately once
(`--load`) to extract the manifest, then built it **again, independently**
(`registry_bake()`, `type=registry`) at push time. Nothing compared the two, so the
manifest committed to `package-manifests-versioned/` documented a different build
than what actually reached GHCR — a silent build-on-push fallback. The correction
is project-owned and establishes the evidence an eventual generic command would need.
**Fix:** `oci_layout_bake()` (`modern-debian-tools-python-debug/scripts/release-bake.sh`)
builds once to a local OCI layout (`type=oci,dest=...`) per bake target.
`extract_manifests()` reads the manifest straight out of that layout via
`regctl image get-file ocidir://<dir>:<tag> <path>` — no daemon load, no second
build. `_push_oci_layouts()` (`build-push.py`) pushes each target's layout directly
via `crane push <dir> <tag>` and asserts the registry-reported digest
(`crane digest`) equals the pre-push local digest (`regctl manifest digest
ocidir://...`); a mismatch fails the release closed instead of publishing silently.
Validated end-to-end against a real local `registry:2` container (not mocked) in
`modern-debian-tools-python-debug/scripts/test_oci_layout_push.py`. The download-level
artifact cache (`stage_tool_artifacts.py`) is unaffected — it already never caches a
`/latest` URL and always re-resolves "latest" live before deciding whether to reuse a
version-pinned download; this change only removes the redundant *second BuildKit
invocation*, it doesn't touch that cache.
**Resolved gap:** `test_oci_layout_push.py` is now wired into mdt's `run-tests` gate
(`cmru.toml`) and runs for real there. `cmru/src/cmru/tester_gate.py`'s
`tester-gate --enable-docker` gives that gate container its own ephemeral, fully
isolated nested Docker daemon (`dind_sidecar()` — a `docker:dind` sidecar,
`--privileged`, polled for readiness via `docker exec <sidecar> docker version`
before use) rather than the HOST's real daemon: the gate container attaches to the
sidecar's network namespace (`--network container:<sidecar>`) and points its Docker
CLI at it (`DOCKER_HOST=tcp://localhost:2375`). Chosen over the simpler host-socket
bind-mount alternative specifically to avoid giving a sandboxed test gate
root-equivalent host access — everything the gate does lives inside the disposable
sidecar and disappears when it stops (`docker stop`, `--rm`). Deliberately opt-in per
project step, not a default: only mdt's `run-tests` step passes it — every other
project's gate is unaffected. `tester-unified`'s image ships
`docker`/`buildx`/`crane`/`regctl`/`jq` (inherited from its
`modern-debian-tools-python-debug-vsc-devcontainer` base image); the sidecar image
(`docker:dind`) supplies `dockerd`/`containerd`/`runc` itself — none of that is in
`tester-unified`'s own image, nor needs to be.

Validated end-to-end, including registry networking (the nested daemon's own bridge
network — `test_oci_layout_push.py`'s `registry:2` fixture is reachable at plain
`127.0.0.1:<published-port>` from the gate container, no gateway-IP fallback needed,
since it shares netns with the sidecar) and `docker buildx create --driver
docker-container` + `--output type=oci` working inside the nested daemon. Also
rebuilt `tester-unified:local` (a stale local cache — built before crane/regctl
landed in the base image — had neither; `docker build -f tester-unified/Dockerfile
-t tester-unified:local .` from repo root picks up the current base), then ran all
30 mdt gate tests for real, in-container, through the sidecar.

### FEAT-01 — Multi-variant `bundle`/`tarball` publish + variant-selecting installer — *shipped*
**Status:** landed (code + tests + spec in lockstep).
**SPEC:** `S-REL.6`, `S1.6`, `S2` (`[[project.<name>.variants]]`), `S5.3` (latest.json
`variants`), `S6.12` (installer `--variant`), `V22`.
**Why:** an artifact that carries version-locked C-extension wheels can't serve two host
interpreters from one archive (e.g. netcup-api-filter's `py39` vs `py311`). One release tag
now publishes one asset per named variant (`<tag>-<variant>.tar.xz` + `.sha256`, and for
`bundle` a per-variant `manifest.json` + `.minisig`), recorded in `latest.json`. The operator
selects one explicitly at install time (`get.py --variant NAME`) — the webhoster has no
interpreter to auto-detect, so there is no silent default; the choice is remembered for
`update`. Zero declared variants ⇒ the single-asset path is unchanged (a separate keystone,
`publish_versioned_variants`, leaves `publish_versioned` byte-for-byte intact).
**Resolution** is by `(tag, variant)`: `find_artifact(..., variant=, suffix=)` narrows a
multi-variant `dist/` to one file, so the old ">1 match" guard no longer fires spuriously.

---


### FEAT-03 — `cmru versions`: multi-source dependency resolution with an age window — *implemented*
**Status:** the core resolver and estate-wide age-window tracking shipped in `cmru-v5.5.0`. Follow-on coverage—configurable shipped/all dependency discovery, selected Python extras, project-local requirements manifests, rolling OCI digest checks, and the nine-project adoption—is in `main` and listed under `[Unreleased]` in `CHANGES.md`. Rerun the CMRU tester-unified coverage lane on the release candidate before release.
**SPEC:** `S-CLI.6`, `S2.7`, `V29`.

`cmru versions init` derives supported targets from Python, npm, and Go manifests;
`resolve` queries registries and writes resolved records plus Python constraints, npm package
and lock state, Go module state, OCI JSON, and optional strict Jinja2 outputs; `check` is a
read-only fresh comparison. Sources are `.pypi`, `.npm`, `.go`, and `.oci`, with a 14-day default
window, single-source or aligned selection, and exact overrides requiring a reason and (when
newer than cutoff) a future expiry. Shared target state is stored centrally; project declarations
and their effective policy/state are stored in each project's `cmru.toml`. Refresh stays explicit
and is never coupled to build, release, gates, or schedules.

Timestamp evidence is recorded in resolved state. Go proxy `.info` time is VCS commit time and
emits a warning. OCI prefers registry `Last-Modified`, with a warned image-created annotation/config
fallback. Missing timestamp evidence and unavailable registry metadata fail closed. `init` reports
manifest syntax/source forms outside its supported registry subset.

## Known Issues


> **KI-12 … KI-16 shipped together** in `merge(cmru): KI-12..KI-17 and S15 tool
> dependencies`, with the new S15 tool-dependency feature. SPEC: `S-CLI.5b/5c/5d`,
> `S12.2a`–`S12.2e`, `S2.6`, `S15`. Mutation campaign green (115 candidates, 115
> killed) at 1623 tests and 100% statement and branch coverage; two adversarial
> reviews are recorded in `docs/reviews/`.
>
> **KI-17 shipped 2026-08-19** in a follow-up that also re-aligned KI-16's transaction naming
> to ciu's flat `<prefix>-<YYYYMMDD_HHMMSS>-<feature>` scheme (exact 1:1 branch/directory, no
> nested paths — see the FOLLOW-UP note under KI-16 and SPEC `S-CLI.5b`/`S2.6a`).
>
> Worth reading before filing the next issue here: six defects were found across
> that work and **two were in the issue text rather than the code** — KI-12(b) as
> originally filed made cmru abort on the ordinary just-released state and advise
> `git tag -d` on a real release tag. Its CORRECTION blockquote below is kept in
> place rather than rewritten, because what the entry got wrong is worth more
> than a tidy record.

### KI-03 — S2's single strict configuration contract was not used by `release` — *shipped*
**Status:** resolved as an intentional breaking configuration change. `cli.load_config()` and
the raw step runner invoke `config.load_forge_config()` before mapping any values; all CMRU
verbs therefore use one grammar. A project owns exactly `cmru.toml`; the estate owns exactly
`cmru.orchestration.toml`. Retired central `[projects]`, `github.username`, `[registry]`,
singular `artifact`, `[project.oci]`, delegated strategy/table, old config filenames,
environment-selected config paths, shell sourcing, and aliases are rejected or removed.
Unknown fields and omitted required release/runner fields exit 2. A committed
`[github].token` and inert `[project.publish]`/`[project.resolve]` tables are rejected too.
The checked-in estate and
the standalone empyrion declaration pass `cmru standards`; tests lock failures down.

### KI-04 — S7 delegated-tool configuration is unreachable and has incompatible shapes — *open*
**Status:** S2/S7 now deliberately reject the unimplemented config surface; no release can
claim it performed an optional tool step. This remains a product-design backlog, not a silent
fallback. A future tool must be an explicit release phase with an artifact/digest input,
published output, prerequisite policy, provenance binding, and end-to-end release oracle.

**High-value candidates, in priority order:**

1. **MDT OCI SBOM + vulnerability policy** (`syft` + `grype`) is the clearest near-term value:
   MDT already has a digest-verified local OCI layout, so an SPDX SBOM can be generated from
   the exact layout and attached/published alongside that digest. It is *not* ready to enable
   until we define severity threshold, allowlist expiry, database freshness, scan output
   retention, and whether a transient scanner/database outage blocks a release. Without those,
   a `required = false` scan would only produce security theatre.
2. **TLS-edge bundle minisign** is high value when its public key is distributed as a trusted
   deployment/enrollment input and the installer requires verification. The contract must bind
   the signature to the release manifest hash, name the key rotation path, and require the
   secret signing key at release time. It is not safe to let a missing key/tool silently omit a
   signature.
3. **OCI cosign** is valuable after identity is decided. Keyless signing wants CI OIDC; the
   current local interactive release workflow has no stable OIDC issuer. Key-based signing
   instead needs protected key storage, passphrase handling, verification policy, and registry
   referrer/digest support. Do not add it merely because `cosign` exists.
4. **git-cliff** has low value here: it duplicates the source-first, gated `CHANGES.md` and
   risks two disagreeing histories. **nfpm** has no present consumer; none of the estate ships
   a deb/rpm contract. Do not adopt either now.

**Recommendation:** design the MDT SBOM phase first, but do not implement it in the release
path until its security policy is a concrete reviewed project contract. Then evaluate TLS-edge
minisign as a separate artifact/installer change; do not bundle both into one generic switch.

### KI-05 — S-CLI.4 legacy configuration support remained in the runtime — *shipped*
**Status:** resolved as part of KI-03. CMRU accepts only a current-directory `cmru.toml` or
an explicit `--config` path to `cmru.toml` / `cmru.orchestration.toml`. Every project migrated
from `cmru.build.toml` to `cmru.toml`; there is no alternate parser, sourceable configuration,
environment config override, or compatibility alias in the release path.

### KI-06 — Durable post-tag publication resume — *open; scoped deliberately*
**Status:** the CLI adoption review closes the pre-tag correction gap. If a prepare step or
required gate fails and CMRU retains the release worktree, an operator may fix the candidate,
commit those fixes on its release branch, and rerun `cmru release --resume <worktree>`. Resume
refuses a dirty candidate; after the fixes are committed, prepare and the required gate run
again against that branch tip. The resulting candidate commit is the one CMRU tags, builds,
publishes, and promotes. The worktree remains the forensic location for logs and generated
provenance; fixes should not be copied into the caller checkout and silently left out.

**Still open:** this does not resume after a tag or publication phase has begun. There is no
durable phase record or remote reconciliation protocol for post-tag retry; do not infer that
`--resume` will republish an existing tag or artifact.

**Why this matters for MDT:** its `prepare` phase can spend substantial time downloading/staging
tools and producing exact OCI layouts before it extracts and commits manifest provenance. A
retry that simply repeats prepare is safe but expensive. A real resume could reuse that work
only after proving that the retained layout digest, prepared source commit, build arguments,
tool downloads, and target list still match the pending publication.

**What a safe implementation requires:** a durable per-project phase record outside the source
tree (`prepared`, `gated`, `promoted`, `tagged`, `built`, `published`, `validated`), exact
source and artifact/digest identities, remote tag/release/registry reconciliation, and explicit
invalidation when an operator edits the worktree. Tagged GitHub assets need idempotent
existence/checksum checks; OCI pushes need local-versus-registry digest checks; a pruned local
layout must force a rebuild rather than invent success. A parent failure/revert also means a
resume has to prove the prepared commit can be promoted again, not merely replay a push.

**Incompatibilities/complexity:** generic source preparation, wheel assets, GitHub uploads,
bundle manifests, and no-tag OCI flows do not share one meaningful “done” bit. Preserving
private image layouts consumes disk and crosses retention/cleanup policy; reusing a worktree
after debugging invalidates previous gate evidence. A simplistic `--resume` would therefore
be more dangerous than a fresh release.

**Recommendation:** retain the current pre-tag correction flow and add a separately designed
`resume-publish` state machine only when MDT’s elapsed prepare time justifies it. Start with
MDT’s OCI layout/digest contract; do not promise a universal resume mechanism first.

### KI-07 — Runner log location conflicted with S3.4 — *shipped*
**Status:** resolved. Every runner step writes a line-flushed project-local
`<project>/logs/cmru/<step>.log`, overwriting by default and inserting `\n---\n` with
`--log-append`. In a transaction that path is inside the retained worktree, so a failed
release or build is self-contained for debugging. A successful release moves it project-side
before the worktree is removed, unless `--discard-logs-on-release` is given (an earlier version
of this entry named a nonexistent `--retain-logs-on-release`; corrected by CLI-D5). A successful normal
`cmru build` instead copies it into its commit-addressed local output record before removing its
worktree; a failed build retains the worktree and prints the exact path. The root wrapper also
creates/overwrites the full `cmru.release.log`; `--show-run-details` restores raw console flow
without duplicating that transcript.

### KI-08 — S4 overstates what the `cmru publish` verb implements — *shipped*
**Status:** SPEC S4 now matches the intentional implementation. `cmru publish` fail-fast
checks the publication credential and runs the declared project `push` step. It does not
guess artifact paths or invent a host operation. CMRU's explicit wheel/tarball command
library implements the GitHub Release + checksum convention; another project-owned publisher
must provide equivalent consumer-verifiable evidence itself.

### KI-09 — S3.2's runner-config example did not match the implemented grammar — *shipped*
**Status:** SPEC S3.2 now documents only the validated grammar: `bake_set_prefix`,
`bake_set_vars`, `no_cache_env`, and argv-valued `env_command`. There is no shell-string
environment loader and no alias for the removed names. `quiet` is mandatory on every step.

### KI-10 — publish retained build output by ID — *shipped*

**Decision:** the operator may publish the exact bytes produced by `cmru build`.
`cmru publish PROJECT --build-output ID` selects the retained build record,
revalidates its manifest and complete artifact inventory, then runs the project's
declared `push` step with protected `CMRU_BUILD_OUTPUT_ROOT`,
`CMRU_BUILD_OUTPUT_ID`, and source identity environment. It does not rebuild.
Built-in wheel and tarball handlers select files from the retained inventory;
a custom publisher must read from `CMRU_BUILD_OUTPUT_ROOT` and must not rebuild
from the caller checkout. The user supplies publication credentials as usual.

This publishes retained artifact bytes without promoting a source branch. The
built-in wheel and tarball handlers require the versioned GitHub Release and
its Git tag to exist already; for a versioned artifact the tag must resolve to
the record's exact source commit. They also require the project's existing
`<prefix>-latest` release and tag, then update its release assets without
deleting or recreating the tag. The command creates no Git refs or GitHub
Release records. `cmru release` remains the source-first tag/build/publish/
promote workflow. A custom publisher must consume the retained record and must
not create or move Git refs or promote a source branch. `release --from-candidate`
and durable post-tag retry remain separate, unimplemented workflows;
`--build-output` does not claim their source promotion or recovery semantics.

Built-in handlers copy selected inventoried files to a temporary publish area
before generating checksum sidecars or `latest.json`; the immutable retained
record remains valid and can be published again. Dry-run validates local
evidence but does not query remote release or tag availability.

The build record must refuse publication if any inventoried path is unsafe,
missing, changed, has a different size/digest, or if the record contains an
unexpected file. It must also refuse publication when `source_tree_changes` is
non-empty; projects should ignore expected untracked build outputs so they are
not mistaken for source edits. Ignore rules do not hide modifications to tracked
files. `--dry-run` validates the selected record, shows its exact
source and artifact digests, and displays the declared push commands without
requiring credentials or executing them. `cmru cleanup --delete-build-output`
continues to remove a retained record explicitly.

**Files:** `src/cmru/transaction.py`, `src/cmru/handlers.py`,
`src/cmru/release.py`, `src/cmru/cli.py`, `src/cmru/runner.py`, tests,
`README.md`, `docs/SPEC.md`, `docs/RELEASE-TRANSACTIONS.md`,
`docs/DESIGN-GUIDE.md`, and `docs/CONSUMERS.md`.

### KI-11 — project commands can invoke a different CMRU than the transaction engine — *shipped*

**Evidence:** an estate checkout can run a source-tree CMRU engine, including
inside its isolated release/build worktree. Portable project contracts often
use an argv beginning `cmru tester-gate`; ambient `PATH` could resolve an older
installed wheel. In the 2026-08-12 incident the source engine was
`3.0.1.dev4+g128a3da5` while `/home/vscode/.local/bin/cmru` was `2.0.1` and did
not expose the current `cmru version` verb.

**Resolution:** before running project commands, CMRU creates a launcher bound
to the exact Python executable and installed/source module root that started
the current transaction. It prepends the launcher directory to project `PATH`,
sets protected `CMRU_BIN`, and reapplies both after project environment setup
and `env_command`. The launcher is resolved and its `version` output compared
to the active runtime identity. Missing or mismatched identity fails before the
project command starts; CMRU does not fall back to ambient `PATH`. The same
binding is used by direct `cmru run-step`.

This keeps portable project contracts independent of the CMRU source checkout
while making nested CMRU calls part of the transaction's runtime boundary.
Behavioral tests cover an ambient fake executable and attempts by step
configuration to replace the protected binding.

### KI-02 — CMRU OCI repack is disabled pending production equivalence — *fail-closed*
**Status:** guarded; do not enable for production releases.
**SPEC:** `S14`.
**Symptom:** the prototype used shared `/tmp/oci-src` and `/tmp/oci-dst` paths, blurred
OCI layout-directory and archive/build-context semantics, and its push branch could
fall back to a second bake rather than proving that the validated repacked artifact
was the object published.
**Guard:** direct CMRU command-library `--repack` invocations fail with exit 2 before
authentication, Docker execution, or scratch mutation. `[project.oci]` was removed because
it did not drive execution. Normal explicit OCI build and push commands are unaffected.
**Enablement gate:** unique scratch lifecycle; explicit OCI tar/layout handling;
governed builder resources and concurrency; structural plus runtime validation; final
registry digest verification; and ideally a single-build flow. See `S14` for the
current command-library boundary.
**Related:** `FEAT-02` validates the single-build + digest-verification mechanism
(`type=oci` layout build → `crane push` the layout directly → `crane digest`/`regctl
manifest digest` equality check) end-to-end against a real registry, for MDT's
project-owned script. The same mechanism is a candidate building block for an eventual
evidence-complete CMRU command; it is not itself that closure.

### KI-01 — GHCR package visibility cannot be set via API (platform limitation) — *worked around*
**Status:** worked around (cmru no longer fails the release); full automation is upstream-blocked.
**SPEC:** `S4.7` (amended MUST → best-effort).
**Symptom:** `cmru release` for an OCI project (mdt, pwmcp) pushed the image fine but then aborted with
`[ERROR] set GHCR package visibility … HTTP 404`, failing the whole release after a successful push.
**Root cause (verified 2026-06-21):** GitHub exposes **no REST or GraphQL API** to change a container
package's visibility. `PATCH …/users/<owner>/packages/container/<name>` **and** `…/user/packages/
container/<name>` both return `404` — the route does not exist (not a permission mask). Classic PATs have
**no `admin:packages`** scope (only `read:`/`write:`/`delete:packages`); **fine-grained PATs cannot use
the Packages API at all** ([github/roadmap#558](https://github.com/github/roadmap/issues/558)). So **no
token of any kind** can do this programmatically.
**Fix shipped:** `cmru/src/cmru/ghcr.py` now raises a typed `PackageVisibilityApiUnsupported`;
`mirror_package_visibility` catches it and logs a **non-fatal `[WARN]`** with the one-time UI remediation,
then returns the current visibility. A successful image push no longer fails the release on visibility.
**Operator action (one-time per package):** *Your packages → `<pkg>` → Package settings → Danger Zone →
Change visibility → Public*. Visibility **persists across all future pushes**, so it is never repeated.
**Re-check upstream:** if fine-grained PATs gain Packages API support (roadmap#558), or GitHub adds a
visibility endpoint, restore fully-automatic sync and re-tighten `S4.7` to MUST.

---

### KI-12 — the release plan depends on UNPUSHED local tags, and a tag on the snapshot commit silently disables a project — *shipped*
**Reported by:** assay's wave-3 release, 2026-08-18. **Priority: high** — the only one of
KI-12…KI-16 that produces a *wrong answer* rather than a confusing message.

**What happened.** `./cmru.release.sh --project assay` reported `Release plan: no changed
projects detected; nothing to release` and listed `assay` under `Unchanged, skipping:` —
immediately after ~3,000 lines landed under `assay/`. Cause: a hand-made annotated tag
`assay-v2.1.0` existed **only in the local repository, never pushed**, and pointed at the
snapshot commit itself. `version._latest_tag_for_prefix()` runs `git tag --list "<prefix>*"`
and takes the highest semver; `detect_changed_projects()` then calls `_git_log(repo_root,
last_tag, *paths)`, gets an empty list because the tag *is* HEAD, and `continue`s.

**Two distinct defects.**

1. **The plan is not a function of the pushed repository state.** `git tag --list` returns
   local-only refs. The baseline that decided this release existed on exactly one machine, so
   another operator running the identical command on the identical commit would have computed a
   different plan. For a tool whose premise (`S-CLI.5`) is that a release is an isolated
   transaction against the *exact remote snapshot*, reading the baseline from local scratch refs
   contradicts the isolation it just established.
   **Fix:** derive the baseline from `git ls-remote --tags origin`, or keep the local read and
   **refuse** when the selected baseline tag is absent from the remote.

2. **A tag at or ahead of the snapshot is degenerate and must not be silent.** If a project's
   newest tag points at the commit being released, nothing can *ever* be released for that
   project until a new commit lands. That is almost always operator error — a stray local tag,
   or a half-finished previous release — and the current behaviour folds it into a skip list
   indistinguishable from "genuinely unchanged".
   **Fix — detect, warn, abort.** Abort is the right default because continuing produces a
   silently empty release:

   ```
   [ERROR] assay: latest tag assay-v2.1.0 points AT the snapshot commit 52534ef7.
           Nothing can be released for this project until a new commit lands.
           This usually means a tag was created by hand (cmru owns tag creation) or a
           previous release half-completed. Inspect:  git tag --list 'assay-v*'
           If hand-made and unpushed:                git tag -d assay-v2.1.0
           Re-run, or pass --allow-tag-ahead-of-head to skip this project deliberately.
   ```

   > **CORRECTION, 2026-08-18, measured against the first implementation — the
   > two-state rule above is WRONG and must not be built as written.** Driving
   > the implemented functions against real repositories with a real bare origin
   > showed that "tag at or ahead of the snapshot" covers a *benign* state and an
   > anomalous one, and that the benign state is the common one:
   >
   > 1. **baseline tag not pushed** → refuse. Defect (1) above already catches
   >    this correctly, and it is the state the assay incident was actually in.
   > 2. **tag pushed AND equal to the snapshot commit** → **benign.** This is the
   >    normal state after *any* successful release. The rule as written aborts
   >    here, telling the operator a tag "was created by hand" and advising
   >    `git tag -d <tag>` — advice which, followed, **deletes a legitimate
   >    pushed release tag**. It must be an informative skip instead:
   >    `Unchanged, skipping: proj (already released as proj-v1.0.0 at the
   >    snapshot commit; nothing new since)`.
   > 3. **tag pushed AND strictly ahead of the snapshot commit** → genuine
   >    anomaly: a tag exists on a commit absent from the snapshot's history,
   >    i.e. a previous release tagged and pushed but failed before promoting
   >    `main`. Abort here, worded for *that* cause, with `--allow-tag-ahead-of-head`
   >    as the override.
   >
   > `git merge-base --is-ancestor` cannot separate 2 from 3; compare the
   > resolved commit objects. Measurement that settled it: with an unpushed
   > hand-made tag at HEAD, `require_pushed_baseline` alone already refuses with
   > the correct message, so as originally specified the tag-at-head abort
   > contributed **only** the false positive.

   The *root cause* was hand-tagging a cmru-managed project. cmru owns `tag` in its own
   pipeline (`snapshot → gate → tag → build → publish`), so a manual tag is indistinguishable
   from a completed release. Detection is the guard; `SPEC.md` should also state the rule
   plainly: **never hand-tag a cmru-managed project.**

### KI-13 — the "unchanged" path hides the comparison baseline it just used — *shipped*
**Reported by:** assay's wave-3 release, 2026-08-18.

**Status:** cmru prints the baseline tag *only when it finds changes* — the case where you do
not need it — and withholds it when it finds none, the only confusing case:

```
changed:    [INFO] assay: assay-v2.0.0 → assay-v2.1.0 (minor)      ← baseline shown
unchanged:  [INFO] Unchanged, skipping: ciu, cmru, assay, topos…   ← baseline hidden
```

Faced with `Unchanged, skipping: assay` right after committing to `assay/`, an operator cannot
distinguish wrong `paths` globs, a misplaced tag, commits that missed the configured paths, or
a genuinely unchanged project. Diagnosing KI-12 required two git commands the output never
suggested. **Fix:** name the baseline and the reason on the unchanged path, e.g.
`Unchanged, skipping: assay (no commits under assay/ since assay-v2.1.0 @ 52534ef7)`. That one
line makes KI-12 self-diagnosing without any of KI-12's deeper changes.

### KI-14 — `--dry-run` surfaces diagnostics a real run withholds — *shipped*
**Reported by:** assay's wave-3 release, 2026-08-18.

**Status:** `--dry-run` prints `[DRY] Would tag: assay-v2.1.0` plus the full per-project plan;
the real run prints the plan and then goes quiet through the phases until `[INFO] Released: …`.
The principle: **a dry run must not be the only way to learn something about a real run.** Both
should emit the same decision-level diagnostics — plan, baseline, derived version, per-project
reason — differing only in the `[DRY] Would …` prefix and the absence of effects. Anything
worth telling an operator *before* acting is worth telling them *while* acting; otherwise
operators run everything twice, doubling the wall-clock cost of a gated release to obtain
information the tool already had. Fixing KI-13 on both paths satisfies most of this.

### KI-15 — cleanup prints `error:` and `failed to push` on a SUCCESSFUL run — *shipped*
**Reported by:** assay's wave-3 release, 2026-08-18.

**Status:** every observed run — including a **successful dry run that exited 0** — ends with:

```
error: unable to delete 'cmru/release/a38b64658b35': remote ref does not exist
error: failed to push some refs to 'https://github.com/volkb79-2/vbpub.git'
```

Cleanup tries to delete the origin backup branch on paths where it was never pushed (dry run,
and nothing-to-release). Two harms: it made a genuine no-op look like a failure while
diagnosing KI-12, and — worse for a release tool — it trains operators to read `error:` and
`failed to push` as background noise. **Fix:** delete the origin backup only when this
transaction actually pushed it, tracked as transaction state rather than attempted
unconditionally. Any remaining best-effort delete must not print at `error:` level.

### KI-16 — release branch/worktree names are opaque, unsortable, and accumulate — *shipped*
**Reported by:** assay's wave-3 release, 2026-08-18.

**Status:** the transaction name is `cmru/release/<12 hex>` from `uuid.uuid4().hex[:12]`
(`transaction.py:129`), with worktrees `.worktrees/cmru-release-<id>-<suffix>`. Ten retained
release worktrees exist in this checkout right now. Nothing in a name says *when* it ran, *what*
it was releasing, or whether it is safe to remove — so triage means opening each one.

**The constraint any rename must preserve.** The uuid is not decoration: `transaction.py`
calls it the "uuid-scoped name this ONE transaction created and exclusively owns", and cleanup
depends on that exclusivity — a transaction may delete only a ref it certainly created. A
scheme that can collide (two runs in the same second for the same scope) risks one transaction
deleting another's branch. Collision-freedom is non-negotiable.

**On the proposed `cmru-release-<YYYYMMDD_HHMMSS>-<project-with-version>`:** the timestamp and
readability are right and should be adopted. Two parts cannot work as literally stated:

* **The version is not known when the branch is created.** Ordering is: create the worktree and
  branch at the remote snapshot → *then* detect changed projects → *then* derive versions
  (visible in the log: `Preparing worktree (new branch …)` precedes `assay: assay-v2.0.0 →
  assay-v2.1.0`). Naming the branch after the plan would invert the transaction's own ordering.
* **A run is not always one project.** `release` with no `--project` iterates every changed
  project on a single branch (`S-CLI.5a`), so `<project>` has no single value in general;
  `--project assay` is the scoped special case.

**Proposed instead**, preserving the `cmru/release/` namespace (code and refspecs glob on
`branch.startswith("cmru/release/")`) and collision-freedom:

```
cmru/release/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>
  e.g.  cmru/release/20260818-195012-assay-a3ae580d
        cmru/release/20260818-195012-all-7f2c1b04
```

`<scope>` is the `--project` value when scoped, `all` otherwise. Chronologically sortable,
readable at a glance, still exclusively owned; the worktree directory should mirror it.
**Pair it with a retention policy** — `cmru gc`, or a documented sweep — since readability aids
triage but does not stop accumulation. A timestamped name makes "remove retained transactions
older than N days" expressible for the first time.

> **FOLLOW-UP, 2026-08-19 — re-aligned to ciu; the branch was flattened.** The shipped
> scheme above kept the *nested* `cmru/release/` ref namespace, so the branch string and its
> worktree directory (`cmru-release-…`, slashes→dashes) were 1:1 *derivable* but not
> *identical*. ciu's next version adopts flat `<prefix>-<YYYYMMDD_HHMMSS>-<feature>` names with
> exact 1:1 branch/directory naming and no nested paths, and cmru now matches it (SPEC
> `S2.6a`'s sibling, `S-CLI.5b`):
>
> ```
> cmru-release-<YYYYMMDD_HHMMSS>-<scope>
>   e.g.  cmru-release-20260819_143022-assay   (branch string == worktree dir string)
> ```
>
> The shared allocator derives a six-character lower-case base-36 identity from the canonical
> physical worktree path and records it with the transaction context; a same-second/same-scope
> allocation receives a numeric suffix, while a short-identity collision refuses with both
> paths. The date/time separator is **`_`** to match ciu exactly. The nested `cmru/release/` and
> `cmru/build/` prefixes are **still recognised** for discovery/resume/cleanup (predicates
> `_is_release_branch`/`_is_build_branch`), so the ~50 worktrees retained under the old naming
> in this checkout are not stranded. The retention-policy point above is still open.

### KI-17 — a gate step copied from `cmru.toml` cannot be reproduced standalone — *shipped*
**Reported by:** cmru's own KI-12…KI-16 work, 2026-08-19.
**Shipped:** 2026-08-19, alongside the KI-16 ciu-alignment rename (SPEC `S2.6a`). Both fixes
recommended below were taken.

**Fix landed (both (a) and (b)).** `tester-gate` now runs an up-front preflight
(`_missing_orchestration_env`) before any resolver with a side effect — the slice-existence
probe or the container launch — that resolves every required value at `explicit flag > env`
precedence and, if anything is missing, aborts **once, naming every missing variable together**
(a): image, memory, memory/swap, CPU, probe image, plus the nested-Docker image under
`--enable-docker`. The aggregate report and each individual resolver message now name
`cmru.orchestration.toml [env]` (inherited through `cmru release`) as the real source, and say
it is NOT usually the project's own `cmru.toml [env]` (b). The required set is one shared
constant, `REQUIRED_TESTER_ENV`, that `cmru standards` imports for its static config check — so
the runtime preflight and the static validator can never drift. `cgroup_parent` is required as
`CMRU_TESTER_CGROUP_PARENT`, normally bound to `$CGROUP_PARENT_DEV_GATES`; there is no ambient
fallback. The workaround formerly
in `docs/CONTRIBUTING.md §3` now records the fix.

**Original report (kept for the record).** The `argv` entries in a project's `[steps.*]` depend on environment injected by the
**orchestration** layer from `cmru.orchestration.toml`'s `[env]` — `CMRU_TESTER_UNIFIED_IMAGE`,
`CMRU_WHEEL_BUILDER_IMAGE`, `CMRU_TESTER_MEMORY`, `CMRU_TESTER_MEMORY_SWAP`, `CMRU_TESTER_CPUS`,
`CMRU_TESTER_CGROUP_PROBE_IMAGE`. Nothing in the step says so.

Copying a step out of `cmru.toml` and running it by hand — which is exactly what an operator
does when a release goes red — fails **one missing variable at a time**, and each cycle costs a
container spin-up. Measured while running cmru's own mutation campaign: two failed attempts,
each surfacing precisely one more missing variable, before the third ran.

The individual messages are clear (`no test image configured — pass --image explicitly or set
CMRU_TESTER_UNIFIED_IMAGE`) but they are wrong about *where* the value normally comes from: it
is not usually set in the project's `cmru.toml [env]` at all, it is inherited from the estate
document above it. So the message sends the reader to the wrong file.

**Fix, either or both:** (a) validate the full required-environment set up front and report
**every** missing variable at once, rather than failing on the first; (b) name the real source
in the message — *"normally supplied by `cmru.orchestration.toml [env]`; run through `cmru
release`, or export it"*. Documented as a workaround in `docs/CONTRIBUTING.md` §3 meanwhile.

### KI-18 — cmru's own mutation gate diffs `origin/main`, finding zero candidates at release time — *shipped*
**Reported by:** cmru's first self-release attempt after the KI-12…KI-17 batch, 2026-08-19.

**What happened.** `cmru release --project cmru` ran the full gate — tests, 100% coverage, and
the Assay R0 lane all passed — then failed the `run-tests` mutation step with `RuntimeError:
the declared source diff produced no mutation candidates`. Nothing was tagged, built, or
published (fail-closed; the transaction worktree was retained).

**Root cause.** `cmru/cmru.toml`'s mutation step ran `tools/mutation_campaign.py --base
origin/main --require-candidates`. A release is an isolated transaction that **snapshots
`origin/main`** (`S-CLI.5`) and requires the change to already be pushed there, so inside the
transaction `git diff origin/main HEAD` contains only the generated `CHANGES.md` — **zero
`src/cmru` candidates** — and `--require-candidates` correctly refuses an empty sample. The
`--base origin/main` value only ever makes sense for a *pre-push CI* run (where `origin/main`
is the prior state); it is degenerate for the release gate, which is the one context that must
pass to publish. Latent since the S15/mutation gate landed in the unreleased KI-12…KI-16 batch;
this was simply the first self-release to exercise it. cmru is the only project with a mutation
gate, so the bug is cmru-local.

**Fix.** The base is now the **previous cmru release tag**, resolved at gate time:
`--base $(git describe --tags --abbrev=0 --match 'cmru-v*' 2>/dev/null || echo origin/main)`.
That returns the nearest ancestor release tag (`cmru-v4.0.1` today), so the campaign mutates
every source line changed **since the last release** — exactly the surface a release must
verify — and always finds candidates for a real change. It is future-proof (each release's
`git describe` picks up the newly-created tag as the next baseline) and falls back to
`origin/main` only when no `cmru-v*` tag exists yet (first-ever/bootstrap release). The
tester-gate container already mounts the checkout's real git common dir (`FIX-01`), so the
release tags are visible to `git describe` in-container. Consequence: the release-gate campaign
now covers the whole since-last-release diff, so it runs longer than a single-change campaign.

**Note on the release engine (KI-11, observed here too).** The installed `cmru 4.0.1` could not
even parse the current config (it predates S15's `[[project.tool_dependencies]]`), so this
release had to run through the source engine. The clean resolution is to bootstrap the current
wheel first — `cmru/build-initial-standalone.sh` builds it without a pre-installed cmru — then
`pip install` it so the installed command matches the source before running `cmru.release.sh`.

### KI-19 — the mutation lane's skip path emits no evidence artifact — *FIXED 2026-09-08*
**Status:** FIXED 2026-09-08 (filed 2026-08-22, adversarial review of the
run-gate adoption wave `vbpub@4c6eb2b6..91959b3a`; finding 4 of the
correctness review). The skip path now writes `.assay/mutation-cmru.json`
with `{"status": "skipped", "reason": "no-changed-source", "base": "<ref>"}`
before exiting 0 (the proposed contract, as-is), verified in isolation
(the real lane could not be exercised without exercising the full mutation
campaign, since this same session's other changes touch `src/`).
**Mechanism.** Since KI-18's fix moved into `cmru/run-gate.toml`
`[lanes.mutation]`, the lane short-circuits when the source diff against the
resolved base tag is empty:

```bash
if git diff --quiet "$BASE"..HEAD -- src; then
    echo "mutation: no changed source since $BASE — nothing to mutate, skipping"
    exit 0
fi
```

On that path the lane exits 0 **without writing
`.assay/mutation-cmru.json`** — the file the `--evidence` flag names as this
lane's recorded output. Pre-adoption, the same situation failed loudly via
`--require-candidates` (ugly, but the failure and its reason existed in a
log). The ancestry argument for the skip itself is sound (`git describe
--match 'cmru-v*'` can only return an ancestor, so an empty diff is a true
"nothing to mutate", never a misresolved base) — the gap is purely the
missing record.

**Reproduction.**

```bash
cd cmru && ./run-gate.py mutation      # on any tree with no src/ delta since the last cmru-v* tag
# → "mutation: no changed source since cmru-v4.1.1 — nothing to mutate, skipping"; exit 0
ls .assay/mutation-cmru.json           # → No such file or directory
```

(First reproducible on a tree at/after cmru-v4.1.1 with no subsequent
`src/cmru` change; before that tag the campaign ran.)

**Why it matters.** Any consumer assembling release evidence from the
declared artifact paths (the release transaction's log archive, an auditor,
a future verdict collector) sees the file MISSING exactly when "we checked
and there was nothing to mutate" is the claim being made. Absence is
indistinguishable from "never ran".

**Proposed contract.** The skip path writes a machine-readable skipped
record at the same declared path before exiting 0 — closed vocabulary, e.g.
`{"status": "skipped", "reason": "no-changed-source", "base": "<tag>"}` — so
absence keeps meaning "never ran" and presence always means "ran or
consciously skipped, with the reason recorded". Alternatively verify no
consumer will ever collect these artifacts and document the absence in the
lane comment; the stub is preferred because the file already advertises
itself as evidence via `--evidence`.

**Oracles.**
- Skip-condition run → `.assay/mutation-cmru.json` exists with the closed
  skip shape naming the resolved base.
- Non-skip run → real campaign payload, unchanged shape.
- Controlled wrong implementation: today's bare `exit 0` fails oracle 1.

### KI-20 — `status`/`release`'s "ahead of origin" check reads the shared local `main` ref, with no way to target a different one — *FIXED 2026-09-08*
**Status:** FIXED (filed 2026-08-25, live-hit operating vbpub's own shared
`.git` from the controller session running the ciu-P30/P31/P32 release).
`status`/`release` accept `--ref REF` (default `HEAD`/`main` respectively,
preserving today's exact behavior when omitted): `release` threads it into
`assert_local_main_not_ahead`'s comparison, `status` threads it into
`detect_changed_projects`' changed-since-last-tag preview via a new
`end_ref` parameter on `_git_log`. The release snapshot commit itself was
already `origin/main` unconditionally (`fetch_origin_main`), never the
local `main` ref, so it needed no change.

**Mechanism.** `cmru status --project ciu` / `cmru release --project ciu`
silently compute their diff-since-last-tag and ahead-of-origin refusal
against the repository's **local branch literally named `main`** —
regardless of which worktree or ref the invoking shell is actually sitting
on. Reproduced directly: with `vbpub`'s shared checkout's local `main`
legitimately 2 commits ahead of `origin/main` (an unrelated, unpushed
`fix/assay-stabilization-wave` commit sequence from a concurrent session,
touching nothing under `ciu/`), `cmru release --project ciu` was invoked
from a **separate, freshly created worktree checked out at a detached HEAD
exactly equal to `origin/main`** (no divergence at all in that worktree) —
and still refused: `[ERROR] Local main is 2 commit(s) ahead of origin/main.`
The check consults the shared repo's `refs/heads/main`, not the worktree's
own HEAD, not a caller-specified ref. `status`'s "no changes since last
release" for a project with real, pushed, unreleased commits under its own
path is the same root cause — the local diff-preview path also reads local
`main` rather than `origin/main`.

**Why it matters.** This estate's actual daily shape is many concurrent
sessions sharing one `.git` across dozens of worktrees (`git worktree list`
in vbpub routinely shows 60+ entries). Any one of them can legitimately
advance the shared local `main` branch pointer for work on a completely
different project, without ever intending to affect a `cmru status`/
`release` run for another project that never touched those commits. Today
there is no way to tell cmru "evaluate against this exact ref" — the only
workarounds are waiting for the other session to push, or moving the
shared `main` ref yourself (itself a shared, blast-radius-y operation
affecting every other worktree of the repo, since `refs/heads/main` is one
object-store-wide ref) just to run a release for a project the divergence
never touched. `ciu worktree add`/`ensure` already has the right shape for
this — `--base REF` (`src/ciu/cli.py`, `default="main", metavar="REF"`)
accepts any git-resolvable ref, not only a local branch name — cmru has no
equivalent.

**Proposed contract.** Add an explicit override — e.g. `--ref REF` on both
`status` and `release` — that cmru consults INSTEAD OF `refs/heads/main`
for the ahead-of-origin refusal, the diff-since-last-tag preview, and the
release snapshot commit. Default stays today's local `main` unchanged (no
behavior change for the common single-session case). Accepting the literal
`HEAD` as a value covers "whatever ref this invocation's cwd/worktree
actually has checked out", which a caller running from an isolated worktree
cannot express today.

**Oracles.**
- Shared checkout with local `main` diverged from `origin/main` by a commit
  that does NOT touch the target project's path, `--ref origin/main`
  passed → `status`/`release` proceed against `origin/main`'s tip, no false
  refusal, no false "no changes".
- Same setup, `--ref` omitted → today's refusal/preview behavior unchanged
  (regression guard on the default).
- `--ref` naming something genuinely behind `origin/main` AND touching the
  target project's path → the refusal still fires, now against the
  caller-named target instead of the implicit local `main`.

### KI-21 — `cmru worktrees` crashes on a retained release worktree whose branch has no `/` — *FIXED 2026-09-08*

**Status:** FIXED (filed 2026-08-31, live-hit operating vbpub's own shared `.git` from the
controller session running the ciu+run-gate backlog wave, while recovering a run-gate-project
release from a build-step failure — see KI-22, filed alongside this from the same incident).
New `transaction.workspace_purpose()` recognizes both naming schemes (flat
`cmru-<purpose>-...`, no `/` at all, and legacy nested `cmru/<purpose>/...`)
without index-splitting; an unrecognized legacy-nested branch still extracts
its middle segment (old behavior, preserved), an unrecognized flat branch
falls back to the raw name instead of raising.

**Mechanism.** `cmru worktrees` and `cmru worktrees --json` both crash:

```
Traceback (most recent call last):
  ...
  File "cmru/cli.py", line 1943, in main
    purpose = workspace.branch.split("/", 2)[1]
              ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^
IndexError: list index out of range
```

A retained release worktree's branch is named
`cmru-release-<timestamp>-<project>-<hash>` — zero `/` characters. `split("/", 2)[1]`
unconditionally assumes at least two segments (a `<kind>/<purpose>` shape) and indexes
element `[1]` with no length check. Reproduced directly: a release of `run-gate-project`
failed at the build/publish step (missing `wheel-builder:local` image, unrelated
environment gap, not a cmru defect) and retained its worktree/branch for inspection per
S-CLI.1; `cmru worktrees` on that same checkout crashed instead of listing it.

**Why it matters.** `worktrees` is the documented way to discover what a `--resume` or
`cmru abandon` should target after exactly this kind of failure — the one command an operator reaches for
immediately after a failed release is the one that crashes, forcing a manual
`ls .worktrees/` + branch-name guess instead (which is how the resume in this incident
actually proceeded).

**Proposed fix.** Guard the split: if `branch.split("/", 2)` has fewer than 2 elements,
report the raw branch name as `purpose` (or a fixed placeholder) rather than indexing
blind. Add a regression fixture: a retained worktree whose branch is a bare
`cmru-release-...-<hash>` name (today's actual release-branch shape) must list without
crashing, in both plain and `--json` output.

### KI-22 — a failed publication could revert `origin/main` while leaving a misleading tag — *resolved 2026-09-19*

**Status:** resolved by `1a7b5941` (`cmru: promote release candidates after publication`).
That release-order change moved promotion after tag, build, and publication. A build or
publication failure therefore leaves `origin/main` at its last completed project and retains
the unpromoted candidate branch; it no longer auto-reverts an already-promoted commit while
leaving a tag behind. The release transaction contract and failure-path tests record this
ordering (`docs/RELEASE-TRANSACTIONS.md`; `tests/test_cli_release_revert_outcomes_adversarial.py`).

The original silent-skip scenario is also guarded by S12.2b: a tag strictly ahead of
`origin/main` is refused by default, and the override is explicit. The candidate can be
inspected and resumed under KI-06. Durable retry after tagging/publication has begun remains
open under KI-06; this resolution does not claim that a failed post-tag publication can
always resume automatically.

### KI-23 — a hand-authored pre-release CHANGES.md draft (`## [X.Y.Z] - UNRELEASED`) is silently duplicated, never folded, by `generate_release_changelog` — *FIXED 2026-09-08 (minimal fix a)*

**Status:** FIXED via proposed fix (a) (filed 2026-09-02, from `vbpub`'s own `ciu` project — 6 recurrences found and hand-fixed this session; no consumer repo involved, this is CMRU misbehaving against its own estate sibling). `generate_release_changelog` now raises the same `RuntimeError` class as the existing dated-collision guard when `CHANGES.md` already has a hand-authored `## [<pending-version>] - UNRELEASED` section, refusing the release until it is folded by hand. Fix (b) (auto-splice + rename the heading) remains undone -- deliberately the smaller, existing-precedent-matching fix, per the entry's own recommendation.

**Mechanism.** `generate_release_changelog` (`src/cmru/changelog.py:215-284`) inserts its
freshly-generated `## [<heading>] - <date>` section immediately after the
`<!-- cmru: release history -->` marker (line 273-281), pushing whatever
content was already there — including a hand-authored pre-release draft
section for the SAME upcoming version — further down in the file, unmerged.
The function DOES have a collision guard for this (lines 250-261): if
`heading` (the bare version string, e.g. `"7.11.0"`) is already present in
`existing_versions`, it locates that section and refuses (`RuntimeError`,
"already has a hand-authored section") unless the section already carries
`_GENERATED_MARKER`. But `existing_versions` is populated by `_HEADING_RE`
(line 30):

```python
_HEADING_RE = re.compile(r"^## \[([^\]]+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE)
```

— which requires an actual `YYYY-MM-DD` date after the bracket. This
estate's own documented convention (`ciu/CHANGES.md`'s process note,
established 2026-08-25, "every package's own detailed prose must be folded
into the SAME version section... not left under a separate `## [Unreleased]`
header") is for an implementer to draft that section as `## [<version>] -
UNRELEASED`, to be renamed + folded at release time. `_HEADING_RE` never
matches that heading shape at all (`UNRELEASED` is not a date), so the
collision guard never fires, `generate_release_changelog` proceeds
obliviously, and the two sections — CMRU's terse auto-generated digest and
the implementer's rich hand-authored prose (including the release's own
"Adoption / Migration Notes", which the process note requires) — end up as
two adjacent, un-merged `## [<version>]` headers, one dated, one not,
forever, unless a human notices and manually folds them.

**This is not a one-off.** Verified directly in `ciu/CHANGES.md`'s own git
history: it recurred on **six consecutive releases** across two different
work sessions weeks apart — `[Unreleased]` (ciu 7.5.0, 2026-08-26), then
`[7.8.0]`, `[7.9.0]`, `[7.10.0]`, `[7.10.1]`, and `[7.11.0]` (2026-08-31
through 2026-09-02) — despite `ciu/CHANGES.md`'s own process note
explicitly documenting the required manual fold-in step since the very
first recurrence. A "standing operational rule" written into a controller's
own memory after the first two instances (ciu 7.7.0/7.7.1, per
`vbpub/nyxloom-trove` release notes) did not stop it recurring four more
times — the rule requires a human/agent to actively remember and check at
release time, and across enough independent sessions, someone always
forgets. All six were found and hand-folded in this session
(`vbpub@dcf9c818`, `ciu` repo).

**Why CMRU, not the consumer project.** `ciu/CHANGES.md`'s own file
enforces the "fold before release" convention in prose only, because the
folding step happens inside CMRU's own release transaction, on CMRU's own
generated output, using CMRU's own collision-detection code — the consumer
project has no hook into that step at all. A prose reminder in a file CMRU
itself writes into is not a mechanism; the tool that owns the insertion
point is the only place a real fix can live.

**Proposed fix.** Either:
(a) **Minimal, matches existing precedent**: extend the collision-check
regex (or add a second one) to also match `## [<heading>] - UNRELEASED`
(and arguably any non-`_GENERATED_MARKER` section under that exact bracket
heading, regardless of suffix) and raise the SAME "already has a
hand-authored section, CMRU refuses to overwrite it" error `generate_release_changelog`
already raises for the dated-collision case — forcing the releaser to fold
manually before the release can proceed, rather than silently duplicating.
(b) **More complete, matches the estate's actual desired behavior**:
when such a section is found, splice the generated digest directly into it
(insert right after its own header, ahead of the hand-authored prose) and
rewrite the header from `- UNRELEASED` to the real `- <date>`, instead of
creating a sibling section — this is exactly the manual step every
releaser has had to perform six times now.

**Oracles.** A project's `CHANGES.md` containing a `## [<next-version>] -
UNRELEASED` section (with real body content, no `_GENERATED_MARKER`) before
`cmru release` runs: today, `generate_release_changelog` returns `True`
having inserted a NEW `## [<next-version>] - <date>` section, leaving the
`UNRELEASED` section body untouched immediately below it, split into two
un-merged headers under one version number. Fix (a) must instead raise the
existing `RuntimeError` (same message class as the dated-collision case),
refusing the release until the human/agent folds the sections themselves —
verify by asserting the exception and that the file is byte-unchanged after
the raise. Fix (b) must instead produce a single `## [<next-version>] -
<date>` section containing BOTH the generated digest and the hand-authored
prose, and zero remaining `- UNRELEASED` headers anywhere in the file —
verify with a real project fixture carrying such a section, asserting the
post-release file has exactly one header for that version.

### KI-24 — `get.py` needs an `enroll` subcommand (install + authorized key + host-key fingerprint) so ciu can enroll a bare host without a token, a callback or a self-hosted backend

**Status:** FIXED 2026-09-08 on `cmru-ki24-get-py-enroll` (filed 2026-09-03 from
the ciu v8 design session; operator direction of the same day, ciu CIU-93
revision 2, `vbpub/ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` §4 and oracles
O2/O3/O6).

**Fix.** `templates/get.py.tmpl`: helper section
`# ─── Host enrollment helpers` at L824 — `_parse_authorized_key` (L849,
EXIT_CONFIG on anything that is not `<type> <base64>[ <comment>]`),
`_ak_split_line` (L895, quote-aware options/key split), `_build_key_line`
(L950), `_find_sshd` (L970), `_enroll_check_prerequisites` (L979, root + sshd +
key parse, EXIT_PREREQ naming `openssh-server`), `_enroll_ensure_user` (L1004,
`useradd --create-home --shell /bin/bash` only when absent; docker-group
existence refused with EXIT_PREREQ *before* any user is created),
`_enroll_install_key` (L1061, 0700/0600 + ownership, appended once, EXIT_CONFIG
on same-key-different-options), `_host_key_fingerprints` (L1125, shells out to
`ssh-keygen -lf`), `_host_addresses` (L1148, `hostname -I`, labelled
UNCONFIRMED); `do_enroll` at L1373; parser at L1540; dispatch at L1572. Tests:
`tests/test_installer.py` L1336-2290 (`TestEnrollCLIShape`,
`TestEnrollKeyParsing`, `TestEnrollAuthorizedKeysLine`, `TestEnrollOrdering`,
`TestEnrollInstallStep`, `TestEnrollReport`, `TestEnrollHostProbes`, and
`TestEnrollAgainstRealSystem` at L2097 — the repo's first container-backed
"run the installer for real and assert on real system state" oracle; its fixture
image is built and torn down by the test file itself and the group SKIPS where
docker or `$CGROUP_PARENT_DEV_GATES` is absent, which is the case inside
the gate's own tester-unified container). `src/cmru/getpy.py` needed no change:
the template is placeholder-substituted, not parsed.

**Contract correction (one deviation, measured).** The clause above spells the
restricted line `from="PATTERN",<type> <base64> <comment>`. That form is
REJECTED by OpenSSH: sshd advances past the options field to the first unquoted
whitespace, so a comma-joined line puts the key type inside the options and the
key is never read. Measured on a real sshd in the fixture container — with
`from="*",<key>` the client got `Permission denied (publickey)`; with
`from="*" <key>` the same key authenticated. The installer therefore writes
`from="PATTERN" <type> <base64> <comment>` (whitespace, not comma), and
`_ak_split_line` still RECOGNISES the comma form on read so a pre-existing
malformed entry is seen as a conflict rather than silently duplicated. Any
consumer quoting KI-24's line — ciu CIU-93 / `SPEC.md` S14.7 included — should
be corrected the same way.

**Mechanism.** `templates/get.py.tmpl` renders a transactional installer with
`install|update|status|rollback` subcommands and a `bootstrap|apply|health|
rollback` adapter seam (`ENTRYPOINT`). ciu's remote-host enrollment (v7
`SPEC.md` S14.7, v8 `SPEC-V8.md` S7.2.4) needs the target's admin to run ONE
command on a bare host that (a) installs the project exactly as `install
--scope system` does, (b) creates or confirms a deploy user, (c) appends a
control-generated SSH public key once to that user's `authorized_keys`, and
(d) prints the host's SSH host-key fingerprints so the control host can pin
`known_host` after the operator confirms them. None of that exists in the
template; the previously discussed alternative — a token-authenticated
self-hosted download backend (dstdns D-097) — was withdrawn by the operator
as infrastructure that carried nothing the operator did not have to confirm
anyway.

**Proposed contract** (one new subcommand, available to every project that
renders `get.py`; nothing else in the template changes):

```
get.py [--manifest-pubkey …] enroll --authorized-key 'KEY' --controller FQDN
       [--user USER] [--name NAME] [--from PATTERN] [--docker] [--no-install] [--scope system]
```

In order, fail-fast, idempotent on re-run: (1) prerequisites BEFORE any
network I/O — Linux, root/sudo, an SSH server (`sshd` on `PATH` or
`/usr/sbin/sshd`; absent → `EXIT_PREREQ` naming `openssh-server`), the key
line parses as `<type> <base64>[ <comment>]` with `type ∈ ssh-ed25519 |
ecdsa-sha2-* | sk-* | ssh-rsa` (else `EXIT_CONFIG`); the installer never
installs system packages. (2) `install --scope system` verbatim (transaction,
manifest verification, `current` switch); `--no-install` skips it. (3)
`--user` (default `ciu`) created with `useradd --create-home --shell
/bin/bash` when absent, untouched when present; `--docker` adds it to the
`docker` group, refused with `EXIT_PREREQ` when the group is absent. (4)
`~USER/.ssh` 0700 and `authorized_keys` 0600 owned by USER; the key line
(with `from="PATTERN",` prefixed when `--from` was given) appended ONCE — an
identical line is reported not duplicated, a same-key-different-options line
is refused with `EXIT_CONFIG`. (5) Print: the SHA256 fingerprint of every
`/etc/ssh/ssh_host_*_key.pub` (`ssh-keygen -lf`), the addresses from
`hostname -I` labelled unconfirmed, the user, the installed version, and the
exact completion command `ciu host enroll <NAME> --ssh-host <address>
--fingerprint SHA256:<ed25519 fingerprint>` (`<NAME>` from `--name`, else a
placeholder). It never generates keys, calls anything back, opens a
listener, edits `sshd_config` or runs the adapter verbs.

**Oracles.** O2: in a fixture container with `openssh-server`, `enroll`
creates the user, appends the key once (a re-run leaves one line and reports
it), sets modes/ownership, and prints a fingerprint equal to `ssh-keygen -lf
/etc/ssh/ssh_host_ed25519_key.pub`. O3: without an SSH server it exits
`EXIT_PREREQ` naming `openssh-server` and performs no network I/O. O6: `cmru
get-py --project ciu` renders a script whose `enroll --help` lists exactly
the flags above, byte-identical to the `ciu/get.py` ciu commits and ships as
a release asset. Controlled wrong implementation: one that appends the key
before the prerequisite checks must fail O3; one that duplicates the line on
re-run must fail O2.

**Cross-references.** ciu CIU-93 (the verb, both lines), `vbpub/ciu/docs/
CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2 (the full design), dstdns D-097/D-358
(origin). The self-hosted *wheel* download backend D-097 wished for stays a
separate, optional item (ciu proposal §4.10 item 27) — not required here.

### KI-25 — `get.py enroll`'s security-critical container oracles (O2/O3) are structurally invisible to the automated gate — *FIXED 2026-09-08*

**Status:** FIXED (filed 2026-09-08 by KI-24's own adversarial reviewer). A
new bare-host `[lanes.enroll]` runs `TestEnrollAgainstRealSystem` directly
(real docker access, no `/opt/tester-venv/` -- that venv exists only inside
the tester-unified image) and is now part of `[lanes.gate]`'s own
conjunction. Verified live: all 8 O2/O3 tests pass for real against live
docker containers, no leftovers.

KI-24 shipped `enroll` — a subcommand that creates a real Linux user,
writes to `authorized_keys`, and sets file permissions. Its behavioral
proof (`tests/test_installer.py`'s `TestEnrollAgainstRealSystem`, O2/O3)
spins up a real container with `openssh-server` and asserts on real
system state. `./run-gate.py gate`'s `coverage` lane runs in
`tester-unified`, which has no docker socket (confirmed by reading
`run-gate.toml`'s `[environments.tester-unified]` — no socket/mount
declared, same RG-43-class gap already fixed elsewhere in the estate for
other projects) — so those tests SKIP in the one place whose PASS is
actually trusted as "the gate said so." KI-24's own merge evidence is a
manual, independently-reproduced run outside the gate, not gate output —
correct for THAT package's own dispatch (Touch list correctly excluded
`run-gate.toml`), but it means this repo's real, standing gate currently
certifies nothing about the most security-sensitive code it ships.

Proposed fix: a new `run-gate.toml` lane (or an environment variant of the
existing `coverage`/`assay` lane) that runs with real docker access —
mirroring whatever pattern the estate's own RG-43 sweep already
established for other docker-launching orchestrator lanes (`bare-host`,
per that sweep's own resolution — see the memory/decision record for the
2026-09-03 debian-install-v2/RG-43 pass across the estate) rather than
`tester-unified`. Scope: get `TestEnrollAgainstRealSystem`'s O2/O3 classes
actually running and asserted-on inside `./run-gate.py gate`'s own
conjunction, not just locally reproducible by a human/agent with direct
docker access.

### KI-26 — `cmru get-py` cannot render ANY project's `get.py` from an installed `cmru` — only from a source checkout

**Status:** SHIPPED 2026-09-24 (code, installed-wheel acceptance, SPEC, and adopter docs).
The template now lives under `src/cmru/templates/`, renders through
`importlib.resources`, and is included as `cmru/templates/*.tmpl` in the wheel.
`./run-gate.py gate` includes `installed-wheel`, which builds CMRU, installs it
into an isolated venv without system packages, runs the installed `cmru get-py`
from outside the source tree, and compiles the emitted installer.

**Historical status:** open (found 2026-09-08 by ciu-P52's adversarial reviewer while
verifying a related claim; independently confirmed by the controller with
a live check against the actual installed `cmru-5.1.0`).

**Historical mechanism.** `src/cmru/getpy.py:23`:
```python
_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "get.py.tmpl"
```
This assumes a SOURCE-CHECKOUT layout (`cmru/src/cmru/getpy.py` →
`parents[2]` → `cmru/`, where `templates/get.py.tmpl` genuinely sits two
directories up). Once installed as a real wheel, `__file__` resolves to
something like `.../site-packages/cmru/getpy.py`, and `parents[2]` lands
outside the package entirely (verified live: `.venv/lib/python3.14/`, a
directory that trivially has no `templates/` of its own) — `cmru get-py`
fails outright from any installed `cmru`, not just with a stale template,
confirmed by a live traceback during ciu-P52's own work (they had to
render via `PYTHONPATH` pointed at the cmru SOURCE checkout as a
workaround, not `cmru get-py` itself).

**Compounding, independent bug**: even fixing the path would not help —
`pyproject.toml:42`'s `[tool.setuptools.package-data]` is
`cmru = ["templates/*.toml"]`, a glob that matches ONLY `.toml` files.
`templates/get.py.tmpl` is never included in the built wheel at all;
`find` over the installed package's `site-packages/cmru/` confirms zero
`.tmpl` files present.

**Consequence:** `cmru get-py --project <any>` — the estate's own
documented installer-generation path (`docs/CONSUMERS.md`, the same
command ciu-P52's own `[project.installer]` work depends on) — has never
actually worked from a `pip install cmru`; it only ever worked by
coincidence for whoever ran it from inside this monorepo's own `cmru/`
source checkout, where `parents[2]` happens to still resolve correctly
AND the template file is present on disk regardless of what the wheel
ships. This is exactly the kind of "masked default" `vbpub/AGENTS.md`
warns about (§ "Defaults and fallbacks are hazards") — the failure is
invisible in every context anyone has actually exercised the command
from, and only surfaces the first time someone runs it against a real
installed release.

**Fix implemented** (per the finding's own suggestion, mirroring
`src/cmru/scaffold.py:18-26`'s existing pattern for a similar problem):
resolve the template via `importlib.resources` (package-relative, works
identically whether running from source or an installed wheel) instead of
a `parents[N]`-from-`__file__` filesystem walk, AND move/duplicate
`get.py.tmpl` into `src/cmru/templates/` (inside the actual package
directory `importlib.resources` can address) rather than the former
repo-root-relative `cmru/templates/`, AND widen `package-data` to include
`templates/*.tmpl` (widening the glob alone, without also fixing
`_TEMPLATE_PATH`'s resolution logic and the file's location relative to
the package, does nothing — both parts of this bug must be fixed
together).

**Cross-references.** ciu CIU-93/ciu-P52 (`vbpub/ciu/nyxloom-trove/
reports/ciu-P52-REPORT.md`), which worked around this by rendering
`ciu/get.py` from the cmru SOURCE checkout via `PYTHONPATH` rather than
`cmru get-py` itself — a one-time workaround, not a fix, and every OTHER
project relying on `cmru get-py` against an installed `cmru` (not this
monorepo's own dev environment) is equally broken today.

### KI-27 — `cmru tool-deps --refresh` re-vendors a declaring project's pin but never touches that project's OWN `run-gate.toml`, which pins the same artifact a second time

**Status:** FIXED 2026-09-16 by the source-backed internal-consumer migration.
The original live hit re-pinning `cmru`'s own assay dependency 5.1.0 -> 5.2.0
showed that `--refresh` deleted the vendored
`tools/assay/assay-5.1.0.pyz` and rewrote `cmru/cmru.toml`'s declaration,
but `cmru/run-gate.toml` — which independently names the exact same
version three times, in `[lanes.assay].assay_command`, `[lanes.assay.
pins.assay]`, and the mutation lane's inline `--assay-zipapp` argv — kept
pointing at the now-deleted file. cmru's own release gate would have
failed the next time it ran, until fixed by hand in this same session).
This is a recurrence, not a new class of bug: the exact same gap was
hit and hand-fixed during the 5.0.0 -> 5.1.0 re-pin (`fix(cmru): run-
gate.toml's own assay pin was missed by tool-deps --refresh`,
`6121eec9`), but no backlog entry was filed at the time, so nothing
stopped it from recurring identically on the very next refresh.

**Historical mechanism:** `tool_deps.py`'s `--refresh` (S15) rewrote exactly one
thing: the declaring project's own `cmru.toml` tool-dep table (version +
sha256 + vendored file path) and re-vendors the artifact. It has no model
of a SEPARATE consumer of that same declaration — `run-gate.toml`'s own
`pins.assay`/`assay_command`/inline zipapp-path fields are a second,
independent place the exact same fact is spelled out, and `--refresh`
never looks at that file at all.

**Historical consequence:** every `cmru tool-deps --refresh assay` against a project
that ALSO runs its release gate via a pinned assay zipapp in its own
`run-gate.toml` (today: `cmru` itself — `ciu` and `nyxloom` pin a zipapp
in `run-gate.toml` too but do not yet declare a `cmru.toml` tool-dep at
all, so `--refresh` cannot even target them, a related but distinct gap)
silently breaks that project's own gate until someone notices and
hand-fixes `run-gate.toml` to match — exactly the "masked default"
pattern `AGENTS.md` warns about: correct in every case anyone has
actually re-run the gate right after a refresh, wrong the moment they
don't.

**Resolution:** internal vbpub consumers no longer declare an Assay
`assay_command`, `pins.assay`, vendored zipapp, or S15 tool edge. Their
`run-gate.toml` lane omits both fields, and run-gate installs Assay from the
selected worktree, recording the runtime version and source commit in the
verdict. The mutation campaign receives the same source tree directly. The
explicit `--refresh` path remains for external/copy consumers, where it is
still the correct operation. This removes the duplicated internal fact rather
than teaching one update command to rewrite multiple independent copies.

The previously proposed alternatives were either (a) teach `--refresh` to also rewrite a
`[lanes.*.pins.assay]` table + the matching `assay_command`/inline
zipapp-path occurrences in the declaring project's own `run-gate.toml`
when one exists (mirroring what it already does for `cmru.toml`), or (b)
make `run-gate.toml`'s assay pin DERIVE from the `cmru.toml` declaration
at gate-run time instead of duplicating the version/path literally, so
there is only one place to update. (a) is the smaller change; (b) removes
the duplication that makes the drift possible in the first place.

### KI-28 — release cleanup attempted to rebase a dirty caller `main` after `--allow-uncommitted`

**Status:** FIXED 2026-09-14 (code, behavioral tests, SPEC, and operational
guidance landed together).

**Incident:** On 2026-09-13, `./cmru.release.sh --allow-uncommitted` correctly
kept caller edits out of the isolated release snapshot. The child then failed
its required gate. During parent cleanup, the caller `main` still had unstaged
changes, so cleanup printed Git's `cannot rebase: You have unstaged changes`,
followed by the misleading `fatal: no rebase in progress`, and the useful child
failure was accompanied by noise that looked like a second release failure.

**Root cause:** `transaction.sync_local_main` fetched `origin/main` and invoked
`git rebase origin/main` whenever the caller was currently on `main`, without
first checking for tracked, untracked, or ignored changes. It then unconditionally
tried `git rebase --abort` for every non-zero rebase result, even when Git had
refused to start a rebase because the worktree was dirty. The parent also ignored
the boolean result on the plan-refusal and child-failure cleanup paths and
described the success-path false result as a conflict without checking the actual
state.

**Shipped behavior:** The boolean `sync_local_main` API is preserved. After the
fetch, a dirty current `main`—including ignored files and directories—returns
false before either rebase command runs; the dirty files and local `main` ref
remain untouched. A private per-call result records whether a clean rebase
established an actual unmerged conflict, or instead failed for an undetermined
other reason, and all three parent cleanup branches report that captured reason.
`--allow-uncommitted` remains only a release preflight override and never imports
caller dirt into the immutable remote snapshot. Clean current-main rebases, clean
non-current fast-forwards, and diverged non-current-main refusal retain their
prior behavior.

**Evidence:** `cmru/tests/test_release_transaction.py` constructs tracked,
ordinary untracked, and ignored dirt on current `main`, advances `origin/main`,
including by making an ignored local path remotely tracked, proves no rebase or
rebase-abort runs, and proves content/ref preservation. It also proves a failed
pre-rebase hook is reported as an undetermined non-conflict. The parent
child-failure and plan-refusal tests prove false cleanup results are reported
without calling them conflicts. The normative contract is `docs/SPEC.md`
S-CLI.5/S-CLI.5a; operator steps are in `docs/RELEASE-TRANSACTIONS.md` under
Caller-main cleanup.

### KI-29 — Release abandonment needs a first-class, dry-run-safe operation

**Status:** SHIPPED 2026-09-24 (code, safety tests, SPEC, and adopter docs).

`cmru abandon [BRANCH] [--dry-run] [--yes]` now handles retained release transactions
as a separate top-level operation. It selects an exact branch or the complete retained
release set, displays scope/worktree/known remote refs, requires confirmation, and fails
closed on publication, promotion, untagged publication, or unclear metadata/origin state.
Dry-run is read-only. Remote candidate deletion is verified before the local worktree and
transaction sidecars are removed; if deletion fails, local evidence remains. The old
`release --abandon` parser option and its "abandon and then proceed with a new release"
behavior have been removed. `cleanup` docs now name the GitHub Release/assets, matching Git
tags, and GHCR version classes covered by configured remote cleanup policy.

**Historical status:** OPEN 2026-09-22.

The former release surface hid a whole cleanup operation behind
`release --abandon`; that switch is removed. The shipped top-level
`cmru abandon` verb has this contract:

* With no branch argument, discover the retained release candidates, show the
  exact branch, worktree, transaction/scope, and any remote refs or release
  assets involved, then ask for explicit interactive confirmation. `--yes`
  may pre-confirm that complete candidate set.
* With a branch-name argument, resolve exactly that managed release branch,
  verify its worktree and transaction metadata, and abandon/clean only that
  candidate. Do not infer a different branch from a prefix or silently widen
  the selection.
* The operation fails closed for a promoted/published transaction or for
  ambiguous/stale metadata. Its help and confirmation state what is removed.
  In-worktree logs and artifacts are removed with the candidate checkout;
  `origin/main` is not changed.
* `--dry-run` is a strict no-mutation mode. Candidate discovery, confirmation
  rendering, branch/worktree inspection, and remote-state inspection may run,
  but no abandon, worktree removal, branch deletion, sidecar deletion, or
  remote cleanup may execute. The regression oracle fails if abandonment,
  worktree/ref removal, sidecar deletion, or remote cleanup runs.
* Clarify `cleanup` in help and consumer documentation: it cleans the remote
  release assets on the configured `origin` (for example the origin release,
  tag, and GHCR assets covered by the selected cleanup policy); it is not a
  synonym for abandoning a retained local release transaction or its worktree.
  The docs must say which remote asset classes are actually in scope rather
  than implying that every origin ref is removed.

Evidence is in `tests/test_cli_abandon.py` and the existing shared-worktree
transaction tests. The former covers exact selection, full-set `--yes`
confirmation, publication refusal, and the dry-run mutation oracle. User-facing
surface and normative details are synced in `README.md`, `docs/DESIGN-GUIDE.md`,
`docs/CONSUMERS.md`, `docs/RELEASE-TRANSACTIONS.md`, and `docs/SPEC.md`.

### KI-30 — release leaves the hand-written `## [Unreleased]` section orphaned

**Status:** FIXED 2026-10-05 (program 2026-10 W0-REL, REL-10). A tagged release now refuses while a plain `## [Unreleased]` has a non-empty body (HTML comments and whitespace do not count), in the KI-23 guard's style (`changelog.py::_unreleased_body`). Tests: `tests/test_changelog.py::test_rel10_tagged_release_refuses_a_nonempty_plain_unreleased_section`, `::test_rel10_cleared_unreleased_section_with_a_comment_is_accepted`. Originally OPEN 2026-09-23; filed from assay Wave C P0.

The assay changelog's hand-written `## [Unreleased]` block was left in place
through releases 6.4.0, 6.5.0 and 7.0.0. Its `assay analyze` entry had already
shipped in 6.4.0, while the B101/B102/B093/B079 entries described features
released in 7.0.0. CMRU's changelog generator inserts a generated section after
`<!-- cmru: release history -->` but does not fold the plain `## [Unreleased]`
heading. KI-23 guards the distinct `## [X.Y.Z] - UNRELEASED` form, so that
guard does not detect this recurrence. This is filed as a question for a
separate design decision: should CMRU fold the canonical plain heading, and if
so under what rules? No CMRU implementation change is part of the assay wave.

### KI-31 — transaction child nests a relative project config path twice

**Status:** FIXED 2026-09-25 by `0e96f960` (`fix(cmru): resolve child worktree project configs correctly`).
The child loader now uses project paths already loaded from the execution worktree rather
than remapping them a second time. The regression oracle
`tests/test_workspace_adversarial_review.py::test_load_config_uses_project_paths_already_loaded_from_child`
proves the project config resolves once from the child worktree. The following report is the
historical reproduction against the earlier code.

Exact commands, full stdout/stderr, exit markers and cleanup results for a
fresh reproduction are in
[`assay-WAVE-C-CMRU-probes-2026-09-23.md`](../assay/nyxloom-trove/reports/assay-WAVE-C-CMRU-probes-2026-09-23.md).
The default relative-config route reproduced the doubled child path again.
Passing the central absolute `--config` directly did not correct it. At the time,
the now-removed `release --abandon` flag also continued into a new release attempt
unless it encountered a preflight failure (KI-29 replaced that behavior with the
standalone `cmru abandon` command); the evidence report records the additional
attempts and the exact root-level path needed to clean the last one.

From `/workspaces/vbpub/.worktrees/assay-wave-c-controller`, the exact command
`cmru release assay --dry-run` created a transaction snapshot at
`/workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_213534-assay-0uror9`
and the child failed with:

```
ValueError: assay: transaction project config is missing from the isolated worktree: /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_213534-assay-0uror9/.worktrees/cmru-release-20260923_213534-assay-0uror9/assay/cmru.toml
```

The project path is prefixed by the transaction worktree twice. A second
attempt passing `--config /workspaces/vbpub/cmru.orchestration.toml` directly
from that worktree also failed with the same doubled-path shape, because the
parent rebuilds the child argv in `_child_release_args`. The prior B101
controller log reports a temporary `CMRU_BIN` wrapper that rewrote the child
`--config` argument to that absolute central path and allowed the v7.0.0 dry
run/release to complete; the wrapper itself is no longer present. The retained
`/tmp/assay-release-wrapper-args.log` records the argv rewrite, but the direct
absolute `--config` retry here did not prove that workaround. Treat the wrapper
as a reported workaround until its exact implementation is recovered and
verified. The failed probe transactions were not promoted and were removed by
targeted CMRU abandonment; no older retained release worktree was touched.

The old reproduction and failed workaround attempts above are retained as
historical evidence; they describe the pre-fix code and do not represent current
behavior.

### KI-32 — controller rollback tag can disagree with its manifest identity — *resolved; obsolete (retired 2026-10-05)*

> `cmru-controller` was retired and deleted on 2026-10-05 (operator decision O5); kept as history only.

**Decision:** keep controller rollback and remove `--to`. Rollback always uses
the first wave's complete release coordinate from the plan: tag, manifest URL,
and SHA-256 digest. A tag-only override could select a different artifact while
keeping the plan's URL and digest. `--generation` remains available to choose a
positive generation number. The canonical grammar and semantic result are in
[`docs/SPEC.md` S-CLI.9](docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

### KI-33 — bundle config silently accepts unknown keys and coerces values — *resolved*

`cmru.bundle.run_bundle()` now rejects unknown keys at the root and in
`[wheel]`, `[archive]`, and `[copy]`. It checks booleans, strings, and arrays
against their declared TOML types rather than coercing values. Invalid
configuration fails before planning or mutating output. Tests cover unknown
keys, malformed values, and valid omitted defaults. The full contract is in
[`docs/SPEC.md` S9.4a](docs/SPEC.md#s9--reproducibility).

### KI-34 — bundle help overstates determinism for non-xztar formats — *superseded*

The `python -m cmru.bundle` CLI was removed because it had no distinct operator
workflow; `cmru.bundle` remains a library. The public bundle contract states
that the normalized deterministic writer is guaranteed for `xztar`, while the
other accepted archive formats use the platform archive writer. There is no
bundle CLI help surface left to overstate this guarantee.

### KI-35 — retained worktrees of published or ambiguous releases have no supported cleanup path — *open (partly fixed in source by W3-PREP, 2026-10-06)*

**W3-PREP status.** Wanted item 1 is now covered for the origin-only case that remains in practice: `cmru abandon BRANCH` retires an
origin-only `cmru-release-*` branch whose commits are all on `origin/main` (lease delete, sidecars removed, tags and assets untouched);
a branch with other commits is withheld with the exact `git log` and manual delete command (exit 4). Still open: a retained LOCAL
worktree of a published/ambiguous transaction (items 2 and 3), and orphan sidecars with no branch (no command; see the sweep plan
`nyxloom-trove/reports/PROGRAM-2026-10-W3-SWEEP-PLAN.md`). At planning time all 16 remaining origin branches were origin-only.

**Reported by:** an estate worktree cleanup, 2026-09-28 (vbpub `main` at `c57cac82`).

**Observed.** `cmru abandon --dry-run`, run from current source, classified the 24 retained
`cmru-release-*` worktrees in the vbpub checkout at cleanup time as follows:
- 5 abandonable candidates, abandoned in that cleanup: `20260925_131953-ciu`,
  `20260925_133944-cmru`, `20260925_140101-cmru`, `20260925_141239-cmru`,
  `20260925_211129-cmru`;
- 19 withheld, the oldest dating from 2026-09-13.

The withheld reasons (current tally of the 19):
- 9: "original snapshot commit is unavailable; promotion state is ambiguous";
- 5: "release scope metadata is missing, malformed, or ambiguous" (`20260925_130716-ciu`,
  `cmru-release-dirty-sync`);
- 3: "release result metadata records a published project" (for example
  `20260919_170629-…`, which published assay-v6.5.0, and `20260925_203056-assay`, which published
  assay-v7.1.0);
- 1: release progress metadata is missing (`20260917_041022-all-891e9fce`);
- 1: the untagged-publisher rule (`modern-debian-tools-python-debug`).

Eight of the withheld worktrees also hold uncommitted generated package manifests. One more
(`20260917_043157-all-d5847937`) has an empty index (4,644 staged deletions while the files remain
on disk). Several branches still exist on `origin`.

**Why it matters.** KI-29 correctly makes `abandon` fail closed on publication or ambiguity. That
leaves no supported operation for a transaction that *succeeded* (published) yet kept its
worktree, or whose metadata can no longer be proven. So these worktrees only accumulate: an
operator must remove them with raw git, bypassing the sidecar and remote-ref bookkeeping that
`abandon` exists to keep consistent. Together with KI-16, this is why a busy checkout collects
dozens of release worktrees.

**Also observed:** the devcontainer's installed `cmru` (`5.4.2.dev365+ge5e9b95c`) predates
KI-29, so `cmru abandon` is unavailable from the installed CLI. There, only the removed
`release --abandon` exists, and it still starts a fresh release after abandoning. The dry run
above used the source tree. No released cmru (latest `cmru-v5.5.0`) contains `cmru abandon`:
KI-29 is shipped in source only, and `CHANGES.md` `[Unreleased]` does not list it.

**Wanted:**
1. A distinct, dry-run-safe operation for **completed** transactions ("retire"): verify that the
   published tag or release matches the transaction's recorded result, then remove the local
   worktree, local branch, sidecars and the origin candidate ref, never the published tag or
   assets.
2. For **ambiguous** transactions: a report that says exactly which fact is missing and the
   manual command that would resolve it, instead of a bare "withheld". This includes the
empty-index worktree (`20260917_043157-all-d5847937`), where a raw-git removal is the most
hazardous.
3. Decide whether a successful release should remove its own worktree at the end, and if a
   worktree is intentionally kept, why the release keeps it.

### KI-36 — CMRU's resolved secret did not authenticate its Git transport — *fixed in source, pending release*

**Reported:** 2026-10-01, during the `cmru release cmru` bootstrap.

**Observed.** CMRU resolved the root `[github].token` for GitHub API publishing, but its own
fetch, candidate-branch push, tag push, promotion, and cleanup still invoked Git without that
credential. An HTTPS remote therefore fell through to an unrelated interactive Git helper even
when `cmru.secret.toml` was present.

**Wanted.** CMRU-owned Git operations should use the repository-root token only for the exact
configured GitHub HTTPS origin, without putting it in a URL, argv, or persistent config. SSH and
other-host remotes must retain their own authentication; project-local publisher overrides must
not become repository-wide Git credentials.

**Resolution.** CMRU now supplies the resolved repository credential through a temporary askpass
helper, refuses prompts for other hosts, and disables local Git hooks for credential-bearing
calls so the token cannot be inherited by a hook. The first release bootstrap caught invalid
quoting in the generated helper; its test now compiles the generated Python before invoking it.
Behavioral coverage is in `tests/test_git_auth.py`; README, DESIGN-GUIDE, CONSUMERS, and SPEC
document the transport boundary. Included in the pending CMRU release.

### KI-37 — Cleanup could widen beyond the confirmed target list — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh review of the unreleased CMRU changes.

**Observed.** `cmru cleanup` previewed remote assets, asked for confirmation, then repeated the
discovery pass before deletion. An asset appearing or becoming age-eligible while the prompt was
open could be deleted without appearing in the confirmed preview.

**Resolution.** Cleanup now captures an action plan before confirmation and applies those exact
release IDs, package version IDs, tags, build records, and worktree identities. Age selection is
computed once. Build-worktree discard rechecks the previewed branch, commit, and managed identity
before removal. Regression coverage is in `tests/test_cleanup_deep_adversarial.py` and
`tests/test_cli_extended_semantics.py`; the confirmation contract is in README, DESIGN-GUIDE,
CONSUMERS, and SPEC. Included in the pending CMRU release.

### KI-38 — `cmru abandon` could not load external multi-project policy — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh review of the unreleased CMRU changes.

**Observed.** Abandonment reloaded only the config discovered from the current directory. When a
retained transaction's scope came from an external orchestration file, valid projects were
treated as unknown and CMRU refused the otherwise safe abandonment.

**Resolution.** `cmru abandon --config PATH` now validates the recorded scope against the same
external policy, while an empty candidate set returns without loading policy. Coverage is in
`tests/test_cli_abandon.py`, and README, DESIGN-GUIDE, CONSUMERS, and SPEC document the option.
Included in the pending CMRU release.

### KI-39 — CMRU's Assay R1 baseline lagged its latest release — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh review of the unreleased CMRU changes.

**Observed.** R1 used `cmru-v5.4.1` while the latest ancestor release tag was `cmru-v5.5.0`, so
the changed-line gate included already-released source. The test checked only that the pin looked
like a CMRU tag and matched the docs.

**Resolution.** The baseline is now `cmru-v5.5.0`; the config test derives the latest prior tag
from Git and requires the pin to match. DESIGN-GUIDE and SPEC document the current baseline.
Included in the pending CMRU release.

### KI-40 — Cleanup could commit caller edits with generated files — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** After a confirmed `steps.clean`, `cleanup_commit_deletions` staged the entire caller
checkout. An unrelated file edited before confirmation could therefore be included in CMRU's
cleanup commit. The same helper also skipped the clean-step commit when no release tags were
selected, despite the confirmed plan promising generated-file cleanup.

**Resolution.** CMRU snapshots dirty paths immediately before the clean step, stages only new
literal pathspecs, and commits only those paths. Existing dirty paths remain outside the cleanup
commit, and generated paths are committed even when no release tags were selected. Adversarial
coverage and README, DESIGN-GUIDE, CONSUMERS, and SPEC updates accompany the fix. Included in the
pending CMRU release.

### KI-41 — Resuming a retained release could widen to the default project set — *fixed in source, pending release*

**Reported:** 2026-10-01, while resuming the retained `cmru` release candidate.

**Observed.** The failed candidate recorded scope `cmru`, but `cmru worktrees` printed a
targetless `cmru release --resume PATH` command. A targetless release selects the configured
default project set, so that command planned nine projects after unrelated mainline changes.
The first selected project's gate then failed before CMRU could rewrite the transaction scope.

**Resolution.** Resume now reads the exact scope from the candidate's shared sidecar before
project-family selection, uses it when no target is given, and refuses an explicit target that
widens or narrows the saved scope. It rechecks the metadata after acquiring the release lock.
`cmru worktrees` shows the recorded scope and prints a matching command only when the scope is
readable; JSON exposes that scope with `project_scope_state` (`recorded`, `missing`, or
`unreadable`) so automation can make the same decision. Legacy candidates without metadata
require an explicit target. Regression coverage and README, DESIGN-GUIDE, CONSUMERS, and SPEC
updates accompany the fix.

### KI-42 — `tester-gate` combined mutually exclusive Docker CPU controls — *fixed in source, pending release*

**Reported:** 2026-10-01, when the CMRU resume gate launched CIU's required `tester-gate` step.

**Observed.** The generated Docker argv included both `--cpus` and `--cpu-period`. Docker maps
`--cpus` to `NanoCPUs` and rejects a HostConfig that also sets `CpuPeriod`, so the gate exited
125 before starting its test container.

**Resolution.** `tester-gate` now applies the validated CPU ceiling with `--cpus` alone. The
minimum supported value remains `0.00001`; docs explain why CMRU does not send `--cpu-period`.
The argv regression test asserts the accepted command shape. A live tester-gate acceptance
probe is required before the release is considered complete.

**What the live probe must show (BG review, 2026-10-05; the controller runs it in Wave 3).** Run
the real ciu tester-gate step through `cmru run-step` with the candidate's bound launcher, plus
one synthetic gate whose command prints its own limits:
`cmru tester-gate --cwd . -- sh -c 'cat /sys/fs/cgroup/{cpu.max,memory.max,memory.swap.max,pids.max}; cat /proc/1/comm; exit 7'`.
It must show: (1) no exit 125, and cmru's exit code is 7 (the command's own code survives the
events wrapper; the counters are clean); (2) `cpu.max` = `250000 100000` for 2.5 CPUs, and a
`docker inspect` taken while it runs (the container is now named `cmru-tester-<uuid8>`) shows
`NanoCpus=2500000000`, `CpuPeriod=0`, `CpuQuota=0`; (3) `memory.max` = 1 GiB,
`memory.swap.max` = 15 GiB, and the cgroup parent is `dev-gates.slice`; (4) the slice probe really
ran (`FragmentPath` reported, probe image digest-pinned and present locally because of
`--pull=never`); (5) PID 1 is `docker-init`, `pids.max` equals the declared
`CMRU_TESTER_PIDS_LIMIT`, a 200-commit loop leaves 0 zombies, and a command that exhausts a low
pids limit ends exit 3 naming `pids.events max`. Also verify once that `--init` is accepted by
the DinD sidecar with the cgroup-v2 nesting script (`--enable-docker` step).

### KI-43 — Read-only handler validation crashed without `--dry-run` — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** `wheel-validate` and `tarball-validate` correctly omit the mutation-only
`--dry-run` option, but their shared dispatcher read `args.dry_run` unconditionally. Both verbs
raised `AttributeError` before reaching their read-only validation handler.

**Resolution.** The dispatcher now treats an absent `dry_run` field as false. Regression coverage
invokes both registered validation verbs without adding a meaningless dry-run option.

### KI-44 — Plan status crashed because the read-only parser has no `dry_run` field — *obsolete (retired 2026-10-05)*

> `cmru-controller` and its `status --plan` verb were deleted on 2026-10-05 (operator decision O5); kept as history only.

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** `cmru-controller status --plan PATH` is read-only and intentionally has no
`--dry-run`, but `_build_engine` accessed `args.dry_run` directly. The parsed status command
therefore crashed before querying the plan.

**Resolution.** Engine construction now defaults an absent `dry_run` field to false. A parser-level
regression test runs plan status with the actual read-only argument shape.

### KI-45 — Local Git hooks could inherit publisher credentials — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** Release environment setup exports the project publisher credential. Raw local
`git commit`, `git rebase`, and `git revert` processes inherited that environment, and any local
hook they invoked received the same publisher credential.

**Resolution.** CMRU routes hook-capable local Git operations through a helper that removes
`GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and `CMRU_GIT_AUTH_TOKEN` from the child environment while
preserving other environment values. Credential-bearing remote Git calls continue to disable
hooks. The helper has direct environment regression coverage; README, DESIGN-GUIDE, CONSUMERS,
and SPEC describe the distinction.

### KI-46 — Worktree scope listing depended on path visibility and omitted JSON scope — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** `cmru worktrees` read release scope by statting each inventory path. A Git-reported
worktree outside the current bind-mount namespace could therefore lose its readable scope. JSON
records also omitted scope entirely, leaving automation unable to construct a safe resume.

**Resolution.** Scope lookup now derives the sidecar from the shared Git directory and inventory
branch without touching the listed path. Release JSON rows include `project_scope` and the closed
`project_scope_state` values `recorded`, `missing`, and `unreadable`; non-recorded scopes are null.
Text and JSON tests cover recorded, missing, unreadable, and invisible-path cases. README,
DESIGN-GUIDE, CONSUMERS, and SPEC document the interface.

### KI-47 — Legacy resume guidance omitted the original external config — *fixed in source, pending release*

**Reported:** 2026-10-01, Sol xhigh follow-up review of the unreleased CMRU changes.

**Observed.** When a retained candidate had no scope sidecar, `cmru worktrees` required an
explicit target but did not tell the operator to repeat the external `--config PATH` used for the
original release. The command could consequently load a different policy file.

**Resolution.** Missing-scope guidance now requires candidate inspection, the explicit target,
and the same original external config when one was used. The transaction does not claim to
remember that path. README, DESIGN-GUIDE, CONSUMERS, and SPEC carry the same recovery rule.

### KI-48 — Abandon ancestry probes inherited publisher credentials — *fixed in source, pending release*

**Reported:** 2026-10-03, when the CMRU release gate exercised `cmru abandon` with publisher
credentials present in the inherited process environment.

**Observed.** Four local `git merge-base --is-ancestor` probes used `subprocess.run` directly,
bypassing `run_local_git` and passing `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and
`CMRU_GIT_AUTH_TOKEN` to the Git child process.

**Resolution.** All four ancestry probes now use `run_local_git`, preserving their return-code
handling while removing publisher credential variables from the Git environment. The registered
gate exercises the regression; README, DESIGN-GUIDE, CONSUMERS, and SPEC document the behavior.

### KI-49 — `get.py enroll` runs unauthenticated as root: the installer is not verified before it executes, minisign is skipped without a key, and the install is not pinned to the requested release

**Status: moved to ciu CIU-123 (2026-10, cmru program O4, W1-CIU-ENROLL).** `get.py enroll` is ciu-owned code now (`ciu/installer/enroll.py`, inlined by the installer `extensions` mechanism); the full finding, evidence, contract and oracles are preserved in `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` CIU-123. The generic installer's verification/pinning half is cmru's installer-hardening package (GETPY-REDESIGN R1-R4), not this entry.

### KI-50 — `get.py enroll` mutates root-trusted `authorized_keys` through attacker-controllable paths and an unvalidated `--from` pattern

**Status: moved to ciu CIU-122 (2026-10, cmru program O4, W1-CIU-ENROLL).** The code moved to `ciu/installer/enroll.py`; the full finding, evidence, contract and oracles are preserved in `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` CIU-122.

### KI-51 — Adopt cli-extended (unified adoption, order 2 of 8) — *planned*

**Filed:** 2026-10-05 by the cli-extended unified-adoption program (W10). **Type:** feature. **Source documents:** [`libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`](../libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md) (decisions CX-D1..CX-D12, section "W10 - planned adoptions") and [`libraries/cli-extended/docs/ADOPTION-CHECKLIST.md`](../libraries/cli-extended/docs/ADOPTION-CHECKLIST.md) (AC-01..AC-25). **Dependency:** cli-extended 0.2.0 released first (W8, the controller). Not executed in the program's session. **SPEC:** `S-CLI.9` (`docs/SPEC.md:373`), which this moves onto the surface lifecycle; the SPEC change is part of the work.

**Observed mechanism (verified in source). cmru already uses cli-extended, but by vendoring, and every tool adopts it through cmru's own release path, so this is the order-2 prerequisite for the rest.**

- Vendoring (AC-24, CX-D1): `pyproject.toml:28` adds `../libraries/cli-extended/src` to `packages.find` and `:32` maps `cli_extended = "../libraries/cli-extended/src/cli_extended"` through `[tool.setuptools.package-dir]` (`:30`). `dependencies = []` at `:14`; no `cli-extended` requirement. `README.md:64` tells readers to use installed console scripts because of the vendored libraries.
- Library path hacks (AC-25): none toward `libraries/cli-extended` in `src/` (the `sys.path.insert` calls at `src/cmru/config.py:206` and `src/cmru/transaction.py:248` point at `libraries/worktree/src`, a different shared library with the same shape; they are out of this entry's scope but should be tracked with the worktree library's own adoption). Gate lanes carry the library checkout on `PYTHONPATH`: `assay.toml:41`, `run-gate.toml:58,116,171` (`PYTHONPATH=src:../libraries/cli-extended/src:../libraries/worktree/src`), plus `tools/run_release_gate.py:49` and `tools/project_fixture.py:15` copying `libraries/cli-extended` into release-gate fixtures. Under CX-D3 a gate lane may keep the worktree source for the revision under test; decide per lane in the carve.
- Version reader (AC-01): `src/cmru/cli.py:2345-2358` (`_cmru_version`: `_source_tree_version()`, then `importlib.metadata.version("cmru")`, then the literal `"dev"`), and `src/cmru/cli_support.py:20-33` (a second reader that calls it and falls back to `importlib.metadata` and then `"dev"`, with a `pragma`-marked bootstrap branch). `src/cmru/manifest.py:134-146` reads the installed cmru version again for the release manifest. `cmru_identity` at `cli_support.py:35` builds `CliIdentity(...)` from that string. `CliIdentity.resolve` replaces all three, removing the literal fallback; the manifest reader needs a decision (it must not hide a missing version).
- Exception wrapper (AC-10, AC-11): no `unexpected_exceptions=`/`expected_exceptions=` and no `--traceback` anywhere in `src/`. `except Exception as exc:` handlers: 17 in `src/cmru/cli.py` (~~8 in `src/cmru/controller/cli.py` and 1 in `src/cmru/agent/cli.py:109`~~: retired 2026-10-05). Each must be classified: domain error to `CliFailure`/`expected_exceptions`, or boundary to `unexpected_exceptions="report"`.
- Registries (AC-03): `CliRegistry(` is constructed at 12 sites, not the 5 groups the program table names: `src/cmru/cli.py:5484`, ~~`controller/cli.py:212`, `agent/cli.py:246`~~ (retired 2026-10-05: 10 sites remain), `handlers.py:592`, `tester_gate.py:636`, `runner.py:627`, `tool_deps.py:572`, `scaffold.py:403`, `getpy.py:185`, `resolve.py:102`, `versions.py:2053`, `standards.py:168`. Consolidate into one registry (cli, agent, controller, handlers, tester plus the seven per-feature ones) so one surface manifest and one skills/doctor registration cover cmru.
- `--dry-run` copies (AC-05, AC-12): hand-declared `OptionSpec(("--dry-run",), ...)` at `runner.py:652`, `tool_deps.py:609`, `standards.py:202`, `handlers.py:648`, `scaffold.py:428`, `getpy.py:222`, `versions.py:2068`, `tester_gate.py:675`, ~~`agent/cli.py:262` and `:272`~~ (retired); a `--yes` literal is passed to a delegated tool at `delegated.py:55` (not a CLI option). These collide with the library-owned `--dry-run` and become `VerbSpec(dry_run=True)` on mutating verbs; the carve must check each verb against KI-43/KI-44 (read-only parsers whose handlers read `args.dry_run`), because `--dry-run` on a verb without `dry_run=True` is refused with exit 2.
- `parse_target_names` (AC-09): `src/cmru/cli_support.py:82-98` (empty name, duplicate, exclusive `all`, raising `TargetSelectionError`) is the reference semantics for `SelectorList` (W2 documents any intentional difference); `select_target_names` at `:101` stays as the registry-resolving layer.
- Surface lifecycle (AC-16..AC-18): S-CLI.9's audit is hand-maintained (`docs/SPEC.md:373`, `docs/reviews/cli-extended-adoption-review.md`); it becomes `cli-extended surface sync/check` with a review catalog and findings file.
- Skills (AC-19): `.claude/skills/cmru-cli/SKILL.md` is the source tree; `pyproject.toml:47-48` package-data lists only `templates/*`. Move the skill to package data and call `register_skills_verbs`.
- Doctor (AC-20): cmru has external prerequisites (git, docker, forge CLIs); decide which of the existing diagnostics become named `doctor` checks.
- Tests (AC-23): no `_invoke*` subprocess helper found beyond `tests/test_cli_abandon.py:128` (`_invoke_abandon`, which calls the handler in-process); 52 test files call `subprocess.run` directly, which are candidates for `invoke_script(home=...)`.
- Installer (CX-D2, specific to cmru): `get.py.tmpl` installs each bundled wheel on its own with `pip install --no-index <wheel>` (`src/cmru/templates/get.py.tmpl:634`, in the function documented at `:618` as "Create/update private venv and install bundled wheels (no-index)"); a tool wheel that depends on `cli-extended` cannot resolve it that way. `[[project.installer.wheels]]` is parsed at `src/cmru/config.py:377-390` (`path`, `distribution`) and consumed e.g. by `ciu/cmru.toml:50-52`. Work: install cli-extended first from the sha256-verified release manifest, then every tool wheel with `--no-index --find-links <cache>`; bundle cli-extended's wheel in every installer bundle. Verified in review (2026-10-05, toy wheels, offline): installing the dependency wheel first into the same venv makes the existing per-wheel `--no-index` loop succeed, so install ORDER is the necessary change and `--find-links` is defence in depth — do not over-scope the installer rewrite. This is the change every other tool's adoption depends on.

**Common shape (tick each, cite the AC row).**

- [ ] Declare `cli-extended>=0.2.0`; delete the `package-dir` entry (`pyproject.toml:32`) and the `packages.find` source root (`:28`) (AC-24).
- [ ] `[[installer.wheels]]` / `get.py` install cli-extended first, `--no-index --find-links` (CX-D2).
- [ ] `CliIdentity.resolve(...)` for `_cmru_version`, `cli_support.py`, `manifest.py` (AC-01, AC-02).
- [ ] `unexpected_exceptions="report"`; classify the 26 `except Exception` handlers (AC-10, AC-11).
- [ ] One consolidated registry; own `--dry-run` options become `dry_run=True`; `parse_target_names` becomes `SelectorList`; constraints where handlers check options (AC-03, AC-05, AC-06, AC-09, AC-12).
- [ ] Surface lifecycle with review and findings; S-CLI.9 moved onto it (AC-16, AC-17, AC-18).
- [ ] Skills via `register_skills_verbs` (AC-19); `doctor` (AC-20).
- [ ] Tests: `assert_cli_contract`, `invoke_script`, plugin if a catalog exists (AC-21, AC-22, AC-23).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; cmru's own registered gate passes (`run-gate.py`, lanes in `run-gate.toml`); a released cmru version is deployed (merge + `cmru release` + devcontainer install; remember the `--resume` / `--set-version` hazard recorded in memory). A fresh `get.py enroll` bundle on a pip-less host installs cmru and cli-extended offline.

**Oracles.** Installing the built cmru wheel into a scratch venv with `--no-index` and no cli-extended wheel fails with the dependency named; with the verified cli-extended wheel it passes `cmru --version`; the installer test in `tests/test_installer.py` gains a case where the tool wheel `Requires-Dist: cli-extended` and installation order is wrong (must fail) versus right (must pass); a controlled wrong implementation that leaves the vendored copy in the wheel fails a wheel-contents check.

### KI-52 — `cmru tester-gate` starts tester-unified without `--init`: the gate command becomes PID 1, never reaps orphans, and git's detached auto-maintenance fills the container's `pids.max` with zombies until every fork fails — *fixed in source (a, a', c', b), pending release; (b) is bypassed by assay's hermetic git and filed as assay B147; severity was critical (silent false kills)*

**Status:** fixed in source 2026-10-05 by program 2026-10 package W0-TESTER (filed 2026-10-05, investigated on the host with `host-escape`, read-only).

**Resolution (W0-TESTER).**
- (a) `--init` on the gate workload and on the privileged DinD sidecar (`tester_gate.build_docker_command`, `_dind_start_argv`); `tests/test_w0_tester_hardening.py::test_every_container_cmru_starts_has_the_declared_argv_policy` drives a real `main()` against a recording fake `docker` and asserts the argv of every container it starts.
- (a') `CMRU_TESTER_PIDS_LIMIT` is a required `REQUIRED_TESTER_ENV` member passed as `--pids-limit` (positive integer, no default; the 19,117 ceiling was systemd's DefaultTasksMax, not a declared limit). Declared as `4096` in `cmru.orchestration.toml`, both templates and both samples.
- (c') the gate command runs inside an in-container wrapper that, after the command exits, copies the container's own `pids.events` and `memory.events` into `.cmru/tester-gate-events-<uuid>.txt` on the mounted worktree (`--rm` deletes the cgroup, so nothing outside could read it afterwards). cmru reads and removes it: a missing/incomplete file, a non-zero `pids.events max`, or a non-zero `memory.events oom_kill` is an infrastructure failure (exit 3, naming the counter) even when the command exited 0; otherwise the command's own exit status is preserved.
- (b) `tester-unified/Dockerfile` sets `maintenance.autoDetach false` and `gc.autoDetach false` system-wide (asserted at build time; README documents it). **Not sufficient alone:** assay's hermetic git environment sets `GIT_CONFIG_NOSYSTEM=1` and bypasses `/etc/gitconfig`; filed as assay B147 (`assay/nyxloom-trove/4-backlog.md`).
- The image build and the `docker run` behaviour were NOT exercised live in W0-TESTER (no docker builds, no real containers); the KI-42 live probe below covers (a)/(a').

**Observed (2026-10-05, host-wide 19,109 zombies):**
- 19,108 zombies had one parent: PID 1414479, `python3 ./run-gate.py --base main assay-r2`, which is PID 1 of container `pedantic_antonelli` (`tester-unified:local`, cgroup `dev.slice/dev-gates.slice/docker-df361f8a….scope`). 19,057 of them are `git`, 42 `sleep`, 9 `docker`.
- The container matches `cmru tester-gate`'s argv exactly: `--rm`, worktree bind-mounted at `/worktree`, a relative `--workdir`, `--memory 1g`, `--cpus 2.5`, `CGROUP_PARENT_*` env, the command as the image CMD. `docker inspect` shows `HostConfig.Init = <nil>`.
- Zombies accrued from 02:40:55Z to 03:11:18Z (~10/s). At 03:11Z the container's `pids.current` reached **19,115 of `pids.max` 19,117** and zombie growth stopped — because nothing could fork any more. `docker exec pedantic_antonelli sh -c true` then failed with `OCI runtime exec failed: … procReady not received`.
- **Consequence (filed separately as assay B145):** the operator discarded the campaign's Assay state and progress and invalidated all post-03:11Z candidate outcomes and its final verdict. None of those results are used as evidence here.

**Root cause (reproduced):**
1. `src/cmru/tester_gate.py:150` `_docker_run_argv` builds `docker run --cgroup-parent=… --rm --mount … <image> <command>` with no `--init` (the tester-gate call at `:577`; the dind sidecar at `:472` and the probes at `:233`/`:297` are separate). The image has no init in its ENTRYPOINT, so the gate command is PID 1, and an ordinary program as PID 1 never `wait()`s for orphans it did not spawn.
2. git 2.55 (the image's git) runs auto-maintenance **detached** after `commit`/`merge`/`fetch`: the foreground git spawns `git maintenance run --auto --detach`, which daemonizes and is orphaned to PID 1. Reproduced in a disposable no-init `tester-unified:local` container: 20 `git commit`s → **20 new zombies**; with `-c maintenance.autoDetach=false -c gc.autoDetach=false` → 0; with `-c maintenance.auto=false` → 0. A test suite or mutation campaign that makes thousands of commits in scratch repos therefore leaks one zombie per commit.
3. The same mechanism caused dstdns's 2026-10-04 incident (755 zombies, mostly git, under a `sleep infinity` runner PID 1; dstdns D-670 "TEST-RUNNER-INIT"). That one was a dstdns runner without `init: true`; this one is cmru's own launcher. run-gate's own container launches already pass `--init` (`run-gate.py:5873`, `:9856`).

**Fix:**
- (a) `_docker_run_argv` (or the tester-gate call site) always passes `--init`; pin it with a test that asserts `--init` is in the argv of every container cmru starts for a command it does not control. Decide explicitly for the dind sidecar (its entrypoint runs `dockerd`; `--init` is harmless there and reaps dind's own orphans).
- (b) Defence in depth in the image: `tester-unified/Dockerfile` sets `git config --system maintenance.autoDetach false` and `git config --system gc.autoDetach false` (verified to stop the leak). This also stops background maintenance from mutating a test or snapshot repository mid-run, which is a determinism win on its own. Document it in `tester-unified/README.md`.
- (c) `cmru tester-gate` checks the container's `pids.events` `max` counter after the command: non-zero means at least one fork was refused at the pid limit, and the step must fail as an infrastructure error (never pass), naming the counter. (This is the generic guard; B145 is assay's own.)

**Oracles:** the tester-gate argv contains `--init` (a controlled wrong implementation without it fails); in a scratch tester-gate run whose command makes 200 `git commit`s in a temp repo and then exits, the container ends with zero zombies (`ps -eo stat` inside, before exit); with (b) and no `--init`, the same run also leaves zero; a command that exhausts a low `--pids-limit` makes the step fail infrastructure-red via `pids.events`.

**Immediate operator action (completed 2026-10-05):** the operator confirmed the campaign container is gone and no zombies remain, discarded its Assay state and progress, and invalidated all post-03:11Z candidate outcomes and the final verdict. None of that campaign's post-event results are evidence.

**Related:** assay B145 (false kills under fork exhaustion), run-gate RG-84 (refuse to run as an unreaping PID 1), dstdns D-670 TEST-RUNNER-INIT.

### KI-53 — `cmru wheel-build` mounts only `cwd.parent` and forwards no build env: a project nested two levels deep (`libraries/cli-extended`) cannot resolve its git version in the wheel-builder, and `SOURCE_DATE_EPOCH` / `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>` never reach the container — *fixed in this change, severity: high (blocks a release after the tag is pushed)*

**Status:** fixed (2026-10-05, branch `cmru-ki53-nested-wheel`).

**Observed (2026-10-05):** `cmru release cli-extended --set-version 0.2.0` (transaction `cmru-release-20261005_084103-cli-extended-bmluh2`) tagged and pushed `cli-extended-v0.2.0`, then failed in `cmru.handlers wheel-build`: setuptools-scm could not detect a version. Nothing was published; the tag was already pushed.

**Defect (a) — mount root.** `cmd_wheel_build` bind-mounted only `cwd.parent` (plus the git common dir). cli-extended is the estate's first wheel project nested two levels deep (`libraries/cli-extended`, `[tool.setuptools_scm] root = "../.."`). For every top-level project `cwd.parent` is the worktree root; here the linked worktree's root and its `.git` gitfile were outside the mount, so git discovery failed inside `wheel-builder:local`. Mounting the worktree root makes `git describe` resolve `cli-extended-v0.2.0` there (controller-verified).

**Defect (b) — environment not forwarded.** `resolve_versions_from_git` exports `SOURCE_DATE_EPOCH` and `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>` into cmru's own environment for reproducible timestamps and the exact tag version. Since the build moved into the `wheel-builder` container, the `docker run` passed no `-e`, so neither reached the build (a regression from the retired local build path, which inherited the environment).

**Fix:**
- `handlers._wheel_builder_mount_root(cwd)`: the git top-level (`discover_git_root`) when it contains `cwd.parent`, else `cwd.parent` (copied one-project repo where top-level is `cwd` itself, or no top-level). Mounted at its host bind source; passed as `mount_root` to `_wheel_builder_git_mount_args`. `-w cwd.parent` and the positional source are unchanged, so top-level projects get an identical command.
- `handlers._wheel_builder_env_args()`: name-only `-e NAME` for `SOURCE_DATE_EPOCH` and every `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*` present, sorted (the bare `SETUPTOOLS_SCM_PRETEND_VERSION` is deliberately NOT forwarded: cmru exports only the `_FOR_<DIST>` form and a bare value would apply to every project); nothing for absent names, never `NAME=value` on argv.
- Audit: no other cmru container launch uses the `cwd.parent` mount (`docker buildx bake` image builds, `docker login`, `tester_gate` docker runs).

**Oracles:** `tests/test_builtin_handlers.py::test_wheel_build_nested_project_mounts_the_worktree_root` (real linked worktree, `libraries/pkg`: mount is the worktree root, none of only `libraries`), `..._top_level_project_argv_is_unchanged`, `..._copied_one_project_repo_mounts_the_parent`, `..._forwards_build_env_by_name_only`, `..._forwards_no_env_when_unset`; each fix was reverted by hand and a test failed.

### KI-54 — `release --dry-run` with an internal snapshot handoff escapes as an uncaught `RuntimeError` instead of exit 1, and its test has been red on main since 2026-10-04 — *FIXED 2026-10-05 (program 2026-10 W0-REL, REL-01), severity: major*

**Fixed:** BOTH handoff guards in `_release_or_status` (`cli.py`: "valid only for a new family release launcher" and "cannot span Git families") now `log_error(msg); sys.exit(exit_codes.FAILURE)` instead of raising `RuntimeError`. The review found the sibling guard had the identical defect (its test was also red). Tests: `tests/test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run` and `::test_release_rejects_an_internal_snapshot_spanning_multiple_git_families`.

**Status (historical):** was open (filed 2026-10-05 by the cli-extended program while gating KI-53; not caused by that program — reproduced identically at main `c0d1f4410`, before the cli-extended merge).

**Observed:** `tests/test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run` (`:137`) expects `cli.main(["release", "demo", "--dry-run", "--config", "cmru.orchestration.toml"]) == 1` and the message "valid only for a new family release launcher" on stderr. `cli.py` (around `:4581`) raises `RuntimeError("the internal origin/main snapshot handoff is valid only for a new family release launcher")` when `_ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT` is set and `transaction_child or vargs.dry_run or vargs.resume`; the exception propagates out of `cli.main` uncaught, so the test fails. The guard and test arrived with the 2026-10-04 snapshot-boundary work (`071046398`, `fccd44be3`, `0f96d124f`).

**Fix direction:** either convert the refusal to the CLI's failure type (exit 1 with the message on stderr, matching the sibling multi-family refusal at the next guard) or, if escaping is intended for an internal-only path, correct the test; whichever the snapshot-boundary owner intended. Then re-run the `coverage` and `canary` lanes on main.

**Oracle:** the test passes; a controlled wrong implementation that lets the `RuntimeError` escape fails it; the `coverage` lane on main is green.

### KI-55 — The durable candidate backup branch is not re-pushed after a merge-promote (REL-04 follow-up) — *open, severity: minor*

**Observed:** `transaction.promote_workspace` now merges `origin/main` into the candidate when main advanced (`_merge_origin_main_into_candidate`), then pushes `HEAD` to `main`. The origin backup branch `cmru-release-...` (refreshed by `push_backup_branch` before the gate and after each tag commit) still points at the pre-merge tip until the transaction is cleaned up. If promotion then stops (a later retry fails, or the process dies between merge and push), the durable copy on origin lacks the merge commit that exists only in the local retained worktree.

**Fix direction:** call `push_backup_branch` after each successful merge inside the promote loop, before the retry push.

**Oracle:** an end-to-end case where main advances twice and the second promotion attempt fails: the origin candidate branch equals the local worktree tip. Fails on the current code.

### KI-56 — Nothing prevents a publishing step inside the REL-05 tag-rollback window (`build_step = "push"`) — *open, severity: minor*

**Observed:** the REL-05 rollback treats the `build_step` phase as non-publishing and deletes the freshly pushed tag when it fails. A project that sets `build_step = "push"` (or whose build step publishes) would have already published when the rollback runs, so the rollback could delete a tag whose artifacts are public. Config validation accepts it today.

**Fix direction:** refuse `build_step = "push"` (and any step the project declares as publishing) at config validation, with a message naming the rollback guarantee.

**Oracle:** a config with `build_step = "push"` is rejected at load; a normal `build` step is accepted.

### KI-57 — No multi-project or nested-project end-to-end case for merge-promote (REL-15 follow-up) — *open, severity: minor*

**Observed:** `tests/test_release_end_to_end_real_git.py` drives a single project under `demo/`. Merge-promote's project-path check (`project_paths`), the per-project promotion order and the checkpointing are never exercised with two projects in one transaction or a project nested more than one level deep.

**Fix direction:** add an end-to-end case with two projects (one gated while main advances touching only the other project's path) and one nested project.

**Oracle:** the new tests pass; planting the path check as always-true or always-false fails one of them.

### KI-58 — Non-empty `## [Unreleased]` bodies in three estate changelogs will make their next tagged releases fail (KI-30 follow-up, estate) — *open, severity: major (blocks releases)*

**Observed:** since W0-REL (REL-10 / KI-30) a tagged release is refused while a plain `## [Unreleased]` section has a non-empty body. `assay/CHANGES.md`, `run-gate-project/CHANGES.md` and `scripts/cgroup-profiler/CHANGES.md` carry non-empty bodies today, so their next releases will be refused.

**Fix direction:** per project, move the text into the release notes (generated section) or empty it, keeping a comment. Out of W0-REL's scope; an estate follow-up for the controller.

**Oracle:** `cmru release --dry-run` for each of the three projects passes the changelog check.

### KI-59 — The `file:` resume bump-commit skip matches any subject starting with `chore: bump <prefix> to ` (W0-REL review nit) — *open, severity: minor*

**Observed:** `changelog._project_commits_after_cursor` ignores cmru's own version-bump commit so a resumed `file:` release re-tags the gated commit. The match is a subject prefix only. The W0-REL reviewer committed a real code change to `demo/core.py` with subject `chore: bump demo-v to 9.9.9 and rewrite core`; `generate_release_changelog` then returned False, so that work was missed by the changelog and by the resume decision.

**Fix direction:** skip only a commit whose subject equals `chore: bump <prefix> to <pending_version>` exactly AND whose diff touches only the declared version file (plus the generated changelog cmru wrote).

**Oracle:** the reviewer's probe (a real change with a look-alike subject) is counted as project work; the existing `file:VERSION` resume e2e case stays green.

### KI-60 — Digest pins of locally present helper images age and have no refresh policy (W0-TESTER review) — *open, severity: minor*

**Observed:** since W0-TESTER the privileged probe and DinD images must be `@sha256:` pinned and are started `--pull=never`, so the pin must name an image already on the host. `cmru.orchestration.toml` (`debian@sha256:d7e1...`) and mdt's `cmru.toml` (`docker@sha256:5efe...`) were pinned on 2026-10-05 from the local `RepoDigests`. Nothing records when, nothing warns when a pin gets old (base-image CVEs), and nothing says how to refresh one; a refresh is also the only moment a pull happens, so it is easy to forget.

**Fix direction:** either (a) `cmru standards` warns when a pinned helper-image digest is older than N days (needs a pin-date record next to the pin, e.g. a `CMRU_TESTER_PIN_DATE_<VAR>` or a `[pins]` table, and the freshness rule's 14-day vetting buffer in the other direction), or (b) a documented refresh procedure in `docs/CONSUMERS.md` (pull the new tag deliberately, read `docker image inspect --format '{{index .RepoDigests 0}}'`, update every estate pin in one change, run the KI-42 probe). (b) is the minimum; (a) is the guard.

**Oracle:** for (a), a project whose recorded pin date is older than the threshold gets a standards warning naming the variable; for (b), the procedure is reproducible by someone who has never done it.

### KI-61 — `cmru skills` lacks `--log-prefix-time-short`: the cli-extended surface check carries a one-message tolerance (W2-PKG5; library defect CLI-EXT-26) — *resolved 2026-10-06 (cli-extended-v0.3.0), severity: minor*

**Resolved 2026-10-06:** cli-extended `cli-extended-v0.3.0` ships CLI-EXT-26. cmru's floor is now `cli-extended>=0.3.0`; the tolerance test `test_surface_check_reports_nothing_beyond_the_known_library_gap` is deleted and `tests/test_cli_spec_inventory.py::test_surface_check_reports_no_findings` requires an empty finding list; finding `adoption-skills-global-option` is `fixed`. (Package CMRU-FLOOR, report `nyxloom-trove/reports/cmru-FLOOR-2026-10-REPORT.md`.)

**Observed:** `cli-extended surface check` (and audit AC-17/AC-18) report `cmru skills: delegated parser does not register inherited global option(s): --log-prefix-time-short`. The cause is cli-extended 0.2.0 `register_skills_verbs`, which builds its child registry without the parent's `global_options` (filed in `libraries/cli-extended/BACKLOG.md` as CLI-EXT-26). The controller ruled (2026-10-06) to accept the gap for cmru 6.0.0 rather than re-implement the skills group with private library helpers. `tests/test_cli_spec_inventory.py::test_surface_check_reports_nothing_beyond_the_known_library_gap` asserts the findings EQUAL exactly that one message, so a library fix makes the test fail and forces this item.

**Fix direction:** when cli-extended ships the CLI-EXT-26 fix, bump the `cli-extended>=` floor in `pyproject.toml` (and `CLI_EXTENDED_REQUIREMENT` / the doctor floor check) to that release, delete the tolerance and the exact-message assertion so the test requires an empty finding list, run `cli-extended surface sync`, and close finding `adoption-skills-global-option` in `docs/cli-review-findings.toml`.

**Oracle:** `cli-extended surface check` and `cli-extended audit --cli cmru` both exit 0 with no tolerance in the test.

### KI-62 — R2 mutation campaign for 6.0.0 postponed by operator decision 2026-10-06; run it against the `cmru-v6.0.0` tag and backport fixes (LANDPREP) — *open, severity: major (release evidence gap)*

**Observed:** the operator decided to merge and release cmru 6.0.0 with the R2 mutation campaign postponed. The release step (`cmru.toml` `[steps.run-tests]`) therefore runs `./run-gate.py gate-provisional`, which is `tools/run_release_gate.py --postpone-mutation KI-62`: every lane except `mutation` runs as in `gate`, and `.assay/mutation-postponed-cmru.json` (tracking id, timestamp, reason) is retained with the gate evidence next to one WARN line. The 6.0.0 release therefore has NO mutation evidence.

**Fix direction:** after the release, run `./run-gate.py gate` (full, including R2) against the `cmru-v6.0.0` tag, triage survivors, and backport fixes. Then revert `cmru.toml` `[steps.run-tests]` to `./run-gate.py gate`, delete the `gate-provisional` lane in `run-gate.toml`, and grep for `TODO(cmru-6.0 post-release)`.

**Oracle:** a full `gate` verdict (with `.assay/mutation-cmru.json`) exists for the `cmru-v6.0.0` tag, every survivor is fixed or recorded, and no `TODO(cmru-6.0 post-release)` marker remains that this item owns.

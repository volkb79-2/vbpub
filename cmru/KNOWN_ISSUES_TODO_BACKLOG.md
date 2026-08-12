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

## Known Issues

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
**Status:** the documented promise was narrowed to current behavior: `--resume` is useful for
investigating/correcting a retained **pre-tag** transaction worktree; it is not an automatic
post-tag publish retry. It remains useful when a prepare step or the tester gate fails: inspect
the isolated source tree, make a deliberate correction there, re-run the required gate, then
resume. The worktree is also the right forensic location for logs and generated provenance;
do not copy unreviewed files into the caller checkout.

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

**Recommendation:** retain the current pre-tag debug use case and add a separately designed
`resume-publish` state machine only when MDT’s elapsed prepare time justifies it. Start with
MDT’s OCI layout/digest contract; do not promise a universal resume mechanism first.

### KI-07 — Runner log location conflicted with S3.4 — *shipped*
**Status:** resolved. Every runner step writes a line-flushed project-local
`<project>/logs/cmru/<step>.log`, overwriting by default and inserting `\n---\n` with
`--log-append`. In a transaction that path is inside the retained worktree, so a failed
release or build is self-contained for debugging. Successful releases remove it with the
worktree unless `--retain-logs-on-release` moves it project-side. A successful normal
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

### KI-10 — `cmru build` artifacts cannot safely feed `cmru publish` — *open; decision required*
**Evidence:** `cmru build` creates an isolated `cmru/build/<id>` worktree, runs its
prepare/gate/build phases there, and on success copies logs and declared artifact directories
into commit-addressed, gitignored local records under `<project>/logs/` and
`<project>/artifacts/`. The `build.json` inventory binds their source SHA, digest inventory,
and any tracked prepared-tree changes and explicitly says `publication: forbidden`. CMRU then
removes the successful worktree; a failed child or output-retention failure retains it for
debugging. `cmru publish`, by contrast, runs the project's declared `push` step in the caller
checkout and is deliberately unaware of those local records. Consequently the seemingly natural
sequence `cmru build --project X` then `cmru publish --project X` still does **not** publish the
reviewed build; it finds no declared push input or can publish a different caller-side artifact.

**Current safe workflow:** use `cmru release`, whose one source-first transaction
performs gate → tag policy → build → push in one worktree. `cmru build` is a
local-consumption/inspection verb, not a pre-publication staging verb.

**`--from-candidate` is deliberately postponed.** It would be a release-verb addition for a
different use case, not an alias for `--resume`: a durable, deliberate promotion boundary after
an immutable commit has been built and gated, while offsite fuzzing/mutation evidence, review,
or an approval may take hours or days. `--resume <worktree>` instead continues one retained
pre-tag source transaction after immediate investigation; a manual worktree edit invalidates its
old gate evidence and it is not a durable post-tag publication retry (KI-06). Holding such a
mutable worktree while waiting for a remote result is not a candidate protocol.

**What a future candidate must prove:** a persisted immutable record must bind the exact source
SHA and resolved version/tag intent, artifact digests, declared gate verdict, pinned build
toolchain/image, remote-job request and returned evidence/attestation, and idempotent remote
publication state. Promotion must revalidate every identity before minting/pushing the tag and
publishing the recorded artifacts. A normal local `build.json` cannot qualify: it explicitly
forbids publication and may truthfully record uncommitted deterministic `prepare` outputs.

**Decision for now:** keep `publish` as a low-level caller-worktree command and retain this
non-composability. Do not add `publish --worktree`, `release --from-candidate`, or a convenience
alias until a concrete remote-qualification release policy requires it. At that point design
`release --from-candidate <id>` as a full immutable promotion state machine, not a generic
retry. Copying `dist/` back merely to make the command chain work would defeat the isolation rule.

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

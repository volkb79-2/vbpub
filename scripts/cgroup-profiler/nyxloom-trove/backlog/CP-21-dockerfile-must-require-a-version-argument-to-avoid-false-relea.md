---
kind: backlog-entry
schema_version: 1
id: CP-21
title: "Dockerfile must require a version argument to avoid false release identity"
status: carved
type: "bugfix"
severity: "high"
component: "build"
context_estimate: "small"
provenance: "Sol xhigh contract-2 release review, 2026-10-12"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler image build"
priority: 1
filed_date: "2026-10-12"
---

## Problem

The Dockerfile defaulted `CGPROFILE_VERSION` to an older release identity, and the consumer guide showed a direct source build with the release value regardless of checkout. Those examples were corrected, but Sol xhigh reviews found that the release handoff must bind published bytes to CMRU's recorded candidate commit and clear ambient release-pair values from ordinary steps. A clean checkout with a moved local tag could publish the wrong source under the release version. A failed `git rev-parse HEAD` could also become the image revision `unknown`, and version and revision were read separately. The socket golden also named 1.1.0 although the combined contract-2 tree is planned as the first 1.0.0 release.

## Expected behavior

The Dockerfile has no release-version or Git-revision default and fails before package installation when either build identity field is absent or empty. CMRU passes protected `CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` values to tagged build and push steps and clears ambient values from ordinary steps; project configuration cannot replace the tagged values. The pair proves Git source consistency, not CMRU provenance, and is not an authentication token. The builder checks both `HEAD` and the peeled local tag against that exact commit, refuses a dirty tree, and builds from a temporary full-repository worktree at the verified commit. Version and commit are resolved as one identity, and Git failures never become `unknown`. An untagged local build uses `0.0.0-dev`, refuses modified or untracked files in the cgprofile and sibling DAMON build contexts, and builds from a temporary tree at the checked revision; publishing refuses when the release pair is absent or invalid, even if `CGPROFILE_VERSION` is set. Local consumers use the wrapper rather than invoking Docker with a manually selected identity. The socket golden pins the combined first release as 1.0.0. README, DESIGN-GUIDE, and CONSUMERS explain the protected handoff and require `cmru release` for publication.

## Acceptance oracles

- Dockerfile regressions assert neither the version nor revision argument has a literal default and both fail-closed guards run before package installation.
- The consumer-doc regression directs local builds through the identity-resolving wrapper, documents `0.0.0-dev` and `cgprofile:local`, and avoids manually assigning a release identity.
- Resolver regressions prove a moved local tag, changed checkout, incomplete/missing CMRU identity, dirty source, and ambient `CGPROFILE_VERSION` cannot authorize publication.
- A Git failure cannot be converted to `unknown`; local builds pass one checked version/commit pair.
- Dirty or untracked changes inside either local build context are refused before Docker starts; unrelated dirty files do not trigger the refusal. Clean local builds use a temporary worktree at the exact revision named by the image label.
- Release build and push regressions prove the Docker context is a temporary worktree at the protected commit, and mismatched identity is refused before registry login.
- Runner regressions prove ambient release-pair values do not enter ordinary project steps, and public docs state that the pair is not authentication or proof of CMRU provenance.
- Socket golden daemon-version fields match the documented combined first release, 1.0.0.
- Documentation regressions keep README, DESIGN-GUIDE, and CONSUMERS aligned on the exact-tag rule.
- The registered unit and coverage gates pass with the supported local and release build paths.
- A live build acceptance verifies that the declared Bake path supplies the version and that an omitted direct build argument is refused before installing packages.

## Updates

**2026-10-13** — 2026-10-13: The protected CMRU commit/tag handoff, exact temporary build tree, and revision-label deployment check are present in the candidate. Keep this entry carved until the registered gates, exact release build acceptance, and post-deploy revision check pass.

**2026-10-13** — 2026-10-13: Protected release source identity and version arguments are implemented in the release candidate. Exact-tip gates, release build acceptance, and post-deploy revision verification remain pending.

**2026-10-21** — CGP-110: the code obligations are present on main (no `ARG` default in the Dockerfile, protected CMRU commit/tag handoff, exact temporary build tree, and the dirty-source refusal in `cmru/build-initial-standalone.sh`, which the Sol review found missing at 1.0.0). What remains is EVIDENCE, to be produced at the 1.1.0 release: registered `r0-r1` and `r3` receipts from a clean exact tree (`dirty:false`, `history_eligible:true`), one live Bake build acceptance (an omitted direct build argument is refused before package installation), and the post-deploy `org.opencontainers.image.revision` check against the peeled release tag (`docs/CONSUMERS.md`, "Deploy a published daemon image with CIU"). This entry stays `carved` until those are recorded.

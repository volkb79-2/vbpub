---
kind: backlog-entry
schema_version: 1
id: CP-21
title: "Dockerfile must require a version argument to avoid false release identity"
status: open
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

The Dockerfile has no release-version default and fails before package installation when `CGPROFILE_VERSION` is absent or empty. CMRU passes protected `CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` values to tagged build and push steps and clears ambient values from ordinary steps; project configuration cannot replace the tagged values. The pair proves Git source consistency, not CMRU provenance, and is not an authentication token. The builder checks both `HEAD` and the peeled local tag against that exact commit, refuses a dirty tree, and builds from a temporary full-repository worktree at the verified commit. Version and commit are resolved as one identity, and Git failures never become `unknown`. An untagged local build uses `0.0.0-dev`; publishing refuses when the release pair is absent or invalid, even if `CGPROFILE_VERSION` is set. The direct source-build example uses `0.0.0-dev` and a local-only tag. The socket golden pins the combined first release as 1.0.0. README, DESIGN-GUIDE, and CONSUMERS explain the protected handoff and require `cmru release` for publication.

## Acceptance oracles

- A Dockerfile regression asserts the version argument has no literal default and that the fail-closed guard runs before package installation.
- The consumer-doc regression asserts the direct source build uses `0.0.0-dev` and `cgprofile:local`, never a release identity.
- Resolver regressions prove a moved local tag, changed checkout, incomplete/missing CMRU identity, dirty source, and ambient `CGPROFILE_VERSION` cannot authorize publication.
- A Git failure cannot be converted to `unknown`; local builds pass one checked version/commit pair.
- Release build and push regressions prove the Docker context is a temporary worktree at the protected commit, and mismatched identity is refused before registry login.
- Runner regressions prove ambient release-pair values do not enter ordinary project steps, and public docs state that the pair is not authentication or proof of CMRU provenance.
- Socket golden daemon-version fields match the documented combined first release, 1.0.0.
- Documentation regressions keep README, DESIGN-GUIDE, and CONSUMERS aligned on the exact-tag rule.
- The registered unit and coverage gates pass with the supported local and release build paths.
- A live build acceptance verifies that the declared Bake path supplies the version and that an omitted direct build argument is refused before installing packages.

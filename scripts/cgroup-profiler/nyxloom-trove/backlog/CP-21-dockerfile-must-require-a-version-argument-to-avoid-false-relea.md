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

The Dockerfile defaulted `CGPROFILE_VERSION` to an older release identity, and the consumer guide showed a direct source build with the release value regardless of checkout. The Dockerfile and example were corrected, but a later Sol xhigh review found that `resolve_build_version` still accepted an ambient `CGPROFILE_VERSION` when no CMRU tag pointed at `HEAD`. `build-push.py --push` could therefore publish arbitrary source under a claimed release version. CMRU creates its exact release tag before the image build, so the environment override is unnecessary.

## Expected behavior

The Dockerfile has no release-version default and fails before package installation when `CGPROFILE_VERSION` is absent or empty. The resolver derives a release version only from the exact CMRU tag at `HEAD`, and refuses release identity when the tagged tree is dirty. An untagged local build uses `0.0.0-dev`, and publishing refuses even if the environment contains `CGPROFILE_VERSION`. The direct source-build example uses `0.0.0-dev` and a local-only tag. README, DESIGN-GUIDE, and CONSUMERS explain that the environment variable carries identity after resolution but does not create release authority.

## Acceptance oracles

- A Dockerfile regression asserts the version argument has no literal default and that the fail-closed guard runs before package installation.
- The consumer-doc regression asserts the direct source build uses `0.0.0-dev` and `cgprofile:local`, never a release identity.
- Resolver regressions prove an ambient `CGPROFILE_VERSION` cannot change an untagged development build or satisfy the exact-tag requirement for publishing.
- A dirty working tree at a release tag is refused so the image cannot contain source that differs from its tagged commit.
- A `build-push.py --push` regression proves an ambient version override is refused before authentication or registry login.
- Documentation regressions keep README, DESIGN-GUIDE, and CONSUMERS aligned on the exact-tag rule.
- The registered unit and coverage gates pass with the supported local and release build paths.
- A live build acceptance verifies that the declared Bake path supplies the version and that an omitted direct build argument is refused before installing packages.

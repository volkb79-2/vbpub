---
kind: backlog-entry
schema_version: 1
id: CP-14
title: "release bake pushes cgprofile:local to Docker Hub along with the versioned GHCR image"
status: fixed
type: "bugfix"
severity: "high"
component: "packaging"
context_estimate: "small"
provenance: "RG-55 release audit, 2026-10-01; buildx bake --print exposed local tag in registry-push target"
filed_by: "RG-55 controller"
spec_owner: "CMRU image publication"
filed_date: "2026-10-01"
closed_date: "2026-10-01"
closed_reason: "Release publication now selects cgprofile-release, whose resolved Bake output contains only the versioned GHCR tag; cgprofile:local is isolated to the local build target. Verified with buildx --print for both targets and the focused build-push tests."
---

## Observed mechanism and reproduction

`docker-bake.hcl` gave one target both `cgprofile:local` and
`ghcr.io/volkb79-2/cgprofile:<version>` tags, and `build-push.py --push`
invoked `docker buildx bake ... all --push`. A read-only
`docker buildx bake --print all` showed both output tags on that target.
Docker resolves an image name without a registry hostname to Docker Hub, so
the release command would also have attempted to publish
`docker.io/library/cgprofile:local`. That alias is for local daemon testing,
not a release coordinate, and could cause an unintended registry write or
make a GHCR release fail on unrelated Docker Hub credentials/ownership.

## Why cgroup-profiler owns it

The project's own build wrapper selects Bake targets and owns the release
artifact coordinates; callers should not have to repair the target list.

## Proposed contract

Keep the build definition shared, but expose distinct local and release Bake
targets. `--build` may load `cgprofile:local`; `--push` must select only the
versioned GHCR target. No unqualified image name may be present in the
registry-push target.

## Oracles

- `tests/test_build_push.py` asserts the publish command selects
  `cgprofile-release`, not the development `all` group.
- `docker buildx bake --print cgprofile-release` resolves to exactly the
  versioned GHCR tag; `docker buildx bake --print all` resolves the local
  development target.
- A controlled wrong implementation that makes `--push` select `all` fails
  the unit oracle; the release-target print must never contain
  `cgprofile:local` or any unqualified tag.

## SPEC ownership

`build-push.py`, `docker-bake.hcl`, and the CMRU `build`/`push` steps.

## Updates

---
kind: backlog-entry
schema_version: 1
id: NL-15
title: "OCI image publishing (build-push.py) is uncapped and publishes the irreversible GitHub Release before the image push -- deferred, not fixed"
status: open
type: "bugfix"
severity: "medium"
component: "release"
provenance: "nyxloom cmru-adoption review, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`nyxloom/cmru.toml` (commit `853b5406`) declared `artifacts = ["wheel",
"oci-image"]` and wired `[steps.build]`/`[steps.push]` to unconditionally
invoke `python3 build-push.py --build`/`--push`, building/pushing the
`nyxloomd`+`nyxloom-agent-cli` OCI images via `docker buildx bake` on
every `cmru release --project nyxloom`.

Adversarial review of nyxloom's cmru adoption
(`nyxloom-trove/reports/nyxloom-CMRU-ADOPTION-CODE-REVIEW.md`, 2026-09-08)
found two real problems with this path, neither touched by the adoption
work itself (both pre-existing since `853b5406`):

1. **The buildx build is uncapped** -- no `--builder`, no
   `[project_metadata.builder]`, no `BUILDX_BUILDER` env var anywhere in
   `build-push.py` or `nyxloom/cmru.toml`. This host hit load 85 on
   2026-09-02 from an uncapped gate container; an uncapped multi-stage
   docker buildx build is the same risk class.
2. **Unsafe publish ordering.** `[steps.push]`'s commands run the wheel
   publish (which mints the git tag + creates the GitHub Release --
   irreversible, per `RELEASE-TRANSACTIONS.md`: "tags/GitHub Releases/ghcr
   pushes aren't reverted") BEFORE the OCI image push. `pwmcp/cmru.toml`
   does this in the opposite, safer order (images first, then the
   irreversible release) -- worth confirming which pattern the general
   cmru template/CONSUMERS.md actually recommends and matching it.

Since nyxloom is currently offline (no running `nyxloomd` container
anywhere, confirmed repeatedly this session) with no real consumer for
these images, the adoption work (nyxloom-cmru-adoption, 2026-09-08)
scoped the FIRST real `cmru release --project nyxloom` to the wheel only
-- `artifacts = ["wheel"]`, `[steps.build]`/`[steps.push]` no longer call
`build-push.py` at all. This entry tracks re-enabling OCI publishing once
there's a real reason to.

## Why nyxloom owns it

`nyxloom/cmru.toml`, `nyxloom/build-push.py`, `nyxloom/docker-bake.hcl`
are nyxloom's own release/build contract.

## Proposed contract

Before re-enabling `oci-image` in `artifacts` and restoring the
`build-push.py` calls in `[steps.build]`/`[steps.push]`:
1. Cap the buildx builder (a dedicated `docker buildx create` instance
   with resource limits, or route through the same
   `dev-background.slice`/cgroup discipline the estate's other gate/build
   containers use) -- mirror whatever `[project_metadata.builder]`
   mechanism (if any) `pwmcp`/`modern-debian-tools-python-debug`'s own
   OCI-image steps already use.
2. Swap `[steps.push]`'s command order to push the OCI images BEFORE the
   wheel publish, matching `pwmcp/cmru.toml`'s pattern -- so a failed
   image push doesn't leave an already-irreversible GitHub Release
   without its images.
3. A real forcing function: nyxloomd actually needs to run somewhere
   (currently it doesn't).

## Oracles

- A capped buildx build is observably bounded (a `docker stats` sample
  during the build shows CPU/memory ceilings actually applied, not
  Docker's unconfined default).
- `[steps.push]`'s commands list has the OCI push command before the
  wheel-publish command.
- A deliberately-broken OCI push (e.g. an invalid registry credential)
  during a **build** step (not push) leaves NO git tag and NO GitHub
  Release behind -- confirming the safer ordering actually protects
  against the failure mode it exists to prevent.

## SPEC ownership

`nyxloom/cmru.toml`, `nyxloom/build-push.py`, `nyxloom/docker-bake.hcl`.

## Provenance

Found during nyxloom's cmru-release adoption, 2026-09-08
(`nyxloom-trove/reports/nyxloom-CMRU-ADOPTION-CODE-REVIEW.md`) -- both
issues pre-existing since `853b5406`, not introduced by the adoption work,
which deferred OCI publishing rather than fixing them in place.

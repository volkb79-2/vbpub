# mdt — TODO / backlog

## Decouple the mdt base image from `ciu` (do not enforce a ciu dependency)

**Problem:** the mdt devcontainer base currently **bundles the `ciu` wheel** — `Dockerfile` takes
`CIU_WHEEL_TAG` / `CIU_WHEEL_ASSET_NAME` / `CIU_WHEEL_URL` / `CIU_WHEEL_SHA256` build args and installs
the wheel into the image, and `docker-bake.hcl` declares those variables + the resolver injects them.
That makes **every** mdt-based devcontainer carry (and version-couple to) `ciu`, even though `ciu` is a
specific orchestration tool, not part of a general Debian/Python dev base.

**Want:** mdt is a general-purpose base; `ciu` should be a **consumer-repo install** (each consuming
repo installs the ciu wheel via its own mechanism — e.g. its post-create / resolver), NOT baked into
the base image.

**TODO:**
- Make the ciu-wheel injection **optional / removable** from the base build (gate it behind an opt-in
  build arg defaulting to off, or drop it entirely and move the install to the consumer repo's
  post-create flow).
- Drop the `CIU_WHEEL_*` args/vars from the default mdt `Dockerfile` + `docker-bake.hcl` path (keep an
  optional hook if a consumer wants to pre-bake it).
- Audit for any other ciu coupling in the base (post-create assumptions, PATH entries, etc.).
- Note: the unified `container-exec.py` (dstdns) already avoids importing ciu — it parses the ciu
  config files instead — so the *tooling* side does not add a ciu dependency.

_Rationale captured 2026-06-23 per user direction: "we dont want our mdt devcontainer enforcing ciu dependency."_

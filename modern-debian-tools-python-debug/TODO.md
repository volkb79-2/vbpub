# mdt — TODO / backlog

## Principle: ship + encourage ciu, but never ENFORCE it (no hard ciu dependency)

mdt **ships the `ciu` wheel** (the `CIU_WHEEL_*` build args bake it into the image) and **encourages**
its use — that is intentional and stays. The rule is the other direction: a repo that uses the mdt
devcontainer must **not be forced** to adopt ciu. ciu is *available*, not *required*.

Concretely:
- **Keep** the ciu wheel baked into the mdt base image (provided + encouraged).
- **No hard dependency:** mdt's base + lifecycle (the post-create flow, provided tooling) must function
  for a consumer repo that does **not** use ciu — never fail or assume a ciu-managed stack.
- **ciu-aware tooling must stay import-free of ciu:** e.g. dstdns's `scripts/container-exec.py` is
  ciu-aware (it *parses* `ciu.global.toml`/`.env.ciu` to resolve container names) but imports nothing
  from ciu and degrades gracefully when ciu config is absent. That is the pattern to follow.

**Audit TODO:** confirm nothing in the mdt base image or its post-create path hard-requires ciu
(config, CLI, or a ciu-rendered stack) to complete successfully; where ciu is used, make it optional
with a graceful no-ciu fallback.

_Captured 2026-06-23 per user direction: "we do ship ciu in mdt and encourage its use, but we do not enforce usage of ciu, so no hard dependencies for the repos using mdt."_

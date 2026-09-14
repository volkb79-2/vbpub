# MDT host-setup — the single normal BuildKit backend for MDT builds.
# The host installer registers this name as a Buildx remote builder in this
# shared, root-owned state directory after the mdt-buildkitd service socket is
# available. Every Docker-capable host-shell user reads the same registration;
# BUILDX_BUILDER is intentionally explicit so a missing/stale registration is
# an error rather than a fallback to Docker's default builder.
export BUILDX_CONFIG=/etc/mdt/buildx
export BUILDX_BUILDER=mdt-managed
export BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock

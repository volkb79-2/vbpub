# MDT host-setup — the single normal BuildKit backend for MDT builds.
# The host installer registers this name as a Buildx remote builder after the
# mdt-buildkitd service socket is available. Do not replace this with a
# docker-container builder: its worker would not be the governed service.
export BUILDX_BUILDER=mdt-managed
export BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock

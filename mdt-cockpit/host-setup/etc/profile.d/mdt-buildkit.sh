# MDT host-setup — the single normal BuildKit backend for MDT builds.
# `mdt-managed` is a Buildx remote builder pointing at the mdt-buildkitd service socket.
# Each user registers it in their OWN Buildx state (~/.docker/buildx): Buildx writes build
# refs and a last-activity stamp into its state directory on every build, so a shared,
# root-owned BUILDX_CONFIG cannot serve non-root users (measured: "permission denied"
# on /etc/mdt/buildx/activity). The docker-buildx shim installed next to this file's
# sibling /usr/local/lib/docker/cli-plugins/docker-buildx registers it on first use, so
# nothing here needs the registration to exist yet. This file is only the early, explicit
# selection for shells that read it (bash/sh login shells); /etc/environment (pam_env:
# ssh, sudo, su, login, cron) and the shim cover every other way a build starts.
# BUILDX_BUILDER is explicit so a missing registration is an error, never a fallback to
# Docker's default builder. docs/BUILD-ARCHITECTURE.md "Making every build use mdt-managed".
export BUILDX_BUILDER=mdt-managed
export BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock

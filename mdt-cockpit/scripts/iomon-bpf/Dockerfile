# Test-infra image for the iomon eBPF collector: bpftrace on the repo's usual Debian
# devcontainer base (already present locally, so no Docker Hub pull).
# Build: docker build -t iomon-bpf:local scripts/iomon-bpf
ARG BASE_IMAGE=mcr.microsoft.com/devcontainers/python:dev-3.14-trixie
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends bpftrace \
 && rm -rf /var/lib/apt/lists/*
ENTRYPOINT []

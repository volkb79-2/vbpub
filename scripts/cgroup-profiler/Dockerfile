# LEGACY cgprofile container image. Native deployments use `cgpd install` and
# systemd-owned per-level sockets; do not use this image with cgpd 2.2.0+.
# See docs/NATIVE-DEPLOYMENT.md.
#
# cgprofile — the RG-55 profiling daemon (RG55-INTERFACE-CONTRACT.md).
#
# Base: python:3.14-slim — matches this project's own dev environment
# exactly (DESIGN.md §6: "Python 3.14.6 in both the devcontainer and the
# helper image"), and satisfies the estate's freshness floor (>=14 days
# past release; 3.14 has been out far longer than that as of this build).
# If this tag cannot be pulled where you are building, use the newest 3.x
# `-slim` tag available and record the substitution in the P1 REPORT — do
# not silently drift the pin.
#
# Two tiers baked into ONE image, never fetched at runtime (setup.sh's own
# rule, restated for the container: "nothing in this tool installs,
# downloads, or assembles anything at run time"):
#   - COLLECTOR (`serve`, `ctl`, `mark`): stdlib only, runs on the system
#     python3 this base image already ships.
#   - REPORT (`ctl report`'s subprocess, `cgprofile report`): pandas/
#     plotly/etc., built into venv/ from requirements-report.txt (the pinned
#     lock — see that file's own header) at BUILD time only.
#
# Two stages: `ruptures==1.1.9` (pyproject.toml's pin) ships no prebuilt
# wheel for cp314 yet, so it compiles a C extension at `pip install` time —
# needing gcc, which has no business in the RUNNING daemon's attack surface
# (this image is privileged but uses private namespaces and explicit host
# proc/cgroup mounts, with only proc read-only and placement writes guarded;
# every package
# not needed at runtime is one this daemon does not carry). The builder
# stage installs build-essential and produces venv/; the final stage copies
# only the finished venv, never the compiler that made it.
ARG CGPROFILE_VERSION
ARG GIT_REVISION
FROM python:3.14-slim AS builder
ARG CGPROFILE_VERSION
ARG GIT_REVISION
RUN if [ -z "${CGPROFILE_VERSION}" ]; then echo >&2 'CGPROFILE_VERSION build arg is required'; exit 1; fi \
    && if [ -z "${GIT_REVISION}" ]; then echo >&2 'GIT_REVISION build arg is required'; exit 1; fi

WORKDIR /opt/cgprofile
COPY requirements-report.txt ./requirements-report.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/cgprofile/venv \
    && /opt/cgprofile/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/cgprofile/venv/bin/pip install --no-cache-dir -r requirements-report.txt \
    && /opt/cgprofile/venv/bin/python -c "import matplotlib, numpy, pandas, plotly, ruptures, scipy; import plotly.offline"

FROM python:3.14-slim

ARG CGPROFILE_VERSION
ARG GIT_REVISION

# `busctl` is the stdlib-free transport for the one host-systemd placement
# operation. The daemon uses only AttachProcessesToUnit over the explicitly
# mounted system bus; it does not run systemd as PID 1. This image does not
# select namespaces: the managed CIU service alone joins the host PID namespace
# and retains a private cgroup namespace. One-shot helpers keep private PID and
# cgroup namespaces.
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd \
    && rm -rf /var/lib/apt/lists/*

ENV CGPROFILE_VERSION="${CGPROFILE_VERSION}"

LABEL org.opencontainers.image.title="cgprofile" \
      org.opencontainers.image.description="RG-55 cgroup/DAMON profiling daemon (RG55-INTERFACE-CONTRACT.md)" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/scripts/cgroup-profiler" \
      org.opencontainers.image.version="${CGPROFILE_VERSION}" \
      org.opencontainers.image.revision="${GIT_REVISION}"

WORKDIR /opt/cgprofile

COPY --from=builder /opt/cgprofile/venv /opt/cgprofile/venv
COPY pyproject.toml ./pyproject.toml
COPY src/ ./src/
COPY docs/ ./docs/
# The DAMON library (Classifier/KDAMONDS_DIR/SysfsInterface, pure Python) is
# vendored inside the package as cgprofile._damon.damon_analysis since
# cgprofile 2.0 package 1, so it arrives with `src/` above; the named
# `damon_analysis` build context that used to deliver the sibling checkout's
# copy is no longer read by this Dockerfile.
# The bash shim `cgprofile` is gone (package 2); the daemon starts as
# `python3 -m cgprofile.cli.cgpd run` (the `cgpd` console script's module).
RUN find . -name '__pycache__' -type d -prune -exec rm -rf {} +

# `/var/lib/cgprofile/sessions` is the contract's own default (§1.8) and
# the named volume's mount point (see the C7 ciu stack) — created here so
# the daemon's first `os.makedirs(..., exist_ok=True)` has a real
# directory (not a dangling anonymous one) to inherit ownership/perms
# from even before a volume is attached.
RUN mkdir -p /var/lib/cgprofile/sessions

# No EXPOSE: the legacy daemon uses Unix-domain sockets, not a TCP port.
# 2.0: the daemon is `cgpd run` (config file, no flags, no CGPROFILE_* daemon
# environment). This image path is superseded by the native wheel deployment
# (docs/NATIVE-DEPLOYMENT.md); a container run needs cli-extended on the
# image's python path and a mounted /etc/cgprofile/cgpd.toml, which the 1.x
# ciu stack does not provide (backlog CP-54).
ENV PYTHONPATH=/opt/cgprofile/src
ENTRYPOINT ["python3", "-m", "cgprofile.cli.cgpd"]
CMD ["run"]

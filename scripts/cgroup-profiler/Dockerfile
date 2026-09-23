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
#     plotly/etc., built into venv/ from requirements.txt (the pinned
#     lock — see that file's own header) at BUILD time only.
#
# Two stages: `ruptures==1.1.9` (pyproject.toml's pin) ships no prebuilt
# wheel for cp314 yet, so it compiles a C extension at `pip install` time —
# needing gcc, which has no business in the RUNNING daemon's attack surface
# (this image is `--privileged --pid=host --cgroupns=host`; every package
# not needed at runtime is one this daemon does not carry). The builder
# stage installs build-essential and produces venv/; the final stage copies
# only the finished venv, never the compiler that made it.
FROM python:3.14-slim AS builder

WORKDIR /opt/cgprofile
COPY requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/cgprofile/venv \
    && /opt/cgprofile/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/cgprofile/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/cgprofile/venv/bin/python -c "import matplotlib, numpy, pandas, plotly, ruptures, scipy; import plotly.offline"

FROM python:3.14-slim

ARG CGPROFILE_VERSION=1.0.0
ARG GIT_REVISION=unknown

ENV CGPROFILE_VERSION="${CGPROFILE_VERSION}"

LABEL org.opencontainers.image.title="cgprofile" \
      org.opencontainers.image.description="RG-55 cgroup/DAMON profiling daemon (RG55-INTERFACE-CONTRACT.md)" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/scripts/cgroup-profiler" \
      org.opencontainers.image.version="${CGPROFILE_VERSION}" \
      org.opencontainers.image.revision="${GIT_REVISION}"

WORKDIR /opt/cgprofile

COPY --from=builder /opt/cgprofile/venv /opt/cgprofile/venv
COPY cgprofile.py ./cgprofile.py
COPY lib/ ./lib/
COPY docs/ ./docs/
COPY cgprofile ./cgprofile
# lib/damon.py imports Classifier/KDAMONDS_DIR/SysfsInterface from the
# SIBLING `scripts/damon-analysis/lib/damon_analysis.py` (pure Python, no
# `damo` CLI dependency — that module's own docstring explains the split),
# resolved relative to lib/damon.py's own location as `../../damon-analysis/
# lib`. Since the build context here is `scripts/cgroup-profiler/` alone,
# that sibling has to arrive as a SEPARATE named build context (`--build-
# context damon_analysis=../damon-analysis`, wired in docker-bake.hcl) —
# without this, damon_mod.available() is unconditionally False inside the
# image regardless of what the host kernel actually exposes (verified
# live: `/sys/kernel/mm/damon/admin/kdamonds` was writable in the
# container, but `ctl version` reported DAMON unavailable before this
# fix). Landing at /opt/damon-analysis/lib/damon_analysis.py so
# `os.path.join(dirname(damon.py), "..", "..", "damon-analysis", "lib")`
# (damon.py: /opt/cgprofile/lib/damon.py) resolves to it unmodified.
COPY --from=damon_analysis lib/damon_analysis.py /opt/damon-analysis/lib/damon_analysis.py
RUN chmod +x ./cgprofile \
    && find . -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && ln -s /opt/cgprofile/cgprofile /usr/local/bin/cgprofile

# `/var/lib/cgprofile/sessions` is the contract's own default (§1.8) and
# the named volume's mount point (see the C7 ciu stack) — created here so
# the daemon's first `os.makedirs(..., exist_ok=True)` has a real
# directory (not a dangling anonymous one) to inherit ownership/perms
# from even before a volume is attached.
RUN mkdir -p /var/lib/cgprofile/sessions

# No EXPOSE: the control socket is a Unix domain socket
# (/run/cgprofile/ctl.sock, never a TCP port) and the daemon's own
# network_mode is "none" (RG55-INTERFACE-CONTRACT.md §5) — there is
# nothing to publish.
ENTRYPOINT ["cgprofile", "serve"]

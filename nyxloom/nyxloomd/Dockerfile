# nyxloom controller image (P19) — SELF-CONTAINED (2026-07-27).
#
# Base = the mdt devcontainer image (binary-compatible with the host CLIs the
# daemon dispatches). Two stages:
#
#   1. wheelbuild — build the nyxloom wheel FROM SOURCE with a pinned version.
#      setuptools-scm normally derives the version from git tags, but a Docker
#      build context has no .git, so we pass the host-resolved version in as
#      SETUPTOOLS_SCM_PRETEND_VERSION (build ARG NYXLOOM_VERSION). This keeps the
#      build hermetic (no git needed) and lets BOTH `ciu`/compose (local,
#      NYXLOOM_VERSION defaults to 0.0.0-dev) and cmru/bake (release, the real
#      nyxloom-vX.Y.Z resolved by build-push.py) build the same image.
#
#   2. final — the mdt base + a SELF-CONTAINED /opt venv with the nyxloom wheel
#      (and its deps) installed. The daemon runs the INSTALLED package and logs
#      its real version; the ghcr image needs no bind-mounted source to run.
#
# DEV OVERRIDE: the local ciu stack bind-mounts /workspaces/vbpub/nyxloom/src and
# sets PYTHONPATH to it (ciu.compose.yml.j2), so at runtime the mounted source
# SHADOWS the installed package — a daemon code change needs only `docker restart`,
# no image rebuild. The installed wheel is the portable fallback + version source;
# the bind-mount is the fast-iteration path. (A new *dependency* still needs a
# rebuild, same as before.)
#
# The /opt venv (NOT /home/vscode/.venv, NOT /workspaces/vbpub/.venv):
#   * /home/vscode is bind-mounted at runtime (host home) — would shadow a venv there.
#   * /workspaces/vbpub/.venv is ephemeral (observed deleted mid-session 2026-07-16).
#   * `--copies` so bin/python is a REAL interpreter binary, not a symlink back
#     into the shadowed /home/vscode tree.
ARG NYXLOOM_BASE=ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest

# ─── stage 1: build the nyxloom wheel from source (hermetic, no git) ──────────
FROM python:3.14-slim AS wheelbuild
ARG NYXLOOM_VERSION=0.0.0-dev
WORKDIR /build
# Install the builder before copying package sources. The wheel build RUNs below
# then execute only their version assignment and build command.
RUN pip install --no-cache-dir build==1.2.2
# The pinned builder stage starts with no inherited package sources. Clear these
# paths anyway so the wheel input tree is exactly the Docker context below.
RUN rm -rf /build/libraries/cli-extended
# cli-extended is a REAL wheel dependency of nyxloom (CX-D1, NYX-CLIX): its own
# wheel is built here from the monorepo source (the stand-in for the released
# asset, which is on GitHub Releases only, never PyPI) and installed FIRST in
# the final stage. It is NOT vendored into the nyxloom wheel.
# TODO(NL-39): replace this source build + pretend version with the RELEASED
# cli-extended wheel, sha256-verified like tester-unified (CX-D2).
COPY libraries/cli-extended/pyproject.toml libraries/cli-extended/README.md ./libraries/cli-extended/
COPY libraries/cli-extended/src ./libraries/cli-extended/src
# The library resolves its version from its own git tag, which a no-.git context
# lacks. The pretend version must satisfy nyxloom's declared floor
# (cli-extended>=0.4.0); bump this ARG together with that floor.
ARG CLI_EXTENDED_VERSION=0.4.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=${CLI_EXTENDED_VERSION}
WORKDIR /build/libraries/cli-extended
RUN rm -rf /wheels \
 && mkdir -p /wheels \
 && python -m build --wheel --outdir /wheels \
 && ls -1 /wheels
# Copy Nyxloom's wheel inputs after the cli-extended build. No separate RUN may
# modify these source files before Nyxloom itself is built below.
WORKDIR /build
RUN rm -rf /build/nyxloom
COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml
COPY nyxloom/src ./nyxloom/src
WORKDIR /build/nyxloom
# Pretend-version short-circuits setuptools-scm's git lookup (which would fail with
# no .git in the context). Targeted `_FOR_NYXLOOM` form so it can only affect this
# dist, never a transitively-built one.
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NYXLOOM=${NYXLOOM_VERSION}
RUN python -m build --wheel --outdir /wheels \
 && ls -1 /wheels

# ─── stage 2: self-contained runtime image (mdt base + installed wheel) ───────
FROM ${NYXLOOM_BASE}
USER root
RUN rm -rf /opt/nyxloom-venv /tmp/wheels && mkdir -p /tmp/wheels
COPY --from=wheelbuild /wheels/*.whl /tmp/wheels/
RUN python3 -m venv --copies /opt/nyxloom-venv \
 && /opt/nyxloom-venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/wheels/cli_extended-*.whl \
 && /opt/nyxloom-venv/bin/pip install --no-cache-dir /tmp/wheels/nyxloom-*.whl \
 && /opt/nyxloom-venv/bin/python -c "import yaml, jsonschema, structlog, cli_extended, nyxloom; print('nyxloom', nyxloom.__version__, 'self-contained OK')" \
 && rm -rf /tmp/wheels
USER 1003

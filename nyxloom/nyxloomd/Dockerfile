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
WORKDIR /build/nyxloom
# Only the wheel inputs — deliberately NOT the .git dir. The version comes from
# the ARG below, so setuptools-scm never shells out to git.
COPY pyproject.toml ./
COPY src ./src
# Pretend-version short-circuits setuptools-scm's git lookup (which would fail with
# no .git in the context). Targeted `_FOR_NYXLOOM` form so it can only affect this
# dist, never a transitively-built one.
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NYXLOOM=${NYXLOOM_VERSION}
RUN pip install --no-cache-dir build==1.2.2 \
 && python -m build --wheel --outdir /wheels \
 && ls -1 /wheels

# ─── stage 2: self-contained runtime image (mdt base + installed wheel) ───────
FROM ${NYXLOOM_BASE}
USER root
COPY --from=wheelbuild /wheels/*.whl /tmp/wheels/
RUN python3 -m venv --copies /opt/nyxloom-venv \
 && /opt/nyxloom-venv/bin/pip install --no-cache-dir /tmp/wheels/*.whl \
 && /opt/nyxloom-venv/bin/python -c "import yaml, jsonschema, structlog, nyxloom; print('nyxloom', nyxloom.__version__, 'self-contained OK')" \
 && rm -rf /tmp/wheels
USER 1003

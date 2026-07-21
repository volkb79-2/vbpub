# wheel-builder — the single vbpub wheel-build container.
#
# cmru's built-in wheel profile (cmru/src/cmru/handlers.py: cmd_wheel_build) runs
# `python -m build --wheel`. That needs the PyPA `build` frontend package present in
# whatever interpreter invokes it — a repo-root convenience venv used to be pressed
# into that role ad hoc (pip-installing whatever was missing, one gap at a time). This
# image is the dedicated replacement: one purpose-built, stateless place `python -m
# build` runs, so no repo venv needs `build` installed on the fly again.
#
# Stateless tool image: no project source is baked in. The project directory is
# supplied via bind mount at run time (cmd_wheel_build mounts the repo root and runs
# the build against the bind-mounted source), so this image only needs the build
# frontend itself. Reproducibility of the actual wheel bytes comes from each
# project's own pinned `[build-system].requires` (setuptools/wheel/setuptools_scm),
# which `python -m build`'s isolation installs fresh per invocation — this image does
# not need to pin those itself.
#
# Both ciu and cmru declare artifacts = ["wheel"] and fall through the same built-in
# handler — this one image covers both, same as tester-unified covers every
# project's test suite.
#
# Build from the REPO ROOT so the (bind-mounted-at-runtime) project sources would be
# in context if ever needed — today nothing is COPY'd from it, so the build is fast:
#   docker build -f wheel-builder/Dockerfile -t wheel-builder:local /workspaces/vbpub
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest

USER root

# A dedicated /opt venv, not the base image's /home/vscode/.venv: this container runs
# as uid 1003 (see below), a different user than the image's baked-in vscode/1000, and
# /home/vscode's permissions are that user's to control — same reasoning tester-unified
# already documents for its own /opt/tester-venv ("a real interpreter binary, so a
# runtime home bind cannot shadow it").
RUN /usr/local/bin/python3.14 -m venv --copies /opt/wheel-builder-venv \
 && /opt/wheel-builder-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/wheel-builder-venv/bin/pip install --no-cache-dir build

# The container runs as uid 1003 (matches this devcontainer's host-remapped vscode
# user, same reasoning as tester-unified/Dockerfile), which the base image has no
# /etc/passwd entry for and no writable HOME. Provide both, so writes to the
# bind-mounted project's dist/ (owned by the host uid) succeed.
RUN echo "builder:x:1003:994:builder:/home/builder:/bin/bash" >> /etc/passwd \
 && echo "docker:x:994:builder" >> /etc/group \
 && mkdir -p /home/builder/.config /home/builder/.cache /home/builder/.local/state \
 && chown -R 1003:994 /home/builder

USER 1003
ENV HOME=/home/builder \
    XDG_CONFIG_HOME=/home/builder/.config \
    XDG_CACHE_HOME=/home/builder/.cache \
    XDG_STATE_HOME=/home/builder/.local/state

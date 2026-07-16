# handoffctl controller image (P19)
#
# Base = the mdt devcontainer image (binary-compatible with the host CLIs the
# daemon dispatches). handoffctl's only runtime deps (PyYAML, jsonschema) are
# baked into a SELF-CONTAINED venv under /opt:
#   * NOT under /home/vscode — the runtime bind-mounts the host home there,
#     which would shadow the image's own /home/vscode/.venv interpreter.
#   * NOT the external /workspaces/vbpub/.venv — that is ephemeral (observed
#     deleted mid-session 2026-07-16) and must not be a runtime dependency.
#   * `--copies` so bin/python is a REAL interpreter binary, not a symlink back
#     into the shadowed /home/vscode tree.
#
# handoffctl itself is provided at runtime via PYTHONPATH over the bind-mounted
# src (/workspaces/vbpub/handoffctl2/src), so daemon code changes need only a
# container restart — no image rebuild.
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest

USER root
RUN python3 -m venv --copies /opt/handoffctl-venv \
 && /opt/handoffctl-venv/bin/pip install --no-cache-dir "PyYAML>=6" "jsonschema>=4" \
 && /opt/handoffctl-venv/bin/python -c "import yaml, jsonschema; print('handoffctl deps OK')"
USER 1003

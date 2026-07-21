# nyxloom controller image (P19)
#
# Base = the mdt devcontainer image (binary-compatible with the host CLIs the
# daemon dispatches). nyxloom's runtime deps (PyYAML, jsonschema, structlog)
# are baked into a SELF-CONTAINED venv under /opt:
#   * NOT under /home/vscode — the runtime bind-mounts the host home there,
#     which would shadow the image's own /home/vscode/.venv interpreter.
#   * NOT the external /workspaces/vbpub/.venv — that is ephemeral (observed
#     deleted mid-session 2026-07-16) and must not be a runtime dependency.
#   * `--copies` so bin/python is a REAL interpreter binary, not a symlink back
#     into the shadowed /home/vscode tree.
#
# nyxloom itself is provided at runtime via PYTHONPATH over the bind-mounted
# src (/workspaces/vbpub/nyxloom/src), so daemon code changes need only a
# container restart — no image rebuild. A new *dependency* is the exception:
# structlog was added by the logging track (LP01, pyproject `structlog>=24,<27`)
# while the daemon sat stopped, so the image MUST be rebuilt to pick it up —
# a bare restart on the old image crash-loops on `import structlog`.
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest

USER root
RUN python3 -m venv --copies /opt/nyxloom-venv \
 && /opt/nyxloom-venv/bin/pip install --no-cache-dir "PyYAML>=6" "jsonschema>=4" "structlog>=24,<27" \
 && /opt/nyxloom-venv/bin/python -c "import yaml, jsonschema, structlog; print('nyxloom deps OK')"
USER 1003

# tester-unified — the single vbpub gating container.
#
# Cockpit doctrine: the gating suite NEVER runs in the devcontainer (its base
# pins are not a ship signal). dstdns has its `test-runner`; this is the vbpub
# equivalent — one overarching container whose closure is the SUM of every
# vbpub Python project's deps (see requirements.by-stack.txt, grouped per stack).
#
# Base = the mdt devcontainer image: same toolchain as the cockpit, and it
# carries BOTH python 3.13 and 3.14 so a gate can pick either runtime.
#
# The deps live in a COPIES venv under /opt (a real interpreter binary, not a
# symlink) so a bind-mount of the host home at /home/vscode cannot shadow it —
# the same pattern the controller image uses.
FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest

USER root
COPY requirements.by-stack.txt /opt/tester/requirements.by-stack.txt

# Baseline venv on python 3.13 (the current green baseline; python 3.14 exposes
# a test-isolation flake in handoffctl2's concurrency test — tracked separately).
# A 3.14 sibling venv can be added when that flake is fixed.
RUN /usr/bin/python3.13 -m venv --copies /opt/tester-venv \
 && /opt/tester-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/tester-venv/bin/pip install --no-cache-dir -r /opt/tester/requirements.by-stack.txt \
 && /opt/tester-venv/bin/python -c "import pytest, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, paramiko, requests, telethon; print('tester-unified closure OK')"

USER 1003
ENV TESTER_VENV=/opt/tester-venv

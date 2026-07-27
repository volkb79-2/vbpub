# nyxloom agent-CLI session image (D-G5, docs/plan-resource-governance.md)
#
# ONE image carrying all four agent CLIs, reused for every dispatched session.
# Replaces the current model where an agent is a forked PROCESS -- which cannot
# be cgroup-placed at all, and whose only sandbox is whatever the CLI itself
# implements. The container IS the boundary here, which is why the CLIs can be
# run with their own sandboxes relaxed: this session hit a real failure where
# codex emitted BLOCKED because its internal sandbox saw the worktree's `.git`
# as read-only, so it could neither commit nor gate its own work.
#
# NOT a build environment. Gates run in the project's OWN declared gate
# container (canonical L16: nyxloom requires an interface, never mandates
# infra). This image only has to run the agent CLI and talk to git.

# The tag's major MUST match the host toolchain the CLIs were validated on
# (node v26.5.0 / npm 11.17.0 as of 2026-07-27). Pinning the major means a
# mismatch fails the BUILD loudly rather than silently running the CLIs on an
# untested runtime.
FROM node:26-slim

# git          -- agents commit their own work on the task branch
# ca-certs     -- model API TLS
# curl         -- claude's native installer
# ripgrep      -- every one of these CLIs shells out to it for search
# less         -- git pager; without it `git log` can hang a non-tty session
# procps       -- agents inspect their own background jobs
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl ripgrep less procps \
    && rm -rf /var/lib/apt/lists/*

# --- run-uid identity ------------------------------------------------------
# The agent writes into a bind-mounted worktree owned by the host user. A uid
# mismatch produces files the host cannot manage and a git index the agent
# cannot write -- the same class of failure that BLOCKED codex. Pass the real
# host uid/gid at build time; the defaults are only a convenience.
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN if ! getent group "${AGENT_GID}" >/dev/null; then groupadd -g "${AGENT_GID}" agent; fi \
 && if ! getent passwd "${AGENT_UID}" >/dev/null; then \
        useradd -m -u "${AGENT_UID}" -g "${AGENT_GID}" -s /bin/bash agent; \
    fi \
 && install -d -o "${AGENT_UID}" -g "${AGENT_GID}" /home/agent /work

ENV HOME=/home/agent \
    NPM_CONFIG_PREFIX=/home/agent/.local \
    PATH=/home/agent/.local/bin:$PATH

USER ${AGENT_UID}:${AGENT_GID}
WORKDIR /work

# --- the CLIs --------------------------------------------------------------
# Versions pinned deliberately. An agent CLI silently changing major behaviour
# under a running factory is indistinguishable from a model regression, and
# would be attributed to the wrong layer (see D-R2b: capability scoring is only
# meaningful when the harness is held constant).
ARG CODEX_VERSION=0.145.0
ARG REASONIX_VERSION=1.17.12
ARG OPENCODE_VERSION=1.18.1
RUN npm install -g --no-fund --no-audit \
        "@openai/codex@${CODEX_VERSION}" \
        "reasonix@${REASONIX_VERSION}" \
        "opencode-ai@${OPENCODE_VERSION}"

# claude ships its own native installer rather than an npm package.
RUN curl -fsSL https://claude.ai/install.sh | bash

# --- persistent state ------------------------------------------------------
# Auth + session state live in a NAMED VOLUME, never in the worktree. Two
# consequences, both deliberate:
#   1. Subscription logins (claude, codex) survive container replacement, so
#      auth is a one-time INSTALL step, never part of a dispatch path.
#   2. A session can be resumed later against a DIFFERENT worktree, because
#      nothing about the session is stored inside the tree it happened to run
#      against.
# Mount one volume at /home/agent covering every CLI's config dir at once
# (~/.claude, ~/.codex, ~/.config/opencode, ~/.reasonix) rather than four
# fragile per-tool mounts that drift when a CLI moves its config path.
VOLUME ["/home/agent"]

# The task worktree bind-mounts here. Nothing else from the host is visible.
VOLUME ["/work"]

# No ENTRYPOINT: the daemon supplies the full argv (adapters.build_dispatch
# already constructs per-CLI argv, and baking one in here would fight it).
CMD ["bash"]

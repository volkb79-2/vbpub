# srdm's own gate container.
#
# WHY NOT tester-unified: vbpub's shared gate image is a Python 3.14 venv
# closure built from four projects' pyprojects. It has no Go toolchain, it
# runs unprivileged as uid 1003, and it has no systemd. srdm is a Go project
# whose P02+ oracles (per-class hold services, transient units, mount
# topology, cgroup charging) need a privileged systemd-in-Docker harness.
# Adding a Go toolchain to tester-unified would rebuild and re-risk the gate
# of ciu, cmru, topos and nyxloom to serve a project that still could not run
# its own e2e oracles there. So srdm gets its own image, and the four Python
# projects keep theirs untouched.
#
# Cockpit doctrine still holds: this image is the gate, the devcontainer is
# not. "Green in the cockpit" is not a ship signal — and in srdm's case the
# cockpit has no Go toolchain at all, so it cannot even pretend.
#
# Build both targets from THIS directory:
#   docker build -f gate/Dockerfile --target unit -t srdm-gate:unit gate/
#   docker build -f gate/Dockerfile --target e2e  -t srdm-gate:e2e  gate/
#
# Run placement is NOT this file's business: every container must be placed
# on the host tier (see tools/cgroup-parent.sh, which verifies the slice is
# real rather than trusting the name — a typo'd slice fails OPEN).

# ---------------------------------------------------------------- unit ----
FROM golang:1.25 AS unit

# The gate runs as the uid that owns the host-side bind mount, so build
# artifacts and coverage files it writes are readable outside the container.
# A gate must give its run-uid a FULL identity: a passwd entry (os/user and
# anything calling getpwuid needs one), a group, a writable HOME, and XDG
# dirs. Without them suites fail with errors that look like breakage but are
# pure environment.
ARG RUN_UID=1003
ARG RUN_GID=1003

RUN groupadd -g "${RUN_GID}" srdm \
 && useradd -u "${RUN_UID}" -g "${RUN_GID}" -m -d /home/srdm -s /bin/bash srdm \
 && mkdir -p /home/srdm/.config /home/srdm/.cache /home/srdm/.local/state \
 && chown -R "${RUN_UID}:${RUN_GID}" /home/srdm

# Go's build and module caches must be writable by the run-uid, and they must
# NOT live on the bind-mounted worktree: a gate that writes cache into the
# repo pollutes the tree it is meant to be judging.
ENV HOME=/home/srdm \
    XDG_CONFIG_HOME=/home/srdm/.config \
    XDG_CACHE_HOME=/home/srdm/.cache \
    XDG_STATE_HOME=/home/srdm/.local/state \
    GOCACHE=/gocache \
    GOMODCACHE=/gomodcache \
    GOFLAGS=-mod=mod \
    GOTOOLCHAIN=local

# Prewarm the standard library into the shared cache so a gate run compiles
# only srdm. Built as the run-uid so the cache is owned by whoever will use
# it — a root-owned cache is silently unusable and every run pays full
# rebuild cost while looking fine.
RUN mkdir -p "${GOCACHE}" "${GOMODCACHE}" \
 && chown -R "${RUN_UID}:${RUN_GID}" "${GOCACHE}" "${GOMODCACHE}"
USER ${RUN_UID}:${RUN_GID}
RUN go build std

WORKDIR /src

# ----------------------------------------------------------------- e2e ----
# The privileged harness: systemd as PID 1, so the claims that are about the
# kernel and systemd rather than about Go can actually be measured — that a
# Type=oneshot RemainAfterExit=yes unit keeps its cgroup alive and its pages
# charged after ExecStart exits, that unmounting uncharges them, that it
# does NOT while a reference is held.
#
# Mechanics follow the recipe already proven on this host
# (wings-cgroups/v1-legacy/test/e2e-systemd). Notably --cgroupns=PRIVATE,
# not host: systemd wants its own cgroup namespace root, and a private one
# also keeps the probe self-contained rather than measuring against a tree
# other containers are moving.
FROM unit AS e2e
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd \
      systemd-sysv \
      dbus \
      util-linux \
      mount \
      procps \
      coreutils \
 && rm -rf /var/lib/apt/lists/* \
 # Units that make no sense in a container and only add noise to
 # `systemctl is-system-running`, which the readiness loop reads.
 && rm -f /etc/systemd/system/*.wants/* \
          /lib/systemd/system/multi-user.target.wants/getty* 2>/dev/null; \
    :

# The suite mounts tmpfs and drives systemd, so it runs as root here. The Go
# caches are inherited from the unit stage and are root-writable.
ENV HOME=/root

# SIGRTMIN+3 is systemd's shutdown signal; without it `docker stop` waits
# out the full timeout on every teardown.
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]

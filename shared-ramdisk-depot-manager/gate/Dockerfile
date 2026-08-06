# srdm's own gate container.
#
# The Go toolchain half of this file was PROMOTED to `vbpub/tester-unified-go`
# (assay decisions.md A-043) so a second Go project can use it without copying
# it. What is left here is the part that is genuinely srdm's: the privileged
# systemd-in-Docker harness. srdm's `unit` target is now an alias for the
# shared base, so `tools/gate.sh` and `tools/canary-run.sh` keep working
# unchanged and `srdm-gate:unit` still exists.
#
# WHY NOT tester-unified (the Python one): vbpub's shared PYTHON gate image is
# a 3.14 venv closure built from four projects' pyprojects. It has no Go
# toolchain, it runs unprivileged as uid 1003, and it has no systemd. Adding a
# Go toolchain to it would rebuild and re-risk the gate of ciu, cmru, topos and
# nyxloom to serve a project that still could not run its own e2e oracles
# there. Hence a SIBLING image, `tester-unified-go`, and this file on top.
#
# Cockpit doctrine still holds: this image is the gate, the devcontainer is
# not. "Green in the cockpit" is not a ship signal — and in srdm's case the
# cockpit has no Go toolchain at all, so it cannot even pretend.
#
# The base must exist before either target builds. `tools/gate.sh` builds it
# on demand; by hand:
#   docker build -f ../tester-unified-go/Dockerfile \
#       -t tester-unified-go:local ../tester-unified-go
#   docker build -f gate/Dockerfile --target unit -t srdm-gate:unit gate/
#   docker build -f gate/Dockerfile --target e2e  -t srdm-gate:e2e  gate/
#
# Run placement is NOT this file's business: every container must be placed
# on the host tier (see tools/cgroup-parent.sh, which verifies the slice is
# real rather than trusting the name — a typo'd slice fails OPEN).

# ---------------------------------------------------------------- unit ----
# An alias for the shared Go gate image, kept as a named target so every
# existing `--target unit` invocation and the `srdm-gate:unit` tag survive the
# promotion. Everything that used to live here — the run-uid's full identity,
# the caches held off the bind mount, the prewarmed std library, GOTOOLCHAIN
# pinning — is now in the base and is documented there.
ARG BASE_IMAGE=tester-unified-go:local
FROM ${BASE_IMAGE} AS unit

# ----------------------------------------------------------------- e2e ----
# The privileged harness: systemd as PID 1, so the claims that are about the
# kernel and systemd rather than about Go can actually be measured — that a
# Type=oneshot RemainAfterExit=yes unit keeps its cgroup alive and its pages
# charged after ExecStart exits, that unmounting uncharges them, that it
# does NOT while a reference is held.
#
# This is the part that is genuinely srdm's and the reason srdm keeps its own
# image at all: it is why a Go toolchain in tester-unified would not have been
# enough. Mechanics follow the recipe already proven on this host
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
# caches are inherited from the base image and are root-writable.
ENV HOME=/root

# SIGRTMIN+3 is systemd's shutdown signal; without it `docker stop` waits
# out the full timeout on every teardown.
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]

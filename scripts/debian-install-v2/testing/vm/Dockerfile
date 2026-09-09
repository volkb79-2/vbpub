# QEMU/TCG test-VM harness runner -- deliberately a PLAIN, unprivileged
# image, not a variant of ../Dockerfile's privileged systemd container.
#
# Why separate: QEMU's own software CPU emulation (-accel tcg) needs no
# host privilege at all -- no loop devices, no /dev/kvm, no real devtmpfs,
# none of what the sibling privileged image exists for. qemu-system-x86_64
# reads/writes qcow2 files directly in userspace and boots the GUEST's own,
# entirely separate kernel. Coupling this harness to the privileged image
# would only make it depend on that image's own (separate, sometimes
# broken) container-startup requirements for no reason -- confirmed
# 2026-09-09: this exact package set installs and runs a full VM boot
# end-to-end in a bare `docker run debian:trixie-slim` with no --privileged,
# no --cgroupns tricks, nothing beyond a plain container.
#
# This is also, deliberately, the ONLY place in this whole estate that may
# ever run debian_install_v2's real add-swap --commit / swapon path for
# real: Linux does not namespace loop devices or swap at all (see
# cgroupns-host-loop-device-host-hang-incident.md) -- a --privileged
# container sharing the HOST kernel can never safely run that path, but a
# QEMU guest has its own, genuinely separate kernel, so swap activated
# inside it is the GUEST's own swap, never the host's.
#
# apt-cacher-ng runs inside this same container (vmctl starts it lazily,
# see ensure_apt_cache()) so repeated installer runs across CoW VM resets
# don't re-fetch the same plain-HTTP debian.org/security.debian.org
# packages from the real internet every time -- see vmctl's own comment
# for why this does NOT cover Docker's HTTPS-only apt repo.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        qemu-system-x86 qemu-utils \
        cloud-image-utils \
        openssh-client rsync socat \
        wget ca-certificates \
        apt-cacher-ng \
        procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

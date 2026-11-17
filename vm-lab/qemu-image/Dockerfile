# The QEMU image of vm-lab guests: a PLAIN, unprivileged Debian trixie-slim with QEMU's software CPU emulation (TCG), no KVM,
# no loop devices, nothing that needs --privileged. Built by `vm-lab image build` (default tag vm-lab-qemu:local).
#
# Same package set and the same properties as the debian-install-v2 test-VM image
# (scripts/debian-install-v2/testing/vm/Dockerfile), minus what only that harness uses (cloud-image-utils, ssh, rsync,
# socat, wget, apt-cacher-ng). `vm-lab start` runs the container with the caller's `--user UID:GID`, so there is no
# USER here; UEFI firmware (OVMF) is NOT in the image: the caller passes its own host files to `start` and they are
# bind-mounted read-only. The container script needs only qemu-system-x86_64 and qemu-img; procps is kept for the
# same reason as in the harness image (ps/pgrep when a guest has to be inspected from inside).
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        qemu-system-x86 qemu-utils \
        procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

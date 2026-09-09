# Privileged, systemd-as-PID1 test environment for
# debian_install_v2/inuse_partition_editor.py -- AND, via the qemu-system-x86
# packages below, the boot-level test harness in run-boot-test.sh (real
# initramfs execution, which no container -- privileged or not -- can ever
# exercise, since a container shares the host kernel and never actually
# boots one: no bootloader, no initramfs unpack/execute, no switch_root).
#
# Why this exists instead of adapting tester-unified (vbpub's shared gate
# image): tester-unified runs as an unprivileged uid (1003), has no systemd,
# is not built with --privileged/cgroup access, and doesn't even COPY
# scripts/debian-install into its build context — every one of those would
# have to change to run this tool's real --commit path. That's exactly the
# "needs adaptation" case, so per this repo's own doctrine this gets its own
# small, disposable image instead of bending a shared one.
#
# What only THIS image can test: inuse_partition_editor.py's whole point is
# editing a partition table on a disk that is currently in use — the real
# --commit path needs root, a loop device with kernel partition scanning
# (`losetup --partscan`), and `partx`/`udevadm settle` behaving as they do on
# a real host. Real systemd-as-PID1 (not a bare --privileged shell) also lets
# `systemd-analyze verify` and an actual `systemctl start` of the generated
# stage2 unit be exercised the same way production reboots into it. The same
# loop-device access is reused by run-boot-test.sh to customize a downloaded
# base cloud image (mount its root partition, install the real generated
# initramfs hook, `update-initramfs -u`) before handing it to QEMU.
#
# Nothing here ever touches a host block device: all test images are
# sparse files created inside the container's own tmpfs (/tmp), discarded
# with the container. See README.md in this directory for the run recipe;
# run-privileged-tests.sh drives build + boot + exec + teardown as one step;
# run-boot-test.sh drives the QEMU boot-level harness.
FROM debian:trixie-slim

# sfdisk/cfdisk moved out of util-linux into the separate `fdisk` package on
# trixie — util-linux alone gives you losetup/partx/mkswap/swapon but not
# sfdisk, which this tool is built entirely on top of.
#
# qemu-system-x86 + qemu-utils: the boot-level harness. No KVM anywhere in
# this estate's own infrastructure (confirmed 2026-09-08: this devcontainer's
# own host VM has no /dev/kvm and reports zero vmx/svm cpuinfo flags -- the
# hosting provider doesn't expose nested virtualization) -- TCG software
# emulation only (`-accel tcg`), which needs no /dev/kvm and no extra
# container privilege beyond what this image already has for loop devices.
# wget/ca-certificates: downloading the official Debian cloud base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv udev util-linux fdisk \
        python3 python3-pytest python3-pytest-cov \
        qemu-system-x86 qemu-utils \
        wget ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Standard "systemd in a container" trims: these units assume a real
# console/network stack this container never has, and hang readiness
# (`systemctl is-system-running --wait`) waiting for them otherwise.
RUN systemctl mask \
        getty.target console-getty.service systemd-networkd.service \
        systemd-resolved.service systemd-remount-fs.service \
    || true

# Docker's default /dev is a fixed tmpfs snapshot (null/zero/random/tty/...);
# it never gains new device nodes as the kernel creates them, so
# losetup/partx fail with "device node is lost" the moment they need a
# /dev/loopN or /dev/loopNpM that wasn't in that original snapshot. A real
# devtmpfs auto-creates nodes for every device the kernel knows about
# (loop-control-allocated loops, partx-added partitions, ...) with no udev
# involvement needed for the node itself — exactly what a disk-editing tool
# needs. --privileged supplies the CAP_SYS_ADMIN this mount needs.
COPY testing/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Tells systemd it's PID 1 inside a container so it skips host-only early
# setup (e.g. it otherwise intermittently exits 255 within milliseconds --
# before it has initialized any logging target, so `docker logs` shows
# nothing at all -- confirmed 2026-09-09 by bisecting flags/flakiness until
# only this was left unexplained). Standard requirement for systemd-as-PID1
# in any container runtime, not specific to this image.
ENV container=docker

STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

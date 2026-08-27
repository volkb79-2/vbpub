#!/bin/bash
# Replace Docker's fixed /dev tmpfs snapshot with a real devtmpfs before
# handing off to systemd, so device nodes for anything the kernel creates
# later (loop devices, partx-added partitions) actually appear. See the
# comment above this COPY in the Dockerfile for why.
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
exec /lib/systemd/systemd

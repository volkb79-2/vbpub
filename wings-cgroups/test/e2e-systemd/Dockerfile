# e2e image: systemd as PID 1 + its own dockerd + the slice-manager binary.
# Build context = the wings-cgroups project root (run-e2e.sh handles this).
# Runs ONLY as a privileged container; see run-e2e.sh.

FROM golang:1.25 AS wsm-build
WORKDIR /build
COPY t3a-slice-manager/ .
# The slice-manager may not exist yet when only rungs 1-2 are wanted; build
# best-effort so the image remains usable without it.
RUN CGO_ENABLED=0 go build -o /out/wings-slice-manager ./cmd/wings-slice-manager 2>/dev/null \
    || (mkdir -p /out && touch /out/.no-slice-manager)

FROM debian:13-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv dbus docker.io docker-cli ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    # Trim units that make no sense in a test container.
    && rm -f /etc/systemd/system/*.wants/* \
              /lib/systemd/system/multi-user.target.wants/getty* 2>/dev/null; \
    systemctl enable docker dbus

# Inner dockerd: systemd cgroup driver (the whole point) + vfs storage (nested
# overlay is unreliable inside a privileged container).
RUN mkdir -p /etc/docker && printf '%s\n' \
    '{ "exec-opts": ["native.cgroupdriver=systemd"], "storage-driver": "vfs" }' \
    > /etc/docker/daemon.json

COPY --from=wsm-build /out/ /usr/local/bin/
COPY test/e2e-systemd/inner-test.sh /usr/local/bin/inner-test.sh
RUN chmod +x /usr/local/bin/inner-test.sh

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]

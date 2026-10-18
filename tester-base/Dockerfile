# tester-base — the minimal base image under every vbpub gate container
# (tester-unified, wheel-builder, run-gate `host` environment).
#
# WHY IT EXISTS (docs/DESIGN-GUIDE.md): tester-unified used to be built FROM the 8 GB
# MDT developer cockpit image and used only Python 3.14, git and the docker client of
# it. The cockpit's floating `latest` changed the gate environment without a commit,
# and its baked release CLIs (ciu, cmru, nyxloom, topos) sat on PATH where they could
# shadow the code under test. This image keeps the official python image and adds
# exactly what the gates execute (survey, 2026-10-18): the docker client, minisign,
# and shellcheck. Nothing else - the negative list is asserted by the `contract` lane.
#
# BUILDS FROM THIS REPOSITORY ALONE (T3): `docker build -t tester-base:local tester-base/`.
# Inputs are public upstream only: the python digest below, Debian apt, Docker's apt
# repository (key fingerprint verified), and the shellcheck release (sha256 verified).
#
# BASE PIN (T1, freshness rule: the digest must be >= 14 days old when adopted).
#   tag      python:3.14-trixie (NOT -slim: gcc/g++ stay for ruptures, which has no
#            cp314 wheel and builds from sdist; git, curl, openssh-client come with it)
#   digest   the INDEX digest the tag pointed to from 2026-09-19 until 2026-10-11
#            (== the tag python:3.14.7-trixie); the tag's newest digest
#            (sha256:446efb42..., 3.14.8, 2026-10-11) was 7 days old on 2026-10-18 and
#            therefore not eligible.
#   created  2026-09-19T03:34:51Z (linux/amd64 image config `created`, digest
#            sha256:608322fe51a9b889eee5ed6015c854e77dc44d00c116435f56b69bf6f14c88f4),
#            29 days before adoption.
#   how read Docker Hub API  GET hub.docker.com/v2/repositories/library/python/tags
#            (names + digests + last_updated), then the registry v2 index -> amd64
#            manifest -> config blob for `created`.
FROM python:3.14-trixie@sha256:0876e54cf728d89fd9d0fdaf5837b9ee879ea5fbbd6fd0cddbe5eb0cce3f5f9e

# Docker's official apt repository signing key. Verified by fingerprint, never trusted
# because it downloaded over TLS alone.
ARG DOCKER_APT_KEY_FPR=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
# shellcheck: the version the MDT image shipped when this base was cut (0.11.0), pinned
# by the sha256 of the upstream release asset (linux.x86_64 / linux.aarch64).
ARG SHELLCHECK_VERSION=0.11.0
ARG SHELLCHECK_SHA256_X86_64=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
ARG SHELLCHECK_SHA256_AARCH64=12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588

SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

# docker-ce-cli WITHOUT its recommended buildx/compose plugins (T2: not wanted).
# minisign from Debian. Both in one layer with the apt-list cleanup.
RUN install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.asc; \
    actual="$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: '/^fpr:/ {print $10; exit}')"; \
    test "${actual}" = "${DOCKER_APT_KEY_FPR}" \
      || { echo "docker apt key fingerprint ${actual} != ${DOCKER_APT_KEY_FPR}" >&2; exit 1; }; \
    gpg --dearmor < /tmp/docker.asc > /etc/apt/keyrings/docker.gpg; \
    chmod 0644 /etc/apt/keyrings/docker.gpg; \
    . /etc/os-release; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli minisign; \
    rm -rf /var/lib/apt/lists/* /tmp/docker.asc

RUN case "$(dpkg --print-architecture)" in \
      amd64) arch=x86_64; sum="${SHELLCHECK_SHA256_X86_64}" ;; \
      arm64) arch=aarch64; sum="${SHELLCHECK_SHA256_AARCH64}" ;; \
      *) echo "unsupported architecture" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/shellcheck.tar.xz \
      "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.${arch}.tar.xz"; \
    echo "${sum}  /tmp/shellcheck.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/shellcheck.tar.xz -C /tmp; \
    install -m 0755 "/tmp/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck; \
    rm -rf /tmp/shellcheck.tar.xz "/tmp/shellcheck-v${SHELLCHECK_VERSION}"; \
    shellcheck --version | grep -qx "version: ${SHELLCHECK_VERSION}"

SHELL ["/bin/sh", "-c"]

# python:3.14-trixie already sets LANG=C.UTF-8; no en_US locale (T2).
ENV LANG=C.UTF-8
# This base runs as ROOT (it creates no user, T2) and run-gate's `host` environment bind-mounts the
# judged worktree into it: any `python -m py_compile` there would leave root-owned __pycache__
# directories in the worktree that the uid-1003 gate lanes cannot then write (measured in the
# MDTC-02 T8 run: compileall PermissionError in the next lane). Redirect bytecode out of the
# worktree. Consumers that run as a real user (tester-unified) reset this to empty = unset.
ENV PYTHONPYCACHEPREFIX=/tmp/pycache

LABEL org.opencontainers.image.title="tester-base" \
      org.opencontainers.image.description="Minimal gate base: python 3.14 + docker client + minisign + shellcheck" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub"

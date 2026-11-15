# tester-base

The minimal base image under every vbpub gate container. It is the official
`python:3.14-trixie` image (digest-pinned) plus the tools used by vbpub gates.
On x86-64 it also includes CIU's KSM preload library for Run-Gate's
ephemeral-lane opt-in:

| Added | Why a gate needs it |
|---|---|
| `docker-ce-cli` (no buildx, no compose) | `cmru tester-gate`, assay's SQL qualification, ciu's enroll tests drive the host Docker daemon |
| `minisign` | bundle-manifest signing tests in cmru and the ciu installer |
| `shellcheck` (pinned release, sha256-verified) | host-setup render test, assay distribution and SQL-qualification gates |
| `/usr/local/lib/ksm-optin.so` (amd64) | silent libc-free CIU KSM shim; Run-Gate opts ephemeral lane processes in through the `org.vbpub.tester.ksm-optin` image label |

The shim writes no process output whether the kernel accepts or rejects its
KSM request, and has no verbose-banner setting. See the
[KSM helper contract](docs/CONSUMERS.md#ksm-helper-contract) and
[design rationale](docs/DESIGN-GUIDE.md#decisions).

It keeps `git` 2.47, `curl`, `gcc`/`g++` (the cgprofile `ruptures` extra builds from
sdist), `openssh-client`, `procps`, `util-linux`, `xz`, `tar` and `bash` from the python image.
It deliberately has no AI CLI, node, PHP, zsh, nvim, systemd shim, `/home/vscode`, user,
virtualenv, crane, regctl, buildx, iproute2, systemd-tmpfiles or fdisk; the `contract` lane
asserts that list is absent.

Consumers: `tester-unified` (`ARG TESTER_UNIFIED_BASE_IMAGE` + `FROM ${TESTER_UNIFIED_BASE_IMAGE}`; the
name is a published interface that `build-image.sh` and Run-Gate's release preparation parse),
`wheel-builder` (`ARG WHEEL_BUILDER_BASE_IMAGE`; both default to `tester-base:local`) and run-gate's
`host` environment (`run-gate.root.toml`). Consumers add their own userland on top (tester-unified:
zsh, node, the real `systemctl` client, Docker's pinned `docker-compose-plugin`); the `contract` lane asserts the
negative list for tester-base only.

```bash
TESTER_BASE_SOURCE_HASH="$(cd tester-base && sha256sum Dockerfile ksm-optin.c | sha256sum | cut -d' ' -f1)"
docker buildx build --load --build-arg "TESTER_BASE_SOURCE_HASH=$TESTER_BASE_SOURCE_HASH" \
  -t tester-base:local tester-base/
TESTER_BASE_IMAGE=tester-base:local run-gate contract      # the registered gate
```

Run-Gate uses the shim only for ephemeral lanes and supports `[defaults].ksm`,
lane `ksm`, `--ksm` / `--no-ksm`, and `RUN_GATE_KSM=0|1`. The current
raw-syscall shim supports x86-64 only. ARM64 images keep their existing tools
and omit the KSM label; Run-Gate prints its missing-label notice and continues
without injection. A measured probe merged about 359 MiB of identical
anonymous pages; three pytest workers shared about 1 MiB from roughly 390 MiB
and had negative net profit. Lane authors can opt out with `ksm = false` or
`--no-ksm`. See the [KSM consumer contract](docs/CONSUMERS.md#ksm-helper-contract).

Why it exists and why it contains only this: [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).
How to adopt it, including the bootstrap order: [docs/CONSUMERS.md](docs/CONSUMERS.md).

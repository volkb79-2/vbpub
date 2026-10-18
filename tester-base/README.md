# tester-base

The minimal base image under every vbpub gate container. It is the official
`python:3.14-trixie` image (digest-pinned) plus exactly the three things the gates
execute and the python image does not ship:

| Added | Why a gate needs it |
|---|---|
| `docker-ce-cli` (no buildx, no compose) | `cmru tester-gate`, assay's SQL qualification, ciu's enroll tests drive the host Docker daemon |
| `minisign` | bundle-manifest signing tests in cmru and the ciu installer |
| `shellcheck` (pinned release, sha256-verified) | host-setup render test, assay distribution and SQL-qualification gates |

It keeps `git` 2.47, `curl`, `gcc`/`g++` (the cgroup-profiler `ruptures` extra builds from
sdist), `openssh-client`, `procps`, `util-linux`, `xz`, `tar` and `bash` from the python image.
It deliberately has no AI CLI, node, PHP, zsh, nvim, systemd shim, `/home/vscode`, user,
virtualenv, crane, regctl, buildx, iproute2, systemd-tmpfiles or fdisk; the `contract` lane
asserts that list is absent.

Consumers: `tester-unified` and `wheel-builder` (`ARG TESTER_BASE` + `FROM ${TESTER_BASE}`) and
run-gate's `host` environment (`run-gate.root.toml`).

```bash
docker build -t tester-base:local tester-base/                 # public upstream inputs only
TESTER_BASE_IMAGE=tester-base:local ./run-gate.py contract      # the registered gate
```

Why it exists and why it contains only this: [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).
How to adopt it, including the bootstrap order: [docs/CONSUMERS.md](docs/CONSUMERS.md).

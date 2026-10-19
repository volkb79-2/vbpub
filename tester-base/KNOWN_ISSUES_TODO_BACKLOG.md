# tester-base: known issues and backlog

## TB-01 - unpinned Debian `docker-compose` on top of tester-base: CLOSED 2026-10-19 (INT-01 D-006)

Closed by tester-unified installing Docker's own `docker-compose-plugin` at an exact version with a pinned
sha256 (never Debian's `docker-compose`); a test refuses an unpinned or Debian compose install. See
tester-unified/docs/DESIGN-GUIDE.md "Userland added on top of the base".

## TB-02 - the release graph has no tester-base gate-prerequisite edge (INT-01 A-12, 2026-10-19)

`cmru.orchestration.toml` lists `tester-base <- none` and nothing depends on it, although every gate image is
`FROM tester-base`. Harmless while the default is the local tag. When the defaults switch to the published
digest (tester-base/docs/CONSUMERS.md "After the first release"), the release order must put tester-base
before run-gate-project and cmru; add the edge with that switch.

## TB-03 - a dedicated cgroup-probe image would let tester-unified drop systemd (INT-01 D-004, 2026-10-19)

tester-unified keeps the real `systemctl` client (Debian's `systemd` package) because the cgroup-profiler
placement probe (`scripts/cgroup-profiler/tools/gate.sh`, `lib/access.py` `_PLACEMENT_PROBE`) runs
`systemctl show <slice>` inside the tester image against the host system bus. Decision: keep it for now. The
alternative is a small dedicated probe image carrying only the systemctl/busctl client, used by the
cgroup probes (gate.sh, access.py, shared-ramdisk cgroup-parent.sh), after which tester-unified could drop
`systemd` (and the `systemd-tmpfiles` it puts on PATH). Needs a decision on who builds and pins that image.

## TB-04 - the tester-base source hash must cover every build input (INT-01 A-20, 2026-10-19)

`org.vbpub.tester-base.source-hash` is the sha256 of `tester-base/Dockerfile`, justified as its "only build
input" (no COPY, ADD, mount or build context today). Nothing asserts that: a future `COPY`, a
`--mount=type=bind` or a `--build-context` would change the image without changing the hash, and the stale
local tag would be reused. Fix: a test that fails when the Dockerfile contains COPY, ADD, `--mount` or
`--build-context` unless the hash is widened to cover it (for example the sorted `git ls-tree -r HEAD
tester-base` of the context files). The hash is also only *source*-stale: unpinned apt content (minisign)
fetched at build time is not captured; the DESIGN-GUIDE wording should say so.

## TB-05 - verify the compose .deb pins against Docker's signed index (INT-01 A-21, 2026-10-19)

The per-architecture sha256s of the `docker-compose-plugin` 5.6.0 packages in `tester-unified/Dockerfile` are of
the files downloaded over TLS at pin time; they were not compared with anything signed, so a tampered first
download would be pinned. Cheap one-time fix at the next bump: compare each sum with the `SHA256:` field of the
`docker-compose-plugin` stanza in `https://download.docker.com/linux/debian/dists/trixie/stable/binary-<arch>/Packages`
(signed through InRelease) and record that in the Dockerfile comment; or, on tester-base (which keeps Docker's apt
source and key), install `docker-compose-plugin=<exact version>` with apt for signature verification and keep the
pinned `.deb` only for bases without that source. The cmru side of the freshness gap is KI-85.

## TB-06 - the compose .deb on the upstream bootstrap base is never built for real (INT-01 B-12, 2026-10-19)

Only the tester-base path was built (`tester-unified:int01`, `Docker Compose version v5.6.0`); the MDT and upstream
Python bases are covered by shim tests. The package's control data lists only `Recommends: docker-buildx-plugin`
(`dpkg-deb -f`: no `Depends`, so `dpkg -i` next to Debian's `docker-cli` should not fail), but one real build with
`--base-image mcr.microsoft.com/devcontainers/python:3.14-trixie` would confirm the documented zero-release path.

## TB-07 - build-image.sh classifies base references by the presence of a slash (INT-01 A-23, 2026-10-19)

A Docker Hub library reference without a slash (`python:3.14-trixie`) is treated as registry-less and refused rather than
pulled; a local-only tag that contains a slash (`myorg/base:local`) would be pulled from Docker Hub if absent. Both fail
closed or pull an explicitly named input. Better: classify by a registry host component, or take an explicit
`--pull-base` flag. Owner: `tester-unified/build-image.sh`.


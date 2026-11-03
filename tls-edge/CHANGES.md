# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Changed
- fix(calls): `[tls_edge.calls]` now defaults to `enabled = true, public_port = 8443` (was off / 8444), agreeing with the Mattermost project's default media port. New test `tests/test_calls_defaults_agree.py`.
- BREAKING: the alternate HTTPS port (9443, entrypoint `websecure-alt`, `https_alt`) is removed. Routing is by host name only; the public ports are exactly 80/tcp, 443/tcp and 8443 tcp+udp (Calls TCP and UDP both on 8443). Port 80 now defaults ON (`--disable-port-80`).
- feat: the release is an offline installer instance: `get.py install --variant py313|py314` builds a hash-locked venv from the bundled ciu and dependency wheels and creates `/srv/tls-edge-instance/stack` (stable ciu root, `./ciu` stub, `consumer.env`, seeded `ciu-stack/ciu.toml.j2` from the wizard answers). One bundle per interpreter (`tls-edge-v<ver>-py313.tar.xz`, `-py314`). See `docs/INSTALL.md`.
- BREAKING (ciu deployments): container names are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy` (alias `traefik` kept); the plain-compose render keeps `edge-traefik`. New site switches `ingress_external` and `tls_edge.acme.volume_name` adopt an existing network and ACME volume (migration without re-issuance).
- docs: release intent is explicit: this release is **3.0.0** (two breaking changes above, committed without a `!` marker): `cmru release tls-edge --set-version 3.0.0` (docs/INSTALL.md, "Releasing tls-edge"). Releases are unsigned (SHA-256 + in-bundle manifest only); the trust model and the get.py pinning advice are documented there.
- test: the installer test fails (not skips) when cmru is not importable (`TLS_EDGE_ALLOW_INSTALLER_SKIP=1` opts out); new tamper cases (bad sidecar, modified/unlisted file, replaced wheel) and the `--overwrite-local` case.
- feat: `run-gate.toml` with lane `tests` (tester-unified) and `requirements-test.txt`; `scripts/stage-wheels.py --ciu-version` / `TLS_EDGE_CIU_VERSION` pins the bundled ciu.
- Consumer examples reduced to A (web), D (guarded) and E (media TCP+UDP) with `consumer-examples/README.md`; tests render and lint every example offline.

<!-- cmru: release history -->

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)

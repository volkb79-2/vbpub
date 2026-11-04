# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

<!-- cmru: release history -->

## [3.0.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=9278e19d2cb75ff664bae4074722548af555c74b -->

### Added
- feat(tls-edge): run-gate.toml lane tests (tester-unified) and requirements-test.txt (794478658)
- feat(cmru): tarball-publish --variant publishes a multi-variant release all-or-nothing (KI-115); tls-edge uses it (489bdb8c8)
- feat(tls-edge): installer packaging - bundled ciu wheels per interpreter, stable stack root, wizard, adapter; file CIU-163 and KI-115 (c247cc5cb)
- feat(tls-edge): tested consumer examples (web, media tcp+udp, guarded), render/lint tests, opt-in Traefik config validation (b43abdf55)
- feat(tls-edge): host-name routing only - drop the alternate HTTPS port, publish 80/443/8443; standalone ciu root; adopt-existing network/volume switches (848245f68)
- feat(cmru): W1-INSTALLER finish: INS-18, rollback message, signature docs, authoring guide, plant table, report (c664861b3)
- feat(cmru,ciu): installer extensions mechanism; move get.py enroll into ciu (W1-CIU-ENROLL, O4) (3aada2d08)
- feat(cmru): authenticate transactional Git operations (7c2d7a8be)

### Fixed
- fix(tls-edge): stage-wheels can pin the bundled ciu and logs its sha256 (7c733a7aa)
- fix(tls-edge): Calls on by default at 8443 TCP+UDP, alt HTTPS default 9443; add Calls defaults agreement tests (5cbeef22c)
- fix(tls-edge): re-render get.py with the non-over-claiming 404 message (C-03a) (89f001b17)
- fix(tls-edge): re-render get.py with the clearer missing-asset refusal (R6-03) (953914481)
- fix(tls-edge): re-render get.py from the template without the legacy-name fallback (PWMCP-08) (85060ee95)
- fix(tls-edge): re-render get.py from the updated cmru installer template (KI-89 fallback) (5d91d11a4)
- fix(cmru,ciu): review fixes for W1-CIU-ENROLL (checker gaps, hard-fail drift guard, CIU-131) (2ac2fe2ea)

### Changed
- Merge branch 'fix-cmruki115-20261104c' (5979e3e3c)
- chore(tls-edge): bump docker-socket-proxy v0.4.2 -> v0.5.0 (f087d8c1d)
- Merge branch 'vss-20261103_175855-voice-screen-share' (f80080ed5)
- refactor: move mattermost-server/ to voice-screen-share/mattermost-server/ and update references (24c216156)
- chore(ciu,tls-edge): re-render the committed get.py with the new template; point refs at cmru installer render (e28bc5015)
- backlog: file A-06 (Calls defaults), A-11/A-12 (tester-base TB-01/TB-02), B-06 (MDT-015) from INT-01 review (9eaf6f960)
- Enable Mattermost Calls through tls-edge (93c6a39aa)
- W1-INSTALLER round2: re-render tls-edge/get.py for the manifest key grammar (a96905ff7)
- W1-INSTALLER round1: bundle-manifest verb (--name), SPEC inventory, REPORT section (9c519aded)
- W1-INSTALLER round1: template/producer fixes (WIP, tests next) (7193017cf)
- wip(cmru-w1-installer): generic get.py installer rewrite (fail-closed, per-release dirs, rollback, offline hash-locked wheels, signature policy); checkpoint (c15d5d873)

### Documentation
- docs(tls-edge): explicit 3.0.0 release intent, unsigned-release trust model, tag-pinned get.py, release notes (78c6a6511)
- docs(tls-edge): INSTALL.md with the 8-step migration plan, consumer-examples README, 80/443/8443 everywhere; cut examples b and c, drop wrapper.sh; instance gitignore covers ciu artifacts (76a7d7329)
- docs: formalize version target schema and backlog name (99d460ec1)
- docs(pwmcp): centralize modernization and disable legacy SSE (afe9f7a9b)

### Testing
- test(tls-edge): installer test fails instead of skipping; tamper and --overwrite-local cases (85c700e66)
- test(tls-edge): offline installer test - install, update, rollback, adapter validation against the committed get.py (58b8a8b89)

<!-- cmru: pending release notes begin -->
### Changed
- chore(deps): pinned `tecnativa/docker-socket-proxy` v0.4.2 -> v0.5.0 (haproxy 3.2.4 -> 3.4.2 base, optional `ALLOW_PAUSE`/`ALLOW_UNPAUSE` and `BIND_CONFIG` override; no default or env-name changes, tls-edge config unaffected). Multi-arch index digest sha256:1f5038b54f06c3e18422902cf00ba21803d1c97805aae032e5e6673d532d3459; images stay tag-pinned like traefik.
- fix(calls): `[tls_edge.calls]` now defaults to `enabled = true, public_port = 8443` (was off / 8444), agreeing with the Mattermost project's default media port. New test `tests/test_calls_defaults_agree.py`.
- BREAKING: the alternate HTTPS port (9443, entrypoint `websecure-alt`, `https_alt`) is removed. Routing is by host name only; the public ports are exactly 80/tcp, 443/tcp and 8443 tcp+udp (Calls TCP and UDP both on 8443). Port 80 now defaults ON (`--disable-port-80`).
- feat: the release is an offline installer instance: `get.py install --variant py313|py314` builds a hash-locked venv from the bundled ciu and dependency wheels and creates `/srv/tls-edge-instance/stack` (stable ciu root, `./ciu` stub, `consumer.env`, seeded `ciu-stack/ciu.toml.j2` from the wizard answers). One bundle per interpreter (`tls-edge-v<ver>-py313.tar.xz`, `-py314`). See `docs/INSTALL.md`.
- BREAKING (ciu deployments): container names are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy` (alias `traefik` kept); the plain-compose render keeps `edge-traefik`. New site switches `ingress_external` and `tls_edge.acme.volume_name` adopt an existing network and ACME volume (migration without re-issuance).
- docs: release intent is explicit: this release is **3.0.0** (two breaking changes above, committed without a `!` marker): `cmru release tls-edge --set-version 3.0.0` (docs/INSTALL.md, "Releasing tls-edge"). Releases are unsigned (SHA-256 + in-bundle manifest only); the trust model and the get.py pinning advice are documented there.
- test: the installer test fails (not skips) when cmru is not importable (`TLS_EDGE_ALLOW_INSTALLER_SKIP=1` opts out); new tamper cases (bad sidecar, modified/unlisted file, replaced wheel) and the `--overwrite-local` case.
- feat: `run-gate.toml` with lane `tests` (tester-unified) and `requirements-test.txt`; `scripts/stage-wheels.py --ciu-version` / `TLS_EDGE_CIU_VERSION` pins the bundled ciu.
- fix(release): the per-interpreter bundles publish through `cmru handler tarball-publish --variant` (all variants under one tag, or none) instead of the project script `scripts/publish-variants.py`, which refused every retained build-output release; the standalone `get.py` is now a release asset beside the bundles (cmru KI-115).
- Consumer examples reduced to A (web), D (guarded) and E (media TCP+UDP) with `consumer-examples/README.md`; tests render and lint every example offline.
<!-- cmru: pending release notes end -->

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)

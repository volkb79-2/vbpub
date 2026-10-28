# Changes

All notable changes to this project are recorded here.

<!-- cmru: release history -->

## [0.1.0] - 2026-10-28
<!-- cmru: generated -->
<!-- cmru: source-end=f093aed2e4b5530f90834a725c3cde300bad8538 -->

### Added
- feat(tester-unified): first-party packages have no age window (operator decision 2026-10-27) - test that fails if a first-party name could meet --uploaded-prior-to; iobench and vmscreen join the generator's refusal floor; rule documented (ed8641de3)

### Fixed
- fix(tester-unified): review round 2 - a truncated download or an unwritable staging dir is a refusal, not a traceback; exclusive create for the staged wheel; --help explains the published/age options; tester-base docs-sync lane survives the pin switch (22b963b7f)

### Changed
- INT-01 final round: hash the working-tree tester-base Dockerfile, test a failed pull, file A-20..A-23 and B-12 (d284c1eed)
- tester-unified: Docker's pinned docker-compose-plugin instead of Debian's docker-compose (INT-01 D-006 decided) (2f33410e2)
- INT-01 fix round 2: base pull, marker guard, tester-base identity (reviews A-13..A-17, B-08, B-09) (792bcadc9)
- backlog: file A-06 (Calls defaults), A-11/A-12 (tester-base TB-01/TB-02), B-06 (MDT-015) from INT-01 review (9eaf6f960)
- Gate-image base decisions and docs triad (review A-02, A-03, A-04, A-07, A-08, A-10, B-01, B-02, B-03) (0f3d2ba44)
- Merge mdt-home-persistence-20261018 (daf47a2e9) (INT-01 step 4) (5e2dff706)
- tester-base release: pin the captured docker not-found output, accept 'no such manifest: <ref>'; document the containerd re-run refusal and abandoned candidate tags (W1/W2/W5 follow-ups) (c801db980)
- tester-base release: registry answers only (W1), idempotent version push (W2), private per-release candidate tag (W5) (bc2826da0)
- tester-base: pins test also checks released_at against the estate window and the exact image (V6) (41fd740c3)
- tester-base: the release version is derived late from VERSION, the push logs in explicitly and pushes the version tag last (V1, V2); cmru gaps recorded (908ca11e3)
- tester-base: contract test names the literal-patch-tag rule (b4750b623)
- tester-unified: node 26 ships no corepack; copy node, npm and npx only (709f4f7d9)
- tester-base: cmru versions records the pinned tag and digest (D-004); pins test compares the Dockerfile literals to the record (38e4ce116)
- tester-base: one build is tested and pushed (R1), key set (R2), non-hollow negatives (R3), docs triad test (R4), cmru pins (D-004) (8e9447c62)
- tester-base/tester-unified docs: record what the T8 skip-diff found and where each answer lives (7dd9033de)
- tester-base/tester-unified: explicit systemctl stub (D-001) and pycache guard (D-002) found by the T8 skip-diff; topos backlog TP-SYSTEMCTL (262cb9c30)
- tester-base docs: build commands that work with the estate's remote default builder (2efd408ab)
- tester-base: minimal gate base image; tester-unified, wheel-builder and run-gate host move onto it (MDTC-02 T1-T5, T9) (d8f5fda2f)

### Documentation
- docs(tester-unified,wheel-builder,tester-base): runbook promotes both images, keeps rollbacks and selects by id; release order, bootstrap and failure modes written down; wheel-builder comment stops claiming every backend is pinned; backlog TB-08..TB-12 (0df37955d)

<!-- cmru: pending release notes begin -->
- feat: tester-base, the minimal gate base image (python:3.14-trixie digest-pinned + docker-ce-cli + minisign + shellcheck 0.11.0); tester-unified and wheel-builder build FROM it (MDTC-02).
<!-- cmru: pending release notes end -->

## [Unreleased]


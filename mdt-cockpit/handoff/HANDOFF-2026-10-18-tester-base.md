---
id: MDTC-02
title: tester-base — dedicated minimal gate base image; tester-unified, wheel-builder and run-gate `host` move onto it
contract_class: 2c
model: sonnet
depends_on: [MDTC-01]
worktree: /workspaces/vbpub/.worktrees/mdt-home-persistence-20261018
branch: mdt-home-persistence-20261018
---

# MDTC-02 — tester-base

Same worktree and branch as MDTC-01 (you implemented it; its commits are your base).
All decisions below are FIXED by the operator (interview 2026-10-18). BLOCKED rule,
Rules and test rules of MDTC-01 apply unchanged (re-read its "Rules", "Test rules"
and "BLOCKED rule" sections).

## Why (one paragraph — do not re-argue it)
tester-unified (9.6 GB) is built FROM the 8 GB MDT developer cockpit image, of which
it uses Python 3.14, git and the docker client. The cockpit's floating `latest` tag
changes the gate environment without a commit and invalidates every tester layer on
each cockpit release; in mdt-cockpit its baked release CLIs (`ciu`, `cmru`, `nyxloom`,
`topos`) would sit readable on PATH and could shadow the code under test. A dedicated
base keeps the shared bottom layers (`python:3.14-trixie`) and nothing else.

## Context to read first
1. `/workspaces/vbpub/.worktrees/mdt-home-persistence-20261018/tester-unified/Dockerfile`, `tester-unified/README.md`, `tester-unified/run`.
2. `cmru/tests/test_tester_unified_image.py` (it parses the tester-unified Dockerfile text — your edits must keep it green or update it deliberately, with the reason in the commit).
3. `wheel-builder/Dockerfile`; `cmru.orchestration.toml` (image keys ~l.60-110 and how projects/images are registered, e.g. the mdt-cockpit entry ~l.182); `mdt-cockpit/cmru.toml` as the precedent for an image project CMRU builds/publishes.
4. `run-gate.root.toml` (`[environments.host]`), `run-gate-project/run-gate.py:375-392` (`DEFAULT_HOST_IMAGE`).
5. `scripts/debian-install-v2/run-gate.toml` (`r1-vm-real-commit` lane) and `scripts/debian-install-v2/testing/vm/run-vm-tests.sh`.
6. The survey evidence below (controller-verified on 2026-10-18).

### Survey evidence (normative inputs)
Needed by tester-unified lanes beyond `python:3.14-trixie` (which already has git 2.47,
curl, gcc/g++, openssh-client, procps, util-linux, xz, tar, bash):
- docker client (`docker-ce-cli`, Docker's official apt repo, key fingerprint verified):
  `cmru/src/cmru/tester_gate.py:359,419`, `assay/gate/python/qualify_sql.py:517`,
  `ciu/tests/tests/test_getpy_enroll.py:699`.
- minisign (Debian apt): `cmru/tests/test_bundle_manifest_sign.py:560-564,648`,
  `cmru/tests/test_installer_w1.py:28`, `ciu/get.py:1599`.
- shellcheck (upstream release binary, version = the one the current MDT image ships —
  read it from `/usr/local/bin/shellcheck --version` in the current image — pinned
  by sha256): `host-setup/tests/test-render.sh:392`, `assay/gate/tests/test_distribution_gate.py:806`,
  `assay/gate/tests/test_qualify_sql.py:957`.
- gcc/g++ must stay in the final image: `ruptures` (cgroup-profiler report extra) has
  no cp314 wheel and builds from sdist.
- uid 1003 / gid 994 identity, HOME/XDG, `--copies` venv, git `autoDetach` config —
  unchanged from today's tester-unified.

## Decisions (normative)
- **T1 — Base.** New project dir `tester-base/` (Dockerfile, README, `docs/DESIGN-GUIDE.md`,
  `docs/CONSUMERS.md`, `cmru.toml`, `run-gate.toml`, `CHANGES.md`).
  `FROM python:3.14-trixie@sha256:<digest>`: the newest digest of that tag whose image
  was created ≥14 days before today (freshness rule; record tag, digest, created date
  and how you read them in a Dockerfile comment). Not `-slim`.
- **T2 — Contents.** Add ONLY: `docker-ce-cli` (no buildx, no compose), `minisign`,
  `shellcheck` (as above). Debian git (2.47). `LANG=C.UTF-8` (python image default; no
  en_US locale). No AI CLIs, PHP, node, zsh, nvim, systemctl shim, KSM `LD_PRELOAD`,
  `VIRTUAL_ENV`, `/home/vscode` anything. Explicitly NOT added: crane, regctl, buildx,
  iproute2, systemd/systemd-tmpfiles, fdisk. tester-base itself creates no user — the
  uid-1003 identity stays in tester-unified (wheel-builder adds its own).
- **T3 — Bootstrap from repo alone.** tester-base builds with plain
  `docker build -t tester-base:local tester-base/` and only public upstream inputs
  (python digest, Debian apt, Docker apt repo, shellcheck release URL+sha256) — no estate
  artifact, no cmru. Consumers: `ARG TESTER_BASE` + `FROM ${TESTER_BASE}` in tester-unified
  and wheel-builder. Until the first CMRU release exists, every pin site (the two ARG
  defaults, `run-gate.root.toml` `[environments.host].image`) is `tester-base:local`;
  the controller switches them to the published `ghcr.io/volkb79-2/tester-base@sha256:…`
  after release. No other fallback anywhere. Document the bootstrap order in
  tester-base/docs/CONSUMERS.md: build tester-base → build tester-unified
  (`--build-arg TESTER_BASE=tester-base:local`) → gates/cmru work → cmru releases tester-base.
- **T4 — CMRU project.** Register `tester-base` in `cmru.orchestration.toml` as an image
  project publishing `ghcr.io/volkb79-2/tester-base`, following the mdt-cockpit
  precedent but minimal. Its registered gate lane (`run-gate.toml`, lane `contract`,
  bare-host driver like mdt-cockpit's `image-runtime`, `required_env =
  ["CGROUP_PARENT_DEV_GATES", "TESTER_BASE_IMAGE"]`) runs an in-container contract test
  of the candidate image: python is 3.14 at `/usr/local/bin/python3.14`; `docker --version`,
  `minisign -v`, `shellcheck --version` (= pinned version), `git --version` 2.47.x,
  `gcc --version` work; `crane`, `regctl`, `tc`, `systemd-tmpfiles`, `docker buildx`
  are ABSENT (negative assertions — they encode T2); no `/home/vscode`; `LANG=C.UTF-8`.
  Do NOT run `cmru release`/`publish` — the controller does that after review. If cmru
  cannot express an image project without hand-rolled scripts beyond what mdt-cockpit
  already does, BLOCKED with the evidence.
- **T5 — Consumers.** tester-unified: only the FROM line (+ `ARG TESTER_BASE`) and the
  apt/identity layers change; keep every other RUN byte-identical where
  `cmru/tests/test_tester_unified_image.py` constrains it. Drop reliance on the MDT PATH
  (image `ENV PATH` = `/opt/tester-venv/bin:` + the base's PATH). wheel-builder: FROM
  `${TESTER_BASE}`, keep its venv/identity. `run-gate.root.toml` `[environments.host]`
  → `tester-base:local` (T3). `run-gate.py`'s `DEFAULT_HOST_IMAGE` is NOT changed in
  this package: file it as a new item in `run-gate-project`'s backlog (check its
  CHANGES.md first) referencing this package.
- **T6 — OCI-push test placement.** `mdt-cockpit/scripts/test_oci_layout_push.py` (the
  active `RELEASE_IMAGE_FLOW=load` path: regctl copy + crane digest verify) moves out of
  the tester-unified `release` lane into a new mdt-cockpit lane `oci-push` that runs in
  the mdt-cockpit image (which ships docker/buildx/crane/regctl) with a docker socket,
  `required_env` naming that image ref + `CGROUP_PARENT_DEV_GATES`; in that lane missing
  tools are a FAILURE (replace its `skipUnless` with a required check when the lane
  sets an env flag). Make the mdt-cockpit release step run it.
- **T7 — OS-integration marker.** Tests that execute real low-level OS binaries get
  pytest marker `os_integration` (registered in each project's pytest config). In
  tester-unified lanes they are deselected (`-m "not os_integration"` in the lane argv,
  explicit). In the owning project's QEMU VM lane they are selected and REQUIRED: a
  conftest hook turns a skip of an `os_integration` test into a failure when
  `VBPUB_REQUIRE_OS_INTEGRATION=1` (set by the VM lane). Scope:
  - debian-install-v2: `tests/test_lt_mem.py` real systemd-tmpfiles test;
    `tests/test_inuse_partition_editor_r1.py` sfdisk/blockdev/partx/losetup tests;
    `tests/test_io_benchmark.py` real sysctl test → wire into `r1-vm-real-commit`
    (`run-vm-tests.sh` pytest invocation). The guest must have the tools (add to the
    guest image recipe if missing). Build/run the VM lane once (TCG, ≤ its 30m budget,
    alone on the host) and record the result.
  - gstammtisch-guide `tests/test_soulmask_monitor_r1.py:197` (nsenter): no VM lane
    exists → mark + deselect, and file a backlog item in that project. Do not build a VM lane.
  - topos `tc` (`net_host.py:219`): NOT marked (its tests inject the runner); prove with
    the topos lane's coverage gate that nothing changes.
  - Docker-driving tests (SQL qualification, tester-gate DinD, ciu enroll) stay in
    containers — NOT marked (TCG cannot carry CPU-heavy work).
- **T8 — Acceptance by skip-diff.** For every tester-unified lane (list from the survey:
  ciu, cmru{assay,installed-wheel,coverage,canary}, run-gate-project{selftest,assay-r1..r3},
  nyxloom{tester-unified,session-extract}, pwmcp{r1,r2}, topos{topos-suite},
  libraries/worktree, libraries/cli-extended{r0-r1,r2,r3}, mattermost-server, scripts/telegram,
  scripts/netcup, scripts/damon-analysis, scripts/gstammtisch-guide, scripts/debian-install-v2,
  scripts/cgroup-profiler{r2,r3}, mdt-cockpit{smoke,release}) record pass/skip/fail counts
  and the `-rs` skip reasons on the OLD image (current `tester-unified:local`, do not rebuild it)
  and the NEW one. Run lanes ONE AT A TIME, niced, `docker update --cpus=3 <exact name>`
  after launch; long mutation lanes (R2/R3, cmru mutation) are EXCLUDED from the diff run —
  list them as "not run, reason: budget" instead. Acceptance: identical results except
  (a) tests deselected by T7 and (b) the T6 move — each difference listed and explained.
  Any other difference: fix tester-base (allowed: adding a userland package with
  evidence) or BLOCKED.
- **T9 — Docs triad** for tester-base (README what / DESIGN-GUIDE why incl. T2 rejected
  list and the VM/container placement rule / CONSUMERS how incl. bootstrap); update
  tester-unified/README.md and wheel-builder docs; mention in AGENTS.md's
  "The gate is never the devcontainer" paragraph that tester-unified builds FROM
  tester-base (one sentence).

## Scope
Touch: `tester-base/**` (new), `tester-unified/**`, `wheel-builder/**`,
`run-gate.root.toml`, `cmru.orchestration.toml`, `mdt-cockpit/**` (T6),
`scripts/debian-install-v2/**`, `scripts/gstammtisch-guide/**` (marker + backlog),
pytest config/conftest of projects where T7 adds the marker, `cmru/tests/test_tester_unified_image.py`
(only if T5 requires), backlog files named above, `AGENTS.md` (one sentence).
Forbidden: `modern-debian-tools-python-debug/**`, `nyxloom/nyxloomd/**`,
`run-gate-project/run-gate.py`, publishing/pushing any image, `cmru release`.

## Report (final message)
1. Commits + `git status --short`.
2. tester-base digest/created date, final image sizes (tester-base, new tester-unified vs old 9.6 GB).
3. T8 skip-diff table per lane: old vs new counts, every difference with its T6/T7 reason.
4. VM lane result (T7) with exit marker.
5. Backlog items filed (ids).
6. Anything unverified.

## Amendments (controller, 2026-10-18 evening) — these override the text above where they differ
- **A1: step 0 comes before any Phase 3 work.** Close the MDTC-01 re-review minors:
  - **M1:** codex, reasonix, pi and opencode must install a staged, exact, recorded version.
    - Apply the Claude rule: refuse an empty value or `latest`.
    - Remove the `default="latest"` and `ARG X_VERSION="latest"` defaults.
    - Pin it with a unit test seen RED.
  - **M2:** an explicit npm pin for reasonix, opencode or pi must be a published version. Reuse the Claude registry check at staging time.
  - **M3:** use one shared "install enabled" definition for `stage_tool_artifacts.py` and `resolve-devcontainers-release.py`.
  - Then rebuild the mdt-cockpit local-dev images once (`-16`) and rerun smoke, release and image-runtime.
- **A2: the host images were cleaned by the operator.** `tester-unified:local`, `wheel-builder:local`, the old MDT base image and all earlier mdt-cockpit images are gone.
  - For T8, rebuild the OLD tester-unified baseline from the pre-change Dockerfile: `git show d834ea8eb:tester-unified/` into a scratch dir, built per tester-unified's own README.
  - Tag it `tester-unified:old-baseline-20261018`, and record its image id and the MDT base digest it pulled.
  - Never tag it `tester-unified:local`. The NEW image built on tester-base becomes `tester-unified:local`, because other sessions' gates need that tag to exist again.
  - Build the new image as soon as T5 is done, before the long T8 runs.
- **A3: disk check before every image build.** Run `df -h /`. If less than 60 GB is free, STOP and report. Do not prune anything yourself.
- **A4: AI-CLI freshness policy (operator).** claude, codex, reasonix, opencode and pi follow upstream latest, with no 14-day window. The 14-day rule still applies to the tester-base `FROM` digest (T1).
- **A5: T6 image.** The `oci-push` lane runs in the `-16` mdt-cockpit image.
- **A6: routing.** Stay in this session. The controller sends review findings back to you here.

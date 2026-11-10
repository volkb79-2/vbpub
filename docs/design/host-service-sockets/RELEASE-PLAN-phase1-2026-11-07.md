<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-07; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/sockets-phase1-RELEASE-PLAN.md -->
<!-- relation: host service sockets release plan -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Sockets phase 1: release plan under the P-10 pin hold (read-only analysis, 2026-11-07)

Scope: origin/main `5144b38cb` (phase 1 merged; cgroup-profiler 2.1.0 to 2.2.0, topos 0.6.0 to 0.7.0, run-gate, assay; new per-level socket layout only, no legacy fallback). Nothing was changed, released or run against a host. All line numbers are `origin/main` unless a path says otherwise. The local checkout `<checkout>` is `main` ahead 1 / behind 86 and still holds pre-phase-1 content; do not read these findings from it.

State of the world (verified on the controller just now):

| Item | Value |
|---|---|
| Controller run-gate (venv) | `/opt/mdt/venv/bin/run-gate` 25.2.1 (also topos 0.6.0, assay 12.0.0, ciu 7.24.0, cmru 7.2.1 in that venv; the release controller is `<controller-home>/cmru-src-venv` cmru 7.2.2 + source) |
| Controller tester image | `tester-unified:local` = `sha256:09fa2f5e9846...` = `wave-20261106a`, label `org.vbpub.run-gate.version=25.2.1`; rollback `tester-unified:prev-20261106a` = `27fa63e57ba3` |
| Controller daemon layout | OLD: `/run/cgprofile/ctl.sock` (root:994), `/run/cgprofile/observer/resource-observer.sock` (root:990, dir root:990 0751); `/run/cgprofile/control` and `observer/cgprofile.sock` do not exist |
| Installed `run_gate.py` vs repo | byte-identical to local HEAD (pre-phase-1), DIFFERS from `origin/main` (`cmp` at byte 6763) |
| Old vs new constants | old: `PROFILE_SOCKET_PATH_DEFAULT=/run/cgprofile/ctl.sock`, observer `.../observer/resource-observer.sock` (HEAD `run_gate.py:506-512,727`); new: `/run/cgprofile/control/cgprofile.sock` and `/run/cgprofile/observer/cgprofile.sock` (`run_gate.py:505-513,728`) |
| Hosts <host>/<host> R2 tooling | host-side `~/.venvs/gate-tools2502` (run-gate 25.0.2, OLD layout) inside the cockpit container, image `tester-unified:r25-d38b3324fba5` (run-gate 25.1.1), `RUN_GATE_PROFILE=off`; hardcoded in `run-lane-r25d38.sh:15-16` (copy in `<host>/export-20261105<home>/`), `runner.env:19` `RUN_GATE_VERSION=24.3.0` (explicit pin for fresh provisioning) |

Empirical check (new `run_gate.py` from `origin/main`, imported read-only, run on this controller):

```
resource_observer_docker_flags(observer lane) -> GateInfraError: cgprofile resource observer directory or socket is
  unavailable: /run/cgprofile/observer, /run/cgprofile/observer/cgprofile.sock: [Errno 2] ...; complete the host
  cgprofile socket cutover and verify the service is installed
preflight_control_socket_access(missing_ok=True)     -> None   (no refusal for a profiled, non-observer lane)
ProfilerClient.version() (non-observer lane)         -> (None, 'cgprofile control socket /run/cgprofile/control/cgprofile.sock
  unreachable (cause: socket-not-mounted ... complete the host cgprofile socket cutover)')   (degrades to basic profiling)
```

So the refusal is exactly the lane's observer preflight; a non-observer lane does NOT refuse, it silently loses cgpd admission, placement and daemon sessions (see 1.4).

---

## 1. Observer-enabled lanes and who refuses

### 1.1 What actually refuses, and which binary

- The observer preflight runs in the process that STARTS the lane: `run_gate.py:16072-16090` (`if not dry_run and lane.get("resource_observer") is not None: resource_observer_version_preflight(...)`, then `resource_observer_docker_flags`, `:8813-8896`; refusals at `:8788-8791`, `:8845-8854`; docker-gid source `:8744-8768`). That is the `run-gate` CLI on PATH, i.e. `/opt/mdt/venv/bin/run-gate` on the controller and `gate-tools2502` on the hosts.
- An image that merely CONTAINS the phase-1 wheel changes nothing for observer lanes. Image contents matter only for (a) `ciu/scripts/check_run_gate_wheel.py` (1.3) and (b) the CMRU release identity check, which demands installed venv run-gate == published wheel == image labels (`cmru/tools/run_release_gate.py:469-615`, `_check_tester_image_identity` at `:756-805`). Because (b) couples venv and image, the usual train (install wheel into the venv, rebuild, promote) flips both at once, and that is what breaks the controller.
- Assay is different: its wrappers hardcode the new paths and run from the checkout, independent of any pin (1.2).

### 1.2 Release-flow lanes, per project (release steps from each `cmru.toml [steps.run-tests]`; observer = `resource_observer = "cgprofile"` in `run-gate.toml`)

| Project | Release gate steps | Observer in release flow? |
|---|---|---|
| cli-extended | `run-gate r0-r1`, `r3` (`libraries/cli-extended/cmru.toml:51-52`) | No. `r2` is the only observer lane there (`run-gate.toml:21`) and is excluded by the file's own comment (`cmru.toml:45-47`). |
| tnt | `r0-r1`, `ux-focused` (`truenas/tnt/cmru.toml:41-42`) | No. `r2` observer (`run-gate.toml:51`) is the async campaign. |
| cgroup-profiler | `r0-r1`, `r3` (`scripts/cgroup-profiler/cmru.toml:63-64`) | No (`r2` at `run-gate.toml:96` is the 24 h campaign, deliberately not a release gate, `cmru.toml:49-59`). Evidence: gate report rows a1/a3 ran on the old-layout controller, `reports/sockets-phase1-GATES-REPORT.md`. |
| run-gate-project | `release_candidate.py prepare` + `run-selftest` (lane `selftest`, `cmru.toml:83-87`) | No. Only `assay-r2` is observer (`run-gate.toml:134`) and `cmru.toml:71-79` keeps it out of the release. Report row b1 PASS on the old layout. |
| cmru | `run-gate gate-provisional` (`cmru/cmru.toml:47`) runs lanes ref-probe, installed-wheel-live, assay, coverage, canary (`run_release_gate.py:1523-1553`) | Not in provisional mode: none of those lanes has `resource_observer`. YES in the full `gate` lane: `mutation` (`cmru/run-gate.toml:244-247`, run at `run_release_gate.py:1543-1548`) and the R2 follow-up `assay-first-release-r2` (`run-gate.toml:62-66`). KI-79 deferral is only authorised for cmru 7.2.x (`POSTPONEMENT_RELEASE_VERSIONS`, `run_release_gate.py:1060-1062`, window ends below 7.3.0). A 7.3.0 or later cmru release therefore needs the mutation lane, which needs the observer on the release host, or a new reviewed window extension. |
| ciu | `cmru tester-gate ... check_run_gate_wheel.py && run-ciu-tests.py`, then `... run-gate enroll` (`ciu/cmru.toml:77-86`) | No observer (`enroll` has `profile=false`, `ciu/run-gate.toml:67-76`; the observer lane `ciu` at `:25` is the async R2, the file says "NOT a release gate"). BUT it has the image-vs-checkout dependency, 1.3. |
| assay | `bash tools/tester-unified-gate.sh ..` and `run-gate self-qualification-provisional` (`assay/cmru.toml:29-34`) | **YES, unconditionally.** Both are `bare-host` lanes that call shell wrappers hardcoding `/run/cgprofile/observer/cgprofile.sock` (`tester-unified-gate.sh:38-47,1501-1521`; `self-qualification-container.sh:350-352`; `resource-observer-gid.sh:11-48`: refuses unless observer dir is root:root, socket group is non-root and not the docker gid). They also require `ASSAY_GATE_HOST_DOCKER_GID` or `MDT_HOST_DOCKER_GID`, which this controller does not export. Measured: gate report rows c1/c2, "cgprofile observer directory is not root:root: /run/cgprofile/observer; complete the host socket cutover", exit 3, no container started. |
| topos | `cmru tester-gate ... pytest tests -n auto` (`topos/cmru.toml:29`) | No. |
| pwmcp | `run-gate r1` (`pwmcp/cmru.toml:71`) | No (`r1` is not observer; `r2` at `pwmcp/run-gate.toml:21` is the campaign). |
| iobench | `r0-r1` (`libraries/iobench/cmru.toml:34`) | No (`r2` observer is async). |
| iogovmon | `r0-r1` | No (and not releasable until WP-IGM-ACC, `iogovmon/cmru.toml:3`). |
| nyxloom | `run-gate tester-unified` (`nyxloom/cmru.toml:61`) | No (`session-extract` is the observer lane, `run-gate.toml:39`, not in the release). |
| rigor-record, test-policy, tls-edge, tester-base | `r0-r1` / tester-gate / contract | No. |
| mdt-cockpit | `smoke`, `release`, `oci-push`, `image-runtime-built` (`mdt-cockpit/cmru.toml:66-69`) | No release lane is observer (`assay-full` at `run-gate.toml:74` is separate). It is phase 2 anyway and consumes `-latest` wheels, see 3.3. |

Observer-enabled lanes in the estate (all would refuse with the phase-1 CLI on this controller): ciu `ciu`; cmru `assay-first-release-r2`, `mutation`; cli-extended `r2`; cowtree `cowtree`; iobench `r2`; test-policy `r2`; worktree `worktree`; mdt-cockpit `assay-full`; nyxloom `session-extract`; pwmcp `r2`; run-gate `assay-r2`; cgroup-profiler `r2`; damon-analysis `r2`; debian-install-v2 `r2`; <private-host>-guide `r2`; tnt `r2`. Assay's own `tester-unified`, `self-qualification`, `self-qualification-preflight`, `self-qualification-provisional`, `b110-*`, `analysis-r2-pilot` lanes refuse through the wrappers whatever run-gate version runs them.

### 1.3 The ciu release is blocked right now by the merge itself (not by observer)

`ciu/scripts/check_run_gate_wheel.py:27-37,76-87` requires the in-container `run_gate.py` and `run_gate_admission.py` to be byte-equal to the checkout's `run-gate-project/`. `cmru release` builds from `origin/main` only (`cmru/src/cmru/cli.py:5359` "origin/main is the only release source"). origin/main's `run_gate.py` now differs from the image's (verified above), so `ciu` fails at its first gate step until the image carries the phase-1 wheel. ciu has no commits since `ciu-v7.24.0` today, so nothing is pending, but the next ciu release (CIU bugfixes are queued) hits this.

Shell override does not work: `cmru.orchestration.toml:79` sets the literal `CMRU_TESTER_UNIFIED_IMAGE = "tester-unified:local"`, and the release applies declared env over the process environment (`cli.py:905-911`, `os.environ[key] = str(value)`). Contrast `:93` (`BUILDX_BUILDER = "${BUILDX_BUILDER:-mdt-managed}"`), which does honour the shell. Not executed here; confirm with a dry run before relying on either statement.

### 1.4 Non-observer lanes on the controller with a phase-1 CLI: degrade, not refuse

`ProfilerClient` catches the missing socket (`run_gate.py:2951-2965`, `preflight_control_socket_access(missing_ok=True)` at `:2937`, `:3068`) and the lane starts with basic profiling and one warning. The cost is real: no cgpd admission, placement or session on a host shared with the production game server (the `/run/cgprofile/ctl.sock` stays old and unused). That alone is a reason not to put the phase-1 CLI in `/opt/mdt/venv`.

---

## 2. Does each project's release transaction need an observer lane?

Answer by project (details in 1.2): no for cli-extended, tnt, cgroup-profiler, run-gate-project, ciu (but see 1.3), topos, pwmcp, iobench, rigor-record, test-policy, tls-edge, tester-base, nyxloom, iogovmon, mdt-cockpit. Yes for **assay** (always). Conditional for **cmru**: no under `gate-provisional` for 7.2.x; yes (`mutation`) for 7.3.0 or later or any non-provisional gate. R2 deferred lanes (`ciu`, `r2`, `assay-r2`, shards) are all observer-enabled but are not release steps; they run on <host>/<host> (section 3.2). Run-gate's own `release_candidate.py` builds a per-candidate image (`tester-unified:run-gate-candidate-*` in `docker image ls`) and does not touch `:local`, the venv or any host.

---

## 3. Blast radius of "release run-gate + cgroup-profiler + assay now, rebuild and promote the image"

### 3.1 Controller (scenario A: train-style, venv upgraded, `build-tu.sh` as written, promoted)

`build-tu.sh:9` uses `--run-gate-published latest`, and `train23.sh:12-15` does the pip install into `/opt/mdt/venv` and `promote-image.sh`. Releasing run-gate moves `run-gate-latest`, so that script silently bakes phase 1. Consequences:

- Every observer lane in 1.2 refuses with "complete the host cgprofile socket cutover" (empirically shown above): ciu R2, cmru `mutation`/R2 (so cmru 7.3.0+ cannot release), pwmcp r2, cli-extended r2, iobench r2, run-gate assay-r2, nyxloom session-extract, mdt-cockpit assay-full, cgprofile r2.
- All other lanes keep running but without cgpd (1.4).
- Which releases still pass: cmru 7.2.x (identity check is satisfied because venv, image and published wheel agree), ciu (image matches checkout), cli-extended, tnt, topos, pwmcp, iobench, nyxloom, run-gate, cgprofile.
- Which break: assay release and every assay lane (they break with or without the image, 1.2), plus all observer lanes above.
- Rollback exists (`tester-unified:prev-<date>` is kept by `promote-image.sh`; `pip install run-gate==25.2.1` from the released asset), but any agent gate started in between is already on the wrong judge.

### 3.2 Hosts <host> / <host> and their R2 campaigns

Releasing alone changes nothing on the hosts: they consume no `-latest`. R2 board report `reports/r2-board-20261105b-REPORT.md` (lines 9, 18-21, 56-57, 85-86, 215-216) and the scripts show every campaign (cmru shards `assay-r2-sNN`/`assay-first-release-r2`, ciu `ciu`, pwmcp `r2`, tnt `r2`, run-gate `assay-r2`, dstdns lanes) is started with `gate-tools2502` run-gate 25.0.2 plus `tester-unified:r25-d38b3324fba5`, from checkouts of tagged commits under `~runner/checkouts/<sha>`, against the OLD host observer. They keep working as long as nothing replaces those three things. What would break them: loading a phase-1 image as `:local` on <host> (its `:local` is the same id as r25-d38), upgrading or recreating `gate-tools2502`/`~/.venvs/gate-tools`, `sync-from-devcontainer` plus a re-run of `setup-gate-tools.sh` with `RUN_GATE_VERSION` raised, or a host-setup/`CGPROFILE_WHEEL` pin at 2.2.0 or later (P-10: the old installer renders no socket units). New campaigns from a post-phase-1 checkout are fine with the OLD host CLI (assay source reads the observer path from `ASSAY_RESOURCE_OBSERVER_SOCKET`, `assay/src/assay/resource_limits.py:1078-1088`, which the old run-gate sets to the old path); only assay's own shell lanes are layout-bound.

Both hosts also run the runner cockpit from `COCKPIT_IMAGE=ghcr.io/.../mdt-cockpit-devcontainer:...-latest` (`runner.env:18`): an mdt-cockpit release after phase-1 wheels are `-latest` puts new-layout clients on that PATH (gate-tools2502 stays first on PATH, so R2 is unaffected, but `cgpctl`/`topos` there would be phase 1).

### 3.3 Other consumers of the `-latest` pointers

- `mdt-cockpit/pip/wheels.list:18-20` resolves `topos`, `run-gate`, `cgprofile` at `-latest` at build time: any mdt-cockpit release or devcontainer image rebuild after phase-1 releases carries phase-1 clients. Hold mdt-cockpit (phase 2 anyway) and keep the controller's devcontainer image on its current release.
- dstdns `requirements.txt:72` has `run-gate>=25.0.0 ; extra == "vbpub-tools"`: a refresh of a dstdns tool venv pulls phase 1. Do not refresh it before that host's cutover. Do not drop `.assay-inbox/release.json` for an assay phase-1 release (memory `assay-dstdns-release-notify`).
- `tester-unified/build-image.sh --run-gate-published latest` (see above).
- cmru preflight `tool_dependencies` (stale-pin refusal, `cli.py:5265-5300`): no `cmru.toml` on main declares any, so a new assay/run-gate release blocks nobody.

---

## 4. Releasing without changing what the controller and hosts consume (P-10 hold)

Yes. `cmru release` for each project only builds, gates, tags and publishes (GitHub release plus the `<prefix>-latest` pointer). It never installs into `/opt/mdt/venv`, never builds or promotes `tester-unified:local`, never touches a host. Those are the manual lines of the train scripts: `train23.sh:11-15` (pip force-reinstall, `build-tu.sh`, `promote-image.sh`). Skip them. `build-tu.sh` stays usable only with an explicit version (`--run-gate-published 25.2.1`, not `latest`).

Pins/images/venvs that must stay unchanged until the cutover:

Controller
1. `/opt/mdt/venv`: run-gate 25.2.1 (assay 12.0.0, topos 0.6.0 may be upgraded only to non-phase-1 versions; topos 0.7.0 and cgprofile 2.2.0 are phase 1). Verify `sha256sum /opt/mdt/venv/lib/python3.14/site-packages/run_gate.py` stays the baseline value.
2. `tester-unified:local` = `sha256:09fa2f5e9846...` (run-gate label 25.2.1); keep `prev-20261106a`. No `promote-image.sh`.
3. Old cgpd and the `/run/cgprofile` tree (no `cgpd install`; P-11 would refuse it anyway), devcontainer mounts of `/run/cgprofile`.
4. The controller's devcontainer image (do not rebuild from `-latest` after phase-1 wheels exist).
5. `cmru.orchestration.toml:79` image literal stays `tester-unified:local` unless the reviewed `${...:-}` change in 5.1 lands.

Hosts (<host>, <host>)
6. `~/.venvs/gate-tools2502` (run-gate 25.0.2) and the scripts `run-lane-r25d38.sh`, `run-r2-campaign-r25d38.sh`, `*-r2502.sh`.
7. `tester-unified:r25-d38b3324fba5` (and <host>'s `:local` = same id). Never `docker load` a phase-1 image as `:local`.
8. host-setup version and `CGPROFILE_WHEEL`/topos pins (below the phase-1 versions), `RUN_GATE_VERSION` in `runner.env`, old cgpd 2.1.0 and topos daemon.
9. `~runner/checkouts/<sha>` of running campaigns: no fetch or checkout of main inside them (state is resumable only against the same commit).

dstdns: no tool-venv refresh, no assay release notify.

The one place the hold costs something: ciu (1.3) and assay (1.2). Section 5 handles ciu without breaking the hold; assay needs an operator decision.

---

## 5. Recommended sequence (ONE)

### 5.0 Baseline capture (no state change; run first, keep the output)

```
/opt/mdt/venv/bin/run-gate --version                       # run-gate 25.2.1
sha256sum /opt/mdt/venv/lib/python3.14/site-packages/run_gate.py /opt/mdt/venv/lib/python3.14/site-packages/run_gate_admission.py
docker image inspect tester-unified:local --format '{{.Id}} {{index .Config.Labels "org.vbpub.run-gate.version"}}'   # sha256:09fa2f5e9846... 25.2.1
docker image inspect tester-unified:prev-20261106a --format '{{.Id}}'                                                # sha256:27fa63e57ba3...
ls -l /run/cgprofile/ctl.sock /run/cgprofile/observer/resource-observer.sock      # old layout intact
```
Hosts (identity check first: `source .../dstdns-r2/env.sh; r1002_api_check; v1001_api_check`; read-only): `docker image inspect tester-unified:r25-d38b3324fba5 --format '{{.Id}}'` (expect `d38b3324fba5...`) and `docker exec <cockpit> ~/.venvs/gate-tools2502/bin/run-gate --version` (expect `25.0.2`).

### 5.1 Preparatory change (a small reviewed commit on main, before any release)

Make the tester image selectable per release without promoting: in `cmru.orchestration.toml:79` use `CMRU_TESTER_UNIFIED_IMAGE = "${CMRU_TESTER_UNIFIED_IMAGE:-tester-unified:local}"`, the same pattern as `:93`. Review it independently (every merged change, no size exception). Fallback if the operator prefers no edit: a short serial swap window (promote phase-1 image to `:local` only for the ciu release, then re-promote `prev-<date>`), which blocks every controller gate for its duration and breaks the CMRU identity check while swapped, so it must never overlap a cmru release.

### 5.2 Releases that can go NOW (no controller or host change; one at a time, `release-when-quiet.sh`, trust only `EXIT=`)

Order follows `cmru.orchestration.toml`. Skip `train23.sh:11-15` after each of them.
1. cli-extended (+1 commit), tnt (0 pending), pwmcp r5 (+4), iobench (+7), nyxloom (+2): ordinary, no observer, venv and `:local` unchanged.
2. topos 0.7.0 (phase 1; tester-gate pytest, no observer).
3. cgroup-profiler 2.2.0 (`r0-r1`, `r3`). Recommended precondition: run the VM acceptance `scripts/cgroup-profiler/tools/vm-socket-acceptance.sh` in a disposable guest (plan section 7; still unrun per the 2026-11-07 handoff). The plan lists it as the gate to phase 2 and to any cutover, not to publishing.
4. run-gate-project (phase 1, after cgroup-profiler per `cmru.orchestration.toml`). All 9 commits since 25.2.1 are socket work, so nothing else is waiting on it. `selftest` has no observer.
5. cmru: no commits since 7.2.3; if a release is needed, stay within 7.2.x (KI-79 window) and keep venv and `:local` untouched so the identity check keeps matching.

Immediately after step 4, and before anyone runs `build-tu.sh`: edit `build-tu.sh:9` to `--run-gate-published 25.2.1` (explicit) so a later routine rebuild cannot pick up phase 1.

### 5.3 Releases that need the side image (ciu) and must wait for it

6. After run-gate phase 1 is published: build the phase-1 image under a side tag only, e.g. `tester-unified/build-image.sh --run-gate-published <phase-1 version> --no-cache --tag tester-unified:phase1-20261107` (as `build-tu.sh` does, with that tag), no `promote-image.sh`, no venv install. Then release ciu with `CMRU_TESTER_UNIFIED_IMAGE=tester-unified:phase1-20261107` exported for that single `cmru release ciu` (works after 5.1). During it no observer lane is needed, nothing on the controller changes, and `:local` stays old. Any ciu release attempted BEFORE this step fails at `check_run_gate_wheel.py`.

### 5.4 Must wait

- **assay**: both release steps need the new-layout observer on the release host (1.2). Not releasable from this controller. Meanwhile origin/main's assay gate cannot be run on it either (gate report c1/c2), and assay's 42 unreleased commits include non-socket fixes (B229 Python 3.11 import, B233 leaked-process-tree naming, B226 evidence). Decision for the operator, three options: (a) give the controller host the phase-1 observer after all (contradicts "controller host ignored"); (b) run the assay release (and assay gate lanes) from a cut-over host: cut over <host> FIRST, then use it as the assay gate/release host, which needs a decision about release credentials there; (c) hold assay until after the <host> cutover and accept dstdns stays on assay 12.0.0 meanwhile (it cannot use phase-1 assay before its hosts are cut over anyway). Recommendation: (c) now, (b) for the first assay release after <host>'s cutover. Do not notify dstdns for the phase-1 assay release before its hosts are cut over.
- **cmru 7.3.0 or later / any non-provisional cmru gate**: needs the observer for `mutation` unless a reviewed window extension lands; plan the next cmru as 7.2.x or extend the window deliberately.
- **mdt-cockpit** (and any devcontainer image rebuild): phase 2, consumes `-latest` phase-1 clients (3.3).
- Promotion of the phase-1 image to `:local` and installing the phase-1 wheel into `/opt/mdt/venv`: only in the cutover of that machine.

### 5.5 Cutover later (operator go per host; <host> then <host> by reinstall with the data-area-v2 recipe)

Following plan section 4 steps 1-8 on the host being cut over, in particular step 7: create a NEW venv (not `gate-tools2502`) with the phase-1 run-gate wheel and load the phase-1 image under its own tag on that host only, point that host's `run-lane-*` scripts at them, and make `MDT_HOST_DOCKER_GID` (or `RUN_GATE_HOST_DOCKER_GID`, which wins; `run_gate.py:8744-8768`) available. Drain first: the R2 campaigns of that host (cmru shards, tnt `tnt-r2-run`, ...) must be finished or stopped with the documented kill recipes in the R2 board report; their state resumes only against the same pre-phase-1 pins, so resume them before the cutover, not after. The controller stays on old pins.

### 5.6 Verification commands

After each release (and after the whole train): baseline outputs of 5.0 must be identical (`run-gate 25.2.1`, same sha256 of `run_gate.py`, same `:local` id, same host ids). Additionally:
```
gh release view run-gate-v<ver> --repo volkb79-2/vbpub --json tagName,isDraft,isPrerelease,assets --jq '{tagName,isDraft,isPrerelease,n:(.assets|length)}'
curl -fsSL https://github.com/volkb79-2/vbpub/releases/download/run-gate-latest/latest.json     # points at the phase-1 version: expected, nothing consumes it unpinned
# old CLI still passes an observer lane's host preflight on the controller (dry run, no container is started by the preflight):
cd <clean linked worktree>/pwmcp && RUN_GATE_PROFILE=off /opt/mdt/venv/bin/run-gate --worktree <wt> --dry-run r2   # no "cutover" refusal expected
# phase-1 wheel in a THROWAWAY venv only (never /opt/mdt/venv): proves it is new-layout-only
python3 -m venv <temp-path> && <temp-path>/bin/pip install --no-deps <run_gate phase-1 wheel> && (cd <wt>/pwmcp && <temp-path>/bin/run-gate --worktree <wt> --dry-run r2)   # expected refusal naming the cutover
grep -n 'CMRU_TESTER_UNIFIED_IMAGE' cmru.orchestration.toml        # 5.1 applied (before the ciu release)
docker image inspect tester-unified:phase1-20261107 --format '{{index .Config.Labels "org.vbpub.run-gate.version"}}'   # phase-1 version, side tag only
docker image inspect tester-unified:local --format '{{.Id}}'       # still 09fa2f5e9846...
```
After the ciu release: `cmru release` log shows `check_run_gate_wheel.py` printing "Run-Gate modules and console entry points match the selected checkout" and `:local` unchanged.

---

## 6. Is "controller host is ignored for the cutover" compatible with the controller running release gates with the new run-gate?

Plainly: no, not if the new run-gate is the controller's `run-gate` CLI. Any lane with `resource_observer` refuses, and the assay release gate refuses regardless of which run-gate is installed, because its wrappers come from the checkout. The ruling is compatible only in this form: the controller keeps run-gate 25.2.1 in `/opt/mdt/venv` and `tester-unified:local` as is, runs every release in 5.2 (all observer-free), and gets the phase-1 wheel only in a side image used for ciu (5.3). It is NOT compatible with releasing assay from the controller, with cmru 7.3.0+ (mutation), with any R2/observer lane on the controller, or with the "install wheel, rebuild, promote" routine. The operator should rule on assay (5.4, options a-c), and be told that after the hold the controller can never again run origin/main's assay gates or phase-1 run-gate observer lanes; those move to the first cut-over host.

## 7. Risks and unknowns

- 5.1 and the claim that the shell value is ignored (`cli.py:905-911`) are from source, not executed; confirm before depending on either.
- Whether `cmru` full R2/mutation timing on cut-over hosts is feasible is outside this analysis.
- I did not touch hosts; host facts come from the R2 board report and the exported runner scripts (2026-11-05), so re-run the 5.0 host checks before acting.
- The VM acceptance for cgroup-profiler phase 1 has not been run; phase-1 behaviour under real systemd is unproven (static review and unit tests only).

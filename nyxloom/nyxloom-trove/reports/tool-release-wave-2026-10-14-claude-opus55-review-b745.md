# Independent Claude Opus 5.5 High review

- Candidate: `tool-release-wave-20261014` at `b74548a1b28e56000873f562a3bd7fb0087ec13e`.
- Base: `origin/main` at `e50aa84d02d6d6a5de0e00b8aaf4b8d0a12b7259`.
- Route: Claude Opus 5.5, high effort; session `a1126183-bf67-4dfe-a5f6-94f9127337e7`.
- Read-only review; no tests or gates were run.

# Independent review of `tool-release-wave-20261014` at `b74548a1b`

This was read-only. HEAD was `b74548a1b28e56000873f562a3bd7fb0087ec13e` on branch `tool-release-wave-20261014` before and after, and `git status --short` was empty both times. I ran no tests, gates, builds or containers. The only commands beyond reading files were `docker image ls/inspect` and reading pip metadata. The session couldn't write files, so this review exists only here.

**Short answer:** neither provisional merge nor release is justified yet. Finding 1 means CMRU cannot release itself through `cmru release`, which blocks the stated release order. Both blockers are small, bounded fixes.

## Findings, most severe first

### F1 — High, blocks the CMRU release: the new installed-wheel check refuses CMRU's own release
- **Where:** `cmru/src/cmru/cli.py:5640-5647` calls `_record_release_controller_identity`, which uses `cmru/src/cmru/release_controller.py:31-40`. This conflicts with `cmru/src/cmru/transaction.py:3447-3461`. For a CMRU self-release, that code puts the candidate's `cmru/src` first on `PYTHONPATH` (design rule D10).
- **What happens:** you run `cmru release cmru` from a correctly installed bootstrap wheel.
  - The launcher's check passes.
  - The child process imports `cmru.cli` from `<workspace>/cmru/src`, but the installed distribution's `cmru/cli.py` is in site-packages.
  - The child raises "CMRU release must run from its installed wheel" before it gates or tags anything.
- **Impact:** CMRU 6.2.0 cannot be released through the supported verb. It fails safely and no tag is created, but the release order cannot complete.
- **Why the tests missed it:** an autouse fixture (`cmru/tests/conftest.py:53-70`) replaces the identity check in every test without the `release_controller_provenance` marker. No test runs the child with the D10 environment. `cmru/docs/DESIGN-GUIDE.md:960-970` also claims the child re-hashes the installed wheel, which contradicts D10.
- **Fix:** choose one model and document it.
  - Either: inside a self-release child whose module resolves under `CMRU_WORKSPACE_PATH/cmru/src`, record the candidate commit and the `HEAD:cmru/src` tree hash instead. Keep the wheel check for the launcher and for every non-self child.
  - Or: drop D10, so self-release runs the installed controller.
  - Either way, add one test that runs `_release_child` with the D10 `PYTHONPATH` and does not use the stub.

### F2 — Medium-high: after merge, the newest published Run-Gate can't read vbpub's config, and CGProfile's gate would run under an unrecorded, unpublished Run-Gate
- **Cause:** `run-gate.root.toml:17` and `cmru/run-gate.toml:126` add the `image_env` key. Run-Gate 23.10.0, the newest published release, rejects unknown environment keys (`run-gate-v23.10.0:run-gate-project/run-gate.py:753-762`).
- **Effect on merge:** every vbpub gate run with a Run-Gate of 23.10.0 or older fails to load its config.
- **Effect on CGProfile:** its `run-tests` step calls plain `run-gate r0-r1` and `run-gate r3` from PATH (`scripts/cgroup-profiler/cmru.toml:55-56`). It therefore needs an unpublished 24.x, and nothing records which one judged it.
- **Current cockpit state:** `run-gate --version` reports `24.0.0`. Its `direct_url.json` points to `file://…/tool-release-wave-20261014/run-gate-project/dist/run_gate-24.0.0-py3-none-any.whl` (sha256 `263c9cfb…`). That file no longer exists and its source commit is unknown. This is the same false stable-version problem as the earlier Opus finding #2.
- **Fix:**
  - Document the Run-Gate that judges CGProfile, the same way the bootstrap CMRU controller is documented: a wheel built from the merged commit with a dev version such as `24.0.0.dev0+g<sha>`, its digest recorded in the release log.
  - Alternatively, have CGProfile's step use the receipted CLI from `release_candidate.py`.
  - Reinstall the cockpit's `run-gate` from a correctly identified wheel before collecting more gate evidence.

### F3 — Medium, blocks CIU and CMRU evidence in practice: the shared `tester-unified:local` image is still mislabelled
- **State:** image `8db0df1799cf` carries the labels `run-gate.version=23.10.0` and wheel sha256 `9434bf50…`. According to `run-gate-project/.assay/run-gate-release-wheel.json`, that wheel was built from `af537c9` source, not from the published 23.10.0. The rebuild asked for in the earlier Opus finding #2 has not happened.
- **Impact:**
  - CMRU's new published-asset check refuses this image, and so does CIU's `check_run_gate_wheel.py` once `run_gate.py` differs from `af537c9`.
  - Gates for other projects still run inside an image with a false label.
  - The docs say "rebuild … from the exact published wheel" but give no command anyone could paste.
  - Candidate image tags pile up with no cleanup.
- **Fix:**
  - Rebuild `:local` from a published wheel now, and again after 24.0.0 ships.
  - Add the exact post-release command to `tester-unified/README.md` and `RELEASE-TRANSACTIONS.md`. One option is to retag `tester-unified:run-gate-candidate-<sha12>` after `verify` passes, because `verify` has already proved its bytes match the published wheel.

### F4 — Low-medium: the image override is silent
- **Where:** `run-gate-project/run_gate.py:2009-2014`.
- **Scenario:** a `RUN_GATE_TESTER_UNIFIED_IMAGE` left exported in an operator's shell, after a manual `run-selftest` for example, quietly moves every `tester-unified` and `cmru-mutation` lane, in every project, to another image. Neither the environment-origin line Run-Gate prints (SPEC rule R-05) nor the lane evidence shows the image actually used. This is the silent-fallback pattern AGENTS.md calls a hazard.
- **Fix:** print `image=<x> (override $VAR)` alongside the environment-origin line, and record the effective image and image ID in lane evidence.

### F5 — Low: documentation and test nits
- `run-gate-project/SPEC.md:136` says `mode` is "required … default `ephemeral`", which contradicts itself. The code requires `mode` (`run_gate.py:926-929`).
- The tests for `_check_run_gate_release_artifacts` mock `load_forge_config` (`cmru/tests/test_release_gate.py:1099`). Nothing tests that the real `cmru.orchestration.toml` resolves Run-Gate's tag prefix to `run-gate-v`.
- The identity script in `run_release_gate.py` reads only `archive_info.hashes`, while `release_controller.py` also accepts the older `hash` field. This is harmless with pip 23 or later, but inconsistent.

## Earlier findings rechecked at `b74548a1b`

| Earlier finding | Status now |
|---|---|
| Opus #1: CIU wheel check never compared bytes | Fixed (`ciu/scripts/check_run_gate_wheel.py:13-34`) |
| Opus #2 / Codex #3: unproven stable version stamp; host launcher not the candidate | Mostly fixed. There is now a "newer than the latest tag" guard, an image tag per receipt, a selftest run through a CLI installed offline from the receipted wheel, and module-byte plus `--version` checks inside the container. Still open: the guard only sees local tags and doesn't prove HEAD is CMRU's changelog-fold commit. See F2 and F3. |
| Opus #3: CMRU gate not tied to the published Run-Gate | Fixed in code: it downloads the asset and its `.sha256`, compares wheel, module-payload and image-label digests, and pins each lane to the image ID. Never run against real GitHub assets. |
| Opus #4a: `memory.events` "max" counted as a failure | Fixed (`assay/src/assay/resource_limits.py:205-216`, plus the ancestor exclusion at `:350`) |
| Opus #4b: an unattributable ancestor event aborts the whole lane | Still open; can wait while R2 is postponed, but the pilots must report it |
| Opus #5 / N1: CMRU controller identity not enforced | Now enforced, but breaks self-release (F1) |
| Opus #6: release order exists only in prose | Still open, low. `cmru.orchestration.toml` still has run-gate-project `depends_on = ["cmru"]`. Separate single-project releases work. |
| Opus #7 / Codex: debian-install-v2 exposure | Still open as a scope decision. The ISO and remote bootstrap from `main` are already on origin/main (`9d8fb4328`, `c774201d9`). This branch adds the completed-install refusal and wider cloud-init cache cleanup (`installer.py`, `bootstrap.py`). Merging puts these on every ISO that boots from `main`, and I saw no VM-lane evidence for them. MDT host-setup is a prerequisite for this wave, not unrelated work, but only `test-render.sh` covers it. |
| Codex #1: `shard_not_merged` missing from the schema | Fixed (`analysis-campaign.schema.json:22`); the analysis suite still needs a registered gate run |
| Codex #2: Git index flags hiding edits in the general release path | Fixed (`cli.py:2904-2950`, with real-Git tests for both flags) |

## Merge and release verdict
- **Provisional merge:** not yet justified, because of F1 and F2. Once F1 is fixed and the CGProfile Run-Gate judge is documented and recorded (F2), a provisional merge is reasonable. Before that, debian-install-v2 should either be split out or get VM-lane evidence.
- **CGProfile:** can go once F2's judge record exists.
- **Run-Gate:** its release-transaction path is now sound in code.
- **CMRU:** blocked by F1.
- **CIU:** needs `:local` rebuilt from the published 24.0.0 (F3).
- **Assay:** waits for the B105 and analysis pilots.

## Material evidence gaps
1. No project has gate evidence at exactly `b74548a1b`. Every reported result predates `cd673fecd`.
2. `release_candidate.py run-selftest` has never run for real. That covers the offline venv install, the image override and `verify-running`. Byte equality between the `prepare` wheel and the wheel rebuilt from the tag has also never been shown.
3. CMRU's binding to the published asset and the image has never run against real GitHub assets.
4. Neither the cockpit's `run-gate` 24.0.0 nor `tester-unified:local` can be tied to a commit (F2, F3).
5. There has been no registered run of the Assay analysis suite, no B105 or analysis pilots, and no R2 campaign (postponed under KI-79).
6. There has been no debian-install-v2 VM-lane run for the new installer guards, and no live MDT host-setup apply for the cgprofile observer group.

If you want, I can save this as a report under `nyxloom/nyxloom-trove/reports/` next to the earlier final reviews, or file F1–F5 into the CMRU and Run-Gate backlogs. Either means leaving read-only mode.


# CMRU release-hardening follow-up review — Claude Opus high

- Review target: `a6532ec86fe0bcc89bc81b43fbbe6cebad3d2389`
- Compared CMRU-only changes from: `14d5e653ebc4d77cbe904c99e243ce67f3e828e4`
- Persistent review session: `d6ca6e09-7274-47c3-a6a3-3ded6527bbb9`
- Review was read-only. No edits, tests, gates, containers, or external effects.

[12:10:44] No release blockers remain in the CMRU changes from `14d5e653e` to `a6532ec86`. All six earlier findings you listed are closed in code, docs and tests. The probe trust chain leaves one real gap, which I'd treat as a bounded follow-up rather than a blocker. One KI-79 cleanup path can still leave success-shaped records behind on a failed run.

This was read-only throughout: no edits, tests, gates, containers or agents. Without `git diff` I located the two commits (`9bcaaa3d1`, `a6532ec86`) from the worktree reflog and found the changed code by its entry points, so a CMRU file touched outside those entry points could be missing from this review.

## Probe trust chain

**Answer: tester-gate's existing contract is sound; the new wheel-build path is weaker. Bounded follow-up, not a blocker.**

- **tester-gate (existing contract, `tester_gate.py:1013`):** it places the probe in the same slice it is verifying.
  - If that name is a typo, the probe itself runs for up to 30 s in the auto-created unlimited slice.
  - It then reads an empty `FragmentPath` and tester-gate refuses.
  - So the typo is always caught; the only exposure is that one short, read-only container.
  - Tests deliberately pass the same name for both (e.g. `test_w0_tester_hardening.py:194`).
- **wheel-build (new, `handlers.py:409-456`):** the probe is placed in `CMRU_TESTER_CGROUP_PARENT` but verifies a different slice, the build slice.
  - Nothing ever checks the probe's own parent.
  - **Scenario:** `CGROUP_PARENT_DEV_GATES` is typo'd or the gates unit isn't installed. The orchestration config expands it into `CMRU_TESTER_CGROUP_PARENT` (`cmru.orchestration.toml:113`). Every `cmru handler wheel-build` and every bootstrap run then starts a privileged `--pid=host` probe, with no limits, in a transient slice. The probe correctly confirms the build slice and the build proceeds. The typo is never reported.
  - In a release transaction Run-Gate's gate runs first and may refuse a bad gates tier. Direct handler use and `build-initial-standalone.sh:195-203` have no such earlier check.
- **Why it's only a follow-up:** the probe runs no workload (just `nsenter … systemctl show`, 30 s timeout, `--rm`). Placement is not a security boundary for a privileged container, and the build slice itself is genuinely proven.
- **The docs overclaim:**
  - The docstring (`handlers.py:422-424`) calls this the gates tier "already declared for every CMRU gate".
  - The worklog says that tier is "itself verified by every tester-gate launch". That is an assumption about another command, not a check this code makes.
  - `test_wheel_build_placement.py:74-91` pins the unverified parent as correct, and no test uses a typo'd probe parent.
- **Bounded fix, with no new public input:**
  - Run tester-gate's own self-check on the probe parent first, `check_slice_unit(probe_parent, image, probe_parent)`, and refuse unless it returns `True`. Then probe the build slice.
  - Add a test: transient probe parent → refusal naming `CMRU_TESTER_CGROUP_PARENT`, no builder started, nothing deleted.
  - Correct the docstring.

## Findings, most severe first

**M-1 · Medium · A failure during gate cleanup can still leave success-shaped KI-79 records.**
- **Where:** `run_release_gate.py:1063-1077`, `gate_snapshot.py:1164-1181`.
- **Sequence:**
  1. All lanes and the canary pass, the marker is written (`:1180`), and the state is set to `provisional`.
  2. Cleanup then raises: `remove_source_canary` raises "source canary path changed" when the planted canary was replaced, which is exactly the tamper signal it exists for. `close_gate_snapshot` can raise too.
  3. The inner `finally` still writes the manifest as `state: provisional` with `deferred_lanes`.
  4. `main` returns 1, but the marker (`"result": "provisional"`) stays on disk.
- The same path writes `passed` for a full gate.
- **Impact:** the transaction still refuses to tag, so nothing ships wrongly. But the retained candidate's `.assay` contradicts the acceptance criterion you set.
- **Fix:**
  - Decide the final state only after cleanup succeeds.
  - On any cleanup exception, rewrite the manifest as `failed`, clear `deferred_lanes`, and delete the marker.
  - Test it by making canary removal raise after a provisional run.
- `test_release_gate.py:880-892` (canary fails) also doesn't assert that the marker and manifest are absent.

**L-1 · Low · The marker's `follow_up_lane` is wrong.**
- It says `"mutation"` (`run_release_gate.py:866`).
- That lane refuses to run standalone because it requires `CMRU_ASSAY_BASELINE_FACTS`, which only the gate prepares (`run-gate.toml:191`).
- The docs correctly say to run `run-gate gate` (`CONSUMERS.md:753-755`). Record `gate` instead.

**L-2 · Low · An early refusal leaves the previous run's marker and manifest in place.**
- `_validate_postponement` and the identity/first-release checks can refuse (`:1094-1122`, `:1027`) before the stale-marker cleanup at `:1129`.
- Both files carry the earlier commit, so they aren't false, just stale.
- **Fix:** move the cleanup ahead of the validation.

**L-3 · Low · The tester image's cli-extended bytes aren't independently proven.**
- In `latest` mode, the second identity check after the build (`BOOTSTRAP-FROM-ZERO.md:189-191`) only re-reads the release pointer.
- The image carries no cli-extended digest label, and inside the Run-Gate transaction `latest` isn't tied to the released digest at all (`release_candidate.py:375-376`).
- The fetcher's sha256 check and the pointer checks before and after keep this bounded.
- **Fix:** add an `org.vbpub.cli-extended.wheel-sha256` label and inspect it. The Dockerfile's pinned defaults are still stale; that belongs to tester-unified.

**L-4 · Low · Small inaccuracies in the bootstrap doc and script.**
- **Step 3 (`BOOTSTRAP-FROM-ZERO.md:168-170`):** says `cmru release` "printed" `CLI-EXTENDED_WHEEL_SHA256=`. The cli-extended push step is `quiet = true` (`libraries/cli-extended/cmru.toml:62-64`) and child output goes to logs (`RELEASE-TRANSACTIONS.md:101-104`). Point readers to `cmru.release.log` or `--show-run-details`.
- **Step 2:** `cmru handler wheel-build` doesn't read `CMRU_BOOTSTRAP_CGROUP_PARENT` (`handlers.py:386-390`), although the prerequisites accept that variable alone. An operator who set only that variable gets a refusal at step 2. It fails safe, but the doc should say so.
- **Step 4:** the CGProfile command doesn't re-pin `RUN_GATE_TESTER_UNIFIED_IMAGE`, even though step 3 promises each command pins its image across shells.
- **Bootstrap script (`build-initial-standalone.sh:200-202`):** a config-load failure (for example, `CGROUP_PARENT_DEV_GATES` unset) is reported as "not a verified host slice".

## Closure of the earlier findings

| Item | Status | Evidence |
|---|---|---|
| Slice proven before deletion or build | **Closed** (probe parent: follow-up above) | `handlers.py:595-601`; script `:195-203` runs before Buildx; `test_wheel_build_placement.py:104-160`; `test_installed_wheel_subprocess.py:629-656` |
| Zero-release from a fresh checkout, exact wheel path and digest | **Closed in docs and script; never run live** | `bootstrap-artifacts.env` (`build-initial-standalone.sh:310-316`); `sha256sum -c` and install before the first `cmru` call (`BOOTSTRAP:76-91`) |
| cli-extended release identity reaches the tester image | **Closed, with residual L-3** | `cli_extended_release_identity.py:76-104`; `BOOTSTRAP:176-219` |
| `publish --from-checkout` compares local tag with origin | **Closed** | `handlers.py:81-134` reuses `version.py:137-175`; real-git tests for moved / absent / unreachable / no-origin / annotated tags (`test_release_notes.py:367-449`) |
| KI-79 marked provisional only after all lanes and canary pass | **Closed for lane failures; open for cleanup failures (M-1)** | `run_release_gate.py:1126-1184`; `gate_snapshot.py:1125`; tests `:820-921` |
| KI-78 notes preserved and folded; three docs agree | **Closed** | `README.md:509-524`, `DESIGN-GUIDE.md:898-930`, `CONSUMERS.md:957-989, 1324-1330` all describe the same model; the origin tag check appears in `README:454`, `CONSUMERS:436`, `DESIGN-GUIDE:403` |

## What the registered gate still has to prove

- **Coverage at `a6532ec86`:** the 100.00% PASS was for `14d5e653e`, and these commits add code and tests.
- **One live wheel-build placement check:** run a real `wheel-build` against a real slice, and once against a typo'd build slice. The probe's new docker command line is so far tested only against a fake docker, which proves construction, not acceptance.
- **The in-transaction `gate-provisional` run:** manifest `state: provisional`, the marker bound to the candidate commit, and no marker after any failure.
- **A from-zero bootstrap rehearsal:** steps 1–3 against a scratch forge, which is still unproven.

The release conditions from my previous verdict still apply unchanged, and R2 remains deferred under KI-79 with no R2 pass claimed.
---

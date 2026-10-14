# Independent release-wave review — Claude Opus 5.5 High

- Reviewed commit: 135ecacf0144474a4747f1079693d2dc145f684c
- Base: main at review time (0244fe4e7428d5eb6ce1c37ecc02f2cba0b7719d)
- Route: claude --model opus --effort high; recorded model: claude-opus-5-5
- Read-only review; no tests or gates run.
- Resumable Claude session: 45ad4e04-40c4-445b-a7d4-72285da4b68b
- Review result id: 1c03babc-05f7-403a-bcc7-8fd822360db5

# Review of `tool-release-wave-20261014` against `main` at `135ecacf0`

I found 7 major and 9 minor issues that are worth acting on. Nothing I ran changed the checkout, and I ran no tests or gates, so every finding comes from reading the code. The merge base is `0244fe4e7`; the diff is 128 commits across 520 files. I reviewed Run-Gate and tester-unified myself. Three read-only helper agents reviewed CMRU, CIU with debian-install-v2, and Assay with cgroup-profiler; I re-checked their major findings in the code. Unless I mark a minor finding as checked, it is the agent's report.

## Major

**1. Run-Gate releases as 23.11.0, but the notes, docs and CMRU's minimum all say 24.0.0.**
- **Where:** `run-gate-project/cmru.toml:36`, `cmru/tools/run_release_gate.py:31`, `run-gate-project/CONSUMERS.md:19` and `:1842`.
- **Problem:** The version bump comes from commit messages. No commit touching `run-gate-project` since `run-gate-v23.10.0` marks itself as breaking (`!:` or `BREAKING CHANGE`). Commit `9a0e192ab` is a plain `feat(run-gate):`, so a routine release becomes 23.11.0. The comment in `cmru.toml` itself says not to pass `--set-version`.
- **Failure:** The release body and install line say `run_gate-24.0.0`, the download URL in CONSUMERS returns 404, and CMRU's release gate refuses the new Run-Gate (23.11.0 is below 24.0.0).
- **Fix:** Release with `--set-version 24.0.0`, or add a `BREAKING CHANGE:` commit. Build the notes from the computed version rather than a literal.

**2. `validate-pointers` passes old `run-gate.py` calls when they include a path.**
- **Where:** `run-gate-project/run_gate.py:7064`.
- **Problem:** The pattern `(?<![\w./-])(?:\./)?run-gate\.py` only matches when no `/` comes right before the name.
- **Failure:** I checked the regex directly. `python3 {worktree}/run-gate-project/run-gate.py --worktree {worktree} unit`, `../run-gate.py unit` and `/opt/rg/run-gate.py unit` all go unflagged. A separate pattern (`_INVOCATION_RE`, `:6971`) still recognises them as calls and checks the lane name, so a pointer to a script that no longer exists passes. This is the exact check the release notes advertise.
- **Fix:** Match `run-gate\.py(?![\w.-])` with only a `(?<![\w-])` guard before it, and add tests for the path forms.

**3. Two scripts still build the tester image without the now-required version argument.**
- **Where:** `nyxloom/tools/remote-mutation-audit-host.sh:93` and `scripts/cgroup-profiler/tools/gate.sh:81-83`.
- **Problem:** The tester Dockerfile now stops with an error unless `RUN_GATE_TESTER_VERSION` is passed.
- **Failure:** The nyxloom script's `docker build` (used whenever no `--tester-image` is given) always fails. The cgroup-profiler gate prints a build command as its suggested fix, and that command now fails too.
- **Fix:** Use the README's procedure (derive the version from the built wheel) in both places.

**4. Cloud-init `clean` / `clean-all` lead to a full reinstall on the next boot.**
- **Where:** `scripts/debian-install-v2/debian_install_v2/installer.py:4253-4275`, `:450-456`, `bootstrap.py:286`, `iso_builder.py:340`.
- **Problem:** Nothing checks whether an install already finished (`phase=done`). The code comment acknowledges that `--seed` does not clear an attached NoCloud ISO.
- **Failure:** After cleanup, the next boot looks like a first boot. The launcher downloads the latest bootstrap again, starts with fresh state, repartitions with `sfdisk --force`, and may reboot automatically.
- **Fix:** Refuse to run when `state.json` already shows `phase=done`, or add a marker outside `/var/lib/cloud` that the launcher checks.

**5. `userdata-only` cleanup leaves secrets on disk.**
- **Where:** `scripts/debian-install-v2/debian_install_v2/installer.py:75` and `:4263`.
- **Problem:** It only removes `user-data.txt`. The payload, which holds the Telegram token, Mattermost webhook, Basic-Auth password and SSH keys, also sits in cloud-init's own copies: `user-data.txt.i`, `cloud-config.txt` and `obj.pkl`. That last part is about cloud-init's own file layout.
- **Fix:** Remove those files as well, or document that only `clean` actually removes the secrets.

**6. Assay can leak part of a secret into verdict output.**
- **Where:** `assay/src/assay/process_tree.py:478-483`, which feeds `runner.py:767-793`.
- **Problem:** The new runner keeps only the last 64 KiB of output first and hides secrets afterwards. The function that hides secrets states in its own docstring that it must run before the cut. Both limits are 64 KiB.
- **Failure:** A passed-through token printed across the 64 KiB-from-the-end point loses its start. Hiding then no longer matches it, and the rest of the token appears in `stdout_tail`/`stderr_tail`. Every native R2 command uses this runner.
- **Fix:** Read the limit plus the longest secret length, hide secrets, then cut. Add a test with a secret straddling the boundary.

**7. Sharded Assay campaign analyses fail Assay's own schema.**
- **Where:** `assay/analysis/src/assay_analysis/campaign.py:2012` against `analysis-campaign.schema.json:52-61`.
- **Problem:** Sharded runs are now marked non-qualifying. The schema still says a non-qualifying result must have no verdict and must include pilot evidence (`plan_json`, `selection_file`).
- **Failure:** Every sharded result is invalid against the shipped schema. The test `test_analysis_campaign_verdicts.py:142` validates against that schema and should be failing at HEAD.
- **Fix:** Split the schema's non-qualifying branch into pilot and sharded cases.

## Related CMRU issues (major)

**8. Commit-scope filtering works in previews but not in the real release.**
- **Where:** `cmru/src/cmru/version.py:620-624`.
- **Problem:** The set of known project names is built only from the projects passed in, and the real release passes one (`cli.py:2999`). I checked this in the code. A commit scoped to another project (for example `feat(assay)!:`) is then treated as belonging to the project being released.
- **Failure:** `cmru status` and `--dry-run` can show a patch bump while the real tag is a major bump. The existing test only covers the call with every project passed in.
- **Fix:** Pass the full estate's name set into every call, and test through `release_cmd`.

**9. A published-but-unmerged tag combined with a changelog-only commit is skipped as "unchanged".**
- **Where:** `cmru/src/cmru/version.py:636-656` and `:526-532`.
- **Problem:** When the release tag and `main` have diverged, the code reports the ordinary "behind" case, because changelog-only commits are now excluded from the check.
- **Failure:** The published notes stay under `[Unreleased]` and get published again with the next release.
- **Fix:** Add a "diverged" state and refuse on it, as is already done for "ahead".

## Minor

10. **Gated commit read too late:** `cmru/src/cmru/cli.py:2973` reads HEAD after the gate runs, so a gate step that commits makes the tag land on a commit no gate judged. Read it before the gate and refuse if it moved.
11. **Run-Gate version checked once:** `cmru/tools/run_release_gate.py:443` and `:529` check the version at the start, then start lanes by plain `run-gate` name over hours. A reinstall in between changes the judge while the manifest records the old version. Use the resolved executable path and check again before each lane.
12. **Misleading tag error:** `cmru/src/cmru/gate_snapshot.py:373-376` blames "a release gate lane" for any tag deletion, including one caused by a concurrent release or `fetch --prune-tags`. Reword it so it doesn't assign a cause.
13. **Hollow CMRU notes tests:** in `cmru/tests/test_unreleased_notes.py:459-597`, git is stubbed out, so the tests claiming "no changelog commit after publish" cannot detect one. The `CHANGES.md` case also passes without the new feature. Use a real temporary repository.
14. **CMRU image tests check text, not behaviour:** `cmru/tests/test_tester_unified_image.py` (around `:1227-1309` and `:3307`) asserts Dockerfile strings. The recent `test(cmru): match …` commits show the tests are being edited to match the text, not checking that the image works.
15. **Tester image version check proves nothing:** in `tester-unified/Dockerfile` (around `:101-170` and `:229`), the wheel version is stamped from `RUN_GATE_TESTER_VERSION` and then asserted equal to that same argument. The old `+tester.unified` suffix, which no real release can carry, is gone, so any source tree can claim to be stable 24.0.0. Keep a local suffix, or verify a hash of the source.
16. **Hidden files can be overwritten:** `ciu/src/ciu/worktree.py:4918` runs a git status that hides ignored files before `reset --hard` at `:5217`. An ignored file placed at a tracked path is silently overwritten. Add `--ignored=matching`.
17. **Internal flag exposed:** `ciu/src/ciu/cli.py:1097-1112` exposes the internal `--allocation-lock-held` flag publicly, so a user can skip the allocation lock while another process is allocating.
18. **Cosmetic:** `run-gate-project/tools/coverage_gate.py:421` lost the indentation on one docstring line.

## Checked, no issue found

- **Run-Gate's latest commit:**
  - The `--fresh` refusal removed from `_dispatch` is still enforced earlier in `preflight_inflight_assay_contract`.
  - Removing the empty-verdict guard is safe: `resolve_inflight` validates the record first.
  - The new `--check-env` "INCOMPLETE" reporting is correct.
- **Other Run-Gate changes:**
  - The rename-aware diff-coverage change is correct.
  - The Assay exit-code-to-outcome table matches `assay/errors.py`.
- **Assay and cgroup-profiler:**
  - Both sides use the same field names and values for the new resource-observer interface (contract 2).
  - The Assay verdict schema moves to v16 with a breaking marker, so the next Assay release is a major version.
  - Run-Gate doesn't read the verdict's `schema_version`. That isn't a regression.
- **CIU and debian-install-v2:**
  - CIU's network ownership check compares Docker's object id, not just a name.
  - Partition ordering and ISO generation found no issues (no shell is involved in the ISO build).

Before release, the highest priorities are 1 (it blocks CMRU's own release and publishes wrong notes), 6 (secret leak), 4 (unintended reinstall) and 3 (broken builds). The full list is also saved in `/home/vscode/.claude/plans/independently-review-the-release-wave-breezy-nygaard.md`.

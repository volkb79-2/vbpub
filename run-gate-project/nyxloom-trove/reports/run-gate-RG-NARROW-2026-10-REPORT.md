# run-gate RG-NARROW report (backlog RG-85), 2026-10-06

Branch `run-gate-narrow-mount` (worktree `/workspaces/vbpub/.worktrees/run-gate-narrow`), from main `15b4fbe6a`. Implementation commit `5dd9f880d`. Not pushed, not released, revision NOT bumped (see Open items). Everything below is what I ran; nothing is claimed beyond it.

## 1. Design (as implemented)

One function, `container_mount_flags(repo, worktree, env, *, with_state, create_state)`, builds the mount set for every container run-gate itself creates (ephemeral lanes, and ephemeral probes through `build_env_probe_argv`). Previously both called `dual_mount_flags(repo, ...)` on the common-dir owner.

Linked worktree:
- the judged worktree, dual-mounted (physical + namespace path);
- the git common dir, dual-mounted, READ-WRITE (controller decision citing ciu v8 `SPEC-V8.md` S16.4.9: assay's repository snapshot runs `git worktree add`; the per-worktree admin dir is inside the common dir, so no extra mount);
- `<repo>/.run-gate` (dual) for assay lanes whose environment declares no `state_root` (this is the durable assay resume-state root; it lives in the MAIN checkout, so "mount less" needs it named explicitly);
- `RUN_GATE_EXTRA_MOUNTS` operator mounts unchanged;
- NOT mounted: the main checkout, every other worktree, every ignored file outside the judged worktree. The judged worktree keeps ALL its own files, ignored overlays and rendered files included (what ciu worktree instances rely on).

Credentials: `<common>/config` (and `<common>/config.worktree`, `<admin>/config.worktree` when present) are overlaid read-only with a per-run sanitized copy. The copy is generated from `git config --file X --list -z` and re-rendered, in a private 0700 temp dir (system temp, which is outside every mount; fallback `<repo>/.run-gate` only when system temp is not host-visible; removed at process exit). Rules: userinfo stripped from every URL in keys and values (http/https/ftp any userinfo; other schemes only `user:pass@`, so `ssh://git@host` survives); dropped: `credential.*`, `*.extraheader`, `*.cookiefile`, `core.askpass`, `core.sshCommand`, `include.*`/`includeIf.*`, keys named password/passwd/token/secret/apikey, and any `url.*.(push)insteadOf` whose base or value embeds credentials. No project knowledge.

Plain (main) checkout: ephemeral lanes/probes are REFUSED (ERROR 2, message names `--allow-main-checkout` and `RUN_GATE_ALLOW_MAIN_CHECKOUT`). Opt-in mounts the whole checkout as before, WARNs on stderr (once per tree) and still applies the sanitized config overlay. The flag simply sets the env var for the process, so sequence members and probes inherit it.

`mode = "exec"` lanes: untouched (their containers belong to ciu or the project's stack; e.g. dstdns `test-runner`). Verified by test (below), and dstdns `run-gate.toml` declares one environment, `mode = "exec"`.

### Things I found that contradict or extend the brief
1. Durable assay state defaults to `<repo>/.run-gate` in the MAIN checkout. A pure "worktree + common dir" mount would have broken every assay lane; it is an explicit extra mount (`with_state`). `create_state` mkdirs it when absent (non-dry-run, assay lanes) so the previous "container creates it" behaviour is kept for same-uid cases.
2. `run_gate.py` is a symlink to `run-gate.py`; the edit target is `run-gate.py`.
3. Physical-path derivation (`physical_path`) maps only bind-mount points. The sanitized copy must be Docker-bindable from the host, so it cannot live in a container-private `/tmp`; here `/tmp` IS a host bind mount, hence the temp-dir-then-`.run-gate` fallback.
4. `dual_mount_flags` validated the bare-host alias against the repo root only; it gained an optional `alias_root` so a sub-path (worktree, git dir) maps through the same alias. Default behaviour unchanged.
5. The sanitizer's first draft let a credential survive in a config KEY: `url."https://TOKEN@host/".insteadOf` was parsed with scheme `url.https` (my regex allowed `.`), so it was not stripped. The new test (all credential forms) caught it before commit; the scheme pattern no longer allows `.`.
6. Read-only common dir: not adopted (controller override, S16.4.9). By inspection assay's own git calls other than the snapshot are read-only (`clone --no-local`, `log`, `rev-parse`, `describe`), and `git status` needs the per-worktree admin dir writable, so a ro common dir plus rw admin dir looked feasible; I did NOT test ro in a container and recommend nothing on it.
7. Valueless config keys (`[x] flag`) are rendered valueless (preserved; tested). In-container `git config` writes to the repo config fail (file overlay); `git config --global` (what run-gate itself uses) is unaffected.
8. `doctor` on a main checkout now fails its ephemeral probe with the refusal, since probes share the mount set. Use `doctor --worktree`.

### Proposed ciu v8 SPEC S16.4.9 amendment (controller files it)
The sanitized `<common>/config` overlay is an addition beyond v8: S16.4.9 mounts the git common dir read-write, which exposes `remote.origin.url` credentials from `<common>/config` to every container (and the same mounts are injected into persistent `exec_in` services). Proposed text: "the container-visible `<common>/config` MUST be a per-run copy with credentials removed (URL userinfo, `credential.*`, `*.extraheader`, password/token keys, credential-bearing `url.*.insteadOf`), mounted read-only over the real file".

## 2. Callers that run gates on the main checkout (effect and disposition)
Searched with grep over the repo (not exhaustive for other hosts/repos).
- nyxloom pipeline gate pointers (`nyxloom/nyxloom-trove/nyxloom.toml:85`, `topos/nyxloom-trove/nyxloom.toml:72,84`): `./run-gate.py --worktree {worktree} <lane>` with a package worktree: narrowed, fine. If `{worktree}` were the main checkout, refused.
- `nyxloom-merge-p` skill (`nyxloom/.claude/skills/nyxloom-merge-p/SKILL.md`, step 3 "Post-merge gates from main", "Omit `--worktree` when running from the main checkout"): FLAG. For a project whose gate lanes are ephemeral (vbpub projects on `tester-unified`), post-merge validation from the main checkout is now refused. Remedies: judge a worktree at merged main, or export `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` knowingly. Not edited (nyxloom-owned, outside this package); controller should have nyxloom update the skill. dstdns post-merge (exec mode) is unaffected.
- cmru release gate (`cmru/tools/run_release_gate.py:375`): `./run-gate.py --worktree <release worktree> <lane>` from an isolated linked worktree: narrowed. Its `_common_mount_root`, `_secret_overlay_paths` and secret masking assume the whole repo is mounted and are now redundant (removable; cmru tracks it). Not touched. A plain-checkout `repo_root` would be refused.
- Buildkite tools (`tools/buildkite/pipeline.sh`, `bk-lane.sh`: `./run-gate.py <lane>` on an agent): FLAG. CI clones are plain checkouts, so ephemeral lanes there need `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` in the agent environment (documented in CONSUMERS.md). Not changed.
- `tools/canary-run.sh`: runs bare-host lanes in an rsync copy; no container mounts; unaffected.
- run-gate's own registered lanes (`selftest`, `assay-*`): `environment = "bare-host"`; unaffected by mounts.
- dstdns: single exec environment; unaffected.
- Others listed by grep (topos, shared-ramdisk, modern-debian-tools, pwmcp, plesk, cli-extended, cgroup-profiler configs): not individually opened; any that use an ephemeral environment follow the nyxloom row (worktree = fine, main checkout = refused).

## 3. Tests and evidence (what I ran)
All runs under `flock gate.lock nice -n 19 ionice -c 3`, one container at a time, foreground, after a PSI check. The shared flock was occasionally held by another agent's gate, so one run queued ~10 min.
- Full unit suite before my test edits: 15 failed. Six (`test_bk_run_refuses_outside_a_git_work_tree`, `test_no_stdlib_violations`, 2 doctor/check-env non-git, `outside_a_repo`, `git_error_status`) also fail on the unmodified baseline when run via `git stash` with the basetemp inside the repo; five of them pass with the lane's `GIT_CEILING_DIRECTORIES`. The other nine were mine: legacy plain-checkout fixtures (now opted in by an autouse conftest fixture; tests that pin the refusal delenv it) and assertions on exact mounts (updated).
- Full unit suite after: `3 failed, 1653 passed, 3 skipped` before the new tests were added; remaining failures: `test_no_stdlib_violations` (allowlist lacks `collections`, present on main already; `atexit`/`tempfile` are now allowed), `test_unreleased_changelog_matches_revision_and_recovery_contract` (`'RG-81, rev 55'` not in CHANGES, untouched by me; I did not run either on the baseline, this is by inspection), and the arg-construction test I then fixed. After the last edits I ran only the `RgNarrow`/`full_docker_argv`/`RealContainer` selections, not the whole suite again.
- New tests (`TestRgNarrowSanitizer`, `TestRgNarrowMounts`, `TestRgNarrowRealContainer` at the end of `tests/test_run_gate.py`): 14 pass with the real container included. They cover every credential form (userinfo, bare token, `@` inside password, pushurl, extraheader on global and URL-scoped, credential helpers/usernames, credentialed and clean `insteadOf`/`pushInsteadOf`, askpass, sshCommand, include/includeIf, apiKey/password keys, valueless key, escapes round-trip, unreadable config), both checkout kinds' mount sets, state mount rules, refusal with `0`/empty/`no`, opt-in WARN, flag opt-in via CLI, an exec lane on the main checkout still running (`docker exec`, no `docker run`, no refusal), and exec probes never computing mounts.
- REAL container smoke (`tester-unified:local`, run through the real CLI against a throwaway repo with FAKE secrets): from inside, the planted ignored `cmru.secret.toml`-style file at the repo root, the repo root's `README.md` and a sibling worktree with its own ignored file are ABSENT; the judged worktree's own ignored file is PRESENT; `git log -1`, `rev-parse`, `status`, `diff` work; `git worktree add --detach` + `log` + `remove` against the mounted common dir works (assay-snapshot shape); the container-visible config and `git config --list` contain no fake credential and keep the sanitized URL; the same repo on the main checkout without opt-in exits 2 with the refusal. I did not touch the real `cmru.secret.toml`. I did not run an actual assay lane in a container.
- Plants, each applied, run against the `RgNarrow` tests, killed, and reverted:
  1. mount the whole repo again: killed by `test_command_lane_full_docker_argv`, `test_linked_worktree_mounts_only_worktree_and_git_dir` and the real-container smoke;
  2. skip the sanitizer: killed by `test_overlay_is_a_credential_free_copy_outside_every_mount`, `test_plain_checkout_opt_in_warns_and_still_sanitizes` and the real-container smoke;
  3. allow the main checkout without opt-in: killed by `test_plain_checkout_is_refused_without_opt_in`, `test_flag_sets_the_opt_in_for_the_whole_process` and the real-container smoke.

## 4. Gate verdict
Registered `selftest` lane, run as `tester-unified/run --workdir <wt>/run-gate-project -- ./run-gate.py --worktree <wt> selftest` (a direct run from the devcontainer fails 127 because `/opt/tester-venv` exists only inside tester-unified). Result at commit `5dd9f880d` + report commit: verdict FAIL, exit 1, `2 failed, 1670 passed, 1 skipped`. The two failures, `test_no_stdlib_violations` (missing `allowed.add("collections")`) and `test_unreleased_changelog_matches_revision_and_recovery_contract` (`RG-81, rev 55`), are absent at my base `15b4fbe6a` (`git show` confirms neither the allowance nor the text), and newer `main` (`1628c31e3`, with the release-notes branch merged) already contains both fixes; they are not caused by this package. The skipped test is the real-container smoke (no docker inside tester-unified); it passed from the devcontainer (section 3).

Because pytest failed, the lane's `&&`-chained diff-coverage step did not run. I ran it by hand against the base with the lane's `coverage.json`: `diff-coverage FAIL: 123/139 changed executable lines covered (88.5% < 100.0% floor)`. Uncovered changed lines (line numbers as of `5dd9f880d`): `dual_mount_flags` out-of-root branch (8852-8853); `_make_private_dir` fallback loop (8964-8976); `git_mount_plan` relative-gitfile branch (8995-8996); `git_config_overlay_flags` missing-config refusal (9017-9018); the `--allow-main-checkout` flag handling in `main` (11789, 11792; reached only by the CLI subprocess test, which coverage does not trace). So the registered gate is NOT green for this package yet. Mutation (R2) not run (postponed by the operator).

## 5. Open items for the controller and the reviewer
- `__revision__` stays 55: `test_unreleased_changelog_matches_revision_and_recovery_contract` ties CHANGES text to the revision number; the release owner should bump it and the CHANGES marker together (this is a behaviour change, so the drift marker should move). My CHANGES entry sits under `[Unreleased]` with no version heading, and I did not touch `run-gate-release-notes-20261006`.
- Backlog id RG-85 was free on every branch I could see; the second to land should renumber on conflict.
- SPEC.md was not updated (normative text for the mount set would belong there).
- Not tested: ro common dir; an assay lane end to end in a container; Buildkite flow; nested devcontainer fallback of the temp dir; `config.worktree` overlay (code path exists, no test); container user whose uid differs from the checkout owner.
- cmru: its masking in `cmru/tools/run_release_gate.py` is removable after this ships; cmru tracks it.
- nyxloom: `nyxloom-merge-p` post-merge-from-main recipe needs the worktree/opt-in wording (see section 2).

## 6. Round 2 (fresh successor implementer, 2026-10-06)

Everything below is what I ran. Sections 1-5 are the round-1 text; where they disagree (backlog id, CHANGES location, baseline failures, coverage) this section wins.

### Merge and renumbering
- Merged `origin/main` (`1628c31e3`) into the branch (merge commit `3a212bb19`). Only conflict: `KNOWN_ISSUES_TODO_BACKLOG.md`. Main already owns RG-85 (".run-gate/ must exist before assay lanes launch"), so MY entry is now **RG-86** everywhere (backlog heading and index row, README, CONSUMERS x2, CHANGES, SPEC). Main's RG-85 had no index row; I added one (Minor, OPEN) beside mine.
- CHANGES: my entry already sat inside the `## [23.10.0]` section (git placed it under "Detailed Changes"); there is NO `[Unreleased]` text of mine. I added one pointer bullet under that section's `### Changed`. The `<!-- cmru: generated -->` heading and the `source rev 55` marker are untouched.
- `__revision__` NOT bumped (stays 55). Reason: the 23.10.0 marker test (`source rev {__revision__}`, and the `RG-84, filed as RG-83, rev ...` pin) ties the generated section to rev 55, and the section is not yet tagged, so this change ships inside that release without moving the drift marker. Consequence the release owner should weigh: this IS a behavior change, and copies of run-gate at rev 55 from before this package are indistinguishable by revision. Bump `__revision__` together with the marker and tests if that matters.
- Both round-1 baseline failures (stdlib allowlist, changelog-vs-revision) are fixed on main and no longer fail.

### New tests and one product fix
- `TestRgNarrowEdges` (6 tests, end of the RG-NARROW block): alias sub-path outside the aliased root (and inside it), `_make_private_dir` fallback when system temp has no host path, `_make_private_dir` with no usable candidate, relative gitfile in `git_mount_plan`, missing common config, and `--allow-main-checkout` applied in-process through `run_gate.main`.
- **Real defect found by the registered gate, fixed.** The first gate run through `cmru tester-gate` failed (about 80 tests, all `ERROR ... no host-visible private directory for the sanitized git config`): inside that container `/tmp` is not a bind mount (no host path), so `_make_private_dir` fell to `<repo>/.run-gate`, which does not exist in a fresh checkout, so `mkdtemp` raised and the lane died. The fallback now `mkdir -p`s `<repo>/.run-gate`. The earlier ad-hoc round-1 run did not show it because there `/tmp` resolved. The same defect would hit any real nested-devcontainer or fresh-checkout use of the fallback. The "no usable candidate" test now blocks the fallback with a regular file in place of the repo's parent directory; the "falls back" test now starts without `.run-gate`.

### Gate
- Command (from `run-gate-project/`, tree clean at commit `3291a1059`, one container at a time under `flock ... nice -n 19 ionice -c 3`, run as a tool-managed background job because a foreground call would exceed the tool timeout): `cmru tester-gate --cwd . -- ./run-gate.py selftest`. It needs the `CMRU_TESTER_*` variables that `cmru release` normally supplies; I exported the values from `cmru.orchestration.toml` (image `tester-unified:local`, memory 1g, swap 16g, cpus 2.5, pids 4096, the pinned probe image, cgroup parent `dev-gates.slice`). `cmru --version` prints `5.5.1.dev1401+g1628c31e3` (editable install of main; I did not verify what the 6.0 code reports).
- Run 1 at `6277a8ae0` (before the fix): verdict FAIL, exit 1, the failure above.
- Run 2 at `3291a1059`: verdict **PASS**, exit 0, read in a separate step: `1683 passed, 2 skipped, 1 warning`; `diff-coverage OK: 141/141 changed executable lines covered (100.0% >= 100.0% floor); branches 62/62 taken`. The lane's diff-coverage base is the merge-base with `main`, which equals `origin/main` (`1628c31e3`). Logs: scratchpad `narrow-selftest-r2.log` (run 1) and `narrow-selftest-r2b.log` (run 2). The two skips are not individually inspected; one is the real-container smoke (no docker inside tester-unified), which I ran separately from the devcontainer: `RgNarrow` + `TestArgvConstruction` + `TestExtraMounts` selection, `37 passed` there.
- Mutation (R2) not run (postponed). No plant-and-revert rerun after the merge; the round-1 plants predate the merge.

### SPEC
SPEC.md documents the dual-mount recipe (`R-15`, `R-23`), so I added normative `R-45` (a-d: linked-worktree mount set, credential-free config overlay, plain-checkout refusal/opt-in, exec out of scope) after `R-44`. `R-15`'s wording ("the repo dual-mounted") is NOT edited; `R-45` states that it supersedes it. A reviewer may prefer an in-place edit of `R-15`.

### Still open (round 2)
Unchanged from section 5, except revision/CHANGES/backlog-id items above. Not tested: ro common dir, an assay lane end to end in a container, Buildkite flow, `config.worktree` overlay content, uid mismatch. The `cmru.orchestration.toml`/`cmru tester-gate` env-var requirement is a usability note (the brief said `cmru tester-gate --cwd . -- ./run-gate.py selftest` "should work"; it needs the env first).

## 7. Round 3 (fresh implementer, review verdict ACCEPT-conditional, 2026-10-07)

Everything below is what I ran. Code+tests+docs commit `1d6d2aa47` on `78377a8b8`.

### B1. Credential-key denylist
`_DROP_GIT_CONFIG_KEY_RE` now drops whole `sendemail.*` and `imap.*` sections and any key ending (case-insensitive) in `pass|passwd|password|token|secret|apikey|api-key|api_key`; `strip_url_userinfo` treats any `<x>+http(s)` scheme (`git+https`, `svn+http`) as a web scheme. `test_no_credential_form_survives` gained `sendemail.smtppass`/`smtpuser`, `imap.pass`/`host`, `github.oauth-token`, `gh.myApi-Key`, `remote.o.url = git+https://tok@h/x`, `svn+http://u:p@...`. `test_non_credential_config_is_preserved` keeps `user.name`, `core.bare`, `core.passthrough` and the cleaned `git+https` URLs. Finding: git itself rejects `_` in variable names, so `gh.access_token` can never come out of `git config --list` (my fixture line failed to parse); the rule is covered by an entry-level test (`test_underscore_credential_suffixes_are_dropped`) instead. Known over-drop by design of the ruling: any key whose last segment merely ends in `pass` (for example `compass`) is dropped.

### B2. Private-dir whole-tree test
`test_private_dir_tree_holds_no_credential_in_any_file`, parametrized `system-temp` and `repo-run-gate-fallback` (system temp made non-host-visible, so the dir lands in `<repo>/.run-gate`): full fake-credential config appended to the repo config, then every file under the private dir is read and checked for every needle.

### D1. Prune hazard
`git_config_overlay_flags` now also (a) overlays each `<common>/modules/*/config` with a sanitized copy, (b) hides every sibling `<common>/worktrees/<name>` (all but the judged worktree's own admin dir; for a plain checkout all of them) under ONE empty 0755 directory mounted `:ro` at both mount paths. More than 256 submodule configs plus sibling dirs is an infrastructure error (`_MAX_GIT_OVERLAYS`). Tests: sibling hiding (own not hidden, one shared empty source, both paths ro), plain checkout, submodule overlay, cap. The REAL container smoke now runs inside the container: the sibling admin dir is empty and not writable, `git worktree prune -v || true`, the sibling admin dir still exists, `git status`/`log` still work, the assay-shape `worktree add`/`remove` still works; and from the host afterwards: `gitdir` of `w1` and `other` exist and `git worktree list` has 3 entries. The smoke ran for real (not skipped) from the devcontainer. The in-container script checks emptiness before running prune, so the plant below fails at that earlier check; I did not separately demonstrate the destructive prune against an unhidden dir in this round (that was the reviewer's observation).
SPEC `R-45b` (keys, modules) and new `R-45e`, README, CONSUMERS, CHANGES (23.10.0 detailed entry) and the RG-86 backlog entry document it.

### D2 / D3 / S16.4.9
- D2: `__revision__` stays 55 (unchanged).
- D3: `.run-gate` stays mounted read-write for assay lanes; a one-line follow-up (consider narrowing to `.run-gate/assay-state`; TMPDIR and the ceiling also point into `.run-gate`, see RG-85) is in the RG-86 backlog entry.
- The reviewer found no `git worktree add` in `assay/src` (the snapshot uses `clone --no-local`). The S16.4.9 read-write premise should be re-checked in the ciu v8 amendment; the controller files that. The RW decision is NOT changed here.

### Plants (each applied, run against `-k RgNarrow`, killed, reverted)
| Plant | Killed by |
|---|---|
| no sibling hiding (`siblings` filter forced empty) | 5 failed: `test_linked_worktree_mounts_only_worktree_and_git_dir`, `test_sibling_admin_dirs_are_hidden_under_an_empty_ro_mount`, `test_plain_checkout_hides_every_registered_worktree_admin_dir`, `test_too_many_hidden_git_dirs_are_an_infrastructure_error`, the real-container smoke |
| raw config copy written into the private dir | 2 failed: both `test_private_dir_tree_holds_no_credential_in_any_file` params (system-temp and fallback) |
| old key regex (no suffix rule, no sendemail/imap) | 5 failed: `test_no_credential_form_survives`, `test_underscore_credential_suffixes_are_dropped`, `test_non_credential_config_is_preserved`, both private-tree params |
| no `<x>+http(s)` scheme handling | 2 failed: `test_no_credential_form_survives`, `test_non_credential_config_is_preserved` |

`-k RgNarrow` before the plants: 27 passed (real container included); after the reverts the full gate below covers the same code (the committed tree is clean).

### Gate
`cmru tester-gate --cwd . -- ./run-gate.py selftest` from `run-gate-project/`, tree clean at `1d6d2aa47`, `CMRU_TESTER_*` exported from `cmru.orchestration.toml` as in round 2, foreground under `flock ... nice -n 19 ionice -c 3`, log `narrow-selftest-r3.log` in the scratchpad. Read in a separate step: `1690 passed, 2 skipped, 1 warning`; `diff-coverage OK: 156/156 changed executable lines covered (100.0%); branches 70/70 taken`; `lane 'selftest' verdict PASS; exit_code 0`. A docker-identity profiling WARNING (no docker socket inside tester-unified) is the same coarse-rusage notice as before. The real-container smoke is among the skips inside tester-unified; I ran it from the devcontainer.
Mutation (R2) not run.

## 8. Round 4 (fresh implementer; round-3 verifier REJECT: B-NEW-1, B-NEW-2; 2026-10-07)

Everything below is what I ran. Code commit `06d96b690`, merge of `origin/main` (RG-87) `b0778d958`, then a test-count fix commit (see `git log`).

### B-NEW-1 submodule configs
`_nested_git_config_files` walks `<common>/modules` with `os.walk` and returns every file named `config` or `config.worktree` at any depth (`modules/libs/a/config`, `modules/libs/a/modules/b/config`); in a dir holding `HEAD` the bulk dirs `objects/refs/logs/hooks/info` are not descended. Each gets a sanitized read-only overlay. Test `test_nested_submodule_configs_are_all_overlaid_and_nothing_leaks` plants credentials at four depths plus a `config.worktree`, checks every overlay, and scans every file under `modules/` as the container would see it (overlay copy substituted). The real-container smoke greps the whole common dir (objects excluded) inside the lane for the nested credentials.

### B-NEW-2 data loss (design change per controller ruling)
`git_worktrees_mount_flags`: `<common>/worktrees` mounted READ-ONLY with its REAL contents, the judged worktree's own admin dir mounted READ-WRITE on top (nested bind), each at both mount paths: 4 `-v` values, independent of the sibling count. Hiding code and the sibling cap are gone; the 256 cap remains for module plus sibling `config.worktree` overlays. Existing sibling `config.worktree` files get sanitized overlays. No `worktrees/` dir means no such mount. Plain checkout: whole `worktrees/` read-only.

Live results (real lane container `tester-unified:local`, throwaway repo from `_narrow_repo`, test `TestRgNarrowRealContainer`, one container at a time under flock):
- `git gc --prune=now` kept the detached-HEAD-only commit and the staged-only blob (`cat-file -e` in the container, then again on the host); host `git fsck --no-dangling` clean. gc prints `error: cannot lock ref 'worktrees/<n>/HEAD' ... Read-only file system` and `failed to run reflog` on stderr (it cannot expire sibling reflogs), yet exits 0 under `set -e` and the objects survive. Lanes that run gc will see that noise.
- `git worktree prune -v`: `failed to delete ... Read-only file system` for each sibling; the admin dirs (incl. the prunable `gone` one) survive on the host.
- `git branch -D held` (checked out in a sibling): `cannot delete branch 'held' used by worktree`, refused.
- Sibling `HEAD`/`index` visible, `touch` in a sibling admin dir and creating `worktrees/new-admin` fail.
- No credential (`FAKEPASSWORD123`, `fakeuser`, `FAKEBASIC456`, nested submodule `FAKEMODA`/`FAKEMODB`) found by a recursive grep of the common dir inside the container.
- Judged worktree: `status`, `log`, `commit --allow-empty` work; `git clone --no-local` (assay's snapshot shape) works.
- `git worktree add` in the container FAILS: `fatal: could not create directory of '<common>/worktrees/<name>': Read-only file system`. What breaks: any in-container creation of a new linked worktree (and `worktree remove`/`prune` of others). assay's snapshot uses `clone --no-local`, so it is unaffected; the smoke asserts the failure so the consequence stays visible. Anything needing `worktree add` must opt out of this mount set. The old smoke used `! cmd` under `set -e`, which does not abort; I replaced those with a `no` helper that really fails the lane.

### Docs
SPEC `R-45b` and `R-45e` rewritten, README, CONSUMERS and the 23.10.0 CHANGES bullet updated inside the existing section; `__revision__` untouched (55).

### Plants (applied, run against the RgNarrow selection, killed, reverted)
| Plant | Result |
|---|---|
| (a) `glob("*/config")` instead of the recursive walk | 2 failed; the real-container smoke (credential grep, seen in the log) is one, the other was not named in my filtered output (expected: the nested-submodule mount-plan test) |
| (b) round-3 empty-dir hiding of each sibling instead of the ro `worktrees/` mount | 3 failed: `test_linked_worktree_mounts_only_worktree_and_git_dir`, `test_worktrees_dir_is_ro_with_real_contents_and_own_admin_rw`, and the smoke, which with the gc step moved first dies at `git cat-file -e <detached HEAD commit>` (gc deleted it; `Device or resource busy` on prune) |
| (c) own admin dir mounted read-only | 3 failed: the same two mount-plan tests and the smoke (git exit 128 on the first in-container commit) |

### Merge
`origin/main` merged (RG-87 `6f07e95d7`): conflicts in `KNOWN_ISSUES_TODO_BACKLOG.md` (kept RG-85, RG-86, RG-87) and `tests/test_run_gate.py` (kept both test blocks; `shim_dir_of` is main's version). CHANGES merged automatically with both bullets in 23.10.0.

### Gate
`cmru tester-gate --cwd . -- ./run-gate.py selftest` from `run-gate-project/`, `CMRU_TESTER_*` exported per section 7, flock/nice/ionice. First run (merged tree): FAIL, `test_command_lane_full_docker_argv` still expected 6 mounts; fixed to 10. Second run, read separately: `1699 passed, 2 skipped, 1 warning`; `diff-coverage OK: 165/165 changed executable lines covered (100.0%); branches 72/72 taken`; `lane 'selftest' verdict PASS; exit_code 0`. Logs: scratchpad `narrow-selftest-r4.log`, `narrow-selftest-r4b.log`. Smoke is skipped inside tester-unified; I ran `-k "RgNarrow or full_docker_argv" -rs` from the devcontainer on the committed tree: 30 passed, no skips, no leftover containers.

Process note: in one message I issued two Edit calls without Intent lines (the `git_config_overlay_flags` docstring and the `container_mount_flags` call site), against the one-edit rule. Mutation (R2) not run.

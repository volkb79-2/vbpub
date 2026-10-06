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
(filled in below after the registered lane finished)

## 5. Open items for the controller and the reviewer
- `__revision__` stays 55: `test_unreleased_changelog_matches_revision_and_recovery_contract` ties CHANGES text to the revision number; the release owner should bump it and the CHANGES marker together (this is a behaviour change, so the drift marker should move). My CHANGES entry sits under `[Unreleased]` with no version heading, and I did not touch `run-gate-release-notes-20261006`.
- Backlog id RG-85 was free on every branch I could see; the second to land should renumber on conflict.
- SPEC.md was not updated (normative text for the mount set would belong there).
- Not tested: ro common dir; an assay lane end to end in a container; Buildkite flow; nested devcontainer fallback of the temp dir; `config.worktree` overlay (code path exists, no test); container user whose uid differs from the checkout owner.
- cmru: its masking in `cmru/tools/run_release_gate.py` is removable after this ships; cmru tracks it.
- nyxloom: `nyxloom-merge-p` post-merge-from-main recipe needs the worktree/opt-in wording (see section 2).

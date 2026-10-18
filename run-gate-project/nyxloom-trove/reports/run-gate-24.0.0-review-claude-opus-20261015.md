# Review of `cmru-release-readiness-20261015`, reviewed at HEAD `f466d5839`

**Verdict: Run-Gate 24.0.0 is not ready to release.** Three blockers: H1, H2, and RG-103, which another session filed on `main` during this review.

I made no edits, ran no tests or gates, and changed no containers. I only read files and used read-only `docker image ls/inspect/history` and `buildx ls`. HEAD and a clean status were the same before and after. Local `main` moved during the review from `f8b5c2026` to `da4b67f0b`. That commit files **RG-103**: `run-gate doctor` raises `KeyError: 'environment'` on any `kind="sequence"` lane in this candidate, and nothing here fixes it.

I reviewed the tip commit in depth, plus the bootstrap and release path. The branch has 159 commits, so the rest was only spot-checked.

## Confirmed defects

**H1. Release blocker: lazy import resolution (RG-100) makes imported lanes disappear from `footprint`, `history` and `doctor`.**
- Where: `run-gate-project/run_gate.py:14426-14436`. `doctor`, `history` and `footprint` are on the list of verbs that skip import resolution, so the import is never resolved for them, even when the judge is running.
- With `[assay.import] lanes = "all"`, the placeholders (`:7599`) include only imported names that a sequence references.
- `footprint --write` then builds its lane list from `sorted(lanes)` (`:6249`). It rewrites the footprint manifest with every imported lane's entry missing, and exits 0.
- `history <imported>` and `footprint <imported>` fail with "unknown lane" (`:6049`, `:6229`).
- `doctor` loops over `lanes` (`:9047`), so it skips imported environments but still reports a clean result. That is a false certification.
- All three worked before this candidate.
- Smallest fix: resolve the import for these three verbs. If the judge is unreachable, `footprint --write` should refuse rather than write a partial manifest. Add behavior tests for all three.

**H2. Release blocker: the default tester image has never been built from this Dockerfile, and its new apt step likely conflicts with the base.**
- Where: `tester-unified/Dockerfile:31-38`, which installs Debian's `docker-cli docker-compose`.
- The default MDT base already ships `docker-ce-cli` and `docker-compose-plugin` from download.docker.com (confirmed with `docker history`).
- The apt step arrived in `4ce6e3907`. `tester-unified:local` and `run-gate-candidate-cd673fecda57` were both built before it and don't contain it. The only image that does is `bootstrap-cold-check`, which uses the upstream Python base, not MDT.
- Likely result: dpkg refuses to overwrite `/usr/bin/docker` and the compose plugin. If apt resolves the conflict instead, it silently changes the gate image's Docker CLI version.
- This path is used whenever `RUN_GATE_TESTER_UNIFIED_BASE_IMAGE` is unset (`release_candidate.py:363`), which is the normal estate.
- The only test guard (`cmru/tests/test_tester_unified_image.py:3367`) checks that the string `"docker-cli docker-compose"` appears in the Dockerfile.
- Fix: install each tool only when it is missing (`command -v docker || …`), then build once on each base before release.

**H3. The bootstrap doc relies on an already-published artifact, so it can't work from zero.**
- `docs/BOOTSTRAP-FROM-ZERO.md:146-148` says to keep the default pinned mode for the Run-Gate release gate.
- That default is the existing published `cli-extended-v0.3.0` URL and digest `a0f80f1b…` (`Dockerfile:115-116`). It is not the artifact step 3 produces.
- From zero, step 3's rebuild has different bytes, so the release gate's `prepare` step (`release_candidate.py:500`) fails with a 404 or a digest mismatch.
- `cli-extended` v0.3.0 and v0.4.0 already exist here, so this procedure cannot have been proven from zero in this estate. Step 3's `--set-version 0.3.0` would also collide with the existing tag.
- `:169` uses `--cli-extended-resolve latest`, which contradicts `:147`.
- Fix: set `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE=latest` in step 4, or require committing the new pin before step 4. Fix the contradiction, and state that the from-zero path is unproven.

**M1. RG-98: the refusal tells host-mode users to apply a setting the config loader rejects.**
- `run_bare_host_lane` (`:13148`) forces the default `<repo>/.run-gate` state root.
- The refusal (`:9955-9968`) says to set `[environments.X].state_root`, but the loader rejects `state_root` for `mode="host"` (`:950-953`).
- Result: an R1-R3 host-mode lane judged in a primary checkout can never run, and the printed fix fails to load.
- No lane in this estate is affected (MDT's `vm-system` is R0 only). External `bare-host` consumers such as dstdns are exposed.
- Fix: a host-specific remedy (run from a linked worktree, or use a container mode), or allow a host path for `state_root`.

**M2. RG-99: the `GIT_CONFIG_COUNT` injection overwrites existing entries and quietly needs Git ≥ 2.38.**
- Where: `:10326-10328` and `:10448-10450` hard-set `COUNT=1` and `KEY_0`. Any `GIT_CONFIG_*` entries already in the host or runner environment are overwritten or cut off.
- The correct append-at-next-index pattern already exists at `:4030-4041` (and in `cmru/src/cmru/gate_snapshot.py:220`).
- Git honors `safe.directory` from command-line scope (which includes `GIT_CONFIG_*`) only from 2.38. Older lane images, including Ubuntu 22.04's 2.34 and backported security builds, silently ignore it. The lane then fails on "dubious ownership" and is reported as a product FAIL, which is exactly what RG-99 is meant to prevent. The old `GIT_CONFIG_GLOBAL` approach worked from Git 2.32.
- The old approach also kept lanes from reading `~/.gitconfig`; that isolation is gone and SPEC R-19a doesn't say so.
- The lock-free part is correct: no config file, no lock, no mutex. RG-99 is correctly still marked OPEN for exec mode.
- Fix: append at index `${GIT_CONFIG_COUNT:-0}` (still env-only and lock-free), and document the 2.38 floor.

**M3. The RG-99 tests check text, not the safety behavior.**
- `tests/test_run_gate.py:3346-3386` checks substrings and runs `git config --get` twice. Nothing shows that dubious ownership is actually suppressed.
- Fix: run the built command with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1 git status`. It should pass with the injection and fail without it, which also exercises the image's Git version.
- The backlog's contract silently dropped the old oracle "a forced setup failure yields ERROR". No command-lane setup step can fail any more, so that's acceptable, but it should be stated.

**L1. Run-Gate treats "no local tags" as a first release.** `release_candidate.py:136-150` and `coverage_gate.py:361-381` read only local tags, and the selftest always passes `--allow-first-release`. A clone without tags therefore reports diff coverage as SKIPPED with exit 0. CMRU, by contrast, asks origin and fails closed. Fix: allow first-release mode only inside a CMRU transaction (`CMRU_RELEASE_GATE_COMMIT` set), or check origin.

**L2. Overclaims in tests and docs.**
- The "RG-98 consumer fixture" test mocks the inventory and `subprocess`, so real Assay never runs. The backlog correctly says the fixture is still pending.
- CIU-141 cites CIU SPEC "S10.8", which doesn't exist; S10 is at `ciu/docs/SPEC.md:1867`.
- The RG-101 hint covers only `bare-host`. An undefined legacy `host` gets no migration hint.

**L3. CMRU checks only image labels.** `run_release_gate.py:478-489` compares labels, but its message says the image "does not contain" the wheel. The bytes are verified at build time (`Dockerfile:180`), but CMRU's gate never checks them inside the container. Reword the message, or reuse Run-Gate's `verify-running` check.

## Design risks and unverified claims
- **Base images can't be reproduced.** Both bases are mutable tags, and the remote `mdt-managed` buildx pulls them from the registry. `bootstrap-cold-check` got devcontainers release v0.4.35, while the local `3.14-trixie` is v0.4.33. No base digest is recorded in labels or the release receipt.
- **Build governance.**
  - `build-image.sh` takes `cmru tester-gate … /bin/true` exiting 0 as proof that the cgroup slice was verified. But `tester_gate.py` only warns when its probe can't decide, and the build proceeds.
  - Nothing shows that `--cgroup-parent` actually places RUN steps with the remote buildx driver.
- **The CGProfile → Run-Gate ordering edge checks nothing.** Run-Gate's release gate is `selftest` only. Its R2 needs a deployed observer, and the bootstrap doc deploys that after the CIU release (`:195-198`), which comes later. The release notes should say plainly that 24.0.0 ships without R2 qualification.
- **RG-98 makes the default state root unusable for R1+ lanes in a primary checkout.** This is loud and acceptable, but CHANGES should call it a behavior change.
- **Most new CMRU and tester tests are text greps** of the Dockerfile, `build-image.sh` and the docs (`test_tester_unified_image.py:3354-3389`, `test_release_candidate.py:1154-1162`). They don't show the image builds or bootstraps.

## CIU-141 / RG-102 assessment
Keeping both out of this candidate is right. These points need settling before implementation starts:
1. **`run --rm` conflicts with Run-Gate's lifecycle.** Today's lanes are detached, named, re-attached by name (R-39), removed explicitly afterwards, and need the raw exit status (RG-95). If the client dies under attached `--rm`, the container keeps running and its exit status is lost. Specify instead: `compose run -d --name <unique> --no-deps -T`, inspect it, emit the ID, follow its logs, `docker wait`, then remove it explicitly, plus a label-based reaper for crashes.
2. **Returning the exact ID "before completion" is achievable; "before start" isn't** without a start barrier. Today's R2 already starts its observer session after `docker run -d` (`:12345`). RG-102's oracle should say which guarantee is required.
3. **The event channel can't be stdout**, since that carries the lane's output. Use `--event-fd` or an events file of JSON lines: `launched{container_id,name}`, then `exited{status,cleanup}`.
4. **"The service declaration owns everything" breaks on per-lane facts Run-Gate owns:**
   - the judged worktree and `.git` mounts, chosen per invocation;
   - the state-root mount and `--init`;
   - per-lane memory/cpus/pids limits and admission accounting;
   - the gate tier (`$CGROUP_PARENT_DEV_GATES`), whereas CIU services resolve the background tier;
   - the observer's `--group-add` and read-only socket mount.

   `compose run` can't express most of these. CIU needs a typed "lane overlay" (an override file with a fixed set of keys), or RG-102 will bypass CIU or lose the RG-89/RG-80 guarantees.
5. **`ciu down` or `reset` would remove running lanes.** Both run `compose down -v --remove-orphans` (`ciu/src/ciu/engine.py:854`), which removes one-off containers and their volumes mid-run. CIU-141 must define what happens and tie it to the existing CIU-104/105 guards.
6. **Default to `--no-deps`.** `compose run` starts `depends_on` services by default. Check dependency health beforehand and refuse instead.
7. **TTY and signals.** Always use `-T`. Define how SIGINT/SIGTERM are forwarded and what exit status an interrupted lane gets.

## Smallest path to release
1. Fix H1.
2. Fix M2 and add the M3 test.
3. Fix M1's remedy text.
4. Make the H2 apt step conditional and build the image on both bases.
5. Fix the H3 doc.
6. Fix RG-103.

Then run the registered `run-gate selftest`, `assay-r1` (from a linked worktree, since a primary checkout now hits RG-98's refusal) and the CMRU gate.

Live acceptance still needed:
- **RG-99:** two concurrent `docker exec` lanes in one single-user runner. Both see `safe.directory=*` and no `/tmp/run-gate-gitconfig*` file exists.
- **RG-98:** real Assay reaches R0 with an external `state_root`.
- **RG-100:** with the runner stopped, `--list` and a host lane's `--dry-run` still work, and an imported lane is refused with a remedy.
- **H2:** image builds on both bases.
- **From zero:** one rehearsal against a scratch forge.

Write is disabled in this session, so I couldn't save this as the plan file; everything is in this message.

Sources (Git `safe.directory` command-line scope, 2.38 "protected configuration"):
- [Documentation: define protected configuration](https://lore.kernel.org/all/xmqqbkvi1owx.fsf@gitster.g/t/)
- [git/git PR #1261](https://github.com/git/git/pull/1261)
- [2.38 regression: respect includes in protected config](https://lore.kernel.org/git/xmqqmta0kbkh.fsf@gitster.g/T/)
- [Git v2.33.2 safe.txt (pre-change wording)](https://code.googlesource.com/git/+/refs/tags/v2.33.2/Documentation/config/safe.txt)
<!-- Claude Opus 5.5 high, retained session 9e026c74-5cd0-4a0f-824c-3d2539a854c3. Review snapshot HEAD was recorded by the session as 336336d81; the session could not re-verify HEAD or diff hash due read permissions. -->

# Read-only follow-up: KI-65, MDT `--observer-only`, CMRU KI-72, B165

**Revision binding.** I couldn't record HEAD or the diff hash this turn. Read is denied in this session, and Grep is denied outside the worktree, so `.git/worktrees/…/HEAD` and the refs are unreachable; the hash would need Bash. The session-start snapshot showed HEAD at `336336d81` ("chore: integrate contract-2 release wave"), but that is not re-verified. Every line number below was read from the live working tree through Grep and Glob.

**Overall:** none of the four questions adds a release blocker. Two Medium items should land in this wave: KI-65 restore when a nested worktree vanishes, and MDT host-check plus the hollow test halves.

## 1. KI-65: nested worktree secret overlays (CMRU, not Run-Gate)

**Conclusion: the pass-9 behaviour is correct. Pruning descendants that contain a `.git` entry would create a real exposure, and the earlier recommendation should be withdrawn.**

- **Why including nested worktrees is right:**
  - The CMRU release gate refuses a main checkout before masking (`cmru/tools/run_release_gate.py:523-535`).
  - It inventories from the selected linked worktree (`:565`), and `os.walk` descends into everything except caches and symlinks (`:401-414`).
  - Run-Gate mounts that whole worktree path, so anything physically inside it is visible in the tester, including nested worktrees and nested clones.
- **Concrete gap if `.git`-bearing directories were pruned:**
  1. An agent working in `/workspaces/vbpub/.worktrees/cgprofile-cp20-contract2-20261012` runs `git worktree add .worktrees/hotfix`. Its `.git` is a gitfile, and `.worktrees/` is ignored (`.gitignore:322`), so nothing in status flags it.
  2. The operator drops `cmru.secret.toml` (also ignored, `.gitignore:340`) into `.worktrees/hotfix/`.
  3. With pruning, that file is never inventoried or masked. It stays readable at the same path inside every lane container.
  - "Readable" assumes the tester's uid can read it. The image runs as `USER 1003` (`tester-unified/Dockerfile:223`), with no `--user` for tester-unified (`run-gate.py:11936-11946`). That is the same assumption the whole masker already rests on.
- **Test:** `test_linked_worktree_inventory_includes_nested_worktree_secrets` (`cmru/tests/test_release_gate.py:233-244`) is the correct oracle; keep it.

**What KI-65 still needs:**

- **Medium: restore fails when a nested worktree is removed during the gate.**
  - The KI-65 symptom (`KNOWN_ISSUES_TODO_BACKLOG.md:1752`) now applies only to nested worktrees.
  - Sequence: the nested worktree is removed mid-gate, so `lstat` raises FileNotFoundError and `current` becomes None (`:140-142`). `os.open(..., O_CREAT|O_EXCL)` at `:186` then fails because the parent directory is gone. `_restore` raises (`:201`), and the gate fails after every lane passed (`:328-331`).
  - Variant: if the worktree is removed and recreated at the same path, `:186` succeeds and writes the old credential into a fresh checkout.
  - No test covers either case; `test_release_gate.py` has no vanished-parent restore test.
  - **Repair:** record the parent directory's `(st_dev, st_ino)` at mask time. On restore, if the parent is gone or its identity changed:
    - do not write the secret;
    - keep the 0600 backup;
    - print a WARN naming the original path and the retained backup;
    - don't turn a green gate red.
  - **Oracle:** inside a mocked lane, `rmtree(linked/.worktrees/nested)`. Assert the gate returns 0, the WARN names both paths, and the backup bytes equal the original. Then recreate the directory and assert no secret appears in it.
- **Low:** the exact-gate evidence is still pending (backlog status line `:1752`).
- **Low, optional hardening: the skip rule compares the wrong uid.**
  - `traversal_failed` (`:379-398`) skips directories owned by another uid that it can't list. Its comment says these are "equally unlistable for the gate container's mapped uid".
  - The code actually compares the owner with the *caller's* euid, but the container runs as 1003. A 0700 directory owned by 1003 (one a tester created) is skipped here yet readable in-lane.
  - Exploiting this needs a caller-owned secret inside such a directory, which is contrived.
  - **Fix:** compare against the lane uid, or correct the comment.

## 2. MDT `install.sh --observer-only`

**Does it refuse every drift before writing? No.**

- **Refused before any write:**
  - control directory missing, a symlink, or not a directory (`install.sh:113`);
  - docker GID unresolvable (`:119`);
  - control directory metadata other than `0 <docker> 770` (`:124`);
  - an existing observer group that aliases the docker GID (`:132`);
  - a symlink at the observer path (`:137`).
- **Not refused before writing:**
  - **Wrong type at the observer path** (regular file or socket). The units are installed (`:142-143`) and `systemd-sysusers` runs (`:144`) before tmpfiles fails. The error is a generic "systemd-tmpfiles --create failed" (`:153`) that doesn't name the path.
  - **Wrong owner, group or mode on an existing directory.** A tmpfiles `d` line adjusts the mode and ownership of an existing directory. The directory is silently re-owned, and the post-check (`:158`) then passes.
- **Not a blocker:**
  - The re-own converges on the exact contract, and the socket inode inside survives.
  - The docs promise only the pre-checks plus post-validation (`docs/DESIGN-GUIDE.md:105-110`), which matches the code.
  - **Optional hardening:** if `$observer_directory` exists, require a real directory whose metadata equals `0 <existing observer gid> 751`. If the group doesn't exist yet, any existing directory counts as drift. Otherwise refuse before `:142`, with a remedy naming the path and the found metadata.

**Remedy text:**
- `:114` ("run the full host setup first") is correct; the full install creates `/run/cgprofile` at `:712-713`.
- **Low:** `:125` has no remedy. It should say what `check.sh:365` says: `sudo systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf` or the full install.

**Does `mdt-host-check` validate the observer directory? No. Medium.**
- `host-setup/scripts/check.sh:352-379` checks only `/run/cgprofile` (770 root:docker) and reports `ctl.sock` as info.
- Nothing checks:
  - `/run/cgprofile/observer`;
  - the `cgprofile-observer` group, or that its GID differs from docker's;
  - `/etc/tmpfiles.d/mdt-cgprofile-observer.conf` or `/etc/sysusers.d/mdt-cgprofile.conf`.
- Impact: after a reboot with a missing rule, host-check reports healthy, and the first observer lane fails at Run-Gate's mount preflight. That fails closed but late.
- MDT-006's "live post-install stat" oracle (`KNOWN_ISSUES_TODO_BACKLOG.md:63-64`) is a one-off with no persistent checker.
- **Repair:** add a FAIL block to `check.sh` mirroring `:158`:
  - metadata equals `0 <gid> 751`;
  - the GID differs from docker's;
  - both unit files are present and byte-equal to the shipped copies;
  - remedy: `sudo ./host-setup/install.sh --observer-only`.

**Behavioural tests (`host-setup/tests/test-render.sh`).**
- **Real coverage:**
  - missing control directory is refused before any write (`:346-352`);
  - clean run in sysusers → tmpfiles order, without rewriting the control rule (`:354-403`);
  - docker-GID alias refused before writes and before any systemd call (`:405-421`).
- **Gaps (Medium, test-coverage):**
  - The `stat` mock (`:383-393`) always returns the right owner and group. The owner/group half of `:158` and the control-directory refusal at `:124` are therefore never exercised with a mismatch: a regression that dropped those comparisons would still pass.
  - No idempotent second run.
  - No file, socket or symlink at the observer path; the `:137` refusal is untested.
  - No pre-existing drifted directory.
  - No `ctl.sock` placed in the throwaway control directory, so control-socket survival is untested. tmpfiles is mocked, so this can't show real `d` semantics anyway.
  - The `--observer-only` dispatch and the flag-combination refusal are only source greps (`:301-331`).
- **Fix:** make the `stat` mock read metadata that the mock tmpfiles records, and add mismatch cases.
- **Acceptance evidence, using the existing VM runner (`host-setup/tests/run-vm-acceptance.sh`):**
  - run twice and compare `stat -c '%u %g %a %i'`;
  - a pre-created file, then a symlink, are each refused with `/etc` unchanged;
  - a `ctl.sock` bound beforehand keeps its inode;
  - after a reboot the directory is recreated to the contract.
- **True blocker:** still only the live operator install plus stat evidence (R3 from pass 4).

## 3. CMRU KI-72: Assay source cleanliness

**Ordering** (`cmru/tools/mutation_campaign.py`):
1. clean check (`:975`);
2. `sys.path` prepend (`:991`) and eager imports (`:992-995`);
3. binding check (`:997`);
4. clean check again (`:1001`).
- Some modules load lazily inside functions: `assay.resource_limits` (`:260`), `assay.process_tree` (`:137`) and `assay.errors` (`:288`). They load after `:1001` and are covered by the `:1115` check.
- Candidate subprocesses inherit `os.environ` (`:147`). They import Assay through the lane's editable install of the live tree (`cmru/run-gate.toml:111`), so every candidate can load on-disk Assay afresh. `PYTHONPYCACHEPREFIX` (`:148`) correctly rules out stale `.pyc` files.

**Is the pre-import check enough to *prevent* running uncommitted Assay?**
- No, and no snapshot check can be. The design detects persistent changes at checkpoints `:975`, `:1001`, `:1115`, `:1185`, `:1306` and `:1356`.
- A persistent edit stops the campaign with `infrastructure-error`. The affected candidate is never recorded, because `:1306` runs before `:1323`. Resume reuse is bound to the commit plus the clean check.
- An edit → run → revert inside one window evades all of them. That is acceptable for the realistic threat (a concurrent or forgotten edit). It is not a security boundary, but a writer who can do that can also edit `cmru/tools`, which lives in the same tree.

**The repeated check after import:** defense-in-depth. It closes the window around the eager imports. It is neither false assurance nor a blocker, as long as the wording says "observed clean at each checkpoint" rather than "proved". The current messages ("requires a clean Assay source tree") are fine.

**Behavioural oracles that prove the property:**
1. **Sentinel on import.**
   - Setup: a committed Assay copy in a fixture repo. Separately, dirty a tracked `analysis/src/.../__init__.py`, add an untracked `src/assay/_x.py`, and add an ignored file that isn't generated. Each writes a sentinel file when imported.
   - Expect: a ValueError at `:975`, no sentinel, no evidence file, and no `_run` call.
2. **Persistent edit mid-run.**
   - Setup: on candidate k, the test command modifies a tracked `analysis/src` file and exits 1.
   - Expect: `infrastructure-error` "during candidate k" and `results[k] is None`. After a `git checkout`, `--resume` re-runs k rather than reusing it.
3. **Subprocess binding (Low gap).** A candidate command prints `assay.__file__`; assert it lies under the `--assay-source` package roots. In-process binding is checked at `:997`, but the venv's editable target never is. It matches in the registered lane (`run-gate.toml:111` and `:120`), but not necessarily off-lane.
4. **If prevention is ever required (follow-up, not this wave):**
   - Run from an immutable `git archive <assay_source_commit>` snapshot, both in-process and through `PYTHONPATH` for candidates.
   - Oracle: an edit-and-revert to live Assay mid-run never fires the sentinel, and the evidence records the snapshot's tree hash.

## 4. B165 `SA_NOCLDWAIT`

- **Supported boundary:**
  - one isolated CLI process, `jobs=1`, one window at a time;
  - B165 is explicitly "not a release blocker for the standalone CLI contract" (`assay/nyxloom-trove/4-backlog.md:12742-12747`);
  - embedders are told to avoid `SA_NOCLDWAIT` (`README.md:550-560`, `docs/CONSUMERS.md:3458-3468`).
- **Relevance to a supported CLI run: none.**
  - Linux clears every `sa_flags` value on `execve`. An inherited `SIG_IGN` disposition *does* survive exec, which is why the CLI resets it (`cli.py:617-630`) and the window checks the ignore mask (`process_tree.py:194-207`, `:315`).
  - CPython's `signal` module cannot set `SA_NOCLDWAIT`.
  - Candidate commands run in separate exec'd children and cannot change the parent's flags.
  - CMRU's campaign is also exec'd fresh (`run-gate.toml:119`).
  - So only native code embedded in-process could set the flag. If it did, `subprocess.wait()` would map ECHILD to exit 0, and a failing baseline would read as a pass — a real hazard, but only in unsupported embedding.
- **Recommendation: don't add it this wave.**
  - No standard API reads `sa_flags`, and `/proc/self/status` exposes only SigIgn and SigCgt. A ctypes `sigaction` call needs a per-architecture struct layout, which is fragile.
  - A behavioural probe would be portable, at roughly 1 ms per window: fork a child that exits 0, then require `waitpid` to return that pid rather than ECHILD. But it is time-of-check-to-time-of-use, and it must run before the no-children precondition.
  - A supervisor process or per-candidate cgroup, as B165 proposes, makes the probe unnecessary.
- **Low (docs):** B165's own oracle (`4-backlog.md:12753-12755`) doesn't mention `SA_NOCLDWAIT`; the only cross-reference is at `:12678`. Add it to the oracle, and note in the DESIGN-GUIDE that exec clears the flags, so nobody reopens this for the CLI.

Nothing was edited, run or committed. The session remains available for follow-up.

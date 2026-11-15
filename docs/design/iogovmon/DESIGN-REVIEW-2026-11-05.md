<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/iogovmon-DESIGN-REVIEW.md -->
<!-- relation: iogovmon adversarial review -->

# Adversarial review: iogovmon-DESIGN.md (rev 8), read-only, verified against real code

Reviewer: Sonnet (fresh, not a fork). Date 2026-11-05. Code read: `.worktrees/iogovmon-20261105/iogovmon` (HEAD 70aac26bc), `.worktrees/iobench-wp0-20261105/libraries/iobench` (HEAD fa8affe5e), `.worktrees/host-tuning-wp1-20261105/libraries/host-tuning` (15903c1be), `mdt-cockpit/host-setup`, `scripts/debian-install-v2/debian_install_v2`, `scripts/cgroup-profiler` (cgpd). No file edited, no host touched.

**Verdict: REVISE.** The shape is right (one project, probes unprivileged, one root writer, iobench a subprocess, proven-only, kill switches). But two load-bearing statements are false as written: the root state tree is not protected from the probe user (B1), and "single writer" is not true on real hosts (B2). Several "reuse" and "existing code" claims are wrong in ways that move the work-package sizes (section 2). The critical path is also ordered so that nothing usable ships until the actuation controller is done (section 3).

Severity: BLOCKER = must change before any WP is dispatched; HIGH = change before the WP it touches; MEDIUM = fix in the WP; LOW = note.

---

## 1. Ranked findings

### B1 (BLOCKER) The root state tree lives INSIDE the probe user's directory

Design s.4: `/var/lib/iogovmon/` is "user `iogovmon` 0700" and `/var/lib/iogovmon/ctl/` is "root 0755" (apply journal, manifest, overlay, registry, bench/proof/accepted, `apply.lock`, actuation audit).

Problems:
1. The owner of a directory can rename any entry in it (no sticky bit). The unprivileged `iogovmon` user can `mv ctl ctl.x; mkdir ctl` and populate a forged `apply/journal.jsonl`, `overlay.json`, `proof.json`, `accepted.json`, `registry.json`. The root core and `iogovmonctl` (root) then trust them. `revert` is the worst case: the journal carries `files:[{path, sha_before, sha_after}]` and revert "removes rendered files by marker" and restores `prior` values, so a forged journal is an arbitrary file delete/overwrite and an arbitrary sysfs/proc write as root. This breaks the whole "probes can change nothing on the host" statement (s.2) by one rename.
2. Contradiction inside the document: a 0700 parent makes `ctl/` (0755) unreachable for the "read-only verbs as invoking user" (`status`, `stats`, `doctor`, `config show`); `status --json` reads `ctl/` state.
3. The existing unit sets `StateDirectory=iogovmon` + `StateDirectoryMode=0700` with `User=iogovmon` (`units/iogovmond-tick.service.in`, `iogovmond.service.in`). systemd chowns the state directory (recursively when the top-level owner differs) to the service user at unit start, so a `ctl/` created by a root `install` before the first probe start can be re-owned to `iogovmon`.
4. Existing probe safety code (`safeio.py:63-99`, `config.py` `state_dir`/`scratch_dir` defaults) assumes everything under `/var/lib/iogovmon` is the running uid's; nothing guards the reverse direction (root reading below a user-owned parent).

**Change:** two sibling trees, root-owned parent.
- `/var/lib/iogovmon/` root:root 0755 (root core + ctl state: `apply/`, `bench/`, `registry.json`, `quiet.json`, `actuation.jsonl`, `apply.lock`; created by `install`, never by systemd `StateDirectory`).
- `/var/lib/iogovmon/probe/` iogovmon:iogovmon 0700 (existing probe `state.json`, `journal/`, `scratch/`, locks). Units use `StateDirectory=iogovmon/probe` (systemd only chowns that leaf) or an install-time directory without `StateDirectory`.
- `/run/iogovmon/` root 0755 for runtime lock/overlay flag.
- Journal replay must NOT trust paths in the journal: each txn stores `item` ids only; `files`/`prior` targets are re-derived from the registry's exact-path allowlist (the same table the write guard uses), and a txn whose recorded path is not in the allowlist for its item is quarantined, never replayed.
- Root reads of probe-owned files (journal, state.json): open every path component with `os.open(..., O_NOFOLLOW|O_DIRECTORY, dir_fd=)`, `fstat` regular file + `st_uid == iogovmon uid` + size cap, never `Path.read_text`. The existing `any_owner` readers (`safeio.py:93`, `journal.py:141`) accept ANY owner; the root core must use a stricter `owner == service uid` mode.
- The config key `state_dir`/`scratch_dir` defaults change (probe side) and `Config` gets a separate root-side `ctl_dir` that is not config-overridable by the probe-readable file (see H2).

### B2 (BLOCKER) "Single writer" is false on every host that matters; no ownership matrix exists

s.9 says only `iogovmon.apply` writes host settings and the "old writers are retired in the same transaction". Real code says otherwise:

| Other writer | Evidence | Collision with iogovmon |
|---|---|---|
| mdt reconcile re-asserts scheduler, queue values, `vm.dirty_*`, io.cost EVERY MINUTE against `host-setup.env` | `mdt-dev-governance-reconcile.sh:262-266` calls `_mdt_reassert_io_settings`; `mdt-container-io-caps.lib.sh:1502-1517` (`_mdt_reassert_udev`, `_mdt_reassert_sysctl`, `_mdt_reassert_iocost`); timer `mdt-dev-governance-reconcile.timer.in` | Any overlay value (io.cost qos, dirty_bytes, nr_requests) is reverted within 60 s; the controller sees "drift", counts it, backs off. In `adopt` mode (s.3, IG-15 "adopt first") mdt keeps all its writers, so adoption plus default-ON actuation = two live controllers. mdt has a pause file (`/run/mdt/io-reassert.pause`, `mdt-io-reassert-pause.sh`) but the design does not use it. |
| `mdt-psi-guard.py` owns tier `io.weight` (sets 1 under pressure, lists keys in `/run/mdt/psi-guard/state.json`) | `mdt-psi-guard.py:23,300-322`; reconcile.sh:301-310 documents it | The io.weight knob (s.8) reads "the unit's configured weight" as baseline, but live value is temporarily 1; `systemctl set-property --runtime IOWeight=` fights both the guard and systemd's own re-apply on daemon-reload. Two feedback controllers on the same pressure signal. |
| `iobench matrix`/`run` write dirty_bytes, nr_requests, max_sectors_kb themselves, with their own journal | `iobench/host.py:78-83` (`Matrix.apply` via `safety.set_knob`), `restore.py:15` `JOURNAL=/run/iobench/restore.json`, cli `run` = calibrate + matrix (`cli.py` `_run`) | s.1/s.5 say `benchmark run --profile ...` wraps `iobench`; `run` and `matrix` ARE knob writers, outside `apply.lock`. s.11 says the knob allowlist is "no longer needed for the daemon" but standalone matrix still writes. Wrap `calibrate` only (never `run`/`matrix`) or hold actuation frozen plus overlay reverted during the run. |
| Installer legacy files stay on existing hosts | `templates.py` writes `/etc/sysctl.d/99-vbpub-swap.conf` (dirty 15/5, swappiness etc., `templates.py:211-214`), `zswap-config.service`, `vbpub-min-free-floor.service`, `/etc/tmpfiles.d/vbpub-{ksm,thp}.conf`, `cgroup2-flags.service`, `/etc/modules-load.d/vbpub-zstd.conf`, `vbpub-iocost.service` | The design renders `61-iogovmon-*.conf`; sysctl.d applies files in lexical order so `99-vbpub-swap.conf` silently WINS at boot over `61-`. Live apply and reboot then disagree and `check` reports "drift" forever. The adoption list in s.6/IG-4 covers only `vbpub-iocost`/`mdt-io-cost`. |

**Change:** add a section "Ownership matrix" and make it executable:
1. iogovmon's rendered sysctl files use a prefix that sorts AFTER every known legacy one (for example `/etc/sysctl.d/99-zz-iogovmon.conf`), AND install removes (inside the txn, journalled) the legacy files for items it takes over: `99-vbpub-swap.conf`, `zswap-config.service`, `vbpub-min-free-floor.service`, `vbpub-{ksm,thp}.conf`, `cgroup2-flags.service`, `vbpub-zstd.conf`, `vbpub-iocost.service`+`/etc/vbpub/iocost-model`+`/usr/local/sbin/vbpub-iocost-setup`. Items not taken over keep their old owner; `doctor` lists any key defined in two places (sysctl keys across `/etc/sysctl.d/*`, `/usr/lib/sysctl.d`, `udevadm test` rule order).
2. Per-key ownership record in `manifest.json` (`owner: iogovmon|mdt|installer|external`); the daemon refuses to actuate any key whose owner is not iogovmon, and `doctor` prints the conflict.
3. `IOGOVMON_MODE=adopt` forces `actuation = off` AND no baseline writes (state it in s.3). Only `managed` may enable actuation, and only after mdt's reconcile reads iogovmon's `overlay.json` (or is stopped for the keys iogovmon owns). mdt cannot be "respecting the overlay" until that mdt change ships; IG-15 managed is therefore a prerequisite of default-ON actuation on mdt hosts, not a later cleanup.
4. Drop the `io.weight` knob from the first release (psi-guard owns it; see section 3 cut list).
5. During any iobench run iogovmond takes `bench.lock`, reverts the overlay to baseline (A must be the current baseline), and freezes the controller; it never shares the lock with `apply.lock` (an hour-long standard run must not block kill-switch rollback, see M1).

### H1 (HIGH) Root unit hardening does not fit what the root unit must do; one avoidable root-code-exec path

s.2 lists `ProtectSystem=strict` with `ReadWritePaths=/etc/sysctl.d /etc/udev/rules.d /etc/tmpfiles.d /etc/modules-load.d /etc/systemd/system /etc/iogovmon /var/lib/iogovmon /sys /proc/sys`.
1. It spawns `iobench`/`fio` as children (s.1, s.7). iobench writes its restore journal to `/run/iobench/restore.json` (`restore.py:15`), needs a work dir on a data filesystem (`guard.check_workdir`, `cli.py` `--work-dir`) and, for `target init --file`, the target path. None is in `ReadWritePaths` and `/run` is read-only under `strict`. Result: `iobench` fails inside the sandbox. `TasksMax`/`MemoryHigh` also bound fio.
2. The daemon can write `/etc/systemd/system` and `/etc/udev/rules.d`: any bug in the write guard becomes a unit file written as root, hence code execution at next start. Rendering to /etc is only needed at install and when a tier-2 value becomes proven. The long-running core does not need it.
3. Package location: `install.sh` currently installs a copy into `LIB_DIR=/usr/local/lib/iogovmon-pkg` and the units set `Environment=PYTHONPATH=...`. The script itself notes `/usr/local/lib` is `root:staff 2775` on Debian. A root service with `PYTHONPATH` into a group-writable parent is a local privilege escalation. cgpd's pattern is a root-owned venv (`/opt/cgprofile/current/venv`, `cgpd.service.in` `ExecStart=@WHEEL_VENV@/bin/cgpd run`, no environment).
4. `CapabilityBoundingSet=CAP_SYS_ADMIN ...` is nearly root; the real control is the write guard, so the guard must be tested as the boundary (see below).

**Change:**
- Core `iogovmond.service` RW set: `/sys`, `/proc/sys`, `/sys/fs/cgroup`, `/var/lib/iogovmon`, `/run/iogovmon`. No `/etc/*` and no `/run/iobench`.
- Persisting a proven tier-2 value to /etc, benchmarks, synthetic verification and swap borrow run as separate transient root units (`systemd-run --unit=iogovmon-job-<id> --property=...` or templated `iogovmon-job@.service`) with their own, wider RW sets (`/run/iobench`, work dir, `/etc/...`) and `RuntimeMaxSec`; the core only starts and observes them. The core never forks fio/iobench itself. This also gives the iobench processes their own cgroup (`iogovmon.slice/iogovmon-job-<id>.service`) so `systemctl stop` kills the whole fio tree, which iobench relies on for killability.
- Install into a root-owned versioned venv outside `/usr/local`, absolute `ExecStart`, no `PYTHONPATH`; `Host.run` resolves `iobench`, `fio`, `systemctl`, `udevadm`, `sysctl` to absolute paths owned by root and not group/world-writable at start.
- Write-guard feasibility: exact-path allowlist is feasible for sysctl (`/proc/sys/vm/*` fixed names), zswap params, `/sys/class/block/<disk>/queue/<knob>` (disk resolved by root from the registry, never from probe data), `/sys/fs/cgroup/io.cost.{model,qos}` (line `MAJ:MIN ...` content-validated), and rendered files with the `# managed-by: iogovmon` marker. It is NOT enough alone: the guard also needs content validation per target (value grammar and bounds in code) and `O_NOFOLLOW`/`openat` writes with a post-open `fstat` check (see H3, the shelved `RealHost.write_text` follows symlinks and is non-atomic). The static test "only `apply`/`host` may call a write/exec primitive" is feasible by extending `tests/test_static.py` (it already walks the AST and allow-lists call sites at `test_static.py:23-48,60-105`); keep the per-call-site allow-list style, do not weaken it to per-module.
- Comparison with cgpd (`scripts/cgroup-profiler/src/cgprofile/data/cgpd.service.in`): cgpd is a single root process (`User=root`, `ProtectSystem=strict`, `ReadWritePaths=/run/cgprofile /var/lib/cgprofile /sys/fs/cgroup /sys/kernel/mm/damon`, `AF_UNIX`, `PrivateNetwork`, `NoNewPrivileges`, no capability bounding set) that parses untrusted cgroup/container names as root with a path whitelist (cgpd DESIGN D-25). So iogovmon's split is stricter than cgpd, and the "6k-line observer must not be root" argument is also true of cgpd, which accepts it. The split is justified by the raw-device reads and scratch writes (which cgpd does not do), not by cgroup parsing; say so in s.2, and align the common knobs (`LockPersonality`, `RestrictRealtime`, `Restart` back-off, `MemorySwapMax=0`, `StartLimit*`) with cgpd so the two root units read alike. Neither unit is sandboxed against its own writes beyond the guard; consistent.

### H2 (HIGH) The unprivileged recorder CAN steer root actions; make trust asymmetric

The design's mitigation (schema validation, bounded reads, bounds layer) caps the size of a step but not its direction, target or timing. A compromised or buggy probe (it parses cgroup and container names that tenants influence, opens a raw device, and writes `journal/*.jsonl`) can:
1. Forge sustained `distress` on a stable lane: up to 4 bounded changes per day, 2 per knob, within +-50 % drift of baseline: a bounded but real degradation, repeatable forever (flap caps limit the rate, not the harm).
2. Choose targets: `governed_cgroups = []` means "auto-discover" (s.3). If the root core takes names from probe output, it can pick any unit for `systemctl set-property --runtime <unit> IOWeight=`; a unit name beginning with `-` or containing `=` is an argv injection unless `validate_argv` pins exactly the allowed argument grammar.
3. Feed `iobench ceilings add --kind probe|file` observations (s.7) so stability confirmation is attacker-influenced: it can suppress a demotion (always stable) or force one (permanent freeze = denial of service of the controller).
4. Shape the quiet-window profile (`quiet.json`) so heavy synthetic load or a benchmark starts at a chosen time.
5. Stall the core with huge/sparse files, FIFOs, deep directories, symlink swaps between check and use (see B1; bounded reads exist but the owner/TOCTOU part does not).

**Change (the one new design rule):** asymmetric trust. Probe-written data may only make iogovmon LESS active: freeze, demote, observe-only, extend a cooldown, mark stale. Anything that enables, raises, targets or schedules (distress detection that proposes a step, target selection, quiet window, proof confirmation count that unlocks `proven`) is computed from data the ROOT core collects itself (PSI, `io.pressure`, `io.stat`, device stat: these are about 20 lines each in `collectors.py`, which is stdlib and importable by the core; the core runs them in-process, not through the journal). Probe journals feed (a) the recognizer's `capability_shift` (a freeze signal), (b) reports. Concretely: governed cgroups/targets come only from the root-owned TOML (explicit list or root-side discovery with a `^[A-Za-z0-9_.@:-]+$` unit grammar and no leading `-`), never from probe state; ceilings observations that unlock `proven` come only from raw calibrations produced by root-run iobench plus root-collected file/probe-class samples (the scratch bench can still run as probe, but its `bps` is re-measured/validated by root from `/sys/block/*/stat` deltas or only used to demote). Add a test: "mutating the probe journal to any content never increases the set or magnitude of proposals" (property test on FakeHost).

### H3 (HIGH) Reuse claims about the shelved WP1 code are wrong; the Host layer is new work

s.11 says reusable: "`registry.py` and the 11 `items/*.py` renderers ... with their golden tests" and "`config.py` validator", "5.8k lines".
- `libraries/host-tuning/src/host_tuning/items/*.py` are 5-line stubs: `def render(values): raise NotImplementedError("... not implemented in this version (WP4)")` (e.g. `items/io_cost.py:1-5`). There are no renderers and no golden tests; `BACKLOG.md` HT-02/03/04 say so. `registry.py` (221 lines) is declared metadata only ("Rendering belongs to WP3/WP4").
- The 5.8k includes `.coverage` (1383 lines), `cli-surface.json` (1714), `cli-review.toml` (574), docs, caches. Real code: `host.py` 172, `real_host.py` 64, `registry.py` 221, `plan.py` 139, `config.py` 718 (a validator for the `host_tuning` schema, only its style transfers), `cli.py` 307.
- `host.py` is READ-ONLY by design: `validate_argv` accepts only `uname -r|-m`, one `findmnt --json --target`, one exact `lsblk` invocation (`host.py:64-85`). It cannot run `systemctl`, `udevadm`, `sysctl`, `systemd-analyze`, `swapon`: the allowlist the design says is "reused" must be written from scratch, per verb, with argument grammar.
- `RealHost.write_text` (`real_host.py:44-64`) uses `Path.write_text`: follows symlinks, truncates in place (not atomic, no fsync, no mode), 10 s `subprocess.run` for everything, no env scrubbing. Unusable as the root write primitive. Base the new one on `safeio` primitives (`O_NOFOLLOW`, verified inode, atomic replace, `safeio.py` `save_json`).
- `FakeHost` (virtual FS + recorded calls) is genuinely reusable, but needs sysfs/procfs semantics (write validates against the file's allowed grammar, read-back returns kernel-normalised text such as io.cost `95` to `95.00`; mdt's lib already encodes that normalisation at `mdt-container-io-caps.lib.sh:1543`).

**Change:** reuse = copy `FakeHost`, `HostCall`, `WriteCapability` idea and `test_host.py` (about 300 lines); everything else in IG-1..IG-4 is new. Resize: IG-1 = M to L (split, see section 3). Update s.11 and Q6: delete the branch after copying; do not keep it "for 30 days" as a reference for renderers that are not in it.

### H4 (HIGH) Section 11 describes the extraction branch inaccurately; the real IG-0 work is different

| Design claim | Actual (branch `iogovmon-20261105`) | Verdict |
|---|---|---|
| `pyproject.toml` `[project.scripts]` is `iogovmon = ...cli:main`, "becomes `iogovmond` and `iogovmonctl`" | `pyproject.toml:15-17`: already `iogovmond = "iogovmon.cli:daemon_main"`, `iogovmonctl = "iogovmon.cli:control_main"` | WRONG (already done) |
| units `iogovmon-{tick,bench}.*`, `iogovmon-watch.service`, "naming split in progress renames `-d` units" | `units/` already `iogovmond-tick|bench.service.in`, `iogovmond-*.timer.in`, `iogovmond.service.in` (= the watch sampler), `iogovmon.slice.in`; all start `# TEMPLATE, NOT INSTALLED` | WRONG (already done); NOTE `iogovmond.service` today is the unprivileged watch sampler; the design makes `iogovmond.service` the ROOT core and the old one `iogovmond-probe@watch`: the same name changes meaning |
| "split `cli.py` into `daemon.py`/`probe.py`/`ctl.py`" | `cli.py` (171 lines) already has `DAEMON_COMMANDS = {tick,bench,watch,cleanup}`, `CONTROL_COMMANDS = {plan,analyze,status,preflight,devices,annotate,note,quiet-experiment,prune,config}` and `daemon_main`/`control_main` (`cli.py:19-25,86-97`) | PARTLY WRONG: the split exists; what the design actually needs is a third entry (root core) and a probe entry name that does not collide with `iogovmond` |
| "existing commands ... map to probe lanes and ctl verbs" | the design's verb lists (s.0, s.5) omit `plan`, `analyze`, `annotate`/`note`, `devices`, `prune`, `cleanup`, `quiet-experiment`, `preflight`; `quiet-experiment` ("prints the plan for an approved nr_requests/io.max test, executes nothing", `cli.py:81-84`) contradicts the new actuation story | GAP |
| config "keeps its name (now with the new sections)", `[probes]` "existing recorder keys unchanged" | `config.py:1` "one flat TOML table, strict keys"; recorder keys are top-level (`state_dir`, `scratch_dir`, `kill_switch_file`, ...) | WRONG: nesting under `[probes]` changes every key, the example TOML, install.sh, and tests |
| `DEPRECATED_KEYS` shims | `config.py:15-19` ignores removed keys with a warning for a release | conflicts with "no removed-key shims" (s.3); delete them in IG-0 |
| "static 'changes nothing' test is replaced" | `tests/test_static.py` (ALLOWED_CALLS at :23, `test_only_stdlib_is_imported`, `test_no_module_writes_cgroup_sysfs...` asserting no `subprocess`, unit test asserting no `User=root`) | CONFIRMED test exists; it has FIVE assertions to rewrite, not one (stdlib-only, no-subprocess, no-User=root, template-only units, per-site allow-list) |
| `pyproject` stdlib-only, cli-extended dep only for `ctl` | `pyproject.toml:11-13` comment: "No first-party dependency (no cli-extended, no iobench ...)", `dependencies = []` | reverses a documented decision; fine, but one wheel means the daemon venv gets cli-extended too (the "core is stdlib-only" statement is only about imports) and the stage-1 installer must fetch a third wheel (see M4) |

**Change:** IG-0 = merge both branches; rewrite config to the nested schema with the flat-to-nested move done once and no shims; replace the five static assertions; introduce the probe entry as a separate console script (`iogovmon-probe tick|bench|watch|cleanup`) so `iogovmond` unambiguously means the root core; add the missing verbs to the CLI spec (or delete them: `quiet-experiment` goes). Size M to L (config and test rewrite are real work).

### H5 (HIGH) devkey: recomputing it live is wrong for exactly the main target (serial-less virtio, chroot-created result); two more iobench contract errors

- `iobench` keys a serial-less disk by model+size+hash(`/etc/machine-id`) (`ceilings.py:41-51`, `result.py:118-123`). The installer benchmark runs while `/etc/machine-id` is empty or `uninitialized`, so the stored scope has `machine_id_hash = None` (`result.py` `machine_id_hash` docstring) and a DIFFERENT key from the live key after first boot. iobench's own CHANGES says "Consumers use the key stored in the identity because chroot-created records can have a different live key after first boot". The design names the bench dir by `devkey` and wants `iobench identity --json` to print the key (s.4, s.11): after first boot that live key no longer finds `ctl/bench/<devkey>/`. These are the netcup/vm hosts the program targets.
  **Change:** the registry (`ctl/registry.json`) maps role/disk to `{state_dir, stored_scope_key, name_hint}`; the directory name is an opaque id assigned at import; the live disk is matched through `iobench check` (which already runs `identity_differs` against the stored scope, `result.py:196-211`), not by recomputing the key. `iobench identity --json` is then only needed to compare, so IB-1 loses a verb (a `show --calibration --json` gives `identity.disk_scope.key` once calibrated).
- `iobench ceilings add --source iogovmon` (s.7): `ceilings.py:15` `SOURCES = ("benchmark","governor","probe")`, `SOURCE_KINDS = {"benchmark": raw|file, "governor": raw|file, "probe": probe}`. `iogovmon` is refused at `ceilings.py:80-81` and the CLI `choices`. Either add `iogovmon` to iobench (breaking-schema-free, one line plus tests) or use `governor`/`probe`. WRONG as written.
- `benchmark run --profile veryquick|quick|full` (s.5): iobench profiles are `veryquick|quick|standard|thorough` (`cli.py:23` `PROFILES`); there is no `full`. WRONG; map names explicitly.
- `iobench cells` and `iobench identity` do not exist: CONFIRMED (verbs: `target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`; `cli.py:123-279`).
- `ceilings add` requires an existing calibration in the state dir (`_ceilings_add` raises `no_calibration`, `cli.py:~310`); a host whose install benchmark was `veryquick` has a calibration but veryquick "adds nothing to the ceiling history" (CHANGES, `calibrate.py:424`), so the first file/probe observation starts history from empty: fine, but the 8-observation/48 h proof clock starts only at first daemon run, not at install.

### H6 (HIGH) The install flow cannot produce a measured io.cost model, and later raw runs need the deferred swap-borrow

Decided flow: `veryquick` on the planned raw partition, then `iogovmonctl install --profile`; "writes the io.cost model only if `proven` or `pinned`".
- iobench: every veryquick cap and model value is `unproven: veryquick_profile`, metering `coarse`, `formula_only`, and it adds nothing to history (CHANGES.md "Added"). `ceilings.py` says raw rows alone supply model/cap values, and file/probe rows only confirm stability. So after install the model is never `proven`, only the binding and plausibility exist.
- The raw partition is a throwaway: deleted before mkfs/swap (`installer.py:3469-3504`, `_run_io_benchmark` order). A later raw calibration (needed for `proven`) has no raw target unless swap-borrow (IG-12) or an operator-supplied partition exists.
- Regression versus today: `installer._configure_iocost` (`installer.py:1618-1644`) writes `/etc/vbpub/iocost-model` from the measured benchmark and `vbpub-iocost.service` applies it; the new flow applies nothing measured at install.
**Change (state it; pick one):** (a) honest: install = veryquick for binding + plausibility, io.cost model stays `unmanaged` (kernel `ctrl=auto`) until the operator runs `iogovmonctl benchmark run --profile quick|standard --target ...` or pins a model; document that loudly in the install notification; swap-borrow is then the only automatic path to `proven` and moves onto the critical path of the "measured model" feature but NOT of the first release; or (b) run `quick` (12 min, adds history) at install instead of veryquick if the operator wants a measured model at install. I recommend (a) plus an explicit statement that first release ships io.cost as `pinned`/off, because (b) needs an operator ruling on 9 extra minutes of installer wall time.

### H7 (HIGH) The actuation feedback loop is under-powered; the verification design will mostly revert or freeze

s.8/s.7: a `distress` signal proposes a 10 % step; verification keeps it if `p99_after <= p99_before x noise` and throughput >= 0.8x, else rolls back; "neutral -> revert". Synthetic verification only on an idle host; on a busy host (and distress means busy, by definition) a passive 30-minute vs preceding 30-minute comparison of `io.pressure`/episode counts.
- A 10 % io.cost qos step has an effect far below the run-to-run noise of 30-minute PSI windows on a production host; "neutral -> revert" then makes almost every step a revert. Flap rules count rollbacks (cooldown doubles, two in 7 days freeze the knob): the controller freezes itself within about a week on any normal host and the 7-day soak (ACC) cannot show a kept change.
- Synthetic verification generates load (fio cells) after every actuated change on an idle host: up to 4 loads per day on a production disk, each minutes long, A then B, on the same disk the operator is protecting.
- Recognizer lag: `capability_shift` needs CUSUM detection time. In the window between a provider cap change and its detection the lane reads `distress` on a "stable" lane and the controller tunes around the cap change, the exact failure the design says it prevents.
**Change:** (1) First release: actuation present but only `observe-only` is selectable and writes a counterfactual audit line per would-be step (the audit/journal and bounds code are exercised, nothing is applied); flip to `on` for ONE knob (io.cost qos latency target) after evidence from the soak that the counterfactuals would have helped. (2) Require a "stable-capability" precondition: no `capability_shift`, no baseline reset and no settings-epoch change for N days (config, default 7) before `distress` may propose; a capability shift suspends proposals until the operator `annotate --baseline-reset`s. (3) Passive verification must have an explicit power test (effect size vs historic window-to-window variance); if the test cannot resolve the step, the step is not taken (do not "try and revert"). (4) Count neutral reverts separately from regression rollbacks in the flap rules. This is also the main item where the operator's "default ON" ruling meets "feature hold": defaulting to ON for knobs whose effect cannot be verified is a risk the review recommends confirming with the operator.

### H8 (HIGH) Crash recovery and reboot semantics of the journal are under-specified in a way that can write stale values

s.4 "crash recovery rolls an unfinished txn back on start"; s.8 "actuated values are runtime-only (a reboot returns to baseline)".
- A txn `applied`/`planned` before a power loss holds `prior` runtime values captured in the PREVIOUS boot. If the daemon replays at the next boot it writes those `prior` values over a freshly applied baseline (the operator may have edited `iogovmon.toml` or the static files in between; `vbpub-iocost`/sysctl units have just run). Rule needed: every txn records `boot_id` (`/proc/sys/kernel/random/boot_id`); at start, txns from another boot are marked `abandoned_by_reboot` and NOT replayed for runtime kinds (sysctl/sysfs/cgroup); only persistent-file effects are reconciled (compare `sha_after` to disk, remove or keep by marker).
- Write-ahead rule: `planned` must be fsynced before the first write; `applied` after read-back; `verified` after the verification verdict; a txn left in `applied` is rolled back (verification never completed). Use `O_APPEND` single-write records of bounded size plus a torn-last-line tolerance (the existing `journal.py:append` pattern, `safeio.py` `save_json` fsync+replace).
- Boot ordering: the baseline units (sysctl, io.cost oneshot, udev) must run before `iogovmond`; the core `After=` those plus `systemd-udev-settle`-equivalent (mdt uses an explicit `udevadm settle` in `mdt-io-cost.service`, units/mdt-io-cost.service:12-14). Spell the ordering out; overlay values must never be re-applied at boot ("runtime-only") but `check` must treat a missing overlay after boot as normal, not drift.
- Persistent `apply_when_proven` writes (tier-2 to /etc) get the txn treatment with `daemon-reload`/`udevadm control --reload` in the same txn and re-trigger only the affected disk (mentioned in revert only).
- `kill switch ... aborts and rolls back an in-flight change`: the roll back must itself be a txn that can complete while `apply.lock` is held by a hung CLI; use `flock` with a timeout and a stale-holder rule (holder pid + start time from `/proc`).

### H9 (HIGH, dependency) Release gating and VM harness coupling

- The VM harness is `scripts/debian-install-v2/testing/vm/{vmctl,run-vm-tests.sh}` (not `testing/vm/vmctl` as written in s.10). The design's cmru order is iobench -> iogovmon -> installer/mdt, but iogovmon's own gate (every OS-touching WP "ships its VM assertions") then depends on a harness inside the downstream project. Decide before IG-5: either iogovmon's `run-gate.toml` lane invokes the installer-owned harness by path (documented coupling), or the harness moves to a shared location (`tester-base` is the existing shared-tester project). Without this, IG-5/IG-8 cannot be gated.
- `scripts/debian-install-v2` currently has no iogovmon key and a FLAT config dataclass (`config.py:Config`, fields like `run_zswap_config`, `iocost_enabled`, `run_io_benchmark`, `zswap_pool_percent`, `vm_swappiness`); the design's nested `"iogovmon": {...}` object and "the existing `services.iogovmon.install` key" do not exist (grep: no `iogovmon` in `debian_install_v2/`). IG-14 must add nested-object parsing plus the removal of ~20 flat keys with `RENAMED_V1_SETTINGS`-style refusals (that mechanism exists, `config.py:34-46`), and WP-IG-FLIP deletes them. The sentence in s.3 should say "new key" not "existing key".

### M1 (MEDIUM) `apply.lock` cannot cover a benchmark; add a second lock and a quiesce step
s.2 uses one flock for daemon/CLI txns. A `standard` iobench run (60-75 min) cannot hold it (it would block kill-switch rollback and `revert`). Use `apply.lock` for transactions only (seconds) and a `bench.lock` + `ctl/bench-state.json` (`running`, pid, start) for benchmark and verification jobs; the controller refuses while `bench-state` is `running`; `doctor` reports a stale one (iobench's own restore journal is `/run/iobench/restore.json`, already in the doctor list).

### M2 (MEDIUM) Config ergonomics
- The TOML sample in s.3 is not valid TOML (`[baseline.vm_sysctl]    enabled = true; swappiness = 10`): semicolons. Fine as a sketch, but the review notes it because the docs-test pattern (`test_docs.py`) will try to parse examples.
- Count: about 14 baseline sections, 40+ leaf keys, plus installer JSON mirroring them and 8 mdt env keys. A human cannot hand-write that safely. Propose: `profile = "standard"|"conservative"|"minimal"` (named bundles of item toggles) as the only thing most operators set; per-item sections hold only exceptions. `iogovmonctl config init --profile standard` writes a commented skeleton; `config show --effective` annotates each value `default|profile|file|pinned`.
- Refusal messages: spec (key, allowed set, why) is good; add a line `did you mean` for unknown keys (strict keys already refuse, `config.py` "strict keys").
- Inputs from the installer JSON and mdt env only SEED the TOML (s.3): define which wins on re-run in ONE table (the s.3 rule "diff and keep the TOML unless `--force-seed`" is good); mdt `managed` writes through `config set`, so `iogovmonctl config set` must be atomic and journalled like any apply (it is root-owned config that probes also read).
- `roles = { root = "auto", data = "auto" }` is unexplained: what resolves "auto" (the data-area-v2 layout? `findmnt`?) and what happens with multiple data disks (WP1 review "multi-disk refusal" is not carried over).
- mdt keys: `IOGOVMON_WEIGHT_BAND_PCT="50 200"` and `IOGOVMON_ACTUATION_<KNOB>` expose knobs that do not exist in the first release (see cut list); remove.
- `iogovmon.toml` 0644 root: fine; `max_changes_per_day` etc. must be clamped by code (the existing `HARD_MAX`/`HARD_MIN` clamp pattern, `config.py:21-45`; the sentence "bands may only NARROW the hard bounds in code" should name that mechanism and test it).

### M3 (MEDIUM) Test strategy realism
- FakeHost cannot show: kernel read-back normalisation, write rejection (EINVAL), sysfs files that appear/disappear (`io.cost.model` exists only with the controller), udev rule ordering, systemd ordering at boot, `flock` semantics across processes. The design lists these as VM-only (right) but then lists essentially every apply-engine behaviour as VM assertions: that makes IG-5/IG-8/IG-11 gated by a TCG VM lane whose speed and flakiness are unmeasured. Keep a second FakeHost tier that models the kernel semantics above (`FakeKernel`), so the txn/rollback/bounds logic is proven in milliseconds, and keep the VM lane to a thin set per item (live equals rendered, after reboot, revert restores).
- TCG cannot exercise io.cost vrate dynamics, real cache behaviour, provider throttling (QMP `block_set_io_throttle` shows a cap change but not provider behaviour: label that scenario "mechanism only"); the design says this (s.10); additionally `io.cost` needs a blk-mq device with a registered cost controller: verify early that virtio-blk under TCG accepts `io.cost.qos enable=1` before IG-4 is sized; if it does not, io.cost VM assertions are impossible and IG-4/IG-11 acceptance moves to a real disposable host.
- Quiet-window finder (28-day weekday x hour) can only be tested with synthetic histories; the 7-day soak validates only the fallback path. State that.
- Static-lint test for the write guard is strong; add a mutation-style test (insert a write call in a non-allowed module in a temp copy, assert the lint fails) since assay mutation gates are the estate norm.

### M4 (MEDIUM) Release order and wheel logistics
- cmru order iobench -> iogovmon -> consumers is right. But iogovmon's first usable release needs `iobench >= 0.2.0` released (IB-2), and IB-2 needs IB-1 (`cells` is only needed by IG-8): split IB-1 so the release is not held by the verification verb (see section 3).
- A root install from wheels needs three artifacts with hashes in stage-1: `iogovmon`, `cli-extended` (the daemon venv gets it; today `bootstrap-remote.py:724` resolves ONE wheel pointer via `cli-extended-latest/latest.json`) and `iobench`, plus `fio >= 3.30` from apt. `iogovmonctl install` is the thing that needs them first, chicken-and-egg: the installer must pre-install the venv; decide the layout (single venv at `/opt/iogovmon/<version>/venv`, `current` symlink, exactly cgpd's) and have IG-13 produce an `iogovmon-bundle.tar` (wheels + sha256 manifest) so stage-1 verifies one hash.
- Release wiring (cmru/run-gate/assay, IG-13) is placed late; the branch already has `cmru.toml`, `run-gate.toml`, `.run-gate/`. Gate wiring belongs to IG-0/IG-1 so every later WP is gated by the real runner from the first merge (estate rule: "gate-verified" = the registered gate).
- Probe history: the quiet finder and the proof clock need weeks of history. Release the probes (IG-0 plus the CPU-PSI and foreign-writer additions) as `iogovmon-v0.1.0` EARLY on a test host so history accrues in parallel with IG-1..IG-9, otherwise IG-ACC waits for the data it needs (28 days for the weekday x hour profile).

### M5 (MEDIUM) Provider-change confusion and oscillation: smaller gaps
- Settings epoch exists (`collectors.py:264-294` `settings`/`settings_epoch`): the controller must treat an epoch change not caused by its own txn (operator, mdt, installer) as `capability_shift`-equivalent (freeze + re-baseline), otherwise a legitimate external change reads as distress.
- Hysteresis in time only; no hysteresis across knobs: two knobs (io.cost qos and dirty bytes) may be nudged on the same distress episode; add "one knob per episode".
- `drift.heal = report` default is right (Q3), but s.5 `check` counts "journalled in-bounds actuation as expected": define the comparison against `baseline + overlay`, and `heal=baseline` re-asserts the baseline files only, never runtime overlay values.

### M6 (MEDIUM) Work package sizes: see section 3.

### L1 (LOW) WP-DOC rests on a misreading
`installer.py:3475` ("Does not write io.cost.model/io.cost.qos") is the docstring of `_run_io_benchmark`, which is true; `vendor/README.md:41` says the same of the vendored tool, also true. The io.cost unit is written by `_configure_iocost` (`installer.py:1618-1644`). There is no stale text to fix; the claim should be a clarifying sentence, or dropped (feature hold).
### L2 (LOW) Smaller factual checks
- Recorder "changes nothing" static test: CONFIRMED (`tests/test_static.py:23-48` ALLOWED_CALLS, `:129-135`, `:137-146`).
- iobench disk-scope digest: CONFIRMED (`ceilings.py:53-59`, `result.py:118-123`); `quality.plausibility`, `calibration.veryquick`, `check --calibration`, `--installer`, `--kind` exist (`cli.py:47,182,205`, `plan.py:12`).
- iobench current release is 0.1.1 (tags `iobench-v0.1.0/0.1.1`); the branch CHANGES is `[Unreleased]` with a breaking schema minor: `>= 0.2.0` is right.
- CPU PSI and foreign-writer rate: "two small additions" is roughly right: `system_pressure(resource)` takes any resource (`collectors.py:198`), memory PSI is collected (`:256`), and `others` writers are already rate-tracked (`:227-239`, `:359-372`); only CPU PSI wiring is new.
- `iogovmon.toml` example and the existing `status` output are consumed by `iogovmonctl status` today; the s.5 `status --json` schema is new work, not a rename.
- `docs/DESIGN-GUIDE.md` on the branch is 348 lines of V1 text to rewrite; `CONSUMERS.md` exists already.

---

## 2. Factual claims table (existing code)

| Claim (design) | Verdict | Evidence |
|---|---|---|
| Recorder never runs as root; sandboxed; ACL for raw reads | CONFIRMED | `safeio.py:63-65` refuses root unless `--allow-root`; units set `NoNewPrivileges`, `ProtectKernelTunables=yes`, `CapabilityBoundingSet=`; `99-iogovmon-raw-read.rules.in` read-only ACL by ID_PATH |
| "changes nothing" static test exists | CONFIRMED | `tests/test_static.py:23-48, 130-146` |
| Existing readers can read as root, any owner, no-follow | CONFIRMED, but insufficient for root | `journal.py:141`, `safeio.py:93-99` accept any owner (B1) |
| `pyproject` scripts to be renamed; units to be renamed; cli.py to be split | WRONG (done already) | `pyproject.toml:15-17`; `units/iogovmond-*`; `cli.py:19-25,86-97` |
| Units are installed/rendered | WRONG | all are `TEMPLATE, NOT INSTALLED` (`test_static.py` asserts it and forbids `User=root`) |
| Config "keeps its name"; recorder keys unchanged under `[probes]` | WRONG | flat table, strict keys, DEPRECATED_KEYS shims (`config.py:1,15-19`) |
| WP1 `items/*.py` renderers + golden tests reusable | WRONG | 5-line stubs raising `NotImplementedError` (`items/io_cost.py`); registry is metadata only |
| WP1 `Host`/`validate_argv`/`RealHost` reusable | PARTLY | FakeHost/WriteCapability reusable; `validate_argv` is a read-only allowlist of 3 commands; `RealHost.write_text` follows symlinks, non-atomic (`real_host.py:44-64`) |
| iobench `cells` verb | DOES NOT EXIST | `cli.py` verb list; matrix writes knobs itself (`host.py:78-83`) |
| iobench `identity --json` | DOES NOT EXIST | and wrong key for chroot-created weak scopes (H5) |
| iobench `ceilings add --source iogovmon` | WRONG | `ceilings.py:15-16,80-81` |
| iobench profile `full` | WRONG | `cli.py:23` profiles are veryquick/quick/standard/thorough |
| iobench `--state-dir`, `check/show --json`, `--kind`, disk scope, veryquick, plausibility | CONFIRMED | `cli.py:42,181-212`; `result.py:118`; CHANGES.md |
| iobench restore journal location | `/run/iobench/restore.json` (not in design) | `restore.py:15`; blocks sandbox (H1) |
| mdt reconcile "also writes cgroup/sysctl state"; mdt reassert heals | CONFIRMED | every minute: `mdt-dev-governance-reconcile.sh:262-266`; `mdt-container-io-caps.lib.sh:1502-1517` |
| installer writes vbpub-iocost / zswap / sysctl units | CONFIRMED, incomplete list | `installer.py:1618-1644`; `templates.py` writes `99-vbpub-swap.conf`, zswap-config, min-free-floor, ksm/thp, cgroup2-flags, zstd |
| installer `services.iogovmon.install` existing key | WRONG | no `iogovmon` anywhere in `debian_install_v2/`; config is flat |
| VM harness at `testing/vm/vmctl` | WRONG path | `scripts/debian-install-v2/testing/vm/vmctl` |
| WP-DOC stale io.cost text | NOT STALE | `installer.py:3475` is accurate for `_run_io_benchmark` |

## 3. Work packages: order, sizes, hidden dependencies

Hidden dependencies and circularity:
1. **IG-4 before IG-5 is circular**: IG-4 includes "single-writer adoption of `vbpub-iocost`/`mdt-io-cost` (removed in the same transaction)" but the transaction engine is IG-5. Split IG-4: IG-4a render model/qos + boot unit + bfq refusal (needs only IG-1/IG-3); IG-4b legacy adoption (needs IG-5, and must cover the full legacy list in B2).
2. **IG-1 is two jobs**: (a) host/write-guard/safe write primitives (new, security-critical), (b) config schema/validator/`doctor` skeleton/cli-extended shell. Split; (a) is the foundation of B1/H1/H3 and gates IG-5.
3. **IG-5 (L)** bundles engine + overlay + drift/check + crash recovery + write guard lint + VM assertions. Split: IG-5a txn journal, prior capture, revert, boot_id recovery, `apply.lock`; IG-5b drift/`check`, overlay, static write-guard lint, VM item assertions.
4. **IB-1** bundles three verbs and two contracts. `identity --json` is replaceable (H5); `cells` is needed only by synthetic verification. Split: IB-1a installer/borrow target contract + file fallback (needed by IG-6, IG-14); IB-1b `cells` (needed by IG-8b). IB-2 (release `iobench-v0.2.0`) depends on IB-1a only so iogovmon is not held by `cells`. Also add the `iogovmon` source to `ceilings` (H5) in IB-1a.
5. **IG-6** needs IB-1a but also the registry/devkey redesign (H5): M stays M only with the opaque-id registry.
6. **IG-7 (quiet finder)** depends on IG-10 running for weeks to be meaningful, not only on IG-1 (M4).
7. **IG-8 (L)** = synthetic + passive + verdicts + rollback hooks; it needs IG-5 and, for "demotion hooks", the state machine of IG-9; split IG-8a passive verdicts (needs IG-5), IG-8b synthetic (needs IB-1b), and put the demotion interface in IG-9.
8. **IG-9 (L)**: state machine plus `prove` CLI with `--ab`, `--when quiet`, `--explain`, `--accept`, pin: split IG-9a state machine/`proof.json`/evidence classes (needs IG-6), IG-9b `prove` verbs (needs IG-7, IG-8).
9. **IG-10** (root core) needs B1, H1, H2 designs; it cannot be a late WP in a scheme where IG-13 (`install`) depends on it: the baseline-only install does not need the daemon.
10. **IG-11 (L)** controller+bounds+gates+flap+audit+VM scenarios: split IG-11a bounds/knob registry/audit/observe-only controller (counterfactual mode, H7), IG-11b `on` for one knob plus VM scenarios 1-9.
11. **IG-12** swap borrow: nothing in the first release or in IG-FLIP needs it; IG-ACC "depends on IG-12" without a scenario that uses it. Remove from the ACC dependency list; schedule after FLIP.
12. **IG-14 (L)** edits `installer.py` (4942 lines; `_run_io_benchmark` at 3469, `_configure_*` calls at 4906-4915): it must also make the old steps MUTUALLY EXCLUSIVE with `iogovmon.install=true` (otherwise acceptance runs have two writers, B2). Split IG-14a config+preflight+bundle fetch, IG-14b benchmark-step and `install --profile` call.
13. **IG-15 (L)** mdt: `adopt` (read-only, actuation off) vs `managed` (mdt reconcile reads the overlay, `mdt-io-reassert-pause` integration, removal of `IO_*` writers). Two separate WPs: IG-15a adopt, IG-15b managed (prerequisite of default-ON actuation on mdt hosts).
14. **AB (scheduler A/B)** needs IG-8 (synthetic verification) and real hosts; it can be dropped from the first release: `auto = kernel` already is the safe state (feature hold).

Critical path as stated (IG-0 -> IG-1 -> IG-3 -> IG-5 -> IG-8 -> IG-9 -> IG-11 -> IG-ACC -> IG-FLIP) is NOT the right one: it puts the controller on the path to the first usable result. What consumers need first (static baseline, doctor, revert, binding, plausibility) needs only IG-0, IG-1a/b, IG-2, IG-3, IG-4a, IG-5a/b, IG-4b, IG-6, IG-13a, IB-1a, IB-2.

**Revised order**
- **M0** (week 0): merge iobench branch; IG-0 (rewrite config, replace static tests, probe entry, gate wiring); release `iogovmon-v0.1.0` = probes only (history starts accruing).
- **M1 static baseline** (releasable, install default false): IB-1a, IB-2 (parallel), IG-1a, IG-1b, IG-2, IG-3, IG-4a, IG-5a, IG-5b, IG-4b, IG-6, IG-13a (`install --baseline-only`, `uninstall`, `revert`, `doctor`, release bundle). Then IG-14a/b (installer adapter, flag false) and IG-15a (mdt adopt). Gate: a green VM install-from-wheel, reboot, `revert --all`.
- **M2 daemon observe-only**: IG-10 (root core with B1/H1/H2 fixes, drift report, kill switch, ingestion of root-collected signals), IG-7, IG-8a, IG-9a, IG-9b. Soak on the two test hosts, observe-only.
- **M3 actuation**: IG-11a (observe-only counterfactual) -> evidence -> IG-11b (`on`, one knob), IB-1b + IG-8b (synthetic), IG-15b (mdt managed); IG-ACC; IG-FLIP.
- **Later / cut**: IG-12 swap borrow, AB, io.weight and dirty/queue knobs, `iogovmonctl actuation knob` fine-grained surface, `export` compat projection for ciu (no ciu consumer today).

Realism for one Codex run: IG-1a/IG-5a/IG-5b/IG-10/IG-11a are each M after the splits; IG-12, IG-14b, IG-15b stay L and should be dispatched in two steps at a green-gate boundary. Two L-sized tracks at a time on this host is fine.

## 4. Safety summary (item 4 of the brief)

- Oscillation: hysteresis, cooldown, caps are adequate in form; weaknesses are H7 (self-freezing and under-powered verification), M5 (one knob per episode, external settings-epoch change).
- Provider-change confusion: capability_shift freeze exists; lag between a cap change and its detection is the gap (H7.2).
- Rollback when the daemon crashes mid-apply: H8 (boot_id, write-ahead rule, path re-derivation from the registry per B1).
- Reboot: overlay runtime-only is right; make `check` and the boot ordering explicit; static baseline files must be the only thing that survives reboot, and the legacy 99-file must not shadow them (B2).
- mdt governance: mdt wins today by writing every minute; B2 spells out the required handoff; mdt-psi-guard must keep sole ownership of tier `io.weight`.
- debian-install-v2 migration: B2 legacy list; installer old steps must switch off when the flag is true (IG-14).

## 5. Operator's open questions (s.13)

1. **Privilege split: keep, but conditionally.** The split is sound only if (a) the root tree is outside the probe-owned directory (B1), (b) trust is asymmetric (H2), and (c) iobench/benchmarks/verification run as separate transient root jobs, not inside the core's sandbox (H1). Say that the justification is the raw-device/scratch code, not generic parsing (cgpd runs the same kind of parsing as root). If the operator will not accept B1+H2 complexity, the fallback is option A with the root core being a NEW small process that imports no probe code, which is the same thing under another name.
2. **iobench subprocess only: yes.** Reduce the new iobench surface to IB-1a (target contract/file fallback, `iogovmon` source) and IB-1b (`cells`); drop `identity --json` (H5). Wrap `calibrate`, never `run`/`matrix`, from iogovmon.
3. **Drift policy: report by default, agreed.** `heal = baseline` re-asserts rendered files only, never runtime overlay values, and must not be enabled on a host where mdt reasserts (two healers).
4. **Calibration scheduling: operator-run only, agreed.** The daemon suggests and shows the next quiet window; with install-time veryquick and a deleted raw partition (H6) there is nothing to schedule on most hosts anyway.
5. **mdt keys: drop outright, but at the `managed` flip, not before.** `adopt` forces actuation off and changes nothing; the first `managed` release must ship together with the mdt-side overlay/pause integration (B2.3). mdt-psi-guard keeps `io.weight` ownership.
6. **Shelved WP1 code: copy only `FakeHost`/`WriteCapability` ideas and `test_host.py`, rewrite the rest.** The renderers do not exist (H3); delete the branch after the copy (a 30-day "reference" for stubs has no value); do not cherry-pick history.

## 6. Cut or defer (feature hold)

Defer past FLIP: swap borrow (IG-12), scheduler A/B (AB), `io.weight` knob (owned by mdt-psi-guard), `dirty_*` and `nr_requests`/`max_sectors_kb` actuation (queue freeze, quiet-window machinery), synthetic verification (IG-8b, IB-1b), `iogovmonctl export` compat file, per-knob `actuation knob` verbs and the mdt `IOGOVMON_ACTUATION_<KNOB>`/`WEIGHT_BAND` keys, quiet finder beyond the live-threshold fallback until 28 days of history exist, `prove --accept`/pin ceremony beyond the pinned-model hatch. Keep: baseline items, doctor, revert, kill switch, binding + plausibility, drift report, proof state machine, observe-only controller with audit.

## 7. Verdict

**REVISE.** Resolve B1 and B2 in the document, correct the factual claims in section 2, apply the work-package splits and the revised order (M0-M3), then re-review only s.2 (privilege model), s.4 (paths), s.8 (actuation) and s.12 (work packages). No WP should be dispatched before B1, B2, H1, H2 and H8 are reflected in s.2/s.4/s.9; IG-0, IB-1a/IB-2 and the M0 probe release are safe to start now.

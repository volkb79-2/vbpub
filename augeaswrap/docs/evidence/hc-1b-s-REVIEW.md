<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/hc-1b-s-REVIEW.md -->
<!-- relation: host configuration review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# HC-1b-s independent review: augeaswrap sysctl / sysusers+group / tmpfiles / endpoint adapter / shadow core

Reviewer: fresh Sonnet session (did not write the code). Worktree `<worktree>`
(never modified, `git status` clean), branch `hc-1b-s-20261108`, head `3b34207d42c27d8fef4ca7feed75078a246344f1`.
All probing was done in `git archive` copies under `<controller-scratch>/rv-hc1bs/` (`b` = pristine head, `m` = mutant copy,
`probe/` = scripts). One pytest process at a time under `nice -n 10`; no gate container; no push/merge. The only host
touches were read-only/dry-run runs of the devcontainer's own `systemd-sysusers`/`systemd-tmpfiles` 257.13 (the trixie
version) against scratch roots, `systemd-sysctl --cat-config` (read-only) and `/proc/sys` reads.

## VERDICT: FIX-FIRST

The structure is sound: engine hardening (R1-R4) is in, the shadow core is correct on every rev3 B.3 rule I probed,
revert/precondition/journal behaviour is good, and 60 of 72 hand mutants were killed. But the registered gate cannot
see three native-tool facts that are wrong on real trixie (the fake command runner returns canned success), one
fail-open input hole in the sysctl shadow guard, and seven survivors on safety guards. None of the code defects is a
large change; all have concrete fixes below.

Facts: all touched test files pass on the head (static undefined-name test included; full suite ~21 s).
`git merge-tree --write-tree origin/main HEAD` is **clean** (origin/main is `1b5a212f5`). The gate logs
(`logs/hc-1b-s-r0-r1-commit-3b34207d4.log` PASS, 4,492 stmts / 1,720 branches = 100%, CLI surface check passed;
`hc-1b-s-r3-commit-3b34207d4.log` PASS with "canary rejected") end at 20:31:58 / 20:32:07, after the last source commit
(`94e624591`, 20:30:11) and the test-only fix `3b34207d4` (20:31:10); no `pragma` in the new code (the single
`pragma: no cover` is in the frozen, unchanged `endpoints.py`).

## BLOCKING

### B1. Live `sysctl` is unusable on trixie: `systemd-sysctl` is not on PATH
`sysctl.py:295-296` runs `("systemd-sysctl", "--cat-config")` through `subprocess.run` (PATH lookup), called from
`observe` (`:126`) for every state on root `/`. On Debian trixie the binary is `/usr/lib/systemd/systemd-sysctl`
(`which systemd-sysctl` -> not found here; `ls -l /usr/lib/systemd/systemd-sysctl` -> present). Probe
`probe/p5.py` calling `_native_headers(Path("/"), layers)` with the real runner raised
`FileNotFoundError: 'systemd-sysctl'`; the engine turns it into `resource.plan.failed`, so every live sysctl plan
(present or absent) is refused. With the absolute path the header list equals Python's order
(`10-coredump-debian`, `50-bubblewrap`, `50-pid-max`), so the design itself works.
Fix: resolve the binary (`shutil.which("systemd-sysctl")`, then `/usr/lib/systemd/systemd-sysctl`, `/lib/systemd/systemd-sysctl`),
wrap `OSError`/`TimeoutExpired` into a `PlanRefused(sysctl.native.failed)` problem, and test both the lookup and the
not-found path. This is the BACKLOG "live probe pending" item; it is actually broken, not merely unverified.

### B2. `tmpfiles` native validation reads the wrong file (fail-open on content, refuses first installs)
`tmpfiles.py:337` builds `systemd-tmpfiles --root <stage> --dry-run --create /etc/tmpfiles.d/<f>`. systemd 257 does
**not** resolve the file operand under `--root`: against a scratch root, `--root $D --dry-run --create /etc/tmpfiles.d/t.conf`
gives `Failed to read '/etc/tmpfiles.d/t.conf': No such file or directory`, rc 1 (the host path). Consequences on a
live host: when the fragment is not yet installed the plan is refused every time (`tmpfiles.validate.failed`); when an
older copy exists, the dry-run validates the **old host file, not the staged bytes**, so an invalid staged change passes.
With the staged path as operand (`--create $D/etc/tmpfiles.d/t.conf`) or no operand, the same tool gives rc 0 for a
good file and rc 65 for `bogus line`/unknown group, as intended.
Fix: pass the absolute staged path (or omit the operand so the stage root's drop-ins are read); add a test asserting
the operand is under `stage_root`. Update the BACKLOG item to "verified on 257.13".

### B3. `sysusers`: the dry-run guard is vacuous, the name grammar is wider than systemd's, fixed GIDs can be silently remapped
Observed with real `systemd-sysusers --root <scratch> --dry-run` (257.13):
1. Invalid input exits 0. `g a.b - -`, `g foo$ - -`, `g 1abc - -`: `... is not a valid user or group name.`, `g x gx -`:
   `Failed to parse GID`, all rc 0. `_validate_staged` (`sysusers.py:573-580`) only checks `returncode`, so no syntax
   error can ever refuse. `_NAME_RE` (`sysusers.py:20`) accepts `.` and a trailing `$`, which systemd rejects, so
   `GroupSpec("a.b")` plans clean, is committed, never creates the group, and `verify` fails after commit (exit 4, file written).
   Fix: restrict to systemd's grammar (`[A-Za-z_][A-Za-z0-9_-]{0,30}`) for groups/users, and treat any dry-run
   stderr/diagnostic line (or the phrases "not a valid", "Failed to") as `sysusers.validate.failed`.
2. A fixed GID that another group already holds is **silently remapped**: with `docker:x:999`,
   `g newg 999 -` -> `Suggested group ID 999 for newg already used. Creating group 'newg' with GID 998.`, rc 0.
   The plan accepts it (probe `p2.py`: "gid collision 999" PLAN OK; only a caller-declared `not_gid_of=["docker"]` refuses).
   `planned_gids` then supplies 999 to `@gid:newg`, and the endpoint publish post-action runs in request order **before**
   `Sysusers.verify` notices `found[0] != spec.gid`, so docker's GID is published as the service group (the exact hazard
   the `not_gid_of` design exists to prevent). This is the BACKLOG "native fixed-GID collision" item, now measured.
   Fix: refuse at plan when a fixed `gid` is held by a different existing group (`sysusers.gid.in-use`, in
   `_check_gid_constraints`/`diff`), independent of caller constraints; test with an endpoint after it.

### B4. Leading `-` sysctl key defeats the shadow guard on fake roots (fail-open input)
`SysctlEntry._KEY_RE` (`sysctl.py:30`) allows a leading `-` (systemd's "ignore errors" marker). `_parse_assignments`
strips the `-` from other files' keys but the target key stays `-vm.swappiness`, so `analyze` finds none of our rows and
reports nothing. Probe `p1.py`: with `/etc/sysctl.d/99-later.conf: vm.swappiness = 5`, `SysctlEntry("-vm.swappiness","1")`,
authoritative, fake root -> PLAN OK with no finding (the plain key refuses with `shadow.later`). The live `/proc/sys`
check rejects it, but fake roots (the installer path) skip that check. Fix: key regex must start with `[A-Za-z0-9_*]`
(refuse `-`), and canonicalize identically for target and file keys; test it.

### B5. Survivors on safety guards (hand mutation; all are test gaps, the code is right in every case: I verified the behaviour by probe)
| id | mutant | what is missing |
|---|---|---|
| S4b | `accept_shadow` matches key only (`shadow.py:_accepted`) | no test where the key is right and the file is a near-miss (probe: `/etc/sysctl.d/98-later.conf` + same key correctly refuses). Add to `test_shadow.py` near `:272` |
| S8 | sysctl dir order `/etc` and `/run` swapped (`TARGET_CLASSES["sysctl"]`) | `test_target_classes_include_local_vendor_layer` (`test_shadow.py:106`) pins sysusers and tmpfiles tuples but **not sysctl**; no `/etc` vs `/run` same-basename test. Pin the sysctl tuple + `extra_files`, add the same-basename accepted-`$file` case |
| S14 | own assignment = first instead of last for merge `last` | no test with two own rows overlapping one target key (glob + literal) with a later foreign row between them |
| U9 | group/user names may start with `-` (`sysusers.py:19-20`) | the brief's argv-injection case (`-f`, `--`) is not pinned for `gpasswd`/`groupdel`/`members_*`; probe refuses today (`p4.py`: `--x`, `-f`, `--`). Add tests for `GroupSpec`, `GroupRemoval`, both members tables, plus the TOML loader |
| G5 | `restore_file` ignores the recorded mode (`_resource_files.py` restore image) | kinds' revert tests only use 0644 files; add a 0640/0600 pre-image revert asserting mode (SPEC "restore content/mode/owner") |
| G7 | symlink refusal off in `observe_file` | no test where the **target itself** is a symlink (e.g. to `/dev/null`) for the three kinds; today refused with `refusing symlinked target` (probe `p3.py`), unpinned |
| G9 | `commit_file` drops `owner=` (`_resource_files.py:commit_file`) | `file`'s `test_commit_passes_the_requested_owner_and_group_to_chown` has no counterpart for the resource-file helper |

## Verification detail by checklist item

**(1) R1-R4.** Present and tested: `kinds.py` `_KIND_NAME_RE` + 6 bad names (G1 killed); SPEC paragraph on hard-killed
`applying` journals; `fail()` clamp with `finalize` step (G2 killed by `test_interrupt_after_last_step...`); missing-address
`KeyError` wrapped as `revert.restore.address.missing` (G3 killed).

**(2) Per-kind guards.**
- sysctl: file name regex + fixed `/etc/sysctl.d`; symlink file/dir refused (`p3.py`); duplicate keys (K2 killed); live
  `/proc/sys` key check on `/` only (K1 killed; K6 is an equivalent verdict, see below); `apply_now` argv `sysctl -p <file>` + read-back
  (K3 killed); absent keeps the live value and reports it; `apply_now` forces a change on every plan (documented in SPEC,
  K4 killed). Blocking: B1, B4.
- sysusers/group: `groupdel` refused for any user's primary group, including `root` (U1, U13 killed); remove+define and
  duplicate removal refused in both orders (U12, U17 killed); membership of a group removed in the same request refused
  (U6 killed); unknown user refused (U7); GID constraints: `not_gid`, `not_gid_of`/`distinct_from`, unresolved GID, existing-GID mismatch,
  duplicate GIDs in `/etc/group` (U2, U3, U4, U14, U15 killed); `gpasswd -a/-d` flags (U5 killed); gpasswd/groupdel only via
  `PostAction` argv (no direct subprocess); revert never replays `gpasswd` (U10 killed). Argv safety rests entirely on
  `_NAME_RE` (no `--` separators): U9. Blocking: B3.
- tmpfiles: line grammar, mode, absolute/no `..`, duplicate path, type regex (T1, T2, T3, T5, T8 killed); dry-run argv and
  failure handling (T4, T6 killed) but wrong operand (B2); `--create` only on change (T7 is equivalent: the engine only
  passes changed resources to `post_actions`).
- endpoint: `endpoints.py` byte-identical to the merge base (`git diff` empty); marker/format frozen; `@gid:` resolved at **plan**
  time from an earlier fixed-GID sysusers or `/etc/group`, unresolved/removed group refused (E2, E5 killed); live publish is
  an argv post-action after earlier sysusers actions (E3 killed); withdraw-before-unit-removal enforced (E1 killed);
  restore regenerates shared files and does not replay publish (E4, E7 killed); `endpoints.plan` problems become plan
  errors (E6 killed). Deviation from the brief, documented in SPEC: `@gid:` for a **new, dynamically allocated** group
  is refused at plan (and so is any constraint on it), so the rev3 B.6 example (new group without `gid` + `gid_of`)
  cannot plan; consumers must pin a GID (CONSUMERS says so).

**(3) shadow.py.** Probed all rev3 B.3 rules on fake roots (`p3.py`): dirs `/etc,/run,/usr/local/lib,/usr/lib` + `/etc/sysctl.conf`
last; sorted by basename across dirs; same basename in a higher dir masks the lower (`shadow.mask`, accept key `$file`);
last wins; glob overlap both directions; slash/dot key spelling and `-` keys in foreign files normalised; authoritative +
later different or equal -> `shadow.later` error; authoritative + earlier equal -> note, earlier different -> no finding
(intent satisfied); fallback winning -> `shadow.earlier` error, fallback + equal -> note; `accept_shadow` exact
`(file,key)` (near-miss file/key/glob/slash spelling all still refuse); StalePlan when a new drop-in appears or a vendor
file's bytes change between plan and apply; `--root` parity (live-only probes behind `root == "/"`). Intent is required by the strict loader, validated,
and round-trips through `to_toml` (`p4.py`). Equal-value rule matches the operator confirmation. Notes: the
`target_loses` mask branch (S6 survived) cannot occur for the current kinds because every target lives in the highest
directory; harmless but untested if a later kind targets a lower layer. Foreign files with an unparsable line or non-UTF-8 bytes
refuse the plan with a generic `resource.plan.failed` (fail-closed; systemd would only warn).

**Plug-in API vs HC-1b-u (read `hc-1b-u-20261108`, committed head `0ed125cac` plus its uncommitted working tree).** The
documented seam is usable only partly. `observe_layers` is flat and `.conf`-only (`shadow.py` `endswith(".conf")`), and
`effective_files` masks by **basename alone**, so the unit kind must build a per-unit `TargetClass` over
`<unit>.d` directories (supported: `TargetClass` is a plain dataclass) rather than one class for `/etc/systemd/system`.
u's in-progress `KindSpec.shadow_check(resource, root) -> list[Problem]` runs at plan from the engine, outside the
resource observation, so whatever it reads is **not** in the precondition fingerprint unless `Unit.observe` also embeds
`observe_layers(...)`; I recommend giving the hook the ctx/observation or documenting that `Unit.observe` must embed it.

**(4) Revert via the registry.** Content/owner restored for all three file kinds (G6 killed; mode G5 untested, B5), absent ->
absent removes the file, sysusers revert drops `gpasswd` and drops `systemd-sysusers` for an absent pre-image, sysctl/tmpfiles
drop post-actions for an absent pre-image, endpoint restores fragment bytes and regenerates shared files. Digest, address
and path guards on restore (G12, G13 killed; K8/U18 equivalent). Caveat: a transaction containing `[[group]]` is refused
whole (`revert.unsupported: group resources declare no revert support`, probe `p4.py`), including its sibling file
changes; rev3 B.5.5 reads as "restore the files, list the irreversible effects". Say so in SPEC/CONSUMERS or put group removals
in their own request.

**(5) Mutation summary (72 hand mutants, `probe/muts1.json`, `muts2.json` + `mut.py`, full suite per mutant).**
Killed 60. Survived 12: real gaps S4b, S8, S14, U9, G5, G7, G9 (B5); equivalent or non-safety: K6 (walks host `/proc/sys`
on fake roots but `_check_live_keys` ignores it; observation-only), K8 and U18 (restore path prefix check is redundant with
the file-name regex), T7 (engine only passes changed resources), S6 (unreachable branch). Guards killed include: shadow.later,
shadow.earlier, shadow.mask both directions, accept file-only, equal-value rule, first/last merge, basename sort, sysctl.conf
position, `/dev/null` masks, directory-listing and file-content fingerprints, groupdel primary refusal, all GID constraints,
membership refusals, gpasswd flags, dry-run failure handling, endpoint ordering/gid/restore, apply_now read-back,
symlinked directory refusal, revert owner. Static undefined-name test green; gate log binds 100% line+branch to the
final commit (R0/R1 and R3).

**(6) Docs / SPEC / CHANGES / BACKLOG / assay.** Consistent with the code except: SPEC rows for sysusers/tmpfiles and README say the
staged `--dry-run` "validates the fragment" (B2/B3 show it does not as written); BACKLOG's pending live probes are
partly resolved by this review (record 257.13 results). `assay.toml` lists all new modules (`_resource_files`, `endpoint`,
`shadow`, `sysctl`, `sysusers`, `tmpfiles`) with `fail_under = 100`. CLI surface unchanged (`cli.py` untouched), gate's
`cli-extended surface check --cli augeaswrap` passed.

**(7) Merges.**
- vs `origin/main` (`1b5a212f5`): clean, no conflicts (result tree `d6bb0c62f8ae...`).
- vs `hc-1b-u-20261108` committed head (`0ed125cac`): conflicts in `augeaswrap/BACKLOG.md`, `README.md`, `SPEC.md` (3 hunks),
  `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md` and `src/augeaswrap/__init__.py`; `kinds.py`, `CHANGES.md`, `assay.toml`,
  `engine.py`, `transactions.py` auto-merge. All are additive: keep both sides. For `__init__.py` keep my four imports
  and `from .unit import Unit`, and the union of `__all__` entries (`Unit` between `Tmpfiles` and `apply`).
- vs u's **uncommitted** working tree (simulated with `git merge-file` on copies): `kinds.py` and `engine.py` merge clean;
  `transactions.py` conflicts in `fail()` and `run_steps()`: both branches reworked post-image recording (mine: `RevertHooks.post_image/pre_image`
  + `record_post_action_images`; u: `record_post_image(change, only_if_changed)` + `committed_changes`). Take one mechanism
  (mine, since four kinds depend on the hooks; u's `unit.py` must then supply `post_image`/`pre_image`) and keep u's
  committed-change loop only if it adds coverage. Also, u's engine adds `_action_phase`, which **raises** for unknown argv:
  my endpoint post-action argv is `(sys.executable, "-m", "augeaswrap", "endpoints", "publish", ...)` (`endpoint.py:261`), which
  matches no phase rule, so planning any live endpoint change would fail after the merge. Set `phase="endpoint"` on that
  `PostAction` (field exists in u's `__init__.py`). Mine documents request-order post-actions (BACKLOG) while u implements
  rev3 phases: reconcile the BACKLOG/SPEC text to u's phases when merging.

## NON-BLOCKING (nits and follow-ups)
- N1 `groupdel` criterion is the primary-group rule only (per operator Q2): `sudo`, `docker`, and `root` with no passwd file
  (fake root) are deletable (`p2.py`, `p4.py`). Cheap hardening: refuse GID 0 outright.
- N2 No `--` argv separator for `gpasswd`/`groupdel`; safe only through `_NAME_RE` (U9). Add `--` as defence in depth.
- N3 A membership-only sysusers request (`groups = []`) writes a one-newline fragment and runs `systemd-sysusers` on it (`sysusers.py:455`).
- N4 `tmpfiles` accepts destructive types (`R /etc`, `Z`, ...) without a path allowlist; add a docs note that the caller owns this.
- N5 `m` lines in other sysusers files count as definitions of that group name (documented), so a harmless membership line can raise a false `shadow.*`.
- N6 Existing sysctl/sysusers/tmpfiles files are rewritten to mode 0644 regardless of their prior mode (`expected_image`).
- N7 Foreign `.conf` files with one bad line refuse the whole plan with the generic `resource.plan.failed`; name the file in a structured problem.
- N8 `sysctl.native.order` regex accepts `/lib/...` headers that Python never lists (trixie `/lib -> usr/lib`); check on the first live run that systemd does not print both.
</content>

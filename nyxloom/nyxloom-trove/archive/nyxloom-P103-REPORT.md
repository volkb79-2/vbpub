# nyxloom-P103-ciu-governance-standalone-roots -- REPORT

**Status: implementer pass complete, awaiting a fresh adversarial reviewer.**
All 9 numbered Work items done (see the discrepancy note under "Deviations"
about there being four filing tasks, not three). This package changes ZERO
Python -- `tester-unified` is run once as a pure regression check (see "Gate
run" at the end), but it cannot prove any oracle here. **The evidence below,
run by hand, is the real proof of this package.**

## What changed

- `nyxloom/ciu.global.defaults.toml.j2`: added the pinned `[governance]`
  table (`enabled=true`, `cgroup_parent="dev-background.slice"`,
  `mem_limit="2g"`, `device="/dev/vda"`) before `[ciu]` (Work item 1a).
- `pwmcp/ciu.global.defaults.toml.j2`: same table, `mem_limit="4g"`, plus the
  extra BUILD-TEST-ONLY comment paragraph (Work item 1b).
- `nyxloom/pwmcp-instance/ciu.defaults.toml.j2`: added
  `[pwmcp.governance] mem_limit="4g"` (Work item 2).
- `nyxloom/nyxloomd/ciu.defaults.toml.j2`: deleted the dead
  `cgroup_parent="nyxloom.slice"` under `[nyxloomd.runtime]` (Work item 3d);
  added `[nyxloomd.governance] mem_limit="6g"` (Work item 2b).
- `nyxloom/ntfy/ciu.compose.yml.j2`: deleted the
  `{% if ntfy.runtime.cgroup_parent %}...{% endif %}` Jinja block
  (Work item 3a).
- `nyxloom/ntfy/docker-compose.yml`: REPLACED the `cgroup_parent:
  nyxloom.slice` line with the full inline governance fragment
  (Work item 3b).
- `nyxloom/ntfy/ciu.defaults.toml.j2`: deleted the dead
  `cgroup_parent="nyxloom.slice"` under `[ntfy.runtime]` (Work item 3c).
- `nyxloom/nyxloomd/ciu.toml`: deleted the `cgroup_parent="nyxloom.slice"`
  line under `[nyxloomd.runtime]`, kept `run_as_uid`/`run_as_gid`/`docker_gid`
  (Work item 3e).
- `nyxloom/ntfy/README.md`: reworded the governance-notes line from
  `` `nyxloom.slice` cgroup `` to `` `dev-background.slice` cgroup (via ciu
  governance; inline on the plain-compose path) `` (Work item 3f).
- `nyxloom/docs/plan-resource-governance.md`: appended four dated correction
  blockquotes (both D-G0a occurrences, D-G3, D-G4) noting ciu 7.11.0 has no
  hardcoded `cgroup_parent` default and that dstdns runs
  `dev-background.slice`, not `besteffort.slice`, today (Work item 4).
- `nyxloom/nyxloom-trove/backlog/NL-6-*.md`: replaced "Observed mechanism and
  reproduction" and "Oracles" with corrected text; `status: open` ->
  `fixed` via the CLI (Work item 5).
- `nyxloom/nyxloom-trove/backlog/{NL-8,NL-9,NL-10,NL-11}-*.md`: four new
  entries filed (Work items 6-9).
- `nyxloom/nyxloom-trove/backlog/INDEX.md`: regenerated via `nyxloom backlog
  index` twice (once per backlog-touching commit), never hand-edited.

## Oracle evidence

### O1 -- committed literals + dry-run overlay, both roots

Committed-content greps (from `<worktree>/nyxloom`):

```
$ grep -qx 'cgroup_parent = "dev-background.slice"' ciu.global.defaults.toml.j2 && echo PASS
PASS
$ grep -qx 'cgroup_parent = "dev-background.slice"' ../pwmcp/ciu.global.defaults.toml.j2 && echo PASS
PASS
$ grep -qx 'device = "/dev/vda"' ciu.global.defaults.toml.j2 && echo PASS
PASS
$ grep -qx 'device = "/dev/vda"' ../pwmcp/ciu.global.defaults.toml.j2 && echo PASS
PASS
```

`ciu up --profile default --dry-run --define-root "$PWD"` (fresh run against
HEAD `955291c7`, `ciu version` confirmed `7.11.0`):

```
[SUCCESS] check passed
[INFO] [S15.G9-1] systemctl is present but systemd is not PID 1 in this mount
namespace ... skipping the slice-existence preflight
[INFO] >>> action: deploy
[INFO] --- deploying ntfy (service 'ntfy') ---
...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=2g;
mem_swap_limit=17g; mem_reservation=256m; mem_min=(not declared);
read_iops=200 (fallback default ...); write_iops=400; io_weight=(not set);
read_bps=(uncapped); write_bps=(uncapped); device=/dev/vda (explicit);
ksm_optin=off; services_injected=1 exempt=0
[STEP 16/17] --dry-run: skipping docker compose up
[INFO] --- deploying nyxloomd (service 'nyxloomd') ---
...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=6g;
mem_swap_limit=17g; mem_reservation=256m; mem_min=(not declared);
read_iops=200 (fallback default ...); write_iops=400; io_weight=(not set);
read_bps=(uncapped); write_bps=(uncapped); device=/dev/vda (explicit);
ksm_optin=off; services_injected=1 exempt=0
[STEP 16/17] --dry-run: skipping docker compose up
[INFO] DEPLOY SUMMARY
[INFO]   deployed: 2
[INFO]     + ntfy
[INFO]     + nyxloomd
[INFO]   failed:   0
[SUCCESS] all selected stacks deployed
```

`EXIT=0`. Overlay checks:

```
$ grep -c '^    cgroup_parent: dev-background\.slice$' ntfy/.ciu/ciu.compose.overlay.yml
1
$ grep -c '^    cgroup_parent: dev-background\.slice$' nyxloomd/.ciu/ciu.compose.overlay.yml
1
```

Both greps print exactly `1`, both `[GOVERNANCE]` lines report `device=/dev/vda
(explicit)` and `services_injected=1 exempt=0`, for both `ntfy` and
`nyxloomd`. **O1 PASS.**

### O2 -- ntfy governed by ciu alone (three assertions)

**(i) Template no longer emits a placement key:**

```
$ ! grep -q cgroup_parent ntfy/ciu.compose.yml.j2 && echo PASS
PASS
$ ! grep -q cgroup_parent ntfy/ciu.defaults.toml.j2 && echo PASS
PASS
$ ! grep -q cgroup_parent nyxloomd/ciu.defaults.toml.j2 && echo PASS
PASS
$ ! grep -q cgroup_parent nyxloomd/ciu.toml && echo PASS
PASS
```

**(ii) Rendered compose is clean** (from O1's dry-run):

```
$ grep -c cgroup_parent ntfy/ciu.compose.yml
0
```

**(iii) Fictional slice literal gone from config/compose, run from the
worktree root:**

```
$ cd /workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance
$ git grep -l 'nyxloom\.slice' -- 'nyxloom/**/*.toml' 'nyxloom/**/*.j2' 'nyxloom/**/*.yml' 'pwmcp/**/*.toml' 'pwmcp/**/*.j2' 'pwmcp/**/*.yml'
(no output)
$ echo $?
1
```

`git grep -l` with no matches exits `1` and prints nothing -- the same
command measured at `input_revision` returned exactly the four files the
handoff names (`ntfy/ciu.defaults.toml.j2`, `ntfy/docker-compose.yml`,
`nyxloomd/ciu.defaults.toml.j2`, `nyxloomd/ciu.toml`); it now returns none.
**O2 PASS**, all three assertions.

### O2b -- plain-compose path (ntfy/docker-compose.yml) carries the caps inline

```
$ grep -qx '    cgroup_parent: dev-background.slice' ntfy/docker-compose.yml && echo PASS
PASS
$ grep -q 'mem_limit: 2g' ntfy/docker-compose.yml && echo PASS
PASS
$ grep -q 'memswap_limit: 17g' ntfy/docker-compose.yml && echo PASS
PASS
$ grep -q 'mem_reservation: 256m' ntfy/docker-compose.yml && echo PASS
PASS
$ grep -q 'rate: 200' ntfy/docker-compose.yml && echo PASS
PASS
$ grep -q 'rate: 400' ntfy/docker-compose.yml && echo PASS
PASS
```

Diffed against `ntfy/.ciu/ciu.compose.overlay.yml` from O1's run (whitespace-
normalized, since the two files nest the fragment at different indent
depths):

```
$ diff <(sed -n '/cgroup_parent: dev-background.slice/,/rate: 400/p' ntfy/docker-compose.yml | sed 's/^ *//') \
       <(sed -n '/cgroup_parent: dev-background.slice/,/rate: 400/p' ntfy/.ciu/ciu.compose.overlay.yml | sed 's/^ *//')
$ echo $?
0
```

Content-identical. **O2b PASS.**

### O3 -- the second root, pwmcp, proven independently

```
$ cd /workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance/pwmcp
$ ciu up --dir . --dry-run --define-root "$PWD"
...
[INFO] Creating docker network: pwmcp-016b19-network
[INFO] Connecting devcontainer dstdns-devcontainer-vb to pwmcp-016b19-network
...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=4g;
mem_swap_limit=17g; mem_reservation=256m; mem_min=(not declared);
read_iops=200 (fallback default ...); write_iops=400; io_weight=(not set);
read_bps=(uncapped); write_bps=(uncapped); device=/dev/vda (explicit);
ksm_optin=off; services_injected=1 exempt=0
[STEP 16/17] --dry-run: skipping docker compose up
$ echo $?
0
```

Overlay content:

```
$ cat .ciu/ciu.compose.overlay.yml
services:
  pwmcp:
    cgroup_parent: dev-background.slice
    mem_limit: 4g
    memswap_limit: 17g
    mem_reservation: 256m
    blkio_config:
      device_read_iops:
      - path: /dev/vda
        rate: 200
      device_write_iops:
      - path: /dev/vda
        rate: 400
```

File-identity checks:

```
$ grep -n '^\[governance\]' ciu.global.defaults.toml.j2
59:[governance]
$ ! grep -q governance ciu.global.toml.j2 && echo PASS
PASS
```

Both `cgroup_parent: dev-background.slice` and `mem_limit: 4g` present; the
higher-precedence `ciu.global.toml.j2` remains silent. **O3 PASS.**

**Mandatory teardown**, performed immediately after each of the two runs of
this oracle (mid-work and the final re-verification):

```
$ docker network disconnect pwmcp-016b19-network dstdns-devcontainer-vb
$ docker network rm pwmcp-016b19-network
pwmcp-016b19-network
$ docker network ls | grep -i pwmcp
$ echo $?
1
```

Confirmed absent both times. The worktree's own
`nyxloom-p103-ciu-governance-2b92d3-network` (from `ciu worktree create`,
recorded in `ciu.worktree-instance.json`) was left untouched throughout,
confirmed present via `docker network ls` after each teardown.

### O4 -- per-stack sizing is real; three distinct values in one sweep

```
$ ciu up --profile tools --dry-run --define-root "$PWD"
...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=4g;
mem_swap_limit=17g; mem_reservation=256m; ... device=/dev/vda (explicit);
ksm_optin=off; services_injected=1 exempt=0
[INFO] DEPLOY SUMMARY
[INFO]   deployed: 1
[INFO]     + pwmcp-instance
[SUCCESS] all selected stacks deployed
```

Combined with O1's default-profile run (same worktree state), all three
stacks' overlays in one sweep:

```
$ grep -H mem_limit ntfy/.ciu/ciu.compose.overlay.yml nyxloomd/.ciu/ciu.compose.overlay.yml pwmcp-instance/.ciu/ciu.compose.overlay.yml
ntfy/.ciu/ciu.compose.overlay.yml:    mem_limit: 2g
nyxloomd/.ciu/ciu.compose.overlay.yml:    mem_limit: 6g
pwmcp-instance/.ciu/ciu.compose.overlay.yml:    mem_limit: 4g
```

Three DISTINCT values (2g / 6g / 4g), each traceable to the file that
declares it: nyxloom's root `[governance] mem_limit="2g"` for `ntfy`,
`[nyxloomd.governance] mem_limit="6g"` for `nyxloomd`, and
`[pwmcp.governance] mem_limit="4g"` for `pwmcp-instance`. **O4 PASS.**

### O5 -- mutation-checked, all runs by hand

**Break (a): `enabled = false`.** Edited the committed
`[governance]` table in `nyxloom/ciu.global.defaults.toml.j2`:

```
$ ciu up --profile default --dry-run --define-root "$PWD" | grep -E 'GOVERNANCE|DEPLOY SUMMARY|deployed:|SUCCESS'
[SUCCESS] check passed
[GOVERNANCE] disabled ([<root>.governance].enabled is false)
[INFO] DEPLOY SUMMARY
[INFO]   deployed: 2
[SUCCESS] all selected stacks deployed
```

```
$ cat ntfy/.ciu/ciu.compose.overlay.yml
services:
  ntfy:
    volumes:
    - type: bind
      source: .../ntfy/.ciu/rendered/ntfy/etc/ntfy
      target: /etc/ntfy
      read_only: true
$ grep -c '^    cgroup_parent: dev-background\.slice$' ntfy/.ciu/ciu.compose.overlay.yml
0
$ cat nyxloomd/.ciu/ciu.compose.overlay.yml
cat: nyxloomd/.ciu/ciu.compose.overlay.yml: No such file or directory
```

`ntfy`'s overlay carries only its configfile volume bind (no governance
keys at all); `nyxloomd`'s overlay is not created at all (ciu's
`generate_overlay` returns `None` when there is nothing to inject -- no
secrets, no configfiles, no governance injections, no image revisions --
matching the handoff's own documented true-baseline behaviour). **FAIL
confirmed as intended.**

**Finding**: the handoff's O5 text states the `[GOVERNANCE]` log line is
"absent entirely" when disabled. The measured ciu 7.11.0 behaviour instead
prints `[GOVERNANCE] disabled ([<root>.governance].enabled is false)` for any
stack that itself declares a `[<root>.governance]` table (`nyxloomd` does,
via Work item 2b) -- the log line IS present, just without injection
details, rather than fully absent. This does not weaken the oracle (the
actual cgroup_parent/mem_limit absence is what is being asserted, and it is
confirmed absent), but is a factual correction to the handoff's stated
expectation for a future reader of this REPORT or a future revision of the
handoff.

Restored `enabled = true`:

```
$ ciu up --profile default --dry-run --define-root "$PWD" | grep -E 'GOVERNANCE'
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=2g; ...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=6g; ...
```

Green again.

**Break (b), five runs, exact order:**

(i) Deleted the `[governance]` block from `ciu.global.defaults.toml.j2`
(confirmed: `grep -c governance ciu.global.defaults.toml.j2` -> `0`).

(ii) Wrote the identical block verbatim into an UNTRACKED
`ciu.global.toml.j2`:

```
$ git status --short ciu.global.toml.j2
?? ciu.global.toml.j2
```

(iii) Ran O1 -- PASSES:

```
$ ciu up --profile default --dry-run --define-root "$PWD" | grep -E 'GOVERNANCE|DEPLOY SUMMARY|deployed:|SUCCESS'
[SUCCESS] check passed
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=2g; ...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=6g; ...
[INFO] DEPLOY SUMMARY
[INFO]   deployed: 2
[SUCCESS] all selected stacks deployed
```

This is the false-PASS the break exists to record: a site-layer "fix" looks
green locally.

(iv) Deleted ONLY the untracked `ciu.global.toml.j2`, kept the defaults-file
deletion. Cleaned stale `.ciu/` artifacts from step (iii) FIRST (a
first attempt without doing this read step (iii)'s stale overlay file and
falsely showed injection still present -- `generate_overlay`'s early-return
path does not delete or overwrite an existing overlay when governance
resolves to disabled; traced to
`ciu/src/ciu/composefile.py`'s `if not materialized and not configfile_mounts
and not governance_injections and not image_revisions: return None` via a
direct Python invocation of `governance.resolve_stack_governance`/
`resolve_config`, confirming `enabled` resolves to `False` for a bare
`{"mem_limit": "6g"}` stack table with no root layer present). Re-ran clean:

```
$ ciu up --profile default --dry-run --define-root "$PWD" | grep -E 'GOVERNANCE|DEPLOY SUMMARY|deployed:|SUCCESS'
[SUCCESS] check passed
[GOVERNANCE] disabled ([<root>.governance].enabled is false)
[INFO] DEPLOY SUMMARY
[INFO]   deployed: 2
[SUCCESS] all selected stacks deployed
```

(Only one `[GOVERNANCE]` line: `ntfy` declares no `[ntfy.governance]` table
at all, so `governance` is `None` and no log line is printed for it at
all -- per `composefile.generate_overlay`'s own docstring. `nyxloomd` does
declare `[nyxloomd.governance] mem_limit="6g"`, so it computes and prints
"disabled".)

```
$ grep -c '^    cgroup_parent: dev-background\.slice$' ntfy/.ciu/ciu.compose.overlay.yml
0
$ ls nyxloomd/.ciu/ 2>&1
ls: cannot access 'nyxloomd/.ciu/': No such file or directory
```

**FAIL confirmed**: no cgroup_parent injected for either stack, proving the
per-stack `[nyxloomd.governance]` table ALONE (without a root layer) is not
sufficient -- the layering is real.

(v) Restore. `git checkout -- ciu.global.defaults.toml.j2` --
**process note**: at the time this break sequence was originally run,
Work item 1a had NOT yet been committed, so `git checkout --` reverted the
file all the way to the pre-P103 baseline (HEAD `6322251b`), not to "the
fix" -- confirmed via `grep -n governance ciu.global.defaults.toml.j2`
printing nothing. Work item 1a's block was re-applied by hand (verified
byte-identical, via `git diff`, to the version committed moments later in
`c703cd04`). Re-ran O1:

```
$ ciu up --profile default --dry-run --define-root "$PWD" | grep -E 'GOVERNANCE'
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=2g; ...
[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=6g; ...
```

Green again. **This break sequence was then re-run a second time, cleanly,
against the fully-committed HEAD (`955291c7`)**, where `git checkout --`
correctly restored the committed fix rather than the pre-P103 baseline,
confirming the process note above does not affect the final, committed
state -- only the order operations were run in during development. **O5
PASS, both breaks, both directions.**

## Environment / escalate_if verification

- `git grep -n '\[governance\]' -- nyxloom pwmcp` (worktree root, before any
  edit): zero live table declarations (prose/handoff/backlog hits only). No
  trigger.
- `ciu version` -> `7.11.0`. Matches exactly.
- `grep -n governance pwmcp/ciu.global.toml.j2` -> no hits, both before and
  after this package's edits (re-checked at O3 time above). No trigger.
- `grep -c 'trust = "operator"' routes.host.toml` -> `8`.
  `nyxloom-trove/nyxloom.toml`'s `max_active_tasks` -> `5`. Both match the
  handoff's derivation inputs for Work item 2b's `6g` ceiling exactly.
- `dev-background.slice`'s live `MemoryMax` could NOT be directly queried
  from inside this devcontainer: `/sys/fs/cgroup`'s own root is this
  container's own cgroup (`cat /proc/self/cgroup` -> `0::/`), with no host
  cgroup namespace visible, and `systemctl` reports "systemd is not PID 1"
  the same way ciu's own preflight does. Confirmed no `host-setup.env` file
  exists anywhere under `modern-debian-tools-python-debug/host-setup/`
  (only `host-setup.env.example`), matching the handoff's own statement that
  the 8G figure is derived from the example file, not a deployed one -- no
  evidence found of divergence from that assumption, but the live value
  itself remains unverified from inside this environment (the same
  constraint the handoff's own probe log names for the slice-existence
  preflight).
- No `ciu up --dry-run` run failed for any reason other than a governance
  finding at any point.
- Every `nyxloomd/ciu.toml` render produced exactly the two expected/
  discarded hunks (new `[nyxloomd.governance] mem_limit="6g"` table, stripped
  CR-16 comment block) on every run, checked via `git diff` before each
  `git checkout --`; no third hunk ever appeared.

No `escalate_if` trigger fired at any point.

## Deviations / ambiguities

1. **Missing CARVE-REVIEW file.** The dispatch instructions named
   `nyxloom-trove/reports/nyxloom-P103-CARVE-REVIEW.md` ("all 4 rounds") as
   required reading; no such file exists in this worktree (confirmed via
   `find` and `git log --all --diff-filter=A`). The carve's git history shows
   four repair rounds folded directly into carve commits instead. Read the
   935-line handoff in full in its place -- it carries the same reasoning
   inline (every scope.touch comment, the Memory sizing section, the Probe
   log). Not a blocker; flagging for the reviewer/controller in case this
   indicates a lost artifact from the carve process.
2. **Work item count discrepancy.** The handoff's `scope.touch` frontmatter
   comment for `nyxloom-trove/backlog/` says "the THREE new entries Work
   items 6, 7 and 8 create", but the numbered "Implementation packet" body
   has FOUR filing tasks (6, 7, 8, and 9 -- "File a fourth backlog entry",
   covering nyxloomd's unmeasured memory ceiling). Filed all four (NL-8
   through NL-11), following the more specific and fully-realised numbered
   Work items list over the summary comment; the directory-level
   `scope.touch` permission covers any number of new entries regardless.
3. **`pwmcp/ciu.global.defaults.toml.j2`'s "extra first line" placement.**
   The handoff's Work item 1b says to insert "this extra first line inside
   the comment header" for the BUILD-TEST-ONLY paragraph, without pinning an
   exact position relative to the shared "Resource governance (ciu S15.10)"
   text. Placed the pwmcp-specific paragraph FIRST (before the shared text),
   mirroring how the file's own pre-existing top-of-file header already
   opens with its own BUILD-TEST-ONLY statement. A reviewer may reasonably
   read this differently; the content itself is verbatim either way.
4. **`backlog new`'s CLI flags.** The handoff's example command for Work
   items 6/7 uses `--title "<title>"`; the actual CLI takes `title` as a
   positional argument (confirmed via `backlog new --help`, as the handoff
   itself instructs checking). Filed all four entries with the correct
   positional form.
5. **Anchor point for Work item 2's `[pwmcp.governance]` table.** The
   handoff pins the table's TEXT but not an exact anchor line in
   `pwmcp-instance/ciu.defaults.toml.j2`; appended it at the file's natural
   end, after `[pwmcp.tunables]`.

None of these affected the pinned TOML/YAML content itself, which was
reproduced byte-for-byte in every case (verified via direct `grep -qx`/`diff`
checks shown above, not by eye).

## Gate run (regression check ONLY)

Waited for a different package's `tester-unified:local` container
(`run-gate-vbpub-coverage-1515189-1788843729`, a `cmru` coverage run,
confirmed genuinely active via `docker exec ... ps aux` -- real pytest with
`--cov-fail-under=100`, not hung) to clear, per the standing
one-gate-container rule (shared host, also runs production game servers).

Ran from `<worktree>/nyxloom` (no `--worktree` flag, already cwd'd inside the
target tree):

```
$ python3 run-gate.py tester-unified
```

`docker update --cpus=3` applied to the new container
(`run-gate-vbpub-tester-unified-1588937-1788844006`) within seconds of it
starting; confirmed via `docker inspect --format '{{.HostConfig.NanoCpus}}'`
-> `3000000000`. Confirmed via `docker exec ... ps aux` mid-run that it was
genuinely executing `pytest tests -n auto -q --cov=src/nyxloom` with an
active xdist worker, not hung. The host hit heavy memory pressure during the
wait from unrelated concurrent sessions (`free -h` showed as little as
236Mi free RAM, load average 24.93; two background wait loops in this
session were killed by the system's own low-memory task reaper as a result)
-- this did not affect the gate container itself, which is a different
process the reaper did not target, and was confirmed still genuinely
running via a direct `docker ps`/`docker exec` check immediately afterward.
A THIRD-PARTY package's gate container also started and finished
concurrently at one point (`run-gate-vbpub-assay-1602268-...`) -- not this
session's to manage; this package's own gate ran to completion independently.

**Verdict read as a SEPARATE step from running the gate** (the run's own log
was read directly via a file, not piped/tailed; the verdict JSON was then
read independently as a second source, per LESSONS L4):

```
run-gate: rev 36 | lane tester-unified | env [environments.tester-unified] in central .../run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: budget 30m (advisory)
assay-4.0.0.pyz: OK
run-gate: progress tester-unified: no candidate events (not an R2 lane, or the judge writes none)
tester-unified: PASS (exit 0)
  commit: 3a94ca9f459cb29700a8397496ea491751531572
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance/nyxloom/.assay/verdict-tester-unified.json
run-gate: lane 'tester-unified' exit 0
```

`.assay/verdict-tester-unified.json` (read independently, full content
below):

```json
"outcome": "PASS",
"exit_code": 0,
"commit": "3a94ca9f459cb29700a8397496ea491751531572",
"claims": [
  {"rigor": "R0", "status": "PASS", "verified_by_assay": true},
  {"rigor": "R1", "status": "PASS", "verified_by_assay": true,
   "coverage": {"pct": 100.0, "considered": 0, "executable": 0, "covered": 0}}
]
```

R0 = `tests-pass` (the full `pytest tests -n auto -q` suite passed inside the
actual container). R1 = `changed-line-coverage`, resolved against merge-base
`ff3d5303`, `source_roots: ["src"]` -- and here is the handoff's own claim
made concrete: **`considered: 0, executable: 0, covered: 0`**. Zero Python
statement lines changed under `src/` since the branch diverged from main, so
the coverage check has nothing to consider at all; `pct: 100.0` is the
trivial 0/0 case, not evidence of anything this package did. This is
`tester-unified`'s real, declared `asserts`
(`nyxloom-trove/nyxloom.toml:91`: `["tests-pass", "changed-line-coverage",
"canary-verified"]`) proving, in the verdict artifact itself, exactly the
"Gate argv" section's claim: **a green `tester-unified` run is not evidence
for any oracle in this package** -- it proves no Python was disturbed
(R0/tests-pass) and nothing more.

The judged commit, `3a94ca9f`, is this package's LOG+REPORT commit made
immediately before this run (the tree had to be clean for `run-gate.py` to
accept it). The gate's own container was gone from `docker ps -a`
immediately after the run finished -- `run-gate.py` tears its own container
down; confirmed independently via a direct `docker ps` check (not merely
inferred from the run's own log) before reading the verdict.

## Conclusion

**GREEN, in the sense available to this package.** All 9 Work items complete.
O1-O5 all have direct, hand-run evidence with verbatim command output above
-- this is the real proof, since the registered gate cannot verify any
oracle here (confirmed by the verdict itself: R1's `considered: 0` proves
the coverage check was vacuous, exactly as the handoff predicted). The
`tester-unified` regression check itself is PASS (exit 0, commit
`3a94ca9f`), confirming no Python was disturbed.

No `escalate_if` trigger fired at any point. Five deviations/ambiguities are
recorded above for the reviewer, none of which affected the pinned
TOML/YAML content (all reproduced byte-for-byte, verified programmatically).

Not merged and not claimed ready-to-merge -- that determination is a fresh
adversarial reviewer's, per doctrine. This package's implementer role stops
here.

Final commit range: `6322251b..<this LOG/REPORT gate-run update commit>`
(the freeze tip through this file's own final commit -- see the LOG's last
entry for the exact hash).

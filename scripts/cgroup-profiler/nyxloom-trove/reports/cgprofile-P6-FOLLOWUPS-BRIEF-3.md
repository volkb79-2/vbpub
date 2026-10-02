# cgprofile-P6-FOLLOWUPS — BRIEF for session 3 (successor)

Session 2 (fresh Sonnet, checkpoint clause HARD) shipped C3 and C4, cut
clean at the post-C4-commit boundary per the checkpoint clause (~72 tool
calls, past the ~60 ARM threshold, under the ~90 hard ceiling). This is
the self-authored continuation brief + retention prompt the controller
dispatches a fresh successor with.

## State

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another worktree.
- Tip: `e053276b` (C4, CP-6). Full history this package:
  `376bb9cb` (C1 CP-4) → `16b01c1c` (C2 CP-5) → `614dcd9f` (session-1
  checkpoint docs) → `907ddd50` (C3 CP-7) → `e053276b` (C4 CP-6, this
  session).
- Base was `16f3a29f` (P1's tip at dispatch, RW-35). Session 1's BRIEF-2
  said "check with the controller whether P1 has since merged into
  `main`" — this was NOT checked again this session (no merge signal seen
  in the controller log entries read); still assume base = P1's tip
  unless the controller says otherwise. Do NOT merge speculatively.
- Full per-deliverable evidence for C1-C4: `cgprofile-P6-FOLLOWUPS-REPORT.md`
  (sessions 1+2 combined). Full orientation + implementation narrative:
  `cgprofile-P6-FOLLOWUPS-LOG.md` (four dated commit entries plus a
  session-2 orientation entry).

## What remains (handoff order, C5 onward)

- **C5 — `cgprofile.slice` (D-29) + `infra/README.md` + `ctl host` §8.5.**
  Add `scripts/cgroup-profiler/infra/cgprofile.slice` (`MemoryMin=128M`,
  `MemoryHigh=768M`, `MemoryMax=1G`, `CPUWeight=100`, `IOWeight=50`, a
  `Description` naming RG-55 D-29) + `infra/README.md` (operator install
  steps; what happens when NOT installed: systemd creates the slice
  transiently/unbounded, `ctl host` says so). The ciu compose template
  (`ciu.compose.yml.j2`) AUTHORS `cgroup_parent: cgprofile.slice`
  (replacing whatever it renders today — check the current template
  first, this session did not open it). `ctl host` (`lib/serve.py`'s
  `handle_host`/HostSnapshot builder) gains `gates_slice` and
  `daemon_slice` exactly per contract §8.5 (`present: false` shape when
  absent; gates slice name from `serve --gates-slice`, default
  `dev-gates.slice` under `dev.slice`). Goldens:
  `fixtures/rg55/host-v1.1.json` (check whether this fixture dir/file
  already exists in the worktree — session 2 did not check). This is the
  smallest remaining deliverable (mostly a static unit file + one new
  `HostSnapshot` block) — a natural C5 starting point.
- **C6 — CP-2 socket carrier (D-30, §8.1, §8.6).** Largest remaining
  deliverable. `/run/cgprofile/ctl.sock` host-visible via a compose
  bind-mount; daemon asserts `root:docker 0770` on the dir, `0660` on the
  socket at start; peer creds (`SO_PEERCRED`, `CGPROFILE_ALLOW_UIDS`);
  `ctl version` gains `transports`; per-verb goldens
  `fixtures/rg55/socket/<verb>-{request,response}.json`; new
  `docs/PROTOCOL.md`; a both-carriers parity test (one pytest running
  every verb through both carriers against one serve loop on a temp
  socket, diffing the JSON).
- **C7 — CP-8 watch role (D-27, §8.2, §8.4).** File the CP-8 backlog row
  FIRST (same shape as CP-4/CP-5/CP-6/CP-7's rows — CP-6 and CP-7 are
  already FIXED as of this session but their rows were not yet updated to
  say so, see C9). `start` gains `--progress-stream`, `--idle-bound`,
  `--ceiling`, `--on-stall`; the §8.4 `liveness` block; PSI-paused idle
  clock; state machine (ok/stalled/hung/runaway/throttled/over_ceiling);
  `--on-stall kill` → `cgroup.kill` on the leaf when placed (C8 not done
  yet at C7's start — so this path is SIGKILL-to-subtree only until C8
  lands; note this explicitly rather than silently no-op); `ctl watch`
  streaming on both carriers (exec: hold the socket open, forward each
  line with a flush).
- **C8 — CP-9 placement (D-20, D-25, §8.3).** File CP-9 first. `start
  --place --memory-high --memory-max --cpu-weight`; the ONE non-leaf
  whitelist write (`+memory +cpu +pids` to the gates slice's
  `cgroup.subtree_control`, `+` only, per RW-35(a)); leaf lifecycle;
  D-15 write-guard whitelist extension + a refusal test.
- **C9 — close-out.** `docs/PROTOCOL.md` complete; README/ATTACH-GUIDE/
  DESIGN.md updates; `CHANGES.md [Unreleased]`; backlog rows CP-2, CP-4..
  CP-9 → FIXED with commit hashes (CP-4=`376bb9cb`, CP-5=`16b01c1c`,
  CP-7=`907ddd50`, CP-6=`e053276b` — **update these two rows to FIXED now
  if a future session has spare budget before C9 proper**, they are
  currently still `status: open` in `nyxloom-trove/backlog/`); version
  string `1.0.0` → `1.1.0` (grep, confirmed NOT yet bumped through C4 —
  `lib/serve.py`'s `CGPROFILE_VERSION` constant still reads `"1.0.0"` as
  of this session's close); contract mirror byte-identical; `INDEX.md`
  regenerated.

## Orientation already banked (do NOT re-read from scratch)

From session 1: controller log RW-30, RW-31, RW-34, RW-35 (LOG quotes
them in full); contract `RG55-INTERFACE-CONTRACT.md` §8 in full.

From session 2 (this session): the design doc
`DESIGN-2026-09-12-liveness-placement-admission.md` was read DIRECTLY in
full (§1-§7, A1 D-27..D-29, A2 D-30) — session 1 only had it second-hand.
Key facts for C6-C8, so a session-3 successor need not re-read the whole
doc: **D-27** the watcher is the daemon itself, singleton, no detached
run-gate owner (D-21 dropped by D-28) — `ctl watch` streams, `--on-stall
kill` enforces via `cgroup.kill`/SIGKILL. **D-29** the daemon ships its
OWN `cgprofile.slice` (not a `dev-*` slice — that's mdt's, for dev LOAD
containment only); the compose template authors `cgroup_parent:
cgprofile.slice` directly, no env var, no fallback. **D-30/A2** one
protocol, two carriers (exec always-on default, socket opt-in via a
shared mounted dir), full parity required, `RUN_GATE_PROFILE_TRANSPORT`
client-side (not this package's concern — that's P5/run-gate). **D-25**
whitelist extension for C8's placement is additive to D-15, not a
relaxation — the leaf under the gates slice is the ONLY new writable
path. Full contract §8.1-§8.9 text is in `RG55-INTERFACE-CONTRACT.md` on
`main` (session 1 already read and summarized it; re-fetch verbatim
sections as needed while implementing rather than re-reading all of §8
cold).

Also from session 2: `lib/analyze.py`'s `_CHART_META`/`build_series`/
`build()` structure, `lib/model.py`'s `PANELS`/`Series`/`Analysis`,
`lib/report_html.py`'s `GROUP_LABELS` (confirmed generic, no further
changes needed there for anything C5-C9 touches, but re-verify if a
future deliverable adds a NEW panel group), `lib/damon.py`'s
`last_class_bytes`, `cgprofile.py`'s `_limits_snapshot`/`cmd_collect`,
`lib/serve.py`'s `_manifest_for`/`_create_session_locked`/
`_on_session_sample` in the areas C3/C4 touched.

Still NOT read, needed before C5-C8: `ciu.compose.yml.j2` (current
`cgroup_parent` rendering — C5 needs this FIRST); `lib/serve.py`'s
`handle_host`/HostSnapshot builder (for C5's `gates_slice`/`daemon_slice`
additions) and `handle_start`/argument parsing (for C6's socket-carrier
request handling and C7/C8's new `start` options) beyond what C3/C4
touched; backlog rows CP-2 (read in full — see below), CP-8/CP-9 (do not
exist yet, C7/C8 file them); P1's `-REPORT.md`/`-LOG.md` beyond the
dispatch-table row (subtree resolver's pid-discovery shape for C7's
`--on-stall kill`/C8's placement); `README.md` "Running the daemon";
`docs/ATTACH-GUIDE.md` beyond one grep hit; `lib/subtree.py`,
`lib/summary.py` beyond what C2/C4's greps touched; whether
`fixtures/rg55/host-v1.1.json`/`fixtures/rg55/socket/` already exist in
this worktree (check `ls tests/fixtures/rg55/` — session 2 never looked
there at all, this whole directory is unverified).

CP-2's backlog row WAS read in full this session (quoted findings below,
so a session-3 successor doesn't need to re-fetch it): it frames the
socket carrier as "not designed here... sketch: mount
`/run/cgprofile/ctl.sock` to a well-known host path... Must preserve
every safety property of D-15." The actual design is now settled by
D-30/§8.1/§8.6 (read above) — CP-2's row is superseded by the contract,
not a separate source of truth; C9 should mark it FIXED alongside the
others once C6 ships.

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
cat /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
docker ps --no-trunc --format '{{.Names}}\t{{.Status}}' | grep -c run-gate-vbpub-r2
```
Re-check HOST LOAD first (both named mutation runs were STILL alive at
session 2's close — `run-gate-vbpub-r2-2315801-1789214565` container up,
`assay-r2` pid 2415767 alive — a session 3 should not assume either has
exited; if BOTH are gone, the r0/r1 lane + r3 + eventually r2 become
available; if either is still alive, stay in targeted-pytest-only mode).
Then `cat ciu.compose.yml.j2` and `grep -n 'cgroup_parent\|handle_host\|HostSnapshot' lib/serve.py ciu.compose.yml.j2`,
then start C5 — it is the smallest remaining deliverable.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the REPORT's C1-C4 evidence (tip hashes
`376bb9cb`/`16b01c1c`/`907ddd50`/`e053276b`, test-pass counts, the CP-5
red-first bug-catch narrative, the C3 git-checkout mistake and its fix,
the C3/C4 structural findings — oversubscription is a permanent no-op for
daemon sessions, report_html.py needed zero changes for DAMON); the
design-doc key facts (D-27/D-29/D-30/D-25) summarized above — don't
re-read the whole doc; the exact HOST LOAD recheck commands above; the
`cp`-backup/restore mutation-testing discipline (NOT `git checkout` on an
uncommitted worktree).
DROP: the C1-C4 implementation blow-by-blow in the LOG (already shipped
and committed — read it only if debugging a regression in one of those
four specifically, not for general context); both sessions' own
tool-call-by-tool-call narration.

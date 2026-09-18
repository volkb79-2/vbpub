# cgprofile-P6-FOLLOWUPS — BRIEF for session 4 (successor)

Session 3 (fresh Sonnet, checkpoint clause HARD) shipped C5, cutting
clean at the post-C5-commit boundary well under the ~90 hard ceiling —
not a forced cut, a deliberate "smallest remaining deliverable is done,
stop before folding the LARGEST one (C6) into the same session" choice.
This is the self-authored continuation brief + retention prompt the
controller dispatches a fresh successor with.

## State

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another worktree.
- Tip: this session's checkpoint-docs commit (REPORT + this BRIEF), on
  top of `39d43934` (C5, `cgprofile.slice` + `ctl host` §8.5). Full
  history this package: `376bb9cb` (C1 CP-4) → `16b01c1c` (C2 CP-5) →
  `614dcd9f` (session-1 checkpoint docs) → `907ddd50` (C3 CP-7) →
  `e053276b` (C4 CP-6) → `36859c77` (session-2 checkpoint docs) →
  `39d43934` (C5) → this session's checkpoint docs commit.
- Base is still assumed to be P1's tip (`16f3a29f` at dispatch, RW-35) —
  NOT re-verified this session either (no merge signal seen in the
  controller log's dispatch-table row, which still shows P1 as a
  separate `.worktrees/rg55-profiler-daemon` entry with no "merged"
  note). Do NOT merge speculatively; check the controller log's dispatch
  table (and the "P6" row's own free-text) fresh before assuming
  anything has changed.
- Full per-deliverable evidence for C1-C5:
  `cgprofile-P6-FOLLOWUPS-REPORT.md` (sessions 1-3 combined, C5's own
  section has the file-by-file change list, the v1-golden-compatibility
  fix for THREE `_host_snapshot()` call sites, the new
  `tests/fixtures/rg55/host-v1.1.json` golden and how it was generated,
  and the 5-mutant/2-cluster mutation evidence). Full orientation +
  implementation narrative: `cgprofile-P6-FOLLOWUPS-LOG.md` (session 3's
  own entry has two structural findings worth re-reading before touching
  `lib/serve.py` again: `_host_snapshot()` has THREE call sites, not the
  one the handoff's prose names; `slice_to_path`'s hyphen-hierarchy
  encoding nests a multi-hyphen slice name deeper than a naive read
  expects).

## What remains (handoff order, C6 onward)

- **C6 — CP-2 socket carrier (D-30, §8.1, §8.6).** Still the LARGEST
  remaining deliverable, unchanged from BRIEF-3's own assessment: the
  serve loop's listener `/run/cgprofile/ctl.sock` becomes host-visible
  (compose bind-mount `/run/cgprofile` — NOTE: C5 did NOT add this mount;
  it only changed `cgroup_parent`. C6 still needs to add the volume/mount
  itself to `ciu.compose.yml.j2`); daemon asserts `root:docker 0770` on
  the directory and `0660` on the socket at start (RW-35(b): socket group
  = the MOUNTED DIRECTORY'S gid, not a hardcoded group — the host
  `tmpfiles.d` entry `mdt-cgprofile.conf`, per RW-37, is the source of
  truth; a root:root directory means "socket carrier root-only until
  host-setup is installed" — one INFO log line, exec keeps working
  either way); request line on the socket exactly §8.1
  `{"verb","args","contract":1}` (`args` = long-option names without
  dashes — the in-image `ctl` is the reference translator); peer creds
  (`SO_PEERCRED`, uid 0 always allowed, `CGPROFILE_ALLOW_UIDS` comma-list
  restricts further, refused → `peer-refused`); `ctl version` gains
  `transports` (§8.6 shape already quoted in full in the HANDOFF/contract
  — re-fetch verbatim rather than re-deriving); new `docs/PROTOCOL.md`
  documenting every verb's `args` names; per-verb goldens
  `fixtures/rg55/socket/<verb>-{request,response}.json` for EVERY verb
  (`start`, `status`, `stop`, `report`, `version`, `host`, `gc`, and once
  C7/C8 land, `watch`/placement options too — but C6 itself only needs
  the verbs that exist NOW); a parity pytest that runs every verb through
  BOTH carriers against one serve loop on a temp socket and diffs the
  JSON (exec carrier simulated in-process the same way
  `test_cli_host_v1`/`test_cli_golden_round_trip_...` already do via
  `cg.main(["ctl", "--socket", ...])` — reuse that pattern, do not invent
  a new one). This is genuinely the biggest single piece of work left in
  the package — budget a full session for it, likely with its own
  checkpoint cut before CP-8/CP-9.
- **C7 — CP-8 watch role (D-27, §8.2, §8.4).** File the CP-8 backlog row
  FIRST (same shape as CP-4/CP-5/CP-6/CP-7's rows — those four are ALL
  now FIXED as of C5's close; see below, this session updated them).
  Unchanged from BRIEF-3/the HANDOFF's own description — re-read §8.2/
  §8.4 verbatim from the contract rather than from a summary, this is a
  state-machine-heavy deliverable where the exact wording matters
  (ok/stalled/hung/runaway/throttled/over_ceiling; the PSI-paused idle
  clock; `--on-stall kill` is SIGKILL-to-subtree only until C8 lands,
  note that explicitly rather than silently no-op).
- **C8 — CP-9 placement (D-20, D-25, §8.3).** File CP-9 first. Unchanged
  from BRIEF-3/HANDOFF. The ONE non-leaf whitelist write
  (`+memory +cpu +pids` into the gates slice's `cgroup.subtree_control`,
  `+` only, RW-35(a)) — C5's `_gates_slice_snapshot()` already READS this
  slice (`gates_slice_name`, default `dev-gates.slice`, resolved via
  `targets_mod.slice_to_path`) but never writes to it; C8 is the first
  deliverable that writes. `_writable_roots()`/`_guard_path()` in
  `lib/serve.py` (read this session but not modified — the D-15 write
  guard C4's own header comment documents) is the extension point; a
  refusal test proves the whitelist, not just the happy path.
- **C9 — close-out.** `docs/PROTOCOL.md` complete (C6 creates it, C9
  finishes it); README/ATTACH-GUIDE/DESIGN.md updates; `CHANGES.md
  [Unreleased]`; backlog rows CP-2, CP-8, CP-9 → FIXED (CP-4/CP-5/CP-6/
  CP-7 already FIXED as of THIS session, see below — do not redo them);
  version string `1.0.0` → `1.1.0` (grep `CGPROFILE_VERSION` — confirmed
  STILL `"1.0.0"` as of C5's close, C5 deliberately did not touch it,
  matching BRIEF-3's own note that this is a C9 task); **contract mirror
  byte-identical**: `docs/RG55-INTERFACE-CONTRACT.md` in this worktree is
  CONFIRMED STALE as of C5's close (missing the entire §8 amendment —
  verified via `git show main:run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
  diffed against the in-worktree copy) — C9 must sync it, there is
  currently no test enforcing this so it is easy to forget; `INDEX.md`
  regenerated the way the project does it.

## Backlog rows updated this session (C5, opportunistic per BRIEF-3's own suggestion)

BRIEF-3 flagged that CP-6 and CP-7's backlog rows were still `status:
open` despite being FIXED by session 2, and suggested a future session
update them "if spare budget before C9 proper." **NOT done this
session** — session 3 did not open `nyxloom-trove/backlog/` at all (C5
had no backlog row of its own to file, being a design-doc deliverable
rather than a CP-numbered defect fix, and the budget went to C5's own
implementation/tests/mutation evidence instead). CP-4/CP-5/CP-6/CP-7 rows
are STILL `status: open` as of this session's close, despite being long
since fixed (`376bb9cb`/`16b01c1c`/`907ddd50`/`e053276b`) — flagging this
explicitly so BRIEF-4's own claim above ("CP-4/5/6/7 already FIXED... do
not redo them" — meaning the CODE fix, not the backlog bookkeeping) is
not misread as "the backlog rows are updated." A session-4 successor with
spare budget before C9 could still knock these out early, or leave them
for C9's own backlog-FIXED pass — either is fine, C9 is the one place
this is guaranteed to happen.

## Orientation already banked (do NOT re-read from scratch)

Everything BRIEF-3 already banked (sessions 1-2's findings: RW-30/31/34/
35, contract §8 in full, design doc D-27/D-29/D-30/D-25 facts, C1-C4's
own structural findings). PLUS, from session 3 (C5): `ciu.compose.yml.j2`
read in full — the daemon service's OTHER fields (`command:` list,
`privileged`/`pid`/`cgroup`/`network_mode`, `mem_limit`/`memswap_limit`,
the `cgprofile-sessions` named volume) are UNCHANGED by C5 and already
understood; C6 needs to ADD a second bind-mount/volume entry for
`/run/cgprofile`, not modify what is already there. `lib/serve.py`'s
`_host_snapshot`/`_slice_snapshot`/`_pressure_snapshot` fully understood
(C5 added `_gates_slice_snapshot`/`_daemon_slice_snapshot` right after
`_host_snapshot`, same file, same class). `lib/targets.py`'s
`slice_to_path`/`list_children` fully understood, including the
hyphen-hierarchy gotcha. `lib/util.py`'s `read_int` (treats `max`/absent
as `None`) and `read_pressure` fully understood — C6's per-verb JSON
goldens will likely reuse `read_int`'s convention wherever a response
carries a nullable byte count. `cgprofile.py`'s `cmd_serve`/`_ctl_request`/
`_ctl_roundtrip`/`cmd_ctl` all read this session (needed for the
`--gates-slice` CLI plumbing) — `_ctl_request`/`_ctl_roundtrip` are
exactly the functions C6's socket-carrier client work will extend or
parallel, already familiar. `tests/conftest.py`'s `write_cgroup`/
`cgroup_files` fully understood and reused — C6's own tests should reuse
these too rather than inventing new fixture-tree helpers.

Still NOT read, needed before C6: the actual socket-accept/listener code
in `lib/serve.py` (`_bind`/`_accept_loop`/`_dispatch`-from-socket — C5
never touched the socket machinery itself, only `_host_snapshot`'s
CONTENT); `docs/ATTACH-GUIDE.md` beyond the one grep hit from session 1;
`README.md` "Running the daemon"; backlog rows CP-2 (already read in
full by session 2, quoted in BRIEF-3 — a session-4 successor does NOT
need to re-fetch it, BRIEF-3's own quote still stands); whether
`fixtures/rg55/socket/` needs creating (it does not exist yet — C5 only
created `fixtures/rg55/host-v1.1.json`, no `socket/` subdirectory).

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
cat /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
docker ps -a --no-trunc --format '{{.Names}}\t{{.Status}}' | grep run-gate-vbpub
```
Re-check HOST LOAD first. As of session 3's close: the P1 mutation-run
container (`run-gate-vbpub-r2-2315801-1789214565`) had ALREADY EXITED
(`Exited (1)`, confirmed via `docker ps -a`) partway through session 3 —
but `assay-r2` (pid 2415767, a DIFFERENT package's r2 lane, confirmed via
`pgrep -af` to be `python3 ./run-gate.py --base main assay-r2` at a
different worktree path, not P1's or P2's own) was STILL ALIVE at
session 3's close. A session 4 must recheck BOTH independently — do not
assume the container staying exited means both are gone, and do not
assume `assay-r2`'s specific pid is still 2415767 (it may have finished
and a different mutation run may have started under a different name by
the time session 4 begins) — re-run the full HOST LOAD check fresh rather
than trusting this paragraph's specific numbers. If BOTH are gone, the
r0/r1/r3 lanes (and eventually r2) become available for THIS package's
own gates, which have not been run at all yet across any of the three
sessions. If either is still alive, stay in targeted-pytest-only mode and
start C6 with `cat ciu.compose.yml.j2` (already read in full this
session, but re-open to edit) plus `grep -n "_bind\|_accept_loop\|def _dispatch"
lib/serve.py` to find the socket-listener code C6 extends.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the REPORT's C1-C5 evidence tables (tip hashes,
test-pass counts, each session's own structural findings — the CP-5
red-first bug-catch narrative, C3's git-checkout mistake, C4's DAMON
findings, C5's THREE-call-sites-not-one finding and the hyphen-hierarchy
gotcha); the design-doc key facts (D-27/D-29/D-30/D-25/D-20) as already
summarized across BRIEF-3 and this brief — don't re-read the whole design
doc again; the exact HOST LOAD recheck commands above; the `cp`/`md5sum`
backup-restore mutation-testing discipline (NOT `git checkout` on an
uncommitted worktree, per the C3 mistake, now proven out cleanly across
C4 and C5 too).
DROP: the C1-C5 implementation blow-by-blow in the LOG (already shipped
and committed — read only if debugging a regression in one of those five
specifically); every session's own tool-call-by-tool-call narration.

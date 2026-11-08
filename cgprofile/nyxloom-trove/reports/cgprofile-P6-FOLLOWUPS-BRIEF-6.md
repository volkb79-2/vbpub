# cgprofile-P6-FOLLOWUPS — BRIEF for session 6 (successor)

Session 5 (fresh Opus, checkpoint clause HARD) shipped **C7 — CP-8, the
watch role** and cut at the post-C7-commit boundary WITHOUT starting C8,
per the dispatch's own "cut BEFORE starting C8 rather than leaving it
half-done". This is the self-authored continuation brief + retention
prompt.

## State

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another worktree.
- Tip: this session's checkpoint-docs commit, on top of **`4fa725dc`**
  (C7). History: `376bb9cb` (C1) → `16b01c1c` (C2) → `614dcd9f` →
  `907ddd50` (C3) → `e053276b` (C4) → `36859c77` → `39d43934` (C5) →
  `c60644ac` → `bb575fd4` (C6) → `b865556b` → `4fa725dc` (C7) → this
  commit.
- Base is still assumed to be P1's tip (`16f3a29f` at dispatch, RW-35),
  NOT re-verified in any session. Check the controller log's dispatch
  table before assuming P1 merged; do not merge speculatively.
- **The `r0-r1` lane has now been run** (twice — see "Gates" below); no
  other lane has (`r2`, `r3` still unexecuted, and no live probe of any
  kind has been done in five sessions).

## What C7 shipped (do not redo, do not re-litigate)

`4fa725dc`. New `lib/liveness.py` (policy parser, bounded progress-stream
reader, the §8.4 state machine as a pure object), `tests/test_liveness.py`,
`tests/test_serve_watch.py`; `lib/serve.py` gained the policy parse, the
per-session tracker, `_observe_liveness`, the kill path, `liveness`/`watch`
in `status`/`stop`/the Summary, `_validate_wire`, and the whole streaming
path (`_watch_connection`/`_watch_prepare`/`_stream_watch`, the
`watch_wait` seam); `cgprofile.py` gained `ctl start`'s four policy options,
`ctl watch` and `_ctl_stream`; `docs/PROTOCOL.md` documents the streaming
exception and every new `args` name; goldens `status-v1.1.json`,
`summary-v1.1.json`, `watch-{reading,verdict,end}.json`,
`socket/watch-response.json`, plus three REGENERATED socket goldens.

**Ten rulings are settled in the LOG's session-5 "Decision asks" — read
them, do not re-open them.** The two that shape C8 most: (3) state
precedence is `over_ceiling > hung > throttled > stalled/runaway > ok`, and
(4) what a kill may target without a token.

## What remains (handoff order, C8 onward)

- **C8 — CP-9 placement (D-20, D-25, §8.3).** File the CP-9 backlog row
  FIRST (same shape as CP-8's; `nyxloom backlog index` regenerates
  INDEX.md). Unchanged from BRIEF-4/5, plus what C7 left ready for it:
  - `_enforce_stall_kill` in `lib/serve.py` carries the **marked C8 seam**:
    a placed session is killed by one write of `"1"` to `<gates slice>/
    rg-<token>/cgroup.kill` (atomic — it cannot miss a pid that forked
    during the walk). The pid loop stays as the fallback for unplaced
    sessions; it is never dead code.
  - `lib.liveness.LivenessSample` already carries `leaf_psi_full_avg10` and
    `leaf_memory_high_applied`, and the `throttled` state is implemented
    and unit-tested. **C8's only remaining work for `throttled` is feeding
    those two readings** from the leaf in `_observe_liveness`.
  - Every §8.2 `reading` line already carries `"placement": null` and
    `_watch_lines` is where it is built — C8 fills a value into a key
    consumers already parse, it does not change a shape.
  - `_writable_roots()`/`_guard_path()` in `lib/serve.py` is still the
    whitelist extension point; RW-35(a)'s ONE non-leaf write (`+memory
    +cpu +pids` into the gates slice's `cgroup.subtree_control`, `+` only)
    and a REFUSAL test are the load-bearing parts.
  - `docs/PROTOCOL.md` §3 already publishes the option names (`place`,
    `memory_high`, `memory_max`, `cpu_weight`) and the table's last
    paragraph says they land with C8 — update that sentence when they do.
- **C9 — close-out.** As BRIEF-5, minus what C7 did: `docs/PROTOCOL.md`
  needs only its C8 half and the removal of the remaining "lands with C8"
  hedge; README's daemon section still needs watch + placement;
  ATTACH-GUIDE + DESIGN.md still untouched; `CHANGES.md [Unreleased]`;
  backlog rows CP-2, CP-4..CP-9 → FIXED with hashes (**still all
  `status: open`**); `CGPROFILE_VERSION` still `"1.0.0"` → `1.1.0`; the
  in-worktree mirror `docs/RG55-INTERFACE-CONTRACT.md` is **still stale**
  (no §8) and nothing tests it.
- **Live probes** — C6's 7-step plan (REPORT) and C7's own watch probe
  (`sleep` subtree, `--idle-bound 20 --on-stall kill`, expect
  `stalled`/`killed` in ≤ 60 s) both still pending: they need an image
  build, still forbidden while an estate mutation run is live (RW-39
  relaxes only bare-host pytest lanes).

## Gates (what session 5 actually ran)

- `./run-gate.py r0-r1` — **first run RED**, and not because of C7: the
  lane's coverage gate is `fail-under=100` on the WHOLE project and the
  tree carried two pre-existing gaps (`lib/analyze.py:703`, C4's
  `_damon_series` `n == 0` early return; `lib/serve.py 872->878`, C2's
  `limit_drift` baseline guard). 1260 tests passed; only coverage failed.
  Session 5 covered both with two named tests (see the LOG) and re-ran.
- **`--base` is meaningless for this lane**: `run-gate.py --base X r0-r1`
  is REFUSED ("a command lane whose argv carries no `{base}` token"). Run
  it with no `--base` at all.
- RW-39 was honoured: memory `full avg10` checked before each launch
  (0.85, then 0.00), one lane at a time, `nice -n 19 ionice -c 3`, no
  image build, no r2, singleton container untouched.

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
head -2 /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
docker ps --no-trunc --format '{{.Names}}\t{{.Status}}' | grep run-gate-vbpub
```
Then file CP-9 and start C8 from `git show
main:run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §8.3
verbatim + `grep -n "_writable_roots\|_guard_path\|C8 SEAM" lib/serve.py`.
If both mutation runs are gone: the live probes and the r3 canary become
available, and r2 is LAST and once.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the REPORT's C7 section (the CP-8 oracle table,
what is actually tested, the golden table, the mutation table) and its
C1–C6 evidence tables; the LOG's session-5 "Decision asks" (ten settled
rulings — the two-clock state machine, the state precedence, the kill
targets, the Summary-injection point) and its four structural findings;
`lib/liveness.py`'s module docstring (the two clocks, and why `runaway`
needs a progress stream); the HOST LOAD / RW-39 rules and the `--base`
gotcha above; the `cp`/`md5sum` mutation discipline (never `git checkout`
on uncommitted work).
DROP: C1–C6's implementation blow-by-blow; C7's own tool-call-by-tool-call
narration; the contract and design text, all re-fetchable from `main` in
one command each.

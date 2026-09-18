# cgprofile-P6-FOLLOWUPS — BRIEF for session 5 (successor)

Session 4 (fresh Opus, checkpoint clause HARD) shipped C6 — the CP-2
socket carrier — and cut at the post-C6-commit boundary WITHOUT starting
C7, per the dispatch's own "if you cannot finish C7, cut BEFORE starting
it rather than leaving it half-done". This is the self-authored
continuation brief + retention prompt.

## State

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another worktree.
- Tip: this session's checkpoint-docs commit, on top of **`bb575fd4`**
  (C6, the socket carrier). History: `376bb9cb` (C1 CP-4) → `16b01c1c`
  (C2 CP-5) → `614dcd9f` → `907ddd50` (C3 CP-7) → `e053276b` (C4 CP-6) →
  `36859c77` → `39d43934` (C5) → `c60644ac` → `bb575fd4` (C6) → this
  commit.
- Base is still assumed to be P1's tip (`16f3a29f` at dispatch, RW-35) —
  NOT re-verified in session 4 either. Check the controller log's dispatch
  table fresh before assuming P1 has merged; do not merge speculatively.
- **No run-gate lane has been run for this package in ANY of the four
  sessions.** r0/r1/r3/r2 are all still unexecuted. Both estate mutation
  runs (`run-gate-vbpub-r2-680904-…` in a container, `assay-r2` pid
  2415767 bare-host) were alive through all of session 4. First thing:
  recheck BOTH independently; when both are gone, the lanes (and the C6
  live probe, plan in the REPORT) become available.

## What C6 actually shipped (do not redo, do not re-litigate)

`bb575fd4`. The three rulings it settled are in the LOG's "Decision asks"
— they are decided, not open: (a) the dispatcher migrated to the §8.1
wire shape, handlers take `args`; (b) an unknown TOP-LEVEL request key is
a hard `bad-argument` (this is what makes "one shape" checkable, and what
refuses v1's flat request); (c) unreadable peer credentials fail CLOSED
under an allowlist, open without one; a malformed `CGPROFILE_ALLOW_UIDS`
refuses to start the daemon.

Concretely in the tree now: `lib/serve.py`'s `_dispatch` wire validation,
`_assert_socket_permissions`, `_peer_uid`/`_peer_allowed`/`_log`,
module-level `parse_allow_uids`/`_WIRE_KEYS`/`SOCKET_DIR_MODE`/
`SOCKET_MODE`/`ALLOW_UIDS_ENV`, `handle_version`'s §8.6 `transports`;
`cgprofile.py`'s `_ctl_wire`/`_ctl_request`/`CONTRACT_VERSION` and
`cmd_serve`'s env parse; the compose bind-mount + `CGPROFILE_ALLOW_UIDS`
env; `ciu.defaults.toml.j2`'s `socket_dir`/`allow_uids`;
`docs/PROTOCOL.md` (new); README's two-carrier section;
`tests/fixtures/rg55/socket/` (goldens + README);
`tests/test_serve_socket_carrier.py` (35 tests). 323 tests green across
the three touched test files, 100% line+branch on changed lines.

**The one thing C6 left for you inside its own scope:** the live probe
(REPORT has the exact 7-step plan) and the r0/r1/r3/r2 lanes.

## What remains (handoff order, C7 onward)

- **C7 — CP-8 watch role (D-27, §8.2, §8.4).** File the CP-8 backlog row
  FIRST (same shape as CP-4/CP-5's rows). Re-read §8.2/§8.4 VERBATIM from
  `git show main:run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
  — the state machine's wording is load-bearing (ok / stalled / hung /
  runaway / throttled / over_ceiling; the PSI-paused idle clock;
  `--on-stall kill` is SIGKILL-to-the-token-subtree ONLY until C8's
  placement exists, so the `cgroup.kill` branch is an explicit TODO, never
  a silent no-op). Groundwork C6 already laid for you:
  - `docs/PROTOCOL.md`'s §3 table ALREADY documents `watch`'s `args`
    (`session`, `watch_interval`) and start's policy option names
    (`progress_stream`, `idle_bound`, `ceiling`, `on_stall`) — implement
    to those names, they are published.
  - `tests/fixtures/rg55/socket/watch-request.json` is ALREADY frozen, and
    `test_watch_is_not_implemented_yet` pins that the daemon answers
    `bad-argument` for it today. When C7 lands, that test must be replaced
    (not deleted quietly) by the real response/stream goldens
    (`watch-*.json`, `status-v1.1.json`, `summary-v1.1.json`), and the
    fixtures README's paragraph about "no response golden" updated.
  - New verbs must be added to `VERBS` in
    `tests/test_serve_socket_carrier.py` — `test_every_verb_is_covered_by_
    a_golden` fails otherwise, by design.
  - Streaming needs `_handle_connection` to write MORE than one line; it
    currently sends exactly one response and closes, and the 25 s
    connection timeout is for non-streaming verbs only (§8.1 rule 4).
    That is the single real structural change C7 needs in the socket code.
- **C8 — CP-9 placement (D-20, D-25, §8.3).** File CP-9 first. Unchanged
  from BRIEF-4: the ONE non-leaf whitelist write (`+memory +cpu +pids`
  into the gates slice's `cgroup.subtree_control`, `+` only, RW-35(a));
  `_writable_roots()`/`_guard_path()` in `lib/serve.py` is the extension
  point; a refusal test proves the whitelist, not just the happy path.
  `docs/PROTOCOL.md` already publishes the option names (`place`,
  `memory_high`, `memory_max`, `cpu_weight`).
- **C9 — close-out.** Unchanged from BRIEF-4, minus what C6 did:
  `docs/PROTOCOL.md` EXISTS now (C9 finishes its C7/C8 sections and drops
  the "lands with C7/C8" hedges); README's daemon section has the socket
  half already (C9 adds watch/placement); ATTACH-GUIDE + DESIGN.md still
  untouched; `CHANGES.md [Unreleased]`; backlog rows CP-2 (now fixed in
  code by `bb575fd4`), CP-4..CP-9 → FIXED with hashes — **none of these
  rows has been updated yet, all still `status: open`**; `CGPROFILE_VERSION`
  still `"1.0.0"` → `1.1.0`; the in-worktree contract mirror
  `docs/RG55-INTERFACE-CONTRACT.md` is STILL STALE (no §8) and nothing
  tests it; `INDEX.md` regenerated the project's own way.

## Orientation already banked (do NOT re-read from scratch)

Everything BRIEF-3/BRIEF-4 banked, PLUS from session 4: contract §1/§2/§8
in full; design A2/D-30; `lib/serve.py`'s socket machinery end to end
(`_bind`, `_assert_socket_permissions`, `_handle_connection`,
`_accept_loop`, `_dispatch`, `serve_forever`) and `handle_*`'s new
signatures; `cgprofile.py`'s `_ctl_wire`/`_ctl_request`/`_ctl_roundtrip`/
`cmd_ctl`/`cmd_serve`; `ciu.compose.yml.j2` + `ciu.defaults.toml.j2` in
full; `handle_report`'s subprocess contract; `_session_loop`'s
sampler-sleep seam. Three reusable test patterns now exist and should be
reused rather than re-invented: `_wire()`/`_wire_bytes()` in
`tests/test_serve.py`, and in `tests/test_serve_socket_carrier.py` the
`_Scenario` harness (one serve loop, both carriers, deterministic clocks)
plus `_TickBarrier` (exactly one sample per session, no wall-clock sleep)
— C7's own end-to-end tests want both.

Still NOT read, needed for C7: `lib/sampler.py`'s loop internals beyond
the sleep seam; `lib/subtree.py`'s token resolver (C7 kills by it);
`docs/ATTACH-GUIDE.md`; the CP-8 backlog row (it does not exist yet — you
file it).

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
cat /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
docker ps -a --no-trunc --format '{{.Names}}\t{{.Status}}' | grep run-gate-vbpub
```
If BOTH are gone: run the r0/r1 lane FIRST (this package has never had one
run) before writing any C7 code — a red gate on four sessions of
accumulated work is better found now than after C7. Then the C6 live probe
(REPORT's 7 steps). If either is alive: stay in targeted-pytest-only mode,
file CP-8, and start C7 with `git show
main:run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §8.2/§8.4
verbatim + `grep -n "_handle_connection\|conn.sendall" lib/serve.py` for
the streaming seam.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the REPORT's C6 section (what is tested, the
mutation table, the deferred live-probe plan) and its C1-C5 evidence
tables; the LOG's session-4 "Decision asks" (five settled rulings — do not
re-open) and its four structural findings (the `replace_all`-inside-a-
string-literal gotcha, the shared-`os`-module monkeypatch trap, the
per-thread sampler clock, the one-loop parity ordering); the design-doc
facts (D-27/D-29/D-30/D-25/D-20) as already summarized across BRIEF-3/4/5;
the HOST LOAD recheck commands above; the `cp`/`md5sum` mutation
discipline (never `git checkout` on uncommitted work).
DROP: C1-C5's implementation blow-by-blow in the LOG; C6's own
tool-call-by-tool-call narration; the contract text you can re-fetch from
`main` in one command.

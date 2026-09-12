# run-gate-WAVE-RG55-P2 — BRIEF-5 (successor continuation)

C3 is now FULLY DONE (config+client+accumulator+sampler from `4d684920`,
the wiring from `d8003d36`, a real live-probe-found fix from `38089fe6`).
This brief hands you C4 through C8 — everything else in the package.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there
(the double-checkout hazard BRIEF-1..4 all named still applies — verify
with `git -C /workspaces/vbpub status --porcelain -- run-gate-project/`
after every commit; it must stay empty).

Git log at hand-off:
```
38089fe6 fix(rg55-p2): basic-path final sample must not record a total docker-exec failure as data
d8003d36 feat(rg55-p2): C3 wiring -- token, daemon/basic orchestration, ephemeral+exec flows, re-attach/promote profiling rules
40c1aa65 docs(rg55-p2): checkpoint after C3 (partial) -- LOG, REPORT, BRIEF-4
4d684920 feat(rg55-p2): C3 (partial) -- profiling config + client + accumulator + sampler
```

**Gate state at hand-off (all against `38089fe6`):**
- `nice -n 19 ionice -c 3 python3 -m pytest tests/ -q`: **940 passed, 3
  skipped**, ~108s.
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty`:
  diff-coverage **OK 529/529 (100.0%) lines, 200/200 (100.0%) branches**,
  exit 0.
- `./run-gate.py --base main assay-r1`: **PASS**.
- `./run-gate.py assay-r3`: **PASS** (`canary: 1 rejected, 0 survived`).
- `assay-r2`: deliberately NOT run yet — run it ONCE, at the very end of
  the WHOLE package, by whichever session makes the final commit (handoff
  §4's own timing rule). If that is you: run it last, under `nice`, record
  the verdict + survivors, kill survivors with tests or justify each.
- Live acceptance (handoff §4.1/§4.2): BOTH run and satisfying their
  numeric criteria — see REPORT's "Live acceptance" section for the full
  transcript and headline numbers. §4.3 (`footprint --write`) deferred to
  you — the verb does not exist yet; do it as part of C5.

## What NOT to re-read

Everything BRIEF-1..4 already marked read/skipped stays that way: the
interface contract's own text (already fully absorbed into the shipped
code and its docstrings), the golden fixtures, `scripts/cgroup-profiler`'s
parsers. Also now settled, do not re-derive or re-litigate:

- The config layer, `ProfilerClient`, `ResourceAccumulator`, `BasicSampler`
  — all proven, all wired, all live-probed. If something about them looks
  wrong, that is a real bug to fix in place (see the final-sample story
  below for what that looks like when it happens), not a reason to
  re-design.
- The wiring shape itself (`start_lane_profiling`/`tick_lane_profiling`/
  `finish_lane_profiling`, the `print_profile_*` disclosure helpers,
  `await_container`'s RW-12 tick loop, `run_exec_lane`'s Popen rewrite,
  the re-attach/collect/promote rules) — all four lane runners are done.
  C4-C8 CONSUME this wiring (the record's `resources`/`profile_error`/
  `profile_ref` fields it already writes); none of them need to touch it.
- RW-11 (12-file basic-path list) and RW-12 (tick shape) — both APPLIED,
  not open questions.

## One thing to know before touching history/footprint code

**The footprint disclosure line (contract §4.6, line 3) was deliberately
NOT printed by C3** — it needs `series_stats` (C4) to compute "history
median peak {n} MiB ({k} runs)". Your C4 work makes the data exist; your
C5 work is where you actually go add that print statement (find the
`print_profile_*` helpers around run-gate.py:1275-1450 — `grep -n "def
print_profile"` — and add a `print_footprint_line` alongside them, then
call it from the same three call sites `print_profile_session_line`
already has: `run_container_lane`'s daemon-mode branch, `run_exec_lane`'s
finally, and — per contract — nowhere else; bare-host and disabled lanes
never print it either).

## Remaining work (handoff §2, verbatim scope; current anchors only)

Re-read the handoff's own C4-C8 bullets in full (`run-gate-project/
nyxloom-trove/reports/run-gate-WAVE-RG55-P2-HANDOFF.md` §2) — they are
detailed and unchanged; this section gives you WHERE, not WHAT again.

**C4 — history schema 2 (R-36).** `HISTORY_SCHEMA = 1` is at run-gate.py:87
(bump to 2, with the note in the SAME style `__revision__`'s own giant
changelog comment uses one level up — see line 15's comment for the
pattern, though yours need not be nearly that long). `_apply_record`
(2108) is where a record's fields become a stored entry — `resources`/
`profile_error`/`profile_ref` are ALREADY being written into every
`run_record` by C3's wiring (verify: `grep -n 'run_record\["resources"\]'
run-gate.py` — 5+ call sites), so schema 2's entry SHAPE already has real
data flowing into it; C4's job is `series_stats(entries, getter)`
(generalizing `duration_stats` at 2657 — read it, it is short and the
exact pattern to copy: median never mean, `count` alongside, absent
values excluded from the series not treated as zero), the five new stat
keys (`memory_peak_bytes`, `memory_peak_over_baseline_bytes`,
`hot_set_p90_bytes`, `cpu_cores_avg`, `memory_full_stall_seconds` — pull
each from `entry["resources"]["memory"/"cpu"/"host"]` with the exact
field names in `RG55-INTERFACE-CONTRACT.md` §3, `None` when `resources`
itself is `None` on that entry), and `cmd_history`'s table (2733) +
`--json` gaining the PEAK/+BASE/HOT p90/CORES/STALL columns (`_fmt_stats`
2692, `_fmt_seconds` 2688 — read both, they are the exact formatting
pattern for a nullable numeric stat column). Schema-1→2 migration: a
schema-1 store loads fine today (`load_history_store` already
`setdefault("schema", HISTORY_SCHEMA)`), so the real migration work is
making sure an OLD entry without `resources`/`profile_error`/`profile_ref`
keys reads back as `resources: None` (a plain `.get("resources")`
wherever `series_stats`/`cmd_history` reads an entry, never a bare
subscript) and gets those three keys added on the NEXT write (the write
path is `_apply_record`, which already just does `dict(record)` minus
underscore keys — a schema-1 entry re-read and re-written through a NEW
`run_record` will naturally gain them; a schema-1 entry that is NEVER
re-run stays schema-1-shaped forever in the JSON, which is fine — `schema`
is a STORE-level field, not a per-entry one, and the contract's own text
says exactly this ("schema-1 stores are read as-is... written back as
schema 2 on the next write"). Test: a schema-1 fixture store (hand-write
one, or use an existing schema-1 test fixture as a base) → one run → the
STORE's own `schema` key flips to 2, the OLD entries are untouched byte-
for-byte, the NEW entry has the full shape. RG-27 traps (median resists a
10× outlier; a dirty run never touches a commit's history entry) — already
proven for `duration_stats`; re-prove for AT LEAST ONE of the five new
series (the pattern is identical, one test suffices to prove the
GENERALIZATION works, not five redundant ones).

**C5 — `footprint` verb (R-44).** New CLI verb, `usage()` is at 6137 (add
the verb's help text there, matching `history`'s own entry just above it
in that function). `run-gate footprint [LANE] [--json] [--write]
[--worktree PATH]`: reads the (now schema-2) store, no lock (matches
`cmd_history`'s own no-lock read), distills PASS + history-eligible
entries into the manifest object (contract §4.5's exact JSON shape —
quoted in full in the contract file, copy it precisely: `schema`,
`generated_by`, `revision`, `distilled_at`, `from_commit`, `keep`,
`lanes.<lane>.{runs, completed_runs, scope, method, duration_s,
memory_peak_bytes, memory_peak_over_baseline_bytes, hot_set_bytes,
cpu_cores, memory_full_stall_s, last_commit, last_at}`). `--write` writes
`run-gate.footprint.json` next to the EFFECTIVE project's `run-gate.toml`
(temp file + `os.replace`, sorted keys, `indent=2`, trailing newline —
`_write_json_atomic` at run-gate.py's history-store section is the exact
pattern to reuse, it already does all of this); refuses when the store has
no eligible profiled run at all (name why — "no lane has a completed,
profiled run in its history yet"). `footprint` joins `_RESERVED_POINTER_VERBS`
(grep that name — it is a set literal near the top-level constants,
`history`/`doctor`/`validate-pointers` are already in it) — flag the
change as a load-time BREAKING note in CHANGES (C8). Add the footprint
disclosure print (see "one thing to know" above) as part of THIS
deliverable, not C3's — it needs the manifest this verb produces. `doctor`
gets the divergence/staleness warnings described in the handoff — that
touches `cmd_doctor`, a separate function from the verb itself; find it
via `grep -n "def cmd_doctor"`. The run path (`run_container_lane`/
`run_exec_lane`) reads the manifest, when present, to fill `meta.expected`
in the daemon `start()` call (`profile_meta()`'s own `"expected": None`
line — change it to read the manifest when one exists) and the `|
manifest {n} MiB` tail of the footprint line.

**C6 — RG-48 (`resources.cpus`).** The `resources` table already exists
and is validated (`_validate_lane` at 276, the `resources` block a few
lines below its own `_check_keys` call — `{"memory", "memory_swap",
"cpu_weight", "io_weight", "shared"}` is the current allowed-key set for
`[lanes.<n>.resources]`; add `"cpus"` there, plus the SAME key on
`[environments.<e>].resources` in `_validate_environment`, which does NOT
currently accept a `resources` sub-table at all — check `_validate_environment`'s
own `_check_keys` call, currently `{"image", "cgroup_slice", "mode",
"container_name", "forward_env"}`). Validate `^\d+(\.\d+)?$`, `> 0`
(docker's own `--cpus` grammar). Ephemeral lanes: `run_container_lane`'s
argv assembly already has a `mem_cap`/`--memory` pattern right next to
where `--cpus` belongs — mirror it, lane value wins over environment
value. Exec lanes: naming-only WARNING (mirror the EXISTING `cgroup_slice`
naming-only warning in `run_exec_lane`, word-for-word pattern). `doctor`
warns when a container lane's argv contains `-n auto`/`--workers auto`
and neither lane nor environment declares `cpus` — a new check inside
`cmd_doctor`, scanning `lane.get("argv", [])` text. Backlog RG-48 → FIXED.

**C7 — `doctor` profiler check.** New check inside `cmd_doctor`: daemon
container present/running (`docker ps`/`container_state` on
`PROFILE_DAEMON_DEFAULT` or the resolved `[profile].daemon`), `ctl
version` (use `ProfilerClient` — it already exists, just call it from
`cmd_doctor`), effective `[profile]` config (call `resolve_profile_settings`
per-lane or once centrally, whichever `doctor`'s existing per-check loop
shape makes natural), `ctl host` pressure when the daemon answered
`version`. The EXISTING R-29 slice-read-from-private-namespace check
(search `cmd_doctor` for its current WARN text) gets a WHY appended when
it fails and `/proc/self/cgroup` reads `0::/` (cgroupns=private) — read
that file directly, do not guess from the WARN alone.

**C8 — docs/spec/backlog/revision.** SPEC.md gets `R-43` (a–h
sub-clauses — token, scopes, daemon path, basic path, degradation,
inflight fields, disclosure lines, config; write these from the ALREADY-
SHIPPED code, not from a fresh design — the wiring commits are the ground
truth now), `R-44` (footprint manifest), amendments to `R-29` (cpus) and
`R-36` (schema 2 + the five new stats), a backfilled rule id for RG-41's
log-stream liveness (e.g. `R-40f` — keep ids monotonic, note the backfill
in the Status block), the drift fixes named in the handoff (stall_timeout
text in R-08/R-40c, R-07 gains `mode`/`container_name`, the R-08 duplicate
paragraph removed), `Rev 10` in the Status block naming R-43/R-44 and the
amendments. README.md: lane schema (`profile`, `resources.cpus`), verbs
(`footprint`), "What each lane costs" gains the footprint story.
CONSUMERS.md: the daemon is host infrastructure started from the vbpub
checkout (`cd scripts/cgroup-profiler && ciu up`), `run-gate.footprint.json`
is TRACKED (commit it), `.run-gate/` stays ignored. LANE-AUTHORING.md: one
paragraph on footprint-informed budgets. CHANGES.md `[Unreleased]`: RG-55
(this whole wave), RG-53 BREAKING (from C1, already shipped — check it is
actually in there, C1 was two sessions ago), RG-48, `footprint`
reserved-name BREAKING, schema 2. Backlog: RG-55/RG-48 → FIXED with
measured evidence (this package's own REPORT is the evidence — cite
commit hashes and the live-probe numbers); RG-56/RG-57 stay untouched
(explicitly out of scope, per every prior brief). `__revision__ = 41`
(currently 40 at run-gate.py:15) with the note prepended in the EXACT
style every prior revision bump in that comment uses — read a couple of
the existing per-revision notes there first, they are long and specific
by design (this file's own culture, not incidental verbosity). `usage()`
(6137) gets the `footprint` verb + its flags documented.

## Test debt

Every C4-C7 behavior above needs its own tests, following this file's
established pattern (unit-level where a function is directly callable and
cheap, `run_gate.main()` in-process — NEVER `run_tool()`'s subprocess, see
"a real finding" below — for anything that must show up in diff-coverage).
`selftest`'s diff-coverage gate is 100% lines AND branches, no
`pragma: no cover`, same as every prior package in this wave.

**A real finding from this session, worth internalizing before you write
ANY new test:** `run_tool()` spawns run-gate.py as a SEPARATE subprocess,
and `coverage.py`'s instrumentation in the pytest process does NOT extend
into a spawned child interpreter without extra configuration this project
does not have. Three of this session's first-draft exec-lane tests used
`run_tool()`, passed, and were STILL invisible to `selftest`'s coverage
gate (0/22 of `run_exec_lane`'s own new lines counted) until converted to
`monkeypatch.setattr(sys, "argv", [...]); run_gate.main([...])`. If a new
test needs to prove something through the REAL CLI entrypoint parsing
(argv handling, `--help`, etc.) `run_tool()` is still right — but for
anything whose LINE COVERAGE matters to the diff-coverage gate, call
`run_gate.main()` in-process.

## Process notes (unchanged from BRIEF-1..4, restated)

- Edit tool only; `git -C <worktree> commit -F <msgfile> --only --
  <paths>`; both trailers — match whatever YOUR session's own current
  attribution instruction says (this session's was `Claude Sonnet 5` +
  the session URL in its own dispatch prompt — always use YOUR reminder,
  not a prior brief's copy).
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from
  `<worktree>/run-gate-project`, verdict read in a SEPARATE step from the
  captured log. Targeted `pytest tests/test_run_gate.py -k <cluster>`
  while iterating (whole suite ~108-118s).
- `assay-r1`/`assay-r3` need a CLEAN tree (commit first).
- HOST LOAD (handoff §6): 8 cores shared with a production game server;
  serial pytest only; at most 2 gate containers estate-wide (`docker ps
  --format '{{.Image}}'` for `tester-unified:local` first); `docker update
  --cpus=3` right after launching any container for live probes; teardown
  in `finally` (or by hand immediately after, if driving a probe outside
  run-gate's own process — this session's probes lived in
  `/tmp/.../scratchpad/rg55-probe`, a throwaway git repo symlinking
  `run-gate.py`; reuse that pattern, it works).
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate). C4 and C5 are naturally
  separable checkpoints (C5 depends on C4's schema but is otherwise
  independent of C6/C7/C8) — a sanctioned mid-package cut is "C4 done and
  gate-verified, before C5", mirroring this wave's own C1/C2/C3 cut
  points.

## Retention prompt (paste into your own `/compact` if you need to compact mid-session)

```
KEEP: C3 is FULLY DONE (config/client/accumulator/sampler + wiring + the
basic-path final-sample fix), commits 4d684920/d8003d36/38089fe6, all
gate-verified (selftest 940 passed 3 skipped, 100% line+branch diff-
coverage; assay-r1/r3 PASS; both live probes run and satisfy their numeric
criteria). The footprint disclosure line is deliberately UNPRINTED until
C5 (needs C4's series_stats first) -- do not add it during C4. The
run_tool()-subprocess coverage blind spot (use run_gate.main() in-process
for anything diff-coverage must see). Current line-number anchors for
HISTORY_SCHEMA (87), duration_stats (2657), _apply_record (2108),
cmd_history (2733), LANE_KEYS/_validate_lane (253/276), usage() (6137) --
re-verify with grep before editing regardless. Decision asks raised this
session and their resolution (test kill switch, container-id-resolution
reuse) -- both flagged for controller review, neither blocking. HOST LOAD
container-count state. Which of C4/C5/C6/C7/C8 you have finished, gate-
verified, and committed.
DROP: the full wiring source-code listings (re-readable in one call from
run-gate.py directly -- it IS the spec now); the final-sample root-cause
narrative in full (keep only "sample_final() exists, sample_once() is
untouched, commit 38089fe6" as the pointer); the live-probe transcripts
(keep only the headline numbers already in REPORT).
```

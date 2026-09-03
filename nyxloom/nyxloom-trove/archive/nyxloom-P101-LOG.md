# nyxloom-P101-retire-tier-band -- LOG

Chronological record. Implementer: fresh Sonnet 5 session.
Worktree: `/workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom` (branch
`nyxloom-p101-tier-band`).

## Orientation

- `git log --oneline -5`: HEAD was `ac61f7d9` ("carve(nyxloom): nyxloom-P101 --
  re-freeze input_revision at a32c4bb6 after repair round 2"), matching the
  dispatch instruction exactly. No STOP condition.
- Read the handoff (`nyxloom-trove/handoffs/nyxloom-P101-retire-tier-band.md`)
  in full, including the "Why this package exists" section explaining NL-7's
  corrected mechanism (a live one-way suppressor, not dead code), and
  `nyxloom-trove/reports/nyxloom-P101-CARVE-REVIEW.md` in full (3 rounds).
- Pre-edit `escalate_if` sweep: `git grep -n '_TIER_BAND' -- src/ tests/` ->
  exactly `src/nyxloom/adapters.py` at lines 181, 202, 219 (three hits, one
  file) -- matches the handoff's measurement. `git grep -n
  'compute_review_depth_directive' -- src/` -> exactly one production caller,
  `src/nyxloom/effects_review.py:434`. Neither trigger fired.
- Read `src/nyxloom/adapters.py` lines 176-249, `src/nyxloom/effects_review.py`
  lines 420-444, `tests/test_adapters.py` lines 2075-2450, and
  `tests/test_effects_review.py` lines 254-333 (the `TestWaveLeaseUnion`
  harness to mirror for O5, plus the `_routes()` helper).
- `docker ps` / `uptime` before starting: no nyxloom/gate container running
  from another package; host load elevated (~6-9 on the three averages, this
  host's own shared-machine baseline per the standing rule) from other
  sessions' concurrent work, not something to fix.

## `978f7a5c` -- fix(nyxloom): nyxloom-P101 -- retire _TIER_BAND, scope.touch size is the sole band signal

Work items 2, 3, 4, 5, 6, 9 (all committed together as one coherent edit
cluster; the controlled breaks of Work item 7 were run by hand against this
commit's state afterward and reverted -- see below, not committed).

- **Work item 2**: replaced `src/nyxloom/adapters.py` lines 176-188 (the
  comment block, `_TIER_BAND`, and the threshold comment) with the pinned
  verbatim text byte-for-byte. Verified via a Python string-containment probe
  before committing: `pinned in adapters.py source` -> count 1, anchored
  before `def compute_review_depth_directive` (see O4 test evidence in the
  REPORT).
- **Work item 3**: dropped the `tier` parameter from
  `compute_review_depth_directive`'s signature, rewrote the docstring's band
  paragraph to the pinned text, replaced the parenthetical `(i.e.
  implement-1/implement-2, or the fallback's small-scope case)` with `(a small
  scope_touch)`, and collapsed the two-step band computation (old lookup +
  fallback) to the unconditional scope-size ternary. Kept the `if band <
  _HIGH_BAND and not shallow: return ""` early return exactly as instructed
  (a provable pure fast path after this edit -- the handoff's own enumeration
  over 0/1/4/5/6/8/30 paths x rigorous/shallow/empty/None asserts finds zero
  differing cases; noting this here so a downstream reviewer does not flag it
  as dead code). `_LOW_BAND`, `_HIGH_BAND`, `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`,
  `_RIGOROUS_ASSERTS` all kept, all still referenced.
- **Work item 5**: `src/nyxloom/effects_review.py`'s sole production caller
  (`ReviewEffector.launch_review`, ~line 434) -- deleted the
  `tier=first_fm.tier if first_fm is not None else None,` line, kept
  `first_fm` (still read for `scope_touch=` and by `incapable_escalation_note`
  below) and both remaining kwargs unchanged.
- **Work item 4**: updated all 12 `tests/test_adapters.py` call sites (lines
  2087, 2112, 2141, 2148, 2167, 2173, 2178, 2188, 2234, 2266, 2291, 2441 in the
  pre-edit file) to drop `tier=`; the five that used `tier="implement-3"`
  purely to manufacture a non-empty directive now pass a 6-path `scope_touch`
  instead. Updated the four non-call docstring/name hits (2082, 2106,
  2133-2134, 2158-2163) per the handoff's per-line disposition table --
  renamed `test_compute_review_depth_directive_scope_size_fallback` to
  `test_compute_review_depth_directive_scope_size_band` and deleted its
  now-meaningless third "unparseable tier falls back the same way" assertion
  block (that concept is now the only path, not a distinct case). Added three
  new tests: `test_compute_review_depth_directive_signature_and_threshold_boundary`
  (O1: 6-path fires / 5-path empty / exact signature-tuple equality),
  `test_adapters_module_carries_no_tier_shaped_string_literal` (O3: AST scan of
  every string `Constant` in `adapters.py`), and
  `test_adapters_carries_the_pinned_nyxloom_p101_band_comment_exactly` (O4:
  verbatim-and-anchored pinned-comment containment). Added `import inspect`
  and `import re` to the top of the file (both previously absent; `ast` and
  `Path` were already imported).
- **Work item 6**: added
  `tests/test_effects_review.py::TestReviewDepthReachesTheRealCaller`,
  mirroring `TestWaveLeaseUnion`'s full-path harness (lines 254-314) --
  monkeypatches `effects_dispatch.frontmatter_for`, `wrapper.launch_detached`,
  `effects_review.adapters.build_dispatch` (a kwarg-capturing fake, not a
  fixed return), and `effects_review.config.Routes.load` (reusing the file's
  own `_routes()` helper), then drives the real `ReviewEffector.launch_review`
  and asserts both directions through the real caller: a 6-path
  `scope.touch` captures a `review_depth` containing `high-complexity`; a
  1-path `scope.touch` captures one that does not.
- **Work item 9**: appended the pinned one-line supersession note to
  `docs/plan-next-batches.md` immediately after the BATCH C block's final line
  (anchor `grep -c 'SELECTION by band'` -> 1, unique). Did not touch the
  historical `✅ DONE` record itself.

Verified locally before committing: `pytest tests/test_adapters.py
tests/test_effects_review.py -q` -> all pass (cockpit venv, not a ship
signal -- see the real gate run below).

## Work item 7 -- MUTATION-CHECKED controlled breaks (run by hand, NOT committed)

The gate's `asserts` for `tester-unified` are `["tests-pass",
"changed-line-coverage", "canary-verified"]` -- no `mutation` -- so this is the
only proof the new oracles are not hollow. Backed up the fixed
`src/nyxloom/adapters.py` and `src/nyxloom/effects_review.py` to the
scratchpad, applied each break via a Python string-replace against that
backup, ran the exact named test(s), captured the failure, then restored from
the backup and re-verified `diff -q` against the backup was empty before
moving to the next break. All 11 prescribed breaks were applied, all 11
failed their named oracle for the named reason. Full transcripts in the
REPORT; summary:

| break | applied to | ran | result |
|---|---|---|---|
| B1 | adapters.py | `test_compute_review_depth_directive_signature_and_threshold_boundary` | FAILED -- `TypeError: ... missing 1 required positional argument: 'tier'` (restored `_TIER_BAND` + required `tier` param) |
| B1' | adapters.py | same | FAILED -- exact signature-tuple equality catches the renamed+defaulted `handoff_tier` param even though the 6-path/5-path calls still succeed (suppressor fully intact) |
| B2 | adapters.py | same | FAILED -- 5-path case wrongly returns the high-complexity reason (`>` -> `>=`) |
| B3 | adapters.py | `test_adapters_module_carries_no_tier_shaped_string_literal` | FAILED -- `_BANDS` dict's three tier-shaped keys detected |
| B3' | adapters.py | same | FAILED -- `dict(zip(...))` form (evades an `ast.Dict`-keys check) still detected by the widened string-constant scan |
| B3'' | adapters.py | same | FAILED -- reverting only the docstring parenthetical is caught (the whole docstring is one `ast.Constant` node) |
| B4 | adapters.py | `test_adapters_carries_the_pinned_nyxloom_p101_band_comment_exactly` | FAILED -- `src.count(pinned) == 1` -> `0 == 1` |
| B4' | adapters.py | same | FAILED -- anchor check `index(pinned) < index("def ...")` -> `65188 < 9466` is False |
| B5 | adapters.py | same | FAILED -- absence check on the old false "mechanical/cheap vs. hard" sentence -> `1 == 0`, while the pinned-containment and anchor checks both still passed (isolating the absence half specifically) |
| B6' | effects_review.py | `TestReviewDepthReachesTheRealCaller` + `TestWaveLeaseUnion` (both) | 1 failed, 1 passed -- O5's small-scope direction wrongly fires `high-complexity` (hardcoded 6-element list); `TestWaveLeaseUnion` stays green, confirming B6' isolates O5 alone |
| B7' | adapters.py | `test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc` | FAILED -- `else _LOW_BAND` -> `else _HIGH_BAND` makes the neutral case wrongly fire |

After each break: restored from backup, `diff -q` against the backup empty.
Final full-suite re-run after all 11 breaks: `pytest tests/test_adapters.py
tests/test_effects_review.py -q` -> all pass, confirming clean restoration.

## `0b5eb9d4` -- docs(nyxloom): nyxloom-P101 -- close NL-7 with the corrected mechanism, regenerate INDEX.md

Work item 8. Order followed exactly as specified (prose edit, then
`set-status`, then `backlog index`):

1. Replaced NL-7's "## Observed mechanism and reproduction" section body with
   the pinned text. Verified byte-exact via a Python containment probe against
   the handoff's own pinned block before committing (first attempt had a
   line-wrap mismatch on "Batch C)." vs "Batch\nC)." -- caught and fixed before
   committing).
2. `python3 exec-nyxloom.py backlog set-status NL-7 fixed --reason
   "nyxloom-P101: retired _TIER_BAND; scope.touch size is now the sole band
   signal. Option 1 of the two proposed; option 2 (a routes.toml-backed band)
   needs a per-tier complexity fact neither routes file carries."` -> exit 0.
   Confirmed the file's frontmatter now reads `status: fixed`,
   `closed_date: "2026-09-03"`, and the exact `closed_reason` string.
3. `python3 exec-nyxloom.py backlog index` -> exit 0. `git diff` on
   `INDEX.md` shows exactly one row moved from the `open` group to the `fixed`
   group (NL-7), nothing else changed.
4. `python3 exec-nyxloom.py lint` (Work item 8(c)) -> `EXIT=1`, 794 output
   lines. Read in a SEPARATE step (never a piped tail): `grep -c "BLG"` on the
   captured output -> **0**. Matches the documented baseline exactly: `grep
   -oE 'L[0-9]+ (error|warning)' | sort | uniq -c` -> 164 L7 error, 92 L14
   error, 56 L1 error, 30 L10 warning, 23 L11 error, 10 L4 warning, 6 L7
   warning, 5 L12 error, 2 L10 error (342 errors, 444 warnings total, 0 BLG
   lines) -- byte-for-byte the same histogram the handoff's own "Baseline"
   paragraph records, confirming this package causes no new lint findings and
   the regenerated INDEX.md is genuinely fresh (BLG3-clean). Full evidence in
   the REPORT; not pasting the 794-line output per the handoff's own
   instruction.

Also confirmed `tests/test_core_characterization.py -q` -> 26 passed (the
ownership-inventory tolerance rows all hold; no `escalate_if` re-measurement
needed).

## Gate run

`docker ps`/`uptime` before starting: a DIFFERENT package's `tester-unified:
local` container (`boring_napier`, a `cmru-release`/assay qualification run,
confirmed genuinely active via `docker exec boring_napier ps aux` -- real
xdist pytest workers, not hung) was up. Per the standing one-gate-container
rule, waited rather than stacking a second one -- a background poll (`until !
docker ps --format '{{.Image}}' | grep -qi '^tester-unified'; do sleep 20;
done`), not a tight loop. It cleared naturally (~2 minutes); confirmed via a
fresh `docker ps` (host load down from ~8.6 to ~4.5-7) before proceeding.

Ran, from `/workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom`
(no `--worktree` flag, since already cwd'd inside the target tree -- passing
it here would double-append the path and break the docker mount):

```
python3 run-gate.py tester-unified
```

in the background. Located the new container within seconds
(`docker ps --filter ancestor=tester-unified:local`, `3cee628ece81`) and
immediately ran `docker update --cpus=3 3cee628ece81`, confirmed via `docker
inspect --format '{{.HostConfig.NanoCpus}}'` -> `3000000000`. Confirmed via
`docker exec ... ps aux` mid-run that it was genuinely executing `pytest
tests -n auto -q --cov=src/nyxloom` with active xdist workers, not hung.

The commit judged is `4241922e` -- the LOG+REPORT commit made just before the
gate was launched (the tree had to be clean; `run-gate.py` refuses a dirty
tree, which is exactly what happened on the first launch attempt before this
one -- the two untracked LOG/REPORT files were committed first, see the
commit list above).

**Verdict read as a SEPARATE step from running the gate** (the log file was
read via the file-read tool directly, not piped/tailed; the verdict JSON was
then read as an independent second source):

```
run-gate: admission: lane 'tester-unified' declares no resources.memory -- not memory-accounted (shared-infra rules still apply)
run-gate: rev 33 | lane tester-unified | env [environments.tester-unified] in central .../run-gate.toml | slice dev-background.slice
run-gate: budget 30m (advisory)
assay-4.0.0.pyz: OK
tester-unified: PASS (exit 0)
  commit: 4241922e41e6332d3637f272ab4a08a8230db020
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom/.assay/verdict-tester-unified.json
run-gate: lane 'tester-unified' exit 0
```

`.assay/verdict-tester-unified.json` (read independently, full content in the
REPORT): `"outcome": "PASS"`, `"exit_code": 0`, `"commit":
"4241922e41e6332d3637f272ab4a08a8230db020"` -- this package's HEAD at the time
of the run. `claims`: R0 (`tests-pass`) = PASS; R1 (`changed-line-coverage`) =
PASS at `pct: 100.0` (`considered: 1` file, `executable: 4`, `covered: 4`,
resolved against merge-base `5857045c` under `source_roots: ["src"]`, which
counts only `src/` statement lines, not test files -- the full `pytest tests
-n auto -q` run this verdict reflects nonetheless includes every new/updated
test this package added, all passing). The gate's own container was gone (no
longer listed in `docker ps -a`) immediately after the run finished --
`run-gate.py` tears its own container down.

## Conclusion: GREEN

All 10 Work items complete. O1-O5: proven both by isolated local test runs
(above) and by the real gate's full `pytest tests -n auto -q` run, which
includes every new/updated test in this package, passing inside the actual
container. O6: the real `tester-unified` gate PASS (exit 0, commit
`4241922e`) plus the separately-read BLG-findings-zero evidence (Work item
8(c)). No `escalate_if` trigger fired at any point. Not merged (controller's
step, per doctrine) -- this package's implementer role stops here.

This final LOG + REPORT update committed as `ba2e09c2`.

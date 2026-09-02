# Wave C (Go) — continuation brief 3

**Cumulative delta since BRIEF-2 only.** BRIEF-1 is the seam map and BRIEF-2 is
the first delta — read both, then this. Nothing here re-copies either.

Written by **generation 2** at its checkpoint. Generation 2 was a fresh
session seeded with BRIEF-1 + BRIEF-2 and nothing else; you are generation 3
on the same terms.

---

## 1. BRIEF-2 §5's task list, item by item

| BRIEF-2 §5 item | state |
|---|---|
| 1. `requires_statement_attribution` + the `statement_blocks` hook | **DONE**, signature recorded as **A-397** |
| 2. the A-392 refusal in both evaluate entry points | **DONE** |
| 3. the two members on python/javascript/sql + the FakeAdapter copies + the go registration test | **DONE** |
| 4. expose the ONE key join instead of duplicating it | **DONE** — `evaluate.resolve_coverage_keys` |
| 5. the runner wiring at `runner.py:969-1030` | **DONE** — `runner._attribute_statements_for_lane` |
| 6. `external_tools = ("go",)` | **DONE** |
| **7. register `GoAdapter` at `{"R1"}` (A-394)** | **NOT DONE — this is your first task** |
| **8. srdm covergate qualification (F008-A5)** | **NOT DONE — blocked behind 7** |

Also still untouched, unchanged from BRIEF-2's own tail: **item 3** (fixture
regeneration, F008-A4) and **item 5** (`go-cover` producer vocabulary, B047
item 3). Item 6 (`helpers[]` gate envelope) is now **PARTIAL** — see §4.

## 2. New load-bearing facts (do not re-derive)

**The oracle now has a Python half.** `src/assay/adapters/go_stmtpos.py`:
finds the shipped helper via `HELPER_DIR` (derived from the package's own
location — `importlib.resources` was rejected because `go run .` needs a real
directory as cwd), forces `GOPROXY=off GOWORK=off GOTOOLCHAIN=local
GOFLAGS=-mod=mod`, pins `OUTPUT_SCHEMA = 1` against the helper's own
`const outputSchema`, and refuses every partial shape. `_read_document` is
split out precisely so its checks are testable with a synthetic document and
no toolchain — that is not the thing A-334 forbids, because the subject of
those tests is assay's reader, not Go.

**Inputs are validated before the toolchain is probed**, and the order is
deliberate: a profile naming a file the tree lacks is a staleness finding
everywhere, and reporting it as `MISSING_EXTERNAL_TOOL` would fold it into an
environment fact.

**`identity` comes from the helper's own `runtime.Version()`**, formatted as
`go version <v>` — the toolchain that actually compiled and ran the program,
which is a stronger fact than a separate `go version` subprocess (that could
be a different binary). Do not "improve" this into a second subprocess.

**A-397's one narrow amendment to the path contract**, restated in
`adapters/base.py`'s module docstring: `statement_blocks` receives `repo_top`
as its own named parameter. Path STRINGS stay repo-relative; only the anchor
is new. Do not pass absolute path strings — that would put a second spelling
into the protocol, which is the invariant the original sentence protected.

**The key join is exposed, not copied.** `evaluate.resolve_coverage_keys`
(public) and `_repo_path_by_raw_key` (private, one loop, one collision
refusal); `_normalized_profile_files` now inverts the latter. The runner
borrows it. **Do not re-derive `normalize_coverage_key`/`project_prefix`
anywhere** — that is the drift A-385/A-367 rule against, and there is now a
test that would catch it (`test_the_oracle_receives_the_key_the_evaluator_
will_judge_not_the_raw_one`).

## 3. Thirteen pre-existing tests were repaired, and it is a tracked debt

A-392's guard correctly refused thirteen Go tests that judge committed
coverprofiles with **no toolchain**. Seven now route through
`conftest.as_pre_oracle_attributed` (flag set, line sets untouched); six
(`test_canary_go_pipeline.py`) through a named `_PreOracleGoAdapter` subclass
with `requires_statement_attribution = False`.

Filed as **B057**. Read the entry before touching either shortcut: the honest
fix for the first IS F008-A4, and the second needs a real decision (thread a
canned oracle through `canary.run_go_canary`, or split the file). B057's third
acceptance box asks for a test that goes RED if the shipped adapter's
declaration is ever flipped — that box is unticked and is cheap; consider it.

## 4. Item 6 is now partial, and its remaining half has a real obstacle

`HelperInvocation` is produced and delivered exactly once per lane through
`evaluate_r1`'s new `on_helper_invoked` callback (the same additive,
default-`None` channel `on_base_resolved`/`on_added_resolved` use). Nothing
yet carries it into `Verdict.helpers`.

**The obstacle, written as decision ask DA-3 in REPORT §12:** A-395 requires
the parallel "helpers IS present" test to be proven against a real toolchain,
never a mock — but the registered gate runs in `tester-unified:local`, which
has **no Go**. So that test cannot be a gate-run test. Either the evidence is
a recorded probe in `carve-assets/P27-recarve/` (generation 1's pattern), or
the gate lane needs a Go image. **Do not resolve this by mocking `go`** —
A-334 and A-395 both forbid exactly that.

## 5. Your task list, in order

1. **Register `GoAdapter` at `{"R1"}` in `cli._built_in_registry`** (A-394).
   The whole chain it was sequenced behind is now landed and gate-verified, so
   this is unblocked. Expect fallout in `cli.py`'s own docstring (it explains
   why each language is registered at which level), in any test asserting
   `judge.language = "go"` is refused, and in README's "still no" paragraph —
   which generation 2 wrote deliberately so it would have to be revisited.
   R2/R3 stay unregistered.
2. **Item 7 / F008-A5** — qualify against
   `shared-ramdisk-depot-manager/tools/covergate` on the same commits. Needs a
   real Go lane through the real CLI, hence step 1 first. Known caveat: memory
   records covergate silently skipping a package (P14) in a past run — if the
   two disagree, work out which side is right before concluding assay is.
3. **Item 3 / F008-A4** — fixture regeneration. This also discharges half of
   B057.
4. **Item 5 / B047 item 3** — the `go-cover` producer vocabulary. Opens
   A-354's deliberately closed vocabulary; the refusal is at
   `config.py:2101-2107` and its test at
   `tests/test_config_coverage_producer.py:234-252` is parametrized on exactly
   `("go-cover", "go-test")` and inverts.
5. **Item 6's remainder**, once DA-3 is answered.

## 6. Ledger

Decisions: A-397 (this generation). **Next free: A-398.**
Backlog: B057 (this generation). **Next free: B058.**

## 7. Gate

**PASS on `c85c703a`**, the commit this brief accompanies — 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, and the installed wheel
(`assay-4.0.1.dev20+gc85c703a`) names the judged commit itself. Full
transcript in REPORT §14; devcontainer `pytest tests/` is 3846 passed / 13
skipped, `PYTEST_EXIT=0`. **The tip you inherit is gate-green, and the only
commit after run 4 is docs-only** (this section, the LOG's gate line and
REPORT §14) — no source, test or packaging file changes after `c85c703a`.

The verdict is read from the log in a separate step, as always — and the wrapper-vs-job trap fired a **third**
time this generation (REPORT §13: "exit code 0" over `PYTEST_EXIT=1` and 13
red tests). Read `GATE_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE=1` yourself,
separately, every time. It is not superstition; it has now caught three real
false greens in one wave.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map); BRIEF-2 in full; this brief in full;
the wave prompt's "Wave C" section; BRIEF-1's rules block (A-334, A-335,
A-042/A-043, A-097/A-101, decisions.md append-only from **A-398**, backlog
from **B058**, `git commit --only -- <paths>`, the trailer, no `!` marker);
§5 above as the literal next task list; the gate command and the
separate-verdict-read discipline.

**DROP:** how the `statement_blocks` signature was chosen (closed — A-397
carries the whole argument, including the three rejected alternatives); the
thirteen-red-tests repair (closed — B057 and REPORT §11 carry it); the
`_read_document` refusal catalogue (closed — it is 13 tests and its own
module docstring); the A-396 packaging thread (already dropped by BRIEF-2 and
nothing reopened it).

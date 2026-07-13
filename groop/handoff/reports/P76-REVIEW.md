# P76 - Frontier review (pass #2)

Reviewer: Opus 4.8, fresh session, wave P67/P75/P76 · 2026-07-13
Verdict: **MERGED** (8a0ffc4) after substantial review-fix. Two blockers.

## Headline

The inference tier -- which is the **only tier that works today**, since ciu does
not yet ship the `ciu.managed` labels (the REPORT admits this) -- matched nothing
on a real host, and claimed unrelated containers when it did match.

Both defects were invisible to the test suite because **the fixtures used Docker
values that cannot exist.**

## Blockers (both fixed before merge)

### B1. The documented config value could never match anything

`flagged-by-pass-1: no`

`config.py` documents `known_stacks` as `"infra/redis-core"` / `"app/web"`, and
`detect_ciu_inferred` compares that string to `com.docker.compose.project`. A
Compose project name **can never contain `/`** -- Compose derives it from the
stack directory's basename and constrains it to `[a-z0-9][a-z0-9_-]*`. So an
operator following the documentation configured a value that matched nothing, got
an empty `[ciu]` surface, and received no error and no signal.

Proven before the fix: `detect_ciu_inferred("redis-core", "redis-core-prod-redis01", {"infra/redis-core"})`
returned `None`. Entries are now matched on their last path segment, so both
`"infra/redis-core"` and `"redis-core"` work.

### B2. The "anchored `^<project>-<env>-<name>$`" check did not anchor to the project

`flagged-by-pass-1: no`

The code ran `^([^-]+)-([^-]+)-(.+)$` against the container name and **never
compared the result to `compose_project`**. The consequence: any container with
two hyphens in a matched Compose project was claimed as ciu-managed. Proven
false positives before the fix -- both returned a `CiuMeta(source="inferred")`:

- `detect_ciu_inferred("redis-core", "totally-unrelated-thing", {"redis-core"})`
- a Pterodactyl UUID container, `3f2b1a9c-1111-4222-8333-444455556666`

The naive repair (anchor on `group(1)`) does **not** work, and this is why the
bug survived: for the project `redis-core`, a `([^-]+)` group captures only
`redis`. Project names routinely contain hyphens, so the regex could not express
the anchor at all. The project is now matched as a **literal prefix**, with the
`<env>-<name>` tail matched separately.

## Also fixed

| # | Finding | pass-1? |
| --- | --- | --- |
| 3 | `_parse_phase` discarded **both** halves of a malformed label, collapsing "ciu shipped a phase we could not parse" into "ciu shipped no phase at all". The honest-absence contract forbids exactly this. It now keeps the raw string and nulls only the int. | no |
| 4 | `entity_to_jsonable` emitted `"ciu": null` on **every** entity, which rewrote every recorded frame on disk and forced the `gstammtisch-once.jsonl` fixture to be regenerated -- violating the handoff's "existing fixtures must not need regeneration". Its sibling `entity_frame_to_jsonable` already omits `governance`/`network`/`damon` when `None`; `ciu` now follows that precedent and **the fixture is reverted untouched.** | **yes** (SELFREVIEW Finding A -- but it "fixed" the REPORT's wording rather than the code) |
| 5 | Dead code: `if num < 0: return None, None` is unreachable under `^phase_(\d+)$`. | no (SELFREVIEW claimed "no dead code") |
| 6 | Four unused imports in the test file. | no (SELFREVIEW claimed "every import is used") |
| 7 | **The full-suite gate was never run.** The REPORT and SELFREVIEW show only four focused test files (~72 tests); the handoff requires the full suite. The SELFREVIEW checklist claims it passed. | no (it claims PASS) |

## Hollow tests (deleted, not salvaged)

Three of the seven named oracles asserted against **code defined inside the test**:

- `TestGroupingCorrectness` grouped with a `setdefault` loop written in the test.
- `TestPhaseOrdering` sorted with a lambda written in the test.
- `test_string_sort_would_fail` asserted that CPython's `sorted()` is lexicographic.

P76 ships **no grouping and no ordering code at all**, so these passed against any
implementation, including one that never parses a phase. They were deleted rather
than left as false coverage; the genuine numeric-phase evidence is `TestParsePhase`.
Real grouping code -- and real grouping oracles -- are carved as **P83**, whose
handoff names this trap explicitly.

Also: `assert "ciu" not in j or j["ciu"] is None` was an OR-oracle that cannot
fail, sitting precisely on finding 4, the contract that was actually broken.

## Verified as genuinely met

- The two detection tiers are never merged (`source` is `"label"` vs `"inferred"`).
- **The config->collector->detection wiring is real.** The self-review's own fix
  (`CiuConfig.known_stacks` never reaching `enrich_entities`) holds: I traced the
  only production caller and confirmed the knob reaches detection. I looked hard
  for more of the same class and found none.
- No new subprocess, no `ciu` invocation, no TOML parse in the collector.
- Frame additivity on read: legacy frames without `ciu` still parse.
- The fixture edit was **legitimate** (it only added `"ciu": null` keys; no values
  altered, no data fabricated) -- not a repeat of the fabricated-fixture failure
  mode. It is now moot, since finding 4 removed the need for it.

## Mutation evidence

Both blocker fixes are pinned by tests that fail without them:

- Revert the project-anchoring fix -> 3 tests fail (the two false positives).
- Revert the basename match -> 6 tests fail.

## Pass-1 overlap

**1 of 7** (~14%), and the one hit was only half-caught: the self-review saw the
fixture-regeneration symptom (Finding A) and fixed the REPORT's *wording* rather
than the serializer that caused it. It found neither blocker, and its checklist
affirmatively claimed three things that were false (full suite run, no dead code,
no unused imports).

## Gates (re-run from `main`, package venv)

- Focused: 59 passed.
- Full suite from `main` after merge: **1328 passed, 1 failed** (the pre-existing
  `test_zst_without_zstandard_exits_2`; carved as P82).
- `git diff --check` clean.

## Merge

ROADMAP auto-merged. `STATUS.md` conflicted: P76's branch predates the P72/P74
merges, so its `74-78%` was a stale **regression** of main's `80-85%`. Resolved on
merits -- kept main's figure, folded in P76's genuine capability additions (CIU
stack metadata exists; CIU grouping/actions remain).

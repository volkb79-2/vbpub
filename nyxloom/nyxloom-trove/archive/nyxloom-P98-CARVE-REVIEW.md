# nyxloom-P98 — pre-dispatch adversarial handoff review

**Reviewed:** `nyxloom-trove/handoffs/nyxloom-P98-retire-toolkit-gate-verify.md`, frozen at `acc0d86e`
(`input_revision: "73887702"`, its own first commit).
**Reviewer stance:** hostile implementer + hostile environment + independent acceptance engineer,
per `reference/AUTHORING.md`'s "Pre-dispatch adversarial handoff review" section.
**Method:** every factual/sweep claim below was independently re-derived from the actual tree at
this worktree's HEAD (`acc0d86e`) — `git grep`, direct file reads, running `nyxloom lint` itself,
and executing the exact JSON-path snippet from O4 — not trusted from the handoff's prose.

## Verdict up front

**NOT READY.** The handoff's central sweep claim ("scope.touch is the complete edit list; the
carve's reverse-dependency sweep found the real call sites") does not hold. Re-running the sweep
surfaces **at least seven files with real, verified references to material this package deletes,
none of them in `scope.touch`**, one of them core production wiring (`planning.py`). Independently,
`config.py`'s scope entries misattribute a field to the wrong class three times, and Work items 6
and 7 give contradictory instructions for the same STANDARD.md paragraph. Full detail follows.

---

## 1. Blocking ambiguities

**B1 — STANDARD.md: three occurrences of "nyxloom gate verify", Work item 7 addresses one, O5 requires all three gone, and item 7 collides with item 6.**
`grep -n "nyxloom gate verify" reference/STANDARD.md` (re-run, verified) returns three hits:
line 192 (the "fail closed" bullet describing the TRANSPORT_UNTRUSTED sentinel probe, also cited by
`nyxloom doctor`), line 214 (inside the "Gate rigor is a first-class, per-project fact" paragraph,
immediately after the `asserts=[...]` sentence), and line 266 ("Validation methodology" item 7).
Work item 7 names only two targets: the "OFFERED, not mandated" paragraph (lines 199-204) and "the
`nyxloom gate verify`/GA1 paragraph that follows the asserts-enum sentence" — i.e. the single
occurrence at line 214. It says nothing about lines 192 or 266. Oracle O5 is a **total** count
(`grep -c ... == 0`), so satisfying O5 as literally specified requires removing all three, which
Work item 7 never instructs. Worse: lines 210-225 are **one unbroken paragraph** in the source (no
blank line inside it) — the `asserts=[...]` sentence Work item 6 (NL-4) requires **keeping and
editing** ("Mirror the addition ... in STANDARD.md's prose list") lives inside the exact paragraph
Work item 7 says to "delete ... in full". The implementer must invent which half survives; the
handoff gives no split point.

**B2 — `gate_verify_interval_days` and `mutation_gate` are `Policy` fields, not `GateDef` fields — verified against `config.py`, contradicting the handoff three times.**
`config.py`: `class GateDef` spans lines 56-71; `class Policy` spans lines 112-265.
`gate_verify_interval_days` is defined at line 173, `mutation_gate` at line 198 — **both inside
`Policy`**. The handoff nonetheless says, three separate times: (scope.touch) "remove
`GateDef.gate_verify_interval_days` field"; (Work item 3) "In `config.py`: delete
`GateDef.gate_verify_interval_days`"; (Scope/forbid) "`src/nyxloom/config.py`'s
`GateDef.mutation_gate: bool` field ... stays. Only `GateDef.gate_verify_interval_days` is removed
... `mutation_gate` is a different field". No such class attribute exists; a literal-following
implementer is left to reconcile a class name that is simply wrong.

**B3 — `nyxloom/reference/STANDARD.md`, `nyxloom/assay.toml`, and four "Context to read first" paths carry a `nyxloom/` prefix that does not exist in this repo, and `nyxloom lint` does not catch it.**
This worktree's root (`cfg.root` for the `nyxloom` project, confirmed by directly invoking
`lint.resolve_project_for_path`) has no nested `nyxloom/` subdirectory (`ls` confirms). The real
paths are `reference/STANDARD.md`, `assay.toml`, `src/nyxloom/effects_merge.py`, etc. — no `nyxloom/`
prefix. I ran `nyxloom lint nyxloom-trove/handoffs/nyxloom-P98-retire-toolkit-gate-verify.md`
directly against this checkout (`PYTHONPATH=src python3 -m nyxloom.cli lint ...`) and it reports
**"clean", zero findings** — not because the paths resolve, but because `_check_path_resolution`'s
existence check is gated on `not is_touch`, i.e. **scope.touch entries are never existence-checked
at all**, only `scope.forbid`/`source.ref` are. So this is real, lint-invisible, and left for the
implementer to silently paper over. Low severity alone, but it demonstrates the frontmatter was
never mechanically exercised against the real tree before freezing — the same failure mode behind
the far more serious B1/B2 and the sweep gaps in §4.

**B4 — reconcile.py's numbering-repair instruction doesn't match the actual coupling.**
Work item 3 says: "renumber neighboring cross-references to item 16 if any refer to it by number —
check items 15 and 17's prose." Verified: item 17's own docstring (`reconcile.py:335`) says "UNLIKE
`test_health_interval_days` and `gate_verify_interval_days`, this does NOT fire..." — a reference by
**field name**, not "item 16" by number, so a literal reading of the instruction (numeric
cross-refs only) misses it. It's also unstated whether item 17 should be renumbered to 16 (closing
the gap) — cascading into `docs/plan-gap-engine-and-reviewer-repair.md`, `docs/plan-next-batches.md`,
and the "WHERE EACH ITEM LIVES" table, none of which are in scope — or left with a numbering hole.

## 2. False-PASS attacks

**The single most damaging attack spans O1, O2, O3, O5, O6, O7 simultaneously:** delete the three
module files + their dedicated tests (Work item 1), delete the CLI `verify` subcommand (Work item
2), do the schema/doc edits (Work items 5-9) — but for Work items 3-4, only remove the two literal
import lines that would otherwise break collection (`gate_canary` import in `effects_gates.py` and
`daemon.py`) and stub the one call site that used it (e.g. `verdict = "INCONCLUSIVE"`
unconditionally). **Leave `GateEffector.verify_gate`/`_run_verify_probe`/`drain_verify`, the
`verify_running`/`verify_results` state, the effect-registration entry, `reconcile.VerifyGate`,
`rules_attention`'s gate-verify rule, `planning.py`'s `RuleSpec(name="gate-verify", ...)`,
`types.EventType.GATE_VERIFY_RECORDED`, and `Policy.gate_verify_interval_days` /
`ReconcileInput.days_since_gate_verify` all fully in place.** This passes:
- O1 (only checks the 7 named *files* are gone — none of these are files)
- O2 (nothing here breaks collection or fails a test; the background probe just always resolves
  INCONCLUSIVE now, silently, forever)
- O3 (the CLI verb is gone)
- O5 (STANDARD.md text is independent of code)
- O6/O7 (doc-only)

...while directly violating the stated contract: "Remove the GA4 daemon cadence end to end"
(Work item 3's own heading) is not met — the daemon still spawns a background thread on a timer,
still emits `GATE_VERIFY_RECORDED`, still runs an inert planner rule, none of which any oracle
asserts the absence of. **No oracle checks for the absence of any of `VerifyGate`, `drain_verify`,
`_run_verify_probe`, `GATE_VERIFY_RECORDED`, `gate_verify_interval_days`, or
`days_since_gate_verify` as symbols** — O1's closed-list check is file-path-only.

Per-oracle wrong implementations, additionally:
- **O1**: `git mv src/nyxloom/coverage_gate.py src/nyxloom/_legacy_coverage_helpers.py` and inline
  its body into `gate_runner.py` under a new name. All 7 named paths are gone; the toolkit's logic
  still ships, just relocated — passes the closed-list oracle, violates "retire the toolkit."
- **O3**: rename `gate verify` to `gate audit` (new subparser name, identical `cmd_gate_verify` body
  underneath). `gate --help` no longer lists `verify` — passes — while GA1 is fully intact under a
  new name.
- **O4**: add `"assay-verdict"` to the schema enum and nowhere else. Passes O4 exactly as worded
  (only the JSON path is checked); the value is now schema-legal but has zero consumers anywhere in
  the codebase (the only historical consumer of the `asserts` declarations, `cmd_gate_verify`'s
  DECLARATION MISMATCH check, is deleted in this same package) — a dead enum value, and nothing
  catches it.
- **O5**: delete the *entire* "Gate rigor is a first-class, per-project fact" paragraph (lines
  210-225), including the `asserts=[...]` prose sentence Work item 6 needed preserved-and-edited.
  Both `grep -c` targets go to 0 — O5 passes — while STANDARD.md now has no prose description of
  `asserts` at all (a stealth regression no oracle checks, since O4 only checks the JSON schema).
- **O6**: `nyxloom-trove/decisions.md` entry: *"Decision (2026-09-02): reversing the 2026-07-27
  mutation_gate premise; GA1/GA4 superseded by Assay R2/R3."* — a bare keyword-stuffed one-liner
  with zero rationale. O6's negative only rules out "removed dead code" with *neither* fact named;
  it does not require the entry to be an actual reasoned decision record as AUTHORING §6 intends.
- **O7**: prepend literally `<!-- superseded -->` as line 1 of the moved P90 file and nothing else.
  "first 10 lines contain the word 'superseded'" — passes — while Work item 9's actual ask (a real
  explanatory note citing the reorientation analysis) is not done.

## 3. Missing implementation-packet content

This package touches ~25 files with cross-file numbering/prose coupling and a daemon
concurrency surface (background threads, an idempotency-keyed effect registry) — squarely the kind
of "solution-bearing execution" AUTHORING's ladder says should carry an
`## Implementation packet (normative)` section. **None exists.** The handoff goes directly from
frontmatter to `## BLOCKED protocol` / `## Context to read first` / `## Work`. Specifically absent:

- **Owned-interface/decision table for the STANDARD.md edit** (§B1): which of the three "nyxloom
  gate verify" occurrences survive in what form, and how the `asserts=[...]` prose sentence
  (needed by NL-4) is preserved when its containing paragraph is deleted.
- **An edit map or even a mention of `src/nyxloom/planning.py`.** It is absent from Context-to-read,
  scope.touch, *and* scope.forbid, yet it owns the live `RuleSpec(name="gate-verify", contract_items=(16,),
  rule=rules_attention.gate_verify, emits=frozenset({"VerifyGate"}), channel=Channel.GATE_VERIFY)`
  entry inside `rule_table()` (verified, `planning.py:1218-1224`) that directly calls
  `rules_attention.gate_verify` — the exact function Work item 3 instructs deleting. Leaving this
  entry after deleting the function is an `AttributeError` the first time `rule_table()`/
  `plan_project` runs, i.e. in nearly every daemon/planner test in the suite.
- **Any decision for `tools/remote_mutation_audit.py`.** Verified: line 33 does
  `from nyxloom.mutation_gate import Mutant, generate_mutants`, and both names are used
  substantively (mutant-job construction, lines 179-206) — not a decorative import. Work item 1
  deletes `mutation_gate.py` outright with no replacement named for this real consumer. This is a
  product decision (reimplement inline? point at Assay's own `mutation.py`? mark the tool
  intentionally dead?), not mechanical work, and the handoff never even surfaces it as an open
  question.
- **Coverage of the shared corpus/fixture layer.** `tests/test_gap_audit.py`'s `_inp` helper builds
  `ReconcileInput(**base)` with `days_since_gate_verify=100.0` in `base` (line 82).
  `tests/corpus_profiles.py` carries a `("gate-verify-due", {"_policy": {"gate_verify_interval_days":
  7}, "days_since_gate_verify": None}, "contract item 16")` profile tuple (lines 192-194), consumed
  by `tests/planner_corpus.py` and `tests/test_planner_differential.py` in addition to
  `test_planning.py` (which *is* in scope). None of the other three files are in scope.touch.
- **A second onboarding_gate.py reference.** Work item 5 names only the "missing-gate guidance
  text." Verified: `_has_gate_recommendation` (lines 60-69) separately says "run `nyxloom gate verify
  <project>` yourself..." and cites `gate_verify_interval_days` by name — untouched by the handoff's
  instructions for this same file.
- **No tracer-bullet/probe evidence.** AUTHORING requires the carver to "run the skeleton and witness
  each acceptance negative fail before dispatch" and record the commands/results. Nothing in the
  handoff or its commit history shows the sweep claims were executed rather than asserted — and
  independently re-running them (this review) falsifies the completeness claim outright.

## 4. Scope/dependency defects

Verified real call sites entirely missing from `scope.touch` (none are hypothetical — each was
directly grepped/read against `acc0d86e`):

| File | Reference | Consequence if scope.touch is followed literally |
|---|---|---|
| `src/nyxloom/planning.py` | `RuleSpec(name="gate-verify", rule=rules_attention.gate_verify, emits=frozenset({"VerifyGate"}), channel=Channel.GATE_VERIFY)` in `rule_table()` | `AttributeError` on first `plan_project`/`rule_table()` call once `rules_attention.gate_verify` is deleted — breaks nearly the whole suite |
| `tools/remote_mutation_audit.py` | `from nyxloom.mutation_gate import Mutant, generate_mutants` (used, not decorative) | `ModuleNotFoundError` once `mutation_gate.py` is deleted; breaks a real production tool, not a test |
| `tests/test_remote_mutation_audit_tools.py` | module-scoped `worker` fixture `exec_module`s the file above | every test using the `worker` fixture errors (no skip markers gate these) |
| `tests/test_snapshot_faults.py` | module-level `IRREVERSIBLE` tuple includes `EventType.GATE_VERIFY_RECORDED` (line 174) | `AttributeError` at **collection time** (module-level constant) once the enum member is deleted |
| `tests/test_gap_audit.py` | `_inp` helper: `ReconcileInput(**base)` where `base` sets `days_since_gate_verify=100.0` (line 82) | `TypeError: unexpected keyword argument` once the field is removed |
| `tests/corpus_profiles.py` | `"gate-verify-due"` profile tuple sets `gate_verify_interval_days`/`days_since_gate_verify` (lines 192-194) | breaks any generic sweep over all profiles (consumed by `planner_corpus.py`, `test_planner_differential.py`, `test_planning.py`) |
| `src/nyxloom/onboarding_gate.py` | `_has_gate_recommendation`'s second `nyxloom gate verify` / `gate_verify_interval_days` mention (lines 60-69) | stale prose survives; no oracle catches it, but it contradicts Work item 5's intent |

Each of the first five guarantees this package trips its own `escalate_if` #1 ("any touched
non-test file outside this list needs an edit to keep the gate green") on the very first dispatch
attempt — the package as scoped cannot reach a green gate without an edit outside `scope.touch`.
That is not the escalation mechanism working as designed; it is evidence the carve was not
re-verified against the tree before freezing.

Claims that **do** hold up under re-verification (stated for balance):
- `effects_merge.py`'s `("mutation", cfg.policy.mutation_gate, effects_gates.select_mutation_gate)`
  wiring is confirmed generic — `git grep -n "coverage_gate\|gate_canary" -- src/nyxloom/effects_merge.py`
  returns nothing, and its only "mutation_gate" hit (line 134) is the `Policy.mutation_gate` boolean
  read. Correctly forbidden.
- The six `test_mutation_gate_*` functions in `test_daemon.py` (lines 6136-6390) really do only use
  `mutation_gate=True/False` as a `Policy` kwarg plus bare `argv=['true']/['false']` `GateDef`s —
  never import the deleted module. Correctly excluded from editing.
- O4's JSON path (`properties.gates.additionalProperties.properties.asserts.items.enum`) resolves
  exactly as claimed, and the cited line numbers (127-130) are correct — verified by loading the
  actual schema file and by direct line grep.
- The narrow claim in `escalate_if` #2 — "no real importer of the three modules exists **anywhere
  in vbpub** [outside nyxloom itself]" — is independently verified TRUE: a repo-wide grep across
  `ciu/`, `assay/`, `cmru/`, `pwmcp/` (excluding stale `.worktrees/` snapshots) found zero real
  `from nyxloom...import` hits. This narrow claim should not be read as validating the much broader
  "the sweep of scope.touch is complete" claim, which does not hold (see table above).

## 5. Corrected oracle/fixture matrix

**Traceability (requirement → oracle → gap):**

| Work item | Oracle(s) covering it | Coverage gap |
|---|---|---|
| 1 (delete 3 modules + tests) | O1, O2 | O1 checks paths only, not that the *logic* is gone (relocation attack, §2) |
| 2 (remove GA1 CLI) | O3 | none — solid, but see rename attack (§2) |
| 3 (remove GA4 end-to-end: effects_gates/daemon/reconcile/rules_attention/types/config) | none directly; only incidentally covered by O2's "gate stays green" | **no oracle asserts the absence of `VerifyGate`, `drain_verify`, `_run_verify_probe`, `GATE_VERIFY_RECORDED`, `gate_verify_interval_days`, `days_since_gate_verify`** — the "stub it and leave it" attack (§2) passes everything |
| 4 (update test files, keep 6 mutation_gate tests) | O2 (gate green) | fine in principle, but O2 as worded doesn't enumerate which files must be *collected*, so a scope-widening fix (touching planning.py etc.) done silently would also pass O2 without ever being declared |
| 5 (onboarding_gate.py, gate_scaffold.py stale-reference cleanup) | **none** | zero oracle coverage; can be skipped entirely and every oracle still passes |
| 6 (NL-4 schema + two doc mirrors) | O4 covers the schema only | config.py docstring mirror and STANDARD.md prose mirror are unverified |
| 7 (STANDARD.md paragraph removal) | O5 | undercounts occurrences (§B1) and conflicts with item 6 |
| 8 (decisions.md entry) | O6 | wording of O6's negative is weak enough for a boilerplate one-liner (§2) |
| 9 (archive P90) | O7 | "contains the word 'superseded'" is satisfiable with a content-free stub (§2) |

**Pairwise input matrix (axes: sweep depth × reference kind × in-scope?):**

| | token-only sweep (module name) | field/symbol sweep (attribute, enum member, class) |
|---|---|---|
| **production code, in scope.touch** | coverage_gate/mutation_gate/gate_canary imports in cli.py, effects_gates.py, daemon.py — correctly found | GateDef vs Policy misattribution (B2) — found the field, named the wrong owner |
| **production code, NOT in scope.touch** | (none found — token sweep was actually complete for the 3 literal names) | `planning.py`'s `RuleSpec`/`Channel.GATE_VERIFY` — **missed entirely**; this is the carve's real blind spot |
| **test/fixture code, in scope.touch** | test_coverage_gate.py etc. — correctly found | test_daemon.py's 6 kept functions correctly identified as safe |
| **test/fixture code, NOT in scope.touch** | tools/remote_mutation_audit.py, test_remote_mutation_audit_tools.py | test_snapshot_faults.py, test_gap_audit.py, corpus_profiles.py + its 2 consumers — **all missed** |

**Three combined-axis fixtures that break a convenient implementation:**

1. **F1 — call `planning.rule_table()` twice in one process, once via `test_planning.py` and once
   via any daemon test that calls `plan_project` indirectly (e.g. `test_gap_audit.py`).** Combines
   (production-wiring axis) × (repeated-execution axis). A convenient implementation that only fixes
   `rules_attention.py` and reruns `test_planning.py`'s narrow, already-scoped assertions in
   isolation could miss that `rule_table()` itself breaks for *every* other caller — this fixture
   forces the break to surface from an unrelated entry point.
2. **F2 — load `tools/remote_mutation_audit.py` via `test_remote_mutation_audit_tools.py`'s
   module-scoped `worker` fixture in the same pytest session as an unrelated, earlier-alphabetical
   test file that also imports `nyxloom.daemon`.** Combines (tool-script axis) × (test-order/
   module-scope-caching axis) — proves the failure isn't hidden by import caching or fixture scope,
   and that it surfaces regardless of xdist worker assignment.
3. **F3 — run `test_planning.py`'s generic profile-sweep test (the one that iterates
   `corpus_profiles.PROFILES`) and separately construct a `ReconcileInput` by hand with
   `days_since_gate_verify` omitted.** Combines (shared-fixture-corpus axis) × (declared/effective
   field axis) — shows the break is inherited from `corpus_profiles.py`'s data even though no
   *test body* anywhere says "gate_verify" — a hostile implementer who greps only test *bodies*
   (not shared data tables) for the token would miss it exactly as the carve did.
4. **F4 (bonus, declared-vs-actual namespace) — construct a `GateDef` with a `gate_verify_interval_days`
   kwarg (per the handoff's own claim) and separately load `Policy()` and inspect
   `dataclasses.fields(Policy)`.** The first raises `TypeError` (no such field on `GateDef`); the
   second shows the real owner. This is the fixture that would have caught B2 before dispatch.

## 6. READY or NOT READY

**NOT READY.**

1. The scope.touch sweep is verifiably incomplete: at least five files with real, executable
   references to deleted/removed material (`planning.py`, `tools/remote_mutation_audit.py`,
   `tests/test_snapshot_faults.py`, `tests/test_gap_audit.py`, `tests/corpus_profiles.py` + 2
   consumers) are absent from it, one of them core production wiring. As scoped, this package is
   guaranteed to trip its own `escalate_if` on first dispatch.
2. `config.py`'s target field is misattributed to the wrong class (`GateDef` vs. the actual
   `Policy`) three independent times across scope.touch, Work item 3, and Scope/forbid.
3. Work items 6 and 7 give contradictory instructions for the same STANDARD.md paragraph, and O5
   under-specifies which of STANDARD.md's three "nyxloom gate verify" occurrences must go.
4. Work items 3 and 5 have little-to-no oracle coverage: GA4's daemon-cadence machinery can be left
   fully in place (just import-patched) and every stated oracle still passes; Work item 5's
   onboarding/scaffold text cleanup has zero oracle coverage.
5. No implementation packet, and no evidence the sweep claims were mechanically re-run before
   freezing — re-running them here is what surfaced all of the above.

Recommend: re-carve with (a) a symbol-level sweep (not just the three module-name tokens) across
`src/` and `tests/`, explicitly including `planning.py`'s `rule_table()` and the shared
`corpus_profiles.py`/`planner_corpus.py` fixture layer; (b) a decision (product-level, not
mechanical) for `tools/remote_mutation_audit.py`'s real dependency on `mutation_gate.py`; (c) a
single unambiguous edit plan for STANDARD.md covering all three occurrences, sequenced so the NL-4
asserts-prose edit and the GA1-paragraph deletion don't collide; (d) oracles that assert the
*absence* of the GA4 symbols being removed, not just that the gate stays green.

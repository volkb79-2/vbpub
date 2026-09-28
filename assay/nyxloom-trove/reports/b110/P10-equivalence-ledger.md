# B110-P10 — Native Python equivalence ledger with stable site anchors (P10a design, P10b implementation)

| Field | P10a (design and probe) | P10b (producer implementation) |
|---|---|---|
| Backlog | B120, plan P10 | B120 |
| Branch | `assay-b110-p10a-ledger-design`, cut from the integration line | `assay-b110-p10b-ledger`, cut from `assay-b110-v14` |
| Depends on | nothing; can start on day one | P10a design **accepted by a fresh-session review**; **P3a** merged into `assay-b110-v14` with the ledger wire fields from P10a; **P7** (candidate-selection execution path) merged into the integration line and `assay-b110-v14` |
| Contract class | **2a**: design-bearing, with open choices enumerated below | **2b**: solution fixed by the accepted P10a design |
| Implementer | Opus | Opus |
| Decisions | A-465 (plan D1); A-470 (v14 wire, plan D6); A-464 (time never classifies); A-209 (equivalents need a proof source) | same |
| Size | M | L |

## Why this package exists

**The problem.**
- A passing native R2 campaign may contain only kills (`judge_mutation`, mutation.py 3626-3769).
- A survivor can never pass (A-462).
- Native lanes cannot declare `fail_under`.
- `equivalence_artifact` is SQL-only (config.py 459-472 and 3082-3106).

So a genuinely equivalent Python mutant blocks B105 forever.

**The operator's ruling (A-465):**
- Such mutants are dispositioned by a **reviewed, committed ledger** keyed by a **stable site anchor**, not by the candidate ID, which changes whenever anything in the file changes.
- Entries land in the existing `equivalent` bucket.
- **Middle path:**
  - The qualifying run does **not** execute ledger candidates.
  - A mandatory **ledger audit** runs at the **same commit** first, in the non-qualifying screen. It gives each entry a full declared-suite run.
  - Any entry that is killed, hung, crashed, budget-exceeded or unresolvable is refused.

**Shared design with B109.** The anchor is the same one B109 needs: "a semantic site fingerprint and unambiguous structural anchor" (4-backlog.md `## B109`, desired-behavior bullet 2). Design it once for both.

**Timing.** P10a must be **accepted before P3a freezes the v14 schema**, because it contributes the `ledger` execution mode and `judgment.r2.equivalence_ledger`.

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`:
   - §3 D1 and D6;
   - §5 (the v14 wire, where `ledger` is `{mode, anchor}` and `equivalence_ledger` is `{path, sha256, entry_count, audit_sha256}`);
   - §9.1 step 4 and §9.2.
2. `nyxloom-trove/decisions.md`: A-462 (:941), A-464 (:943), and the A-209/A-223d rows. Search for "A-209" and "A-223".
3. `nyxloom-trove/4-backlog.md`: `## B109` (anchor requirements and acceptance, the Luna design-review requirement for schema changes) and `## B105` acceptance (the complete-inventory rule).
4. `src/assay/adapters/python.py`:
   - operator catalogues 440-478;
   - `_Site` 480-494;
   - `_compare_swap_sites` 535-567;
   - `_boolop_swap_sites` 570-598;
   - `_bool_const_flip_site` 600-617;
   - `_falsy_swap_site` 651-690;
   - `_candidate_sites` 708-719;
   - `_generate_python_sites` 722-787. It is a full `ast.walk`; `description` has the form `Eq->NotEq`, `True->False`, `And->Or`.
5. `src/assay/mutation.py`:
   - `MutationSite` 320-410;
   - `candidate_id` 1023-1034 (with `candidate_identity.py`);
   - the `_classify_mutant_result_with_equivalence` SQL path 1702-1745;
   - `mutation_pct` 3601-3623;
   - `judge_mutation` 3626-3769 (the `ALL_MUTANTS_EQUIVALENT` branch near the end);
   - the job-submission and `budget_exceeded_mask` bucket assembly 3009-3035.
6. `src/assay/config.py`:
   - `_MUTATION_OPTIONAL_FIELDS` 438-450;
   - `_MUTATION_SQL_ONLY_FIELDS` 459-472;
   - the reserved-key refusal ~2946-2952;
   - the SQL `equivalence_artifact` loading 3082-3106;
   - `_validate_artifact_path` (grep for it).
7. `src/assay/verify.py:1283-1304` `_check_equivalence_pairing`, and `src/assay/verdict.py:5038-5055` `_check_equivalence_pairing`. Both implement A-209 "both present or both absent".
8. `src/assay/cli.py`: the `plan` parser 330-367 and `_cmd_plan` 1571-1886, where the candidate rows are built (~1820-1840).
9. `tools/b105_report_check.py:14-107`: the source-bound checker P10b extends.
10. The P7 brief (`b110/P7-pilot-tooling.md`): `--candidates-file`, the non-qualifying run, R3 not run, and exit 6. The audit reuses that execution path.

---

## P10a — Design packet (2a)

**P10a deliverables:**
1. `nyxloom-trove/reports/assay-B120-P10A-DESIGN.md`: the frozen contract. Resolve every open choice below, and include the exact JSON-Schema fragments P3a must add.
2. `tools/b120_anchor_probe.py`: the tracer-bullet probe, which contains the prototype `site_anchor`.
3. Its output, `nyxloom-trove/reports/assay-B120-P10A-PROBE.json`.
4. A fresh-session design review verdict of **READY**. It routes per the plan §10 review rule and is recorded in the design doc. Where a verdict or schema change is involved, it doubles as B109's required independent design review.

### Proposed contract (the carver's recommendation; the review may amend it)

**Anchor grammar** (one string per candidate; a pure function of `(path, source text, all sites of that file)`):

```
python-site/1:<path>:<qualname>:<operator>:<change>:<ordinal>
```

Components:
- `path`: the plan row's project-relative POSIX path, for example `src/assay/base.py`.
- `qualname`: the dotted chain of enclosing `ClassDef`/`FunctionDef`/`AsyncFunctionDef` names from the module root, in `__qualname__` style. Nested functions look like `Outer.method.<locals>.inner`. Module level is `<module>`. Lambdas and comprehensions are not scopes. A **role suffix** marks sites outside a body:
  - `@decorator` for sites in a def/class `decorator_list`;
  - `@signature` for argument defaults, keyword defaults and annotations, and returns;
  - `@bases` for class bases and keywords.

  For example, a dataclass flag `@dataclass(frozen=True)` on `class Candidate` has qualname `Candidate@decorator`. Without roles, all 148 dataclass-flag sites would sit in `<module>` and depend on ordinals.
- `operator`: the site's operator, for example `python:compare-swap`.
- `change`: the site's `description`, for example `Eq->NotEq`. It uses AST class names, so it is whitespace-insensitive.
- `ordinal`: the 0-based index among the sites of the same file with equal `(qualname, operator, change)`, ordered by `start_byte`.
- **Encoding:** each component percent-encodes `%` → `%25` and `:` → `%3A`, so `python:compare-swap` becomes `python%3Acompare-swap`. The anchor is ≤ 4096 UTF-8 bytes. Regex: `^python-site/1(:[^:]+){5}$`.

**Semantic fingerprint** (64 hex): `sha256(ast.dump(subject, annotate_fields=True, include_attributes=False) + "\0" + target_path + "\0" + change)`.

- `subject` is the smallest of the following that contains the site:
  - a **simple statement**;
  - the **header expression field** of a compound statement: `If.test`, `While.test`, `For.target`/`For.iter`, `With.items`, `Match.subject`, and the `Try` handler `type`;
  - for role sites, the **specific decorator/default/annotation/base expression**.

  Never a whole compound body.
- `target_path` is the field/index path from `subject` down to the mutated node, for example `ops[0]` or `values[1]`.

Positions, comments, blank lines and formatting never enter an anchor or a fingerprint.

**Ledger eligibility (the ambiguity rule).**
- A candidate may be ledgered only if its **fingerprint class** `(path, qualname, operator, change, fingerprint)` has **exactly one member** in the current plan.
- This holds both at audit time and at qualifying time.
- The ordinal keeps anchors unique for every candidate, which B109 needs, but **resolution never depends on the ordinal alone**. Inserting an identical-shape statement shifts ordinals; it must not silently re-target an entry.

**Ledger file.** Committed TOML at the lane-declared path. It is read from the **baseline snapshot** (the judged commit), never from the worktree.

*Valid example:*

```toml
schema = 1
lane = "self-qualification"

[[entry]]
anchor = "python-site/1:src/assay/example.py:Window.contains:python%3Acompare-swap:LtE->Lt:0"
fingerprint = "4f7c…(64 hex)"
operator = "python:compare-swap"
reason = "end is exclusive and every caller passes end > start + 1, so <= and < agree on all reachable inputs"
reviewer = "operator"
reviewed_on = 2026-10-05
```

Field rules:
- exactly the keys shown;
- `operator` must equal the anchor's operator component;
- `reason` is ≥ 40 characters;
- `reviewer` is 1-128 characters;
- `reviewed_on` is a TOML local date;
- duplicate anchors are refused;
- `lane` must equal the declaring lane.

The file's SHA-256 over its exact bytes is the `ledger_sha256`.

*Invalid example 1:*
```toml
schema = 1
lane = "self-qualification"
[[entry]]
anchor = "python-site/1:src/assay/example.py:Window.contains:python%3Acompare-swap:LtE->Lt:0"
fingerprint = "4f7c…"
operator = "python:compare-swap"
reason = "equivalent, trust me"
reviewer = "operator"
reviewed_on = 2026-10-05
```
It is refused for two reasons: `reason` is under 40 characters, and there is no proof argument.

*Invalid example 2:* the first entry repeated with the same `anchor` and `operator = "python:boolop-swap"`. It is refused as a duplicate anchor, and the operator does not match the anchor's component.

**Lane key.** `judge.mutation.equivalence_ledger = "<project-relative path>"`.
- It is allowed only on a native lane (`format` absent) with `judge.language == "python"`.
- It is refused on SQL lanes (which keep `equivalence_artifact`), on ingested lanes and on other languages.
- A lane never has both keys.
- It is added to `_MUTATION_OPTIONAL_FIELDS`, and it is refused on ingested lanes via `orchestration_only`.
- The lane schema needs no version bump: the key is additive, and older binaries fail closed on unknown keys.

**Audit command:**

```
assay ledger audit <lane> --file <assay.toml> --ledger <path> --state-dir <DIR> --receipt <PATH>
```

- `--ledger` must equal the lane's declared path. This cross-check exists because "defaults are hazards".
- The command resolves every entry under the eligibility rule. It then runs the resolved candidates through **P7's candidate-selection execution path**, using the **declared** command:
  - no transform;
  - no cold stop;
  - full suite;
  - R0/R1 baseline;
  - R3 not run;
  - no verdict.
- It writes the receipt with `_write_new`, never overwriting. Exit codes: 0 when accepted, 1 when refused, 2 on an evidence or argument error.

*Receipt* (`src/assay/schemas/ledger-audit-receipt.schema.json`, `"schema_version": {"const": 1}`):

```json
{"schema_version": 1, "kind": "assay-ledger-audit", "lane": "self-qualification",
 "commit": "<40hex>", "git_tree": "<40hex>", "assay_version": "7.2.0.dev14+g…",
 "ledger_path": "mutation-equivalence-ledger.toml", "ledger_sha256": "<64hex>", "entry_count": 3,
 "judge_sha256": "<64hex>",
 "entries": [{"anchor": "python-site/1:…", "candidate_id": "<64hex>", "bucket": "survived",
              "elapsed_s": 512.3, "refusal": null}],
 "result": "accepted"}
```

Receipt rules:
- `result` is `accepted` only if every entry resolved and every bucket is `survived`.
- An unresolved entry has `candidate_id: null`, `bucket: null` and `refusal` ∈ {`unresolved`, `ambiguous`, `fingerprint-mismatch`}.
- A resolved entry with a non-`survived` bucket has `refusal: "not-survived"`.
- `judge_sha256` is the audit run's own judge identity: the declared plan, `cold_witness_kills=False`, and every P3b cold-only input `None`.

Invalid receipts:
1. `result: "accepted"` with any `refusal` non-null.
2. `len(entries) != entry_count`.

**Qualifying consumption:**

```
assay run <lane> --equivalence-audit <receipt> …
```

Order of operations inside `_run_prepared_lane` / `run_mutation`, **after both baselines and before any candidate submission**:
1. Read the ledger from the baseline snapshot and compute its sha256.
2. Resolve the entries using the eligibility rule.
3. Check the binding. All of the following must hold:
   - `receipt.result == "accepted"`;
   - the receipt's `commit`, `git_tree`, `lane`, `assay_version`, `ledger_path` and `ledger_sha256` equal this run's values;
   - the entries' `candidate_id` set equals the resolved set;
   - `receipt.judge_sha256` equals the declared-command identity this run recomputes. That is `judge_sha256` over the **declared plan after liveness injection and before any witness or manifest injection**, with `cold_witness_kills=False` and all cold-only inputs `None`.
4. Remove the ledger candidates from the submission list.
5. Place them in `equivalent`, with execution `{"mode": "ledger", "anchor": "<anchor>"}` and no `evidence`.

Additionally:
- `judgment.r2.equivalence_ledger = {path, sha256, entry_count, audit_sha256}`, where `audit_sha256` is the sha256 of the receipt bytes.
- The qualifying run's judge identity includes `equivalence_ledger_sha256` (plan §5).
- **Any binding or resolution failure** → the R2 claim is `ERROR`/`BAD_LANE_CONFIG`, naming the failed check. **No candidate is counted `equivalent`.**
- **`--equivalence-audit` passed when the lane declares no ledger** → the same refusal.
- **The lane declares a ledger but no `--equivalence-audit` is passed:**
  - the ledger is ignored for classification, and its candidates execute normally (screens do this);
  - the verdict carries `equivalence_ledger: null`;
  - the progress `plan` event carries `equivalence_ledger: "declared-unaudited"`.

**v14 wire and verifier rules.** P3a implements these in the schema, the model and `verify.py`:
- **V1.** `judgment.r2.equivalence_ledger` is `null` on every native judgment unless it is set. When set, it is exactly `{path, sha256, entry_count, audit_sha256}`:
  - `path` is project-relative POSIX with no `..` segment;
  - both digests are 64 hex;
  - `entry_count` is an integer ≥ 1, and a bool is rejected.

  It is absent on ingested judgments, and it is mutually exclusive with `equivalence_artifact`.
- **V2.** Extend A-209 at both pairing checks (verify.py:1283 and verdict.py:5038): a non-empty native `equivalent` bucket requires a non-null `equivalence_ledger`, and a SQL one requires `equivalence_artifact`.
- **V3.** When the ledger is non-null:
  - every `equivalent` entry's execution is exactly `{mode: "ledger", anchor}`;
  - `len(equivalent) == entry_count`;
  - anchors are unique and match the grammar regex;
  - no `evidence` or `witness` is present.
- **V4.** Mode `ledger` in any other bucket, or while the ledger is `null`, is refused.
- **V5.** The existing inventory equality (`candidate_ids` equals the union of the buckets) already covers the ledger candidates, and must keep doing so.
- **V6.** No change to `judge_mutation`:
  - equivalents stay out of `mutation_pct`'s denominator (3601-3623);
  - `killed + survived == 0` with a non-empty `equivalent` still gives `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT`.

**Plan exposure.** `assay plan --json` candidate rows gain `anchor`, `fingerprint` and `ledger_eligible` (bool). Authors copy anchors from there; nobody writes them by hand.

### Open choices

| # | Choice | Admissible options | Recommended | Deciding evidence | Needs a D-decision? |
|---|---|---|---|---|---|
| OC1 | Anchor encoding | (a) `:` with percent-encoding; (b) `\|` separator; (c) structured TOML fields with no string form | (a). One wire string, and exact | Round-trip property on all 3,760 anchors in the probe | no |
| OC2 | Qualname and roles | (a) lexical `__qualname__` plus a role suffix; (b) evaluation scope (decorators belong to the outer scope); (c) no roles | (a) | Probe: the 148 dataclass-flag sites get `@decorator`; ordinal>0 rate with (a) versus (c) | no |
| OC3 | Fingerprint subject | (a) smallest simple statement / header expression / role expression; (b) whole enclosing statement; (c) the enclosing function body | (a) | Probe E3: an unrelated edit in the same function must not change other statements' fingerprints | no |
| OC4 | Ambiguity handling | (a) fingerprint-class uniqueness required; (b) trust the ordinal; (c) add neighbour-statement context to the fingerprint | (a). Refusal beats silent re-targeting | Probe: the count of non-unique fingerprint classes, and the E2 re-target test | **yes** (A-465 amendment: some equivalents are unledgerable and must be fixed by a refactor) |
| OC5 | Code location | (a) `adapters/python.py` (adapter-owned; B109 reuses it); (b) a new `site_anchor.py` | (a) for anchors. The ledger itself goes in a new `src/assay/equivalence_ledger.py` (add it to both `assay.toml` target lists). | B105 target-list rule (`tests/test_self_lane.py:128-135`) | no |
| OC6 | Ledger read path | (a) baseline snapshot; (b) `git show <commit>:path`; (c) worktree | (a). Committed bytes, with no extra git process | A-161 ("ignored or untracked files are not implicit inputs") | no |
| OC7 | Review policy | Free text; a named reviewer plus mandatory adversarial review of each commit that adds entries | Named `reviewer`, plus the plan §10 review covering every added entry explicitly | Estate review rule | **yes** (A-465 amendment) |
| OC8 | Audit execution | (a) P7 selection path, declared command, full; (b) a special cold-off `assay run` | (a) | Reuses the reviewed P7 path; no second executor | no |
| OC9 | Audit binding | (a) recompute the declared-command judge identity; (b) bind commit, tree, argv, assay version and ledger only | (a). Strictly stronger | A trial in P10b: recompute equals the audit's judge on the tiny fixture | no |
| OC10 | Ledger declared, no audit supplied | (a) execute normally, ledger `null`; (b) refuse the lane | (a). Screens stay possible; nothing is claimed | Screen runbook §9.1 | **yes** (A-465 amendment) |
| OC11 | v14 wire | Exactly as V1-V6 | as proposed | Pre-dispatch review of P3a | recorded under A-470 |
| OC12 | Runtime fingerprint of the audit run | (a) not bound, documented limit; (b) bind via a manifest-only capture on the coverage baseline (needs `runtime_fingerprint_sha256` on `coverage_baseline`, plan §5) | (b) **chosen:** the carver added `runtime_fingerprint_sha256` to `coverage_baseline` in plan §5 and in the P3a/P3b briefs on 2026-09-28. | Resolved. | no |

### Invariants (any design must keep them)

1. **Complete inventory.** Ledger candidates are still discovered, listed in `candidate_ids`, and appear exactly once, in `equivalent`.
2. No entry counts as `equivalent` without an accepted, same-commit, identity-bound audit.
3. Resolution is unique by fingerprint class. Anything else refuses; nothing is re-targeted.
4. Anchors and fingerprints are pure functions of the source text and sites: no line numbers, byte offsets, whitespace or comments.
5. SQL `equivalence_artifact` semantics are unchanged.
6. A-464: an audit bucket of `hung` or `budget_exceeded` means refused, never equivalent. Time never decides.
7. Ledger bytes come from the judged commit.

### Tracer-bullet probe (P10a; run once; no gates, no containers)

1. Work in a **scratch clone** of the integration line, never a gate worktree. Run `nice -n 19 ionice -c3 assay plan self-qualification --file assay.toml > $SCRATCH/plan.json`. This is a planner-only step (AST parsing of about 50 files) and runs no tests.
2. `tools/b120_anchor_probe.py --plan plan.json --root .` prototypes `site_anchor` exactly as proposed, over all candidates. Record:
   - the total;
   - the number of unique anchors, which must equal the total;
   - the round-trip parse failures, which must be 0;
   - the role counts (expect about 148 `@decorator` bool-const-flip sites; report the actual count);
   - the ordinal>0 count;
   - the number of non-unique fingerprint classes, and the size of each;
   - per-file maximum ordinals.
3. **Stability.** Make three scratch copies with `git worktree add` under `$SCRATCH` and apply a scripted edit to every candidate-bearing file. Map old sites to new ones through the known inserted byte length (sites after the insertion point shift by +L).
   - **E1:** insert a comment line and a blank line after the import block. Expect 100% identical anchors and fingerprints.
   - **E2:** insert, right after the imports, a new top-level function containing one `==` and one `and`. Expect:
     - function-scoped anchors and all fingerprints unchanged;
     - `<module>`-scope ordinal shifts counted;
     - **no silent re-target** under the eligibility rule.
   - **E3:** change one integer constant in one function per file. Expect only the statements containing that constant to change fingerprint.
   - **E4, a negative control:** swap the operands of one targeted comparison. Its fingerprint **must** change.
4. Write the probe's JSON output, and a short table in the design doc. **If E1 is not 100%, or E4 does not change the fingerprint, the proposed fingerprint is wrong:** revise OC3 before the review.

---

## P10b — Implementation packet (normative, after P10a is accepted)

### Owned interfaces

- **`src/assay/adapters/python.py`:**
  ```python
  @dataclass(frozen=True, kw_only=True)
  class SiteAnchor:
      anchor: str
      fingerprint: str
      qualname: str
      ordinal: int
      ledger_eligible: bool

  def site_anchors(*, path: str, text: str, sites: Sequence[MutationSite]) -> tuple[SiteAnchor, ...]
  ```
  - The result is aligned with `sites`.
  - It is a pure function.
  - P1's `tests/fixtures/dataclass-contract.json` must gain the `SiteAnchor` entry.
- **`src/assay/equivalence_ledger.py`** (new; add to **both** target lists):
  ```python
  @dataclass(frozen=True, kw_only=True)
  class LedgerEntry: ...

  @dataclass(frozen=True, kw_only=True)
  class Ledger:
      path: str
      sha256: str
      lane: str
      entries: tuple[LedgerEntry, ...]

  def load_ledger(data: bytes, *, path: str, lane: str) -> Ledger          # raises LaneConfigError with the field named
  def resolve_ledger(ledger: Ledger, rows: Sequence[Mapping]) -> dict[str, str]   # anchor -> candidate_id; raises LedgerResolutionError
  class LedgerResolutionError(AssayError)                                   # Outcome.ERROR / ReasonCode.BAD_LANE_CONFIG
  ```
- **The audit receipt:** a model plus the schema file `src/assay/schemas/ledger-audit-receipt.schema.json`.
- **CLI:**
  - `assay ledger audit` (a new top-level `ledger` group in `cli.py`);
  - `assay run --equivalence-audit PATH`;
  - `assay plan --json` rows gain `anchor`, `fingerprint` and `ledger_eligible`.
- **Config:** `judge.mutation.equivalence_ledger`, as specified in the design.
- **Runner/mutation plumbing:** the qualifying-consumption flow above, and `equivalence_ledger_sha256` passed into the judge identity (P3b's input).
- **`tools/b105_report_check.py`:** when the report's `judgment.r2.equivalence_ledger` is non-null:
  - `git show <commit>:<project>/<path>` bytes must hash to `sha256`;
  - the parsed entry count must equal `entry_count`;
  - every `ledger` outcome's anchor must appear in that file.

  When the committed ledger has entries but the report's ledger is `null` **and** it has survivors, print a diagnostic. The PASS rule already refuses survivors.

### Decision table (qualifying run)

| Lane ledger | `--equivalence-audit` | Receipt and binding | Result |
|---|---|---|---|
| absent | absent | — | today's behavior |
| absent | given | — | R2 `ERROR`/`BAD_LANE_CONFIG`; no candidate executed as equivalent |
| declared | absent | — | candidates executed normally; `equivalence_ledger: null`; plan event `declared-unaudited` |
| declared | given | accepted, and every binding check equal | ledger candidates not executed; placed in `equivalent` with mode `ledger` |
| declared | given | any mismatch (commit, tree, lane, version, ledger sha, judge, resolved set) or `result != accepted` | R2 `ERROR`/`BAD_LANE_CONFIG`, naming the check; no equivalents |

### Degrees of freedom

- Private helper names.
- The AST traversal strategy inside `site_anchors`, provided the probe's E1-E4 results are reproduced by unit tests.

## Work

**P10a**
1. Build the probe and run it (tracer bullet).
2. Write the design doc, resolving OC1-OC12.
3. Hand the P3a schema fragments to the controller.
4. Obtain a READY review.
5. Commit. Record the A-465 amendments for OC4, OC7 and OC10 as a proposed `decisions.md` row, marked "proposed, pending operator" if the review requests it.

**P10b**
1. Implement and unit-test `site_anchors`, then the ledger loader and resolver, then the config key.
2. Add the plan rows' anchor fields.
3. Implement `assay ledger audit` and the receipt.
4. Implement qualifying consumption and the judge-identity input.
5. Extend the report checker.
6. If P3a did not already implement V1-V6, implement them in `verify.py`, `verdict.py` and the schema **on the v14 branch**, together with their tests.
7. **Gate mode** (added by the carver on 2026-09-28; plan §9.2 and §11.6). This lands as a second commit after P7b merges, because it shares P7b's `case` in the gate script.
   - Add a `b110-ledger-audit` arm to `tools/self-qualification-gate.sh`. After the shared clone, build and venv steps, it runs `"$assay_bin" ledger audit self-qualification --file assay.toml --ledger mutation-equivalence-ledger.toml --state-dir .assay/b110-audit-state --receipt .assay/ledger-audit.json`, captures the exit, prints `B110_LEDGER_AUDIT_EXIT=<n>` and `B110_LEDGER_AUDIT_RECEIPT=.assay/ledger-audit.json`, and exits 0. Results are read from the markers and the receipt. It passes no campaign deadline.
   - Add a `[lanes.b110-ledger-audit]` run-gate lane, shaped like P7b's `b110-screen` (tester-unified, 3 CPU / 2g / 8g, `timeout … 7h30m`, `budget = "8h"`, the receipt as its artifact).
   - The qualifying `self-qualification` arm passes `--equivalence-audit .assay/ledger-audit.json` **only when** the lane declares `judge.mutation.equivalence_ledger`.
   - Extend the `tests/test_self_lane.py` gate-script pins with `b110-ledger-audit`, `B110_LEDGER_AUDIT_EXIT=` and `--equivalence-audit`.
8. Docs, then gates, then the report.

## Oracles (P10b; P10a's oracle is the probe JSON plus the READY review)

| # | Observable | Negative it distinguishes |
|---|---|---|
| O1 | E1-E4 as unit tests on a synthetic module: comment and blank-line insertion keeps anchors and fingerprints; an operand swap changes the fingerprint; an unrelated statement edit keeps the other fingerprints | Position- or text-based fingerprints |
| O2 | A `@dataclass(frozen=True, kw_only=True)` class's flag sites get qualname `<Class>@decorator` | No role awareness |
| O3 | Two identical `if x == 0:` statements in one function: both are `ledger_eligible: false`, and an entry for either is refused `ambiguous` | Ordinal-only resolution |
| O4 | The loader refuses each of: a duplicate anchor; an operator mismatch; a `reason` under 40 characters; an unknown key; a lane mismatch; a non-date `reviewed_on`. Each error names the field. | A permissive parser |
| O5 | The config key is refused on a SQL lane, an ingested lane, and together with `equivalence_artifact` | Scope leak |
| O6 | Real audit on a tiny project (`tests/zz_slow/test_equivalence_ledger_real_runs.py`, `_seed_pytest_mutation`-style): an entry for a mutant the suite cannot kill → receipt `accepted`; an entry for a killable mutant → `refused` with `not-survived`; a bogus anchor → `refused` with `unresolved`. The receipt validates against the schema. | An audit that trusts the ledger |
| O7 | Qualifying run with an accepted receipt: the tracked process runner shows **no** candidate invocation for the ledger candidates; they are in `equivalent` with mode `ledger`; R2 PASS when all others are killed; `assay verify` accepts; `mutation_pct` ignores them | Executing them anyway, or counting them in the score |
| O8 | Binding negatives, each giving R2 `ERROR`/`BAD_LANE_CONFIG` with zero equivalents: a receipt from another commit; a ledger whose `reason` text was edited after the audit (sha changes); a receipt with `result: refused`; a receipt whose `judge_sha256` came from a different lane `env` | Weak binding |
| O9 | Every candidate ledgered → `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` | A-223d regression |
| O10 | Lane ledger declared with no audit flag → candidates execute; survivors stay `survived`; `equivalence_ledger: null` | A silent claim |
| O11 | Verifier negatives, if P10b implements V1-V6: mode `ledger` on `killed`; ledger `null` with native equivalents; `entry_count` mismatch; a bad anchor grammar; `equivalence_artifact` and ledger both present | Verifier gaps |
| O12 | `b105_report_check` refuses a report whose ledger sha differs from `git show` bytes | Self-reported ledger |

### Oracle anti-patterns (AUTHORING.md §3b, pasted verbatim)

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Docs sync

- **README (WHAT):** the native Python equivalence ledger. What an entry claims, that an audit at the same commit is mandatory, and that ledgered candidates are not executed in the qualifying run.
- **DESIGN-GUIDE (WHY):**
  - the middle path (A-465);
  - why anchors, not candidate IDs;
  - the ambiguity rule;
  - why an audit `survived` is consistency evidence, not a proof of equivalence;
  - why the reviewer and reason are mandatory;
  - the interplay with A-209, A-223d and `mutation_pct`;
  - that B109 reuses the anchor.
- **CONSUMERS (HOW):**
  - how to find anchors (`assay plan --json`);
  - a pasteable ledger file;
  - `assay ledger audit`;
  - `assay run --equivalence-audit`;
  - how to read refusals.
- **CHANGES.md** `### Added`; backlog B120 status; plan §9.2 cross-reference.

## Scope / forbid

**P10a touches only:**
- `tools/b120_anchor_probe.py`;
- `nyxloom-trove/reports/assay-B120-P10A-*.{md,json}`;
- a proposed `decisions.md` row.

**P10b touches:**
- `src/assay/adapters/python.py` (anchors only);
- `src/assay/equivalence_ledger.py` (new);
- `src/assay/config.py` (the key);
- `src/assay/cli.py`;
- `src/assay/runner.py` and `src/assay/mutation.py` (the consumption flow and the judge input only);
- `src/assay/schemas/ledger-audit-receipt.schema.json`;
- the V1-V6 verifier, model and schema pieces, **only if P3a did not implement them**;
- `tools/b105_report_check.py`;
- `tools/self-qualification-gate.sh` and `run-gate.toml` (work step 7 only);
- `tests/test_self_lane.py` (gate-script pins only);
- `assay.toml` (target lists);
- `tests/fixtures/dataclass-contract.json`;
- the new tests, the docs, CHANGES, the backlog and the report.

**Forbid:**
- SQL `equivalence_artifact` behavior;
- `judge_mutation` and `mutation_pct` semantics;
- any new `ReasonCode`;
- executing the full B105 campaign;
- editing the B105 lanes to declare a ledger. That happens only after the §9.1 screen, in its own reviewed commit.

## Gate

1. **P10a:** no gate. It is probe and docs only, with `nice`-wrapped planner and probe invocations.
2. **P10b, focused tests serially:** `nice -n 19 ionice -c3 python -m pytest tests/test_equivalence_ledger.py tests/zz_slow/test_equivalence_ledger_real_runs.py tests/test_self_lane.py tests/test_dataclass_contract.py -q -p no:cacheprovider`.
3. `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` (L4).
4. `python ./run-gate.py self-qualification-preflight`.

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file: STOP. Write `BLOCKED: <reason>` to the LOG (`nyxloom-trove/reports/assay-B110-P10-REPORT.md`), commit, and exit. Do NOT improvise a workaround.

**Specific triggers:**
- The probe shows E1 < 100% or E4 unchanged after one OC3 revision.
- P3a merged without the ledger wire fields.
- P7's selection path cannot run the declared command with cold off.
- Recomputing the declared-command judge identity (OC9) is impossible without re-running a baseline.

## Report

Write `nyxloom-trove/reports/assay-B110-P10-REPORT.md` with:
- the P10a decisions and review verdict;
- the probe summary;
- a P10b traceability table (`work | owner | oracle | test | controlled break`) with real names and red-first failure counts;
- the gate verdicts, read separately;
- residuals, including OC12's state.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`

# B110-P10 — Native Python equivalence ledger with stable site anchors (P10a design, P10b implementation, P10c gate mode)

*Revised 2026-09-28 after round-1, round-2 and round-3 reviews (see REVIEW-2026-09-28-round{1,2,3}.md).*
- **Round 3:**
  - P10R3-1: under `--equivalence-audit` the judge identity is computed unconditionally and before placement, and never skipped (O8, no-`--resume` case).
  - Minor 1: resolution runs over all adapter sites of the entry's file, not the discovered subset.
  - Minor 2: the full 4532 tuple is extended, never rewritten.
  - Minor 3: the cap formula includes fixed cost and a fallback factor, and names the survivor-only p90 source.
  - Minor 4: every audit invocation's log is retained.
  - G3-1: P10c's header no longer says "v14 including P10b".
- Round 1 applied P10-1..P10-10, the wrong anchors, and carver decisions C10 and C15.
- Round 2 applies P10R2-1..P10R2-9 and carver decisions C23 (the pinned `run_mutation` order) and C24 (the receipt binds `assay_version`, the wheel digest is recorded only, reuse prints `REUSED`, the audit gets `--resume`, and a measured ledger cap).
- **Plan N-1:** v14 merges back **without** P10b. P10b is cut from `assay-b110-v14` and merged separately, only if the screen leaves ledger candidates.

| Field | P10a (design and probe) | P10b (producer implementation) | P10c (gate mode) |
|---|---|---|---|
| Backlog | B120, plan P10 | B120 | B120 |
| Branch | `assay-b110-p10a-ledger-design`, cut from the integration line | `assay-b110-p10b-ledger`, cut from `assay-b110-v14` **after** v14's P3a–P3d are green. It merges into the integration line **separately**, and **only if** the survivor screen leaves ledger candidates (plan N-1). v14 itself merges back without P10b. | `assay-b110-p10c-ledger-gate`, cut from the **integration line** after P10b **and** P7b have merged into it. Plan §11.6: P7b → {P10c, P9 arms} in either order, whichever is dispatched; the later one rebases. |
| Depends on | nothing; can start on day one | • P10a design **accepted** by a fresh-session review, with OC4, OC7, OC10, OC15 **and the audit-outside-deadline narrowing** **ratified by the operator** in `decisions.md`<br>• **P3a** merged into `assay-b110-v14` with the ledger wire and verifier rules X8–X12<br>• **P3b** (the R2 attempt path, native B145 resource-limit evidence, and combined judge identity `/4`)<br>• **P3d** (P10b replaces P3d's `ledger binding not implemented (B110-P10b)` checker refusal; P10R2-7)<br>• **P6** (plan-digest check; the C23 order) and **P7** (candidate-selection execution path, C5 state records), both in the v14 base | P10b and P7b merged into the integration line |
| Contract class | **2a**: design-bearing, with open choices enumerated below | **2b**: solution fixed by the accepted P10a design | **2c**: bounded gate-script integration |
| Implementer | Opus | Opus | Sonnet |
| Decisions | A-465 (plan D1); A-470 (v14 wire, plan D6); A-464 (time never classifies); A-209 (equivalents need a proof source); carver decisions C10, C23, C24 | same | same |
| Size | M | L | S |

**Design-review routing (B109 deviation, recorded).** B109's acceptance asks for a GPT-6-Luna xhigh design recommendation before a verdict-schema change. Luna/codex/terra are unavailable (memory `assay-carver-terra-handoff`). P10a's review is therefore a **fresh-session Opus xhigh** review, capped at 3 rounds. The design doc records this as a deviation from B109's stated reviewer. It still doubles as B109's independent design review for the shared anchor.

## Why this package exists

**The problem.**
- A passing native R2 campaign may contain only kills (`judge_mutation`, mutation.py 3626-3769).
- A survivor can never pass (A-462).
- Native lanes cannot declare `fail_under` (config.py:457, `_MUTATION_INGESTED_FIELDS`).
- `equivalence_artifact` is SQL-only (config.py 459-472 and 3082-3106).

So a genuinely equivalent Python mutant blocks B105 forever.

**The operator's ruling (A-465), as refined by C10:**
- Such mutants are dispositioned by a **reviewed, committed ledger** keyed by a **stable site anchor**, not by the candidate ID, which changes whenever anything in the file changes.
- Entries land in the existing `equivalent` bucket.
- **Middle path:**
  - The qualifying run does **not** execute ledger candidates.
  - A mandatory **ledger audit** runs first, at the **same commit**, in the non-qualifying screen. It runs each entry through **the same attempt path the qualifying run uses**: the no-cov R2 command with the cold attempt, falling back to the declared command on proof uncertainty (A-470/A2). Each must end `survived` with valid survivor evidence.
  - Any entry that is killed, hung, crashed, budget-exceeded, unresolvable, ambiguous, fingerprint-mismatched or scope-stale is refused.
- **Honest limits.**
  - An audit `survived` is consistency evidence, not a proof of equivalence: a weak suite also "survives".
  - The proof is the reviewed `reason`, bound to the reviewed code by `scope_sha256`. Every entry gets a fresh-session review.
  - The audit runs **outside** the persisted campaign deadline (A-473). This narrows B105's "8-hour ceiling covering … R2" for ledger entries. A-465 records the narrowing, and it is on the **operator-ratification list** together with OC4, OC7, OC10 and OC15 (P10R2-8), because it amends B105's acceptance.

**Shared design with B109.** The anchor is the same one B109 needs: "a semantic site fingerprint and unambiguous structural anchor" (4-backlog.md `## B109`, desired-behavior bullet 2). Design it once for both.

**Timing.** P10a must be **accepted before P3a freezes the v14 schema**, because it contributes the `ledger` execution mode's anchor grammar and `judgment.r2.equivalence_ledger`. P3a has a documented BLOCKED-PARTIAL escape (anchor length-only) if P10a is late.

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`:
   - §3 D1 and D6;
   - §5 (the v14 wire, where `ledger` is `{mode, anchor}` and `equivalence_ledger` is `{path, sha256, entry_count, audit_sha256}`; the judge-identity inputs);
   - §9.1 step 4, §9.2 and §11.6.
2. `nyxloom-trove/reports/b110/REVIEW-2026-09-28-round1.md`: C1 (whole-lane refusal precedent), C10 and C15.
3. `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`:
   - rules **X8–X12**: the ledger pairing split by producer; `ledger` only in `equivalent`; ledger XOR artifact; key presence;
   - the `EquivalenceLedger` model;
   - `MutationExecution` mode `ledger`.
4. `nyxloom-trove/reports/b110/P3b-r2-command-cold-witness.md`: the cold/declared attempt decision table, the `evidence` rules, the `judge_sha256` signature with `equivalence_ledger_sha256`, and `R2CommandProofError` (C1).
5. `nyxloom-trove/decisions.md`: A-458, A-462 (:941), A-464 (:943), and the A-209/A-223d rows. Search for "A-209" and "A-223".
6. `nyxloom-trove/4-backlog.md`: `## B109` (anchor requirements and acceptance, the Luna design-review requirement) and `## B105` acceptance (the complete-inventory rule).
7. `src/assay/adapters/python.py`:
   - operator catalogues **434-478**;
   - `_Site` 480-494;
   - `_compare_swap_sites` 535-567;
   - `_boolop_swap_sites` 570-598;
   - `_bool_const_flip_site` 600-617;
   - `_falsy_swap_site` 651-690;
   - `_candidate_sites` 708-719;
   - `_generate_python_sites` 722-787. It is a full `ast.walk`; `description` has the form `Eq->NotEq`, `True->False`, `And->Or`.
8. `src/assay/mutation.py`:
   - `MutationSite` 320-410;
   - `candidate_id` 1023-1034 (with `candidate_identity.py`);
   - the `_classify_mutant_result_with_equivalence` SQL path 1702-1745;
   - the state-record resume load `_load_validated_state_record` 1273-1402;
   - the `budget_exceeded_mask` is built in the wave loop **2890-2993**, and the bucket assembly is at **3009-3035**;
   - `mutation_pct` 3601-3623;
   - `judge_mutation` 3626-3769 (the `ALL_MUTANTS_EQUIVALENT` branch near the end).
9. `src/assay/runner.py:4525-4545`: the whole-lane refusal re-raise for `InvalidRejudgeIdError` (B094/A-458). Ledger binding failures use the same path (C1 precedent), because an R2-only `BAD_LANE_CONFIG` beside a passing R0 fails `verify._check_r2_rederivation` (`verify.py:2592`) by design: `BAD_LANE_CONFIG` is deliberately absent from `_INDEPENDENT_R2_TERMINALS` (the set at `verify.py:2472`, with its comment just above).
10. `src/assay/config.py`:
    - `_MUTATION_OPTIONAL_FIELDS` 438-450;
    - `_MUTATION_SQL_ONLY_FIELDS` 459-472;
    - the reserved-key refusal ~2946-2952;
    - the SQL `equivalence_artifact` loading 3082-3106;
    - `_validate_artifact_path` (grep for it).
11. `src/assay/verify.py:1283-1304` and `src/assay/verdict.py:5038-5055`: `_check_equivalence_pairing`, which P3a extends (X8).
12. `src/assay/cli.py`: the `plan` parser 330-367 and `_cmd_plan` 1571-1886, where the candidate rows are built (~1820-1840). P8 adds `source_sha256`/`mutated_file_sha256` and the span to the rows (C13).
13. `src/assay/analysis.py:1006`: `_write_new` (never overwrite).
14. `tools/b105_report_check.py:14-147`: the source-bound checker P10b extends (P3d's version is in the base).
15. The P7 brief (`b110/P7-pilot-tooling.md`): `--candidates-file`, the non-qualifying run, R3 not run, exit 6, and C5 (a selection writes state records). The audit reuses that execution path.

---

## P10a — Design packet (2a)

**P10a deliverables:**
1. `nyxloom-trove/reports/assay-B120-P10A-DESIGN.md`: the frozen contract. Resolve every open choice below, and include the exact JSON-Schema fragments P3a must add.
2. `tools/b120_anchor_probe.py`: the tracer-bullet probe, which contains the prototype `site_anchor`.
3. Its output, `nyxloom-trove/reports/assay-B120-P10A-PROBE.json`.
4. A fresh-session design review verdict of **READY**. It routes per the plan §10 review rule (fresh Opus xhigh; see the B109 deviation note above) and is recorded in the design doc. It doubles as B109's independent design review for the shared anchor.
5. A proposed `decisions.md` row carrying these as A-465 amendments, marked **"proposed, pending operator"**:
   - OC4, OC7, OC10 and OC15;
   - **the audit-outside-deadline narrowing of B105's 8-hour ceiling** (P10R2-8);
   - the operational ledger cap (C24; see "Ledger file").

   P10b is **not dispatched** until the operator ratifies them.

### Proposed contract (the carver's recommendation; the review may amend it)

**Canonical AST serialization** (OC14; used by the fingerprint and `scope_sha256`; **never** `ast.dump`). `ast.dump`'s default output changed in Python 3.13 (`show_empty` now defaults to `False`), and `requires-python` is `>=3.11`. The contract therefore owns its serializer, `canon(node)`:

```python
_SKIP_FIELDS = frozenset({"ctx", "type_comment"})
def canon(node) -> str:
    if isinstance(node, ast.AST):
        parts = []
        for name in node._fields:                     # declaration order of the running interpreter
            if name in _SKIP_FIELDS:
                continue
            value = getattr(node, name, None)
            if value is None or value == []:          # omit absent/empty: version-stable (3.12 added type_params=[])
                continue
            parts.append(f"{name}={canon(value)}")
        return f"{type(node).__name__}({','.join(parts)})"
    if isinstance(node, list):
        return "[" + ",".join(canon(v) for v in node) + "]"
    return f"{type(node).__name__}:{node!r}"           # constants: type-tagged repr (True vs 1 differ)
```

- Positions are never read: `lineno`/`col_offset` are `_attributes`, not `_fields`.
- The probe's E5 (below) diffs `canon` output between **python3.13** (`/usr/bin/python3.13`) and **python3.14** (`/usr/local/bin/python3.14`), both present on the host, over every candidate's subject.
- As a belt-and-braces guard, the ledger header records `python = "3.14"` (major.minor of the authoring interpreter). The loader refuses a ledger whose `python` differs from the running interpreter's major.minor, with refusal `interpreter-mismatch`. An interpreter change then forces a deliberate re-audit and header bump instead of silently trusting cross-version fingerprints (OC14).

**Anchor grammar** (one string per candidate; a pure function of `(path, source text, all sites of that file)`):

```
python-site/1:<path>:<qualname>:<operator>:<change>:<ordinal>
```

Components:
- `path`: **exactly the plan row's `path` string**, which is repo-top-relative POSIX, for example `assay/src/assay/base.py` (`mutation.py:451`; P7's selector uses the same form). Never re-derive a project-relative form (P10R2-6).
- `qualname`: the dotted chain of enclosing `ClassDef`/`FunctionDef`/`AsyncFunctionDef` names from the module root, in `__qualname__` style. Nested functions look like `Outer.method.<locals>.inner`. Module level is `<module>`. Lambdas and comprehensions are not scopes. A **role suffix** marks sites outside a body:
  - `@decorator` for sites in a def/class `decorator_list`;
  - `@signature` for argument defaults, keyword defaults and annotations, and returns;
  - `@bases` for class bases and keywords.

  For example, a dataclass flag `@dataclass(frozen=True)` on `class Candidate` has qualname `Candidate@decorator`. Without roles, all 148 dataclass-flag sites would sit in `<module>` and depend on ordinals.
- `operator`: the site's operator, for example `python:compare-swap`.
- `change`: the site's `description`, for example `Eq->NotEq`. It uses AST class names, so it is whitespace-insensitive.
- `ordinal`: the 0-based index among the sites of the same file with equal `(qualname, operator, change)`, ordered by `start_byte`.
- **Encoding:** each component percent-encodes `%` → `%25` and `:` → `%3A`, so `python:compare-swap` becomes `python%3Acompare-swap`. The anchor is ≤ 4096 UTF-8 bytes.
- **Grammar check:** `re.fullmatch(r"python-site/1(:[^:\n]+){5}", anchor)`. Use `fullmatch`, never `match` with `$`, because `$` also matches before a trailing newline. The last component must also match `(0|[1-9][0-9]*)`.

**Semantic fingerprint** (64 hex, versioned):

```
sha256("python-fp/1\0" + canon(subject) + "\0" + target_path + "\0" + change)
```

- `subject` is the smallest of the following that contains the site:
  - a **simple statement**;
  - the **header expression field** of a compound statement: `If.test`, `While.test`, `For.target`/`For.iter`, `With.items`, `Match.subject`, `match_case.guard`, and the `Try`/`TryStar` handler `type`;
  - for role sites, the **specific decorator/default/annotation/base/`type_params` expression**.

  Never a whole compound body. A site whose smallest container is none of these (for example a `match_case.pattern` value) uses the **enclosing statement** header, and the probe reports the count of such fallbacks.
- `target_path` is the field/index path from `subject` down to the mutated node, for example `ops[0]` or `values[1]`.

Positions, comments, blank lines and formatting never enter an anchor or a fingerprint.

**Scope digest** (`scope_sha256`, 64 hex; OC15). It binds the **reviewed code**: the fingerprint alone covers only the smallest statement, while an equivalence `reason` usually depends on surrounding code.

```
sha256("python-scope/1\0" + canon(scope_node))
```

`scope_node` is the smallest enclosing `FunctionDef`/`AsyncFunctionDef`/`ClassDef` node, in full: decorators, signature and body. For a module-level site with no enclosing def or class, it is the top-level statement that contains the site.

Any edit inside that scope changes the digest, and the entry is then refused as `stale-review` until it is re-reviewed and re-recorded. Edits outside the scope keep the entry valid. The `reason` may still depend on callers outside the scope; the per-entry fresh-session review (OC7) must state that dependency, and DESIGN-GUIDE documents it as the ledger's residual.

**Resolution algorithm** (OC13; strict, with no fallback search and no re-targeting). Each entry is resolved against the **current source of its file**, over **all sites the adapter generates for that file**, not the (possibly truncated) plan:
1. **Exact anchor.** Find the site whose current anchor string equals `entry.anchor`. If there is none, refuse `unresolved`.
2. **Fingerprint.** That site's fingerprint must equal `entry.fingerprint`. If not, refuse `fingerprint-mismatch`. This covers ordinal shifts that point the old anchor at a different statement: it is refused, **never** re-targeted to the moved original.
3. **Uniqueness.** The site's fingerprint class `(path, qualname, operator, change, fingerprint)` must have exactly one member among the file's sites. If not, refuse `ambiguous`.
4. **Scope.** The site's `scope_sha256` must equal `entry.scope_sha256`. If not, refuse `stale-review`.
5. **Plan membership.** The site's `candidate_id` must be in the current plan, i.e. not truncated by `max_mutants`. If not, refuse `unresolved`.

The wire anchor is the site's **current** anchor, which equals `entry.anchor` by step 1. **Ledger eligibility** (`ledger_eligible` in plan rows) is true exactly when step 3 holds.

**Ledger file.** Committed TOML at the lane-declared path. It is read from the **baseline snapshot** (the judged commit), never from the worktree.

**The cap has two parts (C24, P10R2-5):**
- **Hard cap:** `MAX_LEDGER_ENTRIES = 200`, enforced by the loader. An empty ledger or more than 200 entries is refused at load.
- **Operational cap (formula revised by round-3 minor note 3):** the P10a design doc records `ledger_cap = min(200, floor((5 h × 3600 − fixed_s) × jobs / (p90_survivor_s × fallback_factor)))`, where:
  - `fixed_s` is the audit invocation's fixed cost: both baselines plus R0/R1, taken from the pilot report §2 (`coverage baseline` + `no-cov R2 baseline` rows);
  - `p90_survivor_s` is the pilot report §3's **survivor-only elapsed p90** column (P7's template, added in round 3), not the per-stratum all-outcome p90;
  - `fallback_factor = 2`, a conservative allowance for an A2 declared-command fallback, which runs a second full suite;
  - `jobs` is the lane's committed `jobs`.

  That is the number of entries whose audit fits **one** 5 h lane invocation, including fixed cost and possible fallbacks. `--resume` lets a larger audit finish across invocations, but the cap keeps a single invocation sufficient.
  - The operator ratifies the operational cap (Deliverable 5).
  - The screen runbook refuses to author more entries than that.
  - Because the audit has a judge-bound `--resume` (below), a larger audit still completes over several invocations without starting over. But it is outside what the operator ratified, and needs a new ratification.

*Valid example:*

```toml
schema = 1
lane = "self-qualification"
python = "3.14"

[[entry]]
anchor = "python-site/1:assay/src/assay/example.py:Window.contains:python%3Acompare-swap:LtE->Lt:0"
fingerprint = "4f7c…(64 hex)"
scope_sha256 = "9a01…(64 hex)"
operator = "python:compare-swap"
reason = "end is exclusive and every caller in this function passes end > start + 1, so <= and < agree on all reachable inputs"
reviewer = "operator"
reviewed_on = 2026-10-05
review_ref = "nyxloom-trove/reports/assay-B120-LEDGER-REVIEW-2026-10-05.md#entry-1"
```

Field rules:
- exactly the keys shown, header and entry;
- `python` matches `^3\.[0-9]+$`;
- `operator` must equal the anchor's operator component;
- `reason` is ≥ 40 characters;
- `reviewer` is 1-128 characters;
- `reviewed_on` is a TOML local date;
- `review_ref` is a repo-relative path plus fragment naming the **fresh-session review record** for this entry (OC7). The loader checks only the shape; the P10b checker extension checks that the file exists at `<commit>`;
- duplicate anchors are refused;
- `lane` must equal the declaring lane.

The file's SHA-256 over its exact bytes is the `ledger_sha256`.

*Invalid example 1:*
```toml
schema = 1
lane = "self-qualification"
python = "3.14"
[[entry]]
anchor = "python-site/1:assay/src/assay/example.py:Window.contains:python%3Acompare-swap:LtE->Lt:0"
fingerprint = "4f7c…"
scope_sha256 = "9a01…"
operator = "python:compare-swap"
reason = "equivalent, trust me"
reviewer = "operator"
reviewed_on = 2026-10-05
review_ref = "x.md#1"
```
It is refused because `reason` is under 40 characters, and there is no proof argument.

*Invalid example 2:* the first entry repeated with the same `anchor` and `operator = "python:boolop-swap"`. It is refused as a duplicate anchor, and the operator does not match the anchor's component.

*Invalid example 3:* a valid entry without `scope_sha256` or `review_ref`. It is refused for missing keys.

*Invalid example 4:* `python = "3.13"` loaded under 3.14. It is refused with `interpreter-mismatch`.

**Lane key.** `judge.mutation.equivalence_ledger = "<project-relative path>"`.
- It is allowed only on a native lane (`format` absent) with `judge.language == "python"`.
- It is refused on SQL lanes (which keep `equivalence_artifact`), on ingested lanes and on other languages.
- A lane never has both keys.
- It is added to `_MUTATION_OPTIONAL_FIELDS`, and it is refused on ingested lanes via `orchestration_only`.
- The lane schema needs no version bump: the key is additive, and older binaries fail closed on unknown keys.

**Judge identity.** Whenever the lane **declares** a ledger, its `ledger_sha256` is an input to `judge_sha256`: in screens, the audit and the qualifying run (plan §5; P3b's `equivalence_ledger_sha256` kwarg). A ledger edit therefore invalidates every stored record of that lane, which is conservative, and it makes the audit's and the qualifying run's identities **directly comparable**.

**Audit command:**

```
assay ledger audit <lane> --file <assay.toml> --ledger <path> --state-dir <DIR> --receipt <PATH> [--resume] [--wheel-sha256 HEX]
```

- **`--resume` (C24, P10R2-5):** judge-bound resume over `--state-dir`, with exactly `assay run --resume` semantics. The audit's C5 state records are keyed by its cold judge identity, so a stale identity re-executes. A re-invocation executes only the entries that have no valid record, so a long audit continues across lane-budget invocations instead of starting over.
- **Signal handling (P10R2-4):** `_cmd_ledger_audit` installs P6's `cli._install_termination_handlers()` exactly as `_cmd_run` does, restoring in `finally`, and only on the main thread (C16). That is what makes the `interrupted` terminal state reachable.

**Static phase.** Nothing executes in this phase.
- `--ledger` must equal the lane's declared path. This cross-check exists because "defaults are hazards".
- Load the ledger from the judged commit (`git show <commit>:<project>/<path>` bytes, the same bytes the baseline snapshot materializes).
- Ledger-level failures give exit 2 and no receipt: schema, keys, the 1–200 entry bound, `python` ≠ the running major.minor, or `--receipt` already existing (`_write_new` never overwrites). With `--resume`, the P10c gate mode moves a non-accepted prior receipt aside first; the state dir, not the receipt, carries the progress.
- Resolve every entry against the judged commit's source with the **strict algorithm** above.
- If any entry is refused, write the receipt with `result: "refused"` and exit 1 **without executing anything**.

**Execution phase.** Run the resolved candidates through **P7's candidate-selection execution path with the cold-witness policy**: the same attempt path the qualifying run uses (A-470/A2; P3b).
- R0/R1 coverage baseline, then the no-cov R2 baseline.
- Per candidate: cold attempt on the R2 command, then the declared command only on proof uncertainty.
- R3 is not run, no verdict is written, and C5 state records go into `--state-dir`.
- The lane `budget` bounds the invocation. The campaign deadline is **not** used: the audit is part of the non-qualifying screen. A-465 records that this narrows B105's "8-hour ceiling covering … R2" for ledger entries.

**Acceptance per entry:** the bucket is `survived` with valid survivor evidence per P3a X5/X6. That means `evidence.command == "r2"` matching the R2 baseline's collection and hook digests, or `"declared"` matching the coverage baseline's (the A2 fallback). Any other bucket gives `refusal: "not-survived"`.

**Terminal states:**

| Situation | `result` | exit |
|---|---|---|
| every entry resolved and survived with valid evidence | `accepted` | 0 |
| any static refusal, or any executed entry not `survived` | `refused` | 1 |
| interrupted (SIGTERM/SIGINT, C3), lane `LANE_TIMEOUT`, coverage baseline not PASS, R2 baseline not PASS, or `R2CommandProofError` (C1) | `incomplete`, with `incomplete_reason` ∈ {`interrupted`, `lane-timeout`, `baseline-failed`, `r2-proof-failed`}. Entries that did not finish have `bucket: null`, `refusal: "not-run"` | 3 |
| argument or ledger-level error | no receipt | 2 |

`incomplete` and `refused` receipts are **never** accepted by the qualifying run.

*Receipt* (`src/assay/schemas/ledger-audit-receipt.schema.json`, `"schema_version": {"const": 1}`; written with `sort_keys=True`):

```json
{"schema_version": 1, "kind": "assay-ledger-audit", "lane": "self-qualification",
 "commit": "<40hex>", "git_tree": "<40hex>", "assay_version": "7.2.0.dev14+g…", "wheel_sha256": "<64hex or null>",
 "python": "3.14",
 "ledger_path": "mutation-equivalence-ledger.toml", "ledger_sha256": "<64hex>", "entry_count": 3,
 "judge_sha256": "<64hex>",
 "r2_collection_sha256": "<64hex>", "r2_runtime_fingerprint_sha256": "<64hex>",
 "coverage_runtime_fingerprint_sha256": "<64hex>",
 "entries": [{"anchor": "python-site/1:…", "candidate_id": "<64hex>", "bucket": "survived",
              "execution_mode": "full", "evidence_command": "r2", "elapsed_s": 512.3, "refusal": null}],
 "result": "accepted", "incomplete_reason": null}
```

Receipt rules:
- `result` is `accepted` only if every entry has `bucket: "survived"`, a non-null `evidence_command` and `refusal: null`.
- An entry refused statically has `candidate_id` (or `null` when `unresolved`), `bucket: null`, `execution_mode: null`, `evidence_command: null`, and `refusal` ∈ {`unresolved`, `fingerprint-mismatch`, `ambiguous`, `stale-review`}.
- `not-survived` and `not-run` are as above.
- `judge_sha256` is the audit run's **own cold identity**. It is computed over the R2 plan and the seven campaign-level identity values of A-470's canonical list, plus the ledger sha, and is **identical** in construction to the qualifying run's identity (plan §5).
- **Binding (C24, P10R2-1).** The receipt binds `assay_version`, `commit` and `git_tree`, exactly as P6's deadline does. `wheel_sha256` is **recorded only, never compared**. The audit and the qualifying run are separate gate invocations that each build their own wheel, and the gate's `pip wheel` is not byte-reproducible (the gate's build sets no `SOURCE_DATE_EPOCH`; `pyproject.toml:1-3` and `gate/distribution/build_release.py:230-268` show reproducibility needs it). The same source commit and `assay_version` identify the judge.
- `r2_collection_sha256` and both runtime fingerprints are copied from this run's `R2Command` baselines (OC12: bound).
- `incomplete_reason` is non-null iff `result == "incomplete"`.

Invalid receipts:
1. `result: "accepted"` with any `refusal` non-null;
2. `len(entries) != entry_count`;
3. `result: "incomplete"` with `incomplete_reason: null`;
4. `evidence_command: "declared"` on an entry whose `execution_mode` is `witness-cold`.

**Qualifying consumption:**

```
assay run <lane> --cold-witness --equivalence-audit <receipt> …
```

**Static refusals** (whole-lane `ERROR`/`BAD_LANE_CONFIG`, before any execution, through the existing pre-run refusal path):
- `--equivalence-audit` passed when the lane declares no ledger;
- `--equivalence-audit` without `--cold-witness`;
- `--equivalence-audit` together with `--shard` or `--candidates-file`: the `len(equivalent) == entry_count` rule is whole-inventory only;
- a receipt that fails its schema;
- `result != "accepted"`;
- a receipt whose `commit`, `git_tree`, `lane`, `assay_version`, `python`, `ledger_path`, `ledger_sha256` or `entry_count` differs from this run's values, where the ledger sha is taken from the committed blob.

**Dynamic binding.** This runs after both baselines and R2 discovery, and **before resume lookup and any candidate submission**. It follows the **C23 order** that P6, P7 and P10 all share in `run_mutation` (P10R2-3):
1. discovery (`job_list`);
2. P6's campaign plan-digest check, over the **full** discovered `job_list`, **including** the ledger candidates. The qualifying run always passes `--campaign-deadline`, and that deadline's `plan_sha256` covers the full plan. Removing the ledger candidates before this check would fail it with `CampaignPlanMismatchError` after the baselines;
3. **ledger placement:**
   1. Resolve the entries with the strict algorithm over **all sites the adapter generates for each entry's file**, exactly as in the "Resolution algorithm" section and O3. It is **not** over the (possibly `changed_lines`-filtered) discovered set, because uniqueness and ordinals would otherwise be computed on the wrong set (round-3 minor note 1). The resolved `candidate_id` set must equal the receipt's, and every resolved ID must also be in the discovered `job_list`, else `LedgerBindingError`.
   2. `receipt.judge_sha256` must equal this run's judge identity, and `r2_collection_sha256` and both runtime fingerprints must equal this run's baselines.
      - **Round-3 P10R3-1:** `run_mutation` today computes the judge identity only `if state_root is not None` (`mutation.py:2353-2364`), and only after the shard step (:2327-2343). `state_root` is set only by `--resume` or `--shard` (`runner.py:4522`), and `--shard` is refused with `--equivalence-audit`. So without `--resume` the identity would not exist at this step, and an `if judge is not None and …` implementation would silently skip the primary OC9 binding.
      - **Under `--equivalence-audit`, compute the judge identity unconditionally and before placement.** It does not depend on selection or shard. The comparison is **never** skipped.
      - A missing identity at this point is itself a `LedgerBindingError`.
   3. **Place before resume.** Remove the ledger candidates from the job list and place them in `equivalent`, with execution `{"mode": "ledger", "anchor": "<current anchor>"}` and no `evidence`. Stored state records for those candidate IDs are **ignored**, never resumed, and counted in the existing `resume` progress event as a new key, `superseded_by_ledger: <n>`. Event names stay closed; keys are free.
4. selection or shard. Both are refused statically with `--equivalence-audit`, so this step is inert here;
5. resume lookup over the remaining jobs.

Add a comment at the block naming the order, the same one P7 adds: `# C23: discovery → campaign digest (full list) → ledger placement → selection/shard → resume`.

Any failure in steps 3.1–3.2 raises a new `LedgerBindingError(AssayError)` (`ERROR`/`BAD_LANE_CONFIG`). It is re-raised through the **whole-lane refusal path** at `runner.py:4532`, exactly like `InvalidRejudgeIdError` and P3b's `R2CommandProofError` (C1, A-458).
- **Extend, never rewrite, the 4532 tuple** (P3B2-7; round-3 minor note 2).
- In this base it already reads `except (mutation.InvalidRejudgeIdError, <P6's CampaignPlanMismatchError>, mutation.R2CommandProofError, mutation.R2ManifestWriteError, mutation.R2BaselineTimeoutError): raise`.
- P10b appends `LedgerBindingError`, giving the full tuple `(InvalidRejudgeIdError, CampaignPlanMismatchError, R2CommandProofError, R2ManifestWriteError, R2BaselineTimeoutError, LedgerBindingError)`. Earlier R0/R1 measurements are discarded. An R2-only `BAD_LANE_CONFIG` beside a passing R0 would fail `verify` by design. **No candidate is counted `equivalent`.**

**Additionally:**
- `judgment.r2.equivalence_ledger = {path, sha256, entry_count, audit_sha256}`, where `audit_sha256` is the sha256 of the receipt bytes.
- **The lane declares a ledger but no `--equivalence-audit` is passed** (screens):
  - the ledger is ignored for classification, and its candidates execute normally;
  - the verdict carries `equivalence_ledger: null`;
  - the progress `plan` event carries `"equivalence_ledger": "declared-unaudited"`;
  - the ledger sha is still in the judge identity.
- The `plan` event's `equivalence_ledger` value is exactly one of `null` (none declared), `"declared-unaudited"`, or `"audited"`.

**Trust model (stated honestly; also in DESIGN-GUIDE).**
- The qualifying run checks the receipt against everything it can recompute: commit, tree, version, ledger bytes, resolution, judge identity, collection digest and fingerprints.
- It **cannot** detect a hand-forged receipt whose buckets say `survived` for runs that never happened. The trust root is the **gate-produced** receipt:
  - P10c's `b110-ledger-audit` mode;
  - the gate log **of the invocation that actually ran the audit**, with its `B110_LEDGER_AUDIT_EXIT=0` marker;
  - the retained audit state dir.
- **Reuse (C24, P10R2-2).** A later gate invocation that finds an existing accepted receipt prints `B110_LEDGER_AUDIT_REUSED=1` and **never** `B110_LEDGER_AUDIT_EXIT=0`. A reuse marker proves nothing about an in-gate audit, so the qualifying runbook (plan §9.2/§9.3) retains the **original** audit invocation's gate log next to the receipt. A receipt with no retained original `EXIT=0` log is not trusted.
- **Multi-invocation audits (round-3 minor note 4).** With `--resume`, the invocation that finally prints `EXIT=0` may have executed **no** entries: all were resumed from earlier invocations' records. The runbook therefore retains **every** audit invocation's gate log, not only the last, and cites them all with the receipt.
- The B105 checker extension re-validates the receipt against source. The qualifying runbook (plan §9.3) retains all of it.

**v14 wire and verifier rules.** **P3a implements these** as rules **X8–X12** in the schema, the model and `verify.py`:
- the ledger pairing is split by producer: ledger → all equivalents `ledger`, with `len == entry_count`; artifact → no `ledger` modes; neither → empty;
- `ledger` appears only in `equivalent`;
- a ledger and an artifact are mutually exclusive;
- native key presence.

P10a contributes only the anchor grammar (`fullmatch` above) and the `equivalence_ledger` field constraints (`path` project-relative POSIX without `..`, two 64-hex digests, `entry_count` an int in 1–200, bool rejected). P10b does **not** touch `verify.py`, `verdict.py` or the verdict schema. If X8–X12 are missing from the base, that is BLOCKED.

There is no change to `judge_mutation`:
- equivalents stay out of `mutation_pct`'s denominator (3601-3623);
- `killed + survived == 0` with a non-empty `equivalent` still gives `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT`.

**Plan exposure.** `assay plan` candidate rows gain `anchor`, `fingerprint`, `scope_sha256` and `ledger_eligible` (bool). Authors copy these into the ledger; nobody writes them by hand.

### Open choices

| # | Choice | Admissible options | Recommended | Deciding evidence | Needs a D-decision? |
|---|---|---|---|---|---|
| OC1 | Anchor encoding | (a) `:` with percent-encoding; (b) `\|` separator; (c) structured TOML fields with no string form | (a). One wire string, and exact | Round-trip property on all 3,760 anchors in the probe | no |
| OC2 | Qualname and roles | (a) lexical `__qualname__` plus a role suffix; (b) evaluation scope (decorators belong to the outer scope); (c) no roles | (a) | Probe: the 148 dataclass-flag sites get `@decorator`; ordinal>0 rate with (a) versus (c) | no |
| OC3 | Fingerprint subject | (a) smallest simple statement / header expression (including `match_case.guard`, `TryStar` handler types) / role expression (including `type_params`); (b) whole enclosing statement; (c) the enclosing function body | (a), with the enclosing-statement fallback counted | Probe E3: an unrelated edit in the same function must not change other statements' fingerprints. Probe: the count of fallback subjects. **Staleness is not measured by the fingerprint**; it is covered by `scope_sha256` (OC15) | no |
| OC4 | Ambiguity handling | (a) fingerprint-class uniqueness required; (b) trust the ordinal; (c) add neighbour-statement context to the fingerprint | (a). Refusal beats silent re-targeting | Probe: the count of non-unique fingerprint classes, and E2(b)/(c) | **yes** (A-465 amendment: some equivalents are unledgerable and must be fixed by a refactor). **Operator ratification required before P10b dispatch** |
| OC5 | Code location | (a) `adapters/python.py` (adapter-owned; B109 reuses it); (b) a new `site_anchor.py` | (a) for anchors and `canon`. The ledger itself goes in a new `src/assay/equivalence_ledger.py` (add it to **both** `assay.toml` target lists). | B105 target-list rule (`tests/test_self_lane.py:128-135`) | no |
| OC6 | Ledger read path | (a) baseline snapshot / committed blob; (b) `git show <commit>:path`; (c) worktree | (a). Committed bytes. The static phase reads the committed blob through the prepared seed; the bytes are identical to the baseline snapshot's | A-161 ("ignored or untracked files are not implicit inputs") | no |
| OC7 | Review policy | (a) free text; (b) a named reviewer plus a **fresh-session review record per entry** (`review_ref`), plus the plan §10 review of every commit that adds entries | (b) | Estate review rule; C10 | **yes** (A-465 amendment). **Operator ratification required before P10b dispatch** |
| OC8 | Audit execution | (a) P7 selection path with the **cold-witness attempt path** (R2 command, then declared on uncertainty), the same as the qualifying run; (b) the declared command only, full; (c) a special `assay run` mode | (a) (C10). The audit checks survival under exactly the command that qualifies | Reuses the reviewed P7 and P3b paths; no second executor | no |
| OC9 | Audit binding | (a) the audit's cold judge identity **equals** the qualifying run's (both include the ledger sha); (b) bind commit, tree, argv, assay version and ledger only | (a), plus `assay_version`/commit/tree. The wheel digest is recorded only (C24). Strictly stronger, and needs no recomputation of a foreign identity | A P10b trial: audit and qualifying identities are equal on the tiny fixture **across different install and state roots** (two gate invocations; P10R2-9), and differ when the lane `env` differs | no |
| OC10 | Ledger declared, no audit supplied | (a) execute normally, ledger `null`; (b) refuse the lane | (a). Screens stay possible; nothing is claimed | Screen runbook §9.1 | **yes** (A-465 amendment). **Operator ratification required before P10b dispatch** |
| OC11 | v14 wire | P3a's X8–X12 plus the `equivalence_ledger` field constraints | as proposed | Pre-dispatch review of P3a | recorded under A-470 |
| OC12 | Runtime fingerprint of the audit run | (a) not bound, documented limit; (b) bound | **(b), wired concretely.** The audit runs the cold path, so its R2 baseline's runtime fingerprint is inside `judge_sha256` (OC9). The receipt also carries `r2_runtime_fingerprint_sha256` and `coverage_runtime_fingerprint_sha256` from its `R2Command` baselines (both required on both baselines per P3a). The qualifying run compares all three to its own | no |
| OC13 | Resolution algorithm | (a) strict: exact anchor → fingerprint equality → class uniqueness over the file's sites → scope → plan membership, refusing on any failure; (b) fall back to a fingerprint-class search when the exact anchor misses | (a). (b) re-targets after ordinal shifts, which P10-4 forbids | Probe E2(b)/(c) | no |
| OC14 | Cross-interpreter stability | (a) the `canon` serializer omitting `None`/`[]` fields; (b) pin the authoring interpreter (`python` header, loader refusal); (c) `ast.dump` | (a) **and** (b). (c) is rejected: `ast.dump` output changed in 3.13 | Probe E5 on python3.13 versus python3.14 | no |
| OC15 | Scope binding (`scope_sha256`) | (a) smallest enclosing def/class node, or the top-level statement at module level; (b) the whole module; (c) none | (a). (b) makes any edit in the file stale; (c) leaves `reason` unbound to the reviewed code | Probe E6 | **yes** (A-465 amendment, C10). **Operator ratification required before P10b dispatch** |
| OC16 | Receipt re-run policy | (a) `_write_new`; a commit-bound path; the gate mode moves a non-accepted receipt aside and re-runs with `--resume`; it reuses an accepted receipt with the `REUSED` marker, never `EXIT=0` (C24); (b) overwrite | (a) | P10c | no |
| OC17 | Operational ledger cap (C24) | (a) `min(200, floor((5 h × 3600 − fixed_s) × jobs / (p90_survivor_s × 2)))`, from the pilot report's §2 fixed costs and §3 survivor-only elapsed p90 (round-3 minor note 3); (b) the hard cap of 200 only | (a) | The pilot report's survivor-only elapsed p90 and fixed baseline costs | **yes** (operator ratification with Deliverable 5) |

### Invariants (any design must keep them)

1. **Complete inventory.** Ledger candidates are still discovered, listed in `candidate_ids`, and appear exactly once, in `equivalent`.
2. No entry counts as `equivalent` without an accepted, same-commit, identity-bound audit.
3. **Resolution is strict** (OC13). Anything else refuses; nothing is re-targeted.
4. Anchors, fingerprints and scope digests are pure functions of the source text and sites under the **`canon` serializer**: no line numbers, byte offsets, whitespace or comments. They are stable across the interpreters the probe checks (E5), and the `python` header guards the rest.
5. SQL `equivalence_artifact` semantics are unchanged (P3a X8(b)).
6. A-464: an audit bucket of `hung` or `budget_exceeded`, or an `incomplete` audit, means refused, never equivalent. Time never decides.
7. Ledger bytes come from the judged commit.
8. **Scope binding.** An entry is valid only while the code its review covered (`scope_sha256`) is unchanged.
9. **Honest trust.** The receipt's executed buckets are trusted as gate-produced evidence. This residual is documented, never hidden.

### Tracer-bullet probe (P10a; run once; no gates, no containers)

1. Work in a **scratch clone** of the integration line, never a gate worktree. Run `nice -n 19 ionice -c3 assay plan self-qualification --file assay.toml > $SCRATCH/plan.json`. This is a planner-only step (AST parsing of about 50 files) and runs no tests.
2. `tools/b120_anchor_probe.py --plan plan.json --root .` prototypes `site_anchor`, `canon`, the fingerprint and `scope_sha256` exactly as proposed, over all candidates. Record:
   - the total;
   - the number of unique anchors, which must equal the total;
   - the round-trip parse failures, which must be 0;
   - the role counts (expect about 148 `@decorator` bool-const-flip sites; report the actual count);
   - the ordinal>0 count;
   - the number of non-unique fingerprint classes, and the size of each;
   - per-file maximum ordinals;
   - the count of fingerprint-subject fallbacks (OC3).
3. **Stability.** Make scratch copies with `git worktree add` under `$SCRATCH` and apply scripted edits. Map old sites to new ones through the known inserted byte length (sites after the insertion point shift by +L).
   - **E1:** insert a comment line and a blank line after the import block of every candidate-bearing file. Expect 100% identical anchors, fingerprints and scope digests.
   - **E2, re-aimed** (P10-4; the dangerous seams are **module-level** ordinals and look-alike statements):
     - **(a)** in a file with module-level sites, insert a new module-level statement `_PROBE = 0 == 0` **before** the existing module-level sites. Expect the new site to take ordinal 0 of its `(qualname=<module>, compare-swap, Eq->NotEq)` class. Every pre-existing entry for that class must resolve **only if** its exact anchor still points at a site with its fingerprint. Report how many would now refuse `fingerprint-mismatch`. **Zero may be re-targeted.**
     - **(b)** duplicate one eligible statement verbatim in the same function. Expect both copies `ledger_eligible: false` and any entry for them refused `ambiguous`.
     - **(c)** insert, before an entry's statement, a **different-shape** statement with the same scope, operator and change. Expect the old anchor to point at the new statement and the entry refused `fingerprint-mismatch`, **never** re-targeted to the original.
   - **E3:** change one integer constant in one function per file. Expect only the statements containing that constant to change fingerprint, and that function's `scope_sha256` to change.
   - **E4, a negative control:** swap the operands of one targeted comparison. Its fingerprint **must** change.
   - **E5, cross-interpreter** (OC14): run the probe's anchor, fingerprint and scope computation under `/usr/bin/python3.13` and under `/usr/local/bin/python3.14` on the same tree, and diff. Expect 0 differences. Any difference is listed per node type, and the `canon` rule must be revised before the review. Report `ast.dump` differences too, for the record, to justify rejecting OC14(c).
   - **E6, scope** (OC15): an edit in a sibling function leaves the entry's `scope_sha256` unchanged; an edit elsewhere in the entry's own function changes it.
4. Write the probe's JSON output, and a short table in the design doc. **If E1 is not 100%, E4 does not change the fingerprint, E5 shows differences, or E2 shows any re-target, the proposal is wrong:** revise it before the review.

---

## P10b — Implementation packet (normative, after P10a is accepted)

### Owned interfaces

- **`src/assay/adapters/python.py`:**
  ```python
  @dataclass(frozen=True, kw_only=True)
  class SiteAnchor:
      anchor: str
      fingerprint: str
      scope_sha256: str
      qualname: str
      ordinal: int
      ledger_eligible: bool

  def canon(node: ast.AST | list | object) -> str          # the OC14 serializer, exactly as specified
  def site_anchors(*, path: str, text: str, sites: Sequence[MutationSite]) -> tuple[SiteAnchor, ...]
  ```
  - The result is aligned with `sites`.
  - It is a pure function.
  - `ledger_eligible` is the OC13 step-3 uniqueness over **all** `sites` of the file.
- **`src/assay/equivalence_ledger.py`** (new; add to **both** `assay.toml` target lists, keeping the 100% line+branch floor):
  ```python
  @dataclass(frozen=True, kw_only=True)
  class LedgerEntry:
      anchor: str; fingerprint: str; scope_sha256: str; operator: str
      reason: str; reviewer: str; reviewed_on: datetime.date; review_ref: str

  @dataclass(frozen=True, kw_only=True)
  class Ledger:
      path: str
      sha256: str
      lane: str
      python: str
      entries: tuple[LedgerEntry, ...]           # 1 <= len <= 200

  MAX_LEDGER_ENTRIES = 200
  def load_ledger(data: bytes, *, path: str, lane: str) -> Ledger
      # raises LaneConfigError naming the field; refuses empty / >200 / python mismatch
  def resolve_ledger(ledger: Ledger, anchors_by_file: Mapping[str, Sequence[SiteAnchor]],
                     plan_ids: Mapping[str, str]) -> tuple[ResolvedEntry, ...]
      # OC13 strict; per-entry refusal in ResolvedEntry.refusal
  class LedgerBindingError(AssayError)      # Outcome.ERROR / ReasonCode.BAD_LANE_CONFIG; re-raised to whole-lane refusal
  ```
  `plan_ids` maps each anchor to a `candidate_id`, for candidates in the plan only.
- **The audit receipt:** a model plus the schema file `src/assay/schemas/ledger-audit-receipt.schema.json`, exactly the shape above.
- **CLI:**
  - `assay ledger audit` (a new top-level `ledger` group in `cli.py`);
  - `assay run --equivalence-audit PATH`, which requires `--cold-witness`;
  - `assay plan` rows gain `anchor`, `fingerprint`, `scope_sha256` and `ledger_eligible` (additive keys, beside P8's C13 row fields).
- **Config:** `judge.mutation.equivalence_ledger`, as specified in the design.
- **Runner/mutation plumbing:**
  - the static and dynamic qualifying-consumption flow above;
  - `LedgerBindingError` **appended** to the existing `runner.py:4532` whole-lane re-raise tuple (extend, never rewrite). The full tuple is `(InvalidRejudgeIdError, CampaignPlanMismatchError, R2CommandProofError, R2ManifestWriteError, R2BaselineTimeoutError, LedgerBindingError)`;
  - under `--equivalence-audit`, the judge identity is computed unconditionally and before placement, and the OC9 comparison is never skipped (round-3 P10R3-1);
  - `equivalence_ledger_sha256` passed into the judge identity whenever the lane declares a ledger (P3b's input);
  - ledger placement **before** resume lookup, and `superseded_by_ledger` in the `resume` event.
- **`tools/b105_report_check.py`** (P3d's version is in the base). When the report's `judgment.r2.equivalence_ledger` is non-null, the checker requires `--ledger-audit PATH` and replaces P3d's `ledger binding not implemented (B110-P10b)` refusal with these checks, each with its own substring:
  - **L1:** the committed lane's `judge.mutation.equivalence_ledger` (`git show <commit>:assay/assay.toml`) equals `equivalence_ledger.path` (`ledger path differs from the committed lane key`);
  - **L2:** `git show <commit>:assay/<path>` bytes hash to `sha256`, and the parsed entry count equals `entry_count` (`ledger bytes differ from the commit`);
  - **L3:** every `ledger` outcome's anchor appears in that file (`ledger outcome anchor not in the committed ledger`);
  - **L4:** every entry's `review_ref` file exists at `<commit>` (`ledger review record missing at the commit`);
  - **L5:** `ledger audit receipt does not bind this report` when any of these fail:
    - `sha256(receipt bytes) == audit_sha256`;
    - the receipt validates;
    - `result == "accepted"`;
    - the receipt's `commit`/`git_tree`/`lane`/`assay_version` equal the expected values. Its `wheel_sha256` is **not** compared (C24, P10R2-1): the audit's wheel is a separate, non-reproducible build;
    - its entries' `candidate_id` set equals the report's `ledger`-mode candidate IDs;
    - its `r2_collection_sha256` and `r2_runtime_fingerprint_sha256` equal the report's `r2_command.r2_baseline` values.
  - The verdict carries **no** `judge_sha256` (verified: 0 occurrences in `verdict.py` and the schema). The judge-identity equality is therefore enforced **only in-process**, by the qualifying run's dynamic binding. The checker does not claim it. DESIGN-GUIDE records this split.

  When the committed ledger has entries but the report's ledger is `null` **and** the report has survivors, print a diagnostic. The PASS rule already refuses survivors.

### Decision table (qualifying run)

| Lane ledger | Flags | Receipt and binding | Result |
|---|---|---|---|
| absent | no `--equivalence-audit` | — | today's behavior (the ledger sha is not in the identity) |
| absent | `--equivalence-audit` | — | static whole-lane `ERROR`/`BAD_LANE_CONFIG`; nothing executed |
| declared | no `--equivalence-audit` | — | candidates executed normally; `equivalence_ledger: null`; plan event `declared-unaudited`; the ledger sha **is** in the identity |
| declared | `--equivalence-audit` without `--cold-witness`, or with `--shard`/`--candidates-file` | — | static whole-lane refusal; nothing executed |
| declared | `--equivalence-audit --cold-witness` | static receipt checks fail (schema, `result`, commit, tree, lane, version, python, ledger path/sha/count) | static whole-lane refusal before any baseline |
| declared | same | static checks pass; dynamic binding fails (resolution, set, judge identity, collection digest, fingerprints) | `LedgerBindingError` → whole-lane refusal after the baselines; R0/R1 discarded (A-458 precedent); no equivalents |
| declared | same | every check passes | ledger candidates not executed, stored records for them ignored (`superseded_by_ledger`), placed in `equivalent` with mode `ledger`; `equivalence_ledger` set; plan event `audited` |
| declared | same, plus `--campaign-deadline` whose `plan_sha256` covers the **full** plan | every check passes | as above. P6's digest check runs **before** placement (C23) and passes |
| declared | same, plus a deadline whose `plan_sha256` was computed over the plan **minus** the ledger candidates | — | P6's `CampaignPlanMismatchError`, a whole-lane refusal. The digest is over the full list, and placement never runs first |

### Degrees of freedom

- Private helper names.
- The AST traversal strategy inside `site_anchors`, provided the probe's E1–E6 results are reproduced by unit tests and `canon` matches the specification exactly.

## Work

**P10a**
1. Build the probe and run it (tracer bullet). The probe imports nothing from `assay`: it parses the plan JSON and the source with the standard library, so it runs under python3.13 and python3.14 for E5.
2. Write the design doc, resolving OC1–OC17, with the probe table. OC17's value is computed from the pilot report once it exists; until then, record the formula.
3. Hand the P3a anchor-grammar and `equivalence_ledger` field constraints to the controller.
4. Obtain a READY review (fresh-session Opus xhigh; record the B109 deviation).
5. Commit the proposed `decisions.md` row for OC4, OC7, OC10, OC15, OC17 and the audit-outside-deadline narrowing, marked "proposed, pending operator". **Stop.** P10b is dispatched only after the operator ratifies it.

**P10b** (off `assay-b110-v14`)
1. Check the base: `grep -n "X8\|equivalence_ledger" src/assay/verify.py` finds P3a's rules, and `grep -n "R2CommandProofError" src/assay/runner.py` finds P3b's re-raise. If either is missing → BLOCKED.
2. Implement and unit-test `canon`, `site_anchors` and `scope_sha256`, then the ledger loader and resolver, then the config key.
3. Add the plan rows' anchor fields.
4. Implement `assay ledger audit` and the receipt, including:
   - the static phase;
   - the terminal states;
   - `_write_new`;
   - judge-bound `--resume`;
   - P6's termination handlers.
5. Implement qualifying consumption (the static and dynamic binding in the **C23 order**, with P6's digest before placement, and placement before resume) and the judge-identity input.
6. Extend the report checker (L1–L5 and `--ledger-audit`).
7. Update `tests/fixtures/dataclass-contract.json` with P1's documented regeneration command (C15) for `SiteAnchor`, `LedgerEntry`, `Ledger` and any other new dataclass.
8. Docs, then gates, then the report.

**P10c** (off the integration line, after v14, P10b (merged separately from v14, and only if the ledger is non-empty) and P7b have merged)
1. Add a `b110-ledger-audit` arm to `tools/self-qualification-gate.sh`'s lane `case`, after the shared clone, build and venv steps, in the same block structure P7b uses for `b110-screen`. It does the following:
   ```bash
   receipt=".assay/ledger-audit-${source_commit:0:12}.json"
   ledger_path="$("$assay_bin" lanes --file assay.toml --json \
     | "$scratch/run-venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); l=next(x for x in d["lanes"] if x["name"]=="self-qualification"); print((l["mutation"] or {}).get("equivalence_ledger") or "")')"
   [[ -n "$ledger_path" ]] || { echo "B110_LEDGER_AUDIT_EXIT=2"; echo "B110_LEDGER_AUDIT_ERROR=no-ledger-declared"; exit 0; }
   if [[ -f "$receipt" ]]; then
     prior="$("$scratch/run-venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$receipt")"
     if [[ "$prior" == "accepted" ]]; then
       # C24 / P10R2-2: a reuse is NOT an in-gate audit; never print EXIT=0 here
       echo "B110_LEDGER_AUDIT_REUSED=1"; echo "B110_LEDGER_AUDIT_RECEIPT=$receipt"; exit 0
     fi
     mv -- "$receipt" "$receipt.$(date -u +%Y%m%dT%H%M%SZ).prev"
   fi
   set +e
   "$assay_bin" ledger audit self-qualification --file assay.toml --ledger "$ledger_path" --resume \
     --state-dir ".assay/b110-audit-state-${source_commit:0:12}" --receipt "$receipt" --wheel-sha256 "$wheel_digest"
   audit_status=$?
   set -e
   echo "B110_LEDGER_AUDIT_EXIT=$audit_status"
   echo "B110_LEDGER_AUDIT_RECEIPT=$receipt"
   exit 0      # results are read from the markers and the receipt
   ```
   - The JSON path is the `assay lanes --json` shape at `cli.py:1972-2060`: `lanes` is a **list** of entries with `name`, and `mutation` is `MutationConfig.as_declared()` or `null`. It echoes `equivalence_ledger` only when declared, because P10b adds it to `as_declared`. Pin this in a `tests/test_cli_lanes_json.py` case.
   - It passes no campaign deadline.
   - It always passes `--resume`, so a re-invocation continues from `.assay/b110-audit-state-<commit12>` (C24).
   - **Runbook (C24; round-3 minor note 4).** The controller retains **every** audit invocation's gate log next to the receipt, including the one that printed `B110_LEDGER_AUDIT_EXIT=0` and every earlier `--resume` invocation that executed entries, and cites them in the qualifying evidence. A `REUSED` log never replaces them.
2. In `run_and_verify_lane`'s `self-qualification` arm, replace P3d's `# B110-P10c:` marker. When the same `assay lanes --json` query yields a non-empty ledger path, append `--equivalence-audit ".assay/ledger-audit-${source_commit:0:12}.json"` to `lane_flags` and pass `--ledger-audit` with the same path to the checker. Otherwise pass neither.
3. Add a `[lanes.b110-ledger-audit]` run-gate lane, shaped like P7b's `b110-screen`:
   - tester-unified, 3 CPU / 2g / 8g;
   - `timeout … 7h30m`, `budget = "8h"`;
   - artifacts: the receipt glob and the audit state dir;
   - a comment citing A-465 and "non-qualifying; outside the campaign deadline".
4. Extend the `tests/test_self_lane.py` gate-script pins with:
   - `b110-ledger-audit`, `B110_LEDGER_AUDIT_EXIT=`, `B110_LEDGER_AUDIT_REUSED=1`, `ledger-audit-${source_commit:0:12}.json`, `--resume` and `--equivalence-audit`;
   - a negative pin: the accepted-receipt branch contains no `B110_LEDGER_AUDIT_EXIT=0`;
   - an ordering pin: the accepted-receipt short-circuit precedes the audit command;
   - the stub-binary pattern from P3d, extended with a declared-ledger lane JSON: the qualifying run argv carries `--equivalence-audit` exactly when a ledger is declared.

## Oracles (P10b and P10c; P10a's oracle is the probe JSON plus the READY review)

| # | Observable | Negative it distinguishes |
|---|---|---|
| O1 | E1–E4 and E6 as unit tests on a synthetic module: comment and blank-line insertion keeps anchors, fingerprints and scope digests; an operand swap changes the fingerprint; an unrelated statement edit keeps the other fingerprints; a sibling-function edit keeps `scope_sha256`, an own-function edit changes it | Position- or text-based fingerprints; unbound scope |
| O1b | `canon` omits `None`/`[]` fields, skips `ctx`/`type_comment`, and type-tags constants: `canon(ast.parse("x = True"))` ≠ `canon(ast.parse("x = 1"))`, and `FunctionDef` with `type_params=[]` equals the same node without the attribute | `ast.dump` reuse; untagged constants |
| O2 | A `@dataclass(frozen=True, kw_only=True)` class's flag sites get qualname `<Class>@decorator` | No role awareness |
| O3 | Two identical `if x == 0:` statements in one function: both are `ledger_eligible: false`, and an entry for either is refused `ambiguous`. Uniqueness is computed over the file's sites even when `max_mutants` truncates the plan | Ordinal-only resolution; uniqueness over the truncated plan |
| O3b | Ordinal shift: an entry recorded before a **different-shape** statement with the same (qualname, operator, change) is inserted ahead of it → `fingerprint-mismatch`, and the moved original is **not** selected | Re-targeting by fingerprint search |
| O4 | The loader refuses each of: a duplicate anchor; an operator mismatch; a `reason` under 40 characters; an unknown key; a missing `scope_sha256`/`review_ref`; a lane mismatch; a non-date `reviewed_on`; an empty ledger; 201 entries; `python` ≠ the running major.minor; an anchor with a trailing `\n` (`fullmatch`). Each error names the field | A permissive parser |
| O4b | Scope staleness: edit a line inside the entry's function (not the subject statement) → the entry is refused `stale-review` | Fingerprint-only binding |
| O5 | The config key is refused on a SQL lane, an ingested lane, and together with `equivalence_artifact` | Scope leak |
| O6 | Real audit on a tiny project (`tests/zz_slow/test_equivalence_ledger_real_runs.py`, `_seed_pytest_mutation`-style), **with the cold path**:<br>• an entry for a mutant the suite cannot kill → receipt `accepted`, `evidence_command` `r2`;<br>• an entry for a killable mutant → `refused` with `not-survived`;<br>• a bogus anchor → `refused` with `unresolved`, **and zero candidate executions** (static phase);<br>• the receipt validates against the schema, and its `judge_sha256` equals a subsequent qualifying run's identity;<br>• **P10R2-9:** the audit runs with one state dir and one venv/install root (a copy of the project under `tmp_path/a`), and the qualifying run with **different** ones (`tmp_path/b`, a different `--state-dir`). The receipt's `judge_sha256`, `r2_collection_sha256` and runtime fingerprints still equal the qualifying run's, as two gate invocations would need (P3b's purelib-relative and `<anon>` normalization) | An audit that trusts the ledger; an audit run in a different mode; path-dependent identities |
| O6b | Audit terminal states: a baseline that fails → `incomplete` / `baseline-failed`, exit 3; an injected LANE_TIMEOUT → `incomplete` / `lane-timeout` with `not-run` entries; a second audit on an existing receipt path → exit 2, and the first receipt is unchanged | Missing terminal states; overwrite |
| O6c | `--resume` (C24): an audit interrupted after entry 1 of 2 (`incomplete`); a second invocation with `--resume`, the same state dir and a new receipt path executes **only** entry 2 (counted from `candidate` progress events), and gives `accepted`. With a different ledger sha, the identity changes and both re-execute | An audit that restarts from zero; a resume that ignores the identity |
| O6d | `interrupted` (P10R2-4): `runner.request_termination()` set before `main(["ledger", "audit", …])` in-process gives `incomplete` / `interrupted`, exit 3. The installer is called on the main thread (spy on `cli._install_termination_handlers`) | No handler in `ledger audit` |
| O7 | Qualifying run with an accepted receipt: the tracked process runner shows **no** candidate invocation for the ledger candidates; they are in `equivalent` with mode `ledger`; R2 PASS when all others are killed; `assay verify` accepts; `mutation_pct` ignores them | Executing them anyway, or counting them in the score |
| O7b | Resume interplay: an unaudited screen run in the same state dir wrote `survived` records for the ledger candidates; the audited qualifying `--resume` places them as `ledger` equivalents, reports `superseded_by_ledger: n`, and never resumes those records | Resume before placement |
| O7c | **C23 with a deadline (P10R2-3).** In-process `campaign init` (P6) over the full plan, then `assay run --cold-witness --equivalence-audit R --campaign-deadline D` → runs, ledger candidates placed, R2 PASS. A second deadline file whose `plan_sha256` was computed over the plan **minus** the ledger candidates → a whole-lane `CampaignPlanMismatchError` refusal, and no candidate is placed or executed | Placement before P6's digest check: the full-plan deadline is refused, and the reduced one is accepted |
| O8 | Binding negatives, each a **whole-lane** `ERROR`/`BAD_LANE_CONFIG` with zero equivalents and `assay verify` `[]` on the refusal verdict:<br>• static: a receipt from another commit; `result: refused`; `result: incomplete`; a ledger whose `reason` text was edited after the audit (sha changes); `--equivalence-audit` without `--cold-witness`; with `--shard`;<br>• dynamic: a receipt whose `judge_sha256` came from a different lane `env`; a receipt whose `r2_runtime_fingerprint_sha256` differs; a receipt whose candidate set is missing one resolved ID;<br>• **dynamic, no `--resume` (round-3 P10R3-1):** `assay run --cold-witness --equivalence-audit R` **without** `--resume` or `--state-dir`, where R's `judge_sha256` came from a different lane `env` → still a whole-lane `LedgerBindingError` (the identity is computed unconditionally); a matching R without `--resume` → R2 PASS with the entries placed | Weak binding; R2-only refusals that fail verify; an `if judge is not None` guard that silently skips the OC9 comparison when no state root is set |
| O9 | Every candidate ledgered → `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` | A-223d regression |
| O10 | Lane ledger declared with no audit flag → candidates execute; survivors stay `survived`; `equivalence_ledger: null`; plan event `declared-unaudited` | A silent claim |
| O11 | `b105_report_check` L1–L5 refuses each of these with its substring:<br>• a ledger sha that differs from the `git show` bytes;<br>• a ledger path differing from the committed lane key;<br>• a missing `review_ref` file at the commit;<br>• a receipt whose bytes differ from `audit_sha256`;<br>• a receipt for a different `assay_version`.<br>A receipt whose `wheel_sha256` differs but everything else matches is **accepted** (C24) | Self-reported ledger; comparing a non-reproducible wheel digest |
| O12 | P10c gate wiring:<br>• the substring and ordering pins;<br>• the stub-binary test (a declared ledger → `--equivalence-audit` present; none → absent);<br>• an accepted prior receipt short-circuits without running the audit and prints `B110_LEDGER_AUDIT_REUSED=1`, **not** `EXIT=0`;<br>• a refused prior receipt is moved to `.prev`, and the re-run passes `--resume` | Gate mode silently skipped; a stale receipt consumed; a reuse that impersonates an in-gate audit |

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
  - the middle path (A-465), and that the audit runs outside the campaign deadline (the narrowed 8-hour ceiling for ledger entries);
  - why anchors, not candidate IDs;
  - the strict resolution rule and why it never re-targets;
  - the `canon` serializer and the interpreter guard (why not `ast.dump`);
  - `scope_sha256`, and the residual that a `reason` may depend on callers outside the scope;
  - why an audit `survived` is consistency evidence, not a proof of equivalence;
  - why the reviewer, the reason and the per-entry review record are mandatory;
  - the trust model: the gate-produced receipt, and the judge-identity binding that is in-process only;
  - the interplay with A-209, A-223d and `mutation_pct`;
  - that B109 reuses the anchor.
- **CONSUMERS (HOW):**
  - how to find anchors (`assay plan` rows: `anchor`, `fingerprint`, `scope_sha256`, `ledger_eligible`);
  - a pasteable ledger file;
  - `assay ledger audit` and its terminal states;
  - `assay run --cold-witness --equivalence-audit`;
  - the `b110-ledger-audit` gate mode (P10c);
  - how to read refusals (`unresolved`, `fingerprint-mismatch`, `ambiguous`, `stale-review`, `not-survived`, `not-run`, `interpreter-mismatch`).
- **CHANGES.md** `### Added`; backlog B120 status; plan §9.2 cross-reference.

## Scope / forbid

**P10a touches only:**
- `tools/b120_anchor_probe.py`;
- `nyxloom-trove/reports/assay-B120-P10A-*.{md,json}`;
- a proposed `decisions.md` row.

**P10b touches:**
- `src/assay/adapters/python.py` (anchors, `canon`, scope only);
- `src/assay/equivalence_ledger.py` (new);
- `src/assay/config.py` (the key, and `as_declared`);
- `src/assay/cli.py`;
- `src/assay/runner.py` and `src/assay/mutation.py` (the consumption flow, the `LedgerBindingError` re-raise, placement before resume, and the judge input only);
- `src/assay/schemas/ledger-audit-receipt.schema.json`;
- `tools/b105_report_check.py` (L1–L5, `--ledger-audit`);
- `assay.toml` (target lists only);
- `tests/fixtures/dataclass-contract.json` (C15);
- `tests/test_cli_lanes_json.py` (the `equivalence_ledger` echo case);
- the new tests, the docs, CHANGES, the backlog and the report.

**P10c touches:**
- `tools/self-qualification-gate.sh` (the `b110-ledger-audit` arm and the qualifying arm's `--equivalence-audit`/`--ledger-audit` wiring only);
- `run-gate.toml` (the new lane only);
- `tests/test_self_lane.py` (pins and the stub-binary case only);
- CONSUMERS and CHANGES.

**Forbid (all phases):**
- `verify.py`, `verdict.py` and the verdict schema (P3a owns X8–X12; if they are missing → BLOCKED);
- SQL `equivalence_artifact` behavior;
- `judge_mutation` and `mutation_pct` semantics;
- any new `ReasonCode`;
- executing the full B105 campaign;
- editing the B105 lanes to declare a ledger. That happens only after the §9.1 screen, in its own reviewed commit;
- P6's, P0's, P3d's and P7b's existing gate-script lines, except the `# B110-P10c:` marker.

## Gate

1. **P10a:** no gate. It is probe and docs only, with `nice`-wrapped planner and probe invocations.
2. **P10b, focused tests serially:** `nice -n 19 ionice -c3 python -m pytest tests/test_equivalence_ledger.py tests/zz_slow/test_equivalence_ledger_real_runs.py tests/test_b105_report_check.py tests/test_mutation_judge_identity.py tests/test_cli_lanes_json.py tests/test_self_lane.py tests/test_dataclass_contract.py -q -p no:cacheprovider`.
3. **P10c, focused tests serially:** `nice -n 19 ionice -c3 python -m pytest tests/test_self_lane.py -q -p no:cacheprovider`. Also run `bash -n tools/self-qualification-gate.sh`.
4. For P10b and for P10c: `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` (L4).
5. For P10b and for P10c: `python ./run-gate.py self-qualification-preflight`, run alone. Read its markers in a separate step. 100% line+branch and the exact exclusions inventory must hold.
6. **Never** run the `b110-ledger-audit` lane as a test: it is the controller's §9.2 runbook step.

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
- The probe shows E1 < 100%, E4 unchanged, any E5 difference, or any E2 re-target after one revision of the proposal.
- P3a merged without the ledger wire fields or rules X8–X12.
- P3b's cold attempt path, `R2CommandProofError` re-raise or `equivalence_ledger_sha256` identity input is absent.
- P7's selection path cannot run with `--cold-witness`, or does not write C5 state records.
- The operator has not ratified OC4, OC7, OC10, OC15, OC17 and the audit-outside-deadline narrowing when P10b is dispatched.
- P6's plan-digest check or P3d's checker refusal marker is not in the v14 base (C23; P10R2-7).
- The audit's cold identity and the qualifying run's identity (OC9) cannot be made equal without changing P3b's identity function.
- The `assay lanes --json` shape does not expose `mutation.equivalence_ledger` (P10c).

## Report

Write `nyxloom-trove/reports/assay-B110-P10-REPORT.md` with:
- the P10a decisions and review verdict, with the B109 reviewer deviation recorded;
- the probe summary (E1–E6);
- the operator ratification reference for OC4, OC7, OC10, OC15, OC17 and the audit-outside-deadline narrowing;
- P10b and P10c traceability tables (`work | owner | oracle | test | controlled break`) with real names and red-first failure counts;
- the gate and preflight verdicts, read separately;
- residuals: the trust-model residual (forged receipt), the scope residual (callers outside the scope) and the interpreter guard.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`

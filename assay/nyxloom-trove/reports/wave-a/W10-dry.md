# W10 — DRY consolidation of repeated judge rules (B129)

| Field | Value |
|---|---|
| Backlog | **B129** (S9; R11 #1-#4; A-479; A-476 moved A-468(d)'s dataclass contract here) |
| Branch | `wave-a-w10-dry` from `assay-b110-landing`, after W3 and W1-W9 have merged (stage 4, last). Own worktree; merge `--no-ff`, then delete it |
| Contract class | **2b**: signatures, site sets and rejections are fixed. Free: I4 private names, placement within a file, test layout |
| Implementer | **Sonnet**, fresh session (operator rule). Behaviour identity is decided per site below; a site that does not match its row is BLOCKED |
| Decisions | A-479, A-476, A-182, A-129, A-466 (central-helper precedent), A-478 |

**Goal:** one helper per repeated rule per trust side, so each decision is written and mutated once. **Nothing observable changes**: messages, exception types, reason codes and public names stay byte-identical.

## Context to read first
Anchors were verified at `5bbd916e` (and hold at `be803c3a`). W3 (`tests/<component>/`), W2 (`analysis.py` moves out) and W6/W8 move them. **Re-resolve each anchor by file and enclosing function.** Every former carver question is marked "Decided (CDn)"; where a review fix text and a CD conflict, the CD wins.
0. `CARVER-DECISIONS.md` (this directory): CD23 (amended), CD27, CD29 and CD18 bind.
1. The plan `assay-WAVE-A-PLAN-2026-09-29.md`, §2 and §4.
2. `b110/research/R11-dry-libraries.md`, §2.2, §3, §6 and §7.3.
3. `b110/P1-suite-scope.md` §"W4. Dataclass contract": the fixture format and generator.
4. `verify.py:1-130` and `:658-826`; `verdict.py:4747-4940`; `test_self_lane.py:128-135`; `tests/fixtures/b105-coverage-exclusions.json`.
5. The r11 scripts `candidates.py`, `idioms.py`, `dupes.py` and `dry_estimate_strict.py`.

## Implementation packet (normative)
**Placement.**
- `src/assay/records.py` and `src/assay/guards.py` are **core leaf modules**: stdlib imports only.
- Decided (CD27): W3's `tests/core/test_import_contracts.py` already lets adapters and parsers import the core leaf modules `records` and `guards` (and nothing else from core) in `ADAPTER_DEPS`/`PARSER_DEPS`. W10 does not edit that file; it checks both entries exist. This supersedes the review's "add both" text.
- No helper is *defined* in an adapter or parser package; they only import `records`/`guards`.
- `candidate_identity.py` is excluded from every rewrite (CD27, A-182): `verify.py` imports it and it is documented dependency-free.
- `verify.py` imports neither module (A-182). Its own helpers are private to `verify.py`.
- Add both modules to both self-qualification lanes' `targets` in `assay.toml`, sorted and identical.

### I1. Records (`records.py`, exact)
```python
from dataclasses import dataclass, field
from typing import TypeVar, dataclass_transform
_T = TypeVar("_T")
@dataclass_transform(frozen_default=True, kw_only_default=True, field_specifiers=(field,))
def record(cls: type[_T]) -> type[_T]:
    return dataclass(frozen=True, kw_only=True)(cls)
@dataclass_transform(frozen_default=True, field_specifiers=(field,))
def positional_record(cls: type[_T]) -> type[_T]:
    return dataclass(frozen=True)(cls)
```
- Replace the 68 `@dataclass(frozen=True, kw_only=True)` with `@record`.
- Replace the 12 `@dataclass(frozen=True)` with `@positional_record`: in config the Coverage-, Mutation-, Canary-, Evidence-, Judge-, ResultReport- and IsolationConfig, `Lane`, `LaneFile`; `go_modfile.ModuleDeclaration`; `_Worst` in python and sql. They are constructed positionally (`python.py:767`, `sql.py:756`, `test_b105_config_boundaries.py:65,69,72,76,88,308,309`), so kw-only is rejected.
- Remove `dataclass` from the `from dataclasses import` lines (keep it where a file still uses it).
- **Mutation effect:** 148 flags become 6 in `records.py`, and O2 kills those 6; the per-class evidence is the fixture, not mutation. Record this in the LOG and DESIGN-GUIDE.

### I2. Guards
Producer side: `guards.py` (exact, with the imports `from __future__ import annotations`, `import math`, `from datetime import datetime` first; `__all__` lists the public functions). Each keeps today's short-circuit order and raises only what the inline code raised: `is_finite_positive(10**400)` and `is_positive_or_inf(10**400)` raise `OverflowError` (ints beyond float range). Never catch it.
```python
def is_strict_int(v: object) -> bool: return isinstance(v, int) and not isinstance(v, bool)
def is_int_at_least(v: object, minimum: int) -> bool: return is_strict_int(v) and v >= minimum
def is_real(v: object) -> bool: return isinstance(v, (int, float)) and not isinstance(v, bool)
def is_finite_positive(v: object) -> bool: return is_real(v) and math.isfinite(v) and v > 0
def is_positive_or_inf(v: object) -> bool: return is_real(v) and (math.isfinite(v) or v == math.inf) and v > 0
def is_nonempty_str(v: object) -> bool: return isinstance(v, str) and v != ""
def is_sha256_hex(v: object) -> bool: return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)
def is_aware(m: datetime) -> bool: return m.tzinfo is not None and m.tzinfo.utcoffset(m) is not None
def is_percentage(v: float) -> bool: return 0.0 <= v <= 100.0
```
**Rewriting call sites.** The site sets are the `idioms.py` families at HEAD, in producer files. Replace only the named operands; keep every other operand, the `raise`/`return` and the message. A site is in a family iff the row's operands form a contiguous run inside one BoolOp over the same subject; other operands stay in place. Also in scope: `adjudication 182` (`not is_strict_int(schema_version) or schema_version not in _ACCEPTED_SCHEMA_VERSIONS`).

| Today | Rewrite | Sites (HEAD) |
|---|---|---|
| `not isinstance(x,str) or not x` | `not is_nonempty_str(x)` | attestation 156,423; config 1596,1600,2617,3117,3524; mutation 1806 (x2); mutation_report_json 169,276,289; provenance 180; runner 865; verdict 738,795,820,1552,2951,2956,3954,4439 |
| `isinstance(x,bool) or not isinstance(x,int)` (either order) | `not is_strict_int(x)` | adapters/base 106; go_stmtpos 408,417; config 1476,2990,3000,3205; coverage_istanbul_json 682,748,762; coverage_py_json 269,320; mutation 351,709,2348,2352; mutation_report_json 414; vitest_json 37; verdict 954,1498 (keep `or value != 1`),1653,1896,2465,2886,2890,3505 |
| the same + `or x < k` / `or x <= k` (k an int literal) | `not is_int_at_least(x, k)` / `(x, k+1)` | coverage_istanbul_json 450,463,641; isolation 136; mutation 491; verdict 804,3741 (keep the leading `is None or`),4493; liveness 708; liveness_resources 133 |
| `..bool.. or not isinstance(x,(int,float)) or not math.isfinite(x) or x <= 0` | `not is_finite_positive(x)` | mutation 2370; runner 243,282; verdict 1935,2924 |
| `isinstance(x,bool) or not isinstance(x,(int,float))` | `not is_real(x)` | config 3688; liveness 798,905; runner 254 (keep `or not math.isfinite(started)`); verdict 958,2345,3024 |
| `..bool.. or (not math.isfinite(t) and t != math.inf) or t <= 0` | `not is_positive_or_inf(t)` | isolation 1123; runner 1157 |
| sha256 as `len != 64` plus a charset loop, or `_SHA256_RE.fullmatch` | `not is_sha256_hex(x)` | provenance 186; verdict 1544,1669,1720,1927,2152 |
| `m.tzinfo is None or m.tzinfo.utcoffset(m) is None` | `not is_aware(m)` | verdict 612,777 |
| `not 0.0 <= v <= 100.0` | `not is_percentage(v)` (a `float(...)` stays at the call site) | config 2524,3302; verdict 960,2351,3030 |

- For sha256, the two spellings are identical, because `fullmatch` on `^...$` refuses a trailing `\n`. Drop `_SHA256_RE` once it is unused.
- **Delete every import a rewrite leaves unused** (pyflakes turns red otherwise): at `be803c3a`, `import math` in `verdict.py` (uses only at 1937, 2926) and in `isolation.py` (code use only at 1125). Before each commit run the gate's pyflakes over `src/assay`, or check by AST.
- Add `claim_for` and `claim_carries` to `verdict.__all__`. Dataclasses added after `be803c3a` by W6, W8 or W9 follow the same mapping; any non-frozen or other dataclass form is BLOCKED.
- **Verify side**, private to `verify.py`:
  - `_is_int(v)` has the body of `is_strict_int`. It is used at 1186, 1188, 1559, 1594, 1663 and 1665, and negated at 392, 1577 and 1579.
  - `_is_text(v)` has the body of `is_nonempty_str`. It is negated at 478, 511 and 1826.
  - Leave `_is_sha256_digest` (1817) and line 1479 as they are.

### I3. Policy present iff rN attempted (one set per side)
Producer side: `verdict.py`, module level.
```python
def claim_for(claims: Iterable[Claim], rigor: str) -> Claim | None:
    return next((claim for claim in claims if claim.rigor == rigor), None)
def claim_carries(claim: Claim | None, payload: str) -> bool:  # public: runner uses it
    return claim is not None and getattr(claim, payload) is not None
def _require_policy_iff_attempted(policy: object | None, attempted: bool, *, orphan: str, missing: str) -> None:
    if policy is not None and not attempted:
        raise ValueError(orphan)
    if policy is None and attempted:
        raise ValueError(missing)
```
- `claim_for`: verdict 4769, 4799, 4865, 4910, 4990 and 5019; runner 2176. **Not** runner 4653/4655: they have no default and raise `StopIteration`.
- `claim_carries`: verdict 4770 (`coverage`), 4807 (`mutation`), 4866 (`canary`), 4911 (`red_first`); runner 2177. At verdict 4991 and 5020 use `not claim_carries(r2_claim, "mutation")`.
- `_require_policy_iff_attempted`: 4785-4797, 4813-4825, 4868-4879 and 4913-4924, with their strings unchanged.

Verify side: private to `verify.py`, with the same bodies over the raw document.
- `_raw_claim(claims: list, rigor: str) -> dict | None` matches `isinstance(i, dict) and i.get("rigor") == rigor`. Sites: 684, 712, 775, 915, 1043 (keep its list guard), 1527, 1625.
- `_claim_of(verdict: Verdict, rigor: str) -> Claim | None`. Sites: 2565, 2637, 2685, 2703, 2812, 2959.
- `_raw_policy_iff_attempted(present: bool, attempted: bool, *, orphan: str, missing: str, failures: list[str]) -> None` appends `orphan`, then `missing`, in that order. Sites: 700-709, 726-735 and 781-788 (R3 passes `r3_judged`).
- Never unify its wording with `verdict`'s (`verify.py:665-669`).

### I4. Other same-side clusters (T1)
Accepted. Each helper is private to its file, and messages stay byte-identical.

| # | Sites (HEAD) | Helper |
|---|---|---|
| E1 | git 952-959, 1026-1033, 1097-1104 | `_zero_true_one_false(returncode, stderr: bytes, label) -> bool`, raising `_git_failed(f"{label} failed ({returncode}): {stderr.decode('utf-8', errors='replace').strip()[:200]}")`. Labels: `f"git check-ignore {relative_path}"`, `"git merge-base --is-ancestor"`, `"git diff --quiet"` |
| E2 | liveness_resources 201-213, 222-234 | `_counter_delta(old, new) -> int \| None`: `None` if either is a bool, a non-int or negative, or if `new < old`; otherwise the difference. Call site: `delta = _counter_delta(old_value, new_value)`; `if delta is None: return "unknown", {}`; `if delta: deltas[...] = delta`. Test a zero delta on every counter giving `"clear"` (`if not delta` is the plausible wrong form) |
| E3 | verdict 952-957, 1894-1899 | `_require_non_negative_ints(obj, names, prefix)`, with prefix `"coverage"` or `"mutation"` |
| E4 | verdict 2389-2409, 2766-2783 | `_check_whole_target_targets(targets, label)`, with label `"judgment.r1"` or `"judgment.r2"` |
| E5 | cli 1320-1325, 1408-1413, 1545-1553 | `_deliver_verdict(verdict: Verdict) -> int`, a **nested** function in `_run_reserved`, defined directly after the closure `_emit_verdict_written` (`cli.py:1206`), which a file-level function cannot call. It closes over `destination`, `args` and `out` (none is rebound in `_run_reserved`). Its body is `cli.py:1545-1553` verbatim, including the A-181 comment. The three sites become `return _deliver_verdict(verdict)` |
| E6 | liveness `_EventProgressReader.__init__` 1094-1103 | set `_candidate_pid`, then call `self._reset()` (the same 8 attributes and defaults) |
| E7 | mutation_witness 241, 248, 283 | `_is_false(receipt, key)`: `receipt.get(key) is False` |
| E8 | canary 267, 422 | `_r1_unreached(r0_claim, lane)` |
| E9 | coverage 269, 309 | `_capability(capabilities)` |
| E10 | evaluate 678, 1307 | `_branch_only_shortfall(missing_lines, covered, total)` |

**Rejected (do not touch), with the reason for each:**
- `candidate_identity.py` (all sites): imported by `verify.py`, documented dependency-free; routing it through `guards` would make the raw verifier share a producer helper (A-182).
- Cross-boundary twins (A-129/A-182): never unify across them. Each side still applies its own rule: verdict 777 takes `is_aware` (row 8); verify 1479 and `_is_sha256_digest` stay inline.
  - canary 985 <-> verify 2758;
  - mutation_witness 246 <-> verify 1808;
  - the two `_check_judgment_matches_claims`.
- The analysis framer `'1' <= char <= '9'` x8: A-478 moves it out, and §7.3 forbids an `isdigit` rewrite.
- Scanner loops (sql 371/459/467; go 329-357, 400/402; go_modfile 218/220, 246/250, 442<->javascript 433; sql_lex 234-265): intrinsic, and W6's guards live there.
- Type-narrowing asserts (git 326, 1473-1483, 1596; runner 4339, 4895, 4953): not decisions. runner 3024<->3045: not identical.
- config 644<->verdict 3107, config 3195<->mutation 2399, mutation 2407<->runner 6127, attestation 433<->config 2629: load-time vs run-time layers with different exception types; each ≤3 candidates and would need a new import edge.
- Clones `_malformed`, `_append_snippet`, `_reject_duplicate_pairs` (0-1 candidates each); R11 #5-#7.

**Bookkeeping.**
- A line added above a `pragma: no cover` shifts the lines pinned in `b105-coverage-exclusions.json`. At HEAD those are config 94, liveness 94, mutation 148, mutation_witness 14, and git 376 and 1342.
- Prefer imports below the `TYPE_CHECKING` block. Otherwise shift the pinned numbers by the amount you measure. W6's O5b and the preflight enforce this. Before every commit run `grep -n "pragma: no cover" src/assay/{config,liveness,mutation,mutation_witness,git}.py` and compare with the fixture's lines (the fast suite cannot see drift).

## Work
Make one commit per step, with its focused tests green.
0. **Base inventory.** Record the base SHA. Run `candidates.py`, `dry_estimate_strict.py`, `idioms.py --json` and T1 `dupes.py` into `/tmp/w10-before/`. Match every site to its row by (file, function); a new site of a listed family is in scope; note any vanished site; a site whose shape does not match its row is BLOCKED.
1. **Characterization tests, run against unchanged source.** Add `test_dataclass_contract.py` to W3's core test directory and generate its fixture with the P1 generator. Decorators are detected **by the resolved decorator object** (CD27), not the literal `@dataclass`: resolve each decorator expression (a Name or Attribute, or the `func` of a Call) through the module's bindings to identity with `dataclasses.dataclass`, `assay.records.record` or `assay.records.positional_record`, so aliases and re-exports resolve and the step-1 functions pass before and after I1 without edits. Step 2's bypass and params rules are **new** test functions. For every I2 family, I3 path and I4 site, pin accept or refuse and the exact message, or cite an existing test that already pins it.
2. **I1.** Add the AST bypass rule, the params-per-decorator check and the `__dataclass_transform__` pins. The fixture must stay byte-identical.
3. **I2**, together with `test_guards.py`.
4. **I3.**
5. **I4.**
6. **`test_trust_boundary.py`.**
7. **Bookkeeping and docs.**
8. **Final inventory.** Re-run step 0 into `/tmp/w10-after/`. Record in the LOG the per-operator totals, the per-file deltas and the residual T1 union.

## Oracles
- **O1. Helpers** (`test_guards.py`).
  - Obs: each comparison is tested on both sides, and each operand with a value that alone decides the result. Values: `True`, `False`, 0, ±1, k-1, k, 1.5, nan, ±inf, -0.0, 100.0, 100.0000001, `""`, `"x"`; 63-, 64- and 65-character hex, uppercase hex, 64 hex + `"\n"`; a naive datetime and one whose `utcoffset()` returns `None`; `10**400` giving `OverflowError` for `is_finite_positive` and `is_positive_or_inf`. These tests alone must kill every helper mutant (B130 partition).
  - Neg: `is_strict_int(True)` returns true; `>` is used for `>=`.
- **O2. Record contract.**
  - Obs: the fixture is unchanged since step 1; every `src/assay` dataclass is at module level with exactly one of the two decorators (resolved by object, CD27); params match the decorator; both `__dataclass_transform__` values are pinned.
  - Negatives, fed to the pure checkers: a bare `@dataclass(...)` in synthetic source; `dataclass(frozen=False, kw_only=True)(type("X", (), {}))`; a function-local `@record`.
- **O3. No behaviour change.** Obs: the full suite is green, the step 1 tests are unchanged after step 1, and no existing assertion is edited. Neg: at `isolation 136`, write `is_int_at_least(value, 0)` instead of `(value, 1)`: a step-1 characterization test fails.
- **O4. Per-rigor messages.** Obs: every orphan/missing text is its own and unchanged. Neg: the arguments are swapped.
- **O5. Trust boundary.**
  - Obs:
    - (a) the exact set of `(module, name)` pairs `verify.py` imports equals the committed base, and excludes `guards` and `records`;
    - (b) A-182 boundary (CD27): at runtime, the **defining module** (`__module__`) of every name bound in `vars(assay.verify)` is not `assay.guards` or `assay.records`, and none is `assay.verdict.claim_for`, `claim_carries` or `_require_policy_iff_attempted`. This catches re-exports that leave the module set unchanged;
    - (c) `guards`, `records` and `candidate_identity.py` import no `assay` module;
    - (d) no module under `src/assay` other than `verify.py` imports a `_`-prefixed name from `verify` (tests are exempt: `test_verify_snapshot_policy.py:17`).
  - Negatives (synthetic sources): `from .guards import ...` and `from .verdict import is_strict_int` in verify, `verdict.claim_for(...)` used in verify, `from .verify import _raw_claim` in a producer, `import assay.guards`, and an `assay` import in `candidate_identity`.
- **O6. Count measured** (step 0 against step 8), never predicted. A file whose count rises, or a drop in a file you did not touch, is a finding.
- **O7.** tester-unified is green, and the preflight is at R0/R1 100%.

**AUTHORING §3b (binding):**
A no deadline, sleep or elapsed-time assertions (a red on a slow host is real). B restore global state; no `setattr` on `__getattr__` proxies; fresh `tmp_path`. C no hollow, trivia or weakened assertions. D no `pragma: no cover`. E control network, clock, filesystem. F predict no numbers. Check: could it flip with a slower host, another worker or another order?

## Scope / forbid
**Touch:** `src/assay/**` (the listed sites, their imports, the two new modules); target lists in `assay.toml`; the line numbers in the exclusions fixture; new tests and fixtures; `docs/DESIGN-GUIDE.md` and `CHANGES.md`; the LOG.

**Forbid:** any message, exception, reason code, public name or signature; the analysis package (`analysis/src/assay_analysis`), `gate/`, `tools/`, `run-gate.toml`, the schema, `tests/core/test_import_contracts.py`; existing assertions; pragmas; count-only rewrites (R11 §7.3); helpers shared across the verify boundary; `decisions.md` and `4-backlog.md`.

**Docs:** DESIGN-GUIDE's B105 section (`:1972`) gets "Shared records and guard predicates (B129)": the mutation effect, the fixture regeneration command, the verify boundary and the measured count. One sentence goes into the A-182 paragraph (`:1459`). CHANGES gets `### Changed` and `### Testing` entries. README and CONSUMERS stay unchanged; say so in the LOG.

## Gate
**Host-load rule (plan §4):** shared production host; everything under nice/ionice; at most one gate container and none while another session's gate runs (`docker ps` first); never the full `self-qualification` lane; remove containers by exact name only.

**Steps:**
1. Focused tests, serially, under `nice -n 19 ionice -c3`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/w10-gate.log 2>&1; echo "exit=$?"`. Then, as a separate step, grep the log for `ASSAY_GATE_CONTAINER_EXIT=` and `ASSAY_REGISTERED_GATE_COMPLETE=`.
3. **Mandatory.** Once step 2 has finished, run `self-qualification-preflight` the same way and read `B105_VERIFIED_LANE=` separately. If the host does not permit it, write BLOCKED.

**No known red (CD29).** Main's `35adca38` (merged at `1e3c8a49`) deleted that test. Any gate failure is red.

## BLOCKED rule
If a named contract cannot be met as specified, or the scope needs a forbidden file, STOP: write `BLOCKED: <reason>` to the LOG, commit, and exit. Do NOT improvise.

Triggers:
- a site does not match its row;
- a characterization test needs a changed assertion;
- a helper would have to raise (beyond the inline code's own `OverflowError`) or reorder evaluation;
- W3's contract refuses an edge other than adapter/parser module to `assay.records`/`assay.guards` (CD27 says it allows those).

## Review and LOG
**Review.** Before the merge, a fresh session reviews adversarially: each rewrite against its row; every `raise`/`append` line changed in `git diff <base>..HEAD -- src/assay` only moved; the fixture is byte-identical; O5 and O6.

**LOG** (`nyxloom-trove/reports/wave-a/W10-dry-LOG.md`): commits (trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`); a traceability table (work, oracle, node, controlled break, result; breaks are local, never committed: `kw_only=False`, `>=` to `>`, swapped orphan/missing, a `guards` import in `verify.py`); before and after counts; gate markers.

**Checkpoint.** Arm at ~120k context or ~60 tool calls. Cut at a green commit.

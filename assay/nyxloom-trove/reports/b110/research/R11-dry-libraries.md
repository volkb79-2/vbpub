# R11: Can assay be made DRY, with code moved into its own managed libraries?

**Written:** 2026-09-28 by a fresh, read-only research agent. **Status:** analysis only. It changes no decision, backlog item, brief or source file.
**Checkout:** `.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy`, branch `assay-b105-evidence-integrity`, HEAD `95622e3a`. All paths below are relative to that clone. Assay's source is the same at `30eec294`, the commit of the stopped B105 attempt.
**Labels:** [M] measured by a script or command here · [C] calculated from [M] values · [A] assumption · [I] inference.
**Scripts and outputs:** `scratchpad/r11/`. Each section names the script it used. Every script is stdlib-only, runs `ast` over the source, and ran under `nice -n 19 ionice -c3`. No test suite, gate or container was started.

---

## 0. Short answer

1. **Little copy-paste, many repeated idioms** [M]. Whole-function clones hold 16 candidates; pylint finds 5 blocks of ≥6 lines. The duplicated candidates are typed-field guards (122 occurrences, 177 candidates), repeated decision expressions, the "judgment.rN iff rN attempted" rule written for each of R1–R4, and 148 dataclass flag constants.
2. **Internal DRY, no library: ≈395–528 of 3,760 candidates (10.5–14.0%)** [C]; 250–383 without the dataclass flags. That is ≈27–37 wall-min per qualifying run (3 workers, ≈12.5 s per kill), with no claim change and no new decision.
3. **≈290 of `verify.py`'s 327 candidates are deliberate semantic twins** of producer logic (A-129, A-182) and must stay. A syntax-based detector sees only 14 of them, because the wording is deliberately different.
4. **A managed library moves candidates; it does not remove them.** It saves time only if the library is requalified less often than assay, or its lane is cheaper per candidate than assay's ≈10–15 s. The generic ("estate-core") share of assay is ≤197 candidates (5.2%), realistically 80–150, and assay would be the *provider* of the strictest primitives (bounded git, no-follow IO).
5. **Moving code out of `src/assay` silently narrows B105** unless bound: the claim becomes "assay source + a pinned library subtree with its own verifier-accepted R0–R3 verdict". Only **(b) build-time vendoring** fits the pip-less zipapp (A-402), and it still needs **new decisions** (A-005 wording, A-462 scope, checker binding). (a) and (c) conflict with A-005.
6. **Table rewrites:** genuine when one rule instantiated N times becomes one helper, since the rule is still mutated once. Hiding when boundary comparisons become `in`-sets, regexes, `str.isdigit()` or lookups, which no operator sees.

---

## 1. Method and inventory reproduction

**No per-candidate list was retained.** The progress stream (`.worktrees/assay-b105-gate-copy-30eec294/assay/.assay/progress-self-qualification.jsonl`) has a `plan` event (baseline 519.9 s, budget per candidate 1,559.7 s) and a `candidates` event (`candidate_total 3760`). It has no per-candidate list, only 15 `candidate` events [M]. **So the inventory was regenerated statically.**

**How:** `r11/candidates.py` re-implements `_candidate_sites` (`src/assay/adapters/python.py:708-719`) with no line filter (`whole_target`): compare-swap on `< <= > >= == != is is not`, one boolop-swap per `and`/`or` token, bool constants, and falsy returns (`None`, `0`, `""`, `b""`, empty list/tuple/set/dict).

**Result [M]:** 3,760 candidates at both `30eec294` and `95622e3a`: compare-swap 2,169, boolop-swap 958, bool-const-flip 498, falsy-swap 135. That is 47 candidate-bearing files, with a minimum of 1, a median of 30 and a maximum of 623 per file. **This matches the recorded plan exactly,** so every per-site count below rests on the same inventory.

**Other scripts:**
- `dupes.py`: clones of functions (F), maximal statement runs (W) and decision expressions (E). T2 alpha-renames identifiers; T1 keeps them. Both abstract string constants and keep numbers.
- `idioms.py`: typed-field guard families (G).
- `dry_estimate.py` / `dry_estimate_strict.py`: the union of removable sites at T2 / T1, respecting the `verify.py`↔producer boundary.
- `hotspots.py`, `none_patterns.py`: site kinds. `verify_layers.py`: `verify.py` stages.
- `estate.py`, `estate_union.py`: cross-estate scan. `churn.py`: per-file changes over the 12 release intervals `assay-v5.2.0`→HEAD.
- `pylint-dup.txt`: pylint R0801 cross-check (`--min-similarity-lines=6`).

---

## 2. Q1: Duplication inside `assay/src/assay`

### 2.1 Literal duplication is low [M]

| Detector | Clusters | Redundant candidates |
|---|---:|---:|
| F: whole-function T2 clones (≥25 AST nodes) | 15 | 8 |
| W: statement runs ≥3 (T2) | 102 | 74 (union) |
| W: statement runs ≥2 (T2) | 332 | 215 |
| E: decision expressions with ≥2 candidates (T2) | 55 | 249 |
| E: decision expressions with ≥2 candidates (T1, identifiers kept) | — | 101 |
| pylint R0801 (≥6 lines) | 5 blocks | — |

The function clones hold almost nothing: the adapters' `_append_snippet`/`_inject_*` pairs (`adapters/go.py:487-517` ↔ `adapters/javascript.py:441-469`), `_malformed` in 5 coverage parsers and `_reject_duplicate_pairs` (`mutation_witness.py:295` ↔ `reuse.py:189`), both 0 candidates, and the deliberate R3 twin `canary.py:967` ↔ `verify.py:2747`.

### 2.2 Idiom duplication is where the candidates are [M] (`idioms.py`)

**Typed-field guard groups:** 122 occurrences, 177 candidates, 36 families. A guard group is the operands of one `and`/`or` that test the same subject and include an `isinstance`.

| Family (operand shape) | Occ. | Candidates | Examples |
|---|---:|---:|---|
| `not isinstance(x,str) or not x` | 27 | 27 | `attestation.py:156,423`, `candidate_identity.py:22,24`, `config.py:2617` |
| `isinstance(x,bool) or not isinstance(x,int)` | 24 | 24 | `adapters/base.py:106`, `adapters/go_stmtpos.py:408,417`, `config.py:2990,3000` |
| `… or x < k` (strict int with lower bound) | 7 | 21 | `isolation.py:136`, `mutation.py:491`, `verdict.py:804`, `coverage_parsers/coverage_istanbul_json.py:450,463,641` |
| `… (int,float) or not math.isfinite(x) or x <= 0` | 5 | 20 | `mutation.py:2121`, `runner.py:243,282`, `verdict.py:1935,2924` |
| `isinstance(x,bool) or not isinstance(x,(int,float))` | 8 | 8 | `config.py:3688`, `isolation.py:1123`, `liveness.py:778,885`, `runner.py:1153` |
| `isinstance(x,int) and not isinstance(x,bool)` (verify side) | 6 | 6 | `verify.py:1186,1188,1559,1663,1665` |
| sha256-hex (`len(x) != 64`, `_SHA256_RE`, `'0123456789abcdef'`) | 8 | 17 | `candidate_identity.py:31`, `provenance.py:186`, `verdict.py:1544,1669,1720,1927,2152`, `verify.py:1819` |
| aware datetime (`tzinfo is None or utcoffset() is None`) | 3 | 9 | `verdict.py:612,777`, `verify.py:1479` |

**Top repeated decision expressions (T2):**

| Expression | Copies | Candidates per copy | Locations |
|---|---:|---:|---|
| `'0'/'1' <= char <= '9'` (6 + 2; strings are abstracted) | 8 | 2 | `analysis.py:410-478` |
| `isinstance(claim, dict) and claim.get('rigor') == 'RN'` | 8 | 2 | `reuse.py:91`, `verify.py:684,712,775,915,1043,1527,1625` |
| `budget_per_candidate_auto and budget_per_candidate_seconds is not None` | 6 | 2 | `mutation.py:2135,2660`, `runner.py:4585`, `verdict.py:4828,4880,4925` |
| `write_progress is None and raw_write is not None` | 4 | 3 | `mutation.py:2206`, `runner.py:3157,3187,5631` |
| `proc.stdin is not None and proc.stdout is not None` | 4 | 3 | `git.py:1398,1473,1480,1482` |

**The per-rigor correspondence rule** ("judgment.rN is present iff rN was attempted, and vice versa") is written four times in `Verdict._check_judgment_matches_claims` (`verdict.py:4747-4940`, 58 candidates, R1/R2/R3/R4 blocks at `verdict.py:4769`, `4799`, `4865`, `4910`). T2 also finds it matching unrelated code (`failure is None and oversized_pending`, `analysis.py:797`). That is why T1 is the lower bound.

**Dataclass flags:** `@dataclass(frozen=True, kw_only=True)` ×68 and `@dataclass(frozen=True)` ×12 give 148 bool constants (3.9%) [M]. These are the "5 of the first 7 survivors" of A-468.

### 2.3 The idioms the brief asked about [M]

- **Duration parsing** is already central: `config.parse_duration` (`config.py:348-404`, 6 candidates).
- **Digest/hex checks** use three spellings: a regex `verdict._SHA256_RE` (`verdict.py:669`), a predicate `verify._is_sha256_digest` (`verify.py:1817`), and inline loops or `re.fullmatch` (`candidate_identity.py:31`, `analysis.py:98,173`).
- **Path containment/grammar** has at least 5 divergent implementations: `safeio._lexical_components` (`safeio.py:47`), `verdict._check_wire_path` (`verdict.py:719`), `attestation._validate_reviewed_path` (`attestation.py:193`), `config._validate_omission_path` (`config.py:905`) and `config._validate_evidence_dir` (`config.py:2600`). They differ in backslash, `.`, trailing-slash and byte-bound rules. **Together they hold only 38 candidates**: `".." in parts`, `startswith("/")` and `if not value` are all outside the catalogue (`in` is excluded by A-112, and calls and truthiness are never mutated). Consolidating them is a correctness decision, not an R2 saving.
- **JSON reads:** 18 `json.loads` sites, with four different duplicate-key or non-finite policies (`attestation.py:239`, `analysis.py:41`, `reuse.py:44`, `mutation_witness.py:218`). They hold about 0–5 candidates.
- **Schema/version checks:** about 10 sites, for example `analysis.py:90,159`. Small.
- **Error construction:** 1,266 `raise` sites, all with **0 candidates**. DRY there is R2-neutral.

### 2.4 Union estimate [C] (`dry_estimate.py`, `dry_estimate_strict.py`)

**Method:** keep one copy per family per trust side; the other copies are removable. A copy on the other side of `verify.py`↔producer is counted as deliberate and never removable.

| Source | T1 removable | T2 removable |
|---|---:|---:|
| G: guard families | 111 | 111 |
| E: repeated decisions | 101 | 227 |
| W: repeated runs | 51 | 77 |
| F: function clones | 3 | 4 |
| D: dataclass flags | 145 | 145 |
| **Union (no double count)** | **395 (10.5%)** | **528 (14.0%)** |
| Union without D | 250 | 383 |

By file (T1): `verdict.py` 107, `runner.py` 35, `analysis.py` 26, `mutation.py` 23, `config.py` 22, `git.py` 18, `isolation.py` 14.

---

## 3. Duplication that is deliberate and must stay

**`verify.py` is an independent validator by design** (`verify.py:1-80`; A-129 at `decisions.md:392`; A-182 at `decisions.md:495`). A-182 says: "leaving these only in producer constructors lets a raw verifier share producer trust". Its candidates split by stage [M] (`verify_layers.py`):

raw-document cross-checks 205 · independent re-derivation of R1–R4 85 · reconstruction glue 37.

So **290 of 327 candidates are semantic twins** of rules in `Verdict.__post_init__`, `canary`, `evaluate` or `mutation`. Nine raw checks (102 candidates) name their producer twin in their docstring, for example `_check_judgment_matches_claims` (`verify.py:658`, 36 candidates, twin at `verdict.py:4747`, 58 candidates).

**The syntax-based detector sees only 14 cross-boundary candidates.** `verify.py:666-673` records why: "a mutation test proved that an identical wording makes the raw check's own failure indistinguishable from reconstruction". **That is the real cost of defense in depth:** the candidates are doubled, and a raw-check mutant is killable only by a test that asserts the raw check's own message, because reconstruction would reject the same defect anyway.

**Other deliberate twins:**
- the R3 aggregation, hand-transcribed (`canary.py:967` ↔ `verify.py:2747`, "exactly what A-182 forbids" to import);
- the mutation catalogue in `nyxloom/src/nyxloom/mutants.py:26` ↔ `adapters/python.py:434-468`. A-112 adopts the catalogue verbatim but uses a different engine.

**The consolidation rule this implies [I]:**
- DRY within a side is fine: a verify-side `_raw_claim(claims, rigor)` helper, or one producer-side guard module.
- Never share a helper across `verify.py`↔producer for cross-object relations or re-derivation.
- Leaf format predicates (sha256 hex, path grammar) are the grey zone. A-182 assigns "local grammar to model+Schema+raw verifier" without saying whether the implementation may be shared. **That needs an explicit ruling** before a shared `_sha256_hex()` crosses the boundary.

---

## 4. Q2: Duplication across the estate

### 4.1 Sizes [M] (`estate.py`; tests, fixtures and symlinks excluded)

| Project | Files | Lines | R2 candidates | Infra-helper union (≤80-line functions): count / lines / candidates |
|---|---:|---:|---:|---|
| assay | 50 | 47,265 | 3,760 | 63 / 2,053 / **197** |
| ciu | 45 | 31,155 | 2,553 | 62 / 1,927 / 274 |
| cmru | 42 | 19,089 | 2,086 | 100 / 2,294 / 387 |
| nyxloom | 103 | 57,371 | 4,617 | 144 / 3,339 / 525 |
| run-gate (one file, symlinked into 18 projects) | 1 | 8,841 | 1,034 | 35 / 934 / 179 |
| topos | 98 | 30,579 | 3,691 | 64 / 1,375 / 231 |
| libraries/worktree | 2 | 1,307 | 183 | 7 / 219 / 57 |
| libraries/cli-extended | 6 | 2,581 | 341 | — |

"Infra" means any of: git argv plus a subprocess call, subprocess, atomic write, TOML/JSON load, hashing, duration parsing, canonical JSON, path containment, or `O_NOFOLLOW` IO. The column is an **upper bound**: many of these functions are domain loaders that happen to call `json.loads`.

### 4.2 The same kind of helper, but a different contract [M]

**Cross-project T2 function clones:** 19 clusters, all tiny.
- `ciu/src/ciu/output.py` ≈ `cmru/src/cmru/output.py` (about 20 candidates);
- the argparse `add_subparsers`/`error` overrides, identical in `assay/cli.py:108,119`, `ciu/cli_utils.py:40,51`, `nyxloom/cli.py:264,275`, `topos/cli_diagnostics.py:26,37` and `run-gate.py:150` (0 candidates). This is exactly `libraries/cli-extended`'s territory.

**The substantive overlap is conceptual, and the contracts diverge:**
- **Duration grammars:** assay refuses bare numbers and accepts decimal `1h30m` (`config.py:348`); ciu accepts a bare integer with an optional single unit (`ciu/src/ciu/deploy.py:124-144`); run-gate has its own `budget_seconds` (`run-gate.py:461`). One parser would change a user-visible config grammar in at least two tools.
- **Git runners:** assay's `_run_bounded` (`git.py:283-395`) bounds output (`git.py:195`), resolves the executable (`git.py:398`), retries on EAGAIN (`git.py:243`) and pins config (A-472). `worktree._git` (`libraries/worktree/src/worktree/core.py:364`), `cmru/runner.py:60` and `run-gate.py:2473` are plain unbounded `subprocess.run(["git", …])`. **Assay could not consume any existing estate git helper without losing its guarantees; it would be the provider.**

### 4.3 Library precedents that already exist [M]

| Model | Example | How it is consumed | Binding to the consumer's claim |
|---|---|---|---|
| Vendored in-repo source | `libraries/worktree` | ciu/cmru `pyproject.toml` `package-dir` → `../libraries/worktree/src` (`ciu/pyproject.toml:46-50`); "not a separately released package" (`libraries/worktree/README.md:11`); own R0–R3 lane | **None.** `CONSUMERS.md:86-96` only says the product lanes "remain required" |
| Separate distribution | `libraries/cli-extended` 0.1.0 | `pip install`, R0–R2 whole-target lane | None |
| Symlinked single file | `run-gate-project/run-gate.py` | 18 symlinks | n/a (a tool, not qualified per consumer) |

### 4.4 Is there an estate-core? [I]

Yes, but it is small and assay-led. The plausible contents are:
- assay's `safeio` (536 lines, 30 candidates, 0/12 churn);
- the bounded git runner core (roughly 50–80 of `git.py`'s 143 candidates, excluding the snapshot-specific `_p22_*`);
- canonical JSON, strict JSON load, sha256-file and duration parsing (≈15–30 candidates).

That is **≈80–150 candidates of assay's (≤197 upper bound, 5.2%)** [C/A]. For ciu, cmru and nyxloom, adopting it is a behavior change: bounded output and stricter refusals. Their gain is strictness more than R2 time, because their R2 lanes are mostly changed-lines; only 5 project configs (6 lanes) in the estate declare `whole_target` [M].

---

## 5. Q3: What a managed library does to R2 cost and to the claim

### 5.1 Cost, precisely

**Per-candidate cost in a lane is `S + C + P_i`:** snapshot, then interpreter plus collection, then time to the first failing test (B110 plan, reuse review B1).
- For assay after P5, `S + C` ≈ 9.4–11.4 s per kill and ≈385 s per survivor (B110 plan).
- 3,760 × 12.5 s ÷ 3 workers ≈ 4.35 h [C]. That agrees with the plan's "3.3–4.7 h of the 5 h candidate budget".
- **Each 1% of the inventory (37.6 candidates) ≈ 2.6 min of wall time** [C].

**Moving N candidates into a library L:**

```
assay qualification: -N × (S_a + C_a + P)                     (always)
L qualification:     +N × (S_L + C_L + P) per L requalification (0 while L's subtree OID is unchanged)
find-and-fix:        survivors in L are fixed in L's loop with L's (smaller) full suite
```

**The saving holds only if at least one of these is true:**
1. **L is stable:** it is requalified less often than assay.
2. **L's lane is cheaper per candidate:** a small suite collects fast, although today's library lanes still snapshot the whole monorepo (`libraries/cli-extended/assay.toml` isolation).
3. **L is shared:** qualified once and consumed by several whole-target claims.

**Stability evidence [M]** (`churn.py`, intervals `assay-v5.2.0` → HEAD):
- **Stable pool:** 821 candidates sit in files changed in ≤1/12 intervals (553 in files changed 0/12). Parsers and adapters make up most of it: 527 of their 590 candidates are in files changed ≤1/12 intervals.
- **Big modules churn:** `verdict.py` 6/12, `runner.py` 7/12, `verify.py` 5/12, `mutation.py` 8/12, `config.py` 6/12, `cli.py` 8/12.
- **Caveat [I]:** B110 P2 adds `errors.require_advance` at scanner cursor sites (A-466), which touches the "stable" adapters and analysis. The first stable window would start after B110.

**Worked estimates [C]:**

| Scenario | Candidates moved | Saving per qualification |
|---|---:|---|
| estate-core, unchanged between two qualifications | ≈150 | ≈150 × 12.5 s ≈ 31 worker-min ≈ 10 wall-min |
| Parsers and adapters, if split into their own libraries | ≈590 | ≈41 wall-min while unchanged |

**Survivors cost more than kills.** If survivors were spread evenly (the opening prefix had 7/15 and 9/39 [M]), a 14% smaller inventory also means about 14% fewer survivors in each find-and-fix screen, at 385 s each plus triage [A: uniform distribution].

### 5.2 How the B105 claim changes

**Today (A-462, `decisions.md:941`):** the claim covers every production `src/assay/**/*.py`. It holds only when the complete verifier-accepted report is bound to the unchanged source commit/tree.
- `tests/test_self_lane.py:128-135` pins the declared targets to `src/assay/**/*.py`.
- `tools/b105_report_check.py:14-104` binds commit, tree, lane, rigor, version and wheel sha256.

**Code imported from outside `src/assay` would pass every one of those checks and simply leave the claim.** That is a silent narrowing. **The honest replacement claim is:**

> assay source at commit C is qualified R0–R3 by assay's declared suite, **and** for each library L that assay embeds, `libraries/L` at subtree OID X (= the OID in C's tree) is qualified R0–R3 whole-target by L's declared suite in verdict V_L.

**Required binding** [I, a design sketch, not a decision]:
1. **The pin is a git subtree OID, not a wheel digest.** vbpub is a monorepo, so C's tree already contains `libraries/L`. Pin `git rev-parse C:libraries/L`, which covers L's source, tests and `assay.toml`. Any change to any of them forces requalification.
2. **V_L** must be `assay verify`-accepted, PASS, claim R0–R3, be `whole_target` over every `libraries/L/src/**/*.py` (with an L-side drift test like `test_self_lane.py:128-135`), and come from a commit C_L with `C_L:libraries/L == C:libraries/L`. Record `sha256(V_L)`.
3. **Judge and runtime compatibility.** V_L's `judge_provenance` and the A-471 runtime fingerprint must satisfy the same rules as assay's own verdict, or the claim must state the difference.
4. **The checker enforces it.** Extend `b105_report_check.py` with `--qualified-library L=<oid>:<V_L path>`. This avoids a verdict-schema cut. Putting the list into the verdict itself would be a v14/v15 schema change.
5. **The artifact matches.** `gate/distribution/build_release.py` vendors `libraries/L` from the same commit, so the zipapp/wheel digest already recorded in `judge_provenance` covers the vendored bytes. Add a test that the vendored files equal the qualified subtree.
6. **Accept the circularity.** L is part of the judge that judges L. This is the same self-judgment B105 already accepts, mitigated by the independent checker.

### 5.3 Interaction with A-005 (zero runtime dependencies)

**Constraints [M]:**
- A-005 is at `decisions.md:17`. `pyproject.toml:20-24` reads "ZERO RUNTIME DEPENDENCIES (decision A-005). stdlib only" with `dependencies = []`.
- `tests/test_dependency_purity.py:37` sets `ALLOWED_ROOTS = stdlib | {"assay"}`.
- A-402 (`decisions.md:844`) makes the zipapp the only install path into `tester-unified-go`: no pip, no ensurepip.

| Option | A-005 | A-402 zipapp | R2 effect | Verdict |
|---|---|---|---|---|
| **(a)** Real dependency (third-party or PyPI) | **Violates** it | Breaks it: no pip in the image | Removes the code from the inventory; qualification is outsourced to someone else's tests, so there is no assay-grade evidence | Not compatible. Reverses A-005 |
| **(b)** Vendor at build time from `libraries/L` (the worktree model) | `dependencies = []` survives, but the "stdlib only" wording and the purity test's `ALLOWED_ROOTS` must be amended | Compatible: bytes live inside the zipapp | Removes L from assay's R2; L is qualified in its own lane; bound as in §5.2 | **The only viable option. Needs new decisions:** an A-005 clarification, an A-462 scope amendment and the checker binding |
| (b′) Vendor under `src/assay/_vendor/L` | Same as (b) | Compatible | **None** unless excluded: `test_self_lane.py:128-135` puts it back into targets. Excluding it needs the same binding | Same decisions as (b), with no advantage |
| **(c)** A separate in-estate distribution as a declared dependency (the cli-extended model) | Violates it literally | Pip-less images still need it embedded, which collapses to (b) | As (b) | Dominated by (b) for assay |

---

## 6. Q4: DRY that needs no library

**Internal consolidation removes candidates outright, with no claim change** [C, from §2.4]:

| Change | Candidates removed | Notes |
|---|---:|---|
| One `@record` decorator (`dataclass_transform(frozen_default, kw_only_default)` wrapper) for the 80 dataclasses | ≈144 of 148 | About 4 sites remain in the wrapper; a reflective test kills them because `__dataclass_transform__` is observable at runtime. Each class's choice moves from a mutated bool literal to the choice of decorator, which no operator mutates. The A-468(d) contract fixture already pins every class's parameters, so that fixture, not mutation, is the evidence. About 144 cheap kills disappear. The gate runs only pyflakes (`gate/distribution/lint-requirements.txt`), so there is no type-checker risk |
| Guard helpers: `_strict_int(x, minimum)`, `_finite_positive(x)`, `_nonempty_str(x)`, `_sha256_hex(x)`; one set producer-side and a separate verify-side set | ≈111 | §2.2 families |
| One per-rigor correspondence helper in `verdict.py`, and a separate one in `verify.py`, plus `_raw_claim(claims, rigor)` in `verify.py` | ≈20 + ≈15 | Same rule instantiated for R1–R4 |
| Other repeated decisions and runs within one side | ≈100–230 | T1–T2 range |
| Standard-library flag wrappers: `_mkdirs` (13× `parents=True, exist_ok=True`), `canonical_json` (8× `sort_keys=True`), `strict_json_loads` | ≈30 | Also **fewer equivalent mutants.** A `mkdir` flag flip is often equivalent in a fresh tmp dir, and each equivalent costs an A-465 ledger entry plus an audit run. `strict_json_loads` unifies duplicate-key policy, which is a behavior change |
| Null objects for optional plumbing (progress, liveness calibration, diagnostics, reuse source) in `runner.py`/`mutation.py` | ≈30–60 [A] | Removes real branches; fewer R1 branches too |

**Total:** about 395–528 candidates (10.5–14%), about 27–37 wall-min per qualifying run, and proportionally fewer survivors and equivalents in the screen. **This is a larger saving than an estate-core library** (§5.1) **and it needs no new decision.**

**What consolidation changes about the evidence [I]:**
- After consolidation, a helper's mutant is killed by *any* caller's test. Today, each copy's mutant proves that *that* call site's boundary is exercised.
- The four operators never mutated call-site wiring (argument choice, helper choice), and R0/R1 still require every call site to run. So what is lost is a redundant re-test of the same rule, not coverage of a distinct decision.
- **A-466 is the precedent** (`decisions.md:961`): `errors.require_advance` was chosen because "a central helper adds no per-site mutants or uncovered branches".

**Where duplication must stay:**
- the `verify.py` twins (290 candidates, §3);
- the hand-transcribed R3 aggregation;
- the per-boundary path and JSON grammars **until** a contract ruling says which grammar is canonical. Their divergence may be intentional per trust boundary: `safeio` normalizes, `verdict` refuses backslashes. Merging them changes accepted inputs.

**Timing [I]:** every DRY change alters the candidate plan. Land it before the B110 pilot (or after the qualifying run), never between the pilot and the qualifying run. Otherwise the pilot's measurements no longer describe the plan.

---

## 7. Q5: Mutation hotspots

### 7.1 Overall site mix [M] (`hotspots.py`, `none_patterns.py`)

boolop 958 (25%) · `is None`/`is not None` 907 (24%) · dispatch (`==`/`is` against a constant or enum) 593 (16%) · boundary (`< <= > >=`) 410 (11%) · other equality 259 (7%) · bool keyword arguments 180 (5%) · other bool constants 170 (5%) · decorator flags 148 (4%) · falsy returns 135 (4%).

The 907 None-checks break down as 646 if-guards, 127 `x if x is not None else d`, and 58 type-narrowing `assert x is not None`.

**Boundaries (the classic off-by-one class) are only 11% of the inventory.** Optional plumbing and boolean composition dominate.

### 7.2 Top functions

| Candidates | Function | Site mix | Intrinsic or reducible |
|---:|---|---|---|
| 107 | `runner.py:3561-4763` `_run_prepared_lane` (1,202 lines) | none 52, boolop 22, flag-kwarg 16, dispatch 12 | **Partly reducible:** null objects for optional features (e.g. `liveness_calibration` ×4, `reuse_source`, `diagnostics`, `liveness_dir`, `progress_stream`): ≈−15. The early-claim/error sentinels are intrinsic orchestration state. Splitting the function alone removes nothing |
| 69 | `mutation.py:1912-2556` `run_mutation` | none 28, boolop 26 | As above; also the repeated budget and shard conjunctions (§2.2) |
| 58 | `verdict.py:4747-4940` `Verdict._check_judgment_matches_claims` | none 29, boolop 20 | **Reducible:** the R1–R4 rule written 4× (≈−20). Genuine, because the rule stays in code once |
| 44 | `adapters/sql_lex.py:174-287` `_lex_once` | dispatch 14, boolop 13, boundary 8 | **Intrinsic** scanner decisions. A master-regex rewrite would hide them |
| 40 | `runner.py:2800-3218` `_execute_snapshot_unit` | none 30 | Optional-result plumbing; partly reducible with a result object |
| 38 | `verify.py:2778-2942` `_check_r3_rederivation` | none 13, dispatch 12 | **Intrinsic and deliberate** (A-182) |
| 37 / 35 / 22 | `config.py` `_load_lane` / `_load_mutation` / `_load_judge` | boolop-heavy | **Partly reducible** via guard helpers. Cross-key rules are intrinsic |
| 36 | `verify.py:658-826` `_check_judgment_matches_claims` | boolop 25 | **Reducible within verify.py** (`_raw_claim`, the per-rigor loop). The twin itself stays |
| 57 | `analysis.py:417-580` `_ReportJSONFramer._char` + `_number_char` | dispatch 34, boundary 15 | See §7.3 |
| 31 | `mutation_witness.py:228-263` `witness_from_receipt` | falsy 9, dispatch 8 | A validation chain of `return None` exits; merging exits trims about 8. Mostly intrinsic |
| 28 / 24 | `verdict.py:2708-2971` `JudgmentR2.__post_init__` / `_check_native_policy` | none, boolop, boundary | Intrinsic cross-field rules; the finite-number guard (6 candidates ×2) is reducible |
| 24 | `liveness.py:1441-1539` `LivenessRunner._monitor` | boundary 9 | **Intrinsic** timing decisions |

### 7.3 Table-driven rewrites: genuine reduction or hiding the decisions?

**What the four operators still see in data:** only bool constants, because `ast.Constant` bool is flipped anywhere, including inside tables. They do **not** see `in`/`not in` (excluded by A-112, `adapters/python.py:445-453`), dict or tuple lookups, string or int constants, regex contents, `match`/`case` patterns, method calls such as `str.isdigit()` or `startswith`, or truthiness (`if x:`).

**Three kinds of rewrite:**
- **Genuine:** one rule instantiated N times becomes one helper plus a parameter table, for example the R1–R4 correspondence or the guard families. The rule's comparisons stay in code, mutated once, and killed by any instantiation. Keep a contract test that pins the table rows (the A-468(d) style).
- **Hiding:** boundary comparisons turned into `in`-sets, regexes or `isdigit`, as in the framer's `'0' <= char <= '9'` ×6 and `'1' <= char <= '9'` ×2 (`analysis.py:410-478`). The candidates disappear but every decision remains.

  **`str.isdigit()` also changes behavior:** it accepts Unicode digits, so `'²'.isdigit()` is `True`.

  The honest DRY version is one `_is_digit(c)` / `_is_digit19(c)` helper that keeps the comparisons. That removes about 12 of the 16 candidates without hiding anything.
- **Grey:** dispatch chains on state strings (`state == "minus"`, …). Their Eq→NotEq mutants carry little information, since any test through the path kills them. A transition table would remove about 34 of them from the framer. That is acceptable only if an explicit transition-table test replaces the lost evidence **and** the B105 report states that the candidate count fell because the decisions moved into data.

**Recommendation:** no rewrite whose only justification is the candidate count. Any change that lowers the inventory should be listed in the B105 report with its reason (DRY, dead-code removal, or disclosed moves into data).

---

## 8. Recommendations

| # | Change | Candidates removed [C] | Effort | Claim effect | New decision? |
|---|---|---:|---|---|---|
| 1 | Shared `@record` dataclass decorator (dataclass_transform wrapper) plus a reflective test of the wrapper | ≈144 | S | None | No (align with the A-468(d) fixture) |
| 2 | Guard helpers (strict int, finite positive, non-empty str, sha256 hex, aware datetime), per side | ≈111 | S–M | None | No; sharing leaf predicates across `verify.py` needs an A-182 ruling |
| 3 | Per-rigor correspondence helpers (`verdict.py`, and separately in `verify.py`) plus `_raw_claim` | ≈35 | S | None | No |
| 4 | Remaining same-side repeated decisions/runs (E/W at T1) | ≈100 (up to ≈230) | M | None | No |
| 5 | Standard-library flag wrappers (`_mkdirs`, `canonical_json`, `strict_json_loads`) | ≈30, plus fewer equivalents | S | None (`strict_json_loads` changes accepted inputs) | Small ruling on JSON policy |
| 6 | Null objects for optional plumbing in runner/mutation | ≈30–60 [A] | M–L | None | No |
| 7 | Path-grammar consolidation | ≈15 | M | Behavior change | Yes: which grammar is canonical per boundary |
| 8 | `libraries/estate-core` (safeio, bounded git core, canonical/strict JSON, sha256, duration), vendored (option b), bound by subtree OID plus V_L | ≈80–150 (≤197) | L | "assay + pinned qualified library" | **Yes:** A-005 wording, A-462 scope, checker binding, purity test |
| 9 | Split the stable internals (parsers ≈192, adapters ≈398) into separately qualified in-repo libraries | ≤590 while unchanged | L | Same as #8, for each library | **Yes**; not before B110 lands (P2 touches the adapters) |
| 10 | Third-party replacements (SQL lexer, incremental JSON) | ≈150–250 [A] | M | Evidence outsourced | **Yes: reverses A-005.** Not recommended |
| 11 | Table/regex rewrites of the framer or lexer for count reasons | 30–60 | M | Hides decisions | Not recommended (§7.3) |
| 12 | Keep the `verify.py` semantic twins | 0 | — | Preserves independence | No |

**Suggested order [I]:** do #1–#4 first. They take 10.5–14% off the inventory, save about 27–37 min of wall time per qualifying run, cause no claim change and need no new decision. Land them before the B110 pilot so it measures the real plan. Decide #8 and #9 only after B110's pilot shows whether the remaining time still misses 6 h. If it does, #9 (the stable internal pool) is worth about 4× #8, and both need the same A-005/A-462 decision and binding.

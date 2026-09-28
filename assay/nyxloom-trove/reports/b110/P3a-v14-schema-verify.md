# B110-P3a — Verdict schema v14 hard cut: model, schema, verifier, fixtures, carve assets

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | `assay-b110-p3a-schema`. It branches off `assay-b110-v14`, which is cut from the integration line `assay-b105-evidence-integrity` after the plan §11.1 reconciliation. |
| Depends on | P10a accepted: the ledger wire shape and the anchor grammar. If P10a is not accepted when you start, see the BLOCKED rule. |
| Contract class | **2b**. Public shapes are fixed below; the private construction is yours. |
| Implementer | Opus (fresh session) |
| Decisions | A-470 (D6, v14 cold-witness contract, A1–A9), A-465 (ledger wire fields), A-469 (D5 liveness disclosure fields), A-471 (runtime fingerprint field) |
| Size | L. This is a hard cut: ~50 fixtures, carve assets W10, gate markers, and about 15 test files with version literals. |

**What this package is.** It is the consumer side of v14, meaning the model, the JSON schema, the independent raw verifier, reconstruction and version plumbing. It has **no producer behaviour**:
- `run_mutation` never emits `witness-cold`, `ledger` or `evidence` here;
- the native producer emits only `cold_witness_kills: false`, `r2_command: null`, `equivalence_ledger: null`, and the liveness defaults.

P3b produces cold evidence, P3c produces the liveness values, and P10b produces the ledger.

**v14 integration-branch protocol (binding on P3a–P3d and P10b):**
1. `assay-b110-v14` is cut once from the integration-line tip.
2. Each sub-branch (`-p3a-`, `-p3b-`, `-p3c-`, `-p3d-`, P10b) branches from the **current** `assay-b110-v14` tip. After its own review and gate, it merges back `--no-ff`, **serially**, in the order P3a → P3b → P3c → P3d → P10b. P3c may merge before P3b if it is ready first.
3. If the integration line moves (for example P2 lands), the controller merges it into `assay-b110-v14` (`--no-ff`). Never rebase.
4. **Nothing from `assay-b110-v14` merges into the integration line until P3a–P3d and P10b are all merged, reviewed and green.** Then there is exactly one `--no-ff` merge into the integration line, followed by one `tester-unified` gate and one `self-qualification-preflight` run on the merge commit.
5. `VERDICT_SCHEMA_VERSION = 14` is set **in this package** and never partially. The branch is either wholly v13 or wholly v14.

---

## Context to read first

Paths are relative to `assay/`. Line numbers are verified at `db85f747`; re-anchor by symbol if they drift.

**Plan and decisions:**
- `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md` §3 (D6 A1–A9) and §5 (**the wire contract; this brief implements it exactly**).
- `nyxloom-trove/decisions.md`, rows A-461 (the v13 precedent), A-465, A-469, A-470, A-471.

**`src/assay/verdict.py`:**
- 337-350: the version history paragraph and `VERDICT_SCHEMA_VERSION = 13`.
- 1474-1510: `MutationWitnessReceipt`. Reuse it unchanged.
- 1512-1583: `MutationExecution`. The closed mode check is at 1522-1525; keep the substring `mode must be`, which `tests/test_b106_reuse_and_witness.py:340` pins.
- 1585-1775: `MutantOutcome`. The B106 fields are at 1647-1650, the all-or-none rule at 1705-1740, `to_dict` at 1768-1773.
- 1806-2010: `Mutation`. The non-killed witness rule is at 1978-1983.
- 2524-3111: `JudgmentR2`:
  - `_NATIVE_ONLY_FIELDS`/`_INGESTED_ONLY_FIELDS` at 2817-2842;
  - `_check_producer_fork` at 2843-2881, **including the `[:-3]` slice trap at 2856**;
  - `_check_native_policy` at 2883-2971, with the liveness exact-key check at ~2934-2970;
  - `to_dict` at 3061-3111.
- 4747-4861: `_check_judgment_matches_claims`, the R2 block. `_check_equivalence_pairing` is called at ~4805.
- 5038-5060: `_check_equivalence_pairing` (model).

**`src/assay/schemas/verdict.schema.json`:**
- 1-24: `$id` and the `schema_version` const.
- 1287-1323: `mutation_witness_receipt` and `mutation_execution` (a 2-branch `oneOf`).
- 1326-1480: `mutant_outcome` and the per-bucket rules.
- 1631-1909: `judgment_r2`. `liveness` is at 1730-1748; the native/ingested fork is at 1862-1909.

**`src/assay/verify.py`:**
- 1280-1305: `_check_equivalence_pairing` (raw).
- 1619-1738: `_check_b106_mutation_provenance`.
- 1741-1790: `_check_b106_execution`. Its "unknown execution mode" branch is at 1758-1760.
- 1793-1814: `_check_b106_receipt`. Reuse it unchanged; it already rejects bool exits.
- 2011-2082: `_reconstruct_judgment_r2` (`liveness=raw.get("liveness")` at 2079).
- 2163-2196: `_reconstruct_mutant_outcome`.
- 3012-3091: `verify_document`. The version gate is at 3027-3042 and the check order at 3060-3075.

**Everything else:**
- `src/assay/reuse.py`: 14 (`V12_COLD_START`), 50-90 (the version switch and the "current v13 verifier" message).
- `src/assay/runner.py`: 6033-6038 (reuse cold-start wording).
- `src/assay/cli.py`: 296-298 (the `--reuse-from` help, "native v13 verdict; v12 starts cold").
- `src/assay/mutation.py`: 1800-1840, `_outcome_of`. **A never-started budget leftover is `_outcome_of(job)`, i.e. `execution == MutationExecution(mode="full")` with no witness.** That fact is used below.
- `tools/tester-unified-gate.sh`: 570-660, the historical collect-only loop, hard-cut probe, W9 run, successors, B106 suite and markers.
- `tests/test_distribution_gate.py`: 215-256 (the marker names and order).
- `tests/test_gate_harness_version_pins.py` (whole file).
- `gate/python/qualify_topos.py`: 111 (`_EXPECTED_ROOT` → W9), 964-965 and 1021-1022 (literal 13), 1287, 1324, 1372-1373 (template names).
- `nyxloom-trove/carve-assets/W9/`: `test_acceptance_v13.py` (26-30 is the byte-identity assertion), `verdict.schema.v13.json`, `expected/*`, `MANIFEST.md`.
- `tests/test_verdict_v13_successors.py` (1-40).
- `tests/test_verdict_conformance.py`: 389-413 (every fixture is schema- and verify-valid), 1209, 1323.
- `tests/test_analysis.py`: 823 (**the replace trap**).
- The v13 cut itself, as a precedent: `git show --stat d43167c6` and `git diff e5e9b95c^1 e5e9b95c -- CHANGES.md`.

---

## Implementation packet (normative)

### Interfaces and grammar

**Owner `src/assay/r2_command.py` (NEW).** It is a pure, standard-library-only module. It is shared by the verifier (this package), the producer (P3b) and tests, following the `candidate_identity.py` precedent. Add it to **both** `judge.targets` lists in `assay.toml` (self-qualification ~108-165 and preflight ~218-275, sorted). `tests/test_self_lane.py:128-135` requires the declared targets to equal the discovered files. The module must reach 100% line+branch coverage from the ordinary suite.

```python
R2_TRANSFORM_ID: Final = "assay-r2-pytest-nocov/1"
R2_APPENDED: Final[tuple[str, ...]] = ("-p", "no:pytest_cov")

class UnrecognizedCoverageOption(ValueError): ...

def transform_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Remove exactly the recognized pytest-cov options, preserving every
    other token and its order.
    Removed: t == "--cov-branch"; t.startswith("--cov=") and len(t) > 6;
             t.startswith("--cov-report=") and len(t) > 13.
    Refused (raise UnrecognizedCoverageOption naming the token): any remaining
             t == "--cov" or t == "--no-cov" or t.startswith("--cov") or
             t.startswith("--no-cov").
    Never raises for any other token."""

def collection_digest(node_ids: Sequence[str]) -> str:
    """sha256 over b"".join(str(len(b)).encode("ascii") + b":" + b + b","
    for b in (n.encode("utf-8") for n in node_ids)), lowercase hex.
    ORDER- and DUPLICATE-preserving. NOTE: this is NOT
    assay.isolation.netstring (character length, no comma); do not reuse it."""
```

**Owner `src/assay/verdict.py`.** New frozen `kw_only` dataclasses:

```python
@dataclass(frozen=True, kw_only=True)
class R2BaselineFacts:
    collection_count: int            # int, not bool, >= 0
    collection_sha256: str           # 64 lowercase hex
    duplicates: int                  # int, not bool; MUST be 0
    hook_fingerprint_sha256: str     # 64 lowercase hex
    hook_count: int                  # int, not bool, >= 0
    runtime_fingerprint_sha256: str | None = None   # hex; REQUIRED on BOTH baselines (carver 2026-09-28, P10 OC12)
    wall_s: float | None = None                     # finite >= 0; required iff role == "r2"
    # validated by R2Command, which knows the role

@dataclass(frozen=True, kw_only=True)
class R2Command:
    transform: str                   # == R2_TRANSFORM_ID
    argv_declared: tuple[str, ...]
    argv_transformed: tuple[str, ...]   # == transform_argv(argv_declared)
    appended: tuple[str, ...]           # == R2_APPENDED
    cwd: str                         # non-empty POSIX, relative, no "..", e.g. "assay"
    config_sha256: str | None        # hex, or None when pytest found no inifile
    coverage_baseline: R2BaselineFacts  # runtime_fingerprint_sha256 required (differs from r2's by design: pytest-cov is loaded there, blocked in R2; no equality rule); wall_s is None
    r2_baseline: R2BaselineFacts        # both non-None
    # __post_init__: the transform equality, both duplicates == 0, and
    # coverage_baseline.collection_{count,sha256} == r2_baseline.collection_{count,sha256}

@dataclass(frozen=True, kw_only=True)
class EquivalenceLedger:
    path: str                        # non-empty POSIX project-relative, no "..", no leading "/"
    sha256: str                      # hex
    entry_count: int                 # int, not bool, >= 1
    audit_sha256: str                # hex

@dataclass(frozen=True, kw_only=True)
class MutantEvidence:
    command: str                     # "r2" | "declared"
    collection_count: int            # >= 0
    collection_sha256: str           # hex
    hook_fingerprint_sha256: str     # hex
    started_count: int | None = None
    failed_call_index: int | None = None
    # __post_init__: started_count and failed_call_index are both None or both int.
    # If both are int: command == "r2", started_count >= 1,
    # failed_call_index == started_count - 1, started_count <= collection_count.
```

**Changes to existing classes:**
- **`MutationExecution`** gains `anchor: str | None = None`. `mode ∈ ("full", "witness-prefix", "witness-cold", "ledger")`. The error text keeps `mode must be`.
  - `witness-cold`: `witness` required; `prior_*` and `anchor` must be None.
  - `ledger`: `anchor` required, a non-empty valid-UTF-8 string of ≤ 4096 bytes whose grammar is the one P10a pins; `witness`, `prior_*` must be None.
  - `full`/`witness-prefix`: `anchor` must be None.
  - `to_dict`: `witness-cold` → `{"mode","witness"}`; `ledger` → `{"mode","anchor"}`.
- **`MutantOutcome`** gains `evidence: MutantEvidence | None = None`. It is emitted as `"evidence"` **only when not None**. The ingested producer (no B106 fields) forbids it.
- **`JudgmentR2`** gains:
  - `cold_witness_kills: bool | None = None`;
  - `r2_command: R2Command | None = None`;
  - `equivalence_ledger: EquivalenceLedger | None = None`.
- **The `liveness` mapping** gains `cpu_window_s` and `idle_floor_s`. Both are a finite float > 0 when `active is True`, and `None` when `active is False`. The key set is exactly `{active, reason, plugin, cpu_window_s, idle_floor_s}`.

**The producer-fork rewrite removes the `[:-3]` slice trap.** Replace the positional slice with two named tuples:

```python
_NATIVE_REQUIRED_FIELDS = ("jobs", "max_mutants", "operators", "cold_witness_kills")
_NATIVE_OPTIONAL_FIELDS = ("equivalence_artifact", "budget_per_candidate_derived_s",
                           "liveness", "r2_command", "equivalence_ledger")
_NATIVE_ONLY_FIELDS = _NATIVE_REQUIRED_FIELDS + _NATIVE_OPTIONAL_FIELDS
```

- `required = _NATIVE_REQUIRED_FIELDS` for native.
- Ingested forbids every name in `_NATIVE_ONLY_FIELDS` that `is not None`.
- `cold_witness_kills` must be `type(x) is bool` (reject `0`/`1`).

**Native `to_dict` wire keys:**
- `cold_witness_kills` is **always** emitted.
- `r2_command` and `equivalence_ledger` are **always** emitted, as `null` when None.
- `liveness` keeps its current optional emission.
- None of the three new keys is emitted for ingested.

### Serialized examples

**`execution: witness-cold`**
- Valid: `{"mode":"witness-cold","witness":{"node_id":"tests/test_x.py::test_a","when":"call","outcome":"failed","session_exit_status":1,"process_exit_status":1}}`
- Invalid:
  - `{"mode":"witness-cold"}` (no witness);
  - `{"mode":"witness-cold","witness":{…},"prior_verdict_sha256":"<hex>"}` (prior field);
  - a receipt with `"process_exit_status":true` (bool);
  - `witness-cold` inside `survived`.

**`execution: ledger`**
- Valid: `{"mode":"ledger","anchor":"<P10a anchor string>"}`
- Invalid:
  - `{"mode":"ledger"}`;
  - `{"mode":"ledger","anchor":"a","witness":{…}}`;
  - `ledger` inside `killed`;
  - `ledger` when `judgment.r2.equivalence_ledger` is null.

**`evidence`**
- Valid on witness-cold: `{"command":"r2","collection_count":5700,"collection_sha256":"<hex>","hook_fingerprint_sha256":"<hex>","started_count":812,"failed_call_index":811}`.
- Valid on a survivor: the same with `"started_count":null,"failed_call_index":null`.
- Invalid:
  - `"command":"cov"`;
  - `failed_call_index` 810 with `started_count` 812;
  - `started_count` 5701 > `collection_count` 5700;
  - `started_count` set with `failed_call_index` null;
  - a missing key (all six keys are always present);
  - an extra key.

**`r2_command`.** The valid example is plan §5 verbatim, with `argv_transformed == transform_argv(argv_declared)`. Invalid:
- `argv_transformed` still containing `--cov-branch`;
- `appended == ["-p","no:pytest_cov","-x"]`;
- `coverage_baseline.collection_sha256 != r2_baseline.collection_sha256`;
- `duplicates: 1`;
- `r2_baseline.runtime_fingerprint_sha256` missing;
- `coverage_baseline.runtime_fingerprint_sha256` missing;
- `coverage_baseline.wall_s` present;
- `transform: "assay-r2-pytest-nocov/2"`.

**`equivalence_ledger`**
- Valid: `{"path":"mutation-equivalence-ledger.toml","sha256":"<hex>","entry_count":3,"audit_sha256":"<hex>"}`.
- Invalid: `entry_count: 0`; `path: "../x"`; a missing `audit_sha256`.

**`liveness` additions**
- Valid: `{"active":true,"reason":null,"plugin":"...","cpu_window_s":30.0,"idle_floor_s":15.0}`.
- Invalid:
  - `{"active":false,…,"cpu_window_s":30.0}` (must be null when inactive);
  - the 3-key v13 dict (missing keys);
  - `"cpu_window_s": true`.

### Cross-object rules (model `Verdict._check_cold_witness_policy` and raw verify must BOTH enforce these)

Add the model check after `_check_equivalence_pairing` in the R2 block of `_check_judgment_matches_claims`, around verdict.py:4805-4861. Here `P = judgment.r2`, `M = claim[R2].mutation`, and "payload" means `M` is not None.

| # | State | Rule |
|---|---|---|
| X1 | native `P` | `cold_witness_kills` is a bool; ingested: absent |
| X2 | `cold_witness_kills is False` | `r2_command is None`; no outcome has `evidence`; no `witness-cold` execution anywhere |
| X3 | `cold_witness_kills is True` and payload present (including the `MUTANT_LIMIT_EXCEEDED` sentinel) | `r2_command` is not None |
| X4 | R2 claim payload-free (refusal/error) | `r2_command is None`, regardless of `cold_witness_kills` |
| X5 | `witness-cold` outcome | bucket is `killed`, `cold_witness_kills is True`, `evidence` is not None with non-null `started_count`, `evidence.command == "r2"` and `evidence.collection_{count,sha256} == r2_baseline.collection_{count,sha256}` and `evidence.hook_fingerprint_sha256 == r2_baseline.hook_fingerprint_sha256` |
| X6 | `survived` outcome, `cold_witness_kills is True` | `evidence` required; `started_count is None`. `command == "r2"` → the digests match `r2_baseline` (collection and hook). `command == "declared"` → `collection_{count,sha256}` match `coverage_baseline` and `hook_fingerprint_sha256 == coverage_baseline.hook_fingerprint_sha256` |
| X7 | `killed` with `full`/`witness-prefix`, or `crashed`/`hung`/`budget_exceeded` | `evidence` optional; if present, `started_count is None` |
| X8 | `equivalent` bucket non-empty on native | `equivalence_ledger` is not None, every equivalent has `mode == "ledger"` and no evidence, and `len(equivalent) == equivalence_ledger.entry_count` |
| X9 | `mode == "ledger"` anywhere | bucket is `equivalent` and `equivalence_ledger` is not None |
| X10 | `equivalence_ledger` not None | `equivalence_artifact is None` (SQL-only; both declared is refused) |
| X11 | `r2_command` not None | `r2_command.argv_declared == document.argv_declared` (top level; raw verify only; the model has no document-level argv in scope) |

`_check_equivalence_pairing` (model 5038-5060 and raw 1280-1305) now reads: the `equivalent` bucket requires **either** `equivalence_artifact` **or** `equivalence_ledger`.

**The `Mutation`-level rule at 1978-1983** becomes: a non-killed bucket cannot carry a witness, a `witness-prefix` or a `witness-cold`. A non-equivalent bucket cannot carry a `ledger`.

### Required flow (verify)

1. The version gate (3027-3042) is unchanged in shape; the version is 14. A v13 document yields exactly the existing single diagnostic with 13 and 14 substituted.
2. `_check_b106_mutation_provenance` reads `policy.get("cold_witness_kills")`. For native it requires `type is bool`. It passes `cold_policy=` and `ledger_declared=` (keyword-only, with defaults `None`/`False`) into `_check_b106_execution` (positional callers in `tests/test_b106_reuse_and_witness.py:143-202` stay valid).
3. `_check_b106_execution` adds two branches before the unknown-mode refusal at 1758:
   - `witness-cold`: keys exactly `{"mode","witness"}`; `bucket == "killed"`; `cold_policy is True`; `_check_b106_receipt`.
   - `ledger`: keys exactly `{"mode","anchor"}`; `bucket == "equivalent"`; `ledger_declared is True`; anchor type and bound.
4. The new `_check_v14_evidence(bucket, entry, policy, failures)` runs per outcome. It checks the raw key set of `evidence` (exactly six keys), the types (`type(x) is int`, rejecting bool) and X5–X7.
5. The new `_check_v14_r2_command(document, policy, claim, failures)`:
   - checks the key sets of `r2_command`, of each baseline (`coverage_baseline` has 5 keys; `r2_baseline` has 7) and of `equivalence_ledger`;
   - re-applies `r2_command.transform_argv`;
   - checks the equalities, `duplicates == 0` and X2–X4, X8–X11.
6. **Register every new wire field in reconstruction in this same commit.** `_reject_unknown_keys` is top-level only; this is the A-323 lesson.
   - `_reconstruct_judgment_r2` adds the three fields, building the dataclasses from the raw dicts.
   - `_reconstruct_mutant_outcome` adds `anchor` to `MutationExecution(...)` and `evidence`.
7. `_check_r2_rederivation` is unchanged: `killed` is killed regardless of execution mode.

### Topology

The model and verify see only the wire document. There are no paths to resolve: `r2_command.cwd` and `equivalence_ledger.path` are recorded strings, checked for shape only. The ledger file itself is not read by `assay verify`; P10b's audit binding does that.

### Decision table (verifier outcomes)

| Input | Result |
|---|---|
| v13 document | exactly one version diagnostic; nothing else is checked |
| v14 native with the key `cold_witness_kills` missing | failure: "native judgment.r2 requires cold_witness_kills" |
| v14 native cold false, any `evidence` | failure (X2) |
| v14 cold true, `witness-cold` in `survived` | failure |
| v14 cold true, survivor without evidence | failure (X6) |
| v14 cold true, payload present, `r2_command` null | failure (X3) |
| v14 native, `equivalent` non-empty, ledger null, artifact null | failure (pairing) |
| v14 ingested with `cold_witness_kills` present | failure (fork) |
| every fixture in `tests/fixtures/verdicts/` after migration | `[]` |

### Bounds

- A receipt `node_id` is ≤ 4096 UTF-8 bytes (existing).
- An `anchor` is ≤ 4096 UTF-8 bytes.
- `argv_*` elements: no new bound. They are the lane's own argv, already bounded by lane loading.
- No list of node IDs ever appears in the verdict: the manifest is a digest plus a count only.

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| `transform_argv` | `r2_command.py` | table test of 12 argv cases, including the B105 argv, an identity case, and `--cov src` (two-token → refused) | `tests/test_r2_command.py` (new) | remove the `len(t) > 6` guard → `--cov=` is silently accepted → the test goes red |
| `collection_digest` | `r2_command.py` | digest of `[]`, of `["a"]`, of `["é"]` (byte length 2 ≠ char length 1), of `["a","a"]` ≠ `["a"]`, and of `["a","b"]` ≠ `["b","a"]`. The expected hex values are computed in the test by an independent inline `hashlib` expression | same | swap to `isolation.netstring` → the `é` case goes red |
| `witness-cold` model/schema/verify | verdict/schema/verify | 4 invalid + 1 valid per the examples above, each through **both** the model constructor and `assay verify` on a JSON document | `tests/test_v14_contract.py` (new) | drop the bucket check in raw verify only → the model rejects but raw accepts → a paired test fails |
| evidence rules X5–X7 | same | one test per X-row with a minimally mutated copy of the carver fixture below | same | drop the X6 `declared` branch → the declared-survivor fixture is refused → red |
| ledger rules X8–X10 | same | valid ledger PASS; the 4 invalid cases | `tests/fixtures/verdicts/r2_pass_cold_witness_ledger.json` | allow `ledger` in `killed` → red |
| r2_command rules | same | each invalid example | same test file | skip re-applying the transform → the `--cov-branch`-left-in case is accepted → red |
| hard cut | verify/schema | every v13 fixture copy (`schema_version` patched back to 13) gets exactly one version diagnostic | `tests/test_v14_contract.py` | — |
| producer defaults | runner/mutation | a real small native R2 run (existing `make_lane`) emits `cold_witness_kills: false`, `r2_command: null`, `equivalence_ledger: null`, and liveness with 30.0/15.0 when active | existing `tests/test_cli_run.py` exact-dict pins, updated | — |

**Carver-authored expected artifact (normative).** Build `tests/fixtures/verdicts/r2_pass_cold_witness.json` from `r2_pass.json`:
- set `schema_version` to 14;
- make `judgment.r2` exactly plan §5's native block;
- set `argv_declared` to `["pytest","tests","-q","--cov=pkg","--cov-branch","--cov-report=json:cov.json"]`, and set the top-level `argv_declared` to the same;
- `argv_transformed` is then `["pytest","tests","-q"]`;
- give the baselines equal collection facts (`collection_count: 3`, one sha computed by `collection_digest(["tests/test_x.py::test_a","tests/test_x.py::test_b","tests/test_x.py::test_c"])`), different hook digests, `hook_count` 9 and 7, and `wall_s: 1.5`;
- its killed entry gets `"execution":{"mode":"witness-cold","witness":{"node_id":"tests/test_x.py::test_b",…}}` and `"evidence":{"command":"r2","collection_count":3,"collection_sha256":<same>,"hook_fingerprint_sha256":<r2 hook>,"started_count":2,"failed_call_index":1}`;
- add a second killed entry with `mode:"full"` and no `evidence` key. A PASS has no survivors.

The expected verify result is `[]`. Its sibling `r2_fail_cold_witness_declared_survivor.json` is a FAIL/MUTANTS_SURVIVED with one survivor carrying `evidence.command == "declared"` that matches the coverage baseline. Its expected verify result is also `[]`.

A third fixture, `r2_pass_cold_witness_ledger.json`, is the first fixture plus:
- `judgment.r2.equivalence_ledger = {"path":"mutation-equivalence-ledger.toml","sha256":<64 hex>,"entry_count":1,"audit_sha256":<64 hex>}`;
- one `equivalent` entry with `"execution":{"mode":"ledger","anchor":<an anchor string valid under P10a's accepted grammar>}` and no `evidence`.

Its expected verify result is `[]`.

### Degrees of freedom

- Private helper names, and decomposition inside verify/verdict.
- Error message wording, **except** that messages must name the offending field path (for example `mutation.killed[0].evidence.started_count`) and keep `mode must be`.
- Nothing serialized above is a degree of freedom.

---

## Work

1. **Branch.** Create `assay-b110-p3a-schema` from `assay-b110-v14`, in a worktree under `/workspaces/vbpub/.worktrees/`. The gate launcher refuses other roots.
2. **Red first.** Add `tests/test_r2_command.py` and `tests/test_v14_contract.py`, plus the two carver fixtures. Run them and record that they fail (import error / version 13).
3. **Create `src/assay/r2_command.py`** per the packet. Add it to both `judge.targets` lists in `assay.toml`, sorted. Run `tests/test_self_lane.py`.
4. **Update `verdict.py`:**
   - add the new dataclasses;
   - extend `MutationExecution` and `MutantOutcome`;
   - rewrite the producer fork (**remove the `[:-3]` slice**);
   - extend `_check_native_policy` (the liveness 5-key set; the type of `cold_witness_kills`);
   - add `_check_cold_witness_policy` (X1–X10);
   - extend `Mutation` 1978-1983 and `_check_equivalence_pairing`;
   - update `to_dict` emission.
   - Then bump `VERDICT_SCHEMA_VERSION = 14` and add a "Bumped 13 → 14 (B110)" history paragraph after 337-349, naming A-470/A-465/A-469/A-471.
5. **Update the schema (`verdict.schema.json`):**
   - `$id` `urn:assay:schema:verdict:14`; const 14;
   - new `$defs`: `r2_baseline_facts`, `r2_command`, `equivalence_ledger`, `mutant_evidence`;
   - two new `mutation_execution` `oneOf` branches (`witness-cold` requires `mode`+`witness`; `ledger` requires `mode`+`anchor`; both `additionalProperties:false`);
   - `mutant_outcome.evidence`;
   - `judgment_r2` properties;
   - the native `required` list (1876-1880) gains `cold_witness_kills`, `r2_command`, `equivalence_ledger`;
   - the ingested `not required` list (1897-1904) gains all three;
   - the `liveness` object gets the 5 required keys.

   The schema cannot express X5–X11; the model and verify own those, the same layering as today.
6. **Update `verify.py`** per the "Required flow (verify)" steps 2–6, including reconstruction in the same commit.
7. **Producer defaults** (no new behaviour). In `runner._build_judgment_r2` (runner.py:4964-5041):
   - pass `cold_witness_kills=False`, `r2_command=None`, `equivalence_ledger=None`;
   - extend the liveness dict with `cpu_window_s=30.0, idle_floor_s=15.0` when active, and `None` when inactive.
   - Import the defaults from `liveness._HUNG_CPU_WINDOW_S` and `liveness.LIVENESS_IDLE_FLOOR_S`; never re-type the literals.
8. **Update `reuse.py`:**
   - replace `V12_COLD_START = 12` with `COLD_START_VERSIONS = frozenset({12, 13})`;
   - any version in that set is a bounded cold start that trusts only the version and consumes nothing. The version is 12 or 13 exactly; ≤ 11 stays refused as today;
   - update the messages at 57-85 ("current v14 verifier", "expected 12 or 13 cold start or current version 14");
   - update `runner.py:6033-6038` and the `cli.py:296-298` help ("native v14 verdict; v12 and v13 start cold");
   - a prior `witness-cold` kill is replay-eligible (`reuse.classify_candidate` needs only `execution.witness`). Add a test that proves it with a v14 prior document.
9. **Fixtures.**
   - Set `"schema_version": 14` in all 50 `tests/fixtures/verdicts/*.json`.
   - The 11 native-R2 fixtures listed in the v14 implementation map (`inconclusive.json`, `r2_*.json`) gain `cold_witness_kills: false`, `r2_command: null`, `equivalence_ledger: null`. If they carry an active liveness block, it gains the two keys.
   - `tests/test_verdict_conformance.py:389-413` must stay green over the full set.
10. **Carve assets.**
    - Leave `carve-assets/W9/` byte-unchanged.
    - Create `carve-assets/W10/`:
      - `verdict.schema.v14.json` (a byte copy of the shipped schema);
      - `expected/p25-pass-v14-template.json` and `expected/p25-missing-v14-template.json` (the W9 templates lifted to 14, with the three native keys added if they carry native R2);
      - `test_acceptance_v14.py`, modelled on W9's (byte identity, both templates schema- and verify-valid, W9 templates hit the v14 hard cut);
      - `MANIFEST.md`.
    - Point `gate/python/qualify_topos.py:111` `_EXPECTED_ROOT` at W10. Change the literal 13 at 964-965 and 1021-1022 to `VERDICT_SCHEMA_VERSION`-driven or 14, as `tests/test_gate_harness_version_pins.py` demands, and the template names at 1287/1324/1372-1373 to `-v14-`.
11. **Successor tests.**
    - `git mv tests/test_verdict_v13_successors.py tests/test_verdict_v14_successors.py`.
    - Lift W8's R3/R4 controls **and** W9's two P25 templates to 14 in it; the module docstring explains v14.
12. **Gate script** (`tools/tester-unified-gate.sh`):
    - add `"nyxloom-trove/carve-assets/W9/test_acceptance_v13.py"` to the historical collect-only loop (~576-584);
    - `assert VERDICT_SCHEMA_VERSION == 14` (599);
    - add `("W9", 13)` to the hard-cut probe tuple (602-610);
    - rename the marker `verdict-v6-v12-hard-cut-verified` → `verdict-v6-v13-hard-cut-verified` and its print text;
    - run W10 in place of W9, with the marker `verdict-v14-p25-successors-verified`;
    - point the successors file at `test_verdict_v14_successors.py`;
    - after the B106 suite, add `tests/test_v14_contract.py` and `tests/test_r2_command.py`, with the marker `verdict-v14-successors-verified` replacing `verdict-v13-successors-verified`.

    Update `tests/test_distribution_gate.py:215-256` to the new markers, file names and order (`v13_cut < v14_p25 < v14_successors < v14_suite < v14 < self_hosted`).
13. **Version-literal tests.** Update every literal `13` that means the schema version:
    - `tests/test_standalone.py` 343, 702, 1231, 1600;
    - `tests/test_verify_layer_independence.py` 111, 515;
    - `tests/test_verdict_conformance.py` 1209 and the message at 1323;
    - `tests/test_reuse_coverage_controls.py` 19, 93;
    - `tests/test_b106_reuse_and_witness.py` 96 and the `_v13_*` helpers (rename them `_v14_*` or make them version-driven);
    - `tests/test_verdict_schema_is_packaged.py:267` → `urn:assay:schema:verdict:14`.

    **Trap:** `tests/test_analysis.py:823` does `text.replace('"schema_version": 13', '"schema_version": 11')`. At v14 that replace silently does nothing. Change it to replace `"schema_version": 14`, and add an `assert '"schema_version": 11' in text` after the replace, so a future cut cannot hollow it again.
14. **Hand-built native `JudgmentR2`/raw r2 dicts** in the test files the map lists must gain `cold_witness_kills=False`:
    - `test_verdict_judgment.py` (27);
    - `test_verdict_mutation_artifacts.py` (6);
    - `test_runner_assemble_verdict_mutation.py`, `test_verdict_b105_constructors.py`, `test_verdict_interval_and_unsupported.py`, `test_verdict_claims.py`, `test_verdict_serialises.py`, `test_verify_layer_independence.py` (5 raw), `test_reuse_coverage_controls.py` (5), `test_standalone.py`, `test_verify_raw_b105.py`;
    - the exact `judgment.r2` dict pins in `tests/test_cli_run.py` (~409-443, 576-577, 672, 760), `tests/test_standalone.py:775` and `tests/test_verdict_judgment.py:549`.

    Use `grep -rn "producer=\"native\"\|'producer': 'native'\|\"producer\": \"native\"" tests/` to find the rest.
15. **Docs sync** (below). CHANGES `## [Unreleased]`.
16. **Run** the focused suites, then the gate (below). Write the report.

## Oracles

Each oracle names its observable, the negative case (what a broken implementation does), and the gate that checks it.

1. **Hard cut.**
   - Observable: `assay verify` on any v13 fixture copy returns exactly one diagnostic naming 13 and 14.
   - Negative: a verifier that migrates v13 in place returns `[]`.
   - Gate: `tests/test_v14_contract.py` plus the gate's hard-cut probe with `("W9", 13)`.
2. **`witness-cold` is killed-only and policy-bound.**
   - Observable: the valid fixture gives `[]`. Each of these fails in the model **and** in raw verify: witness-cold on a survivor; with `prior_verdict_sha256`; with `cold_witness_kills: false`; with a bool exit status; with an extra key.
   - Negative: a verifier checking only the schema accepts a survivor with a witness-cold (the schema cannot express the bucket).
   - Gate: tester-unified.
3. **Evidence binds to the right baseline.**
   - Observable: a `declared` survivor matching the coverage baseline is `[]`. The same survivor with `command:"r2"` fails, because the hook digest differs.
   - Negative: comparing only the collection digest accepts it.
   - Gate: tester-unified.
4. **Transform re-derivation.**
   - Observable: an `r2_command` with `--cov-branch` left in `argv_transformed` fails.
   - Negative: trusting the producer's `argv_transformed` accepts it.
   - Gate: tester-unified.
5. **Ledger pairing.**
   - Observable: a native equivalent with ledger null and artifact null fails. A ledger with `entry_count` ≠ the number of equivalents fails. A `ledger` in `killed` fails.
   - Gate: tester-unified.
6. **Ingested unaffected.**
   - Observable: every existing ingested fixture verifies `[]` after the version bump. Adding `cold_witness_kills` to one fails.
   - Gate: tester-unified.
7. **The producer fork has no positional trap.**
   - Observable: a test constructs a native `JudgmentR2` with every optional field None and `cold_witness_kills=False` → OK. Without `cold_witness_kills` → `ValueError` naming it.
   - Negative: keeping `[:-3]` after adding fields silently makes `liveness` required or `cold_witness_kills` optional.
8. **Reuse cold start.**
   - Observable: `--reuse-from` a v13 verdict gives `cold_start=True` and consumes nothing. v11 is refused with the updated message. A v14 prior with a witness-cold kill yields that candidate as replay-eligible.
   - Gate: tester-unified (`tests/test_b106_reuse_and_witness.py` extended).

### Forbidden oracle patterns (AUTHORING.md §3b, verbatim)

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

Verify each anchor with `grep -n` first.

- **README.md:**
  - :71 "current v13 verdict schema" → v14;
  - :152-167 (the B092/B106 bullets): judge identity is unchanged here (P3b changes it);
  - add a v14 line to the version-compatibility text if present.
- **docs/DESIGN-GUIDE.md:**
  - §6 verdict contract (:402ff): document the three new native `judgment.r2` fields, `evidence`, and the `witness-cold`/`ledger` modes as *wire contract* (the semantics paragraphs come with P3b/P10b);
  - the hard-cut policy (~:1477-1485): add v14 and the v12/v13 reuse cold start;
  - fix the stale "`schema_version: 8`" at ~:1446 if still present.
- **docs/CONSUMERS.md:**
  - :2746 "Since verdict schema v13 …": add the v14 fields;
  - :2885 "Verdict schema v13 and lane schema v2 are both hard cuts" → v14;
  - the `--reuse-from` cold-start text: v12 and v13.
- **CHANGES.md** `## [Unreleased]` (above `<!-- cmru: release history -->`): `### Changed` "Verdict schema v14 (hard cut): …"; `### Testing` W10.

## Scope / forbid

- **Touch:** `src/assay/{verdict.py,verify.py,reuse.py,r2_command.py}`, `src/assay/schemas/verdict.schema.json`, `src/assay/runner.py` (only `_build_judgment_r2` defaults and the reuse wording), `src/assay/cli.py` (help text only), `assay.toml` (the targets lists only), `gate/python/qualify_topos.py` (version/W10 only), `tools/tester-unified-gate.sh` (the markers section only), `nyxloom-trove/carve-assets/W10/**` (new), `tests/**` as listed, README/DESIGN-GUIDE/CONSUMERS/CHANGES.
- **Forbid:**
  - any producer behaviour: no `witness-cold`/`evidence`/`ledger` emission, and no plugin changes (`mutation_witness.py` is P3b's);
  - `mutation.py` except reading it;
  - `MUTATION_STATE_SCHEMA_VERSION` (never bump it);
  - `carve-assets/W1–W9` content;
  - lane schema;
  - a new `ReasonCode`;
  - `liveness.py` (P3c).

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_r2_command.py tests/test_v14_contract.py tests/test_verdict_conformance.py tests/test_b106_reuse_and_witness.py tests/test_self_lane.py tests/test_distribution_gate.py tests/test_gate_harness_version_pins.py -q -p no:cacheprovider`
2. Run the full local suite once, serially and niced, as a pre-gate sanity check (not the gate).
3. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3a-gate.log 2>&1; echo EXIT=$?`
4. In a **separate** step, read `ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1` and the new `verdict-v14-successors-verified` marker from the log (L4: never pipe-tail).
5. Also run `python ./run-gate.py self-qualification-preflight`, because `assay.toml` targets changed. It must pass R0/R1, including 100% line+branch over `r2_command.py`.
6. Never run the `self-qualification` lane.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- a §5 wire shape conflicts with an existing invariant you cannot preserve;
- P10a's anchor grammar is not accepted when you reach the `ledger` validation;
- the gate needs a file outside the touch list.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3a-REPORT.md`, commit, and exit. Do NOT improvise a workaround. **Special case:** if P10a is not yet accepted, validate `anchor` as non-empty UTF-8 ≤ 4096 bytes only, record `BLOCKED-PARTIAL: anchor grammar pending P10a` in the report, and finish everything else. P10b then adds the grammar check.

## Report

Write `nyxloom-trove/reports/assay-B110-P3a-REPORT.md` with:
- the commit(s);
- the traceability table repeated with actual file/test names and red-first failure counts;
- the gate log path and the markers read;
- the preflight result;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; the controller merges after an independent review (fresh session, never a fork).

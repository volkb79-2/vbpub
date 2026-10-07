# B110-P3d — B105 gate passes `--cold-witness`; source-bound report checker binds the v15 evidence and the campaign deadline

## Current contract reconciliation — 2026-10-07

This gate/report binding targets verdict v15; v14 shipped in Assay 8.0.0.
Implement it on the CIU-managed `assay-b114-cold-witness` branch after B117/P6
and the B114 producer are present. Keep the independent source-bound checker
refusing all non-null ledger declarations until P10a's REVISE findings are
resolved and P10b is separately implemented. The current release gate remains
tester-unified; the B105 full-source self-qualification is a separately invoked
campaign requiring its bounded pilot/go decision.

**Revised 2026-09-28 after round-1 and round-2 reviews (see REVIEW-2026-09-28-round1.md, REVIEW-2026-09-28-round2.md).** Round 1 applied P3D-1..P3D-6, the wrong anchors, and carver decision C9. Round 2 applied P3D2-1 (the `sed` extraction is mandated and the stub python prints an integer), P3D2-2 (the zz_slow test path) and P3D2-3 (C12 limited to survived and witness-cold), plus C30 (tests are found by name after P1).

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | CIU-managed `assay-b114-cold-witness`; the historical v14 integration-branch protocol is superseded for this serial implementation. |
| Depends on | The B114/P3b cold-witness producer and P3a v15 `CampaignBinding` model/schema/verify in this same worktree; B111/P0's shipped source-bound report checker and partial-scope refusals; and B117/P6's campaign-deadline command/wiring. The current implementation is serial in `assay-b114-cold-witness`; no historical `assay-b110-v14` integration branch is created. |
| Contract class | **2c** |
| Implementer | Sonnet (fresh session) |
| Decisions | A-470 (D6), Wave A W4's current B105 lane argv, A-474 (checker refusals), A-473 (deadline), carver decision **C9** (the verdict binds its deadline) |
| Size | M |

**What this package is.**
- The registered `self-qualification` gate invokes its R2 with the cold-witness policy and retains the ordered R2 manifest sidecar.
- **Producer wiring (C9):** when `assay run` gets `--campaign-deadline PATH`, the verdict carries P3a's top-level `campaign` block. The block is built from the deadline file P6 already loads. Every verdict-producing path in `_run_reserved` carries it, including refusals and LANE_TIMEOUT verdicts.
- `tools/b105_report_check.py` is B105's **source-bound** check that runs after `assay verify`. It binds the v14 facts and the deadline to the exact source revision:
  - re-derives the declared argv and the pytest config from `git show <commit>:…`;
  - re-applies the transform **independently**;
  - requires the cold policy;
  - recomputes the manifest digest from the sidecar and checks every cold kill's witness node against its manifest position;
  - requires `--deadline FILE` and matches the verdict's `campaign` block to that file by sha256.

`assay verify` checks internal consistency. This checker is what makes "the transform matches the lane that was actually committed" true: the backlog says "a self-reported argv hash is not source proof". Its checks are labelled **source-bound** or **consistency** below; only the source-bound ones are source proof.

---

## Context to read first

Paths are relative to `assay/`, verified at `db85f747`.

- `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §5 (the wire contract, the `collection_sha256` definition) and §9.3.
- `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`: the packet (the `R2Command`, `MutantEvidence` and X-rules; the `collection_digest` definition).
- `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`: `CampaignBinding` (C9), the valid example and its invalid examples; X13.
- `nyxloom-trove/reports/b110/P3b-r2-command-cold-witness.md`: "Owned interfaces" (`--r2-manifest`; the sidecar format is one node ID per line, each followed by `\n`, UTF-8).
- `nyxloom-trove/reports/b110/P6-campaign-deadline.md`: the deadline document (schema `assay-campaign-deadline/1`, its keys, `sort_keys=True, indent=2`), and gate integration Flow 10–12. The gate variables are `campaign`, `deadline`, `remaining_s` and the `timeout … --campaign-deadline "$deadline"` wrap.
- `nyxloom-trove/reports/b110/P0-measurement-hygiene.md` W5: `--plan-json`, `check_campaign_scope`, refusals 1–9, and the gate's `plan_path` ordering pin.
- `tools/b105_report_check.py` (the whole file, ~150 lines):
  - `verify_report_document` at 14-107: exit, commit, lane, rigor, tree via `git rev-parse <commit>^{tree}` at ~46-61, PASS, claims, version, judge provenance;
  - `main` / argparse at 110-147. The exception tuple at 139 is `(OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError)` → exit 2. A `KeyError`/`TypeError`/`IndexError` escapes it today and exits 1.
- `tools/self-qualification-gate.sh` (lines at `db85f747`; P6 and P0 add lines in the base):
  - **156-214**: `run_and_verify_lane`. The `assay run` invocation is at ~183-189, `assay verify` at **195**, and the checker call at ~198-208;
  - 175-179: the qualification lane `unset`s the four `ASSAY_B105_*` variables;
  - 216-246: the lane sequencing and the `B105_*` echoes.
- `src/assay/cli.py`: `_run_reserved` (1124-~1560). `judge_provenance` is threaded into every verdict-producing call, at **1315, 1404, 1450, 1488 and 1541** (`runner.refuse_lane(...)` / `runner.run_lane(...)`). The `campaign` binding follows the same path.
- `src/assay/runner.py`: the `refuse_lane` / `run_lane` / `assemble_verdict` parameters that carry `judge_provenance` into `Verdict(...)`. Follow `judge_provenance` with grep; the `campaign` kwarg follows it exactly.
- `run-gate.toml`: 33-52, the `self-qualification` lane and its `artifacts` list at 46-52.
- `assay.toml`: the `self-qualification` lane at 78-182 (`env = {}` and `env_passthrough = ["PATH"]` at 91-92), and `pyproject.toml` at the lane cwd (`assay/pyproject.toml`).
- `tests/test_b105_report_check.py`:
  - `_git_value` at 23;
  - `_verifier_valid_report` at 34-75, which splices `r2_pass.json` into `pass.json` and must stay verifier-valid;
  - `_verify_with_assay_cli` at 78;
  - `_run_checker` at 94, which runs the checker **as a subprocess**;
  - the tests at 142-211.
- `tests/test_self_lane.py`:
  - ~150-170: the `artifacts` pins for both lanes;
  - ~170-205: the gate-script substring pins (`"--resume" in script`, `'"$scratch/source/assay/tools/b105_report_check.py"' in script`, …), plus P0's and P6's pins.
- `tests/fixtures/verdicts/r2_pass_cold_witness.json`, the carver fixture created by P3a.

---

## Implementation packet (normative)

### Gate script changes (`tools/self-qualification-gate.sh`)

P6's wiring is **already in the base**: the `campaign`/`deadline` variables, `campaign init`, `B105_CAMPAIGN_DEADLINE=`, and the `timeout … "$assay_bin" run "$lane" … --campaign-deadline "$deadline"` wrap. Keep it exactly; do not rename its variables and do not add a P6 TODO. P0's `plan_path` / `"$assay_bin" plan "$lane"` step, placed before `run`, is also in the base.

Inside `run_and_verify_lane`, add a per-lane `lane_flags` array. **Only** the `self-qualification` lane gets the cold flags:

```bash
  local r2_manifest_path=".assay/r2-manifest-$lane.txt"
  local -a lane_flags=()
  case "$lane" in
    self-qualification)
      rm -f -- "$r2_manifest_path"          # never let a stale sidecar from an earlier invocation be checked
      lane_flags+=(--cold-witness --r2-manifest "$r2_manifest_path")
      # B110-P10c: pass --equivalence-audit "<commit-bound receipt path>" here when the lane declares a ledger (P10c adds it).
      ;;
  esac
```

Append `"${lane_flags[@]}"` as the **last** argument of the existing (P6-wrapped) `"$assay_bin" run "$lane" …` command, so it is actually expanded into the argv.

- The script runs under `set -u`, and `"${lane_flags[@]}"` on an **empty** array is safe only on bash ≥ 4.4. Work step 1 records `bash --version` in the tester-unified image.
- If that is < 4.4, write `${lane_flags[@]+"${lane_flags[@]}"}` instead, and pin that exact spelling.

The checker call changes:
- It gains `--r2-manifest "$r2_manifest_path"`, passed **only** for `self-qualification`.
- It gains `--deadline "$deadline"` for **every** lane the gate checks: P6 gives both the preflight and the full lane a deadline.

Echoes and artifacts:
- After a verified qualification lane, echo `B105_R2_MANIFEST=$r2_manifest_path`, next to the existing `B105_VERDICT=`/`B105_PROGRESS=` echoes (~245-246).
- `run-gate.toml` `lanes.self-qualification.artifacts` gains `".assay/r2-manifest-self-qualification.txt"`, appended after the existing entries.

### Producer wiring for the `campaign` block (`src/assay/cli.py`, `src/assay/runner.py`) — C9

1. In `_run_reserved`, at the point where P6 has loaded and validated the deadline file, build **once**:

   ```python
   campaign_binding = CampaignBinding(
       name=doc["campaign"],
       deadline_sha256=hashlib.sha256(deadline_file_bytes).hexdigest(),  # the exact bytes P6 read
       created_at_utc=doc["created_at_utc"],
       expires_at_utc=doc["expires_at_utc"],
   )
   ```

   Hash the bytes P6 already read, never a re-read of the file. `campaign_binding` is `None` without `--campaign-deadline`.
2. Pass `campaign=campaign_binding` beside `judge_provenance=judge_provenance` at **every** verdict-producing call in `_run_reserved`: the `runner.refuse_lane(...)` / `runner.run_lane(...)` calls at 1315, 1404, 1450, 1488, 1541, and any P6 added. In `runner.py`, thread it through to `Verdict(campaign=...)` along the same parameter path `judge_provenance` takes.
3. Refusals before the deadline is loaded (argument errors, HEAD-read failures before P6's load) carry no `campaign` block. The B105 checker refuses such a report through D1.

### Checker interface (`tools/b105_report_check.py`)

**New CLI arguments:**
- `--r2-manifest PATH`, optional in argparse, **required** when `--expected-rigor` contains `R2`. When missing, refuse with `"--r2-manifest is required for an R2 report"`. When R2 is not in the rigor, refuse with `"--r2-manifest given for a report without R2"`.
- `--deadline PATH`, **required** (argparse `required=True`). The gate always has a deadline after P6.
- Argument refusals happen before the report is read.

**`verify_report_document(..., r2_manifest: Path | None = None, deadline: Path)`** gains two keyword-only parameters. `deadline` is required.

**Exit mapping (P3D-6).** `main` catches `(OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError, IndexError)` → `B105_REPORT_REJECTED=… / exit 2`. A malformed document (a missing `judgment`, a list where a dict belongs) is then an explicit refusal, never exit 1.

**Refusal order.**
1. The existing checks: exit, commit, lane, rigor, tree, PASS, claims, version and provenance.
2. The deadline checks D1–D4.
3. P0's `check_campaign_scope`, when R2 is expected.
4. Then, when R2 is expected, `_check_v14_r2`: C1–C13 in table order.

**When both `--plan-json` and `--r2-manifest` are wrong,** P0's scope refusal is reported first.

**Deadline checks.** Each has its own refusal message, which tests assert as a substring. All D-checks are **source-bound**: they compare against the deadline file and the commit.

| # | Check | Refusal substring |
|---|---|---|
| D1 | `document["campaign"]` is present and is a dict with exactly the four `CampaignBinding` keys | `B105 report carries no campaign binding` |
| D2 | `sha256(Path(deadline).read_bytes()) == campaign["deadline_sha256"]` | `campaign deadline_sha256 does not match the deadline file` |
| D3 | parse the deadline file with a duplicate-key-refusing `object_pairs_hook`. Then `schema == "assay-campaign-deadline/1"`, `campaign == campaign["name"]`, `created_at_utc`/`expires_at_utc` equal the block's values, `commit == expected_commit`, `git_tree == expected_tree`, `expected_lane in lanes`, and `assay_version == expected_version` | `deadline file does not bind this commit/tree/lane/version` |
| D4 | `created_at_utc <= document["started"]` and `document["ended"] <= expires_at_utc`. Both are compared as timezone-aware datetimes; the verdict uses `+00:00`, the deadline uses `Z` | `report was not produced inside its campaign window` |

**R2 checks (`_check_v14_r2`).** Each has its own refusal message, which tests assert as a substring. **Kind** marks whether a check is source proof:
- **SB** = source-bound. It compares against `git show <commit>:…`, a checker constant, or the retained sidecar bytes.
- **CS** = a consistency check between producer-written fields.

| # | Kind | Check | Refusal substring |
|---|---|---|---|
| C1 | SB (policy constant) | `document["judgment"]["r2"]["cold_witness_kills"] is True` | `B105 R2 requires cold_witness_kills true` |
| C2 | CS | `rc = judgment.r2.r2_command` is a dict | `B105 R2 report has no r2_command` |
| C3 | **SB** | the **source-bound lane argv**. Run `git -C <repo_root> show <commit>:assay/assay.toml` (**never** the worktree file), parse it with `tomllib`, and take `lanes[<lane>].argv` as a list. It must equal `document["argv_declared"]` **and** `rc["argv_declared"]` | `declared argv differs from assay.toml at <commit>` |
| C4 | SB (checker constant) | `rc["transform"] == "assay-r2-pytest-nocov/1"` | `unexpected R2 transform` |
| C5 | **SB** | **independent transform**, the checker's own code with **no** `assay.*` import (see "independence"). Drop every `--cov-branch`, every `--cov=<non-empty>` and every `--cov-report=<non-empty>`; refuse any remaining token starting `--cov` or `--no-cov`. The result must equal `rc["argv_transformed"]` | `argv_transformed is not the transform of the committed argv` |
| C6 | SB (checker constant) | `rc["appended"] == ["-p", "no:pytest_cov"]` | `unexpected R2 appended argv` |
| C7 | SB (checker constant) | `rc["cwd"] == "assay"` | `unexpected R2 cwd` |
| C8 | CS | `rc["coverage_baseline"]["collection_sha256"] == rc["r2_baseline"]["collection_sha256"]`, and the counts are equal | `R2 and coverage baseline collections differ` |
| C9 | **SB** | the committed lane's `env` and `env_passthrough` (from the C3 `tomllib` parse) are `{}` and `["PATH"]`. None of `ASSAY_B105_COVERAGE_SOURCE`, `ASSAY_B105_COVERAGE_ARCHIVE_DIR`, `ASSAY_B105_SOURCE_COMMIT`, `ASSAY_B105_SOURCE_TREE` is a key of `document["env_effective"]` | `archive-hook variables present in the qualification run` |
| C10 | **SB** (sidecar bytes) | **manifest sidecar.** Stat it first and refuse if it exceeds **16 MiB**. Read the bytes; they must be empty or end with `b"\n"`. Split on `b"\n"`, dropping the final empty piece. Every line must be non-empty, valid UTF-8, contain no `\r`, and be ≤ 4096 bytes. Recompute `duplicates = len(lines) - len(set(lines))`; it must be **0** (never trust the report's `duplicates`). Recompute the digest **independently**: `sha256(b"".join(str(len(l)).encode() + b":" + l + b"," for l in lines))`. It must equal `rc["r2_baseline"]["collection_sha256"]`, and `len(lines)` must equal `rc["r2_baseline"]["collection_count"]` | `R2 manifest sidecar does not match r2_baseline` |
| C11 | SB (sidecar) | for **every** outcome in `claims[R2].mutation.killed` whose `execution.mode == "witness-cold"`, not just the first: `ev = outcome["evidence"]`; `ev["failed_call_index"] + 1 == ev["started_count"]`; `0 <= ev["failed_call_index"] < len(lines)`; `lines[ev["failed_call_index"]].decode() == outcome["execution"]["witness"]["node_id"]` | `cold kill <candidate_id> is not the manifest's node at its failed index` |
| C12 | CS | **scope limited to `survived` outcomes and `witness-cold` kills (round-2 P3D2-3)**, the X5/X6 scope. Each such outcome's `evidence` with `command == "r2"` has `collection_sha256 == rc["r2_baseline"]["collection_sha256"]`, and each such outcome with `command == "declared"` matches `rc["coverage_baseline"]["collection_sha256"]`. `full` kills, `witness-prefix` kills, `crashed`, `hung` and `budget_exceeded` are **not** checked: X7 allows non-matching facts there, for example a collection-error kill. This deliberately repeats verify's X5/X6 as a cheap source-side defence | `candidate evidence collection differs from r2_baseline` |
| C13 | **SB** | `rc["config_sha256"] == sha256(git -C <repo_root> show <commit>:assay/pyproject.toml bytes)`. The lane cwd is `assay` (C7), so pytest's inifile is `assay/pyproject.toml`; P1 makes it the only one. A `null` `config_sha256` is refused | `R2 pytest config differs from assay/pyproject.toml at <commit>` |

P3d does not re-implement P0's shard and partial-scope refusals (`check_campaign_scope`).

**Ledger TODO.** If `judgment.r2.equivalence_ledger` is not null, refuse with `ledger binding not implemented (B110-P10b)`. P10b replaces this line with its binding: the ledger file at `<commit>` hashes to `equivalence_ledger.sha256`, the ledger path equals the committed lane key, and the audit receipt is re-validated. This keeps a declared ledger fail-closed until then.

### Serialized examples

**Sidecar, valid** (three IDs):
```
tests/test_x.py::test_a\ntests/test_x.py::test_b\ntests/test_x.py::test_c\n
```
Its digest is `sha256(b"23:tests/test_x.py::test_a,23:tests/test_x.py::test_b,23:tests/test_x.py::test_c,")`, where 23 is the byte length of each ID.

**Sidecar, invalid:**
- missing the final `\n`;
- contains an empty line;
- CRLF line endings;
- the IDs reordered (digest mismatch);
- a duplicated line whose digest was recomputed to match, with the report claiming `duplicates: 0`. C10's own duplicate count refuses it.

**Deadline file, valid:** P6's document verbatim, with `sort_keys=True, indent=2`:
```json
{"assay_version": "<v>", "campaign": "b105-3f391d6e0c1a", "commit": "<40 hex>", "created_at_utc": "2026-10-02T08:00:00Z",
 "expires_at_utc": "2026-10-02T16:00:00Z", "git_tree": "<40 hex>", "lanes": ["self-qualification", "self-qualification-preflight"],
 "plan_sha256": {"self-qualification": "<64 hex>", "self-qualification-preflight": null}, "schema": "assay-campaign-deadline/1",
 "wheel_sha256": "<64 hex>"}
```

**Deadline file, invalid (for the checker):**
- the file edited after the run, e.g. `expires_at_utc` extended by one hour (D2 sha mismatch);
- a file for another commit (D3);
- a verdict whose `started` precedes `created_at_utc`, i.e. a screen verdict for the same commit produced before the deadline was initialized (D4).

### Required flow (the gate, `self-qualification` lane)

1. P0: `"$assay_bin" plan "$lane" --file assay.toml > "$plan_path"`.
2. P6: `timeout … "$assay_bin" run "$lane" … --campaign-deadline "$deadline" "${lane_flags[@]}"`. The `lane_flags` are `--cold-witness --r2-manifest .assay/r2-manifest-self-qualification.txt`. P3b writes the sidecar only after its own digest check. The verdict carries the `campaign` block (C9).
3. `assay verify "$verdict_path"`.
4. On producer exit 0: the checker with `--plan-json`, `--r2-manifest` and `--deadline`. Any refusal → `return 2`, as today.

### Topology

- The checker runs in the gate container with the run-venv `python`.
- `--repo-root "$scratch/source"` is the exact-OID private clone, so `git show <commit>:assay/assay.toml` and `git show <commit>:assay/pyproject.toml` read the **committed** files, never the mounted worktree.
- The sidecar, deadline and plan paths are relative to the project dir (`assay/`), the same base as `.assay/verdict-*.json`.

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| valid v14 report accepted | checker | the v14 valid-report builder (below), with a 3-line sidecar, a real plan dict and a deadline file → checker exit 0; `assay verify` also `[]` | `tests/test_b105_report_check.py` | — |
| C1–C13 and D1–D4 each refuse | checker | one test per row: mutate exactly the named field of the valid report, sidecar or deadline file → exit 2 and the named substring | same | remove C3 → the "argv edited after commit" test passes → red |
| C11 checks every cold kill | checker | a report with **two** cold kills, where only the **second** has a wrong `failed_call_index` → C11 refuses naming the second candidate | same | check only the first cold kill → red |
| source-bound via `git show`, not the worktree (P3D-5) | checker | in a temporary repo, commit `assay.toml` with argv A, then overwrite the **working copy** with argv B, uncommitted. Build a report whose argv is B → C3 refuses. The same for `pyproject.toml` and C13 | same | read the worktree file → the B report is accepted → red |
| C12 scope (round-2 P3D2-3) | checker | (a) a `full` kill whose `evidence` has `command:"r2"` and a `collection_sha256` ≠ the R2 baseline's (a collection-error kill, which X7 allows) → **accepted**. (b) A `survived` outcome with `command:"r2"` and a mismatched `collection_sha256` → refused with the C12 message. (c) A `witness-cold` kill with a mismatched sha → refused | `tests/test_b105_report_check.py` | apply C12 to every r2 evidence → (a) refused → red; drop C12 → (b) accepted → red |
| config source-bound (C13) | checker | argv matching, but `config_sha256` ≠ the committed `pyproject.toml` sha → refused | same | drop C13 → accepted → red |
| independence from the producer | checker | a parametrized table of the **15 argv cases in P3a's transform table** compares the checker's transform with `assay.r2_command.transform_argv`. They must agree on each case, and **both** refuse `--cov src` (two tokens). An AST test asserts the checker source imports nothing from `assay` and does not use `importlib`/`__import__` | same | `from assay import r2_command` → the AST test goes red |
| source-bound, not self-reported | checker | build the report with `argv_declared` = the lane argv **plus** `--deselect=extra`; C3 refuses even though the report is internally consistent (`assay verify` returns `[]` for it) | same | — |
| manifest position | checker | swap two sidecar lines → C10 refuses. Rewrite the witness `node_id` to line 0 while `failed_call_index` stays 1 → C11 refuses | same | — |
| rigor gating | checker | the preflight lane (R0,R1) with `--r2-manifest` → refused; without it (but with `--deadline`) → unchanged acceptance | same | — |
| malformed report | checker | a report with no `judgment` key at all, and one where `claims` is a dict → exit **2** with `B105_REPORT_REJECTED=`, never 1 | same | narrow the exception tuple → exit 1 → red |
| campaign producer wiring | cli / runner | `main(["run", lane, "--campaign-deadline", f, "--verdict-json", v])` on a toy R0 lane → the verdict's top-level `campaign` equals the block built from `f`'s bytes. Also an expired `f` → the LANE_TIMEOUT verdict **also** carries `campaign`. Without the flag → no `campaign` key | `tests/test_campaign_binding_wiring.py` (new) | thread it only into `run_lane` → the expired case has no block → red |
| gate wiring, text | script | `tests/test_self_lane.py` pins, all scoped to the **body of `run_and_verify_lane`** (the text between `run_and_verify_lane() {` and its closing `}` at column 0):<br>• `--cold-witness` appears only inside that body's `self-qualification)` case arm. P7b's `b110-pilot`/`b110-screen` arms outside the function may also pass `--cold-witness`;<br>• `'--r2-manifest "$r2_manifest_path"'` occurs twice (run and checker);<br>• `"${lane_flags[@]}"` (or the bash < 4.4 spelling) occurs **on the `assay run` command** (the text between `"$assay_bin" run "$lane"` and the next line not ending in `\`);<br>• `--deadline "$deadline"` is on the checker call;<br>• `rm -f -- "$r2_manifest_path"` is present;<br>• the `B110-P10c:` marker is present;<br>• `B105_R2_MANIFEST=` is echoed.<br>The `run-gate.toml` artifacts pin is updated | `tests/test_self_lane.py` | pass `--cold-witness` for the preflight → the arm pin goes red; define `lane_flags` but never expand it → the expansion pin goes red |
| gate wiring, executed (P3D-4, round-2 P3D2-1) | script | a **stub-binary test**, no docker. **Mandated: the `sed` extraction.** The test runs `sed -n '/^run_and_verify_lane() {/,/^}/p' tools/self-qualification-gate.sh` and evaluates only that function body in `bash`.<br>**Sourcing the script is forbidden.** `run_and_verify_lane` is defined only after the script's clone (~:53), build-venv and pip wheel/install steps (~:68-126), so sourcing would run that heavy setup in a unit test on the shared host, and no guard placed "before the lane sequencing" can skip it.<br>Run the extracted function with:<br>• `assay_bin` set to a stub script that appends its argv (NUL-separated) to a log and exits 0;<br>• `$scratch/run-venv/bin/python` set to a stub that **prints an integer** (for example `3600`) for P6's `remaining_s` computation (P6 step 12) and otherwise exits 0;<br>• a stub checker path;<br>• `timeout` resolved on PATH;<br>• every other variable the function reads set to temp paths.<br>Call it with `self-qualification`, then `self-qualification-preflight`. Assert:<br>• the recorded `run` argv for the full lane **contains** `--cold-witness`, `--r2-manifest`, `.assay/r2-manifest-self-qualification.txt` and `--campaign-deadline`;<br>• the preflight's does **not** contain `--cold-witness`;<br>• the checker argv carries `--deadline` for both | `tests/test_self_lane.py` (or `tests/test_gate_script_wiring.py`, new) | `lane_flags` never expanded → the recorded argv lacks `--cold-witness` → red |

**Building the valid v14 report (normative, `tests/test_b105_report_check.py`):**
1. Start from P3a's `tests/fixtures/verdicts/r2_pass_cold_witness.json` as the R2 claim/judgment source, spliced into `pass.json` as `_verifier_valid_report` does today.
2. Set the top-level `argv_declared` = `argv_effective` = the **actual** `lanes.self-qualification.argv` read from `git show HEAD:assay/assay.toml` (the test's `_git_value` already resolves HEAD), with `argv_appended = []`.
3. Set `r2_command.argv_declared` to the same list, and `argv_transformed` to the transform of it. Set `r2_command.config_sha256` = sha256 of `git show HEAD:assay/pyproject.toml`.
4. Set both baselines' `collection_count = 3` and `collection_sha256` = the digest of the 3-line sidecar written to `tmp_path`.
5. Set the witness-cold kill's `evidence` to the same digest, `started_count: 2`, `failed_call_index: 1`, and `witness.node_id` = line 1.
6. **Plan (P3D-3):** write `plan.json` = `{"status": "ok", "shard": null, "candidate_count": n, "candidates": [{"id": i} for i in candidate_ids]}`, taking the IDs from the report's R2 `mutation.candidate_ids`, and pass `--plan-json`. This is P0 W5's shape.
7. **Deadline:** write a deadline file for `HEAD`/`HEAD^{tree}`/the report's `assay_version`, with `created_at_utc` ≤ the report's `started` and `expires_at_utc` ≥ its `ended`. Set the report's top-level `campaign` block from that file's bytes and pass `--deadline`.
8. Assert `_verify_with_assay_cli` gives `[]` **before** asserting the checker's result. If `assay verify` rejects it, fix the builder, never the checker.

### Degrees of freedom

Private helper names inside the checker, and the bash variable names **other than** the pinned substrings. Everything else is fixed: the refusal substrings, the check order, the argument names, the sidecar format and bounds (16 MiB, 4096 bytes per line), the exception tuple, and the P6 variable names already in the base.

---

## Work

1. Create a worktree under `/workspaces/vbpub/.worktrees/`, on `assay-b110-p3d-gate` from `assay-b110-v14`, and confirm the base. If any check fails → BLOCKED.
   - `assay run --help` lists `--cold-witness`, `--r2-manifest` and `--campaign-deadline`;
   - `assay campaign init --help` works;
   - `tools/b105_report_check.py` carries P0's `--plan-json`;
   - `tools/self-qualification-gate.sh` carries P6's `--campaign-deadline "$deadline"` wrap;
   - `grep -n "class CampaignBinding" src/assay/verdict.py` finds P3a's model.

   Record `bash --version` from the tester-unified image, read from a prior gate log or the image's Dockerfile; do not start a container just for this.
2. **Red first.** Write the tests, run them, and record the failure counts:
   - extend `tests/test_b105_report_check.py`: the v14 valid-report builder, C1–C13, D1–D4, independence, rigor gating, the two-cold-kill case, the committed-vs-worktree case, and malformed reports;
   - add the campaign producer-wiring test;
   - add the `tests/test_self_lane.py` pins and the stub-binary wiring test.
3. Implement `_check_v14_r2` (C1–C13), `_check_deadline` (D1–D4), the `--r2-manifest`/`--deadline` arguments and the widened exception tuple in `tools/b105_report_check.py`.
4. Implement the C9 producer wiring in `src/assay/cli.py` and `src/assay/runner.py`.
5. Edit `tools/self-qualification-gate.sh` per the packet:
   - the `lane_flags` array and `rm -f` only in the qualification arm;
   - the expansion on the `run` command;
   - `--r2-manifest` and `--deadline` on the checker;
   - the echo, and the `B110-P10c:` marker.
   - Add **no** `B105_GATE_SOURCE_ONLY` guard: the stub test uses the mandated `sed` extraction (round-2 P3D2-1).

   Keep P6's and P0's lines unchanged.
6. Add the artifact entry to `run-gate.toml`.
7. Docs:
   - `docs/CONSUMERS.md` B105 note (:70-92): the qualification gate now runs R2 with `--cold-witness`, retains the manifest sidecar, and the report binds its campaign deadline;
   - `docs/DESIGN-GUIDE.md` B105 section (~:1959-1981): the source-bound checker re-derives the transform, the pytest config and the manifest position, and matches the deadline file. Explain why: a self-reported argv is not source proof, and a clean screen verdict must not pass as a qualifying one. Distinguish the source-bound checks from the consistency checks;
   - README: one sentence that a verdict produced under `--campaign-deadline` carries a `campaign` block;
   - CHANGES `### Changed`.
8. Run the focused tests, the gate and the preflight, then write the report.

## Oracles

The traceability rows are the oracles. Each has its observable, its controlled break or named mutation as the negative, and the gate: tester-unified, plus the preflight, because this package changes `src/assay`, the gate script and `run-gate.toml`.

The oracles that must be demonstrated **red first** are:
- C3 and C13 (source-bound, including the committed-vs-worktree fixture);
- C10 and C11 (the manifest, including the two-cold-kill case);
- D2 and D4 (the deadline);
- the campaign producer-wiring test;
- the stub-binary expansion test;
- the preflight-refuses-manifest rigor gate.

**Combined-axis fixtures** (each is a test):
1. A duplicated sidecar line, with a matching recomputed digest and `duplicates: 0` in the report → C10.
2. Matching argv but a mismatched `config_sha256` → C13.
3. A dirty-worktree `assay.toml` whose report matches the working copy → C3.
4. Two cold kills where only the second has a wrong `failed_call_index` → C11.
5. A report with no `judgment` key at all → exit 2.
6. A clean screen-mode verdict (no `campaign` block) for the same commit → D1.

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

## Scope / forbid

- **Touch:**
  - `tools/b105_report_check.py`;
  - `tools/self-qualification-gate.sh`;
  - `run-gate.toml` (the one artifact line);
  - `src/assay/cli.py` and `src/assay/runner.py` (**only** the C9 `campaign` threading beside `judge_provenance`);
  - `tests/test_b105_report_check.py`, `tests/test_self_lane.py`, `tests/test_campaign_binding_wiring.py` (new), and optionally `tests/test_gate_script_wiring.py` (new);
  - `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, README, `CHANGES.md`.
- **Forbid:**
  - every other part of `src/assay/**`. If the checker needs any other producer change → BLOCKED;
  - `verdict.py` / `verify.py` / the schema (P3a owns `CampaignBinding`);
  - `assay.toml`;
  - `tools/tester-unified-gate.sh` (the release gate);
  - the preflight lane's flags, other than the `--deadline` checker argument;
  - P6's and P0's gate-script lines;
  - any change to how the gate computes `source_commit`/`source_tree`;
  - running the full `self-qualification` lane.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_b105_report_check.py tests/zz_slow/test_b105_report_check_real_plan.py tests/test_self_lane.py tests/test_campaign_binding_wiring.py tests/test_campaign_deadline.py -q -p no:cacheprovider`
   P1 moved P0's real plan+run test into `tests/zz_slow/` (round-2 P3D2-2); find it by name if the path differs.
   Add `tests/test_gate_script_wiring.py` if you created it.
2. Run `bash -n tools/self-qualification-gate.sh` and `shellcheck tools/self-qualification-gate.sh`, if shellcheck is installed; do not install it.
3. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3d-gate.log 2>&1; echo EXIT=$?`
4. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
5. Run `python ./run-gate.py self-qualification-preflight`.
   - It must pass with its report accepted by the checker under `--deadline`. Its `b105-pre-*` deadline comes from P6.
   - In a separate step, read `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` and `B105_CAMPAIGN_DEADLINE=`.
   - This exercises the checker's D-checks end to end on a real preflight verdict.
6. **Do not** run the `self-qualification` lane. The `--cold-witness`/`--r2-manifest` path runs end to end first in the qualifying runbook (plan §9.3). The pilot's `b110-pilot` arm does **not** call `run_and_verify_lane` or the checker. This is why the stub-binary test above is mandatory: it is the only pre-qualifying execution of this wiring.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- P3b's CLI flags or sidecar are absent or differ from the specified format;
- P0's refusals are absent;
- P6's gate wiring or `--campaign-deadline` is absent from the base;
- P3a's `CampaignBinding` is absent;
- the valid report cannot be made `assay verify`-clean without changing `src/` beyond the C9 threading;
- the tester-unified bash is < 4.4 and the fallback expansion spelling is rejected by `bash -n`.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3d-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

## Report

Write `nyxloom-trove/reports/assay-B110-P3d-REPORT.md` with:
- the commits;
- the traceability table with actual tests and red-first counts;
- the gate and preflight log paths and markers;
- the recorded tester-unified `bash --version`;
- residuals, including the `B110-P10c:` marker left for that package.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; a fresh-session review (never a fork) precedes the controller's `--no-ff` merge into `assay-b110-v14`.

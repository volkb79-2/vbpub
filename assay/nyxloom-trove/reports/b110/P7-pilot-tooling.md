# B110-P7 — Pilot tooling: non-qualifying candidate selection runs, a deterministic selector, and the pilot runbook

| Field | Value |
|---|---|
| Backlog | **B118** (split from B110) |
| Branch | `assay-b110-p7-pilot` off the integration line `assay-b105-evidence-integrity` (after the plan §11.1 reconciliation) |
| Depends on | **P0** (B111) for the per-candidate resource and phase fields. **P7b** (Work step 9, the gate-script pilot mode) also needs **P3b** (`--cold-witness`) and **P6** (`campaign init`, `--campaign-deadline`) on its base. The controller dispatches it as a second commit after those merge. |
| Contract class | **2c**: bounded integration against fixed contracts |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-474 (plan D10)**. Relevant: A-461 (the `candidate_ids` inventory is equal to the bucket IDs), A-464 (the pilot is measurement, never qualification) |
| Size | M: `cli.py` (two flags, pilot summary), the `runner.py`/`mutation.py` selection threading, one new tool, two new test files, the gate script (P7b), `run-gate.toml` (P7b), docs |

---

## Context to read first

Paths are relative to `assay/`. Line numbers were verified at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0, §3 D10, §7 (pilot), §8 (go/no-go), §10.
2. `src/assay/cli.py`:
   - `:255-328`: the `run` parser, including `--shard` at `:289`, `--reuse-from` at `:290-300` and `--rejudge*` at `:301-327`.
   - `:725-812`: `_cmd_run`. The `--operators` lane override via `replace(...)` at `:731-760` is the **precedent** for the pilot lane override. `_resolve_state_dir` is called at `:804`.
   - `:1490-1540`: the `run_lane` call.
   - `:1541-1553`: the verdict write, `_emit_verdict_written` and `_print_run_summary`.
   - `:1820-1853`: the `assay plan` row and payload shape (`id`, `path`, `operator`, `start_byte`, `end_byte`, `lineno`, `description`).
3. `src/assay/mutation.py`:
   - `:292-315`: `InvalidRejudgeIdError` and `_reject_unknown_rejudge_ids`, the pattern for unknown IDs.
   - `:1023`: `candidate_id`.
   - `:2318-2343`: the selection block, where the selection is applied and which is mutually exclusive with sharding.
   - `:2436-2455`: the `candidates` progress event, which gains `selection_sha256`.
   - `:2503-2523`: the `candidate_ids` stamping (a shard records its slice).
4. `src/assay/runner.py`:
   - `:4526-4541`: the `except mutation.InvalidRejudgeIdError: raise`, which leads to the outer whole-lane refusal at `:5385`.
   - The `reuse_from` threading, which you mirror for `candidate_selection`: `run_lane` at `:5565`, `_run_higher_rigor_lane` at `:5164`, the `_run_prepared_lane` call at `:5351-5378`, the `_run_prepared_lane` parameter at `:3585`, and the `run_mutation` call at `:4449-4531`.
5. `src/assay/errors.py:57-66`: `EXIT_CODES` 0–5. **6 is unused** and belongs to the pilot.
6. `tools/b105_report_check.py`, the shape of a standalone tool, and `tests/test_b105_report_check.py:1-33`, which loads a tool by path through `PROJECT_ROOT / "tools" / …`.
7. `tests/test_cli_run.py:586-640`: how a toy R0+R2 lane repo is built (`git_repo`, lane TOML, compare-swap site), and `run()` at `:127-130`.
8. For P7b only:
   - `tools/self-qualification-gate.sh`: arguments and lane `case` at `:11-19`, wheel and venv at `:92-154`, `run_and_verify_lane` at `:156-214`.
   - `run-gate.toml:33-62`.

## Implementation packet (normative)

### Interfaces and grammar

**A. `assay run LANE --candidates-file PATH`**

The file is UTF-8 text. Each line is one of:
- empty;
- a comment starting with `#`;
- exactly 64 lowercase hex characters, matching `^[0-9a-f]{64}$`, with no surrounding whitespace.

Limits: at most `judge.mutation.max_mutants` IDs and at least 1.

Valid:
```
# b110 pilot selection seed=b110-pilot-2026 size=64
914787a26941a1f0c9e1d0b6e3c5d0a1f2e3d4c5b6a79881726354a5b6c7d8e9
061eddf74a18c3d2b1a0f9e8d7c6b5a4938271605f4e3d2c1b0a9f8e7d6c5b4a
```
(The IDs are illustrative in shape only.)

Invalid (each refused before any work with `LaneConfigError`, which becomes `ERROR`/`BAD_LANE_CONFIG`, exit 2, and no execution):
- `914787A2…` (uppercase) or `914787a2 ` (trailing space): *"line N is not a 64-hex candidate id"*.
- The same ID twice: *"candidate id … appears more than once"*.
- Only comments or empty lines: *"selects no candidates"*.

**B. `--pilot-jobs N`**
- An integer with `1 ≤ N ≤ 8`. It is refused without `--candidates-file`.
- It overrides `judge.mutation.jobs` for this invocation only.
- The default when omitted is the lane's declared `jobs`.

**C. Flag conflicts.** With `--candidates-file`, each of the following gives `LaneConfigError` → exit 2, and nothing runs:
- `--verdict-json`, `--shard`, `--reuse-from`, `--rejudge` or `--rejudge-outcome` is present;
- `--state-dir` is absent;
- the lane's rigor lacks `R2`.

**D. The pilot lane.** `_cmd_run` builds it once, like the `--operators` precedent:
`replace(lane, rigor=tuple(r for r in lane.rigor if r != "R3"), judge=replace(lane.judge, mutation=replace(lane.judge.mutation, jobs=N)))`.
R3 is **not executed**.

**E. Selection threading.** `candidate_selection: frozenset[str] | None = None` goes from `run_lane` through `_run_higher_rigor_lane` and `_run_prepared_lane` to `run_mutation`, exactly like `reuse_from`. In `run_mutation`, at the selection block (`:2323`):

```python
current_ids = [candidate_id(job) for job in job_list]
if candidate_selection is not None:
    unknown = candidate_selection - set(current_ids)
    if unknown:
        raise InvalidCandidateSelectionError(sorted(unknown))    # ERROR/BAD_LANE_CONFIG
    selected_indices = [i for i, cid in enumerate(current_ids) if cid in candidate_selection]  # PLAN order
```

- `InvalidCandidateSelectionError(AssayError)` is new, sits beside `InvalidRejudgeIdError`, and is exported.
- `runner.py:4532` becomes `except (mutation.InvalidRejudgeIdError, mutation.InvalidCandidateSelectionError): raise`. If P6 has already added `CampaignPlanMismatchError` to that tuple, keep all three.
- The `candidates` progress event gains `"selection_sha256": <hex>`, **only when a selection is active**, so other streams stay byte-identical. The value is `mutation.plan_sha256(selected ids in plan order)`. If P6 has not merged yet, add `plan_sha256` here with P6's exact definition, `sha256(b"".join(f"{len(i)}:{i},".encode("ascii") for i in ids))`, and note it for P6.

**F. The pilot summary.** A pilot never writes a verdict.
- `_cmd_run` receives the in-memory `Verdict` from `run_lane`. It prints **one** JSON document to stdout (`sort_keys=True, indent=2`). It does **not** call `write_verdict`, and it does **not** print `_print_run_summary`.
- It still emits the `verdict_written` progress record, with `destination: null`.

```json
{
  "schema": "assay-pilot-summary/1",
  "qualifying": false,
  "completed": true,
  "lane": "self-qualification",
  "commit": "<40-hex>",
  "jobs": 3,
  "requested": 70,
  "selection_sha256": "<64-hex>",
  "candidates_file_sha256": "<sha256 of the raw file bytes>",
  "state_dir": "/abs/path/.assay/b110-pilot-state",
  "r0": "PASS",
  "r1": "PASS",
  "r2": {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"},
  "r3": "not-run: pilot",
  "buckets": {"killed": 61, "survived": 6, "equivalent": 0, "crashed": 0, "hung": 0, "budget_exceeded": 3},
  "candidates": [{"id": "<64-hex>", "path": "assay/src/assay/adapters/go.py", "operator": "python:compare-swap",
                  "bucket": "killed", "execution_mode": "full"}],
  "unresolved": []
}
```

Field rules:
- `r1` is `null` when the lane has no R1.
- `buckets` always lists all six `MUTATION_BUCKETS`.
- `candidates` is in plan order.
- `execution_mode` is `outcome.execution.mode`.

`completed` is true **iff** all three hold:
1. an R2 claim with a mutation payload exists;
2. its `reason_code` is not `LANE_TIMEOUT`;
3. the union of the bucket IDs equals the selection.

When `completed` is false, `unresolved` lists the selected IDs that are absent from the payload or sit in `budget_exceeded` under an R2 `LANE_TIMEOUT`.

If the run is refused before R2 (R0 fails, a config refusal, a timeout before R2), the summary still prints: `completed: false`, `r2: null`, `buckets: null`, and the refusal's `status`/`reason_code` go under `"refusal"`.

**G. Exit codes.**
- **6** iff `completed`.
- Otherwise the in-memory verdict's own `exit_code`, which is in 1–5.
- A pilot can never exit 0. Enforce this with `assert code != 0` before return, and cover it with a test.

**H. Selector: `tools/b110_pilot_select.py`** (a standalone script; stdlib only)

```
python tools/b110_pilot_select.py --plan PLAN.json --repo-root REPO --out CANDIDATES.txt --report SELECTION.json
                                  [--seed b110-pilot-2026] [--size 64]
```

- `PLAN.json` is exactly the stdout of `assay plan <lane> --file assay.toml`. After stripping whitespace it must be one JSON object with `status == "ok"` and a `candidates` list whose rows carry the §2 keys. Trailing non-whitespace is refused (exit 2).
- `REPO` is the repository top. Row paths are repo-relative, for example `assay/src/assay/adapters/go.py`.
- `rank(id) = hashlib.blake2b((seed + id).encode("ascii"), digest_size=16).hexdigest()`; a lower rank is picked earlier.

The stratified set **S** is built in this order:
1. Per file: for every distinct `path`, taken in sorted order, add the row with the lowest rank. If the file count exceeds `size`, exit 2.
2. Operators: for each operator in sorted order of the plan's operators, if no row in S has that operator, add that operator's lowest-rank row not already in S.
3. Fill: go through all rows by ascending rank and add rows not in S until `|S| == size`, or the plan is exhausted.

The known-hard set **H** holds 6 entries, reported separately. It is also included in the candidates file.

- **Four scanner sites.** Each is a row with `path == "assay/src/assay/adapters/go.py"`, `operator == "python:compare-swap"` and `description == "Eq->NotEq"`. Its innermost enclosing `FunctionDef` (by line span) and its `lineno`'s stripped source line must equal one of:
  - `_scan_raw_string` / `return None if end == -1 else end + 1`
  - `_strip_comments_and_literals` / `if two == "//":`
  - `_strip_comments_and_literals` / `if end == -1:`
  - `_strip_comments_and_literals` / `if close == -1:`

  Each spec must match **exactly one** row; otherwise exit 2 with the spec named.
  Consistency check: the file bytes `[start_byte:end_byte]` of each matched row must equal `b"=="`; otherwise exit 2 with *"source does not match plan"*.
- **Two import-time `bool-const-flip` sites.** These are the two lowest-rank rows with `operator == "python:bool-const-flip"` that are **import-time**.

  A span `[start_byte, end_byte)` is **import-time** iff it lies inside no `FunctionDef`/`AsyncFunctionDef` **body** (the byte range from `body[0]` start to `body[-1]` end) and inside no `Lambda.body` span. Byte offsets come from the AST's UTF-8 `col_offset`/`end_col_offset` plus the line-start byte offsets of the file.

  So module-level code, class bodies, decorators, default values and annotations of `def`s all count as import-time. They are evaluated at definition time.

Outputs:
- **`CANDIDATES.txt`:**
  - header `# b110 pilot selection seed=<seed> size=<size> plan_candidates=<n>`;
  - then the IDs of `S ∪ H` in plan order, one per line, with a trailing newline.
- **`SELECTION.json`** (`schema: "b110-pilot-selection/1"`), containing:
  - `seed`, `size`, `plan_candidate_count`, `files`, `operators`;
  - `stratified`: rows with `reason ∈ {"per-file","operator","fill"}`, plus `rank` and `lineno`;
  - `known_hard`: rows with a `label`;
  - `overlap`: the IDs in both S and H;
  - `import_time_total`: the count over the whole plan, **reported, not asserted**;
  - `selection_sha256`: the same encoding as §E, over `S ∪ H` in plan order.

Exit codes: 0 when written, 2 on any refusal. Output bytes are deterministic for identical inputs.

### Required flow (`assay run … --candidates-file`)

1. In `_cmd_run`, **before** `_resolve_state_dir` and any git call:
   - parse the file (§A), enforce the conflicts (§C), and parse `--pilot-jobs` (§B);
   - build the pilot lane (§D);
   - keep `selection` as a `frozenset` and keep `candidates_file_sha256`.
2. Run the ordinary reserved path with `candidate_selection=selection`. R0 and R1 run as declared. R2 runs only the selection, in plan order, on a queue of `jobs` workers. R3 does not run.
3. After `run_lane` returns, or after the existing LANE_TIMEOUT handler builds a refusal verdict, build the §F summary from the in-memory verdict, print it, emit `verdict_written` with `destination=None`, and return per §G.

### Topology and bounds

- Pilot state lives only in the `--state-dir` passed to the pilot run. The runbook uses `.assay/b110-pilot-state`.
- **Pilot state is never imported into a qualifying campaign** (D10). Its judge identity is shared with the full lane, because the judge identity omits the lane name and selection. That is exactly why the runbook uses a separate state dir and P9's `state import` must not be pointed at it. The DESIGN-GUIDE says so.
- Bounds:
  - selection size ≤ `max_mutants`;
  - `--pilot-jobs` ≤ 8;
  - the summary lists at most the selection (≤ 10,000 rows; the pilot uses about 70).

### Decision table

| Input | Work done | Output | Exit |
|---|---|---|---|
| valid file, R2 completes, any bucket mix | R0 (+R1), R2 on the selection | summary with `completed: true` | 6 |
| valid file, every candidate killed | same | summary with `completed: true` | **6, never 0** |
| valid file, pilot deadline expires during R2 | partial | `completed: false`, `unresolved` non-empty | 4 |
| valid file, R0 fails | R0 only | `completed: false`, `refusal` present | 1 |
| an ID not in the current plan | baseline, then refusal before any candidate | `completed: false`, refusal `ERROR`/`BAD_LANE_CONFIG` | 2 |
| malformed, duplicate or empty file; a conflicting flag; no `--state-dir`; `--pilot-jobs` out of range; lane without R2 | none | stderr refusal only | 2 |
| `--pilot-jobs` without `--candidates-file` | none | stderr refusal | 2 |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| file grammar | `cli._parse_candidates_file` | T1 | tmp files | accept uppercase → T1 red |
| flag conflicts | `cli._cmd_run` | T2 | toy lane | allow `--verdict-json` → a verdict file exists → T2 red |
| selection | `mutation.run_mutation` | T3 | toy lane with 3 sites | ignore the selection → 3 `candidate` events |
| unknown ID | same + `runner.py:4532` | T4 | toy lane | silently drop unknowns → exit 6 |
| never PASS | `cli` | T5 | an all-killed toy lane | return the verdict code → exit 0 |
| R3 skipped | pilot lane | T6 | toy lane declaring R3 | keep R3 → canary progress events |
| selection digest | `mutation`/`cli` | T7 | T3 fixture | hash in file order → mismatch |
| selector | `tools/b110_pilot_select.py` | S1–S7 | synthetic plan + source | see the Oracles section |

### Degrees of freedom

- Private helper names.
- Where in `_cmd_run` the summary is built.
- The selector's internal structure.

**Fixed:** every spelling, exit code, JSON key, algorithm step and definition above.

## Work

1. **Red first.**
   - Add `tests/test_pilot_candidates_file.py` (T1–T7). Build a toy R0+R2 lane repo, following the `tests/test_cli_run.py:586-640` shape, with at least 3 fast-killed compare-swap sites and one surviving site. Call `run([...])` in-process.
   - Add `tests/test_b110_pilot_select.py` (S1–S7). Load the tool with `importlib.util.spec_from_file_location("b110_pilot_select", PROJECT_ROOT / "tools" / "b110_pilot_select.py")` and call `main([...])`.
   - Record the red state in `assay-B118-REPORT.md`.
2. Add `InvalidCandidateSelectionError`, the selection branch, and `selection_sha256` to `mutation.py`. Add `plan_sha256` here if P6 has not merged it yet.
3. Thread `candidate_selection` through `runner.py` (Context 4) and extend the except tuple at `:4532`.
4. In `cli.py`: add the parser flags after `--reuse-from` (`:300`), `_parse_candidates_file`, the conflicts, the pilot lane, the summary and the exit code.
5. Add `tools/b110_pilot_select.py`.
6. Run the focused tests serially: the two new files, `tests/test_cli_run.py`, `tests/test_b105_cli_boundaries.py`, `tests/test_mutation_resume_sharding.py`, `tests/test_mutation_progress_budget_plan.py` and `tests/test_b106_reuse_and_witness.py`.
7. Update the docs (see Docs sync) and CHANGES.
8. Write the REPORT, then commit (**P7a**).
9. **P7b: second commit. It requires P3b and P6 on the base; the controller dispatches it after both merge.**
   - Add a third requested lane, `b110-pilot`, to `tools/self-qualification-gate.sh` (the `case` at `:16-19`). After the existing clone, build and venv steps (`:49-154`), it runs:
     ```bash
     pilot_campaign="b110-pilot-${source_commit:0:12}"
     pilot_deadline=".assay/campaign-deadline-$pilot_campaign.json"
     "$assay_bin" plan self-qualification --file assay.toml > .assay/b110-pilot-plan.json
     "$scratch/run-venv/bin/python" "$scratch/source/assay/tools/b110_pilot_select.py" \
       --plan .assay/b110-pilot-plan.json --repo-root "$worktree" \
       --out .assay/b110-pilot-candidates.txt --report .assay/b110-pilot-selection.json
     [[ -f "$pilot_deadline" ]] || "$assay_bin" campaign init --file assay.toml \
       --campaign "$pilot_campaign" --lane self-qualification --hours 2 \
       --state-dir .assay/b110-pilot-state --wheel-sha256 "$wheel_digest"
     remaining_s=…   # from $pilot_deadline, as P6's gate integration computes it
     set +e
     timeout --verbose --signal=TERM --kill-after=30s "$((remaining_s + 120))s" \
       "$assay_bin" run self-qualification --file assay.toml \
         --candidates-file .assay/b110-pilot-candidates.txt --pilot-jobs 3 --cold-witness \
         --state-dir .assay/b110-pilot-state --progress .assay/progress-b110-pilot.jsonl \
         --campaign-deadline "$pilot_deadline" > .assay/b110-pilot-summary.json
     pilot_status=$?
     set -e
     echo "B110_PILOT_EXIT=$pilot_status"
     echo "B110_PILOT_SUMMARY=.assay/b110-pilot-summary.json"
     [[ $pilot_status -eq 6 ]] && echo "B110_PILOT_COMPLETED=1"
     exit 0      # the pilot is measurement; its completeness is read from the markers, never from this exit
     ```
   - Add a `run-gate.toml` lane `[lanes.b110-pilot]`:
     - `kind = "command"`, `environment = "tester-unified"`;
     - `argv = ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "2h10m", "bash", "{worktree}/assay/tools/self-qualification-gate.sh", "{worktree}", "b110-pilot"]`;
     - `clean_tree = true`, `budget = "2h"`;
     - `resources = { cpus = "3", memory = "2g", memory_swap = "8g" }`;
     - `artifacts` = the six `.assay/b110-pilot-*` and `progress-b110-pilot.jsonl` paths;
     - a comment citing A-474.
   - Extend the substring pins in `tests/test_self_lane.py:176-200` with `b110-pilot`, `--candidates-file` and `B110_PILOT_EXIT=`.
   - **Survivor-screen mode** (added by the carver on 2026-09-28; plan §9.1). Add a fourth requested lane, `b110-screen`, to the same `case`. After the shared clone, build and venv steps, it runs:
     ```bash
     set +e
     "$assay_bin" run self-qualification --file assay.toml --cold-witness --resume \
       --state-dir .assay/b110-screen-state --progress .assay/progress-b110-screen.jsonl \
       --verdict-json .assay/verdict-b110-screen.json
     screen_status=$?
     set -e
     echo "B110_SCREEN_EXIT=$screen_status"
     echo "B110_SCREEN_VERDICT=.assay/verdict-b110-screen.json"
     exit 0      # non-qualifying: survivors (exit 1) are the expected result; read the markers
     ```
     - It passes **no** `--campaign-deadline`. The screen is non-qualifying (plan §9.1), so each invocation is bounded by the lane `budget` and the outer `timeout`, and a re-invocation resumes `.assay/b110-screen-state`.
     - It never passes `--candidates-file`, `--shard` or `--reuse-from`. The optional B106 `--reuse-from` re-screen after fixes is a later controller step, documented in the plan, not part of this mode.
     - Add a `run-gate.toml` lane `[lanes.b110-screen]` with the same shape as `b110-pilot`, but:
       - `timeout … 7h30m`;
       - `budget = "8h"`;
       - `artifacts` = `.assay/verdict-b110-screen.json` and `.assay/progress-b110-screen.jsonl`;
       - a comment: "non-qualifying survivor screen (plan §9.1); never evidence for B105 except through the B119 import path".
     - Extend the `tests/test_self_lane.py` pins with `b110-screen` and `B110_SCREEN_EXIT=`. Add an oracle that the screen branch of the gate script contains neither `--campaign-deadline` nor `--candidates-file`: a static substring check of that `case` arm, extracted between its label and the next `;;`.

## Oracles

**CLI (T1–T7).** Each is in-process `main()` on a toy lane, with no timing.

- **T1: file grammar.**
  - *Observable:* uppercase, trailing-space, duplicate and empty files each give exit 2, a stderr message naming the line or ID, and no progress `run` header.
  - *Negative:* a lenient parser executes the lane.
- **T2: conflicts.**
  - *Observable:* each of `--verdict-json v`, `--shard 0/2`, `--reuse-from x`, `--rejudge <id>`, `--rejudge-outcome killed`, a missing `--state-dir`, `--pilot-jobs 0`, `--pilot-jobs 9`, `--pilot-jobs 2` without `--candidates-file`, and an R0/R1-only lane gives exit 2 with nothing executed. For `--verdict-json`, the file `v` does not exist afterwards.
- **T3: selection.** Select 1 of 3 killable sites.
  - *Observable:*
    - exit 6;
    - stdout is a JSON document with `qualifying` false and `completed` true;
    - exactly one `candidate` progress event, whose `candidate_id` is the selected one;
    - the summary's `candidates` holds exactly that ID;
    - no verdict file is written anywhere under the repo;
    - `progress` ends with `verdict_written` with `destination: null`.
  - *Negative:* an implementation that ignores the selection emits 3 events.
- **T4: unknown ID.**
  - *Observable:* a well-formed ID absent from the plan gives exit 2, a summary with `completed: false` and `refusal.reason_code == "BAD_LANE_CONFIG"`, and no `candidate` event.
- **T5: never PASS.**
  - *Observable:* selecting only killed sites gives exit **6**, with `buckets.killed` equal to the selection size.
  - *Negative:* returning the verdict code gives 0.
- **T6: R3 skipped.**
  - The toy lane declares `rigor = ["R0","R2","R3"]` with a valid canary.
  - *Observable:* `summary["r3"] == "not-run: pilot"`; no canary progress event or canary process runs (a counting process runner sees only the baseline plus the selected candidates).
- **T7: digest.**
  - *Observable:* the `candidates` event's `selection_sha256` equals the summary's, and equals the §E encoding over the selected IDs in **plan** order, even when the file lists them in reverse.

**Selector (S1–S7).** These use a synthetic plan JSON and synthetic source files under `tmp_path`, laid out as `assay/src/assay/...`.

- **S1: determinism.** Two runs produce byte-identical `CANDIDATES.txt` and `SELECTION.json`. With a different `--seed`, the stratified set differs (on a fixture large enough that this is certain).
- **S2: one per file and operator coverage.**
  - *Observable:* every path is represented. An operator that appears only in a row that loses every per-file pick is still present, with `reason: "operator"`.
- **S3: fill and bounds.** `|S| == size` when the plan has at least `size` rows. `size` smaller than the file count gives exit 2. A plan smaller than `size` gives all rows.
- **S4: known-hard exact matching.**
  - *Observable:* a synthetic `go.py` containing the four exact lines in the named functions yields 4 labelled rows.
  - A duplicated line in the same function gives exit 2, naming the spec. A span that does not slice to `b"=="` gives exit 2 with *"source does not match plan"*.
- **S5: the import-time rule.** In a synthetic module, the following `True`/`False` literal sites are classified exactly as listed:

  | Site | Import-time? |
  |---|---|
  | module-level constant | yes |
  | class body attribute | yes |
  | `@dataclass(frozen=True)` decorator argument | yes |
  | function default value | yes |
  | function body statement | no |
  | lambda body | no |

- **S6: refusals.** Trailing garbage after the plan JSON, `status != "ok"`, and a missing `candidates` key each give exit 2.
- **S7: selection digest.** `SELECTION.json["selection_sha256"]` equals the §E encoding over `S ∪ H` in plan order.

**P7b (O-gate).** `tests/test_self_lane.py` asserts the P7b substrings. The lane parses under `./run-gate.py --list`, which is a read-only listing.

### Anti-pattern list (verbatim from `nyxloom/reference/AUTHORING.md` §3b)

#### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

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

- **README (WHAT):** a short "Pilot runs (non-qualifying)" paragraph covering `--candidates-file`, `--pilot-jobs`, no verdict, exit 6, and "never qualification evidence".
- **DESIGN-GUIDE (WHY):**
  - why the pilot never writes a verdict: a partial inventory must not be able to look like a pass (A-462/A-474);
  - why exit 6 sits outside `EXIT_CODES`;
  - why R3 is skipped;
  - why pilot state must never be imported: the judge identity omits the lane and the selection.
- **CONSUMERS (HOW):** a pasteable `assay plan` → selector → `assay run --candidates-file` sequence, and how to read the summary.
- **CHANGES:** `### Added` for B118.

## Scope / forbid

- **Touch:**
  - `src/assay/cli.py`, `src/assay/runner.py` (threading plus the except tuple only), `src/assay/mutation.py` (the selection branch, the error class, the progress key, `plan_sha256` if absent);
  - `tools/b110_pilot_select.py` (new);
  - `tests/test_pilot_candidates_file.py`, `tests/test_b110_pilot_select.py`;
  - README, DESIGN-GUIDE, CONSUMERS, CHANGES, `nyxloom-trove/reports/assay-B118-REPORT.md`;
  - P7b only: `tools/self-qualification-gate.sh`, `run-gate.toml`, `tests/test_self_lane.py`.
- **Forbid:** the verdict schema, `verdict.py`, `verify.py`, `ReasonCode`, `EXIT_CODES` (6 is deliberately *outside* the map), `assay.toml`, the executor loop (P4), `decisions.md`.

  Needing any of these is a BLOCKED trigger. **Do not add a module under `src/assay/`**, because B105's target list must equal the discovered files. The tool lives in `tools/`. Keep the 100% line and branch floor for all new `src/assay` code, with no `pragma: no cover`.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused tests serially (Work step 6) with `nice -n 19 ionice -c3 python -m pytest <files> -q -p no:cacheprovider`.
2. Run `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b118-gate.log 2>&1; echo "exit=$?"`. Then, in a **separate** step, run `grep -E "ASSAY_GATE_CONTAINER_EXIT|ASSAY_REGISTERED_GATE_COMPLETE" /tmp/b118-gate.log`.
3. Run `python ./run-gate.py self-qualification-preflight > /tmp/b118-preflight.log 2>&1` (the B105-collected source changed), only when no other gate is running. In a separate step, read the log for `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1`.
4. **Never run the `b110-pilot` lane as part of this package.** The pilot is a controller-approved runbook step (see below).

## BLOCKED rule

If a named contract cannot be met as specified, or scope needs a forbidden file: STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B118-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- the runner or verdict model refuses the §D pilot lane (R3 removed while `judge.canary` is still declared);
- the in-memory verdict cannot be built for a selection subset;
- T4 cannot produce a summary because the refusal happens before a verdict exists.

If you hit the last one, report which layer raised it; do not add a verdict write.

## Report

`assay-B118-REPORT.md` must contain:
- a traceability table with the actual test names and red/green counts;
- the log paths and marker lines for the gate and the preflight;
- the files touched;
- the P7a and P7b commit hashes.

Commit with the trailer:

```
Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
```

Do not merge.

---

## Pilot runbook (operator/controller)

This mirrors plan §7. It is carried out only after P0–P8 are merged, P7b is on the integration line, and the controller has approved it.

**Preconditions:**
- `docker ps` shows no gate container;
- the host-load rule is respected;
- the integration-line tip is clean;
- `python ./run-gate.py tester-unified` has passed on that exact commit.

**Commands** (from `<worktree>/assay` on the host):
1. `python ./run-gate.py b110-pilot > /tmp/b110-pilot.log 2>&1; echo "exit=$?"`. This is the only gate container; the outer failsafe is 2h10m, and the pilot's own deadline is 2 h via P6.
2. In a separate step: `grep -E "B110_PILOT_EXIT=|B110_PILOT_COMPLETED=|B105_SOURCE_COMMIT=" /tmp/b110-pilot.log`.
3. Offline, on the host, under nice:
   `assay analyze campaign --lane self-qualification --progress .assay/progress-b110-pilot.jsonl --state-dir .assay/b110-pilot-state --json > /tmp/b110-pilot-analysis.json`. This is P8's analysis command; use the exact flags from P8's brief.
4. Offline projection: per stratum (file × operator) p50 and p90 of `elapsed_seconds`, plus fixed overhead (the baseline `wall_s`, R0/R1 and consolidation). Compute it with a short script recorded in the report, and use Wilson 95% for the cold-kill rate.
5. Consolidation cost: `assay state import` (P9) of the pilot records into a **scratch** state dir, then time `--resume` there. **Never** import into a real campaign (D10).
6. Write `nyxloom-trove/reports/assay-B110-PILOT-REPORT.md` from the template below, and give it to the controller for the plan §8 go/no-go decision.

**Stop rules:**
- The pilot's campaign deadline (2 h) is authoritative.
- An expiry gives `B110_PILOT_EXIT=4` and `completed: false`. The candidates in `unresolved` are **unresolved, never classified**.
- Never lengthen the deadline; re-running `campaign init` cannot extend it.

### Template: `reports/assay-B110-PILOT-REPORT.md`

```markdown
# B110 pilot report — <date>

**Commit / tree:** <40-hex> / <40-hex> · **Assay version / wheel sha256:** … · **Gate log:** /tmp/b110-pilot.log
**Selection:** seed `b110-pilot-2026`, size 64 + 6 known-hard, `selection_sha256` … (`.assay/b110-pilot-selection.json`)
**Exit / completed:** B110_PILOT_EXIT=… · completed=… · unresolved=<n>

## 1. Environment
tester-unified, 3 CPUs, 2 GiB / 8 GiB mem+swap, `--pilot-jobs 3`; concurrent host load (PSI avg10 at start/end); other gates running: none.

## 2. Fixed overhead
| Phase | Wall s | Source |
| coverage baseline (R0/R1) | | progress `command_finished` |
| no-cov R2 baseline | | `r2_command.r2_baseline.wall_s` / plan event `r2_baseline_s` |
| campaign init + plan + selector | | gate log phases |

## 3. Per-candidate cost (from P0 fields)
| Stratum (file × operator) | n | killed | survived | other | elapsed p50 / p90 | materialize p50 | command p50 | started_count p50 / p90 | peak RSS p90 |

## 4. Outcomes
cold-kill rate k/n = … (Wilson 95%: … – …); survivors (id, path:line, operator, full-suite wall s); hangs/crashes/budget_exceeded (id, cause).
Known-hard set: per label, bucket + elapsed (guards from P2 expected to turn scanner hangs into fast kills).

## 5. Resources
Aggregate peak RSS …; per-worker peak RSS …; memory-full stall s … (… % of wall); CPU avg cores ….

## 6. Projection (3,760 candidates)
Formula: fixed + Σ_strata (count × p90) / effective workers; also at p50. Result: … h (p90), … h (p50). Assumptions + censoring.

## 7. Shard skew and consolidation
Hash shards (N=3) replay of measured durations vs queue: makespan …; import + `--resume` scratch consolidation: … s.

## 8. Go / no-go inputs (plan §8)
| Criterion | Threshold | Measured | Pass? |
| 1 screen clean | 0/0/0/0 | (not measured by pilot — from §9.1) | n/a |
| 2 projection | ≤ 5 h p90, ≤ 4 h p50 | | |
| 3 memory | peak ≤ 1.6 GiB, stall ≤ 5 % | | |
| 4 fixed overhead | ≤ 60 min | | |
| 5 packages merged/reviewed | P0–P8 | | |

## 9. Limitations
1.7 % of the inventory; stratified by file/operator, not by kill difficulty; censored tails; shared host; not qualification evidence.
```

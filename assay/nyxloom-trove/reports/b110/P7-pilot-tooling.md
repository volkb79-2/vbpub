# B110-P7 — Pilot tooling: non-qualifying candidate selection runs, a deterministic selector, and the pilot runbook

*Revised 2026-09-28 after round-1, round-2 and round-3 reviews (see REVIEW-2026-09-28-round{1,2,3}.md). Round 3 (G3-1): the pilot report template's GO table is renumbered to plan §8.1 criteria 1–4, "screen clean" is relabelled §8.2.1 (Qualifying GO only), the stratum label is operator × size_class (C26), and a survivor-only elapsed p90 column is added for P10's OC17 cap.*
- Round 1 applied findings P7-1..P7-7 and carver decisions C3 (via P6), C5, C6, C7 and C15.
- Round 2 applies findings P7R2-1..P7R2-7 and carver decisions C23, C27 (the `glob("*.json")` record assertion) and C31.

| Field | Value |
|---|---|
| Backlog | **B118** (split from B110) |
| Branch | `assay-b110-p7-pilot` off the integration line (the plan §11.1 / C18 reconciliation branch `assay-b110-integration`); P7b: `assay-b110-p7b-gate-modes` |
| Depends on | **P0** (B111) for the per-candidate resource and phase fields.<br>**P6** (B117) for `mutation.plan_sha256`. P6 is its **single owner** (P7-7): import it and never redefine it.<br>**P7b** (Work step 9, the gate-script `b110-pilot` and `b110-screen` modes) also needs **P3b** (`--cold-witness`), **P6** (`campaign init`, `--campaign-deadline`, the gate's `remaining_s` rule, C3) and B114's v15 work on its base. The controller dispatches it after those merge.<br>The screen's re-invocations depend on **P6's C3** change. Without it, a re-invocation would replay stale lane-timeout `budget_exceeded` records (P7-5). |
| Contract class | **2c**: bounded integration against fixed contracts |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-474 (plan D10)**. Relevant: A-461 (the `candidate_ids` inventory is equal to the bucket IDs), A-464 (the pilot is measurement, never qualification). Round-1 carver decisions:<br>• **C5**: a selection enables the state root;<br>• **C6**: two-stage GO, so the pilot feeds only "Pilot GO";<br>• **C7**: no consolidation measurement in the pilot;<br>• **C15**: dataclass fixture.<br>Round-2 carver decisions:<br>• **C23**: the pinned `run_mutation` order. The selection is applied **after** P6's full-list digest check and after P10's ledger placement, and before resume lookup.<br>• **C27**: record assertions use `glob("*.json")`, so non-JSON store files (`PILOT-STATE`, P9's `.lock` and `CAMPAIGN-IDENTITY`) are tolerated.<br>• **C31**: any `timeout` exit ≥ 124 is a failsafe with no trusted output. |
| Size | M: `cli.py` (two flags, one `_finish` closure, pilot summary), the `runner.py`/`mutation.py` selection threading plus the state-root gate, one new tool, two new test files, the gate script (P7b), `run-gate.toml` (P7b), docs |

---

## Context to read first

Paths are relative to `assay/`. Line numbers were verified at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0, §3 D10, §7 (pilot), §8 (go/no-go), §10.
2. `src/assay/cli.py`:
   - `:255-328`: the `run` parser, including `--shard` at `:289`, `--reuse-from` at `:290-300` and `--rejudge*` at `:301-327`.
   - `:725-812`: `_cmd_run`. The `--operators` lane override via `replace(...)` at `:736-762` is the **precedent** for the pilot lane override. `_resolve_state_dir` is called at `:804`.
   - `:1124`: `_run_reserved`. Its **three verdict tails** each write, emit and print, and all three are intercepted (§F):
     - `:1318-1325` (the HEAD-read LANE_TIMEOUT refusal);
     - `:1407-1413` (the evidence refusal);
     - `:1546-1553` (the common tail, reached by the normal return, the `run_lane` LANE_TIMEOUT handler at `:1527-1545`, and the adapter refusal at `:1440`).

     P6 adds a fourth, its identity refusal.
   - `:1481`: the `run_lane` call.
   - `:1820-1853`: the `assay plan` row and payload shape (`id`, `path`, `operator`, `start_byte`, `end_byte`, `lineno`, `description`). `assay plan` has **no `--json` flag**; it always prints JSON. Plan §7's `--json` is a typo.
3. `src/assay/mutation.py`:
   - `:292-315`: `InvalidRejudgeIdError` (class at `:292`) and `_reject_unknown_rejudge_ids`, the pattern for unknown IDs.
   - `:1023`: `candidate_id`.
   - `:2140-2150`: `run_mutation` refuses `resume`/shard without a `state_root`. The selection now also requires one (C5).
   - `:2318-2343`: the selection block, where the selection is applied and which is mutually exclusive with sharding.
   - `:2436-2455`: the `candidates` progress event, which gains `selection_sha256`.
   - `:2503-2523`: the `candidate_ids` stamping (a shard records its slice).
4. `src/assay/runner.py`:
   - **`:4516-4523`**: the `state_root=` expression passes a root **only** when `resume or shard_index is not None`. Without C5, a pilot (no `--resume`, no `--shard`) writes **no** state records (P7-1).
   - `:4526-4541`: the `except mutation.InvalidRejudgeIdError: raise`, which leads to the outer whole-lane refusal at `:5385`.
   - The `reuse_from` threading, which you mirror for `candidate_selection`: `run_lane` at `:5565`, `_run_higher_rigor_lane` at `:5164`, the `_run_prepared_lane` call at `:5351-5378`, the `_run_prepared_lane` parameter at `:3585`, and the `run_mutation` call at `:4449-4531`.
5. `src/assay/errors.py:57-66`: `EXIT_CODES` 0–5. **6 is unused** and belongs to the pilot.
6. `tools/b105_report_check.py`, the shape of a standalone tool. `tests/test_b105_report_check.py` runs the checker as a **subprocess** (`:18`, `:82`). The selector tests use `importlib.util.spec_from_file_location` instead (Work step 1), registering the module in `sys.modules` before `exec_module`.
7. `tests/test_cli_run.py`, found **by name**, because P1 may move it into `tests/zz_slow/test_cli_run_real_campaigns.py`: the busy-loop test's toy R0+R2 lane repo (`git_repo`, lane TOML, compare-swap site), and `run()` at `:127-130`.
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

`--resume` **is permitted** with `--candidates-file` (C5). An interrupted pilot can be resumed in the same state dir. It then resumes only records whose judge identity matches, as for any `--resume`.

**C2. The pilot sentinel.** Before any execution, `_cmd_run` writes the file `<state-dir>/PILOT-STATE` atomically (temp file plus `os.replace`). It has no `.json` extension, so the `*.json` record globs never see it (P9-1). Its content is the JSON `{"schema": "assay-pilot-state/1", "selection_sha256": "<hex>", "lane": "<lane>"}`.

Refusals, each with exit 2 before any execution and stderr naming `PILOT-STATE` (P7R2-6):
- the file exists with a different `selection_sha256`: one pilot selection per state dir;
- the file exists but is **malformed**: not valid JSON, a duplicate key, a wrong `schema`, or a key set other than exactly those three;
- the file exists for a **different `lane`**;
- the file is **absent**, but the state dir already holds `<64hex>.json` state records. That is a non-pilot store, such as a screen or a qualifying campaign, and a pilot must never write into it.

A same-selection, same-lane re-run (`--resume`) over its own sentinel passes.
- P9's `assay state import` refuses any source directory that contains `PILOT-STATE` (A-474, P7-7). Until P9 exists, this file is the machine-checkable marker; D10 is not left to operator discipline.

**D. The pilot lane.** `_cmd_run` builds it once, like the `--operators` precedent:
`replace(lane, rigor=tuple(r for r in lane.rigor if r != "R3"), judge=replace(lane.judge, mutation=replace(lane.judge.mutation, jobs=N)))`.
R3 is **not executed**.

**E. Selection threading.** `candidate_selection: frozenset[str] | None = None` goes from `run_lane` through `_run_higher_rigor_lane` and `_run_prepared_lane` to `run_mutation`, exactly like `reuse_from`.

**The order in `run_mutation` is pinned by C23 (P7R2-1).** P6, P7 and P10 all edit this block:
1. discovery (`job_list`);
2. P6's campaign plan-digest check, over the **full** discovered `job_list`;
3. P10's ledger placement (only with `--equivalence-audit`, which is refused together with `--candidates-file`, so it is inert for a pilot);
4. **this selection** (or the shard selection);
5. resume lookup.

The selection must **never** narrow `job_list` before step 2. Otherwise every pilot, which always runs with `--campaign-deadline` in P7b, would be refused `BAD_LANE_CONFIG` after its R0/R1 baselines had already run. T13 proves the combined order.

At the selection block (`:2323`), after P6's check:

```python
current_ids = [candidate_id(job) for job in job_list]
if candidate_selection is not None:
    unknown = candidate_selection - set(current_ids)
    if unknown:
        raise InvalidCandidateSelectionError(sorted(unknown))    # ERROR/BAD_LANE_CONFIG
    selected_indices = [i for i, cid in enumerate(current_ids) if cid in candidate_selection]  # PLAN order
```

- `InvalidCandidateSelectionError(AssayError)` is new, sits beside `InvalidRejudgeIdError`, and is exported.
- `runner.py:4532` becomes `except (mutation.InvalidRejudgeIdError, mutation.InvalidCandidateSelectionError, <whatever P6/P3b already added>): raise`. Keep every entry already present.
- The `candidates` progress event gains `"selection_sha256": <hex>`, **only when a selection is active**, so other streams stay byte-identical. The value is `mutation.plan_sha256(selected ids in plan order)`, **imported from P6**. P6 is the single owner. If P6 has not merged on your base, stop with `BLOCKED: plan_sha256 owner (P6) not merged`; never copy the definition.
- **C5: the selection enables the state root.** At `runner.py:4516-4523`, the condition becomes `if (resume or shard_index is not None or candidate_selection is not None)`. Every executed candidate therefore writes its state record into `--state-dir`, which is required, as with `--resume`. In `run_mutation` (`:2140-2150`), a non-`None` `candidate_selection` without a `state_root` is a `ValueError`, like resume and shard. That is a programming error, since the CLI always supplies one.
- **C15.** Adding a dataclass, or a field to one (for example if you model the parsed selection as one), requires regenerating `tests/fixtures/dataclass-contract.json` with the command P1 (B112) documents. Plain frozensets and functions need nothing.

**F. The pilot summary.** A pilot never writes a verdict.
- **Interception mechanism (P7-2).** `_run_reserved` gains **one** local closure, `_finish(verdict) -> int`, and every verdict tail calls it instead of its inline write/emit/print/return:
  - `:1318-1325`;
  - `:1407-1413`;
  - `:1546-1553`;
  - P6's identity refusal, if P6 has merged. If it hasn't, route it through `_finish` when you rebase.
- Without a selection, `_finish` does exactly what each tail does today, **in the same order**: write if `destination`, emit `verdict_written`, print the summary unless `-`, and return `verdict.exit_code`. The existing CLI tests must pass unchanged.
- With a selection, `_finish`:
  - builds the §F JSON from the in-memory verdict and prints it to stdout (`sort_keys=True, indent=2`);
  - emits `verdict_written` with `destination: null`;
  - does **not** call `write_verdict`;
  - does **not** print `_print_run_summary`;
  - returns per §G.
- Refusals **before** HEAD go through `main()`'s handler (stderr only, exit 2), and no summary is printed. The pre-HEAD rows of the decision table say so.

```json
{
  "schema": "assay-pilot-summary/1",
  "qualifying": false,
  "completed": true,
  "lane": "self-qualification",
  "commit": "<40-hex>",
  "jobs": 3,
  "requested": 2,
  "selection_sha256": "<64-hex>",
  "candidates_file_sha256": "<sha256 of the raw file bytes>",
  "state_dir": "/abs/path/.assay/b110-pilot-state",
  "r0": "PASS",
  "r1": "PASS",
  "r2": {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"},
  "r3": "not-run: pilot",
  "buckets": {"killed": 1, "survived": 1, "equivalent": 0, "crashed": 0, "hung": 0, "budget_exceeded": 0},
  "candidates": [
    {"id": "<64-hex-killed>", "path": "assay/src/assay/adapters/go.py", "operator": "python:compare-swap",
     "bucket": "killed", "execution_mode": "full"},
    {"id": "<64-hex-survivor>", "path": "assay/src/assay/adapters/go.py", "operator": "python:compare-swap",
     "bucket": "survived", "execution_mode": "full"}
  ],
  "unresolved": []
}
```

Field rules:
- **`r0` and `r1` (P7-6)** are the `status` string of the verdict's claim at that rigor level. For a whole-lane refusal (`refuse_lane` renders the refusal on every declared level), that is the refusal's status, for example `"ERROR"` or `"BUDGET_EXCEEDED"`. Use `null` when the lane does not declare the level or the verdict carries no claim for it.
- `buckets` always lists all six `MUTATION_BUCKETS`.
- `candidates` is in plan order.
- `execution_mode` is `outcome.execution.mode`.

`completed` is true **iff** all three hold:
1. an R2 claim with a mutation payload exists;
2. its `reason_code` is not `LANE_TIMEOUT`;
3. the union of the bucket IDs equals the selection.

When `completed` is false, `unresolved` lists the selected IDs that were **masked**, defined by the mask and not by the bucket (P7R2-2). Those are selected IDs that are absent from the payload, or that sit in `budget_exceeded` without a valid state record in `--state-dir`: never submitted, or cut off by the lane or campaign deadline and left unclassified by P6's C3.

A `budget_exceeded` candidate **with a valid state record** is a real, per-candidate-budget outcome. It is not unresolved, even when R2 as a whole ends `LANE_TIMEOUT`. The normal state-record validator must prove its candidate and judge identities, campaign binding, presence of `execution`, `evidence`, and `mutated_file_sha256`, and its `terminal_result` must be exactly `BUDGET_EXCEEDED` / `LANE_TIMEOUT` / `null` return code. A legacy or incomplete record without that complete candidate evidence is rejected as a resume cache entry and the candidate is re-executed; a record with no replacement-byte digest cannot be restored as an outcome.

If the run is refused before R2 (R0 fails, a config refusal, a timeout before R2), the summary still prints: `completed: false`, `r2: null`, `buckets: null`, and the refusal's `status`/`reason_code` go under `"refusal"`.

**G. Exit codes.**
- **6** iff `completed`.
- Otherwise the in-memory verdict's own `exit_code`, which is in 1–5.
- A pilot can never exit 0. Enforce this with an **explicit branch**, not an `assert`, because `assert` disappears under `-O` (P7R2-7):
  - `if code == 0:` write one stderr line, `assay: pilot: a non-completed selection produced a PASS verdict; reporting ERROR`, and set `code = EXIT_CODES[Outcome.ERROR]` (2).
  - A test covers that branch, as the 100% floor requires: monkeypatch the completeness predicate (a named private helper, `cli._pilot_completed`) to return `False` on an all-killed PASS verdict, and assert exit 2 plus that line.

**H. Selector: `tools/b110_pilot_select.py`** (a standalone script; stdlib only)

```
python tools/b110_pilot_select.py --plan PLAN.json --repo-root REPO --out CANDIDATES.txt --report SELECTION.json
                                  [--seed b110-pilot-2026] [--size 64]
```

- `PLAN.json` is exactly the stdout of `assay plan <lane> --file assay.toml`. After stripping whitespace it must be one JSON object with `status == "ok"` and a `candidates` list whose rows carry the §2 keys. The selector verifies every row's `source_sha256` against the bytes read from the matching repo-relative file before selecting anything. Trailing non-whitespace is refused (exit 2).
- The plan object must also carry lowercase 40-hex `commit` and `tree`, and its candidate list must be non-empty. These identities must match Git HEAD and tree at `REPO`; the checkout must be clean both before selection and at the final publication check. Candidate source bytes come from that immutable commit tree, not from files reopened later by pathname.
- `REPO` is the repository top. Row paths are repo-relative, for example `assay/src/assay/adapters/go.py`.
- The selection report binds `plan_commit`, `plan_tree`, the SHA-256 of the raw plan bytes as `plan_sha256`, the plan-ordered `selected_ids`, and the SHA-256 of the candidate-file bytes. The selector refuses a stale plan whose unlisted inputs changed (for example, a lane config or target inventory) even when every planned source file is unchanged. It checks Git with global, system, and ambient configuration disabled, verifies `--show-toplevel` matches `REPO`, and rechecks commit, tree and status immediately before publishing.
- Before publishing, it takes nonblocking `flock` locks on the pinned output-parent directory descriptors, ordered by device and inode. Any concurrent selector writing in either directory refuses before replacing either member of the pair. The directory locks coordinate aliases and callers with different `TMPDIR` values; they remain held until the output descriptors close after publication. It verifies requested parent identity both before and after paired publication.
- `rank(id) = hashlib.blake2b((seed + id).encode("ascii"), digest_size=16).hexdigest()`; a lower rank is picked earlier.

The stratified set **S** is built in this order:
1. Per file: for every distinct `path`, taken in sorted order, add the row with the lowest rank.
2. Operators: for each operator in sorted order of the plan's operators, if no row in S has that operator, add that operator's lowest-rank row not already in S.
3. Fill: go through all rows by ascending rank and add rows not in S until `|S| == size`, or the plan is exhausted.

**Size bound (P7-6).** Let `F` be the number of distinct files and `M` the number of operators still missing after step 1.
- If `F + M > size`, exit 2 with *"size N is smaller than per-file (F) plus missing-operator (M) coverage"*. That also covers `F > size`. So `|S| <= size` always holds.
- `|S| == size` exactly whenever the plan has at least `size` rows.

The known-hard set **H** holds 6 entries, reported separately. It is also included in the candidates file.

- **Four scanner sites.** Each is a row with `path == "assay/src/assay/adapters/go.py"`, `operator == "python:compare-swap"` and `description == "Eq->NotEq"`. Its innermost enclosing `FunctionDef` (by line span) and its `lineno`'s stripped source line must equal one of:
  - `_scan_raw_string` / `return None if end == -1 else end + 1`
  - `_strip_comments_and_literals` / `if two == "//":`
  - `_strip_comments_and_literals` / `if end == -1:`
  - `_strip_comments_and_literals` / `if close == -1:`

  Each spec must match **exactly one** row; otherwise exit 2 with the spec named.
  Consistency check: each matched row's byte span must be exactly the `==` token on its declared source line and inside the named innermost function span; a `==` from another line does not satisfy the check. Otherwise exit 2 with *"source does not match plan"*.
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

Exit 0 only after both outputs are written, both requested parents still name the pinned directories, and cleanup succeeds. Exit 2 means refusal, publication, identity, or cleanup failure; it may leave staged temporary files or a complete pair in the pinned directories, so callers must not use outputs after a nonzero exit. Successful output bytes are deterministic for identical inputs.

### Required flow (`assay run … --candidates-file`)

1. In `_cmd_run`, **before** `_resolve_state_dir` and any git call:
   - parse the file (§A), enforce the conflicts (§C), and parse `--pilot-jobs` (§B);
   - build the pilot lane (§D);
   - keep `selection` as a `frozenset` and keep `candidates_file_sha256`.
2. After `_resolve_state_dir`, write or verify `<state-dir>/PILOT-STATE` (§C2).
3. Run the ordinary reserved path with `candidate_selection=selection`. R0 and R1 run as declared. R2 runs only the selection, in plan order, on a queue of `jobs` workers, and **writes a state record per executed candidate** (C5). R3 does not run.
4. Every terminal verdict (§F) reaches `_finish`, which builds the §F summary from the in-memory verdict, prints it, emits `verdict_written` with `destination=None`, and returns per §G.

**State-store exclusion:** before pilot preflight, `_cmd_run` takes nonblocking exclusive locks. A normalized requested-path lock lives under a private per-user directory in the canonical system `/tmp`, independent of `TMPDIR`; processes using different `TMPDIR` values therefore converge on the same path lock, which also survives replacement of the store's parent. A `flock` on the admitted state-directory descriptor coordinates independent path aliases that resolve to the same store inode. The locks stay outside the judged tree and cover replacement of the state directory or its parent. Assay holds them through `_run_reserved` and its terminal summary. Qualifying runs with an explicit `--state-dir`, and qualifying `--resume` / `--shard` runs using the implicit default state root, take the same locks. A concurrent run using the same store refuses immediately with a retry-after-completion message; it does not wait outside the lane budget. Preflight and sentinel helpers use the already-open state-directory descriptor rather than reopening the path. Candidate records are also created and replaced relative to that descriptor, so a replacement path never receives writes from the admitted run. Before certification, Assay checks that the path still names the admitted directory; a moved run refuses its result.

### Topology and bounds

- Pilot state lives only in the `--state-dir` passed to the pilot run, which holds one record per executed candidate (C5) plus `PILOT-STATE`. The runbook uses `.assay/b110-pilot-state`.
- **Pilot state is never imported into a qualifying campaign** (D10). Its judge identity is shared with the full lane, because the judge identity omits the lane name and selection. So separation is enforced three ways:
  - the pilot uses a separate state dir;
  - the `PILOT-STATE` sentinel is refused by P9's `state import`;
  - P6's `campaign init` refuses a state dir holding records not bound to its deadline.

  The DESIGN-GUIDE says so.
- Bounds:
  - selection size ≤ `max_mutants`;
  - `--pilot-jobs` ≤ 8;
  - the summary lists at most the selection (≤ 10,000 rows; the pilot uses about 70).

### Decision table

| Input | Work done | Output | Exit |
|---|---|---|---|
| valid file, R2 completes, any bucket mix (including survivors, so R2 is FAIL/MUTANTS_SURVIVED) | R0 (+R1), R2 on the selection, one state record per executed candidate | summary with `completed: true` | **6** |
| valid file, every candidate killed | same | summary with `completed: true` | **6, never 0** |
| same selection re-run with `--resume` in the same state dir | only non-resumed candidates execute | summary with `completed: true` | 6 |
| `PILOT-STATE` present with a different `selection_sha256`, malformed, or for another lane; or absent while `<64hex>.json` records exist | none | stderr refusal | 2 |
| `--campaign-deadline` whose `plan_sha256` was computed over the **full** plan, plus a selection | runs; records carry `campaign_deadline_sha256` | summary with `completed: true` | 6 |
| `--campaign-deadline` whose `plan_sha256` was computed over the **selection only** | baselines, then P6's whole-lane plan-digest refusal | `completed: false`, refusal `ERROR`/`BAD_LANE_CONFIG` | 2 |
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
| any bucket mix → 6 | `cli._finish` | T8 | selection containing the survivor | "6 only on PASS" → exit 1 |
| records per executed candidate (C5) | `runner.py:4516-4523` + `run_mutation` | T3 (records clause), T9 | T3 fixture, then `--resume` | state root left `None` → no record files |
| sentinel | `cli._cmd_run` | T10 | two selections, one state dir; a malformed sentinel; another lane; a non-pilot store | no sentinel check → second pilot runs |
| C23 order with a deadline | `mutation.run_mutation` (P6 check before selection) | T13 | in-process `campaign init` + selection | selection applied before the digest → a full-plan deadline is refused |
| never exit 0 (explicit branch) | `cli._finish` | T14 | monkeypatched `cli._pilot_completed` | `assert` stripped under `-O` / no branch → exit 0 |
| budget-record authenticity | `_pilot_has_budget_record` + mutation state validator | T15–T16 | corrupted killed and injected timeout records | relabel without terminal proof → false completion; valid timeout evidence misclassified as unresolved |
| state-store exclusion | `_cmd_run`, pilot/resume helpers, and mutation-record writer | T17 | lock acquisition while `_run_reserved` is active; replace the state parent and root; verify writes stay on the admitted descriptor; acquire the same external store from a second clone | release after preflight → pilot/resume interleaving can claim the same store; judged-tree sibling lock → dirty-tree refusal; inode-only lock → replacement splits the lock; per-clone-only lock → shared external store races; path-based write → replacement receives an untrusted record; blocking flock → run waits outside its lane budget |
| consumer command | B118 section in `docs/CONSUMERS.md` | T18 | shipped documentation | omit full-plan deadline or cold witness → adopter measures a different campaign |
| every verdict tail intercepted | `cli._finish` | T4 (the common tail via the whole-lane refusal), T11 (the HEAD-read timeout tail), T12 (structural) | toy lane; `budget = "0.001s"` precedent from `tests/test_lane_timeout_writes_a_verdict.py` | a tail bypassing `_finish` → T11 prints `_print_run_summary` text or returns 4 without a summary; T12 finds a second `runner.write_verdict(` call in `_run_reserved` |
| R3 skipped | pilot lane | T6 | toy lane declaring R3 | keep R3 → canary progress events |
| selection digest | `mutation`/`cli` | T7 | T3 fixture | hash in file order → mismatch |
| selector | `tools/b110_pilot_select.py` | S1–S8 | synthetic plan + source | see the Oracles section |

### Degrees of freedom

- Private helper names.
- Where in `_cmd_run` the summary is built.
- The selector's internal structure.

**Fixed:** every spelling, exit code, JSON key, algorithm step and definition above.

## Work

1. **Red first.**
   - Add `tests/core/test_pilot_candidates_file.py` (T1–T18). Build a toy R0+R2 lane repo, following the busy-loop test's shape in `tests/core/test_cli_run.py` (found by name), with at least 3 fast-killed compare-swap sites and one surviving site. Call `run([...])` in-process.
   - **No counting process runner.** `default_process_runner` is bound as a default argument (`runner.py:1099`, `:5528`), so there is no seam for counting children (P7R2-5). Count **progress events** instead:
     - `candidate` events per candidate ID;
     - the `resume` event's totals;
     - canary-phase events.

     The one exception is P6's `runner._wait_child` seam, if a test really needs to count child processes. Monkeypatch it; never the bound default.
   - Add `tests/core/test_b110_pilot_select.py` (S1–S8). Load the tool with `importlib.util.spec_from_file_location("b110_pilot_select", PROJECT_ROOT / "tools" / "b110_pilot_select.py")`, register it in `sys.modules["b110_pilot_select"]` **before** `spec.loader.exec_module(...)` (a dataclass in the tool would otherwise fail, per P1-1), and call `main([...])`.
   - Record the red state in `assay-B118-REPORT.md`.
2. Add `InvalidCandidateSelectionError`, the selection branch, and `selection_sha256` to `mutation.py`, importing `plan_sha256` from P6's merged code (BLOCKED if it is absent).
   - Place the selection **after** P6's plan-digest check, in the C23 order.
   - Add a one-line comment at the block naming the order: `# C23: discovery → campaign digest (full list) → ledger placement → selection/shard → resume`.
3. Thread `candidate_selection` through `runner.py` (Context 4), apply the C5 state-root condition at `:4516-4523`, and extend the except tuple at `:4532`.
4. In `cli.py`:
   - add the parser flags after `--reuse-from` (`:300`); P6 also inserts one there, so rebase trivially;
   - add `_parse_candidates_file`, the conflicts, the `PILOT-STATE` sentinel and the pilot lane;
   - add the `_finish` closure, and route **every** verdict tail through it (§F);
   - add the summary and the exit code.
5. Add `tools/b110_pilot_select.py`.
6. Run the focused tests serially: the two new files, `tests/test_cli_run.py`, `tests/test_b105_cli_boundaries.py`, `tests/test_mutation_resume_sharding.py`, `tests/test_mutation_progress_budget_plan.py` and `tests/test_b106_reuse_and_witness.py`.
7. Update the docs (see Docs sync) and CHANGES.
8. Write the REPORT, then commit (**P7a**).
9. **P7b: a separate branch, `assay-b110-p7b-gate-modes`. It requires P6 and B114's v15 work (P3b/P3d) on its base; the controller dispatches it after that.**
   - **Script structure (P7-4).** Today the `case` at `tools/self-qualification-gate.sh:16-19` only validates lane names, with empty `;;` arms, and the lane logic follows later. Change it to:
   - Extend the validation `case` to accept `b110-pilot|b110-screen`.
     - Define **two shell functions**, `run_b110_pilot()` and `run_b110_screen()`. Each is declared with `name() {` at column 0 and closed by a lone `}` at column 0. Follow the sequencing, bounds, marker and failure contracts below; these normative rules supersede earlier copied function bodies.
     - After the shared clone, build and venv steps (`:49-154`), add a dispatch: `case "$requested_lane" in b110-pilot) run_b110_pilot; exit 0 ;; b110-screen) run_b110_screen; exit 0 ;; esac`. It sits before the existing `self-qualification`/preflight flow, which stays unchanged.
   - **Mode-specific timeout and completion contract (P7R7-1).** The current container wrapper waits 65 minutes for every lane except `self-qualification`; merely adding the two names would therefore truncate both B110 modes. Update `tools/self-qualification-container.sh` with an exhaustive lane `case` for the inner argv and `docker wait` timeout, and pin each bound and its completion evidence in `gate/tests/test_self_qualification_container.py`:

     | Mode | Inner bound | Wrapper `docker wait` bound | Run-gate argv timeout / budget | Required child completion evidence |
     |---|---:|---:|---:|---|
     | `self-qualification` | existing persisted campaign and 7h30m failsafe | 7h40m | none / 8h | `ASSAY_SELF_QUALIFICATION_VERIFIED=1` |
     | `self-qualification-preflight` | remaining 1h campaign seconds + 120s, with 30s `timeout --kill-after` grace; maximum 62m30s | 65m | none / 75m | `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` |
     | `b110-pilot` | Campaign init, planning, selection and Assay execution share a hard 90m per-invocation cap; planning and selection are bounded by remaining campaign seconds; Assay run gets only the remaining cap, then at most 30s `timeout --kill-after`; campaign init follows the planned ≤10m build/venv allowance, pending registered-gate measurement | 2h15m | 2h20m command timeout / 140m run-gate budget (same duration; run-gate's budget grammar accepts one unit) | `B110_PILOT_EXIT=6` and `B110_PILOT_COMPLETED=1`; reject init-refused, timeout-failsafe, absent or non-6 exit markers |
     | `b110-screen` | Assay lane's 5h budget; 7h10m timeout plus 30s `timeout --kill-after` is a failure-only failsafe | 7h15m | 7h30m / 8h | `assay verify` passes and `b110_screen_report_check.py` emits `B110_SCREEN_VERIFIED=1` only for the current commit/tree with R0/R1 PASS, a complete R2 mutation payload matching the current plan inventory, and R2 status `PASS` or `FAIL`; it refuses R2 `ERROR`, `BUDGET_EXCEEDED`, `INCONCLUSIVE` and `LANE_TIMEOUT`; verdict exit must match producer status; the raw exit and a nonempty verdict alone never certify completion |

     The wrapper's log-follow bound remains `docker wait` bound + 90s. Keep
     the existing self-qualification/preflight behavior unchanged. The pilot's
     campaign deadline remains authoritative across retries. Campaign init,
     planning, selection and Assay execution share the 90m per-invocation work
     cap; planning and selection are also bounded by remaining campaign time.
     The planned 10m build/venv allowance precedes campaign init and remains
     unmeasured until the registered gate records its timing. The 2h15m wrapper
     includes the planned ≤10m build/venv allowance, two-hour campaign, and
     five-minute log/cleanup margin. The 2h20m run-gate timeout adds a further
     five-minute outer margin. The
     screen has no campaign deadline; its 5h Assay lane budget is the effective
     work limit, while the explicit 7h10m timeout, 7h15m wait and 7h30m
     run-gate timeout leave process/container cleanup time. Print
     `ASSAY_B110_GATE_COMPLETE=<mode>` only
     after the mode-specific evidence is verified, alongside the verified
     container exit marker. A timeout or incomplete pilot is a failed gate
     invocation, not a completed measurement.

     For `b110-screen`, first write `.assay/b110-screen-plan.json` from the
     exact commit/tree with `assay plan`. After `assay run`, call
     `assay verify .assay/verdict-b110-screen.json`, then
     `tools/b110_screen_report_check.py` with the plan, verdict and expected
     commit/tree. It must bind the verdict to that plan, require R0 and R1
     `PASS`, require an R2 mutation payload whose candidate IDs exactly cover
     the plan inventory with no duplicates, and refuse `LANE_TIMEOUT`. Only a
     successful check emits `B110_SCREEN_VERIFIED=1`; the wrapper requires
     that marker before emitting `ASSAY_B110_GATE_COMPLETE=b110-screen`.
     This accepts a genuine complete survivor screen with exit 1, while an R0
     refusal with exit 1 and a nonempty verdict is rejected. The helper accepts
     completed R2 `PASS` or `FAIL` outcomes whose payload accounts for the full
     plan; R2 errors, budget exhaustion and inconclusive outcomes do not
     complete the screen. It uses the R2 claim status rather than an exit-code
     allowlist as a proxy.

     Tests must construct each legitimate mode and assert the exact selected
     wait/inner timeout (including the log-follow margin), marker allowlist,
     and refusal of timeout, missing-marker, wrong-mode-marker, and empty or
     malformed verdict cases. This prevents a timeout guard from passing on a
     mere marker-name substring or refusing an ordinary survivor screen.
   - `run_b110_pilot()` follows this bounded sequence:
     1. Start the 90-minute invocation clock before campaign initialization and clear only the current attempt's plan, selection, summary and run log. Preserve the campaign deadline, mutation state and progress stream for safe resume.
     2. If the campaign deadline does not exist, initialize the two-hour campaign through `timeout --kill-after=30s`, bounded by the remaining invocation cap. An initialization refusal emits `B110_PILOT_INIT_REFUSED=1`; a timeout emits `B110_PILOT_TIMEOUT_FAILSAFE=1`.
     3. Bound each of `assay plan`, `b110_pilot_select.py` and `assay run` by the smaller of the remaining 90-minute invocation cap and remaining persisted campaign time. Refuse a zero-second remainder; use `timeout --kill-after=30s` for each phase.
     4. Run Assay with the selected candidate file, three pilot jobs, `--cold-witness --resume`, `.assay/b110-pilot-state`, `.assay/progress-b110-pilot.jsonl` and the persisted campaign deadline. Capture stdout in the summary and stderr in the run log.
     5. After Assay exits, re-read both the invocation remainder and persisted campaign remainder. If either is exhausted, replace the process status with 124 before writing completion markers. Emit `B110_PILOT_COMPLETED=1` only for status 6; the wrapper rejects every other status and any timeout/refusal marker.
   - Add a `run-gate.toml` lane `[lanes.b110-pilot]` using the current B105 launcher topology:
     - `kind = "command"`, `environment = "bare-host"`; the run-gate lane calls `tools/self-qualification-container.sh`, which owns the bounded cgroup-visible tester-unified child. Do not run the inner script directly in a generic tester-unified run-gate container: it needs Docker access to launch and inspect that child.
     - `argv = ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "2h20m", "bash", "{worktree}/assay/tools/self-qualification-container.sh", "{worktree}", "b110-pilot"]`;
     - `clean_tree = true`, `budget = "140m"` (run-gate budget durations use one unit; the argv timeout remains `2h20m`);
     - `resources = { shared = ["assay-self-qualification"] }`; the wrapper owns the pilot child limits and host cgroup placement, using the same verified 3 CPU, 2 GiB memory, and 8 GiB memory-plus-swap envelope as the B105 lanes.
     - `artifacts` = the `.assay/b110-pilot-*` files and `progress-b110-pilot.jsonl`, including `.assay/b110-pilot-run.log`;
     - a comment citing A-474 and giving the **margin (P7-6, P7R2-4)**: `2h20m ≥ clone/build/venv (planned ≤ 10 min allowance, to be measured and recorded in the REPORT from the gate log) + 2 h campaign + 5 min wrapper/log/cleanup margin + 5 min run-gate margin`; Assay execution uses only the remaining 90m invocation cap, followed by at most 30s kill-after.
       - The campaign is initialised right **after the build and before** `assay plan` and the selector. So plan and selector time are inside the 2 h campaign, and the outer failsafe only has to add the build time.
   - Add `tools/b110_screen_report_check.py` and `gate/tests/test_b110_screen_report_check.py`. Accept a valid complete survivor verdict with R0/R1 PASS and R2 `PASS` or `FAIL`; refuse a verifier-valid R2 `ERROR/EXEC_FAILED` crashed-mutant verdict, an R0 failure that has exit 1 and a nonempty verdict, an R1 failure, a missing R2 payload, a wrong commit/tree, an incomplete or duplicate candidate inventory, and `LANE_TIMEOUT`.
   - Extend the substring and behavior pins in `gate/tests/test_self_lane.py` with the 90m pilot cap, prior-verdict removal before planning, checker/producer exit-code equality, redirected run output and failure propagation. Extend `gate/tests/test_self_qualification_container.py` to prove both B110 modes use their exact bounds, are launched only through the verified child-container path, and emit the correct outer completion marker only after validating their mode-specific child evidence. In particular, prove that an R0 failure with exit 1 and a nonempty verdict cannot emit the screen completion marker.
   - Pin that, inside `run_b110_pilot`'s body, `campaign init` precedes `plan self-qualification`.
   - **Survivor-screen mode** (plan §9.1). `run_b110_screen()` follows this order:
     1. Preserve `.assay/verdict-b110-screen-prev.json` as an explicit `--reuse-from` source when present; remove the current verdict before planning or running.
     2. Write a plan for the full current inventory, then run `assay run self-qualification --cold-witness --resume --state-dir .assay/b110-screen-state --progress .assay/progress-b110-screen.jsonl --verdict-json .assay/verdict-b110-screen.json` under the 7h10m timeout. Redirect the complete command output to `.assay/b110-screen-run.log`.
     3. Capture and write the exact Assay process status. For a timeout failsafe, emit `B110_SCREEN_TIMEOUT_FAILSAFE=1` and return without any completion marker.
     4. For other statuses, run `assay verify`, then `b110_screen_report_check.py` with the plan, verdict, current commit/tree and captured process status. The checker accepts only R0/R1 PASS plus an exact full-plan native R2 payload whose status is `PASS` or `FAIL`; an R2 error, budget exhaustion, inconclusive result, timeout or mismatched exit status fails.
     5. Emit `B110_SCREEN_VERIFIED=1` only after both checks pass. Every output write must propagate failure because the dispatcher calls `run_b110_screen || exit $?`, which disables `errexit` inside the function. The outer wrapper emits `ASSAY_B110_GATE_COMPLETE=b110-screen` only after validating the child log and exit status.
     - The launcher clears the current verdict, plan and run log under the shared Git-common-dir lock before ordinary host/Docker admission checks, so refused attempts cannot expose prior artifacts as current. It preserves `.assay/progress-b110-screen.jsonl`, `.assay/b110-screen-state` and the explicit previous-verdict reuse input.
     - It passes **no** `--campaign-deadline`. The screen is non-qualifying (plan §9.1); Assay's `self-qualification` lane budget is 5h, and the 7h10m inner timeout, wrapper's 7h15m wait, run-gate 7h30m timeout and 8h budget are outer failure/cleanup bounds. A re-invocation resumes `.assay/b110-screen-state`.
     - It depends on **P6's C3**: a candidate the lane budget cut off is unrecorded and re-executes on the next invocation. So re-invocations make progress instead of replaying stale `budget_exceeded` records (P7-5).
     - It never passes `--candidates-file` or `--shard`. `--reuse-from` combined with `--resume` is accepted today, since only `--shard` is refused (`runner.py:5983-6050`). `"${reuse_args[@]}"` with an empty array under `set -u` needs bash ≥ 4.4, which tester-unified has; note it in the REPORT.
     - Add a `run-gate.toml` lane `[lanes.b110-screen]` with the same bare-host `self-qualification-container.sh` topology as `b110-pilot`, but:
       - `argv = ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "7h30m", "bash", "{worktree}/assay/tools/self-qualification-container.sh", "{worktree}", "b110-screen"]`;
       - `clean_tree = true`, `budget = "8h"`;
       - `resources = { shared = ["assay-self-qualification"] }`;
       - `artifacts` = `.assay/b110-screen-plan.json`, `.assay/verdict-b110-screen.json`, `.assay/b110-screen-run.log` and `.assay/progress-b110-screen.jsonl`;
       - a comment: "non-qualifying survivor screen (plan §9.1); never evidence for B105 except through the B119 import path, whose use for pre-deadline records awaits the operator's D7 answer (C8)".
     - Extend the `gate/tests/test_self_lane.py` pins with `b110-screen`, `B110_SCREEN_EXIT=` and `verdict-b110-screen-prev.json`, and extend the container-wrapper acceptance to bind the screen mode to its verified child container.
     - **Static/body oracle (P7-4).** Extract each function body from the script text: from the line `run_b110_screen() {` to the next line that is exactly `}`, and the same for `run_b110_pilot`. Assert that the screen body contains `--cold-witness`, `--resume`, a call using `"$assay_bin" verify` (or an equivalent executable variable followed by the `verify` subcommand), `b110_screen_report_check.py`, `B110_SCREEN_VERIFIED=1`, and a check that the R2 claim status is only `PASS` or `FAIL`; it contains neither `--campaign-deadline` nor `--candidates-file`. Assert it removes the prior verdict before the current `assay run`. Assert that the pilot body contains `--candidates-file`, `--campaign-deadline`, and post-run checks for both invocation and campaign expiry. Assert that the dispatch `case` contains `run_b110_screen` and `run_b110_pilot`. Behavioral tests must prove the verifier executes before the checker, stale screen artifacts are removed before host/Docker refusal while lock contention preserves an active attempt, neither verification nor checker failure emits the marker, and pilot status 6 is reclassified after either bound expires.

## Oracles

**CLI (T1–T12).** Each is in-process `main()` on a toy lane, with no timing. T12 is a static AST check.

- **T1: file grammar.**
  - *Observable:* uppercase, trailing-space, duplicate and empty files each give exit 2, a stderr message naming the line or ID, and no progress `run` header.
  - *Negative:* a lenient parser executes the lane.
- **T2: conflicts.**
  - *Observable:* each of `--verdict-json v`, `--shard 0/2`, `--reuse-from x`, `--rejudge <id>`, `--rejudge-outcome killed`, a missing `--state-dir`, `--pilot-jobs 0`, `--pilot-jobs 9`, a 5000-digit `--pilot-jobs` value, `--pilot-jobs 2` without `--candidates-file`, and an R0/R1-only lane gives exit 2 with nothing executed. For `--verdict-json`, the file `v` does not exist afterwards.
- **T3: selection.** Select 1 of 3 killable sites.
  - *Observable:*
    - exit 6;
    - stdout is a JSON document with `qualifying` false and `completed` true;
    - exactly one `candidate` progress event, whose `candidate_id` is the selected one;
    - the summary's `candidates` holds exactly that ID;
    - no verdict file is written anywhere under the repo;
    - `progress` ends with `verdict_written` with `destination: null`;
    - **C5, asserted via `glob("*.json")` (C27):** `sorted(p.name for p in state_dir.glob("*.json"))` is exactly one `<64hex>.json` record, whose `candidate_id` is the selected ID, and `PILOT-STATE` exists. Other **non-JSON** files in the dir are tolerated, because P9 later adds `.lock` and `CAMPAIGN-IDENTITY`. **Never** assert on the full directory listing. No `--resume` was passed.
  - *Negative:* an implementation that ignores the selection emits 3 events. One that leaves the state root at `None` writes no record.
- **T4: unknown ID.**
  - *Observable:* a well-formed ID absent from the plan gives exit 2 after the baseline `command_started` event, a summary with `completed: false` and `refusal.reason_code == "BAD_LANE_CONFIG"`, no `candidate` event, and no `PILOT-STATE` sentinel.
- **T5: never PASS.**
  - *Observable:* selecting only killed sites gives exit **6**, with `buckets.killed` equal to the selection size.
  - *Negative:* returning the verdict code gives 0.
- **T6: R3 skipped.**
  - The toy lane declares `rigor = ["R0","R2","R3"]` with a valid canary.
  - *Observable:* `summary["r3"] == "not-run: pilot"`. The progress stream has no canary-phase event, and its `candidate` events are exactly the selected IDs. These are counted from progress events, not from a process runner (P7R2-5).
- **T7: digest.**
  - *Observable:* the `candidates` event's `selection_sha256` equals the summary's, and equals the §E encoding over the selected IDs in **plan** order, even when the file lists them in reverse.
- **T8: any bucket mix exits 6 (P7-3).** Select two IDs: one killable site and the surviving site.
  - *Observable:* exit **6**, `completed: true`, `r2 == {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}`, `buckets.survived == 1` and `buckets.killed == 1`.
  - *Negative:* "6 only when PASS" exits 1.
- **T9: `--resume` with a selection (C5).** Re-run T3's selection with `--resume` and the same `--state-dir`.
  - *Observable:*
    - exit 6 and `completed: true`;
    - the second run's progress segment has **no** `candidate` event for the resumed ID;
    - its `resume` event reports one resumed record.
  - *Negative:* with no record from the first run (the P7-1 defect), a `candidate` event for it appears again.
- **T10: the sentinel.**
  - (a) Run pilot A with selection X into state dir S, then pilot B with a different selection Y into S. B exits 2 before any execution, with stderr naming `PILOT-STATE`, and `S/PILOT-STATE` still holds X's `selection_sha256`.
  - (b) A malformed `PILOT-STATE` (a duplicate key, or a missing `lane`) gives exit 2.
  - (c) A `PILOT-STATE` for another lane gives exit 2.
  - (d) (P7R2-6) A state dir with no `PILOT-STATE` that already holds a `<64hex>.json` record, written by an ordinary `--resume --state-dir` run of the same lane, gives exit 2, and no sentinel is written.
- **T11: the HEAD-read timeout tail (P7-2).** Use a pilot selection on a lane with `budget = "0.001s"`, following the precedent in `tests/test_lane_timeout_writes_a_verdict.py`.
  - *Observable:* a §F summary on stdout with `completed: false`, and `refusal.reason_code == "LANE_TIMEOUT"`; exit 4; no verdict file anywhere; `verdict_written` with `destination: null`; no `_print_run_summary` text.
  - *Negative:* the `:1318-1325` tail left inline writes or prints the ordinary summary.
- **T12: one write site (structural, supplementary).** Parse `src/assay/cli.py` with `ast`. Inside `_run_reserved`, exactly one call to `runner.write_verdict` exists, and it sits inside `_finish`.
  - *Negative:* any tail keeping its own write.
- **T13: selection under a campaign deadline, in the C23 order (P7R2-1).** Run in-process: `main(["campaign", "init", "--campaign", "t13", "--lane", lane, "--hours", "1", "--state-dir", S, ...])`, then `main(["run", lane, "--candidates-file", F, "--state-dir", S, "--campaign-deadline", <file>, ...])` with a 1-of-3 selection.
  - *Observable:*
    - exit 6 and `completed: true`;
    - the one record in `S` carries `campaign_deadline_sha256 == sha256(<file bytes>)`.
  - Then hand-write a second deadline file for the same identity whose `plan_sha256[lane]` is `mutation.plan_sha256` over the **selected** IDs only. The run with it gives exit 2, a summary with `completed: false` and `refusal.reason_code == "BAD_LANE_CONFIG"`, and no `candidate` event.
  - *Negative:* a selection applied before P6's digest check refuses the full-plan file and accepts the selection-only one.
- **T14: never exit 0 (P7R2-7).** Use an all-killed selection, with `cli._pilot_completed` monkeypatched to return `False`.
  - *Observable:* exit 2, and stderr contains the §G line.
  - *Negative:* an `assert`-based guard exits 0 under `python -O`, and the missing branch leaves the floor uncovered.

- **T15: invalid budget records cannot complete a pilot.** Relabel a killed record as `budget_exceeded` and remove or corrupt the terminal command evidence. The candidate remains in `unresolved`, `completed` is false, and exit 6 is impossible. The helper also rejects records that fail the full identity/state validation.
- **T16: genuine per-candidate timeout evidence.** Inject a deterministic `BUDGET_EXCEEDED` / `LANE_TIMEOUT` command result with no return code for a mutated candidate. Its state record carries the exact `terminal_result`, the candidate is not unresolved, and the overall pilot remains incomplete with exit 4 because the R2 claim is `LANE_TIMEOUT`. Removing candidate identity, source or mutated-file digest, execution, evidence, or changing the campaign binding makes the record unusable.
- **T17: state-store exclusion spans the run.** During a qualifying `--resume` and during a pilot, a second acquisition of the stable lock refuses promptly while `_run_reserved` is active; after it returns, the lock is available. Replace the state root and its parent during an admitted run: a second same-checkout acquisition still refuses on the path lock, the original run refuses certification, and the mutation writer leaves records in the admitted directory rather than the replacement. Acquire one external store from two independent Git clones and prove the second refuses. This distinguishes path-keyed exclusion and descriptor-relative writes from inode-only, per-clone, or path-based alternatives. A bounded contender test proves lock conflict does not wait outside the lane budget.
- **T18: consumer example follows the B110 flow.** The published example initializes the campaign for the full self-qualification plan, uses `--cold-witness` and `--campaign-deadline`, and labels the result measurement only.

**Selector (S1–S12).** These use a synthetic plan JSON and synthetic source files under `tmp_path`, laid out as `assay/src/assay/...`.

- **S1: determinism.** Two runs produce byte-identical `CANDIDATES.txt` and `SELECTION.json`. With a different `--seed`, the stratified set differs (on a fixture large enough that this is certain).
- **S2: one per file and operator coverage.**
  - *Observable:* every path is represented. An operator that appears only in a row that loses every per-file pick is still present, with `reason: "operator"`.
- **S3: fill and bounds.** `|S| == size` when the plan has at least `size` rows. A plan smaller than `size` gives all rows.
- **S8: the size bound (P7-6).** Use a plan whose file count equals `size` and in which one operator appears only in rows that lose every per-file pick. So `F + M = size + 1`.
  - *Observable:* exit 2 with the §H message naming F and M.
  - *Negative:* the old algorithm produced `|S| = size + 1`.
- **S4: known-hard exact matching.**
  - *Observable:* a synthetic `go.py` containing the four exact lines in the named functions yields 4 labelled rows.
  - A duplicated line in the same function gives exit 2, naming the spec. A span that does not slice to `b"=="`, points to an equality on another line, or falls outside the named function gives exit 2 with *"source does not match plan"*.
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
- **S9: source checkout binding.** Every plan row carries a lowercase 64-hex `source_sha256`; the selector compares it with the bytes it reads for that repo-relative path before writing outputs. Changing any source byte while retaining the old plan gives exit 2 and writes neither output.
- **S10: exact plan identity.** The plan's commit and tree must match Git HEAD and its tree at `REPO`, and the worktree must be clean at initial check and immediately before publication. Planned source bytes are read from the immutable Git tree object. A commit or tree mismatch, dirty checkout, or source-hash mismatch returns exit 2 before either final output is written. A stale-plan oracle changes only an unlisted plan input in a new commit while leaving every planned source byte unchanged. Another oracle dirties an unlisted tracked input after initial identity checking but before publication; selection must refuse and leave no final report. The report records `plan_commit`, `plan_tree`, the raw plan's `plan_sha256`, plan-ordered `selected_ids`, and `candidates_file_sha256` so a controller can reconcile the measured sample to its source plan.
- **S11: ambient Git isolation.** Set `GIT_DIR` and `GIT_WORK_TREE` to a clean clone while the supplied `--repo-root` is dirty. The selector must inspect the supplied checkout and refuse it as dirty.
- **S12: output/source separation.** Point either output directly or through a symlink at a source file named by the plan. The selector refuses before writing either output and leaves the source bytes unchanged.
- **S13: output descriptor pinning.** Redirect an output-parent symlink to a planned source directory after selection starts. The selector either refuses or writes only through the originally pinned parent descriptor; the source file remains unchanged. A pre-existing output-parent alias of a planned source file is refused by parent inode plus basename identity.
- **S14: report commit marker.** Inject a failure replacing the report after candidates are installed. The old report is already removed, no report remains, and consumers cannot bind the new candidate bytes to stale selection metadata.
- **S15: concurrent paired publication.** Run two selector processes with different seeds and the same output paths but different `TMPDIR` values; pass one through a symlink alias to the shared directory. Pause the first after replacing the candidate file; the second must refuse while the shared parent-directory lock is held. After the first completes, the report digest must match the final candidate bytes. Also exercise partially overlapping output-parent sets and inject failure acquiring the second parent lock; the first lock must be released as the output descriptors close, so a retry can acquire both.
- **S16: cleanup failure releases locks.** Inject a publish failure after both parent locks are acquired, then fail removal of one staged file. The selector returns exit 2, still closes every pinned output descriptor, and a fresh invocation can acquire the same output parents. Also inject a close error after successful publication; exit 2 may leave a complete digest-consistent pair in the pinned directory, which callers must not use.
- **S17: parent retarget during publication.** Retarget an output-parent alias after the report replacement but before the paired-publication helper returns. If the retarget remains when the post-publication identity check runs, the selector returns exit 2, leaves the replacement directory untouched, and the pair written through the pinned descriptors has a matching report digest. Also retarget and restore the alias before that check; selection may return 0 with the same digest-consistent pinned pair.

**P7b (O-gate).**
- `gate/tests/test_self_lane.py` asserts the P7b substrings and the static function-body oracle (Work step 9); `gate/tests/test_self_qualification_container.py` verifies both modes use the current bare-host / verified cgroup-visible tester-unified child-launch path.
- Both lanes parse under `./run-gate.py --list`, which is a read-only listing.

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
  - why a selection writes state records (C5): the pilot's measurement lives in them;
  - why pilot state must never be imported: the judge identity omits the lane and the selection. Hence the `PILOT-STATE` sentinel.
- **CONSUMERS (HOW):** a pasteable `assay plan` → selector → `assay run --candidates-file` sequence, and how to read the summary.
- **CHANGES:** `### Added` for B118.

## Scope / forbid

- **Touch:**
  - `src/assay/cli.py`;
  - `src/assay/runner.py`: the threading, the C5 state-root condition at `:4516-4523`, and the except tuple only;
  - `src/assay/mutation.py`: the selection branch, the error class, the progress key, and the `state_root` requirement for a selection. `plan_sha256` is **imported from P6, never defined here**;
  - `tests/fixtures/dataclass-contract.json`, only under C15;
  - `tools/b110_pilot_select.py` (new);
  - `tests/core/test_pilot_candidates_file.py`, `tests/core/test_b110_pilot_select.py`;
  - README, DESIGN-GUIDE, CONSUMERS, CHANGES, `nyxloom-trove/reports/assay-B118-REPORT.md`;
  - P7b only: `tools/self-qualification-gate.sh`, `tools/self-qualification-container.sh`, `run-gate.toml`, `gate/tests/test_self_lane.py`, `gate/tests/test_self_qualification_container.py`.
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
- T4 cannot produce a summary because the refusal happens before a verdict exists;
- `mutation.plan_sha256` (P6) is not on your base;
- P6's digest check is not on your base, so T13 cannot be written. Do not reorder around a missing check;
- routing a verdict tail through `_finish` changes any existing CLI test's output.

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

This mirrors plan §7. It is carried out only after all of these are on the integration line: P0–P8, B114's v15 work (P3a–P3d; the pilot uses `--cold-witness`), and P7b. The controller must also have approved it.

**Preconditions:**
- `docker ps` shows no gate container;
- the host-load rule is respected;
- the integration-line tip is clean;
- `python ./run-gate.py tester-unified` has passed on that exact commit.

**Commands** (from `<worktree>/assay` on the host):
1. `python ./run-gate.py b110-pilot > /tmp/b110-pilot.log 2>&1; echo "exit=$?"`. This is the only gate container.
   - The outer failsafe is 2h20m.
   - The pilot's own deadline is 2 h, as P6 campaign `b110-pilot-<commit12>`, created inside the gate after the build.
   - Never run `campaign init` on the host.
2. In a separate step: `grep -E "B110_PILOT_EXIT=|B110_PILOT_COMPLETED=|B110_PILOT_TIMEOUT_FAILSAFE=|B110_PILOT_INIT_REFUSED=|B105_SOURCE_COMMIT=" /tmp/b110-pilot.log`.
   - `B110_PILOT_TIMEOUT_FAILSAFE=1` means a timeout failsafe fired or the post-run check found that the 90-minute invocation cap or persisted campaign deadline had expired. A status discovered after Assay exits is normalized to `B110_PILOT_EXIT=124`; if no time remains before `assay run`, the wrapper can refuse before writing an exit marker. The summary is not a completed pilot. If only the invocation cap expired and the campaign deadline remains active, rerun the same registered lane to resume it. An expired campaign cannot be extended or reused as a completed measurement.
   - `B110_PILOT_INIT_REFUSED=1` means no pilot ran.
   - An outer run-gate exit ≥ 124 is likewise a failsafe with no trusted output.

**Re-piloting on a new commit (P7R2-6).** The new commit gets a new deadline file name, `b110-pilot-<new commit12>`. But `.assay/b110-pilot-state` still holds the old commit's deadline-bound records and its `PILOT-STATE`: P6's `init` refuses the unbound records, and the sentinel refuses the new selection. The append-only `.assay/progress-b110-pilot.jsonl` also contains the previous run, and analysis must not combine progress from one commit with another commit's plan or state. So **before** re-piloting, move both files aside with the old report (for example, `.assay/b110-pilot-state-<old commit12>` and `.assay/progress-b110-pilot-<old commit12>.jsonl`). The new pilot then starts with fresh active paths. Preserve both archived files; never delete or edit their contents.
3. Offline, on the host, under nice: run P8's `assay analyze campaign` over the matching progress and state pair: the active `.assay/progress-b110-pilot.jsonl` with `.assay/b110-pilot-state`, or, for an archived pilot, `.assay/progress-b110-pilot-<commit12>.jsonl` with `.assay/b110-pilot-state-<commit12>`. Use the exact flags from P8's brief, including P8's required `--project-jobs 3` for the projection. Write the result to `/tmp/b110-pilot-analysis.json`.
4. Offline projection: use P8's projection output, with the plan §7/C19 strata fall-back (operator × file-size class → operator → all), p50 and p90, plus fixed overhead:
   - the coverage-baseline wall time;
   - the no-cov baseline `wall_s`;
   - R0/R1;
   - an R3 estimate of two suite runs taken from the measured baselines (C19).

   Use Wilson 95% for the cold-kill rate. **No consolidation measurement here (C7):** consolidation cost is measured in B119's acceptance.
5. Write `nyxloom-trove/reports/assay-B110-PILOT-REPORT.md` from the template below, and give it to the controller for the plan §8 **Pilot GO** decision (C6).

**Stop rules:**
- The pilot's campaign deadline (2 h) is authoritative.
- Assay returns exit 4 and writes a `completed: false` summary only if it observes campaign expiry and exits before the wrapper's GNU `timeout` fires. The wrapper bounds the run by the floored remaining campaign seconds, so its timeout can fire first; that produces exit 124 and may leave no usable summary. Either path is an incomplete pilot: the B110 gate reports `B110_PILOT_EXIT=124` and `B110_PILOT_TIMEOUT_FAILSAFE=1`, and no incomplete or missing summary is a result. If Assay writes a summary, candidates in `unresolved` are **unresolved, never classified**: they have no state record, and P6's C3 left any in-flight one unclassified. If GNU `timeout` fires first, any candidate without a state record is likewise unclassified even when no `unresolved` list was written. A `budget_exceeded` candidate **with** a record (its per-candidate budget) is a real outcome and is reported, not unresolved.
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
| campaign init, then plan + selector (inside the 2 h campaign) | | gate log phases |

## 3. Per-candidate cost (from P0 fields)
| Stratum (operator × size_class: small ≤10 / medium 11–100 / large >100 candidates per file, C26) | n | killed | survived | other | elapsed p50 / p90 | survivor-only elapsed p90 | materialize p50 | command p50 | started_count p50 / p90 | peak RSS p90 |

## 4. Outcomes
cold-kill rate k/n = … (Wilson 95%: … – …); survivors (id, path:line, operator, full-suite wall s); hangs/crashes/budget_exceeded (id, cause).
Known-hard set: per label, bucket + elapsed (guards from P2 expected to turn scanner hangs into fast kills).

## 5. Resources
Aggregate peak memory from the run-gate cgroup `memory.peak` … (C19: this is the GO number); per-candidate 1 Hz RSS samples (a lower bound only) …; memory-full stall s … (… % of wall); CPU avg cores ….

## 6. Projection (3,760 candidates)
Formula: fixed (coverage baseline + no-cov baseline + R0/R1 + R3 estimate as two suite runs) + Σ_strata (count × p90) / effective workers; also at p50. Strata fall-back: operator × size_class (small ≤10 / medium 11–100 / large >100 candidates per file) → operator → all (C19, C26). Result: … h (p90), … h (p50). Assumptions + censoring.

## 7. Shard skew
Hash shards (N=3) replay of measured durations vs queue: makespan …. (Consolidation cost is not measured by the pilot; it is B119's acceptance, C7.)

## 8. Pilot GO inputs (plan §8.1 criteria 1–4, C6)
| Criterion | Threshold | Measured | Pass? |
| 8.1.1 projection | ≤ 5 h p90, ≤ 4 h p50 (strata operator × size_class, C26) | | |
| 8.1.2 memory | cgroup `memory.peak` ≤ 1.6 GiB, stall ≤ 5 % | | |
| 8.1.3 fixed overhead | ≤ 60 min (preflight + coverage and no-cov baselines + R3 estimate + consolidation/verify) | | |
| 8.1.4 readiness | P0–P8, B114 v15 work (P3a–P3d), P7b merged, reviewed, gated; P8 analysis reports no evidence error | | |
| (§8.2.1, Qualifying GO only) screen clean | complete screen at X*, 0/0/0/0 except audited ledger entries | not a Pilot GO input | — |

Section 3's survivor-only elapsed p90 is the source for P10's ledger cap (OC17).

## 9. Limitations
1.7 % of the inventory; stratified by file/operator, not by kill difficulty; censored tails; shared host; not qualification evidence.
```

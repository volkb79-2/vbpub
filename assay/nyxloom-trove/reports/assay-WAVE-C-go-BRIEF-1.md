# Wave C (Go) — continuation brief 1

**Written at checkpoint 1** (E-008 clause: ~60 tool calls reached, cut at a
commit boundary). Cumulative-delta only — this is the first brief, so it is
also the seam map; brief 2 must NOT re-copy it.

Branch `feature/assay-wave-c-go`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-go`, from `main` at `25b1f7fb`.

---

## 1. What is DONE and committed

| wave item | state |
|---|---|
| **2. the oracle (B047 item 1)** | **DONE**, `271af037`. Proven against all 8 frozen witnesses. |
| **1. `blocks` field + core join (A-239)** | **DONE** for the model/parser/core half — see §3 for the half that remains. |
| 3. fixture regen (F008-A4) | not started |
| 4. `external_tools = ("go",)` (B047 item 2) | not started |
| 5. `go-cover` producer vocabulary (B047 item 3) | not started |
| 6. gate-envelope `helpers[]` (B047 item 5) | not started |
| 7. srdm covergate qualification (F008-A5) | not started |

## 2. The load-bearing facts a successor must not re-derive

**The oracle works and is proven.** `src/assay/helpers/go/stmtpos/stmtpos.go`
(+ `go.mod`) is transcribed from `cmd/cover`'s instrumenter. Its derived
extents and `numStmts` reproduce **all eight** frozen P27 profiles exactly;
`collision-colA` → `{4,6}` and `collision-colB` → `{4,5}` from byte-identical
profile bytes; `commit2/calc.go` → executed `{5,12,13,22,25}` / missing
`{15,30}`, matching the independent hand manifest. Evidence and provenance:
`nyxloom-trove/carve-assets/P27-recarve/`. **Do not re-probe to confirm this;
re-probe only if you change `stmtpos.go`.**

**The image had to be built.** `tester-unified-go:local` was NOT on this host.
Built from the committed `vbpub/tester-unified-go/Dockerfile`:

```sh
docker build -f tester-unified-go/Dockerfile -t tester-unified-go:local \
    /workspaces/vbpub/tester-unified-go
```

Probe recipe (committed, re-runnable):
`ASSAY=/workspaces/vbpub/.worktrees/assay-wave-c-go/assay bash
nyxloom-trove/carve-assets/P27-recarve/probe-stmtpos.sh`. Toolchain in the
image is **go1.25.14**; the frozen witnesses are go1.25.12, and that delta is
*measured* inert (all eight extents joined) rather than assumed.

**`go run` needs the files in ONE directory** — hence the committed `go.mod`
and `go run .` with cwd = the helper dir, not `go run /abs/path/x.go` alongside
other dirs. That was a real failure, not a preference.

**`carve-assets/P27/` is carver-owned and was not touched.** New evidence goes
to `carve-assets/P27-recarve/`.

## 3. Seams — file:line, current state

| seam | where | state |
|---|---|---|
| `CoverageBlock` | `src/assay/coverage_parsers/model.py`, after `ClassifiedLineBudget` | **added** |
| `FileCoverage.blocks` | same file, field list | **added**, `tuple[CoverageBlock,...] \| None = None` |
| `CoverageProfile.statement_attributed` | same file | **added**, `bool = False` |
| `go_cover.parse` emits blocks | `src/assay/coverage_parsers/go_cover.py:~110` | **added**; `_parse_block` now returns `(path, CoverageBlock)`, `_parse_pos` returns `(line, col)` |
| the pure join | `src/assay/statement_attribution.py` (**new**) — `StatementBlock`, `attribute_statements` | **added** |
| witness tests | `tests/test_statement_attribution_go_witnesses.py` (**new**, 14 tests) | **added, green** |

### STILL TO WIRE (item 1's remaining half)

1. **`LanguageAdapter.requires_statement_attribution: bool`** — a sixth
   attribute in `src/assay/adapters/base.py` (protocol at `base.py:91`,
   attributes `base.py:108-139`). Symmetric with `requires_span_attribution`
   (`base.py:133`).
2. **The NEW protocol hook** (A-239's item 2; A-097/A-101 FREEZE
   `statement_spans` — do not overload it; it is called at exactly one place,
   `evaluate.py:427`, guarded by `evaluate.py:426`). Intended signature, not
   yet written:
   ```python
   def statement_blocks(
       self, paths: Sequence[Path]
   ) -> StatementBlockReport | None
   ```
   returning per-path `tuple[StatementBlock, ...]` plus a helper-identity
   record; `None` = this adapter does no statement attribution, paired with
   `requires_statement_attribution = False` (the `statement_spans`/`None`
   convention A-101 established). **This hook is allowed to shell out** — that
   is what `external_tools` declares. Define the identity record in `base.py`
   (e.g. `HelperInvocation(tool, resolved_path, identity)`), NOT by importing
   `verdict.Helper`, to keep the layering clean; the runner maps it to
   `verdict.Helper(role="statement-positions", ...)`.
   **Record its exact signature as a decision row** — A-239 explicitly defers
   it to this re-carve, and A-390..A-393 are already taken.
3. **The `evaluate` refusal (A-392)** — in `evaluate_coverage`
   (`evaluate.py:280`) and `evaluate_targets` (`evaluate.py:803`): refuse when
   `adapter.requires_statement_attribution and not
   profile.statement_attributed`. This is the guard that makes A-392 real; it
   is documented in `model.py` and does not exist yet.
4. **Every other adapter needs the two new members**: `python.py:806`,
   `javascript.py:493`, `sql.py:671` (each already has `external_tools = ()`
   nearby), plus the `FakeAdapter` copies at `tests/conftest.py:780-812`,
   `tests/test_runner_evaluate_r1.py:109` and `:575`,
   `tests/test_mutation_isolation.py:322`.
5. **Runner wiring**: parse happens at `runner.py:1952` (lane path) and
   `runner.py:969-974` (canary/direct path); the profile reaches
   `evaluate_r1` at `runner.py:2468`. The correction must sit between.

## 4. Facts discovered that change the remaining items

* **`Helper` and `HELPER_ROLES` already exist and reserve our role.**
  `verdict.py:2780` (`Helper`), `verdict.py:292-296`
  (`HELPER_ROLES = ("statement-positions", "mutation-sites",
  "executable-code")`), `Verdict.helpers` at `verdict.py:3046`, validated by
  `_check_helpers` at `verdict.py:3164` (which **requires a correspondingly
  judged claim per role**), verify-side twin `verify.py:1131`/`1662`,
  serialized `verdict.py:3796`. **There is no producer today** — `tests/
  test_cli_run.py:406` asserts `"helpers" not in document`. This wave becomes
  the FIRST real producer, so that test and its neighbours will need revisiting.
  Item 6 is therefore more than a doc check.
* **`GoAdapter` is NOT in the built-in CLI registry** (`cli.py:392-403`
  registers python/sql/javascript only). A Go lane cannot be run through the
  real CLI today. **This is decision-ask territory for item 7 (F008-A5): the
  srdm covergate qualification needs a runnable Go lane.** Registering Go at
  R1 is arguably inside "F008-A5" but is not named in the wave prompt's scope
  list — resolve explicitly, do not silently register.
* **Packaging**: `pyproject.toml:46-47` package-data is `assay =
  ["schemas/*.json"]` ONLY. The helper's `.go`/`.mod` files **will silently
  vanish from the wheel** unless that list is extended — and
  `tests/test_verdict_schema_is_packaged.py` is the precedent for proving both
  sides. **This is not yet done and is a shipping blocker.**
* **`external_tools` preflight** is inline at `runner.py:3472-3489`
  (`shutil.which`), tested by `tests/test_runner_external_tool_preflight.py`.
  Adding `("go",)` to `GoAdapter` (`go.py:501`) means every Go lane refuses in
  this devcontainer — correct, and A-253 says the mechanism is already built,
  so only the declaration is owed. `tests/test_adapters_go_registration.py:42`
  asserts `external_tools == ()` and must change.
* **`go-cover` producer vocabulary** is absent from
  `COVERAGE_PRODUCERS_BY_FORMAT` (`vocabulary.py:189-208`) *by decision*
  (A-354, flagged as "the B045 call most open to challenge"). Item 5 opens it
  with `("go-test", "covdata")`; the refusal it replaces is at
  `config.py:2101-2107` and its test is
  `tests/test_config_coverage_producer.py:234-252`, **parametrized on exactly
  `("go-cover", "go-test")`** — that test inverts when the vocabulary opens.

## 5. Decisions recorded so far

`A-390` (the `blocks` representation), `A-391` (join on extents, not
containment; refuse on disagreement), `A-392` (`statement_attributed` + the
evaluate refusal, as a masked-default guard), `A-393` (`lit.go` laundering NOT
fixed — documented and asserted, filed as B053).
Next free row: **A-394**. Next free backlog id: **B053** (max in
`4-backlog.md` is B052).

## 6. Gate state

See §"Gate" in the REPORT. `pytest tests/` is NOT gate-green (A-335); the
registered gate is
`bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go`
run from `/workspaces/vbpub`, verdict read in a SEPARATE step (exit code + the
`ASSAY_REGISTERED_GATE_COMPLETE=1` marker), ~13 minutes.

---

## SELF-COMPACTION PROMPT (for the controller's fresh successor)

**KEEP:**
- This brief in full — it is the seam map.
- Wave C scope: the 7 items in
  `nyxloom-trove/WAVE-PROMPT-2026-08-30-js-consumer-producer.md` §"Wave C",
  items 3-7 outstanding plus item 1's remaining wiring (§3 above).
- The rules: A-334 (no test double as evidence about Go — probe through
  `tester-unified-go:local`, `--network=none`, tar-pipe, under
  `$CGROUP_PARENT_DEV_BACKGROUND`), A-335 (the registered gate, not `pytest`),
  A-042/A-043 (no Go in the devcontainer, ever), A-097/A-101 (`statement_spans`
  is frozen — build the new hook), DESIGN-GUIDE §5 (cite a source for every
  convention), decisions.md append-only from A-394, backlog from B053,
  `git commit --only -- <paths>`, trailer
  `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`, no `!` marker.
- The three docs that must land WITH the work, not after (AGENTS.md): README
  (what), DESIGN-GUIDE (why), CONSUMERS.md (how to adopt).
- The two open decision asks in §4: registering Go in the built-in registry,
  and what to do about `tests/test_cli_run.py:406` once `helpers[]` has a
  producer.

**DROP:**
- How `cmd/cover`'s `addCounters`/`statementBoundary` work internally — that
  question is closed, transcribed into `stmtpos.go`, and proven; do not
  re-read `/usr/local/go/src/cmd/cover/cover.go`.
- The derivation of each witness's expected line set — frozen in
  `carve-assets/P27-recarve/PROVENANCE.md` and asserted in
  `tests/test_statement_attribution_go_witnesses.py`.
- The `go run` one-directory failure and the image-build steps — resolved,
  recorded in §2.

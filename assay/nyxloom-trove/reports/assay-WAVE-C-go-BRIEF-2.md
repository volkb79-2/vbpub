# Wave C (Go) — continuation brief 2

**Cumulative delta since BRIEF-1 only.** BRIEF-1 is still the seam map — read
it first, then this. Nothing here is re-copied from it.

---

## 1. The two decision asks are ANSWERED (controller, `vbpub@8fd9dd68`)

Both are now decision rows; do not re-derive the reasoning.

* **A-394 — register `GoAdapter` at `{"R1"}`: YES, but as the LAST step of the
  wiring**, after `requires_statement_attribution`, the `statement_blocks`
  hook, the `evaluate` refusal (A-392) and `external_tools = ("go",)` have all
  landed. Never with the oracle standing alone: registering earlier makes a Go
  lane runnable while `go_cover` still reports block extents as statement
  truth, which publishes the exact wrong verdict A-217 exists to prevent. After
  the chain it is safe everywhere — no Go toolchain gives
  `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` through A-253's built preflight.
  R2/R3 stay unregistered.
* **A-395 — `tests/test_cli_run.py:406` is item 6's own scope.** Do NOT weaken
  `"helpers" not in document`; it is true for every adapter that invokes no
  helper and is the control. Add a PARALLEL test asserting `helpers` IS present
  with `role="statement-positions"` and an identity naming `go version …`, for
  a Go lane, proven against `tester-unified-go:local` — never a mock (a mocked
  `go version` would assert only that assay can echo a string it was handed,
  which is what A-334 forbids).

## 2. BRIEF-1 §4's packaging bullet was WRONG — retracted (A-396)

BRIEF-1 called the missing `pyproject.toml` package-data entry a **real
shipping blocker**. It is not, and the controller had already accepted the
claim, so this correction matters.

**Measured:** built with the ENTIRE `[tool.setuptools.package-data]` stanza
deleted, the wheel still carries `assay/helpers/go/stmtpos/{stmtpos.go,go.mod}`
AND `assay/schemas/verdict.schema.json` (47 members, not the five that file's
docstring predicts). `setuptools_scm` installs a git file finder and
setuptools' `include_package_data` defaults to true under pyproject metadata,
so every git-tracked file under the package directory already ships.

The declaration was still added, **rescoped**: explicitness for the
git-metadata-absent build `[tool.setuptools_scm]`'s own `fallback_version`
anticipates. `tests/test_go_helper_is_packaged.py` asserts the OUTCOME (in the
wheel, resolves from inside the venv) rather than the mechanism, so it survives
the correction.

Side finding, filed as **B054**: `test_verdict_schema_is_packaged.py`'s
docstring states this same mechanism as measured fact, and a re-run refutes it
— so that test's named negative is currently unreachable. Filed, not patched;
three options are laid out in the entry.

## 3. What changed in the tree since BRIEF-1

| file | change |
|---|---|
| `pyproject.toml` | package-data extended with `helpers/go/stmtpos/*.go` + `go.mod`, comment states the measured truth |
| `tests/test_go_helper_is_packaged.py` | **new**, 6 tests: source-tree presence, no `require` directives, stdlib-only imports, wheel namelist, venv resolution, `go run .` layout |
| `nyxloom-trove/decisions.md` | A-394, A-395, A-396 |
| `nyxloom-trove/4-backlog.md` | B054 |
| `reports/assay-WAVE-C-go-BRIEF-1.md` | packaging bullet retracted in place |
| `reports/assay-WAVE-C-go-REPORT.md` | gate transcript (§6), the two wrapper-exit-code incidents (§7), the A-396 retraction (§8) |

## 4. Gate

**PASS on `4408622b`** — all 11 phases, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`GATE_EXIT=0`. Full transcript in REPORT §6. A re-run on the new tip (this
brief's commit) is recorded there too; **do not start the wiring on a tip whose
gate you have not read.**

Two live wrapper-vs-job exit-code incidents are recorded in REPORT §7 — the
full pytest run reported "exit code 0" while pytest said `4 failed`, and gate
run 1 reported "exit code 0" while `GATE_EXIT=1` on a gate that executed
nothing. **Read `GATE_EXIT` and the completion marker from the log, in a
separate step, every time.**

## 5. Next chunk, in order (unchanged from BRIEF-1 §3, with one addition)

1. `LanguageAdapter.requires_statement_attribution` + the `statement_blocks`
   hook in `adapters/base.py` (**record its exact signature as a decision row
   — next free id is A-397**).
2. The `evaluate` refusal (A-392) in `evaluate_coverage` (`evaluate.py:280`)
   and `evaluate_targets` (`evaluate.py:803`).
3. The two new members on `python.py:805`, `javascript.py:492`, `sql.py:670`,
   plus the FakeAdapter copies at `tests/conftest.py:799`,
   `tests/test_runner_evaluate_r1.py:108`, `tests/test_mutation_isolation.py:321`.
   Also `tests/test_adapters_go_registration.py:41-42` asserts
   `external_tools == ()` and the protocol surface — it must change.
4. **Expose the ONE key-resolution join instead of duplicating it** —
   `evaluate.py:625-676` already owns `normalize_coverage_key` →
   `_to_repo_relative_key` → repo-relative path, and its docstring says it
   exists so the mapping cannot drift between the two modes. The oracle needs
   real paths for those keys; A-385/A-367 rule that there is ONE join, so
   refactor to expose it rather than re-deriving it in the runner. Controller
   endorsed this explicitly. Note `attribute_statements` is keyed by the
   artifact's OWN spelling, so what you need out is `raw_key → repo_path`.
5. Runner wiring: the seam is `runner.py:969-1030`, between
   `coverage.check_empty_coverage(profile)` and the `evaluate_targets` /
   `evaluate_coverage` fork, after `repo_top = git.repo_top(...)`.
6. Then `external_tools = ("go",)` (item 4), then registration (A-394), then
   item 7 (srdm covergate qualification) — the first thing needing a runnable
   Go lane.

Still untouched after that: item 3 (fixture regeneration, F008-A4) and item 5
(`go-cover` producer vocabulary, B047 item 3 — opens A-354's deliberately
closed vocabulary; the refusal is at `config.py:2101-2107` and its test at
`tests/test_config_coverage_producer.py:234-252` is parametrized on exactly
`("go-cover", "go-test")` and inverts).

## 6. Ledger

Decisions used: A-390..A-393 (brief 1), **A-394, A-395, A-396** (this brief).
Next free: **A-397**. Backlog: B053 (brief 1), **B054** (this brief). Next
free: **B055**.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map) and this brief in full; the wave
prompt's "Wave C" section; the rules block from BRIEF-1's own compaction
prompt; §5 above as the literal next task list; the gate command and the
separate-verdict-read discipline.

**DROP:** the packaging investigation (closed — A-396 + B054 carry the whole
result); the derivation of the two decision asks (closed — A-394/A-395); the
`go.mod` substring-match test bug (fixed, and its lesson is in the test's own
comment).

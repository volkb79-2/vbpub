# run-gate RG-26 sidecar review — round 2

**Date:** 2026-09-14
**Reviewer:** fresh adversarial review, Luna xhigh
**Reviewed:** `e766b75a6431b69577df1c080b6331a68596e19` against its parent
`63fcd32f`
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-gate-full-base-propagation`

## Verdict: ACCEPT

The round-1 proof gap is closed. The new committed test reads the real shipped
`run-gate-project/run-gate.toml`, pins the exact `gate-full` conjunction shape
and ordering, and demonstrably goes red when the production `{base}` token is
removed or moved. Runtime base safety and the existing RG-26/RG-52 behavior
remain intact. No blocker remains for this final review round.

## Review findings

### B1 from round 1 — closed

The tip commit changes only `run-gate-project/tests/test_run_gate.py`:

```text
M run-gate-project/tests/test_run_gate.py
```

At `tests/test_run_gate.py:32`, `RUN_GATE_DIR` is derived from the test file's
real parent directory. The new test at `tests/test_run_gate.py:4911-4947`
opens `RUN_GATE_DIR / "run-gate.toml"` and parses that file with `tomllib`.
It does not call `make_project`, use a fixture TOML, or construct a synthetic
conjunction. It asserts the shipped schema version, effective lane set,
`gate-full` kind/environment/clean-tree policy, the complete ordered argv, and
the unchanged `selftest`, `assay-r1`, `assay-r3`, and `assay-r2` declarations.

The exact production contract asserted is:

```text
./run-gate.py selftest && ./run-gate.py --base {base} assay-r1 && ./run-gate.py assay-r3
```

Controlled red-first checks used temporary copies under `/tmp` only; the
review worktree was never edited:

- Original shipped config: the new test passed, **1 passed, 989 deselected**.
- Temporary config with `--base {base}` removed: **exit 1**, assertion failed
  at the `gate-full.argv` comparison, **1 failed, 979 deselected**.
- Temporary config with the token moved after `assay-r1`: **exit 1**, the same
  exact-argv assertion failed, **1 failed, 979 deselected**.

This closes the precise false-green identified in round 1: reverting or moving
the production token cannot leave this committed proof green.

### Runtime base safety and argv ordering

The production fix remains the earlier `84fab73e` config-only change;
`e766b75a` does not alter runtime code. The relevant implementation still:

- recognizes propagation only when a command lane's argv contains `{base}`;
- resolves an explicit base through `check_base_charset()` before substitution;
- refuses unsafe or option-like bases rather than passing shell text onward;
- substitutes the resolved value through `build_command_inner()` in the exact
  argv element used by `bash -c`.

The focused RG-26/RG-52 regression set, run serially with `nice -n 19 ionice
-c 3`, passed **17 tests, 973 deselected, exit 0**. It includes the shipped
declaration test, generic conjunction propagation, no-base refusal, refusal
when a command lane has no propagation token, unresolved-token defense,
explicit injection refusal before any lane runs, non-delegating assay refusal,
and leading-dash refusal.

The real shipped runner also passed this non-mutating dry-run probe, serially
niced/ioniced:

```text
nice -n 19 ionice -c 3 ./run-gate.py gate-full --base main --dry-run
exit 0
run-gate: comparison base main (from --base) → {base} in the lane argv
run-gate: DRY RUN — would run in /workspaces/vbpub/.worktrees/rg55-gate-full-base-propagation/run-gate-project: bash -c './run-gate.py selftest && ./run-gate.py --base main assay-r1 && ./run-gate.py assay-r3'
```

The shipped loader/list path also passed:

```text
nice -n 19 ionice -c 3 ./run-gate.py --list
exit 0
assay-r1  assay    bare-host
assay-r2  assay    bare-host
assay-r3  command  bare-host
gate-full command  bare-host
selftest  command  bare-host
```

### Acceptance evidence and evidence discipline

The authoritative exact-tip gate record is present in
`run-gate-project/.run-gate/history.json`:

- `gate-full`, commit
  `e766b75a6431b69577df1c080b6331a68596e19`, dirty `false`, exit `0`, outcome
  `pass`: lines 140-168;
- `selftest`, commit
  `e766b75a6431b69577df1c080b6331a68596e19`, dirty `false`, exit `0`, outcome `pass`:
  lines 198-225;
- `assay-r3`, commit
  `e766b75a6431b69577df1c080b6331a68596e19`, dirty `false`, exit `0`, outcome `pass`:
  lines 82-110.

Per the controller instruction, the separate `assay-r1` record at lines 24-39
is not treated as acceptance evidence: it is the independently reported
duplicate-output-write controller interference (`exit 2`), not a conclusion
about the gate-full fix.

### Documentation and regression scope

The earlier production fix's documentation remains accurate:

- `run-gate-project/run-gate.toml:145-148` carries `{base}` only into the
  `assay-r1` sub-invocation;
- `run-gate-project/README.md:87-92` documents `gate-full` as
  `selftest + assay-r1 + assay-r3`, explains that `{base}` forwards an
  explicit base to `assay-r1`, and preserves the statement that `selftest`
  is the release gate;
- `run-gate-project/CHANGES.md:12-14` records the linked-worktree fix;
- `run-gate-project/CONSUMERS.md:345-505` documents RG-26 base resolution,
  refusal behavior, and `{base}` conjunction propagation for adopters;
- `run-gate-project/SPEC.md:R-35/R-35b` states the resolution, safety, and
  conjunction contracts.

The review tip adds no user-facing behavior, production implementation, or
configuration. `py_compile` for `run-gate.py` and `tests/test_run_gate.py`
passed (exit 0), and `git diff --check` on the reviewed commit passed (exit 0).
After review, the only worktree change is this requested, uncommitted report;
no code or operator-owned file was modified.

## Final disposition

**ACCEPT.** The required committed production-configuration oracle is present,
its removal and relocation are red-tested, the exact-tip gate evidence is
accepted with the instructed assay-r1 interference excluded, and no minimal
repair is required.

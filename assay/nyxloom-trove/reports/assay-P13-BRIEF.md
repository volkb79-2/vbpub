# P13 successor brief — for P14 (self-hosted conformance, the FINAL package)

P13 proved the SHIPPED WHEEL — not the source checkout — is a genuine,
zero-runtime-dependency executable: a real `assay run` through the
INSTALLED console script emits a genuine R0 verdict (PASS and FAIL, matched
field-for-field against `tests/fixtures/verdicts/r0_pass.json` modulo
`assay_version`/`commit`/`started`/`ended`), a real committed Python
fixture passes through the full pipeline that way, the Go adapter ships and
is callable from inside the scratch venv (adapter-level only — no Go
toolchain exists here), and three real mutated-wheel builds (package data
removed, console entry point removed, a runtime dependency declared) each
break a different, correctly-targeted property while everything else keeps
working. `src/assay` was not touched at all — this was a pure proof
package. Here is what you need for P14.

## The mechanism you'll reuse, again without reimplementing it

`tests/conftest.py`'s session-scoped `standalone` fixture
(`_build_backend_home`/`_clean_env`/`Standalone`, ~lines 624-741) is still
the one true two-environment offline build/install recipe (A-070/A-123).
`Standalone.run(*argv)` launches `venv/bin/<argv[0]>` with a clean
environment. If P14's self-hosting proof needs its own mutated or
independent wheel build (unlikely, but if so), `_build_and_install_mutant`
in `tests/test_standalone.py` is a worked, reusable PATTERN (not itself
importable across test modules without adding it somewhere shared) for
building a wheel from a MUTATED `pyproject.toml` copy without disturbing
the shared fixture.

## `assay_version` reads `"0.0.0"` in the real gate image — plan for it

`setuptools_scm` is absent from every interpreter in `tester-unified:local`
(A-069/A-124, now independently reconfirmed a third time by P13's own three
mutated-wheel builds, all producing `assay-0.0.0-py3-none-any.whl`). If
P14's self-hosting proof runs `assay` against ITS OWN repo (the likely
shape of "self-hosted conformance" — assay judging its own gate), any
version string it emits or embeds will be `"0.0.0"`, not a real semver.
Don't compare it against a hand-written fixture's own `assay_version`
without excluding/normalizing that field, the same way P13 had to.

## `assay run`'s exact CLI shape, confirmed empirically

`assay run <lane> --file <path> --verdict-json -` writes ONLY the verdict
JSON to stdout (no human summary line — `cli.py`'s own `_cmd_run` skips
`_print_run_summary` when `--verdict-json == "-"`). `--verdict-json`
omitted writes nothing and prints the human summary instead. The process
exit code IS `Outcome.exit_code` (PASS=0, FAIL=1, ERROR=2, NO_MEASUREMENT=3,
BUDGET_EXCEEDED=4, INCONCLUSIVE=5) — assert on it directly rather than only
parsing stdout.

## A real, R0-only lane recipe that Just Works, if you need one

```toml
schema_version = 1

[lanes.package]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = { MOCK_MODE = "true" }
env_passthrough = []
budget = "5m"
allow_argv_append = false
```

This is the EXACT lane `tests/fixtures/verdicts/r0_pass.json` documents.
For a real Python subprocess (R0 through a genuine `pytest` run rather than
`/bin/sh`), use `argv = [sys.executable, "-m", "pytest", "tests", "-q"]` —
`sys.executable` is the GATE's own interpreter (has the `test` extra
installed), never the scratch venv, which deliberately ships nothing but
`assay` itself. This is the same pattern
`test_canary_python_pipeline.py`/`test_mutation_python_pipeline.py` already
use for their own real-pytest-subprocess proofs.

## What P13 deliberately left alone

`pyproject.toml`, `README.md`, `tools/standalone-proof.sh` are all
unchanged from before P13 — nothing in packaging metadata needed adding
(everything O1-O3 needed was already declared). If P14 needs something new
declared in `pyproject.toml` (unlikely for a self-hosting proof, but
possible if it needs its own optional-dependency group or entry point),
that is new ground P13 did not touch.

## A-125 still applies

`tests/conftest.py`'s `collect_ignore_glob` still only covers
`fixtures/canary/python/**` and `fixtures/mutation_exec/python/**`. If P14
needs its OWN committed pytest-shaped fixture project and `conftest.py` is
in its `scope.touch`, extending that glob is fine — it wasn't in P13's
`scope.touch`, which is the only reason P13 had to route around it rather
than use it.

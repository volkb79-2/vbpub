# assay — final-state brief (post-P14, for whoever picks this up next)

This is the LAST package in the P00–P14 series. There is no P15 successor —
this document is for whoever next touches `assay`, whether that's a bug fix,
a new rigor level, or a totally different agent six months from now who has
never read any of the other 14 handoffs. It describes what actually exists,
not what was planned.

## What assay is, in one paragraph

`assay` reads a project's declared lane (`assay.toml`), runs exactly the
declared `argv` once, judges the result against declared rigor (R0 today;
R1–R3 exist as a model/schema/CLI-shape but no producer for R1+ ships in
this build beyond what P05–P12 already wired for OTHER consumers of the
library), and emits one machine-readable verdict JSON — six closed outcomes,
a closed reason-code vocabulary, a JSON Schema shipped as package data. Zero
runtime dependencies (stdlib only). Full reasoning: `docs/DESIGN-GUIDE.md`.

## The self-hosting claim, concretely

**assay can gate itself without becoming the only witness to its own
correctness.** Two independent things now prove that, neither of which is
`assay verify`:

1. **`pytest`, run through the real, built, installed WHEEL** — not a
   PYTHONPATH-shadowed source tree. `nyxloom-trove/nyxloom.toml`'s gate
   script builds a real `assay` wheel from the checkout, installs it into a
   scratch venv, and runs `assay run tester-unified` (assay's own CLI,
   through that wheel) against assay's own test suite. If a bug made
   `assay` return `PASS` unconditionally, this pytest run — which knows
   nothing about assay's own verdict logic — would fail on its own merits.
2. **`tests/test_self_hosting.py`**, run as a SEPARATE, second step via the
   gate's ambient interpreter (never the scratch venv), reading the
   artifact step 1 just produced and checking it against facts computed
   independently (`git rev-parse HEAD` directly, the lane's own argv
   transcribed by hand) — never against anything `assay verify` said.

`assay verify` (`assay verify <path-or-->`, `src/assay/verify.py`) exists
and is exercised by both the real gate (as a secondary check inside
`test_self_hosting.py`) and a dedicated test suite
(`tests/test_verdict_conformance.py`), but it is explicitly documented,
in its own module docstring, as NOT the sole oracle for anything — DESIGN-
GUIDE §9 names this failure mode directly ("`assay verify`... is a useful
second layer but is **not independent**, and must be documented as such
rather than presented as the proof"), and P14's own producer-mutation test
demonstrates exactly why: a mutated build that lies about its own outcome
is schema-valid and internally self-consistent, which is all `assay
verify` can ever check. Only a test that knows, independently, what
*should* have happened catches it.

## How the gate actually runs now (read `nyxloom-trove/nyxloom.toml` for the literal script)

Inside one `docker run tester-unified:local`:

1. `cd {worktree}/assay`.
2. Build a real `assay` wheel via a **fresh, blank scratch venv's own
   `pip`** (never `/opt/tester-venv`'s pip directly — it cannot import
   `setuptools.build_meta` even with `PYTHONPATH` set, for reasons not
   fully understood but confirmed reproducible; conftest.py's own
   `standalone` fixture already avoided this by always using a fresh venv,
   which is why P13 never hit it).
3. Write a `.pth` file into that scratch venv's own site-packages naming
   `/opt/tester-venv`'s site-packages directory — this is how the scratch
   venv gets `pytest`/`coverage`/`pytest-cov` visible. **`--system-site-
   packages` does NOT work for this**: a venv created from a venv resolves
   "system site packages" to the *original* base install
   (`/usr/local` here), not the immediate parent (`/opt/tester-venv`), so
   the pytest/coverage/pytest-cov that live only in `/opt/tester-venv`'s
   own site-packages stay invisible. (`/opt/tester-venv` itself is also not
   writable by the container's run-uid, so installing the wheel directly
   into it is not an option either.)
4. `pip install --no-index` the built wheel into that same scratch venv.
5. `export PATH=$scratch/venv/bin:$PATH`, then `assay run tester-unified
   --verdict-json <path>` — `assay.toml`'s own declared `argv[0]="python"`
   resolves, via `PATH`, to the scratch venv's interpreter, which now has
   BOTH the wheel-installed `assay` AND (via the `.pth` file) `pytest`.
   `assay.toml`'s lane carries `--override-ini=pythonpath=`: without it,
   `pyproject.toml`'s own `[tool.pytest.ini_options] pythonpath = ["src"]`
   (a *developer convenience* — bare `pytest` locally still works and still
   imports from source) inserts `"src"` at `sys.path[0]` regardless of
   `PATH`/`PYTHONPATH`, silently re-shadowing the wheel. It also carries
   `--ignore=tests/test_self_hosting.py`, so this collection never includes
   (and never recursively re-triggers) the second step.
6. A SEPARATE `pytest tests/test_self_hosting.py -q` invocation, via
   `/opt/tester-venv/bin/python` (the ambient gate interpreter, never the
   scratch venv), with `PYTHONPATH` pointed at the scratch venv's site-
   packages (so `tests/conftest.py`'s own top-level `import assay` — which
   EVERY test file needs, since pytest always loads the nearest
   `conftest.py` first — resolves to the wheel, not nothing) and
   `ASSAY_SELF_HOSTING_VERDICT` pointed at step 5's own artifact.

If you need to change this script: re-verify all three "does not work as
you'd expect" findings above by actually running things against
`tester-unified:local`, not by reasoning about it — all three were found
that way, not by inspection, and each one looks perfectly reasonable until
you actually run it.

## `assay verify`'s contract, if you need to extend it

`src/assay/verify.py` never imports `jsonschema` (a runtime dependency
would violate A-005). "Validates against the packaged schema" is achieved by
attempting to reconstruct the real `assay.verdict` dataclass graph
(`Verdict`/`Claim`/`Coverage`/`CanaryResult`/`Mutation`/`MutantOutcome`/
`Evidence`/`EvidenceDeclaration`) field-by-field from the parsed JSON —
those dataclasses are maintained, by `verdict.py`'s own module docstring, to
refuse anything the shipped schema would refuse. Four things JSON Schema
2020-12 cannot express (two-location cross-checks within one instance) are
re-checked explicitly, on the RAW parsed dict, ahead of reconstruction:
outcome-agrees-with-rollup, `argv_effective` arithmetic, claims-cover-
declared-rigor, evidence-covers-declared-evidence. If you add a new claim
payload shape or a new top-level field to `Verdict`, you must add the
matching `_reconstruct_*`/`_reject_unknown_keys` wiring in `verify.py` too —
nothing here auto-derives from the schema or the dataclasses; it is
maintained by hand, deliberately, the same way the schema itself is.

## What's still R0-only, permanently, and why that's not a gap

`assay.toml`'s own lane and `nyxloom-trove/nyxloom.toml`'s own `asserts`
both declare R0/`["tests-pass"]` only — not because a future package will
upgrade this, but because assay's subject matter is judging OTHER projects'
diffs, and applying R1+ rigor to its own diff would need a coverage/canary/
mutation judge configured against assay's own source, which nothing in this
project's scope asks for. Two earlier comments in these files said "P11
upgrades this when the capability is real" — that was simply wrong (P11 was
valid-mutant construction and never touched either file); P14 corrected
both comments to state the real, permanent reason instead of pointing at a
package that already came and went without doing it. If a future maintainer
DOES want assay to gate itself at R1+, that is new, deliberate scope — not
an oversight this brief is flagging.

## Known, accepted, permanent debt (not P14's to fix)

- Three `(outcome, reason_code)` pairs in the closed 19-pair vocabulary are
  structurally unreachable as complete `Verdict` artifacts and are not
  fixtured: `ERROR`/`GIT_FAILED`, and claim-level `ERROR`/`FORMAT_MISMATCH`
  and `ERROR`/`UNREADABLE_ARTIFACT` (evidence-level `UNREADABLE_ARTIFACT`
  IS reachable and IS fixtured). All three propagate uncaught out of
  `evaluate_r1`/`cli.py`'s own top-level handler before any `Verdict` is
  ever constructed — this is documented, deliberate, pre-existing behavior
  (A-128), not a gap.
- P00/P01's own handoff-body linter errors (missing worktree/branch/BLOCKED
  mentions in some early handoff files, a broken cross-repo glob) remain
  unfixed — out of scope for every package that has looked at them,
  recorded as accepted debt each time.
- One test (`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`)
  fails when run from this devcontainer's own ambient Python (which has a
  working `setuptools_scm`, unlike the real `tester-unified:local` image) —
  this is environment-specific and expected; it passes in the real gate.

## Where to actually look

- `docs/DESIGN-GUIDE.md` — the reasoning behind every rule (§6 verdict
  contract, §9 self-hosting, §12 for the P14-era framing).
- `nyxloom-trove/decisions.md` — every ruling, in order, with the evidence
  behind it. A-128 through A-133 are this package's own readiness pass;
  reading them before touching `assay.toml`/`nyxloom.toml`/`verify.py`
  again will save you from re-deriving things already settled.
- `nyxloom-trove/reports/assay-P14-self-hosted-conformance-LOG.md` — this
  package's own full evidence trail, including the exact real gate output
  and every self-review mutation with its real, observed failure count.

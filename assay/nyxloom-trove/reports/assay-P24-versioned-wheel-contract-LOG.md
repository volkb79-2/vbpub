# P24 — versioned wheel contract — implementation LOG

Implementer: fresh Sonnet xhigh.
Worktree: `/workspaces/vbpub/.worktrees/assay-P24-versioned-wheel-contract`
Branch: `feat/assay-P24-versioned-wheel-contract`
Sol freeze commit (worktree HEAD at start): `c7ff15a1c7071c5c899df58f1e1307c387c5b338`
Production/source anchor (freeze commit's sole parent): `7c52ecc2f9f500991d2ba74689458ae1e6644a18`

## Pre-implementation verification

1. Verified all 13 files named in `nyxloom-trove/carve-assets/P24/fixture-manifest.json`
   against their real sha256 bytes — all matched exactly. Also verified the
   manifest's own self sha256
   (`ff5fc8dbf8adf08e92438850ffce3d74f57a24cfea4b955d947bb5340ea4c31a`) matched
   `sha256sum fixture-manifest.json`.
2. Applied `nyxloom-trove/carve-assets/P24/skeleton.patch` once with
   `git apply` (clean, no fuzz):
   - created `assay/gate/distribution/release_wheel.py` (the standalone
     helper skeleton with `_sha256`/`_wheel_name_and_version` as the two
     `NotImplementedError` TODOs).
   - updated `assay/pyproject.toml`'s `[build-system].requires` to the exact
     five-pin closure in order: `setuptools==84.0.0`, `wheel==0.47.0`,
     `setuptools-scm==10.0.5`, `packaging==26.3`, `vcs-versioning==2.2.4`.
3. Mechanically copied the locked production offline closure, byte-for-byte
   (verified with `diff -q` against every carve-asset source):
   - `nyxloom-trove/carve-assets/P24/build-requirements.txt` ->
     `gate/distribution/build-requirements.txt`
   - `nyxloom-trove/carve-assets/P24/wheelhouse-manifest.json` ->
     `gate/distribution/build-wheelhouse-manifest.json`
   - `nyxloom-trove/carve-assets/P24/wheelhouse/*.whl` (5 files) ->
     `gate/distribution/build-wheelhouse/`
   No `pip download`, no regenerated hashes, no extra/omitted wheel.

## Required initial proof — controlled red

Ran, from `assay/` in the P24 worktree, exactly the locked command:

```text
python -m pytest nyxloom-trove/carve-assets/P24/test_acceptance.py -q
```

Result:

```text
7 failed, 17 passed in 18.17s
```

Matches the JIT report's recorded controlled red exactly. The seven failures
all cross the two skeleton TODOs (`_sha256` / `_wheel_name_and_version`),
not collection/setup:

- `test_verifier_emits_exact_hash_requirement_only_after_success`
- `test_manifest_command_recreates_only_the_locked_document`
- `test_basename_and_hash_are_independent_refusals`
- `test_metadata_is_independently_bound_after_hash[name]`
- `test_metadata_is_independently_bound_after_hash[version]`
- `test_metadata_is_independently_bound_after_hash[duplicate]`
- `test_pip_rechecks_the_verified_hash_at_install_time`

Each failure's traceback shows `NotImplementedError: P24: implement bounded
streaming sha256` or the wheel-metadata equivalent, confirming they cross the
two deliberate TODOs and are not spurious collection/setup breaks.

## Implementation

### 1. Two skeleton TODOs (`gate/distribution/release_wheel.py`)

- `_sha256`: seeks the already-open descriptor to 0, streams it in fixed
  1 MiB chunks (bounded by `_open_regular`'s own <=64 MiB `fstat` check),
  computes sha256, then rewinds to 0 so a subsequent `zipfile.ZipFile` read
  (either before or after this call — `verify_release` calls it first,
  `create_release_manifest` calls it second) always sees the whole file.
- `_wheel_name_and_version`: opens the descriptor with `zipfile.ZipFile`,
  requires `infolist()` to contain exactly one `*.dist-info/METADATA` member
  whose name is exactly `assay-<expected_version>.dist-info/METADATA`
  (catches both a duplicate member and a wrong dist-info path), requires its
  declared/uncompressed size <= `MAX_METADATA_BYTES`, reads it once, and
  parses with `email.parser.BytesParser`, requiring exactly one `Name` and
  one `Version` header. Never extracts the ZIP. Name/version agreement with
  the manifest is checked by the two existing callers, unchanged.

Locked acceptance suite after completing only these two TODOs:

```text
python -m pytest nyxloom-trove/carve-assets/P24/test_acceptance.py -q
........................  [100%]
24 passed in 24.61s
```

### 2. Registered gate transformation (`tools/tester-unified-gate.sh`)

Rewrote the inner-mode build/install path per the normative packet, keeping
outer-mode cgroup validation, host-bind derivation, and the final receipt
marker unchanged except for one addition:

- Outer mode: added `--network=none` to the `docker run` invocation.
- Inner mode, in order: `make_exact_oid_clone` (records `git rev-parse HEAD`
  of the mounted worktree, makes a `git clone --no-local --no-checkout` of
  the worktree itself into scratch, cone-sparse-checks-out only `assay/`,
  checks out that exact OID detached, and requires the clone's own HEAD to
  equal the recorded OID) → `build_offline_closure_venvs` (creates separate
  `build-venv`/`run-venv` from the image's base Python; installs the five
  locked requirements into `build-venv` with
  `--no-index --find-links <wheelhouse> --require-hashes`; queries
  `importlib.metadata` for all five names/versions before building) →
  `build_one_wheel` (`pip wheel --no-index --no-build-isolation --no-deps`
  from `clone/assay`; requires exactly one `assay-*.whl`) →
  `require_real_wheel_version` (cross-checks the wheel's filename version
  against its own METADATA version; refuses `0.0.0`/`0+unknown`) →
  `install_wheel_into_run_venv` (`--no-index --no-deps` into `run-venv`) →
  emits `ASSAY_GATE_PHASE=wheel-installed` → `write_tester_closure_pth`
  (the existing `.pth` trick, now targeting `run-venv` only) →
  `require_installed_purity` (installed version == wheel METADATA version ==
  `assay.__version__`; no unconditional `Requires-Dist`; `assay.__file__`
  under `run-venv` and outside `/workspaces/vbpub`) → `run_self_hosted_lane`
  (runs `assay run tester-unified` against the ORIGINAL reviewed worktree,
  never the clone, with `run-venv/bin` first on PATH; on failure prints a
  diagnostic rerun and returns 1 unconditionally, never emitting the success
  marker; on success requires the verdict's own `assay_version` to equal the
  installed version before emitting `ASSAY_GATE_PHASE=self-hosted-lane-passed`)
  → `run_independent_witness` (unchanged shape: `/opt/tester-venv`'s own
  pytest against `tests/test_self_hosting.py`, `PYTHONPATH` pointed at
  `run-venv`'s site-packages; emits
  `ASSAY_GATE_PHASE=independent-self-hosting-passed`).
- All four required phase/completion markers are byte-for-byte unchanged:
  `ASSAY_GATE_PHASE=wheel-installed`, `ASSAY_GATE_PHASE=self-hosted-lane-passed`,
  `ASSAY_GATE_PHASE=independent-self-hosting-passed`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`.
- Removed: the ambient-setuptools `PYTHONPATH="$setuptools_home"` build route
  and the single shared scratch venv it built into. `grep -rn
  "setuptools_home"` across the tracked tree now matches nothing outside
  this LOG/the handoff/report prose.

Not run: the registered gate itself (`bash tools/tester-unified-gate.sh
<worktree>`) — per instructions, that evidence belongs to the controller.
Instead verified the new/changed shell functions directly: `bash -n` and
`shellcheck` both clean; every function sourced from the real (unmodified)
script file and exercised with real `git`/`pip`/venvs against synthetic
fixtures (see `tests/test_distribution_gate.py`) — including a real offline
build through the exact five-wheel closure from a synthetic untagged repo,
which reproduced the documented `0.0.0` placeholder when built ambient-only
(no closure) and a real non-placeholder `setuptools-scm` dev identity
(`…dev0+g<sha>…`) when built through the closure, confirming
`require_real_wheel_version` accepts the real case and refuses the old gap.

### 3. `nyxloom-trove/nyxloom.toml`

Rewrote the `[gates.tester-unified]` comment block's P14/A-130 self-hosting
description to match the new mechanism (exact-OID clone, two-venv hash-bound
closure, `--network=none`, placeholder refusal, purity checks). No key in the
`[gates.tester-unified]` table itself changed — confirmed by
`tomllib.load` before/after and `git diff` showing only `#`-prefixed lines.

### 4. `docs/DESIGN-GUIDE.md`

- Corrected a stale cross-reference in §13: "P24 qualifies the versioned
  installed wheel against a disposable current Topos tree" is now P25's job
  (the P20–P32 recarve moved Topos qualification off the number this handoff
  now owns; STATE.md's own execution order confirms `P24 wheel → P25 real
  Python qualification`).
- Added §14 "Versioned wheel distribution (P24)": the five-wheel closure and
  why the old three-package declaration wasn't a real closure, the four
  source-shape/identity table, the private-clone rationale (with the real
  582,656-vs-232,651-byte contamination probe as evidence), the
  manifest/verify/pip-hash recipe and why verification alone leaves a
  check/use race, and the two-venv isolation boundary.

### 5. Reachability/coverage sweep (work item 7)

`grep -rn` for `setuptools_home`, the old `PYTHONPATH="$setuptools_home"`
route, and `82.0.1` across the tracked tree (excluding this LOG and the
carver's own handoff/report/carve-assets prose, which correctly narrate the
history) returned no matches. Remaining `0.0.0`/`0+unknown` occurrences are:
this LOG/report prose, the gate script's own new refusal check, and
`tests/test_standalone.py`'s three pre-existing, decision-pinned (A-069/
A-124) assertions about `conftest.py`'s separate `standalone` fixture (a
dependency-purity/schema-packaging oracle unrelated to the registered gate,
explicitly documented in `nyxloom-trove/STATE.md` as "do not fix it") —
left untouched, out of P24's scope per the handoff's own non-goals.

### 6. New ordinary tests (ADDED, not part of the carver's locked packet)

`tests/**` is in scope; the locked acceptance suite lives under
`nyxloom-trove/carve-assets/P24/` and is never collected by the registered
gate (`pytest tests -q ...`), so per work item 2 the release-wheel helper's
attacks are promoted into the ordinary tree, and new gate-script-level tests
cover what the locked suite structurally cannot (it never touches
`tools/tester-unified-gate.sh`):

- `tests/test_distribution_release_wheel.py` (21 tests): positive verify,
  manifest creation + check/use refusal on a second write, six hostile
  manifest shapes, duplicate manifest member, wrong basename/wrong hash,
  three METADATA mismatches (name/version/duplicate) after a correct hash,
  a wrong dist-info path, a corrupt ZIP, oversized/symlinked manifest, a
  symlinked wheel, a FIFO wheel, an oversized wheel, and the real pip
  `--require-hashes` recheck of bytes mutated after a successful verify.
- `tests/test_distribution_gate.py` (11 tests): `bash -n` + shellcheck
  static checks; required markers/flags present and the old ambient-backend
  route absent (text-content regression guard); `pyproject.toml`'s five-pin
  closure; production/locked wheelhouse byte-identity; the exact-OID clone
  excluding ignored residue (synthetic repo); a HEAD-less worktree refusing
  cleanly; a real closure build producing a non-placeholder dev identity; an
  ambient-only build (no closure) still producing the documented placeholder
  and being refused; a failing self-hosted lane never laundered into success
  by its own diagnostic rerun (even when the diagnostic rerun itself also
  fails); and the emitted-`assay_version`-must-match-installed-version check,
  both matching and mismatched.

None of the new tests import `assay` from source (subprocess/installed-wheel
only, matching the locked suite's own discipline). None depend on wall-clock
duration; every `subprocess.run` timeout is a generous (10–120s) hang
failsafe, never an oracle.

## Verification run (foreground, this worktree)

```text
python -m pytest nyxloom-trove/carve-assets/P24/test_acceptance.py -q
........................  [100%]
24 passed in 24.77s (rerun after implementation, matches JIT report's disposable-completion figure exactly)

python -m pytest tests/test_distribution_gate.py tests/test_distribution_release_wheel.py -q
................................  [100%]
32 passed in 22.09s

python -m pytest tests -q --ignore=tests/test_self_hosting.py
[...2280 passed in 139.66s (0:02:19)...]

bash -n tools/tester-unified-gate.sh && shellcheck tools/tester-unified-gate.sh
(clean, no output)
```

All 13 P24 locked assets re-verified byte-identical against
`fixture-manifest.json` after implementation (no drift). `git status --short`
on every forbidden path (`src/assay`, `assay.toml`,
`nyxloom-trove/carve-assets/P20`-`P24`) is empty.

`nyxloom lint assay/nyxloom-trove/handoffs/assay-P24-versioned-wheel-contract.md`
→ `clean`.

Not run: the registered `tester-unified` gate itself, per instructions — that
evidence belongs to the controller.

## Scope

Touched only: `pyproject.toml`, `gate/distribution/**` (new),
`tools/tester-unified-gate.sh`, `nyxloom-trove/nyxloom.toml` (comments only),
`docs/DESIGN-GUIDE.md`, `tests/test_distribution_gate.py` (new),
`tests/test_distribution_release_wheel.py` (new), this LOG. Did not touch
`README.md` (does not exist at the assay project root; scope.touch names it
as permission, not obligation — A-127's own established precedent for an
absent optional file) or any forbidden path.

No BLOCKED trigger fired.

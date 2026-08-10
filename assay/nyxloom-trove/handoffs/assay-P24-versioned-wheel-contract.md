---
schema_version: 1
id: assay-P24-versioned-wheel-contract
project: assay
title: "Every consumable Assay wheel has a reproducible, hash-bound identity"
tier: implement-2
input_revision: "7c52ecc2f9f500991d2ba74689458ae1e6644a18"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P23-exact-reexecution-integration]
session: fresh
scope:
  touch: ["pyproject.toml", "gate/distribution/**", "tools/tester-unified-gate.sh", "nyxloom-trove/nyxloom.toml", "tests/**", "README.md", "docs/DESIGN-GUIDE.md", "nyxloom-trove/reports/assay-P24-versioned-wheel-contract-LOG.md"]
  forbid: ["src/assay", "assay.toml", "nyxloom-trove/carve-assets/P20", "nyxloom-trove/carve-assets/P21", "nyxloom-trove/carve-assets/P22", "nyxloom-trove/carve-assets/P23", "nyxloom-trove/carve-assets/P24"]
oracles:
  - id: O1
    observable: "The real network-disabled gate installs the exact five-wheel hash-bound build closure, builds from the reviewed committed OID, and installs a wheel whose metadata, assay.__version__, and emitted assay_version are the same non-placeholder identity"
    negative: "Removing setuptools-scm/packaging/vcs-versioning, restoring the ambient setuptools PYTHONPATH, or forcing 0.0.0 fails before a self-hosted PASS"
    gate: tester-unified
  - id: O2
    observable: "Two independent clean assay-v1.2.3 builds from tracked source and one SOURCE_DATE_EPOCH are byte-identical; a dirty source and a no-VCS source have their exact distinct SCM/fallback identities and cannot satisfy the clean manifest"
    negative: "Ignored pycache/egg-info residue enters a wheel, a manual version shadows Git, or dirty bytes still install under the clean release identity/hash"
    gate: tester-unified
  - id: O3
    observable: "A closed release manifest binds exact filename/version/sha256; the standalone verifier checks bounded regular bytes and one matching METADATA before emitting the sole pip --require-hashes input"
    negative: "Unknown/duplicate fields, basename/hash/metadata mismatch, duplicate METADATA, symlink/special/oversized input, or a post-verification byte change reaches a successful install"
    gate: tester-unified
  - id: O4
    observable: "The installed-wheel self-host and independent second witness retain all registered phase/receipt markers, zero runtime dependencies, no Assay source import, and no reachable 0.0.0/ambient-backend compatibility path"
    negative: "Deleting the installed wheel, exposing the source tree, or reviving the old standalone expectation leaves the registered gate green"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any locked P24 asset hash differs before implementation"
  - "the five locked wheels cannot install under --no-index --require-hashes on tester-unified Python 3.14"
  - "setuptools-scm cannot derive the reviewed clone or synthetic tagged fixture identity without an environment-supplied version"
  - "a required behavior needs any forbidden src/assay or prior-carver asset"
  - "the existing gate markers, independent witness, cgroup validation, uid identity, or network-disabled execution cannot be preserved"
mutexes: [merge-lane]
---

# P24 — versioned wheel contract

The claim to attack: **the bytes a consumer installs are the bytes named by a
real Assay version, and every gate/release build derives that identity from a
controlled source rather than from an ambient backend or placeholder.**

## Dispatch contract

- Contract class: **2d — constrained implementation**.
- Required roles: **Sonnet xhigh implementer → fresh Opus xhigh reviewer**.
- Readiness: **READY only from the Sol freeze commit that carries this handoff,
  decisions A-198–A-201, and the byte-locked P24 packet.** The frontmatter
  `input_revision` is the post-P23 source anchor; branch from the controller's
  later exact `READY_FROM_SOL` commit so the packet itself is present.
- Apply `nyxloom-trove/carve-assets/P24/skeleton.patch` once before production
  work. It fixes the build requirement grammar and standalone helper API. Do
  not replace the API with a different design. The two TODO functions are the
  intended implementation work.
- Implementer freedom is limited to private helper decomposition, ordinary
  test placement, and shell function names. Sources of version truth, five
  dependency versions/hashes, source topology, manifest grammar, bounds,
  helper CLI/stdout, pip re-verification, gate phases, and refusal behavior are
  fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P24-versioned-wheel-contract`
on branch `feat/assay-P24-versioned-wheel-contract`, created from the exact Sol
freeze commit named by the controller packet.

## Context to read first

Read these surfaces in order; a whole-repository orientation is unnecessary:

1. `nyxloom/reference/{AUTHORING,STANDARD,DOCTRINE}.md`, then
   `assay/nyxloom-trove/{nyxloom.toml,STATE.md,decisions.md}` decisions
   A-005/A-029/A-040–A-041/A-057/A-067/A-069–A-070/A-123–A-131/A-198–A-201.
2. This handoff and every file in `nyxloom-trove/carve-assets/P24/`. Verify
   `fixture-manifest.json` before applying the skeleton or copying anything.
3. `pyproject.toml` build-system/SCM/package-data sections and
   `src/assay/__init__.py` only to understand the existing authority boundary.
   `src/assay/**` is forbidden: installed versions already come from
   `importlib.metadata`; source-only `0+unknown` remains honest and unchanged.
4. `tools/tester-unified-gate.sh` and `nyxloom-trove/nyxloom.toml`'s complete
   gate declaration/comment. Preserve cgroup validation, derived fail-loud host
   bind, run-uid identity, installed isolation, both witnesses, and all receipt
   markers.
5. `tests/conftest.py`, `tests/test_standalone.py`,
   `tests/test_self_hosting.py`, `tests/test_dependency_purity.py`, and
   `tests/test_verdict_schema_is_packaged.py`. Locate every `0.0.0`,
   `setuptools_home`, and “plugin absent” premise before editing.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 10 and
   `assay-v2-post-series-review-sol-P15-P19.md`'s distribution/external-project
   goals. P24 establishes distribution; P25 performs real Topos qualification.

## Prepared inputs — copy; do not rediscover

The carver packet contains:

```text
nyxloom-trove/carve-assets/P24/
  build-requirements.txt
  wheelhouse-manifest.json
  wheelhouse/*.whl                         # exact five-wheel build closure
  fixtures/assay-1.2.3-py3-none-any.whl   # positive verifier fixture
  fixtures/release-manifest.json          # independent one-line expectation
  skeleton.patch
  test_acceptance.py                       # 24 behavioral cases
  fixture-manifest.json
  probe-results.json
```

After verifying `fixture-manifest.json`, mechanically create the production
offline closure with exactly these copies and no generated replacements:

```text
gate/distribution/build-requirements.txt
gate/distribution/build-wheelhouse-manifest.json
gate/distribution/build-wheelhouse/<the five locked wheels>
```

The production files must be byte-identical to their P24 sources. Do not run
`pip download`, choose newer compatible versions, regenerate hashes, or omit a
transitive wheel. The locked acceptance suite compares every byte and rejects
an extra wheel.

## Normative implementation packet

### 1. Exact build closure

`[build-system].requires`, in this order, is exactly:

```toml
requires = [
    "setuptools==84.0.0",
    "wheel==0.47.0",
    "setuptools-scm==10.0.5",
    "packaging==26.3",
    "vcs-versioning==2.2.4",
]
```

`setuptools-scm` and `wheel` both need packaging; setuptools-scm also needs
vcs-versioning, whose locked 2.2.4 release needs packaging >=26.2. The old
three-entry declaration and ambient `PYTHONPATH="$setuptools_home"` are false
closures and must disappear. Setuptools 82.0.1 is not retained: it has a
published vulnerability fixed in 83.0.0, and the real 84.0.0 probe passed.

The build venv installs only from the production wheelhouse with all of:

```text
--no-index
--find-links <exact production build-wheelhouse>
--require-hashes
-r <exact production build-requirements.txt>
```

Then and only then may `pip wheel --no-index --no-build-isolation --no-deps`
run. `--no-build-isolation` is truthful because the complete pinned backend is
already installed and hash-checked; it is not permission to borrow ambient
packages.

### 2. Four version/source shapes

| source shape | required identity | release manifest? |
|---|---|---|
| clean tracked synthetic repo tagged `assay-v1.2.3` | exactly `1.2.3` | yes |
| same repo with one tracked source mutation after tag | pinned SCM `.dev…+g….d20000101`, not `1.2.3` | clean manifest refuses |
| tracked `pyproject.toml` + `src/**` copy with no `.git` | exactly declared fallback `0.1.0` | no; source-distribution fallback witness only |
| exact reviewed untagged vbpub clone | non-placeholder SCM development identity | no; self-hosting development build |

`src/assay/__init__.py` continues to read `importlib.metadata.version("assay")`.
Its `0+unknown` branch describes a source import with no installed
distribution; it is not a wheel version and must not be removed, changed to a
release, or used in a manifest. No environment pretend-version variable,
manual version file, `0.0.0`, or “first wheel in directory” selection exists.

Synthetic release sources copy only paths returned by Git for
`assay/pyproject.toml` and `assay/src/**`, then create two independent parent
repositories with fixed author/committer name, email, timestamp, message, and
lightweight tag. They never `copytree(src)` and then `git add` ignored residue.
Both builds use `LC_ALL=C.UTF-8`, `TZ=UTC`, `PYTHONHASHSEED=0`, and
`SOURCE_DATE_EPOCH=946684800`, independent build/output/venv directories, and
complete byte comparison.

The self-hosting gate does not build in the caller's worktree. From the
validated worktree it records the exact full OID, makes a private
`git clone --no-local --no-checkout` under its scratch directory, configures a
sparse checkout containing `assay`, checks out that exact OID detached, and
requires the clone HEAD to equal it before building `clone/assay`. `--no-local`
forbids local-clone hardlinks/alternates; sparse checkout excludes unrelated
working-tree content; ignored build/egg-info/pycache residue from the consumer
cannot enter the clone. Any clone/checkout/OID mismatch is a gate failure, not
a no-VCS fallback.

### 3. Closed release manifest and helper

The only manifest schema is one JSON object with exactly four members:

```json
{"schema_version":1,"filename":"assay-1.2.3-py3-none-any.whl","version":"1.2.3","sha256":"fdec65b2c944de61cfbfb0e0672f96136becc2c42db88c131d70ea19383a7578"}
```

- `schema_version` is integer 1, never boolean.
- `version` is the skeleton's canonical PEP 440 grammar: at least three
  numeric release components, optional canonical epoch/pre/post/dev/local
  suffixes, ASCII only, no leading-zero numeric component.
- `filename` is exactly `assay-<version>-py3-none-any.whl`.
- `sha256` is exactly 64 lowercase hexadecimal characters.
- Unknown, missing, or duplicate JSON members are refusal. Producer output is
  the skeleton's one-line canonical JSON plus one newline.

The standalone stdlib-only interface is fixed by `skeleton.patch`:

```text
python gate/distribution/release_wheel.py manifest \
  --wheel <exact-wheel> --version <exact-version> --output <new-manifest>

python gate/distribution/release_wheel.py verify \
  --wheel <exact-wheel> --manifest <exact-manifest>
```

`manifest` refuses an existing output and derives filename/hash/METADATA from
the wheel; it does not accept caller-supplied hash or name. `verify` succeeds
with exit 0, empty stderr, and exactly one stdout line:

```text
assay @ file:///absolute/path/assay-1.2.3-py3-none-any.whl --hash=sha256:<64hex>
```

On expected bad input both commands return 2, print only
`release-wheel: REFUSED: <reason>` to stderr, and emit no stdout/traceback.

Complete only `_sha256` and `_wheel_name_and_version` in the skeleton:

1. The already-open regular wheel is <=64 MiB. Stream it in fixed-size chunks,
   calculate sha256, and rewind the same descriptor.
2. Open that same descriptor with `zipfile.ZipFile`; enumerate `infolist()` and
   require exactly one total `*.dist-info/METADATA`, whose name is exactly
   `assay-<expected-version>.dist-info/METADATA`.
3. Require declared/uncompressed METADATA <=1 MiB before reading; read it once,
   parse headers with `email.parser.BytesParser`, and require exactly one
   `Name: assay` and one exact `Version`.
4. A duplicate member, wrong dist-info path, bad ZIP, oversized member, duplicate
   header, or name/version mismatch is refusal. Never extract the ZIP.

The skeleton already owns manifest <=4 KiB, wheel <=64 MiB, metadata <=1 MiB,
duplicate-key parsing, regular-file `fstat`, nonblocking/no-follow opening, and
canonical writing. Do not weaken/reimplement those facts. The v1 verifier is a
POSIX-container release tool and deliberately fails if the required safe-open
flags do not exist; the built wheel itself remains `py3-none-any`.

Verification is not installation. The consumer writes the successful stdout
line into a private requirements file and runs:

```text
python -m pip install --no-index --require-hashes -r <verified-requirement>
```

Pip's second hash check is mandatory: it binds the bytes pip actually opens
and makes a byte change after helper verification fail. Ordinary
`pip install <verified-path>` would retain a check/use race and is forbidden.

### 4. Registered gate transformation

Refactor only the build/install portion of `tools/tester-unified-gate.sh`:

1. Outer mode keeps validated cgroup and derived host bind, adds
   `--network=none`, and retains the exact final completion marker.
2. Inner mode validates/records the source OID and makes the private exact-OID
   sparse clone above.
3. Create separate `build-venv` and `run-venv` from the image's base Python.
4. Install the five build requirements into `build-venv` with the exact
   no-index/find-links/require-hashes recipe. Query `importlib.metadata` and
   require all five installed names/versions before building.
5. Build exactly one `assay-*.whl` from `clone/assay`; require its filename and
   METADATA version to be nonempty and not `0.0.0`/`0+unknown`.
6. Install that exact wheel into `run-venv` with `--no-index --no-deps`. Add the
   existing tester-unified test closure `.pth` only to `run-venv`; no build
   wheel or Assay source path enters it.
7. Before self-hosting, independently require wheel METADATA,
   `importlib.metadata.version("assay")`, and `assay.__version__` to match;
   require `importlib.metadata.requires("assay")` to be empty; require
   `assay.__file__` below `run-venv` and outside every vbpub source path.
8. Run the installed `assay` against the original reviewed worktree exactly as
   today. Require its emitted `assay_version` to equal the installed version.
9. Preserve, byte-for-byte, these successful phase markers:

```text
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
```

The existing independent `/opt/tester-venv` witness continues to read the
artifact with only `run-venv` site-packages exposed. A failure diagnostic may
rerun tests for logs but must retain the original nonzero result. Do not remove
uid/HOME/XDG identity, cgroup validation, the bind-source fail-loud logic, or
the independent witness to simplify distribution work.

## Work and traceability

1. Verify the P24 fixture manifest; apply the skeleton; copy the exact locked
   production wheelhouse/requirements/manifests.
2. Complete only the two named verifier TODOs and promote their locked attacks
   into ordinary tests without importing Assay.
3. Replace build-system requirements and remove every old ambient-backend /
   `0.0.0` compatibility premise from gate fixtures/tests/comments.
4. Implement the exact committed-clone two-venv gate flow and preserve all
   outer/inner security and receipt behavior.
5. Promote the real tagged×2, dirty, no-VCS, installed-artifact, zero-runtime,
   and source-residue tests. Keep independent expected hashes/metadata; do not
   have production code generate its own expected fixture.
6. Document the release manifest → verifier → pip-hash consumer recipe and the
   distinction among release, SCM development, no-VCS fallback, and source-only
   identities.
7. Perform a reachability/coverage sweep after replacing the gate path: search
   for `0.0.0`, `setuptools_home`, ambient backend `PYTHONPATH`, “plugin absent”,
   dead compatibility branches, and stale v3 artifact language. Each remaining
   occurrence must be a deliberate hostile input or historical explanation,
   not reachable acceptance behavior.
8. Produce `nyxloom-trove/reports/assay-P24-versioned-wheel-contract-LOG.md`
   with exact source/review OIDs, wheel filenames/versions/hashes, dependency
   versions, locked result, ordinary suite, registered marker receipt, and
   A-067 break counts.

| requirement | oracle | independent proof | controlled break |
|---|---|---|---|
| exact offline build closure | O1/O4 | locked files + pip hash mode + installed metadata | remove each transitive/backend wheel separately |
| committed source only | O1/O2 | exact-OID private clone and tracked-path synthetic repo | seed ignored pycache/egg-info and compare wheel membership |
| reproducible clean release | O2 | two independent full wheel bytes | source mutation after tag / changed epoch |
| four honest identities | O1/O2 | wheel filename + METADATA + installed import/artifact | `0.0.0`, manual/env version, fallback used for Git repo |
| closed manifest | O3 | carver one-line fixture + stdlib ZIP/email | field/hash/name/version/member mutations |
| bytes actually installed | O3 | pip `--require-hashes` after verifier output | mutate wheel between verify and pip |
| self-host isolation | O1/O4 | run-venv path + independent tester witness | source `PYTHONPATH` or deleted wheel |
| route completeness | O4 | stale-shape search + ordinary/full gate | leave old compatibility path reachable |

The locked diagnostic suite command is
`python -m pytest nyxloom-trove/carve-assets/P24/test_acceptance.py -q`.

Run it from `assay/` in the P24 worktree. Post-implementation it is exactly
**24 passed**. The JIT report records both the intentional skeleton red and the
disposable reference completion that proved all cases can pass.

## Required combined-axis reviewer attacks

The fresh reviewer must add at least one new combined-axis attack and state the
passing-wrong implementation it targets. At minimum rerun these:

1. ignored pycache + existing egg-info + clean tag + repeated builds: bytes may
   be reproducible yet contain undeclared source;
2. correct manifest hash + changed METADATA Name/Version + duplicate METADATA:
   a hash-only verifier passes a self-consistent wrong wheel;
3. correct first verification + byte mutation before pip: ordinary install has
   a check/use race, hash mode refuses;
4. backend present ambiently + missing locked plugin/transitive wheel + network
   disabled: an ambient fallback must not save the build;
5. exact reviewed OID + dirty original worktree + clean private clone: the
   build consumes committed bytes, while the controller separately refuses a
   dirty dispatch rather than smuggling dirt into the wheel;
6. successful installed self-host + deleted/renamed wheel or source exposure:
   the independent witness/path assertions must still fail.

Break backend availability, each transitive closure member, reproducibility,
dirty identity, fallback identity, manifest grammar, sha, filename, metadata,
ZIP multiplicity, safe-open bounds, pip race, runtime dependency purity,
source import, and each registered receipt path separately. Record actual
failure counts under A-067; never state “mutation testing was done” without the
counts and exact command.

## Test constraints

- No assertion depends on build duration, sleeps, or elapsed time. Subprocess
  timeouts are generous hang failsafes only and never decide identity.
- Every build/install gets an independent scratch source, output, HOME, and
  venv. No test order, ambient environment, or sibling output supplies facts.
- Tests inspect real wheel bytes/METADATA/imports/artifacts and compare against
  carver/hand expectations. “Command did not raise” and string-only source
  checks are not acceptance evidence.
- Network, clock-derived version date, locale, Git identity, source epoch,
  wheelhouse, and PATH are explicit inputs. Runtime dependencies remain zero.
- No coverage exclusions/evasion. Any changed production branch needs an
  ordinary behavioral case; full-suite parallel pollution is a real red.

## Scope and non-goals

P24 changes distribution/gating only. It does not alter Assay runtime logic,
CLI/verdict semantics, artifact schema v4, lane configuration, mutation,
canary, attestation, publish/tag state, or a real external project. Schema v4
remains the single current artifact version; historical schemas are not
writers or upgrade paths. P25 owns external Python/Topos qualification. P26
owns F8's in-snapshot Git deadline closure (A-201).

## Mechanical BLOCKED rule

On any `escalate_if` trigger or need to edit a forbidden path:

1. stop before improvising, weakening an oracle, changing a locked asset, or
   using network/ambient packages;
2. write `BLOCKED: <trigger>; observed <exact fact>; needed <exact owner/API>`
   to the P24 LOG;
3. commit only that log on the implementation branch; and
4. return `STOP: ROUTE_TO_SOL` with the exact resume message.

A product choice already fixed above is not BLOCKED merely because the model
prefers another design. Conversely, a locked hash mismatch or scope conflict
is mechanical BLOCKED, not implementer discretion.

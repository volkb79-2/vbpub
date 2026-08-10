# Assay P24 JIT carve and pre-dispatch adversarial specification review

Date: 2026-08-10
Carver/reviewer: gpt-5.6-sol xhigh
Post-P23 merge: `a7f49bb4ef54ea4cf23da795cd05db46c41a2851`
Post-P23 carver correction/source anchor: `7c52ecc2f9f500991d2ba74689458ae1e6644a18`
AUTHORING revision: `2026-08-08-r5`
Disposition: **READY after correction**
Decisions: A-197–A-201

## Result first

P24 is ready for a Sonnet xhigh implementation and a fresh Opus xhigh review
from the exact Sol freeze commit that contains this report and its locked
packet. It was **not** dispatchable in its provisional form.

The provisional handoff named the correct outcome but left the implementer to
discover the real offline dependency closure, choose versions/hashes, decide
whether a known-vulnerable estate pin remained authoritative, invent a release
helper API, decide what “verify before install” meant across a check/use gap,
choose which source bytes enter a release, and reconstruct the gate's build
environment. Worse, one premise was already false: tester-unified does not
contain setuptools-scm, and the old ambient-setuptools build therefore emits
the deliberately accepted `0.0.0` placeholder.

The corrected 2d packet transfers the solution rather than only its goal. It
locks a five-wheel Python >=3.11 closure and pip hash file, supplies a real
positive Assay wheel/manifest, pins the standalone helper CLI/data grammar and
safe-I/O bounds in a compiling skeleton, specifies the exact committed-source
and two-venv gate topology, and provides 24 independent behavioral cases. With
the skeleton plus prescribed mechanical wheelhouse copies, the suite is a
controlled red of **7 failed, 17 passed**; completing only the skeleton's two
named functions in a disposable proof worktree yields **24 passed**.

No P24 production implementation was landed or dispatched. P23's production
state remains unchanged. The only pre-P24 direct correction is carver-owned
SB-P23-01 at `7c52ecc2`; its locked suite is 19/19.

## Exact AUTHORING adversarial prompt used

The canonical prompt was applied first to the provisional P24 handoff plus the
landed P23/gate/packaging state, and again to the corrected handoff and locked
packet:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

## 1. Blocking ambiguities and resolutions

| provisional ambiguity/false premise | why it blocked | frozen correction |
|---|---|---|
| build wheelhouse “contains exactly requirements named in pyproject” but only three were named | `wheel` and setuptools-scm have transitive requirements; a network-disabled resolver cannot invent them | A-198: exact five direct pins and locked wheel bytes/hashes |
| tester-unified was assumed to exercise setuptools-scm | neither `/opt/tester-venv` nor the base Python has the plugin; old `PYTHONPATH` exposes only setuptools | remove ambient backend path; hash-install full closure in a fresh build venv |
| estate pin `setuptools==82.0.1` versus greenfield security | official PyPI reports a vulnerability fixed in 83.0.0 | use witnessed current `84.0.0`; lock filename/hash/size |
| “same release source” did not say which filesystem bytes | copying `src/**` and committing can include ignored pycache/egg-info; repeated wheels are still identically contaminated | A-199: tracked paths only for fixtures; exact-OID no-local sparse clone for the gate |
| current self-host build versus synthetic clean release | actual vbpub has no Assay release tag, while a fake `1.2.3` on the gate artifact would misstate the tested commit | gate uses the real untagged SCM dev identity; separate synthetic fixture proves clean release behavior |
| manifest version grammar was merely “semver/PEP440” | helper had to invent accepted prerelease/dev/local spellings and filename mapping | skeleton freezes canonical PEP 440 subset with >=3 release components and exact wheel name |
| helper accepted “exactly `(wheel_path, manifest_path)`” but no public command/output/error API | implementer could return a bool, install itself, search a directory, or emit ambiguous prose | exact `manifest`/`verify` CLI, exit codes, canonical output, and PEP 508 stdout |
| “verify before install” ignored local replacement between opens | a helper can verify clean bytes, then ordinary pip opens hostile changed bytes | A-200: helper output is the sole pip requirement and pip must use `--require-hashes` |
| hash/version agreement did not define ZIP identity | a self-consistent wrong wheel or duplicate METADATA passes hash-only checks | exact one bounded `assay-<version>.dist-info/METADATA`, one Name and Version |
| manifest/wheel reads were unbounded/pathname-reopened | special files can block; symlinks/races/ZIP bombs exceed finite work | skeleton fixes regular no-follow/nonblocking open and 4 KiB/64 MiB/1 MiB bounds |
| old gate used one scratch venv for build/runtime concepts | build tools can leak into the claimed runtime environment and obscure dependency purity | separate build/run venvs; tester closure `.pth` only in run venv; zero Assay requirements asserted |
| gate source was the mutable worktree | ignored residue and build writes influence source; read-only bind can fail on an old build directory | exact reviewed OID cloned privately, detached and sparse before building |
| gate refactor scope did not preserve network/cgroup/receipt facts explicitly | a green inner test could drop the outer receipt or start an unplaced/networked container | exact step/marker list; `--network=none`; cgroup/bind/identity retained |

No externally visible P24 choice remains open after A-198–A-200. Private
function decomposition and ordinary test-file placement do not affect the
contract.

## 2. False-PASS attacks

| oracle | convenient passing-wrong implementation | locked discriminator |
|---|---|---|
| O1 offline identity | expose base setuptools on PYTHONPATH and keep expecting `0.0.0`; self-host tests only require a nonempty string | exact five installed versions plus metadata/import/artifact equality and explicit placeholder refusal |
| O1 closure | lock only the three build-system packages; rely on ambient packaging/vcs-versioning | fresh venv, network disabled, pip hash file names all five; delete each wheel separately |
| O2 reproducibility | `copytree(src)` then `git add .`; build twice and compare equal contaminated wheels | source tree is derived from `git ls-files`; wheel membership rejects pycache/egg-info |
| O2 dirty identity | inject `SETUPTOOLS_SCM_PRETEND_VERSION=1.2.3` for every build | no pretend variable; dirty wheel METADATA/name/bytes differ and clean manifest refuses |
| O2 source selection | build in original worktree after deleting/ignoring residue | exact-OID private clone; original ignored files cannot enter it and source is never cleaned to make proof pass |
| O3 byte binding | verify sha once, then ordinary `pip install <path>` | mutate the file after successful helper verification; pip `--require-hashes` must refuse |
| O3 semantic binding | calculate hash and trust filename, never inspect METADATA | mutate Name/Version, update manifest hash, and require refusal |
| O3 ZIP grammar | use `ZipFile.getinfo`, which returns one of duplicate names | duplicate exact METADATA entry; enumerate and require one total member |
| O3 input safety | `is_file()` then reopen/read all | symlink, FIFO, directory, 4097-byte manifest, 64 MiB+1 wheel, oversized METADATA |
| O4 installed isolation | prepend source `PYTHONPATH`; installed wheel can be absent | imported path must be below run venv and outside vbpub; wheel deletion/source exposure is a break |
| O4 gate receipt | let diagnostic rerun pass and return zero after original self-host red | original result controls exit; exact phase/final markers only after their facts pass |
| O4 route cleanup | add new path but retain old `setuptools_home`/`0.0.0` compatibility branch | required reachability/search sweep and ordinary tests; retained occurrence must be a named negative/history only |

The central lesson is that **byte reproducibility is not source correctness**,
and **pre-install verification is not install-time byte binding**. Both shapes
can look unusually rigorous while proving the wrong fact.

## 3. Frozen implementation packet

### Requirement-to-oracle traceability

| requirement | owner | oracle | independent observable | controlled break |
|---|---|---|---|---|
| five exact build pins/hashes | pyproject + committed gate assets | O1/O4 | PyPI wheel METADATA, carver hashes, pip hash install, installed versions | remove/alter each wheel or requirement |
| current secure backend | build closure | O1 | exact setuptools 84 metadata and successful real probe | restore 82 pin or mismatched hash |
| committed input | gate clone + release fixture builder | O1/O2 | exact OID/tracked file list and wheel members | seed ignored residue/read-only old build |
| clean reproducibility | distribution tests | O2 | independent full bytes + METADATA | changed epoch/source/toolchain |
| dirty/fallback/dev identity | SCM config + real Git | O1/O2 | exact filenames/METADATA and clean-manifest rejection | manual/env version or fallback in Git repo |
| closed manifest | standalone helper | O3 | independent one-line fixture and stdlib ZIP/email inspection | field/type/duplicate/name/version/hash corruptions |
| finite safe verifier | helper | O3 | descriptor type/size and bounded member enumeration | symlink/FIFO/oversize/duplicate member |
| actual installed bytes | pip consumer recipe | O3 | pip hash mode after successful helper | mutate between verify/install |
| isolated self-host | two venvs + independent witness | O1/O4 | import path, empty runtime requirements, artifact version | source exposure/build-dep reliance |
| registered receipt | gate outer/inner | O4 | four exact markers after Docker zero | delete one phase/witness or diagnostic launders result |

### Values transferred instead of delegated

The handoff now directly supplies:

1. all five dependency versions, filenames, sizes, and sha256 values;
2. pip's exact hash-checked install argv shape;
3. exact pyproject list/order;
4. exact four source/version cases and allowed manifest status;
5. fixed Git identities/timestamps/tag/source epoch for the release witness;
6. exact private clone/sparse/exact-OID gate topology;
7. manifest schema, canonical bytes, accepted version regex, and filename rule;
8. helper commands, stdout, errors, exit codes, safe-open and size bounds;
9. exact ZIP member/header rules and verification order;
10. pip re-verification recipe;
11. separate build/run venv ownership and isolation assertions;
12. exact registered marker and preserved outer security facts;
13. 24 locked behavioral cases and a compiling two-TODO skeleton; and
14. mechanical BLOCKED triggers and a reviewer attack matrix.

This is the intended “solution-bearing handoff” mode: Sonnet still writes and
integrates the production code, but it is no longer asked to perform package
resolution, API/product design, security threat modeling, and proof design
inside the same implementation turn.

## 4. Scope/dependency corrections

- `src/assay/**` is now forbidden. P24 needs no runtime change; allowing it made
  a manual version constant the easiest shortcut.
- `tests/**` is allowed because the old standalone backend fixture lives in
  `tests/conftest.py`, not only the two initially named files.
- `gate/distribution/**` owns production wheelhouse copies and the standalone
  helper; P24 assets remain immutable/forbidden.
- `tools/tester-unified-gate.sh` and the gate's documentation are explicit
  because distribution proof must run in the real registered gate, not only a
  unit venv.
- `assay.toml`, CLI/runner/verdict/schema/adapters, and every prior locked packet
  are forbidden. Artifact schema v4 remains the one current schema.
- `merge-lane` remains because the registered gate and its declared comment are
  shared serial surfaces.

Every P24 oracle is satisfiable within corrected scope. A runtime-code need is
mechanical ROUTE_TO_SOL, not authority to add a second version mechanism.

## 5. Corrected input/fixture matrix

| axis | values | combined witness |
|---|---|---|
| source authority | tracked clean tag; tracked dirty; no VCS; reviewed untagged clone | filenames/METADATA/manifests and exact clone OID |
| source residue | none; ignored pycache; existing egg-info/build | tracked export and wheel-member absence |
| build closure | all five; plugin missing; packaging missing; vcs missing; ambient setuptools | network-disabled pip hash install |
| repetition | two independent roots/outputs/venvs | byte comparison, not just version |
| manifest | positive; missing/unknown/duplicate/type; basename/hash mismatch | helper exit/stdout/stderr |
| wheel ZIP | valid; wrong Name; wrong Version; duplicate METADATA; oversize | updated hash forces semantic check |
| path/file kind | regular; symlink; FIFO; directory; oversized | no-follow/nonblocking/fstat bounds |
| install sequence | unchanged after verify; mutated after verify | pip `--require-hashes` |
| environment | build venv; run venv; independent tester; source exposed | versions/requirements/import path/artifact |
| gate result | build red; self-host red; independent red; all green | phase/final marker placement |

Mandatory combined-axis attacks are listed in the handoff. The reviewer must
add a seventh materially different pair rather than simply rerun these.

## 6. Witnessed evidence

### Real tester-unified package facts

The gate image is Python 3.14. In the existing image:

- `/opt/tester-venv`: setuptools, wheel, and setuptools-scm are absent;
- base `/usr/local/bin/python3`: setuptools 82.0.1 and wheel 0.46.3 are
  present, setuptools-scm is absent; and
- the old gate's ambient-setuptools-only `PYTHONPATH` build consequently
  creates `assay-0.0.0` and its old standalone test asserts that placeholder.

The five locked wheels installed successfully under `--no-index` in a fresh
Python 3.14 venv and reported exactly the versions in A-198.

### Official release metadata/security premise

Official PyPI JSON supplied the filenames/hashes/sizes recorded in the locked
manifest. In particular, `setuptools/82.0.1` reports CVE-2026-59890 /
GHSA-h35f-9h28-mq5c, fixed in 83.0.0; current 84.0.0 reports the locked
`setuptools-84.0.0-py3-none-any.whl` hash. Sources:

- `https://pypi.org/pypi/setuptools/82.0.1/json`
- `https://pypi.org/pypi/setuptools/84.0.0/json`
- `https://pypi.org/pypi/wheel/0.47.0/json`
- `https://pypi.org/pypi/setuptools-scm/10.0.5/json`
- `https://pypi.org/pypi/packaging/26.3/json`
- `https://pypi.org/pypi/vcs-versioning/2.2.4/json`

### Real offline distribution probe

The final probe ran in `tester-unified:local` with `--network=none` and the
repository-mandated validated background cgroup. From tracked source at the
P23 correction anchor, prospective setuptools 84, fixed identity/tag/epoch:

```text
clean first   assay-1.2.3-py3-none-any.whl
              fdec65b2c944de61cfbfb0e0672f96136becc2c42db88c131d70ea19383a7578
clean second  assay-1.2.3-py3-none-any.whl
              fdec65b2c944de61cfbfb0e0672f96136becc2c42db88c131d70ea19383a7578
dirty         assay-1.2.4.dev0+g0cf5262.d20000101-py3-none-any.whl
              c9d95d6e0658693e8e7535958f9e2550f3764697e084d47822f9af924be306b3
no VCS        assay-0.1.0-py3-none-any.whl
              aed198a74b8df4876bde3d10c826c83563f0519beed3432b2bb4ba53e092d440
current repo  assay-0.1.1.dev3007+g7c52ecc2f-py3-none-any.whl
              210f292a0cf062a7f047ac0a39757db328c4e71d1c494877975741ea401783c9
```

The first probe used a naive `cp -a src` before `git add`. It committed ignored
pycache/egg-info, produced a reproducible 582,556-byte wheel, and thereby
demonstrated the false-PASS rather than merely hypothesizing it. Switching to
tracked paths produced the correct 232,651-byte clean fixture.

### Pip check/use proof

The standalone expected requirement line installed the locked fixture under
`--no-index --require-hashes` into a fresh venv. Installed metadata and
`assay.__version__` were both `1.2.3`, and the import path was under that venv.
The locked suite then mutates bytes after successful helper verification and
requires pip's install-time hash check to refuse.

### Locked acceptance

With the skeleton applied and production wheelhouse files copied exactly:

```text
....FF........FFFF..F...  [100%]
7 failed, 17 passed in 16.51s
```

The seven failures all cross the two deliberate TODOs: positive verification,
manifest creation, hash mismatch typing, three metadata corruptions, and
pip-recheck flow. Asset closure, manifest grammar, safe-open front half, real
offline builds, dirty/fallback identities, and installed artifact agreement
already pass.

Completing only `_sha256` and `_wheel_name_and_version` in the disposable proof
worktree:

```text
........................  [100%]
24 passed in 24.79s
```

This is satisfiability evidence, not a production implementation commit.

### Locked P24 hashes

`fixture-manifest.json` binds every content asset except itself. Its own current
sha256 is:

```text
ff5fc8dbf8adf08e92438850ffce3d74f57a24cfea4b955d947bb5340ea4c31a
```

The manifest contains exact hashes for 13 files, including all six wheels.
Critical human-authored inputs are:

```text
9e3646b192a5bf06d4a83143b1e83a9430226024fb64f0b8444bb8ec07457757  README.md
f61c1885c592cf1fb527154916308b04382057334fc8c1473dafda63134ed031  build-requirements.txt
8004d0d66b8acb3ab6a17c85b046744744e8df229c49255bfcf8c34b0fafd650  skeleton.patch
b4f59e5eb720060cc55b61d1788f19671da2e11a853c375f51be9cde74fe5ca5  test_acceptance.py
0559e51ac8138916f21c6fd7a081e220d9b6c5503349a50d815c516faa8c01e4  wheelhouse-manifest.json
99c06e8691c675dd80f4773f5f5c0b297f9de64038b2926b20125682ef013580  probe-results.json
a2068c5306b479ec404809b0ce812110aa04e28ac5eeca958cfb714a0d32eee4  fixtures/release-manifest.json
```

## 7. P23 successor dispositions

| disposition | P24 resolution |
|---|---|
| SB-P23-01 locked adapter defect | fixed directly by carver in A-197 / `7c52ecc2`; exact locked result 19 passed |
| SB-P23-02 symbolic `judge.base` | promoted to P29/P30: helper wire forbids refs; P30 must freeze moved-base/merge-base and merge-HEAD witnesses |
| SB-P23-03 reachability after dispatch | explicit sweep added to P24, P25, P29, and P30; old compatibility/unsupported/early-return owners must be deleted or witnessed |
| F8/O4 Git evaluation deadline | A-201 assigns it to P26 and expands P26 scope/oracle/work; it is not disguised as P24 work |
| F9 unresolved `project_root` inconsistency | no production route reaches it because lane loading resolves the root; retained as a documented internal robustness observation, not a new package |
| F10 hypothetical raw `OSError` after R0 | no reachable owner exists: current safeio/Git/coverage/process boundaries translate first; retained for reachability sweeps rather than adding a dead-path handoff |

F9/F10 are not silently called fixed. If a future dispatch/API change makes
either reachable, that package's mandatory sweep must promote it immediately.
Creating work now around an unproducible terminal would add tests of an invented
path, not product confidence.

## 8. Model route judgment

After this freeze, Sonnet xhigh is a good P24 implementer fit. The remaining
work is substantial shell/test integration but almost no product invention:
two small Python TODOs, exact binary copies/config pins, and a fully specified
gate refactor with 24 locked observables. Luna/cheap implementers remain risky
because preserving the outer/inner gate security and independently promoting
the locked tests spans several files and failure namespaces. Opus xhigh remains
appropriate for the fresh review because check/use, source-selection, ZIP, and
gate-receipt attacks can all remain green under locally plausible code.

No Sol implementation is required unless a mechanical BLOCKED trigger fires.
The Sol cost has already been spent where it is valuable: package/security
resolution, API design, hostile-source probing, and independent acceptance.

## 9. Disposition

**READY.** The corrected P24 contract leaves no external version, dependency,
hash, source shape, helper interface, file bound, verification order, install
binding, gate topology, marker, scope owner, or expected proof for the
implementer to invent. The packet is deliberately much more detailed than the
provisional goal brief; that is what makes the Sonnet implementation route a
cost optimization rather than a bet that implementation and review will infer
the same hidden design.

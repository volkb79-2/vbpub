# W3-ZERO report: build/release from ZERO upstream releases, and a correct dependency graph

Branch `cmru-w3-zero` from `90193c4d3`. Implementer: fresh Sonnet. Claims below are what was run; a fresh reviewer verifies.
Operator rulings applied mid-package (controller update): no new helper scripts, reuse existing tools and fix their
setup; "tiers" = multi-level graph. An early draft with a bespoke `pip wheel` builder script was DELETED and reworked.

## What changed

| Item | Change |
| --- | --- |
| Z1 bootstrap | `cmru/build-initial-standalone.sh`: `CMRU_BOOTSTRAP_CLI_EXTENDED=release\|source\|<wheel-path>`, default `release`. `source` builds `libraries/cli-extended` with cmru's OWN builder (`python -m cmru.handlers wheel-build --cwd <library>`, the wheel-builder image), pretend version `<floor>+bootstrap.source` (override `CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION`, must be `X.Y.Z+local`), library source on PYTHONPATH for THAT build step only, logs `cli-extended source wheel sha256=<hex> file=<name>`, stages it exactly like a released wheel (unpack into the private staging dir). `release` with no pointer/wheel: exit 2 naming `CMRU_BOOTSTRAP_CLI_EXTENDED=source`. No silent fallback. `<wheel-path>` needs the sha256 env. `source` + `..._WHEEL` is refused. |
| Z2 image | NO new resolver script. `tester-unified/Dockerfile` gets `CLI_EXTENDED_RESOLVE=local` (modes: `pinned` default, `latest`, `local`; documented in the Dockerfile header and `tester-unified/README.md`). It reuses the EXISTING `CLI_EXTENDED_WHEEL_SHA256` arg and a file in the build context (`tester-unified/local-wheel/`, `.gitkeep` tracked, `*.whl` git-ignored, whitelisted in `.dockerignore`). Why a new value at all: the only existing input is `--wheel-url`, which `fetch-cli-extended.py` allowlists to GitHub release URLs, so no existing input can carry a locally built wheel. The local wheel is verified with `sha256sum -c` and installed by the unchanged `pip install --no-index --no-deps` line; the post-install assertion requires a `+local` version in `local` mode and keeps "no `+`" for pinned/latest. Invalid values still fail the build (message names all three). |
| Z3 graph | `cmru.orchestration.toml`: `cmru -> cli-extended`; `mdt -> cli-extended`; `ciu`, `run-gate-project -> cmru` (declared edges used as build-time edges, same as assay/topos/pwmcp; the file already has no separate build-time kind, a `tool_dependencies` edge is for consumed artifacts and is excluded from ordering); `project_order` re-levelled (cli-extended, cmru, ciu, run-gate-project, assay, ...). `modern-debian-tools-python-debug/pip/wheels.list` names `cli-extended` (its install is `pip --no-index`; its resolver is generic, `GitHubReleases.resolve_latest(<name>)`, so no resolver change). `assay`: NO edge, because assay does not import or declare cli-extended on this tree (grep of `assay/src` and its pyproject: nothing; the audit's "1 import file" was not reproduced). `cli-extended -> cmru` is deliberately NOT declared: cmru releases cli-extended but that would be a cycle (documented in the orchestration comment and the bootstrap doc). |
| Z3 guard | Inside `cmru/src/cmru/dependencies.py` (so `cmru dependencies` AND the load-time release preflight run it): derives first-party names from each project's `pyproject.toml` `[project.dependencies]` + `[project.optional-dependencies]` (not imports), matched against project ids, scm dists and pyproject `[project].name`; every one must be a declared edge or the preflight fails with `'cmru' requires first-party 'cli-extended' in pyproject.toml [project.dependencies], but orchestration.project.cmru.depends_on does not declare 'cli-extended'`. Self-extras and third-party names are ignored. Also: `levels` (longest declared chain, transitive) on `DependencyReport`, in `as_dict()` and as a `LEVELS` section of `render_text` (so of the generated comment block). |
| Z4 doc | `docs/BOOTSTRAP-FROM-ZERO.md`: the cycle, the `project-order` fenced block (the level table), steps 1-7 using existing commands only, open items. |
| Z5 | `README.md` cli-extended row fixed. `scripts/debian-install-v2/bootstrap-remote.py:102-106` (still fetches the cli-extended SOURCE subtree) is in this tree but is the W9b branch's concern (`cli-ext-w9b-debian` exists): NOT edited, listed as follow-up. |

## Estate level table (from `cmru dependencies`, written to the generated block in `cmru.orchestration.toml`)

```
L0: cli-extended
L1: cmru
L2: ciu, run-gate-project, assay, topos, nyxloom, pwmcp, tls-edge, cgroup-profiler
L3: modern-debian-tools-python-debug
```

`cmru dependencies --write` was run on this worktree (the block regenerated); `--write --dry-run` then reported no change; the plain
`cmru dependencies` verb prints `PREFLIGHT: PASS`. (Run in-process from the worktree source: the installed cmru is main's.)

Transitive ordering finding: the existing check (provider position < consumer position for every DIRECT edge in a linear
`project_order`) already yields a valid topological order over all levels, since pairwise order composes. No code fix was needed;
a test now proves it with a 4-level fixture plus a diamond, and shows a consumer ordered before a TRANSITIVE provider is refused.
Not changed and worth knowing: a single-project `cmru release <p>` does not check that p's transitive providers are released
(only `project_order` for multi-project runs); out of scope here.

## Tests (all run; `pt.py`, serial, flock, nice/ionice, PSI checked)

- Full cmru suite: 4001 passed, 6 skipped (before the last small test addition; the final run is in the gate section).
- New: `tests/test_dependency_levels_pyproject.py` (levels, 3+/4-level chain, transitive-order refusal, cycle/unknown handling, the guard on synthetic and the REAL estate, doc order vs graph).
- `tests/test_installed_wheel_subprocess.py`: release mode fails with the hint and never builds (even with the library present), source mode builds via the handler (fake docker; the handler call is stood in by `pip wheel` on the real interpreter, recording args/env/PYTHONPATH), logs the sha256 of exactly the built wheel, staged like a release, the real `cmru_registry` builds in a BARE interpreter with it, version override must be `+local`, source+wheel refused, `<wheel-path>` mode verified by sha256.
- `tests/test_tester_unified_image.py`: the Dockerfile's real `local` shell block is extracted and executed (good wheel, digest mismatch, no wheel, two wheels), the three modes + invalid-value message, `+local` assertion text, `.dockerignore`/`.gitignore`.
- `tests/test_packaging_cli_extended.py`: the old "bootstrap never names the library" guard is narrowed: the library path appears exactly once (the source-mode assignment) and reaches PYTHONPATH only in that build subshell.
- `modern-debian-tools-python-debug/scripts/test_release_flow.py` (run from that directory): 31 passed, with a new `cli-extended` wheels.list assertion.
- `cli-extended surface check`: still exactly the CLI-EXT-26 message, as proved by the existing `test_surface_check_reports_nothing_beyond_the_known_library_gap` inside the full suite (the CLI was not run separately).

## Plants (each applied to the real file, the tests run, then restored)

| Plant | Killed by |
| --- | --- |
| default mode changed from `release` to `source` (a silent switch to source) | 12 failures, incl. `test_release_mode_with_no_release_fails_with_the_zero_release_hint_and_never_builds_from_source` |
| source mode no longer logs the sha256 | `test_source_mode_builds_the_wheel_offline_logs_its_sha256_and_stages_it` |
| `cmru depends_on = ["cli-extended"]` removed from the real orchestration | `cmru dependencies` `PREFLIGHT: FAIL` with the precise message; `TestRealEstate` and the doc test failed |
| doc order disagrees (L0/L1 swapped, or L3 dropped) | `test_a_doc_whose_order_disagrees_with_the_graph_is_detected` (asserts the checker raises, on the real graph) |

## NOT verified / for the controller

- No docker build of `CLI_EXTENDED_RESOLVE=local` and no real `build-initial-standalone.sh` run: the handler/docker step is faked in tests, and the `local` shell block is executed only on synthetic files. First real use should be watched.
- The real `cmru.handlers wheel-build` of `libraries/cli-extended` with the pretend version and the checked-out library on PYTHONPATH was not run (needs docker + the builder image).
- mdt: the resolver was not run (no network); that mdt has no zero-release path for its own base image is an OPEN ITEM (stated in the doc): `tester-unified` builds `FROM` a published mdt image, which is an L3 output of this very graph.
- nyxloom vendors the cli-extended source into its wheel without a pyproject dependency, so the guard cannot see it (follow-up; moving to the wheel dependency makes it guarded automatically).
- `bootstrap-remote.py` (W9b) still fetches cli-extended source; not edited here.
- The `+bootstrap.source` wheel is NOT the future release wheel: step 5 of the doc re-pins the image to the real release.

## Gate

Recorded after the commit (see the commit that adds this line, if present): coverage and canary lanes on `tester-unified:cmru6-integ`.

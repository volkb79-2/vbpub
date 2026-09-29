# W1 REPORT (B124 + B125)

Branch `wave-a-w1-retire`, base `762b500d`. No `src/assay` or `carve-assets/**` byte changed (checked with `git diff --stat 762b500d -- src nyxloom-trove/carve-assets`). The registered gate was not run by the implementer (CD44); see `W1-LOG.md`.

## What changed
- Deleted: `gate/python/qualify_topos.py`, `gate/python/fixtures/P25/` (7 files), `gate/python/release/P25/` (wheel + manifest), `gate/python/qualify_cmru_b006a.py`, and the tests `test_python_qualification.py`, `test_gate_qualify_cmru_b006a.py`, `test_gate_harness_version_pins.py`.
- `tools/tester-unified-gate.sh`: seven phases removed; order is now `wheel-installed`, `attestation-hardened`, `run_self_hosted_lane`, `run_independent_witness`, lint. P26 command and its 8 `--deselect`s unchanged, comments rewritten.
- `assay.toml`: four `--deselect` lines and two comments. `nyxloom.toml`: P25 comment replaced.
- Tests: embargo section removed from `test_runner_snapshot_selection.py`; marker test rewritten; `test_self_lane.py` pins no `--deselect` for both B105 lanes; one refusal test per assay-owned schema (verdict, lane, shard, state record, resource snapshot; the analysis manifest one is W2's).
- `ROOT_PINNED` in `tests/core/test_import_contracts.py` lost the three deleted names.
- Docs, decisions (A-475: A-202..A-206, A-278, A-435, A-269 WI-5 only; A-477: A-222, A-224, A-226, A-229, A-318) and backlog updated. B124/B125 say `DONE (W1, merge hash pending)`: the controller must fill in the merge hash.

## For decision
Old-version acceptance paths (B125 inventory). None was changed.

| Code | Accepts | Disposition |
|---|---|---|
| `reuse.py:14,57-69`; `runner.py:6040-6045`; `cli.py:1870` | a v12 verdict as a `--reuse-from` cold start | **REPORT** (CD6): consumer-visible; deferred to the v14 wave (A-477 hands A-470 A9 there) |
| `adjudication.py:106-113` `(1, 2)` | ciu provenance schema 1 (ciu 6.0.3) | **REPORT, change nothing** (CD7 amended: deferred to the v14 wave; the only real green ciu reference, `carve-assets/W2/ciu-provenance-green-reference.json`, is schema 1 and frozen, and two pipeline tests use it byte for byte) |
| `mutation.py:1566-1583` | nothing; the old record is refused and re-run | keep |
| `provenance.py:177-179` | PEP 610's legacy `hash` key | keep: this is pip's format, not assay's |

Decision rows that still mention a deleted path or retired marker and were **not** annotated (not on the brief's list):
- A-317 (cites `qualify_topos.py` as where a bug was found).
- A-350 (cites `qualify_topos.py` and `tests/test_python_qualification.py` as the shape of an env-gated harness).
- A-468 (its `--ignore=tests/test_python_qualification.py`; the brief says that `--ignore` never landed).

Other prose that still names retired items and was left alone as history: `nyxloom-trove/W1-*`, `W3-CARVE-*`, `WAVE-*` prompts, `handoffs/`, `4-backlog.md` older sections, and the dstdns SQL harness/test docstrings (W5).

## Residual history/outside-`assay/` readers (Work 13)
`git grep -nE "PROJECT_ROOT|REPO_ROOT|/workspaces/" -- tests/`, classified (only hits that run git on the real repository or read outside `assay/`):
- `tests/test_distribution_build_release.py`: `build_release.build(REPO_ROOT, ...)` clones HEAD; setuptools-scm `describe` walks tags. Tooling; W4 moves it (marked `requires_parent_repository`).
- `tests/test_b105_report_check.py`: `cwd=REPO_ROOT` and `--repo REPO_ROOT`; HEAD only (tooling checker, W4).
- `tests/test_self_hosting.py:122`: `git -C PROJECT_ROOT rev-parse HEAD`; HEAD only.
- `tests/test_gate_qualify_dstdns_sql.py:93`: reads `/workspaces/dstdns` when present (W5).
- `tests/qualification/test_go_r1_real.py`: `--repo _REPO_ROOT` into `build_release` plus a docker inspect of `/workspaces/vbpub`; environment-gated real-toolchain test, not part of the registered gate collection (`tests/qualification/`).
- `tests/conftest.py:218,280-332`: defines `REPO_ROOT` and `requires_parent_repository` (helpers).
- Not readers (paths inside `assay/` or literals only): `test_self_lane.py`, `test_distribution_gate.py`, `test_cgroup_parent.py` (read scripts/config in `assay/`), `test_standalone.py`, `test_dependency_purity.py`, `test_verdict_schema_is_packaged.py`, `test_go_helper_is_packaged.py`, `test_distribution_release_wheel.py`, and the `/workspaces/...` strings in docstrings and env fixtures.

Result: no judge test reads history or tags. The release-tag readers were deleted with the embargo tests.

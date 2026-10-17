# CMRU 6.2.0 integrated candidate — review and in-scope repair (Claude Opus, high)

Date: 2026-10-17. Reviewer/implementer: Claude Opus 5.5 (controller-dispatched).

## Bindings

- Checkout: `/workspaces/vbpub/.worktrees/cmru-candidate-integrated-20261017`,
  branch `cmru-candidate-integrated-20261017` (path and branch unchanged).
- Reviewed starting revision: `7f5437588f0fd1555a1ece0aac218f7a597c7e4c`, clean
  tracked/untracked state. Ignored gate evidence (`cmru/coverage.json`,
  `cmru/junit-coverage.xml`, `/tmp/run-gate/lanes/coverage/…`) left untouched.
- CIU identity: `ciu worktree inspect` was not authorized in this session, so
  only the git branch/path were checked. Nothing in the identity record, the
  path, or the branch was changed.
- Read-only comparison sources: reference candidate
  `a1ad46444af4a3604e80107d0434a9dce02e3eed`, released tag `run-gate-v24.0.0`
  (an ancestor of the starting HEAD), and, as an extra data point only,
  `cmru-validation-context-20261017` HEAD `e2f1563e2`.
- Baseline: registered `coverage` lane log
  `/tmp/run-gate/lanes/coverage/0371cbc5ec326f8616f947fc7bc5716d.log`:
  5,364 passed, 6 skipped, 19 failed, 99.87 % total coverage.

## Root cause of the 19 failures

At the starting HEAD, `tester-unified/`, `wheel-builder/`, `run-gate-project/`
and `run-gate.root.toml` are byte-identical to `run-gate-v24.0.0`. The
integration therefore took those shared support trees from the Run-Gate
release. It did not take the CMRU candidate's own support commits
(`d8d5e9250 tester-unified: pin image builds to wheel inputs`, part of
`a2b7dfa9f`). Meanwhile `cmru/tests/**`, `.dockerignore` and
`docs/BOOTSTRAP-FROM-ZERO.md` came from the CMRU candidate. Every failure is
that CMRU-side consumer asserting a producer the Run-Gate 24.0.0 tree does not
contain.

## Findings (producer → consumer)

| # | Failing oracle(s) | Producer (what is missing) | Consumer | Disposition |
|---|---|---|---|---|
| F1 | `test_tester_image_cli_extended_identity.py` (9) | `tester-unified/build-image.sh`: no `--base-image`, `--cli-extended-resolve`, `--cli-extended-wheel-sha256` or `--no-cache`, and no post-build `org.vbpub.cli-extended.wheel-sha256` check, so a stale or unlabelled image was accepted (`test_the_default_pinned_image_must_be_labelled…` built `stale` with exit 0) | the builder contract in `tester-unified/README.md` and `docs/BOOTSTRAP-FROM-ZERO.md` steps 2–4 | **Fixed** |
| F2 | `test_tester_unified_image.py` (7 of 8): pin/latest digest binding, resolve modes and README, `fetch_wheel(expect_sha256=…)` ×2, explicit zero-release base, wheel-input COPY/RUN certification ×2 | `tester-unified/Dockerfile` (no `ARG TESTER_UNIFIED_BASE_IMAGE`, no capability-based host-tool RUN, no cli-extended label, `latest` installed pointer bytes without a caller digest, no post-fetch `sha256sum -c`); `fetch-cli-extended.py` (no `--expect-sha256`); README, CONSUMERS and DESIGN-GUIDE | the CMRU image oracles; the guide's pasteable build commands | **Fixed** |
| F3 | `test_tester_unified_image.py::test_wheel_builder_does_not_depend_on_a_published_vbpub_image`; `test_installed_wheel_subprocess.py::test_fresh_checkout_bootstrap_is_the_only_source_build_launcher` | `wheel-builder/Dockerfile` still uses `FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug…` and lacks `ARG WHEEL_BUILDER_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie` and `LABEL org.vbpub.wheel-builder.kind="python-wheel-builder"`. `cmru/build-initial-standalone.sh` (the bootstrap) already requires that label | the bootstrap source-build path and the zero-release guide | **BLOCKED**: path outside scope |
| F4 | `test_bootstrap_from_zero_sequence.py::test_every_run_gate_tester_input_the_guide_passes_is_read_by_run_gate_source` | Run-Gate 24.0.0 `run-gate-project/tools/release_candidate.py` `prepare()` calls `build-image.sh` with only `--wheel-dir` and `--tag`, and reads none of `RUN_GATE_TESTER_UNIFIED_BASE_IMAGE`, `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE` or `RUN_GATE_TESTER_CLI_EXTENDED_SHA256` | `docs/BOOTSTRAP-FROM-ZERO.md` step 4 (`cmru release cgroup-profiler/run-gate-project`) | **BLOCKED**: needs a new Run-Gate release |
| F5 | coverage 99.87 % < 100 % (not one of the 19) | `cmru/src/cmru/cli.py` from `a87489743 fix(cmru): add safe OCI release body retry`, which exists only on this branch: 16 lines and 12 branches of refusal paths in `_prepare_oci_release_body_retry` and the `publish --release-tag` dispatch had no oracle | the coverage lane's `fail-under=100` | **Fixed** (tests only) |

### F1/F2 repair

The seven `tester-unified/` files are restored to the CMRU candidate's
reviewed versions, file by file. The result is byte-identical to `a1ad46444`
and to `e2f1563e2`, so either integration route converges. The whole branch
was not copied. The contracts now hold:

- An unlabelled image or an old-pin image fails the post-build check.
- `latest` and `local` without a 64-lowercase-hex digest are refused before
  any Docker call.
- A digest in `pinned` or default mode is refused.
- The fetcher refuses a pointer or download naming other bytes.

Compatibility with Run-Gate 24.0.0: `release_candidate.py prepare` calls the
builder with only `--wheel-dir` and `--tag`, so it gets the default `pinned`
mode, which still works. Two Run-Gate test files changed alongside the port:

- `run-gate-project/tests/test_release_candidate.py::test_release_image_builder_checks_governed_parent_and_exact_labels`
  pinned the literal line `cmru tester-gate --cwd run-gate-project
  --cgroup-parent "$cgroup_parent" /bin/true`. It now pins the
  `tester_gate_args` form, matching what the validation-context line did. The
  governed-parent contract is unchanged, and the oracle is no weaker: it now
  also pins the `--image "$base_image"` preflight.
- `run-gate-project/tests/test_tester_unified_base_variants.py` was added
  verbatim from `e2f1563e2`. The ported `tester-unified/docs/DESIGN-GUIDE.md`
  names it as the executable oracle for the capability-based host-tool RUN, so
  without it that reference would dangle. It reads only the Dockerfile and
  changes no released Run-Gate code.

### F5 repair

New behavioral tests in `cmru/tests/test_oci_image_release.py`, all asserting
that neither the handler nor (where relevant) the origin query ran:

- a pre-release carrying `+`/`.dev`, a version no OCI tag can carry, and a
  version Git cannot store as a tag;
- a push step without the OCI command;
- uninspectable or un-recheckable checkouts, which raise `RuntimeError`, never
  "clean";
- a malformed local tag commit;
- a HEAD or worktree that changes during origin verification;
- a declared push command absent despite a validated image (fail-closed
  guard);
- `publish --release-tag` with more than one selected project (exit 2);
- a project spanning more than one Git family, in both real and dry runs
  (exit 1).

No product code changed.

## BLOCKED

BLOCKED: `wheel-builder/Dockerfile` is outside this task's allowed paths. F3's
two oracles (bootstrap source-build path and zero-release wheel builder)
require its reviewed CMRU-candidate change:
`ARG WHEEL_BUILDER_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie`,
`FROM ${WHEEL_BUILDER_BASE_IMAGE}`,
`LABEL org.vbpub.wheel-builder.kind="python-wheel-builder"`, and the explicit
`ca-certificates git passwd` apt step (`git diff HEAD a1ad46444 --
wheel-builder/Dockerfile`, 9 lines). Note that `e2f1563e2` carries a different
18-line variant, so the controller must choose which one to take.

BLOCKED: the from-zero guide's three `RUN_GATE_TESTER_*` inputs are ignored by
every released Run-Gate source. Run-Gate 24.0.0's `tools/release_candidate.py`
has no consumer for them, so the documented step-4 behavior is unavailable:

- the release-gate image rebuild honors the resolve mode;
- `latest` requires the published digest;
- the image's `org.vbpub.cli-extended.wheel-sha256` label is inspected.

A consumer exists only in unreleased Run-Gate work
(`cmru-validation-context-20261017`: +244 lines in `release_candidate.py`,
+423 in `run_gate.py`). Delivering it requires a new public Run-Gate release
(and then the guide's step-4 version). Changing the guide to describe
24.0.0's actual behavior would instead be a product decision, because that
step would then rebuild with the stale pin and the MDT base. Neither is
taken here.

## Files changed

- `tester-unified/{Dockerfile,README.md,build-image.sh,fetch-cli-extended.py,gen-requirements.py}`
- `tester-unified/docs/{CONSUMERS.md,DESIGN-GUIDE.md}`
- `run-gate-project/tests/test_release_candidate.py` (one oracle line → three)
- `run-gate-project/tests/test_tester_unified_base_variants.py` (new)
- `cmru/tests/test_oci_image_release.py` (new tests)
- this report

## Expected gate state (not a claim of success)

No tests or gates were run; the controller runs the registered lanes. After
this commit, the 16 F1/F2 oracles and the F5 coverage lines should turn green.
The coverage lane should still fail on the three F3/F4 oracles until both
BLOCKED items are resolved. Run-Gate's own lanes include the updated builder
oracle and the new base-variant oracle. The CMRU 6.2.0 release transaction
must not start until CMRU's `coverage` and `gate-provisional` lanes pass.

## Commit

The commit hash is reported in the controller hand-back; this file is part of
that commit.

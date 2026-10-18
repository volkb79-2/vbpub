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
| F3 | `test_tester_unified_image.py::test_wheel_builder_does_not_depend_on_a_published_vbpub_image`; `test_installed_wheel_subprocess.py::test_fresh_checkout_bootstrap_is_the_only_source_build_launcher` | `wheel-builder/Dockerfile` still uses `FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug…` and lacks `ARG WHEEL_BUILDER_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie` and `LABEL org.vbpub.wheel-builder.kind="python-wheel-builder"`. `cmru/build-initial-standalone.sh` (the bootstrap) already requires that label | the bootstrap source-build path and the zero-release guide | **Fixed** in the follow-up, after the scope amendment (see "F3 follow-up") |
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

## F3 follow-up (scope amended to `wheel-builder/**`)

The first pass recorded F3 as BLOCKED because `wheel-builder/` was outside the
allowed paths. The controller then added `wheel-builder/**` to the scope. The
follow-up started at `54a9934d91ac98b98c7cb7e8ff41351e1d946377` (clean).

**Contracts.**

- The bootstrap (`cmru/build-initial-standalone.sh` lines 259–282) accepts a
  configured builder tag only when the tag carries
  `org.vbpub.wheel-builder.kind=python-wheel-builder`. Otherwise it rebuilds
  the tag with
  `docker buildx build --load --pull -f wheel-builder/Dockerfile` and no
  `--build-arg`, then refuses if the label is still wrong.
- The wheel-build contract (`cmd_wheel_build` in `cmru/src/cmru/handlers.py`)
  runs the image as its own `USER 1003`, with `HOME=/home/builder`. It invokes
  `/opt/wheel-builder-venv/bin/python -m build` against the bind-mounted
  project, and setuptools-scm runs `git` inside the container.

**The two variants.** Both carry the ARG, `FROM ${WHEEL_BUILDER_BASE_IMAGE}`,
the label, and the `ca-certificates git passwd` apt step, so both satisfy
every literal the two oracles assert.

- The 9-line reference variant (`a1ad46444`) keeps the old
  `echo … >> /etc/passwd` / `>> /etc/group` lines.
- The 18-line variant (`e2f1563e2`; introduced by `4ce6e3907` and `c3d82cb01`
  on 2026-10-15) creates the gate identity only when it is missing:
  `getent group 994 || groupadd --gid 994 docker` and
  `getent passwd 1003 || useradd --uid 1003 --gid 994 --create-home …`.

**Choice: the 18-line `e2f1563e2` variant, taken verbatim.**

- It is the complete original change. The reference variant is a partial
  carry: it installs `passwd`, whose only purpose is to provide
  `groupadd`/`useradd`, yet it dropped the hunk that uses them.
- On the new upstream base (not the MDT image the appends were written for),
  blind appends silently add a second entry when gid 994 or uid 1003 already
  exists. The conditional form never duplicates an id. A conflicting existing
  `docker` group name makes `groupadd` fail the build loudly, instead of
  producing an ambiguous identity.
- Both variants give the same runtime identity to `cmd_wheel_build` (uid 1003,
  gid 994, writable `/home/builder`).
- The file now matches `e2f1563e2` byte for byte, so the two integration lines
  converge. The validation-context worktree was read only through git objects;
  its checkout and dirty state were not touched.

**Docs.** No docs changed:

- `WHEEL_BUILDER_BASE_IMAGE` is internal. No caller passes it (the bootstrap
  and `cmd_wheel_build` use the default), and no README, CONSUMERS or guide
  presents it as a setting.
- The public facts are already documented and match the change:
  - the builder uses Microsoft's upstream Python base and needs no released
    vbpub image;
  - the bootstrap checks the `org.vbpub.wheel-builder.kind` label and
    rebuilds a missing or mismatched tag.

  They appear in `cmru/README.md` (lines 79–81 and 115–119),
  `cmru/docs/CONSUMERS.md` (60–61), `cmru/docs/DESIGN-GUIDE.md` (262–266) and
  `docs/BOOTSTRAP-FROM-ZERO.md` (93, 128–129).
- `wheel-builder/` has no README of its own.

**Static check only.** By reading the file (not by running anything), the new
Dockerfile contains the four literals the oracles require: the ARG, the
`FROM ${…}`, the apt line, and the `LABEL`. It no longer contains
`ghcr.io/volkb79-2/modern-debian-tools-python-debug`. Both oracles are
unchanged.

## BLOCKED

*Resolved 2026-10-18: Run-Gate 24.0.1 is public and carries the consumer.
See "Follow-up 2026-10-18" below. The original record is kept as written.*

BLOCKED (retained; deliberately outside the follow-up's scope): the from-zero guide's three `RUN_GATE_TESTER_*` inputs are ignored by
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

First pass (commit `54a9934d91ac98b98c7cb7e8ff41351e1d946377`):

- `tester-unified/{Dockerfile,README.md,build-image.sh,fetch-cli-extended.py,gen-requirements.py}`
- `tester-unified/docs/{CONSUMERS.md,DESIGN-GUIDE.md}`
- `run-gate-project/tests/test_release_candidate.py` (one oracle line → three)
- `run-gate-project/tests/test_tester_unified_base_variants.py` (new)
- `cmru/tests/test_oci_image_release.py` (new tests)
- this report

F3 follow-up (the commit after `54a9934d9`, hash given in the controller
hand-back):

- `wheel-builder/Dockerfile` (+15/−3; byte-identical to `e2f1563e2`)
- this report (F3 marked fixed; F4 still BLOCKED)

## Expected gate state (not a claim of success)

No tests or gates were run, in either pass; the controller runs the registered
lanes. Nothing here claims that any test or gate passed.

- The 16 F1/F2 oracles, the 2 F3 oracles and the F5 coverage lines are
  expected to turn green.
- The coverage lane is expected to stay red on the single F4 oracle,
  `test_every_run_gate_tester_input_the_guide_passes_is_read_by_run_gate_source`,
  until the Run-Gate prerequisite is resolved.
- Run-Gate's own lanes include the updated builder oracle and the new
  base-variant oracle.

The CMRU 6.2.0 release transaction must not start until CMRU's `coverage` and
`gate-provisional` lanes pass.

## Follow-up 2026-10-18: review against published Run-Gate 24.0.1

- Base: `b97143b7f54b92e7dae9675f37a427e81f1d5379` (merge of tag
  `run-gate-v24.0.1`, peeled `f793a3b8a316e8dd36d1d5b91bcc7f76d1b7d6cc`),
  clean. `run-gate-project/` and `tester-unified/` match the tag byte for
  byte (`git diff run-gate-v24.0.1 HEAD -- run-gate-project tester-unified`
  is empty) and stay unchanged.
- **F4 is resolved by the release.** Run-Gate 24.0.1's
  `tools/release_candidate.py` reads all three `RUN_GATE_TESTER_*` inputs. It
  also requires the image's `org.vbpub.tester-unified.base-image` label to
  equal the requested base.

Findings in CMRU scope, all fixed here:

| # | Severity | Behavior |
|---|---|---|
| G1 | High (false refusal) | Guide step 4 always exported `RUN_GATE_TESTER_CLI_EXTENDED_SHA256`. Run-Gate 24.0.1 refuses that digest unless the mode is `latest`, so a release in `pinned` mode (what the identity check reports once the Dockerfile pin is current) was refused. The digest is now passed only with `latest`. |
| G2 | Medium (silently ignored input) | The CGProfile release also received `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE`/`_SHA256`, but nothing in that release reads them. It now receives only `RUN_GATE_TESTER_UNIFIED_IMAGE`. |
| G3 | High (red oracle) | The guide named Run-Gate 24.0.0. `test_the_release_commands_follow_the_declared_graph_and_one_set_of_versions` requires the version to be at least the newest `run-gate-project/CHANGES.md` release (24.0.1). The wheel build, the wheel globs and `--set-version` now all say 24.0.1. CMRU's own gate floor (`RUN_GATE_MIN_VERSION = "24.0.0"`) stays as it is. |
| G4 | High (false refusal of valid builds in tests) | 24.0.1's `build-image.sh` re-reads `org.vbpub.tester-unified.base-image`. The fake `docker` in `test_tester_image_cli_extended_identity.py` did not answer that label, so every expected-success build exited 2. The fake now answers it (by default with the Dockerfile's default base, as a real default build records). |
| G5 | Medium (missing oracle) | Nothing in CMRU's tests covered the base label. New tests in both directions: a default build with another base label, an explicit base the build did not consume, and an unlabelled image are each refused; a correctly labelled explicit base is accepted and passed to Buildx. A Dockerfile oracle requires the label to follow an in-stage `ARG TESTER_UNIFIED_BASE_IMAGE` with no default of its own, with exactly one global default. |

**Step-4 oracle.** The new tests run the guide's real step-4 block with a
recording `cmru`, for `pinned` and for `latest`. They check the exact
per-command environment, then pass the Run-Gate command's environment to
Run-Gate 24.0.1's own `_tester_image_options()` and
`_tester_image_build_command()`. That checks the guide's output against the
real consumer, not a copy of it. One control test shows Run-Gate refuses the
old unconditional digest. Another shows the Run-Gate release never starts
without its step-3 inputs.

**Docs.** `docs/BOOTSTRAP-FROM-ZERO.md` was updated: step 2 now covers the
base label, and step 4 gives the per-mode inputs and names 24.0.1 as their
consumer. `cmru/CHANGES.md`'s stale note ("consumer outside this change set")
was replaced. The CMRU README, CONSUMERS and DESIGN-GUIDE do not describe
tester-image labels (`tester-unified/docs` does, at the tag), so they are
unchanged.

**Gate state.** No tests or gates were run. Only static checks were made
(Python AST parse of the edited tests, `bash -n` of the guide's shell
blocks). Nothing here claims that a test or gate passed.

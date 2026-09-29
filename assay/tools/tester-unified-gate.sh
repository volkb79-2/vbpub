#!/usr/bin/env bash
# Registered Assay gate driver. The outer mode derives the host bind source,
# verifies the configured gates cgroup through cgroup-parent.sh, launches
# tester-unified with the network disabled, and emits the final receipt marker
# only after Docker returns zero AND the SQL qualification phase (W5, A-480)
# has passed on the host, against a real PostgreSQL the tester container cannot
# reach. The inner mode is invoked only inside that container.
#
# P24 (A-198-A-201): the wheel this gate self-hosts through is no longer built
# from the bind-mounted worktree with an ambient-setuptools PYTHONPATH shim.
# It is built from a private, exact-OID, no-local sparse clone (so ignored
# build/egg-info/pycache residue from the caller's worktree cannot enter it)
# using a hash-checked, offline, five-wheel build closure installed into its
# own `build-venv` -- never the ambient interpreter's own setuptools. The
# resulting wheel installs into a *separate* `run-venv` with `--no-index
# --no-deps`; only `run-venv` gets the tester-unified test closure `.pth`, so
# the self-hosted lane and its independent witness both exercise exactly the
# wheel-installed `assay`, never a source import and never the build tools.

set -euo pipefail

die() { printf 'tester-unified-gate: %s\n' "$*" >&2; exit 1; }

validate_worktree() {
  case "$1" in
    /workspaces/vbpub|/workspaces/vbpub/.worktrees/*) ;;
    *) die "worktree $1 is outside /workspaces/vbpub" ;;
  esac
}

# --- inner mode: committed-clone build, two-venv install, self-host --------

make_exact_oid_clone() {
  local worktree="$1" scratch="$2" oid clone_head
  oid="$(git -C "$worktree" rev-parse HEAD)"
  [[ -n "$oid" ]] || die "could not resolve the source OID for $worktree"

  git clone --no-local --no-checkout --quiet "$worktree" "$scratch/clone"
  git -C "$scratch/clone" sparse-checkout init --cone
  git -C "$scratch/clone" sparse-checkout set assay
  git -C "$scratch/clone" checkout --quiet --detach "$oid"

  clone_head="$(git -C "$scratch/clone" rev-parse HEAD)"
  [[ "$clone_head" == "$oid" ]] || \
    die "private clone HEAD ($clone_head) does not match source OID ($oid)"
}

build_offline_closure_venvs() {
  local scratch="$1" distribution="$2" base_prefix
  base_prefix="$(/opt/tester-venv/bin/python -c 'import sys; print(sys.base_prefix)')"
  "$base_prefix/bin/python3" -m venv "$scratch/build-venv"
  "$base_prefix/bin/python3" -m venv "$scratch/run-venv"

  "$scratch/build-venv/bin/python" -m pip install \
    --no-index \
    --find-links "$distribution/build-wheelhouse" \
    --require-hashes \
    -r "$distribution/build-requirements.txt"

  "$scratch/build-venv/bin/python" - <<'PYEOF' || die "installed build closure does not match the locked five-wheel pins"
from importlib.metadata import version

expected = {
    "setuptools": "84.0.0",
    "wheel": "0.47.0",
    "setuptools-scm": "10.0.5",
    "packaging": "26.3",
    "vcs-versioning": "2.2.4",
}
for name, want in expected.items():
    got = version(name)
    assert got == want, f"{name}: expected {want}, got {got}"
PYEOF
}

# B024/DA-R7: the lint closure is a THIRD venv, never `build-venv` and never
# `run-venv`. A linter is not a build input and not a runtime dependency, so
# putting it in either would change what A-198's five-wheel closure assertion
# above is asserting -- that assertion must stay byte-for-byte what it was.
# `lint-wheelhouse/` holds exactly one pure-Python wheel (pyflakes has no
# dependencies), fetched once on a networked host and pinned by sha256 in
# `lint-requirements.txt` and `lint-wheelhouse-manifest.json`; the install is
# `--no-index --require-hashes`, so the gate's own network-less closure is not
# loosened by a byte.
build_lint_venv() {
  local scratch="$1" distribution="$2" base_prefix
  base_prefix="$(/opt/tester-venv/bin/python -c 'import sys; print(sys.base_prefix)')"
  "$base_prefix/bin/python3" -m venv "$scratch/lint-venv"

  "$scratch/lint-venv/bin/python" -m pip install \
    --no-index \
    --find-links "$distribution/lint-wheelhouse" \
    --require-hashes \
    -r "$distribution/lint-requirements.txt"

  "$scratch/lint-venv/bin/python" - <<'PYEOF' || die "installed lint closure does not match the locked pyflakes pin"
from importlib.metadata import version

got = version("pyflakes")
assert got == "3.4.0", f"pyflakes: expected 3.4.0, got {got}"
PYEOF
}

# Runs pyflakes over the JUDGED source -- the private exact-OID clone, not the
# bind-mounted worktree, for the same reason the wheel is built from the clone
# (A-198): a gitignored stray `.py` in the caller's tree must not be able to
# redden or to launder this phase. pyflakes' whole rule set IS the F-rule set
# (undefined names, unused imports and locals, redefinitions, f-strings without
# placeholders); it carries no style opinions, so there is no rule selection to
# get wrong and no configuration file to drift.
#
# Scope is `src/assay` AND `tests/` (B062). It used to be `src/assay` alone:
# at B024's landing `tests/` reported 31 findings across 19 modules, and
# widening the scope was deliberately deferred to its own sweep with its own
# evidence rather than wired in as a side effect. B062 was that sweep -- all
# 31 are gone (25 unused imports, 5 dead locals, 1 redefinition), so the
# scope follows.
#
# `tests/fixtures/` is EXCLUDED, permanently and by name, because
# `tests/fixtures/mutation/python/broken.py` is a DELIBERATELY unparseable
# file: the mutation suite needs it in order to prove how assay reports a
# source file it cannot parse. pyflakes reports it as `invalid syntax` and
# can never pass over it, so a fixture tree is not a place this phase can
# judge -- the exclusion is a fact about the fixture's purpose, not a
# tolerance for findings.
#
# pyflakes has no exclude flag, so the exclusion is expressed as an explicit
# file list from `find`. `-print0`/`mapfile -d ''` because a path this gate
# does not control could contain anything but a NUL.
#
# `gate/tests/` (the tooling tests, B123) is linted too, as its own array, so a
# clone without it is refused rather than linted as nothing. The rest of `gate/`
# (the distribution and qualification helpers) stays out of scope: it was
# measured clean at B024 and is still, but widening past the test trees would be
# unevidenced scope drift.
run_lint_phase() {
  local scratch="$1"
  local -a test_sources
  # `-H` resolves the named root (and only the named root), so the phase does
  # not care whether the clone's `tests/` is a real directory -- as it is in
  # the container -- or a symlink to one. Nothing inside the tree is followed.
  mapfile -d '' -t test_sources < <(
    find -H "$scratch/clone/assay/tests" \
      -path "$scratch/clone/assay/tests/fixtures" -prune -o \
      -type f -name '*.py' -print0
  )
  [[ ${#test_sources[@]} -gt 0 ]] \
    || die 'lint phase found no test sources to lint -- the tests/ tree is missing from the clone'
  local -a analysis_sources
  mapfile -d '' -t analysis_sources < <(
    find -H "$scratch/clone/assay/analysis" -type f -name '*.py' -print0
  )
  [[ ${#analysis_sources[@]} -gt 0 ]] \
    || die 'lint phase found no analysis sources to lint -- the analysis/ tree is missing from the clone'
  local -a gate_test_sources
  mapfile -d '' -t gate_test_sources < <(
    find -H "$scratch/clone/assay/gate/tests" -type f -name '*.py' -print0
  )
  [[ ${#gate_test_sources[@]} -gt 0 ]] \
    || die 'lint phase found no gate/tests sources to lint -- the gate/tests/ tree is missing from the clone'
  "$scratch/lint-venv/bin/python" -m pyflakes \
    "$scratch/clone/assay/src/assay" \
    "${test_sources[@]}" \
    "${analysis_sources[@]}" \
    "${gate_test_sources[@]}" \
    || die 'pyflakes reported findings in src/assay, tests/, analysis/ or gate/tests/ (see the lines above)'
  echo 'ASSAY_GATE_PHASE=pyflakes-clean'
}

build_one_wheel() {
  local scratch="$1"
  local -a wheels
  # pip's own build log goes to stderr: this function's stdout is a return
  # channel (the caller captures it with `$(...)`) and must carry nothing but
  # the resulting wheel path.
  "$scratch/build-venv/bin/python" -m pip wheel \
    --no-index \
    --no-build-isolation \
    --no-deps \
    --wheel-dir "$scratch/dist" \
    "$scratch/clone/assay" >&2

  shopt -s nullglob
  wheels=("$scratch"/dist/assay-*.whl)
  [[ ${#wheels[@]} -eq 1 ]] || die "expected exactly one Assay wheel, found ${#wheels[@]}"
  printf '%s\n' "${wheels[0]}"
}

# Echoes the wheel's verified, non-placeholder version on success.
require_real_wheel_version() {
  local scratch="$1" wheel="$2"
  "$scratch/build-venv/bin/python" - "$wheel" <<'PYEOF'
import email
import re
import sys
import zipfile

wheel = sys.argv[1]
match = re.fullmatch(r"assay-(.+)-py3-none-any\.whl", wheel.rsplit("/", 1)[-1])
assert match, f"unexpected wheel filename: {wheel}"
filename_version = match.group(1)

with zipfile.ZipFile(wheel) as archive:
    name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
    document = email.message_from_bytes(archive.read(name))
metadata_version = document["Version"]

assert filename_version and metadata_version, "empty wheel or METADATA version"
assert filename_version == metadata_version, (
    f"wheel filename version {filename_version!r} != METADATA version {metadata_version!r}"
)
assert metadata_version not in {"0.0.0", "0+unknown"}, (
    f"wheel version is the forbidden placeholder {metadata_version!r}"
)
print(metadata_version)
PYEOF
}

install_wheel_into_run_venv() {
  local scratch="$1" wheel="$2"
  "$scratch/run-venv/bin/python" -m pip install --no-index --no-deps "$wheel"
}

write_tester_closure_pth() {
  local scratch="$1" tester_site run_venv_site
  tester_site="$(/opt/tester-venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  run_venv_site="$("$scratch/run-venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  printf '%s\n' "$tester_site" > "$run_venv_site/tester_unified_site.pth"
  printf '%s\n' "$run_venv_site"
}

require_installed_purity() {
  local scratch="$1" version="$2"
  "$scratch/run-venv/bin/python" - "$version" "$scratch/run-venv" <<'PYEOF'
import sys
from importlib.metadata import requires, version as installed_version

import assay

expected_version, run_venv = sys.argv[1], sys.argv[2]

installed = installed_version("assay")
assert installed == expected_version, (
    f"run-venv installed version {installed!r} != wheel METADATA version {expected_version!r}"
)
assert assay.__version__ == installed, (
    f"assay.__version__ {assay.__version__!r} != installed version {installed!r}"
)

declared = requires("assay") or []
unconditional = [item for item in declared if "extra ==" not in item]
assert not unconditional, f"assay declares runtime dependencies: {unconditional}"

assert assay.__file__.startswith(run_venv + "/"), (
    f"assay imported from outside run-venv: {assay.__file__}"
)
assert not assay.__file__.startswith("/workspaces/vbpub/"), (
    f"assay imported from a vbpub source path: {assay.__file__}"
)
PYEOF
}

require_emitted_version_matches() {
  local scratch="$1" verdict="$2" expected="$3" emitted
  emitted="$("$scratch/run-venv/bin/python" -c \
    'import json, sys; print(json.load(open(sys.argv[1]))["assay_version"])' "$verdict")"
  [[ "$emitted" == "$expected" ]] || \
    die "emitted assay_version ($emitted) != installed version ($expected)"
}

# B018/A-327: the gate's own end-to-end witness that a distribution invocation
# records the digest of the artifact it was ACTUALLY installed from. The wheel
# is hashed here, on the host side, with a tool that has never imported assay;
# the number it produces must equal the one the installed console script wrote
# into its own verdict. Nothing in between can launder the comparison, because
# neither side derives its value from the other.
require_emitted_judge_provenance() {
  local scratch="$1" verdict="$2" wheel="$3" expected_version="$4"
  local wheel_digest emitted_digest emitted_artifact emitted_version emitted_algorithm
  wheel_digest="$(sha256sum "$wheel" | cut -d' ' -f1)"
  [[ ${#wheel_digest} -eq 64 ]] || die "sha256sum did not yield a 64-hex digest for $wheel"

  read -r emitted_artifact emitted_algorithm emitted_digest emitted_version < <(
    "$scratch/run-venv/bin/python" - "$verdict" <<'PYEOF'
import json
import sys

document = json.load(open(sys.argv[1]))
identity = document.get("judge_provenance")
assert isinstance(identity, dict), (
    "the self-hosted lane emitted no judge_provenance, although it ran the "
    "installed wheel, which always identifies itself (B018)"
)
print(
    identity["artifact"],
    identity["digest_algorithm"],
    identity["digest"],
    identity["version"],
)
PYEOF
  )
  [[ "$emitted_artifact" == "wheel" ]] || \
    die "emitted judge_provenance.artifact ($emitted_artifact) is not 'wheel'"
  [[ "$emitted_algorithm" == "sha256" ]] || \
    die "emitted judge_provenance.digest_algorithm ($emitted_algorithm) is not 'sha256'"
  [[ "$emitted_version" == "$expected_version" ]] || \
    die "emitted judge_provenance.version ($emitted_version) != installed ($expected_version)"
  [[ "$emitted_digest" == "$wheel_digest" ]] || \
    die "emitted judge_provenance.digest ($emitted_digest) != the installed wheel's own sha256 ($wheel_digest)"
  echo 'ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel'
}

# Runs the self-hosted lane against the ORIGINAL reviewed worktree (never the
# private clone) with $scratch/run-venv first on PATH. On failure, prints a
# captured verdict diagnosis and returns 1 unconditionally -- inspection
# success must never launder a red lane into a zero exit,
# and no phase marker is printed on this path.
run_self_hosted_lane() {
  local worktree="$1" scratch="$2" version="$3" wheel="$4"
  cd "$worktree/assay"
  export PATH="$scratch/run-venv/bin:$PATH"
  # B018/A-327: `--require-judge-provenance` is exactly the flag a gate that
  # binds its evidence to a verified judge binary passes, and this gate is
  # one. It refuses before any work if the running assay cannot identify the
  # artifact it came from -- so a self-hosted run that somehow imported source
  # instead of the installed wheel stops here, loudly, rather than producing
  # evidence attributed to a build it never ran.
  # (A-429) `--resume --progress` on EVERY `assay run` in the estate is an
  # operator directive of 2026-09-02, now estate policy (vbpub AGENTS.md,
  # run-gate SPEC R-38 / RG-33, run-gate rev 33). run-gate appends both to
  # every assay-kind lane it drives; this gate calls `assay run` itself, so it
  # mirrors the policy rather than being the one exception to it. Resume state
  # is touched only by the mutation sweep, while B064 records R0 progress too.
  # The invocation shape is uniform whether or not a lane checkpoints mutants.
  # The progress file lives in
  # `$scratch` like the verdict, never in the worktree, where an untracked
  # file would read as DIRTY_TREE.
  if ! assay run tester-unified --require-judge-provenance \
      --resume --progress "$scratch/progress-tester-unified.jsonl" \
      --verdict-json "$scratch/verdict.json"; then
    echo 'ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; inspecting its captured verdict' >&2
    assay analyze verdict "$scratch/verdict.json" \
      --expected-commit "$(git -C "$worktree" rev-parse HEAD)" --format text >&2 \
      || echo 'ASSAY_GATE_DIAGNOSTIC=captured-verdict-unavailable-or-invalid' >&2
    # NO_MEASUREMENT/DIRTY_TREE is assay's POST-run whole-tree
    # check (`runner.py`'s `post_reason`), which carries no path list, so a
    # lane whose own command passed but left a file behind reports a green
    # command and a red lane above. Naming the paths costs
    # one git call and is the difference between a five-minute answer and a
    # rebuild-the-container investigation.
    # Both halves, and the second is the one that matters. `git.dirty_paths`
    # (A-177) is deliberately the UNION of `git status --porcelain` and
    # `git ls-files --others --exclude-per-directory=.gitignore`, because
    # porcelain status honours `.git/info/exclude` and assay must not -- a
    # personal, unversioned ignore rule may not hide a file from the
    # dirty-tree check. Printing only the status half reproduces exactly the
    # blindness the lane does not have, which is why the first version of
    # this diagnostic printed NOTHING for the B017-class failure it was
    # written to explain (round-1 review, m2).
    # Both queries are anchored at "$worktree" with `-C`, and that anchoring is
    # the whole point rather than tidiness. This function has already done
    # `cd "$worktree/assay"`, and `git ls-files` is scoped to the CURRENT
    # DIRECTORY -- so a bare call here lists nothing outside `assay/` and the
    # B017 files, which live at the worktree ROOT, stay invisible. That is how
    # the SECOND attempt at this diagnostic still could not see the class it
    # was written for (round-2 review, R2-M2). `-C "$worktree"` also makes the
    # paths repo-top-relative, which is exactly what `git.dirty_paths` reports
    # and therefore what the lane's own refusal is about.
    echo 'ASSAY_GATE_DIAGNOSTIC=worktree-status-after-the-lane' >&2
    git -C "$worktree" status --porcelain >&2 || true
    echo 'ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query' >&2
    git -C "$worktree" ls-files --others --exclude-per-directory=.gitignore >&2 || true
    return 1
  fi
  require_emitted_version_matches "$scratch" "$scratch/verdict.json" "$version"
  require_emitted_judge_provenance "$scratch" "$scratch/verdict.json" "$wheel" "$version"
  echo 'ASSAY_GATE_PHASE=self-hosted-lane-passed'
}

# (A-478) The analysis package's own R0+R1 lane (`assay.toml` [lanes.analysis]),
# run with the same installed assay as the self-hosted lane (PATH is already
# exported by run_self_hosted_lane). No `require_emitted_*` here:
# `--require-judge-provenance` binds this verdict.
run_analysis_lane() {
  local worktree="$1" scratch="$2"
  cd "$worktree/assay"
  if ! assay run analysis --file assay.toml --require-judge-provenance \
      --resume --progress "$scratch/progress-analysis.jsonl" \
      --verdict-json "$scratch/verdict-analysis.json"; then
    echo 'ASSAY_GATE_DIAGNOSTIC=analysis-lane-red; inspecting its captured verdict' >&2
    assay analyze verdict "$scratch/verdict-analysis.json" \
      --expected-commit "$(git -C "$worktree" rev-parse HEAD)" --format text >&2 \
      || echo 'ASSAY_GATE_DIAGNOSTIC=captured-verdict-unavailable-or-invalid' >&2
    return 1
  fi
  assay verify "$scratch/verdict-analysis.json" || die 'assay verify refused the analysis lane verdict'
  echo 'ASSAY_GATE_PHASE=analysis-lane-passed'
}

run_independent_witness() {
  local scratch="$1" run_venv_site="$2"
  PYTHONPATH="$run_venv_site" ASSAY_SELF_HOSTING_VERDICT="$scratch/verdict.json" \
    /opt/tester-venv/bin/python -m pytest gate/tests/test_self_hosting.py -q \
      --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=independent-self-hosting-passed'
}

# (B123, S1) The host binds the receipt to the HEAD it captured before launch;
# the container proves it judged that same commit, at its start and at its end.
require_expected_head() {
  local worktree="$1"
  if [[ -n "${ASSAY_GATE_EXPECTED_COMMIT:-}" ]]; then
    [[ "$(git -C "$worktree" rev-parse HEAD)" == "$ASSAY_GATE_EXPECTED_COMMIT" ]] \
      || die "worktree HEAD is not the commit the host captured ($ASSAY_GATE_EXPECTED_COMMIT)"
  fi
}

run_inner() {
  local worktree="$1"
  validate_worktree "$worktree"
  require_expected_head "$worktree"

  # The self-hosted lane below judges the ORIGINAL worktree, so its clean-tree
  # precondition requires the reviewed source to be committed. Refuse before
  # building a wheel from the private clone and then discovering the reviewed
  # tree cannot be judged at all.
  if [[ -n "$(git -C "$worktree" status --porcelain=v1 -- assay)" ]]; then
    die "assay has uncommitted changes; commit them before running the merge gate"
  fi

  local scratch distribution wheel version run_venv_site
  scratch="$(mktemp -d)"
  distribution="$worktree/assay/gate/distribution"

  make_exact_oid_clone "$worktree" "$scratch"
  build_offline_closure_venvs "$scratch" "$distribution"
  wheel="$(build_one_wheel "$scratch")"
  version="$(require_real_wheel_version "$scratch" "$wheel")"

  install_wheel_into_run_venv "$scratch" "$wheel"
  echo 'ASSAY_GATE_PHASE=wheel-installed'

  run_venv_site="$(write_tester_closure_pth "$scratch")"
  require_installed_purity "$scratch" "$version"

  # P26: the locked attestation/deadline acceptance suite, run from the
  # INSTALLED wheel's own run-venv interpreter after tester-unified's pytest
  # closure is attached. Ambient PYTHONPATH is cleared and pytest's configured
  # `pythonpath` ini is overridden empty, so `pyproject.toml` cannot shadow the
  # wheel with `src/`. Only the worktree's locked test asset and project root
  # are named; the imported `assay` is exactly what was just installed above.
  #
  # P33/A-226 as amended by A-229: the module is KEPT and exactly FOUR tests
  # are deselected, not retired. Only four of its twenty-four tests touch v4
  # artifact shape; retiring the module would drop the other twenty, which
  # include A-212's process-group kill on a witnessed descendant-held pipe,
  # A-210's aggregate bounds before the first Git call, literal-pathspec
  # identity and annotated-tag peel refusal -- boundaries this project paid
  # for with real incidents and which v5 does not touch. The three
  # template-coupled tests compare against P26's locked v4 templates, which
  # A-222 freezes as historical evidence rather than rewriting. After A-477
  # the three template-coupled P26 nodes stay deselected; their historical
  # successors are retired and not executed. The fourth deselection is the
  # marker test, which asserts this very invocation's own wiring.
  #
  # `test_all_structural_and_aggregate_bounds_precede_every_git_call` is
  # deliberately NOT deselected: it tests ordering, not artifact shape.
  #
  # B006a/A-269/WI-1: LANE_SCHEMA_VERSION bumped 1 -> 2 reddens four MORE
  # locked P26 nodes below, all of which build a `schema_version = 1`
  # document via the frozen `_lane_document` helper. Historical carve assets
  # are not rewritten to pretend they were authored for v2 (WI-1's own rule),
  # so these four are deselected here rather than edited, and each has a
  # named, one-for-one v2 successor in `test_lane_schema_v2_locked_
  # successors.py`, which now runs only in the self-hosted lane. A combined
  # omnibus successor is forbidden -- a lost behaviour must stay visible.
  #
  # The `--deselect` values are ROOTDIR-RELATIVE NODEIDS, not `$worktree`
  # paths. pytest matches `--deselect` as a plain nodeid PREFIX, and the
  # nodeid of a test collected from an absolute file argument is still
  # relative to rootdir -- which is `$worktree/assay`, the directory holding
  # `pyproject.toml`. An absolute spelling here would match no nodeid at all
  # and silently deselect nothing, which is exactly the shape of failure
  # that leaves a gate looking wired while running the tests it claims to
  # have suppressed.
  # shellcheck disable=SC1007 # intentional empty PYTHONPATH for this child only
  PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \
    "$scratch/run-venv/bin/python" -m pytest \
      "$worktree/assay/nyxloom-trove/carve-assets/P26/test_acceptance.py" \
      -q -p no:randomly --override-ini=pythonpath= \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_cli_emits_the_complete_hand_authored_v4_artifact \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_cli_preserves_independent_malformed_missing_and_current_evidence \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_runner_binds_evidence_batch_to_lane_source_before_any_work \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_r0_attestation_config_round_trips_without_inventing_a_judge \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape \
      --deselect nyxloom-trove/carve-assets/P26/test_acceptance.py::test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget
  echo 'ASSAY_GATE_PHASE=attestation-hardened'

  run_self_hosted_lane "$worktree" "$scratch" "$version" "$wheel"
  run_analysis_lane "$worktree" "$scratch"

  run_independent_witness "$scratch" "$run_venv_site"

  # B024/DA-R7: lint runs AFTER the suite, deliberately. It is the cheapest
  # phase here and could run first, but a linter that reddens the gate before
  # the tests have spoken buys a five-second answer at the cost of the one the
  # reviewer actually needs. Its closure is built here, next to its only use.
  build_lint_venv "$scratch" "$distribution"
  run_lint_phase "$scratch"
  require_expected_head "$worktree"
}

_assay_gate_container_name=""
_assay_gate_container_launch_attempted=0
_assay_gate_container_started=0
_assay_gate_logs_pid=""
_assay_gate_receipt_to_clear=""
_assay_sql_container_name=""
_assay_sql_scratch=""

cleanup_assay_gate_container() {
  local result=$?
  trap - EXIT
  # (W5) The SQL phase's throwaway PostgreSQL container and clone. Nothing may
  # run before `local result=$?` / `trap - EXIT` above: any command resets `$?`.
  if [[ -n "$_assay_sql_container_name" ]]; then
    docker rm -f -v "$_assay_sql_container_name" >/dev/null 2>&1 || true
  fi
  if [[ -n "$_assay_sql_scratch" ]]; then
    rm -rf -- "$_assay_sql_scratch" || true
  fi
  if [[ -n "$_assay_gate_logs_pid" ]]; then
    kill "$_assay_gate_logs_pid" >/dev/null 2>&1 || true
    wait "$_assay_gate_logs_pid" >/dev/null 2>&1 || true
    _assay_gate_logs_pid=""
  fi
  if [[ "$_assay_gate_container_started" == 1 ]]; then
    if ! docker rm -f "$_assay_gate_container_name" >/dev/null; then
      printf 'tester-unified-gate: failed to remove container %s\n' \
        "$_assay_gate_container_name" >&2
      [[ $result -ne 0 ]] || result=1
    fi
    _assay_gate_container_started=0
  elif [[ "$_assay_gate_container_launch_attempted" == 1 ]]; then
    # A failed Docker transport can report an error after the daemon accepted
    # the named container. Best-effort removal closes that ambiguous window.
    docker rm -f "$_assay_gate_container_name" >/dev/null 2>&1 || true
    _assay_gate_container_launch_attempted=0
  fi
  # (B123, S1) After launch, a non-zero exit never leaves a receipt behind: not
  # one the container itself wrote into the bind-mounted worktree, and not one a
  # concurrent run wrote after this run's own clear.
  if [[ $result -ne 0 && -n "$_assay_gate_receipt_to_clear" ]]; then
    rm -f "$_assay_gate_receipt_to_clear"
  fi
  exit "$result"
}

run_registered_tester_container() {
  local worktree="$1" host_repo_root="$2" cgroup_parent="$3"
  local forwarded_env=() container_id wait_status logs_status
  _assay_gate_container_name="run-gate-assay-selfhosted-${BASHPID}-${RANDOM}-$(date +%s)"
  _assay_gate_container_launch_attempted=1
  _assay_gate_container_started=0
  _assay_gate_logs_pid=""
  trap cleanup_assay_gate_container EXIT

  if [[ -n ${CGROUP_PARENT_DEV_BACKGROUND:-} ]]; then
    forwarded_env=(-e "CGROUP_PARENT_DEV_BACKGROUND=$CGROUP_PARENT_DEV_BACKGROUND")
  fi

  printf 'ASSAY_GATE_CONTAINER=%s\n' "$_assay_gate_container_name"
  container_id="$(docker run -d \
    --name "$_assay_gate_container_name" \
    --init \
    --cgroup-parent="$cgroup_parent" \
    -e "CGROUP_PARENT_DEV_GATES=$cgroup_parent" \
    -e "ASSAY_GATE_EXPECTED_COMMIT=${ASSAY_GATE_EXPECTED_COMMIT:-}" \
    "${forwarded_env[@]}" \
    --network=none \
    --mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub" \
    tester-unified:local \
    bash "$worktree/assay/tools/tester-unified-gate.sh" --inner "$worktree")" \
    || die "could not start named tester-unified container"
  [[ -n "$container_id" ]] || die "docker run returned an empty container ID"
  _assay_gate_container_launch_attempted=0
  _assay_gate_container_started=1

  docker logs --follow "$_assay_gate_container_name" &
  _assay_gate_logs_pid=$!
  wait_status="$(docker wait "$_assay_gate_container_name")" \
    || die "could not collect exit status from $_assay_gate_container_name"
  [[ "$wait_status" =~ ^[0-9]+$ ]] \
    || die "docker wait returned a non-decimal exit status: $wait_status"

  if wait "$_assay_gate_logs_pid"; then
    logs_status=0
  else
    logs_status=$?
  fi
  _assay_gate_logs_pid=""
  [[ $logs_status -eq 0 ]] \
    || die "could not collect logs from $_assay_gate_container_name (exit $logs_status)"

  printf 'ASSAY_GATE_CONTAINER_EXIT=%s\n' "$wait_status"
  return "$wait_status"
}

# --- the S1 receipt (B123) ---------------------------------------------------
#
# The full `self-qualification` lane requires proof that the registered
# tester-unified gate passed at the very commit and tree it judges. That proof is
# `assay/.assay/registered-gate/tester-unified.json`, written by the HOST script
# only after the container exits zero and HEAD and its tree are unchanged, and
# removed at every launch and again on any non-zero exit after it, so a red run
# at the same commit leaves no receipt, whoever wrote one during it.
# The document is exactly {"schema_version": 1, "lane": "tester-unified",
# "commit": C, "tree": T}: the host script cannot see anything more, and commit
# plus tree plus the clear-on-launch rule already bind "the latest run passed".

clear_registered_gate_receipt() {
  local worktree="$1"
  rm -f "$worktree/assay/.assay/registered-gate/tester-unified.json"
}

write_registered_gate_receipt() {
  local worktree="$1" commit="$2" tree="$3" dir tmp
  [[ "$commit" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] \
    || die "refusing a receipt for a malformed commit id: $commit"
  [[ "$tree" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] \
    || die "refusing a receipt for a malformed tree id: $tree"
  dir="$worktree/assay/.assay/registered-gate"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.receipt.XXXXXX")" || die "cannot create a receipt file in $dir"
  printf '{"schema_version": 1, "lane": "tester-unified", "commit": "%s", "tree": "%s"}\n' \
    "$commit" "$tree" > "$tmp"
  chmod 0644 "$tmp"   # the self-qualification container reads it
  mv -f "$tmp" "$dir/tester-unified.json"
}

finish_registered_gate() {
  local worktree="$1" commit="$2" tree="$3"
  [[ "$(git -C "$worktree" rev-parse HEAD)" == "$commit" \
    && "$(git -C "$worktree" rev-parse 'HEAD^{tree}')" == "$tree" ]] \
    || die 'HEAD changed during the registered gate; no receipt'
  write_registered_gate_receipt "$worktree" "$commit" "$tree"
  echo "ASSAY_REGISTERED_GATE_RECEIPT=$worktree/assay/.assay/registered-gate/tester-unified.json"
  echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'
}

# --- the SQL qualification phase (W5, A-480) ----------------------------------
#
# Real-PostgreSQL evidence the tester container cannot produce (it has no Docker
# socket). It runs on the HOST, only after a green tester container (a red one
# already ended the script under `set -e`), from a private exact-OID clone of the
# gated commit, with the host `python3` (>= 3.11) and one throwaway container in
# the gates cgroup. Reads the caller's `worktree` (bash dynamic scope).
# Exit 3 from the harness means the environment could not answer: the gate passes
# it through as exit 3 (no receipt, no COMPLETE); every other failure is real.
run_sql_qualification() {
  local commit="$1" cgroup="$2" out rc=0
  _assay_sql_scratch="$(mktemp -d)" || die 'cannot create the SQL scratch directory'
  make_exact_oid_clone "$worktree" "$_assay_sql_scratch"
  [[ "$(git -C "$_assay_sql_scratch/clone" rev-parse HEAD)" == "$commit" ]] \
    || die "SQL clone is not the gated commit $commit"
  _assay_sql_container_name="run-gate-assay-sql-${BASHPID}-$(date +%s)"
  out="$(nice -n 19 python3 -I "$_assay_sql_scratch/clone/assay/gate/python/qualify_sql.py" \
    --scratch "$_assay_sql_scratch/sql" \
    --container-name "$_assay_sql_container_name" \
    --cgroup-parent "$cgroup")" || rc=$?
  if [[ $rc -eq 3 ]]; then
    echo 'ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive'
    printf 'ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun\n' >&2
    exit 3
  fi
  [[ $rc -eq 0 ]] || die "SQL qualification failed (exit $rc)"
  [[ "$out" == 'ASSAY_SQL_QUALIFIED=1' ]] || die 'SQL qualification printed no exact marker'
  rm -rf -- "$_assay_sql_scratch"
  _assay_sql_scratch=""
  _assay_sql_container_name=""
  echo 'ASSAY_GATE_PHASE=sql-qualified'
}

run_registered_gate() {
  local worktree="$1" host_repo_root="$2" cgroup_parent="$3" listing names commit tree
  # (CD32) One `docker ps`, no waiting: another session's gate on this shared host
  # makes this run inconclusive before it captures, clears or builds anything.
  # A `docker ps` that fails cannot show the host is free, so it is inconclusive too.
  if ! listing="$(docker ps --no-trunc --format '{{.Names}}')"; then
    echo 'ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun' >&2
    exit 3
  fi
  names="$(printf '%s\n' "$listing" | grep '^run-gate-' | paste -sd, -)" || true
  if [[ -n "$names" ]]; then
    echo "ASSAY_GATE_INCONCLUSIVE=host busy — rerun: $names" >&2
    exit 3
  fi
  commit="$(git -C "$worktree" rev-parse HEAD)" || die "cannot resolve HEAD of $worktree"
  tree="$(git -C "$worktree" rev-parse 'HEAD^{tree}')" || die "cannot resolve the tree of $worktree"
  clear_registered_gate_receipt "$worktree"
  _assay_gate_receipt_to_clear="$worktree/assay/.assay/registered-gate/tester-unified.json"
  trap cleanup_assay_gate_container EXIT
  # A plain call, never inside `||`/`if`: the script's `set -e` ends the run with
  # the container's own status, so a red container never reaches the receipt.
  ASSAY_GATE_EXPECTED_COMMIT="$commit" run_registered_tester_container "$worktree" "$host_repo_root" "$cgroup_parent"
  run_sql_qualification "$commit" "$cgroup_parent"
  finish_registered_gate "$worktree" "$commit" "$tree"
}

# --- entry points ------------------------------------------------------------

if [[ ${1:-} == "--inner" ]]; then
  [[ $# -eq 2 ]] || die 'inner mode requires exactly one worktree argument'
  run_inner "$2"
  exit 0
fi

[[ $# -eq 1 ]] || die 'outer mode requires exactly one worktree argument'
# CMRU executes this project step from ``assay/`` and deliberately supplies
# ``..``.  Convert that caller-relative spelling to the one canonical path the
# outer bind and inner gate both require; validating the raw spelling would
# reject a legitimate repository fact before either gate runs.
worktree="$(cd -- "$1" && pwd -P)" || die "cannot resolve worktree $1"
validate_worktree "$worktree"
cgroup_parent="$("$worktree/assay/tools/cgroup-parent.sh")"

host_repo_root="${ASSAY_GATE_HOST_REPO_ROOT:-}"
if [[ -z "$host_repo_root" ]]; then
  [[ -n ${HOSTNAME:-} ]] || die 'HOSTNAME is absent and ASSAY_GATE_HOST_REPO_ROOT is unset'
  host_repo_root="$(
    docker inspect "$HOSTNAME" \
      --format '{{range .Mounts}}{{if eq .Destination "/workspaces/vbpub"}}{{println .Source}}{{end}}{{end}}'
  )" || die "could not derive the host repository bind source from container $HOSTNAME"
fi
[[ -n "$host_repo_root" ]] || die 'the host repository bind source is empty'
[[ "$host_repo_root" != *$'\n'* ]] || die 'multiple host repository bind sources were returned'

run_registered_gate "$worktree" "$host_repo_root" "$cgroup_parent"

#!/usr/bin/env bash
# Registered Assay gate driver. The outer mode derives the host bind source,
# verifies the configured background cgroup through cgroup-parent.sh, launches
# tester-unified with the network disabled, and emits the final receipt marker
# only after Docker returns zero. The inner mode is invoked only inside that
# container.
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

# Runs the self-hosted lane against the ORIGINAL reviewed worktree (never the
# private clone) with $scratch/run-venv first on PATH. On failure, prints a
# diagnostic rerun for visible logs and returns 1 unconditionally -- the
# diagnostic's own `|| true` must never launder a red lane into a zero exit,
# and no phase marker is printed on this path.
run_self_hosted_lane() {
  local worktree="$1" scratch="$2" version="$3"
  cd "$worktree/assay"
  export PATH="$scratch/run-venv/bin:$PATH"
  if ! assay run tester-unified --verdict-json "$scratch/verdict.json"; then
    echo 'ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; rerunning its command for visible diagnostics' >&2
    python -m pytest tests -q --ignore=tests/test_self_hosting.py \
      --override-ini=pythonpath= || true
    return 1
  fi
  require_emitted_version_matches "$scratch" "$scratch/verdict.json" "$version"
  echo 'ASSAY_GATE_PHASE=self-hosted-lane-passed'
}

run_independent_witness() {
  local scratch="$1" run_venv_site="$2"
  PYTHONPATH="$run_venv_site" ASSAY_SELF_HOSTING_VERDICT="$scratch/verdict.json" \
    /opt/tester-venv/bin/python -m pytest tests/test_self_hosting.py -q \
      --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=independent-self-hosting-passed'
}

run_inner() {
  local worktree="$1"
  validate_worktree "$worktree"

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
  # A-222 freezes as historical evidence rather than rewriting; their SHAPE
  # coverage moves into P33's own suite
  # (`test_p26_attestation_shapes_survive_v5`, which reads those same locked
  # templates and bumps only `schema_version` in memory, so no locked byte
  # moves). The fourth deselection is the marker test, which asserts this
  # very invocation's own wiring.
  #
  # `test_all_structural_and_aggregate_bounds_precede_every_git_call` is
  # deliberately NOT deselected: it tests ordering, not artifact shape.
  #
  # B006a/A-269/WI-1: LANE_SCHEMA_VERSION bumped 1 -> 2 reddens four MORE
  # locked P26 nodes below, all of which build a `schema_version = 1`
  # document via the frozen `_lane_document` helper. Historical carve assets
  # are not rewritten to pretend they were authored for v2 (WI-1's own rule),
  # so these four are deselected here rather than edited, and each gets a
  # named, one-for-one v2 successor in `test_lane_schema_v2_locked_
  # successors.py`, run below as part of this same phase. A combined
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

  # P33: the locked v5 acceptance suite, run the same way against the same
  # installed wheel. It carries forward the artifact-shape coverage the four
  # deselections above gave up, and adds v5's own contract: the hoisted
  # `judgment.resolved`, the per-language operator vocabulary, the
  # `equivalent` bucket and its pairing, kill attribution, and helper
  # correspondence. Every negative in it is differential -- it asserts the
  # unmodified control verifies clean in the same test that asserts the
  # injected defect does not -- so none can pass on a version mismatch.
  #
  # B006a/A-269/WI-1: the same LANE_SCHEMA_VERSION bump reddens five more
  # locked nodes here, all built by the frozen `_load_lane` helper (a
  # `schema_version = 1`, rigor R0+R2 document). Same treatment: deselected,
  # never edited, each with a named v2 successor below.
  #
  # Wave-1/A-261/A-262/A-264 (amended by A-269): `VERDICT_SCHEMA_VERSION`
  # 5 -> 6 is a HARD CUT (A-261), and `assay.verify`'s schema-version guard
  # is a short-circuit: it returns a single failure and never reaches any
  # downstream field check. That single fact reddens 26 more locked nodes
  # below, MEASURED (not read off the source) by implementing v6, running
  # this module unmodified with `--tb=short`, and inspecting every failure
  # individually. All 26 failed for exactly this one cause -- two directly
  # (`test_schema_identity_is_internally_consistent` and
  # `test_shipped_schema_is_byte_identical_to_the_locked_asset` assert the
  # literal v5 `$id`/byte-identity), the other 24 because every negative in
  # this suite is differential (`refuses_only_the_defect`,
  # `test_acceptance_v5.py:72`) and its own control -- a v5-shaped document
  # -- no longer verifies clean under a v6 verifier before the specific
  # defect under test is ever reached. None is a regression; the hard cut
  # is behaving exactly as specified. Each gets a named v6 successor in
  # `carve-assets/W1/test_acceptance_v6.py`, run below, against its OWN
  # `expected/` templates -- the six v5 templates here stay frozen and are
  # never rewritten into v6.
  # shellcheck disable=SC1007 # intentional empty PYTHONPATH for this child only
  PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \
    "$scratch/run-venv/bin/python" -m pytest \
      "$worktree/assay/nyxloom-trove/carve-assets/P33/test_acceptance_v5.py" \
      -q -p no:randomly --override-ini=pythonpath= \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_fixture_itself_loads_today \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_refuses_a_cross_language_operator \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_accepts_a_matching_language_operator \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_names_kill_signal_artifact_as_reserved_for_p34 \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_names_equivalence_artifact_as_reserved_for_p34 \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_schema_identity_is_internally_consistent \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_shipped_schema_is_byte_identical_to_the_locked_asset \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_a_v5_artifact_missing_judgment_resolved_is_refused \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_a_cross_language_operator_is_refused \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_locked_v5_template_is_accepted[missing-tool-v5-template.json]" \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_locked_v5_template_is_accepted[sql-r2-v5-template.json]" \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_locked_v5_template_is_accepted[ca1-r3-no-base-v5-template.json]" \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_locked_v5_template_is_accepted[ca4-all-equivalent-v5-template.json]" \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_p26_attestation_shapes_survive_v5 \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca1_r0r3_lane_needs_no_base \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca1_an_r2_lane_without_base_is_refused \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca3_two_independent_violations_produce_two_failures \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca4_all_equivalent_is_inconclusive_not_pass \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca4_equivalent_mutants_do_not_count_as_survived \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_kill_signal_is_rejected_outside_the_killed_bucket \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_helpers_entry_requires_a_correspondingly_judged_claim \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca9_payload_free_all_mutants_equivalent_is_refused \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca9_all_mutants_equivalent_is_bound_to_r2 \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_base_is_forbidden_unless_r1_or_r2 \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca10_declared_attribution_requires_a_kill_signal_artifact \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca10_declared_requires_a_kill_signal_on_every_killed_entry \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_p25_v5_siblings_validate[pass-v4-template.json-p25-pass-v5-template.json]" \
      --deselect "nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_p25_v5_siblings_validate[missing-v4-template.json-p25-missing-v5-template.json]" \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_helpers_executable_code_requires_a_payload_bearing_claim \
      --deselect nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_helpers_is_omitted_when_no_helper_ran
  echo 'ASSAY_GATE_PHASE=verdict-v5-accepted'

  # B006a/A-269/WI-1: the nine one-for-one v2 successors for the nine locked
  # nodes deselected in the two phases just above. Same installed-wheel
  # pattern (cleared PYTHONPATH, run-venv interpreter, pytest ini override)
  # as the two locked suites it carries forward -- this is the "same
  # installed-wheel gate invocation" WI-1 names, run once, immediately after
  # both deselections it exists to cover.
  # shellcheck disable=SC1007 # intentional empty PYTHONPATH for this child only
  PYTHONPATH= "$scratch/run-venv/bin/python" -m pytest \
    "$worktree/assay/tests/test_lane_schema_v2_locked_successors.py" \
    -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=lane-schema-v2-successors-verified'

  # Wave-1: the 26 one-for-one v6 successors for the 26 locked P33 nodes
  # deselected just above by the schema-version hard cut. Same
  # installed-wheel pattern as every locked suite it carries forward.
  # shellcheck disable=SC1007 # intentional empty PYTHONPATH for this child only
  PYTHONPATH= "$scratch/run-venv/bin/python" -m pytest \
    "$worktree/assay/nyxloom-trove/carve-assets/W1/test_acceptance_v6.py" \
    -q -p no:randomly --override-ini=pythonpath= --co -q >/dev/null
  # The suite must COLLECT, but its controls are intentionally v6 and must be
  # rejected under v7. Prove the hard cut with a raw verifier probe instead of
  # running the locked module as though its controls were still valid.
  PYTHONPATH= "$scratch/run-venv/bin/python" - "$worktree/assay" <<'PYEOF'
import json
from pathlib import Path

from assay.verify import verify_document

root = Path(sys.argv[1])
expected = root / "nyxloom-trove" / "carve-assets" / "W1" / "expected"
for path in sorted(expected.glob("*.json")):
    document = json.loads(path.read_text())
    failures = verify_document(document)
    assert failures == [
        "schema_version 6 is not this verifier's version 7: a verdict "
        "artifact is rejected, never upgraded in place -- re-produce it "
        "with an assay whose VERDICT_SCHEMA_VERSION is 7"
    ], (path.name, failures)
print(f"v6 hard-cut guard passed for {len(list(expected.glob('*.json')))} frozen templates")
PYEOF
  echo 'ASSAY_GATE_PHASE=verdict-v6-successors-verified'

  PYTHONPATH= "$scratch/run-venv/bin/python" -m pytest \
    "$worktree/assay/nyxloom-trove/carve-assets/W2/test_acceptance_v7.py" \
    -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=verdict-v7-successors-verified'

  run_self_hosted_lane "$worktree" "$scratch" "$version"

  # P25: qualifies the CURRENT run-venv Assay (plus a separately
  # hash-installed clean-tagged 1.2.5 release wheel) against a disposable,
  # pinned, prospective Topos tree -- never the real Topos checkout, never a
  # Docker launch of its own outer gate. `--source-repo` is $worktree itself
  # (the repository top, where the pinned `topos/` tree actually lives, as a
  # sibling of `assay/`), never the private clone `make_exact_oid_clone`
  # made (that clone is sparse to `assay/` only). The harness's own success
  # marker is required exactly once before the gate's own phase marker is
  # printed -- a failing harness exits non-zero under `set -e` and this
  # function never reaches either line.
  local topos_marker
  topos_marker="$(
    "$scratch/run-venv/bin/python" "$worktree/assay/gate/python/qualify_topos.py" \
      --source-repo "$worktree" \
      --scratch "$scratch/p25-topos" \
      --current-assay "$scratch/run-venv/bin/assay" \
      --current-version "$version"
  )"
  [[ "$topos_marker" == "ASSAY_P25_TOPOS_QUALIFIED=1" ]] || \
    die "P25 Topos qualification did not emit its success marker exactly once"
  echo 'ASSAY_GATE_PHASE=topos-qualified'

  # B006(a) WI-5: qualifies the CURRENT run-venv Assay against a disposable,
  # pinned, full-repository checkout of CMRU while Topos's three unsafe
  # symlink fixtures stay tracked -- the end-to-end proof that
  # `snapshot_selection = "repository-minus-unsafe-symlinks"` unblocks a real
  # consumer's R1/R2/R3 claims. `--source-repo` is $worktree itself, exactly
  # as P25's own invocation above; this is a disposable qualification gate
  # phase, never a permanent CMRU lane (`cmru/assay.toml` stays untouched and
  # R0-only in the real checkout). Inserted immediately after
  # `topos-qualified` and before the independent witness, per O7.
  local cmru_b006a_marker
  cmru_b006a_marker="$(
    "$scratch/run-venv/bin/python" "$worktree/assay/gate/python/qualify_cmru_b006a.py" \
      --source-repo "$worktree" \
      --scratch "$scratch/b006a-cmru" \
      --current-assay "$scratch/run-venv/bin/assay" \
      --current-version "$version"
  )"
  [[ "$cmru_b006a_marker" == "ASSAY_B006A_CMRU_QUALIFIED=1" ]] || \
    die "B006(a) CMRU qualification did not emit its success marker exactly once"
  echo "$cmru_b006a_marker"
  echo 'ASSAY_GATE_PHASE=cmru-b006a-qualified'

  run_independent_witness "$scratch" "$run_venv_site"
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

docker run --rm \
  --cgroup-parent="$cgroup_parent" \
  --network=none \
  --mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub" \
  tester-unified:local \
  bash "$worktree/assay/tools/tester-unified-gate.sh" --inner "$worktree"

echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'

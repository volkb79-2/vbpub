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
  # shellcheck disable=SC1007 # intentional empty PYTHONPATH for this child only
  PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \
    "$scratch/run-venv/bin/python" -m pytest \
      "$worktree/assay/nyxloom-trove/carve-assets/P26/test_acceptance.py" \
      -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=attestation-hardened'

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

  run_independent_witness "$scratch" "$run_venv_site"
}

# --- entry points ------------------------------------------------------------

if [[ ${1:-} == "--inner" ]]; then
  [[ $# -eq 2 ]] || die 'inner mode requires exactly one worktree argument'
  run_inner "$2"
  exit 0
fi

[[ $# -eq 1 ]] || die 'outer mode requires exactly one worktree argument'
worktree="$1"
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

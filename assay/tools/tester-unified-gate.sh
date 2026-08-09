#!/usr/bin/env bash
# Registered Assay gate driver. The outer mode derives the host bind source,
# verifies the configured background cgroup through cgroup-parent.sh, launches
# tester-unified, and emits the final receipt marker only after Docker returns
# zero. The inner mode is invoked only inside that container.

set -euo pipefail

die() { printf 'tester-unified-gate: %s\n' "$*" >&2; exit 1; }

validate_worktree() {
  case "$1" in
    /workspaces/vbpub|/workspaces/vbpub/.worktrees/*) ;;
    *) die "worktree $1 is outside /workspaces/vbpub" ;;
  esac
}

run_inner() {
  local worktree="$1"
  validate_worktree "$worktree"
  cd "$worktree/assay"

  local scratch base_prefix tester_site venv_site setuptools_home wheel
  local -a wheels
  scratch="$(mktemp -d)"
  base_prefix="$(/opt/tester-venv/bin/python -c 'import sys; print(sys.base_prefix)')"
  "$base_prefix/bin/python3" -m venv "$scratch/venv"
  tester_site="$(/opt/tester-venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  venv_site="$("$scratch/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  printf '%s\n' "$tester_site" > "$venv_site/tester_unified_site.pth"
  setuptools_home="$(/opt/tester-venv/bin/python -c 'import setuptools, pathlib; print(pathlib.Path(setuptools.__file__).parent.parent)' 2>/dev/null)" || \
    setuptools_home="$("$base_prefix/bin/python3" -c 'import setuptools, pathlib; print(pathlib.Path(setuptools.__file__).parent.parent)')"

  PYTHONPATH="$setuptools_home" "$scratch/venv/bin/python" -m pip wheel \
    --no-build-isolation --no-deps --wheel-dir "$scratch/wheels" .
  shopt -s nullglob
  wheels=("$scratch"/wheels/assay-*.whl)
  [[ ${#wheels[@]} -eq 1 ]] || die "expected exactly one Assay wheel, found ${#wheels[@]}"
  wheel="${wheels[0]}"
  "$scratch/venv/bin/python" -m pip install --no-index "$wheel"
  echo 'ASSAY_GATE_PHASE=wheel-installed'

  export PATH="$scratch/venv/bin:$PATH"
  if ! assay run tester-unified --verdict-json "$scratch/verdict.json"; then
    echo 'ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; rerunning its command for visible diagnostics' >&2
    python -m pytest tests -q --ignore=tests/test_self_hosting.py \
      --override-ini=pythonpath= || true
    return 1
  fi
  echo 'ASSAY_GATE_PHASE=self-hosted-lane-passed'

  PYTHONPATH="$venv_site" ASSAY_SELF_HOSTING_VERDICT="$scratch/verdict.json" \
    /opt/tester-venv/bin/python -m pytest tests/test_self_hosting.py -q \
      --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=independent-self-hosting-passed'
}

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
  --mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub" \
  tester-unified:local \
  bash "$worktree/assay/tools/tester-unified-gate.sh" --inner "$worktree"

echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'

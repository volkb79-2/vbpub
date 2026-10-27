"""W0-TESTER: tester-unified image inputs (KI-52(b), BG-05).

The image is not built here (no docker builds). What is testable offline is the
requirements generator, run against synthetic trees and against the real
estate trees, and the Dockerfile / .dockerignore text that carries the policy.
"""
from __future__ import annotations

import importlib.util
import fnmatch
import hashlib
import json
import posixpath
import os
import re
import shlex
import subprocess
import sys
import textwrap
import tomllib
from functools import lru_cache
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tester-unified" / "gen-requirements.py"
# The real Dockerfile's final stage. Stage 0 is the digest-pinned `node-source` stage (MDTC-02 D-004),
# so the tester image is stage 1; fixtures below that spell "stage-0" are self-contained.
TESTER_STAGE = "stage-1"
DOCKERFILE = REPO_ROOT / "tester-unified" / "Dockerfile"
RUN_GATE_WHEEL_VERSION_HELPER = REPO_ROOT / "tester-unified" / "run-gate-wheel-version.py"
NYXLOOM_DOCKERFILE = REPO_ROOT / "nyxloom" / "nyxloomd" / "Dockerfile"
CMRU_WHEEL_BUILD_COMMAND = (
    r"/tmp/internal-build-venv/bin/pip wheel[\s\S]*?/src/cmru"
)
CMRU_WHEEL_BUILD_BODY = (
    "rm -rf /tmp/internal-wheels && mkdir -p /tmp/internal-wheels && "
    "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified "
    "/tmp/internal-build-venv/bin/pip wheel --no-index --no-deps "
    "--no-build-isolation -w /tmp/internal-wheels /src/cmru"
)
# Host-facing tools, installed by capability: Debian's docker client/compose
# packages are added only when the base lacks them (the MDT base ships Docker's
# own, which they would clash with). It runs before any wheel input is copied.
TESTER_HOST_TOOLS_RUN = (
    'set -eu && packages="ca-certificates curl git jq openssh-client util-linux" '
    '&& if ! command -v docker >/dev/null 2>&1; then packages="$packages docker-cli"; fi '
    "&& apt-get update && apt-get install -y --no-install-recommends $packages "
    "&& rm -rf /var/lib/apt/lists/* && docker --version "
    "&& git --version && nsenter --version"
)
# Docker's OWN docker-compose-plugin at an exact version, checked against a pinned sha256 per
# architecture (INT-01 D-006); never Debian's unpinned `docker-compose`. Runs before any wheel input.
TESTER_COMPOSE_RUN = (
    "set -eu; if ! docker compose version >/dev/null 2>&1; then "
    'case "$(dpkg --print-architecture)" in '
    'amd64) arch=amd64; sum="${DOCKER_COMPOSE_PLUGIN_SHA256_AMD64}" ;; '
    'arm64) arch=arm64; sum="${DOCKER_COMPOSE_PLUGIN_SHA256_ARM64}" ;; '
    '*) echo "unsupported architecture" >&2; exit 1 ;; esac; '
    'curl -fsSL -o /tmp/docker-compose-plugin.deb '
    '"https://download.docker.com/linux/debian/dists/trixie/pool/stable/${arch}/'
    'docker-compose-plugin_${DOCKER_COMPOSE_PLUGIN_VERSION}_${arch}.deb"; '
    'echo "${sum}  /tmp/docker-compose-plugin.deb" | sha256sum -c -; '
    "dpkg -i /tmp/docker-compose-plugin.deb; rm -f /tmp/docker-compose-plugin.deb; fi; "
    "docker compose version"
)
# Records the exact Run-Gate wheel identity in the image (readable by the release
# lane). It writes only /etc and runs before any CMRU wheel input is copied.
RUN_GATE_IDENTITY_FILE_RUN = (
    "printf '{\"version\":\"%s\",\"sha256\":\"%s\"}\\n' "
    '"${RUN_GATE_TESTER_VERSION}" "${RUN_GATE_TESTER_WHEEL_SHA256}" '
    "> /etc/tester-unified-run-gate-wheel.json "
    "&& chmod 0444 /etc/tester-unified-run-gate-wheel.json"
)
TESTER_GIT_MAINTENANCE_RUN = (
    "git config --system maintenance.autoDetach false && git config --system gc.autoDetach false "
    '&& test "$(git config --system --get maintenance.autoDetach)" = false '
    '&& test "$(git config --system --get gc.autoDetach)" = false'
)
RUN_GATE_VERSION_REQUIREMENT_RUN = (
    'test -n "${RUN_GATE_TESTER_VERSION}" && '
    'test -n "${RUN_GATE_TESTER_WHEEL_SHA256}" || { '
    'echo "Run-Gate version and wheel SHA-256 are required; use '
    'tester-unified/build-image.sh" >&2; exit 1; }'
)
RUN_GATE_WHEEL_INSTALL_RUN = (
    'set -eu; set -- /tmp/run-gate-wheels/run_gate-*.whl; '
    'if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then echo '
    '"named run-gate-wheel context must contain exactly one run_gate-*.whl" '
    '>&2; exit 1; fi; printf \'%s  %s\\n\' '
    '"${RUN_GATE_TESTER_WHEEL_SHA256}" "$1" | sha256sum -c -; '
    '/opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "$1"; '
    'rm -rf /tmp/run-gate-wheels /src/run-gate-project'
)
TESTER_REQUIREMENTS_GENERATION_RUN = (
    "/usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py "
    "--root /src > /tmp/tester-requirements.txt && "
    "/usr/local/bin/python3.14 /usr/local/lib/tester-unified/gen-requirements.py "
    "--root /src --build-requires cmru/pyproject.toml "
    "> /tmp/internal-build-requirements.txt && "
    'case "${CLI_EXTENDED_RESOLVE}" in pinned) set -- --wheel-url '
    '"${CLI_EXTENDED_WHEEL_URL}" --sha256 "${CLI_EXTENDED_WHEEL_SHA256}" ;; '
    'latest) set -- --pointer-url "${CLI_EXTENDED_POINTER_URL}" '
    '--expect-sha256 "${CLI_EXTENDED_WHEEL_SHA256}" ;; local) set -- ;; '
    '*) echo "CLI_EXTENDED_RESOLVE must be pinned, latest or local" >&2; exit 1 ;; '
    'esac && if [ "${CLI_EXTENDED_RESOLVE}" = local ]; then '
    'set -- /tmp/cli-extended-local/cli_extended-*.whl; '
    'if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then echo '
    '"CLI_EXTENDED_RESOLVE=local needs exactly one cli_extended-*.whl in '
    'tester-unified/local-wheel" >&2; exit 1; fi; '
    'echo "${CLI_EXTENDED_WHEEL_SHA256}  $1" | sha256sum -c - || '
    '{ echo "local wheel does not match --build-arg CLI_EXTENDED_WHEEL_SHA256" '
    '>&2; exit 1; }; mkdir -p /tmp/cli-extended-release && cp "$1" '
    '/tmp/cli-extended-release/; else '
    '/usr/local/bin/python3.14 /usr/local/lib/tester-unified/fetch-cli-extended.py '
    '--dest /tmp/cli-extended-release --min-version 0.3.0 "$@"; fi && '
    'set -- /tmp/cli-extended-release/cli_extended-*.whl && [ "$#" -eq 1 ] && '
    '[ -f "$1" ] && echo "${CLI_EXTENDED_WHEEL_SHA256}  $1" | sha256sum -c -'
)
TESTER_DEPENDENCY_BOOTSTRAP_RUN = (
    "/usr/local/bin/python3.14 -m venv --copies /opt/tester-venv && "
    '/opt/tester-venv/bin/pip install --no-cache-dir --uploaded-prior-to '
    '"${PIP_MIN_RELEASE_AGE}" --upgrade pip && '
    '/opt/tester-venv/bin/pip install --no-cache-dir --uploaded-prior-to '
    '"${PIP_MIN_RELEASE_AGE}" -r /tmp/tester-requirements.txt && '
    "/opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "
    "/tmp/cli-extended-release/*.whl && /usr/local/bin/python3.14 -m venv "
    "/tmp/internal-build-venv && /tmp/internal-build-venv/bin/pip install "
    '--no-cache-dir --uploaded-prior-to "${PIP_MIN_RELEASE_AGE}" '
    "-r /tmp/internal-build-requirements.txt"
)
CMRU_SOURCE_TREE_CLEAN_RUN = (
    "rm -rf /src/cmru /src/libraries/worktree /src/run-gate-project"
)
CMRU_VENV_CLEAN_RUN = "rm -rf /opt/tester-venv /tmp/internal-build-venv"
CMRU_WHEEL_INSTALL_PREFIX = (
    "/opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "
    "/tmp/internal-wheels/*.whl"
)
CMRU_WHEEL_INSTALL_RUN_BODY = (
    CMRU_WHEEL_INSTALL_PREFIX
    + " && rm -rf /tmp/internal-build-venv /tmp/internal-wheels "
    "/tmp/internal-build-requirements.txt /tmp/cli-extended-release"
    + ' && /opt/tester-venv/bin/python -c "import build, cmru, run_gate, pytest, pytest_cov, '
    "xdist, hypothesis, jsonschema, yaml, jinja2, tomli_w, textual, zstandard, mcp, "
    "paramiko, coverage, structlog, markdown_it, linkify_it; "
    "print('tester-unified closure OK')\""
    + ' && /opt/tester-venv/bin/python -c "import pandas, plotly, matplotlib, '
    "scipy, numpy, ruptures; print('cgroup-profiler report tier OK')\""
)
CMRU_POST_INSTALL_VERSION_ASSERTION_RUN = r'''RUN /opt/tester-venv/bin/python - <<'PY'
import importlib.metadata as metadata
import os
import subprocess
import sys
from pathlib import Path

import cli_extended
import cmru
import run_gate
import worktree

site = "/opt/tester-venv/"
for module in (cli_extended, cmru, run_gate, worktree):
    location = str(Path(module.__file__).resolve())
    assert location.startswith(site), f"{module.__name__} imports from {location}, not {site}"
version = metadata.version("cmru")
assert version.endswith("+tester.unified"), (
    f"cmru {version} was not built from the COPYed source in this image"
)
run_gate_version = metadata.version("run-gate")
expected_run_gate_version = (
    os.environ["RUN_GATE_TESTER_VERSION"]
)
assert run_gate_version == expected_run_gate_version, (
    f"Run-Gate wheel {run_gate_version} does not match the requested image version "
    f"{expected_run_gate_version}"
)
run_gate_location = str(Path(run_gate.__file__).resolve())
assert run_gate_location.startswith(site), (
    f"run_gate imports from {run_gate_location}, not {site}"
)
entrypoint = Path(sys.executable).with_name("run-gate")
reported = subprocess.run([str(entrypoint), "--version"], check=True,
                          capture_output=True, text=True).stdout
assert reported == f"run-gate {run_gate_version}\n", reported
released = metadata.version("cli-extended")
if os.environ["CLI_EXTENDED_RESOLVE"] == "local":
    # Zero-release mode: the operator-built wheel (sha256-verified above) carries a
    # PEP 440 local version segment so it cannot be mistaken for a stable release.
    assert "+" in released, f"cli-extended {released} has no local version segment"
else:
    assert "+" not in released, f"cli-extended {released} is a local build, not the release"
assert any(r.startswith("cli-extended") for r in metadata.requires("cmru") or ()), (
    "cmru's wheel does not declare cli-extended as a dependency"
)
print("cmru local, Run-Gate installed:", run_gate_version,
      "; cli-extended identity:", released)
PY'''
# The run-uid identity block as the Dockerfile declares it (da06e52de gave the
# uid its own group 1003 instead of squatting on the host's docker gid 994),
# joined the way the Dockerfile reader joins continuation lines.
CMRU_POST_INSTALL_IDENTITY_RUN = (
    'RUN if getent group 1003 >/dev/null; then '
    'test "$(getent group 1003 | cut -d: -f1)" != docker; '
    'else groupadd --gid 1003 tester; fi '
    '&& ! getent passwd 1003 >/dev/null && ! getent passwd tester >/dev/null '
    '&& echo "tester:x:1003:1003:tester:/home/tester:/bin/bash" >> /etc/passwd '
    '&& mkdir -p /home/tester/.config /home/tester/.cache /home/tester/.local/state '
    '&& chown -R 1003:1003 /home/tester && chown -R 1003:1003 /opt/tester-venv'
)
CMRU_POST_INSTALL_MUTATIONS = (
    CMRU_POST_INSTALL_VERSION_ASSERTION_RUN,
    CMRU_POST_INSTALL_IDENTITY_RUN,
    "RUN run-gate --version",
)
NYXLOOM_WHEEL_BUILD_BODY = (
    "python -m build --wheel --outdir /wheels && ls -1 /wheels"
)
NYXLOOM_CLI_EXTENDED_WHEEL_BUILD_BODY = (
    "rm -rf /wheels && mkdir -p /wheels && "
    + NYXLOOM_WHEEL_BUILD_BODY
)
NYXLOOM_FINAL_WHEELS_CLEAN_RUN = (
    "rm -rf /opt/nyxloom-venv /tmp/wheels && mkdir -p /tmp/wheels"
)
NYXLOOM_FINAL_WHEEL_INSTALL_RUN_BODY = (
    "python3 -m venv --copies /opt/nyxloom-venv && "
    "/opt/nyxloom-venv/bin/pip install --no-cache-dir --no-index --no-deps "
    "/tmp/wheels/cli_extended-*.whl && "
    "/opt/nyxloom-venv/bin/pip install --no-cache-dir "
    "/tmp/wheels/nyxloom-*.whl && "
    "/opt/nyxloom-venv/bin/python -c \"import yaml, jsonschema, structlog, "
    "cli_extended, nyxloom; print('nyxloom', nyxloom.__version__, "
    "'self-contained OK')\" && rm -rf /tmp/wheels"
)
# Userland the suites and gate scripts execute (MDTC-02 / INT-01): zsh, the real systemctl client and
# the npm/npx links beside the node binary copied from the digest-pinned node stage. None of them
# touches a CMRU wheel input, and they run before any of those inputs are copied.
TESTER_ZSH_RUN = (
    "apt-get update && apt-get install -y --no-install-recommends zsh "
    "&& rm -rf /var/lib/apt/lists/*"
)
TESTER_SYSTEMCTL_RUN = (
    "apt-get update && apt-get install -y --no-install-recommends systemd "
    "&& rm -rf /var/lib/apt/lists/* "
    '&& test "$(dpkg -S /usr/bin/systemctl | cut -d: -f1)" = systemd '
    "&& /bin/systemctl --version"
)
TESTER_NODE_LINKS_RUN = (
    "ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm "
    "&& ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx "
    "&& node --version && npm --version && npx --version"
)
CMRU_PRE_SENSITIVE_RUN_BODIES = (
    TESTER_HOST_TOOLS_RUN,
    TESTER_COMPOSE_RUN,
    TESTER_ZSH_RUN,
    TESTER_SYSTEMCTL_RUN,
    TESTER_NODE_LINKS_RUN,
    CMRU_SOURCE_TREE_CLEAN_RUN,
    CMRU_VENV_CLEAN_RUN,
    TESTER_GIT_MAINTENANCE_RUN,
    RUN_GATE_VERSION_REQUIREMENT_RUN,
    RUN_GATE_IDENTITY_FILE_RUN,
    RUN_GATE_WHEEL_INSTALL_RUN,
    TESTER_REQUIREMENTS_GENERATION_RUN,
    TESTER_DEPENDENCY_BOOTSTRAP_RUN,
)
NYXLOOM_BUILD_TOOL_RUN = "pip install --no-cache-dir build==1.2.2"
NYXLOOM_CLI_EXTENDED_SOURCE_CLEAN_RUN = "rm -rf /build/libraries/cli-extended"
NYXLOOM_SOURCE_CLEAN_RUN = "rm -rf /build/nyxloom"
NYXLOOM_CLI_EXTENDED_BUILD_RUN = NYXLOOM_CLI_EXTENDED_WHEEL_BUILD_BODY


def _load():
    spec = importlib.util.spec_from_file_location("gen_requirements", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The assay canary runs the suite from an isolated copy of ``cmru/`` alone, with
# no repository-root files; these tests judge those files, so they only run
# where the repository tree is present (the coverage lane and the cockpit).
pytestmark = pytest.mark.skipif(
    not GENERATOR.exists(), reason="repository-root tester-unified files absent (isolated canary tree)",
)
gen = _load() if GENERATOR.exists() else None


def _tree(root: Path, **projects: str) -> Path:
    """Write ``<root>/<relative>/pyproject.toml`` for every project."""
    for relative, body in projects.items():
        path = root / relative.replace("__", "/")
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def _all_projects(root: Path, **overrides: str) -> Path:
    base = {
        "ciu": '[project]\nname = "ciu"\ndependencies = ["Jinja2>=3"]\n'
               '[project.optional-dependencies]\nssh = ["paramiko>=5"]\ntest = ["pytest>=8"]\n',
        "cmru": '[project]\nname = "cmru"\ndependencies = []\n'
                '[project.optional-dependencies]\ntest = ["pytest-cov>=5"]\n'
                '[build-system]\nrequires = ["setuptools==1", "setuptools_scm==2"]\n',
        "assay": '[project]\nname = "assay"\ndependencies = []\n'
                 '[project.optional-dependencies]\ntest = ["hypothesis>=6"]\n'
                 '[build-system]\nrequires = ["setuptools==9"]\n',
        "topos": '[project]\nname = "topos"\ndependencies = ["textual>=8"]\n'
                 '[project.optional-dependencies]\nzstandard = ["zstandard>=0.22"]\n'
                 'dev = ["pytest>=8", "topos[zstandard]"]\n',
        "nyxloom": '[project]\nname = "nyxloom"\ndependencies = ["PyYAML>=6"]\n'
                   '[project.optional-dependencies]\ntest = ["coverage>=7"]\n',
        "cgroup-profiler": '[project]\nname = "cgroup-profiler"\ndependencies = []\n'
                           '[project.optional-dependencies]\ntest = ["numpy>=2"]\n',
        "pwmcp__client": '[project]\nname = "pwmcp-client"\ndependencies = ["playwright==1"]\n'
                         '[project.optional-dependencies]\ntest = ["pillow>=10"]\n',
        "libraries__cli-extended": '[project]\nname = "cli-extended"\ndependencies = []\n'
                                   '[build-system]\nrequires = ["setuptools==1", "wheel==3"]\n',
    }
    base.update({key.replace("/", "__"): value for key, value in overrides.items()})
    return _tree(root, **base)


def test_generator_emits_the_third_party_closure_and_expands_self_extras(tmp_path):
    _all_projects(tmp_path)
    produced = list(gen.requirements(tmp_path))
    assert "Jinja2>=3" in produced and "paramiko>=5" in produced
    assert "zstandard>=0.22" in produced  # topos[dev] -> topos[zstandard] expanded
    assert not any(item.startswith("topos[") for item in produced)  # never handed to pip
    assert "setuptools==9" in produced  # assay's build backend (no-build-isolation lanes)
    assert "setuptools==1" not in produced  # cmru's pins go to the throwaway build venv
    assert produced[-1] == "build"


@pytest.mark.parametrize(
    "bad",
    ["Worktree", "cmru==1", "assay", "ciu>=1",
     "nyxloom", "topos[dev]>=1", "CGroup.Profiler", "run_gate"],
)
def test_generator_refuses_estate_internal_names_in_dependencies(tmp_path, bad):
    """BG-05: a PyPI-default pip must never be asked for an estate-internal
    name (unclaimed `cli-extended`, unrelated look-alike `worktree`)."""
    _all_projects(tmp_path, cmru=(
        '[project]\nname = "cmru"\n'
        f'dependencies = ["{bad}"]\n'
    ))
    with pytest.raises(SystemExit, match="estate-internal"):
        list(gen.requirements(tmp_path))


def test_generator_refuses_internal_names_in_extras_and_build_requires(tmp_path):
    _all_projects(tmp_path, ciu=(
        '[project]\nname = "ciu"\ndependencies = []\n'
        '[project.optional-dependencies]\nssh = []\ntest = ["worktree>=1"]\n'
    ))
    with pytest.raises(SystemExit, match="'worktree'"):
        list(gen.requirements(tmp_path))
    _all_projects(tmp_path, assay=(
        '[project]\nname = "assay"\ndependencies = []\n'
        '[project.optional-dependencies]\ntest = []\n'
        '[build-system]\nrequires = ["ciu"]\n'
    ))
    with pytest.raises(SystemExit, match="'ciu'"):
        list(gen.requirements(tmp_path))
    other = _tree(tmp_path / "x", cmru='[build-system]\nrequires = ["cmru"]\n')
    with pytest.raises(SystemExit, match="'cmru'"):
        list(gen.build_requirements(other, ["cmru/pyproject.toml"]))


@pytest.mark.parametrize("line", [
    "cli-extended>=0.2.0", "cli_extended", "CLI.Extended==0.2.0", "cli-extended[x]>=0.2",
])
def test_generator_skips_every_cli_extended_line_so_none_reaches_pip(tmp_path, line, capsys):
    """D8: cmru declares cli-extended as a real dependency. The line must be
    SKIPPED (not refused, not printed): the image installs a verified release
    wheel by default or an explicit local bootstrap wheel, and a PyPI-default
    pip is never asked for the name."""
    _all_projects(
        tmp_path,
        cmru=(
            f'[project]\nname = "cmru"\ndependencies = ["{line}", "Jinja2>=3"]\n'
            f'[project.optional-dependencies]\ntest = ["pytest-cov>=5", "{line}"]\n'
            f'[build-system]\nrequires = ["setuptools==1", "{line}"]\n'
        ),
        assay=(
            '[project]\nname = "assay"\ndependencies = []\n'
            '[project.optional-dependencies]\ntest = ["hypothesis>=6"]\n'
            f'[build-system]\nrequires = ["setuptools==9", "{line}"]\n'
        ),
    )
    produced = list(gen.requirements(tmp_path))
    assert "Jinja2>=3" in produced and "pytest-cov>=5" in produced  # neighbours survive
    assert not [item for item in produced if "extended" in item.lower()]
    assert gen.main(["--root", str(tmp_path)]) == 0
    assert "extended" not in capsys.readouterr().out.lower()
    built = list(gen.build_requirements(tmp_path, ["cmru/pyproject.toml"]))
    assert built == ["setuptools==1"]


def test_generator_still_refuses_other_internal_names_next_to_a_cli_extended_line(tmp_path):
    _all_projects(tmp_path, cmru=(
        '[project]\nname = "cmru"\ndependencies = ["cli-extended>=0.2.0", "worktree>=1"]\n'
    ))
    with pytest.raises(SystemExit, match="'worktree'"):
        list(gen.requirements(tmp_path))


def test_generator_refuses_direct_url_requirements(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\n'
        'dependencies = ["somepkg @ https://example.invalid/somepkg.whl"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    with pytest.raises(SystemExit, match="direct-URL"):
        list(gen.requirements(tmp_path))


def test_generator_build_requires_mode_prints_only_the_build_backends(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path), "--build-requires",
                     "cmru/pyproject.toml", "libraries/cli-extended/pyproject.toml"]) == 0
    assert capsys.readouterr().out.splitlines() == ["setuptools==1", "setuptools_scm==2", "wheel==3"]


def test_generator_deduplicates_and_prints_one_requirement_per_line(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(set(lines)) and lines[-1] == "build"


def test_real_estate_trees_have_no_internal_requirement_in_the_pypi_bound_closure(tmp_path):
    """The generator against the REAL pyprojects, laid out as the Dockerfile
    COPYs them: cmru now declares ``cli-extended`` (KI-51), the closure must
    still be accepted, and no cli-extended line may be in it."""
    for name in ("ciu", "cmru", "assay", "topos", "nyxloom"):
        (tmp_path / name).symlink_to(REPO_ROOT / name)
    (tmp_path / "cgroup-profiler").symlink_to(REPO_ROOT / "scripts" / "cgroup-profiler")
    (tmp_path / "pwmcp").mkdir()
    (tmp_path / "pwmcp" / "client").symlink_to(REPO_ROOT / "pwmcp" / "client")
    (tmp_path / "libraries").mkdir()
    (tmp_path / "libraries" / "cli-extended").symlink_to(REPO_ROOT / "libraries" / "cli-extended")
    closure = list(gen.requirements(tmp_path))
    names = {gen.requirement_name(item) for item in closure}
    assert not names & gen.ESTATE_INTERNAL
    assert {"pytest", "jinja2", "textual"} <= names
    assert "markdown-it-py" in names
    build = list(gen.build_requirements(tmp_path, ["cmru/pyproject.toml"]))
    assert any(item.startswith("setuptools") for item in build)
    # The real cmru pyproject DOES declare it; that is what the skip is for.
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    assert any(gen.requirement_name(item) == "cli-extended"
               for item in declared["project"]["dependencies"])


# --- Dockerfile / .dockerignore policy text (the image itself is not built here)

def _dockerfile_run_lines() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _dockerfile_escape_character(text: str) -> str:
    """Read Docker's top-of-file escape directive, defaulting to backslash."""
    for physical in text.splitlines():
        line = physical.strip()
        if not line:
            break
        if not line.startswith("#"):
            break
        if re.match(r"(?i)^#\s*escape\s*=", line):
            match = re.fullmatch(r"(?i)#\s*escape\s*=\s*([`\\])\s*", line)
            if match is None:
                raise ValueError(f"unsupported Dockerfile escape directive: {line}")
            return match.group(1)
    return "\\"


def _dockerfile_logical_instructions(
    text: str,
    *,
    include_heredoc_bodies: bool = False,
) -> list[str]:
    """Join instructions, optionally retain heredocs, and discard comments."""
    escape = _dockerfile_escape_character(text)
    instructions = []
    pending = ""
    physical_lines = text.splitlines()
    index = 0
    while index < len(physical_lines):
        physical = physical_lines[index]
        index += 1
        line = physical.strip()
        # Docker strips comment-only lines before joining continuations, even
        # when the previous physical line ended with the escape character.
        if not line or line.startswith("#"):
            continue
        if line.endswith(escape):
            pending += line[:-len(escape)].rstrip() + " "
            continue
        pending += line
        if pending.strip():
            instruction = pending.strip()
            heredoc_bodies = []
            for delimiter, strip_leading_tabs in _dockerfile_heredoc_delimiters(
                instruction,
            ):
                found_delimiter = False
                body_lines = []
                while index < len(physical_lines):
                    body_line = physical_lines[index].rstrip("\r")
                    index += 1
                    candidate = (
                        body_line.lstrip("\t")
                        if strip_leading_tabs
                        else body_line
                    )
                    if candidate == delimiter:
                        found_delimiter = True
                        break
                    body_lines.append(body_line)
                if not found_delimiter:
                    raise ValueError(
                        f"Dockerfile heredoc delimiter is missing: {delimiter}"
                    )
                if include_heredoc_bodies:
                    heredoc_bodies.extend((*body_lines, delimiter))
            if heredoc_bodies:
                instruction += "\n" + "\n".join(heredoc_bodies)
            instructions.append(instruction)
        pending = ""
    if pending.strip():
        instructions.append(pending.strip())
    return instructions


def _dockerfile_heredoc_delimiters(
    instruction: str,
) -> list[tuple[str, bool]]:
    """Read shell heredoc delimiters without treating quoted `<<` as syntax."""
    delimiters = []
    quote = None
    escaped = False
    index = 0
    while index < len(instruction):
        char = instruction[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote is not None:
            if char == "\\" and quote != "'":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if instruction.startswith("<<<", index):
            index += 3
            continue
        if not instruction.startswith("<<", index):
            index += 1
            continue

        index += 2
        strip_leading_tabs = index < len(instruction) and instruction[index] == "-"
        if strip_leading_tabs:
            index += 1
        while index < len(instruction) and instruction[index].isspace():
            index += 1
        delimiter_chars = []
        delimiter_quote = None
        while index < len(instruction):
            char = instruction[index]
            if delimiter_quote is not None:
                if char == "\\" and delimiter_quote != "'" and index + 1 < len(instruction):
                    delimiter_chars.append(instruction[index + 1])
                    index += 2
                    continue
                if char == delimiter_quote:
                    delimiter_quote = None
                    index += 1
                    continue
                delimiter_chars.append(char)
                index += 1
                continue
            if char in "'\"":
                delimiter_quote = char
                index += 1
                continue
            if char == "\\" and index + 1 < len(instruction):
                delimiter_chars.append(instruction[index + 1])
                index += 2
                continue
            if char.isspace() or char in ";&|<>" :
                break
            delimiter_chars.append(char)
            index += 1
        delimiter = "".join(delimiter_chars)
        if not delimiter or delimiter_quote is not None:
            raise ValueError(f"cannot parse Dockerfile heredoc delimiter: {instruction}")
        delimiters.append((delimiter, strip_leading_tabs))
    return delimiters


def _dockerfile_instructions_by_stage(
    text: str,
    *,
    include_heredoc_bodies: bool = False,
) -> list[tuple[int, str, int, str]]:
    """Retain ordinal identity, selector, and order for each stage instruction."""
    current_stage = None
    current_stage_ordinal = None
    stage_number = 0
    stage_index = 0
    result = []
    for instruction in _dockerfile_logical_instructions(
        text,
        include_heredoc_bodies=include_heredoc_bodies,
    ):
        if re.match(r"(?i)^FROM(?:[ \t]|$)", instruction):
            alias = re.search(r"(?i)\bAS[ \t]+([^\s]+)$", instruction)
            current_stage = (
                alias.group(1).lower() if alias else f"stage-{stage_number}"
            )
            current_stage_ordinal = stage_number
            stage_number += 1
            stage_index = 0
        if current_stage is not None and current_stage_ordinal is not None:
            result.append((current_stage_ordinal, current_stage, stage_index, instruction))
            stage_index += 1
    return result


def _dockerfile_from_base(instruction: str) -> str:
    parts = shlex.split(instruction)
    if not parts or parts[0].upper() != "FROM":
        raise ValueError(f"cannot parse Dockerfile stage base: {instruction}")
    base_index = 1
    while base_index < len(parts) and parts[base_index].startswith("--"):
        base_index += 1
    if base_index >= len(parts):
        raise ValueError(f"cannot parse Dockerfile stage base: {instruction}")
    return parts[base_index].lower()


def _dockerfile_global_arg_defaults(text: str) -> dict[str, str]:
    """Defaults of the ``ARG`` instructions declared before the first ``FROM``."""
    defaults: dict[str, str] = {}
    for instruction in _dockerfile_logical_instructions(text):
        if re.match(r"(?i)^FROM(?:[ \t]|$)", instruction):
            break
        match = re.match(r"(?i)^ARG[ \t]+([A-Za-z_][A-Za-z0-9_]*)=(.*)$", instruction)
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            defaults[match.group(1)] = value
    return defaults


def _expand_dockerfile_base(base: str, defaults: dict[str, str]) -> str:
    """Expand ``$NAME``/``${NAME}`` in a FROM base from declared global ARG defaults.

    A reference with no declared default stays unexpanded, so the caller still
    refuses to certify an unresolved base.
    """
    lowered = {name.lower(): value.lower() for name, value in defaults.items()}

    def replace(match: re.Match) -> str:
        name = (match.group(1) or match.group(2)).lower()
        return lowered.get(name, match.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", replace, base)


def _assert_no_mutating_instructions_after_install(
    instructions: list[tuple[int, str, int, str]],
    *,
    install_index: int,
    allowed_mutations: tuple[str, ...] = (),
) -> None:
    install_ordinal = instructions[install_index][0]
    if install_ordinal != max(ordinal for ordinal, _selector, _index, _text in instructions):
        raise ValueError("wheel installation is not in the final image stage")
    safe_declarative_instructions = {
        "ARG", "CMD", "ENTRYPOINT", "ENV", "EXPOSE", "HEALTHCHECK", "LABEL",
        "STOPSIGNAL", "USER",
    }
    for ordinal, _selector, _stage_index, instruction in instructions[install_index + 1:]:
        if ordinal != install_ordinal:
            continue
        opcode = instruction.split(None, 1)[0].upper()
        if opcode in safe_declarative_instructions or instruction in allowed_mutations:
            continue
        raise ValueError(
            f"unapproved {opcode} instruction follows the installed wheel"
        )


def _assert_dockerfile_wheel_build_output_is_installed(
    text: str,
    *,
    stage: str,
    build_run_body: str,
    install_run_body: str,
    allowed_post_install_mutations: tuple[str, ...] = (),
    require_final_image: bool = True,
) -> None:
    """Require exact wheel installation and guard the remaining image stage."""
    instructions = _dockerfile_instructions_by_stage(
        text,
        include_heredoc_bodies=True,
    )
    selected = [
        (index, instruction)
        for index, (_ordinal, selector, _stage_index, instruction)
        in enumerate(instructions)
        if selector == stage.lower()
        and re.match(r"(?i)^RUN(?:[ \t]|$)", instruction)
        and re.sub(r"(?i)^RUN[ \t]+", "", instruction, count=1) == build_run_body
    ]
    if len(selected) != 1:
        raise ValueError(
            f"expected one exact wheel build RUN in stage {stage!r}, found {len(selected)}"
        )
    build_index, _build_instruction = selected[0]
    next_index = build_index + 1
    if next_index >= len(instructions):
        raise ValueError("wheel build output is not consumed by the next instruction")
    build_ordinal = instructions[build_index][0]
    next_ordinal, _selector, _stage_index, next_instruction = instructions[next_index]
    if (
        next_ordinal != build_ordinal
        or not re.match(r"(?i)^RUN(?:[ \t]|$)", next_instruction)
    ):
        raise ValueError("wheel build output is not consumed by the next instruction")
    install_body = re.sub(r"(?i)^RUN[ \t]+", "", next_instruction, count=1)
    if install_body != install_run_body:
        raise ValueError("the next RUN does not exactly install the wheel build output")
    if require_final_image:
        _assert_no_mutating_instructions_after_install(
            instructions,
            install_index=next_index,
            allowed_mutations=allowed_post_install_mutations,
        )


def _dockerfile_copy_specs_before_command(
    text: str,
    stage: str,
    command_pattern: str,
    *,
    occurrence: int = 0,
    expected_workdir: str | None = None,
    run_sensitive_sources: tuple[str, ...] = (),
    allowed_pre_sensitive_run_bodies: tuple[str, ...] = (),
    allowed_external_contexts: tuple[str, ...] = (),
    allowed_pure_image_stages: tuple[str, ...] = (),
    allowed_shell_prefix: tuple[str, ...] = (),
    expected_run_body: str | None = None,
) -> list[tuple[PurePosixPath, str, tuple[str, ...]]]:
    """Resolved COPY specs before a RUN command in its active working directory."""
    instructions = _dockerfile_instructions_by_stage(text)
    selected_stage_ordinals = {
        ordinal for ordinal, selector, _index, _instruction in instructions
        if selector == stage.lower()
    }
    declared_stages = {
        selector.lower() for _ordinal, selector, _index, _instruction in instructions
    }
    if len(selected_stage_ordinals) > 1:
        raise ValueError(f"Dockerfile stage selector {stage!r} is ambiguous")
    selected_stage_ordinal = next(iter(selected_stage_ordinals), None)
    stage_from_instructions = {
        ordinal: instruction
        for ordinal, _selector, _index, instruction in instructions
        if re.match(r"(?i)^FROM(?:[ \t]|$)", instruction)
    }
    if run_sensitive_sources and selected_stage_ordinal is not None:
        from_instruction = stage_from_instructions[selected_stage_ordinal]
        base = _expand_dockerfile_base(
            _dockerfile_from_base(from_instruction),
            _dockerfile_global_arg_defaults(text),
        )
        prior_stage_selectors = {
            selector.lower()
            for ordinal, selector, _index, _instruction in instructions
            if ordinal < selected_stage_ordinal
        }
        if "$" in base or base in prior_stage_selectors:
            raise ValueError(
                "cannot certify wheel inputs inherited from a prior or unresolved stage"
            )
    # Docker starts each stage at `/` unless a WORKDIR instruction changes it.
    workdir = "/"
    matches_seen = 0
    specs = []
    run_sensitive_copy_seen = False
    run_sensitive_paths = {PurePosixPath(source) for source in run_sensitive_sources}
    for found_ordinal, _selector, _index, instruction in instructions:
        if found_ordinal != selected_stage_ordinal:
            continue
        workdir_match = re.match(r"(?i)^WORKDIR[ \t]+(.+)$", instruction)
        if workdir_match:
            parts = shlex.split(workdir_match.group(1))
            if len(parts) != 1:
                raise ValueError(f"cannot parse WORKDIR instruction: {instruction}")
            workdir = _resolve_docker_workdir(parts[0], workdir)
            # Docker creates the directory (and missing parents) named by each
            # WORKDIR. Include that filesystem effect in the ordered COPY model
            # so a later file COPY cannot occupy the same path.
            specs.append((PurePosixPath("__workdir__"), workdir, ()))
            continue
        if re.match(r"(?i)^COPY(?:[ \t]|$)", instruction):
            for source, destination, excludes in _copy_source_specs_from_instruction(
                instruction, include_stage=True,
            ):
                if run_sensitive_sources and source.parts[:1] == ("__stage_copy__",):
                    context_or_stage = source.parts[1] if len(source.parts) > 1 else ""
                    # A declared stage is refused (its content is unproven) unless it is named as
                    # a pure-image stage: a stage that is only `FROM <digest-pinned image>` and so
                    # cannot have rewritten a wheel input (the node-source stage; its pin is
                    # asserted by test_tester_unified_has_no_systemctl_stub_...).
                    if context_or_stage in allowed_pure_image_stages:
                        # Verified, not trusted by name: the stage is exactly one digest-pinned FROM.
                        body = [
                            text for _o, selector, _i, text in instructions
                            if selector == context_or_stage
                        ]
                        if len(body) != 1 or not re.fullmatch(
                            rf"(?i)FROM\s+\S+@sha256:[0-9a-f]{{64}}\s+AS\s+{re.escape(context_or_stage)}",
                            body[0],
                        ):
                            raise ValueError(
                                f"stage {context_or_stage!r} is not a lone digest-pinned FROM; "
                                "cannot allow it as a pure-image stage"
                            )
                    if (
                        context_or_stage in declared_stages
                        and context_or_stage not in allowed_pure_image_stages
                    ) or (
                        context_or_stage not in declared_stages
                        and context_or_stage not in allowed_external_contexts
                    ):
                        raise ValueError(
                            "cannot certify wheel inputs copied from another build stage"
                        )
                specs.append((
                    source,
                    _resolve_docker_copy_destination(destination, workdir),
                    excludes,
                ))
                run_sensitive_copy_seen |= source in run_sensitive_paths
            continue
        if re.match(r"(?i)^ADD(?:[ \t]|$)", instruction):
            raise ValueError("cannot prove ADD leaves build inputs unchanged")
        if re.match(r"(?i)^SHELL(?:[ \t]|$)", instruction):
            raise ValueError("cannot prove SHELL preserves build input checks")
        if re.match(r"(?i)^RUN(?:[ \t]|$)", instruction):
            if not re.search(command_pattern, instruction, re.IGNORECASE):
                if run_sensitive_copy_seen:
                    raise ValueError(
                        "cannot prove copied build inputs survive an intervening RUN"
                    )
                if run_sensitive_sources:
                    run_body = re.sub(r"(?i)^RUN[ \t]+", "", instruction, count=1)
                    if run_body not in allowed_pre_sensitive_run_bodies:
                        raise ValueError(
                            "cannot prove a pre-copy RUN leaves wheel inputs unchanged"
                        )
                continue
            if matches_seen == occurrence:
                run_body = re.sub(r"(?i)^RUN[ \t]+", "", instruction, count=1)
                if expected_run_body is not None and run_body != expected_run_body:
                    raise ValueError("wheel build RUN differs from its approved command")
                if expected_workdir is not None:
                    expected = _resolve_docker_workdir(expected_workdir, None)
                    if workdir != expected:
                        raise ValueError(
                            f"build command runs in {workdir!r}, expected {expected!r}"
                        )
                    command_match = re.search(command_pattern, run_body, re.IGNORECASE)
                    assert command_match is not None
                    prefix = run_body[:command_match.start()]
                    # A fully pinned RUN body is itself the shell-syntax allowlist.
                    # This permits the CMRU build to clear its output directory
                    # immediately before writing the wheel, while any changed
                    # command still fails the exact-body check above.
                    if expected_run_body is None:
                        if re.search(
                            r"\|\||(?<!\|)\|(?!\|)|;|(?<!&)\&(?!&)",
                            prefix,
                        ):
                            raise ValueError(
                                "build command follows a conditional or pipeline operator"
                            )
                        if re.search(r"\b(?:cd|pushd|popd)\b", prefix):
                            raise ValueError(
                                "build command changes directory inside its RUN instruction"
                            )
                        if tuple(shlex.split(prefix)) != allowed_shell_prefix:
                            raise ValueError(
                                "build command has an unapproved shell prefix"
                            )
                if run_sensitive_sources and expected_run_body is None:
                    raise ValueError(
                        "wheel input checks require an exact build RUN body"
                    )
                return specs
            matches_seen += 1
    raise ValueError(
        f"Dockerfile has no occurrence {occurrence} of build command "
        f"{command_pattern!r} in stage {stage!r}"
    )


def _resolve_docker_workdir(path: str, current: str | None) -> str:
    """Resolve a literal Docker WORKDIR against its previous known directory."""
    if "$" in path:
        raise ValueError(f"cannot resolve variable-based Docker path: {path}")
    if path.startswith("/"):
        return posixpath.normpath(path)
    if current is None:
        raise ValueError(f"relative Docker path has no known WORKDIR: {path}")
    return posixpath.normpath(posixpath.join(current, path))


def _resolve_docker_copy_destination(destination: str, workdir: str | None) -> str:
    """Resolve a COPY destination as Docker does relative to the active WORKDIR."""
    if "$" in destination:
        raise ValueError(f"cannot resolve variable-based Docker path: {destination}")
    trailing_slash = destination.endswith("/")
    if destination.startswith("/"):
        resolved = posixpath.normpath(destination)
    elif workdir is None:
        raise ValueError(
            f"relative COPY destination has no known WORKDIR: {destination}"
        )
    else:
        resolved = posixpath.normpath(posixpath.join(workdir, destination))
    if trailing_slash and resolved != "/":
        resolved += "/"
    return resolved


def _normalize_copy_source(source: str) -> PurePosixPath:
    """Normalize a build-context source like Docker does before lookup."""
    cleaned = posixpath.normpath(source.strip()).lstrip("/")
    while cleaned == ".." or cleaned.startswith("../"):
        cleaned = cleaned[3:] if cleaned.startswith("../") else "."
    return PurePosixPath(cleaned or ".")


def _copy_source_specs_from_instruction(
    instruction: str,
    *,
    include_stage: bool = False,
) -> list[tuple[PurePosixPath, str, tuple[str, ...]]]:
    if not re.match(r"(?i)^COPY(?:[ \t]|$)", instruction):
        return []
    match = re.match(r"(?i)^COPY(?:[ \t]+(.*))?$", instruction)
    if match is None:
        raise ValueError(f"cannot parse COPY instruction: {instruction}")
    operands = (match.group(1) or "").lstrip()
    if not operands:
        raise ValueError("COPY instruction has no operands")
    # COPY options precede both shell-form and JSON-form operands. Docker's
    # supported forms use --name=value or a closed boolean flag vocabulary.
    value_options = {"--from", "--chown", "--chmod", "--exclude"}
    boolean_options = {"--link"}
    from_stage = None
    excludes: list[str] = []
    while operands.startswith("--"):
        match = re.match(r"(?P<option>--[^\s]+)(?:[ \t]+(?P<rest>.*))?$", operands)
        if match is None or match.group("rest") is None:
            raise ValueError(f"cannot parse COPY options: {instruction}")
        option, separator, value = match.group("option").partition("=")
        if option in value_options:
            if not separator or not value:
                raise ValueError(f"COPY option needs a value: {option}")
        elif option in boolean_options:
            if separator and value.lower() not in {"true", "false"}:
                raise ValueError(f"COPY boolean option has an invalid value: {option}")
        else:
            raise ValueError(f"unsupported COPY option: {option}")
        if option == "--from":
            from_stage = value.lower()
        elif option == "--exclude":
            excludes.append(value)
        operands = match.group("rest").lstrip()
    heredocs = _dockerfile_heredoc_delimiters(instruction)
    if heredocs:
        tokens = shlex.split(operands)
        if len(heredocs) != 1 or len(tokens) != 2 or not tokens[0].startswith("<<"):
            raise ValueError(f"unsupported COPY heredoc form: {instruction}")
        return [(
            PurePosixPath("__heredoc_copy__"),
            tokens[1],
            tuple(excludes),
        )]
    if operands.startswith("["):
        decoded = json.loads(operands)
        if not isinstance(decoded, list) or len(decoded) < 2 or not all(
            isinstance(item, str) for item in decoded
        ):
            raise ValueError(f"cannot parse COPY JSON operands: {instruction}")
        source_tokens, destination = decoded[:-1], decoded[-1]
    else:
        tokens = shlex.split(operands)
        while tokens and tokens[0].startswith("--"):
            tokens.pop(0)
        if len(tokens) < 2:
            raise ValueError(f"cannot parse COPY shell operands: {instruction}")
        source_tokens, destination = tokens[:-1], tokens[-1]
    sources = [_normalize_copy_source(source) for source in source_tokens]
    for source in sources:
        _validate_copy_source_pattern(source)
    if len(sources) > 1 and not destination.endswith("/"):
        raise ValueError(
            "multiple COPY sources require a destination ending in '/': "
            f"{destination}"
        )
    if from_stage is not None and not include_stage:
        return []
    if from_stage is not None:
        sources = [
            PurePosixPath("__stage_copy__", from_stage, *source.parts)
            for source in sources
        ]
    return [(source, destination, tuple(excludes)) for source in sources]


def _copy_source_destinations_from_instruction(
    instruction: str,
) -> list[tuple[PurePosixPath, str]]:
    return [
        (source, destination)
        for source, destination, _excludes in _copy_source_specs_from_instruction(instruction)
    ]


def _copy_sources_from_instruction(instruction: str) -> list[PurePosixPath]:
    return [
        source
        for source, _destination, _excludes in _copy_source_specs_from_instruction(instruction)
    ]


def _dockerfile_copy_specs(
    text: str | None = None,
) -> list[tuple[PurePosixPath, str, tuple[str, ...]]]:
    if text is None:
        text = DOCKERFILE.read_text(encoding="utf-8")
    specs = []
    for instruction in _dockerfile_logical_instructions(text):
        specs.extend(
            spec
            for spec in _copy_source_specs_from_instruction(instruction)
            if spec[0].parts[:1] != ("__heredoc_copy__",)
        )
    return specs


def _dockerfile_copy_sources(text: str | None = None) -> list[PurePosixPath]:
    """Parse source operands from shell-form and JSON-form COPY instructions."""
    if text is None:
        text = DOCKERFILE.read_text(encoding="utf-8")
    return [
        source for source, _destination, _excludes in _dockerfile_copy_specs(text)
    ]


def _dockerfile_copy_pairs(text: str | None = None) -> list[tuple[PurePosixPath, str]]:
    """Parse COPY source/destination pairs in shell and JSON forms."""
    if text is None:
        text = DOCKERFILE.read_text(encoding="utf-8")
    return [
        (source, destination)
        for source, destination, _excludes in _dockerfile_copy_specs(text)
    ]


def _copy_source_overlaps_cli_extended(source: PurePosixPath) -> bool:
    """Whether a COPY source is the cli-extended tree, its parent, or a child."""
    source_text = str(_normalize_copy_source(str(source))).rstrip("/") or "."
    source_parts = PurePosixPath(source_text).parts
    target_parts = PurePosixPath("libraries/cli-extended").parts
    if "$" in source_text:
        return True  # unresolved sources cannot be proven safe
    if source_parts == (".",):
        return True
    shared_length = min(len(source_parts), len(target_parts))
    return all(
        _go_filepath_match(source_part, target_part)
        for source_part, target_part in zip(source_parts[:shared_length], target_parts[:shared_length])
    )


def _go_character_class_regex(pattern: str, opening: int) -> tuple[str, int] | None:
    index = opening + 1
    negate = index < len(pattern) and pattern[index] == "^"
    if negate:
        index += 1
    contents = []
    while index < len(pattern) and pattern[index] != "]":
        if pattern[index] == "\\":
            if index + 1 >= len(pattern):
                return None
            contents.append(re.escape(pattern[index + 1]))
            index += 2
        elif pattern[index] == "-":
            contents.append("-")
            index += 1
        else:
            contents.append(re.escape(pattern[index]))
            index += 1
    if index >= len(pattern) or not contents:
        return None
    return "(?!/)[" + ("^" if negate else "") + "".join(contents) + "]", index + 1


def _go_filepath_match(pattern: str, value: str) -> bool:
    """Match one path component with Go filepath.Match wildcard semantics."""
    pieces = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            if index + 1 == len(pattern):
                raise ValueError(f"invalid COPY filepath pattern: {pattern}")
            pieces.append(re.escape(pattern[index + 1]))
            index += 2
        elif character == "*":
            pieces.append("[^/]*")
            index += 1
        elif character == "?":
            pieces.append("[^/]")
            index += 1
        elif character == "[":
            char_class = _go_character_class_regex(pattern, index)
            if char_class is None:
                raise ValueError(f"invalid COPY filepath pattern: {pattern}")
            class_regex, index = char_class
            pieces.append(class_regex)
        else:
            pieces.append(re.escape(character))
            index += 1
    try:
        return re.fullmatch("".join(pieces), value) is not None
    except re.error as exc:
        raise ValueError(f"invalid COPY filepath pattern: {pattern}") from exc


def _validate_copy_source_pattern(source: PurePosixPath) -> None:
    """Validate every component even when COPY reads from another stage."""
    for component in source.parts:
        _go_filepath_match(component, "")


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("COPY foo/ /dest/", ["foo"]),
        ("copy\tfoo/ /dest/", ["foo"]),
        ('COPY --chown=1003:994 ["foo/", "/dest/"]', ["foo"]),
        ('COPY --chown=1003:994\t["foo/", "/dest/"]', ["foo"]),
        ("COPY \\\n  --link \\\n  foo/ /dest/", ["foo"]),
    ],
)
def test_copy_source_parser_handles_instruction_case_tabs_options_and_continuations(
    instruction, expected,
):
    assert [str(item) for item in _dockerfile_copy_sources(instruction)] == expected


def test_dockerfile_copy_oracle_refuses_variable_based_destinations():
    text = (
        "FROM base:latest AS stage-0\n"
        "ARG DEST=/opt/tester-venv/lib/python3.14/site-packages/cmru\n"
        "COPY ciu/src/ciu/__init__.py ${DEST}/injected.py\n"
        "RUN pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="cannot resolve variable-based Docker path"):
        _dockerfile_copy_specs_before_command(text, "stage-0", r"pip wheel")


def test_copy_source_parser_normalizes_context_aliases():
    assert _dockerfile_copy_sources("COPY /cmru/ /dest/\n") == [PurePosixPath("cmru")]
    assert _dockerfile_copy_sources("COPY ../cmru/ /dest/\n") == [PurePosixPath("cmru")]
    assert _dockerfile_copy_sources("COPY ./cmru/ /dest/\n") == [PurePosixPath("cmru")]
    assert _dockerfile_copy_sources("COPY .. /dest/\n") == [PurePosixPath(".")]


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM scratch\nCOPY \\\n# comment inside continuation\nlibraries/cli-extended/src /src\n",
        (
            "# escape=`\n"
            "FROM scratch\n"
            "COPY `\n"
            "# comment inside continuation\n"
            "libraries/cli-extended/src /src\n"
        ),
    ],
)
def test_copy_source_parser_handles_comment_lines_and_escape_directives(dockerfile):
    sources = _dockerfile_copy_sources(dockerfile)
    assert [str(source) for source in sources] == ["libraries/cli-extended/src"]
    assert _copy_source_overlaps_cli_extended(sources[0])


def test_copy_parser_does_not_treat_dockerfile_heredoc_body_as_instructions():
    text = (
        "FROM base:latest\n"
        "RUN cat <<'EOF'\n"
        "COPY cmru/ /src/cmru/\n"
        "EOF\n"
        "COPY tester-unified/gen-requirements.py /usr/local/lib/gen.py\n"
    )

    assert _dockerfile_copy_specs(text) == [(
        PurePosixPath("tester-unified/gen-requirements.py"),
        "/usr/local/lib/gen.py",
        (),
    )]


def test_copy_parser_rejects_an_unterminated_dockerfile_heredoc():
    with pytest.raises(ValueError, match="heredoc delimiter is missing"):
        _dockerfile_logical_instructions(
            "FROM base:latest\nRUN cat <<'EOF'\nCOPY cmru/ /src/cmru/\n"
        )


def test_copy_parser_accepts_inline_heredoc_without_calling_it_context_input():
    text = (
        "FROM scratch\n"
        "COPY <<'EOF' /etc/example.conf\n"
        "key=value\n"
        "EOF\n"
    )

    (source, destination, _excludes), = _copy_source_specs_from_instruction(
        "COPY <<'EOF' /etc/example.conf"
    )
    assert source.parts == ("__heredoc_copy__",)
    assert destination == "/etc/example.conf"
    assert _dockerfile_copy_specs(text) == []


def test_copy_source_parser_fails_closed_on_unparsed_copy_instruction():
    with pytest.raises(ValueError, match="no operands"):
        _dockerfile_copy_sources("FROM scratch\nCOPY\n")
    with pytest.raises(ValueError, match="unsupported COPY option"):
        _dockerfile_copy_sources("COPY --bogus foo /dest\n")
    assert _dockerfile_copy_specs(
        "COPY --exclude=*.json foo/ /dest/\n"
    ) == [(PurePosixPath("foo"), "/dest/", ("*.json",))]


def test_copy_destination_parser_preserves_the_destination_trailing_slash():
    assert _copy_source_destinations_from_instruction(
        "COPY file /dest/"
    ) == [(PurePosixPath("file"), "/dest/")]
    assert _copy_source_destinations_from_instruction(
        "COPY file /dest"
    ) == [(PurePosixPath("file"), "/dest")]


@pytest.mark.parametrize(
    ("source", "overlaps"),
    [
        (".", True),
        ("libraries", True),
        ("libraries/cli-extended", True),
        ("libraries/cli-extended/src", True),
        ("libraries/cli-*", True),
        ("libraries/*/src", True),
        ("libraries/[^w]*", True),
        ("$PROJECT_SOURCE", True),
        ("libraries/worktree", False),
        ("libraries/[w]orktree", False),
        ("libraries/work*", False),
        ("tester-unified/fetch-cli-extended.py", False),
    ],
)
def test_copy_source_scope_detects_cli_extended_tree_ancestors_and_descendants(
    source, overlaps,
):
    assert _copy_source_overlaps_cli_extended(PurePosixPath(source)) is overlaps


def test_copy_source_scope_normalizes_paths_before_checking_cli_extended_overlap():
    for source in (
        "/libraries/cli-extended", "../libraries/cli-extended",
        "./libraries/cli-extended",
    ):
        assert _copy_source_overlaps_cli_extended(PurePosixPath(source))


def test_dockerfile_disables_detached_git_maintenance_system_wide_and_asserts_it():
    code = _dockerfile_run_lines()
    assert "git config --system maintenance.autoDetach false" in code
    assert "git config --system gc.autoDetach false" in code
    assert 'git config --system --get maintenance.autoDetach)" = false' in code
    assert 'git config --system --get gc.autoDetach)" = false' in code
    # The setting must be in place (as root) before the image drops to uid 1003.
    assert code.index("gc.autoDetach false") < code.index("USER 1003")


def test_dockerfile_installs_estate_internal_packages_only_offline_from_copied_sources():
    code = _dockerfile_run_lines()
    # The old install of cmru from a PyPI-default pip is gone.
    assert "pip install --no-cache-dir --no-deps /src/cmru" not in code
    wheels = [
        command for command in _pip_commands(code)
        if "pip wheel" in command and "/src/cmru" in command
    ]
    assert len(wheels) == 1, wheels
    for flag in ("--no-index", "--no-deps", "--no-build-isolation"):
        assert flag in wheels[0], flag
    # CMRU is built from copied source; Run-Gate is installed from the exact
    # external wheel; cli-extended is a release wheel.
    assert "/src/cmru" in wheels[0] and "/src/libraries/cli-extended" not in code
    assert "/src/run-gate-project" in code
    assert "COPY --from=run-gate-wheel /run_gate-*.whl /tmp/run-gate-wheels/" in code
    assert 'RUN_GATE_TESTER_WHEEL_SHA256}" "$1" | sha256sum -c -' in code
    assert '/opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "$1"' in code
    assert "/tmp/run-gate-build-venv" not in code
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE" not in code
    assert "--no-index --no-deps /tmp/internal-wheels/*.whl" in code
    # The PyPI-bound requirements come from the refusing generator.
    assert "gen-requirements.py --root /src" in code
    assert '-r /tmp/tester-requirements.txt' in code
    # Relative layout cmru's pyproject packages (`../libraries/worktree/src`);
    # cli-extended source is NOT copied (it is a wheel dependency).
    assert "COPY libraries/worktree/ /src/libraries/worktree/" in code
    assert not re.search(r"^COPY libraries/\s", code, re.MULTILINE)


def test_run_gate_wheel_version_helper_reads_the_wheel_metadata(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "run_gate-24.0.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "run_gate-24.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: run-gate\nVersion: 24.0.0\n\n",
        )

    result = subprocess.run(
        [sys.executable, str(RUN_GATE_WHEEL_VERSION_HELPER), str(dist)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "24.0.0\n"

    identity = subprocess.run(
        [sys.executable, str(RUN_GATE_WHEEL_VERSION_HELPER), str(dist), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert identity.returncode == 0, identity.stderr
    assert json.loads(identity.stdout) == {
        "version": "24.0.0",
        "filename": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }


def test_run_gate_wheel_version_helper_refuses_ambiguous_dist_directory(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for version in ("24.0.0", "24.1.0"):
        with ZipFile(dist / f"run_gate-{version}-py3-none-any.whl", "w") as archive:
            archive.writestr(
                f"run_gate-{version}.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: run-gate\nVersion: {version}\n\n",
            )

    result = subprocess.run(
        [sys.executable, str(RUN_GATE_WHEEL_VERSION_HELPER), str(dist)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "expected one Run-Gate wheel" in result.stderr


def test_dockerfile_asserts_where_imports_come_from_and_that_cli_extended_is_the_release():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert all(name in text for name in (
        "import cli_extended", "import cmru", "import run_gate", "import worktree",
    ))
    assert "\n    assert location.startswith(site)," in text and 'site = "/opt/tester-venv/"' in text
    assert '\nassert version.endswith("+tester.unified"), (' in text
    assert 'assert "+" not in released' in text
    assert 'metadata.version("run-gate")' in text
    assert 'os.environ["RUN_GATE_TESTER_VERSION"]' in text
    assert 'run_gate_version == expected_run_gate_version' in text
    assert 'Path(sys.executable).with_name("run-gate")' in text
    assert 'subprocess.run([str(entrypoint), "--version"]' in text
    assert 'RUN_GATE_TESTER_VERSION}"' in text
    assert "ENV PATH=/opt/tester-venv/bin:${PATH}" in text
    assert text.rstrip().endswith("RUN run-gate --version")


def test_estate_has_no_tracked_run_gate_script_copies_or_symlinks():
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    legacy = sorted(
        path.decode("utf-8", "surrogateescape")
        for path in listed.split(b"\0")
        if path and Path(path.decode("utf-8", "surrogateescape")).name == "run-gate.py"
    )
    assert legacy == [], f"retired Run-Gate script copies remain tracked: {legacy}"


def test_dockerfile_installs_the_exact_run_gate_wheel_from_a_named_context():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG RUN_GATE_TESTER_VERSION\n" in text
    assert "ARG RUN_GATE_TESTER_WHEEL_SHA256\n" in text
    assert "COPY --from=run-gate-wheel /run_gate-*.whl /tmp/run-gate-wheels/" in text
    assert '"${RUN_GATE_TESTER_WHEEL_SHA256}" "$1" | sha256sum -c -' in text
    assert '/opt/tester-venv/bin/pip install --no-cache-dir --no-index --no-deps "$1"' in text
    assert 'metadata.version("run-gate")' in text
    assert 'os.environ["RUN_GATE_TESTER_VERSION"]' in text
    assert "RUN_GATE_TESTER_LOCAL_VERSION" not in text
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE" not in text
    assert "/tmp/run-gate-build-venv" not in text
    assert "COPY run-gate-project/run_gate.py" not in text
    assert "COPY run-gate-project/run_gate_admission.py" not in text

    builder = (REPO_ROOT / "tester-unified" / "build-image.sh").read_text(
        encoding="utf-8",
    )
    assert "run-gate-wheel-version.py" in builder
    assert "sha256" in builder
    assert '"$wheel_dir/$run_gate_filename"' in builder
    assert '"run-gate-wheel=$wheel_stage"' in builder
    assert '"RUN_GATE_TESTER_VERSION=$run_gate_version"' in builder
    assert '"RUN_GATE_TESTER_WHEEL_SHA256=$run_gate_sha256"' in builder
    assert "docker buildx build --load" in builder

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!run-gate-project/pyproject.toml" in dockerignore
    assert "!run-gate-project/run_gate.py" not in dockerignore
    assert "!run-gate-project/run_gate_admission.py" not in dockerignore

def _pip_commands(code: str) -> list[str]:
    """Every ``pip ...`` command of the Dockerfile's RUN lines, continuation
    lines joined, split at ``&&``."""
    flat = re.sub(r"\\\n\s*", " ", code)
    commands = []
    for line in flat.splitlines():
        for part in line.split("&&"):
            if re.search(r"\bpip\b", part):
                commands.append(" ".join(part.split()))
    return commands


def test_dockerfile_no_cli_extended_reaches_pip_except_the_sha256_verified_release_wheel():
    """D8: the only pip command that may name cli-extended installs the wheel the
    fetcher verified, offline and without dependency resolution; every other pip
    command either is offline or takes only the generator's (skipped) closure."""
    code = _dockerfile_run_lines()
    commands = _pip_commands(code)
    assert commands
    release = [c for c in commands if "cli-extended" in c.lower() or "cli_extended" in c.lower()]
    assert len(release) == 1, release
    assert re.search(
        r"pip install --no-cache-dir --no-index --no-deps /tmp/cli-extended-release/\*\.whl$",
        release[0],
    ), release[0]
    for command in commands:
        if "--no-index" in command:
            continue
        # An index-capable pip may only read the generator output or upgrade itself.
        assert re.search(
            r"-r /tmp/(tester|internal-build|run-gate-build)-requirements\.txt$"
            r"|--upgrade pip$",
            command,
        ), command
        # Aged-latest: every index-capable install carries the release-age filter.
        assert '--uploaded-prior-to "${PIP_MIN_RELEASE_AGE}"' in command, command
    assert "/src/libraries/cli-extended" not in code
    assert "libraries/cli-extended" not in code


def test_dockerfile_fetches_the_release_by_sha256_before_cmru_is_installed():
    code = _dockerfile_run_lines()
    flat = re.sub(r"\\\n\s*", " ", code)
    fetch = flat.index("fetch-cli-extended.py")
    install_release = flat.index("/tmp/cli-extended-release/*.whl")
    build_cmru = flat.index("pip wheel")
    install_cmru = flat.index("/tmp/internal-wheels/*.whl")
    assert fetch < install_release < build_cmru < install_cmru
    # Pointer or pin, both through the digest-verifying fetcher; no bare index install.
    assert "CLI_EXTENDED_POINTER_URL" in code and "--sha256" in flat and "--min-version" in flat
    assert re.search(r"^ARG CLI_EXTENDED_POINTER_URL=https://github\.com/volkb79-2/vbpub/releases/download/"
                     r"cli-extended-latest/latest\.json$", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
    # The requirements file is generated before pip reads it, and the fetcher is COPYed.
    assert "COPY tester-unified/fetch-cli-extended.py" in code


def test_dockerfile_pins_the_released_asset_and_digest_by_default_and_latest_is_opt_in():
    """D-3: the repo carries the sha256 pin (the pointer cannot vouch for itself);
    `latest.json` mode needs an explicit CLI_EXTENDED_RESOLVE=latest build arg."""
    text = DOCKERFILE.read_text("utf-8")
    args = dict(re.findall(r"^ARG (CLI_EXTENDED_\w+)=(\S*)$", text, re.MULTILINE))
    assert args["CLI_EXTENDED_RESOLVE"] == "pinned"
    url, digest = args["CLI_EXTENDED_WHEEL_URL"], args["CLI_EXTENDED_WHEEL_SHA256"]
    fetcher = _load_fetcher()
    assert fetcher._check_url(url) == url  # the allowlisted release prefix
    named = fetcher._WHEEL.fullmatch(url.rsplit("/", 1)[-1])
    assert named is not None
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    (spec,) = [d for d in declared["project"]["dependencies"] if gen.requirement_name(d) == "cli-extended"]
    assert fetcher._at_least(named["version"], spec.replace(" ", "").split(">=")[1])
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    # PKG-4 N8: the literal default digest, verified against the downloaded
    # cli_extended-0.5.0 wheel (release cli-extended-v0.5.0); a changed pin must be a deliberate edit here too.
    assert digest == "755c6f4919b587bbada9f9988a7bd677428513e93a040b4d3561fd76316ac951"
    flat = " ".join(re.sub(r"\\\n\s*", " ", _dockerfile_run_lines()).split())
    # pinned branch passes the pin, latest branch passes only the pointer, anything else fails the build.
    assert re.search(r'pinned\) set -- --wheel-url "\$\{CLI_EXTENDED_WHEEL_URL\}" '
                     r'--sha256 "\$\{CLI_EXTENDED_WHEEL_SHA256\}" ;;', flat)
    # latest passes the pointer AND the digest the caller must name (never unverified bytes).
    assert re.search(r'latest\) set -- --pointer-url "\$\{CLI_EXTENDED_POINTER_URL\}" '
                     r'--expect-sha256 "\$\{CLI_EXTENDED_WHEEL_SHA256\}" ;;', flat)
    # every mode verifies the wheel file actually installed against that digest
    # `flat` has collapsed runs of spaces, so match the separator loosely here and pin the
    # exact two-space `sha256sum -c` line format on the unflattened text just below.
    assert re.search(r'fi && set -- /tmp/cli-extended-release/cli_extended-\*\.whl && \[ "\$#" -eq 1 \] '
                     r'&& \[ -f "\$1" \] && echo "\$\{CLI_EXTENDED_WHEEL_SHA256\}\s+\$1" \| sha256sum -c -', flat)
    assert 'echo "${CLI_EXTENDED_WHEEL_SHA256}  $1" | sha256sum -c -\n\n' in text, (
        "sha256sum -c reads `<digest><two spaces><file>`"
    )
    assert re.search(r'^LABEL org\.vbpub\.cli-extended\.wheel-sha256="\$\{CLI_EXTENDED_WHEEL_SHA256\}"$',
                     text, re.MULTILINE)
    assert re.search(r'\*\) echo "[^"]*" >&2; exit 1 ;;', flat)
    assert 'fetch-cli-extended.py --dest /tmp/cli-extended-release --min-version 0.3.0 "$@"' in flat


def _local_mode_script(tmp_path: Path) -> str:
    """The Dockerfile's RUN `if local` block, extracted from its text, with the
    image paths redirected into ``tmp_path`` so the shell logic itself runs."""
    flat = re.sub(r"\\\n\s*", " ", DOCKERFILE.read_text("utf-8"))
    block = re.search(r'if \[ "\$\{CLI_EXTENDED_RESOLVE\}" = local \]; then\s+(?P<body>.*?);\s+else\s', flat, re.DOTALL)
    assert block is not None
    body = block["body"].replace("/tmp/cli-extended-local", str(tmp_path / "local"))
    return body.replace("/tmp/cli-extended-release", str(tmp_path / "release"))


def _run_local_mode(tmp_path: Path, sha: str, wheels: list[str]):
    import hashlib
    import subprocess

    (tmp_path / "local").mkdir(parents=True)
    for name in wheels:
        (tmp_path / "local" / name).write_bytes(b"wheel:" + name.encode())
    digest = sha or hashlib.sha256(b"wheel:" + wheels[0].encode()).hexdigest()
    script = f'CLI_EXTENDED_WHEEL_SHA256={digest}\n{_local_mode_script(tmp_path)}'
    return subprocess.run(["sh", "-ec", script], capture_output=True, text=True, check=False)


def test_local_mode_installs_exactly_one_digest_verified_local_wheel(tmp_path):
    name = "cli_extended-0.2.0+local.zero-py3-none-any.whl"
    result = _run_local_mode(tmp_path, "", [name])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "release" / name).read_bytes() == b"wheel:" + name.encode()


def test_local_mode_refuses_a_digest_mismatch_no_wheel_or_two_wheels(tmp_path):
    name = "cli_extended-0.2.0+local.zero-py3-none-any.whl"
    mismatch = _run_local_mode(tmp_path / "a", "0" * 64, [name])
    assert mismatch.returncode != 0 and "does not match" in mismatch.stderr
    assert not (tmp_path / "a" / "release").exists()
    none = _run_local_mode(tmp_path / "b", "0" * 64, [])
    assert none.returncode != 0 and "needs exactly one" in none.stderr
    two = _run_local_mode(
        tmp_path / "c", "0" * 64, ["cli_extended-1+a-py3-none-any.whl", "cli_extended-2+b-py3-none-any.whl"],
    )
    assert two.returncode != 0 and "needs exactly one" in two.stderr


def test_resolve_modes_are_pinned_default_latest_and_local_and_local_asserts_a_local_version():
    text = DOCKERFILE.read_text("utf-8")
    assert re.search(r"^ARG CLI_EXTENDED_RESOLVE=pinned$", text, re.MULTILINE)
    flat = " ".join(re.sub(r"\\\n\s*", " ", text).split())
    assert "local) set -- ;;" in flat
    assert 'pinned, latest or local" >&2; exit 1' in flat  # the invalid-value refusal names all three
    assert 'assert "+" in released' in text and 'assert "+" not in released' in text
    assert 'os.environ["CLI_EXTENDED_RESOLVE"] == "local"' in text
    header = text.split("FROM ", 1)[0] + text.split("ARG CLI_EXTENDED_RESOLVE", 1)[0]
    for mode in ("pinned", "latest", "local"):
        assert re.search(rf"^#\s+{mode}\b", header, re.MULTILINE), mode
    assert "COPY tester-unified/local-wheel/ /tmp/cli-extended-local/" in text
    readme = (REPO_ROOT / "tester-unified" / "README.md").read_text("utf-8")
    # The README documents the local mode through the supported builder's flag.
    assert "--cli-extended-resolve local" in readme and "local-wheel" in readme


def test_dockerignore_admits_the_local_wheel_input_and_gitignore_keeps_wheels_out():
    assert _dockerignore_included("tester-unified/local-wheel/cli_extended-0.2.0+x-py3-none-any.whl")
    assert _dockerignore_included("tester-unified/local-wheel/.gitkeep")
    ignore = (REPO_ROOT / ".gitignore").read_text("utf-8").splitlines()
    assert "tester-unified/local-wheel/*.whl" in ignore


@pytest.mark.parametrize(("trove", "wheel_inputs"), [
    ("cmru/nyxloom-trove", ("cmru/pyproject.toml", "cmru/src/cmru/cli.py")),
    (
        "scripts/cgroup-profiler/nyxloom-trove",
        ("scripts/cgroup-profiler/pyproject.toml", "scripts/cgroup-profiler/cgprofile.py"),
    ),
], ids=["cmru", "cgroup-profiler"])
def test_dockerignore_keeps_repository_only_trove_reports_out_of_the_wheel_source_tree(
    trove, wheel_inputs,
):
    """A review report is evidence, not a wheel input: it must never reach /src/<project>.

    Without the rule every new report under the trove lands in the COPYed wheel tree, so
    persisting a review would fail the COPY-input accounting test until it was committed.
    The wheel's real inputs stay in the context.
    """
    rules = _dockerignore_rules()
    # A report that does not exist yet is excluded too: the rule is the directory, not a list.
    assert not _dockerignore_included(f"{trove}/reports/a-review-not-written-yet.md", rules)
    assert not _dockerignore_included(f"{trove}/nyxloom.toml", rules)
    committed = [path for path in _git_tree_context_paths() if path.as_posix().startswith(f"{trove}/")]
    assert committed, f"{trove} has no committed files; the rule would guard nothing"
    assert not [path for path in committed if _dockerignore_included(path.as_posix(), rules)]
    for wheel_input in wheel_inputs:
        assert _dockerignore_included(wheel_input, rules), wheel_input


_GENERATED_BUILD_OUTPUT_RULES = ("**/build", "**/dist", "**/*.egg-info")


@pytest.mark.parametrize("generated", [
    "cmru/build/lib/cmru/a_module_deleted_from_source.py",
    "cmru/dist/cmru-0.0.0-py3-none-any.whl",
    "cmru/cmru.egg-info/PKG-INFO",
    "cmru/src/cmru.egg-info/SOURCES.txt",
    "ciu/build/lib/ciu/stale.py",
    "assay/dist/assay-0.0.0-py3-none-any.whl",
    "scripts/cgroup-profiler/build/lib/cgprofile.py",
])
def test_dockerignore_keeps_local_build_output_out_of_the_wheel_source_trees(generated):
    """Local wheel-build output must never reach a COPYed /src/<project> tree.

    `pip wheel /src/<project>` builds in place, and setuptools reuses an existing
    build/lib, so a stale module from a developer's earlier local build could be
    packaged into the image's wheel even after it was deleted from source.
    """
    rules = _dockerignore_rules()
    for rule in _GENERATED_BUILD_OUTPUT_RULES:
        assert rule in [line.strip() for line in rules], rule
    assert not _dockerignore_included(generated, rules)


def test_dockerignore_build_output_rules_hide_no_committed_context_path():
    """The generated-output rules exclude only generated output, never a tracked input."""
    rules = _dockerignore_rules()
    without = [line for line in rules if line.strip() not in _GENERATED_BUILD_OUTPUT_RULES]
    assert len(without) == len(rules) - len(_GENERATED_BUILD_OUTPUT_RULES)
    hidden = sorted(
        path.as_posix() for path in _git_tree_context_paths()
        if any(part in {"build", "dist"} or part.endswith(".egg-info") for part in path.parts)
        and _dockerignore_included(path.as_posix(), without)
        and not _dockerignore_included(path.as_posix(), rules)
    )
    assert not hidden, hidden


def test_dockerfile_fetch_floor_equals_cmru_declared_floor():
    code = re.sub(r"\\\n\s*", " ", _dockerfile_run_lines())
    floor = re.search(r"fetch-cli-extended\.py .*?--min-version (\S+)", code).group(1)
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    (spec,) = [d for d in declared["project"]["dependencies"] if gen.requirement_name(d) == "cli-extended"]
    assert spec.replace(" ", "") == f"cli-extended>={floor}"


# --- fetch-cli-extended.py: the digest is mandatory and checked before any file exists

FETCHER = REPO_ROOT / "tester-unified" / "fetch-cli-extended.py"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_cli_extended", FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE = "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.2.0/"
_WHEEL_NAME = "cli_extended-0.2.0-py3-none-any.whl"


def _fake_release(payload: bytes = b"wheel-bytes", **pointer):
    import hashlib
    import json
    body = {"version": "0.2.0", "asset": _WHEEL_NAME, "url": _BASE + _WHEEL_NAME,
            "sha256": hashlib.sha256(payload).hexdigest()}
    body.update(pointer)
    served = {
        "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json":
            json.dumps(body).encode(),
        _BASE + _WHEEL_NAME: payload,
    }

    def fetch(url):
        return served[url]

    return fetch, hashlib.sha256(payload).hexdigest()


def test_fetcher_installs_nothing_unless_the_pointer_digest_matches(tmp_path):
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    wheel = fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "ok", "0.2.0", fetch)
    assert wheel.read_bytes() == b"wheel-bytes" and wheel.name == _WHEEL_NAME

    tampered, _ = _fake_release(sha256="0" * 64)
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "bad", "0.2.0", tampered)
    assert not (tmp_path / "bad").exists()  # nothing written before the digest matched


def test_fetcher_expected_digest_accepts_only_the_published_bytes(tmp_path):
    """The caller's published digest binds `latest` to exact bytes: a pointer that
    names other bytes is refused before any download, and a download that does not
    hash to the expected digest is refused before any file exists."""
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    ok = fetcher.fetch_wheel(
        fetcher.POINTER_URL, None, None, tmp_path / "ok", "0.2.0", fetch, expect_sha256=digest,
    )
    assert ok.read_bytes() == b"wheel-bytes"

    # The newly released bytes differ from what the pointer (and the old pin) name.
    stale = "e" * 64
    with pytest.raises(SystemExit, match="names sha256 " + digest + ", not the expected " + stale):
        fetcher.fetch_wheel(
            fetcher.POINTER_URL, None, None, tmp_path / "stale", "0.2.0", fetch, expect_sha256=stale,
        )
    assert not (tmp_path / "stale").exists()

    # A pointer that vouches for the expected digest but serves different bytes.
    lying, _ = _fake_release(sha256=stale)
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        fetcher.fetch_wheel(
            fetcher.POINTER_URL, None, None, tmp_path / "lying", "0.2.0", lying, expect_sha256=stale,
        )
    assert not (tmp_path / "lying").exists()

    # An invalid expectation is refused, not ignored.
    with pytest.raises(SystemExit, match="--expect-sha256 has no valid sha256"):
        fetcher.fetch_wheel(
            fetcher.POINTER_URL, None, None, tmp_path / "bad", "0.2.0", fetch, expect_sha256="XYZ",
        )


def test_fetcher_expected_digest_also_binds_a_pinned_wheel_url(tmp_path):
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    with pytest.raises(SystemExit, match="not the expected"):
        fetcher.fetch_wheel(
            fetcher.POINTER_URL, _BASE + _WHEEL_NAME, digest, tmp_path / "x", "0.2.0", fetch,
            expect_sha256="f" * 64,
        )


@pytest.mark.parametrize("digest", [None, "", "abc", "A" * 64, 7])
def test_fetcher_refuses_a_pointer_without_a_valid_sha256(tmp_path, digest):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release(sha256=digest)
    with pytest.raises(SystemExit, match="no valid sha256"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", fetch)
    assert not (tmp_path / "x").exists()


def test_fetcher_refuses_foreign_hosts_old_versions_and_odd_names(tmp_path):
    fetcher = _load_fetcher()
    with pytest.raises(SystemExit, match="not under"):
        fetcher.fetch_wheel("https://evil.invalid/latest.json", None, None, tmp_path, "0.2.0",
                            lambda url: b"")
    foreign, _ = _fake_release(url="https://pypi.org/packages/" + _WHEEL_NAME)
    with pytest.raises(SystemExit, match="not under"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.2.0", foreign)
    fetch, _ = _fake_release()
    with pytest.raises(SystemExit, match="older than the declared floor"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.3.0", fetch)
    odd, _ = _fake_release(url=_BASE + "cmru-0.2.0-py3-none-any.whl", asset="cmru-0.2.0-py3-none-any.whl")
    with pytest.raises(SystemExit, match="not a cli_extended"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.2.0", odd)


@pytest.mark.parametrize("field, value, message", [
    ("asset", "cli_extended-0.9.9-py3-none-any.whl", "asset .* is not the url's file name"),
    ("asset", None, "asset None is not the url's file name"),
    ("version", "0.3.0", "version '0.3.0' is not the wheel's '0.2.0'"),
    ("version", None, "version None is not the wheel's '0.2.0'"),
])
def test_fetcher_cross_checks_the_pointer_asset_and_version_against_the_url(tmp_path, field, value, message):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release(**{field: value})
    with pytest.raises(SystemExit, match=message):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", fetch)
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("version, floor, expected", [
    ("0.2", "0.2.0", True), ("0.2.0", "0.2", True), ("0.2.0.0", "0.2", True),
    ("0.1.9", "0.2", False), ("0.2", "0.2.1", False), ("1", "0.2.0", True),
])
def test_fetcher_pads_version_tuples_before_comparing_to_the_floor(version, floor, expected):
    assert _load_fetcher()._at_least(version, floor) is expected


def test_fetcher_turns_a_failed_wheel_download_into_a_clean_exit(tmp_path):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release()

    def pointer_only(url):
        if url.endswith(_WHEEL_NAME):
            raise OSError("connection reset")
        return fetch(url)

    with pytest.raises(SystemExit) as caught:
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", pointer_only)
    assert caught.value.code == "fetch-cli-extended: cannot download " + _BASE + _WHEEL_NAME + ": connection reset"
    assert not (tmp_path / "x").exists()


# --- B2: HTTPS only across redirects, to allowlisted hosts only ---------------

@pytest.mark.parametrize("target, message", [
    ("http://github.com/volkb79-2/x.whl", "not https"),
    ("http://objects.githubusercontent.com/x", "not https"),
    ("ftp://github.com/x", "not https"),
    ("https://evil.invalid/x.whl", "host not allowed"),
    ("https://github.com.evil.invalid/x.whl", "host not allowed"),
    ("https://pypi.org/x.whl", "host not allowed"),
])
def test_redirect_handler_refuses_non_https_and_foreign_hosts(target, message):
    import urllib.request
    fetcher = _load_fetcher()
    handler = fetcher._HttpsOnlyRedirects()
    request = urllib.request.Request("https://github.com/volkb79-2/vbpub/releases/download/x")
    with pytest.raises(SystemExit, match=message):
        handler.redirect_request(request, None, 302, "Found", {}, target)


@pytest.mark.parametrize("host", [
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
])
def test_redirect_handler_follows_https_to_each_allowlisted_host(host):
    import urllib.request
    fetcher = _load_fetcher()
    request = urllib.request.Request("https://github.com/volkb79-2/vbpub/releases/download/x")
    followed = fetcher._HttpsOnlyRedirects().redirect_request(
        request, None, 302, "Found", {}, f"https://{host}/a/b?sig=1")
    assert followed.full_url == f"https://{host}/a/b?sig=1"


def test_real_opener_refuses_an_http_redirect_served_by_a_fake_origin():
    """End to end through the real opener (no fake fetch): a local origin that
    302s to http:// and to a foreign https host; the origin's own host is allowed
    as the pointer host, the redirect targets are not."""
    import http.server
    import threading

    fetcher = _load_fetcher()
    targets = {"/to-http": "http://127.0.0.1:1/x", "/to-foreign": "https://evil.invalid/x"}

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(SystemExit, match="not https"):
            fetcher._urlopen_bytes(base + "/to-http")
        with pytest.raises(SystemExit, match="host not allowed"):
            fetcher._urlopen_bytes(base + "/to-foreign")
    finally:
        server.shutdown()
        server.server_close()


def test_fetcher_pinned_asset_needs_both_url_and_digest_and_skips_the_pointer(tmp_path):
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    pointer = fetcher.POINTER_URL

    def no_pointer(url):
        assert url != pointer, "a pinned fetch must not read the pointer"
        return fetch(url)

    wheel = fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, digest, tmp_path / "p", "0.2.0", no_pointer)
    assert wheel.name == _WHEEL_NAME
    with pytest.raises(SystemExit, match="together"):
        fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, None, tmp_path / "q", "0.2.0", fetch)
    with pytest.raises(SystemExit, match="together"):
        fetcher.fetch_wheel(pointer, None, digest, tmp_path / "q", "0.2.0", fetch)
    with pytest.raises(SystemExit, match="no valid sha256"):
        fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, "xyz", tmp_path / "q", "0.2.0", fetch)


def test_fetcher_main_prints_the_wheel_path_and_treats_empty_pin_args_as_unset(tmp_path, monkeypatch, capsys):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release()
    monkeypatch.setattr(fetcher, "_urlopen_bytes", fetch)
    # The Dockerfile passes unset ARGs as empty strings.
    assert fetcher.main(["--dest", str(tmp_path / "m"), "--min-version", "0.2.0",
                         "--pointer-url", fetcher.POINTER_URL,
                         "--wheel-url", "", "--sha256", ""]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "m" / _WHEEL_NAME)
    assert (tmp_path / "m" / _WHEEL_NAME).read_bytes() == b"wheel-bytes"


def test_fetcher_refuses_unreadable_or_malformed_pointers(tmp_path):
    fetcher = _load_fetcher()
    pointer = fetcher.POINTER_URL

    def broken(url):
        raise OSError("down")

    with pytest.raises(SystemExit, match="cannot read the release pointer"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", broken)
    with pytest.raises(SystemExit, match="cannot read the release pointer"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"{not json")
    with pytest.raises(SystemExit, match="not a JSON object"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"[]")
    with pytest.raises(SystemExit, match="no url"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"{}")
    with pytest.raises(SystemExit, match="plain release version"):
        fetcher.fetch_wheel(pointer, _BASE + "cli_extended-0.2.0rc1-py3-none-any.whl", "a" * 64,
                            tmp_path, "0.2.0", lambda url: b"")


def _dockerignore_pattern_matches(pattern: str, path: str) -> bool:
    """Match one Docker ignore pattern against one candidate path."""
    pattern = posixpath.normpath(pattern.strip()).lstrip("/")
    path = posixpath.normpath(path).lstrip("/")
    if not pattern or pattern == ".":
        return False
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 2] == "**":
            if pattern[index + 2:index + 3] == "/":
                pieces.append("(?:[\\s\\S]*/)?")
                index += 3
            else:
                pieces.append("[\\s\\S]*")
                index += 2
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            pieces.append("[^/]")
            index += 1
        elif pattern[index] == "[":
            char_class = _go_character_class_regex(pattern, index)
            if char_class is None:
                raise ValueError(f"invalid Docker ignore pattern: {pattern}")
            class_regex, index = char_class
            pieces.append(class_regex)
        elif pattern[index] == "\\":
            if index + 1 >= len(pattern):
                raise ValueError(f"invalid Docker ignore pattern: {pattern}")
            pieces.append(re.escape(pattern[index + 1]))
            index += 2
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(pieces), path) is not None


def _dockerignore_rules(
    dockerfile: Path = DOCKERFILE,
    context_root: Path = REPO_ROOT,
) -> list[str]:
    """Use Docker's Dockerfile-specific ignore file when one exists."""
    specific = dockerfile.with_name(f"{dockerfile.name}.dockerignore")
    path = specific if specific.is_file() else context_root / ".dockerignore"
    return path.read_text(encoding="utf-8").splitlines()


def _dockerignore_included(relative: str, rules: list[str] | None = None) -> bool:
    """Docker's .dockerignore semantics: the LAST matching pattern decides;
    ``!`` re-includes, and ``**`` can match zero or more directories.
    """
    if rules is None:
        rules = _dockerignore_rules()
    relative = posixpath.normpath(relative).lstrip("/")
    parts = relative.split("/")
    candidates = ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]
    included = True
    for raw in rules:
        if not raw or raw.startswith("#"):
            continue
        pattern = raw.strip()
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]
        pattern = posixpath.normpath(pattern)
        if any(_dockerignore_pattern_matches(pattern, candidate) for candidate in candidates):
            included = negate
    return included


def _copy_source_matches_path(source: PurePosixPath, relative: str) -> bool:
    """Match Docker COPY's Go-style source pattern against a context entry."""
    if source.parts[:1] == ("__heredoc_copy__",):
        return False
    source_parts = _normalize_copy_source(str(source)).parts
    path_parts = PurePosixPath(relative).parts
    if not source_parts:
        return True
    if len(source_parts) > len(path_parts):
        return False
    return all(
        _go_filepath_match(pattern_part, path_part)
        for pattern_part, path_part in zip(source_parts, path_parts)
    )


def _copy_excludes_path(
    source: PurePosixPath,
    relative: str,
    excludes: tuple[str, ...],
) -> bool:
    """Apply COPY --exclude patterns to the matched source's relative path."""
    if not excludes:
        return False
    source_parts = _normalize_copy_source(str(source)).parts
    static_prefix_length = 0
    for part in source_parts:
        if any(marker in part for marker in "*?["):
            break
        static_prefix_length += 1
    path_parts = PurePosixPath(relative).parts
    # With a wildcard source, --exclude paths are relative to the stable
    # prefix before expansion. For `cmru/*`, that keeps `src/` in scope so
    # `--exclude=src` also excludes entries below the matched directory.
    suffix_parts = path_parts[static_prefix_length:]
    candidates = (
        ["/".join(suffix_parts[:depth]) for depth in range(1, len(suffix_parts) + 1)]
        if suffix_parts
        else [path_parts[-1]] if path_parts else []
    )
    return any(
        _dockerignore_pattern_matches(pattern, candidate)
        for pattern in excludes
        for candidate in candidates
    )


def _copy_spec_covers_context_path(
    spec: tuple[PurePosixPath, str, tuple[str, ...]],
    relative: str,
) -> bool:
    """Whether one COPY source includes a specific path after --exclude."""
    source, _destination, excludes = spec
    return (
        _copy_source_matches_path(source, relative)
        and not _copy_excludes_path(source, relative, excludes)
    )


def _assert_copy_spec_covers_path_at_destination(
    specs: list[tuple[PurePosixPath, str, tuple[str, ...]]],
    relative: str,
    expected_source: str,
    expected_destination: str,
) -> None:
    source = PurePosixPath(expected_source)
    destination = PurePosixPath(expected_destination)
    source_path = PurePosixPath(relative)
    if source_path == source:
        target = (
            destination
            if destination.name == source_path.name
            else destination / source_path.name
        )
    else:
        try:
            source_suffix = source_path.relative_to(source)
        except ValueError:
            source_suffix = PurePosixPath()
        target = destination / source_suffix

    valid_writers = [
        index
        for index, spec in enumerate(specs)
        if spec[0] == source
        and spec[1] == expected_destination
        and _copy_spec_covers_context_path(spec, relative)
    ]
    for spec in specs:
        source_spec = spec[0]
        uncertain = (
            source_spec.parts[:1] in {("__stage_copy__",), ("__heredoc_copy__",)}
            or any(any(marker in part for marker in "*?[") for part in source_spec.parts)
        )
        if not uncertain:
            continue
        copy_destination = PurePosixPath(spec[1])
        if (
            copy_destination == target
            or copy_destination in target.parents
            or target in copy_destination.parents
        ):
            copy_kind = (
                "stage COPY"
                if source_spec.parts[:1] == ("__stage_copy__",)
                else "heredoc COPY"
                if source_spec.parts[:1] == ("__heredoc_copy__",)
                else "wildcard COPY"
            )
            raise ValueError(
                f"cannot determine whether {copy_kind} {source_spec} to {spec[1]} "
                f"changes the type or contents of {target}"
            )

    ordered_outputs = _cached_ordered_copy_outputs(tuple(specs))
    final_target_writer = None
    target_is_directory = False
    for index, outputs in enumerate(ordered_outputs):
        if outputs is None:
            continue
        for output, is_directory in outputs:
            if output == target:
                target_is_directory = is_directory
                final_target_writer = None if is_directory else index
            elif target in output.parents:
                # Writing any child requires the target itself to be a directory.
                target_is_directory = True
                final_target_writer = None
    assert (
        valid_writers
        and not target_is_directory
        and final_target_writer in valid_writers
    ), (
        f"{relative} is not copied from {expected_source} to {expected_destination} "
        "as the effective final input"
    )


def _assert_copy_outputs_are_accounted_for(
    specs: list[tuple[PurePosixPath, str, tuple[str, ...]]],
    *,
    protected_roots: tuple[str, ...],
    expected_files: set[PurePosixPath],
) -> None:
    """Reject untracked or uncertain COPY outputs inside wheel build roots."""
    roots = tuple(PurePosixPath(root) for root in protected_roots)
    ordered_outputs = _cached_ordered_copy_outputs(tuple(specs))
    for spec, outputs in zip(specs, ordered_outputs, strict=True):
        source, raw_destination, _excludes = spec
        destination = PurePosixPath(raw_destination)
        if outputs is None:
            if any(
                destination == root
                or destination in root.parents
                or root in destination.parents
                for root in roots
            ):
                raise ValueError(
                    f"cannot determine whether COPY source {source} to "
                    f"{destination} adds files to a wheel package tree"
                )
            continue
        for output, is_directory in outputs:
            if is_directory or not any(
                output == root or root in output.parents for root in roots
            ):
                continue
            if output not in expected_files:
                raise ValueError(
                    f"unaccounted COPY output {output} enters a wheel package tree"
                )


def _expected_context_copy_destinations(
    *,
    copy_sources: tuple[tuple[str, str], ...],
    rules: list[str] | None = None,
) -> set[PurePosixPath]:
    committed_paths = _git_tree_context_paths()
    expected = set()
    for source_text, destination_text in copy_sources:
        source = _normalize_copy_source(source_text)
        destination = PurePosixPath(destination_text)
        source_path = REPO_ROOT.joinpath(*source.parts)
        source_is_directory = source_path.is_dir()
        spec = (source, destination_text, ())
        for relative in _copy_source_context_entries(spec, rules=rules):
            context_path = REPO_ROOT / relative
            relative_path = PurePosixPath(relative)
            if relative_path not in committed_paths or not context_path.is_file():
                continue
            if source_is_directory:
                suffix = relative_path.relative_to(source)
                output = destination if not suffix.parts else destination / suffix
            elif destination_text.endswith("/"):
                output = destination / relative_path.name
            else:
                output = destination
            expected.add(output)
    return expected


def _git_tree_context_paths() -> frozenset[PurePosixPath]:
    """Use the candidate commit's path inventory, never the ambient filesystem."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(
            "cannot read candidate Git tree for Docker COPY inventory"
            + (f": {detail}" if detail else f" (exit {result.returncode})")
        )
    return frozenset(
        PurePosixPath(path.decode("utf-8", "surrogateescape"))
        for path in result.stdout.split(b"\0")
        if path
    )


def _assert_copy_specs_cover_all_committed_context_files(
    specs: list[tuple[PurePosixPath, str, tuple[str, ...]]],
    *,
    copy_sources: tuple[tuple[str, str], ...],
    rules: list[str] | None = None,
) -> None:
    committed_paths = _git_tree_context_paths()
    for source_text, destination in copy_sources:
        source = _normalize_copy_source(source_text)
        spec = (source, destination, ())
        for relative in _copy_source_context_entries(spec, rules=rules):
            if (
                PurePosixPath(relative) not in committed_paths
                or not (REPO_ROOT / relative).is_file()
            ):
                continue
            _assert_copy_spec_covers_path_at_destination(
                specs,
                relative,
                source_text,
                destination,
            )


def _assert_copy_specs_do_not_target_roots(
    specs: list[tuple[PurePosixPath, str, tuple[str, ...]]],
    *,
    protected_roots: tuple[str, ...],
) -> None:
    roots = tuple(PurePosixPath(root) for root in protected_roots)
    for source, raw_destination, _excludes in specs:
        if source.parts[:1] == ("__workdir__",):
            continue
        destination = PurePosixPath(raw_destination)
        if any(
            destination == root
            or destination in root.parents
            or root in destination.parents
            for root in roots
        ):
            raise ValueError(
                f"COPY source {source} targets protected installed package path "
                f"{destination}"
            )


@lru_cache(maxsize=32)
def _cached_ordered_copy_outputs(
    specs: tuple[tuple[PurePosixPath, str, tuple[str, ...]], ...],
) -> tuple[tuple[tuple[PurePosixPath, bool], ...] | None, ...]:
    """Simulate an ordered COPY list once for every required-input assertion."""
    known_directories: set[PurePosixPath] = set()
    known_files: set[PurePosixPath] = set()
    ordered_outputs = []
    for spec in specs:
        outputs = _copy_spec_outputs(spec, known_directories)
        if outputs is None:
            ordered_outputs.append(None)
            continue
        for output, is_directory in outputs:
            if is_directory:
                conflict = (
                    output in known_files
                    or any(parent in known_files for parent in output.parents)
                )
                if conflict:
                    raise ValueError(f"Docker COPY directory conflicts with file at {output}")
            else:
                conflict = (
                    output in known_directories
                    or any(parent in known_files for parent in output.parents)
                    or any(output in directory.parents for directory in known_directories)
                )
                if conflict:
                    raise ValueError(f"Docker COPY file conflicts with directory at {output}")
            if is_directory:
                known_directories.add(output)
            else:
                known_files.add(output)
            known_directories.update(output.parents)
        ordered_outputs.append(tuple(outputs))
    return tuple(ordered_outputs)


def _copy_spec_outputs(
    spec: tuple[PurePosixPath, str, tuple[str, ...]],
    known_directories: set[PurePosixPath],
) -> list[tuple[PurePosixPath, bool]] | None:
    """List destination paths and their types for one ordered context COPY."""
    source, raw_destination, _excludes = spec
    destination = PurePosixPath(raw_destination)
    if source.parts[:1] == ("__heredoc_copy__",):
        return None
    if source.parts[:1] == ("__workdir__",):
        return [(destination, True)]
    if source.parts[:1] == ("__stage_copy__",):
        return None
    source_root = _normalize_copy_source(str(source))
    if any(any(marker in part for marker in "*?[") for part in source.parts):
        # Wildcard expansion can select either files or directories; model it
        # only when its destination cannot affect the target under inspection.
        return None
    entries = _cached_default_copy_source_context_entries(spec)
    if not entries:
        return []

    source_path = REPO_ROOT.joinpath(*source_root.parts)
    source_is_directory = source_path.is_dir()
    destination_is_directory = (
        raw_destination.endswith("/")
        or destination in known_directories
        or source_is_directory
    )
    outputs: list[tuple[PurePosixPath, bool]] = []
    for relative in entries:
        path = PurePosixPath(relative)
        source_entry = REPO_ROOT / relative
        is_directory = source_entry.is_dir()
        if source_is_directory:
            suffix = path.relative_to(source_root)
            output = destination if not suffix.parts else destination / suffix
        elif destination_is_directory:
            output = destination / path.name
        else:
            output = destination
        outputs.append((output, is_directory))
    return outputs


def _copy_source_context_entries(
    spec: tuple[PurePosixPath, str, tuple[str, ...]],
    *,
    context_root: Path | None = None,
    rules: list[str] | None = None,
) -> list[str]:
    """Expand a COPY source over entries surviving .dockerignore and --exclude."""
    if context_root is None:
        context_root = REPO_ROOT
    source, _destination, excludes = spec
    source = _normalize_copy_source(str(source))
    source_parts = source.parts
    static_prefix = []
    for part in source_parts:
        if any(marker in part for marker in "*?["):
            break
        static_prefix.append(part)
    search_root = context_root.joinpath(*static_prefix) if static_prefix else context_root
    if not search_root.exists():
        return []
    candidates = [search_root, *search_root.rglob("*")]
    if rules is None:
        rules = _dockerignore_rules(context_root=context_root)
    matches = []
    for candidate in candidates:
        relative = candidate.relative_to(context_root).as_posix()
        if not _dockerignore_included(relative, rules):
            continue
        if not _copy_source_matches_path(source, relative):
            continue
        if _copy_excludes_path(source, relative, excludes):
            continue
        matches.append(relative)
    return sorted(matches)


@lru_cache(maxsize=128)
def _cached_default_copy_source_context_entries(
    spec: tuple[PurePosixPath, str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Cache root-context COPY expansion across per-file build assertions."""
    return tuple(_copy_source_context_entries(spec))


def test_dockerignore_later_recursive_exclusion_overrides_an_allowlist():
    rules = ["*", "!README.md", "**/*.md"]
    assert not _dockerignore_included("README.md", rules)


def test_dockerignore_patterns_use_filepath_clean_normalization():
    assert not _dockerignore_included(
        "cmru/src/cmru/cli.py",
        ["cmru/src/./cmru/cli.py"],
    )
    assert _dockerignore_included("README.md", ["*", "!foo/../README.md"])


def test_copy_globs_expand_after_dockerignore_filtering():
    spec = _copy_source_specs_from_instruction("COPY cmru/test* /tmp/\n")[0]
    assert _copy_source_context_entries(spec, rules=_dockerignore_rules()) == []


def test_copy_source_oracle_rejects_malformed_globs(tmp_path):
    (tmp_path / "cmru").mkdir()
    (tmp_path / "cmru" / "file.py").write_text("source", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid COPY filepath pattern"):
        _copy_source_specs_from_instruction("COPY cmru/[oops /dst/\n")


def test_copy_source_parser_rejects_malformed_globs_in_stage_copy():
    with pytest.raises(ValueError, match="invalid COPY filepath pattern"):
        _copy_source_specs_from_instruction(
            "COPY --from=wheelbuild wheels/[oops /tmp/\n"
        )


def test_copy_source_parser_rejects_unmodeled_parents_option():
    with pytest.raises(ValueError, match="unsupported COPY option: --parents"):
        _copy_source_specs_from_instruction(
            "COPY --parents cmru/ /src/cmru/\n"
        )


def test_copy_source_parser_requires_directory_destination_for_multiple_sources():
    with pytest.raises(ValueError, match="multiple COPY sources require a destination"):
        _copy_source_specs_from_instruction(
            "COPY cmru/pyproject.toml nyxloom/pyproject.toml ./nyxloom/pyproject.toml\n"
        )


def test_copy_exclude_filters_matched_context_paths(tmp_path):
    (tmp_path / "cmru" / "src").mkdir(parents=True)
    (tmp_path / "cmru" / "tests").mkdir(parents=True)
    (tmp_path / "cmru" / "src" / "cli.py").write_text("cli", encoding="utf-8")
    (tmp_path / "cmru" / "tests" / "test_cli.py").write_text("test", encoding="utf-8")
    spec = _copy_source_specs_from_instruction(
        "COPY --exclude=tests cmru/ /src/cmru/\n"
    )[0]
    entries = _copy_source_context_entries(spec, context_root=tmp_path, rules=[])
    assert "cmru/src/cli.py" in entries
    assert "cmru/tests/test_cli.py" not in entries


def test_copy_exclude_applies_to_a_directory_matched_by_a_wildcard(tmp_path):
    source_root = tmp_path / "cmru"
    (source_root / "src").mkdir(parents=True)
    (source_root / "README.md").write_text("readme", encoding="utf-8")
    (source_root / "src" / "module.py").write_text("module", encoding="utf-8")
    spec = _copy_source_specs_from_instruction(
        "COPY --exclude=src cmru/* /dst/\n"
    )[0]

    entries = _copy_source_context_entries(spec, context_root=tmp_path, rules=[])

    assert "cmru/README.md" in entries
    assert "cmru/src" not in entries
    assert "cmru/src/module.py" not in entries


def test_copy_exclude_matches_a_direct_file_source(tmp_path):
    (tmp_path / "foo.txt").write_text("foo", encoding="utf-8")
    spec = _copy_source_specs_from_instruction(
        "COPY --exclude=*.txt foo.txt /dst/\n"
    )[0]
    assert "foo.txt" not in _copy_source_context_entries(
        spec, context_root=tmp_path, rules=[],
    )


def test_copy_exclude_cannot_silently_remove_required_wheel_sources(tmp_path):
    source_root = tmp_path / "libraries" / "cli-extended" / "src"
    package_file = source_root / "cli_extended" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("wheel source", encoding="utf-8")
    spec = _copy_source_specs_from_instruction(
        "COPY --exclude=cli_extended libraries/cli-extended/src /build/src\n"
    )[0]
    entries = _copy_source_context_entries(spec, context_root=tmp_path, rules=[])
    relative = package_file.relative_to(tmp_path).as_posix()

    # The source directory still exists, but its only package payload is gone.
    assert "libraries/cli-extended/src" in entries
    assert relative not in entries
    assert not _copy_spec_covers_context_path(spec, relative)


def test_copy_oracle_rejects_required_source_copied_only_to_wrong_destination():
    specs = _dockerfile_copy_specs(
        "COPY --exclude=cli_extended libraries/cli-extended/src ./libraries/cli-extended/src\n"
        "COPY libraries/cli-extended/src /tmp/unrelated/\n"
    )
    with pytest.raises(AssertionError, match="not copied from"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "libraries/cli-extended/src/cli_extended/__init__.py",
            "libraries/cli-extended/src",
            "./libraries/cli-extended/src",
        )


def test_dockerignore_patterns_support_go_character_classes_and_escaping():
    assert not _dockerignore_included(
        "libraries/cli-extended/README.md", ["libraries/[^w]*"]
    )
    assert _dockerignore_included(
        "libraries/cli-extended/README.md", ["libraries/[!w]*"]
    )
    assert not _dockerignore_included(
        "libraries/cli-extended/README.md",
        [r"libraries/cli-extended/READM\E.md"],
    )
    assert _dockerignore_pattern_matches(r"literal\*.txt", "literal*.txt")
    assert not _dockerignore_pattern_matches(r"literal\*.txt", "literalX.txt")


def test_dockerignore_child_exception_can_reinclude_below_excluded_parent():
    assert _dockerignore_included("private/needed.txt", ["*", "!private/needed.txt"])
    assert not _dockerignore_included("private/needed.txt", ["*"])


def test_dockerfile_specific_dockerignore_overrides_context_root_file(tmp_path):
    context = tmp_path
    docker_dir = context / "tester-unified"
    docker_dir.mkdir()
    dockerfile = docker_dir / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    (context / ".dockerignore").write_text("root-rule\n", encoding="utf-8")
    specific = docker_dir / "Dockerfile.dockerignore"
    specific.write_text("specific-rule\n", encoding="utf-8")

    assert _dockerignore_rules(dockerfile, context) == ["specific-rule"]


def test_dockerignore_excludes_test_suites_without_dropping_package_data_named_tests():
    for suite in (
        "ciu/tests/test_cli.py",
        "cmru/tests/test_cli.py",
        "assay/gate/tests/test_runner.py",
        "assay/analysis/tests/test_report.py",
        "assay/gate/python/fixtures/sql/tests/expected.sql",
        "topos/tests/test_cli.py",
        "nyxloom/tests/test_cli.py",
        "scripts/cgroup-profiler/tests/test_profile.py",
    ):
        assert not _dockerignore_included(suite), suite
    assert _dockerignore_included(
        "libraries/cli-extended/src/cli_extended/skills/tests/SKILL.md"
    )


def _dockerfile_required_context_files() -> list[str]:
    """Files consumed by dependency discovery or the offline CMRU wheel build."""
    if gen is None:
        return []  # isolated cmru canary tree; module-level skip handles the tests
    required = {
        "scripts/" + project_file if project_file.startswith("cgroup-profiler/") else project_file
        for project_file, _extras in gen.PROJECTS
    }
    required.update({
        "tester-unified/gen-requirements.py",
        "tester-unified/fetch-cli-extended.py",
        "tester-unified/local-wheel/.gitkeep",
    })
    # The Dockerfile builds CMRU from copied source and packages worktree from
    # its sibling source tree; all their package data must survive filtering.
    ignored_components = {
        ".git", ".venv", ".tox", ".nox", "__pycache__", ".pytest_cache", ".hypothesis",
        ".mypy_cache", "node_modules", ".worktrees",
    }
    for source_root in (
        REPO_ROOT / "cmru" / "src" / "cmru",
        REPO_ROOT / "libraries" / "worktree" / "src" / "worktree",
    ):
        required.update(
            path.relative_to(REPO_ROOT).as_posix()
            for path in source_root.rglob("*")
            if path.is_file()
            and ignored_components.isdisjoint(path.relative_to(source_root).parts)
            and path.suffix != ".pyc"
        )
    return sorted(required)


def _cmru_wheel_inputs() -> list[str]:
    """Files required to build CMRU and its bundled worktree package."""
    return [
        path for path in _dockerfile_required_context_files()
        if path == "cmru/pyproject.toml"
        or path.startswith("cmru/src/cmru/")
        or path.startswith("libraries/worktree/src/worktree/")
    ]


def _wheel_python_modules(source_root: Path) -> list[Path]:
    """Python files that are real wheel inputs, not generated or ignored data."""
    ignored_components = {
        ".git", ".venv", ".tox", ".nox", "__pycache__", ".pytest_cache", ".hypothesis",
        ".mypy_cache", "node_modules", ".worktrees",
    }
    return sorted(
        path
        for path in source_root.rglob("*.py")
        if path.is_file()
        and ignored_components.isdisjoint(path.relative_to(source_root).parts)
        and not any(
            part.endswith(".egg-info")
            for part in path.relative_to(source_root).parts
        )
    )


def _nyxloom_cli_extended_wheel_inputs() -> list[str]:
    """Every non-generated source file used by Nyxloom's source-built wheel."""
    required = {
        "libraries/cli-extended/pyproject.toml",
        "libraries/cli-extended/README.md",
    }
    source_root = REPO_ROOT / "libraries" / "cli-extended" / "src"
    ignored_components = {
        ".git", ".venv", ".tox", ".nox", "__pycache__", ".pytest_cache", ".hypothesis",
        ".mypy_cache", "node_modules", ".worktrees",
    }
    required.update(
        path.relative_to(REPO_ROOT).as_posix()
        for path in source_root.rglob("*")
        if path.is_file()
        and ignored_components.isdisjoint(path.relative_to(source_root).parts)
        and not any(part.endswith(".egg-info") for part in path.relative_to(source_root).parts)
        and path.suffix != ".pyc"
    )
    return sorted(required)


def _nyxloom_wheel_inputs() -> list[str]:
    """Every non-generated Nyxloom source file copied into its wheel build."""
    required = {"nyxloom/pyproject.toml"}
    ignored_components = {
        ".git", ".venv", ".tox", ".nox", "__pycache__", ".pytest_cache", ".hypothesis",
        ".mypy_cache", "node_modules", ".worktrees",
    }
    project_root = REPO_ROOT / "nyxloom"
    required.update(
        path.relative_to(REPO_ROOT).as_posix()
        for path in _setuptools_selected_package_files(project_root)
        if ignored_components.isdisjoint(path.relative_to(project_root).parts)
        and not any(part.endswith(".egg-info") for part in path.relative_to(project_root).parts)
        and path.suffix != ".pyc"
    )
    return sorted(required)


def _setuptools_package_inventory(
    project_root: Path,
) -> tuple[dict[str, Path], dict[str, Path], list[Path]]:
    """Discover setuptools packages and apply their configured selection."""
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = config.get("tool", {}).get("setuptools", {})
    package_dir = setuptools.get("package-dir", {})
    if set(package_dir) - {""}:
        raise ValueError("explicit per-package setuptools package-dir mappings are unsupported")
    find = setuptools.get("packages", {}).get("find", {})
    search_roots = find.get("where") or [package_dir.get("", ".")]
    includes = find.get("include", ["*"])
    excludes = find.get("exclude", [])
    namespaces = find.get("namespaces", True)
    if not isinstance(namespaces, bool):
        raise ValueError("setuptools package discovery namespaces must be boolean")
    if any(not isinstance(pattern, str) for pattern in [*includes, *excludes]):
        raise ValueError("setuptools package discovery patterns must be strings")
    discovered: dict[str, Path] = {}
    resolved_search_roots: list[Path] = []
    for relative_root in search_roots:
        search_root = (project_root / relative_root).resolve()
        if not search_root.is_dir():
            raise ValueError(f"setuptools package search root is missing: {search_root}")
        resolved_search_roots.append(search_root)
        package_directories: set[Path] = set()
        for module in search_root.rglob("*.py"):
            parent = module.parent
            while parent != search_root:
                if namespaces or (parent / "__init__.py").is_file():
                    package_directories.add(parent)
                parent = parent.parent
        for package_root in package_directories:
            package_name = ".".join(package_root.relative_to(search_root).parts)
            if package_name in discovered:
                raise ValueError(f"duplicate setuptools package discovery: {package_name}")
            discovered[package_name] = package_root
    selected = {
        name: package_root
        for name, package_root in discovered.items()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in includes)
        and not any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)
    }
    return discovered, selected, resolved_search_roots


def _setuptools_selected_package_roots(project_root: Path) -> list[Path]:
    """Derive selected package directories from setuptools discovery config."""
    _discovered, selected, _search_roots = _setuptools_package_inventory(project_root)
    return sorted(selected.values())


def _setuptools_selected_package_files(project_root: Path) -> list[Path]:
    """Return selected package files plus explicit package-data inputs."""
    discovered, selected, search_roots = _setuptools_package_inventory(project_root)
    selected_paths = set(selected.values())
    files: set[Path] = set()
    for search_root in search_roots:
        for path in search_root.rglob("*"):
            if not path.is_file():
                continue
            containing = [
                package_root
                for package_root in discovered.values()
                if package_root == path.parent or package_root in path.parents
            ]
            if not containing:
                continue
            nearest_package = max(containing, key=lambda root: len(root.parts))
            if nearest_package in selected_paths:
                files.add(path)
    files.update(_setuptools_package_data_files(project_root, selected))
    return sorted(files)


def _setuptools_package_data_files(
    project_root: Path,
    selected: dict[str, Path] | None = None,
) -> set[Path]:
    """Resolve explicit package-data globs even below excluded child packages."""
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = config.get("tool", {}).get("setuptools", {})
    package_data = setuptools.get("package-data", {})
    if not isinstance(package_data, dict):
        raise ValueError("setuptools package-data must be a mapping")
    if selected is None:
        _discovered, selected, _search_roots = _setuptools_package_inventory(project_root)

    files: set[Path] = set()
    for package_pattern, patterns in package_data.items():
        if not isinstance(package_pattern, str):
            raise ValueError("setuptools package-data package names must be strings")
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list) or any(
            not isinstance(pattern, str) for pattern in patterns
        ):
            raise ValueError("setuptools package-data patterns must be strings")
        matching_packages = {
            name: package_root
            for name, package_root in selected.items()
            if fnmatch.fnmatchcase(name, package_pattern)
        }
        for package_root in matching_packages.values():
            for pattern in patterns:
                relative_pattern = PurePosixPath(pattern)
                if relative_pattern.is_absolute() or ".." in relative_pattern.parts:
                    raise ValueError(
                        f"setuptools package-data pattern escapes its package: {pattern}"
                    )
                for path in package_root.glob(pattern):
                    if path.is_file() and package_root in path.parents:
                        files.add(path)
    return files


@pytest.mark.parametrize("path", _dockerfile_required_context_files())
def test_dockerignore_admits_every_file_the_offline_build_needs(path):
    assert _dockerignore_included(path), path


@pytest.mark.parametrize("path", _nyxloom_cli_extended_wheel_inputs())
def test_dockerignore_admits_cli_extended_wheel_inputs_for_nyxloom(path):
    # The same repo-root context serves Nyxloom's source-built wheel. The
    # tester-unified Dockerfile still installs a wheel input and does not COPY
    # these sources; see the Dockerfile-specific assertions above.
    assert _dockerignore_included(path, _dockerignore_rules(NYXLOOM_DOCKERFILE)), path


@pytest.mark.parametrize("path", _nyxloom_wheel_inputs())
def test_dockerignore_admits_nyxloom_wheel_inputs_for_nyxloom(path):
    assert _dockerignore_included(path, _dockerignore_rules(NYXLOOM_DOCKERFILE)), path


def test_nyxloom_dockerfile_copies_all_cli_extended_wheel_inputs():
    text = NYXLOOM_DOCKERFILE.read_text(encoding="utf-8")
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        occurrence=0,
        expected_workdir="/build/libraries/cli-extended",
        run_sensitive_sources=(
            "libraries/cli-extended/pyproject.toml",
            "libraries/cli-extended/README.md",
            "libraries/cli-extended/src",
        ),
        allowed_pre_sensitive_run_bodies=(
            NYXLOOM_CLI_EXTENDED_SOURCE_CLEAN_RUN,
            NYXLOOM_BUILD_TOOL_RUN,
        ),
        expected_run_body=NYXLOOM_CLI_EXTENDED_WHEEL_BUILD_BODY,
    )
    pairs = {
        (str(source).rstrip("/"), destination)
        for source, destination, _excludes in specs
    }
    assert {
        ("libraries/cli-extended/pyproject.toml", "/build/libraries/cli-extended/"),
        ("libraries/cli-extended/README.md", "/build/libraries/cli-extended/"),
        ("libraries/cli-extended/src", "/build/libraries/cli-extended/src"),
    } <= pairs
    for path in _nyxloom_cli_extended_wheel_inputs():
        if path in {
            "libraries/cli-extended/pyproject.toml",
            "libraries/cli-extended/README.md",
        }:
            source = path
            destination = "/build/libraries/cli-extended/"
        else:
            source = "libraries/cli-extended/src"
            destination = "/build/libraries/cli-extended/src"
        _assert_copy_spec_covers_path_at_destination(
            specs, path, source, destination,
        )
    cli_extended_copy_sources = (
        ("libraries/cli-extended/pyproject.toml", "/build/libraries/cli-extended/"),
        ("libraries/cli-extended/README.md", "/build/libraries/cli-extended/"),
        ("libraries/cli-extended/src", "/build/libraries/cli-extended/src"),
    )
    rules = _dockerignore_rules(NYXLOOM_DOCKERFILE)
    _assert_copy_specs_cover_all_committed_context_files(
        specs,
        copy_sources=cli_extended_copy_sources,
        rules=rules,
    )
    _assert_copy_outputs_are_accounted_for(
        specs,
        protected_roots=("/build/libraries/cli-extended",),
        expected_files=_expected_context_copy_destinations(
            copy_sources=cli_extended_copy_sources,
            rules=rules,
        ),
    )


def test_nyxloom_cli_extended_wheel_source_tree_has_usable_copied_modules():
    package_root = (
        REPO_ROOT / "libraries" / "cli-extended" / "src" / "cli_extended"
    )
    modules = _wheel_python_modules(package_root)
    assert modules
    assert package_root / "__init__.py" in modules
    specs = _dockerfile_copy_specs_before_command(
        NYXLOOM_DOCKERFILE.read_text(encoding="utf-8"),
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        occurrence=0,
        expected_workdir="/build/libraries/cli-extended",
        run_sensitive_sources=(
            "libraries/cli-extended/pyproject.toml",
            "libraries/cli-extended/README.md",
            "libraries/cli-extended/src",
        ),
        allowed_pre_sensitive_run_bodies=(
            NYXLOOM_CLI_EXTENDED_SOURCE_CLEAN_RUN,
            NYXLOOM_BUILD_TOOL_RUN,
        ),
        expected_run_body=NYXLOOM_CLI_EXTENDED_WHEEL_BUILD_BODY,
    )
    rules = _dockerignore_rules(NYXLOOM_DOCKERFILE)
    for module in modules:
        relative = module.relative_to(REPO_ROOT).as_posix()
        assert _dockerignore_included(relative, rules), relative
        _assert_copy_spec_covers_path_at_destination(
            specs,
            relative,
            "libraries/cli-extended/src",
            "/build/libraries/cli-extended/src",
        )


def test_nyxloom_dockerfile_copies_all_nyxloom_wheel_inputs():
    text = NYXLOOM_DOCKERFILE.read_text(encoding="utf-8")
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        occurrence=1,
        expected_workdir="/build/nyxloom",
        run_sensitive_sources=("nyxloom/pyproject.toml", "nyxloom/src"),
        allowed_pre_sensitive_run_bodies=(
            NYXLOOM_BUILD_TOOL_RUN,
            NYXLOOM_CLI_EXTENDED_SOURCE_CLEAN_RUN,
            NYXLOOM_CLI_EXTENDED_BUILD_RUN,
            NYXLOOM_SOURCE_CLEAN_RUN,
        ),
        expected_run_body=NYXLOOM_WHEEL_BUILD_BODY,
    )
    pairs = {(str(source).rstrip("/"), destination) for source, destination, _ in specs}
    assert {
        ("nyxloom/pyproject.toml", "/build/nyxloom/pyproject.toml"),
        ("nyxloom/src", "/build/nyxloom/src"),
    } <= pairs
    for path in _nyxloom_wheel_inputs():
        if path == "nyxloom/pyproject.toml":
            source, destination = path, "/build/nyxloom/pyproject.toml"
        else:
            source, destination = "nyxloom/src", "/build/nyxloom/src"
        _assert_copy_spec_covers_path_at_destination(
            specs, path, source, destination,
        )
    nyxloom_copy_sources = (
        ("nyxloom/pyproject.toml", "/build/nyxloom/pyproject.toml"),
        ("nyxloom/src", "/build/nyxloom/src"),
    )
    rules = _dockerignore_rules(NYXLOOM_DOCKERFILE)
    _assert_copy_specs_cover_all_committed_context_files(
        specs,
        copy_sources=nyxloom_copy_sources,
        rules=rules,
    )
    _assert_copy_outputs_are_accounted_for(
        specs,
        protected_roots=("/build/nyxloom",),
        expected_files=_expected_context_copy_destinations(
            copy_sources=nyxloom_copy_sources,
            rules=rules,
        ),
    )


def test_nyxloom_wheel_source_tree_has_usable_copied_modules():
    project_root = REPO_ROOT / "nyxloom"
    package_roots = _setuptools_selected_package_roots(REPO_ROOT / "nyxloom")
    assert project_root / "src" / "nyxloom" in package_roots
    assert package_roots
    modules = [
        path for path in _setuptools_selected_package_files(project_root)
        if path.suffix == ".py"
    ]
    assert modules
    assert project_root / "src" / "nyxloom" / "__init__.py" in modules
    specs = _dockerfile_copy_specs_before_command(
        NYXLOOM_DOCKERFILE.read_text(encoding="utf-8"),
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        occurrence=1,
        expected_workdir="/build/nyxloom",
        run_sensitive_sources=("nyxloom/pyproject.toml", "nyxloom/src"),
        allowed_pre_sensitive_run_bodies=(
            NYXLOOM_BUILD_TOOL_RUN,
            NYXLOOM_CLI_EXTENDED_SOURCE_CLEAN_RUN,
            NYXLOOM_CLI_EXTENDED_BUILD_RUN,
            NYXLOOM_SOURCE_CLEAN_RUN,
        ),
        expected_run_body=NYXLOOM_WHEEL_BUILD_BODY,
    )
    rules = _dockerignore_rules(NYXLOOM_DOCKERFILE)
    for module in modules:
        relative = module.relative_to(REPO_ROOT).as_posix()
        assert _dockerignore_included(relative, rules), relative
        _assert_copy_spec_covers_path_at_destination(
            specs, relative, "nyxloom/src", "/build/nyxloom/src",
        )


def test_nyxloom_final_image_installs_wheels_from_the_checked_build_stage():
    text = NYXLOOM_DOCKERFILE.read_text(encoding="utf-8")
    instructions = _dockerfile_instructions_by_stage(text)
    wheelbuild_ordinals = {
        ordinal for ordinal, selector, _index, _instruction in instructions
        if selector == "wheelbuild"
    }
    assert len(wheelbuild_ordinals) == 1
    wheelbuild_ordinal = next(iter(wheelbuild_ordinals))
    wheelbuild_instructions = [
        instruction
        for ordinal, _selector, _index, instruction in instructions
        if ordinal == wheelbuild_ordinal
    ]
    assert wheelbuild_instructions[-1] == f"RUN {NYXLOOM_WHEEL_BUILD_BODY}"

    runtime_ordinals = {
        ordinal
        for ordinal, _selector, _index, instruction in instructions
        if re.match(r"(?i)^FROM(?:[ \t]|$)", instruction)
        and _dockerfile_from_base(instruction) == "${nyxloom_base}"
    }
    assert len(runtime_ordinals) == 1
    runtime_ordinal = next(iter(runtime_ordinals))
    runtime_instructions = [
        (index, instruction)
        for index, (ordinal, _selector, _stage_index, instruction)
        in enumerate(instructions)
        if ordinal == runtime_ordinal
    ]
    assert [instruction for _index, instruction in runtime_instructions] == [
        "FROM ${NYXLOOM_BASE}",
        "USER root",
        f"RUN {NYXLOOM_FINAL_WHEELS_CLEAN_RUN}",
        "COPY --from=wheelbuild /wheels/*.whl /tmp/wheels/",
        f"RUN {NYXLOOM_FINAL_WHEEL_INSTALL_RUN_BODY}",
        "USER 1003",
    ]

    wheel_copy_specs = []
    for index, instruction in runtime_instructions:
        if re.match(r"(?i)^COPY(?:[ \t]|$)", instruction):
            wheel_copy_specs.extend(
                (index, source, destination)
                for source, destination, _excludes
                in _copy_source_specs_from_instruction(
                    instruction, include_stage=True,
                )
                if destination == "/tmp/wheels/"
            )
    assert len(wheel_copy_specs) == 1
    copy_index, source, destination = wheel_copy_specs[0]
    assert source.parts == (
        "__stage_copy__", "wheelbuild", "wheels", "*.whl",
    )
    assert destination == "/tmp/wheels/"
    copy_position = next(
        position
        for position, (index, _instruction) in enumerate(runtime_instructions)
        if index == copy_index
    )
    assert copy_position > 0
    assert runtime_instructions[copy_position - 1][1] == (
        f"RUN {NYXLOOM_FINAL_WHEELS_CLEAN_RUN}"
    )
    assert copy_position + 1 < len(runtime_instructions)
    install_index, next_instruction = runtime_instructions[copy_position + 1]
    assert next_instruction == f"RUN {NYXLOOM_FINAL_WHEEL_INSTALL_RUN_BODY}"
    _assert_no_mutating_instructions_after_install(
        instructions,
        install_index=install_index,
    )


def test_setuptools_package_roots_follow_package_discovery_config(tmp_path):
    project_root = tmp_path / "example"
    source_root = project_root / "python"
    for package in ("selected", "other"):
        package_root = source_root / package
        package_root.mkdir(parents=True)
        (package_root / "__init__.py").write_text("", encoding="utf-8")
    pyproject = project_root / "pyproject.toml"
    pyproject.write_text(
        "[tool.setuptools.packages.find]\n"
        'where = ["python"]\n'
        'include = ["selected*"]\n',
        encoding="utf-8",
    )

    assert _setuptools_selected_package_roots(project_root) == [source_root / "selected"]

    pyproject.write_text(
        "[tool.setuptools.packages.find]\n"
        'where = ["python"]\n'
        'include = ["other*"]\n',
        encoding="utf-8",
    )
    assert _setuptools_selected_package_roots(project_root) == [source_root / "other"]


def test_setuptools_discovery_includes_namespaces_and_excludes_nested_packages(tmp_path):
    project_root = tmp_path / "example"
    source_root = project_root / "src"
    package_init = source_root / "nyxloom" / "__init__.py"
    package_init.parent.mkdir(parents=True)
    package_init.write_text("", encoding="utf-8")
    excluded_module = source_root / "nyxloom" / "devtools" / "tool.py"
    excluded_module.parent.mkdir()
    (excluded_module.parent / "__init__.py").write_text("", encoding="utf-8")
    excluded_module.write_text("", encoding="utf-8")
    namespace_module = source_root / "nyxloom_plugin" / "feature.py"
    namespace_module.parent.mkdir()
    namespace_module.write_text("", encoding="utf-8")
    schemas_package = source_root / "nyxloom" / "schemas"
    schemas_package.mkdir()
    schemas_init = schemas_package / "__init__.py"
    schemas_init.write_text("", encoding="utf-8")
    schema_data = schemas_package / "contract.json"
    schema_data.write_text("{}", encoding="utf-8")
    (project_root / "pyproject.toml").write_text(
        '[tool.setuptools.package-dir]\n"" = "src"\n'
        "[tool.setuptools.packages.find]\n"
        'where = ["src"]\n'
        'include = ["nyxloom*"]\n'
        'exclude = ["nyxloom.devtools*", "nyxloom.schemas*"]\n'
        "[tool.setuptools.package-data]\n"
        'nyxloom = ["schemas/*.json"]\n',
        encoding="utf-8",
    )

    package_roots = _setuptools_selected_package_roots(project_root)
    selected_files = _setuptools_selected_package_files(project_root)
    excluded_copy = _copy_source_specs_from_instruction(
        "COPY --exclude=nyxloom_plugin src/ /dst/\n"
    )[0]

    assert project_root / "src" / "nyxloom" in package_roots
    assert project_root / "src" / "nyxloom" / "devtools" not in package_roots
    assert project_root / "src" / "nyxloom_plugin" in package_roots
    assert namespace_module in selected_files
    assert excluded_module not in selected_files
    assert schemas_init not in selected_files
    assert schema_data in selected_files
    assert not _copy_spec_covers_context_path(
        excluded_copy,
        namespace_module.relative_to(tmp_path).as_posix(),
    )


def test_nyxloom_copy_oracle_rejects_inputs_after_or_outside_the_build_stage():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "RUN python -m build --wheel --outdir /wheels\n"
        "COPY nyxloom/src ./nyxloom/src\n"
        "FROM runtime:latest AS final\n"
        "COPY nyxloom/src /app/nyxloom/src\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build",
    )
    with pytest.raises(AssertionError, match="not copied from"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/src/nyxloom/__init__.py",
            "nyxloom/src",
            "/build/nyxloom/src",
        )


def test_nyxloom_copy_oracle_rejects_a_later_project_metadata_overwrite():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml\n"
        "COPY cmru/pyproject.toml ./nyxloom/pyproject.toml\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(AssertionError, match="effective final input"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_nyxloom_copy_oracle_keeps_unknown_stage_source_indeterminate():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml\n"
        "COPY --from=build-tools /tmp/unrelated.txt ./nyxloom/\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(ValueError, match="cannot determine whether stage COPY"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_nyxloom_copy_oracle_keeps_overlapping_inline_heredoc_indeterminate():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml\n"
        "COPY <<EOF ./nyxloom/pyproject.toml\n"
        "[project]\nname = 'wrong'\n"
        "EOF\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(ValueError, match="heredoc COPY"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_wheel_input_oracle_rejects_alias_colliding_with_unnamed_stage_selector():
    text = (
        "FROM base:latest\n"
        "COPY cmru/ /src/cmru/\n"
        "FROM base:latest AS stage-0\n"
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="stage selector 'stage-0' is ambiguous"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_inherited_build_stage():
    text = (
        "FROM base:latest AS seed\n"
        "RUN mkdir -p /src/cmru/src/cmru "
        "&& printf 'x=1\\n' > /src/cmru/src/cmru/injected.py\n"
        "FROM seed AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="wheel inputs inherited from a prior"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_copy_from_another_build_stage():
    text = (
        "FROM base:latest AS seed\n"
        "COPY cmru/ /seed/cmru/\n"
        "FROM base:latest AS stage-0\n"
        "COPY --from=seed /seed/cmru/ /src/cmru/\n"
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="wheel inputs copied from another build stage"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_nyxloom_copy_oracle_rejects_a_file_destination_with_trailing_slash():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml/\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(AssertionError, match="effective final input"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_nyxloom_copy_oracle_tracks_a_file_destination_created_as_directory():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml/\n"
        "COPY cmru/pyproject.toml ./nyxloom/pyproject.toml\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(AssertionError, match="effective final input"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_nyxloom_copy_oracle_tracks_a_directory_created_by_workdir():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build/nyxloom/pyproject.toml\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/pyproject.toml\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(AssertionError, match="effective final input"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/pyproject.toml",
            "nyxloom/pyproject.toml",
            "/build/nyxloom/pyproject.toml",
        )


def test_nyxloom_copy_oracle_rejects_a_directory_copy_onto_an_existing_file():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "COPY nyxloom/pyproject.toml ./nyxloom/src\n"
        "COPY nyxloom/src ./nyxloom/src\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        "wheelbuild",
        r"python -m build --wheel --outdir /wheels",
        expected_workdir="/build/nyxloom",
    )
    with pytest.raises(ValueError, match="Docker COPY directory conflicts with file"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "nyxloom/src/nyxloom/__init__.py",
            "nyxloom/src",
            "/build/nyxloom/src",
        )


def test_nyxloom_build_oracle_rejects_wrong_project_workdir():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build\n"
        "WORKDIR /build/nyxloom\n"
        "RUN python -m build --wheel --outdir /wheels\n"
    )
    with pytest.raises(ValueError, match="build command runs in"):
        _dockerfile_copy_specs_before_command(
            text,
            "wheelbuild",
            r"python -m build --wheel --outdir /wheels",
            expected_workdir="/build/libraries/cli-extended",
        )


def test_nyxloom_build_oracle_rejects_shell_directory_change():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build/libraries/cli-extended\n"
        "RUN cd /tmp && python -m build --wheel --outdir /wheels\n"
    )
    with pytest.raises(ValueError, match="changes directory"):
        _dockerfile_copy_specs_before_command(
            text,
            "wheelbuild",
            r"python -m build --wheel --outdir /wheels",
            expected_workdir="/build/libraries/cli-extended",
        )


def test_nyxloom_build_oracle_rejects_a_short_circuited_build_command():
    text = (
        "FROM python:3.14-slim AS wheelbuild\n"
        "WORKDIR /build/libraries/cli-extended\n"
        "RUN true || python -m build --wheel --outdir /wheels\n"
    )
    with pytest.raises(ValueError, match="conditional or pipeline"):
        _dockerfile_copy_specs_before_command(
            text,
            "wheelbuild",
            r"python -m build --wheel --outdir /wheels",
            expected_workdir="/build/libraries/cli-extended",
        )


def test_every_nyxloom_dockerfile_copy_source_is_admitted_by_its_dockerignore():
    text = NYXLOOM_DOCKERFILE.read_text(encoding="utf-8")
    rules = _dockerignore_rules(NYXLOOM_DOCKERFILE)
    for spec in _dockerfile_copy_specs(text):
        source = spec[0]
        assert _copy_source_context_entries(spec, rules=rules), source


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, NYXLOOM_DOCKERFILE])
@pytest.mark.parametrize("path", [
    "tester-unified/run",
    "tester-unified/Dockerfile",
    # The root ignore applies to both images; project suites are not part of
    # either image's dependency metadata or source-built wheel inputs.
    "cmru/tests/test_github_resolve.py",
    "nyxloom/tests/test_workflow.py",
    "assay/tests/test_smoke.py",
    "ciu/tests/test_cli.py",
    # cli-extended is a wheel dependency: the tester image copies none of its source or build inputs
    # (asserted on the Dockerfile's COPY sources below). Its pyproject.toml and README.md are NOT in
    # this list on purpose: nyxloomd's image builds the cli-extended wheel from monorepo source
    # (NYX-CLIX, c6557bb79, 2026-10-07, after this test was written) and the repo-root .dockerignore
    # whitelists them for it - see the nyxloom-need test below. Only its tests stay excluded.
    "libraries/cli-extended/tests/test_x.py",
    "libraries/worktree/tests/test_core.py",
    ".git/config",
    "cmru/.git/config",
    "cmru/.venv/bin/python",
    "cmru/src/cmru/__pycache__/config.cpython-314.pyc",
    "cmru/src/cmru/config.pyc",
    "cmru/.pytest_cache/v/cache/nodeids",
    "cmru/.assay/progress-cmru.jsonl",
    "assay/.assay/progress-r2.jsonl",
    "cmru/.run-gate/history.json",
    "cmru/src/cmru/.tox/py314/lib/python3.14/site-packages/shadow.py",
    "cmru/src/cmru/.nox/py314/lib/python3.14/site-packages/shadow.py",
    "cmru/.hypothesis/examples/1",
    "cmru/.mypy_cache/3.14/cache.db",
    "cmru/node_modules/example/index.js",
    "cmru/.worktrees/feature/HEAD",
    "nyxloom/src/nyxloom/__pycache__/config.cpython-314.pyc",
    "nyxloom/src/nyxloom/.pytest_cache/v/cache/nodeids",
    "nyxloom/src/nyxloom/.tox/py314/lib/python3.14/site-packages/shadow.py",
    "nyxloom/src/nyxloom/.nox/py314/lib/python3.14/site-packages/shadow.py",
    "libraries/cli-extended/src/cli_extended/__pycache__/parser.cpython-314.pyc",
    "libraries/cli-extended/src/cli_extended/.tox/py314/lib/python3.14/site-packages/shadow.py",
    "libraries/cli-extended/src/cli_extended/.nox/py314/lib/python3.14/site-packages/shadow.py",
    "libraries/cli-extended/.venv/bin/python",
])
def test_dockerignore_still_excludes_everything_else(dockerfile, path):
    assert not _dockerignore_included(path, _dockerignore_rules(dockerfile)), (dockerfile, path)


def test_refusal_set_includes_every_pyproject_name_under_the_root(tmp_path):
    _all_projects(tmp_path)
    assert "foo-lib" not in gen.derive_internal(tmp_path)
    _tree(tmp_path, libraries__foo='[project]\nname = "Foo_Lib"\n', brandnew='[project]\nname = "brand.new"\n')
    derived = gen.derive_internal(tmp_path)
    assert {"foo-lib", "brand-new"} <= derived
    assert gen.ESTATE_INTERNAL <= derived  # the static list stays as a floor


def test_new_library_name_is_refused_as_a_dependency(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\ndependencies = ["foo_lib>=1"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    assert "foo_lib>=1" in list(gen.requirements(tmp_path))  # unknown name: third-party
    _tree(tmp_path, libraries__foo='[project]\nname = "foo-lib"\n')
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.requirements(tmp_path))
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.build_requirements(
            _tree(tmp_path / "y", libraries__foo='[project]\nname = "foo-lib"\n',
                  cmru='[build-system]\nrequires = ["foo-lib"]\n'),
            ["cmru/pyproject.toml"],
        ))


def test_dockerfile_builds_cmru_from_the_build_requires_mode():
    """The throwaway build venv must get the pinned backends of the one internal
    project it builds (a dropped one would only fail at image build time)."""
    code = _dockerfile_run_lines()
    match = re.search(r"--build-requires (?P<paths>[^\n\\]*)", code)
    assert match is not None
    assert match["paths"].split() == ["cmru/pyproject.toml"]
    wheels = [
        command for command in _pip_commands(code)
        if "pip wheel" in command and "/src/cmru" in command
    ]
    assert len(wheels) == 1, wheels


def test_tester_unified_build_instructions_use_the_exact_wheel_builder():
    readme = (REPO_ROOT / "tester-unified" / "README.md").read_text(
        encoding="utf-8",
    )
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    builder = (REPO_ROOT / "tester-unified" / "build-image.sh").read_text(
        encoding="utf-8",
    )

    assert "tester-unified/build-image.sh --wheel-dir run-gate-project/dist" in readme
    assert "docker buildx build --load" in builder
    assert '--build-context "run-gate-wheel=$wheel_stage"' in builder
    assert '"RUN_GATE_TESTER_VERSION=$run_gate_version"' in builder
    assert '"RUN_GATE_TESTER_WHEEL_SHA256=$run_gate_sha256"' in builder
    assert "Run-Gate version and wheel SHA-256 are required; use tester-unified/build-image.sh" in dockerfile
    for document in (readme, dockerfile):
        assert "docker build -f tester-unified/Dockerfile" not in document

    consumers = (REPO_ROOT / "tester-unified" / "docs" / "CONSUMERS.md").read_text(
        encoding="utf-8",
    )
    remote_lanes = (REPO_ROOT / "run-gate-project" / "REMOTE-LANES-BUILDKITE.md").read_text(
        encoding="utf-8",
    )
    profiler_gate = (REPO_ROOT / "scripts" / "cgroup-profiler" / "tools" / "gate.sh").read_text(
        encoding="utf-8",
    )
    audit_launcher = (REPO_ROOT / "nyxloom" / "tools" / "remote-mutation-audit-host.sh").read_text(
        encoding="utf-8",
    )
    for document in (consumers, remote_lanes):
        assert "tester-unified README" in document
    assert "tester-unified/build-image.sh" in profiler_gate
    assert '"$checkout/tester-unified/build-image.sh"' in audit_launcher
    assert 'cmru handler wheel-build --cwd "$checkout/run-gate-project"' in audit_launcher
    # The README tells operators the builder checks the exact wheel's version and
    # digest; the version helper itself is an implementation detail of the builder.
    flat_readme = " ".join(readme.split())
    assert "version and SHA-256 are checked during the build" in flat_readme
    assert "run-gate-wheel-version.py" in builder


def test_zero_release_image_base_and_cli_extended_source_are_explicit():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    builder = (REPO_ROOT / "tester-unified" / "build-image.sh").read_text(
        encoding="utf-8",
    )
    readme = (REPO_ROOT / "tester-unified" / "README.md").read_text("utf-8")
    design = (REPO_ROOT / "tester-unified" / "docs" / "DESIGN-GUIDE.md").read_text("utf-8")
    consumers = (REPO_ROOT / "tester-unified" / "docs" / "CONSUMERS.md").read_text("utf-8")
    bootstrap = (REPO_ROOT / "docs" / "BOOTSTRAP-FROM-ZERO.md").read_text("utf-8")

    # MDTC-02: the default base is tester-base (the floating MDT cockpit image is no longer the
    # gate environment); the zero-release path stays an explicit --base-image.
    assert re.search(r"^ARG TESTER_UNIFIED_BASE_IMAGE=tester-base:local$", dockerfile, re.MULTILINE)
    assert "modern-debian-tools-python-debug" not in "\n".join(
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    )
    assert "FROM ${TESTER_UNIFIED_BASE_IMAGE}" in dockerfile
    assert "docker compose version" in dockerfile
    assert 'packages="$packages docker-cli"' in dockerfile
    assert '--base-image) base_image=${2:?}; shift 2 ;;' in builder
    assert 'build_args+=(--build-arg "TESTER_UNIFIED_BASE_IMAGE=$base_image")' in builder
    assert 'tester_gate_args+=(--image "$base_image")' in builder
    assert 'cmru tester-gate "${tester_gate_args[@]}" /bin/true' in builder
    assert '--cli-extended-resolve) cli_extended_resolve=${2:?}; shift 2 ;;' in builder
    assert 'build_args+=(--build-arg "CLI_EXTENDED_WHEEL_SHA256=$cli_extended_sha256")' in builder
    assert '--no-cache) no_cache=1; shift ;;' in builder
    assert "mcr.microsoft.com/devcontainers/python:3.14-trixie" in readme
    assert "mcr.microsoft.com/devcontainers/python:3.14-trixie" in consumers
    # Prose wraps differently over time; compare whitespace-normalized text.
    flat_design = " ".join(design.split())
    flat_readme = " ".join(readme.split())
    flat_consumers = " ".join(consumers.split())
    assert "first-release bootstrapping" in flat_design
    assert "before `tester-unified:local` exists" in flat_readme
    assert "does not depend on an existing `tester-unified:local` image" in flat_consumers
    orchestration = tomllib.loads(
        (REPO_ROOT / "cmru.orchestration.toml").read_text("utf-8"),
    )
    declared_env = orchestration["orchestration"]["defaults"]["env"]
    for key in (
        "CMRU_TESTER_MEMORY", "CMRU_TESTER_MEMORY_SWAP", "CMRU_TESTER_CPUS",
        "CMRU_TESTER_PIDS_LIMIT", "CMRU_TESTER_CGROUP_PROBE_IMAGE",
    ):
        # The doc writes each declared value as a shell assignment; the value
        # may be single-quoted (the digest-pinned probe image is).
        value = declared_env[key]
        assert f"{key}={value}" in bootstrap or f"{key}='{value}'" in bootstrap, key
    assert f"docker pull {declared_env['CMRU_TESTER_CGROUP_PROBE_IMAGE']}" in bootstrap


def test_the_final_stage_labels_the_base_it_was_built_from():
    """``org.vbpub.tester-unified.base-image`` must carry the build-time base value.

    A pre-FROM ``ARG`` is not visible inside the stage unless redeclared, so the
    label must follow an in-stage ``ARG TESTER_UNIFIED_BASE_IMAGE`` with no
    default of its own; otherwise it would record an empty or fixed value, and
    ``build-image.sh`` / Run-Gate's release preparation would compare against it.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    # build-image.sh and Run-Gate both read the single default with one pattern.
    defaults = re.findall(r"^ARG TESTER_UNIFIED_BASE_IMAGE=(.*)$", text, re.MULTILINE)
    assert len(defaults) == 1 and defaults[0].strip(), defaults
    instructions = _dockerfile_instructions_by_stage(text)
    final = max(ordinal for ordinal, _selector, _index, _instruction in instructions)
    stage = [instruction for ordinal, _s, _i, instruction in instructions if ordinal == final]
    assert stage[0] == "FROM ${TESTER_UNIFIED_BASE_IMAGE}"
    label = 'LABEL org.vbpub.tester-unified.base-image="${TESTER_UNIFIED_BASE_IMAGE}"'
    assert stage.count(label) == 1
    redeclared = [index for index, instruction in enumerate(stage) if instruction == "ARG TESTER_UNIFIED_BASE_IMAGE"]
    assert redeclared and redeclared[0] < stage.index(label)
    # Nothing in the stage rebinds the value the label records.
    assert not any(instruction.startswith("ARG TESTER_UNIFIED_BASE_IMAGE=") for instruction in stage)
    builder = (REPO_ROOT / "tester-unified" / "build-image.sh").read_text(encoding="utf-8")
    assert '{{index .Config.Labels "org.vbpub.tester-unified.base-image"}}' in builder


COMPOSE_PIN_RE = r"\d+\.\d+\.\d+-\d+~debian\.\d+~trixie"


def assert_compose_is_pinned_and_not_debians(dockerfile_text: str) -> None:
    """INT-01 D-006: compose is Docker's own docker-compose-plugin at an exact version with a pinned sha256
    per architecture, verified before dpkg; Debian's unpinned `docker-compose` package is refused."""
    code = [line for line in dockerfile_text.splitlines() if not line.lstrip().startswith("#")]
    joined = " ".join(code)
    version = re.search(rf"^ARG DOCKER_COMPOSE_PLUGIN_VERSION=({COMPOSE_PIN_RE})$", dockerfile_text, re.MULTILINE)
    assert version, "DOCKER_COMPOSE_PLUGIN_VERSION must be an exact version, not floating or empty"
    for arch in ("AMD64", "ARM64"):
        assert re.search(rf"^ARG DOCKER_COMPOSE_PLUGIN_SHA256_{arch}=[0-9a-f]{{64}}$", dockerfile_text, re.MULTILINE), arch
    # no apt install of Debian's package (or of any compose package by name), floating or not
    assert not re.search(r"(?<![\w-])docker-compose(?![\w-])", joined), "Debian's docker-compose package is refused"
    for command in re.findall(r"apt-get install[^;&|]*", joined):
        assert "docker-compose-plugin" not in command, "install the plugin from the pinned, checksummed .deb"
    assert "sha256sum -c -" in joined
    assert joined.index("sha256sum -c -") < joined.index("dpkg -i /tmp/docker-compose-plugin.deb")
    assert "docker-compose-plugin_${DOCKER_COMPOSE_PLUGIN_VERSION}_${arch}.deb" in joined
    assert "docker compose version" in joined


def test_compose_is_dockers_pinned_plugin_not_debians_package():
    assert_compose_is_pinned_and_not_debians(DOCKERFILE.read_text(encoding="utf-8"))


def test_the_compose_pin_check_can_fail():
    good = DOCKERFILE.read_text(encoding="utf-8")
    sha = "a" * 64
    pinned_version = re.search(rf"^ARG DOCKER_COMPOSE_PLUGIN_VERSION=({COMPOSE_PIN_RE})$", good, re.MULTILINE).group(1)
    assert_compose_is_pinned_and_not_debians(good)
    bad_variants = {
        "floating version": good.replace(f"ARG DOCKER_COMPOSE_PLUGIN_VERSION={pinned_version}", "ARG DOCKER_COMPOSE_PLUGIN_VERSION=latest"),
        "empty version": good.replace(f"ARG DOCKER_COMPOSE_PLUGIN_VERSION={pinned_version}", "ARG DOCKER_COMPOSE_PLUGIN_VERSION="),
        "short sha": re.sub(r"(ARG DOCKER_COMPOSE_PLUGIN_SHA256_AMD64=)[0-9a-f]{64}", r"\g<1>abc", good),
        "debian package": good.replace('packages="$packages docker-cli"; fi', 'packages="$packages docker-cli docker-compose"; fi'),
        "unpinned plugin from apt": good.replace("&& apt-get install -y --no-install-recommends $packages", "&& apt-get install -y --no-install-recommends $packages docker-compose-plugin"),
        "no checksum before dpkg": good.replace('echo "${sum}  /tmp/docker-compose-plugin.deb" | sha256sum -c -; ', ""),
    }
    for label, text in bad_variants.items():
        assert text != good, label
        with pytest.raises(AssertionError):
            assert_compose_is_pinned_and_not_debians(text)
    del sha


def test_the_tester_image_records_the_identity_of_the_base_it_was_built_on():
    """INT-01 A-10: the base reference can be a mutable local tag, so build-image.sh resolves its identity
    (image id, or the digest of a digest reference), refuses a base it cannot identify, passes it as a
    build argument, and re-reads the label the Dockerfile writes."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG TESTER_UNIFIED_BASE_IMAGE_ID$", text, re.MULTILINE)
    assert 'LABEL org.vbpub.tester-unified.base-image-id="${TESTER_UNIFIED_BASE_IMAGE_ID}"' in text
    builder = (REPO_ROOT / "tester-unified" / "build-image.sh").read_text(encoding="utf-8")
    assert 'docker image inspect --format \'{{.Id}}\' "$base_ref"' in builder
    assert "has no registry to pull from" in builder and 'docker pull "$base_ref"' in builder
    assert 'build_args+=(--build-arg "TESTER_UNIFIED_BASE_IMAGE_ID=$expected_base_image_id")' in builder
    assert '{{index .Config.Labels "org.vbpub.tester-unified.base-image-id"}}' in builder


def _canonical_image(name: str) -> str:
    if name.startswith("docker.io/"):
        return name
    return "docker.io/library/" + name if "/" not in name else "docker.io/" + name


def assert_tester_base_has_no_published_vbpub_input(root: Path) -> None:
    """tester-base builds from public upstream inputs alone. The allowed upstream images are DERIVED from
    the version targets tester-base/cmru.toml declares (the images `cmru versions` vets); every FROM must
    be a literal, digest-pinned reference to one of them, with no ARG substitution, and no non-comment
    line (FROM, COPY --from, curl) may name the vbpub namespace."""
    declared = tomllib.loads((root / "tester-base" / "cmru.toml").read_text("utf-8"))
    allowed = {
        _canonical_image(target["oci"]["image"])
        for target in declared.get("versions", {}).get("targets", {}).values()
        if isinstance(target, dict) and "oci" in target
    }
    assert allowed, "tester-base/cmru.toml declares no upstream image targets to derive the allowed list from"
    code = [
        line for line in (root / "tester-base" / "Dockerfile").read_text("utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    froms = [line for line in code if re.match(r"(?i)^FROM\s", line)]
    assert len(froms) == 1, froms
    match = re.fullmatch(r"FROM (?P<image>[^\s:@$]+):[^\s@$]+@sha256:[0-9a-f]{64}", froms[0])
    assert match, f"tester-base FROM must be a literal digest-pinned reference: {froms[0]!r}"
    assert _canonical_image(match.group("image")) in allowed, (match.group("image"), allowed)
    logical = re.sub(r"\\\n", " ", "\n".join(code)).splitlines()
    # a LABEL may point at the source repository (metadata); nothing else may name the vbpub namespace
    assert [line for line in logical if "volkb79" in line.lower() and not line.lstrip().upper().startswith("LABEL")] == []


def test_the_no_published_vbpub_image_check_can_fail(tmp_path):
    """The invariant above goes RED for a vbpub FROM, an ARG-substituted FROM, a second FROM, an
    undeclared upstream image, and a vbpub namespace in a download line."""
    (tmp_path / "tester-base").mkdir()
    (tmp_path / "tester-base" / "cmru.toml").write_text(
        '[versions.targets."oci.python"]\n[versions.targets."oci.python".oci]\nimage = "docker.io/library/python"\n',
        encoding="utf-8",
    )
    digest = "sha256:" + "a" * 64
    good = f"FROM python:3.14@{digest}\nRUN echo ok\n"
    dockerfile = tmp_path / "tester-base" / "Dockerfile"
    dockerfile.write_text(good, encoding="utf-8")
    assert_tester_base_has_no_published_vbpub_input(tmp_path)
    for bad in (
        f"FROM ghcr.io/volkb79-2/tester-base@{digest}\n",
        f"FROM docker.io/other/python:3.14@{digest}\n",
        f"ARG B=python:3.14@{digest}\nFROM ${{B}}\n",
        good + f"FROM python:3.14@{digest}\n",
        good + "RUN curl -fsSL https://github.com/volkb79-2/vbpub/releases/download/x/y\n",
        "FROM python:3.14\n",
    ):
        dockerfile.write_text(bad, encoding="utf-8")
        with pytest.raises(AssertionError):
            assert_tester_base_has_no_published_vbpub_input(tmp_path)


def test_wheel_builder_does_not_depend_on_a_published_vbpub_image():
    dockerfile = (REPO_ROOT / "wheel-builder" / "Dockerfile").read_text("utf-8")
    readme = (REPO_ROOT / "cmru" / "README.md").read_text("utf-8")
    design = (REPO_ROOT / "cmru" / "docs" / "DESIGN-GUIDE.md").read_text("utf-8")
    consumers = (REPO_ROOT / "cmru" / "docs" / "CONSUMERS.md").read_text("utf-8")

    # The invariant is "no PUBLISHED vbpub image": the base is the LOCAL tester-base tag (no registry
    # host), and tester-base itself is built from public upstream inputs alone (its FROM names no
    # vbpub registry path). Operator decision 2026-10-18: wheel-builder moves to tester-base.
    assert re.search(r"^ARG WHEEL_BUILDER_BASE_IMAGE=tester-base:local$", dockerfile, re.MULTILINE)
    assert "FROM ${WHEEL_BUILDER_BASE_IMAGE}" in dockerfile
    assert_tester_base_has_no_published_vbpub_input(REPO_ROOT)
    assert "apt-get install -y --no-install-recommends ca-certificates git passwd" in dockerfile
    assert "ghcr.io/volkb79-2/modern-debian-tools-python-debug" not in dockerfile
    for document in (readme, design, consumers):
        assert "CMRU_BOOTSTRAP_CLI_EXTENDED=source" in document
        assert "BOOTSTRAP-FROM-ZERO.md" in document

def test_tester_unified_venvs_are_cleared_before_creation():
    instructions = _dockerfile_instructions_by_stage(
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    clean_index, = [
        index
        for index, (_ordinal, _selector, _stage_index, instruction)
        in enumerate(instructions)
        if instruction == f"RUN {CMRU_VENV_CLEAN_RUN}"
    ]
    create_index, = [
        index
        for index, (_ordinal, _selector, _stage_index, instruction)
        in enumerate(instructions)
        if instruction.startswith(
            "RUN /usr/local/bin/python3.14 -m venv --copies /opt/tester-venv",
        )
    ]
    assert clean_index < create_index


def test_dockerfile_copies_every_generator_project_to_its_expected_src_path():
    text = DOCKERFILE.read_text(encoding="utf-8")
    specs = _dockerfile_copy_specs_before_command(
        text, TESTER_STAGE, r"gen-requirements.py --root /src",
    )
    for project_file, _extras in gen.PROJECTS:
        project = PurePosixPath(project_file).parts[0]
        if project == "cgroup-profiler":
            source = "scripts/cgroup-profiler/pyproject.toml"
            relative = "scripts/cgroup-profiler/pyproject.toml"
        else:
            source = project_file
            relative = project_file
        # Nested sources (pwmcp/client/pyproject.toml) keep their full relative path under /src.
        destination = f"/src/{project_file}" if project != "cgroup-profiler" else "/src/cgroup-profiler/pyproject.toml"
        _assert_copy_spec_covers_path_at_destination(
            specs, relative, source, destination,
        )


def test_generator_project_copy_oracle_rejects_excluded_metadata():
    text = (
        "FROM base:latest AS stage-0\n"
        "WORKDIR /\n"
        "COPY --exclude=pyproject.toml ciu/ /src/ciu/\n"
        "RUN python /usr/local/lib/tester-unified/gen-requirements.py --root /src\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text, "stage-0", r"gen-requirements.py --root /src",
    )
    with pytest.raises(AssertionError, match="not copied from"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "ciu/pyproject.toml",
            "ciu",
            "/src/ciu/",
        )


def test_generator_project_copy_oracle_rejects_add_overwriting_metadata():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/pyproject.toml /src/cmru/pyproject.toml\n"
        "ADD cmru/alternate.toml /src/cmru/pyproject.toml\n"
        "RUN python /usr/local/lib/tester-unified/gen-requirements.py --root /src\n"
    )
    with pytest.raises(ValueError, match="ADD leaves build inputs unchanged"):
        _dockerfile_copy_specs_before_command(
            text, "stage-0", r"gen-requirements.py --root /src",
        )


def test_dockerfile_copies_every_cmru_wheel_input_before_build():
    text = DOCKERFILE.read_text(encoding="utf-8")
    instructions = _dockerfile_instructions_by_stage(text)
    clean_index, = [
        index
        for index, (_ordinal, _selector, _stage_index, instruction)
        in enumerate(instructions)
        if instruction == f"RUN {CMRU_SOURCE_TREE_CLEAN_RUN}"
    ]
    source_copy_indices = [
        index
        for index, (_ordinal, _selector, _stage_index, instruction)
        in enumerate(instructions)
        if instruction in {
            "COPY cmru/ /src/cmru/",
            "COPY libraries/worktree/ /src/libraries/worktree/",
        }
    ]
    assert len(source_copy_indices) == 2
    assert clean_index < min(source_copy_indices)
    _assert_dockerfile_wheel_build_output_is_installed(
        text,
        stage=TESTER_STAGE,
        build_run_body=CMRU_WHEEL_BUILD_BODY,
        install_run_body=CMRU_WHEEL_INSTALL_RUN_BODY,
        allowed_post_install_mutations=CMRU_POST_INSTALL_MUTATIONS,
    )
    specs = _dockerfile_copy_specs_before_command(
        text,
        TESTER_STAGE,
        CMRU_WHEEL_BUILD_COMMAND,
        expected_workdir="/",
        run_sensitive_sources=("cmru", "libraries/worktree"),
        allowed_pre_sensitive_run_bodies=CMRU_PRE_SENSITIVE_RUN_BODIES,
        allowed_external_contexts=("run-gate-wheel",),
        allowed_pure_image_stages=("node-source",),
        allowed_shell_prefix=(
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified",
        ),
        expected_run_body=CMRU_WHEEL_BUILD_BODY,
    )
    _assert_copy_specs_do_not_target_roots(
        specs,
        protected_roots=("/opt/tester-venv",),
    )
    for path in _cmru_wheel_inputs():
        if path.startswith("cmru/"):
            source, destination = "cmru", "/src/cmru/"
        else:
            source, destination = "libraries/worktree", "/src/libraries/worktree/"
        _assert_copy_spec_covers_path_at_destination(
            specs, path, source, destination,
        )
    source_copy_sources = (
        ("cmru", "/src/cmru/"),
        ("libraries/worktree", "/src/libraries/worktree/"),
    )
    _assert_copy_specs_cover_all_committed_context_files(
        specs,
        copy_sources=source_copy_sources,
    )
    _assert_copy_outputs_are_accounted_for(
        specs,
        protected_roots=(
            "/src/cmru",
            "/src/libraries/worktree",
        ),
        expected_files=_expected_context_copy_destinations(
            copy_sources=source_copy_sources,
        ),
    )


def test_wheel_input_oracle_rejects_an_ignored_untracked_source_file(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    package = repository / "fixture" / "src" / "cmru"
    package.mkdir(parents=True)
    (repository / ".gitignore").write_text(
        "fixture/src/cmru/local_settings.py\n", encoding="utf-8",
    )
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "oracle test"], cwd=repository, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "oracle@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "candidate source"],
        cwd=repository,
        check=True,
    )
    (package / "local_settings.py").write_text("SECRET = True\n", encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", repository)
    monkeypatch.setattr(
        sys.modules[__name__], "_dockerignore_rules", lambda **_kwargs: [],
    )
    copy_sources = (("fixture", "/src/cmru/"),)
    specs = [(PurePosixPath("fixture"), "/src/cmru/", ())]
    expected = _expected_context_copy_destinations(
        copy_sources=copy_sources,
        rules=[],
    )

    with pytest.raises(ValueError, match="unaccounted COPY output"):
        _assert_copy_outputs_are_accounted_for(
            specs,
            protected_roots=("/src/cmru",),
            expected_files=expected,
        )


def test_wheel_package_tree_oracle_rejects_an_unaccounted_source_file():
    cases = (
        (
            "/src/cmru/src/cmru/injected.py",
            "/src/cmru",
            "/src/cmru/src/cmru/__init__.py",
        ),
        (
            "/src/cmru/src/injected/__init__.py",
            "/src/cmru",
            "/src/cmru/src/cmru/__init__.py",
        ),
        (
            "/src/cmru/setup.py",
            "/src/cmru",
            "/src/cmru/src/cmru/__init__.py",
        ),
        (
            "/build/nyxloom/src/nyxloom/injected.py",
            "/build/nyxloom",
            "/build/nyxloom/src/nyxloom/__init__.py",
        ),
        (
            "/build/nyxloom/src/injected/__init__.py",
            "/build/nyxloom",
            "/build/nyxloom/src/nyxloom/__init__.py",
        ),
        (
            "/build/libraries/cli-extended/src/injected/__init__.py",
            "/build/libraries/cli-extended",
            "/build/libraries/cli-extended/src/cli_extended/__init__.py",
        ),
    )
    for destination, root, known_file in cases:
        specs = _copy_source_specs_from_instruction(
            f"COPY ciu/src/ciu/__init__.py {destination}\n"
        )
        with pytest.raises(ValueError, match="unaccounted COPY output"):
            _assert_copy_outputs_are_accounted_for(
                specs,
                protected_roots=(root,),
                expected_files={PurePosixPath(known_file)},
            )


def test_wheel_package_tree_oracle_rejects_an_unresolved_stage_copy():
    specs = _copy_source_specs_from_instruction(
        "COPY --from=seed /replacement/ /src/cmru/src/cmru/\n",
        include_stage=True,
    )
    with pytest.raises(ValueError, match="cannot determine whether COPY source"):
        _assert_copy_outputs_are_accounted_for(
            specs,
            protected_roots=("/src/cmru",),
            expected_files=set(),
        )


def test_wheel_install_oracle_rejects_copying_into_the_venv_before_install():
    specs = _copy_source_specs_from_instruction(
        "COPY ciu/src/ciu/__init__.py "
        "/opt/tester-venv/lib/python3.14/site-packages/cmru/injected.py\n"
    )
    with pytest.raises(ValueError, match="targets protected installed package path"):
        _assert_copy_specs_do_not_target_roots(
            specs,
            protected_roots=("/opt/tester-venv",),
        )


def test_wheel_output_oracle_rejects_a_stage_copy_between_build_and_install():
    text = (
        "FROM base:latest AS stage-0\n"
        f"RUN {CMRU_WHEEL_BUILD_BODY}\n"
        "COPY --from=seed /replacement.whl /tmp/internal-wheels/\n"
        f"RUN {CMRU_WHEEL_INSTALL_RUN_BODY}\n"
    )
    with pytest.raises(ValueError, match="not consumed by the next instruction"):
        _assert_dockerfile_wheel_build_output_is_installed(
            text,
            stage="stage-0",
            build_run_body=CMRU_WHEEL_BUILD_BODY,
            install_run_body=CMRU_WHEEL_INSTALL_RUN_BODY,
            allowed_post_install_mutations=CMRU_POST_INSTALL_MUTATIONS,
        )


def test_wheel_output_oracle_rejects_mutation_after_install_in_same_run():
    text = (
        "FROM base:latest AS stage-0\n"
        f"RUN {CMRU_WHEEL_BUILD_BODY}\n"
        f"RUN {CMRU_WHEEL_INSTALL_RUN_BODY}"
        " && rm -rf /opt/tester-venv/lib/python3.14/site-packages/cmru\n"
    )
    with pytest.raises(ValueError, match="does not exactly install"):
        _assert_dockerfile_wheel_build_output_is_installed(
            text,
            stage="stage-0",
            build_run_body=CMRU_WHEEL_BUILD_BODY,
            install_run_body=CMRU_WHEEL_INSTALL_RUN_BODY,
        )


def test_wheel_output_oracle_rejects_copy_after_install():
    text = (
        "FROM base:latest AS stage-0\n"
        f"RUN {CMRU_WHEEL_BUILD_BODY}\n"
        f"RUN {CMRU_WHEEL_INSTALL_RUN_BODY}\n"
        "COPY injected.py /opt/tester-venv/lib/python3.14/site-packages/cmru/\n"
    )
    with pytest.raises(ValueError, match="unapproved COPY instruction"):
        _assert_dockerfile_wheel_build_output_is_installed(
            text,
            stage="stage-0",
            build_run_body=CMRU_WHEEL_BUILD_BODY,
            install_run_body=CMRU_WHEEL_INSTALL_RUN_BODY,
        )


def test_tester_copy_oracle_rejects_run_mutation_after_package_copy():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        "RUN rm -rf /src/cmru/src/cmru\n"
        "RUN pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="survive an intervening RUN"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_run_injection_before_package_copy():
    text = (
        "FROM base:latest AS stage-0\n"
        "RUN mkdir -p /src/cmru/src/cmru "
        "&& printf 'x=1\\n' > /src/cmru/src/cmru/injected.py\n"
        "COPY cmru/ /src/cmru/\n"
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="pre-copy RUN leaves wheel inputs unchanged"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_add_after_package_copy():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        "ADD ciu/pyproject.toml /src/cmru/pyproject.toml\n"
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="cannot prove ADD leaves build inputs unchanged"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            CMRU_WHEEL_BUILD_COMMAND,
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_shell_instruction_after_package_copy():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        'SHELL ["/bin/sh", "-c", "touch /src/cmru/src/cmru/injected.py; eval \\"$0\\""]\n'
        "RUN /tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="SHELL preserves build input checks"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            CMRU_WHEEL_BUILD_COMMAND,
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_a_mutating_command_before_build_in_same_run():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        "RUN echo changed >> /src/cmru/src/cmru/cli.py && "
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified "
        "/tmp/internal-build-venv/bin/pip wheel /src/cmru\n"
    )
    with pytest.raises(ValueError, match="unapproved shell prefix"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            r"pip wheel[\s\S]*?/src/cmru",
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
        )


def test_wheel_input_oracle_rejects_command_substitution_in_build_prefix():
    text = (
        "FROM base:latest AS stage-0\n"
        "COPY cmru/ /src/cmru/\n"
        "RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified "
        "/tmp/internal-build-venv/bin/pip wheel --no-index --no-deps "
        "--no-build-isolation -w /tmp/internal-wheels "
        "\"$(rm -rf /src/cmru/src/cmru; printf /src/cmru)\"\n"
    )
    with pytest.raises(ValueError, match="RUN differs from its approved command"):
        _dockerfile_copy_specs_before_command(
            text,
            "stage-0",
            CMRU_WHEEL_BUILD_COMMAND,
            expected_workdir="/",
            run_sensitive_sources=("cmru",),
            allowed_shell_prefix=(
                "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified",
            ),
            expected_run_body=CMRU_WHEEL_BUILD_BODY,
        )


def test_tester_copy_oracle_rejects_excluding_all_cmru_modules():
    text = (
        "FROM base:latest\n"
        "COPY --exclude=src cmru/ /src/cmru/\n"
        "RUN pip wheel /src/cmru\n"
    )
    specs = _dockerfile_copy_specs_before_command(
        text, "stage-0", r"pip wheel[\s\S]*?/src/cmru",
    )
    with pytest.raises(AssertionError, match="not copied from"):
        _assert_copy_spec_covers_path_at_destination(
            specs,
            "cmru/src/cmru/cli.py",
            "cmru",
            "/src/cmru/",
        )


def test_tester_cmru_and_worktree_package_source_trees_are_nonempty():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    specs = _dockerfile_copy_specs_before_command(
        dockerfile,
        TESTER_STAGE,
        CMRU_WHEEL_BUILD_COMMAND,
        expected_workdir="/",
        run_sensitive_sources=("cmru", "libraries/worktree"),
        allowed_pre_sensitive_run_bodies=CMRU_PRE_SENSITIVE_RUN_BODIES,
        # The Run-Gate wheel arrives through an external named BuildKit context
        # (`COPY --from=run-gate-wheel`); it is not a CMRU input. Declared or
        # intermediate stages are still refused.
        allowed_external_contexts=("run-gate-wheel",),
        allowed_pure_image_stages=("node-source",),
        allowed_shell_prefix=(
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+tester.unified",
        ),
        expected_run_body=CMRU_WHEEL_BUILD_BODY,
    )
    for package_root, source, destination in (
        (REPO_ROOT / "cmru" / "src" / "cmru", "cmru", "/src/cmru/"),
        (
            REPO_ROOT / "libraries" / "worktree" / "src" / "worktree",
            "libraries/worktree",
            "/src/libraries/worktree/",
        ),
    ):
        assert package_root.is_dir()
        modules = _wheel_python_modules(package_root)
        assert modules, package_root
        assert package_root / "__init__.py" in modules
        for module in modules:
            relative = module.relative_to(REPO_ROOT).as_posix()
            assert _dockerignore_included(relative), relative
            _assert_copy_spec_covers_path_at_destination(
                specs, relative, source, destination,
            )


def test_ignored_python_files_do_not_make_a_wheel_package_tree_nonempty(tmp_path):
    package_root = tmp_path / "src" / "example"
    ignored_module = package_root / ".venv" / "shadow.py"
    ignored_module.parent.mkdir(parents=True)
    ignored_module.write_text("ignored", encoding="utf-8")
    tox_module = package_root / ".tox" / "shadow.py"
    tox_module.parent.mkdir(parents=True)
    tox_module.write_text("generated", encoding="utf-8")
    nox_module = package_root / ".nox" / "shadow.py"
    nox_module.parent.mkdir(parents=True)
    nox_module.write_text("generated", encoding="utf-8")
    assert _wheel_python_modules(package_root) == []

    real_module = package_root / "module.py"
    real_module.write_text("wheel input", encoding="utf-8")
    assert _wheel_python_modules(package_root) == [real_module]


def test_source_modules_outside_the_selected_package_do_not_make_it_nonempty(tmp_path):
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "other.py").write_text("unrelated", encoding="utf-8")
    package_root = source_root / "nyxloom"
    package_root.mkdir()
    assert _wheel_python_modules(package_root) == []


@pytest.mark.parametrize("path", [
    "libraries/cli-extended/pyproject.toml",
    "libraries/cli-extended/README.md",
    "libraries/cli-extended/src/cli_extended/__init__.py",
])
def test_dockerignore_admits_the_cli_extended_wheel_build_inputs_for_nyxloomd(path):
    """The whitelist is intended: nyxloomd builds the cli-extended wheel from these (NYX-CLIX). The
    tester-unified image still copies none of them (test_every_dockerfile_copy_source_...)."""
    assert _dockerignore_included(path), path


def test_tester_unified_copies_no_cli_extended_source_whatever_the_dockerignore_admits():
    sources = []
    for line in _dockerfile_run_lines().splitlines():
        parts = line.split()
        if parts and parts[0] == "COPY" and not any(part.startswith("--from=") for part in parts):
            sources.extend(parts[1:-1])
    assert sources, "no COPY sources parsed"
    assert [s for s in sources if "cli-extended" in s.replace("fetch-cli-extended.py", "")] == []


def test_every_dockerfile_copy_source_is_admitted_by_the_dockerignore():
    """Every COPY source resolves to context entries after ignore and exclude rules."""
    specs = _dockerfile_copy_specs()
    sources = [source for source, _destination, _excludes in specs]
    source_names = [str(source).rstrip("/") for source in sources]
    assert "tester-unified/fetch-cli-extended.py" in source_names
    for spec in specs:
        source = spec[0]
        assert _copy_source_context_entries(spec), source
        assert not _copy_source_overlaps_cli_extended(source), source


# --- MDTC-02: the image is built FROM tester-base, not the MDT cockpit image ------------------

WHEEL_BUILDER = REPO_ROOT / "wheel-builder" / "Dockerfile"
# Anything that names the MDT cockpit image family or its filesystem layout, outside comments (R7).
MDT_MARKERS = ("modern-debian-tools", "/opt/mdt", "mdt-cockpit", "/home/vscode")


def _code_lines(dockerfile):
    return [line for line in dockerfile.read_text("utf-8").splitlines() if not line.lstrip().startswith("#")]


def test_tester_unified_is_built_from_tester_base_with_no_other_fallback():
    """The base ARG keeps its published name (build-image.sh and Run-Gate 24.0.1's release preparation
    parse `ARG TESTER_UNIFIED_BASE_IMAGE=` and compare the recorded label); only its default moved."""
    text = DOCKERFILE.read_text("utf-8")
    assert re.search(r"^ARG TESTER_UNIFIED_BASE_IMAGE=tester-base:local$", text, re.MULTILINE)
    froms = re.findall(r"^FROM (\S+)", text, re.MULTILINE)
    # the only other FROM allowed is tester-unified's digest-pinned node stage (D-004)
    assert [f for f in froms if not f.startswith("docker.io/library/node:")] == ["${TESTER_UNIFIED_BASE_IMAGE}"], froms
    assert re.search(r'^LABEL org\.vbpub\.tester-unified\.base-image="\$\{TESTER_UNIFIED_BASE_IMAGE\}"$', text, re.MULTILINE)


def test_bootstrap_builds_tester_base_before_the_wheel_builder_that_is_based_on_it():
    """The wheel builder's base is tester-base:local, so the zero-release bootstrap script must make
    that tag exist first and must not ask a registry for it (`--pull`)."""
    script = (REPO_ROOT / "cmru" / "build-initial-standalone.sh").read_text("utf-8")
    assert script.index('"${repo_root}/tester-base"') < script.index('-f "${repo_root}/wheel-builder/Dockerfile"')
    assert "--pull" not in "\n".join(line for line in script.splitlines() if "buildx build" in line)
    assert script.count("BUILDX_BUILDER=default docker buildx build") == 2


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, WHEEL_BUILDER])
@pytest.mark.parametrize("marker", MDT_MARKERS)
def test_no_gate_image_instruction_names_the_mdt_cockpit_image_or_layout(dockerfile, marker):
    offenders = [line for line in _code_lines(dockerfile) if marker in line]
    assert offenders == [], offenders


def test_the_no_mdt_check_can_fail():
    """The check above is only worth something if its scan sees a violation."""
    sample = "FROM x\n# /home/vscode is fine in a comment\nRUN ls /home/vscode\n"
    lines = [line for line in sample.splitlines() if not line.lstrip().startswith("#")]
    assert [line for line in lines if "/home/vscode" in line] == ["RUN ls /home/vscode"]


def test_tester_unified_path_is_its_own_venv_then_the_base_path():
    text = DOCKERFILE.read_text("utf-8")
    assert re.search(r"^ENV PATH=/opt/tester-venv/bin:\$\{PATH\}(?: \\)?$", text, re.MULTILINE)


def test_a_pure_image_stage_must_be_a_lone_digest_pinned_from():
    """The allowance for the node-source stage is verified: add a RUN, drop the digest or add a COPY
    to that stage and the oracle refuses to allow it."""
    base = "FROM docker.io/library/node:26@sha256:" + "a" * 64 + " AS node-source\n"
    final = "FROM base:latest\nCOPY --from=node-source /a /a\nCOPY cmru/ /src/cmru/\nRUN pip wheel /src/cmru\n"

    def run(first_stage):
        return _dockerfile_copy_specs_before_command(
            first_stage + final, "stage-1", r"pip wheel[\s\S]*?/src/cmru",
            run_sensitive_sources=("cmru",), allowed_pure_image_stages=("node-source",),
            expected_run_body="pip wheel /src/cmru",
        )

    run(base)  # the lone pinned FROM is accepted
    for bad in (
        base + "RUN echo tampered > /a\n",
        "FROM docker.io/library/node:26 AS node-source\n",
        base + "COPY x /a\n",
    ):
        with pytest.raises(ValueError, match="pure-image stage|not a lone"):
            run(bad)


def test_tester_unified_has_no_systemctl_stub_and_installs_what_its_suites_run():
    """D-001: no fake systemd (topos handles a missing systemctl itself) - but the REAL client IS
    installed, because host-placement gate scripts run /bin/systemctl inside this image (the
    cgprofile r0-r1 probe exited 127 without it). D-003: zsh. D-004: node, npm and npx copied from
    the digest-pinned official node image (node 26 ships no corepack)."""
    code = "\n".join(_code_lines(DOCKERFILE))
    # no stub: the ONLY instruction that mentions systemctl is the exact real-package RUN, no COPY/ADD
    # lands anything at a systemctl path, and the build proves the binary is owned by the systemd package
    # (a stub that merely prints a version would not satisfy `dpkg -S`).
    mentioning = [
        line for line in _dockerfile_logical_instructions(DOCKERFILE.read_text("utf-8"))
        if "systemctl" in line
    ]
    assert mentioning == [f"RUN {TESTER_SYSTEMCTL_RUN}"], mentioning
    for instruction in _dockerfile_logical_instructions(DOCKERFILE.read_text("utf-8")):
        if re.match(r"(?i)^(COPY|ADD)\b", instruction):
            assert "systemctl" not in instruction, instruction
    assert 'dpkg -S /usr/bin/systemctl | cut -d: -f1)" = systemd' in code
    assert "apt-get install -y --no-install-recommends systemd" in code
    assert "/bin/systemctl --version" in code
    assert "apt-get install -y --no-install-recommends zsh" in code
    assert "nodejs" not in code, "Debian's nodejs is too old; node comes from the official image"
    for source in ("/usr/local/bin/node", "/usr/local/lib/node_modules"):
        assert f"COPY --from=node-source {source} {source}" in code
    for tool in ("npm", "npx"):
        assert f"/usr/local/bin/{tool}" in code
    assert re.search(r"^FROM docker\.io/library/node:26\.\d+\.\d+-trixie@sha256:[0-9a-f]{64} AS node-source$", code, re.MULTILINE)


def host_environment_users(config_text: str) -> list[str]:
    """Where a parsed run-gate config runs something in the root-run `host` environment: a lane's own
    `environment`, the `[assay]` default environment lanes without one inherit, and `[assay.import]`."""
    parsed = tomllib.loads(config_text)
    users = []
    for name, lane in (parsed.get("lanes") or {}).items():
        if isinstance(lane, dict) and lane.get("environment") == "host":
            users.append(f"lanes.{name}")
    assay = parsed.get("assay") or {}
    if isinstance(assay, dict):
        if assay.get("environment") == "host":
            users.append("assay.environment (inherited by every assay lane without its own)")
        imported = assay.get("import")
        if isinstance(imported, dict) and imported.get("environment") == "host":
            users.append("assay.import.environment")
    return users


def test_the_host_user_scan_parses_toml_and_sees_every_spelling():
    for text in (
        '[lanes.a]\nkind="command"\nenvironment = "host"\n',
        "[lanes.a]\nkind='command'\nenvironment='host'   # trailing comment\n",
        '[lanes.a]\nkind = "command"\nargv = ["x"]\nenvironment="host"\n',
        '[lanes]\na = { kind = "command", environment = "host" }\n',
        '[assay]\nenvironment = "host"\n',
        '[assay.import]\nenvironment = "host"\nlanes = "all"\n',
    ):
        assert host_environment_users(text), text
    for text in (
        '[lanes.a]\nkind = "command"\nenvironment = "tester-unified"\n',
        '# environment = "host"\n[lanes.a]\nkind = "command"\nenvironment = "bare-host"\n',
        '[lanes.a]\nkind = "command"\nenvironment = "tester-unified"\nargv = ["echo environment = \\"host\\""]\n',
    ):
        assert host_environment_users(text) == [], text


def test_run_gate_host_environment_names_tester_base_and_no_lane_runs_there():
    """D-002: tester-base runs as root with no identity, so no gate lane may use `host` - neither
    directly, nor through the [assay] defaults lanes inherit, nor through a central lane (root config)."""
    declared = tomllib.loads((REPO_ROOT / "run-gate.root.toml").read_text("utf-8"))
    assert declared["environments"]["host"]["image"] == "tester-base:local"
    configs = [REPO_ROOT / "run-gate.root.toml"]
    configs += [
        c for c in REPO_ROOT.glob("**/run-gate.toml")
        # relative parts: the checkout itself usually lives under a `.worktrees/` directory
        if not {".worktrees", "node_modules", "fixtures"} & set(c.relative_to(REPO_ROOT).parts)
    ]
    assert len(configs) > 20, f"the scan saw only {len(configs)} run-gate configs: it would pass vacuously"
    users = {}
    for config in configs:
        found = host_environment_users(config.read_text("utf-8"))
        if found:
            users[str(config.relative_to(REPO_ROOT))] = found
    assert users == {}, users


def test_dockerfile_ages_third_party_packages_by_default_fourteen_days():
    text = DOCKERFILE.read_text("utf-8")
    assert re.search(r"^ARG PIP_MIN_RELEASE_AGE=P14D$", text, re.MULTILINE)
    assert 'LABEL org.vbpub.tester-unified.min-release-age="${PIP_MIN_RELEASE_AGE}"' in text
    # No fixed third-party pins were introduced into the closure install.
    assert "==" not in "\n".join(
        c for c in _pip_commands(_dockerfile_run_lines()) if "-r /tmp/" in c
    )


def _load_run_gate_fetcher():
    spec = importlib.util.spec_from_file_location(
        "fetch_run_gate_wheel", GENERATOR.with_name("fetch-run-gate-wheel.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run_gate_release(version="24.1.0", *, sidecar=None, pointer_sha=None, wheel=b"rg-wheel"):
    fetcher = _load_run_gate_fetcher()
    digest = hashlib.sha256(wheel).hexdigest()
    name = fetcher._asset_name(version)
    url = fetcher._asset_url(version)
    pointer = {
        "project": "run-gate", "version": version, "tag": f"run-gate-v{version}",
        "asset": name, "url": url, "sha256": pointer_sha or digest,
    }
    files = {
        fetcher.POINTER_URL: json.dumps(pointer).encode(),
        url: wheel,
        url + ".sha256": f"{sidecar or digest}  {name}\n".encode(),
    }

    def fetch(requested):
        if requested not in files:
            raise OSError(f"unexpected url {requested}")
        return files[requested]

    return fetcher, fetch, digest, name


def test_run_gate_fetcher_stages_the_published_wheel_from_the_latest_pointer(tmp_path):
    fetcher, fetch, digest, name = _fake_run_gate_release()
    wheel = fetcher.fetch_wheel(None, tmp_path / "stage", fetch)
    assert wheel.name == name and wheel.read_bytes() == b"rg-wheel"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == digest


def test_run_gate_fetcher_takes_a_pinned_version_without_the_pointer(tmp_path):
    fetcher, fetch, _, name = _fake_run_gate_release("24.0.1")
    assert fetcher.fetch_wheel("24.0.1", tmp_path / "s", fetch).name == name


@pytest.mark.parametrize("version", ["24.0", "24.0.1.dev1", "v24.0.1", "24.0.1+local", ""])
def test_run_gate_fetcher_refuses_non_stable_versions(tmp_path, version):
    fetcher, fetch, _, _ = _fake_run_gate_release()
    with pytest.raises(SystemExit, match="not a stable"):
        fetcher.fetch_wheel(version, tmp_path / "s", fetch)


def test_run_gate_fetcher_refuses_a_sidecar_pointer_or_bytes_that_disagree(tmp_path):
    fetcher, fetch, _, _ = _fake_run_gate_release(sidecar="0" * 64)
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        fetcher.fetch_wheel("24.1.0", tmp_path / "a", fetch)
    assert not (tmp_path / "a").exists()
    fetcher, fetch, _, _ = _fake_run_gate_release(pointer_sha="1" * 64)
    with pytest.raises(SystemExit, match="disagree"):
        fetcher.fetch_wheel(None, tmp_path / "b", fetch)
    assert not (tmp_path / "b").exists()
    fetcher, fetch, _, _ = _fake_run_gate_release(sidecar="not-a-digest")
    with pytest.raises(SystemExit, match="no valid sha256"):
        fetcher.fetch_wheel("24.1.0", tmp_path / "c", fetch)


def test_run_gate_fetcher_refuses_a_pointer_for_another_project_or_asset(tmp_path):
    fetcher, _, _, _ = _fake_run_gate_release()
    for key, value in (("project", "cmru"), ("tag", "run-gate-v9.9.9"),
                       ("asset", "run_gate-9.9.9-py3-none-any.whl"),
                       ("url", "https://example.invalid/run_gate-24.1.0-py3-none-any.whl"),
                       ("version", "24.1.0.dev1")):
        pointer = {
            "project": "run-gate", "version": "24.1.0", "tag": "run-gate-v24.1.0",
            "asset": fetcher._asset_name("24.1.0"), "url": fetcher._asset_url("24.1.0"),
            "sha256": "2" * 64,
        }
        pointer[key] = value
        with pytest.raises(SystemExit):
            fetcher.resolve(None, fetcher.POINTER_URL, lambda _u, p=pointer: json.dumps(p).encode())


def test_run_gate_fetcher_refuses_a_second_wheel_in_the_stage_directory(tmp_path):
    fetcher, fetch, _, _ = _fake_run_gate_release()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "run_gate-1.0.0-py3-none-any.whl").write_bytes(b"x")
    with pytest.raises(SystemExit, match="already holds"):
        fetcher.fetch_wheel(None, stage, fetch)


def test_run_gate_fetcher_turns_download_failures_into_clean_exits(tmp_path):
    fetcher, _, _, _ = _fake_run_gate_release()

    def broken(_url):
        raise OSError("boom")

    with pytest.raises(SystemExit, match="cannot read the release pointer"):
        fetcher.fetch_wheel(None, tmp_path / "s", broken)
    with pytest.raises(SystemExit, match="cannot read the checksum sidecar"):
        fetcher.fetch_wheel("24.1.0", tmp_path / "s", broken)


def test_run_gate_fetcher_main_prints_the_staged_path(tmp_path, monkeypatch, capsys):
    fetcher, fetch, _, name = _fake_run_gate_release("24.1.0")
    monkeypatch.setattr(fetcher._net, "_urlopen_bytes", fetch)
    assert fetcher.main(["--dest", str(tmp_path / "s"), "--version", "24.1.0"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "s" / name)


def _run_build_image(tmp_path, *args, stub_fetcher=None, env=None):
    """Run a COPY of build-image.sh whose repo root is a scratch tree (exit 2 happens before docker)."""
    root = tmp_path / "repo"
    (root / "tester-unified").mkdir(parents=True)
    source = GENERATOR.parent
    (root / "tester-unified" / "build-image.sh").write_text((source / "build-image.sh").read_text("utf-8"))
    (root / "tester-unified" / "Dockerfile").write_text((source / "Dockerfile").read_text("utf-8"))
    fetcher = root / "tester-unified" / "fetch-run-gate-wheel.py"
    fetcher.write_text(stub_fetcher or "raise SystemExit('stub fetcher must not run')\n")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    return subprocess.run(
        ["bash", str(root / "tester-unified" / "build-image.sh"), *args],
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "TMPDIR": str(scratch), **(env or {})},
    ), scratch


def test_build_image_refuses_published_and_wheel_dir_together_before_any_fetch(tmp_path):
    result, scratch = _run_build_image(tmp_path, "--run-gate-published", "latest", "--wheel-dir", "x")
    assert result.returncode == 2 and "mutually exclusive" in result.stderr
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("age", ["14", "P14", "14D", "P1W", "P14D;x"])
def test_build_image_rejects_malformed_pip_age_durations(tmp_path, age):
    result, _ = _run_build_image(tmp_path, "--pip-min-release-age", age)
    assert result.returncode == 2 and "ISO-8601 day duration" in result.stderr


def test_build_image_checks_the_cgroup_parent_before_downloading(tmp_path):
    result, scratch = _run_build_image(tmp_path, "--run-gate-published", "latest")
    assert result.returncode == 2 and "CGROUP_PARENT_DEV_GATES" in result.stderr
    assert "stub fetcher must not run" not in result.stderr
    assert list(scratch.iterdir()) == []


def test_build_image_removes_the_published_staging_directory_when_it_stops_early(tmp_path):
    stub = (
        "import sys\nfrom pathlib import Path\n"
        "dest = Path(sys.argv[sys.argv.index('--dest') + 1])\n"
        "(dest / 'run_gate-1.0.0-py3-none-any.whl').write_bytes(b'x')\n"
        "print('staged', file=sys.stderr)\n"
    )
    # The cgroup parent is given, so the fetch runs; the missing `cmru` binary then stops the script.
    result, scratch = _run_build_image(
        tmp_path, "--run-gate-published", "1.0.0", "--cgroup-parent", "dev-gates.slice",
        stub_fetcher=stub,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert "staged" in result.stderr and result.returncode != 0
    assert list(scratch.iterdir()) == []


def test_build_image_stops_when_the_fetcher_refuses(tmp_path):
    result, scratch = _run_build_image(
        tmp_path, "--run-gate-published", "latest", "--cgroup-parent", "dev-gates.slice",
        stub_fetcher="raise SystemExit(1)\n",
    )
    assert result.returncode == 2
    assert list(scratch.iterdir()) == []


def test_wheel_builder_ages_its_pip_installs_and_pins_nothing():
    text = (GENERATOR.parents[1] / "wheel-builder" / "Dockerfile").read_text("utf-8")
    assert re.search(r"^ARG PIP_MIN_RELEASE_AGE=P14D$", text, re.MULTILINE)
    installs = [line for line in text.splitlines() if "/opt/wheel-builder-venv/bin/pip install" in line]
    assert len(installs) == 2
    for line in installs:
        assert '--uploaded-prior-to "${PIP_MIN_RELEASE_AGE}"' in line and "==" not in line

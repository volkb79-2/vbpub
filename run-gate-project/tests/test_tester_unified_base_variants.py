"""Tester-image host-tool step must work on both supported bases.

The MDT cockpit base already ships Docker's own `docker-ce-cli` and
`docker-compose-plugin`; the upstream Python devcontainer base ships neither.
Debian's `docker-cli` / `docker-compose` own the same files as the Docker
packages, so an unconditional install over the MDT base is refused by dpkg (or
silently swaps the client). The step must therefore install only what the base
lacks.

No image is built here (a build is a registered release-lane activity). The
Dockerfile's real RUN step is extracted and executed under `/bin/sh` -- the
shell Docker uses -- against shim tools that behave like each base, with a
dpkg-style file-ownership clash in the `apt-get` shim. This proves the step's
behaviour, not its spelling: a change that reintroduces the unconditional
install fails on the MDT variant, which the legacy-step control also shows.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "tester-unified" / "Dockerfile"

BASE_PACKAGES = {"ca-certificates", "curl", "git", "jq", "openssh-client",
                 "util-linux"}

LEGACY_STEP = (
    "set -eu && apt-get update && apt-get install -y --no-install-recommends "
    "ca-certificates curl docker-cli docker-compose git jq openssh-client "
    "util-linux && rm -rf /var/lib/apt/lists/* && docker --version && "
    "docker compose version && git --version && nsenter --version")

DOCKER_SHIM = """\
#!/bin/sh
case "$1" in
  --version) echo "Docker version 29.0.0"; exit 0 ;;
  compose)
    if [ -e "$SHIM_STATE/compose" ]; then
      echo "Docker Compose version v2.40.3"; exit 0
    fi
    exit 1 ;;
esac
exit 0
"""

# dpkg refuses to overwrite a file owned by another package; model that for
# the two Debian packages that collide with Docker's own.
APT_SHIM = """\
#!/bin/sh
echo "$*" >> "$SHIM_LOG"
[ "$1" = install ] || exit 0
for package in "$@"; do
  case "$package" in
    docker-cli)
      if [ -e "$SHIM_BIN/docker" ]; then
        echo "dpkg: error: trying to overwrite '/usr/bin/docker', which is" \\
          "also in package docker-ce-cli" >&2
        exit 100
      fi
      /bin/cp "$SHIM_BIN/docker.template" "$SHIM_BIN/docker" ;;
    docker-compose)
      if [ -e "$SHIM_STATE/compose" ]; then
        echo "dpkg: error: trying to overwrite the compose plugin, which is" \\
          "also in package docker-compose-plugin" >&2
        exit 100
      fi
      : > "$SHIM_STATE/compose" ;;
  esac
done
exit 0
"""


def host_tools_step() -> str:
    """The Dockerfile's apt RUN instruction as one shell command line."""
    instructions: list[str] = []
    pending: list[str] = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if pending:
            pending.append(raw)
        elif raw.startswith("RUN "):
            pending = [raw]
        else:
            continue
        if not raw.rstrip().endswith("\\"):
            joined = " ".join(
                part.rstrip().removesuffix("\\").strip() for part in pending)
            instructions.append(joined.removeprefix("RUN ").strip())
            pending = []
    # The Dockerfile also installs zsh and systemd in their own RUNs (MDTC-02 / INT-01); this test is
    # about the host-tools step, the one that chooses packages by capability.
    steps = [step for step in instructions if "apt-get install" in step and "packages=" in step]
    assert len(steps) == 1, steps
    return steps[0]


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_step(tmp_path: Path, step: str, *, docker: bool, compose: bool):
    """Run `step` in a world that has (or lacks) the Docker client/compose."""
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    log = tmp_path / "apt.log"
    log.write_text("")
    _write(bin_dir / "docker.template", DOCKER_SHIM)
    if docker:
        _write(bin_dir / "docker", DOCKER_SHIM)
    if compose:
        (state / "compose").write_text("")
    _write(bin_dir / "apt-get", APT_SHIM)
    _write(bin_dir / "git", "#!/bin/sh\necho 'git version 2.47.3'\n")
    _write(bin_dir / "nsenter", "#!/bin/sh\necho 'nsenter from util-linux'\n")
    # the step deletes apt's lists; never let a test touch the real ones
    _write(bin_dir / "rm", "#!/bin/sh\nexit 0\n")
    # PATH is ONLY the shim directory, so a real /usr/bin/docker on the
    # machine running this test cannot make the "upstream" variant look
    # like the MDT one.
    env = {"PATH": str(bin_dir), "SHIM_STATE": str(state),
           "SHIM_LOG": str(log), "SHIM_BIN": str(bin_dir)}
    result = subprocess.run(["/bin/sh", "-c", step], env=env,
                            capture_output=True, text=True)
    installs = [line.split() for line in log.read_text().splitlines()
                if line.startswith("install")]
    return result, installs


def installed_packages(installs: list[list[str]]) -> set[str]:
    return {item for line in installs for item in line[1:]
            if not item.startswith("-")}


def test_mdt_base_keeps_its_docker_client_and_compose_plugin(tmp_path):
    result, installs = run_step(tmp_path, host_tools_step(),
                                docker=True, compose=True)
    assert result.returncode == 0, result.stderr
    assert installed_packages(installs) == BASE_PACKAGES
    assert "dpkg: error" not in result.stderr


def test_base_with_docker_client_but_no_compose_installs_only_compose(
        tmp_path):
    result, installs = run_step(tmp_path, host_tools_step(),
                                docker=True, compose=False)
    assert result.returncode == 0, result.stderr
    assert installed_packages(installs) == BASE_PACKAGES | {"docker-compose"}


def test_upstream_python_base_gets_both_debian_docker_packages(tmp_path):
    result, installs = run_step(tmp_path, host_tools_step(),
                                docker=False, compose=False)
    assert result.returncode == 0, result.stderr
    assert installed_packages(installs) == BASE_PACKAGES | {
        "docker-cli", "docker-compose"}
    # ... and the step's own verification ran against what it installed
    assert "Docker version" in result.stdout
    assert "Docker Compose version" in result.stdout


def test_the_legacy_unconditional_install_fails_on_the_mdt_base(tmp_path):
    """Control: the oracle can go red. The pre-fix step collides on the MDT
    base exactly as dpkg would."""
    result, _installs = run_step(tmp_path, LEGACY_STEP,
                                 docker=True, compose=True)
    assert result.returncode != 0
    assert "trying to overwrite" in result.stderr


def test_the_step_still_verifies_docker_compose_git_and_nsenter(tmp_path):
    step = host_tools_step()
    for check in ("docker --version", "docker compose version",
                  "git --version", "nsenter --version"):
        assert check in step
    assert os.path.isfile(DOCKERFILE)

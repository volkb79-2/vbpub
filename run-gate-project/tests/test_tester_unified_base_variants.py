"""Tester-image host-tool and compose steps must work on every supported base.

The MDT cockpit base already ships Docker's own `docker-ce-cli` and
`docker-compose-plugin`; tester-base ships `docker-ce-cli` only; the upstream Python devcontainer
base ships neither. Compose is Docker's own plugin at an exact version checked against a pinned
sha256 (INT-01 D-006), never Debian's `docker-compose`.
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


def _instructions() -> list[str]:
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
            instructions.append(" ".join(
                part.rstrip().removesuffix("\\").strip() for part in pending).removeprefix("RUN ").strip())
            pending = []
    return instructions


def compose_step() -> str:
    steps = [step for step in _instructions() if "dpkg -i /tmp/docker-compose-plugin.deb" in step]
    assert len(steps) == 1, steps
    return steps[0]


def compose_pins() -> dict[str, str]:
    import re
    text = DOCKERFILE.read_text(encoding="utf-8")
    return dict(re.findall(r"^ARG (DOCKER_COMPOSE_PLUGIN_\w+)=(\S+)$", text, re.MULTILINE))


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_step(tmp_path: Path, step: str, *, docker: bool, compose: bool, arch: str = "amd64", sha_rc: int = 0):
    """Run `step` in a world that has (or lacks) the Docker client/compose."""
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir(parents=True)
    state.mkdir(parents=True)
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
    # the compose step: download, checksum and dpkg, recorded; installing the package makes compose answer
    _write(bin_dir / "dpkg", """#!/bin/sh
case "$1" in
  --print-architecture) echo "$SHIM_ARCH" ;;
  -i) echo "dpkg $*" >> "$SHIM_LOG"; : > "$SHIM_STATE/compose" ;;
esac
exit 0
""")
    _write(bin_dir / "curl", "#!/bin/sh\necho \"curl $*\" >> \"$SHIM_LOG\"\nexit 0\n")
    _write(bin_dir / "sha256sum", """#!/bin/sh
read -r line
echo "sha256sum $* <<$line" >> "$SHIM_LOG"
exit "$SHIM_SHA_RC"
""")
    # PATH is ONLY the shim directory, so a real /usr/bin/docker on the
    # machine running this test cannot make the "upstream" variant look
    # like the MDT one.
    eatmydata_lib = {
        "amd64": "/usr/lib/x86_64-linux-gnu/libeatmydata.so.1",
        "arm64": "/usr/lib/aarch64-linux-gnu/libeatmydata.so.1",
    }[arch]
    env = {"PATH": str(bin_dir), "SHIM_STATE": str(state),
           "SHIM_LOG": str(log), "SHIM_BIN": str(bin_dir),
           "SHIM_ARCH": arch, "SHIM_SHA_RC": str(sha_rc),
           "TESTER_EATMYDATA_LIB": eatmydata_lib, **compose_pins()}
    result = subprocess.run(["/bin/sh", "-c", step], env=env,
                            capture_output=True, text=True)
    installs = [line.split() for line in log.read_text().splitlines()
                if line.startswith("install")]
    result.shim_log = log.read_text().splitlines()
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
    compose, _ = run_step(tmp_path / "c", compose_step(), docker=True, compose=True)
    assert compose.returncode == 0, compose.stderr
    assert not any(line.startswith(("curl", "dpkg", "sha256sum")) for line in compose.shim_log)


def test_tester_base_gets_dockers_pinned_compose_plugin_and_no_debian_package(tmp_path):
    """docker-ce-cli present, compose absent: the host-tools step installs nothing extra and the compose
    step fetches Docker's own plugin at the pinned version, checks the pinned sha256, then dpkg -i."""
    tools, installs = run_step(tmp_path / "t", host_tools_step(), docker=True, compose=False)
    assert tools.returncode == 0, tools.stderr
    assert installed_packages(installs) == BASE_PACKAGES
    result, installs = run_step(tmp_path / "c", compose_step(), docker=True, compose=False)
    assert result.returncode == 0, result.stderr
    pins = compose_pins()
    version = pins["DOCKER_COMPOSE_PLUGIN_VERSION"]
    log = result.shim_log
    curl = next(line for line in log if line.startswith("curl"))
    assert f"/pool/stable/amd64/docker-compose-plugin_{version}_amd64.deb" in curl
    assert f"sha256sum -c - <<{pins['DOCKER_COMPOSE_PLUGIN_SHA256_AMD64']}  /tmp/docker-compose-plugin.deb" in log
    assert log.index(next(line for line in log if line.startswith("sha256sum"))) < log.index("dpkg -i /tmp/docker-compose-plugin.deb")
    assert installs == []  # no apt install at all, so never Debian's docker-compose
    assert "Docker Compose version" in result.stdout


def test_the_arm64_pin_is_used_on_arm64(tmp_path):
    result, _ = run_step(tmp_path, compose_step(), docker=True, compose=False, arch="arm64")
    assert result.returncode == 0, result.stderr
    pins = compose_pins()
    assert any(f"<<{pins['DOCKER_COMPOSE_PLUGIN_SHA256_ARM64']}  " in line for line in result.shim_log)
    assert any("_arm64.deb" in line for line in result.shim_log if line.startswith("curl"))


def test_a_checksum_mismatch_stops_before_dpkg(tmp_path):
    result, _ = run_step(tmp_path, compose_step(), docker=True, compose=False, sha_rc=1)
    assert result.returncode != 0
    assert not any(line.startswith("dpkg") for line in result.shim_log)


def test_an_unsupported_architecture_is_refused_before_any_download(tmp_path):
    result, _ = run_step(tmp_path, compose_step(), docker=True, compose=False, arch="riscv64")
    assert result.returncode != 0
    assert "unsupported architecture" in result.stderr
    assert not any(line.startswith("curl") for line in result.shim_log)


def test_upstream_python_base_gets_debians_docker_client_only_and_the_pinned_compose(tmp_path):
    tools, installs = run_step(tmp_path / "t", host_tools_step(), docker=False, compose=False)
    assert tools.returncode == 0, tools.stderr
    assert installed_packages(installs) == BASE_PACKAGES | {"docker-cli"}
    assert "Docker version" in tools.stdout
    compose, _ = run_step(tmp_path / "c", compose_step(), docker=True, compose=False)
    assert compose.returncode == 0, compose.stderr


def test_the_legacy_unconditional_install_fails_on_the_mdt_base(tmp_path):
    """Control: the oracle can go red. The pre-fix step collides on the MDT
    base exactly as dpkg would."""
    result, _installs = run_step(tmp_path, LEGACY_STEP,
                                 docker=True, compose=True)
    assert result.returncode != 0
    assert "trying to overwrite" in result.stderr


def test_the_steps_still_verify_docker_compose_git_and_nsenter(tmp_path):
    tools = host_tools_step()
    for check in ("docker --version", "git --version", "nsenter --version"):
        assert check in tools
    assert "docker compose version" in compose_step()
    assert os.path.isfile(DOCKERFILE)

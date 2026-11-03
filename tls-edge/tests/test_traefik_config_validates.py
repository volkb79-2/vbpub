"""Start Traefik against the rendered static + dynamic config, with NO network and NO published port.

Opt-in (TLS_EDGE_VALIDATE_TRAEFIK=1) because it runs one short container.  The
container is created, fed the config with `docker cp` (no bind mount, so it
works under DooD), started with no network, observed for a few seconds and
removed by its exact name.  A static-config error exits the process at start; a
dynamic-config (file provider) error is logged by the provider.  Provider
errors about the unreachable docker socket proxy are expected and ignored.

Host rules: 512 MiB, dev-gates.slice (CGROUP_PARENT_DEV_GATES), one container.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

import pytest

from conftest import defaults_config, merge, needs_docker, needs_jinja, render_to

pytestmark = [
    needs_docker, needs_jinja,
    pytest.mark.skipif(os.environ.get("TLS_EDGE_VALIDATE_TRAEFIK") != "1",
                       reason="set TLS_EDGE_VALIDATE_TRAEFIK=1 to run the Traefik config validation"),
]

IMAGE = "traefik:v3.7"
FATAL = re.compile(r"field not found|command traefik error|cannot parse|invalid|unknown", re.I)


def _docker(*args: str, check: bool = True, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "DOCKER_CONFIG": os.path.expanduser("~/.docker-nocreds")}
    return subprocess.run(["docker", *args], capture_output=True, env=env, check=check, input=stdin)


def _config_tar(out) -> bytes:
    """A tar of etc/traefik/{traefik.yml,conf.d/*} for `docker cp - NAME:/` (no bind mount)."""
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(out / "traefik.yml", arcname="etc/traefik/traefik.yml")
        tar.add(out / "conf.d", arcname="etc/traefik/conf.d")
    return buffer.getvalue()


@pytest.mark.parametrize("mode", ["acme-tls", "acme-http", "acme-dns", "static"])
def test_traefik_accepts_the_rendered_config(tmp_path, mode):
    override = {"tls": {"mode": mode}}
    if mode == "acme-dns":
        override["acme"] = {"dns": {"provider": "cloudflare"}}
    if mode == "static":
        override["static"] = {"domains": ["edge.example.test"]}
    out = render_to(tmp_path, merge(defaults_config(), override))
    name = f"tls-edge-validate-{os.getpid()}-{mode}"
    slice_name = os.environ.get("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    try:
        _docker("create", "--name", name, "--memory", "512m", "--cgroup-parent", slice_name,
                "--network", "none", "-e", "CF_DNS_API_TOKEN=dummy",
                IMAGE, "--configFile=/etc/traefik/traefik.yml")
        _docker("cp", "-", f"{name}:/", stdin=_config_tar(out))
        _docker("start", name)
        time.sleep(4)
        state = _docker("inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", name).stdout.decode().strip()
        logs = _docker("logs", name, check=False)
        text = (logs.stdout + logs.stderr).decode()
        assert state.startswith("running"), f"traefik exited early ({state}):\n{text[-1500:]}"
        bad = [l for l in text.splitlines()
               if "level=error" in l and "providerName=docker" not in l and "docker" not in l.lower()
               and FATAL.search(l)]
        assert not bad, "\n".join(bad)
        assert "providerName=file" not in "\n".join(
            l for l in text.splitlines() if "level=error" in l), text[-1500:]
    finally:
        _docker("rm", "-f", name, check=False)

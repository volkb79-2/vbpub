"""Stdlib-only command entry point for the packaged fleetd host agent."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import sys
from pathlib import Path
from typing import Sequence

from .admission import AdmissionError, ExitCode
from .agent_policy import AgentPolicy, DEFAULT_AGENT_POLICY_PATH, load_agent_policy
from .agent_providers import docker_container_identity_inspector, docker_object_inspector, legacy_job_ids
from .agent_server import AgentServer, serve_forced_command
from .agent_verbs import AgentVerbService
from .contracts import ContractError
from .facts import collect_host_facts

DEFAULT_AGENT_STATE_DIR = Path("/var/lib/fleetd-agent")
_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")
_PEP440_RE = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:(?P<dev>\.dev[0-9]+)|(?P<alpha>a[0-9]+)|(?P<beta>b[0-9]+)|(?P<rc>rc[0-9]+))?"
    r"(?:\.post(?P<post>[0-9]+))?(?:\+(?P<local>[0-9A-Za-z.-]+))?\Z"
)


def agent_semver(raw_version: str) -> str:
    """Convert fleetd's supported setuptools-scm/PEP 440 versions to SemVer."""
    if _VERSION_RE.fullmatch(raw_version):
        return raw_version
    match = _PEP440_RE.fullmatch(raw_version)
    if match is None:
        raise ContractError(f"fleetd distribution version {raw_version!r} cannot be represented as SemVer")
    core = f"{match['major']}.{match['minor']}.{match['patch']}"
    prerelease = ""
    if match["dev"]:
        prerelease = f"-dev.{match['dev'][4:]}"
    elif match["alpha"]:
        prerelease = f"-alpha.{match['alpha'][1:]}"
    elif match["beta"]:
        prerelease = f"-beta.{match['beta'][1:]}"
    elif match["rc"]:
        prerelease = f"-rc.{match['rc'][2:]}"
    elif match["post"]:
        prerelease = f"-post.{match['post']}"
    local = f"+{match['local']}" if match["local"] else ""
    return f"{core}{prerelease}{local}"


def installed_agent_version() -> str:
    try:
        raw_version = importlib.metadata.version("fleetd")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ContractError("fleetd-agent requires the packaged fleetd distribution metadata") from exc
    return agent_semver(raw_version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetd-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="serve the local host-agent socket")
    endpoint = serve.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--socket-path", type=Path)
    endpoint.add_argument("--listen-fd", type=int)
    serve.add_argument("--policy", type=Path, default=DEFAULT_AGENT_POLICY_PATH)
    serve.add_argument("--state-dir", type=Path, default=DEFAULT_AGENT_STATE_DIR)

    forced = commands.add_parser("forced-command", help="serve one framed ssh forced-command request")
    forced.add_argument("--group", required=True)
    forced.add_argument("--policy", type=Path, default=DEFAULT_AGENT_POLICY_PATH)
    forced.add_argument("--state-dir", type=Path, default=DEFAULT_AGENT_STATE_DIR)

    return parser


def _service(policy_path: Path, state_dir: Path) -> tuple[AgentVerbService, AgentPolicy]:
    policy = load_agent_policy(policy_path)
    provider_kwargs: dict[str, object] = {}
    if isinstance(policy, AgentPolicy):
        provider_kwargs = {
            "legacy_jobs_provider": lambda: legacy_job_ids(policy.legacy_jobs_dir),
            "inspector": docker_object_inspector(policy.query_timeout_s),
            "container_inspector": docker_container_identity_inspector(policy.query_timeout_s),
        }
    service = AgentVerbService(
        state_dir,
        policy,
        collect_host_facts,
        agent_version=installed_agent_version(),
        **provider_kwargs,
    )
    return service, policy


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        service, policy = _service(args.policy, args.state_dir)
        if args.command == "serve":
            if args.socket_path is not None:
                server = AgentServer.on_path(args.socket_path, service, policy)
            else:
                server = AgentServer.from_fd(args.listen_fd, service, policy)
            try:
                server.serve_forever()
            finally:
                server.close()
        else:
            return serve_forced_command(sys.stdin.buffer, sys.stdout.buffer, service, policy, args.group)
    except (ContractError, AdmissionError) as exc:
        print(f"fleetd-agent: ERROR {exc}", file=sys.stderr, flush=True)
        if isinstance(exc, AdmissionError):
            return int(exc.exit_code)
        return ExitCode.REFUSAL
    except OSError as exc:
        print(f"fleetd-agent: ERROR {exc}", file=sys.stderr, flush=True)
        return ExitCode.RUNNER_FAILURE
    return ExitCode.PASS

#!/usr/bin/env python3
"""Read-only estate requirement checks for the current cockpit or a runner host."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


HOST_KINDS = {"controller", "test-host"}
UNKNOWN_EXIT = 125
TIMEOUT_EXIT = 124
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "ESTATE-REQUIREMENTS.toml"
REMOTE_GATE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "netcup" / "runner" / "remote-gate.py"


@dataclass(frozen=True)
class Result:
    rc: int
    out: str = ""
    err: str = ""


class Runner(Protocol):
    """The only process boundary; tests replace it with a fake."""

    def run(self, argv: Sequence[str], timeout: float) -> Result: ...


class ProcessRunner:
    """Run one bounded read-only probe and its children in a private process group."""

    def run(self, argv: Sequence[str], timeout: float) -> Result:
        try:
            process = subprocess.Popen(
                list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError:
            return Result(127, "", "could not start verifier")
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                out, err = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                out, err = process.communicate()
            return Result(TIMEOUT_EXIT, out.decode(errors="replace"), "timeout")
        return Result(
            process.returncode,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
        )


@dataclass(frozen=True)
class RemoteTarget:
    ssh: Sequence[str]
    cockpit: str
    runner_home: str
    guard: Any


def load_requirements(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"`([^`]*)`", r"\1", match.group(1)).lower()
        base = re.sub(r"-+", "-", re.sub(r"[^\w -]", "", heading))
        base = re.sub(r"\s+", "-", base).strip("-")
        suffix = counts.get(base, 0)
        counts[base] = suffix + 1
        anchors.add(base if suffix == 0 else f"{base}-{suffix}")
    return anchors


def validate_requirements(data: dict[str, Any], manifest_path: Path) -> list[str]:
    """Check required schema fields, probes, dependencies, and local doc links."""
    errors: list[str] = []
    components = data.get("components")
    if not isinstance(components, dict) or not components:
        return ["components must be a non-empty table"]

    seen: set[str] = set()
    for component_id, row in components.items():
        if not isinstance(row, dict):
            errors.append(f"{component_id}: component must be a table")
            continue
        for field in ("kind", "applies_to", "requires", "provides", "minimum_version", "verify", "fix", "owner", "doc"):
            if field not in row:
                errors.append(f"{component_id}: missing {field}")
        applies = row.get("applies_to")
        if not isinstance(applies, str) or applies not in ("controller", "test-host", "both"):
            errors.append(f"{component_id}: invalid applies_to")
        if "required" in row and not isinstance(row["required"], bool):
            errors.append(f"{component_id}: required must be a boolean")
        for field in ("kind", "minimum_version", "fix", "owner", "doc"):
            if field in row and (not isinstance(row[field], str) or not row[field].strip()):
                errors.append(f"{component_id}: {field} must be a non-empty string")
        for field in ("provides", "requires"):
            if field in row and (
                not isinstance(row[field], list)
                or not all(isinstance(value, str) and value.strip() for value in row[field])
            ):
                errors.append(f"{component_id}: {field} must be a list of non-empty strings")
        verify = row.get("verify")
        if not isinstance(verify, dict):
            errors.append(f"{component_id}: verify must be a table")
        else:
            for host_kind in ("controller", "test-host"):
                if applies == "both" or applies == host_kind:
                    probe = verify.get(host_kind, verify.get("both", verify if "command" in verify else None))
                    if not isinstance(probe, dict) or not probe.get("command") or not probe.get("expected_regex"):
                        errors.append(f"{component_id}: missing {host_kind} command or expected_regex")
                    else:
                        try:
                            if float(probe.get("timeout_seconds", 10)) <= 0:
                                errors.append(f"{component_id}: timeout_seconds must be positive")
                            re.compile(probe["expected_regex"], flags=re.MULTILINE)
                            if probe.get("version_pattern"):
                                re.compile(probe["version_pattern"])
                        except (TypeError, ValueError, re.error):
                            errors.append(f"{component_id}: invalid timeout or regular expression")
        requires = row.get("requires", [])
        if not isinstance(requires, list):
            errors.append(f"{component_id}: requires must be a list")
        else:
            for required_id in requires:
                if required_id not in seen:
                    errors.append(f"{component_id}: requirement {required_id!r} must precede it")
        doc = row.get("doc")
        if isinstance(doc, str) and doc.strip():
            target = (manifest_path.parent / doc.split("#", 1)[0]).resolve()
            if not target.is_file():
                errors.append(f"{component_id}: doc link does not resolve")
            elif "#" in doc and doc.split("#", 1)[1] not in _markdown_anchors(target):
                errors.append(f"{component_id}: doc anchor does not resolve")
        seen.add(component_id)
    return errors


def _probe_for(row: dict[str, Any], host_kind: str) -> dict[str, Any]:
    verify = row["verify"]
    return verify.get(host_kind, verify.get("both", verify if "command" in verify else {}))


def _argv_for(
    command: str,
    host_kind: str,
    repo_root: Path,
    remote: RemoteTarget | None,
) -> list[str]:
    if remote is None:
        script = f"export ESTATE_REPO_ROOT={shlex.quote(str(repo_root))}\n{command}"
        return ["bash", "-c", script]
    script = (
        f"export ESTATE_REPO_ROOT={shlex.quote(remote.runner_home + '/src/vbpub-current')}\n"
        f"export ESTATE_COCKPIT={shlex.quote(remote.cockpit)}\n"
        f"{command}"
    )
    return list(remote.ssh) + [remote.guard(script)]


def _evidence(text: str, fallback: str) -> str:
    printable = " ".join(text.replace("\x00", " ").split())
    return printable[:180] if printable else fallback


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def run_checks(
    data: dict[str, Any],
    host_kind: str,
    runner: Runner,
    manifest_path: Path,
    repo_root: Path,
    remote: RemoteTarget | None = None,
) -> list[dict[str, Any]]:
    if host_kind not in HOST_KINDS:
        raise ValueError(f"unsupported host kind: {host_kind}")
    results: list[dict[str, Any]] = []
    status_by_id: dict[str, str] = {}
    components = data["components"]

    for component_id, row in components.items():
        applies = row["applies_to"]
        if applies not in ("both", host_kind):
            continue
        required = bool(row.get("required", True))
        failed_dependencies = [
            dependency for dependency in row.get("requires", [])
            if status_by_id.get(dependency) != "pass"
        ]
        if failed_dependencies:
            result = {
                "component": component_id,
                "status": "unknown",
                "evidence": "blocked by requirement: " + ", ".join(failed_dependencies),
                "fix": row["fix"],
                "required": required,
            }
        else:
            probe = _probe_for(row, host_kind)
            timeout = float(probe.get("timeout_seconds", 10))
            argv = _argv_for(probe["command"], host_kind, repo_root, remote)
            try:
                execution = runner.run(argv, timeout=timeout)
            except TimeoutError:
                execution = Result(TIMEOUT_EXIT, "", "timeout")

            expected = re.search(probe["expected_regex"], execution.out, flags=re.MULTILINE)
            version_pattern = probe.get("version_pattern")
            minimum_version = row["minimum_version"]
            if execution.rc in (TIMEOUT_EXIT, UNKNOWN_EXIT):
                status = "unknown"
                evidence = (
                    f"verification timed out after {timeout:g}s"
                    if execution.rc == TIMEOUT_EXIT
                    else "probe could not observe this host context"
                )
            elif execution.rc != 0:
                status = "fail"
                evidence = f"verifier exited {execution.rc}"
            elif not expected:
                status = "fail"
                evidence = "expected output was not present"
            elif version_pattern:
                match = re.search(version_pattern, execution.out)
                if not match:
                    status = "fail"
                    evidence = "version was not present in verifier output"
                elif minimum_version not in ("not established", "capability-based", "current checkout", "optional") and _version_tuple(match.group(1)) < _version_tuple(minimum_version):
                    status = "fail"
                    evidence = f"version {match.group(1)} is below {minimum_version}"
                else:
                    status = "pass"
                    evidence = _evidence(execution.out, "exit 0")
            else:
                status = "pass"
                evidence = _evidence(execution.out, "exit 0")
            result = {
                "component": component_id,
                "status": status,
                "evidence": evidence,
                "fix": row["fix"],
                "required": required,
            }
        status_by_id[component_id] = result["status"]
        results.append(result)
    return results


def _load_remote_gate() -> Any:
    spec = importlib.util.spec_from_file_location("estate_doctor_remote_gate", REMOTE_GATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("remote-gate loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _render_table(host_kind: str, results: list[dict[str, Any]], exit_code: int) -> str:
    lines = [f"Estate requirements: {host_kind}", "component | status | evidence | fix hint"]
    for row in results:
        lines.append(" | ".join((row["component"], row["status"].upper(), row["evidence"], row["fix"].replace("\n", " "))))
    lines.append(f"exit: {exit_code}")
    return "\n".join(lines)


def checks_exit_code(results: list[dict[str, Any]]) -> int:
    return 0 if all(row["status"] == "pass" for row in results if row["required"]) else 1


def main(argv: Sequence[str] | None = None, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the estate's read-only host requirement probes.")
    parser.add_argument("--host", help="test-host name from the existing remote-gate hosts file")
    parser.add_argument("--hosts-file", help="remote-gate hosts JSON; defaults to REMOTE_GATE_HOSTS or its normal path")
    parser.add_argument("--requirements", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="write machine-readable results")
    args = parser.parse_args(argv)
    process_runner = runner or ProcessRunner()
    manifest_path = args.requirements.resolve()
    repo_root = Path(__file__).resolve().parents[2]

    try:
        data = load_requirements(manifest_path)
        schema_errors = validate_requirements(data, manifest_path)
        if schema_errors:
            raise ValueError("requirements file failed validation")
        host_kind = "test-host" if args.host else "controller"
        remote_target = None
        if args.host:
            remote_gate = _load_remote_gate()
            hosts_path = args.hosts_file or os.environ.get("REMOTE_GATE_HOSTS") or remote_gate.DEFAULT_HOSTS
            hosts = remote_gate.load_hosts(os.path.expanduser(hosts_path))
            context = remote_gate.Ctx(
                runner=process_runner, hosts=hosts, out_dir=Path.cwd(), say=lambda _message: None,
            )
            host = remote_gate.get_host(context, args.host)
            remote_gate.identity(context, host)
            remote_target = RemoteTarget(
                ssh=host.ssh,
                cockpit=host.cockpit,
                runner_home=host.runner_home,
                guard=lambda script: remote_gate.guarded(host, script),
            )
        results = run_checks(data, host_kind, process_runner, manifest_path, repo_root, remote_target)
        exit_code = checks_exit_code(results)
        payload = {"host_kind": host_kind, "checks": results, "exit_code": exit_code}
    except Exception:
        exit_code = 1
        payload = {"host_kind": "test-host" if args.host else "controller", "checks": [], "error": "requirements or remote host preflight failed", "exit_code": exit_code}

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(_render_table(payload["host_kind"], payload["checks"], payload["exit_code"]))
        if "error" in payload:
            print(payload["error"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

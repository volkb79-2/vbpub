#!/usr/bin/env python3
"""Publish the daemon-wide run-gate gates cap from host capacity (MDT-077).

run-gate's count admission (``[admission] enabled = true`` in a consumer's run-gate.toml) reads one
Docker-daemon object: a never-started ``ciu-admission-<generation>`` container whose label
``ciu.admission.tiers.gates.max_concurrent`` is the cap. Nothing but the operator verb
``run-gate admission set --max-concurrent N`` creates it, and a fresh Docker daemon (a reinstalled host)
has none, so every lane of an admission-enabled project ends ``NOT_RUN no-headroom`` while the host idles.

The limit is the HOST's to state (AGENTS.md "limits are host-owned"). This module derives it from the same
capacity host-setup already enforces on ``dev-gates.slice`` and publishes it, idempotently:

    cap = max(1, min(cpu_cap, memory_cap))
    cpu_cap    = floor(gates CPU cores / ADMISSION_CPUS_PER_GATE)               (default 2 CPUs per gate)
    memory_cap = floor(gates MemoryHigh / ADMISSION_MEMORY_PER_GATE)            (default 2G per gate;
                 no term when the gates tier has no MemoryHigh)

``gates CPU cores`` is the effective minimum of the ``dev.slice`` and gates-tier CPUQuota values. Auto quotas
come from ``mdt_capacity``; explicit ``DEV_CPU_QUOTA`` and ``DEV_GATES_CPU_QUOTA`` percentages bound that
share, and ``-`` omits that level's quota. ``gates MemoryHigh`` is the dedicated-dev
derivation (``mdt_capacity.dedicated_memory``) or the configured ``DEV_GATES_MEMORY_HIGH``. A 4 vCPU / 8 GB
dedicated-dev host (v1001) therefore publishes 2.

Pure functions take the parsed env and the host totals; every host effect (docker, run-gate) goes through an
injected ``run`` callable, so the tests use fakes and nothing here touches a host or Docker on its own.

Run context. ``run-gate admission set`` needs a run-gate config with an ``[admission]`` table naming a local
ticket image, a local Docker endpoint and ``$CGROUP_PARENT_DEV_GATES``. host-setup supplies a minimal config
(stdin or a temporary directory, never a consumer checkout) and the gates slice, and runs the verb either with
a run-gate on the host or inside the cockpit container that carries it (``ADMISSION_COCKPIT``).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping, Sequence

import mdt_capacity as cap

GATES_SLICE = "dev-gates.slice"
GATES_ENV = "CGROUP_PARENT_DEV_GATES"

DEFAULT_CPUS_PER_GATE = "2"
DEFAULT_MEMORY_PER_GATE = "2G"
DEFAULT_UNREADABLE_POLICY = "refuse"
DEFAULT_COCKPIT_USER = "vscode"
MEMORY_PER_GATE_MIN = 256 * cap.MIB

PUBLISH_MODES = ("auto", "missing", "replace", "off")
POLICIES = ("refuse", "unbudgeted")

# ------------------------------------------------------------------------------------------- validators
# One definition for the wizard's strict validator (mdt-host-setup-wizard.py merges VALIDATORS) and for
# the settings parser below. Each returns None when valid, else the reason.

_IMAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]*")
_PATH_RE = re.compile(r"[A-Za-z0-9_./][A-Za-z0-9_./+-]*")
_CONTAINER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_USER_RE = re.compile(r"[a-z_][a-z0-9_-]*|[0-9]+")


def validate_publish(value: str) -> str | None:
    if value != "" and value not in PUBLISH_MODES:
        return ("choose 'auto' (publish when absent; --admission-only reconciles), 'missing' (publish only when "
                "no cap is readable), 'replace' (also replace on full install), or 'off'")
    return None


def validate_max_concurrent(value: str) -> str | None:
    if value in ("", "auto"):
        return None
    if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
        return f"{value!r} is not valid: use 'auto' (derive from the gates tier) or a whole number of at least 1"
    return None


def validate_unreadable_policy(value: str) -> str | None:
    if value in ("",) + POLICIES:
        return None
    return "choose exactly 'refuse' or 'unbudgeted' (empty = refuse)"


def validate_cpus_per_gate(value: str) -> str | None:
    if value == "":
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        parsed = Decimal(0)
    if not re.fullmatch(r"[0-9]+(\.[0-9])?", value) or parsed < Decimal("0.5"):
        return f"{value!r} is not valid: use a number of CPUs per concurrent gate, at least 0.5, one decimal at most (e.g. 2)"
    return None


def validate_memory_per_gate(value: str) -> str | None:
    if value == "":
        return None
    parsed = cap.parse_size_bytes(value)
    if parsed is None or parsed < MEMORY_PER_GATE_MIN:
        return f"{value!r} is not valid: use a size of at least 256M such as 2G"
    return None


def validate_ticket_image(value: str) -> str | None:
    if value and not _IMAGE_RE.fullmatch(value):
        return f"{value!r} is not an image reference (no spaces or shell characters); empty = the cockpit's own image"
    return None


def validate_run_gate(value: str) -> str | None:
    if value and not _PATH_RE.fullmatch(value):
        return f"{value!r} is not a command name or path (must start with a letter, digit, _, . or /); empty = run-gate on PATH"
    return None


def validate_cockpit(value: str) -> str | None:
    if value and not _CONTAINER_RE.fullmatch(value):
        return f"{value!r} is not a Docker container name; empty = run run-gate on the host"
    return None


def validate_cockpit_user(value: str) -> str | None:
    if value and not _USER_RE.fullmatch(value):
        return f"{value!r} is not a user name or uid; empty = {DEFAULT_COCKPIT_USER}"
    return None


VALIDATORS: dict[str, Callable[[str], str | None]] = {
    "ADMISSION_PUBLISH": validate_publish,
    "ADMISSION_MAX_CONCURRENT": validate_max_concurrent,
    "ADMISSION_UNREADABLE_POLICY": validate_unreadable_policy,
    "ADMISSION_CPUS_PER_GATE": validate_cpus_per_gate,
    "ADMISSION_MEMORY_PER_GATE": validate_memory_per_gate,
    "ADMISSION_TICKET_IMAGE": validate_ticket_image,
    "ADMISSION_RUN_GATE": validate_run_gate,
    "ADMISSION_COCKPIT": validate_cockpit,
    "ADMISSION_COCKPIT_USER": validate_cockpit_user,
}


class AdmissionConfigError(ValueError):
    """An ADMISSION_* key holds a value its validator refuses."""


# ------------------------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class Settings:
    publish: str = "auto"
    max_concurrent: int | None = None  # None = derive
    unreadable_policy: str = DEFAULT_UNREADABLE_POLICY
    cpus_per_gate: Decimal = Decimal(DEFAULT_CPUS_PER_GATE)
    memory_per_gate: int = 2 * cap.GIB
    ticket_image: str = ""
    run_gate: str = ""
    cockpit: str = ""
    cockpit_user: str = DEFAULT_COCKPIT_USER

    @property
    def pinned(self) -> bool:
        return self.max_concurrent is not None


def parse_settings(values: Mapping[str, str]) -> Settings:
    """The nine optional ADMISSION_* keys of a parsed host-setup.env; an absent or empty key is its default."""
    for key, validate in VALIDATORS.items():
        error = validate(values.get(key, ""))
        if error:
            raise AdmissionConfigError(f"{key}: {error}")
    maximum = values.get("ADMISSION_MAX_CONCURRENT", "")
    return Settings(
        publish=values.get("ADMISSION_PUBLISH", "") or "auto",
        max_concurrent=None if maximum in ("", "auto") else int(maximum),
        unreadable_policy=values.get("ADMISSION_UNREADABLE_POLICY", "") or DEFAULT_UNREADABLE_POLICY,
        cpus_per_gate=Decimal(values.get("ADMISSION_CPUS_PER_GATE", "") or DEFAULT_CPUS_PER_GATE),
        memory_per_gate=cap.parse_size_bytes(values.get("ADMISSION_MEMORY_PER_GATE", "") or DEFAULT_MEMORY_PER_GATE) or 0,
        ticket_image=values.get("ADMISSION_TICKET_IMAGE", ""),
        run_gate=values.get("ADMISSION_RUN_GATE", ""),
        cockpit=values.get("ADMISSION_COCKPIT", ""),
        cockpit_user=values.get("ADMISSION_COCKPIT_USER", "") or DEFAULT_COCKPIT_USER,
    )


# ------------------------------------------------------------------------------------------- derivation


@dataclass(frozen=True)
class Derivation:
    max_concurrent: int
    source: str  # "derived" | "pin"
    basis: str  # one human line: how the number came about
    unreadable_policy: str


def gates_cpu_cores(values: Mapping[str, str], capacity: cap.Capacity) -> tuple[Decimal, str]:
    """Effective gates cores under the parent and tier CPUQuota values install.sh renders."""

    def configured_quota(key: str, fallback: Decimal) -> tuple[Decimal, str, bool]:
        raw = (values.get(key, "") or "").strip()
        if raw in ("", "auto"):
            return fallback, f"{key} auto ({fallback.normalize():f} cores from mdt_capacity)", False
        if raw == "-":
            return capacity.cpu_mdt, f"{key}=- (no additional CPUQuota; dev.slice share)", False
        match = re.fullmatch(r"([0-9]+)%", raw)
        if not match:  # the wizard validator refuses this shape first; never guess a number
            raise AdmissionConfigError(f"{key}={raw!r} is not N%, auto or -")
        pinned = min(Decimal(int(match.group(1))) / 100, capacity.cpu_mdt)
        return pinned, f"{key}={raw}", True

    dev_cores, dev_basis, dev_pinned = configured_quota("DEV_CPU_QUOTA", capacity.cpu_mdt)
    tier_cores, tier_basis, tier_pinned = configured_quota("DEV_GATES_CPU_QUOTA", capacity.tier_cpu)
    effective = min(dev_cores, tier_cores)
    if dev_pinned or tier_pinned:
        return effective, f"min({dev_basis}; {tier_basis}) = {effective.normalize():f} effective gates cores"
    if (values.get("DEV_GATES_CPU_QUOTA", "") or "").strip() == "-":
        return effective, f"no gates CPUQuota; bounded by dev.slice ({dev_basis})"
    return effective, f"tier CPU share ({tier_basis}), bounded by dev.slice ({dev_basis})"


def gates_memory_high(values: Mapping[str, str], *, mem_total: int, swap_total: int) -> tuple[int | None, str]:
    """MemoryHigh the gates tier is rendered with (``None`` = the tier has none)."""
    if values.get("HOST_ROLE", "") == "dedicated-dev":
        try:
            mem = cap.dedicated_memory(dict(values), mem_total=mem_total, swap_total=swap_total)
        except cap.CapacityError as exc:
            raise AdmissionConfigError(str(exc)) from exc
        return mem.gates_high, f"dedicated-dev gates MemoryHigh ({mem.gates_high_source})"
    raw = (values.get("DEV_GATES_MEMORY_HIGH", "") or "").strip()
    if raw in ("", "-"):
        return None, "no gates MemoryHigh"
    parsed = cap.parse_size_bytes(raw)
    if parsed is None:
        raise AdmissionConfigError(f"DEV_GATES_MEMORY_HIGH={raw!r} is not a size")
    return parsed or None, f"DEV_GATES_MEMORY_HIGH={raw}"


def derive(values: Mapping[str, str], *, nproc: int, mem_total: int, swap_total: int) -> Derivation:
    """The cap this host publishes: an ADMISSION_MAX_CONCURRENT pin, else the formula in the module docstring."""
    settings = parse_settings(values)
    if settings.pinned:
        return Derivation(settings.max_concurrent or 1, "pin",
                          f"ADMISSION_MAX_CONCURRENT={settings.max_concurrent} (pinned in host-setup.env)",
                          settings.unreadable_policy)
    try:
        capacity = cap.derive_from_config(dict(values), mem_total=mem_total, swap_total=swap_total, nproc=nproc)
    except cap.CapacityError as exc:
        raise AdmissionConfigError(str(exc)) from exc
    cores, cores_basis = gates_cpu_cores(values, capacity)
    cpu_cap = int(cores / settings.cpus_per_gate)
    terms = [cpu_cap]
    parts = [f"cpu {cores.normalize():f} cores ({cores_basis}) / {settings.cpus_per_gate.normalize():f} per gate = {cpu_cap}"]
    high, high_basis = gates_memory_high(values, mem_total=mem_total, swap_total=swap_total)
    if high is not None:
        memory_cap = high // settings.memory_per_gate
        terms.append(memory_cap)
        parts.append(f"memory {cap.format_size(high)} ({high_basis}) / {cap.format_size(settings.memory_per_gate)} per gate = {memory_cap}")
    else:
        parts.append(f"memory: no term ({high_basis})")
    result = max(1, min(terms))
    floored = " (raised to the minimum of 1)" if min(terms) < 1 else ""
    return Derivation(result, "derived", f"min({'; '.join(parts)}) = {result}{floored}", settings.unreadable_policy)


# ------------------------------------------------------------------------------------------- run-gate context


@dataclass(frozen=True)
class Proc:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def text(self) -> str:
        return (self.out + self.err).strip()


RunFn = Callable[..., Proc]
WhichFn = Callable[[str], "str | None"]


def default_run(argv: Sequence[str], *, input: str | None = None, env: Mapping[str, str] | None = None,
                cwd: str | None = None, timeout: float = 90.0) -> Proc:
    """Run ``argv`` (no shell). A missing program is rc 127 and a timeout rc 124, never an exception."""
    try:
        done = subprocess.run(list(argv), input=input, capture_output=True, text=True, cwd=cwd,
                              env=None if env is None else dict(env), timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return Proc(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return Proc(124, "", f"timed out after {timeout:g}s: {' '.join(argv[:3])}")
    return Proc(done.returncode, done.stdout, done.stderr)


def run_gate_config(image: str, policy: str) -> str:
    """The minimal run-gate.toml ``admission set|show`` needs: the table names the ticket image. Lanes are not needed."""
    return ("schema_version = 1\n\n[admission]\nenabled = true\n"
            f'ticket_image = "{image}"\nunreadable_policy = "{policy}"\n')


# Run inside the cockpit: write the config from stdin into a private temp dir, run the verb from there.
_COCKPIT_SCRIPT = ('d=$(mktemp -d) || exit 97; trap \'rm -rf "$d"\' EXIT; '
                   'cat > "$d/run-gate.toml" && cd "$d" && "$@"')


class Skip(Exception):
    """A prerequisite that a fresh host does not have yet: reported loudly, never a failure."""


class RunGate:
    """``run-gate admission ...`` in the configured context (host binary or cockpit container)."""

    def __init__(self, settings: Settings, image: str, run: RunFn, docker: str, rg: str, workdir: str | None) -> None:
        self.settings, self.image, self._run, self._docker, self._rg, self._workdir = settings, image, run, docker, rg, workdir
        self._config = run_gate_config(image, settings.unreadable_policy)

    def invoke(self, *args: str) -> Proc:
        if self.settings.cockpit:
            argv = [self._docker, "exec", "-i", "-u", self.settings.cockpit_user, "-e", f"{GATES_ENV}={GATES_SLICE}",
                    self.settings.cockpit, "sh", "-c", _COCKPIT_SCRIPT, "sh", self._rg, "admission", *args]
            return self._run(argv, input=self._config)
        # A run-gate config is read from the working directory; this one is private to the call.
        env = dict(os.environ)
        env[GATES_ENV] = GATES_SLICE
        (Path(self._workdir or ".") / "run-gate.toml").write_text(self._config, encoding="utf-8")
        return self._run([self._rg, "admission", *args], env=env, cwd=self._workdir)

    def show(self) -> "Published | None":
        done = self.invoke("show")
        if done.rc != 0:
            raise AdmissionFailure(f"`run-gate admission show` failed (rc {done.rc}): {done.text[:300]}")
        return parse_show(done.out + done.err)


class AdmissionFailure(RuntimeError):
    """A publish or read that was attempted and did not work (as opposed to a Skip)."""


class UnreadablePublished(AdmissionFailure):
    """run-gate reported an admission object whose concurrency limit is not parseable."""


@dataclass(frozen=True)
class Published:
    max_concurrent: int
    unreadable_policy: str
    generation: int | None = None


_SHOW_VALUE = re.compile(r"\bmax_concurrent=(?P<n>\S+)")


def parse_show(text: str) -> Published | None:
    """``run-gate admission show`` output -> the readable cap, or None for "no published object".

    Fail closed: output that states neither is an error (a changed format must not read as "absent" and
    cause a blind replace, nor as "present")."""
    match = _SHOW_VALUE.search(text)
    if match is not None and not re.fullmatch(r"[0-9]+", match.group("n")):
        raise UnreadablePublished(f"`run-gate admission show` reported an unparseable max_concurrent limit: {text[:200]!r}")
    if match is None and "no published object" in text:
        return None
    if match is None:
        raise AdmissionFailure("`run-gate admission show` printed neither a published cap nor 'no published object' "
                               f"(output format changed?): {text[:200]!r}")
    policy = re.search(r"unreadable_policy=(?P<p>[a-z]+)", text)
    generation = re.search(r"generation=(?P<g>[0-9]+)", text)
    return Published(int(match.group("n")), policy.group("p") if policy else "",
                     int(generation.group("g")) if generation else None)


@dataclass
class Prepared:
    gate: RunGate
    workdir: tempfile.TemporaryDirectory | None = None

    def close(self) -> None:
        if self.workdir is not None:
            self.workdir.cleanup()


def prepare(settings: Settings, *, run: RunFn, which: WhichFn) -> Prepared:
    """Resolve the run context or raise ``Skip`` naming the prerequisite this host does not have (yet)."""
    docker = which("docker")
    if docker is None:
        raise Skip("the docker CLI is not on PATH")
    rg = settings.run_gate or "run-gate"
    workdir = None
    if settings.cockpit:
        state = run([docker, "inspect", "-f", "{{.State.Running}}", settings.cockpit])
        if state.rc != 0 or state.out.strip() != "true":
            raise Skip(f"the cockpit container {settings.cockpit} is not running")
        probe = run([docker, "exec", "-u", settings.cockpit_user, settings.cockpit, "sh", "-c", 'command -v "$1"', "sh", rg])
        if probe.rc != 0:
            raise Skip(f"run-gate ({rg}) is not installed in the cockpit container {settings.cockpit} for user {settings.cockpit_user}")
        image = settings.ticket_image
        if not image:
            found = run([docker, "inspect", "-f", "{{.Config.Image}}", settings.cockpit])
            image = found.out.strip() if found.rc == 0 else ""
    else:
        resolved = which(rg)
        if resolved is None:
            raise Skip(f"run-gate ({rg}) is not installed on the host")
        rg, image = resolved, settings.ticket_image
    if not image:
        raise Skip("no ticket image: set ADMISSION_TICKET_IMAGE (a local image that provides /bin/true)")
    present = run([docker, "image", "inspect", image])
    if present.rc != 0:
        error = present.text
        lowered = error.lower()
        if any(marker in lowered for marker in (
            "cannot connect to the docker daemon", "error during connect:",
            "permission denied while trying to connect to the docker api",
        )):
            detail = f": {error[:240]}" if error else ""
            raise Skip(f"the Docker daemon is not reachable while checking the ticket image{detail}")
        raise Skip(f"the ticket image {image} is not present locally (run-gate never pulls it)")
    if not settings.cockpit:
        workdir = tempfile.TemporaryDirectory(prefix="mdt-admission-")
    return Prepared(RunGate(settings, image, run, docker, rg, workdir.name if workdir else None), workdir)


# ------------------------------------------------------------------------------------------- publish / check


def skip_advice(settings: Settings) -> str:
    return ("run `sudo ./host-setup/install.sh --admission-only` once run-gate and the ticket image exist "
            "(host-setup.env: ADMISSION_COCKPIT / ADMISSION_RUN_GATE / ADMISSION_TICKET_IMAGE)")


def pinned_mismatch_advice(settings: Settings) -> str:
    if settings.publish == "missing":
        return ("set ADMISSION_PUBLISH=replace in /etc/mdt/host-setup.env and run "
                "`sudo ./host-setup/install.sh` to reconcile the published cap")
    return "run `sudo ./host-setup/install.sh --admission-only` to reconcile the published cap"


def publish(settings: Settings, derivation: Derivation, *, run: RunFn, which: WhichFn,
            log: Callable[[str], None], operator_reconcile: bool = False) -> str:
    """Make the daemon publish ``derivation``; returns published | replaced | unchanged | kept | skipped.

    Idempotent: a readable cap equal in value and policy is left alone (no new generation); ``auto`` on a full
    install and ``missing`` preserve a differing readable cap, while ``replace`` and explicit operator
    reconciliation replace it. An absent cap is published. The explicit reconcile uses ``--replace`` even when
    ``show`` cannot parse the old limit, so the operator can repair that state. Raises AdmissionFailure when the
    attempt fails or the read-back differs."""
    if settings.publish == "off":
        log("admission: ADMISSION_PUBLISH=off; the gates cap is not managed by host-setup")
        return "skipped"
    try:
        prepared = prepare(settings, run=run, which=which)
    except Skip as why:
        log(f"admission: NOTE not published: {why}; {skip_advice(settings)}")
        return "skipped"
    try:
        gate = prepared.gate
        unreadable = False
        try:
            current = gate.show()
        except UnreadablePublished:
            # A malformed value is no readable cap. Non-replacing modes may try a plain set, which run-gate
            # will refuse if the unreadable object still exists; only explicit replace policy may repair it.
            current, unreadable = None, True
        want = Published(derivation.max_concurrent, derivation.unreadable_policy)
        if current is not None and (current.max_concurrent, current.unreadable_policy) == (want.max_concurrent, want.unreadable_policy):
            log(f"admission: gates cap already published: max_concurrent={want.max_concurrent} "
                f"unreadable_policy={want.unreadable_policy} (generation {current.generation}); nothing to do")
            return "unchanged"
        should_replace = settings.publish == "replace" or (settings.publish == "auto" and operator_reconcile)
        if current is not None and not should_replace:
            log(f"admission: a cap is published (max_concurrent={current.max_concurrent} unreadable_policy={current.unreadable_policy}); "
                f"ADMISSION_PUBLISH={settings.publish} keeps it (host policy would be {want.max_concurrent}: {derivation.basis})")
            return "kept"
        args = ["set", "--max-concurrent", str(want.max_concurrent), "--unreadable-policy", want.unreadable_policy]
        if should_replace:
            args.append("--replace")
        done = gate.invoke(*args)
        if done.rc != 0:
            raise AdmissionFailure(f"`run-gate admission {' '.join(args)}` failed (rc {done.rc}): {done.text[:400]}")
        after = gate.show()
        if after is None or (after.max_concurrent, after.unreadable_policy) != (want.max_concurrent, want.unreadable_policy):
            raise AdmissionFailure(f"published, but the read-back shows {after} instead of max_concurrent={want.max_concurrent} "
                                   f"unreadable_policy={want.unreadable_policy}")
        if current is None and not unreadable:
            log(f"admission: published gates cap max_concurrent={want.max_concurrent} unreadable_policy={want.unreadable_policy}: {derivation.basis}")
            return "published"
        old = (f"{current.max_concurrent}/{current.unreadable_policy}" if current is not None
               else "unparseable published cap")
        log(f"admission: replaced gates cap {old} -> {want.max_concurrent}/{want.unreadable_policy}: {derivation.basis}")
        return "replaced"
    finally:
        prepared.close()


def check(settings: Settings, derivation: Derivation, *, run: RunFn, which: WhichFn) -> list[str]:
    """Read-only verification as ``OK|WARN|FAIL|INFO <text>`` lines (mdt-host-check.sh parses them)."""
    if settings.publish == "off":
        return ["INFO ADMISSION_PUBLISH=off: host-setup does not manage the run-gate gates cap on this host"]
    try:
        prepared = prepare(settings, run=run, which=which)
    except Skip as why:
        return [f"WARN the published gates cap cannot be read: {why}; a project with [admission] enabled "
                f"is refused (no-headroom) until one is published; {skip_advice(settings)}"]
    try:
        try:
            current = prepared.gate.show()
        except AdmissionFailure as exc:
            return [f"FAIL {exc}"]
    finally:
        prepared.close()
    if current is None:
        return ["FAIL no gates cap is published on this Docker daemon: every lane of a project with [admission] "
                "enabled ends NOT_RUN no-headroom; run `sudo ./host-setup/install.sh --admission-only`"]
    expected = f"max_concurrent={derivation.max_concurrent} unreadable_policy={derivation.unreadable_policy}"
    published = f"max_concurrent={current.max_concurrent} unreadable_policy={current.unreadable_policy}"
    if (current.max_concurrent, current.unreadable_policy) == (derivation.max_concurrent, derivation.unreadable_policy):
        return [f"OK run-gate gates cap {published} matches the host policy ({derivation.basis})"]
    differs = f"published gates cap {published}, host policy says {expected} ({derivation.basis})"
    if settings.pinned:
        return [f"FAIL {differs}; {pinned_mismatch_advice(settings)}"]
    return [f"WARN {differs}; run `sudo ./host-setup/install.sh --admission-only` to reconcile it, "
            "or pin the value with ADMISSION_MAX_CONCURRENT"]


# ------------------------------------------------------------------------------------------- CLI


def _nproc() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def main(argv: Sequence[str] | None = None, *, run: RunFn = default_run, which: WhichFn = shutil.which) -> int:
    ap = argparse.ArgumentParser(description="run-gate gates admission cap: derive from host capacity, publish, check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("derive", "print the cap host policy yields as KEY=VALUE lines (no host access)"),
                       ("publish", "publish the derived cap through run-gate, idempotently"),
                       ("check", "read-only: print OK/WARN/FAIL/INFO lines for mdt-host-check.sh")):
        p = sub.add_parser(name, help=text)
        p.add_argument("env", type=Path)
        p.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
        p.add_argument("--nproc", type=int, default=None)
        if name == "publish":
            p.add_argument("--operator-reconcile", action="store_true",
                           help="with ADMISSION_PUBLISH=auto, replace a differing cap for an explicit operator action")
    args = ap.parse_args(argv)
    values = cap._parse_env(args.env)
    try:
        settings = parse_settings(values)
        total, swap = cap._meminfo_bytes(args.meminfo)
        derivation = derive(values, nproc=args.nproc or _nproc(), mem_total=total, swap_total=swap)
    except AdmissionConfigError as exc:
        if args.cmd == "check":
            print(f"FAIL the gates admission cap cannot be derived: {exc}")
            return 0
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "derive":
        print(f"ADMISSION_PUBLISH={settings.publish}")
        print(f"ADMISSION_MAX_CONCURRENT={derivation.max_concurrent}")
        print(f"ADMISSION_UNREADABLE_POLICY={derivation.unreadable_policy}")
        print(f"ADMISSION_SOURCE={derivation.source}")
        print(f"ADMISSION_BASIS={derivation.basis}")
        return 0
    if args.cmd == "check":
        for line in check(settings, derivation, run=run, which=which):
            print(line)
        return 0
    try:
        publish(settings, derivation, run=run, which=which, log=lambda line: print(line, flush=True),
                operator_reconcile=args.operator_reconcile)
    except AdmissionFailure as exc:
        print(f"ERROR: admission: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

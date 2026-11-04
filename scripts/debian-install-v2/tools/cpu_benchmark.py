#!/usr/bin/env python3
"""Per-host CPU benchmark: Geekbench (6 or 7, default 7), measured ONCE, persisted in host state (HB-1).

The download/extract/parse logic is lifted from scripts/debian-install/geekbench_runner.py (v1) and generalised over the
Geekbench major. Scores of different majors are never comparable; the record carries `tool_major` and every
normalisation baseline is per major (`BASELINE_SCORE`, `baseline_for`, `require_same_major`).

One shared stdlib-only tool for debian-install-v2 (step ``run_cpu_benchmark``) and
``mdt host-setup cpu-benchmark``. It is pinned by sha256 (``cpu_benchmark.py.sha256`` next to it),
the same pattern as ``iocost_coef_gen.py``; never copy it into a second implementation.

Files (state dir default ``/var/lib/mdt/host-facts``)::

    cpu-benchmark.json        0644  schema mdt-host-cpu-benchmark/1   (scores, CPU, conditions, result URL)
    cpu-benchmark.claim.json  0600  schema mdt-host-cpu-benchmark-claim/1 (the Geekbench claim link, root only)

Rules:
  * Measure once per host. An existing cpu-benchmark.json is refused without ``--force``.
  * Refuse to run under CPU pressure (``some avg10`` above ``--max-psi``, default 25).
  * The claim link is written ONLY to the 0600 file. It is never printed, logged, put in the readable
    file, or kept in an exception message (every output line is redacted).
  * ``--resolve`` re-reads the public result page of an existing record and never executes Geekbench.
  * Hermetic: every network, exec, /proc and clock access goes through :class:`Env`; tests inject a fake.

Exit codes: 0 ok, 2 refused (existing file, CPU pressure, bad arguments), 3 failed (download, sha256 mismatch,
timeout, benchmark error), 4 partial (record written without scores, ``--resolve`` can complete it later).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA = "mdt-host-cpu-benchmark/1"
CLAIM_SCHEMA = "mdt-host-cpu-benchmark-claim/1"
TOOL = "geekbench"
# Geekbench majors are NOT comparable (a GB6 score says nothing about a GB7 score). The major is recorded in every
# record (`tool_major`) and every normalisation baseline is per major. SUPPORTED_MAJORS: what this tool can run and parse;
# DEFAULT_MAJOR: the newest major found on the CDN (7.0.0 and 7.1.0 verified 2026-11-04, `geekbench7 --help` read).
SUPPORTED_MAJORS = (6, 7)
DEFAULT_MAJOR = 7
# Score of the Geekbench baseline (reference) machine per major (x_ref = x * single_core / baseline). Documentation only,
# not stored in the record: GB6 = 2500 on an Intel Core i7-12700; GB7 = 2500 on an AMD Ryzen 7 7700 (Lenovo Legion),
# confirmed by the controller 2026-11-05 from public sources (letsdatascience, hwbusters, xenospectrum, notebookcheck,
# signal65). Equal constants do not make the majors comparable: records of different majors are never mixed.
BASELINE_SCORE: dict[int, int | None] = {6: 2500, 7: 2500}
DEFAULT_STATE_DIR = "/var/lib/mdt/host-facts"
RESULT_NAME = "cpu-benchmark.json"
CLAIM_NAME = "cpu-benchmark.claim.json"
DEFAULT_MAX_PSI = 25.0
DEFAULT_TIMEOUT = 1800
CDN_BASE = "https://cdn.geekbench.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) mdt-cpu-benchmark"

EXIT_OK, EXIT_REFUSED, EXIT_FAILED, EXIT_PARTIAL = 0, 2, 3, 4

RESULT_URL_RE = re.compile(r"https://browser\.geekbench\.com/v(\d+)/cpu/(\d+)(?![\d/])")
CLAIM_URL_RE = re.compile(r"https://browser\.geekbench\.com/v\d+/cpu/\d+/claim\?key=[A-Za-z0-9]+")
CLAIM_REDACT_RE = re.compile(r"(https?://\S*?/claim\?key=)[^\s\"'<>]*")
VERSION_RE = re.compile(r"Geekbench\s+(\d+\.\d+\.\d+)")
PIN_VERSION_RE = re.compile(r"^(\d+)\.\d+\.\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCORE_MAX = 1_000_000


class BenchmarkError(Exception):
    """A refusal or failure with the exit code to use. The message is redacted before it is shown."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


def redact(text: str) -> str:
    """Remove the claim key from any text that might be shown or logged."""
    return CLAIM_REDACT_RE.sub(r"\1<redacted>", text)


# ---------------------------------------------------------------------------------------------
# Parsing (pure; ported from the v1 debian-install geekbench_runner and made stricter)
# ---------------------------------------------------------------------------------------------

def _score(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = int(raw.replace(",", ""))
    return value if 0 < value <= SCORE_MAX else None


def parse_stdout(text: str) -> dict[str, Any]:
    """Extract version, both scores, the result id/URL and the claim URL from Geekbench CLI output."""
    out: dict[str, Any] = {"version": None, "single_core": None, "multi_core": None,
                           "result_url": None, "result_id": None, "claim_url": None}
    m = VERSION_RE.search(text)
    if m:
        out["version"] = m.group(1)
    # GB6 prints "Single-Core Score  1801" in its summary. The GB7 summary line format could NOT be verified without
    # running the benchmark (the 7.1.0 `--help` and strings give no sample), so the same label is accepted with an
    # optional ":" or "=" and the result-page fallback covers any other layout.
    m = re.search(r"Single-Core\s+Score\s*[:=]?\s*([\d,]+)", text)
    if m:
        out["single_core"] = _score(m.group(1))
    m = re.search(r"Multi-Core\s+Score\s*[:=]?\s*([\d,]+)", text)
    if m:
        out["multi_core"] = _score(m.group(1))
    claim = CLAIM_URL_RE.search(text)
    if claim:
        out["claim_url"] = claim.group(0)
    # the claim URL starts with the result URL; search the text with claims blanked so the
    # plain result link is the one that is found
    m = RESULT_URL_RE.search(CLAIM_URL_RE.sub("", text))
    if m is None and claim:
        m = re.match(r"https://browser\.geekbench\.com/v(\d+)/cpu/(\d+)", claim.group(0))
    if m:
        out["result_url"] = m.group(0)
        out["result_major"] = int(m.group(1))
        out["result_id"] = m.group(2)
    return out


_SINGLE_PATTERNS = (
    r"class=['\"]score['\"][^>]*>\s*([\d,]+)\s*</div>\s*<div[^>]*>\s*Single-Core\s+Score",
    r"Single-Core\s+Score[^>]*>\s*<[^>]*>\s*([\d,]+)",
    r"Single-Core\s+Score.{0,200}?<(?:div|span|td)[^>]*>\s*([\d,]+)",
)
_MULTI_PATTERNS = (
    r"class=['\"]score['\"][^>]*>\s*([\d,]+)\s*</div>\s*<div[^>]*>\s*Multi-Core\s+Score",
    r"Multi-Core\s+Score[^>]*>\s*<[^>]*>\s*([\d,]+)",
    r"Multi-Core\s+Score.{0,200}?<(?:div|span|td)[^>]*>\s*([\d,]+)",
)


def _first(patterns: tuple[str, ...], html: str) -> int | None:
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            value = _score(m.group(1))
            if value is not None:
                return value
    return None


def parse_result_page(html: str) -> dict[str, int | None]:
    """Extract both scores from a Geekbench Browser result page (labelled first, ordered score divs last)."""
    single = _first(_SINGLE_PATTERNS, html)
    multi = _first(_MULTI_PATTERNS, html)
    if single is None or multi is None:
        divs = re.findall(r"<(?:div|span|td)[^>]*class=['\"][^'\"]*\bscore\b[^'\"]*['\"][^>]*>\s*([\d,]{3,9})", html,
                          re.IGNORECASE)
        if len(divs) >= 2:
            single = single if single is not None else _score(divs[0])
            multi = multi if multi is not None else _score(divs[1])
    return {"single_core": single, "multi_core": multi}


# ---------------------------------------------------------------------------------------------
# Environment seam
# ---------------------------------------------------------------------------------------------

class Env:
    """Every side effect of the tool. Tests replace this class; nothing else touches the host."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def machine(self) -> str:
        return os.uname().machine

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def cpu_count(self) -> int:
        return os.cpu_count() or 1

    def fetch_file(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:  # noqa: S310 fixed https hosts
            shutil.copyfileobj(resp, fh)

    def artifact_exists(self, url: str) -> bool:
        """1-byte ranged GET: a real artifact answers 200/206 (not text/html); a missing version answers 404."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 fixed https host
                return resp.status in (200, 206) and "text/html" not in resp.headers.get("Content-Type", "").lower()
        except OSError:  # HTTPError and URLError are OSErrors: a blip counts as missing (callers need 2 misses)
            return False

    def fetch_text(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 fixed https hosts
            return resp.read().decode("utf-8", errors="replace")

    def run(self, argv: list[str], timeout: float, cwd: str | None) -> tuple[int, str]:
        """Run argv in its own process group; kill the group on timeout (raises subprocess.TimeoutExpired)."""
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return proc.returncode, out or ""


# ---------------------------------------------------------------------------------------------
# Host readings
# ---------------------------------------------------------------------------------------------

def psi_some_avg10(env: Env, resource: str = "cpu") -> float | None:
    try:
        for line in env.read_text(f"/proc/pressure/{resource}").splitlines():
            if line.startswith("some "):
                m = re.search(r"avg10=([\d.]+)", line)
                return float(m.group(1)) if m else None
    except OSError:
        return None
    return None


def _cpu_stat(env: Env) -> tuple[int, int] | None:
    """(steal, total) jiffies from the aggregate cpu line of /proc/stat."""
    try:
        for line in env.read_text("/proc/stat").splitlines():
            if line.startswith("cpu "):
                f = [int(x) for x in line.split()[1:]]
                steal = f[7] if len(f) > 7 else 0
                return steal, sum(f[:8])
    except (OSError, ValueError):
        return None
    return None


def _loadavg1(env: Env) -> float | None:
    try:
        return float(env.read_text("/proc/loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def cpu_identity(env: Env) -> dict[str, Any]:
    model, hypervisor = None, False
    try:
        for line in env.read_text("/proc/cpuinfo").splitlines():
            key, _, val = line.partition(":")
            key = key.strip()
            if key == "model name" and model is None:
                model = " ".join(val.split())
            elif key == "flags" and "hypervisor" in val.split():
                hypervisor = True
    except OSError:
        pass
    return {"cpu_model": model, "cpus_online": env.cpu_count(), "hypervisor": hypervisor}


def _iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------

def _atomic_write(path: Path, data: str, mode: int) -> None:
    """Write data to path atomically with exactly `mode` (never wider, independent of umask)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_files(state_dir: Path, record: dict[str, Any], claim_url: str | None) -> None:
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if "claim" in text.lower():  # structural guard: the readable file never carries the claim link
        raise BenchmarkError("internal error: claim data in the readable record", EXIT_FAILED)
    if claim_url:
        claim = {"schema": CLAIM_SCHEMA, "result_url": record["result_url"], "claim_url": claim_url,
                 "measured_at": record["measured_at"]}
        _atomic_write(state_dir / CLAIM_NAME, json.dumps(claim, indent=2, sort_keys=True) + "\n", 0o600)
    _atomic_write(state_dir / RESULT_NAME, text, 0o644)


# ---------------------------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------------------------

def artifact_url(version: str, machine: str) -> str:
    suffix = "LinuxARMPreview" if machine in ("aarch64", "arm64") else "Linux"
    return f"{CDN_BASE}/Geekbench-{version}-{suffix}.tar.gz"


def discover_version(env: Env, major: int) -> str:
    """Newest <major>.x.y on the CDN by probing (lifted from the v1 geekbench_runner; geekbench.com itself is behind
    Cloudflare and 403s scripts). Pass 1: highest minor that publishes an x.0; pass 2: highest patch within it."""
    machine = env.machine()
    best_minor, misses, minor = None, 0, 0
    while misses < 2 and minor < 40:
        if env.artifact_exists(artifact_url(f"{major}.{minor}.0", machine)):
            best_minor, misses = minor, 0
        else:
            misses += 1
        minor += 1
    if best_minor is None:
        raise BenchmarkError(f"no Geekbench {major}.x.y found on {CDN_BASE}; pass --geekbench-version explicitly")
    best_patch, misses, patch = 0, 0, 1
    while misses < 2 and patch < 40:
        if env.artifact_exists(artifact_url(f"{major}.{best_minor}.{patch}", machine)):
            best_patch, misses = patch, 0
        else:
            misses += 1
        patch += 1
    return f"{major}.{best_minor}.{best_patch}"


def baseline_for(record: dict[str, Any]) -> int:
    """Normalisation baseline of a record's Geekbench major. Refuses a major without a verified constant."""
    major = record.get("tool_major")
    base = BASELINE_SCORE.get(major) if isinstance(major, int) else None
    if base is None:
        raise BenchmarkError(f"no verified normalisation baseline for Geekbench major {major!r}", EXIT_REFUSED)
    return base


def require_same_major(*records: dict[str, Any]) -> int:
    """Scores of different Geekbench majors are not comparable: refuse to mix them."""
    majors = {r.get("tool_major") for r in records}
    if len(majors) != 1 or None in majors:
        raise BenchmarkError(f"refusing to mix Geekbench majors {sorted(map(str, majors))}", EXIT_REFUSED)
    return majors.pop()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if root not in target.parents and target != root:
            raise BenchmarkError(f"unsafe path in archive: {member.name}")
        if member.issym() or member.islnk():
            link = (target.parent / member.linkname).resolve()
            if root not in link.parents and link != root:
                raise BenchmarkError(f"unsafe link in archive: {member.name}")
    tar.extractall(dest)  # noqa: S202 members validated above


def fetch_binary(env: Env, version: str, sha256: str | None, major: int, workdir: Path) -> tuple[Path, str]:
    """Download the tarball, verify its sha256 BEFORE extracting when a pin is given, return (binary, tarball sha256)."""
    url = artifact_url(version, env.machine())
    tarball = workdir / "geekbench.tar.gz"
    env.fetch_file(url, tarball)
    actual = _sha256_file(tarball)
    if sha256 is not None and actual != sha256:
        raise BenchmarkError(f"sha256 mismatch for the Geekbench archive (expected {sha256}, got {actual}); refusing to run")
    out = workdir / "gb"
    out.mkdir()
    with tarfile.open(tarball) as tar:
        _safe_extract(tar, out)
    for path in sorted(out.rglob(f"geekbench{major}")):
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o111)
            return path, actual
    raise BenchmarkError(f"geekbench{major} executable not found in the archive")


# ---------------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------------

def measure(env: Env, state_dir: Path, *, version: str | None, sha256: str | None, binary: Path | None, force: bool,
            max_psi: float, timeout: float, produced_by: str | None, major: int | None = None
            ) -> tuple[dict[str, Any], int]:
    result_path = state_dir / RESULT_NAME
    if result_path.exists() and not force:
        raise BenchmarkError(f"{result_path} exists; the CPU benchmark is measured once per host (use --force to re-measure)",
                             EXIT_REFUSED)
    if version:
        m = PIN_VERSION_RE.match(version)
        if not m:
            raise BenchmarkError("--geekbench-version must look like 7.1.0", EXIT_REFUSED)
        if major is not None and major != int(m.group(1)):
            raise BenchmarkError(f"--geekbench-version {version} is not major {major}", EXIT_REFUSED)
        major = int(m.group(1))
    major = DEFAULT_MAJOR if major is None else major
    if major not in SUPPORTED_MAJORS:
        raise BenchmarkError(f"Geekbench major {major} is not supported (supported: {list(SUPPORTED_MAJORS)})", EXIT_REFUSED)
    if sha256 is not None and not SHA256_RE.match(sha256):
        raise BenchmarkError("--sha256 must be 64 lowercase hex characters", EXIT_REFUSED)
    verified = binary is None and sha256 is not None
    psi_start = psi_some_avg10(env)
    if psi_start is not None and psi_start > max_psi:
        raise BenchmarkError(f"CPU pressure some avg10={psi_start} is above {max_psi}; refusing to measure on a busy host",
                             EXIT_REFUSED)
    load_start = _loadavg1(env)
    identity = cpu_identity(env)
    workdir = Path(tempfile.mkdtemp(prefix="mdt-cpu-benchmark-"))
    try:
        if binary is None:
            if not version:  # no pin given: newest of the major on the CDN (the installer passes a pin + sha256)
                version = discover_version(env, major)
            exe, tool_sha = fetch_binary(env, version, sha256, major, workdir)
        else:
            exe, tool_sha = binary, _sha256_file(binary)
        stat0, t0 = _cpu_stat(env), env.monotonic()
        try:
            # no flags: the documented default of Geekbench 6 and 7 free is the CPU benchmark (7.1.0 --help)
            rc, output = env.run([str(exe)], timeout, str(exe.parent))
        except subprocess.TimeoutExpired:
            raise BenchmarkError(f"Geekbench exceeded the {timeout:g}s timeout and was killed") from None
        duration = env.monotonic() - t0
        stat1 = _cpu_stat(env)
        psi_end = psi_some_avg10(env)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # the mkdtemp dir created above

    parsed = parse_stdout(output)
    if rc != 0 and not (parsed["single_core"] and parsed["multi_core"]):
        raise BenchmarkError(f"Geekbench exited with code {rc}")
    out_major = (parsed["version"] or "").split(".")[0]
    if out_major and out_major != str(major):
        raise BenchmarkError(f"Geekbench reports major {out_major}, expected {major}; majors are not comparable")
    if parsed.get("result_major") not in (None, major):
        raise BenchmarkError(f"result URL is for Geekbench major {parsed['result_major']}, expected {major}")

    steal_fraction = None
    if stat0 and stat1 and stat1[1] > stat0[1]:
        steal_fraction = round((stat1[0] - stat0[0]) / (stat1[1] - stat0[1]), 4)
    psis = [p for p in (psi_start, psi_end) if p is not None]
    reason = None
    scores = {"single_core": parsed["single_core"], "multi_core": parsed["multi_core"]}
    source: str | None = "stdout" if all(scores.values()) else None
    if not all(scores.values()) and parsed["result_url"]:
        try:
            page = parse_result_page(env.fetch_text(parsed["result_url"]))
        except Exception as exc:  # network is advisory: the record is still written
            reason = f"RESULT_PAGE_UNAVAILABLE: {type(exc).__name__}"
        else:
            for key in scores:
                if scores[key] is None:
                    scores[key] = page[key]
            if all(scores.values()):
                source = "result-page"
    if not all(scores.values()) and reason is None:
        reason = "SCORES_NOT_FOUND" if parsed["result_url"] else "NO_SCORES_AND_NO_RESULT_URL"
    if not parsed["result_url"] and not all(scores.values()):
        raise BenchmarkError("Geekbench produced neither scores nor a result URL")

    record = {
        "schema": SCHEMA, "tool": TOOL, "tool_version": parsed["version"] or version, "tool_major": major,
        "baseline_score": BASELINE_SCORE.get(major), "tool_sha256": tool_sha, "archive_sha256_verified": verified,
        "measured_at": _iso(env.now()), "cpu_model": identity["cpu_model"], "cpus_online": identity["cpus_online"],
        "hypervisor": identity["hypervisor"], "single_core": scores["single_core"], "multi_core": scores["multi_core"],
        "score_source": source, "result_url": parsed["result_url"], "result_id": parsed["result_id"],
        "conditions": {"loadavg1_start": load_start, "psi_cpu_some_avg10_max": max(psis) if psis else None,
                       "steal_fraction": steal_fraction, "duration_seconds": round(duration)},
        "reason": reason, "produced_by": produced_by,
    }
    write_files(state_dir, record, parsed["claim_url"])
    return record, (EXIT_OK if all(scores.values()) else EXIT_PARTIAL)


def resolve(env: Env, state_dir: Path) -> tuple[dict[str, Any], int]:
    """Re-read the result page of an existing record. Never executes Geekbench."""
    path = state_dir / RESULT_NAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkError(f"cannot read {path}: {type(exc).__name__}", EXIT_REFUSED) from None
    url = record.get("result_url")
    um = RESULT_URL_RE.fullmatch(url) if isinstance(url, str) else None
    if um is None:
        raise BenchmarkError("the record has no result_url to resolve", EXIT_REFUSED)
    if record.get("tool_major") not in (None, int(um.group(1))):
        raise BenchmarkError("result_url major differs from the record's tool_major; refusing to mix majors", EXIT_REFUSED)
    try:
        page = parse_result_page(env.fetch_text(url))
    except Exception as exc:
        raise BenchmarkError(f"result page unavailable: {type(exc).__name__}") from None
    for key in ("single_core", "multi_core"):
        if record.get(key) is None and page[key] is not None:
            record[key] = page[key]
    if record.get("single_core") and record.get("multi_core"):
        record["score_source"] = record.get("score_source") or "result-page"
        record["reason"] = None
        record["resolved_at"] = _iso(env.now())
        write_files(state_dir, record, None)
        return record, EXIT_OK
    record["reason"] = "SCORES_NOT_FOUND"
    write_files(state_dir, record, None)
    return record, EXIT_PARTIAL


def summary(record: dict[str, Any]) -> str:
    return (f"cpu-benchmark: single_core={record.get('single_core')} multi_core={record.get('multi_core')} "
            f"cpu={record.get('cpu_model')} vcpus={record.get('cpus_online')} geekbench={record.get('tool_version')} "
            f"result={record.get('result_url')}" + (f" reason={record['reason']}" if record.get("reason") else ""))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Measure the host CPU once with Geekbench and persist it in host state.")
    p.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    p.add_argument("--resolve", action="store_true", help="re-read the result page of the existing record; never runs Geekbench")
    p.add_argument("--force", action="store_true", help="re-measure although a record exists")
    p.add_argument("--major", type=int, help=f"Geekbench major {list(SUPPORTED_MAJORS)} (default {DEFAULT_MAJOR}, the newest)")
    p.add_argument("--geekbench-version", help="exact Geekbench x.y.z (default: newest of the major found on the CDN)")
    p.add_argument("--sha256", help="sha256 of the Geekbench archive for this architecture (verified; the installer pins it)")
    p.add_argument("--binary", help="use an already unpacked geekbench6 binary instead of downloading")
    p.add_argument("--max-psi", type=float, default=DEFAULT_MAX_PSI, help="refuse when CPU some avg10 exceeds this (default 25)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--produced-by", help="free text recorded as produced_by, e.g. 'mdt host-setup 1.2.3'")
    return p


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = env or Env()
    state_dir = Path(args.state_dir)
    try:
        if args.resolve:
            record, code = resolve(env, state_dir)
        else:
            record, code = measure(env, state_dir, version=args.geekbench_version, sha256=args.sha256, major=args.major,
                                   binary=Path(args.binary) if args.binary else None, force=args.force,
                                   max_psi=args.max_psi, timeout=args.timeout, produced_by=args.produced_by)
    except BenchmarkError as exc:
        print(redact(f"cpu-benchmark: {exc}"), file=sys.stderr)
        return exc.code
    except Exception as exc:  # last resort: never leak a traceback that could embed output
        print(redact(f"cpu-benchmark: unexpected {type(exc).__name__}"), file=sys.stderr)
        return EXIT_FAILED
    print(redact(summary(record)))
    return code


if __name__ == "__main__":
    sys.exit(main())

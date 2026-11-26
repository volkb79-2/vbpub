#!/usr/bin/env python3
"""Run a real activated probe and two real hostobs clients in one PID namespace.

This sends `SIGTERM`, so it runs only in the `tools/dev` container or a lane
container. The Rust gate/dev image marker is checked before any process starts.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

CONTAINER_MARKER = Path("/usr/local/share/hostobs-Cargo.lock.registry-sha256")
QUALITY_CODES = {
    "ok",
    "absent",
    "unreadable",
    "stale",
    "reset",
    "not-sampled",
    "unsupported",
    "unknown-topology",
}
WATCH_OBSERVE = "=== watch observe ==="
WATCH_OPERATOR = "=== watch operator ==="
WATCH_TIMEOUT_FLOOR_S = 20
PROBE_STOP_S = 10


class Arguments:
    def __init__(self, probe: Path, watch: Path, ticks: int, cgroup_root: Path, out: Path | None):
        self.probe = probe
        self.watch = watch
        self.ticks = ticks
        self.cgroup_root = cgroup_root
        self.out = out


def usage_error(message: str) -> int:
    print(f"live-demo: {message}", file=sys.stderr)
    return 2


def parse_args(argv: list[str]) -> Arguments | int:
    values: dict[str, str] = {}
    known = {"--probe", "--watch", "--ticks", "--cgroup-root", "--out"}
    index = 0
    while index < len(argv):
        option = argv[index]
        if option not in known:
            return usage_error(f"unknown argument {option!r}")
        if option in values:
            return usage_error(f"{option} may be given once")
        index += 1
        if index >= len(argv):
            return usage_error(f"{option} requires a value")
        values[option] = argv[index]
        index += 1
    for required in ("--probe", "--watch", "--ticks"):
        if required not in values:
            return usage_error(f"{required} is required")
    try:
        ticks = int(values["--ticks"])
    except ValueError:
        return usage_error(f"invalid tick count {values['--ticks']!r}")
    if ticks <= 0:
        return usage_error("--ticks must be greater than zero")
    probe = Path(values["--probe"]).resolve()
    watch = Path(values["--watch"]).resolve()
    for label, path in (("probe", probe), ("watch", watch)):
        if not path.is_file() or not os.access(path, os.X_OK):
            return usage_error(f"--{label} must name an executable file: {path}")
    cgroup_root = Path(values.get("--cgroup-root", "/sys/fs/cgroup"))
    if not cgroup_root.is_absolute() or not cgroup_root.is_dir():
        return usage_error(f"--cgroup-root must name an existing absolute directory: {cgroup_root}")
    out = Path(values["--out"]) if "--out" in values else None
    return Arguments(probe, watch, ticks, cgroup_root, out)


def field(line: str, name: str) -> str | None:
    for item in line.split("\t")[1:]:
        key, separator, value = item.partition("=")
        if separator and key == name:
            return value
    return None


def section(text: str, marker: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = lines.index(marker) + 1
    except ValueError:
        return []
    out = []
    for line in lines[start:]:
        if line.startswith("=== ") and line.endswith(" ==="):
            break
        out.append(line)
    return out


def exit_status(lines: list[str], prefix: str) -> int | None:
    for line in lines:
        if line.startswith(prefix):
            try:
                return int(line[len(prefix) :].strip())
            except ValueError:
                return None
    return None


def numeric_value(value: str) -> float | None:
    found = re.search(r"\(([-+0-9.eE]+)\)", value)
    if found is None:
        return None
    try:
        return float(found.group(1))
    except ValueError:
        return None


def tick_rows(lines: list[str]) -> tuple[list[tuple[int, str | None, str | None, str | None, str | None]], str | None]:
    rows = []
    for line in lines:
        if not line.startswith("tick\t"):
            continue
        raw_seq = field(line, "seq")
        if raw_seq is None:
            return rows, f"invalid tick sequence {raw_seq!r}"
        try:
            seq = int(raw_seq)
        except ValueError:
            return rows, f"invalid tick sequence {raw_seq!r}"
        rows.append(
            (seq, field(line, "entity"), field(line, "metric"), field(line, "value"), field(line, "quality"))
        )
    return rows, None


def close_line(lines: list[str]) -> tuple[int, str] | str:
    """The client's `closed` line: `closed<TAB>blocks=<n><TAB>reason=<reason>`, nothing else."""
    found = [line for line in lines if line.startswith("closed\t") or line == "closed"]
    if len(found) != 1:
        return f"expected one closed line, found {len(found)}"
    parts = found[0].split("\t")
    if len(parts) != 3 or parts[0] != "closed":
        return f"closed line is not the client's contract form: {found[0]!r}"
    blocks_text, _, blocks_value = parts[1].partition("=")
    reason_text, _, reason = parts[2].partition("=")
    if blocks_text != "blocks" or reason_text != "reason":
        return f"closed line is not the client's contract form: {found[0]!r}"
    if re.fullmatch(r"[0-9]+", blocks_value) is None:
        return f"closed line has no block count: {found[0]!r}"
    try:
        blocks = int(blocks_value)
    except ValueError:
        return f"closed line has no block count: {found[0]!r}"
    return blocks, reason


# The live reasons `end_reason_ok` accepts (crates/client/src/client.rs:190-196).
LIVE_CLOSE_REASONS = {"unsubscribed", "client-eof"}


def tick_sequence(
    rows: list[tuple[int, str | None, str | None, str | None, str | None]],
) -> list[tuple[int, int]]:
    """(seq, row count) in first-appearance order, one entry per tick block."""
    order: list[int] = []
    counts: dict[int, int] = {}
    for seq, _, _, _, _ in rows:
        if seq not in counts:
            order.append(seq)
            counts[seq] = 0
        counts[seq] += 1
    return [(seq, counts[seq]) for seq in order]


def row_rules(rows) -> str | None:
    """The quality rules: present values, known codes, and no number under a non-`ok` code."""
    for _, _, metric, value, quality in rows:
        if metric is not None and (value is None or quality is None):
            return "row is missing its value or quality"
    qualities = [quality for _, _, _, _, quality in rows if quality is not None]
    unknown = next((quality for quality in qualities if quality not in QUALITY_CODES), None)
    if unknown is not None:
        return f"unknown quality code {unknown!r}"
    for _, _, _, value, quality in rows:
        if quality is not None and quality != "ok" and value != "<not-measured>":
            return f"{quality} quality shows a value"
    return None


def full_row_form(rows, label: str) -> str | None:
    """Every tick line must be a complete client row.

    `watch` prints a full row (seq, entity, metric, value, quality) for a non-empty block and the
    compact `tick<TAB>seq=N<TAB>rows=0<TAB>columns=0` form for an empty one. The compact form
    carries no value and no quality, so it is not a measurement and must not satisfy the
    stream: a level that resolved nothing would otherwise pass unremarked.
    """
    for _, entity, metric, value, quality in rows:
        if not entity or not metric or not value or not quality:
            return f"{label} tick line is not a client row"
    return None


def assess(transcript: str, expected_ticks: int) -> tuple[bool, str]:
    observe = section(transcript, WATCH_OBSERVE)
    operator = section(transcript, WATCH_OPERATOR)
    if exit_status(observe, "watch exit:") != 0:
        return False, "watch observe did not exit 0"
    if exit_status(operator, "watch exit:") != 0:
        return False, "watch operator did not exit 0"

    observe_rows, error = tick_rows(observe)
    if error is not None:
        return False, error
    operator_rows, error = tick_rows(operator)
    if error is not None:
        return False, error

    for rows, label, expected_blocks in (
        (observe_rows, "observe", expected_ticks),
        (operator_rows, "operator", 1),
    ):
        sequence = tick_sequence(rows)
        previous = None
        for seq, _, _, _, _ in rows:
            if seq != previous:
                if previous is not None and seq <= previous:
                    return False, f"{label} tick sequence is not increasing: {previous} then {seq}"
                previous = seq
        if len(sequence) != expected_blocks:
            if label == "observe":
                return False, f"expected {expected_blocks} ticks, got {len(sequence)}"
            return False, f"expected {expected_blocks} operator tick block, got {len(sequence)}"

        for (earlier, _), (later, _) in zip(sequence, sequence[1:]):
            if later != earlier + 1:
                return False, (
                    f"{label} tick sequence numbers are not consecutive: {earlier} then {later}"
                )

        seen_pairs = set()
        for seq, entity, metric, _, _ in rows:
            if entity is None or metric is None:
                continue
            pair = (seq, entity, metric)
            if pair in seen_pairs:
                return False, (
                    f"{label} tick seq={seq} repeats entity/metric pair "
                    f"({entity!r}, {metric!r})"
                )
            seen_pairs.add(pair)

        if label == "observe":
            first = sequence[0][1] if sequence else 0
            for seq, count in sequence:
                if count != first:
                    return False, (
                        f"tick seq={seq} has {count} rows where the first tick has {first}; "
                        "a cgroup created or removed during the run changes the row count, "
                        "so repeat the run on a quiet target"
                    )

    for lines, label, blocks_expected in (
        (observe, "observe", expected_ticks),
        (operator, "operator", 1),
    ):
        closed = close_line(lines)
        if isinstance(closed, str):
            return False, f"{label}: {closed}"
        found_blocks, reason = closed
        if found_blocks < blocks_expected:
            return False, (
                f"{label} closed line says blocks={found_blocks}, fewer than the client was "
                f"asked for {blocks_expected}"
            )
        if reason not in LIVE_CLOSE_REASONS:
            return False, (
                f"{label} closed reason {reason!r} is not a reason the client accepts"
            )

    all_rows = observe_rows + operator_rows
    violation = row_rules(all_rows)
    if violation is not None:
        return False, violation
    violation = full_row_form(observe_rows, "observe")
    if violation is not None:
        return False, violation
    # The operator stream is one tick of the same contract (N3), with at least one full row.
    violation = full_row_form(operator_rows, "operator")
    if violation is not None:
        return False, violation

    cpu: dict[str, list[tuple[int, float]]] = {}
    for seq, entity, metric, value, quality in observe_rows:
        if metric == "cpu.usage_usec" and quality == "ok" and entity is not None and value is not None:
            parsed = numeric_value(value)
            if parsed is not None:
                cpu.setdefault(entity, []).append((seq, parsed))
    if not any(max(values)[1] > min(values)[1] for values in cpu.values()):
        return False, "no cpu change"

    probe_lines = section(transcript, "=== probe exit ===")
    probe_text = "\n".join(probe_lines)
    match = re.search(r"probe exit:\s*(-?\d+)", probe_text)
    status = int(match.group(1)) if match else None
    if status != -signal.SIGTERM or "SIGTERM" not in probe_text:
        return False, f"probe did not exit by SIGTERM (status {status})"
    if "panicked at" in "\n".join(section(transcript, "=== probe stderr ===")):
        return False, "probe panicked"
    return True, "PASS"


def listener(directory: Path, name: str) -> tuple[socket.socket, Path]:
    path = directory / f"{name}.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(8)
    return sock, path


def spawn_probe(
    probe: Path,
    config: Path,
    observe: socket.socket,
    operator: socket.socket,
    stderr_path: Path,
):
    # observe is created before operator, so the fd duplicated to 3 is never operator's.
    command = 'exec 3<&"$2" && exec 4<&"$3" && LISTEN_PID=$$ exec "$0" --config "$1"'
    env = os.environ.copy()
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        env.pop(name, None)
    env["LISTEN_FDS"] = "2"
    env["LISTEN_FDNAMES"] = "observe:operator"
    with stderr_path.open("w", encoding="utf-8") as stderr:
        child = subprocess.Popen(
            [
                "sh",
                "-c",
                command,
                str(probe),
                str(config),
                str(observe.fileno()),
                str(operator.fileno()),
            ],
            pass_fds=(observe.fileno(), operator.fileno()),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            text=True,
            env=env,
        )
    return child


def run_watch(watch: Path, path: Path, level: str, ticks: int, timeout_s: int) -> tuple[str, str, int]:
    command = [
        str(watch),
        "--socket",
        str(path),
        "--level",
        level,
        "--ticks",
        str(ticks),
    ]
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            stdout, stderr = child.communicate(timeout=PROBE_STOP_S)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate()
        stderr += f"watch timed out after {timeout_s}s\n"
    return stdout, stderr, child.returncode


def stop_probe(child: subprocess.Popen[str]) -> int | None:
    if child.poll() is None:
        child.send_signal(signal.SIGTERM)
    try:
        child.wait(timeout=PROBE_STOP_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    return child.returncode


def append_watch(lines: list[str], marker: str, output: tuple[str, str, int]) -> None:
    stdout, stderr, status = output
    lines.append(marker)
    lines.extend(stdout.rstrip("\n").splitlines())
    if stderr:
        lines.append("watch stderr:")
        lines.extend(stderr.rstrip("\n").splitlines())
    lines.append(f"watch exit: {status}")


def main(argv: list[str]) -> int:
    parsed = parse_args(argv)
    if isinstance(parsed, int):
        return parsed
    if not CONTAINER_MARKER.is_file():
        return usage_error(f"refusing outside a dev/lane container: marker {CONTAINER_MARKER} is absent")

    transcript: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hostobs-live-") as temporary:
        directory = Path(temporary)
        config = directory / "hostobsd.toml"
        config.write_text(
            '[probe]\nlevels = ["observe", "operator"]\ntick_ms = 1000\n'
            f"cgroup_root = {json.dumps(str(parsed.cgroup_root))}\n\n[limits]\nmax_rows = 1024\n",
            encoding="utf-8",
        )
        observe, observe_path = listener(directory, "observe")
        operator, operator_path = listener(directory, "operator")
        probe_stderr = directory / "probe.stderr"
        child = None
        try:
            child = spawn_probe(parsed.probe, config, observe, operator, probe_stderr)
            transcript.extend(
                [
                    "=== probe argv ===",
                    f"{parsed.probe} --config {config}",
                    "=== probe env (LISTEN_*) ===",
                    "LISTEN_FDS=2",
                    "LISTEN_FDNAMES=observe:operator",
                    f"LISTEN_PID={child.pid}",
                    "=== /proc/self/cgroup ===",
                ]
            )
            try:
                transcript.extend(Path("/proc/self/cgroup").read_text(encoding="utf-8").rstrip("\n").splitlines())
            except OSError as error:
                transcript.append(f"<cannot read /proc/self/cgroup: {error}>")
            transcript.extend(["=== config ===", config.read_text(encoding="utf-8").rstrip("\n")])
            timeout_s = max(WATCH_TIMEOUT_FLOOR_S, parsed.ticks * 2 + 10)
            append_watch(
                transcript,
                WATCH_OBSERVE,
                run_watch(parsed.watch, observe_path, "observe", parsed.ticks, timeout_s),
            )
            append_watch(
                transcript,
                WATCH_OPERATOR,
                run_watch(parsed.watch, operator_path, "operator", 1, timeout_s),
            )
        except OSError as error:
            print(f"live-demo: cannot start child: {error}", file=sys.stderr)
            return 1
        finally:
            observe.close()
            operator.close()
            if child is not None:
                status = stop_probe(child)
                try:
                    stderr = probe_stderr.read_text(encoding="utf-8").rstrip("\n")
                except OSError as error:
                    stderr = f"<cannot read probe stderr: {error}>"
                transcript.extend(["=== probe stderr ===", stderr])
                label = "SIGTERM" if status == -signal.SIGTERM else "exit"
                transcript.extend(["=== probe exit ===", f"probe exit: {status} ({label})"])

    passed, reason = assess("\n".join(transcript) + "\n", parsed.ticks)
    transcript.extend(["=== verdict ===", "PASS" if passed else f"FAIL {reason}"])
    rendered = "\n".join(transcript) + "\n"
    print(rendered, end="")
    if parsed.out is not None:
        try:
            parsed.out.write_text(rendered, encoding="utf-8")
        except OSError as error:
            print(f"live-demo: cannot write {parsed.out}: {error}", file=sys.stderr)
            return 1
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

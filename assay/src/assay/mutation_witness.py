"""Bounded pytest failure-witness capture for provenance-safe mutation reuse."""

from __future__ import annotations

import configparser
import json
import os
import re
import select
import shlex
import threading
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import safeio
from .errors import AssayError
from .r2_command import UnrecognizedCoverageOption, transform_argv
from .records import record

if TYPE_CHECKING:  # pragma: no cover -- annotation-only import; importing at runtime creates a cycle
    from .runner import CommandPlan

MAX_NODE_ID_UTF8_BYTES = 4096
MAX_INTERNAL_RECEIPT_BYTES = 16 * 1024
MAX_INTERNAL_MANIFEST_BYTES = 64 * 1024 * 1024
WITNESS_PLUGIN_MODULE = "assay_mutation_witness_plugin"
WITNESS_FILE_ENV = "ASSAY_MUTATION_WITNESS_FILE"
WITNESS_FD_ENV = "ASSAY_MUTATION_WITNESS_FD"
WITNESS_TARGET_ENV = "ASSAY_MUTATION_WITNESS_TARGET"
WITNESS_PLUGIN_PATH_ENV = "ASSAY_MUTATION_WITNESS_PLUGIN_PATH"
WITNESS_LIVENESS_PLUGIN_PATH_ENV = "ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH"
WITNESS_COLD_ENV = "ASSAY_MUTATION_WITNESS_COLD"
WITNESS_MANIFEST_FILE_ENV = "ASSAY_MUTATION_WITNESS_MANIFEST_FILE"
B105_ARCHIVE_ENV = (
    "ASSAY_B105_COVERAGE_SOURCE",
    "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
    "ASSAY_B105_SOURCE_COMMIT",
    "ASSAY_B105_SOURCE_TREE",
)
# pytest 9.1.1 plus pytest-xdist 3.8.0 short options used by the conservative
# cold-command parser. Unknown letters refuse closed instead of being guessed.
COLD_PYTEST_FLAG_OPTIONS = frozenset("qvslxdVh")
COLD_PYTEST_VALUE_OPTIONS = frozenset("Wckmnopr")
HOOK_FINGERPRINT_HOOKS = (
    "pytest_runtestloop",
    "pytest_runtest_protocol",
    "pytest_runtest_logstart",
    "pytest_runtest_logreport",
    "pytest_runtest_call",
    "pytest_runtest_makereport",
    "pytest_runtest_setup",
    "pytest_runtest_teardown",
    "pytest_collectreport",
    "pytest_collection_modifyitems",
    "pytest_sessionfinish",
)


@record
class ReceiptFacts:
    collection_count: int
    collection_sha256: str
    duplicates: int
    hook_fingerprint_sha256: str
    hook_count: int
    runtime_fingerprint_sha256: str | None
    config_sha256: str | None
    started_count: int
    collection_error: bool


class ReceiptCapture:
    """Capture one bounded, framed receipt from a child process.

    The child gets only the pipe's write descriptor. The parent drains it in a
    thread while the command runs, so a child cannot block on a full pipe. The
    retained bytes are capped, and final decoding accepts exactly one complete
    frame with no trailing data. This keeps post-session pytest hooks from
    replacing a receipt after ``pytest_sessionfinish`` has emitted it.
    """

    _HEADER_BYTES = 4
    _READ_CHUNK_BYTES = 64 * 1024
    _POLL_INTERVAL_SECONDS = 0.05
    _JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(self) -> None:
        self._read_fd, self.write_fd = os.pipe()
        # The reader thread owns a distinct descriptor. If it does not stop
        # before finish()'s join timeout, the parent may close and reuse its
        # descriptor without letting that stale thread read another capture.
        self._drain_fd = os.dup(self._read_fd)
        os.set_blocking(self._read_fd, False)
        os.set_blocking(self._drain_fd, False)
        self._stop = threading.Event()
        self._data = bytearray()
        self._overflow = False
        self._read_failed = False
        self._finished = False
        self._thread = threading.Thread(
            target=self._drain,
            name="assay-witness-receipt",
            daemon=True,
        )
        self._thread.start()

    def _drain(self) -> None:
        try:
            poller = select.poll()
            poller.register(self._drain_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
            maximum = MAX_INTERNAL_RECEIPT_BYTES + self._HEADER_BYTES
            while not self._stop.is_set():
                try:
                    ready = poller.poll(int(self._POLL_INTERVAL_SECONDS * 1000))
                except OSError:
                    self._read_failed = True
                    return
                if self._stop.is_set():
                    return
                for _fd, _events in ready:
                    while not self._stop.is_set():
                        try:
                            chunk = os.read(self._drain_fd, self._READ_CHUNK_BYTES)
                        except BlockingIOError:
                            break
                        except InterruptedError:
                            continue
                        except OSError:
                            self._read_failed = True
                            return
                        if not chunk:
                            return
                        if len(self._data) + len(chunk) <= maximum:
                            self._data.extend(chunk)
                        else:
                            self._overflow = True
        except OSError:
            self._read_failed = True
        finally:
            try:
                os.close(self._drain_fd)
            except OSError:
                pass

    def _drain_final_bytes(self) -> None:
        """Drain after the reader has stopped, requiring all writers closed."""
        maximum = MAX_INTERNAL_RECEIPT_BYTES + self._HEADER_BYTES
        while True:
            try:
                chunk = os.read(self._read_fd, self._READ_CHUNK_BYTES)
            except BlockingIOError:
                # A descendant still owns the write end and could change this
                # receipt after finish() returns, so fail closed.
                self._read_failed = True
                return
            except InterruptedError:
                continue
            except OSError:
                self._read_failed = True
                return
            if not chunk:
                return
            if len(self._data) + len(chunk) <= maximum:
                self._data.extend(chunk)
            else:
                self._overflow = True
                return

    def finish(self) -> dict[str, Any] | None:
        """Stop draining and decode the one complete receipt, if trustworthy."""
        if self._finished:
            return None
        self._finished = True
        try:
            os.close(self.write_fd)
        except OSError:
            pass
        self._stop.set()
        self._thread.join(self._JOIN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            self._read_failed = True
        try:
            if not self._thread.is_alive() and not self._overflow and not self._read_failed:
                self._drain_final_bytes()
            if self._overflow or self._read_failed or self._thread.is_alive():
                return None
            raw = bytes(self._data)
            if len(raw) < self._HEADER_BYTES:
                return None
            payload_length = int.from_bytes(raw[: self._HEADER_BYTES], "big")
            if (
                payload_length <= 0
                or payload_length > MAX_INTERNAL_RECEIPT_BYTES
                or len(raw) != self._HEADER_BYTES + payload_length
            ):
                return None
            return _decode_internal_receipt(raw[self._HEADER_BYTES :])
        finally:
            try:
                os.close(self._read_fd)
            except OSError:
                pass


def receipt_facts(receipt: Mapping[str, Any] | None) -> ReceiptFacts | None:
    if not isinstance(receipt, Mapping) or receipt.get("manifest_supported") is not True:
        return None
    integer_fields = (
        "collection_count", "collection_duplicates", "hook_count", "started_count"
    )
    values = [receipt.get(name) for name in integer_fields]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    count, duplicates, hook_count, started_count = values
    if started_count > count:
        return None
    digests = (
        "collection_sha256", "hook_fingerprint_sha256",
        "runtime_fingerprint_sha256", "config_sha256",
    )
    for name in digests:
        value = receipt.get(name)
        if name in ("runtime_fingerprint_sha256", "config_sha256") and value is None:
            continue
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            return None
    if type(receipt.get("collection_error")) is not bool:
        return None
    return ReceiptFacts(
        collection_count=count,
        collection_sha256=receipt["collection_sha256"],
        duplicates=duplicates,
        hook_fingerprint_sha256=receipt["hook_fingerprint_sha256"],
        hook_count=hook_count,
        runtime_fingerprint_sha256=receipt["runtime_fingerprint_sha256"],
        config_sha256=receipt["config_sha256"],
        started_count=started_count,
        collection_error=receipt["collection_error"],
    )


def _facts_match(receipt: Mapping[str, Any], expected: ReceiptFacts) -> bool:
    observed = receipt_facts(receipt)
    return bool(
        observed is not None
        and not observed.collection_error
        and observed.collection_count == expected.collection_count
        and observed.collection_sha256 == expected.collection_sha256
        and observed.duplicates == 0
        and observed.hook_fingerprint_sha256 == expected.hook_fingerprint_sha256
        and observed.runtime_fingerprint_sha256 == expected.runtime_fingerprint_sha256
        and observed.runtime_fingerprint_sha256 is not None
    )


def survivor_proof_ok(
    receipt: Mapping[str, Any] | None,
    *,
    process_exit_status: int,
    expected: ReceiptFacts,
    command: str,
) -> bool:
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != _INTERNAL_RECEIPT_KEYS
        or command not in ("r2", "declared")
    ):
        return False
    if type(process_exit_status) is not int or process_exit_status != 0:
        return False
    if type(receipt.get("session_exit_status")) is not int or receipt["session_exit_status"] != 0:
        return False
    if any(receipt.get(name) is not False for name in ("auxiliary_failure", "earlier_failure")):
        return False
    if receipt.get("collection_error") is not False or receipt.get("started_prefix_ok") is not True:
        return False
    if any(
        receipt.get(name) is not None
        for name in ("witness_node_id", "witness_when", "witness_outcome", "failed_call_index")
    ):
        return False
    if receipt.get("stopped_at_target") is not False or receipt.get("stopped_cold") is not False:
        return False
    unsupported = receipt.get("unsupported")
    coverage_only = receipt.get("unsupported_pytest_cov_only")
    if type(unsupported) is not bool or type(coverage_only) is not bool:
        return False
    if coverage_only and not unsupported:
        return False
    if unsupported and not coverage_only:
        return False
    facts = receipt_facts(receipt)
    if facts is None or facts.started_count != facts.collection_count:
        return False
    if command == "r2" and unsupported:
        return False
    return _facts_match(receipt, expected)


def cold_witness_from_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    process_exit_status: int,
    expected: ReceiptFacts,
) -> tuple[dict[str, Any], int, int] | None:
    if not isinstance(receipt, Mapping) or set(receipt) != _INTERNAL_RECEIPT_KEYS:
        return None
    if any(receipt.get(name) is not True for name in ("cold_requested", "stopped_cold", "started_prefix_ok")):
        return None
    if (
        receipt.get("unsupported") is not False
        or receipt.get("unsupported_pytest_cov_only") is not False
        or receipt.get("target_node_id") is not None
    ):
        return None
    if any(receipt.get(name) is not False for name in ("earlier_failure", "auxiliary_failure", "collection_error")):
        return None
    if receipt.get("witness_when") != "call" or receipt.get("witness_outcome") != "failed":
        return None
    if type(receipt.get("session_exit_status")) is not int or receipt["session_exit_status"] != 1:
        return None
    if type(process_exit_status) is not int or process_exit_status != 1:
        return None
    node_id = receipt.get("witness_node_id")
    if not isinstance(node_id, str) or not _bounded_node_id(node_id):
        return None
    facts = receipt_facts(receipt)
    if facts is None or not _facts_match(receipt, expected):
        return None
    started = receipt.get("started_count")
    failed_index = receipt.get("failed_call_index")
    if type(started) is not int or started < 1 or type(failed_index) is not int:
        return None
    if failed_index != started - 1 or started > facts.collection_count:
        return None
    return (
        {
            "node_id": node_id,
            "when": "call",
            "outcome": "failed",
            "session_exit_status": 1,
            "process_exit_status": 1,
        },
        started,
        failed_index,
    )


def declared_failure_proof_ok(
    receipt: Mapping[str, Any] | None,
    *,
    process_exit_status: int,
    expected: ReceiptFacts,
    manifest_node_ids: Sequence[str],
) -> bool:
    """Prove a declared-command failure came from a started test call."""
    if not isinstance(receipt, Mapping) or set(receipt) != _INTERNAL_RECEIPT_KEYS:
        return False
    if type(process_exit_status) is not int or process_exit_status != 1:
        return False
    if type(receipt.get("session_exit_status")) is not int or receipt["session_exit_status"] != 1:
        return False
    if receipt.get("witness_when") != "call" or receipt.get("witness_outcome") != "failed":
        return False
    unsupported = receipt.get("unsupported")
    coverage_only = receipt.get("unsupported_pytest_cov_only")
    if type(unsupported) is not bool or type(coverage_only) is not bool:
        return False
    if coverage_only and not unsupported:
        return False
    if unsupported and not coverage_only:
        return False
    node_id = receipt.get("witness_node_id")
    if not isinstance(node_id, str) or not _bounded_node_id(node_id):
        return False
    if any(receipt.get(name) is not False for name in ("earlier_failure", "auxiliary_failure", "collection_error")):
        return False
    if receipt.get("started_prefix_ok") is not True:
        return False
    facts = receipt_facts(receipt)
    if facts is None or not _facts_match(receipt, expected):
        return False
    started = facts.started_count
    if started < 1 or started > len(manifest_node_ids):
        return False
    return node_id in manifest_node_ids[:started]


@record
class WitnessPluginInjection:
    plan: "CommandPlan"
    active: bool
    reason: str


def supports_sequential_pytest(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Recognize direct pytest only when its known options are sequential.

    Runtime callers also inspect the project config and effective environment,
    while ``assay plan`` uses the same facts to avoid claiming replay when
    pytest configuration adds xdist behind the declared argv.
    """
    tokens = tuple(argv)
    if not tokens:
        return False
    first = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
    if first == "pytest":
        pytest_args = tokens[1:]
    elif first.startswith("python") and len(tokens) >= 3 and tokens[1:3] == ("-m", "pytest"):
        # The accepted shape is deliberately direct: interpreter, ``-m pytest``,
        # then pytest arguments. Interpreting arbitrary Python launcher options or
        # scripts as transparent wrappers would make the preview overclaim replay.
        pytest_args = tokens[3:]
    else:
        return False
    if _contains_xdist_option(pytest_args):
        return False
    if any(token in ("-o", "--override-ini") or token.startswith("--override-ini=") for token in pytest_args):
        # `addopts` overrides can introduce execution options in a spelling
        # this small parser cannot safely distinguish from ordinary config.
        return False
    environment = os.environ if env is None else env
    if environment.get("PYTEST_PLUGINS"):
        return False
    try:
        if _contains_xdist_option(shlex.split(environment.get("PYTEST_ADDOPTS", ""))):
            return False
    except ValueError:
        return False
    if cwd is not None and not _pytest_configs_allow_sequential(Path(cwd), pytest_args):
        return False
    return True


def _contains_xdist_option(tokens: Sequence[str]) -> bool:
    return any(
        token in ("-n", "--numprocesses", "--dist")
        or token.startswith(("-n=", "--numprocesses=", "--dist="))
        or (token.startswith("-n") and len(token) > 2)
        for token in tokens
    )


def _pytest_configs_allow_sequential(cwd: Path, argv: Sequence[str]) -> bool:
    explicit: Path | None = None
    for index, token in enumerate(argv):
        if token in ("-c", "--config-file"):
            if index + 1 >= len(argv):
                return False
            explicit = Path(argv[index + 1])
            break
        if token.startswith("--config-file="):
            explicit = Path(token.partition("=")[2])
            break
    if explicit is not None:
        if not explicit.is_absolute():
            explicit = cwd / explicit
        return _config_addopts_allow_sequential(explicit)

    # Inspect every recognized config on the path to the filesystem root.
    # pytest selects one config, but treating any inherited xdist setting as
    # uncertainty is a safe fallback when its rootdir selection is external
    # to this read-only preview.
    for directory in (cwd, *cwd.parents):
        for name in ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
            candidate = directory / name
            if candidate.is_file() and not _config_addopts_allow_sequential(candidate):
                return False
    return True


def _config_addopts_allow_sequential(path: Path) -> bool:
    try:
        if path.name == "pyproject.toml":
            document = tomllib.loads(path.read_text(encoding="utf-8"))
            pytest_config = document.get("tool", {}).get("pytest", {})
            options = pytest_config.get("ini_options", {})
            addopts = options.get("addopts", ()) if isinstance(options, dict) else ()
            if isinstance(addopts, str):
                tokens = shlex.split(addopts)
            elif isinstance(addopts, list) and all(isinstance(item, str) for item in addopts):
                tokens = [token for item in addopts for token in shlex.split(item)]
            else:
                return False
        else:
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            with path.open(encoding="utf-8") as stream:
                parser.read_file(stream)
            section_names = (
                ("pytest", "tool:pytest")
                if path.name == "setup.cfg"
                else ("pytest", "tool:pytest")
            )
            addopts = next(
                (parser.get(section, "addopts") for section in section_names if parser.has_option(section, "addopts")),
                "",
            )
            tokens = shlex.split(addopts)
    except (OSError, UnicodeDecodeError, configparser.Error, tomllib.TOMLDecodeError, ValueError):
        return False
    return not _contains_xdist_option(tokens)


def inject_witness_plugin(
    plan: "CommandPlan",
    *,
    plugin_dir: Path,
    cwd: Path | None = None,
    liveness_plugin_path: Path | None = None,
) -> WitnessPluginInjection:
    """Add the capture plugin to supported native pytest command plans."""
    if not supports_sequential_pytest(
        plan.argv_effective,
        cwd=cwd,
        env=plan.env_effective,
    ):
        return WitnessPluginInjection(
            plan=plan, active=False, reason="unsupported-pytest-command"
        )
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_path = plugin_dir / f"{WITNESS_PLUGIN_MODULE}.py"
    if not plugin_path.exists() or plugin_path.read_text(encoding="utf-8") != _PLUGIN_SOURCE:
        temporary = plugin_path.with_suffix(".tmp")
        temporary.write_text(_PLUGIN_SOURCE, encoding="utf-8")
        temporary.replace(plugin_path)
    env = dict(plan.env_effective)
    env[WITNESS_PLUGIN_PATH_ENV] = str(plugin_path.resolve())
    if liveness_plugin_path is None:
        env.pop(WITNESS_LIVENESS_PLUGIN_PATH_ENV, None)
    else:
        env[WITNESS_LIVENESS_PLUGIN_PATH_ENV] = str(
            Path(liveness_plugin_path).resolve()
        )
    existing = env.get("PYTHONPATH", "")
    paths = existing.split(os.pathsep) if existing else []
    if str(plugin_dir) not in paths:
        env["PYTHONPATH"] = os.pathsep.join([str(plugin_dir), *paths])
    cli_only = plan.argv_appended if plan.cli_argv_appended is None else plan.cli_argv_appended
    already_enabled = any(
        token == f"-p{WITNESS_PLUGIN_MODULE}"
        or (
            token == "-p"
            and index + 1 < len(plan.argv_effective)
            and plan.argv_effective[index + 1] == WITNESS_PLUGIN_MODULE
        )
        for index, token in enumerate(plan.argv_effective)
    )
    appended = (
        plan.argv_appended
        if already_enabled
        else plan.argv_appended + ("-p", WITNESS_PLUGIN_MODULE)
    )
    return WitnessPluginInjection(
        plan=replace(
            plan,
            argv_appended=appended,
            argv_effective=plan.argv_declared + appended,
            env_effective=env,
            cli_argv_appended=cli_only,
        ),
        active=True,
        reason="direct-sequential-pytest",
    )


def make_attempt_plan(
    plan: "CommandPlan",
    *,
    receipt_path: Path | None = None,
    receipt_fd: int | None = None,
    target_node_id: str | None,
    cold: bool = False,
    manifest_path: Path | None = None,
) -> "CommandPlan":
    if cold and target_node_id is not None:
        raise ValueError("a cold witness attempt cannot target one node")
    if (receipt_path is None) == (receipt_fd is None):
        raise ValueError("exactly one receipt path or descriptor is required")
    env = dict(plan.env_effective)
    env.pop(WITNESS_FILE_ENV, None)
    env.pop(WITNESS_FD_ENV, None)
    if receipt_fd is not None:
        if type(receipt_fd) is not int or receipt_fd < 0:
            raise ValueError("receipt descriptor must be a non-negative integer")
        env[WITNESS_FD_ENV] = str(receipt_fd)
    else:
        assert receipt_path is not None
        env[WITNESS_FILE_ENV] = str(receipt_path)
    if target_node_id is not None:
        env[WITNESS_TARGET_ENV] = target_node_id
    else:
        env.pop(WITNESS_TARGET_ENV, None)
    if cold:
        env[WITNESS_COLD_ENV] = "1"
    else:
        env.pop(WITNESS_COLD_ENV, None)
    if manifest_path is None:
        env.pop(WITNESS_MANIFEST_FILE_ENV, None)
    else:
        env[WITNESS_MANIFEST_FILE_ENV] = str(manifest_path)
    return replace(plan, env_effective=env)


def cold_shape_refusal(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    appended: Sequence[str] = (),
) -> str | None:
    """Return a conservative static reason that a pytest command cannot stop safely."""
    try:
        transformed = transform_argv(argv)
    except UnrecognizedCoverageOption as exc:
        return f"unrecognized coverage option {exc}"
    full_argv = (*transformed, *appended)
    if not supports_sequential_pytest(full_argv, env=env):
        return "not a sequential pytest command"
    if env.get("PYTEST_ADDOPTS", ""):
        return "pytest environment option"
    if "PYTEST_PLUGINS" in env:
        return "pytest environment option"
    if any(env.get(name, "") for name in ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG")):
        return "coverage re-enabled by environment"

    tokens = full_argv
    if tokens and tokens[0].replace("\\", "/").rsplit("/", 1)[-1].startswith("python"):
        start = 3
    else:
        start = 1
    args = list(tokens[start:])
    expanded: list[tuple[str, str | None]] = []
    index = 0
    flag_letters = COLD_PYTEST_FLAG_OPTIONS
    value_letters = COLD_PYTEST_VALUE_OPTIONS
    while index < len(args):
        token = args[index]
        index += 1
        if token == "--":
            break
        if token.startswith("--") or not token.startswith("-") or token == "-":
            expanded.append((token, None))
            continue
        body = token[1:]
        position = 0
        while position < len(body):
            letter = body[position]
            option = f"-{letter}"
            if letter in flag_letters:
                expanded.append((option, None))
                position += 1
                continue
            if letter in value_letters:
                value = body[position + 1 :] or None
                if value is None and index < len(args):
                    value = args[index]
                    index += 1
                expanded.append((option, value))
                position = len(body)
                continue
            return f"unrecognized short option cluster {token}"

    for option, value in expanded:
        lower = option.lower()
        if option in ("-h", "-V"):
            return "not a sequential pytest command"
        if option in ("-o",) or lower == "--override-ini" or lower.startswith("--override-ini="):
            return "pytest override"
        if option == "-c" or lower == "--config-file" or lower.startswith(
            ("--config-file", "--rootdir", "--confcutdir")
        ):
            return "pytest configuration override"
        if option == "-x" or lower in ("--exitfirst", "--maxfail") or lower.startswith("--maxfail="):
            return "fail-fast option"
        if option in ("-d", "-f", "-n") or lower.startswith(
            ("--numprocesses", "--dist", "--looponfail")
        ):
            return "parallel option"
        if lower in {
            "--lf", "--last-failed", "--ff", "--failed-first", "--nf",
            "--new-first", "--sw", "--stepwise", "--stepwise-skip", "--sw-skip",
        } or lower.startswith(("--randomly", "--random-order")):
            return "order-changing option"
        if option == "-p":
            plugin = value or ""
            normalized = plugin.casefold()
            if normalized in {"randomly", "random_order", "pytest_randomly"}:
                return "order-changing option"
            if normalized in {"pytest_cov", "pytest-cov"}:
                return "coverage plugin re-enabled"
            if normalized == "xdist":
                return "parallel option"
        if lower.startswith("-p") and len(option) > 2:
            plugin = option[2:]
            normalized = plugin.casefold()
            if normalized in {"randomly", "random_order", "pytest_randomly"}:
                return "order-changing option"
            if normalized in {"pytest_cov", "pytest-cov"}:
                return "coverage plugin re-enabled"
            if normalized == "xdist":
                return "parallel option"
        if option.startswith("--") and option.startswith(("--numprocesses", "--dist", "--looponfail")):
            return "parallel option"
    return None


def read_internal_receipt(path: Path) -> dict[str, Any] | None:
    """Read a bounded regular-file receipt; unsafe or malformed means none."""
    try:
        raw = safeio.read_bounded_file(
            path.parent, path.name, limit=MAX_INTERNAL_RECEIPT_BYTES
        )
    except (AssayError, OSError, ValueError):
        return None
    if not raw:
        return None
    return _decode_internal_receipt(raw)


def _decode_internal_receipt(raw: bytes) -> dict[str, Any] | None:
    if not raw or len(raw) > MAX_INTERNAL_RECEIPT_BYTES:
        return None
    try:
        decoded = raw.decode("utf-8", errors="strict")
        document = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return document if isinstance(document, dict) else None


def _is_false(receipt: dict[str, Any], key: str) -> bool:
    """Whether *receipt* carries *key* as the literal ``False`` (never falsy)."""
    return receipt.get(key) is False


def witness_from_receipt(
    receipt: dict[str, Any] | None, *, process_exit_status: int | None
) -> dict[str, Any] | None:
    """Project only the bounded current-call failure facts onto the verdict."""
    if receipt is None or set(receipt) != _INTERNAL_RECEIPT_KEYS:
        return None
    if (
        type(receipt.get("session_exit_status")) is not int
        or receipt.get("session_exit_status") != 1
    ):
        return None
    if type(process_exit_status) is not int or process_exit_status != 1:
        return None
    # B114's cold path vets every lifecycle hook. B106's existing targeted
    # replay keeps its narrower standard-loop contract, recorded separately,
    # so turning on cold witnesses does not erase reusable B106 evidence.
    if (
        not _is_false(receipt, "unsupported")
        and receipt.get("replay_supported") is not True
    ):
        return None
    node_id = receipt.get("witness_node_id")
    if not isinstance(node_id, str) or not _bounded_node_id(node_id):
        return None
    if receipt.get("witness_when") != "call" or receipt.get("witness_outcome") != "failed":
        return None
    if not _is_false(receipt, "auxiliary_failure"):
        return None
    target_count = receipt.get("target_count")
    if target_count is not None and (
        type(target_count) is not int or target_count != 1
    ):
        return None
    if receipt.get("target_node_id") not in (None, node_id):
        return None
    return {
        "node_id": node_id,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }


def replay_witness_from_receipt(
    receipt: dict[str, Any] | None,
    *,
    process_exit_status: int | None,
    target_node_id: str,
    expected_facts: ReceiptFacts | None = None,
) -> dict[str, Any] | None:
    # A replay can still reach the target node after pytest reports a failed
    # collection for another module (for example with
    # ``--continue-on-collection-errors``). That receipt cannot certify the
    # suite prefix used by cold-policy reuse.
    if receipt is None or not _is_false(receipt, "collection_error"):
        return None
    witness = witness_from_receipt(receipt, process_exit_status=process_exit_status)
    if witness is None or witness["node_id"] != target_node_id:
        return None
    if (
        type(receipt.get("target_count")) is not int
        or receipt.get("target_count") != 1
        or receipt.get("target_node_id") != target_node_id
    ):
        return None
    if receipt.get("stopped_at_target") is not True:
        return None
    if not _is_false(receipt, "earlier_failure"):
        return None
    if expected_facts is not None and not _facts_match(receipt, expected_facts):
        return None
    return witness


def _bounded_node_id(value: str) -> bool:
    try:
        return bool(value) and len(value.encode("utf-8")) <= MAX_NODE_ID_UTF8_BYTES
    except UnicodeEncodeError:
        return False


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


_INTERNAL_RECEIPT_KEYS = frozenset(
    {
        "unsupported",
        "replay_supported",
        "target_node_id",
        "target_count",
        "earlier_failure",
        "auxiliary_failure",
        "witness_node_id",
        "witness_when",
        "witness_outcome",
        "session_exit_status",
        "stopped_at_target",
        "cold_requested",
        "stopped_cold",
        "collection_error",
        "manifest_supported",
        "collection_count",
        "collection_sha256",
        "collection_duplicates",
        "started_count",
        "started_prefix_ok",
        "failed_call_index",
        "hook_fingerprint_sha256",
        "hook_count",
        "runtime_fingerprint_sha256",
        "config_sha256",
        "archive_hook_exception_used",
        "unsupported_pytest_cov_only",
    }
)


_PLUGIN_SOURCE = r'''"""Temporary standard-library/pytest plugin written by assay."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
import platform
import sys
import sysconfig
import pytest
from pathlib import Path
from _pytest.reports import TestReport as _TestReport

_HOOKS = (
    "pytest_runtestloop", "pytest_runtest_protocol", "pytest_runtest_logstart",
    "pytest_runtest_logreport", "pytest_runtest_call", "pytest_runtest_makereport",
    "pytest_runtest_setup",
    "pytest_runtest_teardown", "pytest_collectreport",
    "pytest_collection_modifyitems", "pytest_sessionfinish",
)
_ASSAY_PLUGIN_NAME = "assay_mutation_witness_plugin"
_ASSAY_PLUGIN_HOOKS = (
    "pytest_load_initial_conftests",
    "pytest_plugin_registered",
    "pytest_collection_finish",
    "pytest_collectreport",
    "pytest_runtest_logstart",
    "pytest_runtest_logreport",
    "pytest_sessionfinish",
)
_LIVENESS_PLUGIN_NAME = "assay_liveness_plugin"
_LIVENESS_PLUGIN_HOOKS = (
    "pytest_configure",
    "pytest_runtest_logreport",
    "pytest_sessionfinish",
    "pytest_unconfigure",
)
_WITNESS_FD = None
try:
    _raw_witness_fd = os.environ.get("ASSAY_MUTATION_WITNESS_FD")
    if _raw_witness_fd is not None:
        _WITNESS_FD = int(_raw_witness_fd)
        if _WITNESS_FD < 0:
            _WITNESS_FD = None
        else:
            # The descriptor is explicitly inherited into this pytest process.
            # Keep it closed across any later exec unless a child deliberately
            # opts in with pass_fds.
            os.set_inheritable(_WITNESS_FD, False)
except (OSError, ValueError):
    _WITNESS_FD = None
_HOOK_REGISTRY_HOOKS = tuple(dict.fromkeys((*_HOOKS, *_LIVENESS_PLUGIN_HOOKS)))
_SESSION = None
_ITEMS = ()
_TARGET = None
_TARGET_COUNT = None
_STANDARD_LOOP = False
_REPLAY_SUPPORTED = False
_UNSUPPORTED_PYTEST_COV_ONLY = False
_EARLIER_FAILURE = False
_AUXILIARY_FAILURE = False
_COLLECTION_ERROR = False
_FIRST_CALL_FAILURE = None
_WITNESS = None
_STOPPED_AT_TARGET = False
_COLD = False
_STOPPED_COLD = False
_FAILED_CALL_INDEX = None
_STARTED = 0
_PREFIX_OK = True
_MANIFEST_SUPPORTED = False
_COLLECTION_COUNT = None
_COLLECTION_SHA256 = None
_COLLECTION_DUPLICATES = None
_HOOK_FINGERPRINT_SHA256 = None
_HOOK_COUNT = None
_RUNTIME_FINGERPRINT_SHA256 = None
_CONFIG_SHA256 = None
_ARCHIVE_EXCEPTION_USED = False
_HOOK_IMPL_REGISTRY = {}
_PINNED_BUILTIN_HOOKS = {}
_PINNED_BUILTIN_SOURCE_CODES = {}
_PINNED_LATE_BUILTIN_PLUGIN_TYPES = {}
_BUILTIN_HOOKS_PINNED = False
_PINNED_ASSAY_HOOKS = {}
_ASSAY_HOOKS_PINNED = False
_PINNED_LIVENESS_HOOKS = {}
_LIVENESS_HOOKS_PINNED = False
_EXPECTED_TEST_REPORT_CONSTRUCTOR = vars(_TestReport).get("from_item_and_call")


def _bounded(value):
    try:
        encoded = value.encode("utf-8")
        return bool(value) and len(encoded) <= 4096 and b"\n" not in encoded and b"\r" not in encoded
    except Exception:
        return False


def _pin_builtin_hook_callables(config):
    global _PINNED_BUILTIN_HOOKS, _PINNED_BUILTIN_SOURCE_CODES
    global _PINNED_LATE_BUILTIN_PLUGIN_TYPES, _BUILTIN_HOOKS_PINNED
    _PINNED_BUILTIN_HOOKS = {}
    _PINNED_BUILTIN_SOURCE_CODES = {hook_name: {} for hook_name in _HOOKS}
    _PINNED_LATE_BUILTIN_PLUGIN_TYPES = {}
    _BUILTIN_HOOKS_PINNED = False
    pinned = {}
    try:
        # Pytest creates several builtin plugin instances after the early hook
        # below. Pin the function and class objects already imported from the
        # pytest package now, then bind their registered HookImpl objects when
        # pytest registers those known builtin plugins.
        for module_name, module in tuple(sys.modules.items()):
            if not isinstance(module_name, str) or not module_name.startswith("_pytest."):
                continue
            if module is None:
                continue
            for attribute_name, value in tuple(vars(module).items()):
                if isinstance(value, type):
                    if getattr(value, "__module__", "").startswith("_pytest."):
                        for class_attribute_name, descriptor in tuple(vars(value).items()):
                            function = getattr(descriptor, "__func__", descriptor)
                            code = getattr(function, "__code__", None)
                            hook_names = {
                                name for name in (
                                    class_attribute_name,
                                    getattr(function, "__name__", None),
                                )
                                if name in _PINNED_BUILTIN_SOURCE_CODES
                            }
                            if code is not None:
                                for hook_name in hook_names:
                                    _PINNED_BUILTIN_SOURCE_CODES[hook_name][function] = code
                else:
                    function = getattr(value, "__func__", value)
                    code = getattr(function, "__code__", None)
                    hook_names = {
                        name for name in (
                            attribute_name,
                            getattr(function, "__name__", None),
                        )
                        if name in _PINNED_BUILTIN_SOURCE_CODES
                    }
                    if code is not None:
                        for hook_name in hook_names:
                            _PINNED_BUILTIN_SOURCE_CODES[hook_name][function] = code

        late_builtin_classes = {
            "capturemanager": ("_pytest.capture", "CaptureManager"),
            "session": ("_pytest.main", "Session"),
            "lfplugin": ("_pytest.cacheprovider", "LFPlugin"),
            "nfplugin": ("_pytest.cacheprovider", "NFPlugin"),
            "terminalreporter": ("_pytest.terminal", "TerminalReporter"),
            "logging-plugin": ("_pytest.logging", "LoggingPlugin"),
            "funcmanage": ("_pytest.fixtures", "FixtureManager"),
        }
        for plugin_name, (module_name, class_name) in late_builtin_classes.items():
            module = sys.modules.get(module_name)
            plugin_type = getattr(module, class_name, None) if module is not None else None
            if isinstance(plugin_type, type):
                _PINNED_LATE_BUILTIN_PLUGIN_TYPES[plugin_name] = plugin_type

        for hook_name in _HOOKS:
            hook_impls = getattr(config.hook, hook_name).get_hookimpls()
            implementations = []
            for impl in hook_impls:
                function = getattr(impl, "function", None)
                module_name = getattr(function, "__module__", None)
                if not (
                    isinstance(module_name, str)
                    and module_name.startswith("_pytest.")
                ):
                    continue
                callable_object = getattr(function, "__func__", function)
                code = getattr(callable_object, "__code__", None)
                if code is None:
                    return False
                _PINNED_BUILTIN_SOURCE_CODES[hook_name][callable_object] = code
                implementations.append((impl, function, code))
            pinned[hook_name] = tuple(implementations)
    except Exception:
        return False
    if set(pinned) != set(_HOOKS):
        return False
    _PINNED_BUILTIN_HOOKS = pinned
    _BUILTIN_HOOKS_PINNED = True
    return True


def _pin_assay_hook_callables(config):
    global _PINNED_ASSAY_HOOKS, _ASSAY_HOOKS_PINNED
    _PINNED_ASSAY_HOOKS = {}
    _ASSAY_HOOKS_PINNED = False
    pinned = {}
    try:
        plugin = sys.modules.get(__name__)
        if plugin is None or config.pluginmanager.get_name(plugin) != _ASSAY_PLUGIN_NAME:
            return False
        for hook_name in _ASSAY_PLUGIN_HOOKS:
            implementations = [
                impl
                for impl in getattr(config.hook, hook_name).get_hookimpls()
                if getattr(impl, "plugin_name", None) == _ASSAY_PLUGIN_NAME
            ]
            if len(implementations) != 1:
                return False
            impl = implementations[0]
            function = getattr(impl, "function", None)
            code = getattr(function, "__code__", None)
            if (
                code is None
                or getattr(function, "__module__", None) != _ASSAY_PLUGIN_NAME
                or getattr(function, "__globals__", None) is not vars(plugin)
            ):
                return False
            pinned[hook_name] = (impl, function, code, function.__globals__)
    except Exception:
        return False
    if set(pinned) != set(_ASSAY_PLUGIN_HOOKS):
        return False
    _PINNED_ASSAY_HOOKS = pinned
    _ASSAY_HOOKS_PINNED = True
    return True


def _pin_liveness_hook_callables(config):
    global _PINNED_LIVENESS_HOOKS, _LIVENESS_HOOKS_PINNED
    _PINNED_LIVENESS_HOOKS = {}
    _LIVENESS_HOOKS_PINNED = False
    pinned = {}
    try:
        plugin = config.pluginmanager.get_plugin(_LIVENESS_PLUGIN_NAME)
        expected_path = os.environ.get("ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH")
        if expected_path is None:
            if plugin is not None:
                return False
            if any(
                getattr(impl, "plugin_name", None) == _LIVENESS_PLUGIN_NAME
                for hook_name in _LIVENESS_PLUGIN_HOOKS
                for impl in getattr(config.hook, hook_name).get_hookimpls()
            ):
                return False
            _LIVENESS_HOOKS_PINNED = True
            return True
        if plugin is None or config.pluginmanager.get_name(plugin) != _LIVENESS_PLUGIN_NAME:
            return False
        module_path = getattr(plugin, "__file__", None)
        if (
            not isinstance(module_path, str)
            or Path(module_path).resolve() != Path(expected_path).resolve()
        ):
            return False
        for hook_name in _LIVENESS_PLUGIN_HOOKS:
            implementations = [
                impl
                for impl in getattr(config.hook, hook_name).get_hookimpls()
                if getattr(impl, "plugin_name", None) == _LIVENESS_PLUGIN_NAME
            ]
            if len(implementations) != 1:
                return False
            impl = implementations[0]
            function = getattr(impl, "function", None)
            code = getattr(function, "__code__", None)
            if (
                function is not getattr(plugin, hook_name, None)
                or code is None
                or getattr(function, "__module__", None) != _LIVENESS_PLUGIN_NAME
                or getattr(function, "__globals__", None) is not vars(plugin)
            ):
                return False
            pinned[hook_name] = (impl, function, code, function.__globals__)
    except Exception:
        return False
    if set(pinned) != set(_LIVENESS_PLUGIN_HOOKS):
        return False
    _PINNED_LIVENESS_HOOKS = pinned
    _LIVENESS_HOOKS_PINNED = True
    return True


def _pin_registered_builtin_plugin(plugin, plugin_name, manager):
    if not _BUILTIN_HOOKS_PINNED:
        return
    expected_type = _PINNED_LATE_BUILTIN_PLUGIN_TYPES.get(plugin_name)
    if expected_type is None or type(plugin) is not expected_type:
        return
    try:
        for hook_name in _HOOKS:
            for impl in getattr(manager.hook, hook_name).get_hookimpls():
                if getattr(impl, "plugin_name", None) != plugin_name:
                    continue
                function = getattr(impl, "function", None)
                callable_object = getattr(function, "__func__", function)
                expected_code = _PINNED_BUILTIN_SOURCE_CODES.get(hook_name, {}).get(
                    callable_object
                )
                code = getattr(callable_object, "__code__", None)
                if expected_code is None or code is not expected_code:
                    continue
                implementations = list(_PINNED_BUILTIN_HOOKS.get(hook_name, ()))
                implementations.append((impl, function, code))
                _PINNED_BUILTIN_HOOKS[hook_name] = tuple(implementations)
    except Exception:
        # A later hook check will fail closed if any builtin implementation
        # needed for replay could not be pinned during registration.
        return


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config):
    # This runs before pytest's initial-conftest loader. Keep the actual
    # registered builtin callables so a candidate conftest cannot substitute
    # a same-module/same-path function before the collection-time checks.
    _pin_builtin_hook_callables(early_config)
    _pin_assay_hook_callables(early_config)
    _pin_liveness_hook_callables(early_config)


@pytest.hookimpl(tryfirst=True)
def pytest_plugin_registered(plugin, plugin_name, manager):
    # Pytest creates some builtin plugin instances during startup. Accept only
    # their pre-pinned class and function objects, and bind the exact HookImpl
    # before candidate conftests can replace its callable.
    _pin_registered_builtin_plugin(plugin, plugin_name, manager)


_REVIEWED_EXTERNAL_HOOKS = {
    ("hypothesis", "pytest_runtest_call"): (
        "hypothesispytest", "_hypothesis_pytestplugin", "pytest_runtest_call",
        ("hookwrapper",),
    ),
    ("hypothesis", "pytest_runtest_makereport"): (
        "hypothesispytest", "_hypothesis_pytestplugin", "pytest_runtest_makereport",
        ("hookwrapper",),
    ),
    ("hypothesis", "pytest_collection_modifyitems"): (
        "hypothesispytest", "_hypothesis_pytestplugin", "pytest_collection_modifyitems",
        (),
    ),
    ("pytest-cov", "pytest_runtestloop"): (
        "_cov", "pytest_cov.plugin", "CovPlugin.pytest_runtestloop", ("wrapper",),
    ),
    ("pytest-cov", "pytest_runtest_call"): (
        "_cov", "pytest_cov.plugin", "CovPlugin.pytest_runtest_call", ("hookwrapper",),
    ),
}


def _resolve_reviewed_external_callable(module_name, qualname):
    try:
        value = importlib.import_module(module_name)
        for part in qualname.split("."):
            value = getattr(value, part)
        return getattr(value, "__func__", value)
    except Exception:
        return None


_REVIEWED_EXTERNAL_CALLABLES = {
    key: _resolve_reviewed_external_callable(expected[1], expected[2])
    for key, expected in _REVIEWED_EXTERNAL_HOOKS.items()
}
_REVIEWED_EXTERNAL_CALLABLE_CODES = {
    key: getattr(function, "__code__", None)
    for key, function in _REVIEWED_EXTERNAL_CALLABLES.items()
}

_REVIEWED_EXTERNAL_DISTRIBUTIONS = {
    "hypothesis": (
        "6.156.6",
        "c0b8c0b39b3eed02eea3591c642d652f2e127acb8b22027058cefe3416b64c35",
    ),
    "pytest-cov": (
        "7.1.0",
        "7cc8a14b8cc8effa886c40fdfa058459d89686d62c53fea6721750e722a60f40",
    ),
}


def _reviewed_external_hook(impl, hook_name, distribution_name):
    expected = _REVIEWED_EXTERNAL_HOOKS.get((distribution_name, hook_name))
    if expected is None:
        return False
    plugin_name, module_name, qualname, expected_flags = expected
    registered_function = impl.function
    function = getattr(registered_function, "__func__", registered_function)
    if (
        getattr(impl, "plugin_name", None) != plugin_name
        or getattr(function, "__module__", None) != module_name
        or getattr(function, "__qualname__", None) != qualname
        or function is not _REVIEWED_EXTERNAL_CALLABLES.get(
            (distribution_name, hook_name)
        )
        or getattr(function, "__code__", None)
        is not _REVIEWED_EXTERNAL_CALLABLE_CODES.get((distribution_name, hook_name))
    ):
        return False
    flags = tuple(sorted(
        name for name in ("hookwrapper", "tryfirst", "trylast", "wrapper")
        if getattr(impl, name, False)
    ))
    if flags != tuple(sorted(expected_flags)):
        return False
    module = sys.modules.get(module_name)
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str) or getattr(function, "__globals__", None) is not getattr(
        module, "__dict__", None
    ):
        return False
    code_file = getattr(getattr(function, "__code__", None), "co_filename", None)
    if not isinstance(code_file, str):
        return False
    try:
        resolved_module_file = Path(module_file).resolve()
        if resolved_module_file != Path(code_file).resolve():
            return False
        reviewed_version, reviewed_source_sha256 = _REVIEWED_EXTERNAL_DISTRIBUTIONS[
            distribution_name
        ]
        distribution = metadata.distribution(distribution_name)
        declared_files = distribution.files
        normalized_name = (distribution.metadata.get("Name") or "").casefold().replace("_", "-")
        wanted_name = distribution_name.casefold().replace("_", "-")
        if (
            normalized_name != wanted_name
            or distribution.version != reviewed_version
            or declared_files is None
        ):
            return False
        owned = any(
            Path(distribution.locate_file(item)).resolve() == resolved_module_file
            for item in declared_files
        )
        if not owned:
            return False
        return hashlib.sha256(resolved_module_file.read_bytes()).hexdigest() == reviewed_source_sha256
    except (ImportError, OSError, RuntimeError, ValueError, metadata.PackageNotFoundError):
        return False


def _is_pinned_builtin_hook(hook_name, impl):
    if not _BUILTIN_HOOKS_PINNED:
        return False
    for pinned_impl, pinned_function, pinned_code in _PINNED_BUILTIN_HOOKS.get(
        hook_name, ()
    ):
        if impl is not pinned_impl:
            continue
        function = getattr(impl, "function", None)
        callable_object = getattr(function, "__func__", function)
        return (
            function is pinned_function
            and getattr(callable_object, "__code__", None) is pinned_code
        )
    return False


def _is_pinned_assay_hook(hook_name, impl):
    if not _ASSAY_HOOKS_PINNED:
        return False
    pinned = _PINNED_ASSAY_HOOKS.get(hook_name)
    if pinned is None:
        return False
    pinned_impl, pinned_function, pinned_code, pinned_globals = pinned
    function = getattr(impl, "function", None)
    return (
        impl is pinned_impl
        and function is pinned_function
        and getattr(function, "__code__", None) is pinned_code
        and getattr(function, "__globals__", None) is pinned_globals
    )


def _assay_hook_registry_matches(config):
    if not _ASSAY_HOOKS_PINNED:
        return False
    try:
        for hook_name, expected in _PINNED_ASSAY_HOOKS.items():
            (
                expected_impl,
                expected_function,
                expected_code,
                expected_globals,
            ) = expected
            implementations = [
                impl
                for impl in getattr(config.hook, hook_name).get_hookimpls()
                if getattr(impl, "plugin_name", None) == _ASSAY_PLUGIN_NAME
            ]
            if len(implementations) != 1:
                return False
            impl = implementations[0]
            function = getattr(impl, "function", None)
            if (
                impl is not expected_impl
                or function is not expected_function
                or getattr(function, "__code__", None) is not expected_code
                or getattr(function, "__globals__", None) is not expected_globals
            ):
                return False
    except Exception:
        return False
    return _liveness_hook_registry_matches(config)


def _is_pinned_liveness_hook(hook_name, impl):
    if not _LIVENESS_HOOKS_PINNED:
        return False
    pinned = _PINNED_LIVENESS_HOOKS.get(hook_name)
    if pinned is None:
        return False
    pinned_impl, pinned_function, pinned_code, pinned_globals = pinned
    function = getattr(impl, "function", None)
    return (
        impl is pinned_impl
        and function is pinned_function
        and getattr(function, "__code__", None) is pinned_code
        and getattr(function, "__globals__", None) is pinned_globals
    )


def _liveness_hook_registry_matches(config):
    if not _LIVENESS_HOOKS_PINNED:
        return False
    try:
        plugin = config.pluginmanager.get_plugin(_LIVENESS_PLUGIN_NAME)
        expected_path = os.environ.get("ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH")
        if expected_path is None:
            return plugin is None and not _PINNED_LIVENESS_HOOKS
        if (
            plugin is None
            or config.pluginmanager.get_name(plugin) != _LIVENESS_PLUGIN_NAME
            or Path(getattr(plugin, "__file__", "")).resolve() != Path(expected_path).resolve()
            or set(_PINNED_LIVENESS_HOOKS) != set(_LIVENESS_PLUGIN_HOOKS)
        ):
            return False
        for hook_name, expected in _PINNED_LIVENESS_HOOKS.items():
            expected_impl, expected_function, expected_code, expected_globals = expected
            implementations = [
                impl
                for impl in getattr(config.hook, hook_name).get_hookimpls()
                if getattr(impl, "plugin_name", None) == _LIVENESS_PLUGIN_NAME
            ]
            if len(implementations) != 1:
                return False
            impl = implementations[0]
            function = getattr(impl, "function", None)
            if (
                impl is not expected_impl
                or function is not expected_function
                or getattr(function, "__code__", None) is not expected_code
                or getattr(function, "__globals__", None) is not expected_globals
            ):
                return False
    except Exception:
        return False
    return True


def _trusted_hook_impl(config, hook_name, impl):
    global _ARCHIVE_EXCEPTION_USED
    module_name = getattr(impl.function, "__module__", None)
    module = sys.modules.get(module_name) if isinstance(module_name, str) else None
    module_file = getattr(module, "__file__", None)
    code_file = getattr(getattr(impl.function, "__code__", None), "co_filename", None)
    if not isinstance(module_file, str) or not isinstance(code_file, str):
        return False
    try:
        resolved_module_file = Path(module_file).resolve()
        resolved_code_file = Path(code_file).resolve()
    except (OSError, RuntimeError):
        return False
    if resolved_module_file != resolved_code_file:
        return False
    if module_name == "assay_mutation_witness_plugin":
        if not _is_pinned_assay_hook(hook_name, impl):
            return False
        expected = os.environ.get("ASSAY_MUTATION_WITNESS_PLUGIN_PATH")
        return bool(expected) and resolved_module_file == Path(expected).resolve()
    if module_name == "assay_liveness_plugin":
        expected = os.environ.get("ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH")
        return (
            bool(expected)
            and _is_pinned_liveness_hook(hook_name, impl)
            and resolved_module_file == Path(expected).resolve()
        )
    if (
        hook_name == "pytest_sessionfinish"
        and module_name == "conftest"
        and not any(name in os.environ for name in (
            "ASSAY_B105_COVERAGE_SOURCE", "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
            "ASSAY_B105_SOURCE_COMMIT", "ASSAY_B105_SOURCE_TREE",
        ))
    ):
        try:
            relative = resolved_module_file.relative_to(Path(config.rootpath).resolve())
        except (OSError, RuntimeError, ValueError):
            return False
        if relative.as_posix() == "tests/conftest.py":
            _ARCHIVE_EXCEPTION_USED = True
            return True
    if isinstance(module_name, str) and module_name.startswith("_pytest."):
        if not _is_pinned_builtin_hook(hook_name, impl):
            return False
        try:
            import _pytest
            pytest_root = Path(_pytest.__file__).resolve().parent
            resolved_module_file.relative_to(pytest_root)
            return True
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False
    return _reviewed_external_hook(impl, hook_name, "hypothesis")


def _only_builtin_hook_impls(config, hook_name, primary=None, *, allow_coverage=False):
    """Accept pytest built-ins and reviewed Hypothesis hooks, rejecting unknown hooks."""
    try:
        impls = getattr(config.hook, hook_name).get_hookimpls()
    except Exception:
        return False
    if any(
        not (
            _trusted_hook_impl(config, hook_name, impl)
            or (allow_coverage and _reviewed_external_hook(impl, hook_name, "pytest-cov"))
        )
        for impl in impls
    ):
        return False
    if primary is None:
        return True
    ordinary = [impl for impl in impls if not impl.wrapper and not impl.hookwrapper]
    return (
        len(ordinary) == 1
        and ordinary[0].plugin_name == primary[0]
        and ordinary[0].function.__module__ == primary[1]
    )


def _coverage_hook_set_is_only_reviewed(config, *, xdist_active):
    """True when the only unsupported execution hooks are reviewed pytest-cov hooks."""
    if xdist_active:
        return False
    if not _only_builtin_hook_impls(
        config, "pytest_runtestloop", primary=("main", "_pytest.main"), allow_coverage=True
    ) or not _only_builtin_hook_impls(
        config, "pytest_runtest_protocol", primary=("runner", "_pytest.runner"), allow_coverage=True
    ):
        return False
    coverage_seen = False
    for hook_name in _HOOKS:
        try:
            impls = getattr(config.hook, hook_name).get_hookimpls()
        except Exception:
            return False
        for impl in impls:
            if _reviewed_external_hook(impl, hook_name, "pytest-cov"):
                coverage_seen = True
            elif not _trusted_hook_impl(config, hook_name, impl):
                return False
    return coverage_seen


def _hook_impl_signature(impl):
    function = getattr(impl, "function", None)
    code = getattr(function, "__code__", None)
    flags = tuple(sorted(
        name for name in ("hookwrapper", "tryfirst", "trylast", "wrapper")
        if getattr(impl, name, False)
    ))
    return (
        id(impl), id(function), id(code), getattr(impl, "plugin_name", None),
        getattr(function, "__module__", None), getattr(function, "__qualname__", None), flags,
    )


def _mark_hook_registry_changed():
    global _STANDARD_LOOP, _REPLAY_SUPPORTED, _UNSUPPORTED_PYTEST_COV_ONLY
    _STANDARD_LOOP = False
    _REPLAY_SUPPORTED = False
    _UNSUPPORTED_PYTEST_COV_ONLY = False


def _test_report_constructor_is_trusted():
    try:
        from _pytest.reports import TestReport

        return (
            _EXPECTED_TEST_REPORT_CONSTRUCTOR is not None
            and vars(TestReport).get("from_item_and_call")
            is _EXPECTED_TEST_REPORT_CONSTRUCTOR
        )
    except Exception:
        return False


def _is_builtin_strict_xpass(report, item):
    if (
        report.outcome != "failed"
        or not isinstance(getattr(report, "longrepr", None), str)
        or not report.longrepr.startswith("[XPASS(strict)]")
    ):
        return False
    marker = item.get_closest_marker("xfail")
    strict = marker.kwargs.get("strict") if marker is not None else None
    if strict is None:
        try:
            strict = item.config.getini("xfail_strict")
        except Exception:
            return False
    return strict is True


def _check_hook_registry(hook_name):
    if hook_name not in _HOOK_REGISTRY_HOOKS or _SESSION is None:
        return
    try:
        expected = _HOOK_IMPL_REGISTRY.get(hook_name)
        impls = getattr(_SESSION.config.hook, hook_name).get_hookimpls()
        observed = tuple(_hook_impl_signature(impl) for impl in impls)
    except Exception:
        _mark_hook_registry_changed()
        return
    if expected is None or observed != expected:
        _mark_hook_registry_changed()


def _before_hook_call(hook_name, methods, kwargs):
    _check_hook_registry(hook_name)
    if hook_name == "pytest_runtest_makereport" and not _test_report_constructor_is_trusted():
        _mark_hook_registry_changed()


def _after_hook_call(outcome, hook_name, methods, kwargs):
    _check_hook_registry(hook_name)
    if hook_name != "pytest_runtest_makereport":
        return
    if not _test_report_constructor_is_trusted():
        _mark_hook_registry_changed()
    if getattr(outcome, "excinfo", None) is not None:
        _mark_hook_registry_changed()
        return
    try:
        report = outcome.get_result()
        call = kwargs.get("call")
        item = kwargs.get("item")
        if call is None or item is None or report.when != call.when or report.nodeid != item.nodeid:
            _mark_hook_registry_changed()
            return
        if report.when == "call":
            if report.outcome == "passed" and call.excinfo is not None:
                _mark_hook_registry_changed()
            elif (
                report.outcome == "failed"
                and call.excinfo is None
                and not getattr(report, "wasxfail", None)
                and not _is_builtin_strict_xpass(report, item)
            ):
                _mark_hook_registry_changed()
            elif report.outcome not in ("passed", "failed", "skipped"):
                _mark_hook_registry_changed()
    except BaseException:
        # The monitor must not replace pytest's own hook outcome. Any report
        # it cannot inspect is unsupported for cold/replay proof.
        _mark_hook_registry_changed()


def _hook_fingerprint(config):
    lines = []
    root = Path(config.rootpath).resolve()
    try:
        import _pytest
        pytest_root = Path(_pytest.__file__).resolve().parent
    except Exception:
        pytest_root = None
    try:
        paths = {
            key: Path(value).resolve()
            for key, value in sysconfig.get_paths().items()
            if key in ("purelib", "platlib", "stdlib") and value
        }
    except Exception:
        paths = {}
    for hook_name in _HOOKS:
        try:
            impls = getattr(config.hook, hook_name).get_hookimpls()
        except Exception:
            return None, None
        for impl in impls:
            function = impl.function
            module = getattr(function, "__module__", None)
            qualname = getattr(function, "__qualname__", None)
            filename = getattr(getattr(function, "__code__", None), "co_filename", None)
            plugin_name = str(getattr(impl, "plugin_name", ""))
            if not isinstance(module, str) or not isinstance(qualname, str) or not isinstance(filename, str):
                return None, None
            token = "<anon>" if plugin_name.isdigit() else (
                "<path>" if "/" in plugin_name or "\\" in plugin_name else plugin_name
            )
            try:
                resolved = Path(filename).resolve()
                if module in ("assay_mutation_witness_plugin", "assay_liveness_plugin"):
                    relpath = module
                else:
                    try:
                        relpath = resolved.relative_to(root).as_posix()
                    except ValueError:
                        relpath = None
                        if pytest_root is not None:
                            try:
                                relpath = "_pytest/" + resolved.relative_to(pytest_root).as_posix()
                            except ValueError:
                                pass
                        if relpath is None:
                            for label in ("purelib", "platlib", "stdlib"):
                                try:
                                    relpath = label + "/" + resolved.relative_to(paths[label]).as_posix()
                                    break
                                except (KeyError, ValueError):
                                    continue
                        if relpath is None:
                            relpath = resolved.as_posix()
            except (OSError, RuntimeError):
                return None, None
            flags = [
                name for name in ("hookwrapper", "tryfirst", "trylast", "wrapper")
                if getattr(impl, name, False)
            ]
            lines.append("|".join((
                hook_name, token, module + "." + qualname, relpath,
                ",".join(sorted(flags)) or "-",
            )))
    lines.sort()
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(lines)


def _runtime_fingerprint(config):
    try:
        import pytest
        dists = sorted({
            "%s==%s" % (dist.project_name, dist.version)
            for _plugin, dist in config.pluginmanager.list_plugin_distinfo()
        })
        impl = sys.implementation
        doc = {
            "python": sys.version,
            "implementation": "%s-%s" % (
                impl.name, ".".join(str(item) for item in impl.version[:3])
            ),
            "machine": platform.machine(),
            "pytest": pytest.__version__,
            "plugins": dists,
        }
        encoded = json.dumps(
            doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except Exception:
        return None


def _config_fingerprint(config):
    inipath = getattr(config, "inipath", None)
    if inipath is None:
        return None
    try:
        return hashlib.sha256(Path(str(inipath)).read_bytes()).hexdigest()
    except Exception:
        return None


def _write_manifest(session):
    global _MANIFEST_SUPPORTED, _COLLECTION_COUNT, _COLLECTION_SHA256, _COLLECTION_DUPLICATES
    items = [item.nodeid for item in session.items]
    _COLLECTION_COUNT = len(items)
    encoded_ids = []
    try:
        for item in items:
            encoded = item.encode("utf-8")
            if b"\n" in encoded or b"\r" in encoded or len(encoded) > 4096:
                raise ValueError("unsupported node id")
            encoded_ids.append(encoded)
    except (AttributeError, UnicodeEncodeError, ValueError):
        _MANIFEST_SUPPORTED = False
        _COLLECTION_SHA256 = None
        _COLLECTION_DUPLICATES = None
        return
    _MANIFEST_SUPPORTED = True
    _COLLECTION_DUPLICATES = len(items) - len(set(items))
    digest = hashlib.sha256()
    for encoded in encoded_ids:
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)
        digest.update(b",")
    _COLLECTION_SHA256 = digest.hexdigest()
    path = os.environ.get("ASSAY_MUTATION_WITNESS_MANIFEST_FILE")
    if path:
        try:
            Path(path).write_bytes(b"".join(item + b"\n" for item in encoded_ids))
        except Exception:
            pass


def _write(session_exit_status):
    payload = {
        "unsupported": not _STANDARD_LOOP,
        "unsupported_pytest_cov_only": bool(_UNSUPPORTED_PYTEST_COV_ONLY),
        "replay_supported": bool(_REPLAY_SUPPORTED),
        "target_node_id": _TARGET,
        "target_count": _TARGET_COUNT,
        "earlier_failure": bool(_EARLIER_FAILURE),
        "auxiliary_failure": bool(_AUXILIARY_FAILURE),
        "witness_node_id": _WITNESS or _FIRST_CALL_FAILURE,
        "witness_when": "call" if (_WITNESS or _FIRST_CALL_FAILURE) else None,
        "witness_outcome": "failed" if (_WITNESS or _FIRST_CALL_FAILURE) else None,
        "session_exit_status": int(session_exit_status),
        "stopped_at_target": bool(_STOPPED_AT_TARGET),
        "cold_requested": _COLD,
        "stopped_cold": bool(_STOPPED_COLD),
        "collection_error": bool(_COLLECTION_ERROR),
        "manifest_supported": bool(_MANIFEST_SUPPORTED),
        "collection_count": _COLLECTION_COUNT,
        "collection_sha256": _COLLECTION_SHA256,
        "collection_duplicates": _COLLECTION_DUPLICATES,
        "started_count": _STARTED,
        "started_prefix_ok": bool(_PREFIX_OK),
        "failed_call_index": _FAILED_CALL_INDEX,
        "hook_fingerprint_sha256": _HOOK_FINGERPRINT_SHA256,
        "hook_count": _HOOK_COUNT,
        "runtime_fingerprint_sha256": _RUNTIME_FINGERPRINT_SHA256,
        "config_sha256": _CONFIG_SHA256,
        "archive_hook_exception_used": bool(_ARCHIVE_EXCEPTION_USED),
    }
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if _WITNESS_FD is not None:
            if not 0 < len(encoded) <= 16384:
                return
            frame = len(encoded).to_bytes(4, "big") + encoded
            view = memoryview(frame)
            while view:
                try:
                    written = os.write(_WITNESS_FD, view)
                except InterruptedError:
                    continue
                if written <= 0:
                    break
                view = view[written:]
            return
    except Exception:
        return
    finally:
        if _WITNESS_FD is not None:
            try:
                os.close(_WITNESS_FD)
            except OSError:
                pass
    path = os.environ.get("ASSAY_MUTATION_WITNESS_FILE")
    if not path:
        return
    try:
        if len(encoded) <= 16384:
            Path(path).write_bytes(encoded)
    except Exception:
        pass


def pytest_collection_finish(session):
    global _SESSION, _ITEMS, _TARGET, _TARGET_COUNT, _STANDARD_LOOP, _REPLAY_SUPPORTED
    global _COLD, _HOOK_FINGERPRINT_SHA256, _HOOK_COUNT, _RUNTIME_FINGERPRINT_SHA256, _CONFIG_SHA256
    global _UNSUPPORTED_PYTEST_COV_ONLY
    global _HOOK_IMPL_REGISTRY
    _SESSION = session
    assay_hooks_trusted = _assay_hook_registry_matches(session.config)
    _ITEMS = tuple(item.nodeid for item in session.items)
    _TARGET = os.environ.get("ASSAY_MUTATION_WITNESS_TARGET")
    _COLD = os.environ.get("ASSAY_MUTATION_WITNESS_COLD") == "1"
    _TARGET_COUNT = None if _TARGET is None else sum(1 for node_id in _ITEMS if node_id == _TARGET)
    _write_manifest(session)
    config = session.config
    xdist_active = False
    try:
        numprocesses = config.getoption("numprocesses", default=0)
        xdist_active = numprocesses not in (None, 0, "0", False)
    except Exception:
        pass
    try:
        distribution = config.getoption("dist", default="no")
        xdist_active = xdist_active or distribution not in (None, "no")
    except Exception:
        pass
    try:
        standard_loop = _only_builtin_hook_impls(
            config, "pytest_runtestloop", primary=("main", "_pytest.main")
        )
        standard_protocol = _only_builtin_hook_impls(
            config, "pytest_runtest_protocol", primary=("runner", "_pytest.runner")
        )
        required_hooks = [_only_builtin_hook_impls(config, name) for name in _HOOKS]
        _STANDARD_LOOP = bool(
            standard_loop
            and standard_protocol
            and all(required_hooks)
            and assay_hooks_trusted
            and _test_report_constructor_is_trusted()
            and not xdist_active
        )
    except Exception:
        _STANDARD_LOOP = False
    # Preserve the pre-B114 B106 targeted-replay boundary. The stricter
    # complete hook set above is exclusively the cold-witness proof boundary.
    try:
        replay_loop = _only_builtin_hook_impls(
            config, "pytest_runtestloop", primary=("main", "_pytest.main")
        )
        replay_protocol = _only_builtin_hook_impls(
            config, "pytest_runtest_protocol", primary=("runner", "_pytest.runner")
        )
        replay_reports = _only_builtin_hook_impls(
            config, "pytest_runtest_logreport"
        ) and _only_builtin_hook_impls(config, "pytest_collectreport") and _only_builtin_hook_impls(
            config, "pytest_runtest_makereport"
        )
        replay_session_finish = _only_builtin_hook_impls(
            config, "pytest_sessionfinish"
        )
        _REPLAY_SUPPORTED = bool(
            replay_loop
            and replay_protocol
            and replay_reports
            and replay_session_finish
            and assay_hooks_trusted
            and not xdist_active
        )
    except Exception:
        _REPLAY_SUPPORTED = False
    try:
        _UNSUPPORTED_PYTEST_COV_ONLY = bool(
            not _STANDARD_LOOP
            and assay_hooks_trusted
            and _coverage_hook_set_is_only_reviewed(config, xdist_active=xdist_active)
        )
    except Exception:
        _UNSUPPORTED_PYTEST_COV_ONLY = False
    _HOOK_FINGERPRINT_SHA256, _HOOK_COUNT = _hook_fingerprint(config)
    _RUNTIME_FINGERPRINT_SHA256 = _runtime_fingerprint(config)
    _CONFIG_SHA256 = _config_fingerprint(config)
    try:
        _HOOK_IMPL_REGISTRY = {
            name: tuple(
                _hook_impl_signature(impl)
                for impl in getattr(config.hook, name).get_hookimpls()
            )
            for name in _HOOK_REGISTRY_HOOKS
        }
        config.pluginmanager.add_hookcall_monitoring(
            _before_hook_call, _after_hook_call
        )
    except Exception:
        _HOOK_IMPL_REGISTRY = {}
        _mark_hook_registry_changed()


def pytest_collectreport(report):
    global _COLLECTION_ERROR
    if report.failed:
        _COLLECTION_ERROR = True


def pytest_runtest_logstart(nodeid, location):
    global _STARTED, _PREFIX_OK
    _STARTED += 1
    if _STARTED > len(_ITEMS) or _ITEMS[_STARTED - 1] != nodeid:
        _PREFIX_OK = False


def pytest_runtest_logreport(report):
    global _EARLIER_FAILURE, _AUXILIARY_FAILURE, _FIRST_CALL_FAILURE, _WITNESS
    global _STOPPED_AT_TARGET, _STOPPED_COLD, _FAILED_CALL_INDEX
    if report.when != "call":
        if report.outcome == "failed":
            _AUXILIARY_FAILURE = True
            if _COLD and _SESSION is not None:
                _SESSION.shouldfail = "assay stopped at the first failure (cold witness)"
        return
    if report.outcome != "failed":
        return
    if not _FIRST_CALL_FAILURE and _bounded(report.nodeid):
        _FIRST_CALL_FAILURE = report.nodeid
    if _COLD:
        if (
            _STANDARD_LOOP and not _EARLIER_FAILURE and not _AUXILIARY_FAILURE
            and not _COLLECTION_ERROR and _PREFIX_OK and _STARTED > 0
            and _STARTED <= len(_ITEMS) and _ITEMS[_STARTED - 1] == report.nodeid
            and _bounded(report.nodeid)
        ):
            _WITNESS = report.nodeid
            _STOPPED_COLD = True
            _FAILED_CALL_INDEX = _STARTED - 1
        if _SESSION is not None:
            _SESSION.shouldfail = "assay stopped at the first failure (cold witness)"
        return
    if _TARGET is None:
        return
    if report.nodeid != _TARGET:
        _EARLIER_FAILURE = True
        return
    if not _REPLAY_SUPPORTED or _TARGET_COUNT != 1 or _EARLIER_FAILURE or _AUXILIARY_FAILURE or not _bounded(report.nodeid):
        return
    _WITNESS = report.nodeid
    _STOPPED_AT_TARGET = True
    if _SESSION is not None:
        _SESSION.shouldfail = "assay stopped after the current mutation witness"


def pytest_sessionfinish(session, exitstatus):
    _write(exitstatus)'''

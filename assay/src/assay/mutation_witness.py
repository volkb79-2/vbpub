"""Bounded pytest failure-witness capture for provenance-safe mutation reuse."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import platform
import re
import shlex
import sys
import sysconfig
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .records import record
from .r2_command import R2_APPENDED, UnrecognizedCoverageOption, transform_argv

if TYPE_CHECKING:  # pragma: no cover -- annotation-only import; importing at runtime creates a cycle
    from .runner import CommandPlan

MAX_NODE_ID_UTF8_BYTES = 4096
MAX_INTERNAL_RECEIPT_BYTES = 16 * 1024
WITNESS_PLUGIN_MODULE = "assay_mutation_witness_plugin"
WITNESS_FILE_ENV = "ASSAY_MUTATION_WITNESS_FILE"
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
    return manifest_node_ids[started - 1] == node_id


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
    receipt_path: Path,
    target_node_id: str | None,
    cold: bool = False,
    manifest_path: Path | None = None,
) -> "CommandPlan":
    if cold and target_node_id is not None:
        raise ValueError("a cold witness attempt cannot target one node")
    env = dict(plan.env_effective)
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
    """Read the plugin's bounded private receipt; malformed means no witness."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_INTERNAL_RECEIPT_BYTES + 1)
    except OSError:
        return None
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
) -> dict[str, Any] | None:
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
import importlib.metadata as metadata
import json
import os
import platform
import sys
import sysconfig
from pathlib import Path

_HOOKS = (
    "pytest_runtestloop", "pytest_runtest_protocol", "pytest_runtest_logstart",
    "pytest_runtest_logreport", "pytest_runtest_call", "pytest_runtest_makereport",
    "pytest_runtest_setup",
    "pytest_runtest_teardown", "pytest_collectreport",
    "pytest_collection_modifyitems", "pytest_sessionfinish",
)
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


def _bounded(value):
    try:
        encoded = value.encode("utf-8")
        return bool(value) and len(encoded) <= 4096 and b"\n" not in encoded and b"\r" not in encoded
    except Exception:
        return False


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


def _reviewed_external_hook(impl, hook_name, distribution_name):
    expected = _REVIEWED_EXTERNAL_HOOKS.get((distribution_name, hook_name))
    if expected is None:
        return False
    plugin_name, module_name, qualname, expected_flags = expected
    function = impl.function
    if (
        getattr(impl, "plugin_name", None) != plugin_name
        or getattr(function, "__module__", None) != module_name
        or getattr(function, "__qualname__", None) != qualname
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
    code_file = getattr(getattr(function, "__code__", None), "co_filename", None)
    if not isinstance(module_file, str) or not isinstance(code_file, str):
        return False
    try:
        resolved_module_file = Path(module_file).resolve()
        if resolved_module_file != Path(code_file).resolve():
            return False
        distribution = metadata.distribution(distribution_name)
        declared_files = distribution.files
        normalized_name = (distribution.metadata.get("Name") or "").casefold().replace("_", "-")
        wanted_name = distribution_name.casefold().replace("_", "-")
        if normalized_name != wanted_name or declared_files is None:
            return False
        return any(
            Path(distribution.locate_file(item)).resolve() == resolved_module_file
            for item in declared_files
        )
    except (ImportError, OSError, RuntimeError, ValueError, metadata.PackageNotFoundError):
        return False


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
        expected = os.environ.get("ASSAY_MUTATION_WITNESS_PLUGIN_PATH")
        return bool(expected) and resolved_module_file == Path(expected).resolve()
    if module_name == "assay_liveness_plugin":
        expected = os.environ.get("ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH")
        return bool(expected) and resolved_module_file == Path(expected).resolve()
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
    path = os.environ.get("ASSAY_MUTATION_WITNESS_FILE")
    if not path:
        return
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
        if len(encoded) <= 16384:
            Path(path).write_bytes(encoded)
    except Exception:
        pass


def pytest_collection_finish(session):
    global _SESSION, _ITEMS, _TARGET, _TARGET_COUNT, _STANDARD_LOOP, _REPLAY_SUPPORTED
    global _COLD, _HOOK_FINGERPRINT_SHA256, _HOOK_COUNT, _RUNTIME_FINGERPRINT_SHA256, _CONFIG_SHA256
    global _UNSUPPORTED_PYTEST_COV_ONLY
    _SESSION = session
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
            standard_loop and standard_protocol and all(required_hooks) and not xdist_active
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
            and not xdist_active
        )
    except Exception:
        _REPLAY_SUPPORTED = False
    try:
        _UNSUPPORTED_PYTEST_COV_ONLY = bool(
            not _STANDARD_LOOP
            and _coverage_hook_set_is_only_reviewed(config, xdist_active=xdist_active)
        )
    except Exception:
        _UNSUPPORTED_PYTEST_COV_ONLY = False
    _HOOK_FINGERPRINT_SHA256, _HOOK_COUNT = _hook_fingerprint(config)
    _RUNTIME_FINGERPRINT_SHA256 = _runtime_fingerprint(config)
    _CONFIG_SHA256 = _config_fingerprint(config)


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

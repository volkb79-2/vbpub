"""Bounded pytest failure-witness capture for provenance-safe mutation reuse."""

from __future__ import annotations

import configparser
import json
import os
import shlex
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from .runner import CommandPlan

MAX_NODE_ID_UTF8_BYTES = 4096
MAX_INTERNAL_RECEIPT_BYTES = 16 * 1024
WITNESS_PLUGIN_MODULE = "assay_mutation_witness_plugin"
WITNESS_FILE_ENV = "ASSAY_MUTATION_WITNESS_FILE"
WITNESS_TARGET_ENV = "ASSAY_MUTATION_WITNESS_TARGET"
WITNESS_PLUGIN_PATH_ENV = "ASSAY_MUTATION_WITNESS_PLUGIN_PATH"
WITNESS_LIVENESS_PLUGIN_PATH_ENV = "ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH"


@dataclass(frozen=True, kw_only=True)
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
    appended = plan.argv_appended + ("-p", WITNESS_PLUGIN_MODULE)
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
    plan: "CommandPlan", *, receipt_path: Path, target_node_id: str | None
) -> "CommandPlan":
    env = dict(plan.env_effective)
    env[WITNESS_FILE_ENV] = str(receipt_path)
    if target_node_id is not None:
        env[WITNESS_TARGET_ENV] = target_node_id
    else:
        env.pop(WITNESS_TARGET_ENV, None)
    return replace(plan, env_effective=env)


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
    if receipt.get("unsupported") is not False:
        return None
    node_id = receipt.get("witness_node_id")
    if not isinstance(node_id, str) or not _bounded_node_id(node_id):
        return None
    if receipt.get("witness_when") != "call" or receipt.get("witness_outcome") != "failed":
        return None
    if receipt.get("auxiliary_failure") is not False:
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
    if receipt.get("earlier_failure") is not False:
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
        "target_node_id",
        "target_count",
        "earlier_failure",
        "auxiliary_failure",
        "witness_node_id",
        "witness_when",
        "witness_outcome",
        "session_exit_status",
        "stopped_at_target",
    }
)


_PLUGIN_SOURCE = r'''"""Temporary standard-library/pytest plugin written by assay."""
import json
import os
import sys
from pathlib import Path

_SESSION = None
_TARGET = None
_TARGET_COUNT = None
_STANDARD_LOOP = False
_EARLIER_FAILURE = False
_AUXILIARY_FAILURE = False
_FIRST_CALL_FAILURE = None
_WITNESS = None
_STOPPED_AT_TARGET = False


def _bounded(value):
    try:
        return bool(value) and len(value.encode("utf-8")) <= 4096
    except Exception:
        return False


def _only_builtin_hook_impls(config, hook_name, primary=None):
    """Reject third-party lifecycle hooks whose effect on replay is unknown."""
    try:
        impls = getattr(config.hook, hook_name).get_hookimpls()
    except Exception:
        return False

    def trusted(impl):
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
        if not module_name.startswith("_pytest."):
            return False
        try:
            import _pytest
            pytest_root = Path(_pytest.__file__).resolve().parent
            resolved_module_file.relative_to(pytest_root)
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False
        return True

    if any(not trusted(impl) for impl in impls):
        return False
    if primary is None:
        return True
    ordinary = [impl for impl in impls if not impl.wrapper and not impl.hookwrapper]
    return (
        len(ordinary) == 1
        and ordinary[0].plugin_name == primary[0]
        and ordinary[0].function.__module__ == primary[1]
    )


def _write(session_exit_status):
    path = os.environ.get("ASSAY_MUTATION_WITNESS_FILE")
    if not path:
        return
    payload = {
        "unsupported": not _STANDARD_LOOP,
        "target_node_id": _TARGET,
        "target_count": _TARGET_COUNT,
        "earlier_failure": bool(_EARLIER_FAILURE),
        "auxiliary_failure": bool(_AUXILIARY_FAILURE),
        "witness_node_id": _WITNESS or _FIRST_CALL_FAILURE,
        "witness_when": "call" if (_WITNESS or _FIRST_CALL_FAILURE) else None,
        "witness_outcome": "failed" if (_WITNESS or _FIRST_CALL_FAILURE) else None,
        "session_exit_status": int(session_exit_status),
        "stopped_at_target": bool(_STOPPED_AT_TARGET),
    }
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) <= 16384:
            Path(path).write_bytes(encoded)
    except Exception:
        pass


def pytest_collection_finish(session):
    global _SESSION, _TARGET, _TARGET_COUNT, _STANDARD_LOOP
    _SESSION = session
    _TARGET = os.environ.get("ASSAY_MUTATION_WITNESS_TARGET")
    _TARGET_COUNT = None if _TARGET is None else sum(1 for item in session.items if item.nodeid == _TARGET)
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
        ordinary_reports = _only_builtin_hook_impls(
            config, "pytest_runtest_logreport"
        ) and _only_builtin_hook_impls(config, "pytest_collectreport")
        session_finish = _only_builtin_hook_impls(config, "pytest_sessionfinish")
    except Exception:
        standard_loop = False
        standard_protocol = False
        ordinary_reports = False
        session_finish = False
    _STANDARD_LOOP = bool(
        standard_loop
        and standard_protocol
        and ordinary_reports
        and session_finish
        and not xdist_active
    )


def pytest_runtest_logreport(report):
    global _EARLIER_FAILURE, _AUXILIARY_FAILURE, _FIRST_CALL_FAILURE, _WITNESS, _STOPPED_AT_TARGET
    if report.when != "call":
        if report.outcome == "failed":
            _AUXILIARY_FAILURE = True
        return
    if report.outcome != "failed":
        return
    if not _FIRST_CALL_FAILURE and _bounded(report.nodeid):
        _FIRST_CALL_FAILURE = report.nodeid
    if _TARGET is None:
        return
    if report.nodeid != _TARGET:
        _EARLIER_FAILURE = True
        return
    if not _STANDARD_LOOP or _TARGET_COUNT != 1 or _EARLIER_FAILURE or _AUXILIARY_FAILURE or not _bounded(report.nodeid):
        return
    _WITNESS = report.nodeid
    _STOPPED_AT_TARGET = True
    if _SESSION is not None:
        _SESSION.shouldfail = "assay stopped after the current mutation witness"


def pytest_sessionfinish(session, exitstatus):
    _write(exitstatus)


def _reject_non_json_constant(value):
    raise ValueError("non-JSON numeric constant %r" % (value,))
'''

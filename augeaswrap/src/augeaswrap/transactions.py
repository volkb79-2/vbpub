"""Transaction journals, exact pre-images, and explicit restore operations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import threading
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ApplyFailed, Change, Plan, PlanRefused, PostAction, Problem, Request, Result, StalePlan
from . import _fs
from .kinds import kind_spec
from .engine import (
    _Context,
    _check_preconditions,
    _locked,
    _observation_fingerprint,
    _plan_locked,
    _post_action_bindings,
    plan,
    _resource_changes,
    _root_path,
    _run_post_action,
    _unique_resources,
    request_to_toml,
)

_STATE_RELATIVE = Path("var/lib/augeaswrap")
_TXN_RELATIVE = _STATE_RELATIVE / "txn"
_TXN_ID_RE = re.compile(r"^(?P<stamp>[0-9]{8}T[0-9]{6}Z)-(?P<sequence>[0-9a-f]{4})$")
_MAX_TRANSACTIONS = 50
_TXN_ALLOCATION_ATTEMPTS = 100
_RESERVED_RECORD_KEYS = frozenset({"schema", "kind", "address", "data_file"})

def _state_dir(root: Path) -> Path:
    return root / _STATE_RELATIVE


def _txn_root(root: Path) -> Path:
    return root / _TXN_RELATIVE


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(root: Path, path: Path, value: object) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _fs.ensure_directory(root, path.parent)
    _fs.atomic_write(root, path, data, 0o600, temp_prefix=".augeaswrap-json-")


def _read_json(root: Path, path: Path) -> Any:
    _fs.regular_or_missing(root, path)
    return json.loads(path.read_text(encoding="utf-8"))


def _new_txn_id() -> str:
    import secrets

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def _ordered_txn_id(root: Path, candidate: str) -> str:
    """Keep IDs increasing so lexical order is the transaction order.

    The random suffix in ``_new_txn_id`` prevents collisions in normal use.
    Within one timestamp, however, random suffixes cannot establish which
    transaction came later. The retained transaction directories are the
    allocator's only sequence state, so advance a colliding or out-of-order
    candidate past their greatest ID. This also handles a wall clock moving
    backwards without adding a separate state file.
    """

    match = _TXN_ID_RE.fullmatch(candidate)
    if match is None:
        raise ValueError(f"transaction allocator returned invalid id {candidate!r}")
    existing = [entry.name for entry in _transaction_dirs(root)]
    if not existing:
        return candidate
    latest = max(existing)
    if candidate > latest:
        return candidate
    latest_match = _TXN_ID_RE.fullmatch(latest)
    assert latest_match is not None
    stamp = latest_match["stamp"]
    sequence = int(latest_match["sequence"], 16) + 1
    if sequence > 0xFFFF:
        point = datetime.strptime(stamp[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        stamp = (point + timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
        sequence = 0
    return f"{stamp}-{sequence:04x}"


def _ensure_state_dirs(root: Path) -> None:
    # Only the state directory itself is private; var and var/lib keep the default mode.
    _fs.ensure_directory(root, _state_dir(root).parent)
    _fs.ensure_directory(root, _state_dir(root), mode=0o700)
    _fs.ensure_directory(root, _txn_root(root), mode=0o700)


def _allocate_txn(root: Path) -> tuple[str, Path]:
    _ensure_state_dirs(root)
    for _attempt in range(_TXN_ALLOCATION_ATTEMPTS):
        txn_id = _ordered_txn_id(root, _new_txn_id())
        txn_dir = _txn_root(root) / txn_id
        try:
            txn_dir.mkdir(mode=0o700)
        except FileExistsError:
            continue
        try:
            os.chmod(txn_dir, 0o700)
            _fs.ensure_directory(root, txn_dir / "stage", mode=0o700)
        except BaseException:
            shutil.rmtree(txn_dir, ignore_errors=True)
            raise
        return txn_id, txn_dir
    raise OSError(f"could not allocate a unique transaction id after {_TXN_ALLOCATION_ATTEMPTS} attempts")


def _address_hash(address: str) -> str:
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


def _transaction_dirs(root: Path) -> list[Path]:
    try:
        _fs.safe_components(root, _txn_root(root))
        entries = list(_txn_root(root).iterdir())
    except FileNotFoundError:
        return []
    result = []
    for entry in entries:
        if _TXN_ID_RE.fullmatch(entry.name) is None:
            continue
        if entry.is_dir(follow_symlinks=False):
            result.append(entry)
    return sorted(result, key=lambda item: item.name)


def _prune_transactions(root: Path) -> None:
    entries = _transaction_dirs(root)
    for entry in entries[:-_MAX_TRANSACTIONS]:
        _fs.safe_components(root, entry)
        shutil.rmtree(entry)


def _discard_txn(root: Path, txn_dir: Path) -> None:
    _fs.safe_components(root, txn_dir)
    shutil.rmtree(txn_dir, ignore_errors=True)


def _pre_path(txn_dir: Path, address: str) -> Path:
    return txn_dir / "pre" / f"{_address_hash(address)}.json"


def _write_preimage(root: Path, txn_dir: Path, change: Change, observed: object) -> dict[str, Any]:
    pre_dir = txn_dir / "pre"
    _fs.ensure_directory(root, pre_dir, mode=0o700)
    spec = kind_spec(change.kind)
    if spec is None or spec.revert is None:
        # No revert hooks: keep the plain observation as evidence; revert will refuse.
        payload = {"schema": 1, "kind": change.kind, "address": change.address, "observation": observed}
        _write_json(root, _pre_path(txn_dir, change.address), payload)
        return {"kind": change.kind, "address": change.address, "fingerprint": _observation_fingerprint(observed)}
    record = dict(spec.revert.snapshot(change, observed))
    reserved = _RESERVED_RECORD_KEYS.intersection(record)
    if reserved:
        raise ValueError(f"{change.kind} snapshot uses reserved key {sorted(reserved)[0]!r}")
    blob = record.pop("data_b64", None)
    data_name: str | None = None
    if blob is not None:
        data = base64.b64decode(blob, validate=True)
        if hashlib.sha256(data).hexdigest() != record.get("sha256"):
            raise ValueError(f"{change.kind} snapshot sha256 does not match its data_b64")
        data_name = _address_hash(change.address) + ".data"
        _fs.atomic_write(root, pre_dir / data_name, data, 0o600, temp_prefix=".augeaswrap-pre-")
    metadata = {"schema": 1, "kind": change.kind, "address": change.address, "data_file": data_name, **record}
    _write_json(root, _pre_path(txn_dir, change.address), metadata)
    return {key: value for key, value in metadata.items() if key != "data_file"}


def _invalid_preimage(address: str, detail: str) -> PlanRefused:
    return PlanRefused([Problem("revert.preimage.invalid", f"invalid pre-image for {address}: {detail}", path=address)])


def _read_preimage(root: Path, txn_dir: Path, address: str, kind: str) -> dict[str, Any]:
    """Read one stored pre-image record back, with its bytes inline as ``data_b64``."""

    try:
        value = _read_json(root, _pre_path(txn_dir, address))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _fs.PathRefusal) as exc:
        raise _invalid_preimage(address, f"cannot read it ({exc})") from exc
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value.get("schema") != 1
        or value.get("address") != address
        or value.get("kind") != kind
    ):
        raise _invalid_preimage(address, "wrong schema, address or kind")
    record = {key: item for key, item in value.items() if key not in _RESERVED_RECORD_KEYS}
    data_name = value.get("data_file")
    if data_name is not None:
        if not isinstance(data_name, str) or Path(data_name).name != data_name:
            raise _invalid_preimage(address, "invalid data reference")
        try:
            data_path = txn_dir / "pre" / data_name
            _fs.regular_or_missing(root, data_path)
            data = data_path.read_bytes()
        except (OSError, _fs.PathRefusal) as exc:
            raise _invalid_preimage(address, f"cannot read its bytes ({exc})") from exc
        if hashlib.sha256(data).hexdigest() != value.get("sha256"):
            raise _invalid_preimage(address, "digest differs")
        record["data_b64"] = base64.b64encode(data).decode("ascii")
    return record


def _request_digest(request: Request) -> str:
    try:
        encoded = request_to_toml(request).encode("utf-8")
    except ValueError:
        summary = {
            "writer": request.writer,
            "resources": [
                {
                    "kind": resource.kind,
                    "addresses": list(resource.addresses()),
                    "type": type(resource).__name__,
                }
                for resource in request.resources
            ],
        }
        encoded = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_journal(root: Path, txn_dir: Path, value: Mapping[str, Any]) -> None:
    _write_json(root, txn_dir / "journal.json", value)


def _invoke_fail_step(fail_step: object, name: str, index: int) -> None:
    if fail_step is None:
        return
    if callable(fail_step):
        result = fail_step(name)
        if isinstance(result, BaseException):
            raise result
        if result:
            raise RuntimeError(str(result) if isinstance(result, str) else f"injected failure at {name}")
        return
    if (isinstance(fail_step, int) and not isinstance(fail_step, bool) and fail_step == index) or fail_step == name:
        raise RuntimeError(f"injected failure at {name}")


@contextmanager
def _sigterm_as_exit() -> Iterator[None]:
    """Turn SIGTERM into ``SystemExit`` while a transaction runs, so it is journaled.

    Only the main thread can install a handler, and a handler the caller already set
    is left alone.
    """

    if threading.current_thread() is not threading.main_thread() or signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        yield
        return

    def handler(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _execute_plan(
    plan_value: Plan,
    root: Path,
    *,
    txn_type: str = "apply",
    reverts: str | None = None,
    fail_step: object = None,
    prune: bool = True,
) -> Result:
    changes = _resource_changes(plan_value, action="change")
    action_bindings = _post_action_bindings(changes)
    actions = [action for action, _addresses in action_bindings]
    verify_resources = _unique_resources(
        [*changes, *(change for change in plan_value.changes if change.kind == "check")]
    )
    txn_id, txn_dir = _allocate_txn(root)
    stage_dir = txn_dir / "stage"
    pre_dir = txn_dir / "pre"
    ctx = _Context(root, plan_value.request.writer, txn_id, txn_dir, stage_dir, pre_dir)
    staged: dict[str, object] = {}
    try:
        for change in changes:
            staged[change.address] = change.resource.stage(ctx, change)
    except PlanRefused:
        _discard_txn(root, txn_dir)
        raise
    except Exception as exc:
        _discard_txn(root, txn_dir)
        raise PlanRefused([Problem("stage.failed", f"staging failed for {change.address}: {exc}", path=change.address)]) from exc

    pre_images: dict[str, Any] = {}
    try:
        for change in changes:
            observed = plan_value.observed.get(change.address) if isinstance(plan_value.observed, Mapping) else None
            pre_images[change.address] = _write_preimage(root, txn_dir, change, observed)
    except Exception as exc:
        _discard_txn(root, txn_dir)
        raise PlanRefused([Problem("preimage.write.failed", f"cannot save transaction pre-images: {exc}")]) from exc

    step_names = [f"commit {change.address}" for change in changes]
    step_names.extend(f"post-action {action.name}" for action in actions)
    step_names.extend(f"verify {resource.kind} #{index}" for index, resource in enumerate(verify_resources, start=1))
    steps = [{"name": name, "status": "not-run"} for name in step_names]
    journal_value: dict[str, Any] = {
        "schema": 1,
        "id": txn_id,
        "type": txn_type,
        "writer": plan_value.request.writer,
        "root": str(root),
        "request_sha256": _request_digest(plan_value.request),
        "created_at": _timestamp(),
        "status": "applying",
        "reverts": reverts,
        "reverted_by": None,
        "changes": [
            {
                "kind": change.kind,
                "address": change.address,
                "action": change.action,
                "before": change.before,
                "after": change.after,
                "details": list(change.details),
            }
            for change in changes
        ],
        "pre_images": pre_images,
        "post_images": {},
        "unknown_post_images": [],
        "steps": steps,
        "done": [],
        "failed": None,
        "not_run": list(step_names),
        "actions": [
            {
                "name": action.name,
                "argv": list(action.argv),
                "timeout": action.timeout,
                "addresses": list(addresses),
                "status": "not-run",
            }
            for action, addresses in action_bindings
        ],
        "irreversible_effects": [],
    }
    _write_journal(root, txn_dir, journal_value)

    completed: list[str] = []
    action_results: list[str] = []
    step_index = 0
    ctx.expected.update(staged)

    def finish_step(result: str | None = None) -> None:
        nonlocal step_index
        step = steps[step_index]
        step["status"] = "done"
        if result is not None:
            step["result"] = result
        completed.append(step["name"])
        journal_value["done"] = list(completed)
        journal_value["not_run"] = [entry["name"] for entry in steps[step_index + 1 :]]
        _write_journal(root, txn_dir, journal_value)
        step_index += 1

    def fail(exc: BaseException, change: Change | None = None, status: str = "failed") -> ApplyFailed:
        step = steps[step_index]
        if change is not None:
            try:
                current = change.resource.observe(ctx)
                actual = current.get(change.address) if isinstance(current, Mapping) else None
                before = plan_value.observed.get(change.address) if isinstance(plan_value.observed, Mapping) else None
                if actual is None:
                    journal_value["unknown_post_images"].append(change.address)
                elif _observation_fingerprint(actual) != _observation_fingerprint(before):
                    journal_value["post_images"][change.address] = _observation_fingerprint(actual)
            except Exception:
                journal_value["unknown_post_images"].append(change.address)
        step["status"] = "failed"
        step["error"] = str(exc)
        for entry in steps[step_index + 1 :]:
            entry["status"] = "not-run"
        journal_value["status"] = status
        journal_value["failed"] = {"step": step["name"], "reason": str(exc)}
        journal_value["done"] = list(completed)
        journal_value["not_run"] = [entry["name"] for entry in steps[step_index + 1 :]]
        journal_value["finished_at"] = _timestamp()
        _write_journal(root, txn_dir, journal_value)
        if prune:
            _prune_transactions(root)
        return ApplyFailed(txn_id, completed, f"{step['name']}: {exc}", len(steps))

    def run_steps() -> None:
        for change in changes:
            try:
                _invoke_fail_step(fail_step, steps[step_index]["name"], step_index + 1)
                post_image = change.resource.commit(ctx, staged[change.address])
                journal_value["post_images"][change.address] = _observation_fingerprint(post_image)
                finish_step()
            except Exception as exc:
                raise fail(exc, change) from exc

        for action, action_record in zip(actions, journal_value["actions"], strict=True):
            try:
                action_record["status"] = "running"
                _write_journal(root, txn_dir, journal_value)
                _invoke_fail_step(fail_step, steps[step_index]["name"], step_index + 1)
                result = _run_post_action(root, action)
                action_results.append(result)
                action_status = "skipped(root)" if result.endswith("skipped(root)") else "done"
                action_record["status"] = action_status
                action_record["result"] = result
                finish_step(result)
            except Exception as exc:
                action_record["status"] = "failed"
                action_record["error"] = str(exc)
                raise fail(exc) from exc

        for resource in verify_resources:
            try:
                _invoke_fail_step(fail_step, steps[step_index]["name"], step_index + 1)
                problems = resource.verify(ctx)
                if not isinstance(problems, list) or any(not isinstance(problem, Problem) for problem in problems):
                    raise ValueError(f"{resource.kind}.verify must return list[Problem]")
                if problems:
                    raise RuntimeError("; ".join(str(problem) for problem in problems))
                finish_step()
            except Exception as exc:
                raise fail(exc) from exc

    try:
        with _sigterm_as_exit():
            run_steps()
    except Exception:
        raise
    except BaseException as exc:
        # KeyboardInterrupt, SystemExit or SIGTERM after the journal exists: record it,
        # keep what was committed revertible, and let the interruption continue.
        interrupted = RuntimeError(f"interrupted by {type(exc).__name__}")
        fail(interrupted, changes[step_index] if step_index < len(changes) else None, status="interrupted")
        raise

    journal_value["status"] = "done"
    journal_value["not_run"] = []
    journal_value["finished_at"] = _timestamp()
    _write_journal(root, txn_dir, journal_value)
    if prune:
        _prune_transactions(root)
    return Result(txn_id, tuple(change.address for change in changes), tuple(action_results), ())


def apply(
    planned: Plan | Request | Mapping[str, Any] | str | os.PathLike[str],
    root: str | os.PathLike[str] | None = None,
    *,
    dry_run: bool = False,
    fail_step: object = None,
) -> Result | Plan:
    """Apply a reviewed plan after rechecking its observations under a lock."""

    plan_value = planned if isinstance(planned, Plan) else plan(planned, root="/" if root is None else root)
    host_root = _root_path(plan_value.root if root is None else root)
    if host_root != Path(plan_value.root):
        raise StalePlan("apply root differs from the root captured by the plan")
    if dry_run:
        return plan_value
    if not plan_value.has_changes:
        with _locked(host_root, exclusive=False):
            _check_preconditions(host_root, plan_value)
        return Result(None)
    with _locked(host_root, exclusive=True):
        _check_preconditions(host_root, plan_value)
        return _execute_plan(plan_value, host_root, fail_step=fail_step)


def _journal_missing(txn_dir: Path) -> bool:
    """True for a transaction directory whose journal was never written.

    The journal is written before the first commit, so such a directory changed
    nothing; a present but unreadable journal is a different case and stays refused.
    """

    return not os.path.lexists(txn_dir / "journal.json")


def _read_journal(root: Path, txn_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(txn_id, str) or _TXN_ID_RE.fullmatch(txn_id) is None:
        raise PlanRefused([Problem("txn.id.invalid", "transaction id has an invalid format")])
    txn_dir = _txn_root(root) / txn_id
    journal_path = txn_dir / "journal.json"
    try:
        value = _read_json(root, journal_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _fs.PathRefusal) as exc:
        raise PlanRefused([Problem("txn.journal.invalid", f"cannot read transaction {txn_id}: {exc}", path=f"/var/lib/augeaswrap/txn/{txn_id}/journal.json")]) from exc
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value.get("schema") != 1 or value.get("id") != txn_id:
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has an invalid journal")])
    if value.get("root") != str(root):
        raise PlanRefused([Problem("txn.root.mismatch", f"invalid journal root: transaction {txn_id} belongs to a different root")])
    return txn_dir, value


def journal(txn_id: str | None = None, *, root: str | os.PathLike[str] = "/") -> list[dict[str, Any]] | dict[str, Any]:
    """List retained transaction journals or return one journal by id."""

    host_root = _root_path(root)
    with _locked(host_root, exclusive=False):
        if txn_id is not None:
            return _read_journal(host_root, txn_id)[1]
        values = []
        for entry in reversed(_transaction_dirs(host_root)):
            if _journal_missing(entry):
                warnings.warn(
                    f"transaction directory {entry.name} has no journal.json (a crash before its first "
                    "journal write; nothing was committed); skipped",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            values.append(_read_journal(host_root, entry.name)[1])
        return values


def _later_transaction(root: Path, txn_id: str, address: str) -> str | None:
    for entry in _transaction_dirs(root):
        if entry.name <= txn_id or _journal_missing(entry):
            continue
        try:
            _later_dir, value = _read_journal(root, entry.name)
        except PlanRefused as exc:
            raise PlanRefused(
                [Problem("revert.order.unknown", f"cannot establish revert order: later transaction {entry.name} has an unreadable journal")]
            ) from exc
        changes = value.get("changes")
        post_images = value.get("post_images")
        unknown_post_images = value.get("unknown_post_images", [])
        status = value.get("status")
        if (
            not isinstance(changes, list)
            or any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("address"), str)
                or not isinstance(item.get("action"), str)
                or item.get("action") not in {"change", "noop"}
                for item in changes
            )
            or not isinstance(post_images, Mapping)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in post_images.items())
            or not isinstance(unknown_post_images, list)
            or any(not isinstance(address, str) for address in unknown_post_images)
            or not isinstance(status, str)
            or status not in {"applying", "done", "failed", "interrupted"}
        ):
            raise PlanRefused(
                [Problem("revert.order.unknown", f"cannot establish revert order: later transaction {entry.name} has an invalid journal")]
            )
        changed = any(
            isinstance(item, Mapping)
            and item.get("address") == address
            and item.get("action") == "change"
            for item in changes
        )
        if not changed:
            continue
        if address in post_images or address in unknown_post_images or status in {"done", "applying"}:
            return entry.name
    return None


def _prepare_revert(root: Path, txn_id: str) -> tuple[Plan, dict[str, Any], Path]:
    txn_dir, old_journal = _read_journal(root, txn_id)
    if not isinstance(old_journal.get("status"), str) or old_journal.get("status") not in {"done", "failed", "interrupted"}:
        raise PlanRefused([Problem("revert.incomplete", f"transaction {txn_id} is not complete and cannot be reverted")])
    if old_journal.get("reverted_by"):
        raise PlanRefused([Problem("revert.already", f"transaction {txn_id} was already reverted by {old_journal['reverted_by']}")])
    post_images = old_journal.get("post_images")
    if not isinstance(post_images, Mapping):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid post-images")])
    if any(not isinstance(address, str) or not isinstance(fingerprint, str) for address, fingerprint in post_images.items()):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid post-images")])
    unknown_post_images = old_journal.get("unknown_post_images", [])
    if not isinstance(unknown_post_images, list) or any(not isinstance(address, str) for address in unknown_post_images):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid unknown post-images")])
    if unknown_post_images:
        raise PlanRefused(
            [Problem("revert.postimage.unknown", f"cannot revert {txn_id}: post-image could not be observed for {unknown_post_images[0]}", path=unknown_post_images[0])]
        )
    raw_changes = old_journal.get("changes")
    if not isinstance(raw_changes, list) or any(not isinstance(item, Mapping) for item in raw_changes):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid changes")])
    changes_by_address = {
        item.get("address"): item
        for item in raw_changes
        if isinstance(item, Mapping) and isinstance(item.get("address"), str)
    }
    ordered_addresses = [
        item["address"]
        for item in raw_changes
        if isinstance(item.get("address"), str) and item["address"] in post_images
    ]
    if len(ordered_addresses) != len(set(ordered_addresses)) or set(ordered_addresses) != set(post_images):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has post-images without one change per address")])
    raw_actions = old_journal.get("actions")
    if not isinstance(raw_actions, list):
        raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid actions")])
    replay: dict[str, list[PostAction]] = {}
    for item in raw_actions:
        if not isinstance(item, Mapping):
            raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid actions")])
        name, argv, timeout = item.get("name"), item.get("argv"), item.get("timeout")
        addresses, status = item.get("addresses"), item.get("status")
        if (
            not isinstance(name, str)
            or not isinstance(argv, list)
            or not isinstance(addresses, list)
            or any(not isinstance(address, str) for address in addresses)
            or not isinstance(status, str)
            or status not in {"not-run", "done", "failed", "skipped(root)"}
        ):
            raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid actions")])
        try:
            action = PostAction(name, tuple(argv), timeout)
        except (TypeError, ValueError) as exc:
            raise PlanRefused([Problem("txn.journal.invalid", f"transaction {txn_id} has invalid actions: {exc}")]) from exc
        if status in {"done", "failed", "skipped(root)"}:
            for address in addresses:
                if address in post_images:
                    replay.setdefault(address, []).append(action)
    restore_resources = []
    for address in ordered_addresses:
        expected = post_images[address]
        change = changes_by_address.get(address)
        kind = change.get("kind") if isinstance(change, Mapping) else None
        spec = kind_spec(kind) if isinstance(kind, str) else None
        if spec is None or spec.revert is None:
            raise PlanRefused([Problem("revert.unsupported", f"transaction {txn_id} cannot be reverted: {kind} resources declare no revert support ({address})", path=address)])
        later = _later_transaction(root, txn_id, address)
        if later is not None:
            raise PlanRefused([Problem("revert.stale", f"cannot revert {txn_id}: {address} was touched by later transaction {later}", path=address)])
        record = _read_preimage(root, txn_dir, address, kind)
        try:
            resource = spec.revert.restore(address, record, tuple(replay.get(address, ())))
        except ValueError as exc:
            raise _invalid_preimage(address, str(exc)) from exc
        try:
            current = resource.observe(_Context(root, f"augeaswrap revert {txn_id}"))[address]
        except (OSError, _fs.PathRefusal) as exc:
            raise PlanRefused([Problem("revert.stale", f"cannot observe current state for {address}: {exc}", path=address)]) from exc
        if _observation_fingerprint(current) != expected:
            raise PlanRefused([Problem("revert.stale", f"cannot revert {txn_id}: {address} changed outside Augeaswrap", path=address)])
        restore_resources.append(resource)
    request = Request(f"augeaswrap revert {txn_id}", tuple(restore_resources))
    return _plan_locked(request, root), old_journal, txn_dir


def preview_revert(txn_id: str, *, root: str | os.PathLike[str] = "/") -> Plan:
    """Build the guarded restore plan used by the CLI before confirmation."""

    host_root = _root_path(root)
    with _locked(host_root, exclusive=False):
        return _prepare_revert(host_root, txn_id)[0]


def revert(txn_id: str, root: str | os.PathLike[str] = "/") -> Result:
    """Explicitly restore a transaction's pre-images when its post-images are current."""

    host_root = _root_path(root)
    with _locked(host_root, exclusive=True):
        planned, old_journal, old_txn_dir = _prepare_revert(host_root, txn_id)
        if not planned.has_changes:
            return Result(None)
        _check_preconditions(host_root, planned)
        result = _execute_plan(planned, host_root, txn_type="revert", reverts=txn_id, prune=False)
        old_journal["reverted_by"] = result.txn
        old_journal["reverted_at"] = _timestamp()
        _write_journal(host_root, old_txn_dir, old_journal)
        _prune_transactions(host_root)
        return result



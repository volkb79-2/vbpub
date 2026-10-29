"""Schema checked, read-only v27 overview models shared by the CLI and Textual screens."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .errors import Refused, TransportError
from .jobs import JobRegistry


_MAX_ROWS = 10_000
_JOBS_PAGE = 200
_LOCAL_JOBS_PAGE = 200


def _mapping(value: Any, method: str, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Refused("schema", f"{method} returned an invalid {label}: expected an object")
    return value


def _rows(value: Any, method: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise Refused("schema", f"{method} returned an invalid row list")
    return value


def _required(row: Mapping[str, Any], method: str, field: str, check: Any) -> Any:
    value = row.get(field)
    if field not in row or not check(value):
        raise Refused("schema", f"{method} returned a row with an invalid {field!r} field")
    return value


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _is_nullable_text(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _valid_pool_scan(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    if set(value) != {"function", "state", "start_time", "end_time", "percentage", "bytes_to_process",
                      "bytes_processed", "bytes_issued", "pause", "errors", "total_secs_left"}:
        return False
    return (
        value.get("function") in ("SCRUB", "RESILVER")
        and value.get("state") in ("SCANNING", "FINISHED", "CANCELED")
        and _is_text(value.get("start_time"))
        and _is_nullable_text(value.get("end_time"))
        and _is_number(value.get("percentage")) and 0 <= value["percentage"] <= 100
        and all(_is_integer(value.get(field)) and value[field] >= 0
                for field in ("bytes_to_process", "bytes_processed", "bytes_issued", "errors"))
        and _is_nullable_text(value.get("pause"))
        and (value.get("total_secs_left") is None
             or (_is_integer(value.get("total_secs_left")) and value["total_secs_left"] >= 0))
    )


def _valid_aggregations(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or set(value) != {"min", "mean", "max"}:
        return False
    return all(isinstance(series, Mapping)
               and all(isinstance(label, str) and _is_number(number) for label, number in series.items())
               for series in value.values())


def _valid_reporting_points(value: Any) -> bool:
    """Accept object samples from the API schema and ordered numeric rows observed on v27 RC.1."""
    if not isinstance(value, list):
        return False
    for point in value:
        if isinstance(point, Mapping):
            continue
        if (not isinstance(point, list) or len(point) < 2
                or any(not _is_number(sample) for sample in point)):
            return False
    return True


class ReadViews:
    """Expose bounded v27 reads without turning denied, missing, or malformed data into healthy values."""

    def __init__(self, rpc: Any, host_name: str, job_registry: JobRegistry):
        self.rpc = rpc
        self.host_name = host_name
        self.job_registry = job_registry

    def _safe_error(self, exc: BaseException) -> str:
        return self.job_registry.sanitize_error(exc)

    def dashboard(self) -> dict[str, Any]:
        method = "system.info"
        raw = _mapping(self.rpc.call(method), method, "system information")
        hostname = _required(raw, method, "hostname", _is_text)
        physmem = _required(raw, method, "physmem", lambda v: _is_integer(v) and v >= 0)
        model = _required(raw, method, "model", lambda v: v is None or _is_text(v))
        cores = _required(raw, method, "cores", lambda v: _is_integer(v) and v >= 0)
        physical_cores = _required(raw, method, "physical_cores", lambda v: _is_integer(v) and v >= 0)
        loadavg = raw.get("loadavg")
        uptime = _required(raw, method, "uptime", _is_text)
        uptime_seconds = _required(raw, method, "uptime_seconds", lambda v: _is_number(v) and v >= 0)
        if (not isinstance(loadavg, list) or len(loadavg) != 3
                or any(not _is_number(value) or value < 0 for value in loadavg)):
            raise Refused("schema", f"{method} returned an invalid 'loadavg' field")

        result: dict[str, Any] = {
            "host": {"name": self.host_name, "hostname": hostname,
                     "version": raw.get("version") if _is_text(raw.get("version")) else None},
            "physmem": physmem,
            "model": model,
            "cores": cores,
            "physical_cores": physical_cores,
            "loadavg": list(loadavg),
            "uptime": uptime,
            "uptime_seconds": uptime_seconds,
        }
        graphs = [{"name": "cpu", "identifier": None}, {"name": "memory", "identifier": None}]
        query = {"unit": "HOUR", "page": 1, "aggregate": True}
        try:
            raw_graphs = self.rpc.call("reporting.get_data", graphs, query)
        except (TransportError, Refused) as exc:
            error = self._safe_error(exc)
            result["cpu"] = {"status": "unavailable", "error": error}
            result["memory"] = {"status": "unavailable", "error": error}
            return result
        rows = _rows(raw_graphs, "reporting.get_data")
        by_name: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            name = _required(row, "reporting.get_data", "name", _is_text)
            if name not in {"cpu", "memory"}:
                raise Refused("schema", f"reporting.get_data returned an unrequested graph {name!r}")
            _required(row, "reporting.get_data", "identifier", lambda v: v is None or _is_text(v))
            if name in by_name:
                raise Refused("schema", f"reporting.get_data returned duplicate {name!r} graphs")
            _required(row, "reporting.get_data", "data",
                      _valid_reporting_points)
            _required(row, "reporting.get_data", "aggregations", _valid_aggregations)
            _required(row, "reporting.get_data", "start", lambda v: _is_integer(v) and v > 0)
            _required(row, "reporting.get_data", "end", lambda v: _is_integer(v) and v > 0)
            _required(row, "reporting.get_data", "legend",
                      lambda v: isinstance(v, list) and all(_is_text(label) for label in v))
            by_name[name] = row
        for name in ("cpu", "memory"):
            row = by_name.get(name)
            if row is None:
                result[name] = {"status": "unavailable", "error": "the v27 response did not include this graph"}
            else:
                result[name] = {"status": "available", "data": row["data"],
                                "aggregations": row["aggregations"], "start": row["start"],
                                "end": row["end"], "legend": row["legend"]}
        return result

    def alerts(self) -> list[dict[str, Any]]:
        method = "alert.list"
        rows = _rows(self.rpc.call(method), method)
        required = {
            "id": lambda v: _is_text(v) or _is_integer(v),
            "uuid": _is_text, "source": _is_text, "klass": _is_text,
            "args": lambda v: isinstance(v, Mapping), "node": _is_text, "key": _is_text,
            "datetime": _is_text, "last_occurrence": _is_text, "dismissed": lambda v: isinstance(v, bool),
            "mail": lambda v: isinstance(v, Mapping), "text": _is_text, "level": _is_text,
            "formatted": _is_nullable_text, "one_shot": lambda v: isinstance(v, bool),
        }
        result = []
        for row in rows:
            clean = {name: _required(row, method, name, check) for name, check in required.items()}
            result.append(self.job_registry.sanitize_value(clean))
        return result

    def jobs(self, *, limit: int = _JOBS_PAGE, offset: int = 0) -> dict[str, Any]:
        if not _is_integer(limit) or not 1 <= limit <= _MAX_ROWS:
            raise Refused("paging", "jobs limit must be between 1 and 10000")
        if not _is_integer(offset) or offset < 0:
            raise Refused("paging", "jobs offset must be a non-negative integer")
        method = "core.get_jobs"
        options = {"limit": limit, "offset": offset, "order_by": ["-id"],
                   "select": ["id", "method", "state", "progress", "logs_excerpt", "error"]}
        rows = _rows(self.rpc.call(method, [], options), method)
        selected: list[dict[str, Any]] = []
        for row in rows:
            ident = _required(row, method, "id", _is_integer)
            method_name = _required(row, method, "method", lambda v: _is_text(v) and bool(v))
            state = _required(row, method, "state", lambda v: isinstance(v, str) and v in
                               {"WAITING", "RUNNING", "SUCCESS", "FAILED", "ABORTED"})
            progress = row.get("progress")
            if progress is not None and not isinstance(progress, Mapping):
                raise Refused("schema", f"{method} returned an invalid 'progress' field")
            if isinstance(progress, Mapping):
                percent = progress.get("percent")
                description = progress.get("description")
                if percent is not None and (not _is_number(percent) or not 0 <= percent <= 100):
                    raise Refused("schema", f"{method} returned an invalid progress percentage")
                if description is not None and not _is_text(description):
                    raise Refused("schema", f"{method} returned an invalid progress description")
            logs = row.get("logs_excerpt")
            error = row.get("error")
            if not _is_nullable_text(logs) or not _is_nullable_text(error):
                raise Refused("schema", f"{method} returned invalid log or error text")
            safe_row = self.job_registry.sanitize_value(dict(row))
            selected.append({"id": ident, "method": method_name, "state": state,
                             "progress": dict(safe_row["progress"]) if progress is not None else None,
                             "logs_excerpt": safe_row.get("logs_excerpt"), "error": safe_row.get("error")})

        scope = "current session only"
        role_status = "not verified"
        role_error = None
        try:
            me = _mapping(self.rpc.call("auth.me"), "auth.me", "current user")
            privilege = me.get("privilege")
            roles = privilege.get("roles") if isinstance(privilege, Mapping) else None
            if isinstance(roles, list) and all(isinstance(role, str) for role in roles):
                role_status = "verified"
                if "FULL_ADMIN" in roles:
                    scope = "host-wide"
        except (TransportError, Refused) as exc:
            # A failed role probe cannot promote a session to FULL_ADMIN. The RPC result itself remains scoped.
            role_error = self._safe_error(exc)

        local = self.job_registry.snapshot()
        remote_by_id = {(str(row["id"]), row["method"]): row for row in selected}
        local_only = []
        for item in local:
            pair = (str(item["job_id"]), item["method"])
            remote = remote_by_id.get(pair)
            if remote is not None:
                remote["local"] = item
            else:
                local_only.append(item)
        local_only.sort(key=lambda item: item["created_at"], reverse=True)
        local_jobs = local_only[:_LOCAL_JOBS_PAGE] if offset == 0 else []
        return {"scope": scope, "role_status": role_status, "role_error": role_error, "rows": selected,
                "local_jobs": local_jobs,
                "local_jobs_limit": _LOCAL_JOBS_PAGE,
                "local_jobs_possibly_more": offset == 0 and len(local_only) > _LOCAL_JOBS_PAGE,
                "limit": limit, "offset": offset, "possibly_more": len(rows) == limit}

    def services(self) -> dict[str, Any]:
        method = "service.query"
        options = {"extra": {"include_state": True},
                   "select": ["id", "service", "enable", "state", "pids"],
                   "limit": _MAX_ROWS, "offset": 0, "order_by": ["service"]}
        rows = _rows(self.rpc.call(method, [], options), method)
        result = []
        for row in rows:
            ident = _required(row, method, "id", _is_integer)
            name = _required(row, method, "service", _is_text)
            enabled = _required(row, method, "enable", lambda v: isinstance(v, bool))
            state = _required(row, method, "state", _is_text)
            pids = _required(row, method, "pids", lambda v: isinstance(v, list) and all(_is_integer(i) for i in v))
            result.append({"id": ident, "service": name, "enable": enabled, "state": state, "pids": list(pids)})
        return {"rows": result, "limit": _MAX_ROWS, "possibly_more": len(rows) == _MAX_ROWS}

    def storage_health(self) -> dict[str, Any]:
        pool_options = {"limit": _MAX_ROWS, "offset": 0,
                        "select": ["id", "name", "healthy", "warning", "status", "status_code", "scan", "expand"]}
        disk_options = {"extra": {"pools": True}, "limit": _MAX_ROWS, "offset": 0,
                        "select": ["name", "size", "model", "serial", "devname", "pool", "type"]}
        pool_rows = _rows(self.rpc.call("pool.query", [], pool_options), "pool.query")
        disks = _rows(self.rpc.call("disk.query", [], disk_options), "disk.query")
        pools_result = []
        for row in pool_rows:
            ident = _required(row, "pool.query", "id", _is_integer)
            name = _required(row, "pool.query", "name", _is_text)
            healthy = _required(row, "pool.query", "healthy", lambda v: isinstance(v, bool))
            warning = _required(row, "pool.query", "warning", lambda v: isinstance(v, bool))
            status = _required(row, "pool.query", "status", _is_text)
            status_code = _required(row, "pool.query", "status_code", _is_nullable_text)
            scan = _required(row, "pool.query", "scan", _valid_pool_scan)
            expand = _required(row, "pool.query", "expand", lambda v: v is None or isinstance(v, Mapping))
            pools_result.append({"id": ident, "name": name, "healthy": healthy, "warning": warning,
                                 "status": status, "status_code": status_code,
                                 "scan": scan, "expand": expand})
        disks_result = []
        for row in disks:
            name = _required(row, "disk.query", "name", _is_text)
            size = row.get("size")
            if size is not None and (not _is_integer(size) or size < 0):
                raise Refused("schema", "disk.query returned an invalid 'size' field")
            for key in ("model", "serial", "devname", "pool", "type"):
                if not _is_nullable_text(row.get(key)):
                    raise Refused("schema", f"disk.query returned an invalid {key!r} field")
            disks_result.append({key: row.get(key) for key in
                                 ("name", "size", "model", "serial", "devname", "pool", "type")})
        return {"pools": pools_result, "disks": disks_result,
                "pool_limit": _MAX_ROWS, "disk_limit": _MAX_ROWS,
                "pools_possibly_more": len(pool_rows) == _MAX_ROWS,
                "disks_possibly_more": len(disks) == _MAX_ROWS}

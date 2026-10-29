from __future__ import annotations

import pytest

from tnt.errors import Refused, TransportError
from tnt.jobs import JobRegistry
from tnt.readviews import ReadViews


INFO = {
    "version": "TrueNAS-27.0.0-RC.1", "hostname": "lab", "physmem": 17179869184,
    "model": "Example CPU", "cores": 8, "physical_cores": 4,
    "loadavg": [0.2, 0.3, 0.4], "uptime": "2 days", "uptime_seconds": 172800,
}

REPORTING = [
    {"name": name, "identifier": None, "data": [],
     "aggregations": {"min": {"value": 10.0}, "mean": {"value": mean}, "max": {"value": 30.0}},
     "start": 100, "end": 200, "legend": ["value"]}
    for name, mean in (("cpu", 20.0), ("memory", 50.0))
]


class Rpc:
    def __init__(self, **results):
        self.results = results
        self.calls = []

    def call(self, method, *params, job=False, callback=None):
        self.calls.append((method, params))
        result = self.results.get(method, [])
        if isinstance(result, Exception):
            raise result
        return result


def _alert():
    return {"id": "alert-1", "uuid": "uuid-1", "source": "pool", "klass": "PoolStatus",
            "args": {}, "node": "lab", "key": "pool-status", "datetime": "2026-10-28T10:00:00Z",
            "last_occurrence": "2026-10-28T10:00:00Z", "dismissed": False, "mail": {},
            "text": "Pool tank is degraded", "level": "ERROR", "formatted": None, "one_shot": False}


def test_dashboard_validates_system_info_and_uses_bounded_reporting_graphs():
    rpc = Rpc(**{"system.info": INFO, "reporting.get_data": REPORTING})
    result = ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()
    assert result["host"]["hostname"] == "lab"
    assert result["loadavg"] == [0.2, 0.3, 0.4]
    assert result["cpu"]["status"] == "available"
    assert result["memory"]["status"] == "available"
    assert rpc.calls == [
        ("system.info", ()),
        ("reporting.get_data", ([{"name": "cpu", "identifier": None},
                                  {"name": "memory", "identifier": None}],
                                 {"unit": "HOUR", "page": 1, "aggregate": True})),
    ]


def test_dashboard_marks_reporting_role_or_method_failure_unavailable_without_zeroes():
    rpc = Rpc(**{"system.info": INFO,
                 "reporting.get_data": TransportError("call", "REPORTING_READ is required")})
    result = ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()
    assert result["cpu"] == {"status": "unavailable", "error": "REPORTING_READ is required"}
    assert result["memory"] == result["cpu"]


def test_dashboard_schema_failure_is_not_reported_as_a_zero_or_empty_host():
    rpc = Rpc(**{"system.info": {"hostname": "lab"}, "reporting.get_data": REPORTING})
    with pytest.raises(Refused, match="system.info"):
        ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()


def test_dashboard_rejects_bad_load_average_and_non_object_system_result():
    malformed = {**INFO, "loadavg": [0.2, -0.1, 0.4]}
    with pytest.raises(Refused, match="loadavg"):
        ReadViews(Rpc(**{"system.info": malformed}), "lab", JobRegistry("lab")).dashboard()
    with pytest.raises(Refused, match="expected an object"):
        ReadViews(Rpc(**{"system.info": []}), "lab", JobRegistry("lab")).dashboard()


def test_reporting_rejects_invalid_lists_duplicate_and_unrequested_graphs_and_marks_missing_graphs():
    with pytest.raises(Refused, match="invalid row list"):
        ReadViews(Rpc(**{"system.info": INFO, "reporting.get_data": {}}), "lab", JobRegistry("lab")).dashboard()

    with pytest.raises(Refused, match="unrequested graph"):
        ReadViews(Rpc(**{"system.info": INFO,
                         "reporting.get_data": [{**REPORTING[0], "name": "disk"}]}),
                  "lab", JobRegistry("lab")).dashboard()

    with pytest.raises(Refused, match="duplicate"):
        ReadViews(Rpc(**{"system.info": INFO,
                         "reporting.get_data": [REPORTING[0], REPORTING[0]]}),
                  "lab", JobRegistry("lab")).dashboard()

    result = ReadViews(Rpc(**{"system.info": INFO, "reporting.get_data": REPORTING[:1]}),
                       "lab", JobRegistry("lab")).dashboard()
    assert result["cpu"]["status"] == "available"
    assert result["memory"]["status"] == "unavailable"


def test_reporting_allows_a_null_aggregate_object_as_unavailable():
    rows = [{**REPORTING[0], "aggregations": None}, REPORTING[1]]
    rpc = Rpc(**{"system.info": INFO, "reporting.get_data": rows})
    result = ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()
    assert result["cpu"]["status"] == "available"
    assert result["cpu"]["aggregations"] is None


def test_reporting_accepts_numeric_rows_and_rejects_malformed_rows():
    numeric_rows = [{**REPORTING[0], "data": [[100, 1.5, 2]]}, REPORTING[1]]
    result = ReadViews(Rpc(**{"system.info": INFO, "reporting.get_data": numeric_rows}),
                       "lab", JobRegistry("lab")).dashboard()
    assert result["cpu"]["data"] == [[100, 1.5, 2]]

    for malformed in ([[]], [[100]], [[100, True]], [[100, "cpu"]], ["bad"]):
        rows = [{**REPORTING[0], "data": malformed}, REPORTING[1]]
        with pytest.raises(Refused, match="invalid 'data'"):
            ReadViews(Rpc(**{"system.info": INFO, "reporting.get_data": rows}),
                      "lab", JobRegistry("lab")).dashboard()


def test_reporting_rejects_wrong_aggregate_series_and_invalid_time_window():
    wrong_aggregate = [{**REPORTING[0],
                        "aggregations": {"min": "10", "mean": {"value": 20}, "max": {"value": 30}}}, REPORTING[1]]
    rpc = Rpc(**{"system.info": INFO, "reporting.get_data": wrong_aggregate})
    with pytest.raises(Refused, match="reporting.get_data"):
        ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()

    wrong_window = [{**REPORTING[0], "start": "100"}, REPORTING[1]]
    rpc = Rpc(**{"system.info": INFO, "reporting.get_data": wrong_window})
    with pytest.raises(Refused, match="reporting.get_data"):
        ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()


def test_alerts_validate_v27_rows_and_preserve_rbac_errors():
    rpc = Rpc(**{"alert.list": [_alert()]})
    rows = ReadViews(rpc, "lab", JobRegistry("lab")).alerts()
    assert rows[0]["uuid"] == "uuid-1" and rows[0]["dismissed"] is False
    assert rpc.calls == [("alert.list", ())]

    denied = Rpc(**{"alert.list": TransportError("call", "ALERT_LIST_READ required")})
    with pytest.raises(TransportError, match="ALERT_LIST_READ"):
        ReadViews(denied, "lab", JobRegistry("lab")).alerts()


def test_jobs_view_bounds_query_and_never_claims_host_wide_scope_without_full_admin():
    rpc = Rpc(**{
        "core.get_jobs": [{"id": 4, "method": "app.start", "state": "RUNNING",
                           "progress": {"percent": 25, "description": "Starting"},
                           "logs_excerpt": None, "error": None}],
        "auth.me": {"privilege": {"roles": ["READONLY_ADMIN"]}},
    })
    result = ReadViews(rpc, "lab", JobRegistry("lab")).jobs()
    assert result["scope"] == "current session only"
    assert result["rows"][0]["id"] == 4
    method, params = rpc.calls[0]
    assert method == "core.get_jobs"
    assert params[1]["limit"] == 200
    assert params[1]["offset"] == 0
    assert "credentials" not in params[1]["select"] and "raw_result" not in params[1].get("extra", {})


@pytest.mark.parametrize("kwargs", [{"limit": True}, {"limit": 0}, {"limit": 10001},
                                     {"offset": True}, {"offset": -1}])
def test_jobs_reject_invalid_page_arguments(kwargs):
    with pytest.raises(Refused, match="jobs (limit|offset)"):
        ReadViews(Rpc(), "lab", JobRegistry("lab")).jobs(**kwargs)


def test_jobs_remote_page_stays_bounded_and_local_ledger_is_a_separate_first_page_supplement():
    registry = JobRegistry("lab")
    registry.record_remote(job_id=8, method="app.start", state="RUNNING", generation=3)
    registry.record_remote(job_id=7, method="app.stop", state="SUCCESS", generation=2)
    rpc = Rpc(**{"core.get_jobs": [], "auth.me": {"privilege": {"roles": ["READONLY_ADMIN"]}}})
    views = ReadViews(rpc, "lab", registry)

    first = views.jobs(limit=1, offset=0)
    assert first["rows"] == []
    newest_first = sorted(registry.snapshot(), key=lambda job: job["created_at"], reverse=True)
    assert [job["job_id"] for job in first["local_jobs"]] == [job["job_id"] for job in newest_first]
    assert first["local_jobs_possibly_more"] is False

    later = views.jobs(limit=1, offset=1)
    assert later["rows"] == []
    assert later["local_jobs"] == []


def test_jobs_reject_malformed_rows_and_job_query_permission_errors_are_not_empty():
    malformed = Rpc(**{"core.get_jobs": [{"id": "bad"}], "auth.me": {"privilege": {"roles": []}}})
    with pytest.raises(Refused, match="core.get_jobs"):
        ReadViews(malformed, "lab", JobRegistry("lab")).jobs()

    denied = Rpc(**{"core.get_jobs": TransportError("call", "job query denied")})
    with pytest.raises(TransportError, match="job query denied"):
        ReadViews(denied, "lab", JobRegistry("lab")).jobs()


def test_jobs_page_and_role_probe_failure_never_claim_host_wide_scope():
    rpc = Rpc(**{"core.get_jobs": [{"id": 5, "method": "app.start", "state": "RUNNING",
                                     "progress": None, "logs_excerpt": None, "error": None}],
                 "auth.me": TransportError("call", "auth.me unavailable")})
    result = ReadViews(rpc, "lab", JobRegistry("lab")).jobs(limit=1, offset=200)
    assert result["scope"] == "current session only"
    assert result["role_status"] == "not verified"
    assert result["role_error"] == "auth.me unavailable"
    assert result["possibly_more"] is True and result["offset"] == 200
    assert rpc.calls[0][1][1]["offset"] == 200


def test_job_progress_and_log_fields_are_schema_checked_and_sanitized():
    malformed = Rpc(**{"core.get_jobs": [{"id": 5, "method": "app.start", "state": "RUNNING",
                                           "progress": {"percent": 101}, "logs_excerpt": None, "error": None}]})
    with pytest.raises(Refused, match="progress percentage"):
        ReadViews(malformed, "lab", JobRegistry("lab")).jobs()

    rpc = Rpc(**{"core.get_jobs": [{"id": 5, "method": "app.start", "state": "RUNNING",
                                     "progress": {"percent": 25, "description": "pw-value"},
                                     "logs_excerpt": "pw-value", "error": "pw-value"}],
                 "auth.me": {"privilege": {"roles": ["FULL_ADMIN"]}}})
    registry = JobRegistry("lab", scrub=lambda value: value.replace("pw-value", "<redacted>"))
    result = ReadViews(rpc, "lab", registry).jobs()
    assert result["scope"] == "host-wide"
    row = result["rows"][0]
    assert row["logs_excerpt"] == "<redacted>" and row["error"] == "<redacted>"
    assert row["progress"]["description"] == "<redacted>"


@pytest.mark.parametrize(("extra", "message"), [
    ({"progress": []}, "progress"),
    ({"progress": {"description": 12}}, "description"),
    ({"logs_excerpt": 12}, "log or error"),
])
def test_jobs_reject_malformed_optional_progress_and_log_fields(extra, message):
    row = {"id": 8, "method": "app.start", "state": "RUNNING",
           "progress": None, "logs_excerpt": None, "error": None}
    row.update(extra)
    rpc = Rpc(**{"core.get_jobs": [row]})
    with pytest.raises(Refused, match=message):
        ReadViews(rpc, "lab", JobRegistry("lab")).jobs()


def test_jobs_annotate_a_remote_row_when_the_local_ledger_has_the_same_job():
    registry = JobRegistry("lab")
    registry.record_remote(job_id=4, method="app.start", state="RUNNING", generation=6)
    rpc = Rpc(**{"core.get_jobs": [{"id": 4, "method": "app.start", "state": "RUNNING",
                                     "progress": None, "logs_excerpt": None, "error": None}],
                 "auth.me": {"privilege": {"roles": []}}})
    result = ReadViews(rpc, "lab", registry).jobs()
    assert result["rows"][0]["local"]["generation"] == 6
    assert result["local_jobs"] == []


def test_services_are_schema_checked_and_read_only():
    rpc = Rpc(**{"service.query": [{"id": 3, "service": "cifs", "enable": True,
                                    "state": "RUNNING", "pids": [12]}]})
    result = ReadViews(rpc, "lab", JobRegistry("lab")).services()
    assert result["rows"] == [{"id": 3, "service": "cifs", "enable": True, "state": "RUNNING", "pids": [12]}]
    assert result["possibly_more"] is False
    assert rpc.calls == [("service.query", ([], {"extra": {"include_state": True},
                                                "select": ["id", "service", "enable", "state", "pids"],
                                                "limit": 10000, "offset": 0, "order_by": ["service"]}))]
    assert all(method == "service.query" for method, _ in rpc.calls)


def test_storage_health_uses_pool_and_disk_read_contract_and_does_not_invent_health():
    rpc = Rpc(**{
        "pool.query": [{"id": 1, "name": "tank", "status": "DEGRADED", "healthy": False,
                        "warning": True, "status_code": "DEGRADED",
                        "scan": {"function": "SCRUB", "state": "FINISHED", "start_time": "2026-10-28T10:00:00Z",
                                 "end_time": "2026-10-28T11:00:00Z", "percentage": 100,
                                 "bytes_to_process": 500, "bytes_processed": 500, "bytes_issued": 500,
                                 "pause": None, "errors": 0, "total_secs_left": None},
                        "expand": {"state": "FINISHED"}}],
        "disk.query": [{"name": "sdb", "size": None, "model": None, "serial": "DATA1",
                        "devname": "/dev/sdb", "pool": "tank", "type": None}],
    })
    result = ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()
    assert result["pools"][0]["healthy"] is False
    assert result["pools"][0]["scan"]["function"] == "SCRUB"
    assert result["disks"][0]["size"] is None
    assert rpc.calls[0][0] == "pool.query" and rpc.calls[1][0] == "disk.query"
    assert rpc.calls[1][1][1]["extra"] == {"pools": True}
    assert "passwords" not in rpc.calls[1][1][1]["extra"]


def test_storage_health_schema_and_role_failures_do_not_look_healthy_or_empty():
    malformed = Rpc(**{"pool.query": [{"id": 1, "name": "tank"}], "disk.query": []})
    with pytest.raises(Refused, match="pool.query"):
        ReadViews(malformed, "lab", JobRegistry("lab")).storage_health()

    denied = Rpc(**{"pool.query": TransportError("call", "POOL_READ required")})
    with pytest.raises(TransportError, match="POOL_READ"):
        ReadViews(denied, "lab", JobRegistry("lab")).storage_health()


def test_storage_health_rejects_non_boolean_health_and_malformed_pool_scan():
    pool = {"id": 1, "name": "tank", "status": "ONLINE", "status_code": None,
            "healthy": None, "warning": False, "scan": None, "expand": None}
    rpc = Rpc(**{"pool.query": [pool], "disk.query": []})
    with pytest.raises(Refused, match="healthy"):
        ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()

    pool["healthy"] = True
    pool["scan"] = {"function": "SNAPSHOT"}
    rpc = Rpc(**{"pool.query": [pool], "disk.query": []})
    with pytest.raises(Refused, match="scan"):
        ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()


def test_storage_health_rejects_non_object_scan_bad_aggregates_and_invalid_disk_fields():
    pool = {"id": 1, "name": "tank", "status": "ONLINE", "status_code": None,
            "healthy": True, "warning": False, "scan": "busy", "expand": None}
    rpc = Rpc(**{"pool.query": [pool], "disk.query": []})
    with pytest.raises(Refused, match="scan"):
        ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()

    invalid_aggregates = [{**REPORTING[0], "aggregations": {"min": {}, "mean": {}, "max": {}, "sum": {}}},
                          REPORTING[1]]
    rpc = Rpc(**{"system.info": INFO, "reporting.get_data": invalid_aggregates})
    with pytest.raises(Refused, match="aggregations"):
        ReadViews(rpc, "lab", JobRegistry("lab")).dashboard()

    valid_pool = {"id": 1, "name": "tank", "status": "ONLINE", "status_code": None,
                  "healthy": True, "warning": False, "scan": None, "expand": None}
    rpc = Rpc(**{"pool.query": [valid_pool], "disk.query": [{"name": "sdb", "size": -1}]})
    with pytest.raises(Refused, match="size"):
        ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()

    rpc = Rpc(**{"pool.query": [valid_pool], "disk.query": [{"name": "sdb", "size": None, "pool": 3}]})
    with pytest.raises(Refused, match="pool"):
        ReadViews(rpc, "lab", JobRegistry("lab")).storage_health()

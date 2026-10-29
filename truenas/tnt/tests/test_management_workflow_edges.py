import pytest

from tnt.errors import Refused, TransportError
from tnt.session import open_session


@pytest.fixture
def workflows(server):
    session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
    try:
        yield session.workflows
    finally:
        session.close()


@pytest.mark.parametrize("row", [
    {"id": 7, "name": "App", "state": "RUNNING", "upgrade_available": False},
    {"id": "app", "name": "", "state": "RUNNING", "upgrade_available": False},
    {"id": "app", "name": "App", "state": 1, "upgrade_available": False},
    {"id": "app", "name": "App", "state": "RUNNING", "upgrade_available": 1},
])
def test_app_query_refuses_records_missing_v27_identity_or_upgrade_state(workflows, server, row):
    server.apps = {"malformed": row}
    with pytest.raises(Refused, match="missing its v27 ID"):
        workflows.apps()


def test_app_lifecycle_lookup_and_delete_choice_validation(workflows):
    with pytest.raises(Refused, match="installed app ID is required"):
        workflows.app(" ")
    with pytest.raises(Refused, match="exactly one installed app"):
        workflows.app("missing")
    with pytest.raises(Refused, match="unsupported app lifecycle"):
        workflows.app_lifecycle_action("example-app", "upgrade")
    with pytest.raises(Refused, match="true or false"):
        workflows.app_delete_action("example-app", delete_data_volumes=1)


@pytest.mark.parametrize("name", [None, "", "  ", "x" * 201])
def test_api_key_create_and_rename_validate_names_and_identity(workflows, name):
    with pytest.raises(Refused, match="API key name"):
        workflows.api_key_create_action(name, "operator")
    with pytest.raises(Refused, match="API key name"):
        workflows.api_key_rename_action(1, name)


@pytest.mark.parametrize("username", [None, "", "   "])
def test_api_key_create_requires_owner(workflows, username):
    with pytest.raises(Refused, match="owner username"):
        workflows.api_key_create_action("automation", username)


@pytest.mark.parametrize("expiry", ["", "not-a-date", "2027-01-01T00:00:00", 12])
def test_api_key_create_requires_offset_timestamp(workflows, expiry):
    with pytest.raises(Refused, match="ISO-8601|explicit UTC offset"):
        workflows.api_key_create_action("automation", "operator", expiry)


@pytest.mark.parametrize("key_id", [0, -1, True, "1"])
def test_api_key_operations_require_positive_integer_ids(workflows, key_id):
    with pytest.raises(Refused, match="positive integer"):
        workflows.api_key_rename_action(key_id, "renamed")
    with pytest.raises(Refused, match="positive integer"):
        workflows.api_key_revoke_action(key_id)


def test_api_key_queries_refuse_malformed_rows_and_ambiguous_id_lookup(workflows, server):
    server.api_keys[1] = {"id": True, "name": "invalid"}
    with pytest.raises(Refused, match="valid ID and name"):
        workflows.api_keys()
    server.api_keys[1] = {"id": 1, "name": "valid"}
    with pytest.raises(TransportError, match="API key 2 not found"):
        workflows._api_key_by_id(2)


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"service": "UNKNOWN"}, "service must be"),
    ({"limit": 0}, "limit must"),
    ({"limit": 10001}, "limit must"),
    ({"limit": True}, "limit must"),
    ({"offset": -1}, "offset must"),
    ({"offset": False}, "offset must"),
    ({"since": "not-a-time"}, "ISO-8601"),
    ({"since": "2026-10-28T00:00:00"}, "explicit UTC offset"),
    ({"until": "2026-10-28T00:00:00"}, "explicit UTC offset"),
    ({"since": "2026-10-29T00:00:00Z", "until": "2026-10-28T00:00:00Z"}, "earlier than"),
    ({"user": " "}, "username filter must"),
    ({"event": 4}, "event filter must"),
])
def test_audit_filter_arguments_fail_closed(workflows, kwargs, message):
    with pytest.raises(Refused, match=message):
        workflows.audit_entries(**kwargs)


def test_share_lookups_and_payload_validators_refuse_invalid_paths_and_fields(workflows, server):
    for value in (None, "", "  "):
        with pytest.raises(Refused, match="NFS share path is required"):
            workflows.share_nfs(value)
        with pytest.raises(Refused, match="SMB share name is required"):
            workflows.share_smb_create_action({"path": "/mnt/tank/share"})
    with pytest.raises(Refused, match="exactly one NFS share"):
        workflows.share_nfs("/mnt/tank/missing")
    with pytest.raises(Refused, match="purpose must"):
        workflows.share_smb_create_action({"name": "share", "path": "/mnt/tank/share", "purpose": "UNKNOWN"})
    for guest, purpose, message in ((1, "LEGACY_SHARE", "guest must"), (True, "DEFAULT_SHARE", "only for LEGACY_SHARE")):
        with pytest.raises(Refused, match=message):
            workflows.share_smb_create_action({"name": "share", "path": "/mnt/tank/share",
                                                "purpose": purpose, "guest": guest})
    for options, message in (({"hostsallow": "10.0.0.0/8"}, "must be arrays"),
                             ({"hostsdeny": [""]}, "entries must be non-empty")):
        with pytest.raises(Refused, match=message):
            workflows.share_smb_create_action({"name": "share", "path": "/mnt/tank/share", "options": options})
    for path in ("/mnt", "/mnt/", "relative", "/mnt/boot-pool/share"):
        with pytest.raises(Refused):
            workflows.share_nfs_create_action({"path": path})
    with pytest.raises(Refused, match="security must"):
        workflows.share_nfs_create_action({"path": "/mnt/tank/share", "security": ["INVALID"]})
    valid_smb = workflows.share_smb_create_action({"name": "team", "path": "/mnt/tank/share",
                                                    "purpose": "LEGACY_SHARE", "guest": True,
                                                    "hostsallow": ["10.0.0.0/8"]})
    assert valid_smb.calls[0].params[0]["options"] == {"guestok": True, "hostsallow": ["10.0.0.0/8"]}
    valid_nfs = workflows.share_nfs_create_action({"path": "/mnt/tank/export", "maprootuser": "root",
                                                    "security": ["SYS"]})
    assert valid_nfs.calls[0].params[0]["maproot_user"] == "root"


def test_share_remote_path_errors_and_canonicalization_are_explicit(workflows, server):
    server.fail["filesystem.stat"] = "No such file or directory"
    with pytest.raises(Refused, match="never creates paths implicitly"):
        workflows.share_nfs_create_action({"path": "/mnt/tank/missing"})
    server.fail["filesystem.stat"] = "Permission denied"
    with pytest.raises(TransportError):
        workflows.share_nfs_create_action({"path": "/mnt/tank/share"})
    server.fail.clear()
    server.share_paths.add("/mnt/tank/alias")
    original = server._filesystem_stat
    server._filesystem_stat = lambda params: {"realpath": "/mnt/tank/canonical"}
    with pytest.raises(Refused, match="canonical path"):
        action = workflows.share_nfs_create_action({"path": "/mnt/tank/alias"})
        from tnt.actions import make_plan
        make_plan(action, workflows.rpc)
    server._filesystem_stat = original


@pytest.mark.parametrize(("data", "message"), [
    ({"dataset": "tank"}, "cron schedule"),
    ({"dataset": "tank", "schedule": {"minute": ""}}, "cron schedule"),
    ({"dataset": "tank", "schedule": {"bad": "*"}}, "cron schedule"),
    ({"dataset": "tank", "schedule": {"minute": "0"}, "lifetime_value": True}, "positive integer"),
    ({"dataset": "tank", "schedule": {"minute": "0"}, "lifetime_value": 1,
      "lifetime_unit": "INVALID"}, "lifetime_unit"),
    ({"dataset": "tank", "schedule": {"minute": "0"}, "lifetime_value": 1,
      "lifetime_unit": "DAY", "naming_schema": "daily"}, "naming_schema"),
    ({"dataset": "tank", "schedule": {"minute": "0"}, "lifetime_value": 1,
      "lifetime_unit": "DAY", "naming_schema": "%s", "unknown": True}, "unsupported v27 snapshot task fields"),
])
def test_snapshot_task_creation_validates_schedule_retention_naming_and_schema(workflows, data, message):
    with pytest.raises(Refused, match=message):
        workflows.snapshot_task_create_action(data)


def test_snapshot_replication_and_cloudsync_task_actions_validate_state(workflows, server):
    with pytest.raises(Refused, match="positive integer"):
        workflows.snapshot_task(True)
    with pytest.raises(Refused, match="true or false"):
        workflows.snapshot_task_enable_action(1, 1)
    with pytest.raises(Refused, match="true or false"):
        workflows.replication_enable_action("offsite", "yes")
    server.replication_tasks[10]["source_datasets"] = []
    with pytest.raises(Refused, match="source datasets and target"):
        workflows.replication_run_action("offsite")
    for value in (None, " "):
        with pytest.raises(Refused, match="description is required"):
            workflows.cloudsync_task(value)
    with pytest.raises(Refused, match="true or false"):
        workflows.cloudsync_enable_action("offsite-copy", 1)
    server.cloudsync_tasks[20]["transfer_mode"] = "MOVE"
    assert "MOVE deletes local source" in workflows.cloudsync_run_action("offsite-copy").title


def test_named_and_row_helpers_reject_ambiguous_and_malformed_rpc_replies(workflows, server):
    from tnt.workflows import _rows, _safe_value, _scrub_audit

    for value in (None, {}, [{"ok": True}, 1]):
        with pytest.raises(Refused, match="invalid row collection"):
            _rows(value, "test.query")
    assert _safe_value({"NestedToken": "secret", "items": ({"password": "p"},)}) == {
        "NestedToken": "***", "items": [{"password": "***"}],
    }
    audit = _scrub_audit({"event_data": ["api_key='abc'", "Bearer abc.def", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"]})
    assert all("abc" not in value and "PRIVATE KEY" not in value for value in audit["event_data"])
    server.apps["dupe"] = dict(server.apps["example-app"])
    with pytest.raises(Refused, match="exactly one installed app"):
        workflows.app("example-app")

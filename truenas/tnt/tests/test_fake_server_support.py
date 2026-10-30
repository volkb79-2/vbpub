from __future__ import annotations

from types import SimpleNamespace

import pytest

from tnt.testing import CALL_ERROR, FAKE_KEY, FakeServer, _RpcError


def test_fake_server_protocol_errors_and_local_download_tokens():
    server = FakeServer()
    assert server._process_request(None, SimpleNamespace(path="/api/current")) is None
    bad_path = server._process_request(None, SimpleNamespace(path="/_download/not-an-id"))
    assert bad_path.status_code == 404

    server._download_tokens[7] = "synthetic-token"
    assert server._process_request(None, SimpleNamespace(path="/_download/7?auth_token=wrong")).status_code == 404
    valid = server._process_request(None, SimpleNamespace(path="/_download/7?auth_token=synthetic-token"))
    assert valid.status_code == 200 and valid.body == server.config_backup_bytes
    assert server._process_request(None, SimpleNamespace(path="/_download/7?auth_token=synthetic-token")).status_code == 404

    assert server._login([{"mechanism": "PASSWORD_PLAIN", "username": "tnt", "password": server.password}], False) == (
        {"response_type": "SUCCESS"}, True)
    assert server._login([{"mechanism": "PASSWORD_PLAIN", "username": "tnt", "password": "wrong"}], False) == (
        {"response_type": "AUTH_ERR"}, False)
    assert server._login([{"mechanism": "API_KEY_PLAIN", "username": "tnt", "api_key": FAKE_KEY}], False) == (
        {"response_type": "SUCCESS"}, True)
    assert server._login([{"mechanism": "API_KEY_PLAIN", "username": "other", "api_key": FAKE_KEY}], False) == (
        {"response_type": "AUTH_ERR"}, False)
    assert server._login([{"mechanism": "UNKNOWN"}], False) == ({"response_type": "AUTH_ERR"}, False)

    malformed_first = {"scram_type": "CLIENT_FIRST_MESSAGE", "rfc_str": "n,,n=missing-key-id,r=abc"}
    assert server._scram_login(malformed_first, False) == ({"response_type": "AUTH_ERR"}, False)
    assert server._scram_login({"scram_type": "UNSUPPORTED", "rfc_str": ""}, False) == (
        {"response_type": "AUTH_ERR"}, False)
    server.api_key = "malformed-key"
    invalid_key = {"scram_type": "CLIENT_FIRST_MESSAGE", "rfc_str": "n,,n=tnt:1,r=YWJj"}
    assert server._scram_login(invalid_key, False) == ({"response_type": "AUTH_ERR"}, False)
    server.api_key = FAKE_KEY
    server._scram["known"] = {"client_bare": "", "server_first": "", "material": "x",
                               "salt": b"", "iterations": 1}
    bad_proof = {"scram_type": "CLIENT_FINAL_MESSAGE", "rfc_str": "c=biws,r=known,p=%%%"}
    assert server._scram_login(bad_proof, False) == ({"response_type": "AUTH_ERR"}, False)

    with pytest.raises(_RpcError, match="Method not found") as error:
        server._dispatch("unimplemented.method", [])
    assert error.value.code == -32601
    assert server._get({1: {"id": 1}}, "1", "row") == {"id": 1}
    with pytest.raises(_RpcError, match="not found") as error:
        server._get({}, "absent", "row")
    assert error.value.code == CALL_ERROR


def test_fake_server_query_projection_and_system_reply_shapes():
    server = FakeServer()
    rows = {
        1: {"id": 1, "username": "alice", "state": {"name": "READY", "code": 0}},
        2: {"id": 2, "username": "bob", "state": {"name": "STOPPED", "code": 1}},
    }
    assert FakeServer._matches(rows[1], None)
    assert not FakeServer._matches(rows[1], [["id", "=", 2]])
    assert FakeServer._matches(rows[1], [["malformed"], ["id", "!=", 2]])
    assert not FakeServer._matches(rows[1], [["username", "=", "bob"]], username_key="username")
    projected = server._query(rows, [[["id", "!=", 2]], {"offset": 0, "limit": 2,
                                                           "select": ["id", "state.name", "state.missing"]}])
    assert projected == [{"id": 1, "state": {"name": "READY", "missing": None}}]
    assert server._query(rows, [[], {"select": ["id", 5]}]) == list(rows.values())
    assert server._query(rows, [[], {"offset": True, "limit": False}]) == list(rows.values())
    assert server._query(rows, [[], {"offset": -1, "limit": -1}]) == list(rows.values())

    server.system_state = "BOOTING"
    assert server._system_state([]) == "BOOTING"
    assert server._system_state([]) == "READY"
    server._system_reboot([])
    assert server._system_state([]) == "BOOTING"
    server._system_shutdown([])
    assert server.system_state == "SHUTTING_DOWN"
    server.update_new_version = None
    assert server._update_status([])["status"]["new_version"] is None
    assert server._update_available_versions([]) == [{"train": server.update_target_train, "version": None}]
    with pytest.raises(_RpcError, match="unsupported fake download"):
        server._core_download(["wrong"])
    result = server._core_download(["config.save", [], "config.tar", False])
    assert result[0] in server._download_tokens and "auth_token=" in result[1]


def test_fake_server_api_key_and_audit_query_filters_and_projection():
    server = FakeServer()
    server.api_keys.update({1: {"id": 1, "name": "first", "username": "tnt", "created_at": 1},
                            2: {"id": 2, "name": "second", "username": "tnt", "created_at": 2}})
    keys = server._api_key_query([[ ["username", "=", "tnt"] ], {"order_by": ["-created_at"],
                                                                   "offset": 0, "limit": 1,
                                                                   "select": ["id", "name"]}])
    assert keys == [{"id": 2, "name": "second"}]
    assert server._api_key_query([[], {"order_by": [4], "offset": 0, "limit": 1}])[0]["id"] == 1
    server.api_keys[3] = {"id": 3, "name": "third", "username": "tnt"}
    assert server._api_key_query(["malformed", "bad-options"])[0]["id"] == 1

    server.audit_entries = [
        {"service": "SMB", "message_timestamp": 2, "username": "tnt", "event": "WRITE"},
        {"service": "SMB", "message_timestamp": 1, "username": "other", "event": "READ"},
        {"service": "SYSTEM", "message_timestamp": 3, "username": "tnt", "event": "BOOT"},
    ]
    rows = server._audit_query([{"services": ["SMB"], "query-filters": [
        ["message_timestamp", ">=", 1], ["message_timestamp", "<", 3], ["username", "=", "tnt"]],
        "query-options": {"order_by": ["-message_timestamp"], "offset": 0, "limit": 10}}])
    assert rows == [server.audit_entries[0]]
    all_rows = server._audit_query([{"services": ["SMB"], "query-filters": ["bad", ["event", "?", "x"]],
                                      "query-options": {"order_by": [12], "offset": 0, "limit": 10}}])
    assert {row["event"] for row in all_rows} == {"WRITE", "READ"}


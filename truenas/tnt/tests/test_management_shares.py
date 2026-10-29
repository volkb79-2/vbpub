from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, Static, Tree

from tnt.actions import apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


def _apply(session, action, confirm=None):
    plan = make_plan(action, session.rpc)
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirm)


def test_smb_and_nfs_share_workflows_use_remote_paths_acl_and_typed_delete():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            workflows = session.workflows
            smb = workflows.share_smb_create_action({
                "name": "team", "path": "/mnt/tank/share", "purpose": "DEFAULT_SHARE",
                "options": {"hostsallow": ["10.0.0.0/8"], "hostsdeny": []},
                "enabled": True, "readonly": True, "comment": "team files",
            })
            assert any(call.method == "filesystem.stat" for call in smb.probe)
            assert "sharing.smb.create" in make_plan(smb, session.rpc).render()
            _apply(session, smb)

            nfs = workflows.share_nfs_create_action({
                "path": "/mnt/tank/export", "networks": ["10.0.0.0/8"], "hosts": [],
                "maproot_user": "root", "security": ["SYS"], "ro": True,
            })
            _apply(session, nfs)

            smb_rows = workflows.shares_smb()
            assert smb_rows[0]["name"] == "team"
            assert smb_rows[0]["acl"]["share_name"] == "team"
            assert smb_rows[0]["options"]["hostsallow"] == ["10.0.0.0/8"]
            assert workflows.shares_nfs()[0]["maproot_user"] == "root"

            delete = workflows.share_smb_delete_action("team")
            assert delete.confirm_target == "team"
            _apply(session, delete, "team")
            assert workflows.shares_smb() == []
            assert "filesystem.stat" in server.called()
            assert "sharing.smb.getacl" in server.called()
            acl_request = next(request for request in server.requests if request["method"] == "sharing.smb.getacl")
            assert acl_request["params"] == [{"share_name": "team"}]
        finally:
            session.close()


def test_smb_default_share_refuses_legacy_guest_option_before_create():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            with pytest.raises(Refused, match="guestok.*LEGACY_SHARE"):
                session.workflows.share_smb_create_action({
                    "name": "team", "path": "/mnt/tank/share", "purpose": "DEFAULT_SHARE",
                    "options": {"guestok": False},
                })
            assert "sharing.smb.create" not in server.called()
        finally:
            session.close()


def test_share_cli_reads_json_and_exposes_create_plan(config, opener, capsys, tmp_path):
    cli = build_cli(opener=opener)
    code = cli.run(argv=["share", "smb", "list", "--config", str(config), "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out) == []
    payload = tmp_path / "share.json"
    payload.write_text(json.dumps({"name": "team", "path": "/mnt/tank/share", "purpose": "DEFAULT_SHARE",
                                   "options": {"hostsallow": ["10.0.0.0/8"]}}))
    code = cli.run(argv=["share", "smb", "create", "--config", str(config), "--file", str(payload),
                         "--allow-write", "--dry-run", "--json"])
    out = capsys.readouterr().out
    assert code == 0 and "sharing.smb.create" in json.loads(out)["plan"] and json.loads(out)["applied"] is False


def test_shares_tui_leaf_loads_shared_smb_and_nfs_queries(config):
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(140, 45)) as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "sharing":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                for _ in range(40):
                    await pilot.pause(0.05)
                    if "loaded." in str(app.query_one("#workflow-note", Static).render()):
                        break
                assert "Create SMB share" in str(app.query_one("#workflow-actions", Static).render())
                assert "sharing.smb.query" in server.called() and "sharing.nfs.query" in server.called()
                assert app.query_one("#workflow-table", DataTable).row_count == 0

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_shares_refuse_missing_remote_paths_and_boot_pool_paths_without_creating_them():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            with pytest.raises(Refused, match="does not exist"):
                session.workflows.share_nfs_create_action({"path": "/mnt/tank/not-created"})
            with pytest.raises(Refused, match="boot-pool"):
                session.workflows.share_smb_create_action({
                    "name": "unsafe", "path": "/mnt/boot-pool/export", "purpose": "DEFAULT_SHARE",
                })
            assert "filesystem.mkdir" not in server.called()
            assert "filesystem.stat" in server.called()
        finally:
            session.close()

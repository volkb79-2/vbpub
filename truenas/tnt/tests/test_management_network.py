import asyncio
import json

from textual.widgets import Static, Tree

from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


def test_network_view_reads_interfaces_ips_routes_dns_and_default_gateway():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            view = session.workflows.network_show()
            assert view["interfaces"][0]["name"] == "enp1s0"
            assert view["addresses"]["enp1s0"]["IPV4"] == ["192.0.2.10/24"]
            assert view["routes"][0]["gateway"] == "192.0.2.1"
            assert view["dns"] == ["192.0.2.53"]
            assert view["default_gateways"] == ["192.0.2.1"]
            assert {"interface.query", "route.system_routes", "network.general.summary"} <= set(server.called())
            requests = {request["method"]: request["params"] for request in server.requests}
            assert requests["interface.query"] == [[], {}]
            assert requests["route.system_routes"] == [[], {}]
            assert requests["network.general.summary"] == []
            server.network_summary = {"ips": {}, "nameservers": []}
            try:
                session.workflows.network_show()
            except Refused as exc:
                assert "omitted required" in str(exc)
            else:
                raise AssertionError("incomplete network summary must fail closed")
        finally:
            session.close()


def test_network_cli_returns_the_same_read_only_view_as_json(config, opener, capsys):
    cli = build_cli(opener=opener)
    code = cli.run(argv=["network", "show", "--config", str(config), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["default_gateways"] == ["192.0.2.1"]
    assert result["dns"] == ["192.0.2.53"]


def test_network_tui_loads_the_read_only_view(config):
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(150, 45)) as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "network":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                for _ in range(40):
                    await pilot.pause(0.05)
                    note = str(app.query_one("#network-note", Static).render())
                    if "loaded" in note:
                        break
                assert "read-only" in note.casefold()
                output = str(app.query_one("#network-output", Static).render())
                assert "192.0.2.10/24" in output and "192.0.2.53" in output and "192.0.2.1" in output
                assert not {"interface.update", "network.configuration.update"} & set(server.called())

        try:
            asyncio.run(go())
        finally:
            session.close()

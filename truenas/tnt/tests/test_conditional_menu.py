from __future__ import annotations

import asyncio

from textual.widgets import Static, Tree

from helpers import wait_until
from tnt.menu import MENU, walk
from tnt.session import _valid_kmip_config, open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


def test_conditional_capabilities_distinguish_unsupported_unavailable_and_supported():
    with FakeServer(fail={"app.query": "permission denied"}) as server:
        server.virtualization_supported = False
        server.enclosures = []
        methods = [method for method in server.methods if method != "kmip.config"]
        server.methods = methods
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.conditional_menu_capabilities()
            assert result["apps"]["state"] == "unavailable"
            assert "permission" in result["apps"]["reason"].casefold()
            assert result["containers"]["state"] == "supported"
            assert result["vm"]["state"] == "unsupported"
            assert result["kmip"]["state"] == "unsupported"
            assert result["viewenclosure"]["state"] == "unsupported"
        finally:
            session.close()


def test_conditional_capability_results_are_cached_per_session_generation():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            first = session.conditional_menu_capabilities()
            app_queries = server.called().count("app.query")
            assert app_queries == 1
            assert session.conditional_menu_capabilities() == first
            assert server.called().count("app.query") == app_queries

            session.use_job_registry(session.job_registry, generation=1)
            assert session.conditional_menu_capabilities() == first
            assert server.called().count("app.query") == app_queries + 1

            session.replace_rpc(session.rpc, generation=1)
            assert session.conditional_menu_capabilities() == first
            assert server.called().count("app.query") == app_queries + 2
        finally:
            session.close()


def test_malformed_kmip_reply_is_unavailable():
    with FakeServer() as server:
        server.kmip_config["ssl_version"] = []
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.conditional_menu_capabilities()
            assert result["kmip"]["state"] == "unavailable"
            assert "unexpected v27 shape" in result["kmip"]["reason"]
        finally:
            session.close()

    assert not _valid_kmip_config([("not", "an entry")])

    with FakeServer() as server:
        server.kmip_config = []
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.conditional_menu_capabilities()
            assert result["kmip"]["state"] == "unavailable"
        finally:
            session.close()


def test_conditional_menu_hides_unsupported_and_keeps_unavailable_non_actionable():
    with FakeServer(fail={"app.query": "permission denied"}) as server:
        server.virtualization_supported = False
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

        async def go():
            app = TntApp(session)
            async with app.run_test() as pilot:
                await wait_until(pilot, lambda: app.conditional_capabilities is not None, what="conditional capabilities")
                labels = []

                def collect(node):
                    for child in node.children:
                        labels.append(child.label.plain)
                        collect(child)

                collect(app.query_one("#nav", Tree).root)
                joined = "\n".join(labels)
                assert "Virtual Machines" not in joined
                assert "Enclosure" in joined
                assert "unavailable: Apps" in joined

                app.show(next(item for item in MENU if item.key == "apps"))
                assert "Unavailable" in str(app.query_one("#conditional-unavailable", Static).render())
                assert "permission" in str(app.query_one("#conditional-unavailable", Static).render()).casefold()

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_supported_conditional_result_replaces_checking_panel():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

        async def go():
            app = TntApp(session)
            async with app.run_test() as pilot:
                await wait_until(pilot, lambda: app.conditional_capabilities is not None, what="conditional capabilities")
                app.conditional_capabilities = None
                app.show(next(item for item in MENU if item.key == "apps"))
                assert "Checking" in str(app.query_one("#conditional-unavailable", Static).render())
                app._conditional_capabilities_loaded({"apps": {"state": "supported", "reason": None}})
                await pilot.pause(0.05)
                assert app.query_one("#view-apps") is not None

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_conditional_callbacks_keep_pending_pages_action_safe():
    for key, state in (("vm", "unsupported"), ("apps", "unavailable"), ("containers", "failure"),
                       ("viewenclosure", "missing")):
        with FakeServer() as server:
            session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

            async def go():
                app = TntApp(session)
                async with app.run_test() as pilot:
                    await wait_until(pilot, lambda: app.conditional_capabilities is not None,
                                     what="conditional capabilities")
                    app.current = key
                    if state == "unsupported":
                        app._conditional_capabilities_loaded({key: {"state": state, "reason": "no hardware"}})
                        await pilot.pause(0.05)
                        assert app.current == "dashboard"
                    elif state == "unavailable":
                        app._conditional_capabilities_loaded({key: {"state": state, "reason": "permission denied"}})
                        text = str(app.query_one("#conditional-unavailable", Static).render())
                        assert "Unavailable" in text and "permission denied" in text
                    elif state == "failure":
                        app._conditional_capabilities_failed("probe timed out")
                        text = str(app.query_one("#conditional-unavailable", Static).render())
                        assert "Unavailable" in text and "probe timed out" in text
                    else:
                        app.conditional_capabilities = {}
                        app.show(next(item for _, item in walk() if item.key == key))
                        text = str(app.query_one("#conditional-unavailable", Static).render())
                        assert "Unavailable" in text and "no result" in text

            try:
                asyncio.run(go())
            finally:
                session.close()

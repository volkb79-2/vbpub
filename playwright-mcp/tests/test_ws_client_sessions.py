import unittest

from ws_client import PlaywrightWSClient


class DummyClient(PlaywrightWSClient):
    def __init__(self) -> None:
        super().__init__(url="ws://test")
        self.sent = []

    async def _send_command(self, command, args=None):
        self.sent.append((command, args))
        return {"ok": True}


class TestSessionRouting(unittest.IsolatedAsyncioTestCase):
    async def test_navigate_includes_session_id(self):
        client = DummyClient()
        await client.navigate("https://example.com", session_id="session-a")
        command, args = client.sent[-1]
        self.assertEqual(command, "navigate")
        self.assertEqual(args.get("session_id"), "session-a")

    async def test_create_session_payload(self):
        client = DummyClient()
        await client.create_session(
            workspace_id="admin",
            user_id="admin",
            label="Admin",
            record_har=True,
            har_content="embed",
            har_path="session.har",
        )
        command, args = client.sent[-1]
        self.assertEqual(command, "create_session")
        self.assertEqual(args.get("workspace_id"), "admin")
        self.assertEqual(args.get("user_id"), "admin")
        self.assertEqual(args.get("label"), "Admin")
        self.assertTrue(args.get("record_har"))
        self.assertEqual(args.get("har_content"), "embed")
        self.assertEqual(args.get("har_path"), "session.har")

    async def test_event_dispatch(self):
        client = DummyClient()
        events = []
        client.on_event(events.append)
        await client._dispatch_event({
            "type": "event",
            "event": "command_started",
            "session_id": "s1",
            "data": {"command": "navigate"},
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "command_started")

    async def test_export_console_logs(self):
        client = DummyClient()
        await client.export_console_logs(path="console.json", session_id="s1")
        command, args = client.sent[-1]
        self.assertEqual(command, "export_console_logs")
        self.assertEqual(args.get("path"), "console.json")
        self.assertEqual(args.get("session_id"), "s1")

    async def test_console_event_dispatch(self):
        client = DummyClient()
        events = []
        client.on_event(events.append)
        await client._dispatch_event({
            "type": "event",
            "event": "console",
            "session_id": "s2",
            "data": {"type": "log", "text": "hello"},
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "console")


if __name__ == "__main__":
    unittest.main()

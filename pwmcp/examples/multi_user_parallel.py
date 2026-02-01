#!/usr/bin/env python3
"""Example: multi-user parallel UI testing with session isolation."""

import asyncio
import os

from ws_client import PlaywrightWSClient


async def main() -> None:
    ws_url = os.getenv("WS_URL", "ws://localhost:3000")
    auth_token = os.getenv("WS_AUTH_TOKEN") or os.getenv("ACCESS_TOKEN")

    async with PlaywrightWSClient(ws_url, auth_token=auth_token) as client:
        def _handle_event(evt):
            event_type = evt.get("event")
            session = evt.get("session_id")
            data = evt.get("data")
            if event_type == "console":
                print(f"[CONSOLE] {session}: {data.get('type')} {data.get('text')}")
                return
            print(f"[EVENT] {event_type} {session}: {data}")

        client.on_event(_handle_event)

        admin = await client.create_session(workspace_id="admin", user_id="admin", label="Admin")
        user_a = await client.create_session(workspace_id="user-a", user_id="user-a", label="User A")
        user_b = await client.create_session(workspace_id="user-b", user_id="user-b", label="User B")

        admin_id = admin["session_id"]
        user_a_id = user_a["session_id"]
        user_b_id = user_b["session_id"]

        async def admin_flow():
            await client.navigate("https://example.com/admin", session_id=admin_id)
            await client.click("#publish", session_id=admin_id)

        async def user_a_flow():
            await client.navigate("https://example.com/dashboard", session_id=user_a_id)
            await client.wait_for_selector("#announcement", session_id=user_a_id)

        async def user_b_flow():
            await client.navigate("https://example.com/profile", session_id=user_b_id)
            await client.wait_for_selector("#profile", session_id=user_b_id)

        await asyncio.gather(admin_flow(), user_a_flow(), user_b_flow())

        await client.screenshot(path="admin.png", session_id=admin_id)
        await client.screenshot(path="user-a.png", session_id=user_a_id)
        await client.screenshot(path="user-b.png", session_id=user_b_id)

        print("Artifacts:", await client.list_artifacts(session_id=admin_id))


if __name__ == "__main__":
    asyncio.run(main())

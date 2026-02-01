#!/usr/bin/env python3
import asyncio

from playwright_mcp_client import ArtifactManager, PlaywrightMCPConfig, PlaywrightWSClient, SessionManager, UIHarness


async def main() -> None:
    config = PlaywrightMCPConfig.from_env()
    artifacts = ArtifactManager(config.artifacts_dir)

    async with PlaywrightWSClient(config.ws_url, auth_token=config.auth_token, timeout=config.timeout) as client:
        ui = UIHarness(client=client, config=config, artifacts=artifacts)
        session = SessionManager(client=client, artifacts=artifacts)

        await ui.goto("https://example.com")
        await ui.capture_artifacts(prefix="example")
        await session.save()


if __name__ == "__main__":
    asyncio.run(main())

# Usage

## WebSocket (Recommended)

```python
import asyncio

from pwmcp_client import PlaywrightWSClient

async def main():
    async with PlaywrightWSClient("wss://playwright.example.com/ws", auth_token="<token>") as client:
        await client.navigate("https://example.com")
        await client.screenshot("example.png")

asyncio.run(main())
```

## MCP (VS Code Copilot)

MCP endpoint:
- `https://PUBLIC_FQDN/mcp` (reverse proxy)
- `http://HOST:8765/mcp` (direct)

Authorization header required when `AUTH_REQUIRED=true`:

```
Authorization: Bearer <ACCESS_TOKEN>
```

### VS Code MCP Configuration

Add an entry to your MCP configuration:

```json
{
  "servers": {
    "pwmcp-server": {
      "type": "http",
      "url": "https://PUBLIC_FQDN/mcp",
      "headers": {
        "Authorization": "Bearer <ACCESS_TOKEN>"
      }
    }
  }
}
```

If you are exposing MCP via a public FQDN, set `MCP_ALLOWED_HOSTS` and
`MCP_ALLOWED_ORIGINS` to include the public host to avoid rebinding protection blocks.

## Multi-Project Usage

- Mount a shared workspace directory to `/workspaces`.
- Provide project-specific scripts that call the WebSocket client.
- Use unique tokens per project when needed (`WS_AUTH_TOKEN`, `MCP_AUTH_TOKEN`).

## Client Demo (pip package)

Install the client package:

```bash
pip install pwmcp-client
```

Example:

```python
import asyncio

from pwmcp_client import PlaywrightWSClient


async def main():
  async with PlaywrightWSClient(
    "wss://playwright.example.com/ws",
    auth_token="<token>",
  ) as client:
    await client.navigate("https://example.com")
    await client.screenshot("example.png")


asyncio.run(main())
```

### Offline install from bundle

If you are using the stack bundle, install from the bundled wheel:

```bash
pip install client/pwmcp_client-0.1.0-py3-none-any.whl
```

## CLI (pwmcp)

Ping WebSocket service:

```bash
pwmcp ws ping --url ws://localhost:3000 --token <token>
```

Navigate and take a screenshot:

```bash
pwmcp ws navigate --url ws://localhost:3000 --token <token> --page https://example.com
pwmcp ws screenshot --url ws://localhost:3000 --token <token> --path /workspaces/artifacts/example.png
```

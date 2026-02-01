# Security

## Mandatory Controls

1. **Authentication**
   - `AUTH_REQUIRED=true` by default.
   - Provide `ACCESS_TOKEN` (or service-specific tokens) in production.
   - MCP requires `Authorization: Bearer <token>`.
   - WebSocket requires an initial JSON auth message:
     ```json
     {"type":"auth","token":"<token>"}
     ```

2. **TLS**
   - Prefer the reverse proxy for TLS termination.
   - Mount `/etc/letsencrypt` (parent directory) read-only in the proxy container.

3. **Host/Origin Allowlist**
   - Configure `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` for public deployments.
   - Include your `PUBLIC_FQDN` in both lists when exposing MCP over TLS.

4. **Network Exposure**
   - Expose public ports only when needed.
   - For internal-only deployments, bind ports to a private interface or use SSH tunnels.

## Recommended Reverse Proxy Setup
Use the reverse proxy profile to serve:
- `https://PUBLIC_FQDN/mcp`
- `wss://PUBLIC_FQDN/ws`

## Token Rotation
- Rotate `ACCESS_TOKEN` regularly.
- Use separate `WS_AUTH_TOKEN` and `MCP_AUTH_TOKEN` when you need different scopes.

## Log Hygiene
- Tokens are never logged by the server.
- Ensure your reverse proxy access logs do not include Authorization headers.

## Hardening Ideas (Optional)
- IP allowlists at reverse proxy
- Rate limiting at reverse proxy
- mTLS between proxy and service

#!/usr/bin/env node
"use strict";

// Stream-only HTTP front door for @playwright/mcp. The Playwright MCP CLI's
// --port mode still enables its legacy /sse transport, so that process listens
// on loopback and this gateway exposes only the reviewed public contract.
const http = require("node:http");

const listenPort = Number.parseInt(process.env.PWMCP_MCP_PORT || "8931", 10);
const upstreamPort = Number.parseInt(process.env.PWMCP_MCP_UPSTREAM_PORT || "8934", 10);
const allowedHosts = new Set(
  (process.env.PWMCP_MCP_ALLOWED_HOSTS || "localhost:8931,127.0.0.1:8931")
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean),
);

function reject(response, status, message) {
  response.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  response.end(`${message}\n`);
}

const server = http.createServer((request, response) => {
  const host = String(request.headers.host || "").trim().toLowerCase();
  if (!allowedHosts.has(host)) {
    reject(response, 403, `Access is only allowed at ${[...allowedHosts].join(", ")}`);
    return;
  }

  const url = new URL(request.url || "/", `http://${host}`);
  if (url.pathname !== "/mcp") {
    reject(response, 404, "Only the Streamable HTTP /mcp endpoint is available");
    return;
  }

  const headers = { ...request.headers, host: `127.0.0.1:${upstreamPort}` };
  delete headers.connection;
  const upstream = http.request(
    {
      hostname: "127.0.0.1",
      port: upstreamPort,
      method: request.method,
      path: `${url.pathname}${url.search}`,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", (error) => {
    if (!response.headersSent) reject(response, 502, `MCP backend unavailable: ${error.message}`);
    else response.destroy(error);
  });
  request.pipe(upstream);
});

server.listen(listenPort, "0.0.0.0", () => {
  process.stdout.write(`mcp-gateway listening on ${listenPort}, backend ${upstreamPort}\n`);
});

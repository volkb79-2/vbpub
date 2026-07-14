#!/usr/bin/env node
"use strict";

// Absolute-lease and concurrency gateway for Playwright run-server.
// WebSocket frames are relayed as opaque TCP bytes; only the HTTP upgrade is
// inspected so the gateway can apply policy without understanding Playwright.

const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const { execFile } = require("node:child_process");
const crypto = require("node:crypto");

function positiveInt(name, fallback) {
  const raw = process.env[name] ?? String(fallback);
  if (!/^[1-9][0-9]*$/.test(raw)) throw new Error(`${name} must be a positive integer`);
  return Number(raw);
}

const publicPort = positiveInt("PWMCP_RUN_SERVER_PORT", 3000);
const upstreamPort = positiveInt("PWMCP_RUN_SERVER_UPSTREAM_PORT", 3001);
const adminPort = positiveInt("PWMCP_RUN_SERVER_ADMIN_PORT", 8940);
const defaultLease = positiveInt("PWMCP_RUN_SERVER_DEFAULT_LEASE_S", 1800);
const maxLease = positiveInt("PWMCP_RUN_SERVER_MAX_LEASE_S", 7200);
const maxClients = positiveInt("PWMCP_RUN_SERVER_MAX_CLIENTS", 2);
const idleRecycle = positiveInt("PWMCP_RUN_SERVER_IDLE_RECYCLE_S", 30);
if (defaultLease > maxLease) throw new Error("default lease exceeds maximum lease");

const contractPath = process.env.PWMCP_CONTRACT_PATH || "/opt/pwmcp/contract.json";
const sessions = new Map();
let recycleTimer = null;

function restartRunServer(callback = () => {}) {
  const supervisorConf = process.env.PWMCP_SUPERVISOR_CONF || "/etc/supervisor/conf.d/pwmcp.conf";
  execFile("supervisorctl", ["-c", supervisorConf, "restart", "run-server"], callback);
}

function scheduleIdleRecycle() {
  if (sessions.size || recycleTimer) return;
  recycleTimer = setTimeout(() => {
    recycleTimer = null;
    if (sessions.size) return;
    restartRunServer((error) => {
      process.stdout.write(`[gateway] idle run-server recycle status=${error ? "failed" : "ok"}\n`);
    });
  }, idleRecycle * 1000);
}

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  res.end(body);
}

function sessionView(session) {
  return {
    id: session.id,
    label: session.label,
    started_at: new Date(session.startedAt).toISOString(),
    expires_at: new Date(session.expiresAt).toISOString(),
    lease_seconds: session.lease,
  };
}

function closeSession(session, reason) {
  if (!sessions.delete(session.id)) return;
  clearTimeout(session.timer);
  session.client.destroy();
  session.upstream.destroy();
  process.stdout.write(`[gateway] closed session=${session.id} reason=${reason}\n`);
  scheduleIdleRecycle();
}

function requestHandler(req, res) {
  if (req.method === "GET" && req.url === "/health") {
    return json(res, 200, { status: "ok", active_sessions: sessions.size, max_clients: maxClients });
  }
  if (req.method === "GET" && req.url === "/contract") {
    try {
      const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
      contract.endpoints = {
        ...(contract.endpoints || {}),
        playwright_ws: `ws://pwmcp:${publicPort}/`,
        health: `http://pwmcp:${publicPort}/health`,
        contract: `http://pwmcp:${publicPort}/contract`,
        admin: `http://pwmcp:${adminPort}/`,
      };
      contract.run_server = contract.run_server || {};
      contract.run_server.limits = {
        ...(contract.run_server.limits || {}),
        default_lease_seconds: defaultLease,
        max_lease_seconds: maxLease,
        max_clients: maxClients,
        idle_recycle_seconds: idleRecycle,
      };
      const body = Buffer.from(JSON.stringify(contract));
      res.writeHead(200, { "content-type": "application/json", "content-length": body.length });
      return res.end(body);
    } catch (error) {
      return json(res, 500, { error: "contract_unavailable" });
    }
  }
  return json(res, 404, { error: "not_found" });
}

const gateway = http.createServer(requestHandler);
gateway.on("upgrade", (req, client, head) => {
  if (sessions.size >= maxClients) {
    client.end("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n");
    return;
  }
  const requestedRaw = req.headers["x-pwmcp-lease-seconds"];
  if (requestedRaw !== undefined && !/^[1-9][0-9]*$/.test(String(requestedRaw))) {
    client.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
    return;
  }
  const requested = requestedRaw === undefined ? defaultLease : Number(requestedRaw);
  const lease = Math.min(requested, maxLease);
  const label = String(req.headers["x-pwmcp-session-label"] || "unlabelled").slice(0, 128);
  const id = crypto.randomUUID();
  if (recycleTimer) {
    clearTimeout(recycleTimer);
    recycleTimer = null;
  }
  const upstream = net.createConnection({ host: "127.0.0.1", port: upstreamPort });
  const startedAt = Date.now();
  const session = { id, label, lease, startedAt, expiresAt: startedAt + lease * 1000, client, upstream, timer: null };
  session.timer = setTimeout(() => closeSession(session, "lease_expired"), lease * 1000);
  sessions.set(id, session);

  upstream.once("connect", () => {
    let upgrade = `${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`;
    for (let index = 0; index < req.rawHeaders.length; index += 2) {
      upgrade += `${req.rawHeaders[index]}: ${req.rawHeaders[index + 1]}\r\n`;
    }
    upstream.write(`${upgrade}\r\n`);
    if (head.length) upstream.write(head);
    client.pipe(upstream).pipe(client);
    process.stdout.write(`[gateway] opened session=${id} label=${JSON.stringify(label)} lease_s=${lease}\n`);
  });
  upstream.on("error", () => closeSession(session, "upstream_error"));
  client.on("error", () => closeSession(session, "client_error"));
  upstream.on("close", () => closeSession(session, "upstream_closed"));
  client.on("close", () => closeSession(session, "client_closed"));
});

const admin = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/sessions") {
    return json(res, 200, { sessions: [...sessions.values()].map(sessionView) });
  }
  if (req.method === "POST" && req.url === "/sessions/close") {
    for (const session of [...sessions.values()]) closeSession(session, "admin_close");
    return json(res, 200, { status: "closed" });
  }
  if (req.method === "POST" && req.url === "/run-server/restart") {
    for (const session of [...sessions.values()]) closeSession(session, "server_restart");
    return restartRunServer((error) => {
      json(res, error ? 500 : 200, { status: error ? "restart_failed" : "restarted" });
    });
  }
  return json(res, 404, { error: "not_found" });
});

gateway.listen(publicPort, "0.0.0.0", () => {
  process.stdout.write(`[gateway] listening port=${publicPort} upstream=127.0.0.1:${upstreamPort} max_clients=${maxClients}\n`);
});
admin.listen(adminPort, "0.0.0.0", () => process.stdout.write(`[gateway] admin port=${adminPort}\n`));

function shutdown() {
  if (recycleTimer) clearTimeout(recycleTimer);
  for (const session of [...sessions.values()]) closeSession(session, "shutdown");
  gateway.close();
  admin.close();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

#!/usr/bin/env node
'use strict';
/**
 * pwmcp admin-server — browser_mode=shared safeguard implementation (P03).
 *
 * Node stdlib ONLY (http, net, crypto, url) — no new framework dependency,
 * per the handoff. Exposes exactly three endpoints, closed set, everything
 * else 404. No request bodies are ever parsed/interpreted (no injectable
 * parameters):
 *
 *   POST /browser/reset    — close all CDP browser contexts (cookies/
 *                             storage/pages gone) without killing the
 *                             Chromium process.
 *   POST /browser/restart  — hard-restart the [program:chromium] supervisord
 *                             program (kills + respawns the process).
 *   GET  /browser/health   — CDP liveness (via /json/version) + open target
 *                             (page/context) count + this admin-server's
 *                             own process uptime.
 *
 * Talks to supervisord over its unix_http_server socket (XML-RPC/HTTP,
 * same transport supervisorctl uses) for /browser/restart — no capability
 * beyond stopProcess/startProcess on the single named "chromium" program is
 * used or requested. Talks to Chromium's own CDP HTTP+WebSocket endpoint
 * (127.0.0.1 only, same container) for /browser/reset and /browser/health.
 *
 * Optional idle-recycle: if PWMCP_BROWSER_MAX_IDLE_S is set to a positive
 * integer, a background loop polls CDP target count; when zero targets have
 * been observed for >= that many seconds, it restarts the chromium program
 * via the same supervisord path as /browser/restart (sheds leaked memory).
 * Default (unset or 0) is off. The poll interval is configurable via
 * PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S (default 5) so tests can inject a short
 * interval + short max-idle to exercise the mechanism without waiting.
 */

const http = require('http');
const net = require('net');
const crypto = require('crypto');

const ADMIN_PORT = parseInt(process.env.PWMCP_ADMIN_PORT || '8939', 10);
const CDP_PORT = parseInt(process.env.PWMCP_CDP_PORT || '9222', 10);
const CDP_HOST = '127.0.0.1';
const SUPERVISOR_SOCK = process.env.PWMCP_SUPERVISOR_SOCK || '/tmp/supervisor.sock';
const MAX_IDLE_S = parseInt(process.env.PWMCP_BROWSER_MAX_IDLE_S || '0', 10);
const IDLE_CHECK_INTERVAL_S = parseInt(process.env.PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S || '5', 10);

const startedAt = Date.now();

// ---------------------------------------------------------------------------
// Minimal supervisord XML-RPC client over its unix_http_server socket.
// Only the two calls this program needs (stopProcess/startProcess) — no
// generic XML-RPC library is pulled in, per "no new framework dependency".
// ---------------------------------------------------------------------------
function xmlEscape(s) {
  return String(s).replace(/[<>&'"]/g, (c) => ({
    '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;',
  }[c]));
}

function supervisorCall(method, params) {
  const paramsXml = params
    .map((p) => {
      if (typeof p === 'boolean') return `<param><value><boolean>${p ? 1 : 0}</boolean></value></param>`;
      return `<param><value><string>${xmlEscape(p)}</string></value></param>`;
    })
    .join('');
  const body = `<?xml version="1.0"?><methodCall><methodName>${method}</methodName><params>${paramsXml}</params></methodCall>`;
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        socketPath: SUPERVISOR_SOCK,
        path: '/RPC2',
        method: 'POST',
        headers: { 'Content-Type': 'text/xml', 'Content-Length': Buffer.byteLength(body) },
      },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => {
          if (/<fault>/.test(data)) {
            reject(new Error(`supervisord fault: ${data}`));
          } else {
            resolve(data);
          }
        });
      },
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function restartChromiumProgram() {
  // stopProcess then startProcess — supervisord has no atomic "restartProcess"
  // in its RPC surface for a single program name with wait semantics we can
  // rely on here, so do it explicitly. wait=true (final boolean param) makes
  // stopProcess block until the process is actually down.
  try {
    await supervisorCall('supervisor.stopProcess', ['chromium', true]);
  } catch (e) {
    // Already stopped is fine (fault code for NOT_RUNNING); anything else
    // still attempts the start below — startProcess will surface a real error.
  }
  await supervisorCall('supervisor.startProcess', ['chromium', true]);
}

// ---------------------------------------------------------------------------
// Minimal CDP clients — HTTP for /json/version (health) and a tiny hand-
// rolled WebSocket client for Target.getBrowserContexts / disposeBrowserContext
// (reset). No `ws` package — stdlib net + crypto only.
// ---------------------------------------------------------------------------
function cdpHttpGet(path) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: CDP_HOST, port: CDP_PORT, path, timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('CDP HTTP timeout')));
    req.on('error', reject);
  });
}

// Connects to the browser-level CDP WebSocket endpoint, sends `commands` in
// order (each a {method, params} object), resolves with the array of
// results. Deliberately minimal: unmasked-frame decode is not implemented
// (server->client frames from Chromium are never masked per RFC 6455), only
// single-frame text messages up to CDP's typical reply size are handled,
// which is sufficient for Target domain replies.
function cdpWsCall(commands) {
  return new Promise((resolve, reject) => {
    cdpHttpGet('/json/version')
      .then((info) => {
        const wsUrl = new URL(info.webSocketDebuggerUrl);
        const key = crypto.randomBytes(16).toString('base64');
        const socket = net.connect(CDP_PORT, CDP_HOST, () => {
          const req =
            `GET ${wsUrl.pathname} HTTP/1.1\r\n` +
            `Host: ${CDP_HOST}:${CDP_PORT}\r\n` +
            'Upgrade: websocket\r\n' +
            'Connection: Upgrade\r\n' +
            `Sec-WebSocket-Key: ${key}\r\n` +
            'Sec-WebSocket-Version: 13\r\n\r\n';
          socket.write(req);
        });
        let handshakeDone = false;
        let buf = Buffer.alloc(0);
        const results = [];
        let nextId = 1;
        const pending = new Map();

        function sendFrame(obj) {
          const payload = Buffer.from(JSON.stringify(obj), 'utf8');
          const maskKey = crypto.randomBytes(4);
          const masked = Buffer.alloc(payload.length);
          for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ maskKey[i % 4];
          let header;
          if (payload.length < 126) {
            header = Buffer.from([0x81, 0x80 | payload.length]);
          } else {
            header = Buffer.alloc(4);
            header[0] = 0x81;
            header[1] = 0x80 | 126;
            header.writeUInt16BE(payload.length, 2);
          }
          socket.write(Buffer.concat([header, maskKey, masked]));
        }

        function sendNext() {
          if (commands.length === 0) {
            socket.end();
            resolve(results);
            return;
          }
          const cmd = commands.shift();
          const id = nextId++;
          pending.set(id, cmd);
          sendFrame({ id, method: cmd.method, params: cmd.params || {} });
        }

        socket.on('data', (chunk) => {
          buf = Buffer.concat([buf, chunk]);
          if (!handshakeDone) {
            const headerEnd = buf.indexOf('\r\n\r\n');
            if (headerEnd === -1) return;
            handshakeDone = true;
            buf = buf.subarray(headerEnd + 4);
            sendNext();
          }
          // Decode as many complete unmasked frames as are buffered.
          while (buf.length >= 2) {
            const second = buf[1];
            const len7 = second & 0x7f;
            let offset = 2;
            let len = len7;
            if (len7 === 126) {
              if (buf.length < 4) break;
              len = buf.readUInt16BE(2);
              offset = 4;
            } else if (len7 === 127) {
              break; // not expected for CDP replies here; bail defensively
            }
            if (buf.length < offset + len) break;
            const payload = buf.subarray(offset, offset + len).toString('utf8');
            buf = buf.subarray(offset + len);
            try {
              const msg = JSON.parse(payload);
              if (msg.id && pending.has(msg.id)) {
                results.push(msg);
                pending.delete(msg.id);
                sendNext();
              }
            } catch (e) {
              // ignore non-JSON / partial frame noise
            }
          }
        });
        socket.on('error', reject);
        socket.setTimeout(10000, () => socket.destroy(new Error('CDP WS timeout')));
      })
      .catch(reject);
  });
}

async function resetBrowser() {
  const [ctxResult] = await cdpWsCall([{ method: 'Target.getBrowserContexts' }]);
  const contexts = (ctxResult && ctxResult.result && ctxResult.result.browserContextIds) || [];
  if (contexts.length === 0) return { closedContexts: 0 };
  const disposeCmds = contexts.map((id) => ({
    method: 'Target.disposeBrowserContext',
    params: { browserContextId: id },
  }));
  await cdpWsCall(disposeCmds);
  return { closedContexts: contexts.length };
}

async function health() {
  const version = await cdpHttpGet('/json/version').catch(() => null);
  let targetCount = null;
  try {
    const list = await cdpHttpGet('/json/list');
    targetCount = Array.isArray(list) ? list.length : null;
  } catch (e) {
    targetCount = null;
  }
  return {
    cdpAlive: version !== null,
    browser: version ? version.Browser : null,
    targetCount,
    adminUptimeSeconds: Math.floor((Date.now() - startedAt) / 1000),
  };
}

// ---------------------------------------------------------------------------
// Optional idle-recycle loop.
// ---------------------------------------------------------------------------
let idleSinceMs = null;
if (MAX_IDLE_S > 0) {
  setInterval(async () => {
    try {
      const list = await cdpHttpGet('/json/list');
      const count = Array.isArray(list) ? list.length : 0;
      if (count === 0) {
        if (idleSinceMs === null) idleSinceMs = Date.now();
        const idleForS = (Date.now() - idleSinceMs) / 1000;
        if (idleForS >= MAX_IDLE_S) {
          idleSinceMs = null;
          await restartChromiumProgram().catch(() => {});
        }
      } else {
        idleSinceMs = null;
      }
    } catch (e) {
      // CDP unreachable — leave idle tracking as-is, next tick retries.
    }
  }, IDLE_CHECK_INTERVAL_S * 1000).unref();
}

// ---------------------------------------------------------------------------
// HTTP server — closed endpoint set, no request body is ever read/parsed.
// ---------------------------------------------------------------------------
const server = http.createServer((req, res) => {
  const send = (code, obj) => {
    const body = JSON.stringify(obj);
    res.writeHead(code, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    res.end(body);
  };

  if (req.method === 'POST' && req.url === '/browser/reset') {
    resetBrowser()
      .then((r) => send(200, { ok: true, ...r }))
      .catch((e) => send(502, { ok: false, error: String(e && e.message || e) }));
    return;
  }
  if (req.method === 'POST' && req.url === '/browser/restart') {
    restartChromiumProgram()
      .then(() => send(200, { ok: true }))
      .catch((e) => send(502, { ok: false, error: String(e && e.message || e) }));
    return;
  }
  if (req.method === 'GET' && req.url === '/browser/health') {
    health()
      .then((h) => send(200, { ok: true, ...h }))
      .catch((e) => send(502, { ok: false, error: String(e && e.message || e) }));
    return;
  }
  send(404, { ok: false, error: 'not found' });
});

server.listen(ADMIN_PORT, '0.0.0.0', () => {
  // eslint-disable-next-line no-console
  console.log(`pwmcp admin-server listening on 0.0.0.0:${ADMIN_PORT} (CDP ${CDP_HOST}:${CDP_PORT})`);
});

#!/usr/bin/env node
'use strict';
/**
 * pwmcp ua-override -- shared-browser client-hint consistency (PWMCP-09).
 *
 * Chromium's --user-agent flag changes the UA string, but the client hints
 * (Sec-CH-UA header, navigator.userAgentData.brands) keep the browser's own brand,
 * which is "HeadlessChrome" in headless mode. When an override is configured this
 * process attaches to the shared browser over CDP (127.0.0.1 only), and for every page
 * target, existing and new, calls Emulation.setUserAgentOverride with the resolved UA
 * and its matching userAgentMetadata, both computed by pwmcp_user_agent.py (the single
 * source of truth) and handed over by entrypoint.sh as environment variables.
 *
 * Scope is deliberately only that: the UA string and its client-hint brands. No
 * navigator.webdriver, plugin, canvas or other fingerprint changes.
 *
 * With no override configured it logs one line and exits 0 (supervisord does not
 * restart a clean exit). It reconnects when Chromium restarts.
 *
 * Uses Node's global WebSocket (Node >= 22); it needs no dependency.
 */

const http = require('http');

const CDP_PORT = parseInt(process.env.PWMCP_CDP_PORT || '9222', 10);
const UA = process.env.PWMCP_USER_AGENT_RESOLVED || '';
const SOURCE = process.env.PWMCP_USER_AGENT_SOURCE || 'default';
const METADATA_RAW = process.env.PWMCP_USER_AGENT_METADATA || '';

function log(message) {
  process.stdout.write(`[ua-override] ${message}\n`);
}

function buildParams(ua, metadataRaw) {
  const params = { userAgent: ua };
  if (metadataRaw) params.userAgentMetadata = JSON.parse(metadataRaw);
  return params;
}

function browserWsUrl() {
  return new Promise((resolve, reject) => {
    http
      .get({ host: '127.0.0.1', port: CDP_PORT, path: '/json/version', timeout: 2000 }, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try { resolve(JSON.parse(body).webSocketDebuggerUrl); } catch (e) { reject(e); }
        });
      })
      .on('error', reject)
      .on('timeout', function onTimeout() { this.destroy(new Error('timeout')); });
  });
}

function session(wsUrl, params) {
  return new Promise((resolve) => {
    const ws = new WebSocket(wsUrl);
    let nextId = 1;
    const send = (method, p, sessionId) => {
      const msg = { id: nextId++, method, params: p || {} };
      if (sessionId) msg.sessionId = sessionId;
      ws.send(JSON.stringify(msg));
    };
    const applied = new Set();
    let getTargetsId = null;
    const onPage = (sessionId, targetId) => {
      if (!applied.has(targetId)) {
        applied.add(targetId);
        send('Emulation.setUserAgentOverride', params, sessionId);
        log(`applied to page target ${targetId}`);
      }
    };
    ws.onopen = () => {
      send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
      // Pages that existed before this process attached are not auto-attached: attach them.
      getTargetsId = nextId;
      send('Target.getTargets');
    };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(String(event.data)); } catch (_) { return; }
      if (msg.id !== undefined && msg.id === getTargetsId && msg.result) {
        for (const info of msg.result.targetInfos || []) {
          if (info.type === 'page') send('Target.attachToTarget', { targetId: info.targetId, flatten: true });
        }
      } else if (msg.method === 'Target.attachedToTarget') {
        const info = msg.params.targetInfo || {};
        if (info.type === 'page') onPage(msg.params.sessionId, info.targetId);
        // Every auto-attached session must be resumed, or the target stays paused.
        send('Runtime.runIfWaitingForDebugger', {}, msg.params.sessionId);
      }
    };
    ws.onclose = () => resolve();
    ws.onerror = () => { try { ws.close(); } catch (_) { /* already closed */ } };
  });
}

async function main() {
  if (!UA) {
    log('no user-agent override configured (source=default); nothing to do');
    return;
  }
  if (typeof WebSocket === 'undefined') {
    log('FATAL: this Node has no global WebSocket (needs Node >= 22)');
    process.exitCode = 1;
    return;
  }
  const params = buildParams(UA, METADATA_RAW);
  log(`active: source=${SOURCE} ua=${UA} client_hints=${METADATA_RAW ? 'matching' : 'not set (UA string only)'}`);
  for (;;) {
    try {
      const url = await browserWsUrl();
      await session(url, params);
      log('CDP connection closed; reconnecting');
    } catch (e) {
      // Chromium not up yet or restarting: retry.
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

module.exports = { buildParams };
if (require.main === module) {
  main();
}

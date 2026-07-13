#!/usr/bin/env node
/**
 * pwmcp Lighthouse MCP server — minimal vendored server.
 *
 * Provides two tools:
 *   lighthouse_audit(url, categories?, form_factor?)  — category scores + top opportunities
 *   lighthouse_metrics(url, form_factor?)              — core web vitals / timing metrics
 *
 * Reads CHROME_PATH from PWMCP_CHROMIUM_PATH env var (set by pwmcp entrypoint).
 * Audit timeout defaults to 120 s, configurable via LIGHTHOUSE_TIMEOUT_MS.
 * Response bounds: max 10 opportunities, max 100 KB per tool result.
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ErrorCode,
  ListToolsRequestSchema,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";

// ── Constants ──────────────────────────────────────────────────────────────
const SERVER_NAME = "pwmcp-lighthouse-mcp";
const SERVER_VERSION = "1.0.0";
const ALL_CATEGORIES = ["performance", "accessibility", "seo", "best-practices"];
const VALID_CATEGORIES = new Set(ALL_CATEGORIES);
const VALID_FORM_FACTORS = new Set(["mobile", "desktop"]);
const TIMEOUT_MS = Math.min(
  Math.max(parseInt(process.env.LIGHTHOUSE_TIMEOUT_MS || "120000", 10) || 120000, 10000),
  300000,
);
const MAX_OPPORTUNITIES = 10;
const MAX_RESPONSE_BYTES = 100 * 1024; // 100 KB

// Core Web Vital / timing metric IDs (as used in LHR audits)
const METRIC_IDS = [
  "largest-contentful-paint",
  "cumulative-layout-shift",
  "total-blocking-time",
  "first-contentful-paint",
  "speed-index",
  "interactive",
];

// ── URL validation ─────────────────────────────────────────────────────────
function validateUrl(raw) {
  if (typeof raw !== "string" || raw.trim().length === 0) {
    throw new McpError(ErrorCode.InvalidParams, "URL must be a non-empty string");
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new McpError(ErrorCode.InvalidParams, `Invalid URL: "${raw}"`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new McpError(
      ErrorCode.InvalidParams,
      `Unsupported URL scheme "${parsed.protocol}" — only http:// and https:// are allowed`,
    );
  }
  return parsed.href;
}

function validateCategories(cats) {
  if (cats === undefined || cats === null) return [...ALL_CATEGORIES];
  if (!Array.isArray(cats) || cats.length === 0) {
    throw new McpError(ErrorCode.InvalidParams, "categories must be a non-empty array if provided");
  }
  for (const c of cats) {
    if (!VALID_CATEGORIES.has(c)) {
      throw new McpError(
        ErrorCode.InvalidParams,
        `Unknown category "${c}". Valid: ${ALL_CATEGORIES.join(", ")}`,
      );
    }
  }
  return [...new Set(cats)]; // deduplicate
}

function validateFormFactor(ff) {
  if (ff === undefined || ff === null) return "mobile";
  if (!VALID_FORM_FACTORS.has(ff)) {
    throw new McpError(
      ErrorCode.InvalidParams,
      `Invalid form_factor "${ff}". Valid: mobile, desktop`,
    );
  }
  return ff;
}

// ── Response bounds ────────────────────────────────────────────────────────
function capResponse(data) {
  const serialized = JSON.stringify(data);
  if (Buffer.byteLength(serialized, "utf-8") > MAX_RESPONSE_BYTES) {
    // Truncate opportunities to fit within bounds
    if (data.opportunities && Array.isArray(data.opportunities)) {
      while (
        data.opportunities.length > 0 &&
        Buffer.byteLength(JSON.stringify(data), "utf-8") > MAX_RESPONSE_BYTES
      ) {
        data.opportunities.pop();
      }
    }
  }
  return data;
}

// ── Lighthouse runner ──────────────────────────────────────────────────────
async function runLighthouse(url, categories, formFactor, timeoutMs) {
  const chromePath = process.env.PWMCP_CHROMIUM_PATH;
  const chromeFlags = ["--headless", "--no-sandbox", "--disable-setuid-sandbox"];

  const chrome = await chromeLauncher.launch({
    chromePath: chromePath || undefined,
    chromeFlags,
    logLevel: "error",
  });

  // Timeout: kill Chrome if audit exceeds limit so no Chromium is pinned.
  // NOTE: chrome-launcher's kill() is not guaranteed to return a Promise in
  // every code path (e.g. once the process has already exited) — calling
  // `.catch()` on a non-Promise return value throws synchronously. Since this
  // runs inside a bare setTimeout callback (not awaited by anything), an
  // uncaught throw here is an unhandled exception that crashes the whole
  // Node process — taking down every in-flight audit and the MCP connection
  // itself, not just this one. Wrap defensively: coerce to a Promise via
  // Promise.resolve() and swallow any synchronous OR asynchronous failure.
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    try {
      Promise.resolve(chrome.kill()).catch(() => {});
    } catch {
      // Best-effort — never let timeout cleanup crash the server.
    }
  }, timeoutMs);

  try {
    const options = {
      port: chrome.port,
      output: "json",
      logLevel: "error",
      onlyCategories: categories,
      formFactor,
      // Mobile emulation
      screenEmulation: {
        mobile: formFactor === "mobile",
        width: formFactor === "desktop" ? 1350 : 412,
        height: formFactor === "desktop" ? 940 : 915,
        deviceScaleFactor: formFactor === "desktop" ? 1 : 2.625,
        disabled: false,
      },
      throttling: {
        // Mobile-like throttling for mobile, none for desktop
        rttMs: formFactor === "mobile" ? 150 : 0,
        throughputKbps: formFactor === "mobile" ? 1638.4 : 10 * 1024,
        cpuSlowdownMultiplier: formFactor === "mobile" ? 4 : 1,
      },
    };

    let runnerResult;
    try {
      runnerResult = await lighthouse(url, options, undefined);
    } catch (err) {
      if (timedOut) {
        throw new Error(`Lighthouse audit timed out after ${timeoutMs}ms`);
      }
      throw err;
    }

    if (timedOut) {
      throw new Error(`Lighthouse audit timed out after ${timeoutMs}ms`);
    }

    if (!runnerResult || !runnerResult.lhr) {
      throw new Error("Lighthouse returned no result");
    }

    return runnerResult.lhr;
  } finally {
    clearTimeout(timer);
    try {
      await Promise.resolve(chrome.kill());
    } catch {
      // Best-effort cleanup
    }
  }
}

function extractScores(lhr, categories) {
  const scores = {};
  for (const catId of categories) {
    const cat = lhr.categories[catId];
    if (cat) {
      // Lighthouse scores are 0-1 floats; multiply to 0-100 integer
      const scoreVal = cat.score !== null ? Math.round(cat.score * 100) : null;
      scores[catId] = scoreVal;
    }
  }
  return scores;
}

function extractOpportunities(lhr, categories) {
  const opps = [];
  for (const catId of categories) {
    const cat = lhr.categories[catId];
    if (!cat || !cat.auditRefs) continue;

    for (const ref of cat.auditRefs) {
      const audit = lhr.audits[ref.id];
      if (!audit || audit.score === null || audit.score === 1) continue;

      // An audit is an "opportunity" if its details.type === "opportunity"
      // or if it has numericValue and score < 1 (potential improvement)
      const isOpportunity =
        audit.details?.type === "opportunity" ||
        (audit.numericValue != null && audit.numericValue > 0 && audit.score !== null && audit.score < 1);

      if (isOpportunity) {
        const estimatedSavings =
          audit.metricSavings?.LCP ||
          audit.metricSavings?.TBT ||
          (audit.details?.overallSavingsMs) ||
          audit.numericValue ||
          0;

        opps.push({
          id: audit.id,
          title: audit.title,
          score: audit.score !== null ? Math.round(audit.score * 100) : null,
          estimatedSavingsMs: Math.round(estimatedSavings),
          category: catId,
        });
      }
    }
  }

  // Sort by estimated savings descending, take top N
  opps.sort((a, b) => b.estimatedSavingsMs - a.estimatedSavingsMs);
  return opps.slice(0, MAX_OPPORTUNITIES);
}

function extractMetrics(lhr) {
  const metrics = {};
  for (const metricId of METRIC_IDS) {
    const audit = lhr.audits[metricId];
    if (!audit) continue;
    metrics[metricId] = {
      title: audit.title,
      value: audit.numericValue ?? null,
      displayValue: audit.displayValue ?? null,
      score: audit.score !== null ? Math.round(audit.score * 100) : null,
    };
  }
  return metrics;
}

function safeToolError(message) {
  return {
    content: [{ type: "text", text: message }],
    isError: true,
  };
}

// ── Crash safety net ──────────────────────────────────────────────────────
// Defense in depth: no audit-triggered exception (e.g. a bug in cleanup code
// running outside a tool-call's try/catch, such as a setTimeout callback)
// should be able to crash the whole process and take down every in-flight
// audit plus the MCP connection itself. Per the handoff's failure-safety
// contract, log safely (stderr only — never proxied to the client as a tool
// result) and keep serving.
process.on("uncaughtException", (err) => {
  process.stderr.write(`[lighthouse-mcp] uncaughtException: ${err?.message || err}\n`);
});
process.on("unhandledRejection", (reason) => {
  process.stderr.write(`[lighthouse-mcp] unhandledRejection: ${reason?.message || reason}\n`);
});

// ── MCP Server ─────────────────────────────────────────────────────────────
const server = new Server(
  { name: SERVER_NAME, version: SERVER_VERSION },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "lighthouse_audit",
      description:
        "Run a Lighthouse audit on a URL. Returns per-category scores (0-100) " +
        "for performance, accessibility, seo, best-practices, plus the top opportunities " +
        "with estimated savings. Response capped at 100 KB / 10 opportunities.",
      inputSchema: {
        type: "object",
        properties: {
          url: {
            type: "string",
            description: "URL to audit (http:// or https:// only)",
          },
          categories: {
            type: "array",
            items: {
              type: "string",
              enum: ALL_CATEGORIES,
            },
            description:
              "Categories to audit (default: all four). Valid: performance, accessibility, seo, best-practices",
          },
          form_factor: {
            type: "string",
            enum: ["mobile", "desktop"],
            description: 'Device form factor (default: "mobile")',
          },
        },
        required: ["url"],
      },
    },
    {
      name: "lighthouse_metrics",
      description:
        "Run a Lighthouse audit and return just the core web vitals / timing metrics " +
        "(LCP, CLS, TBT, FCP, SI, TTI) with values and scores. Lighter weight than " +
        "lighthouse_audit — no opportunities.",
      inputSchema: {
        type: "object",
        properties: {
          url: {
            type: "string",
            description: "URL to audit (http:// or https:// only)",
          },
          form_factor: {
            type: "string",
            enum: ["mobile", "desktop"],
            description: 'Device form factor (default: "mobile")',
          },
        },
        required: ["url"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    switch (name) {
      case "lighthouse_audit": {
        const url = validateUrl(args?.url);
        const categories = validateCategories(args?.categories);
        const formFactor = validateFormFactor(args?.form_factor);

        const lhr = await runLighthouse(url, categories, formFactor, TIMEOUT_MS);
        const scores = extractScores(lhr, categories);
        const opportunities = extractOpportunities(lhr, categories);
        const finalUrl = lhr.finalDisplayedUrl || lhr.finalUrl || url;

        const result = capResponse({
          url: finalUrl,
          lighthouseVersion: lhr.lighthouseVersion,
          fetchTime: lhr.fetchTime,
          scores,
          opportunities,
        });

        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
        };
      }

      case "lighthouse_metrics": {
        const url = validateUrl(args?.url);
        const formFactor = validateFormFactor(args?.form_factor);

        // Only need performance category for metrics
        const lhr = await runLighthouse(url, ["performance"], formFactor, TIMEOUT_MS);
        const metrics = extractMetrics(lhr);
        const scores = extractScores(lhr, ["performance"]);
        const finalUrl = lhr.finalDisplayedUrl || lhr.finalUrl || url;

        const result = capResponse({
          url: finalUrl,
          lighthouseVersion: lhr.lighthouseVersion,
          fetchTime: lhr.fetchTime,
          performanceScore: scores.performance ?? null,
          metrics,
        });

        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
        };
      }

      default:
        throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
    }
  } catch (err) {
    if (err instanceof McpError) throw err;
    // Runtime errors → safe tool error (no stack traces)
    const message = err?.message || "Lighthouse audit failed";
    // Sanitize: strip any paths or internal details
    const safe = message
      .replace(/\/[^\s:]*/g, "[path]") // strip absolute paths
      .replace(/\(.*?\)/g, "") // strip parenthesized details
      .trim();
    return safeToolError(`Audit failed: ${safe || "unknown error"}`);
  }
});

// ── Start ──────────────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);

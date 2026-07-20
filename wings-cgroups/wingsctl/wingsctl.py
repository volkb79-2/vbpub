#!/usr/bin/env python3
"""wingsctl — tiny CLI for the Pterodactyl Wings node API.

Talks directly to wings with the node token from /etc/pterodactyl/config.yml,
so servers can be controlled when the panel is unreachable. The token is read
at runtime and never printed.

Usage:
  wingsctl.py list                          # all servers on this node (uuid + state)
  wingsctl.py status <uuid>                 # state / suspension / resource use
  wingsctl.py power  <uuid> <action>        # action: start | stop | restart | kill
  wingsctl.py logs   <uuid> [lines]         # recent console output

Options (all commands):
  --url URL       wings base URL (default: env WINGS_URL, else
                  https://127.0.0.1:8080). Point it at the node's public
                  route if you are not running this on the node itself.
  --config PATH   wings config.yml (default: /etc/pterodactyl/config.yml)
  --insecure      skip TLS verification (only if the cert chain is broken)

Notes:
  * "stop" sets the DESIRED state to offline — wings will not crash-restart
    the server afterwards (unlike stopping the game from inside via RCON).
  * Power requests return HTTP 202 immediately; the actual shutdown runs the
    egg's configured stop sequence and may take a while. Poll with `status`.
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("WINGS_URL", "https://127.0.0.1:8080")
DEFAULT_CONFIG = "/etc/pterodactyl/config.yml"


def read_node_tokens(config_path):
    """Bearer candidates from wings' config.yml (top-level keys only).

    Wings v1 authorizes the node API against the bare `token` value; some
    older builds expect `token_id.token` — try both, bare token first.
    """
    token_id = token = None
    with open(config_path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^token_id:\s*(\S+)\s*$", line)
            if m:
                token_id = m.group(1).strip("'\"")
            m = re.match(r"^token:\s*(\S+)\s*$", line)
            if m:
                token = m.group(1).strip("'\"")
    if not token:
        sys.exit(f"error: token not found in {config_path} (need root?)")
    return [token] + ([f"{token_id}.{token}"] if token_id else [])


def request(base_url, bearers, method, path, body=None, insecure=False):
    url = base_url.rstrip("/") + path
    ctx = ssl._create_unverified_context() if insecure else None
    status, raw = None, ""
    for bearer in bearers:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {bearer}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return resp.status, resp.read().decode() or ""
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read().decode()
            if status not in (401, 403):
                return status, raw  # real answer, not an auth-format problem
        except urllib.error.URLError as exc:
            sys.exit(f"error: cannot reach wings at {url}: {exc.reason}")
    return status, raw


def summarize_server(item):
    """Print only non-sensitive fields (server configs embed egg env vars)."""
    if not isinstance(item, dict):
        print(f"  (unexpected entry: {type(item).__name__})")
        return
    cfg = item.get("configuration", item.get("settings", item))
    uuid = item.get("uuid") or (cfg.get("uuid") if isinstance(cfg, dict) else None)
    state = item.get("state", "?")
    suspended = item.get("is_suspended", cfg.get("suspended", "?") if isinstance(cfg, dict) else "?")
    util = item.get("utilization") or {}
    mem = util.get("memory_bytes")
    mem_s = f" mem={mem / 1048576:.0f}MiB" if isinstance(mem, (int, float)) else ""
    print(f"  {uuid}  state={state} suspended={suspended}{mem_s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--insecure", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("status"); p.add_argument("uuid")
    p = sub.add_parser("power"); p.add_argument("uuid")
    p.add_argument("action", choices=["start", "stop", "restart", "kill"])
    p = sub.add_parser("logs"); p.add_argument("uuid")
    p.add_argument("lines", nargs="?", type=int, default=50)
    args = ap.parse_args()

    bearers = read_node_tokens(args.config)

    if args.cmd == "list":
        status, raw = request(args.url, bearers, "GET", "/api/servers", insecure=args.insecure)
        if status != 200:
            sys.exit(f"HTTP {status}: {raw[:300]}")
        data = json.loads(raw)
        items = data if isinstance(data, list) else data.get("data", [data])
        print(f"{len(items)} server(s):")
        for item in items:
            summarize_server(item)

    elif args.cmd == "status":
        status, raw = request(args.url, bearers, "GET", f"/api/servers/{args.uuid}", insecure=args.insecure)
        if status != 200:
            sys.exit(f"HTTP {status}: {raw[:300]}")
        summarize_server(json.loads(raw))

    elif args.cmd == "power":
        status, raw = request(args.url, bearers, "POST", f"/api/servers/{args.uuid}/power",
                              body={"action": args.action}, insecure=args.insecure)
        if status in (200, 202, 204):
            print(f"accepted: {args.action} -> {args.uuid} (HTTP {status}); poll with: wingsctl.py status {args.uuid}")
        else:
            sys.exit(f"HTTP {status}: {raw[:300]}")

    elif args.cmd == "logs":
        status, raw = request(args.url, bearers, "GET", f"/api/servers/{args.uuid}/logs?size={args.lines}",
                              insecure=args.insecure)
        if status != 200:
            sys.exit(f"HTTP {status}: {raw[:300]}")
        try:
            for line in json.loads(raw).get("data", []):
                print(line)
        except (json.JSONDecodeError, AttributeError):
            print(raw)


if __name__ == "__main__":
    main()

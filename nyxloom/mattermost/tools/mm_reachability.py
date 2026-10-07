#!/usr/bin/env python3
"""mm_reachability.py -- diagnose reachability of the ciu-managed Mattermost stack.

Everything is discovered at runtime from the stack's rendered ``ciu.toml``, the
secret files under ``<stack>/.ciu/secrets/`` and Docker; nothing is hard-coded.

What it checks (see ``--help`` for flags):
  * container state/health/networks (``docker inspect``)
  * ``GET /api/v4/system/ping`` from this process (external + internal base)
    and from a throwaway curl container on EACH network the app is attached to
  * every configured webhook secret: URL anatomy (host/port/hook-id prefix
    only), STALE HOST detection (internal host != current container name, or
    siteurl host != server SiteURL host) and base reachability from the vantage
    that would use it; posts only with ``--post``
  * every configured PAT: ``GET /api/v4/users/me`` externally and internally
  * every configured account: exists, active, admin role, team/channel
    membership, via any working PAT (passwords are ciu ``GEN_LOCAL`` secrets in
    the project store and are deliberately never read; no password login test)

No secret value (token, hook id, password) is ever printed: every secret that
is loaded is registered with a redactor that scrubs the final table/JSON text,
including error messages, and a generic 26-char Mattermost-id pattern is
scrubbed as a safety net.

Exit codes: 0 all pass, 1 any FAIL, 2 usage/config error.
Stdlib only; Python 3.11+.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import re
import socket
import string
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
DEFAULT_IMAGE = "curlimages/curl:latest"
ID_RE = re.compile(r"(?<![A-Za-z0-9])[a-z0-9]{26}(?![A-Za-z0-9])")
BEARER_RE = re.compile(r"(?i)(bearer\s+)\S+")


class ConfigError(Exception):
    """Usage or stack-config problem (exit code 2)."""


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------
class Redactor:
    """Scrubs registered secrets plus generic id/bearer patterns from text."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, value: str | None) -> None:
        if value and len(value) >= 4:
            self._secrets.add(value)

    def scrub(self, text: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(s, "<redacted>")
        text = BEARER_RE.sub(r"\1<redacted>", text)
        return ID_RE.sub("<id-redacted>", text)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
@dataclasses.dataclass
class Result:
    check: str
    vantage: str
    status: str
    detail: str


@dataclasses.dataclass
class Request:
    key: Any
    method: str
    url: str
    headers: dict[str, str] = dataclasses.field(default_factory=dict)
    body: str | None = None


@dataclasses.dataclass
class Response:
    status: int  # 0 = no HTTP response
    body: str = ""
    ms: float = 0.0
    error: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


@dataclasses.dataclass
class Vantage:
    label: str
    network: str | None  # None = this process


DockerFn = Callable[..., tuple[int, str, str]]


@dataclasses.dataclass
class Env:
    """Side-effect seams (docker, local HTTP, DNS, hostname, clock) for tests."""

    docker: DockerFn
    http: Callable[[Request, float], Response]
    resolve: Callable[[str], bool]
    hostname: Callable[[], str]
    now: Callable[[], datetime]


def _docker(args: list[str], stdin: str | None = None, timeout: float = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["docker", *args], input=stdin, capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", "docker CLI not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"docker {args[0]} timed out after {timeout:.0f}s"
    return p.returncode, p.stdout, p.stderr


def _http(req: Request, timeout: float) -> Response:
    r = urllib.request.Request(req.url, method=req.method, headers=dict(req.headers),
                               data=req.body.encode() if req.body is not None else None)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(1 << 20).decode("utf-8", "replace")
            return Response(resp.status, body, (time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as exc:
        body = exc.read(1 << 20).decode("utf-8", "replace")
        return Response(exc.code, body, (time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 - network errors of every kind
        reason = getattr(exc, "reason", exc)
        return Response(0, "", (time.monotonic() - t0) * 1000,
                        f"{type(exc).__name__}: {reason}")


def _resolve(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def default_env() -> Env:
    return Env(_docker, _http, _resolve, socket.gethostname,
               lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------
# config + secrets
# --------------------------------------------------------------------------
def load_config(stack_dir: Path) -> dict:
    path = stack_dir / "ciu.toml"
    if not path.is_file():
        raise ConfigError(f"{path} not found (pass --stack-dir)")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    mm = data.get("mattermost")
    if not isinstance(mm, dict) or not mm.get("container_prefix"):
        raise ConfigError(f"{path}: [mattermost] container_prefix missing")
    return mm


def read_secret(stack_dir: Path, name: str) -> str | None:
    try:
        value = (stack_dir / ".ciu" / "secrets" / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def describe_hook(url: str) -> dict[str, Any]:
    """Anatomy of a webhook URL; the display form never carries more than the
    first four characters of the hook id."""
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError:
        port = None
    port = port or {"http": 80, "https": 443}.get(p.scheme)
    host = p.hostname or ""
    m = re.fullmatch(r"/hooks/([A-Za-z0-9]+)", p.path)
    hid = m.group(1) if m else None
    base = f"{p.scheme}://{p.netloc}"
    tail = f"/hooks/{hid[:4]}…" if hid else "/<unrecognised path>"
    return {"scheme": p.scheme, "host": host, "port": port, "id": hid, "base": base,
            "display": f"{p.scheme}://{host}:{port}{tail}"}


# --------------------------------------------------------------------------
# vantage execution
# --------------------------------------------------------------------------
def _cq(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_curl_config(reqs: list[Request], marker: str, timeout: float) -> str:
    """curl ``-K -`` config, one block per request. Requests (URLs, bearer
    tokens, bodies) travel over stdin so they never appear in any argv."""
    blocks = []
    for r in reqs:
        lines = [f"url = {_cq(r.url)}", f"request = {_cq(r.method)}",
                 f"max-time = {int(timeout)}", 'output = "-"']
        lines += [f"header = {_cq(f'{k}: {v}')}" for k, v in r.headers.items()]
        if r.body is not None:
            lines.append(f"data = {_cq(r.body)}")
        lines.append(f'write-out = "\\n{marker} %{{http_code}} %{{time_total}} '
                     f'%{{exitcode}} %{{errormsg}}\\n"')
        blocks.append("\n".join(lines))
    return "\nnext\n".join(blocks) + "\n"


def parse_curl_output(out: str, marker: str, reqs: list[Request], fail: str) -> dict[Any, Response]:
    parts = out.split(marker)
    if len(parts) != len(reqs) + 1:
        return {r.key: Response(0, error=fail or "probe output unparseable") for r in reqs}
    res: dict[Any, Response] = {}
    for i, r in enumerate(reqs):
        body = parts[i]
        if i:
            body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body[:-1] if body.endswith("\n") else body
        meta = parts[i + 1].split("\n", 1)[0].split(None, 3)
        try:
            code, secs = int(meta[0]), float(meta[1])
        except (IndexError, ValueError):
            res[r.key] = Response(0, error="probe meta unparseable")
            continue
        msg = meta[3].strip() if len(meta) > 3 else ""
        res[r.key] = Response(code, body, secs * 1000,
                              "" if code else (msg or f"curl exit {meta[2] if len(meta) > 2 else '?'}"))
    return res


def run_requests(env: Env, v: Vantage, reqs: list[Request], image: str,
                 timeout: float) -> dict[Any, Response]:
    if not reqs:
        return {}
    if v.network is None:
        return {r.key: env.http(r, timeout) for r in reqs}
    marker = "@@mmr" + "".join(random.choices(string.ascii_lowercase, k=8)) + "@@"
    name = f"mmreach-{os.getpid()}-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    argv = ["run", "--rm", "-i", "--name", name, "--network", v.network,
            "--memory", "64m", "--cpus", "0.5", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", image, "-sS", "-K", "-"]
    rc, out, err = env.docker(argv, build_curl_config(reqs, marker, timeout),
                              timeout * len(reqs) + 30)
    if rc == 124:
        env.docker(["rm", "-f", name], None, 30)  # exact name only
    fail = f"probe container failed (rc={rc}): {(err.strip().splitlines() or [''])[0]}" if rc else ""
    return parse_curl_output(out, marker, reqs, fail)


def ensure_image(env: Env, image: str) -> str | None:
    """Pull only if missing. Returns an error string or None."""
    if env.docker(["image", "inspect", image], None, 30)[0] == 0:
        return None
    rc, _, err = env.docker(["pull", "-q", image], None, 300)
    return None if rc == 0 else f"cannot pull {image}: {(err.strip().splitlines() or [''])[0]}"


# --------------------------------------------------------------------------
# the checker
# --------------------------------------------------------------------------
def eval_ping(resp: Response) -> tuple[bool, str]:
    if resp.status == 0:
        return False, f"no response: {resp.error}"
    if resp.status != 200:
        return False, f"HTTP {resp.status}"
    data = resp.json()
    if not isinstance(data, dict) or data.get("status") != "OK":
        return False, f"HTTP 200 but body is not status OK ({len(resp.body)} bytes)"
    return True, f"200 OK in {resp.ms:.0f} ms"


def bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def run(args: argparse.Namespace, env: Env, red: Redactor) -> tuple[list[Result], dict]:
    stack = Path(args.stack_dir).resolve()
    mm = load_config(stack)
    results: list[Result] = []

    def add(check: str, vantage: str, status: str, detail: str) -> None:
        results.append(Result(check, vantage, status, detail))

    app = mm.get("app") if isinstance(mm.get("app"), dict) else {}
    prov = mm.get("provision") if isinstance(mm.get("provision"), dict) else {}
    container = f"{mm['container_prefix']}-{app.get('name', 'mattermost')}"
    port = app.get("port", 8065)
    expose = bool(mm.get("expose_public"))
    public_host = mm.get("public_host") or ""
    internal_base = f"http://{container}:{port}"
    external_base = f"https://{public_host}" if expose and public_host else None

    disc: dict[str, Any] = {"stack_dir": str(stack), "container": container,
                            "internal_base": internal_base, "external_base": external_base,
                            "networks": [], "site_url": None, "image": args.image}

    # ---- container ----
    nets: list[str] = []
    rc, out, err = env.docker(["inspect", container], None, 30)
    try:
        info = json.loads(out)[0] if rc == 0 else None
    except (ValueError, IndexError):
        info = None
    if info is None:
        add("container", "docker", FAIL,
            f"{container}: docker inspect failed: {(err.strip().splitlines() or ['not found'])[0]}")
    else:
        state = info.get("State", {})
        health = (state.get("Health") or {}).get("Status", "none")
        nets = sorted((info.get("NetworkSettings") or {}).get("Networks", {}) or {})
        disc["networks"] = nets
        ok = state.get("Status") == "running" and health in ("healthy", "none")
        add("container", "docker", PASS if ok else FAIL,
            f"{container}: state={state.get('Status')} health={health} networks={','.join(nets) or '-'}")
        ing = mm.get("ingress_network")
        if expose and ing:
            add("ingress network", "docker", PASS if ing in nets else FAIL,
                f"{ing} {'attached' if ing in nets else 'NOT attached'}")

    # ---- is this process on a container network? ----
    on_net, why = False, "name does not resolve here and this host is on none of its networks"
    if nets:
        rc, out, _ = env.docker(["inspect", env.hostname()], None, 30)
        try:
            mine = set(((json.loads(out)[0].get("NetworkSettings") or {}).get("Networks") or {})) if rc == 0 else set()
        except (ValueError, IndexError, AttributeError):
            mine = set()
        if mine & set(nets):
            on_net, why = True, "shares a network with the container"
        elif env.resolve(container):
            on_net, why = True, "container name resolves here"
    disc["local_on_container_network"] = on_net

    # ---- probe image ----
    net_vantages = [Vantage(f"net:{n}", n) for n in nets]
    if net_vantages:
        perr = ensure_image(env, args.image)
        if perr:
            add("probe image", "docker", INFO, f"{perr}; docker-network vantages skipped")
            net_vantages = []
    local = Vantage("local", None)
    vantages = [local, *net_vantages]

    # ---- secrets ----
    hooks: list[dict] = []
    for spec in prov.get("webhooks", []) or []:
        name = spec.get("secret", "?")
        url = read_secret(stack, name)
        if url is None:
            add(f"webhook {name}", "config", FAIL, "secret file missing or empty")
            continue
        red.add(url)
        d = describe_hook(url)
        red.add(d["id"])
        external = spec.get("url_base") == "siteurl" and expose
        hooks.append({"spec": spec, "name": name, "d": d, "external": external,
                      "url": url})
    toks: list[dict] = []
    for spec in prov.get("tokens", []) or []:
        name = spec.get("secret", "?")
        tok = read_secret(stack, name)
        if tok is None:
            add(f"pat {name}", "config", FAIL, "secret file missing or empty")
            continue
        red.add(tok)
        toks.append({"spec": spec, "name": name, "tok": tok})

    # ---- phase 1: build requests per vantage ----
    reqs: dict[str, list[Request]] = {v.label: [] for v in vantages}

    def ping_url(base: str) -> str:
        return f"{base}/api/v4/system/ping"

    if external_base:
        reqs["local"] += [Request(("ping", "external"), "GET", ping_url(external_base)),
                          Request(("cfg", "external"), "GET", f"{external_base}/api/v4/config/client?format=old")]
    reqs["local"].append(Request(("ping", "internal"), "GET", ping_url(internal_base)))
    for v in net_vantages:
        reqs[v.label] += [Request(("ping", "internal"), "GET", ping_url(internal_base)),
                          Request(("cfg", "internal"), "GET", f"{internal_base}/api/v4/config/client?format=old")]
    for h in hooks:
        if not h["d"]["host"]:
            continue
        r = Request(("hook", h["name"]), "GET", ping_url(h["d"]["base"]))
        if h["external"]:
            reqs["local"].append(r)
        else:
            for v in net_vantages:
                reqs[v.label].append(r)
            if on_net:
                reqs["local"].append(r)
    for t in toks:
        r = lambda base, t=t: Request(("pat", t["name"]), "GET", f"{base}/api/v4/users/me", bearer(t["tok"]))
        if external_base:
            reqs["local"].append(r(external_base))
        for v in net_vantages:
            reqs[v.label].append(r(internal_base))

    res: dict[str, dict[Any, Response]] = {}
    for v in vantages:
        res[v.label] = run_requests(env, v, reqs[v.label], args.image, args.timeout)

    # ---- pings ----
    def ping_row(label: str, kind: str, resp: Response) -> None:
        ok, detail = eval_ping(resp)
        base = external_base if kind == "external" else internal_base
        if ok:
            add(f"ping {kind}", label, PASS, f"{base}: {detail}")
        elif kind == "internal" and label == "local" and not on_net:
            add(f"ping {kind}", label, INFO,
                f"{base}: {detail}; this process is not on any of the container's networks "
                f"({why}), so internal reachability is not applicable here")
        else:
            add(f"ping {kind}", label, FAIL, f"{base}: {detail}")

    if external_base:
        ping_row("local", "external", res["local"][("ping", "external")])
    else:
        add("ping external", "local", INFO, "expose_public is off; no external URL")
    ping_row("local", "internal", res["local"][("ping", "internal")])
    for v in net_vantages:
        ping_row(v.label, "internal", res[v.label][("ping", "internal")])
    if not nets:
        add("ping internal", "docker-network", INFO, "no container networks to probe from")

    # ---- SiteURL ----
    site_url = None
    for label, key in [("local", ("cfg", "external"))] + [(v.label, ("cfg", "internal")) for v in net_vantages]:
        cfg = res.get(label, {}).get(key)
        data = cfg.json() if cfg and cfg.status == 200 else None
        if isinstance(data, dict) and data.get("SiteURL") is not None:
            site_url = str(data["SiteURL"])
            break
    disc["site_url"] = site_url
    site_host = urlsplit(site_url).hostname if site_url else None
    if site_url is None:
        add("SiteURL", "server", INFO, "could not read /api/v4/config/client from any vantage")
    elif not site_host:
        add("SiteURL", "server", INFO, "SiteURL is empty on the server")
    elif expose and public_host and site_host != public_host:
        add("SiteURL", "server", FAIL, f"server SiteURL host {site_host} != configured public_host {public_host}")
    else:
        add("SiteURL", "server", PASS, f"{site_url}")

    # ---- webhooks ----
    for h in hooks:
        d, name, spec = h["d"], h["name"], h["spec"]
        kind = "external" if h["external"] else "internal"
        base_kind = spec.get("url_base", "internal")
        if not d["id"] or not d["host"]:
            add(f"webhook {name}", "config", FAIL, f"malformed URL ({d['display']})")
            continue
        problems = []
        if base_kind == "siteurl":
            if site_host and d["host"] != site_host:
                problems.append(f"STALE HOST {d['host']} != SiteURL host {site_host}")
        elif d["host"] != container:
            problems.append(f"STALE HOST {d['host']} != current container {container}")
        if base_kind != "siteurl" and d["port"] != port:
            problems.append(f"port {d['port']} != app port {port}")
        add(f"webhook {name}", "config", FAIL if problems else PASS,
            f"{d['display']} url_base={base_kind}" + (f"; {'; '.join(problems)}" if problems else ""))
        targets = [local] if h["external"] else ([local] if on_net else []) + net_vantages
        for v in targets:
            ok, detail = eval_ping(res[v.label][("hook", name)])
            add(f"webhook {name} base", v.label, PASS if ok else FAIL,
                f"{d['scheme']}://{d['host']}:{d['port']} ({kind}): {detail}")
        if not targets:
            add(f"webhook {name} base", "-", INFO, f"no vantage can test an {kind} hook base")

    # ---- PATs ----
    readers: list[str] = []
    for t in toks:
        expect = t["spec"].get("user")
        for v in vantages:
            resp = res[v.label].get(("pat", t["name"]))
            if resp is None:
                continue
            data = resp.json() if resp.status == 200 else None
            if resp.status == 200 and isinstance(data, dict) and data.get("username") == expect:
                add(f"pat {t['name']}", v.label, PASS, f"users/me 200, username={expect}")
                if t["tok"] not in readers:
                    readers.append(t["tok"])
            elif resp.status == 200:
                got = data.get("username") if isinstance(data, dict) else "?"
                add(f"pat {t['name']}", v.label, FAIL, f"users/me 200 but username={got!r}, expected {expect!r}")
            elif resp.status in (401, 403):
                add(f"pat {t['name']}", v.label, FAIL, f"users/me HTTP {resp.status}: token rejected")
            else:
                add(f"pat {t['name']}", v.label, FAIL,
                    f"users/me {'HTTP ' + str(resp.status) if resp.status else 'no response: ' + resp.error}")

    # ---- accounts ----
    accounts = prov.get("accounts", []) or []
    data_v = local if external_base and res["local"][("ping", "external")].status == 200 else (
        net_vantages[0] if net_vantages else None)
    if accounts:
        verify_accounts(env, args, add, accounts, prov, readers, data_v, external_base or internal_base)

    disc["vantages"] = [v.label for v in vantages]

    # ---- optional post ----
    if args.post:
        stamp = env.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        body = json.dumps({"text": f"[mm_reachability test {stamp}]"})
        for h in hooks:
            if not h["d"]["id"] or not h["d"]["host"]:
                continue
            v = local if h["external"] else (local if on_net else (net_vantages[0] if net_vantages else None))
            if v is None:
                add(f"webhook {h['name']} post", "-", INFO, "no vantage can reach an internal hook")
                continue
            r = Request("post", "POST", h["url"], {"Content-Type": "application/json"}, body)
            resp = run_requests(env, v, [r], args.image, args.timeout)["post"]
            add(f"webhook {h['name']} post", v.label, PASS if resp.status == 200 else FAIL,
                f"POST {h['d']['display']}: " + (f"HTTP {resp.status}" if resp.status else f"no response: {resp.error}"))
    return results, disc


def verify_accounts(env: Env, args: argparse.Namespace, add: Callable[..., None],
                    accounts: list[dict], prov: dict, readers: list[str],
                    v: Vantage | None, base: str) -> None:
    label = v.label if v else "-"
    if v is None or not readers:
        for a in accounts:
            add(f"account {a.get('username')}", label, INFO,
                "not verifiable: no vantage" if v is None else "not verifiable: no working PAT can read it")
        return

    def with_readers(items: list[Any], make: Callable[[Any], Request],
                     ok: tuple[int, ...]) -> dict[Any, Response]:
        got: dict[Any, Response] = {}
        last: dict[Any, Response] = {}
        pending = list(items)
        for tok in readers:
            if not pending:
                break
            rq = []
            for it in pending:
                r = make(it)
                r.key, r.headers = it, bearer(tok)
                rq.append(r)
            out = run_requests(env, v, rq, args.image, args.timeout)
            for it in pending:
                last[it] = out[it]
                if out[it].status in ok:
                    got[it] = out[it]
            pending = [it for it in pending if it not in got]
        return {**last, **got}

    names = [a["username"] for a in accounts]
    users = with_readers(names, lambda n: Request(n, "GET", f"{base}/api/v4/users/username/{quote(n)}"), (200, 404))
    team = prov.get("team")
    tr = with_readers([team], lambda t: Request(t, "GET", f"{base}/api/v4/teams/name/{quote(t)}"), (200,)) if team else {}
    tdata = tr.get(team).json() if team and tr.get(team) and tr[team].status == 200 else None
    tid = tdata.get("id") if isinstance(tdata, dict) else None
    chan_names = [c["name"] for c in prov.get("channels", []) or []]
    chans: dict[str, Response] = {}
    if tid:
        chans = with_readers(chan_names, lambda c: Request(c, "GET", f"{base}/api/v4/teams/{tid}/channels/name/{quote(c)}"), (200,))
    cid = {c: r.json().get("id") for c, r in chans.items()
           if r.status == 200 and isinstance(r.json(), dict)}
    uid: dict[str, str] = {}
    for a in accounts:
        r = users.get(a["username"])
        d = r.json() if r and r.status == 200 else None
        if isinstance(d, dict) and d.get("id"):
            uid[a["username"]] = d["id"]
    tm = with_readers([n for n in uid], lambda n: Request(n, "GET", f"{base}/api/v4/teams/{tid}/members/{uid[n]}"), (200, 404)) if tid else {}
    cm_items = []
    for a in accounts:
        want = chan_names if a.get("all_channels") else list(a.get("channels", []) or [])
        cm_items += [(a["username"], c) for c in want if a["username"] in uid and c in cid]
    cm = with_readers(cm_items, lambda it: Request(it, "GET", f"{base}/api/v4/channels/{cid[it[1]]}/members/{uid[it[0]]}"), (200, 404)) if cm_items else {}

    for a in accounts:
        n = a["username"]
        r = users.get(n)
        d = r.json() if r and r.status == 200 else None
        if r is not None and r.status == 404 and n not in uid:
            add(f"account {n}", label, FAIL, "user does not exist (404 from an authenticated lookup)")
            continue
        if not isinstance(d, dict):
            add(f"account {n}", label, INFO, f"not verifiable: lookup HTTP {r.status if r else '?'} with available PATs")
            continue
        add(f"account {n}", label, PASS if d.get("delete_at", 0) == 0 else FAIL,
            "exists, active" if d.get("delete_at", 0) == 0 else f"exists but deactivated (delete_at={d.get('delete_at')})")
        if "roles" in d:
            is_admin = "system_admin" in str(d["roles"]).split()
            want_admin = bool(a.get("system_admin"))
            add(f"account {n} role", label, PASS if is_admin == want_admin else FAIL,
                f"system_admin={is_admin}, expected {want_admin}")
        else:
            add(f"account {n} role", label, INFO, "not verifiable: roles not visible")
        if team:
            t = tm.get(n)
            if not tid:
                add(f"account {n} team {team}", label, INFO, "not verifiable: team not readable with available PATs")
            elif t is not None and t.status == 200:
                add(f"account {n} team {team}", label, PASS, "member")
            elif t is not None and t.status == 404:
                add(f"account {n} team {team}", label, FAIL, "not a member")
            else:
                add(f"account {n} team {team}", label, INFO, "not verifiable")
        want = chan_names if a.get("all_channels") else list(a.get("channels", []) or [])
        for c in want:
            if c not in cid:
                add(f"account {n} channel {c}", label, INFO,
                    "not verifiable: channel not readable with available PATs (private channel?)")
                continue
            m = cm.get((n, c))
            if m is not None and m.status == 200:
                add(f"account {n} channel {c}", label, PASS, "member")
            elif m is not None and m.status == 404:
                add(f"account {n} channel {c}", label, FAIL, "not a member")
            else:
                add(f"account {n} channel {c}", label, INFO, "not verifiable")


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def render_table(results: list[Result], disc: dict) -> str:
    rows = [("CHECK", "VANTAGE", "RESULT", "DETAIL")] + [
        (r.check, r.vantage, r.status, r.detail) for r in results]
    w = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = [f"mm_reachability: container={disc.get('container')} "
             f"internal={disc.get('internal_base')} external={disc.get('external_base')} "
             f"siteurl={disc.get('site_url')}"]
    for row in rows:
        lines.append("  ".join(row[i].ljust(w[i]) for i in range(3)) + "  " + row[3])
    c = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, INFO)}
    lines.append(f"\nSUMMARY: {c[PASS]} PASS, {c[FAIL]} FAIL, {c[INFO]} INFO")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mm_reachability.py",
        description="Check reachability of the ciu-managed Mattermost stack (dynamic: "
                    "ciu.toml + .ciu/secrets + docker). Never prints secrets.")
    p.add_argument("--stack-dir", default=str(Path(__file__).resolve().parent.parent),
                   help="Mattermost stack dir with rendered ciu.toml (default: script's parent)")
    p.add_argument("--image", default=DEFAULT_IMAGE,
                   help=f"curl image for docker-network vantages (default {DEFAULT_IMAGE}; pulled only if missing)")
    p.add_argument("--post", action="store_true",
                   help="send one test message per webhook (default: never post)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--timeout", type=float, default=10.0, help="per-request timeout seconds")
    return p


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)  # argparse exits 2 on usage errors
    env = env or default_env()
    red = Redactor()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        results, disc = run(args, env, red)
    except ConfigError as exc:
        print(f"config error: {red.scrub(str(exc))}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last-resort, still redacted
        print(f"internal error: {red.scrub(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return 2
    rc = 1 if any(r.status == FAIL for r in results) else 0
    if args.json:
        counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, INFO)}
        text = json.dumps({"discovery": disc,
                           "results": [{"check": r.check, "vantage": r.vantage,
                                        "result": r.status, "detail": r.detail}
                                       for r in results],
                           "summary": counts, "exit_code": rc}, indent=2, ensure_ascii=False)
    else:
        text = render_table(results, disc)
    print(red.scrub(text))
    return rc


if __name__ == "__main__":
    sys.exit(main())

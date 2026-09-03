#!/usr/bin/env python3
"""exec-soulmask-rcon.py [options] <rcon command [args...]>

Runs Soulmask RCON commands against the running container and prints the
reply, with a read-only connection test first. Command reference:
https://saraserenity.net/soulmask/remote_console.php

  save (no exit):   SaveWorld 0
  save + shutdown:  SaveAndExit <seconds>     (cancel with: StopCloseServer)
  players:          List_OnlinePlayers        (alias: lp)
  message:          broadcast <text>

IMPORTANT: RCON responsiveness is NOT a server-health signal. The RCON
listener runs on its own thread and keeps answering even while the game
thread itself is stalled (e.g. swap/disk-bound under memory pressure) — a
fast reply here does not mean the game tick is healthy. Use `ServerFPS`
(or the zswap monitor's rf_d/s disk-refault column) as the real probe,
not RCON latency.

Design notes:
 - Soulmask speaks Source RCON over TCP and enforces an IP WHITELIST. We
   run the RCON engine (soulmask_rcon.py) INSIDE the Soulmask container's
   own network namespace (`nsenter --net=/proc/<pid>/ns/net`) so the
   connection is 127.0.0.1 (loopback), which is whitelist-friendly and
   needs no IP discovery.
 - The server process WSServer-Linux-Shipping is a CHILD of the
   Pterodactyl entrypoint, so it never shows in `docker ps {{.Command}}`
   (also truncated). We detect it with `docker top`, which sees child
   processes.
 - Every RCON connection — regardless of client, and regardless of how or
   whether it half-closes first — ends with the server logging
   "Error: Receive error: SE_EWOULDBLOCK" / "Closing connection" a couple
   of seconds after its own idle-connection check fires (confirmed live,
   2026-08-27). That's the server's own teardown path misreporting a
   normal condition, not something a client can avoid — this tool is a
   manual/occasional query tool, so it just accepts one connection per
   invocation; a program that polls RCON continuously should hold one
   connection open instead (see soulmask_rcon.py's --relay mode, used by
   soulmask-monitor.py).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RCON_ENGINE = HERE / "soulmask_rcon.py"

DEBUG = False


def log(msg: str) -> None:
    print(f"[rcon] {msg}", file=sys.stderr)


def dbg(msg: str) -> None:
    if DEBUG:
        print(f"[rcon:debug] {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    print(f"[rcon:ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def usage() -> str:
    return """Usage: exec-soulmask-rcon.py [options] [rcon command [args...]]

Options:
  -d                 print diagnostic details on stderr
  -h, --help         show this help and exit
  -i, --interactive  open an interactive RCON session
  -c, --container S  select by container ID/name, UUID prefix, or name substring
      --uuid UUID    select by server UUID (a unique prefix is accepted)

With multiple Soulmask servers running, --uuid (or --container) is required.
Without a command, the script only checks the RCON connection.
"""


def parse_args(argv: list[str]):
    debug = False
    interactive = False
    selector = None
    req_uuid = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-d":
            debug = True
            i += 1
        elif a in ("-h", "--help"):
            print(__doc__)
            print(usage())
            sys.exit(0)
        elif a in ("-i", "--interactive"):
            interactive = True
            i += 1
        elif a in ("-c", "--container"):
            if i + 1 >= len(argv) or not argv[i + 1]:
                die("-c/--container needs a value")
            selector = argv[i + 1]
            i += 2
        elif a == "--uuid":
            if i + 1 >= len(argv) or not argv[i + 1]:
                die("--uuid needs a value")
            req_uuid = argv[i + 1]
            i += 2
        elif a.startswith("--uuid="):
            req_uuid = a[len("--uuid="):]
            if not req_uuid:
                die("--uuid needs a value")
            i += 1
        else:
            break
    if selector and req_uuid:
        die("use either -c/--container or --uuid, not both")
    return debug, interactive, selector, req_uuid, argv[i:]


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


def docker_ps() -> list[tuple[str, str]]:
    res = run(["docker", "ps", "--format", "{{.ID}}\t{{.Names}}"])
    rows = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        cid, _, name = line.partition("\t")
        rows.append((cid, name))
    return rows


def pid_mode_host(cid: str) -> bool:
    res = run(["docker", "inspect", "-f", "{{.HostConfig.PidMode}}", cid])
    return res.stdout.strip() == "host"


def has_wsserver(cid: str) -> bool:
    res = run(["docker", "top", cid])
    return "WSServer-Linux-Shipping" in res.stdout


def container_uuid(cid: str) -> str:
    res = run(["docker", "inspect", "-f", "{{.Name}}", cid])
    return res.stdout.strip().lstrip("/")


def env_of(cid: str, key: str) -> str:
    res = run(["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", cid])
    prefix = key + "="
    for line in res.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return ""


def container_pid(cid: str) -> str:
    res = run(["docker", "inspect", "-f", "{{.State.Pid}}", cid])
    return res.stdout.strip()


def sel_match(selector: str | None, cid: str, name: str) -> bool:
    return not selector or cid.startswith(selector) or selector in name


def uuid_match(req_uuid: str | None, uuid: str) -> bool:
    return not req_uuid or uuid.startswith(req_uuid)


def find_soulmask_containers(selector, req_uuid):
    cids: list[str] = []
    uuids: list[str] = []
    for cid, name in docker_ps():
        # A --pid=host container (admin/nsenter shells, monitors) sees EVERY
        # host process in `docker top`, so it would false-match the game
        # binary below — skip it before paying for a `docker top` call.
        if pid_mode_host(cid):
            dbg(f"{cid} ({name}): --pid=host, skipping")
            continue
        if not sel_match(selector, cid, name):
            dbg(f"{cid} ({name}): does not match -c {selector}")
            continue
        uuid = container_uuid(cid) or name
        if not uuid_match(req_uuid, uuid):
            dbg(f"{cid} ({name}): does not match --uuid {req_uuid}")
            continue
        if has_wsserver(cid):
            cids.append(cid)
            uuids.append(uuid)
        else:
            dbg(f"{cid}: no WSServer")
    return cids, uuids


def rcon_argv(pid: str, port: str, password: str, extra: list[str]) -> list[str]:
    return [
        "nsenter", f"--net=/proc/{pid}/ns/net", "--",
        sys.executable, str(RCON_ENGINE),
        "--port", port, "--password", password, *extra,
    ]


def main(argv: list[str]) -> int:
    global DEBUG
    debug, interactive, selector, req_uuid, command = parse_args(argv)
    DEBUG = debug

    if shutil.which("docker") is None:
        die("docker not in PATH")
    if shutil.which("nsenter") is None:
        die("nsenter not in PATH (part of util-linux)")

    cids, uuids = find_soulmask_containers(selector, req_uuid)

    if not cids:
        if req_uuid:
            die(f"no WSServer container matches --uuid '{req_uuid}' (is that server running?)")
        if selector:
            die(f"no WSServer container matches -c '{selector}' (is that server running?)")
        die("Soulmask container not found (is the server running?)")

    if not req_uuid and not selector and len(cids) > 1:
        print("[rcon:ERROR] multiple Soulmask servers are running; specify --uuid <server-uuid>", file=sys.stderr)
        print("Available server UUIDs:", file=sys.stderr)
        for u in uuids:
            print(f"  {u}", file=sys.stderr)
        return 2

    if len(cids) > 1:
        print("[rcon:ERROR] selector matches multiple Soulmask servers; use a full --uuid", file=sys.stderr)
        print("Matching server UUIDs:", file=sys.stderr)
        for u in uuids:
            print(f"  {u}", file=sys.stderr)
        return 2

    cid, cname = cids[0], uuids[0]
    log(f"container: {cid} ({cname})")

    port = env_of(cid, "RCON_PORT") or "19000"
    password = env_of(cid, "RCON_PASSWORD")
    dbg(f"RCON_PORT={port}  RCON_PASSWORD=" + (f"<set,{len(password)} chars>" if password else "<EMPTY>"))
    if not password:
        die("RCON_PASSWORD empty on container env — is RCON set in the egg variables?")

    pid = container_pid(cid)
    if not pid or pid == "0":
        die(f"could not resolve a running PID for container {cid}")

    # Connection/auth pre-flight (benign, read-only). Runs even in
    # interactive mode so a misconfigured RCON_PASSWORD/port fails fast
    # with a clear message instead of handing the terminal to a session
    # that will just hang or reject auth silently.
    log("connection test: List_OnlinePlayers")
    res = run(rcon_argv(pid, port, password, ["List_OnlinePlayers"]))
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        if "cannot open" in detail and "/ns/net" in detail:
            detail += (
                "\nThat PID isn't visible in this process's own /proc — this shell isn't"
                " running directly on the Docker host (e.g. it's a nested/dev container"
                " talking to the host daemon). Run this script directly on the actual"
                " game host as root instead."
            )
        die(f"RCON test FAILED:\n{detail}", code=2)
    log("connection OK")
    log(f"reply: {res.stdout.rstrip()}")

    if interactive:
        if command:
            log(f"note: -i/--interactive ignores trailing args ({' '.join(command)}); type commands at the prompt instead")
        log("interactive session: type commands at the prompt (e.g. 'help', then n/<page#>/q to page); Ctrl-D to exit")
        import os
        os.execvp("nsenter", rcon_argv(pid, port, password, ["--interactive"]))

    if command:
        log(f"> {' '.join(command)}")
        result = subprocess.run(rcon_argv(pid, port, password, command))
        return result.returncode

    log("(connection test only; no command given)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

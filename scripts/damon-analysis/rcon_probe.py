#!/usr/bin/env python3
"""
rcon_probe.py — persistent RCON latency probe for Soulmask.

Connects once to the game server via Source RCON, then sends a lightweight
probe command at a fixed interval and measures the game-thread round-trip time.
Must be run inside the container's network namespace:

  sudo nsenter --net=/proc/<WSServer-PID>/ns/net \
    python3 rcon_probe.py --host 127.0.0.1 --port 19000 --password <pw> \
    [--interval 0.2] [--output FILE.jsonl] [--duration SEC]

Output (one JSON line per probe, to stdout and optionally --output file):
  {"ts": <unix_s>, "elapsed": <float>, "rtt_ms": <float>, "ok": true/false,
   "rss_kb": <int>, "swap_kb": <int>, "memory_high_bytes": <int|null>}

rss_kb / swap_kb / memory_high_bytes are read from /proc/<pid>/status and
the cgroup scope if --pid and --cgroup-scope are supplied.
"""

import argparse
import json
import os
import socket
import struct
import sys
import time


# ── Source RCON protocol ────────────────────────────────────────────────────

def _pack(req_id: int, pkt_type: int, body: str) -> bytes:
    payload = body.encode() + b'\x00\x00'
    header = struct.pack('<III', 4 + 4 + len(payload), req_id, pkt_type)
    return header + payload


def _recv_packet(sock: socket.socket) -> tuple[int, int, str]:
    raw_len = _recvn(sock, 4)
    length = struct.unpack('<I', raw_len)[0]
    data = _recvn(sock, length)
    req_id, pkt_type = struct.unpack('<II', data[:8])
    body = data[8:-2].decode('utf-8', errors='replace')
    return req_id, pkt_type, body


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('RCON connection closed')
        buf += chunk
    return buf


def rcon_connect(host: str, port: int, password: str, timeout: float = 5.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(_pack(1, 3, password))   # type 3 = SERVERDATA_AUTH
    req_id, _, _ = _recv_packet(sock)
    if req_id == -1:
        raise PermissionError('RCON authentication failed (wrong password?)')
    return sock


def rcon_command(sock: socket.socket, cmd: str, req_id: int = 2) -> tuple[float, str]:
    t0 = time.monotonic()
    sock.sendall(_pack(req_id, 2, cmd))   # type 2 = SERVERDATA_EXECCOMMAND
    _, _, body = _recv_packet(sock)
    rtt = (time.monotonic() - t0) * 1000
    return rtt, body


# ── proc helpers ─────────────────────────────────────────────────────────────

def _read_proc_status_kb(pid: int, key: str) -> int:
    try:
        for line in open(f'/proc/{pid}/status'):
            if line.startswith(key + ':'):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def _read_memory_high(scope: str) -> int | None:
    if not scope:
        return None
    try:
        val = open(f'{scope}/memory.high').read().strip()
        return None if val == 'max' else int(val)
    except OSError:
        return None


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host',         default='127.0.0.1')
    ap.add_argument('--port',         type=int, default=19000)
    ap.add_argument('--password',     required=True)
    ap.add_argument('--interval',     type=float, default=0.2,
                    help='seconds between probes (default 0.2)')
    ap.add_argument('--cmd',          default='List_OnlinePlayers',
                    help='RCON command to use as probe')
    ap.add_argument('--duration',     type=float, default=0,
                    help='stop after N seconds (0 = run until Ctrl-C)')
    ap.add_argument('--output',       default='',
                    help='append JSONL to this file in addition to stdout')
    ap.add_argument('--pid',          type=int, default=0,
                    help='game PID for RSS/swap readings from /proc')
    ap.add_argument('--cgroup-scope', default='',
                    help='cgroup v2 scope path for memory.high reading')
    args = ap.parse_args()

    print(f'[rcon_probe] connecting {args.host}:{args.port} interval={args.interval}s',
          file=sys.stderr)
    sock = rcon_connect(args.host, args.port, args.password)
    print(f'[rcon_probe] authenticated, probing every {args.interval*1000:.0f} ms',
          file=sys.stderr)

    out_fh = open(args.output, 'a') if args.output else None
    start_ts = time.time()
    req_id = 10

    try:
        while True:
            now = time.time()
            if args.duration and (now - start_ts) >= args.duration:
                break

            try:
                rtt_ms, resp = rcon_command(sock, args.cmd, req_id)
                ok = True
            except Exception as e:
                rtt_ms = (args.interval * 1000)
                resp = str(e)
                ok = False
                try:
                    sock.close()
                    sock = rcon_connect(args.host, args.port, args.password)
                except Exception:
                    pass

            req_id = (req_id % 10000) + 1

            entry = {
                'ts':               round(now, 3),
                'elapsed':          round(now - start_ts, 2),
                'rtt_ms':           round(rtt_ms, 2),
                'ok':               ok,
                'rss_kb':           _read_proc_status_kb(args.pid, 'VmRSS') if args.pid else 0,
                'swap_kb':          _read_proc_status_kb(args.pid, 'VmSwap') if args.pid else 0,
                'memory_high_bytes': _read_memory_high(args.cgroup_scope),
            }
            line = json.dumps(entry)
            print(line)
            sys.stdout.flush()
            if out_fh:
                out_fh.write(line + '\n')
                out_fh.flush()

            next_tick = now + args.interval
            sleep_s = next_tick - time.time()
            if sleep_s > 0:
                time.sleep(sleep_s)

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if out_fh:
            out_fh.close()
        print('\n[rcon_probe] done', file=sys.stderr)


if __name__ == '__main__':
    main()

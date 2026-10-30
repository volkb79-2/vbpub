#!/usr/bin/env python3
"""1 s passive stall recorder (read-only /proc + /sys). Usage: stall_recorder.py PID OUT.jsonl"""
import json, os, sys, time, glob

PID = int(sys.argv[1]); OUT = sys.argv[2]
CG = "/sys/fs/cgroup"


def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""


def kv(p):
    out = {}
    for line in rd(p).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def psi_total(p):
    res = {}
    for line in rd(p).splitlines():
        f = line.split()
        if f:
            res[f[0]] = int(f[-1].split("=")[1])
    return res


def proc_cg(pid):
    for line in rd(f"/proc/{pid}/cgroup").splitlines():
        if line.startswith("0::"):
            return CG + line[3:]
    return None


def net_tx(pid):
    rx = tx = rxp = txp = 0
    for line in rd(f"/proc/{pid}/net/dev").splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() == "lo":
            continue
        v = data.split()
        rx += int(v[0]); rxp += int(v[1]); tx += int(v[8]); txp += int(v[9])
    return rx, tx, rxp, txp


def vda():
    v = rd("/sys/block/vda/stat").split()
    # reads, rmerge, rsect, rticks, writes, wmerge, wsect, wticks, inflight, io_ticks, time_in_queue
    return [int(x) for x in v[:11]]


def iostat_cg(path):
    for line in rd(path + "/io.stat").splitlines():
        if line.startswith("254:0 "):
            return {k: int(v) for k, v in (x.split("=") for x in line.split()[1:])}
    return {}


def dthreads(pid):
    out = []
    for t in glob.glob(f"/proc/{pid}/task/*"):
        st = rd(t + "/stat")
        try:
            state = st[st.rindex(")") + 2]
        except ValueError:
            continue
        if state == "D":
            stack = [l.split()[-1] for l in rd(t + "/stack").splitlines()[:8]]
            out.append({"tid": int(os.path.basename(t)), "wchan": rd(t + "/wchan"), "stack": stack})
    return out


game_cg = proc_cg(PID)
VM_KEYS = ("pgmajfault", "pswpin", "pswpout", "allocstall_normal", "allocstall_movable", "pgscan_direct", "pgscan_kswapd",
           "compact_stall", "nr_dirty", "nr_writeback", "zswpin", "zswpout", "zswpwb", "workingset_refault_anon",
           "workingset_refault_file", "thp_fault_alloc", "thp_fault_fallback")
MEM_KEYS = ("pgmajfault", "workingset_refault_anon", "workingset_refault_file", "zswpin", "zswpout", "zswpwb", "pswpin", "pswpout")
TMPFS = glob.glob(CG + "/soulmask_tmpfs.slice/*.slice")

with open(OUT, "a", buffering=1) as f:
    while True:
        t = time.time()
        vm = kv("/proc/vmstat")
        gstat = kv(game_cg + "/memory.stat") if game_cg else {}
        rec = {
            "t": round(t, 3),
            "net": net_tx(PID),
            "vda": vda(),
            "vda_inflight": [int(x) for x in rd("/sys/block/vda/inflight").split()],
            "psi_io": psi_total("/proc/pressure/io"),
            "psi_mem": psi_total("/proc/pressure/memory"),
            "psi_cpu": psi_total("/proc/pressure/cpu"),
            "game_psi_io": psi_total(game_cg + "/io.pressure") if game_cg else {},
            "game_psi_mem": psi_total(game_cg + "/memory.pressure") if game_cg else {},
            "game_mem": {k: gstat.get(k) for k in MEM_KEYS},
            "game_cur": int(rd(game_cg + "/memory.current") or 0) if game_cg else 0,
            "vm": {k: vm.get(k) for k in VM_KEYS},
            "dev_io": iostat_cg(CG + "/dev.slice"),
            "sys_io": iostat_cg(CG + "/system.slice"),
            "wings_io": iostat_cg(CG + "/wings.slice"),
            "tmpfs": {os.path.basename(p): {"swap": int(rd(p + "/memory.swap.current") or 0),
                                            "pswpin": kv(p + "/memory.stat").get("pswpin")} for p in TMPFS},
            "dthreads": dthreads(PID),
        }
        f.write(json.dumps(rec) + "\n")
        time.sleep(max(0.0, 1.0 - (time.time() - t)))

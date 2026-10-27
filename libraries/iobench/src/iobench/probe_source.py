"""The sync-latency probe script (IOB-SWP-02): the ONE program the Host may run as a transient unit.

A leaf module (no imports) so that ``host.check_argv`` can compare the ``systemd-run`` argv's script slot against it byte for byte:
a probe unit runs as root, so any other script in that slot is arbitrary code.  ``sweep`` re-exports it for the planners.
"""

PROBE_SOURCE = '''import os, sys, time

def run(directory, out, duration, interval, now=time.monotonic, sleep=time.sleep, opener=open):
    """Append 200 B and fdatasync every `interval` s; one latency (us) per line in `out`."""
    path = os.path.join(directory, ".iobench-probe")
    lats = []
    start = now()
    nxt = start
    with opener(path, "ab", buffering=0) as fh:
        while now() - start < duration:
            t0 = now()
            fh.write(b"x" * 200)
            os.fdatasync(fh.fileno())
            lats.append((now() - t0) * 1e6)
            nxt += interval
            delay = nxt - now()
            if delay > 0:
                sleep(delay)
    with opener(out, "w") as fh:
        fh.write("".join("%d\\n" % round(v) for v in lats))
    os.unlink(path)

if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))
'''

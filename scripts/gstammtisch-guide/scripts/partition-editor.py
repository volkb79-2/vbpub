#!/usr/bin/env python3
"""
partition-editor.py — scripted MBR partition editor on top of sfdisk's dump/restore.
Handles sector math, 1 MiB alignment, EBR gaps for logicals, free-space detection
(incl. inside an extended partition), and overlap/bounds validation. Safe on a
disk in use: existing entries kept verbatim, no kernel re-read, new partitions
registered via partx. Does what `sfdisk --append` can't (logicals in extended
free space — it reports "No free sectors available").

  list                          human-readable partitions
  free                          free regions (primary + inside extended)
  dump [--out FILE]             current table (sfdisk -d)
  add  --size SIZE|fill [opts]  add ONE partition
  add-swap --count N [opts]     add N swap partitions (+mkswap +fstab by LABEL)
  restore --in FILE             write a saved/edited dump back (recovery)

Globals: --disk (default /dev/vda), --align (2048), --gap (2048 sectors, logical EBR).
Mutating commands need --commit to actually write.  MBR/dos labels only (GPT not handled).

Examples:
  partition-editor.py --disk /dev/vda free
  partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2          # dry-run
  partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit
  partition-editor.py --disk /dev/vda add --size 100G --type 8e --commit
  partition-editor.py --disk /dev/vda dump --out /root/vda.sfdisk
  partition-editor.py --disk /dev/vda restore --in /root/vda.sfdisk --commit
"""
import argparse, os, re, subprocess, sys, time

def run(cmd, *, input=None, check=True, quiet=False):
    if not quiet: print(f"  + {' '.join(cmd)}")
    p = subprocess.run(cmd, input=input, text=True, capture_output=True)
    if not quiet:
        if p.stdout.strip(): print("    | " + p.stdout.strip().replace("\n", "\n    | "))
        if p.stderr.strip(): print("    ! " + p.stderr.strip().replace("\n", "\n    ! "))
    if check and p.returncode: sys.exit(f"FAILED rc={p.returncode}: {' '.join(cmd)}")
    return p

align_up   = lambda x, a: -(-x // a) * a
align_down = lambda x, a: (x // a) * a

def parse_size(s, sector):
    s = str(s).strip().lower()
    if s in ("fill", "rest", "max"): return None
    m = re.fullmatch(r'(\d+)(k|m|g|t|ki|mi|gi|ti)?', s)
    if not m: sys.exit(f"bad size: {s}")
    n, u = int(m.group(1)), m.group(2)
    if not u: return n                              # raw sectors
    return (n * {'k':1024,'m':1024**2,'g':1024**3,'t':1024**4}[u[0]]) // sector

class Table:
    def __init__(self, disk):
        self.disk, self.header, self.parts, self.added, self.sector = disk, [], [], [], 512
        self.raw = run(["sfdisk", "-d", disk], quiet=True).stdout
        for line in self.raw.splitlines():
            ms = re.match(r'sector-size:\s*(\d+)', line)
            if ms: self.sector = int(ms.group(1))
            m = re.match(r'^(/dev/\S+)\s*:\s*(.*)$', line)
            if not m: self.header.append(line); continue
            f = m.group(2)
            self.parts.append(dict(
                dev=m.group(1), num=int(re.search(r'(\d+)$', m.group(1)).group(1)),
                start=int(re.search(r'start=\s*(\d+)', f).group(1)),
                size=int(re.search(r'size=\s*(\d+)', f).group(1)),
                type=re.search(r'type=([0-9A-Fa-f]+)', f).group(1)))
        self.disk_sectors = int(run(["blockdev", "--getsz", disk], quiet=True).stdout.strip())

    @property
    def extended(self):
        return next((p for p in self.parts if p['type'].lower() in ('5', 'f', '85')), None)
    def all(self):    return self.parts + self.added
    def end(self, p): return p['start'] + p['size'] - 1

    def next_num(self, logical):
        nums = [p['num'] for p in self.all()]
        if logical:
            ln = [n for n in nums if n >= 5]; return max(ln) + 1 if ln else 5
        for n in range(1, 5):
            if n not in nums: return n
        sys.exit("no free primary slot")

    def free_regions(self):
        regs = []
        def gaps(lo, hi, members, tag):
            cur = lo
            for p in sorted(members, key=lambda p: p['start']):
                if p['start'] > cur: regs.append((cur, p['start'] - 1, tag))
                cur = max(cur, self.end(p) + 1)
            if cur <= hi: regs.append((cur, hi, tag))
        gaps(2048, self.disk_sectors - 1, [p for p in self.all() if p['num'] <= 4], 'primary')
        if self.extended:
            gaps(self.extended['start'], self.end(self.extended),
                 [p for p in self.all() if p['num'] >= 5], 'logical')
        return regs

    def add(self, size, ptype, placement, align, gap, label=None):
        want_logical = placement == 'logical' or (placement == 'auto' and self.extended)
        kind = 'logical' if want_logical else 'primary'
        cand = [r for r in self.free_regions() if r[2] == kind]
        if not cand: sys.exit(f"no free {kind} region available")
        rs, re_, _ = max(cand, key=lambda r: r[1] - r[0])
        start = align_up(rs + (gap if kind == 'logical' else 0), align)
        if size is None: size = align_down(re_ - start + 1, align)
        if size <= 0 or start + size - 1 > re_:
            sys.exit(f"won't fit: need {size}s @ {start} in region [{rs}..{re_}]")
        num = self.next_num(kind == 'logical')
        part = dict(dev=f"{self.disk}{num}", num=num, start=start, size=size,
                    type=ptype, label=label)
        self.added.append(part); return part

    def to_dump(self):
        out = self.raw.rstrip("\n") + "\n"
        for p in self.added:
            out += f"{p['dev']} : start={p['start']}, size={p['size']}, type={p['type']}\n"
        return out

    def write(self, commit):
        print("== table to write ==\n" + self.to_dump())
        if not commit:
            print("DRY-RUN — nothing written. Re-run with --commit."); return False
        if os.geteuid(): sys.exit("must be root to --commit")
        bak = f"/root/parttable-{os.path.basename(self.disk)}.backup.sfdisk"
        open(bak, "w").write(self.raw); print(f"backup: {bak}  (restore: sfdisk --no-reread --force {self.disk} < {bak})")
        run(["sfdisk", "--no-reread", "--force", self.disk], input=self.to_dump())
        if self.added:
            nums = [p['num'] for p in self.added]
            run(["partx", "--add", "--nr", f"{min(nums)}:{max(nums)}", self.disk], check=False)
            run(["udevadm", "settle"], check=False)
            for p in self.added:
                for _ in range(50):
                    if os.path.exists(p['dev']): break
                    time.sleep(0.1)
                if not os.path.exists(p['dev']): sys.exit(f"{p['dev']} did not appear")
        return True

def gib(sectors, sector): return sectors * sector / 2**30

def make_swap(p, prio, discard):
    run(["mkswap", "-L", p['label'], p['dev']])
    opts = f"sw,pri={prio}" + (",discard=once" if discard else "")
    line = f"LABEL={p['label']}  none  swap  {opts}  0  0"
    if f"LABEL={p['label']}" not in open("/etc/fstab").read():
        open("/etc/fstab", "a").write(line + "\n"); print("  fstab += " + line)
    else:
        print(f"  fstab already has LABEL={p['label']}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disk", default="/dev/vda")
    ap.add_argument("--align", type=int, default=2048)
    ap.add_argument("--gap", type=int, default=2048)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list"); sub.add_parser("free")
    sub.add_parser("dump").add_argument("--out")
    a = sub.add_parser("add")
    a.add_argument("--size", required=True); a.add_argument("--type", default="83")
    a.add_argument("--placement", choices=["auto","logical","primary"], default="auto")
    a.add_argument("--label"); a.add_argument("--mkswap", action="store_true")
    a.add_argument("--fstab", action="store_true"); a.add_argument("--prio", type=int, default=10)
    a.add_argument("--discard", action="store_true"); a.add_argument("--commit", action="store_true")
    s = sub.add_parser("add-swap")
    s.add_argument("--count", type=int, default=2); s.add_argument("--size", default="fill")
    s.add_argument("--labels", default="gswap1,gswap2")
    s.add_argument("--placement", choices=["auto","logical","primary"], default="auto")
    s.add_argument("--prio", type=int, default=10); s.add_argument("--no-discard", action="store_true")
    s.add_argument("--commit", action="store_true")
    r = sub.add_parser("restore"); r.add_argument("--in", dest="infile", required=True)
    r.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    if args.cmd == "restore":
        data = open(args.infile).read()
        print("== restoring table ==\n" + data)
        if not args.commit: print("DRY-RUN — re-run with --commit."); return
        if os.geteuid(): sys.exit("must be root")
        run(["sfdisk", "--no-reread", "--force", args.disk], input=data)
        run(["partx", "--update", args.disk], check=False); return

    t = Table(args.disk)

    if args.cmd == "dump":
        print(t.raw)
        if args.out: open(args.out, "w").write(t.raw); print(f"saved -> {args.out}")
        return
    if args.cmd == "list":
        for p in sorted(t.all(), key=lambda p: p['num']):
            print(f"  {p['dev']:<12} start={p['start']:>12} end={t.end(p):>12} "
                  f"size={p['size']:>12} ({gib(p['size'], t.sector):7.1f} GiB) type={p['type']}")
        return
    if args.cmd == "free":
        for rs, re_, kind in t.free_regions():
            print(f"  [{rs:>12} .. {re_:>12}]  {re_-rs+1:>12} sectors "
                  f"({gib(re_-rs+1, t.sector):7.1f} GiB)  {kind}")
        return

    if args.cmd == "add":
        p = t.add(parse_size(args.size, t.sector), args.type, args.placement,
                  args.align, args.gap, args.label)
        print(f"  NEW {p['dev']}: start={p['start']} size={p['size']} "
              f"({gib(p['size'], t.sector):.1f} GiB) type={p['type']} label={p['label']}")
        if t.write(args.commit):
            if args.mkswap and not args.fstab:
                run(["mkswap"] + (["-L", p['label']] if p['label'] else []) + [p['dev']])
            if args.fstab and p['label']:
                make_swap(p, args.prio, args.discard); run(["swapon", "-a"], check=False)
        return

    if args.cmd == "add-swap":
        labels = args.labels.split(",")
        if len(labels) < args.count: sys.exit("need one --labels entry per partition")
        cand = [r for r in t.free_regions()
                if r[2] == ('logical' if (args.placement != 'primary' and t.extended) else 'primary')]
        if not cand: sys.exit("no suitable free region")
        rs, re_, _ = max(cand, key=lambda r: r[1] - r[0]); free = re_ - rs + 1
        if args.size == "fill":
            per = align_down((free - args.count * args.gap) // args.count, args.align)
            sizes = [per] * (args.count - 1) + [None]          # last partition fills remainder
        else:
            sizes = [parse_size(args.size, t.sector)] * args.count
        new = [t.add(sizes[i], "82", args.placement, args.align, args.gap, labels[i])
               for i in range(args.count)]
        for p in new:
            print(f"  NEW {p['dev']}: start={p['start']} size={p['size']} "
                  f"({gib(p['size'], t.sector):.1f} GiB) label={p['label']}")
        if t.write(args.commit):
            for p in new: make_swap(p, args.prio, not args.no_discard)
            run(["swapon", "-a"], check=False); run(["swapon", "--show"], check=False)
        return

if __name__ == "__main__":
    main()

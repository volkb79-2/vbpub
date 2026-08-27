#!/usr/bin/env python3
"""
inuse-partition-editor.py — add partitions to a disk that is CURRENTLY IN USE
(mounted root, live production host) without unmounting or rebooting, on top
of sfdisk's dump/restore. That is the whole point of this tool: existing
entries are kept verbatim, there is no kernel re-read of the full table, and
new partitions are registered live via partx. It also does what `sfdisk
--append` can't on MBR — logicals in extended free space, which `--append`
refuses ("No free sectors available"). Handles sector math, 1 MiB alignment,
EBR gaps for MBR logicals, free-space detection (incl. inside an extended
partition), and overlap/bounds validation. Both MBR/dos and GPT tables are
supported; other label types are refused rather than silently mis-parsed.

  list                          human-readable partitions
  free                          free regions (primary/GPT + inside extended)
  dump [--out FILE]             current table (sfdisk -d)
  add  --size SIZE|fill [opts]  add ONE partition
  add-swap --count N [opts]     add N swap partitions (+mkswap +fstab by LABEL)
  restore --in FILE             write a saved/edited dump back (recovery)

Globals: --disk (default /dev/vda), --align (2048), --gap (2048 sectors, MBR
logical EBR only — GPT partitions need no gap).
Mutating commands need --commit to actually write.

Examples:
  inuse-partition-editor.py --disk /dev/vda free
  inuse-partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2          # dry-run
  inuse-partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit
  inuse-partition-editor.py --disk /dev/vda add --size 100G --type 8e --commit
  inuse-partition-editor.py --disk /dev/vda dump --out /root/vda.sfdisk
  inuse-partition-editor.py --disk /dev/vda restore --in /root/vda.sfdisk --commit
"""
import argparse, hashlib, os, re, subprocess, sys, time

# GPT has no short type aliases in sfdisk's scripted dump format (unlike MBR's
# 2-hex-digit codes) — a bare "83"/"82" is rejected with "Invalid argument".
# These are sfdisk's own canonical GUIDs (`sfdisk -X gpt -T`).
GPT_TYPE_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
GPT_TYPE_SWAP = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
MBR_TYPE_LINUX = "83"
MBR_TYPE_SWAP = "82"

# Tolerant key=value tokenizer for sfdisk dump attribute strings. Handles both
# the padded real format (`start=        2048`) and quoted values
# (`name="EFI System Partition"`), for either label type.
_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)=\s*("[^"]*"|[^,\s]+)')

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
        self.label_type = self.first_lba = self.last_lba = None
        self.raw = run(["sfdisk", "-d", disk], quiet=True).stdout
        for line in self.raw.splitlines():
            ms = re.match(r'sector-size:\s*(\d+)', line)
            if ms: self.sector = int(ms.group(1))
            ml = re.match(r'label:\s*(\S+)', line)
            if ml: self.label_type = ml.group(1)
            mf = re.match(r'first-lba:\s*(\d+)', line)
            if mf: self.first_lba = int(mf.group(1))
            me = re.match(r'last-lba:\s*(\d+)', line)
            if me: self.last_lba = int(me.group(1))
            m = re.match(r'^(/dev/\S+)\s*:\s*(.*)$', line)
            if not m: self.header.append(line); continue
            attrs = dict(_ATTR_RE.findall(m.group(2)))
            self.parts.append(dict(
                dev=m.group(1), num=int(re.search(r'(\d+)$', m.group(1)).group(1)),
                start=int(attrs['start']), size=int(attrs['size']),
                type=attrs['type'].strip('"')))
        if self.label_type not in ("dos", "mbr", "gpt"):
            sys.exit(f"unsupported disklabel {self.label_type!r} on {disk} "
                     f"— this tool only handles MBR/dos and GPT partition tables")
        self.disk_sectors = int(run(["blockdev", "--getsz", disk], quiet=True).stdout.strip())

    @property
    def is_gpt(self): return self.label_type == "gpt"

    @property
    def extended(self):
        if self.is_gpt: return None  # GPT has no primary/extended/logical distinction
        return next((p for p in self.parts if p['type'].lower() in ('5', 'f', '85')), None)
    def all(self):    return self.parts + self.added
    def end(self, p): return p['start'] + p['size'] - 1
    def part_dev(self, num):
        return f"{self.disk}p{num}" if self.disk[-1].isdigit() else f"{self.disk}{num}"

    def default_type(self, kind):
        table = {"linux": GPT_TYPE_LINUX, "swap": GPT_TYPE_SWAP} if self.is_gpt \
            else {"linux": MBR_TYPE_LINUX, "swap": MBR_TYPE_SWAP}
        return table[kind]

    def next_num(self, logical):
        nums = [p['num'] for p in self.all()]
        if logical:
            ln = [n for n in nums if n >= 5]; return max(ln) + 1 if ln else 5
        if self.is_gpt:
            n = 1
            while n in nums: n += 1
            return n
        for n in range(1, 5):
            if n not in nums: return n
        sys.exit("no free primary slot")

    def free_regions(self, align=2048):
        regs = []
        def gaps(lo, hi, members, tag):
            cur = lo
            for p in sorted(members, key=lambda p: p['start']):
                if p['start'] > cur: regs.append((cur, p['start'] - 1, tag))
                cur = max(cur, self.end(p) + 1)
            if cur <= hi: regs.append((cur, hi, tag))
        lo = self.first_lba if self.first_lba is not None else align
        hi = self.last_lba if self.last_lba is not None else self.disk_sectors - 1
        primary_members = self.all() if self.is_gpt else [p for p in self.all() if p['num'] <= 4]
        gaps(lo, hi, primary_members, 'primary')
        if self.extended:
            gaps(self.extended['start'], self.end(self.extended),
                 [p for p in self.all() if p['num'] >= 5], 'logical')
        return regs

    def add(self, size, ptype, placement, align, gap, label=None):
        if placement == 'logical' and self.is_gpt:
            sys.exit("GPT has no extended/logical partitions — use --placement primary or auto")
        want_logical = placement == 'logical' or (placement == 'auto' and self.extended)
        kind = 'logical' if want_logical else 'primary'
        cand = [r for r in self.free_regions(align) if r[2] == kind]
        if not cand: sys.exit(f"no free {kind} region available")
        rs, re_, _ = max(cand, key=lambda r: r[1] - r[0])
        start = align_up(rs + (gap if kind == 'logical' else 0), align)
        if size is None: size = align_down(re_ - start + 1, align)
        if size <= 0 or start + size - 1 > re_:
            sys.exit(f"won't fit: need {size}s @ {start} in region [{rs}..{re_}]")
        num = self.next_num(kind == 'logical')
        part = dict(dev=self.part_dev(num), num=num, start=start, size=size,
                    type=ptype, label=label)
        self.added.append(part); return part

    def to_dump(self):
        out = self.raw.rstrip("\n") + "\n"
        for p in self.added:
            line = f"{p['dev']} : start={p['start']}, size={p['size']}, type={p['type']}"
            if self.is_gpt and p['label']: line += f', name="{p["label"]}"'
            out += line + "\n"
        return out

    def write(self, commit):
        print("== table to write ==\n" + self.to_dump())
        if not commit:
            print("DRY-RUN — nothing written. Re-run with --commit."); return False
        if os.geteuid(): sys.exit("must be root to --commit")
        bak = f"/root/parttable-{os.path.basename(self.disk)}-{int(time.time())}.backup.sfdisk"
        open(bak, "w").write(self.raw)
        print(f"backup: {bak}  sha256={hashlib.sha256(self.raw.encode()).hexdigest()}  "
              f"(restore: sfdisk --no-reread --force {self.disk} < {bak})")
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
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", default="/dev/vda")
    ap.add_argument("--align", type=int, default=2048)
    ap.add_argument("--gap", type=int, default=2048)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list"); sub.add_parser("free")
    sub.add_parser("dump").add_argument("--out")
    a = sub.add_parser("add")
    a.add_argument("--size", required=True)
    a.add_argument("--type", default=None, help="default: MBR 83 / GPT Linux filesystem GUID")
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
    r.add_argument("--force-mismatched-device", action="store_true",
                    help="restore even if the backup was captured from a different --disk")
    args = ap.parse_args()

    if args.cmd == "restore":
        data = open(args.infile).read()
        print("== restoring table ==\n" + data)
        md = re.search(r'^device:\s*(\S+)', data, re.M)
        if md and os.path.basename(md.group(1)) != os.path.basename(args.disk):
            msg = f"backup was captured from {md.group(1)!r}, not {args.disk!r}"
            if not args.force_mismatched_device:
                sys.exit(f"{msg} — pass --force-mismatched-device to restore anyway")
            print(f"WARNING: {msg} — proceeding due to --force-mismatched-device")
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
        for rs, re_, kind in t.free_regions(args.align):
            print(f"  [{rs:>12} .. {re_:>12}]  {re_-rs+1:>12} sectors "
                  f"({gib(re_-rs+1, t.sector):7.1f} GiB)  {kind}")
        return

    if args.cmd == "add":
        if args.fstab and not args.label: sys.exit("--fstab requires --label")
        ptype = args.type or t.default_type("linux")
        p = t.add(parse_size(args.size, t.sector), ptype, args.placement,
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
        if args.count < 1: sys.exit("--count must be >= 1")
        labels = [l.strip() for l in args.labels.split(",")]
        if len(labels) < args.count: sys.exit("need one --labels entry per partition")
        used = labels[:args.count]
        if any(not l for l in used): sys.exit("--labels entries must not be empty")
        if len(set(used)) < len(used): sys.exit("--labels entries must be unique")
        want_logical = args.placement != 'primary' and t.extended
        kind = 'logical' if want_logical else 'primary'
        cand = [r for r in t.free_regions(args.align) if r[2] == kind]
        if not cand: sys.exit("no suitable free region")
        rs, re_, _ = max(cand, key=lambda r: r[1] - r[0]); free = re_ - rs + 1
        reserve_gap = args.gap if kind == 'logical' else 0  # GPT/primary need no EBR gap
        if args.size == "fill":
            per = align_down((free - args.count * reserve_gap) // args.count, args.align)
            sizes = [per] * (args.count - 1) + [None]          # last partition fills remainder
        else:
            sizes = [parse_size(args.size, t.sector)] * args.count
        swap_type = t.default_type("swap")
        new = [t.add(sizes[i], swap_type, args.placement, args.align, args.gap, labels[i])
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

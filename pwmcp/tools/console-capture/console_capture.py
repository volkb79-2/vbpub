#!/usr/bin/env python3
"""QMP screendump poller that keeps only frames that changed.

Subcommands:
  run   --qmp SOCK --out DIR [--interval 3] [--diff-threshold 0.00002] [--max-runtime 14400] ...
        Owns the QMP connection (QMP allows one client). Polls `screendump`, keeps a frame only
        when it differs from the last kept frame, and executes commands dropped into DIR/inbox/
        by `ctl`. Always writes DIR/DONE (reason inside) on exit.
  ctl   --out DIR (--keys k1,k2 | --type TEXT | --type-file FILE | --qmp-json JSON | --quit)
        Queue a command for the running daemon (writes DIR/inbox/<n>.json).

Cursor handling (see README "Cursor blink"): a change confined to a cursor-sized box is held one
poll; it is dropped when the next poll is back on a known blink phase of the current screen. The
known phases are the last kept frame and, once learned, the other phase. This is deterministic
once both phases have been seen; at most one extra frame per screen is kept while they are learned.
"""
import argparse, hashlib, json, os, signal, socket, sys, time
from pathlib import Path
from PIL import Image, ImageChops

SHIFT = {c: k for c, k in zip('!@#$%^&*()', '1234567890')}
PUNCT = {' ': 'spc', '-': 'minus', '=': 'equal', '.': 'dot', ',': 'comma', '/': 'slash',
         ';': 'semicolon', "'": 'apostrophe', '[': 'bracket_left', ']': 'bracket_right',
         '\\': 'backslash', '`': 'grave_accent', '\n': 'ret', '\t': 'tab'}
SHIFTP = {'_': 'minus', '+': 'equal', ':': 'semicolon', '"': 'apostrophe', '<': 'comma',
          '>': 'dot', '?': 'slash', '{': 'bracket_left', '}': 'bracket_right', '|': 'backslash',
          '~': 'grave_accent'}


def text_to_keys(text):
    out = []
    for ch in text:
        if ch.isalpha() and ch.isascii():
            out.append(['shift', ch.lower()] if ch.isupper() else [ch])
        elif ch.isdigit() and ch.isascii():
            out.append([ch])
        elif ch in SHIFT:
            out.append(['shift', SHIFT[ch]])
        elif ch in SHIFTP:
            out.append(['shift', SHIFTP[ch]])
        elif ch in PUNCT:
            out.append([PUNCT[ch]])
        else:
            raise ValueError(f'unmappable character {ch!r} (US-layout ASCII only)')
    return out


class Qmp:
    def __init__(self, path, timeout):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(timeout)  # applies to connect and to every read/write
        self.s.connect(path)
        self.f = self.s.makefile('rw', encoding='utf-8')
        self.f.readline()  # greeting
        self.cmd('qmp_capabilities')

    def cmd(self, name, **args):
        self.f.write(json.dumps({'execute': name, 'arguments': args}) + '\n')
        self.f.flush()
        while True:
            line = self.f.readline()  # raises socket.timeout when QEMU stops answering
            if not line:
                raise ConnectionError('QMP closed')
            msg = json.loads(line)
            if 'return' in msg or 'error' in msg:
                return msg


def diff_stats(a, b, cursor_box):
    """Return (fraction_changed, small) comparing two RGB images."""
    if a.size != b.size:
        return 1.0, False
    d = ImageChops.difference(a, b).convert('L').point(lambda v: 255 if v > 48 else 0)
    bbox = d.getbbox()
    if bbox is None:
        return 0.0, False
    n = d.histogram()[255]
    frac = n / (a.size[0] * a.size[1])
    small = (bbox[2] - bbox[0]) <= cursor_box[0] and (bbox[3] - bbox[1]) <= cursor_box[1]
    return frac, small


def run(a):
    out = Path(a.out)
    (out / 'inbox').mkdir(parents=True, exist_ok=True)
    fdir = out / 'frames'
    fdir.mkdir(exist_ok=True)
    reason = 'unknown'
    q = None
    try:
        q = Qmp(a.qmp, a.qmp_timeout)
        reason = _loop(a, out, fdir, q)
    except BaseException as e:  # noqa: BLE001 - always record why we stopped
        reason = f'error: {e!r}'
        print(reason, flush=True)
    finally:
        if a.own_qemu and q is not None and reason != 'quit-command':
            try:
                q.cmd('quit')
            except Exception:
                pass
            _kill_pidfile(a.qemu_pidfile)
        (out / 'DONE').write_text(reason + '\n')


def _kill_pidfile(pidfile):
    """Last resort when QMP no longer answers: SIGTERM the pid in the pidfile (own QEMU only)."""
    if not pidfile:
        return
    try:
        time.sleep(2)
        os.kill(int(Path(pidfile).read_text().strip()), signal.SIGTERM)
    except Exception:
        pass


def _loop(a, out, fdir, q):
    manifest = open(out / 'manifest.jsonl', 'a')
    keylog = open(out / 'keys.jsonl', 'a')
    now = time.time()
    st = {'seq': 0, 'base': None}
    mpath, spath = out / 'manifest.jsonl', out / 'state.json'
    t0 = now
    lines = [json.loads(x) for x in mpath.read_text().splitlines() if x.strip()]
    if lines:  # resume: continue numbering, the real-time clock and the last kept frame
        st['seq'] = lines[-1]['sequence']
        try:
            st['base'] = Image.open(fdir / lines[-1]['file']).convert('RGB')
            st['base'].load()
        except Exception:
            st['base'] = None
        if spath.exists():
            t0 = json.loads(spath.read_text())['t0_epoch']
        else:  # legacy dir without state.json: old (lossy) arithmetic, restart gap is not counted
            t0 = now - lines[-1]['elapsed_s'] - a.interval
    else:
        spath.write_text(json.dumps({'t0_epoch': t0}))
    phases = [st['base']] if st['base'] is not None else []
    pending = None
    recent_keys = []
    tmp = str(out / 'tmp.png')
    cb = (a.cursor_w, a.cursor_h)
    thr = a.diff_threshold
    print(f'threshold fraction>={thr} (px luma diff>48), cursor_box={a.cursor_w}x{a.cursor_h}, '
          f'interval={a.interval}s, cap={a.cap}, max_runtime={a.max_runtime}s, qmp_timeout={a.qmp_timeout}s', flush=True)

    def keep(img, frac, keys, small_vs_old):
        old = st['base']
        st['seq'] += 1
        el = int(time.time() - t0)
        name = f"{st['seq']:04d}_{el}s.png"
        img.save(fdir / name)
        sha = hashlib.sha256((fdir / name).read_bytes()).hexdigest()
        manifest.write(json.dumps({'sequence': st['seq'], 'elapsed_s': el, 'file': name, 'sha256': sha,
                                   'width': img.size[0], 'height': img.size[1],
                                   'fraction_of_pixels_changed': round(frac, 6), 'keys_before': keys}) + '\n')
        manifest.flush()
        st['base'] = img
        phases[:] = [img, old] if (small_vs_old and old is not None) else [img]
        if st['seq'] >= a.cap:
            print('FRAME CAP HIT', flush=True)
            return True
        return False

    def is_phase(img):
        return any(diff_stats(img, p, cb)[0] < thr for p in phases)

    while True:
        if time.time() - t0 > a.max_runtime:
            return 'max-runtime'
        for f in sorted((out / 'inbox').glob('*.json')):
            c = json.loads(f.read_text())
            f.unlink()
            if c.get('quit'):
                try:
                    q.cmd('quit')
                except Exception:
                    pass
                return 'quit-command'
            ts = round(time.time() - t0, 1)
            if 'keys' in c or 'type' in c or 'type_file' in c:
                secret = c.get('secret', False)
                if 'type_file' in c:
                    text, secret = Path(c['type_file']).read_text().rstrip('\n'), True
                elif 'type' in c:
                    text = c['type']
                seqs = c['keys'] if 'keys' in c else text_to_keys(text)
                for combo in seqs:
                    q.cmd('send-key', keys=[{'type': 'qcode', 'data': k} for k in combo], **{'hold-time': 80})
                    time.sleep(0.15)
                if 'keys' in c:
                    label = ['+'.join(k) for k in c['keys']]
                elif secret:
                    label = 'TYPE:<%d chars redacted>' % len(text)
                else:
                    label = 'TYPE:' + text
            else:
                r = q.cmd(c['qmp']['execute'], **c['qmp'].get('arguments', {}))
                label = {'qmp': c['qmp'], 'result': r}
            keylog.write(json.dumps({'t': ts, 'sent': label}) + '\n')
            keylog.flush()
            recent_keys.append({'t': ts, 'sent': label})
        r = q.cmd('screendump', filename=tmp, format='png')
        if 'error' in r:
            print('screendump error', r, flush=True)
            time.sleep(a.interval)
            continue
        try:
            img = Image.open(tmp).convert('RGB')
            img.load()
        except Exception:
            time.sleep(1)
            continue
        stop = False
        base = st['base']
        if base is None:
            stop = keep(img, 1.0, recent_keys[:], False)
            recent_keys.clear()
        elif pending is not None:
            pimg, pfrac, pk = pending
            pending = None
            if is_phase(img):          # back on a known phase: the held frame was a blink phase
                phases.append(pimg)
            else:                      # moved on (or P persisted): P is a real change, keep it
                stop = keep(pimg, pfrac, pk, True)
                f2, s2 = diff_stats(img, st['base'], cb)
                if f2 >= thr and not stop:
                    pending = (img, f2, [])
        else:
            frac, small = diff_stats(img, base, cb)
            if frac >= thr and not is_phase(img):
                if small:
                    pending = (img, frac, recent_keys[:])
                else:
                    stop = keep(img, frac, recent_keys[:], False)
                recent_keys.clear()
        if stop:
            return 'cap'
        time.sleep(a.interval)


def ctl(a):
    given = [x for x in (a.keys, a.type, a.type_file, a.qmp_json) if x is not None] + ([True] if a.quit else [])
    if len(given) != 1:
        sys.exit('ctl: give exactly one of --keys, --type, --type-file, --qmp-json, --quit')
    inbox = Path(a.out) / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    if a.keys:
        c = {'keys': [k.split('+') for k in a.keys.split(',')]}
    elif a.type is not None:
        try:
            text_to_keys(a.type)  # validate BEFORE queueing
        except ValueError as e:
            sys.exit(f'ctl: --type rejected, nothing queued: {e}')
        c = {'type': a.type, 'secret': a.secret}
    elif a.type_file is not None:
        try:
            text_to_keys(Path(a.type_file).read_text().rstrip('\n'))
        except (ValueError, OSError) as e:
            sys.exit(f'ctl: --type-file rejected, nothing queued: {e}')
        c = {'type_file': str(Path(a.type_file).resolve())}
    elif a.qmp_json:
        c = {'qmp': json.loads(a.qmp_json)}
    else:
        c = {'quit': True}
    (inbox / f'{time.time_ns()}.json').write_text(json.dumps(c))


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest='mode', required=True)
    r = sp.add_parser('run')
    r.add_argument('--qmp', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--interval', type=float, default=3.0)
    r.add_argument('--diff-threshold', type=float, default=0.00002)
    r.add_argument('--cursor-w', type=int, default=24)
    r.add_argument('--cursor-h', type=int, default=40)
    r.add_argument('--cap', type=int, default=2000)
    r.add_argument('--max-runtime', type=float, default=14400, help='seconds since first start (default 4 h)')
    r.add_argument('--qmp-timeout', type=float, default=30, help='seconds for every QMP connect/read')
    r.add_argument('--own-qemu', action='store_true',
                   help='this daemon owns the guest: quit QEMU (QMP quit, then SIGTERM of --qemu-pidfile) on every exit except an explicit ctl --quit')
    r.add_argument('--qemu-pidfile')
    c = sp.add_parser('ctl')
    c.add_argument('--out', required=True)
    c.add_argument('--keys')
    c.add_argument('--type')
    c.add_argument('--secret', action='store_true', help='redact in keys.jsonl only; argv and inbox still hold the text')
    c.add_argument('--type-file', help='read the text to type from FILE at execution time (always redacted; not in argv/inbox)')
    c.add_argument('--qmp-json')
    c.add_argument('--quit', action='store_true')
    a = p.parse_args()
    (run if a.mode == 'run' else ctl)(a)


if __name__ == '__main__':
    main()

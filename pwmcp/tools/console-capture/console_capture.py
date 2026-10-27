#!/usr/bin/env python3
"""QMP screendump poller that keeps only frames that changed.

Subcommands:
  run   --qmp SOCK --out DIR [--interval 3] [--diff-threshold 0.0002] ...
        Owns the QMP connection (QMP allows one client). Polls `screendump`,
        keeps a frame only when it differs from the last kept frame, and
        executes commands dropped into DIR/inbox/ by `ctl`.
  ctl   --out DIR (--keys k1,k2 | --type TEXT | --qmp-json JSON | --quit)
        Queue a command for the running daemon (writes DIR/inbox/<n>.json).

Cursor handling: a change confined to a cursor-sized box is held as
"pending"; if the very next poll shows the previous kept frame again it was a
blink and is dropped, otherwise the pending frame is kept.
"""
import argparse, hashlib, json, socket, time
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
        elif ch.isdigit():
            out.append([ch])
        elif ch in SHIFT:
            out.append(['shift', SHIFT[ch]])
        elif ch in SHIFTP:
            out.append(['shift', SHIFTP[ch]])
        elif ch in PUNCT:
            out.append([PUNCT[ch]])
        else:
            raise ValueError(f'unmappable char {ch!r}')
    return out


class Qmp:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.f = self.s.makefile('rw', encoding='utf-8')
        self.f.readline()  # greeting
        self.cmd('qmp_capabilities')

    def cmd(self, name, **args):
        self.f.write(json.dumps({'execute': name, 'arguments': args}) + '\n')
        self.f.flush()
        while True:
            line = self.f.readline()
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
    manifest = open(out / 'manifest.jsonl', 'a')
    keylog = open(out / 'keys.jsonl', 'a')
    q = Qmp(a.qmp)
    t0 = time.time()
    st = {'seq': 0, 'base': None}
    mpath = out / 'manifest.jsonl'
    if mpath.exists():  # resume: continue numbering and the elapsed clock after a daemon restart
        lines = [json.loads(x) for x in mpath.read_text().splitlines() if x.strip()]
        if lines:
            st['seq'] = lines[-1]['sequence']
            t0 = time.time() - lines[-1]['elapsed_s'] - a.interval
    pending = None
    recent_keys = []
    tmp = str(out / 'tmp.png')
    cb = (a.cursor_w, a.cursor_h)
    print(f'threshold fraction>={a.diff_threshold} (px luma diff>48), cursor_box={a.cursor_w}x{a.cursor_h}, '
          f'interval={a.interval}s, cap={a.cap}', flush=True)

    def keep(img, frac, keys):
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
        if st['seq'] >= a.cap:
            print('FRAME CAP HIT', flush=True)
            return True
        return False

    while True:
        for f in sorted((out / 'inbox').glob('*.json')):
            c = json.loads(f.read_text())
            f.unlink()
            if c.get('quit'):
                try:
                    q.cmd('quit')
                except Exception:
                    pass
                (out / 'DONE').write_text('quit\n')
                return
            ts = round(time.time() - t0, 1)
            if 'keys' in c or 'type' in c:
                seqs = c['keys'] if 'keys' in c else text_to_keys(c['type'])
                for combo in seqs:
                    q.cmd('send-key', keys=[{'type': 'qcode', 'data': k} for k in combo], **{'hold-time': 80})
                    time.sleep(0.15)
                if 'keys' in c:
                    label = ['+'.join(k) for k in c['keys']]
                elif c.get('secret'):
                    label = 'TYPE:<%d chars redacted>' % len(c['type'])
                else:
                    label = 'TYPE:' + c['type']
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
            stop = keep(img, 1.0, recent_keys[:])
            recent_keys.clear()
        elif pending is not None:
            fb, _ = diff_stats(img, base, cb)
            pimg, pfrac, pk = pending
            pending = None
            if fb >= a.diff_threshold:  # not back to base -> real change
                stop = keep(pimg, pfrac, pk)
                f2, _ = diff_stats(img, st['base'], cb)
                if f2 >= a.diff_threshold and not stop:
                    pending = (img, f2, [])
        else:
            frac, small = diff_stats(img, base, cb)
            if frac >= a.diff_threshold:
                if small:
                    pending = (img, frac, recent_keys[:])
                else:
                    stop = keep(img, frac, recent_keys[:])
                recent_keys.clear()
        if stop:
            (out / 'DONE').write_text('cap\n')
            return
        time.sleep(a.interval)


def ctl(a):
    inbox = Path(a.out) / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    if a.keys:
        c = {'keys': [k.split('+') for k in a.keys.split(',')]}
    elif a.type is not None:
        c = {'type': a.type, 'secret': a.secret}
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
    c = sp.add_parser('ctl')
    c.add_argument('--out', required=True)
    c.add_argument('--keys')
    c.add_argument('--type')
    c.add_argument('--secret', action='store_true')
    c.add_argument('--qmp-json')
    c.add_argument('--quit', action='store_true')
    a = p.parse_args()
    (run if a.mode == 'run' else ctl)(a)


if __name__ == '__main__':
    main()

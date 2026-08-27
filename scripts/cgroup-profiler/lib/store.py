"""On-disk run directory layout (DESIGN.md §3): one line per sample/mark/
event/DAMON-snapshot, one manifest, everything durable enough to survive a
writer that is in a different container than the reader.

``marks.jsonl`` is the file that forces the append/read contract here: it is
appended to by ``cgprofile mark`` invocations that may run from inside the
very container being profiled, sharing this directory over a bind mount with
the collector loop that is simultaneously polling it. Two things follow from
that:

* every append must be exactly one ``write()`` syscall of one complete JSON
  line, under ``O_APPEND`` — POSIX gives that write atomicity against other
  O_APPEND writers on a local filesystem (the kernel serialises the
  seek-to-end-and-write), so two marks emitted in the same instant interleave
  as whole lines, never as spliced JSON. A buffered ``file.write()`` gives no
  such guarantee (it may split a line across two underlying syscalls), so
  ``append`` goes straight to ``os.open``/``os.write``.
* every read must tolerate a line a concurrent writer has only just started —
  by construction every *completed* append is one well-formed line, so a line
  that fails to parse is either that in-flight write or genuine corruption,
  and either way the only sane response is to skip it and keep going, never
  to raise and take the rest of the file down with it.

``samples`` is the file that forces the *compression* decision. Measured on a
real 20-second attach to a slice holding 29 cgroups: 89 KiB per sample, 6.7 MB
for 20 seconds, which projects to ~600 MB for a half-hour gate run — not
something to leave on a host that is already short of disk and memory.

Compressed streams are therefore written as a sequence of **independent gzip
members**, one per line, rather than as a single gzip stream. Concatenated
members are valid gzip (``gzip.open`` reads straight through them), and each
member is still exactly one ``os.write``, so compression costs neither the
append atomicity above nor crash-safety: a run killed mid-write loses at most
the final record, exactly as with plain JSONL. A single long-lived
``GzipFile`` would compress marginally better (6.3x vs 5.9x measured) and give
up both properties, which is a bad trade for 6 %.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from typing import Dict, Iterable, Iterator, Optional

# Streams compressed by default. Only ``samples`` is large enough to matter;
# marks and events stay plain text so a human can `tail -f` them mid-run, and
# so a foreign container appending a mark needs no gzip logic at all.
DEFAULT_COMPRESSED = ("samples",)


def new_run_id(prefix: str = "run", when: Optional[float] = None) -> str:
    """``run-YYYYmmdd-HHMMSS-xxxx`` — sortable by name (``latest`` below
    relies on that), and unique enough for one host.

    The trailing 4 hex chars only matter when two runs start in the same
    wall-clock second (a ``run`` immediately followed by an ``attach``, or a
    test suite creating several); ``os.urandom`` rather than ``random`` so
    there is no seeding/reseeding to reason about.  The timestamp is local
    time, deliberately: this name is read by a human on the host it was
    captured on far more often than it is parsed by code (the machine-usable
    timestamp is ``manifest.json``'s epoch-second ``started`` field).
    """
    ts = time.time() if when is None else when
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
    return f"{prefix}-{stamp}-{os.urandom(2).hex()}"


class RunDir:
    """One profiling run's directory: JSONL streams in, a manifest at the end.

    ``create=False`` is for read-only consumers (``analyze.py``, ``cgprofile
    report``) that must not conjure a run directory into existence just by
    looking for one.
    """

    def __init__(self, base: str, run_id: Optional[str] = None, create: bool = True,
                 compress: Optional[Iterable[str]] = None):
        self.run_id = run_id if run_id is not None else new_run_id()
        self.path = os.path.join(base, self.run_id)
        self.compress = set(DEFAULT_COMPRESSED if compress is None else compress)
        if create:
            os.makedirs(self.path, exist_ok=True)

    def _stem(self, name: str) -> Optional[str]:
        """The stream stem, or None when ``name`` is not a JSONL stream.

        Returning None for anything else is what keeps ``manifest.json`` from
        being rewritten as ``manifest.jsonl``: only the bare and the explicitly
        JSONL forms are stream names, and a caller naming some other file means
        that file.
        """
        for suffix in (".jsonl.gz", ".jsonl"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return None if os.path.splitext(name)[1] else name

    def stream_path(self, name: str) -> str:
        """Resolve a stream name to its file, supplying the extension.

        Callers name streams either way — ``append("samples")`` reads better at
        the call site, while ``phases.py`` writes ``marks.jsonl`` by its literal
        filename because a foreign container appending a mark has no RunDir to
        ask. Both must land on the same file, so the extension is normalised
        here rather than trusted from the caller.

        For reading, an existing file wins over the configured default: a run
        captured before compression was enabled, or with ``compress=()``, must
        stay readable by a reader that would have written ``.gz``.
        """
        stem = self._stem(name)
        if stem is None:
            return os.path.join(self.path, name)
        plain = os.path.join(self.path, stem + ".jsonl")
        packed = plain + ".gz"
        if os.path.exists(packed):
            return packed
        if os.path.exists(plain):
            return plain
        return packed if stem in self.compress else plain

    def append(self, name: str, obj: Dict) -> None:
        """Append ``obj`` as one line — see the module docstring for why this
        is one ``os.write`` under ``O_APPEND``, and why a compressed stream is
        a sequence of one-line gzip members rather than one gzip stream.
        """
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        path = self.stream_path(name)
        if path.endswith(".gz"):
            # mtime=0 keeps the member bytes a pure function of the payload,
            # so identical records compare equal across runs.
            line = gzip.compress(line, compresslevel=6, mtime=0)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            written = os.write(fd, line)
            if written != len(line):
                # A second os.write() call to finish the line would itself be
                # a second, independently-interleavable syscall — exactly the
                # hazard the single-write contract above exists to avoid. The
                # partial write already landed, so the honest response is to
                # surface the failure, not to paper over it with a write that
                # could still corrupt a concurrent writer's line.
                raise OSError(
                    f"short write appending to {path}: wrote {written} of "
                    f"{len(line)} bytes"
                )
        finally:
            os.close(fd)

    def read(self, name: str) -> Iterator[Dict]:
        """Yield every well-formed JSON object in ``name``, in file order.

        A missing stream yields nothing — a run made with ``--damon`` off
        never wrote ``damon.jsonl``, and that is not an error condition for a
        reader to raise on. A line that does not parse (truncated by a
        writer still mid-append, or otherwise corrupt) is skipped rather than
        raising, so one bad line never takes the rest of the file with it.
        """
        path = self.stream_path(name)
        try:
            if path.endswith(".gz"):
                # Reads straight through concatenated members, which is what
                # per-line compression produces; a truncated trailing member
                # raises EOFError/BadGzipFile mid-iteration, handled below.
                fh = gzip.open(path, "rt", encoding="utf-8")
            else:
                fh = open(path, "r", encoding="utf-8")
        except OSError:
            return
        with fh:
            while True:
                # A killed collector leaves a half-written final gzip member,
                # and that surfaces as EOFError/BadGzipFile from the *iterator*
                # rather than as an unparseable line. Everything before it is
                # still perfectly good data, so stop reading rather than lose
                # the whole run to its last record.
                try:
                    line = fh.readline()
                except (EOFError, OSError):
                    return
                if not line:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def write_manifest(self, obj: Dict) -> None:
        """Write ``manifest.json`` atomically: tmp file + ``os.replace``.

        Unlike the streams, the manifest is rewritten wholesale a handful of
        times per run (not appended once per sample), so there is no reason
        to accept a partially-written read of it the way ``read()`` tolerates
        a torn line — instead a reader (``analyze.py`` running concurrently,
        a human ``cat``) only ever observes the previous complete manifest or
        the new complete one, because ``os.replace`` is a single rename on
        the same filesystem.
        """
        path = self.stream_path("manifest.json")
        tmp = f"{path}.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def read_manifest(self) -> Dict:
        with open(self.stream_path("manifest.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def latest(base: str) -> Optional["RunDir"]:
        """The newest run directory under ``base``, or ``None`` when there is
        none — backs ``cgprofile report`` with no ``--run-dir``.

        Judged by the run id's own sortable timestamp (lexical order == time
        order for the fixed-width ``YYYYmmdd-HHMMSS`` stamp), not by
        directory mtime: mtime drifts whenever anything later touches a file
        inside an old run — re-rendering its report, for instance — which
        would make "latest" stop meaning "most recently started".
        """
        if not os.path.isdir(base):
            return None
        names = [n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))]
        if not names:
            return None
        run_id = sorted(names)[-1]
        return RunDir(base, run_id=run_id, create=False)

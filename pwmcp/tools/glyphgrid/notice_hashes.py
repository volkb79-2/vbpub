#!/usr/bin/env python3
"""Compute / check the sha256 values NOTICE.md quotes for the licence snippets of console-setup's ``copyright.fonts``.

  notice_hashes.py --copyright-fonts copyright.fonts[.gz] [--check-notice NOTICE.md]

A snippet is a 1-based inclusive line range of the DECOMPRESSED text; its hash is sha256 of those lines joined with newlines plus a final newline
(what ``sed -n 'A,Bp' | sha256sum`` prints).  ``SNIPPETS`` is the single source of the ranges and expected hashes; ``--check-notice`` also verifies
that NOTICE.md quotes exactly these values.  Exit status 1 on any mismatch."""
import argparse
import gzip
import hashlib
import re
import sys

# name -> (first line, last line, expected sha256); package keyboard-configuration 1.242~deb13u1, copyright.fonts.gz sha256 e51cd660...
SNIPPETS = {
    "V": (5, 8, "0cb77c55d33e0f6131945090234225a389b93b333c0264aff697c7950d37721a"),
    "F": (32, 38, "016f4e3973071399c2b7ad978dc64ecd693a4764f4ee4ed628643117071beaf7"),
    "S": (48, 52, "25ca3792e8c0e6dfb5b37bfcacce81cc96f3da0ead6aff02fd530acb683b5ed0"),
    "T": (114, 122, "1367be4bddcf2a7afb5a251b10805697694c8e803d54cd306cd36ce7ac4e598d"),
}
FILE_SHA256 = "e51cd6608b3d3c3e3e71669532a4c59653a8b84060a47651046eb8556b69ee88"            # copyright.fonts.gz as shipped
TEXT_SHA256 = "adeb95d45c8f1b944dc21032e74ea2e5636e0f4a34a63783fd37044af9852dcb"            # decompressed


def snippet_sha256(text: str, first: int, last: int) -> str:
    lines = text.split("\n")
    return hashlib.sha256(("\n".join(lines[first - 1:last]) + "\n").encode("utf-8")).hexdigest()


def check_text(text: str) -> dict:
    """-> {name: (computed, expected, ok)} for every snippet, plus 'text' for the whole decompressed file."""
    out = {n: (snippet_sha256(text, a, b), h, snippet_sha256(text, a, b) == h) for n, (a, b, h) in SNIPPETS.items()}
    whole = hashlib.sha256(text.encode("utf-8")).hexdigest()
    out["text"] = (whole, TEXT_SHA256, whole == TEXT_SHA256)
    return out


def check_notice(notice: str) -> list:
    """Problems: every SNIPPETS hash and the file hashes must occur in NOTICE.md next to its snippet name, and no other 64-hex value may be claimed
    for a snippet (a wrong hash in the text is exactly what this catches)."""
    bad = []
    for n, (a, b, h) in SNIPPETS.items():
        m = re.search(r"\*\*%s\*\*, lines %d-%d, sha256 `([0-9a-f]{64})`" % (n, a, b), notice)
        if not m:
            bad.append("snippet %s lines %d-%d not quoted in NOTICE.md" % (n, a, b))
        elif m.group(1) != h:
            bad.append("snippet %s: NOTICE.md says %s, expected %s" % (n, m.group(1), h))
    for h in (FILE_SHA256, TEXT_SHA256):
        if h not in notice:
            bad.append("file hash %s not in NOTICE.md" % h[:12])
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--copyright-fonts", required=True)
    ap.add_argument("--check-notice")
    a = ap.parse_args(argv)
    raw = open(a.copyright_fonts, "rb").read()
    if raw[:2] == b"\x1f\x8b":
        if hashlib.sha256(raw).hexdigest() != FILE_SHA256:
            print("copyright.fonts.gz sha256 differs from the recorded one")
            return 1
        raw = gzip.decompress(raw)
    res = check_text(raw.decode("utf-8"))
    for n, (got, want, ok) in res.items():
        print("%-5s %s %s" % (n, got, "ok" if ok else "MISMATCH (expected %s)" % want))
    bad = [n for n, v in res.items() if not v[2]]
    if a.check_notice:
        probs = check_notice(open(a.check_notice, encoding="utf-8").read())
        for p in probs:
            print("NOTICE:", p)
        bad += probs
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

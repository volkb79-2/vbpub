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
# sha256 values NOTICE.md may quote besides the snippets/file hashes (ISOs, debs, kernels, font files, the base fonts); NOTICE.md must not carry any other
KNOWN_OTHER_SHA256 = (
    "515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e",     # TrueNAS 27.0.0-RC.1 ISO
    "a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355",     # Debian 13.7.0 amd64 netinst ISO
    "76e8019ff1b68ddb3e49cecb1ea35ecead9e0f1c1ba22c0e819c9b13de939e72",     # grml-full 2026.09 amd64 ISO
    "c96eb9386cd8d65dceb0a58726dcef7cf7a0aedca7f514139c225ab391baca27",     # console-setup-linux 1.242~deb13u1 deb
    "f8b614f910166a74db8b4fe1d28a1b464dc4f4f726ed60ed9cc321e9eff8fed1",     # keyboard-configuration 1.242~deb13u1 deb
    "9aed29abee9d52d714b393c54a3394dba936694091a984b6d367fbb68cc5547c",     # its main copyright file
)
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
    allowed = {h for _, _, h in SNIPPETS.values()} | {FILE_SHA256, TEXT_SHA256} | set(KNOWN_OTHER_SHA256)
    quoted = set()
    for run in re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64,}(?![0-9a-fA-F])", notice):      # any case, any length >= 64 (sha512 is 128)
        if len(run) != 64:
            bad.append("NOTICE.md contains a %d-hex run (not a recorded sha256): %s..." % (len(run), run[:16]))
        elif run.lower() not in allowed:
            bad.append("NOTICE.md contains a 64-hex sha256 that is not a recorded one: %s" % run)
        else:
            quoted.add(run.lower())
    for h in KNOWN_OTHER_SHA256:
        if h not in quoted:
            bad.append("KNOWN_OTHER_SHA256 entry %s is no longer quoted in NOTICE.md (stale whitelist)" % h[:12])
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

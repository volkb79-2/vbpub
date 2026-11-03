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
# sha256 values NOTICE.md may quote besides the snippets/file hashes (ISOs, debs, kernels, and every committed atlas source file); NOTICE.md must not carry any other
KNOWN_OTHER_SHA256 = (
    "515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e",     # TrueNAS 27.0.0-RC.1 ISO
    "a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355",     # Debian 13.7.0 amd64 netinst ISO
    "76e8019ff1b68ddb3e49cecb1ea35ecead9e0f1c1ba22c0e819c9b13de939e72",     # grml-full 2026.09 amd64 ISO
    "c96eb9386cd8d65dceb0a58726dcef7cf7a0aedca7f514139c225ab391baca27",     # console-setup-linux 1.242~deb13u1 deb
    "f8b614f910166a74db8b4fe1d28a1b464dc4f4f726ed60ed9cc321e9eff8fed1",     # keyboard-configuration 1.242~deb13u1 deb
    "9aed29abee9d52d714b393c54a3394dba936694091a984b6d367fbb68cc5547c",     # its main copyright file
    "624e06de18b4fa535e90db7160d00d3d07d206422b89999bf1e27d920264e4e0",     # OVMF_CODE_4M.fd
    "662eb86fc1ea89635e4bba8413590b79eb714ade5a2b5a5aa3dc5cf5f53a4463",     # TrueNAS GRUB unicode.pf2
    "56e41016311834d94da724d4ac22ddf83533c9c4c48100e7ec207f94bfd3e283",     # TrueNAS kernel VGA table source
    "3818f6f8a805515ed24283b8fa050278af877813782b980509d22cad969634c8",     # Lat15-VGA16.psf.gz
    "4e5d96fa087381e04c66c98df1411d629736ad040a957534377a93d860a4696a",     # Uni3-Fixed16.psf.gz
    "6c9390e7a8101e3600b5e007333eef482154ea21846a0ac3b17672206a5406a8",     # Uni3-TerminusBold32x16.psf.gz
    "c4a73dbbe3c37a934a598500a1288f601ffa4d1343ab38a4e019969cd5ed0991",     # Lat15-VGA28x16.psf.gz
    "1765a808e05c9b1ca88b39aef1777b5a330e83576ff6b9dadc54b0df44d0f61a",     # Lat15-VGA32x16.psf.gz
    "fe3e804ea8e0fbd37b2ed6b462958bf2887fa7f91fa130f9d61340923613e08a",     # Lat15-VGA14.psf.gz
    "6c5cf355632e12e8b91682957dc5a5ae55d7ec9247f70123c75d31c36696641d",     # Lat15-VGA8.psf.gz
    "74b917d61759843a3df65be96a0fe4aa6978840dca437d90a044dbc7b17a68a7",     # Uni3-Fixed13.psf.gz
    "df034303323f2e6c396120f28571386c63fe789b7286523d3faba5eb030123ba",     # Uni3-Fixed14.psf.gz
    "707d0fb26c2240241b7af22ace56013812e59ce3ba4d68637af90694748232e2",     # Uni3-Fixed15.psf.gz
    "bd1d6b7d2864c273ef3cb3cc7f26f288829eb42176672359122cb0b123ca36a1",     # Uni3-Fixed18.psf.gz
    "2b2358b37674d2505350528875bb17afae2a36522a9e8a9417eaca65a7da0e08",     # Debian kernel TER16x32 source
    "5e4f30fddf41903658003c2fb45e4bb7d870fb753000953f87ee51f46e8690b4",     # Uni3-Terminus18x10.psf.gz
    "51a4aa8a1bbff36fa7ae6b5bd905a9308a64fbf57cfb01c52fe5396a8853d27c",     # Uni3-Terminus20x10.psf.gz
    "817a7aecd5414410e43dd41320539473b617fe217a332ba25f0f219a40011165",     # Uni3-Terminus22x11.psf.gz
    "efc268dcd69ef86e3cd5f5a0bfbf4d0e7ae618334e27198c605b7cd00315616b",     # Uni3-Terminus24x12.psf.gz
    "cd0975a4639fdfce20a24607d458ff32d181805ed71101438f2e59c94156f799",     # Uni3-Terminus28x14.psf.gz
    "a13bd9ced51d694bd0f9c40592dd18e747a441616ed6578d797ee8e33a2e2f69",     # Uni3-Terminus32x16.psf.gz
    "d8fec3bade8da7baaf3ff7b1635fe62e2cdbc0914518bdeedb8b66b8f2ee448a",     # Uni3-Terminus12x6.psf.gz
    "27381672f14435611ca9bf5d017f85da5bcf4c2f3d19b14623268c06cfd1d1da",     # Uni3-Terminus14.psf.gz
    "2bc51a16e2bbd3aee7463d8318935f819684579127defff2ef23f092f98c9420",     # Uni3-Terminus16.psf.gz
    "e939fdac22cd698dd1e57d29ef38e0e366f4dee632d65b794996bb4250f80e1b",     # Uni3-TerminusBold24x12.psf.gz
    "7c580d03f9ea4e0da24b1db042a4f482d0d021c1f40a5c948f51696d2fbe7fe5",     # Uni3-TerminusBold16.psf.gz
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

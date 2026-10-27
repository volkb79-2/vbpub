#!/usr/bin/env python3
"""Score the pwmcp OCR sidecar against the hand-written ground truth of this corpus.

The sidecar container publishes only on the daemon host's loopback, which a devcontainer cannot
reach, so by default requests go through `docker exec -i <container> python3 -c <poster>`.
Use --url to post straight to an HTTP address instead.

  python3 ocr_eval.py --container pwmcp-ocr-eval-run --out ocr-results.json

Metrics per (frame, psm, scale):
  char_acc    1 - levenshtein(norm(ocr), norm(truth)) / len(norm(truth)), floored at 0; norm()
              collapses whitespace runs to one space and strips (layout is not scored).
  line_recall fraction of non-empty ground-truth lines that appear as an exact (whitespace-
              collapsed) line of the OCR output, MULTISET matching (each OCR line is used at most
              once); line_recall_fuzzy uses similarity >= 0.9 with the same one-use rule.
  --rescore   recompute these from the stored texts in an existing results file (no sidecar).
"""
import argparse, difflib, json, re, subprocess, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
POSTER = (
    "import sys,urllib.request;"
    "r=urllib.request.Request('http://127.0.0.1:8941/v1/ocr?'+sys.argv[1],data=sys.stdin.buffer.read(),"
    "headers={'Content-Type':'image/png'});"
    "sys.stdout.write(urllib.request.urlopen(r,timeout=120).read().decode())"
)

# (frame stem, phrase) pairs checked for an exact (case-sensitive, whitespace-collapsed) substring
KEY_PHRASES = [
    ("04-installer-console-setup", "TrueNAS 27.0.0-RC.1 Console Setup"),
    ("04-installer-console-setup", "1 Install/Upgrade"),
    ("04-installer-console-setup", "3 Reboot System"),
    ("05-choose-destination-media", "Choose Destination Media"),
    ("05-choose-destination-media", "vda"),
    ("06-choose-destination-selected", "[*]"),
    ("07-erase-warning", "Proceed with the installation?"),
    ("08-auth-method", "Administrative user (truenas_admin)"),
    ("09-password-empty", "Confirm Password:"),
    ("13-progress-complete", "[100%] Installation completed successfully"),
    ("14-installation-succeeded", "Installation Succeeded"),
    ("14-installation-succeeded", "The TrueNAS installation on vda succeeded!"),
    ("14-installation-succeeded", "Please reboot and remove the installation media."),
    ("16-grub-installed-menu", "TrueNAS Scale GNU/Linux 27.0.0-RC.1"),
    ("17-grub-booting-installed", "Loading initial ramdisk ..."),
    ("19-console-setup-with-kernel-noise", "Console setup"),
    ("19-console-setup-with-kernel-noise", "http://10.0.2.15"),
    ("19-console-setup-with-kernel-noise", "https://10.0.2.15"),
    ("19-console-setup-with-kernel-noise", "http://[fec0::5054:ff:fe12:3456]"),
    ("19-console-setup-with-kernel-noise", "4) Change local administrator password"),
    ("19-console-setup-with-kernel-noise", "10) Shutdown"),
    ("19-console-setup-with-kernel-noise", "Enter an option from 1-10:"),
    ("20-console-setup-duplicated-redraw", "Console setup"),
    ("20-console-setup-duplicated-redraw", "https://10.0.2.15"),
    ("20-console-setup-duplicated-redraw", "9) Reboot"),
    ("20-console-setup-duplicated-redraw", "Enter an option from 1-10:"),
]


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def lev(a, b):
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(ocr_text, truth):
    n_o, n_t = norm(ocr_text), norm(truth)
    acc = max(0.0, 1 - lev(n_o, n_t) / max(len(n_t), 1))
    t_lines = [norm(x) for x in truth.splitlines() if norm(x)]
    o_lines = [norm(x) for x in ocr_text.splitlines() if norm(x)]
    # multiset matching: every OCR line can satisfy at most ONE ground-truth line, so duplicated
    # ground-truth lines (frames 12, 13, 20) are only credited as often as they were read
    avail = Counter(o_lines)
    exact = 0
    for t in t_lines:
        if avail[t] > 0:
            avail[t] -= 1
            exact += 1
    unused = list(o_lines)
    fuzzy = 0
    for t in t_lines:
        best, best_i = 0.0, None
        for i, o in enumerate(unused):
            r = difflib.SequenceMatcher(None, t, o).ratio()
            if r >= 0.9 and r > best:
                best, best_i = r, i
        if best_i is not None:
            unused.pop(best_i)
            fuzzy += 1
    n = max(len(t_lines), 1)
    return acc, exact / n, fuzzy / n


def post(png, query, a):
    if a.url:
        import urllib.request
        req = urllib.request.Request(f"{a.url}/v1/ocr?{query}", data=png, headers={"Content-Type": "image/png"})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())
    out = subprocess.run(["docker", "exec", "-i", a.container, "python3", "-c", POSTER, query],
                         input=png, capture_output=True, check=True)
    return json.loads(out.stdout)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--container", default="pwmcp-ocr-eval-run")
    p.add_argument("--url")
    p.add_argument("--out", default=str(HERE / "ocr-results.json"))
    p.add_argument("--psms", default="6,11")
    p.add_argument("--scales", default="1,2,3")
    p.add_argument("--rescore", metavar="RESULTS_JSON",
                   help="do not call the sidecar: re-score the stored OCR texts of this results file against the "
                        "current ground truth and write --out")
    a = p.parse_args()
    if a.rescore:
        old = json.loads(Path(a.rescore).read_text())
        for r in old:
            if "error" in r:
                continue
            truth = (HERE / "ground-truth" / f"{r['frame']}.txt").read_text()
            r["char_acc"], r["line_recall"], r["line_recall_fuzzy"] = (round(x, 4) for x in score(r["text"], truth))
        Path(a.out).write_text(json.dumps(old, indent=1))
        return
    results = []
    for gt in sorted((HERE / "ground-truth").glob("*.txt")):
        stem = gt.stem
        png = (HERE / "frames" / f"{stem}.png").read_bytes()
        truth = gt.read_text()
        for psm in map(int, a.psms.split(",")):
            for scale in map(int, a.scales.split(",")):
                t0 = time.time()
                try:
                    r = post(png, f"psm={psm}&scale={scale}", a)
                except Exception as e:  # keep going; record the failure
                    results.append({"frame": stem, "psm": psm, "scale": scale, "error": repr(e)[:300]})
                    continue
                text = r.get("text", "")
                acc, lr, lf = score(text, truth)
                words = r.get("words", [])
                mc = sum(w["conf"] for w in words) / len(words) if words else 0
                results.append({"frame": stem, "psm": psm, "scale": scale, "char_acc": round(acc, 4),
                                "line_recall": round(lr, 4), "line_recall_fuzzy": round(lf, 4),
                                "mean_conf": round(mc, 1), "n_words": len(words),
                                "seconds": round(time.time() - t0, 2), "text": text})
                print(stem, psm, scale, f"acc={acc:.3f} lr={lr:.2f} lf={lf:.2f}", flush=True)
    Path(a.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()

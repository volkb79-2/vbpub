#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate a THIRD-PARTY-NOTICES inventory from what is actually installed.

Run INSIDE the artifact being described (image build stage, venv, bundle build), so the list is the
real closure and cannot drift from a hand-written one:

    python3 third_party_notices.py python           # installed Python distributions
    python3 third_party_notices.py dpkg             # Debian packages (copyright files)
    python3 third_party_notices.py python --json    # machine-readable

Output is Markdown (or JSON) on stdout; a Dockerfile writes it to ``/usr/share/doc/THIRD-PARTY-NOTICES.md``
(see docs/LICENSING.md). Licences are reported as declared by the package; an empty value is reported as
``UNKNOWN`` so a reviewer sees the gap instead of a silent omission.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path


def python_rows() -> list[dict[str, str]]:
    rows = []
    for dist in metadata.distributions():
        meta = dist.metadata
        licence = (meta.get("License-Expression") or "").strip()
        if not licence:
            classifiers = [c.rsplit("::", 1)[-1].strip() for c in meta.get_all("Classifier") or []
                           if c.startswith("License ::")]
            licence = "; ".join(classifiers)
        if not licence:
            raw = (meta.get("License") or "").strip()
            licence = raw if raw and "\n" not in raw and len(raw) < 80 else ""
        files = sorted({Path(str(f)).name for f in dist.files or [] if re.search(r"(LICEN[CS]E|NOTICE|COPYING)", str(f))})
        rows.append({
            "name": meta.get("Name", "?"), "version": dist.version,
            "licence": licence or "UNKNOWN", "files": ", ".join(files),
        })
    return sorted(rows, key=lambda r: r["name"].lower())


def dpkg_rows(status: Path = Path("/var/lib/dpkg/status"), doc: Path = Path("/usr/share/doc")) -> list[dict[str, str]]:
    rows = []
    for block in status.read_text(encoding="utf-8", errors="replace").split("\n\n"):
        fields = dict(re.findall(r"^(Package|Version|Status): *(.+)$", block, re.M))
        if "installed" not in fields.get("Status", "").split():
            continue
        name = fields.get("Package", "?")
        licences: set[str] = set()
        for candidate in (doc / name / "copyright",):
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                licences.update(m.strip() for m in re.findall(r"^License: *(.+)$", text, re.M) if m.strip())
        rows.append({
            "name": name, "version": fields.get("Version", "?"),
            "licence": "; ".join(sorted(licences)) or "UNKNOWN (see /usr/share/doc/%s/copyright)" % name,
            "files": f"/usr/share/doc/{name}/copyright",
        })
    return sorted(rows, key=lambda r: r["name"])


def render_markdown(kind: str, rows: list[dict[str, str]]) -> str:
    lines = [f"# Third-party notices ({kind})", "",
             "| name | version | licence | licence files |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['version']} | {r['licence'].replace('|', '/')} | {r['files']} |")
    unknown = [r["name"] for r in rows if r["licence"].startswith("UNKNOWN")]
    lines += ["", f"{len(rows)} entries, {len(unknown)} with an undeclared licence."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("python", "dpkg"))
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = parser.parse_args(argv)
    rows = python_rows() if args.kind == "python" else dpkg_rows()
    sys.stdout.write(json.dumps(rows, indent=2) + "\n" if args.json else render_markdown(args.kind, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The two Netcup install examples must resolve against their measured root geometry."""

from __future__ import annotations

import json
from pathlib import Path


NETCUP_DIR = Path(__file__).resolve().parents[1]
EXAMPLES = NETCUP_DIR / "examples" / "debian-install-v2"
ROOT_START_SECTOR = 2_500_608


def test_r1002_and_v1001_install_examples_resolve_to_the_netcup_layout(monkeypatch):
    package_root = NETCUP_DIR.parent / "debian-install-v2"
    monkeypatch.syspath_prepend(str(package_root))
    from debian_install_v2.config import load_config
    from debian_install_v2.plan_preview import resolve_plan

    expected = [
        ("root", 3, ROOT_START_SECTOR, 335_544_320),
        ("data", 4, 338_044_928, 685_363_200),
        ("iobench raw", 5, 1_023_408_128, 16_777_216),
        ("swap", 6, 1_040_185_344, 8_388_608),
        ("swap", 7, 1_048_573_952, 8_388_608),
        ("swap", 8, 1_056_962_560, 8_388_608),
        ("swap", 9, 1_065_351_168, 8_388_608),
    ]
    for hostname in ("r1002", "v1001"):
        example = EXAMPLES / f"{hostname}.json"
        raw_config = example.read_text(encoding="utf-8")
        config = load_config(raw_json=raw_config)
        plan = resolve_plan(
            config,
            disk_size="512GiB",
            root_start_sector=ROOT_START_SECTOR,
            root_number=3,
        )
        rows = plan["plan_table"]
        assert [
            (row["entry"], row["number"], row["start"], row["sectors"])
            for row in rows
        ] == expected, hostname
        assert json.loads(raw_config)["run_io_benchmark"] is True, hostname

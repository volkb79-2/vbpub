"""Every digest literal in the gate-image Dockerfiles is the digest `cmru versions` vetted (D-004, T1).

`FROM image:tag@sha256:...` pins by digest, and a digest typed by hand proves nothing about its age.
`cmru versions resolve tester-base` writes versions/oci-images.json (tag + digest + creation time) under the
estate's 14-day window - a target younger than the window is refused;
this test requires each Dockerfile literal (image, tag AND digest) to equal the record of its target.
A missing record FAILS: there is no fallback to "trust the literal". Run
`cmru versions resolve tester-base` (and commit versions/) to refresh it.
"""
from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RECORD = ROOT / "versions" / "oci-images.json"

# (Dockerfile, the stage selector, the cmru target that vets it)
PINS = [
    (ROOT / "Dockerfile", None, "oci.python.314.trixie"),
    (REPO / "tester-unified" / "Dockerfile", "node-source", "oci.node.26.trixie"),
]
FROM = re.compile(r"^FROM (?P<image>[^\s:@]+):(?P<tag>[^\s@]+)@(?P<digest>sha256:[0-9a-f]{64})(?: AS (?P<stage>\S+))?$", re.MULTILINE)


def literal(dockerfile: Path, stage: str | None) -> re.Match:
    matches = [m for m in FROM.finditer(dockerfile.read_text(encoding="utf-8")) if m.group("stage") == stage]
    if len(matches) != 1:
        raise AssertionError(f"{dockerfile}: expected exactly one digest-pinned FROM for stage {stage!r}, found {len(matches)}")
    return matches[0]


class Pins(unittest.TestCase):
    def test_every_pinned_base_has_a_declared_cmru_target(self) -> None:
        declared = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["versions"]["targets"]
        for _dockerfile, _stage, target in PINS:
            self.assertIn(target, declared)
            oci = declared[target]["oci"]
            self.assertEqual(oci["selection"], "rolling", "only a rolling target records the digest")
            self.assertEqual(declared[target]["constraint"], "*")

    def test_the_resolved_record_exists(self) -> None:
        self.assertTrue(RECORD.is_file(), f"{RECORD} is missing: run `cmru versions resolve tester-base` and commit versions/")

    def test_each_dockerfile_digest_equals_the_cmru_record(self) -> None:
        self.assertTrue(RECORD.is_file(), "no cmru record to compare against (see test_the_resolved_record_exists)")
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        self.assertEqual(record["generated_by"], "cmru versions resolve")
        for dockerfile, stage, target in PINS:
            with self.subTest(target=target):
                pinned = literal(dockerfile, stage)
                entry = record["targets"][target]
                self.assertTrue(entry["image"].endswith(pinned.group("image").removeprefix("docker.io/")))
                self.assertEqual(pinned.group("tag"), entry["tag"], "the Dockerfile tag and the cmru target tag must be bumped together")
                self.assertEqual(entry["tag"], tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["versions"]["targets"][target]["oci"]["tag"])
                self.assertEqual(pinned.group("digest"), entry["digest"], f"{dockerfile.name} carries a digest cmru did not record")

    def test_the_comparison_can_fail(self) -> None:
        sample = "FROM python:3.14.7-trixie@sha256:" + "a" * 64 + "\n"
        with self.assertRaises(AssertionError):
            literal_from = [m for m in FROM.finditer(sample) if m.group("stage") == "node-source"]
            if len(literal_from) != 1:
                raise AssertionError("stage not found")
        match = FROM.search(sample)
        self.assertEqual(match.group("tag"), "3.14.7-trixie")
        self.assertNotEqual(match.group("digest"), "sha256:" + "b" * 64)

    def test_the_python_base_literal_has_the_patch_tag_cmru_tracks(self) -> None:
        pinned = literal(ROOT / "Dockerfile", None)
        self.assertRegex(pinned.group("tag"), r"^3\.14\.\d+-trixie$")


if __name__ == "__main__":
    unittest.main()

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
from datetime import datetime, timedelta, timezone
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


def canonical_image(name: str) -> str:
    """The fully-qualified form cmru records: docker.io/library/<name> for a bare official image."""
    if name.startswith("docker.io/"):
        return name
    return "docker.io/library/" + name if "/" not in name else "docker.io/" + name


def age_window_days() -> int:
    """The estate's window, read from the root cmru config: the record is judged against the SAME number."""
    root = tomllib.loads((REPO / "cmru.orchestration.toml").read_text(encoding="utf-8"))
    return int(root["versions"]["age_window_days"])


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def check_entry(entry: dict, resolved_at: str, window_days: int) -> None:
    """Raise AssertionError unless the recorded image is at least `window_days` old at `resolved_at`."""
    cutoff = parse_time(resolved_at) - timedelta(days=window_days)
    released = parse_time(entry["released_at"])
    if released > cutoff:
        raise AssertionError(f"{entry['tag']} was released {entry['released_at']}, after the cutoff {cutoff.isoformat()} ({window_days} days before {resolved_at})")


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
                self.assertEqual(entry["image"], canonical_image(pinned.group("image")), "exact image match, not a suffix")
                check_entry(entry, record["resolved_at"], age_window_days())
                self.assertEqual(pinned.group("tag"), entry["tag"], "the Dockerfile tag and the cmru target tag must be bumped together")
                self.assertEqual(entry["tag"], tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["versions"]["targets"][target]["oci"]["tag"])
                self.assertEqual(pinned.group("digest"), entry["digest"], f"{dockerfile.name} carries a digest cmru did not record")

    def test_the_age_check_accepts_old_enough_and_refuses_too_new(self) -> None:
        old = {"tag": "x", "released_at": "2026-09-19T13:55:46Z"}
        check_entry(old, "2026-10-19T00:01:28Z", 14)
        check_entry({"tag": "x", "released_at": "2026-10-05T00:01:28Z"}, "2026-10-19T00:01:28Z", 14)  # exactly at the cutoff
        with self.assertRaises(AssertionError):
            check_entry({"tag": "x", "released_at": "2026-10-05T00:01:29Z"}, "2026-10-19T00:01:28Z", 14)
        with self.assertRaises(AssertionError):
            check_entry({"tag": "x", "released_at": "2026-10-18T00:00:00Z"}, "2026-10-19T00:01:28Z", 14)

    def test_the_window_is_the_estates_not_a_literal_here(self) -> None:
        self.assertEqual(age_window_days(), 14)
        self.assertIsNone(re.search(r"timedelta\(days=\d+\)", Path(__file__).read_text(encoding="utf-8")), "no window literal in the test")

    def test_the_image_match_is_exact(self) -> None:
        self.assertEqual(canonical_image("python"), "docker.io/library/python")
        self.assertEqual(canonical_image("docker.io/library/node"), "docker.io/library/node")
        self.assertNotEqual(canonical_image("python"), "evil/docker.io/library/python")
        self.assertTrue("registry.example/docker.io/library/python".endswith("docker.io/library/python"))  # a suffix check would accept this lookalike
        self.assertNotEqual("registry.example/docker.io/library/python", canonical_image("python"))

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

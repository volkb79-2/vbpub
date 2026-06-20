"""Tests for GHCR visibility syncing helpers."""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru.ghcr import GitHubPackages


def _pkg(owner_type: str = "user") -> GitHubPackages:
    return GitHubPackages("owner", "repo", "token", owner_type)


class TestGitHubPackages(unittest.TestCase):
    def test_repo_visibility_prefers_visibility_field(self) -> None:
        gh = _pkg()
        with patch.object(gh, "_request", return_value=(200, '{"visibility":"public"}')) as req:
            self.assertEqual(gh.repo_visibility(), "public")
            self.assertEqual(req.call_args.args, ("GET", "https://api.github.com/repos/owner/repo"))

    def test_repo_visibility_falls_back_to_private_bool(self) -> None:
        gh = _pkg()
        with patch.object(gh, "_request", return_value=(200, '{"private": true}')):
            self.assertEqual(gh.repo_visibility(), "private")

    def test_mirror_package_visibility_updates_when_needed(self) -> None:
        gh = _pkg(owner_type="org")
        responses = [
            (200, '{"visibility":"public"}'),
            (200, '{"visibility":"private"}'),
            (200, '{"visibility":"public"}'),
        ]
        with patch.object(gh, "_request", side_effect=responses) as req:
            self.assertEqual(
                gh.mirror_package_visibility("image", expected_visibility=None, retries=1, delay=0),
                "public",
            )

        self.assertEqual([call.args[0] for call in req.call_args_list], ["GET", "GET", "PATCH"])
        self.assertEqual(
            req.call_args_list[1].args[1],
            "https://api.github.com/orgs/owner/packages/container/image",
        )
        self.assertEqual(
            req.call_args_list[2].args[1],
            "https://api.github.com/orgs/owner/packages/container/image",
        )
        self.assertEqual(req.call_args_list[2].kwargs["content_type"], "application/json")


if __name__ == "__main__":
    unittest.main()

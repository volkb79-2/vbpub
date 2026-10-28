"""Tests for GHCR visibility syncing helpers."""
from __future__ import annotations

import io
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

    # KI-97: the sync runs after a successful push; a package that cannot be
    # inspected must not fail the release by default.
    def test_missing_package_after_retries_warns_and_returns_none(self) -> None:
        gh = _pkg()
        err = io.StringIO()
        with (
            patch.object(gh, "_request", return_value=(404, "")) as req,
            patch("cmru.ghcr.time.sleep") as sleep,
            patch("cmru.ghcr.sys.stderr", err),
        ):
            result = gh.mirror_package_visibility(
                "pwmcp-ocr", expected_visibility="public", retries=3, delay=5,
            )
        self.assertIsNone(result)
        self.assertEqual(len(req.call_args_list), 3)
        self.assertEqual(sleep.call_count, 2)
        text = err.getvalue()
        self.assertIn("[WARN] Could not sync GHCR visibility for pwmcp-ocr", text)
        self.assertIn("not found after 3 lookup(s) over about 10 s", text)
        self.assertIn("The image push already succeeded", text)
        self.assertIn("Change visibility -> public", text)
        self.assertIn("link the package to its repository", text)
        self.assertNotIn("[ERROR]", text)

    def test_missing_package_fails_only_when_the_caller_demands_it(self) -> None:
        gh = _pkg()
        err = io.StringIO()
        with (
            patch.object(gh, "_request", return_value=(404, "")),
            patch("cmru.ghcr.time.sleep"),
            patch("cmru.ghcr.sys.stderr", err),
        ):
            with self.assertRaises(SystemExit) as ctx:
                gh.mirror_package_visibility(
                    "image", expected_visibility="public", retries=2, delay=0, missing="fail",
                )
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("[ERROR] GHCR package image cannot be inspected", err.getvalue())
        with self.assertRaises(ValueError):
            gh.mirror_package_visibility("image", expected_visibility="public", missing="maybe")

    def test_unauthorized_lookup_warns_with_the_scope_hint_and_stops_retrying(self) -> None:
        gh = _pkg()
        err = io.StringIO()
        with (
            patch.object(gh, "_request", return_value=(403, '{"message":"forbidden"}')) as req,
            patch("cmru.ghcr.time.sleep") as sleep,
            patch("cmru.ghcr.sys.stderr", err),
        ):
            result = gh.mirror_package_visibility(
                "image", expected_visibility="public", retries=4, delay=5,
            )
        self.assertIsNone(result)
        self.assertEqual(len(req.call_args_list), 1)
        sleep.assert_not_called()
        text = err.getvalue()
        self.assertIn("the token may not read it (HTTP 403)", text)
        self.assertIn("read:packages", text)
        with (
            patch.object(gh, "_request", return_value=(403, "")),
            patch("cmru.ghcr.sys.stderr", io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                gh.mirror_package_visibility(
                    "image", expected_visibility="public", retries=1, delay=0, missing="fail",
                )

    def test_other_lookup_errors_still_fail(self) -> None:
        gh = _pkg()
        with (
            patch.object(gh, "_request", return_value=(500, "boom")),
            patch("cmru.ghcr.sys.stderr", io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                gh.mirror_package_visibility("image", expected_visibility="public", retries=1)


if __name__ == "__main__":
    unittest.main()

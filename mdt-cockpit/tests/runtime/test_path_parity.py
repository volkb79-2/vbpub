"""11b - one PATH order, everywhere (D5).

`sh -c`, `bash -c`, `bash -lc`, `bash -ic`, `zsh -ic` and `zsh -lc` must all see the
SAME PATH: the image's managed entries first in the D5 order, the base image's own
entries in the middle, `~/.local/bin` last, each managed entry exactly once. Login
shells are the interesting cases - Debian's /etc/profile replaces PATH - and the
interactive ones must not re-order or duplicate it either.
"""
from __future__ import annotations

import unittest

import harness

SHELLS = (
    ("sh -c", ["sh", "-c"]),
    ("bash -c", ["bash", "-c"]),
    ("bash -lc", ["bash", "-lc"]),
    ("bash -ic", ["bash", "-ic"]),
    ("zsh -ic", ["zsh", "-ic"]),
    ("zsh -lc", ["zsh", "-lc"]),
)


class Contract:
    def _image_path(self) -> list[str]:
        return self.image.env["PATH"].split(":")

    def test_the_images_own_path_has_the_d5_shape(self) -> None:
        entries = self._image_path()
        prefix = list(harness.PATH_PREFIX)
        suffix = list(harness.PATH_SUFFIX)
        self.assertEqual(entries[: len(prefix)], prefix, "managed entries first, in order")
        self.assertEqual(entries[-len(suffix):], suffix, "~/.local/bin last")
        for managed in prefix + suffix:
            self.assertEqual(entries.count(managed), 1, f"{managed} must appear exactly once")
        self.assertEqual(self.image.env["MDT_IMAGE_PATH"], self.image.env["PATH"])

    def test_every_shell_sees_the_identical_path(self) -> None:
        expected = self.image.env["PATH"]
        for label, argv in SHELLS:
            with self.subTest(shell=label):
                result = harness.run(self.image.ref, [*argv, "echo \"PATH-IS:$PATH\""], user="vscode")
                self.assertOk(result, label)
                lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
                self.assertEqual(len(lines), 1, f"{label}: expected one PATH line\n{result.output}")
                self.assertEqual(lines[0], "PATH-IS:" + expected, label)

    def test_a_command_resolves_the_same_in_every_shell(self) -> None:
        """The order has an observable consequence: `python` is the venv's everywhere."""
        for label, argv in SHELLS:
            with self.subTest(shell=label):
                result = harness.run(self.image.ref, [*argv, "echo \"WHICH:$(command -v python)\""], user="vscode")
                self.assertOk(result, label)
                self.assertIn("WHICH:/opt/mdt/venv/bin/python", result.stdout.splitlines(), label)


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()

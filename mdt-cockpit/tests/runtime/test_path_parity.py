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
        self.assertNotIn("MDT_IMAGE_PATH", self.image.env, "an env snapshot cannot survive a cleared environment")

    def test_every_shell_sees_the_identical_path(self) -> None:
        expected = self.image.env["PATH"]
        for label, argv in SHELLS:
            with self.subTest(shell=label):
                result = harness.run(self.image.ref, [*argv, "echo \"PATH-IS:$PATH\""], user="vscode")
                self.assertOk(result, label)
                lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
                self.assertEqual(len(lines), 1, f"{label}: expected one PATH line\n{result.output}")
                self.assertEqual(lines[0], "PATH-IS:" + expected, label)

    def _path_line(self, argv: list[str], **kwargs) -> tuple[str, harness.Completed]:
        result = harness.run(self.image.ref, argv, **kwargs)
        lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
        return (lines[-1] if lines else ""), result

    def test_login_shells_with_a_cleared_environment_get_the_same_path(self) -> None:
        """su -, sudo -i, ssh and cron start login shells WITHOUT the image's ENV; the PATH
        must come from a file the build wrote, not from an environment variable."""
        expected = "PATH-IS:" + self.image.env["PATH"]
        script = 'echo "PATH-IS:$PATH"'
        cases = {
            "env -i bash -lc": (["env", "-i", "bash", "-lc", script], {"user": "vscode"}),
            "env -i zsh -lc": (["env", "-i", "zsh", "-lc", script], {"user": "vscode"}),
            "su -l vscode -c (as root)": (["su", "-l", "vscode", "-c", script], {"user": "0"}),
        }
        for label, (argv, kwargs) in cases.items():
            with self.subTest(shell=label):
                line, result = self._path_line(argv, **kwargs)
                self.assertEqual(line, expected, f"{label}\n{result.output}")

    def test_a_missing_path_file_is_reported_not_skipped(self) -> None:
        """The hook must not silently do nothing: one line on stderr, PATH left alone."""
        for login_shell in ("/bin/bash", "/usr/bin/zsh"):
            with self.subTest(shell=login_shell):
                result = harness.run(
                    self.image.ref,
                    ["bash", "-c",
                     "mv /usr/local/share/mdt-cockpit/path /tmp/path.gone && "
                     f"env -i {login_shell} -lc 'echo \"PATH-IS:$PATH\"'"],
                    user="0",
                )
                self.assertEqual(result.returncode, 0, result.output)
                self.assertIn("mdt-cockpit: /usr/local/share/mdt-cockpit/path is missing", result.stderr)
                path_lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
                self.assertEqual(len(path_lines), 1)
                self.assertNotIn("/opt/mdt", path_lines[0], "the managed entries must not appear from nowhere")

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

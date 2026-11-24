"""Global CLI parsing, injected dispatch, and stderr exit outcomes."""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401
from dr_state import errors, cli


class InvocationTest(_support.TemporaryRootTest):
    def test_global_options_paths_and_relative_refusal(self) -> None:
        invocation = cli.parse_invocation(
            ("--sysroot", str(self.root), "--json", "snapshot", "--reason", "apt")
        )
        self.assertEqual(invocation.paths.sysroot, self.root)
        self.assertEqual(invocation.command, ("snapshot", "--reason", "apt"))
        self.assertTrue(invocation.json)
        with self.assertRaisesRegex(errors.UsageError, "absolute"):
            cli.parse_invocation(("--sysroot", "relative", "list"))

    def test_global_options_after_the_verb_belong_to_the_verb(self) -> None:
        invocation = cli.parse_invocation(("snapshot", "--note", "--json"))
        self.assertEqual(invocation.command, ("snapshot", "--note", "--json"))
        self.assertFalse(invocation.json)
        invocation = cli.parse_invocation(
            ("--sysroot", str(self.root), "show", "--sysroot", "/elsewhere", "--json", "@os-1")
        )
        self.assertEqual(invocation.paths.sysroot, self.root)
        self.assertEqual(
            invocation.command, ("show", "--sysroot", "/elsewhere", "--json", "@os-1")
        )
        self.assertFalse(invocation.json)

    def test_default_sysroot_is_root(self) -> None:
        invocation = cli.parse_invocation(("--json", "show", "@os-1"))
        self.assertEqual(invocation.paths.sysroot, Path("/"))
        self.assertEqual(invocation.paths.top, Path("/.top"))
        self.assertTrue(invocation.json)


class ExitTranslationTest(unittest.TestCase):
    def run_error(self, error: BaseException, *, debug: bool | None = None) -> tuple[int, str]:
        stderr = io.StringIO()

        def dispatch(invocation: cli.Invocation) -> int:
            raise error

        code = cli.run(("list",), dispatch, stderr=stderr, debug=debug)
        return code, stderr.getvalue()

    def test_every_error_maps_to_its_exit_code_and_one_reason_line(self) -> None:
        cases = (
            (errors.UsageError("bad request"), 2),
            (errors.Refused("unsafe"), 3),
            (errors.FormatError("newer format"), 3),
            (errors.BootConfigError("bad selector"), 3),
            (errors.Unsupported("no one-shot"), 4),
            (errors.CheckFailed("violations"), 5),
            (errors.LockTimeout("busy"), 6),
            (errors.StorageError("storage failed"), 1),
            (errors.BootError("boot failed"), 1),
            (errors.CapacityError("full"), 1),
        )
        for error, wanted in cases:
            with self.subTest(error=error):
                self.assertEqual(self.run_error(error), (wanted, f"dr-state: {error}\n"))

    def test_generic_exception_traceback_is_debug_only(self) -> None:
        error = RuntimeError("unexpected")
        self.assertEqual(self.run_error(error), (70, "dr-state: unexpected\n"))
        code, stderr = self.run_error(error, debug=True)
        self.assertEqual(code, 70)
        self.assertIn("Traceback (most recent call last)", stderr)
        self.assertEqual(self.run_error(errors.Refused("unsafe"), debug=True), (3, "dr-state: unsafe\n"))

    def test_dispatch_and_missing_command_outcomes(self) -> None:
        stderr = io.StringIO()
        self.assertEqual(cli.run(("list",), lambda invocation: 0, stderr=stderr), 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(cli.run((), lambda invocation: 0, stderr=stderr), 2)
        self.assertIn("command is required", stderr.getvalue())

    def test_json_is_refused_before_dispatch_in_both_positions(self) -> None:
        dispatch = mock.Mock()
        for argv in (
            ("--json", "list"),
            ("show", "@os-000001-20261118-120000Z-install", "--json"),
        ):
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                self.assertEqual(cli.run(argv, dispatch, stderr=stderr), 4)
                self.assertEqual(
                    stderr.getvalue(),
                    "dr-state: JSON output is not supported in this runtime\n",
                )
        dispatch.assert_not_called()

    def test_global_json_without_a_command_is_unsupported(self) -> None:
        stderr = io.StringIO()
        original = cli.sys.stderr
        try:
            cli.sys.stderr = stderr
            self.assertEqual(cli.main(("--json",)), 4)
        finally:
            cli.sys.stderr = original
        self.assertIn("JSON output is not supported", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

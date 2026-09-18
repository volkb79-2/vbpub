"""Argument parser diagnostics for the standalone topos CLI."""

from __future__ import annotations

import argparse
import sys

from topos import __version__


def cli_headline() -> str:
    return f"TOPOS {__version__} — host telemetry and resource control"


def print_cli_error(message: object, *, stream=None) -> None:
    """Write a human-facing CLI diagnostic with the product identity first."""
    if stream is None:
        stream = sys.stderr
    print(cli_headline(), file=stream)
    print(message, file=stream)


class ToposArgumentParser(argparse.ArgumentParser):
    """Prefix parser help, usage and errors with the topos build identity."""

    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)

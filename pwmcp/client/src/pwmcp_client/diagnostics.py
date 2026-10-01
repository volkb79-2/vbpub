"""CLI diagnostics for the independently installable pwmcp client."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def cli_headline() -> str:
    return f"PWMCP {__version__} — client utility"


class PwmcpArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)

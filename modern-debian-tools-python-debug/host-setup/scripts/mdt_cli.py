"""Standalone parser diagnostics for modern-debian-tools scripts."""

from __future__ import annotations

import argparse
import os
import sys


def mdt_version() -> str:
    return os.environ.get("MDT_VERSION", "").strip() or os.environ.get(
        "MDT_IMAGE_VERSION", ""
    ).strip() or "unknown"


def cli_headline() -> str:
    return f"MDT {mdt_version()} — modern Debian tools and Python debug"


class MdtArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)

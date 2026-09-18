#!/usr/bin/env python3
"""Shared CLI helpers."""

from __future__ import annotations

import argparse
import sys


def get_cli_version() -> str:
    try:
        from importlib.metadata import version as package_version

        return package_version("ciu")
    except Exception:
        try:
            from . import __version__  # type: ignore

            return __version__
        except Exception:
            return "unknown"


def cli_headline() -> str:
    """Return the dynamic product headline used by every CIU parser."""
    return f"CIU {get_cli_version()} — Container Infrastructure Utility"


def cli_error(message: object, *, stream=None) -> None:
    """Write one human-facing CIU diagnostic with its identity first."""
    if stream is None:
        stream = sys.stderr
    print(cli_headline(), file=stream)
    print(message, file=stream)


class CiuArgumentParser(argparse.ArgumentParser):
    """Argument parser whose diagnostics identify the running CIU build."""

    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)

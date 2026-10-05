"""CLI identity and authoritative version lookup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.+-]+)?")


class VersionLookupError(RuntimeError):
    """The requested distribution does not expose an installed version."""


@dataclass(frozen=True)
class CliIdentity:
    """The single identity used by a CLI's help, diagnostics, and version output.

    ``name`` is the display name (normally uppercase in help), while
    ``command`` is the executable spelling used by the short version output.
    Keeping both lets adopters match existing conventions such as ``CIU`` in
    a headline and ``ciu 7.15.0`` for ``--version``.
    """

    name: str
    version: str
    long_name: str
    command: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "version", "long_name"):
            value = getattr(self, field_name)
            if not value or "\n" in value or "\r" in value:
                raise ValueError(
                    f"CLI identity field {field_name!r} must be a non-empty single line"
                )
        if self.command is not None and (
            not self.command or "\n" in self.command or "\r" in self.command
        ):
            raise ValueError("CLI identity command must be a non-empty single line")

    @property
    def command_name(self) -> str:
        """Return the executable spelling used by ``version`` output."""

        return self.command or self.name.lower()

    @property
    def headline(self) -> str:
        """Return the first line of human-facing help and diagnostics."""

        return f"{self.name} {self.version} — {self.long_name}"

    @property
    def version_line(self) -> str:
        """Return the exact side-effect-free ``version`` output."""

        return f"{self.command_name} {self.version}"

    @classmethod
    def from_distribution(
        cls,
        *,
        name: str,
        distribution: str,
        long_name: str,
        command: str | None = None,
    ) -> CliIdentity:
        """Build an identity from installed package metadata.

        There is deliberately no invented runtime version fallback. A source
        checkout that has not been installed can pass its project-owned
        version source directly to :class:`CliIdentity` instead.
        """

        try:
            version = installed_version(distribution)
        except PackageNotFoundError as exc:
            raise VersionLookupError(
                f"cannot determine the installed version of distribution {distribution!r}"
            ) from exc
        return cls(name=name, version=version, long_name=long_name, command=command)

    @classmethod
    def resolve(
        cls,
        *,
        name: str,
        long_name: str,
        command: str | None = None,
        distribution: str | None = None,
        version_file: str | os.PathLike | None = None,
    ) -> CliIdentity:
        """Build an identity from installed metadata and/or a version file.

        Every given source is consulted. A source that does not exist is
        "unresolved"; a malformed version file is an error. When both sources
        resolve they must agree. There is never a literal fallback.
        """

        if distribution is None and version_file is None:
            raise ValueError(
                "CliIdentity.resolve needs a distribution, a version_file, or both"
            )
        path: Path | None = None
        if version_file is not None:
            path = Path(version_file)
            if not path.is_absolute():
                raise ValueError(f"version_file must be an absolute path, got {str(path)!r}")
        resolved: list[tuple[str, str]] = []
        missing: list[str] = []
        if distribution is not None:
            try:
                resolved.append((f"distribution {distribution!r}", installed_version(distribution)))
            except PackageNotFoundError:
                missing.append(f"distribution {distribution!r} is not installed")
        if path is not None:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                missing.append(f"version file '{path}' does not exist")
            except OSError as exc:
                raise VersionLookupError(f"cannot read version file '{path}': {exc}") from exc
            else:
                if not _VERSION_RE.fullmatch(text):
                    raise VersionLookupError(
                        f"version file '{path}' has malformed content {text!r}"
                    )
                resolved.append((f"version file '{path}'", text))
        if not resolved:
            raise VersionLookupError("; ".join(missing))
        if len({value for _, value in resolved}) > 1:
            (src_a, val_a), (src_b, val_b) = resolved
            raise VersionLookupError(
                f"version sources disagree: {src_a} says {val_a!r}, {src_b} says {val_b!r}"
            )
        return cls(name=name, version=resolved[0][1], long_name=long_name, command=command)

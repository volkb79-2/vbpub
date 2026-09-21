"""CLI identity and authoritative version lookup."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version


class VersionLookupError(RuntimeError):
    """The requested distribution does not expose an installed version."""


@dataclass(frozen=True, slots=True)
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

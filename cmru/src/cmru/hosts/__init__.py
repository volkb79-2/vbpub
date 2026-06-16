"""ReleaseHost abstraction (S11). Each provider implements this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class ReleaseHost(ABC):
    """Provider interface for a release storage system (S11.1).

    v1 ships GitHub only. Gitea/Forgejo and S3/MinIO are fast-follow (S13).
    """

    @abstractmethod
    def create_release(
        self,
        tag: str,
        name: str,
        body: str,
        commitish: Optional[str] = None,
        draft: bool = False,
        prerelease: bool = False,
    ) -> str:
        """Create a release; return its ID (str)."""

    @abstractmethod
    def upload_asset(self, release_id: str, path: Path, content_type: str = "application/octet-stream") -> str:
        """Upload asset to release; return download URL."""

    @abstractmethod
    def list_releases(self, prefix: str) -> List[Dict[str, Any]]:
        """Return releases matching prefix, each with at least {tag, id, assets:[{name,url,sha256?}]}."""

    @abstractmethod
    def resolve_latest(self, prefix: str) -> Optional[Dict[str, Any]]:
        """Return {version, tag, asset, sha256, url} for highest-semver prefix release (S5)."""

    @abstractmethod
    def download_url(self, tag: str, asset_name: str) -> str:
        """Return the download URL for a specific asset on a specific tag."""

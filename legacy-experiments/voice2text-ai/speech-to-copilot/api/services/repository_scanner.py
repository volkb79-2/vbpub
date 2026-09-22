"""Repository Scanner - Real Implementation

Scans filesystem for coding terms to build context vocabulary.
Also scans GitHub PRs and issues for technical terminology.
Falls back to demo mode if filesystem access fails.
"""
from __future__ import annotations

import re
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    # Fallback to standard logging if structlog is not available
    import logging
    logger = logging.getLogger(__name__)

try:
    from services.github_client import GitHubClient
except ImportError:
    GitHubClient = None  # type: ignore


class RepositoryScanner:
    def __init__(self, root_path: str = ".", github_enabled: bool = True):
        self.root_path = Path(root_path).resolve()
        self._cached_terms: Dict[str, List[str]] = {}
        self._last_scan_time: Optional[datetime] = None
        self._cache_ttl = 3600  # 1 hour
        
        # Initialize GitHub client if enabled
        self.github_enabled = github_enabled and GitHubClient is not None
        self.github_client: Optional[GitHubClient] = None
        if self.github_enabled:
            try:
                self.github_client = GitHubClient()
                logger.info("GitHub integration enabled for repository scanning")
            except Exception as e:
                logger.warning("GitHub client initialization failed", error=str(e))
                self.github_enabled = False
        
        # File extensions to scan by language
        self.language_extensions = {
            'python': {'.py', '.pyi'},
            'typescript': {'.ts', '.tsx'},
            'javascript': {'.js', '.jsx', '.mjs'},
            'rust': {'.rs'},
            'go': {'.go'},
            'java': {'.java'},
            'cpp': {'.cpp', '.cc', '.cxx', '.hpp', '.h'},
            'c': {'.c', '.h'},
        }
        
        # Common programming identifiers pattern
        self.identifier_pattern = re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b')
        
        # Demo fallback terms
        self._demo_terms = [
            "asyncio", "fastapi", "websocket", "transcription", "llm", "whisper", 
            "token", "repository", "context", "diff", "stream", "incremental",
            "buffer", "prefix", "suffix", "enhancement", "scanner", "vocabulary", 
            "prompt", "cache", "client", "server", "api", "endpoint", "router",
            "model", "response", "request", "service", "utils", "config"
        ]

    async def health_check(self) -> bool:
        """Check if scanner can access the root path"""
        try:
            return self.root_path.exists() and self.root_path.is_dir()
        except Exception:
            return False

    async def scan_repository(
        self,
        languages: List[str],
        force_refresh: bool = False,
        max_files: int = 500
    ) -> Dict[str, Any]:
        """Scan repository for programming identifiers"""
        
        # Check cache first
        cache_key = f"scan_{hash(tuple(sorted(languages)))}"
        if not force_refresh and cache_key in self._cached_terms:
            if self._last_scan_time and (datetime.now() - self._last_scan_time).seconds < self._cache_ttl:
                logger.info("Using cached repository scan", languages=languages)
                return {
                    "files_scanned": "cached",
                    "terms_extracted": len(self._cached_terms[cache_key]),
                    "languages": languages,
                    "last_scan": self._last_scan_time.isoformat(),
                    "cached": True,
                    "terms": self._cached_terms[cache_key][:20]  # Sample
                }
        
        try:
            # Get file extensions for requested languages
            extensions_to_scan: Set[str] = set()
            for lang in languages:
                extensions_to_scan.update(self.language_extensions.get(lang, set()))
            
            if not extensions_to_scan:
                # Default to common extensions
                extensions_to_scan = {'.py', '.ts', '.js', '.rs', '.go'}
            
            # Scan files
            all_terms = Counter()
            files_scanned = 0
            
            for file_path in self._walk_files(extensions_to_scan, max_files):
                try:
                    terms = await self._extract_terms_from_file(file_path)
                    all_terms.update(terms)
                    files_scanned += 1
                except Exception as e:
                    logger.debug("Failed to scan file", file=str(file_path), error=str(e))
                    continue
            
            # Get most common terms
            top_terms = [term for term, count in all_terms.most_common(100)]
            
            # Cache results
            self._cached_terms[cache_key] = top_terms
            self._last_scan_time = datetime.now()
            
            logger.info("Repository scan completed",
                       files_scanned=files_scanned,
                       terms_found=len(top_terms),
                       languages=languages)
            
            return {
                "files_scanned": files_scanned,
                "terms_extracted": len(top_terms),
                "languages": languages,
                "last_scan": self._last_scan_time.isoformat(),
                "cached": False,
                "terms": top_terms[:20]  # Sample for debugging
            }
            
        except Exception as e:
            logger.error("Repository scan failed, using demo terms", error=str(e))
            
            # Fallback to demo terms
            self._cached_terms[cache_key] = self._demo_terms
            self._last_scan_time = datetime.now()
            
            return {
                "files_scanned": 0,
                "terms_extracted": len(self._demo_terms),
                "languages": languages,
                "last_scan": self._last_scan_time.isoformat(),
                "cached": False,
                "fallback": True,
                "terms": self._demo_terms[:20]
            }

    async def scan_github_prs_and_issues(
        self,
        max_prs: int = 50,
        max_issues: int = 50,
        since_days: int = 90
    ) -> Dict[str, Any]:
        """Scan GitHub PRs and issues for technical terminology
        
        Args:
            max_prs: Maximum number of PRs to scan
            max_issues: Maximum number of issues to scan
            since_days: Only scan from last N days
            
        Returns:
            Dict with scan results including extracted terms
        """
        if not self.github_enabled or not self.github_client:
            logger.warning("GitHub scanning not enabled or client not available")
            return {
                "prs_scanned": 0,
                "issues_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "enabled": False
            }
        
        try:
            # Scan PRs
            pr_results = await self.github_client.scan_pull_requests(
                state="all",
                max_prs=max_prs,
                since_days=since_days
            )
            
            # Scan issues
            issue_results = await self.github_client.scan_issues(
                state="all",
                max_issues=max_issues,
                since_days=since_days
            )
            
            # Combine terms from both sources
            all_terms = Counter()
            for term in pr_results.get("terms", []):
                all_terms[term] += 1
            for term in issue_results.get("terms", []):
                all_terms[term] += 1
            
            # Get top combined terms
            top_terms = [term for term, count in all_terms.most_common(100)]
            
            logger.info("GitHub scan completed",
                       prs_scanned=pr_results.get("prs_scanned", 0),
                       issues_scanned=issue_results.get("issues_scanned", 0),
                       terms_found=len(top_terms))
            
            return {
                "prs_scanned": pr_results.get("prs_scanned", 0),
                "issues_scanned": issue_results.get("issues_scanned", 0),
                "terms_extracted": len(top_terms),
                "terms": top_terms,
                "enabled": True,
                "since_days": since_days
            }
            
        except Exception as e:
            logger.error("GitHub scanning failed", error=str(e), exc_info=True)
            return {
                "prs_scanned": 0,
                "issues_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "enabled": True,
                "error": str(e)
            }

    async def get_comprehensive_context(
        self,
        file_types: Optional[List[str]] = None,
        include_github: bool = True,
        max_terms: int = 50
    ) -> List[str]:
        """Get comprehensive context combining filesystem and GitHub scanning
        
        Args:
            file_types: Programming languages to scan for
            include_github: Whether to include GitHub PR/issue scanning
            max_terms: Maximum number of terms to return
            
        Returns:
            List of technical terms for context
        """
        if not file_types:
            file_types = ['python', 'typescript', 'javascript']
        
        all_terms = Counter()
        
        # Scan filesystem
        try:
            fs_result = await self.scan_repository(file_types, force_refresh=False)
            for term in fs_result.get('terms', []):
                all_terms[term] += 2  # Weight filesystem terms higher
        except Exception as e:
            logger.warning("Filesystem scan failed", error=str(e))
        
        # Scan GitHub if enabled
        if include_github and self.github_enabled:
            try:
                gh_result = await self.scan_github_prs_and_issues()
                for term in gh_result.get('terms', []):
                    all_terms[term] += 1  # Lower weight for GitHub terms
            except Exception as e:
                logger.warning("GitHub scan failed", error=str(e))
        
        # Return top terms by combined weight
        top_terms = [term for term, count in all_terms.most_common(max_terms)]
        
        if not top_terms:
            # Fallback to demo terms
            top_terms = self._demo_terms[:max_terms]
        
        logger.info("Comprehensive context generated",
                   total_terms=len(top_terms),
                   included_github=include_github and self.github_enabled)
        
        return top_terms

    def _walk_files(self, extensions: Set[str], max_files: int) -> List[Path]:
        """Walk directory tree and collect files with target extensions"""
        found_files = []
        
        try:
            for root, dirs, files in os.walk(self.root_path):
                # Skip hidden and common non-source directories
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {
                    'node_modules', '__pycache__', 'target', 'build', 'dist', '.git'
                }]
                
                for file in files:
                    if any(file.endswith(ext) for ext in extensions):
                        file_path = Path(root) / file
                        found_files.append(file_path)
                        
                        if len(found_files) >= max_files:
                            return found_files
                            
        except Exception as e:
            logger.warning("Error walking directory", path=str(self.root_path), error=str(e))
        
        return found_files

    async def _extract_terms_from_file(self, file_path: Path) -> List[str]:
        """Extract programming identifiers from a source file"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Find all identifiers
            matches = self.identifier_pattern.findall(content)
            
            # Filter out common noise words and very long identifiers
            filtered_terms = []
            noise_words = {'the', 'and', 'for', 'with', 'from', 'import', 'class', 'def', 'if', 'else', 'return'}
            
            for match in matches:
                if (len(match) >= 3 and 
                    len(match) <= 30 and 
                    match.lower() not in noise_words and
                    not match.isdigit()):
                    filtered_terms.append(match)
            
            return filtered_terms
            
        except Exception as e:
            logger.debug("Error reading file", file=str(file_path), error=str(e))
            return []

    async def get_context(self, file_types: List[str] = None, max_terms: int = 20) -> List[str]:
        """Get context terms for voice recognition enhancement"""
        if not file_types:
            file_types = ['python', 'typescript', 'javascript']
        
        # Try to get cached or scan repository
        scan_result = await self.scan_repository(file_types, force_refresh=False)
        terms = scan_result.get('terms', self._demo_terms)
        
        # Return limited subset for context
        return terms[:max_terms]

    async def get_rotating_context(self) -> List[str]:
        """Get rotating context for demo mode"""
        # Simple time-based rotation
        rotation_index = int(time.time()) % len(self._demo_terms) // 4
        
        # Return 4 terms starting from rotation index
        rotated_terms = []
        for i in range(4):
            term_index = (rotation_index + i) % len(self._demo_terms)
            rotated_terms.append(self._demo_terms[term_index])
        
        return rotated_terms

    async def get_realtime_context(self) -> str:
        """Get realtime context as comma-separated string (for backward compatibility)"""
        terms = await self.get_context(['python', 'typescript'], 8)
        return ", ".join(terms[:8])

    def _rotating_slice(self, max_terms: int) -> List[str]:
        if not self._demo_mode:
            return self._last_scan_terms[:max_terms]
        if not self._last_scan_terms:
            return []
        now = int(time.time())
        offset = now % len(self._last_scan_terms)
        extended = self._last_scan_terms[offset:] + self._last_scan_terms[:offset]
        return extended[:max_terms]




__all__ = ["RepositoryScanner"]
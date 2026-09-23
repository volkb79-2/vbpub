"""GitHub API Client

Fetches technical terms from GitHub PRs, issues, and repository content
to enhance speech recognition context.
"""

from __future__ import annotations

import re
import os
from typing import Any, Dict, List, Optional, Set
from datetime import datetime, timedelta
from collections import Counter

try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

import httpx


class GitHubClient:
    """Client for fetching technical terms from GitHub"""
    
    def __init__(
        self,
        repo_owner: Optional[str] = None,
        repo_name: Optional[str] = None,
        token: Optional[str] = None
    ):
        self.repo_owner = repo_owner or os.getenv("GITHUB_REPO_OWNER", "")
        self.repo_name = repo_name or os.getenv("GITHUB_REPO_NAME", "")
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "speech-to-copilot-scanner/1.0"
        }
        
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"
        
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers=self.headers,
            follow_redirects=True
        )
        
        # Technical term extraction pattern
        self.term_pattern = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_-]{2,30}\b')
        
        # Common noise words to filter out
        self.noise_words = {
            'the', 'and', 'for', 'with', 'from', 'this', 'that', 'have',
            'what', 'when', 'where', 'which', 'should', 'would', 'could',
            'will', 'can', 'may', 'must', 'need', 'want', 'like', 'make',
            'get', 'use', 'see', 'know', 'think', 'also', 'just', 'now'
        }
    
    async def health_check(self) -> bool:
        """Check if GitHub API is accessible"""
        try:
            response = await self.client.get(f"{self.base_url}/")
            return response.status_code == 200
        except Exception as e:
            logger.error("GitHub API health check failed", error=str(e))
            return False
    
    async def scan_pull_requests(
        self,
        state: str = "all",
        max_prs: int = 50,
        since_days: int = 90
    ) -> Dict[str, Any]:
        """Scan pull requests for technical terms
        
        Args:
            state: PR state filter ('open', 'closed', 'all')
            max_prs: Maximum number of PRs to scan
            since_days: Only scan PRs from last N days
            
        Returns:
            Dict with scan results including extracted terms
        """
        if not self.repo_owner or not self.repo_name:
            logger.warning("GitHub repository not configured")
            return {
                "prs_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": "Repository not configured"
            }
        
        try:
            since_date = datetime.now() - timedelta(days=since_days)
            
            # Fetch PRs
            url = f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/pulls"
            params = {
                "state": state,
                "per_page": max_prs,
                "sort": "updated",
                "direction": "desc"
            }
            
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            prs = response.json()
            
            # Extract terms from PRs
            all_terms = Counter()
            prs_scanned = 0
            
            for pr in prs:
                # Check if PR is recent enough
                updated_at = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
                if updated_at < since_date.replace(tzinfo=updated_at.tzinfo):
                    continue
                
                prs_scanned += 1
                
                # Extract from title
                terms = self._extract_terms(pr.get("title", ""))
                all_terms.update(terms)
                
                # Extract from body/description
                if pr.get("body"):
                    terms = self._extract_terms(pr["body"])
                    all_terms.update(terms)
                
                # Optionally fetch PR comments (expensive)
                if prs_scanned <= 10:  # Only for most recent 10 PRs
                    try:
                        comments = await self._fetch_pr_comments(pr["number"])
                        for comment in comments:
                            terms = self._extract_terms(comment.get("body", ""))
                            all_terms.update(terms)
                    except Exception as e:
                        logger.debug("Failed to fetch PR comments", pr=pr["number"], error=str(e))
            
            # Get top terms
            top_terms = [term for term, count in all_terms.most_common(100)]
            
            logger.info("PR scan completed",
                       prs_scanned=prs_scanned,
                       terms_found=len(top_terms))
            
            return {
                "prs_scanned": prs_scanned,
                "terms_extracted": len(top_terms),
                "terms": top_terms,
                "since_date": since_date.isoformat()
            }
            
        except httpx.HTTPStatusError as e:
            logger.error("GitHub API request failed",
                        status_code=e.response.status_code,
                        error=str(e))
            return {
                "prs_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": f"API error: {e.response.status_code}"
            }
        except Exception as e:
            logger.error("PR scanning failed", error=str(e), exc_info=True)
            return {
                "prs_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": str(e)
            }
    
    async def scan_issues(
        self,
        state: str = "all",
        max_issues: int = 50,
        since_days: int = 90
    ) -> Dict[str, Any]:
        """Scan issues for technical terms
        
        Args:
            state: Issue state filter ('open', 'closed', 'all')
            max_issues: Maximum number of issues to scan
            since_days: Only scan issues from last N days
            
        Returns:
            Dict with scan results including extracted terms
        """
        if not self.repo_owner or not self.repo_name:
            logger.warning("GitHub repository not configured")
            return {
                "issues_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": "Repository not configured"
            }
        
        try:
            since_date = datetime.now() - timedelta(days=since_days)
            
            # Fetch issues
            url = f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/issues"
            params = {
                "state": state,
                "per_page": max_issues,
                "sort": "updated",
                "direction": "desc"
            }
            
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            issues = response.json()
            
            # Extract terms from issues
            all_terms = Counter()
            issues_scanned = 0
            
            for issue in issues:
                # Skip PRs (they appear in issues endpoint too)
                if "pull_request" in issue:
                    continue
                
                # Check if issue is recent enough
                updated_at = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
                if updated_at < since_date.replace(tzinfo=updated_at.tzinfo):
                    continue
                
                issues_scanned += 1
                
                # Extract from title
                terms = self._extract_terms(issue.get("title", ""))
                all_terms.update(terms)
                
                # Extract from body
                if issue.get("body"):
                    terms = self._extract_terms(issue["body"])
                    all_terms.update(terms)
            
            # Get top terms
            top_terms = [term for term, count in all_terms.most_common(100)]
            
            logger.info("Issue scan completed",
                       issues_scanned=issues_scanned,
                       terms_found=len(top_terms))
            
            return {
                "issues_scanned": issues_scanned,
                "terms_extracted": len(top_terms),
                "terms": top_terms,
                "since_date": since_date.isoformat()
            }
            
        except httpx.HTTPStatusError as e:
            logger.error("GitHub API request failed",
                        status_code=e.response.status_code,
                        error=str(e))
            return {
                "issues_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": f"API error: {e.response.status_code}"
            }
        except Exception as e:
            logger.error("Issue scanning failed", error=str(e), exc_info=True)
            return {
                "issues_scanned": 0,
                "terms_extracted": 0,
                "terms": [],
                "error": str(e)
            }
    
    async def _fetch_pr_comments(self, pr_number: int) -> List[Dict[str, Any]]:
        """Fetch comments for a specific PR"""
        url = f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/issues/{pr_number}/comments"
        response = await self.client.get(url, params={"per_page": 20})
        response.raise_for_status()
        return response.json()
    
    def _extract_terms(self, text: str) -> List[str]:
        """Extract technical terms from text
        
        Args:
            text: Input text to extract terms from
            
        Returns:
            List of extracted terms
        """
        if not text:
            return []
        
        # Find all potential terms
        matches = self.term_pattern.findall(text)
        
        # Filter terms
        filtered_terms = []
        for term in matches:
            # Skip if too short or too long
            if len(term) < 3 or len(term) > 30:
                continue
            
            # Skip common noise words
            if term.lower() in self.noise_words:
                continue
            
            # Skip pure numbers
            if term.isdigit():
                continue
            
            # Skip single-letter or two-letter common words
            if len(term) <= 2:
                continue
            
            # Keep technical-looking terms
            # (contains mix of case, underscores, or hyphens)
            if any([
                '_' in term,
                '-' in term,
                any(c.isupper() for c in term[1:]),  # camelCase or PascalCase
                term.isupper() and len(term) >= 3,  # Acronyms
            ]):
                filtered_terms.append(term)
            # Also keep longer terms that aren't in noise list
            elif len(term) >= 5:
                filtered_terms.append(term)
        
        return filtered_terms
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
        logger.info("GitHub client closed")


__all__ = ["GitHubClient"]

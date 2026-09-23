"""Tests for GitHub Client

Tests GitHub API integration for scanning PRs and issues.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.github_client import GitHubClient


class TestGitHubClientInit:
    """Tests for GitHub client initialization"""
    
    def test_init_with_params(self):
        """Test initialization with parameters"""
        client = GitHubClient(
            repo_owner="testowner",
            repo_name="testrepo",
            token="testtoken"
        )
        
        assert client.repo_owner == "testowner"
        assert client.repo_name == "testrepo"
        assert client.token == "testtoken"
        assert "Authorization" in client.headers
    
    def test_init_without_token(self):
        """Test initialization without token"""
        client = GitHubClient(repo_owner="owner", repo_name="repo")
        
        # Should work but without auth header
        assert client.repo_owner == "owner"
        assert client.repo_name == "repo"


class TestTermExtraction:
    """Tests for technical term extraction"""
    
    def setup_method(self):
        """Setup test client"""
        self.client = GitHubClient(repo_owner="test", repo_name="test")
    
    def test_extract_technical_terms(self):
        """Test extraction of technical terms from text"""
        text = "Implement FastAPI endpoint using Python and PostgreSQL"
        terms = self.client._extract_terms(text)
        
        # Should extract technical-looking terms
        assert any("FastAPI" in term for term in terms)
        assert any("Python" in term for term in terms)
        assert any("PostgreSQL" in term for term in terms)
    
    def test_filter_noise_words(self):
        """Test that common noise words are filtered"""
        text = "the and for with from this that have"
        terms = self.client._extract_terms(text)
        
        # Should filter out all noise words
        assert len(terms) == 0
    
    def test_extract_camelcase(self):
        """Test extraction of camelCase identifiers"""
        text = "Use fetchData and processResults functions"
        terms = self.client._extract_terms(text)
        
        # Should extract camelCase terms
        assert any("fetchData" in term for term in terms)
        assert any("processResults" in term for term in terms)
    
    def test_extract_snake_case(self):
        """Test extraction of snake_case identifiers"""
        text = "Call the get_user_data and process_response functions"
        terms = self.client._extract_terms(text)
        
        # Should extract snake_case terms
        assert any("get_user_data" in term for term in terms)
        assert any("process_response" in term for term in terms)
    
    def test_extract_acronyms(self):
        """Test extraction of acronyms"""
        text = "Configure API with JSON and HTTP headers"
        terms = self.client._extract_terms(text)
        
        # Should extract uppercase acronyms
        assert any("API" in term for term in terms)
        assert any("JSON" in term for term in terms)
        assert any("HTTP" in term for term in terms)


@pytest.mark.asyncio
class TestHealthCheck:
    """Tests for health check functionality"""
    
    async def test_health_check_success(self):
        """Test successful health check"""
        client = GitHubClient(repo_owner="test", repo_name="test")
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            result = await client.health_check()
            assert result is True
    
    async def test_health_check_failure(self):
        """Test health check failure"""
        client = GitHubClient(repo_owner="test", repo_name="test")
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("Connection failed")
            
            result = await client.health_check()
            assert result is False


@pytest.mark.asyncio
class TestScanPullRequests:
    """Tests for PR scanning functionality"""
    
    async def test_scan_prs_no_config(self):
        """Test PR scanning without repository configuration"""
        client = GitHubClient()  # No repo configured
        
        result = await client.scan_pull_requests()
        
        assert result["prs_scanned"] == 0
        assert "error" in result
    
    async def test_scan_prs_success(self):
        """Test successful PR scanning"""
        client = GitHubClient(repo_owner="test", repo_name="test", token="token")
        
        mock_prs = [
            {
                "number": 1,
                "title": "Add FastAPI endpoint",
                "body": "Implement new API using Python",
                "updated_at": "2024-01-15T00:00:00Z"
            },
            {
                "number": 2,
                "title": "Fix PostgreSQL connection",
                "body": "Update database configuration",
                "updated_at": "2024-01-14T00:00:00Z"
            }
        ]
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_prs
            mock_get.return_value = mock_response
            
            result = await client.scan_pull_requests(max_prs=10, since_days=90)
            
            assert result["prs_scanned"] >= 0
            assert "terms" in result
            assert isinstance(result["terms"], list)


@pytest.mark.asyncio
class TestScanIssues:
    """Tests for issue scanning functionality"""
    
    async def test_scan_issues_no_config(self):
        """Test issue scanning without repository configuration"""
        client = GitHubClient()  # No repo configured
        
        result = await client.scan_issues()
        
        assert result["issues_scanned"] == 0
        assert "error" in result
    
    async def test_scan_issues_success(self):
        """Test successful issue scanning"""
        client = GitHubClient(repo_owner="test", repo_name="test", token="token")
        
        mock_issues = [
            {
                "number": 1,
                "title": "Bug in API endpoint",
                "body": "The FastAPI endpoint returns 500",
                "updated_at": "2024-01-15T00:00:00Z"
            },
            {
                "number": 2,
                "title": "Add WebSocket support",
                "body": "Implement real-time updates",
                "updated_at": "2024-01-14T00:00:00Z"
            }
        ]
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_issues
            mock_get.return_value = mock_response
            
            result = await client.scan_issues(max_issues=10, since_days=90)
            
            assert result["issues_scanned"] >= 0
            assert "terms" in result
            assert isinstance(result["terms"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

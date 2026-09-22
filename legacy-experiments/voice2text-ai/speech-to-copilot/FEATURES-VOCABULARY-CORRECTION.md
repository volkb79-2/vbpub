# Repository Scanner and Vocabulary Correction Features

This document describes the enhanced repository scanning, vocabulary correction, and self-correction detection features added to the speech-to-copilot API.

## Overview

The speech-to-copilot API now includes three major enhancements:

1. **GitHub Repository Scanning**: Extracts technical terms from pull requests and issues
2. **Vocabulary Correction**: Post-processes transcriptions to fix common speech recognition errors
3. **Self-Correction Detection**: Identifies when users correct themselves during speech

## Features

### 1. GitHub Repository Scanning

The `GitHubClient` service integrates with GitHub's API to extract technical terminology from your repository's pull requests and issues.

#### Configuration

Set the following environment variables:

```bash
# Required for GitHub scanning
GITHUB_REPO_OWNER=your-username
GITHUB_REPO_NAME=your-repo
GITHUB_TOKEN=ghp_your_token_here  # Optional but recommended for rate limits
```

#### Usage

The GitHub scanner is automatically integrated into the repository scanner:

```python
from services.repository_scanner import RepositoryScanner

# Initialize with GitHub enabled (default)
scanner = RepositoryScanner(root_path=".", github_enabled=True)

# Scan GitHub PRs and issues
result = await scanner.scan_github_prs_and_issues(
    max_prs=50,
    max_issues=50,
    since_days=90
)

# Get comprehensive context (filesystem + GitHub)
terms = await scanner.get_comprehensive_context(
    file_types=['python', 'typescript'],
    include_github=True,
    max_terms=50
)
```

#### What Gets Scanned

- **Pull Request Titles**: Technical terms from PR titles
- **Pull Request Descriptions**: Technical terminology in PR bodies
- **Pull Request Comments**: Terms from the first 10 most recent PRs
- **Issue Titles**: Technical terms from issue titles  
- **Issue Descriptions**: Technical terminology in issue bodies

#### Term Extraction

The scanner identifies technical terms by looking for:

- camelCase identifiers (e.g., `fetchData`, `processResults`)
- snake_case identifiers (e.g., `get_user_data`, `process_response`)
- Acronyms (e.g., `API`, `JSON`, `HTTP`)
- PascalCase names (e.g., `FastAPI`, `PostgreSQL`)
- Terms with hyphens or underscores

Common English words are filtered out automatically.

### 2. Vocabulary Correction

The `VocabularyCorrector` service post-processes transcriptions to fix common speech-to-text errors with technical terminology.

#### How It Works

1. **Common Corrections**: Applies predefined corrections for frequently misrecognized terms
2. **Context-Based Corrections**: Uses repository context to fix similar-sounding technical terms
3. **Automatic Integration**: Runs automatically after transcription when context is enabled

#### Common Corrections

Built-in corrections for frequently misheard technical terms:

| Spoken | Corrected |
|--------|-----------|
| "jay son" | "JSON" |
| "pie thon" | "Python" |
| "fast A P I" | "FastAPI" |
| "web socket" | "WebSocket" |
| "git hub" | "GitHub" |
| "node jay ess" | "Node.js" |
| "post gres" | "Postgres" |
| "A W S" | "AWS" |

#### Context-Based Corrections

When repository context is enabled, the corrector:

1. Compares transcribed words against repository terms
2. Uses fuzzy matching to find similar terms (70%+ similarity)
3. Suggests corrections based on repository vocabulary
4. Preserves capitalization from the repository term

Example:
```
Transcribed: "I'm using fastappi with postgrez"
Context Terms: ["FastAPI", "PostgreSQL", "uvicorn"]
Corrected: "I'm using FastAPI with PostgreSQL"
```

#### API Usage

Vocabulary correction is automatically enabled in the Whisper client:

```python
from services.whisper_client import WhisperClient

# Initialize with vocabulary correction (default)
client = WhisperClient(enable_vocabulary_correction=True)

# Transcribe with context terms for better corrections
result = await client.transcribe(
    audio_data=audio_base64,
    format="wav",
    language="en",
    context_terms=["FastAPI", "PostgreSQL", "Redis"]
)

# Check if corrections were applied
if result.vocabulary_corrected:
    print(f"Original: {result.correction_metadata['corrections']}")
```

#### Transcription Response Fields

The enhanced `TranscriptionResult` includes:

- `vocabulary_corrected` (bool): Whether vocabulary corrections were applied
- `self_correction_detected` (bool): Whether self-correction was detected
- `correction_metadata` (dict): Details about corrections made

### 3. Self-Correction Detection

The vocabulary corrector can detect when users correct themselves during speech and extract the intended final text.

#### Detected Patterns

The system recognizes these self-correction patterns:

- **"no I mean X"**: "Set it to 5, no I mean 10"
- **"actually X"**: "Use Python 3.8, actually 3.11"
- **"wait X"**: "Import requests, wait I mean httpx"
- **"correction: X"**: "Timeout is 30, correction: 60"
- **Multiple markers**: "Wait, no, actually change that to..."

#### How It Works

1. **Pattern Detection**: Scans for self-correction markers in the transcribed text
2. **Intent Extraction**: Extracts the corrected portion after the marker
3. **Confidence Scoring**: Assigns confidence based on pattern strength
4. **Text Replacement**: Returns the corrected text as the final result

#### Example

```python
from services.vocabulary_corrector import VocabularyCorrector

corrector = VocabularyCorrector()

# Detect self-correction
text = "Set the timeout to 30, no I mean 60 seconds"
has_correction, info = corrector.detect_self_corrections(text)

if has_correction:
    # Extract the corrected intent
    final_text = corrector.extract_corrected_intent(
        text, has_correction, info
    )
    print(f"Corrected: {final_text}")
    # Output: "60 seconds"
```

#### Confidence Levels

- **0.8-1.0**: Strong pattern match (e.g., "no I mean", "actually")
- **0.5-0.7**: Multiple correction markers present
- **0.0**: No self-correction detected

## API Integration

### Transcription Endpoint

The `/api/transcribe` endpoint automatically uses these features when context is enabled:

```bash
curl -X POST "https://localhost:8443/api/transcribe" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_data": "base64_encoded_audio",
    "format": "wav",
    "language": "en",
    "enable_context": true,
    "context_languages": ["python", "typescript"]
  }'
```

Response includes correction metadata:

```json
{
  "text": "Create a FastAPI endpoint",
  "original_text": "Create a fast A P I endpoint",
  "confidence": 0.95,
  "vocabulary_corrected": true,
  "self_correction_detected": false,
  "correction_metadata": {
    "corrections_made": 1,
    "corrections": [
      {
        "original": "fast A P I",
        "corrected": "FastAPI",
        "count": 1
      }
    ]
  }
}
```

### Repository Scan Endpoint

Manually trigger a repository scan with GitHub integration:

```bash
curl -X GET "https://localhost:8443/api/repository/scan?languages=python,typescript" \
  -H "Authorization: Bearer your_token"
```

Response:

```json
{
  "status": "completed",
  "scan_result": {
    "files_scanned": 245,
    "prs_scanned": 23,
    "issues_scanned": 15,
    "terms_extracted": 150,
    "terms": ["FastAPI", "WebSocket", "PostgreSQL", "..."]
  }
}
```

## Configuration

### Environment Variables

```bash
# GitHub Integration (optional)
GITHUB_REPO_OWNER=your-org
GITHUB_REPO_NAME=your-repo
GITHUB_TOKEN=ghp_token_here

# Workspace path for filesystem scanning
WORKSPACE_PATH=/path/to/your/code

# Enable/disable vocabulary correction
ENABLE_VOCABULARY_CORRECTION=true
```

### Disabling Features

To disable GitHub scanning:

```python
scanner = RepositoryScanner(root_path=".", github_enabled=False)
```

To disable vocabulary correction:

```python
client = WhisperClient(enable_vocabulary_correction=False)
```

## Performance Considerations

### GitHub API Rate Limits

- **Authenticated**: 5,000 requests/hour
- **Unauthenticated**: 60 requests/hour

The scanner caches results for 1 hour to minimize API calls.

### Caching

- Repository scan results are cached for 1 hour
- GitHub scan results are cached separately
- Force refresh with `force_refresh=True`

### Resource Usage

- GitHub scanning: ~2-5 API requests per scan (depending on PR comment fetching)
- Vocabulary correction: Minimal overhead (~1-2ms per transcription)
- Self-correction detection: ~0.5ms per transcription

## Testing

### Running Tests

```bash
# All unit tests
pytest tests/ -v --ignore=tests/test_integration.py

# Vocabulary correction tests only
pytest tests/test_vocabulary_corrector.py -v

# GitHub client tests only
pytest tests/test_github_client.py -v
```

### Test Coverage

- **Vocabulary Correction**: 14 tests covering common corrections, context-based corrections, and self-correction detection
- **GitHub Client**: 13 tests covering API integration, term extraction, and error handling
- **Integration**: Tests verify end-to-end functionality

## Troubleshooting

### GitHub Scanning Not Working

1. **Check credentials**:
   ```bash
   echo $GITHUB_TOKEN
   echo $GITHUB_REPO_OWNER
   echo $GITHUB_REPO_NAME
   ```

2. **Test GitHub API access**:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/repos/$GITHUB_REPO_OWNER/$GITHUB_REPO_NAME
   ```

3. **Check logs**:
   ```bash
   docker logs speech-to-copilot-api | grep -i github
   ```

### Vocabulary Correction Not Applied

1. **Verify context is enabled**:
   - Set `enable_context: true` in the transcription request
   - Provide `context_languages` array

2. **Check correction metadata**:
   - Review `correction_metadata` in the response
   - Look for `corrections_made` count

3. **Review logs**:
   ```bash
   docker logs speech-to-copilot-api | grep -i vocabulary
   ```

### Self-Correction Not Detected

1. **Use clear markers**: Make sure speech includes clear correction markers like "no I mean", "actually", etc.

2. **Check confidence**: Low confidence may indicate ambiguous correction patterns

3. **Review patterns**: Check if your correction phrase matches supported patterns in `vocabulary_corrector.py`

## Future Enhancements

Potential improvements for future versions:

1. **Machine Learning**: Train custom models on repository-specific vocabulary
2. **User Feedback Loop**: Learn from user corrections over time
3. **Multi-language Support**: Extend corrections to non-English technical terms
4. **Phonetic Matching**: Use phonetic algorithms for better similarity matching
5. **Git History Analysis**: Extract terms from commit messages and code diffs
6. **Custom Dictionaries**: Allow users to define custom correction rules
7. **Real-time Updates**: Stream corrections as they're applied during transcription

## Support

For issues or questions:

1. Check the logs: `docker logs speech-to-copilot-api`
2. Review test files for usage examples
3. Create an issue in the repository with:
   - Sample transcription input/output
   - Correction metadata from the response
   - Relevant log excerpts

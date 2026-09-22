# Whisper Customization: Project-Specific Vocabulary and IT Domain Optimization

## Overview

Yes, you **can** feed project-specific words and IT domain information to Whisper to improve voice recognition accuracy! This document explains multiple approaches to customize Whisper for better recognition of technical terms, abbreviations, and domain-specific language.

## Recognition Challenges in Technical Dictation

Common issues when dictating technical content:
- **Technical Terms**: "PostgreSQL", "Kubernetes", "FastAPI" may be transcribed incorrectly
- **Abbreviations**: "API", "HTTP", "TLS", "DNS" might be misheard
- **Variable Names**: `userID`, `get_data`, `async_func` could be garbled
- **Mixed Languages**: Code comments with German/English mix
- **Project Names**: Custom project or company names

## Approach 1: Post-Processing with Custom Vocabulary (Recommended)

### How It Works

1. **Whisper transcribes** audio normally
2. **Repository scanner** extracts technical terms from your codebase
3. **Post-processor** corrects misrecognized terms using fuzzy matching
4. **LLM enhancement** (optional) further refines the text

### Implementation

#### Step 1: Build Project Vocabulary

The `repository_scanner.py` service automatically:
```python
# Scan your project directory
scanner = RepositoryScanner(workspace_path="/path/to/project")
vocabulary = scanner.extract_technical_terms()

# Returns:
{
  "functions": ["calculate_sum", "fetch_data", "async_handler"],
  "classes": ["UserModel", "DatabaseConnection", "APIClient"],
  "constants": ["MAX_RETRIES", "API_ENDPOINT", "DEFAULT_TIMEOUT"],
  "imports": ["FastAPI", "PostgreSQL", "Redis"]
}
```

#### Step 2: Configure Vocabulary Correction

Edit `speech-to-copilot/compose.config.sample.toml`:
```toml
[api]
enable_vocabulary_correction = true
vocabulary_source = "repository"  # or "file", "hybrid"
vocabulary_file_path = "/workspace/custom-vocab.txt"
fuzzy_match_threshold = 0.8  # Similarity threshold (0.0-1.0)
```

#### Step 3: Custom Vocabulary File (Optional)

Create `custom-vocab.txt` with terms Whisper often misses:
```
FastAPI
PostgreSQL
Kubernetes
kubectl
async
await
SQLAlchemy
Pydantic
docker-compose
```

#### Step 4: Phonetic Matching

For better correction, enable phonetic matching:
```toml
[api]
enable_phonetic_matching = true
phonetic_algorithm = "metaphone"  # or "soundex", "nysiis"
```

This helps correct:
- "fast API" → "FastAPI"
- "post gress" → "PostgreSQL"  
- "cube netties" → "Kubernetes"

### Example Workflow

**Input Audio**: *"create a fast API endpoint to connect to post gress"*

**Whisper Raw Output**: "create a fast API endpoint to connect to postgress"

**After Vocabulary Correction**: "create a FastAPI endpoint to connect to PostgreSQL"

**After LLM Enhancement**: 
```python
# Create a FastAPI endpoint to connect to PostgreSQL
@app.post("/api/data")
async def get_data(db: Database):
    # Implementation here
    pass
```

## Approach 2: Whisper Initial Prompt (Context Injection)

### How It Works

Whisper supports an "initial prompt" parameter that provides context to guide transcription. This is like priming the model with expected vocabulary.

### Configuration

Edit `whisper-trans/compose.config.sample.toml`:
```toml
[whisper]
initial_prompt = "Technical terms: FastAPI, PostgreSQL, Kubernetes, Docker, Python, async, await, SQLAlchemy, Pydantic, Redis, Celery"
```

### Programmatic Usage

When calling Whisper API:
```python
import requests

response = requests.post("http://whisper-service:9000/asr", 
    files={"audio_file": open("audio.wav", "rb")},
    data={
        "initial_prompt": "Technical project discussion about FastAPI, PostgreSQL, and Docker"
    }
)
```

### Limitations

- Initial prompt is limited to ~224 tokens (~150 words)
- Works best for frequently used terms
- Less effective for very specialized vocabulary
- Doesn't handle misheard terms, only guides recognition

## Approach 3: Fine-Tuned Whisper Model (Advanced)

### When to Consider

Fine-tuning is worthwhile if:
- You have 10+ hours of labeled technical audio
- Standard Whisper consistently fails on your domain
- You need maximum accuracy for mission-critical use
- You're willing to invest in model training

### Process Overview

1. **Collect Audio Dataset**
   - Record 10-20 hours of technical dictation
   - Include various speakers and accents
   - Cover your domain vocabulary extensively

2. **Create Transcriptions**
   - Manually transcribe or correct Whisper output
   - Ensure technical terms are spelled correctly
   - Include code snippets and special formatting

3. **Fine-Tune Model**
   ```bash
   # Using OpenAI Whisper fine-tuning tools
   python fine_tune_whisper.py \
       --model base \
       --dataset ./technical_audio_dataset \
       --output ./models/whisper-technical \
       --epochs 10
   ```

4. **Deploy Custom Model**
   Edit `whisper-trans/compose.config.sample.toml`:
   ```toml
   [whisper]
   asr_model = "custom"
   custom_model_path = "/data/whisper/whisper-technical"
   ```

### Considerations

- **Resource Intensive**: Requires GPU for training (8+ hours)
- **Maintenance**: Need to retrain as vocabulary evolves
- **Storage**: Custom models are 1-3 GB
- **Complexity**: Requires ML expertise

## Approach 4: Hybrid Strategy (Best Results)

Combine multiple approaches for optimal results:

```
Audio Input
    ↓
Whisper with Initial Prompt (guides recognition)
    ↓
Raw Transcription
    ↓
Vocabulary Correction (fixes misheard terms)
    ↓
Phonetic Matching (handles sound-alikes)
    ↓
LLM Enhancement (grammar, formatting, context)
    ↓
Final Output
```

### Configuration Example

```toml
[whisper]
# Guide Whisper during transcription
initial_prompt = "Technical terms: FastAPI, PostgreSQL, Docker, Kubernetes, Python"

[api]
# Correct transcription mistakes
enable_vocabulary_correction = true
vocabulary_source = "hybrid"  # repository + file
vocabulary_file_path = "/workspace/tech-vocab.txt"

# Phonetic matching for sound-alikes
enable_phonetic_matching = true
phonetic_algorithm = "metaphone"

# LLM enhancement for final polish
enable_enhancement = true
llm_service_url = "http://openai-shim:8300"
enhancement_prompt = "code_enhancement"  # Uses code-focused prompts
```

## Approach 5: Real-Time Vocabulary Updates

### Dynamic Learning

Implement a feedback loop where corrected terms are automatically added to vocabulary:

```python
# User corrects "post gress" to "PostgreSQL"
correction = {
    "original": "post gress",
    "corrected": "PostgreSQL",
    "confidence": 0.95
}

# System learns and adds to vocabulary
vocabulary_manager.add_term("PostgreSQL", 
    phonetic="PGRSQL",
    frequency=1)
```

### Configuration

```toml
[api]
enable_learning = true
learning_threshold = 0.9  # Only learn high-confidence corrections
max_vocabulary_size = 10000
vocabulary_persistence = "/workspace/.vocabulary_cache.json"
```

## IT Domain Optimization Techniques

### 1. Acronym Dictionary

Create `acronyms.txt`:
```
API = Application Programming Interface
HTTP = HyperText Transfer Protocol
TLS = Transport Layer Security
DNS = Domain Name System
ORM = Object-Relational Mapping
```

Configure:
```toml
[api]
acronym_expansion = true
acronym_dictionary_path = "/workspace/acronyms.txt"
```

### 2. Code Pattern Recognition

Detect and preserve code patterns:
```python
# Detect variable naming patterns
patterns = [
    r"[a-z]+_[a-z]+",  # snake_case
    r"[A-Z][a-z]+[A-Z]",  # PascalCase
    r"[a-z]+[A-Z]",  # camelCase
]
```

### 3. Language-Specific Vocabulary

Load domain-specific terms:
```python
# Python domain
python_terms = ["async", "await", "yield", "lambda", "decorator"]

# Web domain
web_terms = ["HTTP", "REST", "GraphQL", "WebSocket", "CORS"]

# Database domain
db_terms = ["PostgreSQL", "MongoDB", "Redis", "migration", "ORM"]
```

## Error Correction Self-Adjustment

### Self-Correction Pattern

**User speaks**: *"I misspoke, I meant PostgreSQL not MySQL"*

The system should:
1. Detect correction phrase: "I misspoke", "I meant", "correction"
2. Extract corrected term: "PostgreSQL"
3. Replace previous incorrect term: "MySQL" → "PostgreSQL"
4. Update vocabulary with correction pattern

### Implementation

```python
def detect_self_correction(text, previous_text):
    correction_phrases = ["I misspoke", "I meant", "correction", "actually"]
    
    if any(phrase in text.lower() for phrase in correction_phrases):
        # Extract new term
        new_term = extract_correction_target(text)
        # Find what to replace in previous text
        old_term = find_similar_term(previous_text, new_term)
        # Update
        corrected_text = previous_text.replace(old_term, new_term)
        return corrected_text
    
    return text
```

### Configuration

```toml
[api]
enable_self_correction = true
correction_phrases = ["I misspoke", "I meant", "correction", "actually"]
correction_window = 30  # seconds to look back
```

### Streaming Considerations

When using streaming transcription with self-correction:

1. **Buffer Recent History**: Keep last 30 seconds of text in memory
2. **Pattern Matching**: Detect correction phrases in real-time
3. **Retroactive Update**: Replace terms in the buffer
4. **Client Notification**: Send diff update to client

```python
# WebSocket message for correction
{
    "type": "correction",
    "action": "replace",
    "old_text": "connect to MySQL database",
    "new_text": "connect to PostgreSQL database",
    "timestamp": 125.3
}
```

## Testing Vocabulary Improvements

### Benchmark Your Setup

1. **Create Test Audio**
   ```bash
   # Record yourself saying technical terms
   ffmpeg -f alsa -i default -t 60 test_technical.wav
   ```

2. **Test Without Customization**
   ```bash
   curl -X POST http://localhost:9000/asr \
       -F "audio_file=@test_technical.wav" \
       > baseline.txt
   ```

3. **Test With Customization**
   ```bash
   curl -X POST http://localhost:9000/asr \
       -F "audio_file=@test_technical.wav" \
       -F "initial_prompt=FastAPI PostgreSQL Kubernetes" \
       > improved.txt
   ```

4. **Compare Results**
   ```bash
   diff baseline.txt improved.txt
   # Count corrections
   python3 -c "
   import difflib
   baseline = open('baseline.txt').read()
   improved = open('improved.txt').read()
   ratio = difflib.SequenceMatcher(None, baseline, improved).ratio()
   print(f'Similarity: {ratio:.2%}')
   "
   ```

### Accuracy Metrics

Track improvement over time:
- **Word Error Rate (WER)**: Measure transcription accuracy
- **Technical Term Accuracy**: Specifically track IT terms
- **Correction Frequency**: How often manual corrections needed
- **User Satisfaction**: Subjective feedback from users

## Recommended Configuration

For best results with IT domain:

```toml
[whisper]
asr_model = "medium"  # Better than 'base' for technical terms
initial_prompt = "Technical discussion about software development, including terms like FastAPI, PostgreSQL, Docker, Kubernetes, Python, async, await, API, HTTP, REST, database, container"

[api]
enable_vocabulary_correction = true
vocabulary_source = "hybrid"
vocabulary_file_path = "/workspace/tech-vocab.txt"
enable_phonetic_matching = true
phonetic_algorithm = "metaphone"
enable_self_correction = true
correction_window = 30
enable_learning = true
learning_threshold = 0.9
fuzzy_match_threshold = 0.85
```

## Summary

**Yes, you can significantly improve Whisper's recognition of technical terms through:**

1. ✅ **Post-Processing Vocabulary Correction** (Easiest, Recommended)
2. ✅ **Initial Prompt Context Injection** (Simple, Limited)
3. ✅ **Repository Scanning for Project Terms** (Automatic, Accurate)
4. ✅ **Phonetic Matching** (Handles Sound-Alikes)
5. ✅ **Self-Correction Detection** (User-Friendly)
6. ⚠️ **Fine-Tuned Models** (Advanced, High Accuracy, Complex)

**Best Approach**: Use a **hybrid strategy** combining initial prompts, vocabulary correction, and phonetic matching. This provides excellent results without the complexity of fine-tuning.

The self-correction pattern naturally handles misspeaking by detecting correction phrases and updating previous text accordingly, both in batch and streaming modes.

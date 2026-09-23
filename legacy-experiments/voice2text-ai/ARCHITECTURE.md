# Voice2Text-AI Architecture

This document provides comprehensive architectural diagrams for the Voice2Text-AI system.

## Table of Contents

- [System Architecture](#system-architecture)
- [Network and Communication](#network-and-communication)
- [Component Communication](#component-communication)
- [Data Flow - User Voice Processing](#data-flow---user-voice-processing)
- [Container and Volume Persistence](#container-and-volume-persistence)

---

## System Architecture

```mermaid
graph TB
    subgraph External["External Access"]
        User[User Browser/Client]
        CA[Certificate Authority<br/>ca-cert.pem]
    end
    
    subgraph ProxyStack["Reverse Proxy Stack<br/>(HTTPS/TLS Termination)"]
        ReverseProxy[Nginx Reverse Proxy<br/>Port: 8443 HTTPS<br/>Port: 8080 HTTP]
        ProxyCerts[(TLS Certificates<br/>Volume: proxy-certs<br/>- fullchain.pem<br/>- privkey.pem<br/>- ca-cert.pem<br/>- ca-key.pem)]
        ProxyLogs[(Nginx Logs<br/>./vol-reverse-proxy-logs)]
    end
    
    subgraph APIStack["Speech-to-Copilot Stack<br/>(API Orchestration)"]
        API[FastAPI Service<br/>Port: 8000<br/>speech-to-copilot-api]
        Redis[Redis Cache<br/>Port: 6379<br/>speech-to-copilot-redis]
        APILogs[(API Logs<br/>./vol-speech-api-logs)]
        RedisData[(Redis Data<br/>./vol-speech-redis-data)]
        Workspace[(Workspace<br/>Repository Scanner)]
    end
    
    subgraph WhisperStack["Whisper Transcription Stack"]
        Whisper[Whisper ASR Service<br/>Port: 9000<br/>whisper-service<br/>faster-whisper engine]
        WhisperCache[(Model Cache<br/>./vol-whisper-cache<br/>Whisper models)]
        WhisperTemp[(Audio Temp<br/>./vol-whisper-audio-temp<br/>Processing files)]
        TestWav[test.wav<br/>Test Data]
    end
    
    subgraph LLMStack["LLM Enhancement Stack<br/>(Optional)"]
        LLMWebUI[Oobabooga WebUI<br/>Port: 5000<br/>llm-webui<br/>Text-Generation]
        ModelLoader[Model Loader<br/>model-loader<br/>One-time init]
        OpenAIShim[OpenAI Shim<br/>Port: 8300<br/>openai-shim<br/>API Translation]
        LLMModels[(GGUF Models<br/>./vol-oobabooga-models<br/>TinyLlama, Phi-2, etc.)]
        LLMCache[(Model Cache<br/>./vol-oobabooga-cache)]
        SystemPrompt[system-prompt.txt<br/>LLM Instructions]
    end
    
    subgraph Network["Docker Network: voice2text-network<br/>(Internal HTTP Communication)"]
    end
    
    User -->|HTTPS: 8443| ReverseProxy
    CA -.->|Install for trust| User
    
    ReverseProxy -->|Mount| ProxyCerts
    ReverseProxy -->|Mount| ProxyLogs
    ReverseProxy -->|/api/*| API
    ReverseProxy -->|/whisper/*| Whisper
    ReverseProxy -->|/llm/*| OpenAIShim
    
    API -->|HTTP: 9000| Whisper
    API -->|HTTP: 8300| OpenAIShim
    API -->|Cache| Redis
    API -->|Mount| APILogs
    API -->|Mount RO| Workspace
    
    Redis -->|Mount| RedisData
    
    Whisper -->|Mount| WhisperCache
    Whisper -->|Mount| WhisperTemp
    Whisper -->|Mount RO| TestWav
    
    LLMWebUI -->|Mount RO| LLMModels
    LLMWebUI -->|Mount| LLMCache
    LLMWebUI -->|Mount RO| SystemPrompt
    LLMWebUI -->|HTTP: 5000| OpenAIShim
    
    ModelLoader -->|Init once| LLMWebUI
    
    OpenAIShim -->|Mount RO| SystemPrompt
    
    Network -.->|Contains| API
    Network -.->|Contains| Redis
    Network -.->|Contains| Whisper
    Network -.->|Contains| LLMWebUI
    Network -.->|Contains| ModelLoader
    Network -.->|Contains| OpenAIShim
    Network -.->|Contains| ReverseProxy

    style User fill:#e1f5ff
    style CA fill:#fff4e1
    style ReverseProxy fill:#ffe1e1
    style API fill:#e1ffe1
    style Whisper fill:#e1ffe1
    style OpenAIShim fill:#e1ffe1
    style Redis fill:#ffe1f5
    style LLMWebUI fill:#f5e1ff
    style ProxyCerts fill:#fff9e1
    style ProxyLogs fill:#fff9e1
    style APILogs fill:#fff9e1
    style RedisData fill:#fff9e1
    style WhisperCache fill:#fff9e1
    style WhisperTemp fill:#fff9e1
    style LLMModels fill:#fff9e1
    style LLMCache fill:#fff9e1
```

---

## Network and Communication

```mermaid
graph LR
    subgraph Internet["Internet"]
        Client[Client<br/>Browser/App]
    end
    
    subgraph Host["Host Machine<br/>Public IP: auto-detected<br/>FQDN: auto-detected"]
        subgraph DockerNetwork["Docker Network: voice2text-network<br/>Bridge Network - Internal HTTP"]
            Proxy[reverse-proxy<br/>HTTPS: 8443<br/>HTTP: 8080]
            API[speech-to-copilot-api<br/>HTTP: 8000]
            Redis[speech-to-copilot-redis<br/>:6379]
            Whisper[whisper-service<br/>HTTP: 9000]
            LLM[llm-webui<br/>HTTP: 5000]
            Shim[openai-shim<br/>HTTP: 8300]
        end
    end
    
    Client -->|"HTTPS: 8443<br/>(TLS/SSL)"| Proxy
    Proxy -->|"HTTP: 8000<br/>/api/*"| API
    Proxy -->|"HTTP: 9000<br/>/whisper/*"| Whisper
    Proxy -->|"HTTP: 8300<br/>/llm/*"| Shim
    API -->|"HTTP: 9000"| Whisper
    API -->|"HTTP: 8300"| Shim
    API -->|"Redis: 6379"| Redis
    Shim -->|"HTTP: 5000"| LLM
    
    style Client fill:#e1f5ff
    style Proxy fill:#ffe1e1
    style API fill:#e1ffe1
    style Whisper fill:#e1ffe1
    style Shim fill:#e1ffe1
    style Redis fill:#ffe1f5
    style LLM fill:#f5e1ff
```

### Port Mapping

| Service | Internal Port | External Port | Protocol | Purpose |
|---------|--------------|---------------|----------|---------|
| reverse-proxy | 8443 | 8443 | HTTPS | External secure access |
| reverse-proxy | 8080 | 8080 | HTTP | HTTP redirect to HTTPS |
| speech-to-copilot-api | 8000 | - | HTTP | Internal API (via proxy) |
| whisper-service | 9000 | - | HTTP | Internal transcription API |
| openai-shim | 8300 | - | HTTP | Internal LLM API translation |
| llm-webui | 5000 | - | HTTP | Internal LLM service |
| speech-to-copilot-redis | 6379 | - | Redis | Internal cache |

---

## Component Communication

```mermaid
sequenceDiagram
    participant Client
    participant ReverseProxy as Reverse Proxy<br/>(TLS Termination)
    participant API as Speech-to-Copilot API
    participant Redis
    participant Whisper as Whisper Service
    participant Shim as OpenAI Shim
    participant LLM as LLM WebUI
    
    Note over Client,LLM: All external traffic goes through HTTPS (8443)
    Note over ReverseProxy,LLM: Internal communication uses HTTP on voice2text-network
    
    Client->>ReverseProxy: HTTPS Request<br/>(Port 8443)
    ReverseProxy->>API: HTTP Request<br/>(Port 8000)
    
    API->>Redis: Cache Lookup<br/>(Port 6379)
    Redis-->>API: Cache Response
    
    alt Cache Miss
        API->>Whisper: Transcribe Audio<br/>(Port 9000)
        Whisper-->>API: Transcription Text
        
        opt Enhancement Enabled
            API->>Shim: Enhance Text<br/>(Port 8300)
            Shim->>LLM: OpenAI Format Request<br/>(Port 5000)
            LLM-->>Shim: Generated Response
            Shim-->>API: Enhanced Text
        end
        
        API->>Redis: Store Result
    end
    
    API-->>ReverseProxy: HTTP Response
    ReverseProxy-->>Client: HTTPS Response<br/>(Encrypted)
    
    Note over Client,LLM: TLS encryption only between Client and Reverse Proxy
```

---

## Data Flow - User Voice Processing

This swimlane diagram shows a typical flow when a user sends voice for processing.

```mermaid
sequenceDiagram
    actor User
    participant Browser
    participant Proxy as Reverse Proxy<br/>(Nginx)
    participant API as API Service<br/>(FastAPI)
    participant Redis
    participant Scanner as Repo Scanner
    participant Whisper
    participant Shim as OpenAI Shim
    participant LLM as LLM Service
    
    rect rgb(230, 240, 255)
        Note over User,LLM: Phase 1: Initial Connection & Setup
        User->>Browser: Open https://<your-host>:8443/
        Browser->>Proxy: HTTPS Connect (Port 8443)
        Proxy-->>Browser: Serve Landing Page
        Browser->>Proxy: Request /api/health
        Proxy->>API: Forward to Port 8000
        API->>Redis: Check Connection
        Redis-->>API: OK
        API-->>Proxy: Health Status (demo_mode: false)
        Proxy-->>Browser: Health Response
        Browser-->>User: Show UI Ready
    end
    
    rect rgb(255, 240, 230)
        Note over User,LLM: Phase 2: Voice Recording
        User->>Browser: Click "Start Recording"
        Browser->>Browser: Request Microphone Permission
        Browser-->>User: Permission Prompt
        User->>Browser: Allow Microphone
        Browser->>Browser: Start MediaRecorder
        Note over Browser: Record audio chunks<br/>(WebM/Opus or WAV)
        User->>Browser: Click "Stop Recording"
        Browser->>Browser: Stop & Finalize Audio
    end
    
    rect rgb(240, 255, 240)
        Note over User,LLM: Phase 3: Context Extraction (Optional)
        Browser->>Proxy: POST /api/scan-repository
        Proxy->>API: Forward Request
        API->>Scanner: Extract Tech Terms
        Scanner->>Scanner: Parse Code Files
        Scanner-->>API: Technical Vocabulary List
        API->>Redis: Cache Vocabulary
        API-->>Proxy: Vocabulary Ready
        Proxy-->>Browser: 200 OK
    end
    
    rect rgb(255, 245, 230)
        Note over User,LLM: Phase 4: Transcription
        Browser->>Proxy: POST /api/transcribe<br/>(Audio File)
        Proxy->>API: Forward Audio
        
        API->>Redis: Check Cache<br/>(Audio Hash)
        Redis-->>API: Cache Miss
        
        API->>Whisper: POST /asr<br/>(Audio + Params)
        Whisper->>Whisper: Load Model (if needed)
        Whisper->>Whisper: Process Audio<br/>(Faster-Whisper)
        Whisper->>Whisper: Apply VAD Filtering
        Whisper->>Whisper: Generate Timestamps
        Whisper-->>API: Raw Transcription + Metadata
        
        API->>API: Post-Process:<br/>- Apply Vocabulary<br/>- Fix Technical Terms<br/>- Phonetic Matching
    end
    
    rect rgb(245, 240, 255)
        Note over User,LLM: Phase 5: Enhancement (Optional)
        alt Enhancement Enabled
            API->>Shim: POST /v1/chat/completions<br/>(OpenAI Format)
            Shim->>Shim: Translate Request Format
            Shim->>LLM: POST /v1/completions<br/>(Oobabooga Format)
            LLM->>LLM: Load Model (if needed)
            LLM->>LLM: Generate Response<br/>(Grammar & Punctuation)
            LLM-->>Shim: Enhanced Text
            Shim->>Shim: Translate Response Format
            Shim-->>API: OpenAI Format Response
            
            API->>API: Merge Enhancement<br/>with Transcription
        end
    end
    
    rect rgb(255, 250, 240)
        Note over User,LLM: Phase 6: Response & Caching
        API->>Redis: Store Result<br/>(Hash + TTL)
        API->>API: Format Response:<br/>- Text<br/>- Confidence<br/>- Timestamps<br/>- Metadata
        API-->>Proxy: JSON Response
        Proxy-->>Browser: HTTPS Response
        Browser-->>User: Display Transcription
    end
    
    rect rgb(240, 250, 255)
        Note over User,LLM: Phase 7: Post-Processing (Optional)
        User->>Browser: Review & Edit
        opt User makes corrections
            User->>Browser: "I meant PostgreSQL"
            Browser->>Proxy: POST /api/correction
            Proxy->>API: Correction Data
            API->>API: Update Vocabulary<br/>(Learn Correction)
            API->>Redis: Update Cache
            API-->>Proxy: Corrected Text
            Proxy-->>Browser: Updated Response
            Browser-->>User: Show Corrected Text
        end
        
        opt Insert into Editor
            User->>Browser: "Insert Text"
            Browser->>Browser: Copy to Clipboard<br/>or Use API
            Browser-->>User: Text Inserted
        end
    end
```

### Error Handling Flow

```mermaid
sequenceDiagram
    participant Client
    participant Proxy
    participant API
    participant Service as Whisper/LLM
    
    Client->>Proxy: HTTPS Request
    Proxy->>API: HTTP Request
    
    alt Service Available
        API->>Service: Process Request
        Service-->>API: Success Response
        API-->>Proxy: 200 OK
        Proxy-->>Client: HTTPS Response
    else Service Timeout
        API->>Service: Process Request
        Service--xAPI: Timeout (30s)
        API-->>Proxy: 504 Gateway Timeout
        Proxy-->>Client: Error Response
    else Service Unavailable
        API->>Service: Process Request
        Service--xAPI: Connection Refused
        API-->>Proxy: 503 Service Unavailable
        Proxy-->>Client: Error Response
    else Demo Mode
        Note over API: demo_mode = true
        API->>API: Return Simulated Response
        API-->>Proxy: 200 OK (Demo Data)
        Proxy-->>Client: HTTPS Response
    end
```

---

## Container and Volume Persistence

```mermaid
graph TB
    subgraph HostFilesystem["Host Machine Filesystem"]
        ProxyLogs[./vol-reverse-proxy-logs<br/>Nginx access & error logs]
        APILogs[./vol-speech-api-logs<br/>FastAPI application logs]
        RedisData[./vol-speech-redis-data<br/>Redis persistence (RDB/AOF)]
        WhisperCache[./vol-whisper-cache<br/>Downloaded Whisper models<br/>base, medium, large-v3]
        WhisperTemp[./vol-whisper-audio-temp<br/>Temporary audio files<br/>Processing workspace]
        LLMModels[./vol-oobabooga-models<br/>GGUF/ggml model files<br/>TinyLlama, Phi-2, Mistral]
        LLMCache[./vol-oobabooga-cache<br/>Transformers cache<br/>Tokenizers, configs]
        TestData[./test.wav<br/>Sample audio file<br/>For testing]
        SystemPrompt[./config/system-prompt.txt<br/>LLM system instructions]
        WorkspaceDir[${HOME} or custom path<br/>Code repository<br/>For tech term extraction]
    end
    
    subgraph DockerVolumes["Docker Named Volumes"]
        ProxyCerts[(voice2text-proxy-certs<br/>TLS certificates<br/>- fullchain.pem<br/>- privkey.pem<br/>- ca-cert.pem<br/>- ca-key.pem)]
    end
    
    subgraph Containers["Docker Containers"]
        Proxy[reverse-proxy]
        API[speech-to-copilot-api]
        Redis[speech-to-copilot-redis]
        Whisper[whisper-service]
        LLM[llm-webui]
        Shim[openai-shim]
    end
    
    ProxyCerts -->|Mount| Proxy
    ProxyLogs -->|Bind Mount| Proxy
    
    APILogs -->|Bind Mount| API
    WorkspaceDir -->|Bind Mount (RO)| API
    
    RedisData -->|Bind Mount| Redis
    
    WhisperCache -->|Bind Mount| Whisper
    WhisperTemp -->|Bind Mount| Whisper
    TestData -->|Bind Mount (RO)| Whisper
    
    LLMModels -->|Bind Mount (RO)| LLM
    LLMCache -->|Bind Mount| LLM
    SystemPrompt -->|Bind Mount (RO)| LLM
    
    SystemPrompt -->|Bind Mount (RO)| Shim
    
    style ProxyCerts fill:#fff4e1
    style ProxyLogs fill:#fff9e1
    style APILogs fill:#fff9e1
    style RedisData fill:#fff9e1
    style WhisperCache fill:#fff9e1
    style WhisperTemp fill:#fff9e1
    style LLMModels fill:#fff9e1
    style LLMCache fill:#fff9e1
    style TestData fill:#e1fff4
    style SystemPrompt fill:#e1fff4
    style WorkspaceDir fill:#e1fff4
    style Proxy fill:#ffe1e1
    style API fill:#e1ffe1
    style Redis fill:#ffe1f5
    style Whisper fill:#e1ffe1
    style LLM fill:#f5e1ff
    style Shim fill:#e1ffe1
```

### Volume Descriptions

| Volume Path | Type | Purpose | Persistence | Size |
|------------|------|---------|-------------|------|
| `voice2text-proxy-certs` | Named Volume | TLS certificates (CA + server) | Until manually removed | ~10 KB |
| `./vol-reverse-proxy-logs` | Bind Mount | Nginx access/error logs | Host filesystem | Growing |
| `./vol-speech-api-logs` | Bind Mount | FastAPI application logs | Host filesystem | Growing |
| `./vol-speech-redis-data` | Bind Mount | Redis persistence (RDB snapshots) | Host filesystem | ~10-100 MB |
| `./vol-whisper-cache` | Bind Mount | Whisper model files (downloaded once) | Host filesystem | 1-3 GB per model |
| `./vol-whisper-audio-temp` | Bind Mount | Temporary audio processing files | Host filesystem | ~100 MB (cleared) |
| `./vol-oobabooga-models` | Bind Mount | GGUF/ggml LLM model files | Host filesystem | 2-7 GB per model |
| `./vol-oobabooga-cache` | Bind Mount | Transformers cache (tokenizers, configs) | Host filesystem | ~500 MB |
| `${HOME}` or custom | Bind Mount (RO) | Source code for tech term extraction | Host filesystem | Read-only |
| `./test.wav` | Bind Mount (RO) | Test audio file | Host filesystem | ~140 KB |
| `./config/system-prompt.txt` | Bind Mount (RO) | LLM system instructions | Host filesystem | ~1 KB |

### Volume Creation

All `./vol-*` directories are automatically created by `CIU` with correct UID/GID ownership:

```bash
# Automatic creation via CIU
cd <stack-directory>
python3 ../../scripts/ciu/ciu.py  # Scans TOML for *_HOSTDIR_* variables and creates directories
```

---

## Security Considerations

```mermaid
graph TB
    subgraph External["External (Untrusted)"]
        Internet[Internet Clients]
    end
    
    subgraph TLSBoundary["TLS Termination Boundary"]
        ReverseProxy[Reverse Proxy<br/>TLS/SSL Encryption<br/>Certificate Validation]
    end
    
    subgraph TrustedInternal["Trusted Internal Network<br/>(voice2text-network)"]
        API[API Service<br/>No TLS<br/>HTTP Only]
        Whisper[Whisper Service<br/>No TLS<br/>HTTP Only]
        LLM[LLM Services<br/>No TLS<br/>HTTP Only]
    end
    
    Internet -->|"HTTPS (TLS 1.2/1.3)<br/>Port 8443<br/>Encrypted"| ReverseProxy
    ReverseProxy -->|"HTTP<br/>Port 8000<br/>Unencrypted<br/>(Internal Network Only)"| API
    ReverseProxy -->|"HTTP<br/>Port 9000<br/>Unencrypted"| Whisper
    ReverseProxy -->|"HTTP<br/>Port 8300<br/>Unencrypted"| LLM
    
    API -->|HTTP| Whisper
    API -->|HTTP| LLM
    
    style Internet fill:#ffe1e1
    style ReverseProxy fill:#e1ffe1
    style API fill:#e1f5ff
    style Whisper fill:#e1f5ff
    style LLM fill:#e1f5ff
```

### Security Notes

1. **TLS Encryption**: Only between client and reverse proxy
2. **Internal Network**: HTTP within Docker network (trusted)
3. **Certificate Management**: Auto-generated self-signed CA or Let's Encrypt
4. **No Authentication**: Currently no API keys (add if exposing publicly)
5. **CORS**: Configured in API service for browser access
6. **Volume Permissions**: Bind mounts use host UID/GID for proper access control

---

## Deployment Patterns

### Pattern 1: All-in-One (Default)

All stacks on single host machine.

```mermaid
graph TB
    subgraph SingleHost["Single Host Machine"]
        subgraph DockerEngine["Docker Engine"]
            Proxy[Reverse Proxy]
            API[API Stack]
            Whisper[Whisper Stack]
            LLM[LLM Stack]
        end
    end
    
    Internet[Internet] -->|HTTPS: 8443| Proxy
    Proxy --> API
    Proxy --> Whisper
    Proxy --> LLM
    API --> Whisper
    API --> LLM
```

**Use Case**: Development, testing, small deployments

---

### Pattern 2: Distributed Services

Separate heavy services (Whisper, LLM) to dedicated hosts.

```mermaid
graph TB
    subgraph Internet["Internet"]
        Client[Client]
    end
    
    subgraph Host1["Host 1: API + Proxy"]
        Proxy[Reverse Proxy]
        API[API Service]
    end
    
    subgraph Host2["Host 2: Whisper"]
        Whisper[Whisper Service]
    end
    
    subgraph Host3["Host 3: LLM"]
        LLM[LLM Service]
    end
    
    Client -->|HTTPS| Proxy
    Proxy --> API
    API -->|HTTP| Whisper
    API -->|HTTP| LLM
```

**Use Case**: Production, high load, resource isolation

**Configuration Changes**:
```toml
# speech-to-copilot/compose.config.sample.toml
[api]
whisper_service_url = "http://whisper-host.example.com:9000"
llm_service_url = "http://llm-host.example.com:8300"
```

---

## Technology Stack Summary

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Reverse Proxy | Nginx | Alpine | TLS termination, routing |
| API Service | FastAPI (Python) | 3.11+ | Orchestration, business logic |
| Cache | Redis | 7-alpine | Response caching |
| Transcription | Whisper (faster-whisper) | Latest | Speech-to-text |
| LLM | Oobabooga Text-Gen-WebUI | Latest | Text enhancement |
| LLM Shim | Python FastAPI | 3.11 | OpenAI API translation |
| Container Platform | Docker + Compose | Latest | Container orchestration |
| Configuration | CIU | Latest | TOML-based templating |

---

## Related Documentation

- [README.md](README.md) - Main documentation and quick start
- [DEMO-MODE-EXPLAINED.md](DEMO-MODE-EXPLAINED.md) - Demo mode details
- [WHISPER-CUSTOMIZATION.md](WHISPER-CUSTOMIZATION.md) - Whisper configuration
- [WINDOWS-CLIENT-OPTIONS.md](WINDOWS-CLIENT-OPTIONS.md) - Desktop client options
- [TESTING-GUIDE.md](TESTING-GUIDE.md) - Testing procedures

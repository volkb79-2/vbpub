# Docker Bake configuration for Voice2Text-AI stack
# Usage: docker buildx bake [target]
# Examples:
#   docker buildx bake all --load          # Build all images
#   docker buildx bake llm-webui --load    # Build only LLM service
#   docker buildx bake --no-cache          # Force rebuild without cache

variable "BUILD_VERSION" {
  default = "dev"
}

variable "BUILD_TIME" {
  default = ""
}

variable "REGISTRY" {
  default = ""
}

variable "NAMESPACE" {
  default = "voice2text"
}

# Helper function to generate image tags
function "tags" {
  params = [name]
  result = [
    REGISTRY != "" 
      ? "${REGISTRY}/${NAMESPACE}/${name}:${BUILD_VERSION}"
      : "${NAMESPACE}/${name}:${BUILD_VERSION}"
  ]
}

# LLM WebUI (Oobabooga CPU-only)
target "llm-webui" {
  context = "oobabooga-llm"
  dockerfile = "Dockerfile"
  tags = tags("llm-webui")
  args = {
    BUILD_VERSION = BUILD_VERSION
    BUILD_TIME = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
  labels = {
    "org.opencontainers.image.title" = "Oobabooga LLM CPU"
    "org.opencontainers.image.version" = BUILD_VERSION
    "org.opencontainers.image.created" = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
}

# Speech-to-Copilot API
target "speech-api" {
  context = "speech-to-copilot"
  dockerfile = "Dockerfile"
  tags = tags("speech-api")
  args = {
    BUILD_VERSION = BUILD_VERSION
    BUILD_TIME = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
  labels = {
    "org.opencontainers.image.title" = "Speech-to-Copilot API"
    "org.opencontainers.image.version" = BUILD_VERSION
    "org.opencontainers.image.created" = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
}

# WhisperLive Streaming Service
target "whisperlive" {
  context = "whisper-live"
  dockerfile = "Dockerfile"
  tags = tags("whisperlive")
  args = {
    BUILD_VERSION = BUILD_VERSION
    BUILD_TIME = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
  labels = {
    "org.opencontainers.image.title" = "WhisperLive Streaming"
    "org.opencontainers.image.version" = BUILD_VERSION
    "org.opencontainers.image.created" = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
}

# OpenAI Shim (for Oobabooga)
target "openai-shim" {
  context = "oobabooga-llm/shim"
  dockerfile = "Dockerfile"
  tags = tags("openai-shim")
  args = {
    BUILD_VERSION = BUILD_VERSION
    BUILD_TIME = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
  labels = {
    "org.opencontainers.image.title" = "OpenAI Shim"
    "org.opencontainers.image.version" = BUILD_VERSION
    "org.opencontainers.image.created" = BUILD_TIME != "" ? BUILD_TIME : timestamp()
  }
}

# Build all services
group "all" {
  targets = ["llm-webui", "speech-api", "whisperlive", "openai-shim"]
}

# Default target
group "default" {
  targets = ["all"]
}

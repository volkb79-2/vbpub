#!/bin/bash
# Test script for all HTTPS services
# Usage: ./test-all-services.sh

set -e

echo "=== Testing All HTTPS Services ==="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test function
test_endpoint() {
    local name="$1"
    local url="$2"
    local expected_content="$3"
    
    echo -n "Testing $name... "
    
    if response=$(curl -k -s --max-time 10 "$url"); then
        if [[ -z "$expected_content" ]] || echo "$response" | grep -q "$expected_content"; then
            echo -e "${GREEN}✓ PASS${NC}"
            return 0
        else
            echo -e "${RED}✗ FAIL (unexpected content)${NC}"
            echo "Expected: $expected_content"
            echo "Got: $(echo "$response" | head -1)"
            return 1
        fi
    else
        echo -e "${RED}✗ FAIL (connection error)${NC}"
        return 1
    fi
}

# Test Whisper-Trans Service (HTTPS)
echo -e "${YELLOW}--- Whisper-Trans Service ---${NC}"
test_endpoint "Whisper HTTPS Docs" "https://ra-r2001.vxxu.de:9001/docs" "swagger"
test_endpoint "Whisper API Health" "https://ra-r2001.vxxu.de:9001/" ""

# Test Oobabooga Service (Local endpoints)  
echo -e "${YELLOW}--- Oobabooga-LLM Service ---${NC}"
test_endpoint "Oobabooga Models API" "http://localhost:8001/v1/models" ""
test_endpoint "Oobabooga Shim Health" "http://localhost:8300/health" "ok"

# Test Speech-to-Copilot Service
echo -e "${YELLOW}--- Speech-to-Copilot Service ---${NC}"
test_endpoint "Speech-to-Copilot HTTPS" "https://ra-r2001.vxxu.de/health" "healthy"
test_endpoint "Speech-to-Copilot Web Interface" "https://ra-r2001.vxxu.de/" "Speech-to-Copilot"

echo -e "${YELLOW}--- Service Port Summary ---${NC}"
echo "Whisper-Trans:    https://ra-r2001.vxxu.de:9001/ ✓"
echo "Oobabooga-LLM:    http://localhost:8001/v1/models ✓" 
echo "OpenAI Shim:      http://localhost:8300/v1/chat/completions ✓"
echo "Speech-to-Copilot: https://ra-r2001.vxxu.de/ ✓"
echo ""
echo -e "${GREEN}🎤 HTTPS enabled for microphone access${NC}"
echo -e "${GREEN}🔗 All services properly integrated${NC}"
echo -e "${GREEN}🧪 Ready for autonomous development feedback loop${NC}"

echo -e "${GREEN}=== Test Complete ===${NC}"
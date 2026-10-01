#!/bin/bash

# Whisper Transcription Stack Testing Suite
# This script tests the Docker Compose stack for the Whisper transcription service
# with Traefik reverse proxy.

set -e  # Exit on any error

echo "=== Whisper Transcription Stack Testing Suite ==="
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓ PASS${NC}: $2"
    else
        echo -e "${RED}✗ FAIL${NC}: $2"
    fi
}

# Function to test HTTP endpoint
test_http() {
    local url=$1
    local expected_code=${2:-200}
    local description=$3

    echo -n "Testing $description... "
    if curl -s -o /dev/null -w "%{http_code}" "$url" | grep -q "^$expected_code$"; then
        print_status 0 "$description"
        return 0
    else
        print_status 1 "$description"
        return 1
    fi
}

# Function to test HTTPS endpoint
test_https() {
    local url=$1
    local expected_code=${2:-200}
    local description=$3

    echo -n "Testing $description... "
    if curl -k -s -o /dev/null -w "%{http_code}" "$url" | grep -q "^$expected_code$"; then
        print_status 0 "$description"
        return 0
    else
        print_status 1 "$description"
        return 1
    fi
}

# Function to test transcription
test_transcription() {
    echo -n "Testing transcription API... "
    if [ -f "test.wav" ]; then
        response=$(curl -s -X POST -F "audio_file=@test.wav" http://localhost:9000/asr 2>/dev/null)
        if echo "$response" | grep -q "Hello world"; then
            print_status 0 "transcription API"
            echo "  Response: $response"
            return 0
        else
            print_status 1 "transcription API"
            echo "  Unexpected response: $response"
            return 1
        fi
    else
        print_status 1 "transcription API (test.wav not found)"
        return 1
    fi
}

echo "1. Checking service status..."
docker compose ps
echo

echo "2. Testing Traefik health..."
test_http "http://localhost:9000/ping" 200 "Traefik ping endpoint"
echo

echo "3. Testing HTTP routing..."
test_http "http://localhost:9000/docs" 200 "HTTP /docs endpoint"
echo

echo "4. Testing HTTPS routing..."
test_https "https://localhost:9001/docs" 200 "HTTPS /docs endpoint"
echo

echo "5. Testing transcription API..."
test_transcription
echo

echo "6. Testing dashboard access..."
# Note: Dashboard may redirect, this tests basic connectivity
test_https "https://localhost:9005/" 302 "Dashboard endpoint (expects redirect)"
echo

echo "=== Testing Complete ==="
echo
echo "If all tests pass, the stack is working correctly!"
echo "For production, update routing rules from PathPrefix('/') to Host('\${PUBLIC_FQDN}')"
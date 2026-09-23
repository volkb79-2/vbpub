#!/bin/bash
# Performance benchmark for LocalAI models
set -euo pipefail

echo "=== LocalAI Performance Benchmark ==="
echo ""

MODELS=("tinyllama-1.1b" "phi-2" "mistral-7b-instruct")
TEST_PROMPT="Fix grammar: hello world its a test"

echo "Testing with prompt: '$TEST_PROMPT'"
echo ""

for model in "${MODELS[@]}"; do
    echo "─────────────────────────────────────"
    echo "Model: $model"
    echo "─────────────────────────────────────"
    
    # Check if model exists
    if ! curl -s http://localhost:8090/v1/models | jq -r '.data[].id' | grep -q "^$model$"; then
        echo "⚠️  Model not found, skipping..."
        echo ""
        continue
    fi
    
    # Run 3 iterations
    total_time=0
    successful_runs=0
    
    for i in {1..3}; do
        echo -n "  Run $i: "
        
        start_time=$(date +%s%N)
        response=$(curl -s http://localhost:8090/v1/chat/completions \
          -H "Content-Type: application/json" \
          -d "{
            \"model\": \"$model\",
            \"messages\": [{\"role\": \"user\", \"content\": \"$TEST_PROMPT\"}],
            \"temperature\": 0.7,
            \"max_tokens\": 100
          }")
        end_time=$(date +%s%N)
        
        elapsed=$(( (end_time - start_time) / 1000000 ))
        
        if echo "$response" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
            result=$(echo "$response" | jq -r '.choices[0].message.content' | tr '\n' ' ')
            echo "${elapsed}ms - Result: ${result:0:50}..."
            total_time=$((total_time + elapsed))
            successful_runs=$((successful_runs + 1))
        else
            echo "FAILED"
        fi
    done
    
    if [ $successful_runs -gt 0 ]; then
        avg_time=$((total_time / successful_runs))
        echo ""
        echo "  Average: ${avg_time}ms ($successful_runs/3 successful)"
    fi
    
    echo ""
done

echo "=== Benchmark Complete ==="
echo ""
echo "Performance Summary:"
echo "  TinyLlama: ~1500-2000ms (fastest, basic quality)"
echo "  Phi-2:     ~3500-4000ms (balanced)"
echo "  Mistral:   ~9000-10000ms (best quality, slowest)"
echo ""
echo "Recommendation: Choose model based on use case"
echo "  - Real-time chat: TinyLlama"
echo "  - Document processing: Phi-2"
echo "  - Quality-critical: Mistral"

#!/bin/bash

echo "🔍 Testing Piston service URLs..."
echo "=================================="

# Array of URLs to test
urls=(
    "http://localhost:2000"
    "http://127.0.0.1:2000"
    "http://host.docker.internal:2000"
    "http://piston:2000"
    "http://172.17.0.1:2000"
    "http://0.0.0.0:2000"
)

working_urls=()

for url in "${urls[@]}"; do
    echo ""
    echo "Testing: $url"
    echo "----------------------------------------"
    
    # Test if service responds
    if timeout 5 curl -s "${url}/api/v2/runtimes" > /dev/null 2>&1; then
        echo "✅ Service reachable"
        
        # Get runtimes info
        runtimes=$(timeout 5 curl -s "${url}/api/v2/runtimes" 2>/dev/null)
        if [ $? -eq 0 ]; then
            echo "📋 Runtimes: $runtimes"
            working_urls+=("$url")
        else
            echo "⚠️  Service reachable but API failed"
        fi
    else
        echo "❌ Service not reachable"
    fi
done

echo ""
echo "📊 Summary:"
echo "==========="

if [ ${#working_urls[@]} -eq 0 ]; then
    echo "❌ No working URLs found!"
    echo ""
    echo "Troubleshooting steps:"
    echo "1. Check if Piston is running: docker ps | grep piston"
    echo "2. Check Piston logs: docker logs \$(docker ps -q --filter 'name=piston')"
    echo "3. Check if port 2000 is exposed: netstat -tlnp | grep 2000"
    echo "4. Try starting Piston with: docker run -p 2000:2000 ghcr.io/engineer-man/piston"
else
    echo "✅ Working URLs found:"
    for url in "${working_urls[@]}"; do
        echo "   - $url"
    done
    
    # Test code execution with first working URL
    first_url="${working_urls[0]}"
    echo ""
    echo "🧪 Testing code execution with: $first_url"
    
    # Create test payload
    cat > /tmp/piston_test_payload.json << 'EOF'
{
  "language": "python",
  "version": "3.12.0",
  "files": [
    {
      "name": "test.py",
      "content": "print('Production test successful!')"
    }
  ],
  "run_timeout": 3000
}
EOF
    
    response=$(timeout 10 curl -s -X POST "${first_url}/api/v2/execute" \
        -H "Content-Type: application/json" \
        -d @/tmp/piston_test_payload.json 2>/dev/null)
    
    if [ $? -eq 0 ]; then
        echo "✅ Code execution test successful!"
        echo "Response: $response"
        echo ""
        echo "💡 Recommended settings for Django:"
        echo "PISTON_URL = '$first_url'"
    else
        echo "❌ Code execution test failed"
    fi
fi

echo ""
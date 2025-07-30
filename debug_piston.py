#!/usr/bin/env python3
"""
Debug script to test Piston connectivity from Django
Run this in Django shell: python manage.py shell < debug_piston.py
"""

import os
import sys
import socket
import requests
from urllib.parse import urlparse

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dmoj.settings')
import django
django.setup()

from django.conf import settings

def test_url_connectivity(url):
    """Test if a URL is reachable"""
    print(f"\n🔍 Testing connectivity to: {url}")
    
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or 80
        
        # Test socket connection
        print(f"   Socket test to {host}:{port}...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print(f"   ✅ Socket connection successful")
        else:
            print(f"   ❌ Socket connection failed (error {result})")
            return False
            
        # Test HTTP request
        print(f"   HTTP test...")
        response = requests.get(f"{url}/api/v2/runtimes", timeout=10)
        print(f"   ✅ HTTP request successful (status: {response.status_code})")
        print(f"   Response: {response.text[:200]}...")
        return True
        
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False

def main():
    print("🐍 Piston Service Debug Tool")
    print("=" * 50)
    
    # Get current PISTON_URL setting
    piston_url = getattr(settings, 'PISTON_URL', 'http://host.docker.internal:2000')
    print(f"Current PISTON_URL setting: {piston_url}")
    
    # Test URLs to try
    test_urls = [
        piston_url,  # Current setting
        'http://localhost:2000',  # Direct localhost
        'http://127.0.0.1:2000',  # IP localhost
        'http://piston:2000',  # Docker service name
        'http://host.docker.internal:2000',  # Docker Desktop
    ]
    
    print(f"\nTesting {len(test_urls)} possible URLs...")
    
    working_urls = []
    for url in test_urls:
        if test_url_connectivity(url):
            working_urls.append(url)
    
    print(f"\n📊 Results:")
    print(f"   Working URLs: {len(working_urls)}")
    print(f"   Failed URLs: {len(test_urls) - len(working_urls)}")
    
    if working_urls:
        print(f"\n✅ Working URLs found:")
        for url in working_urls:
            print(f"   - {url}")
        
        recommended_url = working_urls[0]
        print(f"\n💡 Recommendation:")
        print(f"   Set PISTON_URL = '{recommended_url}' in your settings")
        
        # Test actual execution
        print(f"\n🧪 Testing code execution with {recommended_url}...")
        try:
            payload = {
                "language": "python",
                "version": "3.12.0",
                "files": [{"name": "test.py", "content": "print('Debug test successful!')"}],
                "run_timeout": 3000
            }
            
            response = requests.post(f"{recommended_url}/api/v2/execute", 
                                   json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if 'run' in result and result['run'].get('stdout'):
                    print(f"   ✅ Code execution successful!")
                    print(f"   Output: {result['run']['stdout'].strip()}")
                else:
                    print(f"   ⚠️  Code execution returned unexpected format: {result}")
            else:
                print(f"   ❌ Code execution failed (status: {response.status_code})")
                print(f"   Response: {response.text}")
                
        except Exception as e:
            print(f"   ❌ Code execution test failed: {e}")
    else:
        print(f"\n❌ No working URLs found!")
        print(f"\nTroubleshooting steps:")
        print(f"1. Check if Piston service is running: docker ps | grep piston")
        print(f"2. Check Piston logs: docker logs <piston_container>")
        print(f"3. Verify network connectivity between containers")
        print(f"4. Make sure Piston is listening on the correct port")

if __name__ == "__main__":
    main()
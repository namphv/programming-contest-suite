import requests
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def execute_tutorial_code(request):
    """
    Execute Python code for tutorials using Piston API
    """
    try:
        data = json.loads(request.body)
        code = data.get('code', '').strip()
        input_data = data.get('input', '')
        
        if not code:
            return JsonResponse({
                'success': False,
                'error': 'No code provided'
            }, status=400)
        
        # Prepare Piston API request
        piston_url = getattr(settings, 'PISTON_URL', 'http://host.docker.internal:2000')
        piston_payload = {
            "language": "python",
            "version": "3.12.0",
            "files": [
                {
                    "name": "main.py",
                    "content": code
                }
            ],
            "stdin": input_data,
            "compile_timeout": 10000,
            "run_timeout": 3000,
            "compile_memory_limit": -1,
            "run_memory_limit": -1
        }
        
        # Execute code via Piston
        response = requests.post(
            f"{piston_url}/api/v2/execute",
            json=piston_payload,
            timeout=70  # Allow extra time for 60s execution + network overhead
        )

        if response.status_code != 200:
            return JsonResponse({
                'success': False,
                'error': f'Piston API error: {response.status_code}'
            }, status=500)
        
        result = response.json()
        
        # Process Piston response
        if 'run' in result and result['run']:
            run_result = result['run']
            return JsonResponse({
                'success': True,
                'output': run_result.get('stdout', ''),
                'error': run_result.get('stderr', ''),
                'code': run_result.get('code', 0),
                'signal': run_result.get('signal', None)
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No execution result from Piston',
                'raw_response': result
            })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except requests.RequestException as e:
        logger.error(f"Piston API request failed: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Code execution service unavailable'
        }, status=503)
    except Exception as e:
        logger.error(f"Tutorial code execution error: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Internal server error'
        }, status=500)


@require_http_methods(["GET"])
def tutorial_runner_health(request):
    """
    Check if Piston service is available
    """
    try:
        piston_url = getattr(settings, 'PISTON_URL', 'http://host.docker.internal:2000')
        response = requests.get(f"{piston_url}/api/v2/runtimes", timeout=5)
        
        if response.status_code == 200:
            runtimes = response.json()
            python_available = any(r['language'] == 'python' for r in runtimes)
            
            return JsonResponse({
                'status': 'healthy',
                'piston_available': True,
                'python_available': python_available,
                'total_runtimes': len(runtimes)
            })
        else:
            return JsonResponse({
                'status': 'unhealthy',
                'piston_available': False,
                'error': f'Piston API returned {response.status_code}'
            })
            
    except requests.RequestException as e:
        logger.error(f"Piston health check failed: {e}")
        return JsonResponse({
            'status': 'unhealthy',
            'piston_available': False,
            'error': 'Piston service unreachable'
        })
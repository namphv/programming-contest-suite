from flask import Flask, request, jsonify
import subprocess
import tempfile
import os
import sys
import signal
from contextlib import contextmanager

app = Flask(__name__)

@contextmanager
def timeout(duration):
    def timeout_handler(signum, frame):
        raise TimeoutError("Code execution timed out")
    
    # Set the signal handler and a alarm
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(duration)
    
    try:
        yield
    finally:
        # Disable the alarm
        signal.alarm(0)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'})

@app.route('/execute', methods=['POST'])
def execute_python():
    try:
        data = request.json
        code = data.get('code', '')
        input_data = data.get('input', '')
        timeout_seconds = min(data.get('timeout', 5), 10)  # Max 10 seconds
        
        if not code.strip():
            return jsonify({'error': 'No code provided'})
        
        # Create temporary file for code
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            code_file = f.name
        
        try:
            # Execute with timeout and resource limits
            with timeout(timeout_seconds):
                result = subprocess.run(
                    [sys.executable, code_file],
                    input=input_data,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    cwd=tempfile.gettempdir()  # Run in temp directory
                )
            
            return jsonify({
                'success': True,
                'output': result.stdout,
                'error': result.stderr,
                'returncode': result.returncode,
                'timeout': False
            })
            
        except (subprocess.TimeoutExpired, TimeoutError):
            return jsonify({
                'success': False,
                'output': '',
                'error': 'Code execution timed out',
                'returncode': -1,
                'timeout': True
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'output': '',
                'error': f'Execution error: {str(e)}',
                'returncode': -1,
                'timeout': False
            })
        finally:
            # Clean up temp file
            if os.path.exists(code_file):
                os.unlink(code_file)
                
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Request error: {str(e)}'
        }), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
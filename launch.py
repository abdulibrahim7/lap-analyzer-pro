"""Reconstructed cross-platform launcher; run with Python 3.11+.
Windows batch / macOS shell bootstrap the project venv before running this.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def setup_dependencies():
    requirements = ROOT / 'requirements.txt'
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Use a startup script or run launch.py inside a virtual environment.')
    stamp = Path(sys.prefix) / '.lap-analyzer-requirements'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if stamp.is_file() and stamp.read_text() == digest:
        return
    print('Installing project dependencies (internet required on first run)...', flush=True)
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', str(requirements)])
    stamp.write_text(digest)

def local_json(url, data=None, timeout=4):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def locate_ollama():
    candidates = [shutil.which('ollama'), str(Path.home() / 'AppData/Local/Programs/Ollama/ollama.exe'),
                  '/usr/local/bin/ollama', '/opt/homebrew/bin/ollama', '/Applications/Ollama.app/Contents/Resources/ollama']
    return next((x for x in candidates if x and Path(x).is_file()), None)


def prepare_ollama(base_url, model):
    parts = urlsplit(base_url)
    if parts.hostname not in {'localhost', '127.0.0.1', '::1'}:
        return
    root = f'{parts.scheme}://{parts.netloc}'
    tags_url = root + '/api/tags'
    exe = locate_ollama()
    try:
        tags = local_json(tags_url)
        print('Ollama already running; reusing it.', flush=True)
    except Exception:
        if not exe:
            raise RuntimeError('Ollama not found. Install/open Ollama from https://ollama.com/download.')
        print('Starting Ollama...', flush=True)
        env = dict(os.environ, OLLAMA_HOST=parts.netloc)
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with open(ROOT / 'ollama.log', 'a', encoding='utf-8') as log:
            subprocess.Popen([exe, 'serve'], env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        for _ in range(25):
            try:
                tags = local_json(tags_url, timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('Ollama API did not become ready. See ollama.log.')
    names = {item.get('name') for item in tags.get('models', [])}
    if model not in names and model + ':latest' not in names:
        print(f'Downloading {model} (one-time model download; may be several GB)...', flush=True)
        if exe:
            subprocess.check_call([exe, 'pull', model], env=dict(os.environ, OLLAMA_HOST=parts.netloc))
        else:
            # The local server may be available although the CLI is not on PATH.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(root + '/api/pull', data=json.dumps({'model':model,'stream':True}).encode(),
                                         headers={'Content-Type':'application/json'})
            with opener.open(req, timeout=300) as response:
                last = None
                for line in response:
                    status = json.loads(line)
                    if status.get('error'):
                        raise RuntimeError('Ollama model download failed.')
                    current = status.get('status')
                    if current != last:
                        print(current, flush=True)
                        last = current
        names = {item.get('name') for item in local_json(tags_url).get('models', [])}
        if model not in names and model + ':latest' not in names:
            raise RuntimeError('Configured Ollama model is not installed.')
    print(f'Ollama model ready: {model}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-ollama', action='store_true', help='Do not start or download Ollama; backend may still use a running model.')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer required.')
    setup_dependencies()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    import main as backend
    import uvicorn
    try:
        port = int(os.getenv('APP_PORT', '8000'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.error('APP_PORT must be an integer between 1 and 65535.')
    if not args.skip_ollama:
        try:
            prepare_ollama(backend.BASE_URL, backend.MODEL)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(f'Ollama setup failed: {exc}\nThe app will still start; fallback is labeled in the UI.', flush=True)
    url = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(backend.app, host='127.0.0.1', port=port, log_level='info'))
    def open_when_ready():
        for _ in range(300):
            if server.started:
                print(f'Open {url} (keep this window open).', flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return
            time.sleep(.1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
    if not server.started:
        raise SystemExit('The backend did not start. Check the console and APP_PORT.')


if __name__ == '__main__':
    main()

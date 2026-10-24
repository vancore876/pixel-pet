"""Read-only checks for the source copy and the process serving Work Chat."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def source_check():
    import server_business_schema as schema
    print('Project folder:', ROOT)
    print('Python:', sys.executable)
    print('Loaded notebook schema:', schema.__file__)
    for module in ('admin.py', 'admin_store.py'):
        path = ROOT / 'work_server' / module
        print('IT module:', path, 'present' if path.is_file() else 'MISSING')
        if not path.is_file():
            return False
    try:
        revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
                                  capture_output=True, text=True, timeout=3)
        if revision.returncode == 0:
            print('Git revision:', revision.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    example = {'id': 'a' * 32, 'kind': 'note', 'title': 'Diagnostic only',
               'body': 'PART-1', 'highlights': ['PART-1']}
    try:
        result = schema.validate_business_entry(example, example['id'])
        if result.get('kind') != 'note' or result.get('highlights') != ['PART-1']:
            raise ValueError('The schema did not retain the Writing note and highlights.')
    except ValueError as error:
        print('SOURCE CHECK FAILED:', error)
        return False
    print('Source check: Writing notes and highlights are supported. No note was saved.')
    return True


def health_check(url):
    address = url.rstrip('/') + '/api/health'
    print('Running server:', address)
    try:
        request = urllib.request.Request(address, headers={'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(request, timeout=3) as reply:
            data = json.loads(reply.read(16384))
        print('Server response:', json.dumps(data, ensure_ascii=True))
    except (OSError, ValueError, urllib.error.URLError) as error:
        print('SERVER CHECK FAILED:', error)
        return False
    version = data.get('notebook_schema') if isinstance(data, dict) else None
    if type(version) is not int or version < 2:
        print('The responding process does not advertise the new notebook schema.')
        print('This is separate from the source check above: updated files do not identify the running process.')
        return False
    print('Running server advertises notebook schema 2 or later.')
    return True


def it_check(url):
    address = url.rstrip('/') + '/api/it/overview'
    try:
        with urllib.request.urlopen(address, timeout=3) as reply:
            print('IT endpoint returned HTTP', reply.status, 'without authentication; check server configuration.')
            return False
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code == 401:
            print('IT endpoint is present and requires an IT sign-in (HTTP 401).')
            return True
        print('IT endpoint:', address, 'HTTP', code)
        if code == 404:
            print('The running process does not provide IT administration. This is not an incorrect password.')
        return False
    except OSError as error:
        print('IT endpoint check failed:', error)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--server', help='Server URL; defaults to localhost on the selected port')
    parser.add_argument('--preflight', action='store_true', help='Refuse to launch a second server on an occupied port')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Port must be from 1 to 65535.')
    source_ok = source_check()
    if args.preflight:
        if listening(args.port):
            print(f'LAUNCH BLOCKED: port {args.port} is already in use on this computer.')
            health_check(f'http://127.0.0.1:{args.port}')
            print('The existing process has not been stopped or changed.')
            print('Close its server window or stop its Windows service before restarting this project copy.')
            return 1
        return 0 if source_ok else 1
    url = args.server or f'http://127.0.0.1:{args.port}'
    health_ok = health_check(url)
    it_ok = it_check(url)
    return 0 if health_ok and it_ok and source_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())

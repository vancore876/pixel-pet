"""Create a release manifest from Git-tracked application files and built EXE."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app_updates import file_digest, managed_path

commit, stamp = subprocess.run(['git', '-C', str(root), 'show', '-s', '--format=%H %ct', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip().split()
tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, check=True).stdout.decode().split('\0')
files = {name: file_digest((root / name).read_bytes(), name) for name in tracked if name and managed_path(name)}
exe = root / 'dist' / 'PixelSystemBuddy.exe'
(root / 'dist' / 'update.json').write_text(json.dumps({'commit': commit, 'committed_at': int(stamp), 'files': files,
    'exe_sha256': hashlib.sha256(exe.read_bytes()).hexdigest()}, indent=2), encoding='utf-8')

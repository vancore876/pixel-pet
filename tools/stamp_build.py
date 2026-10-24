"""Embed the exact Git commit in a frozen desktop build."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
result = subprocess.run(['git', '-C', str(root), 'show', '-s', '--format=%H %ct', 'HEAD'], capture_output=True, text=True, check=True)
commit, stamp = result.stdout.strip().split()
(root / 'app_build.json').write_text(json.dumps({'commit': commit, 'committed_at': int(stamp)}), encoding='utf-8')

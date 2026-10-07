"""Refresh the launcher's private environment only when runtime pins change."""
from importlib import metadata
from pathlib import Path
import subprocess
import sys


def missing_requirements(path):
    missing = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        requirement = line.split('#', 1)[0].strip()
        if not requirement:
            continue
        name, marker, version = requirement.partition('==')
        if not marker or not name or not version:
            raise ValueError('Runtime requirements must use exact package versions.')
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            missing.append(requirement)
    return missing


def main():
    path = Path(__file__).resolve().parent / 'requirements.txt'
    if missing_requirements(path):
        print('Installing or refreshing Jeffery dependencies…', flush=True)
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(path)])
        if result.returncode:
            return result.returncode
        if missing_requirements(path):
            print('The required dependency versions are still unavailable.', file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

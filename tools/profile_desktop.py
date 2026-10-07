"""Record a desktop CPU flamegraph with the optional py-spy developer tool."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def find_profiler() -> str | None:
    """Prefer the profiler installed beside this Python interpreter."""
    name = "py-spy.exe" if sys.platform == "win32" else "py-spy"
    nearby = Path(sys.executable).parent / name
    return str(nearby) if nearby.is_file() else shutil.which(name)


def record_command(profiler: str, output: Path, seconds: int, rate: int,
                   pid: int | None = None) -> list[str]:
    command = [profiler, "record", "--output", str(output), "--format", "flamegraph",
               "--duration", str(seconds), "--rate", str(rate), "--subprocesses"]
    if pid is not None:
        command.extend(["--pid", str(pid)])
    else:
        command.extend(["--", sys.executable, str(ROOT / "main.py")])
    return command


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pid", type=int, help="PID of a running Jeffery Python process")
    target.add_argument("--launch", action="store_true", help="start Jeffery under the profiler")
    parser.add_argument("--output", required=True, type=Path, help="destination SVG flamegraph")
    parser.add_argument("--seconds", type=int, default=60, help="recording duration (1–3600 seconds)")
    parser.add_argument("--rate", type=int, default=100, help="samples per second (1–1000)")
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 3600 or not 1 <= args.rate <= 1000:
        parser.error("Use 1–3600 seconds and 1–1000 samples per second.")
    if args.pid is not None and args.pid <= 0:
        parser.error("The process ID must be positive.")
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".svg":
        parser.error("The output filename must end in .svg.")
    if output.exists():
        parser.error("The output already exists. Choose a new filename.")
    profiler = find_profiler()
    if profiler is None:
        print("Install the profiling tool first: python -m pip install -r requirements-dev.txt",
              file=sys.stderr)
        return 1
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(record_command(profiler, output, args.seconds, args.rate, args.pid),
                                cwd=ROOT, check=False)
    except OSError as error:
        print(f"Could not start profiling: {error}", file=sys.stderr)
        return 1
    if result.returncode == 0:
        print(f"Flamegraph saved: {output}")
    else:
        print("Profiling failed. Review py-spy's error above. Attaching on Windows may require "
              "an administrator terminal; --launch can avoid attaching to an existing process.",
              file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the shared office application with a private Windows user profile."""
from __future__ import annotations

import os
import sys


def main():
    os.environ["JEFFERY_WORK_PROFILE"] = "1"
    sys.dont_write_bytecode = True
    from main import main as run_buddy
    return run_buddy()


if __name__ == "__main__":
    # PDF worker dispatch must happen before importing Qt or creating windows.
    from multiprocessing import freeze_support
    freeze_support()
    sys.exit(main())

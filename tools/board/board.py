#!/usr/bin/env python3
"""Entry point: python tools/board/board.py <command> [options]   (Python 3.10+, standard library only)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentboard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

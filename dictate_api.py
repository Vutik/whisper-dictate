#!/usr/bin/env python3
"""Entry point for the standalone recognition server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from whisper_dictate.api_main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

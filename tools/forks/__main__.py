"""Runs the tool from a checkout: `python3 tools/forks report PATH...`."""

import sys

from forks.cli import main

sys.exit(main())

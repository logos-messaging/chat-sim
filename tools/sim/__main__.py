"""Runs the tool from a checkout: `python3 tools/sim run SCENARIO --chat-cli PATH --out DIR`."""

import sys

from sim.cli import main

sys.exit(main())

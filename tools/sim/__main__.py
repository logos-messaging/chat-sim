"""Runs the tool from a checkout: `python3 tools/sim run SCENARIO --one-process PATH --out DIR`."""

import sys

from sim.cli import main

sys.exit(main())

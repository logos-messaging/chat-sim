"""Runs the tool from a checkout: `python3 tools/sim --chat-cli PATH --peers N --out DIR`."""

import sys

from sim.cli import main

sys.exit(main())

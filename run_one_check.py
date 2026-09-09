#!/usr/bin/env python3
"""Runs a single tracked-page check as its own OS process.

Invoked by scheduler.py (never run directly) so that a Playwright/Chromium
hang can be killed with a hard, OS-level timeout instead of freezing the
whole scheduler. A per-call Playwright `timeout=` isn't always enough on
its own to guarantee a call returns — in production, a check on this exact
site hung for roughly 11 hours past its stated 30-second Playwright
timeout, with systemd still showing the (stuck, not crashed) process as
"active" the whole time. Running each check in its own process lets
scheduler.py enforce a real deadline from the outside.
"""

import json
import sys

from checker import check_one_page

if __name__ == "__main__":
    tracked_page = json.loads(sys.argv[1])
    check_one_page(tracked_page)

#!/usr/bin/env python3
"""Run only offline suites with a process-wide socket guard (standard library only).

The guard covers this interpreter, including worker threads; stdio subprocess
smoke tests deliberately perform only protocol/recipe discovery, not API calls.
Live test_mcp.py and test_recipes.py are never collected here.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent


def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise AssertionError("Network access is forbidden in the offline suite")


def main():
    sys.addaudithook(deny_network)
    success = True
    for pattern in ("test_unit*.py", "test_issue_tools.py", "test_hygiene.py"):
        print("\nOffline suite: " + pattern, flush=True)
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        success = result.wasSuccessful() and result.testsRun > 0 and success
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

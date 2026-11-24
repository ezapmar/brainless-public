#!/usr/bin/env python3
"""The test entry point: run the offline suite with no PYTHONPATH to set.

The modules under tools/ and .agents/scripts/ import each other by bare name
(`import llm`, `from owner_profile import ...`), so those directories have to be
on sys.path before a single test module is imported. The README used to ask for
`PYTHONPATH=tools:.agents/scripts:.`, and a run that forgot it reported twenty
import errors that read like broken tests rather than a missing path.

That is also why `python3 -m unittest discover -s tools/tests` is not supported:
unittest puts only the start directory on sys.path, and the bootstrap below runs
far too late to help. Use this file instead, which is what the docstrings in
tools/tests/ and the README name:

    python3 tools/run_tests.py                       # every test
    python3 tools/run_tests.py test_egress_ledger    # one module (prefix works)
    python3 tools/run_tests.py test_llm_ test_comp   # several prefixes

No test reaches the network or a model: the suite is deterministic by design.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("tools", "tools/tests", ".agents/scripts", "."):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)


def build_suite(prefixes):
    """One suite over the modules matching every prefix, or over the whole folder."""
    tests = str(ROOT / "tools" / "tests")
    loader = unittest.TestLoader()
    if not prefixes:
        return loader.discover(tests, pattern="test_*.py", top_level_dir=tests)
    suite = unittest.TestSuite()
    for prefix in prefixes:
        suite.addTests(loader.discover(tests, pattern=f"{prefix}*.py", top_level_dir=tests))
    return suite


if __name__ == "__main__":
    os.chdir(ROOT)
    suite = build_suite(sys.argv[1:])
    if suite.countTestCases() == 0:
        print(f"run_tests: no test matches {sys.argv[1:] or 'test_*.py'}", file=sys.stderr)
        sys.exit(2)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


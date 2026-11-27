"""The suite's runner: `python -m unittest`, with a time limit on every test. (#94)

`kit.py test` runs this file, and `make check` runs `kit.py test`, so CI's offline jobs run it too.
unittest has no per-test timeout and pytest-timeout does not reach it, so a test that hangs held a
job until its `timeout-minutes`, with nothing in the log saying where. Here each test arms
`faulthandler.dump_traceback_later` when it starts and disarms it when it stops. A test that
outlives the limit gets every thread's traceback dumped to stderr, under a `Timeout (h:mm:ss)!`
line and after the test's own name, which the verbose runner has already printed, and the process
exits 1 at once. Nothing after it runs: a hang has no clean way out of a thread.

KIT_TEST_TIMEOUT sets the limit in seconds (default 300, 0 switches it off). The opt-in tiers that
embed or benchmark with qmd's models run for minutes on a CPU; raise it, or set 0, for those.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import unittest
from pathlib import Path

DEFAULT_LIMIT = 300.0


def limit() -> float:
    raw = os.environ.get("KIT_TEST_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        value = float(raw)
    except ValueError:
        sys.exit(f"KIT_TEST_TIMEOUT must be a number of seconds (0 switches the limit off), not {raw!r}")
    if value < 0:
        sys.exit(f"KIT_TEST_TIMEOUT must not be negative, not {raw!r}")
    return value


class TimedResult(unittest.TextTestResult):
    seconds = DEFAULT_LIMIT

    def startTest(self, test):
        super().startTest(test)  # prints and flushes the test's name first, so the dump follows it
        if self.seconds > 0:
            faulthandler.dump_traceback_later(self.seconds, exit=True)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)


class TimedRunner(unittest.TextTestRunner):
    resultclass = TimedResult


if __name__ == "__main__":
    TimedResult.seconds = limit()
    # Run as a script, Python puts tests/ first on sys.path; `python -m unittest` puts the working
    # directory there. The repository root is what the suite imports from, and discovery's `-t`
    # adds it as well.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    unittest.main(module=None, testRunner=TimedRunner)

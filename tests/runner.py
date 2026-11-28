"""The suite's runner: `python -m unittest`, with a time limit on every test. (#94)

`kit.py test` runs this file, and `make check` runs `kit.py test`, so CI's offline jobs run it too.
unittest has no per-test timeout and pytest-timeout does not reach it, so a test that hangs held a
job until its `timeout-minutes`, with nothing in the log saying where. Here a
`faulthandler.dump_traceback_later` watchdog is armed for the whole run and re-armed whenever a test
starts or stops, so each test, and each stretch between two tests, gets the limit afresh. That
stretch is where unittest runs setUpModule, setUpClass, tearDownClass and tearDownModule, which
never pass through startTest, so a watchdog armed only around the test let a hung fixture run
unbounded. Past the limit every thread's traceback is dumped to stderr under a `Timeout (h:mm:ss)!`
line, and the process exits 1 at once. A hung test is named by the line the verbose runner has
already printed; a hung fixture by its frame in the traceback. Nothing after it runs: a hang has no
clean way out of a thread.

KIT_TEST_TIMEOUT sets the limit in seconds (default 300, 0 switches it off). The opt-in tiers that
embed or benchmark with qmd's models run for minutes on a CPU; raise it, or set 0, for those.
"""
from __future__ import annotations

import faulthandler
import math
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
    if not math.isfinite(value) or value < 0:
        sys.exit(f"KIT_TEST_TIMEOUT must be a finite, non-negative number of seconds, not {raw!r}")
    if value > 0:
        # The largest limit faulthandler accepts depends on the platform's time_t; arm once here,
        # so a limit it cannot take is refused now rather than crashing the first test.
        try:
            faulthandler.dump_traceback_later(value, exit=True)
        except (OverflowError, ValueError):
            sys.exit(f"KIT_TEST_TIMEOUT is more seconds than this platform's timer can count: {raw!r}")
        finally:
            faulthandler.cancel_dump_traceback_later()
    return value


class TimedResult(unittest.TextTestResult):
    seconds = DEFAULT_LIMIT

    def arm(self):
        """(Re)start the watchdog; arming again replaces the running timer."""
        if self.seconds > 0:
            faulthandler.dump_traceback_later(self.seconds, exit=True)

    def startTestRun(self):
        super().startTestRun()
        self.arm()  # covers the first setUpModule and setUpClass

    def startTest(self, test):
        super().startTest(test)  # prints and flushes the test's name first, so the dump follows it
        self.arm()

    def stopTest(self, test):
        super().stopTest(test)
        self.arm()  # covers the fixtures between this test and the next, and the last teardowns

    def stopTestRun(self):
        faulthandler.cancel_dump_traceback_later()
        super().stopTestRun()


class TimedRunner(unittest.TextTestRunner):
    resultclass = TimedResult


if __name__ == "__main__":
    TimedResult.seconds = limit()
    # Run as a script, Python puts tests/ first on sys.path; `python -m unittest` puts the working
    # directory there. The repository root is what the suite imports from, and discovery's `-t`
    # adds it as well.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    unittest.main(module=None, testRunner=TimedRunner)

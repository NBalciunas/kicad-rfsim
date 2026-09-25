"""The guard of `runner._diverged` against a run that INCREASES.

**The rule** (B42, from 2026-09-20): a run is not stable when the largest
|u| of the last tenth of a port trace is too large. The limit is
`GROWTH_LIMIT` times the TURN of its envelope. The turn is the smallest
point of the envelope after its largest point. The rule of before
compared the end with the largest |u| of the FIRST HALF. A growth that starts late did not cause an alarm with
that rule. A trace can decrease by a factor of 1e5 and increase by a factor
of 1e3, and its end is then below its head.

This file does not run the solver. It reads the traces that are on the
disk, and it makes four traces of its own:

1. **a ring with a high Q** that continues to ring at the last row;
2. **a beat** of two modes. Its envelope has a deep null in the middle, and
   it increases much above that null after it;
3. **"a resonator that builds up"**, which ends 3 times above the peak of
   the excitation. The rule of before accepted it on purpose;
4. **a growth that starts late**: the trace decreases and then turns.

Numbers 1 to 3 must give NO ALARM, and number 4 must give an ALARM.

Run it with the python of the solver:

    C:\\openEMS\\venv\\Scripts\\python.exe test_growth.py
"""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.environ.get("RFSIM_PLUGINS") or os.path.join(
    os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import runner  # noqa: E402

# The directory that keeps the traces of a run that grew late. After B30
# closed, no board makes such a trace, thus it is the only measurement
# that can fit the limit again.
LATE = "out_rlc_growth_late_2026-09-19"
# The harness of B30 makes traces that increase ON PURPOSE, and the ladder
# of `run_stability.py` also makes them. The plugin did not accept these
# runs, thus they are not in the population of the limit.
#
# **A directory with a name that ends in `_tmp` is the scratch of a rig.**
# A rig that runs at the same time as this file writes a run that diverges
# into one of them. This file stopped with a failure one time only for that
# cause.
SKIP = ("out_growth", LATE)
SKIP_SUFFIX = "_tmp"

ROWS = 6000          # the rows of a trace that this file makes
F = 3.5e9            # the carrier
DT = 2.0e-11         # the step between two rows, as a port file has it


def _write(path, u):
    """Write |u| as a port file, in the format that openEMS writes."""
    t = np.arange(len(u)) * DT
    with open(path, "w") as fh:
        fh.write("%% a trace of test_growth.py\n")
        for ti, ui in zip(t, u):
            fh.write("%.6e\t%.9e\n" % (ti, ui))


def _carrier(n=ROWS):
    return np.sin(2 * np.pi * F * np.arange(n) * DT)


def ring(tau_ns=400.0):
    """A resonator with a high Q: it continues to ring at the last row."""
    t = np.arange(ROWS) * DT * 1e9
    return np.exp(-t / tau_ns) * _carrier()


def beat(null_at=0.62, depth=4e-4):
    """Two modes that beat: a deep null, and an increase much above it."""
    t = np.arange(ROWS) * DT * 1e9
    env = np.exp(-t / 900.0)
    # a null at `null_at` of the window, as deep as `depth`
    x = (np.arange(ROWS) / float(ROWS) - null_at) / 0.06
    env = env * np.maximum(depth, np.abs(np.tanh(x)))
    return env * _carrier()


def buildup(times=3.0):
    """A resonator that gets energy from the pulse and ends above it.

    The trace ends `times` above the peak of the excitation. The rule of
    before accepted it on purpose, and the rule of today must also accept
    it. The envelope has no TURN, thus it did not decrease.
    """
    n = ROWS
    i = np.arange(n)
    pulse = np.exp(-((i - 0.05 * n) / (0.02 * n)) ** 2)
    rise = times * (i / float(n)) ** 0.5
    return (pulse + rise) * _carrier()


def late(turn_at=0.15, decay=1e-5, grow=1e5):
    """A trace that decreases, turns and then increases: the case of B42.

    The decrease STOPS at the turn. A trace that continues to decrease
    below the growth has no turn. The end is `grow` times the turn. The
    board of `out_rlc_growth_late_2026-09-19` gives 1.4e5 there.
    """
    i = np.arange(ROWS) / float(ROWS)
    down = decay ** (np.minimum(i, turn_at) / turn_at)
    up = grow ** np.maximum(0.0, (i - turn_at) / (1.0 - turn_at))
    return down * up * _carrier()


def check_made(tmp):
    """Give the number of failures for the four traces of this file."""
    cases = [("a ring with a high Q", ring(), False),
             ("a beat with a deep null", beat(), False),
             ("a resonator that builds up", buildup(), False),
             ("a growth that starts late", late(), True)]
    bad = 0
    print("%-30s%12s%12s%10s%8s"
          % ("the trace", "the turn", "the end", "ratio", ""))
    for name, u, want_alarm in cases:
        d = os.path.join(tmp, name.replace(" ", "_"))
        os.makedirs(d, exist_ok=True)
        _write(os.path.join(d, "port_ut_1A"), u)
        got = runner._diverged(d)
        a = np.abs(u)
        nseg = min(runner.GROWTH_SEGMENTS, len(a) // 10)
        env = np.array([c.max() for c in np.array_split(a, nseg) if len(c)])
        env = env[env > 0]
        start = int(np.argmax(env[:max(1, len(env) // 2)]))
        stop = max(start + 1, len(env) - max(1, len(env) // 10))
        turn = env[start:stop].min()
        tail = a[-(len(a) // 10):].max()
        ok = bool(got) == want_alarm
        bad += not ok
        print("%-30s%12.4g%12.4g%10.4g%8s"
              % (name, turn, tail, tail / turn, "OK" if ok else "FAILED"))
    return bad


def check_disk():
    """Give the number of failures for the traces that are on the disk."""
    bad, quiet, alarm = 0, 0, []
    for d in sorted(glob.glob(os.path.join(HERE, "out_*"))):
        for sub in [d] + sorted(glob.glob(os.path.join(d, "exc*"))):
            if not glob.glob(os.path.join(sub, "port_ut_*")):
                continue
            rel = os.path.relpath(sub, HERE)
            if rel.startswith(SKIP) or rel.split(os.sep)[0].endswith(
                    SKIP_SUFFIX):
                continue
            if runner._diverged(sub):
                alarm.append(rel)
            else:
                quiet += 1
    print("\n%d run directories of the plugin, and none gives an alarm"
          % quiet if not alarm else
          "\n%d run directories, and these gave a FALSE alarm: %s"
          % (quiet + len(alarm), alarm))
    bad += len(alarm)
    if quiet < 20:
        print("only %d directories: run the rigs first" % quiet)
    kept = os.path.join(HERE, LATE)
    if not os.path.isdir(kept):
        print("%s is not on the disk: the case of B42 cannot be tested" % LATE)
        return bad + 1
    got = runner._diverged(kept)
    ok = bool(got) and got[1] == "growth"
    bad += not ok
    print("%s gives %s %s" % (LATE, got, "OK" if ok else "FAILED"))
    return bad


def main():
    print("GROWTH_LIMIT = %g over %d segments\n"
          % (runner.GROWTH_LIMIT, runner.GROWTH_SEGMENTS))
    tmp = os.path.join(HERE, "out_growth_guard_tmp")
    bad = check_made(tmp) + check_disk()
    if bad:
        raise SystemExit("\nFAIL: %d check(s)" % bad)
    print("\nPASS")


if __name__ == "__main__":
    main()

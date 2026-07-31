"""Round-trip test: write_touchstone writes, then skrf reads the same
S-matrix.

The test guards the sequence of the columns of a 2-port file
(S11 S21 S12 S22) and the row-major layout of a file with N ports. Run it
with any python that has numpy and skrf:  python test_touchstone.py
"""
import os
import sys
import tempfile

import numpy as np
import skrf

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))
from runner import write_touchstone

rng = np.random.default_rng(0)
freq = np.linspace(1e9, 2e9, 5)
for n in (1, 2, 3, 4, 5):
    S = (rng.standard_normal((len(freq), n, n))
         + 1j * rng.standard_normal((len(freq), n, n)))
    path = os.path.join(tempfile.gettempdir(), "rt.s%dp" % n)
    write_touchstone(path, freq, S, 50.0)
    net = skrf.Network(path)
    assert net.s.shape == S.shape, (n, net.s.shape, S.shape)
    assert np.allclose(net.s, S, atol=1e-6), "S mismatch at n=%d" % n
    print("n=%d OK" % n)
print("all round-trips OK")

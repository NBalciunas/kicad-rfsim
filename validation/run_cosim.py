"""Measure a lumped element as a PORT, and add its value after the run.

**A lumped inductor in the grid costs run time, and no other part does.**
`solverenv.time_step_factor` sets the timestep to 0.5/sqrt(L[nH]) of the
Courant step. Thus 90 uH divides it by 600, and the run must have 600 times
more steps for the same simulated time. The rule is not too careful.
`run_stability.py` measures the boundary, and 90 uH diverges also at 0.12,
which is 70 times the factor that the rule gives.

**A port in the box of the element removes that cost.** The grid then has
no inductance. Thus the run keeps the FULL Courant step for all values of
the part. The value goes into the result after the run, with a circuit
formula on the S-matrix:

    a_i = G b_i                        the load on each element port
    S_red = S_ee + S_ei (I - G S_ii)^-1 G S_ie

where `e` are the ports of the board, `i` are the element ports, and `G` is
the diagonal of the reflection of each load, `G = (Z - z0) / (Z + z0)` with
`Z(f) = R + jwL + 1/jwC`.

It costs ONE more run for each element, because each port must have its own
excitation. Thus a board with 2 ports and 1 inductor runs 3 times at the
full step, against 2 runs at 1/600 of it.

This file measures these things:

- in four cases, the agreement of the two methods;
- in a fifth case (`topology`), the cost of the series path of openEMS for
  a part;
- in a sixth case (`body`), what the run gets when it does not include a
  body that changes nothing.

The first four cases are:

- `r50` puts a 50 ohm resistor in the box. A resistor costs no timestep,
  thus the grid run is cheap. The load is then equal to the reference
  impedance: `G` = 0, and the reduction gives the block of the board back.
  **It tests the PORT and not the mathematics.** The box, the axis, the
  reference plane and the mesh must all be correct. If not, the two curves
  are different.
- `l10` puts the 10 nH inductor of `run_rlc.py` in the box. `G` is not 0
  and it turns with frequency. Thus this case tests the reduction itself.
- `precision` gives the dimension of the error as a number. A RESISTANCE
  moves |G| from 0 to 0.9, and the grid run stays possible at each value.
  Thus the row measures the error against one value. That value makes `r50`
  different from `l10`: the part of the wave that comes back out of the
  element port.
- `sweep` runs ONE co-simulation, and it gives the S-parameters of 6 values
  of L from it, with no new run. It measures the cost of the same 6 values
  in the grid, and it does not run them.

**The results of the runs of 2026-09-22.** The first four results are
correct. The fifth result is the cause that this is not a feature at this
time.

1. **The port does not move the mesh.** The two methods give the same lines
   on each axis. When the two resistors have the same topology, the probe
   data of the two runs has the SAME bits. Thus the port, its box, its axis
   and its reference plane add nothing of themselves.
2. **The timestep cost goes away.** 10 nH costs 6.3 times the steps in the
   grid, and nothing as a port. The `sweep` case gives 6 values of L, up to
   90 uH and 600 times, from ONE run of 14 seconds.
3. **A resistive load is accurate.** `case_r50` holds the two curves
   together to 0.09 dB in all the sweep. The 0.22 dB that stays on S11 and
   S22 is the LEtype difference of openEMS: refer to `TOL_DB`.
4. **The error follows only |G|**, and `precision` measures it at the
   coarse preset. The grid run must have the factor in brackets, and it
   gets to NaN without it. The co-simulated column of each row came from
   ONE run at the full step:

   | R (ohm)  |   50 |  100 |      200 |      500 |     1000 |
   |----------|------|------|----------|----------|----------|
   | \\|G\\|    | 0.00 | 0.33 |     0.60 |     0.82 |     0.90 |
   | steps    | 2231 | 2231 | 4370 (.5)| 8778 (.2)| 8778 (.2)|
   | worst dS | 0.22 | 0.58 |     0.98 |     1.34 |     1.77 |

   0.22 dB at z0 is the LEtype floor above, and not the method. The
   other values are in proportion to |G|. A load that sends nothing back
   cannot send an error of the element port into the answer. A load that
   sends all of it back sends all the error. A reactance is the end of
   that row.

5. **A REACTIVE load is not accurate at this time.** One inductance gives
   most of the difference: the element in the grid has 7% less than the
   circuit has. But about 4 dB stays near the null of S21, and 1.5 dB on
   S11 at the top of the sweep. **The medium preset does not move it**
   (-7.6% and 4.2 dB at coarse, -7.0% and 4.1 dB at medium). More cells in
   the box along the current also do not move it (4.3, 5.7 and 5.0 dB at 1,
   2 and 3 cells, on 2026-09-23). The column of the element port gives out
   1.096 of the power that it gets at 6 GHz. A reactance sends all of that
   error back into the answer, and a load of z0 sends back nothing.

   **The cause is what a lumped element IS here.** openEMS puts an
   element on all the cell edges of its box, and the box is as wide as
   the land. Thus the element is many small elements in parallel. The
   part of the current in each one moves with the value. Its effect is
   then not a bilinear function of its value, and a port, which has two
   terminals, cannot replace it. A fit with three resistors in the grid
   as standards gives other resistors back to 0.02 dB. But it gives
   other capacitors with an error of up to 1.4 dB, for the same cause.

Run it with the python of the solver, which `run_stability.py` names:

    "C:\\openEMS\\venv\\Scripts\\python.exe" run_cosim.py [coarse|medium]

It uses `out_rlc_L1_<preset>/model.json`. Thus run `run_rlc.py <preset>`
first if that directory is missing.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import solverenv  # noqa: E402

# The board of `run_rlc.py`: one element that bridges a gap of 0.5 mm in a
# microstrip of 50 ohm. The preset comes from the command line, because the
# two methods must agree on MORE THAN ONE mesh: refer to `case_l10`.
PRESET = "coarse"
OUT = os.path.join(HERE, "out_cosim")


def src():
    """Give the model.json of the board, for the preset in use."""
    return os.path.join(HERE, "out_rlc_L1_%s" % PRESET, "model.json")


# The S-parameters of the two methods must agree to this, in dB on the
# magnitude of each term of the matrix. The two runs use the SAME mesh and
# the SAME engine. Thus only the port and the reduction make the
# difference. This test does not compare against a closed formula. Such a
# test must have a much larger limit.
#
# **0.3 dB is the floor of the test and NOT the error of the method.** The
# two methods build the same resistor with two different paths of openEMS.
# `build()` gives an element `LEtype=1`, which is the series extension.
# `AddLumpedPort` gives its own resistor the topology of the default, which
# is the parallel one. On 2026-09-22, that difference moved the voltage
# probes by 0.9% of their peak, and the S-parameters by 0.22 dB. **When the
# two topologies are the SAME, the probe data of the two runs has the same
# bits.** Thus the port, its box, its axis and its reference plane add
# nothing. This also makes "one component is the same with the two
# topologies" more accurate: the same to about 0.2 dB, and not the same
# number.
TOL_DB = 0.3
# The test does not compare a term that is below this in THE TWO matrices.
# The gap of the element makes a deep null in S21. A small change of the
# inductance moves that null by more than 20 dB at its floor. The curves
# stay together at all other frequencies. `case_l10` measures the position
# of the null, which is the number that the null gives.
FLOOR_DB = -30.0
# The `precision` case: (the resistance in ohm, the timestep factor of
# the GRID run). A resistance moves |G| from 0 at z0 to 0.9 at 1000 ohm.
# Thus the row goes up to the reactance of an inductor, which gives
# |G| = 1 at all frequencies.
#
# **A large lumped RESISTANCE also must have a smaller timestep.** On
# 2026-09-22, the plugin did not know it, because
# `solverenv.time_step_factor` counted only inductance. At this time,
# `solverenv.series_r_factor` holds that rule (B53). A measurement on this
# board on 2026-09-22, at the full Courant step, gave these results. 10,
# 50, 75 and 100 ohm are stable, and 150, 200, 500 and 1000 ohm all get to
# NaN. 200 ohm is stable at 0.5, and 500 and 1000 ohm at 0.2. Before this
# column, the 1000 ohm run used 9.5 minutes and 300000 steps to get to NaN.
# A co-simulated run does not have this problem. The grid has no element,
# thus an inductance and a resistance cannot set its timestep.
LAST_STEPS = 0
PRECISION_R = ((50.0, None), (100.0, None), (200.0, 0.5), (500.0, 0.2),
               (1000.0, 0.2))
# The `body` case: two runs of the SAME element must agree to this, in dB
# on S21. They do not agree to the bit. openEMS tests its end criteria at
# intervals of wall time, thus the same model can stop some tens of steps
# apart. 2323 and 2369 steps gave 0.0018 dB, and 2369 and 2392 gave
# 0.0035 dB, on one thread on 2026-09-24. 0.01 dB is three times that, and
# a run on the series path is 2 dB or more away.
BODY_NOISE_DB = 0.01
# The values of the `sweep` case, in nH.
SWEEP_NH = (1.0, 10.0, 100.0, 1000.0, 10000.0, 90000.0)


def variant(kind, value, ports=False, parasitics=False, tsf=None):
    """Give the model of one run.

    `kind` is "R" or "L", and `value` is in SI. With `ports`, the element
    becomes a port. The run then excites the ports of the board AND that
    port, because the reduction uses ALL the matrix. `tsf` gives the grid
    run a timestep factor of its own, which a large resistance must have:
    refer to `PRECISION_R`.
    """
    with open(src()) as fh:
        model = json.load(fh)
    e = model["lumped_elements"][0]
    e.update(type=kind, value=value, esl=0.0, esr=0.0, package="Custom")
    n = len(model["ports"])
    model["settings"].update(
        parasitics=parasitics, lumped=True,
        excite=list(range(1, n + 1 + (1 if ports else 0))))
    if ports:
        model["settings"]["lumped_ports"] = [e["ref"]]
    if tsf:
        model["settings"]["time_step_factor"] = tsf
    return model


def null_of(freq, s21):
    """Give (the frequency of the deepest point of |S21|, its depth in dB).

    The gap of the element has a capacitance. Thus a series L across it
    makes a PARALLEL resonance, and |S21| has a deep null. The position of
    that null follows 1/sqrt(L*C). Thus it measures the inductance of the
    run. It is much more sensitive than the magnitude at other frequencies.
    The two methods can have a large difference in dB at this one number,
    and the curves can stay together.
    """
    i = int(np.argmin(np.abs(s21)))
    return freq[i], 20 * np.log10(max(abs(s21[i]), 1e-12))


def fit_value(freq, direct, full, l_h, span=0.4, steps=401):
    """Give the L that makes the reduction agree best with `direct`.

    **The two methods must NOT agree at the same value. That is the result,
    and not a fault.** The circuit has the accurate L at the plane of the
    port. The element in the grid has the value that openEMS scales across
    the node lines of its own box, which is not the same number.
    `run_epc.py` measures +19% for two boxes that touch, and this rig
    measures the error of ONE box.

    Thus the test has two parts. This function finds the value of the GRID
    run: it compares the reduction with the grid run at many values. The
    caller then compares the two curves AT that value. When a method is
    correct, no difference stays after the value. One number then gives all
    the difference in all the sweep.
    """
    best = (None, 1e9)
    for l_try in np.linspace((1 - span) * l_h, (1 + span) * l_h, steps):
        z = impedance(freq, l=l_try)
        red = reduce_ports(full, ((z - 50.0) / (z + 50.0))[:, None])
        a = np.maximum(20 * np.log10(np.maximum(np.abs(direct), 1e-12)),
                       FLOOR_DB)
        b = np.maximum(20 * np.log10(np.maximum(np.abs(red), 1e-12)),
                       FLOOR_DB)
        rms = float(np.sqrt(np.mean((a - b) ** 2)))
        if rms < best[1]:
            best = (l_try, rms)
    return best


def solve(model, name):
    """Run one model. Give (the path of the Touchstone file, the mesh).

    The mesh comes back with the result, because the two methods must use
    the SAME grid. If you compare two different meshes, you measure the
    mesh and not the port.
    """
    tmp = os.path.join(OUT, name)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh)
    py = solverenv.solver_python() or sys.executable
    p = subprocess.run([py, os.path.join(PLUGINS, "runner.py"), path, tmp],
                       capture_output=True)
    txt = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        # The console of Windows is cp1252, and openEMS writes characters
        # that it cannot encode. Thus a bare print of the log gives a
        # UnicodeEncodeError, and it hides the true cause of the failure.
        print(txt[-2000:].encode("ascii", "replace").decode("ascii"))
        raise SystemExit("the run of %s failed" % name)
    mesh = re.search(r"mesh: (\d+ x \d+ x \d+) lines", txt)
    steps = re.search(r"Time for (\d+) iterations", txt)
    globals()["LAST_STEPS"] = int(steps.group(1)) if steps else 0
    found = sorted(f for f in os.listdir(tmp)
                   if re.match(r"results\.s\d+p$", f))
    if not found:
        raise SystemExit("the run of %s wrote no Touchstone file" % name)
    return os.path.join(tmp, found[0]), (mesh.group(1) if mesh else "?")


def read_touchstone(path):
    """Give (the frequencies in Hz, S with the shape (f, n, n)).

    `runner.write_touchstone` writes v1 in RI. Thus one record has 1 +
    2*n*n numbers, for all counts of the lines of that record. The port
    count comes from the name of the file.

    **A 2-port file is NOT in the same sequence as the others.** Touchstone
    v1 writes 2 ports as S11 S21 S12 S22, which is by COLUMN, and 3 ports
    or more by ROW. A reader that reads the 2-port file by row gives the
    TRANSPOSE: it reads S21 where the file has S12. The structure of this
    rig is almost reciprocal. Thus such a reader gives numbers that look
    correct, and it cost an hour on 2026-09-22. The two measured columns of
    one run do NOT fully agree. The two ports of the board cap the length
    of their line against different copper runs. Thus their reference
    planes are at different positions, and S21 and S12 are about 0.15 dB
    apart.
    """
    n = int(re.search(r"\.s(\d+)p$", path).group(1))
    nums = []
    with open(path) as fh:
        for line in fh:
            line = line.split("!")[0]
            if line.startswith("#") or not line.strip():
                continue
            nums += [float(v) for v in line.split()]
    rec = 1 + 2 * n * n
    if len(nums) % rec:
        raise SystemExit("%s does not hold whole records" % path)
    a = np.array(nums).reshape(-1, rec)
    v = a[:, 1:].reshape(-1, n * n, 2)
    z = v[..., 0] + 1j * v[..., 1]
    if n == 2:
        s = np.empty((len(a), 2, 2), dtype=complex)
        s[:, 0, 0], s[:, 1, 0] = z[:, 0], z[:, 1]
        s[:, 0, 1], s[:, 1, 1] = z[:, 2], z[:, 3]
    else:
        s = z.reshape(-1, n, n)
    return a[:, 0], s


def reduce_ports(S, gamma):
    """Put a load on the LAST ports of `S`, and give the block that stays.

    `gamma` has the shape (f, m): the reflection of the load on each of the
    m ports that go away. The ports of the board are the first n - m, and
    they keep their numbers.
    """
    m = gamma.shape[1]
    e = S.shape[1] - m
    see, sei = S[:, :e, :e], S[:, :e, e:]
    sie, sii = S[:, e:, :e], S[:, e:, e:]
    g = np.zeros((S.shape[0], m, m), dtype=complex)
    for k in range(m):
        g[:, k, k] = gamma[:, k]
    eye = np.eye(m)[None, :, :]
    return see + sei @ np.linalg.solve(eye - g @ sii, g @ sie)


def impedance(freq, r=0.0, l=0.0, c=0.0):
    """Give Z(f) of a series R, L and C. A value of 0 is not in the series."""
    w = 2 * np.pi * freq
    z = np.full(len(freq), r, dtype=complex)
    if l:
        z = z + 1j * w * l
    if c:
        z = z - 1j / (w * c)
    return z


def compare(name, freq, direct, cosim):
    """Print the largest difference of the two matrices. Give True if the
    test is satisfactory.

    The test compares the MAGNITUDE in dB of each term, because a user
    reads that from the plots. A term that is very small in the two
    matrices is not important, and the floor keeps it out of the maximum.
    """
    a = np.maximum(20 * np.log10(np.maximum(np.abs(direct), 1e-12)),
                   FLOOR_DB)
    b = np.maximum(20 * np.log10(np.maximum(np.abs(cosim), 1e-12)),
                   FLOOR_DB)
    # **The floor sets a minimum value, and it does not remove terms.** A
    # term below the floor is a path with no power. Thus -28 dB and -57 dB
    # give the same data about the board, and their difference of 29 dB
    # gives no data. If the code removes terms, it compares the pair when
    # ONE of the two is above the floor. That occurs at the two sides of a
    # null.
    d = np.abs(a - b)
    n = direct.shape[1]
    ok = True
    for j in range(n):
        for k in range(n):
            i = int(np.argmax(d[:, j, k]))
            bad = d[i, j, k] > TOL_DB
            ok = ok and not bad
            print("    S%d%d  direct %8.3f dB   co-sim %8.3f dB   "
                  "worst %6.3f dB at %.3f GHz%s"
                  % (j + 1, k + 1, a[i, j, k], b[i, j, k], d[i, j, k],
                     freq[i] / 1e9, "   FAIL" if bad else ""))
    print("  %s: the largest difference over %g dB is %.3f dB, the limit "
          "is %.1f dB" % ("PASS" if ok else "FAIL", FLOOR_DB, d.max(),
                          TOL_DB))
    return ok


def case_r50():
    """A 50 ohm resistor: the load is equal to the reference impedance.

    This isolates the PORT. A resistor of z0 gives `G` = 0. Thus the
    reduction gives the block of the board with no mathematics. Each
    difference that stays comes from the box of the port, its axis, its
    reference plane, the mesh or the topology of the resistor. The topology
    is the one that stays: refer to `TOL_DB`.
    """
    print("\n=== a 50 ohm resistor: the port alone ===")
    d_path, d_mesh = solve(variant("R", 50.0), "r50_direct")
    c_path, c_mesh = solve(variant("R", 50.0, ports=True), "r50_cosim")
    freq, direct = read_touchstone(d_path)
    _, full = read_touchstone(c_path)
    print("  mesh: direct %s, co-sim %s%s"
          % (d_mesh, c_mesh, "" if d_mesh == c_mesh else "   THE MESH MOVED"))
    z = impedance(freq, r=50.0)
    gamma = ((z - 50.0) / (z + 50.0))[:, None]
    return compare("r50", freq, direct, reduce_ports(full, gamma)) \
        and d_mesh == c_mesh


def case_l10():
    """The 10 nH inductor of `run_rlc.py`: the reduction itself.

    The load turns with frequency here, thus `G` is not 0, and all the
    formula gives the answer. The grid run pays the timestep of the rule;
    the co-simulated run does not.
    """
    print("\n=== a 10 nH inductor: the reduction ===")
    l_h = 10e-9
    print("  the rule gives the direct run a factor of %.4f, thus %.0f "
          "times the steps" % (solverenv.time_step_factor(10.0),
                               1.0 / solverenv.time_step_factor(10.0)))
    d_path, d_mesh = solve(variant("L", l_h), "l10_direct")
    c_path, c_mesh = solve(variant("L", l_h, ports=True), "l10_cosim")
    freq, direct = read_touchstone(d_path)
    _, full = read_touchstone(c_path)
    print("  mesh: direct %s, co-sim %s%s"
          % (d_mesh, c_mesh, "" if d_mesh == c_mesh else "   THE MESH MOVED"))
    # The value of the GRID run, and then the two curves AT that value:
    # refer to `fit_value`.
    l_fit, rms = fit_value(freq, direct, full, l_h)
    print("  the element in the grid carries %.3f nH where the circuit "
          "carries %.3f nH: %+.1f%%"
          % (1e9 * l_fit, 1e9 * l_h, 100.0 * (l_fit / l_h - 1.0)))
    z = impedance(freq, l=l_fit)
    gamma = ((z - 50.0) / (z + 50.0))[:, None]
    cosim = reduce_ports(full, gamma)
    ok = compare("l10", freq, direct, cosim) and d_mesh == c_mesh

    # **The null gives the true value of the inductance.** The gap of the
    # element has a capacitance, thus the L across it makes a PARALLEL
    # resonance. The position of that resonance follows 1/sqrt(L*C). The
    # geometry gives the same C to the two methods. Thus the two null
    # frequencies give the ratio of the two inductances. 1% in L moves the
    # null by more than 20 dB at its floor, and by nothing at other
    # frequencies. Thus the null is the sensitive test. A limit in dB on
    # the null itself measures only its depth.
    fd, dd = null_of(freq, direct[:, 1, 0])
    fc, dc = null_of(freq, cosim[:, 1, 0])
    print("  the null of S21: direct %.3f GHz (%.1f dB), co-sim %.3f GHz "
          "(%.1f dB) at the value above" % (fd / 1e9, dd, fc / 1e9, dc))

    # **Tell WHY the other part of the difference stays.** A load of
    # z0 sends nothing back into the board. Thus the reduction is accurate
    # for all the values of the matrix, and `case_r50` is satisfactory. A
    # REACTANCE sends back all of it. Thus each error of the column of the
    # element port goes into the answer. A passive board cannot give out
    # more power than it gets. Thus a column above 1.0 measures that error.
    power = (np.abs(full) ** 2).sum(axis=1)[:, -1]
    i = int(np.argmax(power))
    print("  the element port gives out %.3f of the power that it takes in, "
          "at %.3f GHz" % (power[i], freq[i] / 1e9))
    if power[i] > 1.02:
        print("  **This is the limit of the method, and no mesh moves "
              "it**: a finer preset and more cells in the box both leave "
              "it. openEMS spreads an element over every cell edge of its "
              "box, across the width of the land, thus it is many elements "
              "in parallel and not a two-terminal part, and a port cannot "
              "stand in for it. The co-simulated inductor reads about 14% "
              "high against the closed form of run_rlc.py, where the "
              "element in the grid reads about 8% low.")
    return ok


def case_sweep():
    """ONE run, and then the S-parameters of 6 values of L from it.

    The co-simulated run knows nothing about the value of the part. Thus a
    new value costs a reduction and no run. The table gives the cost of the
    same values in the grid.
    """
    print("\n=== one run, 6 values of L ===")
    c_path, mesh = solve(variant("L", 10e-9, ports=True), "sweep_cosim")
    freq, full = read_touchstone(c_path)
    i = int(np.argmin(np.abs(freq - 3e9)))    # the middle of the sweep
    print("  mesh %s, %d frequencies; S21 at %.2f GHz:\n" % (
        mesh, len(freq), freq[i] / 1e9))
    print("  %10s %12s %12s %14s" % ("L (nH)", "|S21| (dB)", "|S11| (dB)",
                                     "in the grid"))
    for nh in SWEEP_NH:
        z = impedance(freq, l=nh * 1e-9)
        red = reduce_ports(full, ((z - 50.0) / (z + 50.0))[:, None])
        cost = 1.0 / solverenv.time_step_factor(nh)
        print("  %10g %12.2f %12.2f %11.0fx more steps"
              % (nh, 20 * np.log10(abs(red[i, 1, 0])),
                 20 * np.log10(abs(red[i, 0, 0])), cost))
    print("\n  Every row above comes from the SAME run, which took the full "
          "Courant step.")
    return True


def worst_db(direct, cosim):
    """Give the largest difference of two matrices, in dB. The floor of
    `FLOOR_DB` sets a minimum on the two: refer to `compare`."""
    a = np.maximum(20 * np.log10(np.maximum(np.abs(direct), 1e-12)),
                   FLOOR_DB)
    b = np.maximum(20 * np.log10(np.maximum(np.abs(cosim), 1e-12)),
                   FLOOR_DB)
    return float(np.abs(a - b).max())


def case_precision():
    """Measure how near the co-simulation is to the run that puts the
    element in the grid, as the load sends back more and more power.

    **A RESISTOR is the tool of this case.** A resistor costs the grid run
    no timestep. Thus the run that puts it in the grid is cheap at each
    value, and it gives a reference at each one. Also, the value of a
    resistor moves `G` from 0 at z0 to almost 1 at a value far from z0.
    Thus the row of R measures the error against ONE value. That value
    makes `case_r50` different from `case_l10`: the part of the wave that
    comes back out of the element port.

    An inductor is at the end of that row: a reactance gives |G| = 1 at all
    frequencies.

    The closed formula of `run_rlc.py` is adjacent to the two, for a series
    impedance Z between two lines of z0:

        S21 = 2*z0 / (2*z0 + Z)

    It is the theory of the ELEMENT and not of the board, thus it has no
    pad and no gap. The two methods are about 0.5 dB from it on this board
    at the coarse preset. It shows that the two methods stay TOGETHER when
    they go away from it.
    """
    print("\n=== precision against the run that puts the element in the "
          "grid ===")
    c_path, c_mesh = solve(variant("R", 50.0, ports=True), "prec_cosim")
    freq, full = read_touchstone(c_path)
    i = int(np.argmin(np.abs(freq - 3e9)))
    print("  ONE co-simulated run (%s). Each row below also runs the "
          "element in the grid.\n" % c_mesh)
    print("  %8s %6s %7s %8s %9s %9s %9s"
          % ("R (ohm)", "|G|", "factor", "steps", "worst dS", "S21 grid",
             "S21 theory"))
    ok = True
    for r, tsf in PRECISION_R:
        d_path, d_mesh = solve(variant("R", r, tsf=tsf), "prec_r%g" % r)
        steps = LAST_STEPS
        _, direct = read_touchstone(d_path)
        z = impedance(freq, r=r)
        gamma = (z - 50.0) / (z + 50.0)
        cosim = reduce_ports(full, gamma[:, None])
        d = worst_db(direct, cosim)
        ok = ok and d <= TOL_DB and d_mesh == c_mesh
        th = 2 * 50.0 / (2 * 50.0 + z)
        print("  %8g %6.3f %7s %8d %6.3f dB %6.2f dB %6.2f dB%s"
              % (r, abs(gamma[i]), tsf or "1.0", steps, d,
                 20 * np.log10(abs(direct[i, 1, 0])),
                 20 * np.log10(abs(th[i])),
                 "" if d <= TOL_DB else "   over the limit"))
    print("\n  The error follows |G| and nothing else: the reduction is "
          "exact at z0,\n  where the load sends nothing back, and every "
          "error of the column of\n  the element port enters the answer "
          "in proportion to what comes back.\n  A reactance stands at the "
          "end of the row with |G| = 1 at every\n  frequency, thus `l10` "
          "is the hardest case and not a different one.")
    print("  %s: every resistive load agrees to %.1f dB or better"
          % ("PASS" if ok else "FAIL", TOL_DB))
    return ok


def case_topology():
    """The cost of the SERIES path for a part that is not an open.

    openEMS has `LEtype` = 1 in an extension that replaces the E field of
    the box with the branch. Thus it also cuts the in-plane displacement
    current of the gap. The classic path keeps it. Each pair runs the SAME
    resistor with the two paths:

    - only the resistor, which uses the classic path from B53;
    - the resistor with an ESL of 1e-18 H. That is two components, thus the
      series path, but it is no inductance at 6 GHz.

    Adjacent to each pair is the effect of the ESL of an 0603 body (0.5 nH)
    on S21 at the top of the sweep. It comes from the closed formula of a
    series part between two lines of z0. **Where the second column is much
    more than the third, the parasitic costs more than it models.** The
    series path also gets its timestep factor (the last column).
    """
    print("\n=== what the series path costs a part ===")
    print("  %8s %6s %14s %16s %12s"
          % ("R (ohm)", "|Z|/z0", "series path", "an 0603 ESL", "its factor"))
    f_top = 6e9
    for r in (50.0, 200.0, 1000.0):
        c_path, c_mesh = solve(variant("R", r), "topo_c%g" % r)
        m = variant("R", r, parasitics=True)
        m["lumped_elements"][0]["esl"] = 1e-18
        # 1e-18 H moves |Z| by nothing. Thus the run removes it and puts
        # the resistor on the classic path again.
        m["settings"]["keep_idle_body"] = True
        s_path, s_mesh = solve(m, "topo_s%g" % r)
        freq, classic = read_touchstone(c_path)
        _, series = read_touchstone(s_path)
        cost = np.abs(20 * np.log10(np.abs(series[:, 1, 0]))
                      - 20 * np.log10(np.abs(classic[:, 1, 0]))).max()
        w = 2 * np.pi * f_top
        esl = abs(20 * np.log10(abs(100.0 / (100.0 + r + 1j * w * 0.5e-9)))
                  - 20 * np.log10(100.0 / (100.0 + r)))
        tsf = min(solverenv.time_step_factor(0.5),
                  solverenv.series_r_factor(r))
        print("  %8g %6.0f %11.2f dB %13.4f dB %11.3f%s"
              % (r, r / 50.0, cost, esl, tsf,
                 "" if c_mesh == s_mesh else "   THE MESH MOVED"))
    print("\n  A resistor over about 100 ohm pays far more for its ESL than "
          "the ESL does.")
    return True


def case_body():
    """P21: a body that does not change its part stays out of the grid.

    Each resistor has the ESL of an 0603 body (0.5 nH), with the parasitics
    on, which is the default of a board. The run of today removes that body
    where it moves |Z| by 2% or less. Thus the part is one R and uses the
    classic path. Its S-parameters must be the same as the S-parameters of
    the same resistor with NO body, to the bit. The same model with
    `keep_idle_body` gives the run of before P21, on the series path.
    50 ohm keeps its body (the ESL moves |Z| by 4.4% at 6 GHz). Thus its
    two runs must be the same run.
    """
    print("\n=== P21: the body that changes nothing ===")
    print("  %8s %10s %14s %14s %14s"
          % ("R (ohm)", "the body", "against no body", "before P21",
             "the steps"))
    ok = True
    for r in (50.0, 200.0, 1000.0):
        # **ONE thread.** With 4 threads, the same model ended two times at
        # 2336 and at 2272 steps, and it read 0.16 dB apart. One thread
        # also stops some tens of steps apart, because openEMS tests its
        # end criteria at intervals of wall time. That is `BODY_NOISE_DB`.
        bare = variant("R", r)
        bare["settings"]["threads"] = 1
        bare_path, _ = solve(bare, "body_bare%g" % r)
        m = variant("R", r, parasitics=True)
        m["settings"]["threads"] = 1
        m["lumped_elements"][0].update(esl=0.5e-9, package="0603")
        now_path, now_mesh = solve(m, "body_now%g" % r)
        now_steps = LAST_STEPS
        m["settings"]["keep_idle_body"] = True
        old_path, old_mesh = solve(m, "body_old%g" % r)
        old_steps = LAST_STEPS
        _, bare = read_touchstone(bare_path)
        _, now = read_touchstone(now_path)
        _, old = read_touchstone(old_path)

        def db(s):
            return 20 * np.log10(np.abs(s[:, 1, 0]))
        to_bare = np.abs(db(now) - db(bare)).max()
        to_old = np.abs(db(now) - db(old)).max()
        kept = r < 94.0
        # A body that stays gives the run of before; a body that goes out
        # gives the bare resistor.
        good = (to_old if kept else to_bare) < BODY_NOISE_DB
        ok = ok and good and now_mesh == old_mesh
        print("  %8g %10s %11.4f dB %11.2f dB %7d / %d%s"
              % (r, "kept" if kept else "left out", to_bare, to_old,
                 now_steps, old_steps, "" if good else "   FAIL"))
    print("  %s: a body that changes nothing gives the bare part, and a "
          "body that changes it gives the run of before"
          % ("PASS" if ok else "FAIL"))
    return ok


CASES = {"r50": case_r50, "l10": case_l10, "sweep": case_sweep,
         "precision": case_precision, "topology": case_topology,
         "body": case_body}


def main(*names):
    global PRESET
    names = list(names)
    if names and names[0] in ("coarse", "medium", "fine"):
        PRESET = names.pop(0)
    if not os.path.isfile(src()):
        raise SystemExit("run `run_rlc.py %s` first: this file needs its "
                         "model.json" % PRESET)
    print("the board of run_rlc.py at the %s preset" % PRESET)
    names = names or ["r50", "precision", "l10", "sweep"]
    ok = True
    for name in names:
        if name not in CASES:
            raise SystemExit("unknown case %r; give one of %s"
                             % (name, ", ".join(CASES)))
        ok = CASES[name]() and ok
    print("\n%s" % ("PASS: every case that ran holds"
                    if ok else "FAIL: refer to the lines above"))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main(*sys.argv[1:])

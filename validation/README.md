# Validation

The `validation/` folder holds the UI tests, the board tests and the solver tests.
A solver test makes its own test board, runs openEMS or EMerge, and compares the result against closed-form theory.
The files are in three folders:

* `common\` - the UI tests, the board tests, the builders of the test boards, and the rigs of the shared solver tests (`rig_*.py`, `rigsolve.py`).
* `openems\` - the openEMS tests. Each file name ends in `_openems`.
* `emerge\` - the EMerge tests. Each file name ends in `_emerge`.

Run the files from the root of the repository with the Python of KiCad. Run the files marked "openEMS Python" with `C:\openEMS\venv\Scripts\python.exe`.
`[mesh]` is `coarse`, `medium` or `fine`, and `coarse` is the default.
Each run writes its results into an `out_*` folder adjacent to the file.

```bat
set KIPY="C:\Program Files\KiCad\10.0\bin\python.exe"
%KIPY% validation\openems\run_rlc_openems.py coarse
%KIPY% validation\emerge\run_rlc_emerge.py coarse
```

## UI tests (`common\`)

* **`test_dialog.py`**  
The settings dialog with no display: the rows of the parts and the settings that it gives back.
* **`test_views.py`**  
Each view of the results window, with no display. It uses `openems\out_coarse` from `run_headless_openems.py coarse`.
* **`capture_windows.py [outdir]`**  
Writes a PNG of each window of the GUI, with no display. It checks nothing: compare the pictures before and after a change to the layout.

## Board tests (`common\`)

* **`plugins\board_reader.py`**  
The self-test of the board reader: the value parser, the stackup, the geometry, the coaxial feed and the packages.
* **`diag_lumped.py board.kicad_pcb`**  
Shows why the plugin does not simulate an R/L/C part, test by test.
* **`test_touchstone.py`**  
The Touchstone writer. skrf must read back the same S-matrix, for 1 to 5 ports.
* **`make_test_board.py`, `make_lumped_board.py`**  
Make the test boards of the solver tests.
* **`make_port_picture.py`**  
Makes `docs\port-geometry.png`, the geometry of a lumped port.

## Shared solver tests

Each test runs on openEMS and on EMerge, with two files: `run_<rig>_openems.py` and `run_<rig>_emerge.py`.
The two files run the same board and the same checks.

* **`run_rlc_*.py [mesh] [R1|L1|C1]`**  
R, L and C in series between two lines. |S21| must stay flat for R, fall for L and rise for C.
* **`run_lumped_*.py [mesh]`**  
A series resistor of 50 Ω in a 50 Ω line. It must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB.
* **`run_shunt_*.py [mesh] [packages|two]`**  
A capacitor in shunt to ground. The notch in |S21| gives the inductance of its body. `packages` tests the 8 chip packages on their KiCad lands, and `two` puts two parts in series.
* **`run_cpw_*.py [mesh] [cpw|stripline]`**  
The CPW port and the stripline port against theory, for Z0 and eps_eff. The eps_eff of a stripline must be εr.
* **`run_atten_*.py [mesh]`**  
The loss of a line, from two lines of 20 mm and 100 mm. It must agree with the closed form within 15%.
* **`run_zone_holes_*.py [mesh]`**  
A zone with a void below the line, against the same board with no void. The void must make a large step in S11.
* **`run_feature_*.py [mesh] [stub width in mm]`**  
An open stub that no port covers. Its notch in |S21| gives eps_eff, which tests the mesh cells across a narrow feature.
* **`run_via_*.py [mesh]`**  
The inductance of one via against Goldfarb and Pucel, for four drill dimensions. It must agree within 25% in openEMS and 30% in EMerge. At coarse, openEMS reads a drill of 0.3 mm −16%: use a finer preset for such a via.
* **`run_probe_*.py [mesh] [all|single|dual]`**  
A patch fed from below through a plated hole (the coaxial feed port). One feed must give the resonance and the resistance of a probe from the plane to the patch. Two feeds on the same patch must give an isolation of 15 dB and an XPD of 15 dB or more.
* **`run_states_*.py [mesh]`**  
A series capacitor of 1, 0.5 and 2 pF in a 50 Ω line, as three states. The phase difference of S21 to the first state must agree with the closed form within 3°.
* **`run_epc_*.py [mesh]`**  
The self-resonance of an inductor on an 0402 land. The notch in |S21| must be at 1/(2π√(LC)).
* **`run_headless_*.py [mesh] [msl|lumped]`**  
The full path from the board to the Touchstone file. A microstrip of about 50 Ω must give S11 < −10 dB and S21 > −0.5 dB from 1 GHz to 6 GHz.

## openEMS tests (`openems\`)

These test the FDTD engine itself: the timestep, the mesh and the growth of a run.

* **`run_stability_openems.py [fast|slow|all]`**  
The timestep rule for a lumped inductor. `fast` measures the margin to divergence on 8 geometries. `slow` shows that a normal run ends before a mode that grows late.
* **`test_growth_openems.py`** (openEMS Python)  
The guard that refuses a run whose field grew. Three good traces must pass, and one that grows must be refused.
* **`test_ports_openems.py`** (openEMS Python)  
The boxes of the ports and the mesh lines, with no solver run. It takes seconds.
* **`mesh_diff_openems.py [revision]`** (openEMS Python)  
Lists each board whose mesh changes against a git revision (HEAD by default). Use it before a solver run to see which results a change can move.
* **`via_nodes_openems.py [-v] [revision]`** (openEMS Python)  
Counts the mesh nodes in the barrel of each via. A via with no node conducts nothing.
* **`run_le_cells_openems.py [ladder|matrix|all]`**  
The growth of a lumped inductor against the count of mesh cells in series in its box. The mesh of the plugin must give the same result as 1 cell. It uses `out_rlc_L1_coarse` from `run_rlc_openems.py coarse`.
* **`run_cosim_openems.py [mesh] [r50|l10|sweep|precision|topology|body]`** (openEMS Python)  
A port in the box of a lumped element, with the value of the element added after the run. It compares that result with the element in the mesh. It uses `out_rlc_L1_<mesh>` from `run_rlc_openems.py`.
* **`openems_le_growth.py [case f ns|table]`** (openEMS Python)  
A microstrip in openEMS only, with no KiCad and no code of the plugin. It finds the mode that grows late in a long run, and the PML faces that cause it.

## EMerge tests (`emerge\`)

* **`run_boards_emerge.py [mesh] [msl|series_r|shunt_c|cpw|stripline|patch]`**  
Six boards through EMerge: a microstrip, a series resistor of 50 Ω, a capacitor in shunt, a CPW, a stripline and a patch antenna for 2.4 GHz. No board can give out more power than it gets. The resistor must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB, the E-field of the microstrip 7.07 V across the substrate (0.5 W in 50 Ω), and the patch a dip in S11 near 2.4 GHz and 5 to 9 dBi at broadside.

# Validation

The `validation/` folder makes its own test boards, runs the solver and compares the result against closed-form theory. It has three folders:

* `validation\common\` - the tests that need no solver, the builders of the test boards, and the rigs that the two solvers share (`rig_*.py`).
* `validation\openems\` - the files that run openEMS. Each file name ends in `_openems`.
* `validation\emerge\` - the files that run EMerge. Each file name ends in `_emerge`.

Run the files from the root of the repository with the Python of KiCad, and the files marked "solver Python" with `C:\openEMS\venv\Scripts\python.exe`:

```bat
set KIPY="C:\Program Files\KiCad\10.0\bin\python.exe"
%KIPY% validation\openems\run_rlc_openems.py coarse
%KIPY% validation\emerge\run_rlc_emerge.py coarse
```

## No solver (`validation\common\`)

* **`test_dialog.py`**  
The settings dialog with no display: the rows of the parts and the settings that it gives back.
* **`test_views.py`**  
Each view of the results window, with no display. It needs `validation/openems/out_coarse` from `run_headless_openems.py coarse`.
* **`test_touchstone.py`**  
The Touchstone writer. skrf must read back the same S-matrix, for 1 to 5 ports.
* **`diag_lumped.py board.kicad_pcb`**  
Shows why the plugin does not simulate an R/L/C part, test by test.
* **`make_test_board.py`, `make_lumped_board.py`**  
Make the test boards of the rigs of the two solvers.

`%KIPY% plugins\board_reader.py` is the self-test of the value parser (67 cases).

## The two solvers

Each rig below has one file for each solver. The two files run the same board and the same checks (`validation\common\rig_*.py`), and write their results into the folder of their solver.

* **`run_rlc_openems.py [mesh] [R1|L1|C1]`**, **`run_rlc_emerge.py [mesh] [R1|L1|C1]`**  
R, L and C in series between two lines. |S21| must stay flat for R, fall for L and rise for C.
* **`run_lumped_openems.py [mesh]`**, **`run_lumped_emerge.py [mesh]`**  
A series resistor of 50 Ω in a 50 Ω line. It must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB.
* **`run_shunt_openems.py [mesh] [packages|two]`**, **`run_shunt_emerge.py [mesh] [packages|two]`**  
A capacitor in shunt to ground. The notch in |S21| gives the inductance of its body. `packages` tests the 8 chip packages on their KiCad lands, and `two` puts two parts in series.
* **`run_cpw_openems.py [mesh] [cpw|stripline]`**, **`run_cpw_emerge.py [mesh] [cpw|stripline]`**  
The CPW port and the stripline port against theory, for Z0 and eps_eff. The eps_eff of a stripline must be εr.
* **`run_atten_openems.py [mesh]`**, **`run_atten_emerge.py [mesh]`** (solver Python)  
The loss of a line, from two lines of 20 mm and 100 mm. It must agree with the closed form within 15%.
* **`run_zone_holes_openems.py [mesh]`**, **`run_zone_holes_emerge.py [mesh]`**  
A zone with a void below the line, against the same board with no void. The void must make a large step in S11.
* **`run_feature_openems.py [mesh] [stub width in mm]`**, **`run_feature_emerge.py [mesh] [stub width in mm]`** (solver Python)  
An open stub that no port covers, against theory. Its notch in |S21| gives eps_eff, which tests the mesh cells across a narrow feature.
* **`run_via_openems.py [mesh]`**, **`run_via_emerge.py [mesh]`** (solver Python)  
The inductance of one via against Goldfarb and Pucel, for four drill sizes. Each is within 20% in openEMS at the medium preset, and within 30% in EMerge. At coarse, openEMS reads a drill of 0.3 mm −16%: use a finer preset for such a via.
* **`run_probe_openems.py [mesh] [all|single|dual]`**, **`run_probe_emerge.py [mesh] [all|single|dual]`**  
A patch fed from below through a plated hole (the coaxial feed port). A small pad in a small clearance must give the resonance and the resistance of a probe from the plane to the patch. Two feeds on the same patch must give an isolation of 15 dB and an XPD of 15 dB or more.
* **`run_states_openems.py [mesh]`**, **`run_states_emerge.py [mesh]`**  
A series capacitor of 1, 0.5 and 2 pF in a 50 Ω line, as three states. The phase difference of S21 to the first state must agree with the closed form within 3°.
* **`run_epc_openems.py [mesh]`**, **`run_epc_emerge.py [mesh]`**  
The self-resonance of an inductor on an 0402 land, against 1/(2π√(LC)). The notch in |S21| must be at that frequency.
* **`run_headless_openems.py [mesh] [msl|lumped]`**, **`run_headless_emerge.py [mesh] [msl|lumped]`**  
The full path from the board to the Touchstone file. A microstrip of about 50 Ω must give S11 < −10 dB and S21 > −0.5 dB from 1 GHz to 6 GHz.

## openEMS only (`validation\openems\`)

These test the FDTD engine itself: the timestep, the mesh and the growth of a run.

* **`run_stability_openems.py [fast|slow|all]`**  
The timestep rule for a lumped inductor. `fast` measures the margin to divergence on 8 geometries, in about 40 minutes. `slow` shows that a normal run ends before a mode that grows late.
* **`test_ports_openems.py`** (solver Python)  
The boxes of the ports and the mesh lines, with no solver run. It takes seconds.
* **`mesh_diff_openems.py [revision]`** (solver Python)  
Lists each board whose mesh changes against a git revision (HEAD by default). Use it to see which results a change can move before you run the solver.
* **`via_nodes_openems.py [-v] [revision]`** (solver Python)  
Counts the mesh nodes in the barrel of each via. A via with no node conducts nothing. It takes a revision in the same way as `mesh_diff_openems.py`.
* **`test_growth_openems.py`**  
The guard that refuses a run whose field grew. Three good traces must pass, and one that grows must be refused.

## EMerge only (`validation\emerge\`)

* **`run_boards_emerge.py [mesh] [msl|series_r|shunt_c|cpw|stripline|patch]`**  
Six boards through EMerge: a microstrip, a series resistor of 50 Ω, a capacitor in shunt, a CPW, a stripline and a patch antenna for 2.4 GHz. No board may give out more power than it gets. The resistor must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB, the E-field of the microstrip 7.07 V across the substrate (0.5 W in 50 Ω), and the patch a dip in S11 near 2.4 GHz and 5 to 9 dBi at broadside.

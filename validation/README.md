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
The part rows of the settings dialog and the settings that it gives back, with no display.
* **`test_views.py`**  
Each view of the results window, with no display. It uses `openems\out_coarse` from `run_headless_openems.py coarse`.
* **`capture_windows.py [outdir]`**  
A PNG of each GUI window, with no display. It checks nothing, so compare the pictures before and after a layout change.

## Board tests (`common\`)

* **`plugins\board_reader.py`**  
The self-test of the value parser, stackup, geometry, coaxial feed and packages of the board reader.
* **`diag_lumped.py board.kicad_pcb`**  
Shows why the plugin does not simulate an R/L/C part, test by test.
* **`test_touchstone.py`**  
skrf must read back the S-matrix of the Touchstone writer, for 1 to 5 ports.
* **`make_test_board.py`, `make_lumped_board.py`**  
Make the test boards of the solver tests.
* **`make_port_picture.py`**  
Makes `docs\port-geometry.png`, a picture of a lumped port.

## Shared solver tests

Each test has one file for each solver, `run_<rig>_openems.py` and `run_<rig>_emerge.py`.
The two run the same board and checks.

* **`run_headless_*.py [mesh] [msl|lumped]`**  
The full path from a board to a Touchstone file. A 50 Ω microstrip must give S11 < −10 dB and S21 > −0.5 dB from 1 to 6 GHz.
* **`run_cpw_*.py [mesh] [cpw|stripline]`**  
Z0 and eps_eff of the CPW port and the stripline port, against theory. A stripline must give eps_eff = εr.
* **`run_atten_*.py [mesh]`**  
The loss of a line, from the difference of lines of 20 mm and 100 mm. It must be within 15% of the closed form.
* **`run_feature_*.py [mesh] [stub width in mm]`**  
An open stub that no port covers. Its |S21| notch gives eps_eff, which tests the mesh across a narrow feature.
* **`run_zone_holes_*.py [mesh]`**  
A zone with and without a void below the line. The void must make a large step in S11.
* **`run_via_*.py [mesh]`**  
The inductance of one via against Goldfarb and Pucel, for four drills. It must be within 25% in openEMS and 30% in EMerge. At coarse, openEMS reads a 0.3 mm drill −16%, so use a finer preset. Run `run_via_openems.py` with the openEMS Python.
* **`run_rlc_*.py [mesh] [R1|L1|C1]`**  
R, L and C in series between two lines. |S21| must stay flat for R, fall for L and rise for C.
* **`run_lumped_*.py [mesh]`**  
A series resistor of 50 Ω in a 50 Ω line. It must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB.
* **`run_shunt_*.py [mesh] [packages|two]`**  
A shunt capacitor. Its |S21| notch gives the inductance of its body, which must be within 15% of the ESL of the part. `packages` tests 8 chip packages on their KiCad lands, and `two` puts two parts in series.
* **`run_epc_*.py [mesh]`**  
The self-resonance of an inductor on an 0402 land. The |S21| notch must be at 1/(2π√(LC)).
* **`run_states_*.py [mesh]`**  
A series capacitor of 1, 0.5 and 2 pF as three states. The S21 phase difference to the first state must be within 3° of the closed form.
* **`run_probe_*.py [mesh] [all|single|dual]`**  
A patch fed from below through a plated hole (coaxial feed port). One feed must give the resonance within 6% of the closed form, and the resistance of a probe from the plane to the patch. Two feeds must give 15 dB or more of isolation and XPD.

## openEMS tests (`openems\`)

These test the timestep, the mesh and the growth of a run in the FDTD engine.

* **`run_stability_openems.py [fast|slow|all]`**  
The timestep rule of a lumped inductor. `fast` measures the margin to divergence on 8 geometries, and `slow` shows that a normal run ends before a mode that grows late.
* **`test_growth_openems.py`** (openEMS Python)  
The guard that stops a run when its field grows. Three good traces must pass, and one that grows must be refused.
* **`test_ports_openems.py`** (openEMS Python)  
Checks the port boxes and the mesh lines, with no solver run. It takes seconds.
* **`mesh_diff_openems.py [revision]`** (openEMS Python)  
Lists the boards with a different mesh from a git revision (HEAD by default), to see which results a change can move.
* **`via_nodes_openems.py [-v] [revision]`** (openEMS Python)  
Counts the mesh nodes in each via barrel. A via with no node conducts nothing.
* **`run_le_cells_openems.py [ladder|matrix|all]`**  
The growth of a lumped inductor against the count of mesh cells in series in its box. The plugin mesh must give the same result as 1 cell. It uses `out_rlc_L1_coarse` from `run_rlc_openems.py coarse`.
* **`run_cosim_openems.py [mesh] [r50|l10|sweep|precision|topology|body]`** (openEMS Python)  
Simulates a lumped element as a port and adds its value after the run. It compares that result with the element in the mesh. It uses `out_rlc_L1_<mesh>` from `run_rlc_openems.py`.
* **`openems_le_growth.py [case f ns|table]`** (openEMS Python)  
A microstrip in openEMS only, with no KiCad or plugin code. It finds the mode that grows late in a long run, and the PML faces that cause it.

## EMerge tests (`emerge\`)

* **`run_boards_emerge.py [mesh] [msl|series_r|shunt_c|cpw|stripline|patch]`**  
Six boards with a microstrip, a series resistor, a shunt capacitor, a CPW, a stripline and a patch for 2.4 GHz. No board can give out more power than it gets. The patch must give a dip in S11 near 2.4 GHz and 5 to 9 dBi at broadside.

## Results

All the tests pass on the current version, at the coarse preset if the row does not give a different preset.

| Test                 | openEMS                                                          | EMerge                                                   |
|----------------------|------------------------------------------------------------------|----------------------------------------------------------|
| `run_headless`       | S11 −11.0 dB or less, S21 −0.2 dB or more                        | S11 −26.9 dB or less, S21 −0.7 dB or more                |
| `run_cpw cpw`        | eps_eff +1%, Z0 −5%                                              | eps_eff −1%, Z0 −2%                                      |
| `run_cpw stripline`  | eps_eff +1%, Z0 +1%                                              | eps_eff 0%, Z0 +3%                                       |
| `run_atten`          | 0.97 times the closed form                                       | 1.04 times                                               |
| `run_feature`        | 0.4 mm stub +2.4%, 0.3 mm stub at medium −1.2%                   | 0.4 mm stub −0.2%                                        |
| `run_zone_holes`     | S11 step +7.5 dB                                                 | +22.2 dB                                                 |
| `run_via`            | −16%, +13%, +19%, +17%; medium +8% to +20%                       | +19% to +27%                                             |
| `run_rlc`            | R 62.4 Ω, L 12.3 nH, C 0.95 pF at 1.5 GHz                        | R 57.7 Ω, L 11.6 nH, C 0.95 pF at 1.5 GHz                |
| `run_lumped`         | S11 −10.4 dB, S21 −4.3 dB (medium)                               | S11 −9.4 dB, S21 −4.1 dB                                 |
| `run_shunt`          | ESL 0.25 nH +1.5%, 1 nH +3.1%                                    | −3.5%, −2.1%                                             |
| `run_shunt packages` | −4.6% to +0.5% on 8 packages                                     | −4.8% to +0.4%                                           |
| `run_shunt two`      | L 2 nH +3.4%, 1 nH +3.0%                                         | +2.4%, +2.9%                                             |
| `run_epc`            | +8.4%                                                            | −6.2%                                                    |
| `run_states`         | −1.6° to +2.4°                                                   | −2.5° to +1.4°                                           |
| `run_probe`          | patch −4.5% to −3.7%, S21 −19.6 dB, XPD 28.0 dB                  | patch −5.0% to −4.2%, S21 −20.5 dB, XPD 28.2 and 30.0 dB |
| `run_boards`         |                                                                  | 6 boards; the patch dip is at 2.370 GHz, 7.0 dBi         |
| `run_stability fast` | smallest margin 2.2x (limit 1.35x)                               |                                                          |
| `run_stability slow` | smallest margin 3.0x (limit 2.0x)                                |                                                          |
| `test_growth`        | no growth                                                        |                                                          |
| `run_le_cells`       | no growth                                                        |                                                          |
| `run_cosim`          | FAIL on `l10` and on `precision` above 50 Ω, as the file expects |                                                          |

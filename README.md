<img align="right" width="60px" src="resources/icon.png">

# RFsim

Simulate the S-parameters of an RF structure directly in the PCB editor
of KiCad 10.0, with the [openEMS](https://openems.de) FDTD solver. The
geometry goes from the native board objects of KiCad to the primitives of
CSXCAD.

## Features

- Simulate the S-parameters of any number of ports, and write a Touchstone (`.sNp`) file.
- Plot the magnitude, the phase, a Smith chart, the VSWR and the group delay.
- Animate the E-field and the H-field on the mid-plane of the substrate.
- Calculate the far field with NF2FF: three polar cuts, a 3D pattern, Dmax and the efficiency.
- Measure the impedance of a line and its effective permittivity from a de-embedded port.
- Model the R, L and C parts as lumped elements, with the parasitics of the package.
- Feed each port as a lumped, microstrip (MSL), coplanar (CPW) or stripline port.
- Extract the geometry from the board: the pads, tracks, arcs, vias, zones and shapes.
- Draw the board layout that the solver uses.
- Set the substrate, the mesh preset and the CPU threads in the dialog.

## Installation

1) Download the [latest release ZIP file](https://github.com/NBalciunas/kicad-rfsim/releases).
2) Open KiCad and in the main window click on "Plugin and Content Manager".
3) Click "Install from File..." and select the downloaded ZIP file.
4) Install `scikit-rf`, `matplotlib` and `h5py` into the Python of KiCad:

   ```bat
   "C:\Program Files\KiCad\10.0\bin\python.exe" -m pip install --user scikit-rf matplotlib h5py
   ```

   > If KiCad is installed for one user only, its Python is in `%LOCALAPPDATA%\Programs\KiCad\10.0\bin`.

5) Install openEMS. Download the newest `openEMS_x64_v*_msvc.zip` from the [openEMS releases](https://github.com/thliebig/openEMS-Project/releases). Extract the `openEMS` folder to `C:\openEMS`.

   > For a different folder, set the `OPENEMS_PATH` environment variable.

6) Install [Python 3.14](https://www.python.org/downloads/), then make the venv of the solver:

   ```bat
   py -3.14 -m venv C:\openEMS\venv
   C:\openEMS\venv\Scripts\python.exe -m pip install --find-links C:\openEMS\python csxcad openems
   C:\openEMS\venv\Scripts\python.exe -c "import os; os.add_dll_directory('C:/openEMS'); import CSXCAD, openEMS; print('ok')"
   ```

   > The last command must print `ok`. A warning about the version of HDF5 is not a problem.

7) Restart KiCad. The plugin is now installed.

## Usage

![RFsim GUI](docs/example-1.png)

1. Click a pad in the PCB editor. It becomes port 1. Hold the shift key and click more pads for more ports.
2. Click the **RFsim** icon in the toolbar.
3. Look at the preview at the top of the dialog. It shows the ports, the R/L/C parts and the domain, and it follows the "Domain margin" field and the "Model" checkboxes.
4. Set the sweep range, "Define at" (the frequency of the field views and the far field), the ports, the substrate, the mesh preset, the domain margin, the run limits and the output directory.
5. Click Run Simulation. The results open in a plot window, and `results.sNp`, `model.json`, `lines.json` and `farfield_pN.json` go into the output directory.

### Ports

**A port is at a pad that you select, and it drives that pad against the adjacent copper layer.** It is not at the edge of the board. The box covers the whole pad in x and y, and the whole substrate in z.

Each port needs a ground return: copper on the reference layer below the pad. Without it the plugin refuses to run. A CPW port is the one exception, because its return path is the copper at the sides of the line.

The dialog gives only the types that the geometry permits:

| Type                  | Requires                                                 |
|-----------------------|----------------------------------------------------------|
| Lumped Port           | No requirements                                          |
| Microstrip (MSL) Port | Feed line on the x or y axis                             |
| Coplanar (CPW) Port   | Feed line on the x or y axis; Copper on both sides of it |
| Stripline Port        | Feed line on the x or y axis; Plane above and below it   |

> Each excited port costs one full FDTD run. The plugin gives a warning when a port gives out more power than it takes in. Run again at the medium or the fine preset when you see it.

### The impedance of a line

The microstrip, the coplanar and the stripline ports measure their own line. The "Line Impedance" view shows Z0 against the frequency, and `lines.json` holds every value, the effective permittivity included. These values are for the real track on the real stackup, and not for the reference impedance of the dialog. A lumped port has no line, thus it gives no such value.

The coarse preset reads a little low: the microstrip of `validation/` gives 47.7 ohm at coarse, 47.8 ohm at medium and 48.9 ohm at fine, against 49.8 ohm from the theory. Use medium or fine when the number is important.

### The substrate and the domain

"KiCad's Stackup" takes εr, tanδ and the thickness from Board Setup > Physical Stackup, layer by layer. The dialog starts there when the board has a stackup, and the four fields then show what the board gives and stay read-only.

> Save the board first. The plugin reads the saved file.

The other presets make a uniform stackup from the values in the dialog. They fill εr and tanδ, and "Custom" leaves the two fields to you:

| Preset         | εr   | tanδ   |
|----------------|------|--------|
| FR-4           | 4.5  | 0.02   |
| Rogers RO4350B | 3.48 | 0.0037 |
| Rogers RO4003C | 3.38 | 0.0027 |
| PTFE           | 2.20 | 0.0009 |

The domain fits the full board and adds the margin as air around it. The plugin cuts the copper that crosses the outer edge.

### Accuracy

The mesh preset gives the number of cells for each wavelength. The wavelength is the shortest one of the sweep, in the substrate:

| Preset    | Cells per wavelength | Cell on FR-4, sweep to 6 GHz |
|-----------|----------------------|------------------------------|
| Coarse    | 10                   | 2.36 mm                      |
| Medium    | 20                   | 1.18 mm                      |
| Fine      | 40                   | 0.59 mm                      |
| Ultrafine | 80                   | 0.29 mm                      |

The preset controls this step alone. The cells across the strip of a port, the cells in the gap of a CPW and the cells through the substrate stay the same at each preset, because the geometry and not the wavelength gives them.

The coarse preset is for a first look. A small lumped element reads too large at that resolution, thus use medium or fine when a number is important. For the behavior of the solver itself, refer to the [openEMS documentation](https://docs.openems.de).

### Lumped elements

Any footprint with 2 numbered SMD pads on one copper layer gives a row in the "Lumped Elements" part of the dialog. **The first letter of the reference gives the type**: R, L or C. Every other 2-terminal part, for example a diode or a ferrite bead, starts at "Unknown" with its "Model" checkbox off, and it changes no simulation until you select a type and give a value.

**The Value field of the footprint gives the value.** One text, three readings: the same letters serve all three types, and the type of the part decides the unit. The case is important, thus `4m7` is 4.7 mohm and `4M7` is 4.7 Mohm.

| Letter      | Prefix | Text              | Resistor | Capacitor | Inductor |
|-------------|--------|-------------------|----------|-----------|----------|
| `p`         | pico   | `4p7`             | 4.7 pohm | 4.7 pF    | 4.7 pH   |
| `n`         | nano   | `4n7`             | 4.7 nohm | 4.7 nF    | 4.7 nH   |
| `u` or `µ`  | micro  | `4u7`             | 4.7 µohm | 4.7 µF    | 4.7 µH   |
| `m`         | milli  | `4m7`             | 4.7 mohm | 4.7 mF    | 4.7 mH   |
| `R` `F` `H` | unit   | `4R7` `4F7` `4H7` | 4.7 ohm  | 4.7 F     | 4.7 H    |
| `k` or `K`  | kilo   | `4k7`             | 4.7 kohm | 4.7 kF    | 4.7 kH   |
| `M`         | mega   | `4M7`             | 4.7 Mohm | 4.7 MF    | 4.7 MH   |
| `G`         | giga   | `4G7`             | 4.7 Gohm | 4.7 GF    | 4.7 GH   |
| `T`         | tera   | `4T7`             | 4.7 Tohm | 4.7 TF    | 4.7 TH   |

**Each type has its own mark for the unit**, thus `4F7` on a resistor and `4R7` on an inductor give no value. An inductor with `4R7` on its body is 4.7 µH, and no rule can know that from the text alone: give such a value in the dialog. The lower case does the same (`4r7`, `4f7`, `4h7`).

The letter can also come after the number (`4.7k` = 4.7 kohm, `22p` = 22 pF). **The unit letter is optional**, thus `4p7F` and `10uH` read the same as `4p7` and `10u`, and the unit can be a word of its own (`10 kOhm`, `4.7 uF`, `10 nH`). A word that starts with a digit after a space has no effect (`100nF 10%`, `10u 25V`). "DNP" and the other words for a part that is not there give no value. The dialog shows the number that the plugin read, thus you see which value it took.

**Each row also holds the parasitics of the body**, an ESR and an ESL. The plugin reads the package from the name of the footprint (`R_0402_1005Metric` gives `0402`) and fills the two values from its table of 8 codes, from 0201 to 2512. Any other name gives "Custom", thus you give the two values yourself, and "No parasitics" makes an ideal element. A capacitor becomes ESR + ESL + C, which is the usual model of a real part, and an inductor gets its DCR but no self-resonance.

## Examples

The following example shows the magnitude of S11 against the frequency.

![S11 magnitude plot](docs/example-2.png)

The following example shows the Smith chart of the same run.

![Smith chart](docs/example-3.png)

The following example shows the board layout, a top view of the structure that the solver used.

![Board layout view](docs/example-4.png)

The following example shows the size of the E-field on the mid-plane of the substrate. The plugin draws one view for each excited port.

![E-field animation](docs/example-5.png)

The following example shows the 3D radiation pattern that you can rotate.

![Far-field 3D pattern](docs/example-6.png)

## Validation

The `validation/` folder makes its own test boards, runs the solver and compares the result against closed-form theory. Run the files with the Python of KiCad:

```bat
set KIPY="C:\Program Files\KiCad\10.0\bin\python.exe"
%KIPY% validation\run_rlc.py coarse
```

* **`run_rlc.py [mesh] [R1|L1|C1]`**  
R, L and C as a series impedance between two Z0 lines. |S21| stays flat for R, it falls for L and it rises for C. An element that the solver ignored cannot give these slopes.
* **`run_lumped.py [mesh]`**  
A series resistor of 50 Ω in a 50 Ω line must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB, the ideal divider. A gap that stays open gives about 0 dB.
* **`run_shunt.py [mesh] [packages|two]`**  
The package parasitics. A capacitor in shunt to ground makes a notch in |S21| at its series resonance, and the frequency of that notch gives the body inductance back, also below 1 nH. `packages` repeats this for the 8 chip packages on their KiCad land patterns. `two` puts two parts in series to ground, thus they interact.
* **`run_cpw.py [mesh] [cpw|stripline]`**  
The CPW port and the stripline port against closed-form theory, both the impedance and eps_eff. The eps_eff of a stripline must be exactly εr, thus this is the most exact test here.
* **`run_zone_holes.py [mesh]`**  
A filled zone with a void of 8 x 6 mm below the line, against the same board with none. The void must make a large step in S11, which shows that the hole stays open.
* **`run_feature.py [mesh] [stub width in mm]`**  
The number of mesh cells across a copper feature that no port covers, against closed-form theory. An open stub is a quarter-wave resonator, thus the notch of |S21| gives its eps_eff, and two stub lengths remove the end effects. Run it with the python of the solver.
* **`run_stability.py [fast|slow|all]`**  
The timestep rule for a lumped inductor. `fast` compares the timestep of the plugin against the one at which the run diverges, on 6 geometries, in about 15 minutes. `slow` finds a different failure: a lumped inductor also carries a mode that grows. That stage does not pass at present: a body inductance below 1 nH keeps a margin of 7 times, and 10 nH keeps none.
* **`test_ports.py`**  
The geometry of the ports and the mesh: the box of each type, the fallback to a lumped port, the mesh line at each via, and the cells near a CPW and a stripline. It needs no KiCad and no solver, thus it takes seconds. Run it with the python of the solver.
* **`run_headless.py [mesh] [msl|lumped]`**  
The full path from the board to the Touchstone file. A microstrip of 30 mm and about 50 Ω must give S11 < −10 dB and S21 > −0.5 dB from 1 GHz to 6 GHz.
* **`diag_lumped.py board.kicad_pcb`**  
Why the plugin does not simulate an R/L/C part. It shows the result of each test, for each part.
* **`test_dialog.py`**  
The dialog with no display: the rows of the parts, the packages, the parasitics and the values that `get_settings` gives back.
* **`test_views.py`**  
Every view of the results window, with no display. It reads back the title, the labels and the color bar, and it needs `validation/out_coarse` from `run_headless.py coarse`.
* **`test_touchstone.py`**  
The Touchstone writer. skrf must read back the same S-matrix, for 1 to 5 ports.
* **`make_test_board.py`**  
Makes the microstrip board of `run_headless.py`, and the CPW board and the stripline board of `run_cpw.py`.

`%KIPY% plugins\board_reader.py` is the self-test of the value parser (67 cases).

## License

This project is licensed under the MIT License.
Copyright © 2026 Nojus Balčiūnas
<img align="right" width="60px" src="assets/icon.png">

# RFsim

Simulate the S-parameters of an RF structure directly in the PCB editor
of KiCad 10.0, with the [openEMS](https://openems.de) FDTD solver. The
geometry goes from the native board objects of KiCad to the primitives of
CSXCAD.

## Features

- Simulate the S-parameters of any number of ports. The plugin plots the magnitude, the phase, a Smith chart, the VSWR and the group delay, and it writes a Touchstone (`.sNp`) file.
- Calculate the far field with NF2FF. The plugin plots three polar cuts in absolute dBi and a 3D radiation pattern that you can rotate. It also gives Dmax and the radiation efficiency.
- Animate the E-field and the H-field on the mid-plane of the substrate, with one view for each excited port.
- Draw the board layout that the solver uses: a preview in the dialog and a view with the results.
- Extract the geometry directly from the board: the pads, tracks, arcs, vias, filled zones and the graphic shapes on the copper layers.
- Model the resistors, inductors and capacitors as lumped elements, with the value from the footprint.
- Feed each port as a lumped port or as a de-embedded microstrip (MSL)port.
- Define the substrate: εr, tanδ, the height and the copper thickness.
- Mesh the structure at three resolutions: coarse, medium and fine.
- The solver runs in a different process. Thus, a crash cannot stop KiCad, and the same runner also works without a GUI.

## Installation

1) Download the [latest release ZIP file](https://github.com/NBalciunas/kicad-rfsim/releases).
2) Open KiCad and in the main window click on "Plugin and Content Manager".
3) Click "Install from File..." and select the downloaded ZIP file.
4) Install `scikit-rf`, `matplotlib` and `h5py` into the Python of KiCad:

   ```bat
   "%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe" -m pip install --user scikit-rf matplotlib h5py
   ```

   > If KiCad is installed for all the users, its Python is in `C:\Program Files\KiCad\10.0\bin`.

5) Install openEMS. Download the newest `openEMS_x64_v*_msvc.zip` from the [openEMS releases](https://github.com/thliebig/openEMS-Project/releases). Extract the `openEMS` folder to `C:\openEMS`.

   > For a different folder, set the `OPENEMS_PATH` environment variable.

6) Install [Python 3.14](https://www.python.org/downloads/), then make the venv of the solver:

   ```bat
   py -3.14 -m venv C:\openEMS\venv
   C:\openEMS\venv\Scripts\python.exe -m pip install numpy h5py
   C:\openEMS\venv\Scripts\python.exe -m pip install C:\openEMS\python\csxcad-*-cp314-*.whl
   C:\openEMS\venv\Scripts\python.exe -m pip install C:\openEMS\python\openems-*-cp314-*.whl
   C:\openEMS\venv\Scripts\python.exe -c "import CSXCAD, openEMS; print('ok')"
   ```

   > CSXCAD must go in before openEMS.

7) Restart KiCad. The plugin is now installed.

## Usage

![RFsim GUI](docs/example-1.png)

1. Click a pad in the PCB editor. It becomes port 1. Hold the shift key and click more pads for more ports.
2. Click the **RFsim** icon in the toolbar.
3. Look at the preview at the top of the dialog. It shows the ports, the R/L/C parts and the domain, and it follows the "Domain margin" field and the lumped checkbox.
4. Set the sweep range, "Define at" (the frequency of the field views and the far field), the port impedance, the number and the type of each port, the substrate, the mesh preset, the domain margin and the output directory.
5. Click Run Simulation. The results open in a plot window, and`results.sNp`, `model.json` and `farfield_pN.json` go into the output directory.

### Ports

A port needs a ground return: copper on the reference layer (the adjacent copper layer) that reaches at least the edge of the pad. Without it the plugin refuses to run.

A lumped port drives the pad against that layer, and it operates everywhere. A microstrip (MSL) port is de-embedded, and it needs a track that leaves the pad on the x-axis or the y-axis.

Each excited port costs one full FDTD run. Port 1 supplies the field views and the far-field views.

### The substrate and the domain

The values in the dialog always have priority, and the plugin makes a uniform stackup from them. It reads the `(stackup ...)` section of the board file only for a run without a GUI. KiCad does not give that section to Python, thus you must save the board first.

The domain fits the full board and adds the margin as air around it. The plugin cuts the copper that crosses the outer edge. For an antenna, give the radiator more space before the absorber: a margin of 15 mm or more (about λ/8 at 2.4 GHz), not the default 4 mm.

### Accuracy

The coarse preset is for a first look. A small lumped element reads too large at that resolution, so use medium or fine when the value of the element matters. The lumped elements are ideal, and they have no package parasitics. For the behavior of the solver itself, refer to the [openEMS documentation](https://docs.openems.de).

## Examples

The following example shows the magnitude of S11 against the frequency.

![S11 magnitude plot](docs/example-2.png)

The following example shows the Smith chart of the same run.

![Smith chart](docs/example-3.png)

The following example shows the board layout, a top view of the structure that the solver used.

![Board layout view](docs/example-4.png)

The following example shows the E-field on the mid-plane of the substrate. The plugin draws one view for each excited port.

![E-field animation](docs/example-5.png)

The following example shows the 3D radiation pattern that you can rotate.

![Far-field 3D pattern](docs/example-6.png)

## Validation

The `validation/` folder makes its own test boards, runs the solver and compares the result against closed-form theory. Run the files with the Python of KiCad:

```bat
set KIPY="%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe"
%KIPY% validation\run_rlc.py coarse
```

* **`run_rlc.py [mesh] [R1|L1|C1]`**  
Used to validate R, L and C against the theory for a series impedance between two Z0 lines. |S21| stays flat for R, it decreases for L, and it increases for C. The opposite slopes cannot come from an element that the solver ignored.
* **`run_lumped.py [mesh]`**  
Used to validate the lumped elements: a series resistor of 50 Ω in a 50 Ω line must give S11 ≈ −9.5 dB and S21 ≈ −3.5 dB, the ideal resistive divider. A gap that stays open gives about 0 dB.
* **`run_headless.py [mesh] [msl|lumped]`**  
Used to validate the full path from the board to the Touchstone file. A microstrip line of 30 mm and about 50 Ω must give S11 < −10 dB and S21 > −0.5 dB from 1 GHz to 6 GHz.
* **`diag_lumped.py board.kicad_pcb`**  
Used to find why the plugin does not simulate an R/L/C part. It shows the result of each test, for each part.
* **`test_touchstone.py`**  
Used to validate the Touchstone writer. skrf must read back the same S-matrix, for 1 to 5 ports.
* **`make_test_board.py`**  
Used to make the microstrip board that `run_headless.py` needs.

`%KIPY% board_reader.py` is the self-test of the value parser (21 cases).

## License

This project is licensed under the MIT License.
Copyright © 2026 Nojus Balčiūnas
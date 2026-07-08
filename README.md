<img align="right" width="60px" src="assets/icon.png">

# RFsim

KiCad 8 action plugin: simulate S-parameters of a two-port RF structure
directly from the PCB editor with [openEMS](https://openems.de) (FDTD).
Geometry goes straight from KiCad's native board objects to CSXCAD
primitives — no gerbers, no rasterizing.

## Features

- Select one or two pads, run, get S11/S21 plots, Smith chart, VSWR and
  group delay, plus a Touchstone (`.s1p`/`.s2p`) file.
- Stackup (dielectric thickness, εr, tanδ, copper thickness) read from the
  board; copper modeled as lossy conducting sheets, vias as cylinders.
- Lumped or deembedded microstrip (MSL) ports, selectable per port.
- Coarse / medium / fine mesh presets, PML_8 absorbing boundaries.
- The solver runs as a separate process: a crash never takes KiCad down,
  and the same runner works headless (`python runner.py model.json outdir`).

## Installation

1. Copy this folder into KiCad's plugin directory, e.g.
   `C:\Users\<you>\Documents\KiCad\8.0\3rdparty\plugins\rfsim-dev`
   (any directory listed in `pcbnew.PLUGIN_DIRECTORIES_SEARCH` works).

2. Install the Python dependencies **into KiCad's own Python**. On Windows
   (adjust paths to your install):

   ```bat
   "C:\Program Files\KiCad\8.0\bin\python.exe" -m pip install --user scikit-rf matplotlib h5py
   ```

3. Install openEMS. Download the latest `openEMS_v*.zip` from
   [openEMS releases](https://github.com/thliebig/openEMS-Project/releases),
   extract the `openEMS` folder to
   `C:\Users\<you>\Documents\KiCad\8.0\3rdparty\openEMS`
   (alternatives: `C:\openEMS`, or set the `OPENEMS_PATH` env var), then
   install its Python wheels (KiCad 8 bundles Python 3.11 → `cp311`):

   ```bat
   "C:\Program Files\KiCad\8.0\bin\python.exe" -m pip install --user ^
       <openEMS>\python\CSXCAD-*-cp311-*.whl <openEMS>\python\openEMS-*-cp311-*.whl
   ```

4. Restart KiCad. "RFsim: S-parameters (openEMS)" appears under
   Tools → External Plugins.

## Usage

1. Open your board, click a pad (port 1), shift-click a second pad (port 2).
2. Tools → External Plugins → RFsim.
3. Set frequency range, port impedance, port types, margin, mesh preset.
   - **Lumped** port: vertical excitation across the substrate under the
     pad. Works everywhere; reference plane is the pad itself.
   - **Microstrip (MSL)** port: deembedded transmission-line port. Needs a
     track leaving the pad roughly along the x or y axis; the reference
     plane sits a few mm down the line.
4. Run Simulation. Results open in a plot window; `results.s2p` and
   `model.json` land in the output directory.

**Save the board first** — the stackup (Board Setup → Physical Stackup) is
read from the saved file because KiCad 8's Python API doesn't expose it.
Boards without a customized stackup get a default: FR4, εr 4.5, tanδ 0.02,
35 µm copper.

## Scope / limitations (v1)

- Max two ports, no far-field/antenna post-processing.
- Simulated area = bounding box of the selected pads + 2× margin; copper
  crossing that boundary is cut and terminates into the absorber.
- The reference layer for a port is the adjacent copper layer in the
  stackup (e.g. F.Cu → the first inner/bottom layer).
- Copper graphics (text/shapes on copper layers) are ignored; pads, tracks,
  arcs, vias and filled zones are included.

## Validation

`validation/` contains a generator for a 30 mm ~50 Ω microstrip board and a
headless end-to-end check:

```bat
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\run_headless.py medium
```

Expected: S11 < −10 dB, S21 > −0.5 dB across 1–6 GHz, `PASS`.

---

# Development notes

Everything below is for picking up development later, not for users.

## Status (2026-07-08)

Verified working (headless, real openEMS runs on this machine):

- [x] Geometry + stackup extraction from a generated 2-layer board
- [x] 2-port MSL run, coarse & medium mesh (S11 ≤ −10.6 dB, S21 ≥ −0.3 dB)
- [x] 2-port lumped run (S11 ≤ −9.3 dB)
- [x] 1-port → `.s1p` (cut trace terminates into PML, S11 ≈ −21 dB as expected)
- [x] Package imports the way KiCad loads it (`__import__("rfsim-dev")`)
- [x] skrf reads the hand-written Touchstone files

Not yet verified — **do this first next session**:

- [ ] Live click-through in the KiCad GUI: pad selection → SettingsDialog →
      RunDialog log streaming/cancel → ResultsFrame plots. All dialogs are
      import-tested only; nobody has clicked them in a running pcbnew yet.
- [ ] A board with vias / inner layers / zones with holes (fractured-slit
      polygons and `AddCylinder` vias are untested against a real solver run)
- [ ] Boards with a customized (non-default) stackup section in the file

## Architecture / data flow

```
KiCad process                                 subprocess (crash-isolated)
─────────────                                 ──────────────────────────
__init__.py registers RFSimPlugin
rfsim.py Run():
  deps check → selected pads
  board_reader.extract(board, pads, margin)   runner.py model.json outdir:
    → model dict → model.json      ──────────→  _port_geometry → _mesh →
  gui.RunDialog streams stdout                  build CSX → FDTD.Run per
  gui.ResultsFrame(results.sNp)    ←──────────  excitation → CalcPort →
                                                results.s2p / .s1p
```

`board_reader.py` is the only module touching pcbnew for geometry — swap it
for the IPC API on KiCad 9+ without touching `runner.py`. The interface is
the JSON-able model dict (all mm, y flipped to right-handed, z=0 at board
bottom, copper = zero-thickness sheets at dielectric boundaries):

```
{ settings: {f_start, f_stop, z0, margin_mm, mesh, n_freq,
             max_timesteps, end_criteria},
  copper_layers:     [{name, z, thickness}]          # top→bottom
  dielectric_layers: [{name, z_top, z_bottom, epsilon, loss_tangent}],
  region:     {x0,y0,x1,y1},   # crop rect = pads bbox + 2*margin
  board_rect: {x0,y0,x1,y1},   # region ∩ board edges (dielectric extent)
  polygons:   {"F.Cu": [[[x,y],...], ...], ...},     # fractured, no holes
  vias:  [{x, y, r, z0, z1}],
  ports: [{number, label, x, y, layer, ref_layer, width, length,
           direction: [±1,0]|[0,±1]|null, track_width, type}] }
```

## Decisions that weren't obvious (with the bugs that forced them)

**KiCad 8 SWIG gaps** (all discovered the hard way):

- `BOARD_STACKUP` is not wrapped at all (`GetStackupDescriptor()` returns an
  opaque SwigPyObject) → stackup is parsed from the saved file's
  `(stackup ...)` s-expression; fallback = FR4 defaults from
  `GetBoardThickness()` + copper count. Hence "save the board first".
- The `ERROR_LOC` enum values exist nowhere in the bindings. Only
  `PAD.TransformShapeToPolygon(ps, layer, clearance, maxErr)` works (its
  wrapper has a C++ default for the enum). Tracks/arcs/via annulars are
  polygonized with plain math (`_stadium_pts`/`_circle_pts`).
- `pcbnew.BOX2I()` default-constructs at origin (0,0) — `Merge` then wrongly
  includes the origin. Always init from the first pad's bbox.

**openEMS/CSXCAD landmines**:

- A zero-thickness ConductingSheet polygon is silently dropped ("Unused
  primitive" warning) unless a mesh line sits **exactly** on its z plane.
  `SmoothMeshLines` returns 1.5300000000000002 for an input of 1.53 → all
  smoothed lines are rounded to 9 decimals before `AddLine`. If you ever see
  "Unused primitive" again, suspect float drift first.
- MUR boundaries caused slow late-time energy growth (−33 → −19 dB over
  250k steps, never converging) on this exact setup; PEC was stable, so the
  boundary was the culprit, not the sheets. Fix: `PML_8` with the outer
  margin meshed as exactly 8 cells (`_pml_band`).
- Because copper is cropped at the *outer* domain edge (region = bbox +
  2×margin), cut planes/traces run through the PML and terminate
  quasi-matched — same trick as openEMS's own MSL examples. Inner margin
  band = clear air between structure and absorber.
- The Windows wheels need the openEMS binary folder via
  `os.add_dll_directory` (runner tries `../../openEMS`, `OPENEMS_PATH`,
  `C:\openEMS`) and `h5py` at import time.
- `MSLPort` requires start≠stop in all three coords, strip plane at
  `start[2]`, and ≥5 mesh lines along propagation **before** the port is
  created — hence mesh is built before `AddMSLPort` in `build()`.

**Modeling choices** (fine to revisit):

- Copper loss via `AddConductingSheet` (5.8e7 S/m, real thickness);
  dielectric loss as kappa at center frequency.
- Mesh presets = λ_min/{10,20,40} + fixed lines at polygon bbox edges, port
  edges/centers, layer planes, ≥4 cells per dielectric. No 1/3-2/3 rule.
- Full S-matrix = one run per excited port (2 ports → 2 runs). No
  reciprocity shortcut.
- Touchstone written by hand in `runner.write_touchstone` (RI format;
  remember `.s2p` column order is S11 S21 S12 S22).
- Port reference layer = adjacent copper layer in the stackup, not a
  copper-under-pad search.

## Dev commands

```bat
:: full end-to-end check (also regenerates the test board + model.json)
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\run_headless.py coarse [msl|lumped]

:: rerun the solver on an existing model without touching pcbnew
"C:\Program Files\KiCad\8.0\bin\python.exe" runner.py validation\out_coarse\model.json out
```

`validation/out_*/model.json` is the fastest debugging entry point — edit it
by hand and rerun `runner.py` to iterate on solver-side code with zero KiCad
involvement.

## Backlog (rough priority)

1. Manual GUI test in KiCad (see Status); fix whatever falls out.
2. Validation board #2 with vias + zone holes (e.g. CPWG segment or stub
   filter) to exercise the untested geometry paths.
3. Expose max timesteps / end criteria in the dialog for high-Q structures
   (currently fixed 300k / 1e-4 in `gui.get_settings`).
4. Copper-layer graphics (PCB_SHAPE/text on copper) are ignored — add if a
   real board needs it.
5. PCM packaging (zip layout with `plugins/` subfolder) if this should ship
   through the Plugin and Content Manager.

## License

MIT — Copyright © 2026 Nojus Balčiūnas

<img align="right" width="60px" src="assets/icon.png">

# RFsim

KiCad action plugin: simulate S-parameters of an RF structure directly from
the PCB editor with [openEMS](https://openems.de) (FDTD). Geometry goes
straight from KiCad's native board objects to CSXCAD primitives — no
gerbers, no rasterizing.

> **KiCad version: 10.0.0 or newer.** Ported from KiCad 8 on 2026-07-30 —
> it no longer runs on 8 (`SHAPE_POLY_SET.PM_FAST` and friends are
> KiCad-8-only). Why 10 and not 9, and what changed:
> [KiCad API notes](#kicad-api-notes).

## Features

- Select one or more pads, run, get S-parameter magnitude + phase plots,
  Smith chart, VSWR and group delay, plus a Touchstone (`.sNp`) file.
- **Board layout view**: rendered top view of exactly what was simulated —
  B.Cu blue, F.Cu red, ports green, lumped R/L/C parts dark green, board
  edge and domain outlines.
- **E/H-field animations**: traveling-wave views on the substrate mid-plane
  at the dialog's "Define at" frequency — signed E_z and the dominant
  in-plane H component (both red/blue); shows how the signal propagates
  and where energy couples away. One view per excited port ("(Port x)"
  in the dropdown/titles when more than one).
- **Far-field patterns**: three NF2FF polar directivity cuts in absolute
  dBi at the "Define at" frequency — Phi=0, Phi=90 (θ sweeps, θ = 0 is the
  board normal) and Theta=90 (azimuth) — CST-style, each with a stats
  panel: main lobe magnitude/direction, 3 dB angular width, side lobe
  level. Dmax and radiation efficiency are in `farfield_pN.json` (one
  per excited port) and the solver log; like the field views, each
  excited port gets its own far-field entries.
- **Farfield 3D**: rotatable CST-style directivity balloon (full sphere,
  5° grid, radius/color = dBi over a 30 dB range, +z = board normal). The
  balloon is semi-transparent with the PCB drawn as a reference plate at
  the centre (F.Cu red / B.Cu blue / ports green, oriented as the layout).
- Substrate (εr, tanδ, height h, copper thickness hm) set in the dialog
  (defaults: 1.6 mm FR4, 35 µm copper); copper modeled as lossy conducting
  sheets, vias as cylinders.
- Copper geometry includes pads, tracks, arcs, vias, filled zones **and
  graphic shapes on copper layers** (drawn polygons/rects/circles/arcs —
  e.g. antenna patches), from both board drawings and footprint graphics.
- Lumped or deembedded microstrip (MSL) ports, selectable per port.
- **SMD R, L and C parts simulated as lumped elements**: footprints with
  reference R*/L*/C* and exactly two **numbered** pads (extra unnumbered
  mechanical pads are fine) inside the region become openEMS lumped elements
  bridging their pad gap; values parsed from the Value field ("4k7",
  "100nF", "3n3", "DNP" skipped). 0-ohm resistors become metal shorts.
  Toggle in the dialog; skipped parts produce warnings. All three types are
  validated against closed-form theory (`validation/run_rlc.py`).
  Inductors need openEMS >= v0.37 — see [Solver interpreter](#solver-interpreter).
- Any number of ports (one FDTD run per excited port); a per-pad number
  dropdown sets the port order (port 1 drives the E/H-field and far-field
  views), and per-port "Excite" checkboxes let you skip S-columns you
  don't need.
- Sanity guard: refuses to run a port with no copper on its reference
  layer under the pad (no ground return = meaningless total reflection).
- Coarse / medium / fine mesh presets, PML_8 absorbing boundaries.
- The solver runs as a separate process: a crash never takes KiCad down,
  and the same runner works headless (`python runner.py model.json outdir`).

## Requirements

| | needed | notes |
|---|---|---|
| KiCad | **10.0.0+** | does not run on 8; 9.0.x would work but see the decision below |
| Python (plugin side) | KiCad's bundled 3.11 | not user-selectable; 8, 9 and 10 all ship 3.11 |
| KiCad-side packages | `scikit-rf`, `matplotlib`, `h5py` | plus bundled `numpy`, `wxPython` |
| Python (solver side) | **3.13 or 3.14** | its own interpreter; see below |
| Solver | **openEMS ≥ v0.37** + CSXCAD | v0.0.36 also works, but cannot do inductors |

The solver runs in a **separate interpreter** from KiCad. That is not
cosmetic: openEMS ≥ v0.37 — the first release that can simulate lumped
inductors — ships cp313/cp314 wheels only, and KiCad 8, 9 and 10 all bundle
Python 3.11, so it can never be installed into KiCad's own Python. See
[openEMS versions and lumped elements](#openems-versions).

## Installation

Set a shortcut for KiCad's interpreter first — every command below uses it:

```bat
set KIPY="%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe"
```

That is the **per-user** install location, which is what the KiCad Windows
installer uses by default. For a machine-wide install it is
`C:\Program Files\KiCad\10.0\bin\python.exe` instead. Check which you have
with `echo %KIPY%` and `%KIPY% -V` before going further — "The system
cannot find the path specified" means you picked the wrong one.

`set` only affects the current Command Prompt window and is not stored
anywhere; re-run it in each new window.

1. Copy this folder into KiCad's plugin directory, e.g.
   `C:\Users\<you>\Documents\KiCad\10.0\3rdparty\plugins\rfsim-dev`
   (any directory listed in `pcbnew.PLUGIN_DIRECTORIES_SEARCH` works).

2. Install the Python dependencies **into KiCad's own Python**:

   ```bat
   %KIPY% -m pip install --user scikit-rf matplotlib h5py
   ```

3. Install openEMS. Download the newest `openEMS_x64_v*_msvc.zip` from
   [openEMS releases](https://github.com/thliebig/openEMS-Project/releases)
   and extract the `openEMS` folder to **`C:\openEMS`** (alternatives:
   `<kicad>/3rdparty/openEMS`, or set the `OPENEMS_PATH` env var).

4. Give the solver its own Python. openEMS ≥ v0.37 ships cp313/cp314
   wheels only, so it cannot go into KiCad's 3.11 — install
   [Python 3.14](https://www.python.org/downloads/) and make a venv
   **next to the openEMS install**, which is where the plugin looks:

   ```bat
   py -3.14 -m venv C:\openEMS\venv
   C:\openEMS\venv\Scripts\python.exe -m pip install numpy h5py
   C:\openEMS\venv\Scripts\python.exe -m pip install C:\openEMS\python\csxcad-*-cp314-*.whl
   C:\openEMS\venv\Scripts\python.exe -m pip install C:\openEMS\python\openems-*-cp314-*.whl
   ```

   Install CSXCAD before openEMS — the order matters. Verify with:

   ```bat
   C:\openEMS\venv\Scripts\python.exe -c "import CSXCAD, openEMS; print('ok')"
   ```

   openEMS's own README suggests a global `CSXCAD_INSTALL_PATH` variable;
   that is not needed here — the runner adds the DLL directory itself.

5. Restart KiCad. "RFsim: S-parameters (openEMS)" appears under
   Tools → External Plugins.

### Solver interpreter

`runner.py` imports only numpy/h5py/CSXCAD/openEMS — never pcbnew or wx —
so it runs in its own process *and* its own interpreter. `solverenv.py`
picks it, in order:

1. `$RFSIM_PYTHON`, if set — an explicit override.
2. A `venv` beside the openEMS install (step 4 above). **Recommended.**
3. Otherwise KiCad's own Python, which is correct only for openEMS
   v0.0.36 (the last release with a cp311 wheel, and it cannot simulate
   inductors).

The settings dialog reports missing packages for both interpreters
separately, so a half-finished setup says which side is short.

## Usage

1. Open your board, click a pad (port 1), shift-click further pads for
   more ports.
2. Tools → External Plugins → RFsim.
3. The dialog opens with a **preview of what will be simulated** at the
   top — F.Cu red, B.Cu blue, the pads that became ports in green with
   their numbers, detected R/L/C parts as dark-green pad-to-pad links, the
   board outline, and the dashed simulation domain. No legend or axes: the
   board gets the whole 480×285 thumbnail. The domain box follows the
   "Domain margin" field live and the R/L/C markers follow the lumped
   checkbox, so you can see the effect of both before running.
4. Fill in the dialog: sweep range and "Define at" (the frequency used for
   the field animations and far-field), port impedance, per-pad port
   number / type / Excite checkbox, substrate (presets: FR-4 / Rogers
   RO4350B / Custom — editing εr or tanδ flips to Custom), mesh preset,
   domain margin, output directory.
   - **Port number**: KiCad doesn't expose click order, so pads are
     numbered by footprint reference by default — reassign with the
     dropdown. Port 1 drives the field/far-field views.
   - **Excite**: each excited port costs one full FDTD run; uncheck ports
     whose S-columns you don't need.
   - **Lumped** port: vertical excitation across the substrate under the
     pad. Works everywhere; reference plane is the pad itself.
   - **Microstrip (MSL)** port: deembedded transmission-line port. Needs a
     track leaving the pad roughly along the x or y axis; the reference
     plane sits a few mm down the line.
5. Run Simulation. Results open in a plot window; `results.sNp`,
   `model.json` and `farfield_pN.json` land in the output directory.

The dialog's substrate values always win: GUI runs build a uniform stackup
from them (multi-layer boards split h evenly between dielectric layers).
The board file's `(stackup ...)` section is only used by headless runs
without an override — no KiCad SWIG version (8, 9 or 10) exposes the
stackup to Python, so that path needs a saved board.

A port needs a ground return: copper on the reference layer (the adjacent
copper layer in the stackup) reaching at least the edge of the pad — a
filled zone, plane, or drawn shape. Without it the plugin refuses to run;
results would be |S11| = 1 with only stray pad-to-pad coupling. For PCB
antennas, keep the usual layout rule: ground pour up to the antenna
keepout, feed pad at the pour edge. The domain auto-fits the whole board
(Edge.Cuts), so the margin is air around the PCB — for antennas give the
radiator breathing room before the absorber: margin ≥ 15 mm (~λ/8 at
2.4 GHz) instead of the 4 mm default.

## Scope / limitations (v1)

- Lumped elements are ideal (no package parasitics) and require both
  terminals on the same outer copper layer. A real 0402 has ~0.3–0.5 nH
  ESL, so matching networks near self-resonance will be optimistic
  (backlog #2). If a part isn't picked up, run
  `validation/diag_lumped.py` — it prints which check rejected it.
- **Inductors need openEMS ≥ v0.37.** Older engines implement R and C only
  and *silently drop* a pure-L element (open circuit → plausible-looking
  but wrong results), so the runner detects that engine and refuses rather
  than returning numbers. Fix: the venv in step 4 of Installation.
- Lumped inductors force a reduced FDTD timestep, so they cost 2–10× the
  runtime of the same board with R/C only. Handled automatically —
  see [lumped inductor stability](#lumped-inductor-stability).
- Absolute accuracy of a lumped element at the `coarse` preset is limited
  by discretisation: a 2.9 mm trace is only ~1 cell wide at λ/10, and the
  resulting parasitic series inductance inflates the apparent impedance
  with frequency (measured: a 10 nH part reads ~13.5 nH at 1.5 GHz and
  ~18.6 nH at 3 GHz; `medium` brings that to 12.8 / 16.7 nH). Use `medium`
  or `fine` when the element value itself matters.
- Simulated area = the whole board (Edge.Cuts bbox, merged with the
  selected pads' bbox; pads-only if the board has no outline) + 2× margin.
  Copper crossing that boundary is cut and terminates into the absorber.
- The reference layer for a port is the adjacent copper layer in the
  stackup (e.g. F.Cu → the first inner/bottom layer).
- Everything on a copper layer is simulated — pads, tracks, arcs, vias,
  filled zones, and graphic shapes including beziers — **except text**,
  which triggers a warning dialog instead of being silently dropped. This
  is a limitation of how `_copper_polys` is written, not of KiCad: see
  [the layer-converter finding](#text-on-copper-and-the-layer-converter).

## Validation

`validation/` contains a generator for a 30 mm ~50 Ω microstrip board and a
headless end-to-end check:

```bat
%KIPY% validation\run_headless.py medium
```

Expected: S11 < −10 dB, S21 > −0.5 dB across 1–6 GHz, `PASS`.

`validation\run_lumped.py` checks the lumped-element path: a series 50 Ω
resistor in a 50 Ω line must read S11 ≈ −9.5 dB / S21 ≈ −3.5 dB (the
ideal resistive divider), which cleanly separates "element simulated"
from "gap left open".

`validation\run_rlc.py` validates **all three element types** against
closed-form theory for a series impedance between two Z0 lines
(`S21 = 2·Z0/(2·Z0+Z)`), using the fact that each type has an
unmistakable and mesh-robust signature across 1–5 GHz:

```bat
%KIPY% validation\run_rlc.py coarse          :: all three
%KIPY% validation\run_rlc.py medium L1       :: one case, finer mesh
```

| part | |S21| across 1→5 GHz | measured |
|---|---|---|
| R = 50 Ω | flat at −3.5 dB | **+0.6 dB** slope, −4.5 dB at 2 GHz |
| L = 10 nH | **falls** (Z = jωL grows) | **−20.7 dB** slope |
| C = 1 pF | **rises** (Z = 1/jωC shrinks) | **+5.7 dB** slope |

The opposite slopes are the point: a dropped element reads as an open, and
L cannot be confused with C. Magnitudes are asserted only at the low end
of the sweep, where the element dominates the coarse mesh's parasitics.

---

# Development notes

Everything below is for picking up development later, not for users.

## Status (2026-07-30)

### Environment on this machine

As of 2026-07-30, the solver side is built and working:

- KiCad 8 is uninstalled. Present: **9.0.8** and **10.0.5**, both
  per-user at `%LOCALAPPDATA%\Programs\KiCad\<ver>`, both bundling
  **Python 3.11.5**.
- **openEMS v0.37.0-rc1 / CSXCAD v0.7.0-rc1** at `C:\openEMS`
  (`openEMS_x64_v0.37.0-rc1_msvc.zip`, 47.8 MB,
  sha256 `7ED0E3A2…F6556A4C`).
- **Solver venv at `C:\openEMS\venv`** — Python 3.14.5 with numpy 2.5.1,
  h5py 3.16.0, CSXCAD 0.7.0rc1, openEMS 0.37.0rc1 (cp314 wheels).
  `solverenv.solver_python()` finds it automatically.
- GUI side, in KiCad 10's Python: scikit-rf 2.0.1, matplotlib 3.11.1,
  h5py 3.16.0 (`pip install --user`, which lands in
  `Documents\KiCad\10.0\3rdparty\Python311` — KiCad's own user-site dir),
  plus bundled numpy 2.4.2 and wxPython 4.2.2.

Both interpreters are complete, and every layer has now been exercised on
KiCad 10 + openEMS v0.37.0-rc1.

### Changed 2026-07-30

- **KiCad 8 -> 10**: four API fixes in `board_reader.py`
  ([details](#breaks-between-kicad-8-and-10)); `metadata.json`
  `kicad_version` -> `10.0.0`. `gui.py` and `rfsim.py` needed nothing.
  Geometrically neutral — the regenerated `model.json` was bit-identical
  across all nine fields to the pre-port output.
- **openEMS v0.0.36 -> v0.37.0-rc1**, running on its own Python 3.14.
  New `solverenv.py` picks the solver interpreter; `runner.py` reuses its
  directory list for `add_dll_directory`. `rfsim.py`'s dependency check
  split in two and reports each interpreter separately, using `find_spec`
  so a missing openEMS DLL can't look like a missing package.
- **Inductors work**: sent as `LEtype=1` (series), passed only when
  `_has_lumped_rlc()` finds `SetLEtype`, so v0.0.36 still runs. R, L and C
  all validated against theory (`run_rlc.py`).
- **Field-dump format fix** for v0.37, then all 18 result views confirmed
  rendering. Settings dialog's logo replaced by a board-layout preview
  sharing the results renderer via `_draw_board`; it falls back to the icon
  if matplotlib can't be set up, so a preview failure can't block the
  dialog.
- **Lumped detection** now counts *numbered* pads, and warns instead of
  silently skipping when a valued part has the wrong terminal count.

### Lumped inductor stability

A lumped inductor destabilises the FDTD: energy goes to `-nan(ind)` and
`CalcPort` then dies with an opaque
`could not convert string to float: '-nan(ind)'`. It is **not** a topology
problem — series and parallel diverge identically — it scales with the
inductance. Measured on `run_rlc.py`'s geometry (coarse mesh, 6000 steps),
the largest stable `SetTimeStepFactor`:

| L (nH) | 1 | 3 | 10 | 30 | 100 | 300 |
|---|---|---|---|---|---|---|
| max stable factor | 1.0 | 1.0 | 0.5 | 0.35 | 0.18 | 0.12 |
| `1/√L(nH)` | 1.00 | 0.58 | 0.32 | 0.18 | 0.10 | 0.06 |

The boundary tracks `1.8/√L(nH)`, so `runner._time_step_factor()` uses
`min(1, 1/√L(nH))` for ~1.8× margin, and scales `max_timesteps` by `1/f`
so the *simulated time* budget is unchanged. Consequences and escapes:

- It is a heuristic fitted on **one** geometry; the true criterion also
  involves cell size and the element box. So `runner._diverged()` scans
  the port data for NaN after every run and aborts with a message naming
  `time_step_factor`, instead of letting `CalcPort` throw.
- `settings["time_step_factor"]` overrides the automatic value.
- Cost is real: a 10 nH part runs at factor 0.316, i.e. ~3× the timesteps.

### Verified working

On KiCad 10.0.5 + openEMS v0.37.0-rc1 unless noted.

**Extraction** — geometry and stackup; dialog substrate override; copper
from pads, tracks, arcs, vias, filled zones and graphic shapes (poly, rect,
ring, segment, arc, bezier); ground-return guard (fires on a plane-less
board, silent on a good one); clipped-copper warning; whole-board auto-fit
domain; loads the way KiCad loads it (`__import__("rfsim-dev")`). Real
6-layer board: 1388 polygons, 444 vias, 1.0 s.

**Solver** — 2-port MSL at coarse and medium (S11 <= -10.3 dB,
S21 >= -0.3 dB); 2-port lumped-port run (S11 <= -9.3 dB); 1-port `.s1p`
(cut trace terminates into PML, S11 ~ -21 dB); hand-written Touchstone
round-trips against skrf for n = 1..5.

**Lumped elements** — detection: `R*`/`L*`/`C*` footprints with exactly two
**numbered** pads, both SMD, both on one stackup copper layer, value parsed
from the Value field (RKM notation, SI prefixes, DNP variants; 21-case
self-check via `python board_reader.py`). Port-carrying footprints are
excluded, THT is warned and skipped. The element is a box bridging the pad
gap along the nearest axis, with mesh lines pinned to its edges so sub-mm
parts don't depend on cell snapping; `R=0` becomes a metal short. Series
50 ohm in a 50 ohm line reads S11 -10.7 / S21 -4.5 dB at 2 GHz against an
ideal -9.5 / -3.5 (a gap left open would read ~0 / -20+). R, L and C all
validated against closed-form theory: slopes over 1->5 GHz of +0.6 dB
(flat), -20.7 dB (falling) and +5.7 dB (rising).

**Multi-port** — any port count, one FDTD run per excited port; per-pad
port-number dropdown and Excite checkbox (`settings.excite` stores FINAL
port numbers, so renumbering and excitation compose); per-port E/H dumps
and NF2FF into `excN/[EH]f.h5` + `farfield_pN.json`.

**Results window** — 18 views render headless: S-parameter magnitude and
phase, Smith, VSWR, group delay, board layout, E/H field per port, and four
far-field views per port. Only *computed* S columns are plotted — a zero
column used to render as a fake perfect match. Field alignment checked
numerically, not by eye: energy centroid on the validation trace lands at
y = -10.000 mm. Far-field gives three cuts plus a rotatable 3D balloon with
the PCB drawn as a reference plate, and Dmax / Prad / efficiency / lobe
stats. Live click-through in pcbnew verified on KiCad 8.

**Robustness** — the runner clears stale `exc*` / `farfield*.json` from the
output directory before running, and aborts on a diverged (NaN) run instead
of failing obscurely inside `CalcPort`.

### Not yet verified

- [ ] A **live** click-through in pcbnew on KiCad 10 (SettingsDialog →
      RunDialog → ResultsFrame from a real selection). All three render
      headless and the plugin's interpreter/dep logic is tested, but
      nothing has been driven through the actual KiCad UI since KiCad 8.
- [ ] A board with vias / inner layers / zones with holes *through the
      solver* — extraction on such a board is now verified (6-layer demo,
      444 vias), but fractured-slit polygons and `AddCylinder` vias have
      still never reached openEMS.
- [ ] Lumped elements on a real matching network (two or more parts
      interacting) — only single-element boards have been validated.
- [ ] The stability heuristic on geometries other than `run_rlc.py`'s
      (different mesh preset, element box size, or εr).
- [ ] A real antenna simulation end-to-end (graphic-shape support was
      added for one; extraction is tested, a solver run on it is not)

## KiCad API notes

From probing the installed 9.0.8 and 10.0.5 — every pcbnew symbol and call
this plugin makes — plus the KiCad 8.0 source.

### Breaks between KiCad 8 and 10

| KiCad 8 | KiCad 10 |
|---|---|
| `SHAPE_POLY_SET.PM_FAST` | removed; the 1-arg `Simplify` / `Fracture` / `Boolean*` forms are correct |
| `PCB_ARC.GetArcAngle()` | `PCB_ARC.GetAngle()` |
| `PCB_SHAPE.IsFilled()` | `PCB_SHAPE.IsSolidFill()` |
| `PCB_VIA.GetWidth()` | `PCB_VIA.GetWidth(layer)` — vias gained per-layer padstacks |

Every one has a trap:

- `PCB_ARC` and `PCB_SHAPE` **diverged**: `PCB_ARC` has only `GetAngle`,
  `PCB_SHAPE` only `GetArcAngle`. A blanket rename breaks arc graphics.
- `ZONE.IsFilled()` is a different class and still valid. Don't rename it.
- `GetAngle()`'s sign convention matches what `_add_arc` expects (checked
  on a quarter arc), so no mirroring fix is needed.
- `PCB_VIA.GetWidth()` still returns the right number without a layer and
  only trips a debug assert, so a symbol survey cannot catch it — it turns
  up at runtime. The layer-aware call is also more correct: a via with
  different annular rings per layer is modelled per layer.

Roughly 70 further calls probed unchanged, including
`GetStandardLayerName`, `GetEnabledLayers().CuStack()`, the `BOX2I`
operations, `PAD.TransformShapeToPolygon`, `FlashLayer`, `GetRectCorners`,
`GetPolyShape`, `RebuildBezierToSegmentsPointsList`, `GetFilledPolysList`
and `ActionPlugin`.

### Text on copper, and the layer converter

`TransformTextToPolySet` needs the `ERROR_LOC` enum (absent on KiCad 8,
exposed as `pcbnew.ERROR_INSIDE == 1` on 9 and 10). But
`BOARD::ConvertBrdLayerToPolygonalContours(layer, polyset)` is wrapped on
all three versions and does the whole job C++-side: tracks, vias, pads,
footprint shapes **including text**, zones, board shapes, `PCB_TEXT`,
`PCB_FIELD`, text boxes and dimensions. Verified on 10 — a text item on
F.Cu came back as 3 of 9 outlines.

That single call would replace most of `_copper_polys` and all of the
hand-rolled polygonization (backlog #3), and it is more accurate than the
current math. On the validation board's 2.9 mm trace:

| source | points | area |
|---|---|---|
| exact stadium | — | 93.605 mm² |
| KiCad polygonizer | 44 | 93.579 mm² |
| `_stadium_pts`, 8-segment caps | 18 | 93.437 mm² |

Vias are worse: `_circle_pts(n=32)` gives 0.2795 mm² against 0.2827 exact.
Caveats for the rewrite: the converter has no region filter (keep the
`BooleanIntersection` with the region rect) and gives no per-item
information (keep the bbox-based clipped-copper count).

### True on 8, 9 and 10 alike

- **Bundled Python is 3.11**, so no KiCad version can install the openEMS
  wheels the solver needs. Hence the separate solver interpreter.
- **The stackup is opaque**: `GetStackupDescriptor()` returns a bare
  `SwigPyObject` and no `BOARD_STACKUP` type exists in any binding set.
  Hence `_stackup_from_file`'s s-expression parser, its FR4 fallback from
  `GetBoardThickness()` + copper count, and "save the board first".
- **`import wx.svg` fails** with `No module named 'wx.svg._nanosvg'` — see
  the wxPython traps for the stub.

### On the IPC API

The only route to the stackup, but not a replacement for the geometry side:
it exposes `get_pad_shapes_as_polygons()` and zone `filled_polygons`, while
`get_shapes()` explicitly excludes tracks and text — there is no
track/arc/text polygonizer, so a wholesale port would reinstate the
hand-rolled math and lose text again. It also needs KiCad running with the
API server enabled, which breaks the `LoadBoard`-based headless harness.

SWIG is deprecated as of KiCad 9.0 and
[planned for removal in 11.0](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/index.html),
which is the deadline for a full port.

## openEMS versions

Lumped inductors need openEMS from after the lumped-RLC merge (PR #121,
Nov 2023), which added `LEtype`: `0` = parallel (default), `1` = series.
The obstacle is packaging, not features:

| build | date | lumped L | wheels |
|---|---|---|---|
| v0.0.36 | Oct 2023 | ✗ (predates PR #121) | cp310, **cp311** |
| v0.0.36-90 / -93 | Oct 2025 | ✓ | cp313, cp314 |
| nightly -162 / -206 | May 2026 | ✓ | cp313, cp314 |
| v0.37.0-rc1 | Jun 2026 | ✓ | cp313, cp314 |

The only release with a cp311 wheel predates the feature; everything that
has it builds for 3.13/3.14 only (confirmed in the packaging CI,
`.github/workflows/windows-package.yml`). openEMS is not on PyPI, so there
is no source fallback short of building the Cython extensions yourself —
the `.pxd` files ship in the zip if it comes to that. KiCad bundles Python
3.11, so the solver needs its own interpreter.

Older engines log "Lumped Element R or C not specified! skipping" and
silently model an open circuit, which is why `runner.main` refuses
inductors when `_has_lumped_rlc()` says the engine can't do them.

Every openEMS signature this project uses is unchanged between v0.0.36 and
v0.37.

## Architecture / data flow

```
KiCad's Python 3.11                           solver Python 3.13/3.14
(pcbnew + wx + skrf)                          (numpy + CSXCAD + openEMS)
─────────────────────                         ──────────────────────────
__init__.py registers RFSimPlugin
rfsim.py Run():
  deps check (both sides)  ┐
  solverenv.solver_python()┘
  selected pads
  board_reader.extract(board, pads, margin)   runner.py model.json outdir:
    → model dict → model.json      ──────────→  _port_geometry → _mesh →
  gui.RunDialog streams stdout                  build CSX → FDTD.Run per
  gui.ResultsFrame(results.sNp)    ←──────────  excitation → CalcPort →
                                                results.s2p / .s1p
```

The two columns are separate **processes and interpreters**: a solver crash
never takes KiCad down, and the solver can use a Python that KiCad cannot
(openEMS ≥ v0.37 has no cp311 wheel). `solverenv.py` is the only module
imported by both, so it stays free of pcbnew, wx and numpy.

`board_reader.py` is the only module touching pcbnew for geometry. The
interface is the JSON-able model dict (all mm, y flipped to right-handed,
z=0 at board bottom, copper = zero-thickness sheets at dielectric
boundaries):

```
{ settings: {f_start, f_stop, f_field, z0, margin_mm, mesh, n_freq,
             max_timesteps, end_criteria,
             excite: [port numbers],    # omitted/None = excite all
             lumped: bool,              # model R/L/C parts?
             time_step_factor: float},  # optional; overrides the automatic
                                        # sub-Courant step for inductors
  copper_layers:     [{name, z, thickness}]          # top→bottom
  dielectric_layers: [{name, z_top, z_bottom, epsilon, loss_tangent}],
  region:     {x0,y0,x1,y1},   # crop rect = pads bbox + 2*margin
  board_rect: {x0,y0,x1,y1},   # region ∩ board edges (dielectric extent)
  polygons:   {"F.Cu": [[[x,y],...], ...], ...},     # fractured, no holes
  vias:  [{x, y, r, z0, z1}],
  lumped_elements: [{ref, type: "R"|"L"|"C", value,  # SI (ohm/H/F)
                     ny: "x"|"y", layer, start, stop,
                     pads: [[x,y],[x,y]]}],
  ports: [{number, label, x, y, layer, ref_layer, width, length,
           direction: [±1,0]|[0,±1]|null, track_width, type}] }
```

The split also means the solver side can run under a *different Python*
than KiCad's — the openEMS upgrade path depends on this.

Outputs per run: `results.sNp`, `excN/` per excited port (field dumps
`Ef.h5`/`Hf.h5` + NF2FF recordings) and `farfield_pN.json` per excited
port.

## Decisions that weren't obvious (with the bugs that forced them)

**pcbnew traps** (API version differences are in
[KiCad API notes](#kicad-api-notes)):

- `pcbnew.BOX2I()` default-constructs at origin (0,0) — `Merge` then
  wrongly includes the origin. Always init from the first pad's bbox.
- Footprint graphics are `PCB_SHAPE` in **board** coordinates, so no
  footprint transform is needed. Beziers work via
  `RebuildBezierToSegmentsPointsList` + `GetBezierPoints`.
- Don't reach for `GetEffectiveTextShape()`: its subshapes are opaque
  `SHAPE` wrappers whose `Format()`, `Clone()` and `Cast()` **hard-crash
  the interpreter**. Use
  [the layer converter](#text-on-copper-and-the-layer-converter) instead.

**KiCad-bundled wxPython/matplotlib traps** (both hit in the results
window, both still present on 9 and 10):

- KiCad's bundled wxPython ships `wx/svg/` **without the compiled
  `_nanosvg` extension** (only .pyx/.c sources), so `import wx.svg`
  always fails. matplotlib's wx backend does `import wx.svg  # noqa: F401`
  purely as a side effect and never uses it → before importing
  `backend_wxagg`, try the import and on failure stub
  `sys.modules["wx.svg"]` with an empty module (`gui.ResultsFrame`).
- Don't write `import wx.svg` inside a function that also uses `wx` — the
  statement binds the name `wx` as a *local*, and when the import raises,
  every later `wx.*` in that function dies with UnboundLocalError. Probe
  with `importlib.import_module("wx.svg")` instead, which binds nothing.

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
- The FD field-dump HDF5 layout **changed in v0.37**: one native-complex
  `FieldData/FD/f0` (attrs `d_order='NXYZ'`, root `openEMS_HDF5_version`)
  instead of v0.0.36's float32 `f0_real`/`f0_imag` pair. `gui._load_field`
  handles both, and its axis normalisation (squeeze → component axis last →
  swap to `[y, x, 3]`) already covered the reordering. Nothing errors if
  you get the order wrong — the plot just comes out transposed — so check
  the energy centroid against the known geometry, don't eyeball it.
- `AddDump` defaults to `dump_mode=0` (no interpolation): raw staggered
  Yee-grid values plotted at mesh-node positions render the in-plane H
  field half a cell off the copper (user-visible as the patch H pattern
  shifted right by ~1 mm). Always pass `dump_mode=1` (node interpolation)
  for dumps meant for plotting.
- `MSLPort` requires start≠stop in all three coords, strip plane at
  `start[2]`, and ≥5 mesh lines along propagation **before** the port is
  created — hence mesh is built before `AddMSLPort` in `build()`.
- `CalcNF2FF` center must be passed in METERS and lie inside the
  recording box (the default `[0,0,0]` is outside for real board
  coordinates → "invalid center" class errors).
- Lumped elements: openEMS ≤ v0.0.36 implements R and C only and
  **silently drops** a pure-L element, logging "Lumped Element R or C not
  specified! skipping" — an open circuit, not an error. Hence the guard,
  now gated on a feature probe rather than assumed.
- Unspecified components of a lumped element are **NaN, not 0**, and the
  engine reads NaN as "absent". That is why `LEtype` (series vs parallel)
  cannot change a single-component element.
- A lumped inductor **diverges** at the full Courant timestep and openEMS
  writes `-nan(ind)` into the port files rather than erroring, so the
  failure surfaces as a float-parse crash inside `CalcPort`. See
  [Lumped inductor stability](#lumped-inductor-stability).
- openEMS ≥ v0.37's README asks for a global `CSXCAD_INSTALL_PATH`; it is
  not needed — `os.add_dll_directory` alone works (verified on v0.37.0-rc1),
  and the runner already does that.

**Debugging playbook — S11 smooth −20…−30 dB, no resonance anywhere**:
suspiciously *good* broadband match = the structure under test was CUT at
the region boundary and its stump terminates into the PML like a matched
load. Seen live: TI SWRA117D meander antenna (~26 mm) at the default 4 mm
margin (18.8 mm region). Two defenses now: (1) the domain auto-fits the
whole board via `GetBoardEdgesBoundingBox()` merged into the region, and
(2) extract() counts non-zone copper crossing the region edge into
`model["warnings"]` (shown after the settings dialog; zones are exempt
because pours are *supposed* to be cut — the warning now mostly matters
for boards without an Edge.Cuts outline).

**Debugging playbook — S11 ≈ 0 dB flat, S21 −40…−55 dB rising with f**:
that's two floating pads coupling capacitively, i.e. the ports have no
ground return (or the copper between them never reached the model). It is
*correct physics for a broken setup*, not a solver bug. Seen live on a
board with netless pads and no B.Cu plane; that incident produced the
ground-return guard in `extract()` and the graphic-shape support. Compare
against `validation/` first — if the microstrip board passes, the pipeline
is fine and the input board is the problem.

**wx dialog traps**:

- Substrate presets: apply values with `TextCtrl.ChangeValue()` (no
  EVT_TEXT), not `SetValue()` — the er/tanδ fields have an EVT_TEXT handler
  that flips the preset dropdown to "Custom" on user edits, and SetValue
  would fire it during preset application too.
- Headless GUI testing works fine on Windows: `wx.App(False)`, build the
  dialog/frame, exercise logic, `ScreenDC` blit after `Show()` + a few
  `wx.Yield()`/`Update()` rounds for a screenshot (one Yield is not enough,
  the first capture came back unpainted). Beware: ScreenDC blit coords can
  be off with display scaling/multi-monitor — `figure.savefig` after the
  size settles is the reliable way to check what the canvas really shows.
- `FigureCanvasWxAgg` reports the figure's native pixel size (800×550 for
  an 8×5.5 in figure) as its wx **minimum size**, so sizers CLIP it at the
  bottom instead of shrinking it — the x-axis label is invisible until the
  user resizes. Fix trio: `canvas.SetMinSize((320, 240))`,
  `wx.CallAfter(self.SendSizeEvent)` at the end of `__init__` (no size
  event fires on first Show), and `Figure(layout="constrained")` instead
  of a one-shot `tight_layout()` so labels survive every later resize.

**Modeling choices** (fine to revisit):

- Copper loss via `AddConductingSheet` (5.8e7 S/m, real thickness, floored
  at 0.1 µm so thin user-entered copper isn't silently clamped);
  dielectric loss as kappa at center frequency.
- GUI substrate values (er/tanδ/h/cu_t) always override the board file's
  stackup — simpler than merging, and the dialog shows exactly what will
  be simulated. File stackup parsing survives for headless use.
- Lumped-element terminals are the **numbered** pads, not all pads. Real
  footprints carry extra unnumbered copper (mechanical / paste-relief)
  pads that cannot hold a net and are not terminals — found live on a
  KiLib `SMD_2terminal_chip_molded` resistor with **4** pads, two of them
  unnumbered, which a plain `len(fp.Pads()) == 2` test rejected outright.
  The unnumbered copper is still simulated, just through `_copper_polys`
  like any other pad. A footprint whose value parses but whose terminal
  count isn't 2 now warns instead of vanishing silently.
- Ground-return guard = bbox overlap between the pad and any ref-layer
  polygon (overlap, not center containment — antenna feed pads sit at the
  ground pour *edge*, e.g. TI SWRA117D IFA). Upgrade to point-in-polygon
  if odd-shaped pours false-positive.
- Mesh presets = λ_min/{10,20,40} + fixed lines at polygon bbox edges, port
  edges/centers, layer planes, ≥4 cells per dielectric. No 1/3-2/3 rule.
- Full S-matrix = one run per excited port (2 ports → 2 runs). No
  reciprocity shortcut.
- Touchstone written by hand in `runner.write_touchstone` (RI format;
  remember `.s2p` column order is S11 S21 S12 S22).
- Port reference layer = adjacent copper layer in the stackup, not a
  copper-under-pad search.
- `Fracture` turns zone holes into zero-width slits; CSXCAD rasterizes
  these fine. Switch to explicit hole subtraction if artifacts show up.

## Dev commands

```bat
:: KiCad 10's interpreter -- per-user install (installer default).
:: Machine-wide installs live in "C:\Program Files\KiCad\10.0\bin".
set KIPY="%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe"

:: full end-to-end check (also regenerates the test board + model.json)
%KIPY% validation\run_headless.py coarse [msl|lumped]

:: lumped-element check: series 50-ohm R in a 50-ohm line vs ideal divider
%KIPY% validation\run_lumped.py coarse

:: all three element types vs closed-form theory (R flat, L falls, C rises)
%KIPY% validation\run_rlc.py coarse
%KIPY% validation\run_rlc.py medium L1      :: one case at a finer mesh

:: "why isn't my R/L/C part simulated?" -- gate-by-gate verdict per part
%KIPY% validation\diag_lumped.py board.kicad_pcb
:: ...or against the LIVE board incl. unsaved edits, in pcbnew's
:: Tools > Scripting Console:
::   import sys; sys.path.insert(0, r"<repo>\validation")
::   import diag_lumped; diag_lumped.report()

:: value-parser self-check / Touchstone round-trip (fast, no solver)
%KIPY% board_reader.py
%KIPY% validation\test_touchstone.py

:: rerun the solver on an existing model, with the SOLVER python (not KiCad's)
C:\openEMS\venv\Scripts\python.exe runner.py validation\out_coarse\model.json out

:: deploy this checkout into KiCad's plugin dir (KiCad runs the COPY, not
:: the repo) -- then restart KiCad, or at least Tools > External Plugins >
:: Refresh, because stale .pyc files survive an edit
set RFDEST=%USERPROFILE%\Documents\KiCad\10.0\3rdparty\plugins\com_github_nbalciunas_kicad-rfsim
copy /Y *.py "%RFDEST%\"
rmdir /S /Q "%RFDEST%\__pycache__"
```

**The plugin KiCad runs is a copy.** Editing this checkout changes nothing
until you copy the `.py` files across, and a stale `__pycache__` can keep
serving the old module even after you do. A symptom seen live: the E/H-field
views kept failing with `object 'f0_real' doesn't exist` after that bug was
fixed in the repo, purely because the deployed `gui.py` was older. When
something is fixed but still broken in KiCad, diff the two copies first.

`validation/out_*/model.json` is the fastest debugging entry point — edit it
by hand and rerun `runner.py` under the solver venv to iterate on
solver-side code with zero KiCad involvement. Useful knobs to edit in
`settings`: `max_timesteps` (drop to a few thousand for a quick stability
check), `time_step_factor`, `excite` (one port halves the runtime), `mesh`.

Note the two interpreters: everything that touches `pcbnew` needs `%KIPY%`,
and everything that touches `openEMS` needs the venv. The validation
harnesses run under `%KIPY%` and spawn the venv themselves.

## Capability matrix

What openEMS v0.37 offers versus what this plugin uses. Enumerated by
introspecting the installed engine, not from the docs. ✅ used · 🟨 partial ·
⬜ available and unused. The ⬜ rows are the honest roadmap — the backlog
below only picks off the near ones.

### Geometry (KiCad side)

| Capability | | Extent |
|---|---|---|
| Pads, tracks, arcs, vias | ✅ | Polygonised; via annulus per layer (padstack-aware) |
| Filled zones | ✅ | Fractured; holes become zero-width slits |
| Graphic shapes on copper | ✅ | Poly, rect, circle, ring, segment, arc, bezier — board + footprint |
| Multi-layer stackups | ✅ | Verified 6-layer: 1388 polys, 444 vias, 1.0 s |
| Stackup εr / tanδ / thickness | 🟨 | Parsed from the saved file, or dialog override — no SWIG access, so the board must be saved |
| Text on copper | ⬜ | Warned, not modelled; `ConvertBrdLayerToPolygonalContours` would do it |
| Domain auto-fit | ✅ | Edge.Cuts bbox + 2× margin |

### Ports — 2 of 9

| Capability | | Extent |
|---|---|---|
| `AddLumpedPort` | ✅ | Vertical excitation under the pad; works anywhere |
| `AddMSLPort` | ✅ | De-embedded microstrip, feed direction from the attached track |
| `AddCPWPort` | ⬜ | **Coplanar waveguide — very common in RF layout** |
| `AddStripLinePort` | ⬜ | **Any inner-layer trace** |
| `AddCoaxialPort` | ⬜ | Coax / SMA transitions |
| `AddRectWaveGuidePort`, `AddCircWaveGuidePort`, `AddWaveGuidePort`, `AddCurvePort` | ⬜ | Waveguide work; out of scope for PCB |

CPW and stripline structures currently have to go through a lumped or
microstrip port, which mis-defines the reference plane.

### Excitation — 1 of 5

| Capability | | Extent |
|---|---|---|
| `SetGaussExcite` | ✅ | Broadband, centred on the sweep |
| `SetStepExcite` | ⬜ | **TDR — locate impedance discontinuities along a trace** |
| `SetSinusExcite` | ⬜ | Single-frequency steady state; much cheaper for one frequency |
| `SetDiracExcite`, `SetCustomExcite` | ⬜ | Impulse / arbitrary waveform |
| `AddExcitation` (field, not port) | ⬜ | **Plane-wave illumination → EMC immunity, RCS** |

### Lumped elements

| Capability | | Extent |
|---|---|---|
| R, L, C single component | ✅ | Validated against theory (`run_rlc.py`) |
| 0 Ω → copper short | ✅ | `AddMetal` instead of an element |
| Series topology `LEtype=1` | ✅ | Correct for a part bridging a gap |
| Inductor timestep stability | ✅ | Auto `1/√L`, step budget scaled, NaN detection |
| Multi-component R+L+C in one element | ⬜ | The parasitics path — 0402 ESL ≈ 0.3–0.5 nH |
| Parallel topology `LEtype=0` | ⬜ | Only matters once ≥2 components share an element |

### Materials — isotropic only

| Capability | | Extent |
|---|---|---|
| `AddConductingSheet` | ✅ | Copper, 5.8e7 S/m, real thickness |
| `AddMaterial` εr + κ | 🟨 | Isotropic scalar; tanδ → κ at **centre frequency only** |
| `AddMetal` | ✅ | Vias, shorts |
| Anisotropic εr / μr / κ | ⬜ | Takes *vectors* — woven-glass FR4 anisotropy, differential skew |
| Dispersive (Lorentz, Debye) | ⬜ | True wideband εr(f) instead of one-frequency κ |
| `mue` / `sigma` | ⬜ | **Ferrites**, magnetic materials |
| `density` | ⬜ | Required for SAR |
| `AddDiscMaterial` | ⬜ | Voxel data (imported body models) |
| `AddAbsorbingBC` | ⬜ | Lossy terminations as a material |

### Primitives — 3 of 14

| Capability | | Extent |
|---|---|---|
| `AddBox`, `AddLinPoly`, `AddCylinder` | ✅ | Dielectrics, copper sheets, vias |
| `CSPrimPolyhedronReader` | ⬜ | **STL import — antenna inside its real enclosure or connector** |
| `RotPoly`, `Sphere`, shells, `Wire`, `Curve`, `MultiBox`, `Point` | ⬜ | Rotationally symmetric bodies, wires, 3D shapes |

### Mesh, boundaries, engine

| Capability | | Extent |
|---|---|---|
| `SmoothMeshLines` + hand-placed hints | ✅ | λ/{10,20,40}; lines pinned at poly/port/element edges, ≥4 cells per dielectric |
| `PML_8` boundaries | ✅ | All 6 faces, explicitly meshed 8-cell band |
| `SetTimeStepFactor` | ✅ | Automatic when inductors are present |
| PEC / PMC symmetry planes | ⬜ | **Would halve or quarter runtime on symmetric structures** |
| `AddEdges2Grid` | ⬜ | **Automatic refinement at material edges — would improve every run** |
| `SetMultiGrid` | ⬜ | Sub-gridding |
| `SetCylinderCoords` | ⬜ | Cylindrical FDTD (coax, circular structures) |
| Thread count / engine selection | ⬜ | Via v0.37's `SetLibraryArguments()` |

### Outputs — dumps 2 of 12, probes 0 of 6

| Capability | | Extent |
|---|---|---|
| S-parameters → Touchstone | ✅ | Full N×N, `.s1p`…`.sNp`, round-trips vs skrf |
| Magnitude, phase, Smith, VSWR, group delay | ✅ | Only *computed* columns plotted |
| FD E/H dumps (10, 11) | ✅ | Substrate mid-plane, node-interpolated, per excited port |
| NF2FF far-field | ✅ | 3 polar cuts + 3D balloon, Dmax, Prad, efficiency, lobe stats |
| Current / current-density dumps (2, 3, 12, 13) | ⬜ | **Where the current actually flows — EMC hot spots, return paths** |
| Time-domain E/H dumps (0, 1) | ⬜ | Only frequency domain is used |
| SAR (20, 21, 22, 29) | ⬜ | Local, 1 g, 10 g averaged; `sar_calc.exe` already ships in the install |
| `AddProbe` (V, I, E, H, mode matching) | ⬜ | Only what the port objects create internally |

### Where FDTD is the wrong tool

Worth knowing the ceiling before promising any of the above: high-Q
structures (narrowband filters, resonators) need very long runs because
energy decays slowly; electrically small features inside a large domain
blow up the cell count; there is no eigenmode or frequency-domain solver,
so no direct modal analysis; and there is no optimisation loop, so tuning
a matching network means re-running by hand.

## Backlog (rough priority)

Near-term work on what already exists. For unused engine capability — port
types, current dumps, SAR, TDR — see
[Bigger directions](#bigger-directions) at the end and the
[capability matrix](#capability-matrix).

1. **Live click-through in pcbnew on KiCad 10** — the only thing never
   driven through the real UI since KiCad 8. Everything it depends on is
   verified headless (18/18 views, both interpreters, dep probe).
2. Package parasitics for lumped parts, now that series RLC works:
   a real 0402 capacitor has ~0.3–0.5 nH ESL, putting its self-resonance
   inside typical sweep ranges — ideal elements mispredict GHz matching
   networks. Per-package-size defaults + optional override. Needs
   `LEtype=1`.
3. **Rewrite `_copper_polys` on `ConvertBrdLayerToPolygonalContours`**
   (finding 2) — deletes `_stadium_pts`/`_circle_pts`/`_add_arc`/
   `_add_shape` (~100 lines), gains text on copper, improves curve
   fidelity. Keep the region `BooleanIntersection` and the bbox-based
   clip warning. Expect validation numbers to shift.
4. Surface the lumped-inductor timestep cost in the dialog: a warning that
   inductors multiply the runtime, and a `time_step_factor` field (today
   it is model.json-only). Pairs with item 8.
5. Validation board #2 with vias + zone holes (e.g. CPWG segment or stub
   filter) to push those geometry paths through the *solver* — extraction
   on them is now covered by the 6-layer demo-board check.
6. Antenna validation: compare a full antenna run (S11 dip frequency, Dmax,
   pattern shape) against a published reference, e.g. TI SWRA117D.
7. Prefill the dialog's substrate fields from the board stackup — needs
   either the file parser (works today) or IPC `board.get_stackup()`.
8. Expose max timesteps / end criteria in the dialog for high-Q structures
   (currently fixed 300k / 1e-4 in `gui.get_settings`).
9. `model.json` version field — compat handling is accumulating as
   scattered `.get(key, default)` calls (excite, lumped, pads,
   time_step_factor, …).
10. PCM packaging (zip layout with `plugins/` subfolder) if this should
    ship through the Plugin and Content Manager. Note the solver venv is
    *not* packageable — it stays a documented install step.
11. Full IPC API port — only worth it before KiCad 11 removes SWIG, and
    only once IPC can polygonize tracks and text. See
    [On the IPC API](#on-the-ipc-api).

### Bigger directions

Not numbered because they're features, not chores. Each is an ⬜ row in the
[capability matrix](#capability-matrix), roughly cheapest first:

- **CPW and stripline ports** (`AddCPWPort`, `AddStripLinePort`). The best
  value of anything here: both are structures people actually draw in
  KiCad, and today they can only be fed through a lumped or microstrip
  port, which puts the reference plane in the wrong place.
- **Mesh efficiency, no new features needed**: `AddEdges2Grid` for
  automatic refinement at material edges, and PEC/PMC **symmetry planes**,
  which cut runtime 2–4× on any symmetric board. These make every
  existing simulation better or faster.
- **Current-density dumps** (types 12/13) — where the current actually
  flows. EMC hot spots and return-path problems, and a view no other free
  KiCad tool offers.
- **Material fidelity**: dispersive (Lorentz/Debye) εr(f) instead of κ at
  one frequency, and anisotropic εr for woven-glass FR4. Matters most for
  wideband and differential-skew work.
- **TDR** via `SetStepExcite` — locate an impedance discontinuity along a
  trace rather than just seeing it in S11.
- **STL import** via `CSPrimPolyhedronReader` — simulate an antenna inside
  its real enclosure or with a connector body attached.
- **SAR** (dump types 20/21/22; `sar_calc.exe` already ships) — the
  compliance path for anything worn or handheld. Needs material `density`.
- **Plane-wave excitation** via `AddExcitation` — EMC immunity and radar
  cross-section, a category rather than a feature.

Done previously: openEMS v0.37 + working R/L/C lumped elements and the
results window re-verified (2026-07-30, see Status); the KiCad 10 port
(2026-07-30); live GUI
click-through on KiCad 8 (works, incl. two GUI-only crashes fixed:
wx.svg stub + UnboundLocalError — see traps above); graphic shapes on
copper (poly/rect/circle/segment/arc, board + footprint).

## License

MIT — Copyright © 2026 Nojus Balčiūnas

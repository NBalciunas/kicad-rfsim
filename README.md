<img align="right" width="60px" src="assets/icon.png">

# RFsim

KiCad 8 action plugin: simulate S-parameters of an RF structure directly
from the PCB editor with [openEMS](https://openems.de) (FDTD). Geometry
goes straight from KiCad's native board objects to CSXCAD primitives —
no gerbers, no rasterizing.

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
- **SMD R/L/C parts simulated as lumped elements**: 2-pad footprints with
  reference R*/L*/C* inside the region become openEMS lumped elements
  bridging their pad gap; values parsed from the Value field ("4k7",
  "100nF", "3n3", "DNP" skipped). 0-ohm resistors become metal shorts.
  Toggle in the dialog; skipped parts produce warnings.
- Any number of ports (one FDTD run per excited port); a per-pad number
  dropdown sets the port order (port 1 drives the E/H-field and far-field
  views), and per-port "Excite" checkboxes let you skip S-columns you
  don't need.
- Sanity guard: refuses to run a port with no copper on its reference
  layer under the pad (no ground return = meaningless total reflection).
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

1. Open your board, click a pad (port 1), shift-click further pads for
   more ports.
2. Tools → External Plugins → RFsim.
3. Fill in the dialog: sweep range and "Define at" (the frequency used for
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
4. Run Simulation. Results open in a plot window; `results.sNp`,
   `model.json` and `farfield_pN.json` land in the output directory.

The dialog's substrate values always win: GUI runs build a uniform stackup
from them (multi-layer boards split h evenly between dielectric layers).
The board file's `(stackup ...)` section is only used by headless runs
without an override — KiCad 8's Python API doesn't expose the stackup, so
that path needs a saved board.

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

- Lumped elements are ideal (no package parasitics) and require both pads
  on the same outer copper layer.
- **Inductors (L*) are refused**: openEMS ≤ v0.0.36 only implements R and
  C lumped elements and silently drops pure-L ones (open circuit → wrong
  results), so the runner aborts with a clear error instead. Set the
  value to DNP or upgrade openEMS to ≥ v0.37 (lumped RLC support —
  needs plugin changes, see dev notes).
- Simulated area = the whole board (Edge.Cuts bbox, merged with the
  selected pads' bbox; pads-only if the board has no outline) + 2× margin.
  Copper crossing that boundary is cut and terminates into the absorber.
- The reference layer for a port is the adjacent copper layer in the
  stackup (e.g. F.Cu → the first inner/bottom layer).
- Everything on a copper layer is simulated — pads, tracks, arcs, vias,
  filled zones, and graphic shapes including beziers — **except text**,
  which KiCad 8's Python API cannot convert to polygons. Text inside the
  simulated area triggers a warning dialog instead of being silently
  dropped.

## Validation

`validation/` contains a generator for a 30 mm ~50 Ω microstrip board and a
headless end-to-end check:

```bat
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\run_headless.py medium
```

Expected: S11 < −10 dB, S21 > −0.5 dB across 1–6 GHz, `PASS`.

`validation\run_lumped.py` checks the lumped-element path: a series 50 Ω
resistor in a 50 Ω line must read S11 ≈ −9.5 dB / S21 ≈ −3.5 dB (the
ideal resistive divider), which cleanly separates "element simulated"
from "gap left open".

---

# Development notes

Everything below is for picking up development later, not for users.

## Status (2026-07-10, fourth session)

All verified against real solver runs / headless GUI renders.

**Lumped elements** (new feature):

- [x] Detection in `board_reader._lumped_elements`: 2-pad SMD R*/L*/C*
      footprints in the region, both pads on one copper layer in the
      stackup; THT parts warned + skipped (barrel isn't modeled). Values
      parsed from the Value field — RKM notation, SI prefixes, DNP
      variants (self-check: `python board_reader.py`, 21 cases).
      Element = box bridging the pad gap along the nearest axis, pad
      centers stored in `"pads"`; port-carrying footprints skipped.
- [x] Solver side: `AddLumpedElement(ny=..., caps=True, R|C=value)`,
      R=0 → metal short; mesh lines pinned at element box edges (sub-mm
      parts must not rely on cell snapping). End-to-end
      `validation/run_lumped.py coarse`: series 50 Ω in a 50 Ω line →
      S11 −10.7 / S21 −4.5 dB at 2 GHz (ideal −9.5/−3.5; a gap left open
      would read ~0/−20+) PASS
- [x] **Inductor guard**: openEMS ≤ v0.0.36 implements only R and C
      lumped elements — pure L logs "Lumped Element R or C not
      specified! skipping" and is silently DROPPED (open circuit; found
      live via a 20 nH L1) → `runner.main` aborts with a clear error
      instead. Upgrade path investigated: lumped RLC incl. L merged
      upstream Nov 2023 (openEMS PR #121, `LEtype=1` = series topology),
      but v0.37.0-rc1 ships cp313/cp314 wheels only and KiCad 8 is
      Python 3.11 → needs a configurable solver interpreter (runner.py
      never imports pcbnew). See backlog #1.
- [x] Board layout view: element drawn as green pad-to-pad line with
      dots + gap box + ref label + "R/L/C" legend (the gap box alone is
      sub-mm, invisible at board zoom); hidden when `settings.lumped`
      is off.

**Multi-port**:

- [x] Any port count: runner loops N excitations, Touchstone N≥3
      row-major wrapping (round-trip vs skrf for n=1..5:
      `validation/test_touchstone.py`); rfsim.py accepts ≥1 pads.
- [x] Dialog: per-pad port-number dropdown (replaces the swap checkbox —
      KiCad doesn't expose click order, so numbering must be explicit;
      permutation validated) + per-pad Excite checkbox
      (`settings.excite` stores FINAL port numbers so renumbering and
      excitation compose; ≥1 excited enforced). rfsim.py reorders pads
      *and* port_types by the chosen numbers.
- [x] Per-port results: E/H dumps + NF2FF recorded for EVERY excited
      port → `excN/[EH]f.h5` + `farfield_pN.json`; GUI adds one dropdown
      entry per port, "(Port x)" suffixed to entries and titles when
      more than one (plain `farfield.json` still read as legacy).

**Results window**:

- [x] "S-Parameters [Magnitude]" + new "S-Parameters [Phase]" (wrapped
      phase, axis "°"); Smith chart + VSWR plot every *computed* S_ii
      (un-excited ports skipped — a zero column used to render as a fake
      perfect match); Smith legend labels bare "S11"; "Group delay"
      plots every computed S_jk pair in one graph (axis
      "Group delay / ns").
- [x] Field dumps node-interpolated (`dump_mode=1`): the default raw
      staggered-Yee values plotted at node positions drew H half a cell
      off the copper (user-visible on a patch). Alignment verified:
      E/H centroid on the validation trace = −10.000 mm exactly.

**Robustness**:

- [x] Runner deletes stale `exc*`/`farfield*.json` from the outdir
      before running (GUI picked up leftovers when re-running into the
      same folder with a different excite set).
- [x] MSL regression re-run after all refactors: `run_headless.py
      coarse` PASS (S11 ≤ −10.3 dB, S21 ≥ −0.3 dB).

## Status (2026-07-09, third session)

Verified working (headless, real openEMS runs on this machine):

- [x] Geometry + stackup extraction from a generated 2-layer board
- [x] 2-port MSL run, coarse & medium mesh (S11 ≤ −10.6 dB, S21 ≥ −0.3 dB)
- [x] 2-port lumped run (S11 ≤ −9.3 dB)
- [x] 1-port → `.s1p` (cut trace terminates into PML, S11 ≈ −21 dB as expected)
- [x] Package imports the way KiCad loads it (`__import__("rfsim-dev")`)
- [x] skrf reads the hand-written Touchstone files
- [x] **Live GUI click-through in pcbnew** (user-tested on a real board):
      SettingsDialog → RunDialog streaming → ResultsFrame plots all work
- [x] Substrate override from the dialog (er/tanδ/h/cu_t → uniform stackup)
- [x] Graphic-shape copper: poly patch, rect ground, stroked circle ring,
      segment — extracted and accepted by the ground-return guard
- [x] Ground-return guard fires on a plane-less board, silent on a good one
- [x] Clipped-copper warning (structure cut at region edge -> fake matched
      S11; found via TI meander antenna at default margin)
- [x] Board-layout + E/H-field animation views (FD dumps `Ef.h5`/`Hf.h5`
      in exc1/, one plane at substrate mid, one frequency f0; loader
      `gui._load_field` handles the (3, Nz, Ny, Nx) float32 layout, mesh
      stored in meters). All 8 ResultsFrame views render headless via
      wx.App(False) + savefig
- [x] Far-field via NF2FF: `openEMS.nf2ff.nf2ff(csx, name, start, stop,
      frequency=[...])` box in the clear-air band (region inset by
      1.5×margin), FD recording at `settings["f_field"]` (the dialog's
      "Define at" frequency; falls back to band center for old models —
      same frequency drives the Ef/Hf dumps). Writes `farfield.json`
      (D_dBi[phi][theta], Dmax_dBi, efficiency). `CalcNF2FF` center must
      be passed in METERS and lie inside the box (default [0,0,0] is
      outside for real board coordinates -> "invalid center" class errors).
      Thru-line sanity: Dmax 5.6 dBi, 0.7% radiated
- [x] Whole-board auto-fit domain (Edge.Cuts bbox merged into the region;
      margin = air around the PCB) after a patch antenna got cropped twice
- [x] Settings dialog redesign: "RFsim v1.0" header + icon, sections
      Frequency / Port / Substrate / Simulation, units after each field,
      substrate presets (FR-4, Rogers RO4350B, Custom), "Define at"
      frequency, centered Run button. Logic + screenshot verified headless
      (wx.App(False), ScreenDC blit)
- [x] "Define at" f_field drives Ef/Hf dumps and NF2FF — verified 2 GHz
      end-to-end (dump attrs + farfield.json)
- [x] H-field view restyled to match E (dominant in-plane component,
      signed red/blue); clip warning consolidated to one message, titles
      cleaned up per user wording
- [x] Results-window polish pass (user-driven, CST as the reference look):
      window/dropdown renames (S-Parameters, Smith Chart, VSWR), titles
      "S-Parameters [Magnitude]" / "[Impedance View]" / "Voltage Standing
      Wave Ratio (VSWR)", axes "dB" & "Frequency / GHz", Smith grid
      numbers via skrf `draw_labels=True`, E/H/Farfield dropdown entries
      carry "(f=xx GHz)". Farfield cuts: title "Farfield Directivity Abs
      (cut)", bottom caption "Theta|Phi / ° vs dBi", bare angle numbers,
      dBi labels at `set_rlabel_position(270)` with 10 dB rings, and a
      right-hand stats panel from `gui._lobe_stats` (main lobe
      magnitude/direction, 3 dB width, side-lobe level, circular-walk
      implementation)
- [x] First-show canvas clipping fixed (x-axis label was invisible until a
      manual resize; see wx traps below)
- [x] Farfield 3D balloon: third NF2FF pass on a 5° full-sphere grid ->
      `grid3d` in farfield.json; `_plot_farfield3d` renders plot_surface
      with jet facecolors + dBi colorbar; mouse-rotatable in the wx canvas
- [x] 3D balloon made transparent (facecolor alpha 0.3) with the PCB drawn
      as a reference plate via `Poly3DCollection` (B.Cu/F.Cu/ports at
      z=∓dz, centred, scaled so max board dim ~= half the balloon radius —
      the dB-unit radius has no physical scale vs mm, so board size is a
      display choice / orientation marker only)

Not yet verified:

- [ ] A board with vias / inner layers / zones with holes (fractured-slit
      polygons and `AddCylinder` vias are untested against a real solver run)
- [ ] Boards with a customized (non-default) stackup section in the file
      (now a headless-only code path — GUI always overrides)
- [ ] A real antenna simulation end-to-end (the graphic-shape support was
      added for one; extraction is tested, a solver run on it is not)

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
{ settings: {f_start, f_stop, f_field, z0, margin_mm, mesh, n_freq,
             max_timesteps, end_criteria,
             excite: [port numbers],    # omitted/None = excite all
             lumped: bool},             # model R/L/C parts?
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

Outputs per run: `results.sNp`, `excN/` per excited port (field dumps
`Ef.h5`/`Hf.h5` + NF2FF recordings) and `farfield_pN.json` per excited
port.

## Decisions that weren't obvious (with the bugs that forced them)

**KiCad 8 SWIG gaps** (all discovered the hard way):

- `BOARD_STACKUP` is not wrapped at all (`GetStackupDescriptor()` returns an
  opaque SwigPyObject) → stackup is parsed from the saved file's
  `(stackup ...)` s-expression; fallback = FR4 defaults from
  `GetBoardThickness()` + copper count. Hence "save the board first".
- The `ERROR_LOC` enum values exist nowhere in the bindings. Only
  `PAD.TransformShapeToPolygon(ps, layer, clearance, maxErr)` works (its
  wrapper has a C++ default for the enum); `PCB_SHAPE`'s overload demands
  the enum → unusable. Tracks/arcs/via annulars use plain math
  (`_stadium_pts`/`_circle_pts`); graphic shapes use `GetPolyShape()` /
  `GetRectCorners()` / center+radius + the same math (`_add_shape`).
  Footprint graphics are `PCB_SHAPE` in board coordinates in KiCad 8 —
  no footprint transform needed. Beziers: `RebuildBezierToSegmentsPointsList`
  + `GetBezierPoints` works fine.
- **Text is unreachable, don't retry**: `TransformTextToPolySet` needs the
  unwrapped `ERROR_LOC`; `GetEffectiveTextShape()` subshapes are opaque
  `SHAPE` wrappers whose `Format()`, `Clone()` and `Cast()` all **hard-crash
  the interpreter** (access violations, tested 2026-07-08). Text on copper
  inside the region → collected into `model["warnings"]`, shown as a wx
  warning dialog (GUI) and printed by the runner.
- `pcbnew.BOX2I()` default-constructs at origin (0,0) — `Merge` then wrongly
  includes the origin. Always init from the first pad's bbox.

**KiCad-bundled wxPython/matplotlib traps** (both hit in the results window):

- KiCad 8's bundled wxPython ships `wx/svg/` **without the compiled
  `_nanosvg` extension** (only .pyx/.c sources), so `import wx.svg` always
  fails. matplotlib's wx backend does `import wx.svg  # noqa: F401` purely
  as a side effect and never uses it → before importing
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
- `AddDump` defaults to `dump_mode=0` (no interpolation): raw staggered
  Yee-grid values plotted at mesh-node positions render the in-plane H
  field half a cell off the copper (user-visible as the patch H pattern
  shifted right by ~1 mm). Always pass `dump_mode=1` (node interpolation)
  for dumps meant for plotting.
- `MSLPort` requires start≠stop in all three coords, strip plane at
  `start[2]`, and ≥5 mesh lines along propagation **before** the port is
  created — hence mesh is built before `AddMSLPort` in `build()`.

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

## Dev commands

```bat
:: full end-to-end check (also regenerates the test board + model.json)
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\run_headless.py coarse [msl|lumped]

:: lumped-element check: series 50-ohm R in a 50-ohm line vs ideal divider
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\run_lumped.py coarse

:: value-parser self-check / Touchstone round-trip (fast, no solver)
"C:\Program Files\KiCad\8.0\bin\python.exe" board_reader.py
"C:\Program Files\KiCad\8.0\bin\python.exe" validation\test_touchstone.py

:: rerun the solver on an existing model without touching pcbnew
"C:\Program Files\KiCad\8.0\bin\python.exe" runner.py validation\out_coarse\model.json out
```

`validation/out_*/model.json` is the fastest debugging entry point — edit it
by hand and rerun `runner.py` to iterate on solver-side code with zero KiCad
involvement.

## Backlog (rough priority)

1. openEMS ≥ v0.37 upgrade to unlock lumped inductors: run `runner.py`
   under a standalone Python 3.13 with the new cp313 wheels (configurable
   solver interpreter — runner.py never imports pcbnew), send inductors
   as `AddLumpedElement(..., L=..., LEtype=1)`, delete the L guard,
   re-run the whole validation suite against the new engine.
2. Package parasitics for lumped parts once series RLC is available:
   a real 0402 capacitor has ~0.3–0.5 nH ESL, putting its self-resonance
   inside typical sweep ranges — ideal elements mispredict GHz matching
   networks. Per-package-size defaults + optional override.
3. Validation board #2 with vias + zone holes (e.g. CPWG segment or stub
   filter) to exercise the untested geometry paths.
4. Antenna validation: compare a full antenna run (S11 dip frequency, Dmax,
   pattern shape) against a published reference, e.g. TI SWRA117D.
5. Prefill the dialog's substrate fields from the board stackup when the
   file has one (currently static FR4 defaults).
6. Expose max timesteps / end criteria in the dialog for high-Q structures
   (currently fixed 300k / 1e-4 in `gui.get_settings`).
7. `model.json` version field — compat handling is accumulating as
   scattered `.get(key, default)` calls (excite, lumped, pads, …).
8. PCM packaging (zip layout with `plugins/` subfolder) if this should ship
   through the Plugin and Content Manager.

Done previously: live GUI click-through (works, incl. two GUI-only crashes
fixed: wx.svg stub + UnboundLocalError — see traps above); graphic shapes
on copper (poly/rect/circle/segment/arc, board + footprint).

## License

MIT — Copyright © 2026 Nojus Balčiūnas

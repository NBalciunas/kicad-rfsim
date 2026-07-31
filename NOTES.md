# NOTES

Working notes for kicad-rfsim. This file is for the persons who continue
the work. [README.md](README.md) is the document for the users.

## The contract with the README

The README keeps this structure, in this order: Name, Features,
Installation, Usage, Examples, Validation, License. Three rules:

- **Do not put development notes in it.** They come here.
- **Do not mention KiCad 8 or KiCad 9.** The plugin is for KiCad 10.0,
  and the history of the older versions stays in this file.
- **Do not explain what openEMS explains.** Send the user to
  <https://docs.openems.de> in the place of a paragraph. The reasons also
  come here: the README says what to do, not why.

## Conventions

- **All the documents and all the code comments use ASD-STE100**
  (Simplified Technical English): approved words only, the active voice,
  simple tenses, no `-ing` forms as nouns or adjectives, short sentences,
  and one instruction per sentence. The technical names (openEMS, CSXCAD,
  pcbnew, microstrip, via, PML_8, NF2FF, Touchstone) stay as they are.
- **The strings that the user sees are not in STE.** The warnings, the
  dialog text and the `print` output keep their original words. Some
  tests compare against them.
- **Do not use an em dash (—) in the README.** Use a hyphen with a space
  on each side, or make two sentences. The minus sign (−) in a value
  such as −10 dB is a different character: keep it.
- A comment that starts with `ponytail:` marks a deliberate shortcut.
  `/ponytail-debt` collects them. Keep the marker when you edit the
  comment near it.
- The repository uses CRLF line endings, and some editors change them to
  LF. Examine `git diff --stat` after a large edit. One such change
  removed 5 lines from the README without a message.
- To show that an edit changed no code, compare the AST of each file
  against the AST at HEAD, with the docstrings removed.

## Open now

**1. The README documents an installation that does not exist.** The
Installation section tells the user to download a release ZIP file and to
install it with the Plugin and Content Manager. The repository has no
release, no tag and no PCM zip layout. `metadata.json` is a correct PCM
manifest, but nothing builds the package. Two things are necessary: make
the zip with a `plugins/` subfolder that holds the `.py` files, `assets/`
and `resources/`; then publish it as a release at
`github.com/NBalciunas/kicad-rfsim/releases`. Until then, a user must
copy the folder into
`%USERPROFILE%\Documents\KiCad\10.0\3rdparty\plugins\`. The venv of the
solver cannot go into the package: it stays step 6 of the installation.
This is backlog item 10, and it is now a blocker.

**2. The pictures for the README.** The Examples section is a
placeholder with five captions and no images.

**3. A live click-through in pcbnew on KiCad 10.** Backlog item 1. This
is the only path that never ran through the real UI.

## Log

### 2026-07-31 — the documents

- Rewrote each comment, each docstring and the full README in ASD-STE100.
  An AST comparison, with the docstrings removed, showed 0 differences.
  Thus no code changed.
- Divided the documents. The README keeps the material for the users, and
  this file took all the development text. The README links to
  `NOTES.md#<anchor>`, and 4 comments in the code now name NOTES.md.
- Made the README much shorter: 963 lines -> 179. Features 60 -> 20,
  Installation 97 -> 33, Usage 97 -> 58. Examples became a placeholder
  for the pictures. The new Validation section gives one bullet for each
  file in `validation/`. Removed each mention of KiCad 8 and 9, the note
  about the gerbers, and the blockquote about the version; the version is
  now in the first sentence.
- The Installation section uses the Plugin and Content Manager. Refer to
  item 1 of "Open now": that package does not exist yet.
- Corrected the line endings of 5 files that the edits changed to LF.

### 2026-07-30 — KiCad 10 and openEMS v0.37

- **KiCad 8 -> 10**: four changes for the API in `board_reader.py`
  ([details](#changes-between-kicad-8-and-10)), and `kicad_version` in
  `metadata.json` -> `10.0.0`. `gui.py` and `rfsim.py` needed no change.
  The geometry did not change: the new `model.json` is bit-identical to
  the old output in all nine fields.
- **openEMS v0.0.36 -> v0.37.0-rc1**, in its own Python 3.14. The new
  `solverenv.py` selects the interpreter of the solver. `runner.py` uses
  its list of directories for `add_dll_directory`. The test of the
  packages in `rfsim.py` became two tests, one for each interpreter. It
  uses `find_spec`, thus an absent openEMS DLL cannot look like an absent
  package.
- **The inductors operate.** The runner sends `LEtype=1` (series), but
  only when `_has_lumped_rlc()` finds `SetLEtype`. Thus v0.0.36 still
  runs. R, L and C all agree with the theory (`run_rlc.py`).
- **A correction for the format of the field dumps** of v0.37. Then all
  18 result views drew correctly. A preview of the board layout replaced
  the logo of the settings dialog. It uses the renderer of the results
  window through `_draw_board`. If it cannot set up matplotlib, the icon
  replaces it. Thus a failure of the preview cannot stop the dialog.
- **The detection of the lumped parts** now counts the pads that have a
  *number*. It also gives a warning, and does not stay quiet, when a part
  that has a value has the incorrect number of terminals.

### Before 2026-07-30

openEMS v0.37 with R/L/C lumped elements that operate, and the results
window tested again; a live click-through of the GUI on KiCad 8, which
operates and which found two crashes in the GUI only (the wx.svg
replacement module and the UnboundLocalError — refer to
[the traps](#decisions-that-are-not-obvious-and-the-defects-that-caused-them));
and the graphic shapes on copper (poly, rect, circle, segment, arc, from
the board and from the footprints).

## The software on this machine

Examined on 2026-07-30, and still correct on 2026-07-31. The solver side
is complete and it operates:

- KiCad 8 is not installed. **9.0.8** and **10.0.5** are installed, both
  per-user at `%LOCALAPPDATA%\Programs\KiCad\<ver>`, and both contain
  **Python 3.11.5**.
- **openEMS v0.37.0-rc1 and CSXCAD v0.7.0-rc1** are at `C:\openEMS`
  (`openEMS_x64_v0.37.0-rc1_msvc.zip`, 47.8 MB,
  sha256 `7ED0E3A2…F6556A4C`).
- **The venv of the solver is at `C:\openEMS\venv`.** It has Python
  3.14.5 with numpy 2.5.1, h5py 3.16.0, CSXCAD 0.7.0rc1 and openEMS
  0.37.0rc1 (cp314 wheels). `solverenv.solver_python()` finds it
  automatically.
- In the Python of KiCad 10, for the GUI side: scikit-rf 2.0.1,
  matplotlib 3.11.1 and h5py 3.16.0. `pip install --user` puts them into
  `Documents\KiCad\10.0\3rdparty\Python311`, which is the user-site
  directory of KiCad. KiCad also supplies numpy 2.4.2 and wxPython 4.2.2.

The two interpreters are complete, and each layer ran on KiCad 10 with
openEMS v0.37.0-rc1.

## What is tested

The tests used KiCad 10.0.5 and openEMS v0.37.0-rc1, if there is no other
note.

**The extraction** — the geometry and the stackup; the substrate override
from the dialog; the copper from the pads, tracks, arcs, vias, filled
zones and graphic shapes (poly, rect, ring, segment, arc, bezier); the
guard for the ground return (it operates on a board with no plane, and it
stays quiet on a good board); the warning about clipped copper; the
domain that fits the full board automatically; the load method of KiCad
(`__import__("rfsim-dev")`). On a real board with 6 layers: 1388
polygons, 444 vias, 1.0 s.

**The solver** — a 2-port MSL at coarse and at medium (S11 <= -10.3 dB,
S21 >= -0.3 dB); a run with 2 lumped ports (S11 <= -9.3 dB); a 1-port
`.s1p` file (a cut track terminates into the PML, S11 about -21 dB); the
Touchstone file that this code writes, read again by skrf for n = 1 to 5.

**The lumped elements** — the detection finds an `R*`, `L*` or `C*`
footprint that has exactly two pads **with a number**, both SMD, and both
on one copper layer of the stackup. It reads the value from the Value
field: RKM notation, SI prefixes and DNP variants. There is a self-test
of 21 cases (`python board_reader.py`). The code excludes a footprint
that holds a port. A THT part causes a warning, and the code ignores it.
The element is a box that bridges the gap between the pads, along the
nearest axis. The mesh lines are at the edges of the box, thus a part
that is less than 1 mm long does not move with the cells. `R=0` becomes a
metal short circuit. A series 50 ohm in a 50 ohm line gives S11 -10.7 dB
and S21 -4.5 dB at 2 GHz, against the ideal -9.5 dB and -3.5 dB. R, L and
C all agree with closed-form theory: the slopes from 1 GHz to 5 GHz are
+0.6 dB (flat), -20.7 dB (it decreases) and +5.7 dB (it increases).

**More than one port** — any number of ports, with one FDTD run for each
excited port; the dropdown for the port number and the Excite checkbox at
each pad (`settings.excite` holds the FINAL port numbers, thus a change
of the numbers and a change of the excitation operate together); the E/H
dumps and the NF2FF data of each port, in `excN/[EH]f.h5` and
`farfield_pN.json`.

**The results window** — 18 views draw without a GUI: the magnitude and
the phase of the S-parameters, Smith, VSWR, the group delay, the board
layout, the E field and the H field of each port, and four far-field
views of each port. The window plots only the S columns that the run
*calculated*. Before, a column of zeros looked like a perfect match. A
numeric test, not the eye, showed that the fields are correctly aligned:
the centroid of the energy on the validation track is at y = -10.000 mm.
The far field gives three cuts, a 3D balloon that you can rotate with the
PCB as a reference plate, and the values of Dmax, Prad, the efficiency
and the lobes. A live click-through in pcbnew ran on KiCad 8.

**Robustness** — the runner removes the old `exc*` folders and
`farfield*.json` files from the output directory before it runs. It also
stops on a run that diverged (NaN), and does not fail in an unclear way
in `CalcPort`.

## What is not tested

- [ ] A **live** click-through in pcbnew on KiCad 10 (SettingsDialog ->
      RunDialog -> ResultsFrame from a real selection). All three draw
      without a GUI, and there are tests of the interpreter logic and the
      package logic of the plugin. But nothing ran through the real UI of
      KiCad after KiCad 8.
- [ ] A board with vias, inner layers or zones with holes, *through the
      solver*. The extraction from such a board is now tested (the demo
      board with 6 layers and 444 vias), but fractured polygons with
      slits and `AddCylinder` vias never reached openEMS.
- [ ] Lumped elements in a real matching network, where two parts or more
      have an effect on each other. Only boards with one element are
      tested.
- [ ] The approximation for the stability, on geometries that are not the
      geometry of `run_rlc.py`: a different mesh preset, a different size
      of the element box, or a different εr.
- [ ] A full simulation of a real antenna. The support for the graphic
      shapes came from such a board. The extraction is tested, but a run
      of the solver is not.

## Notes on the KiCad API

These notes come from an examination of the installed 9.0.8 and 10.0.5 —
each pcbnew symbol and each call that this plugin makes — and from the
source of KiCad 8.0.

### Changes between KiCad 8 and 10

| KiCad 8 | KiCad 10 |
|---|---|
| `SHAPE_POLY_SET.PM_FAST` | removed; use the `Simplify` / `Fracture` / `Boolean*` functions that have 1 argument |
| `PCB_ARC.GetArcAngle()` | `PCB_ARC.GetAngle()` |
| `PCB_SHAPE.IsFilled()` | `PCB_SHAPE.IsSolidFill()` |
| `PCB_VIA.GetWidth()` | `PCB_VIA.GetWidth(layer)` — a via now has one padstack for each layer |

Each change has a trap:

- `PCB_ARC` and `PCB_SHAPE` are now **different**: `PCB_ARC` has
  `GetAngle` only, and `PCB_SHAPE` has `GetArcAngle` only. If you rename
  all of them, the arc graphics fail.
- `ZONE.IsFilled()` is a different class and it is still correct. Do not
  rename it.
- The sign convention of `GetAngle()` agrees with `_add_arc`. A test on a
  quarter arc showed this. Thus you do not need a correction.
- `PCB_VIA.GetWidth()` still gives the correct number without a layer,
  and it only causes a debug assert. Thus an examination of the symbols
  cannot find this problem: it occurs at run time. The call with a layer
  is also more correct, because it models a via that has different
  annular rings on the layers.

About 70 more calls did not change. They include `GetStandardLayerName`,
`GetEnabledLayers().CuStack()`, the `BOX2I` operations,
`PAD.TransformShapeToPolygon`, `FlashLayer`, `GetRectCorners`,
`GetPolyShape`, `RebuildBezierToSegmentsPointsList`,
`GetFilledPolysList` and `ActionPlugin`.

### Text on copper and the layer converter

`TransformTextToPolySet` needs the `ERROR_LOC` enum. KiCad 8 does not
have it; 9 and 10 give it as `pcbnew.ERROR_INSIDE == 1`. But
`BOARD::ConvertBrdLayerToPolygonalContours(layer, polyset)` is wrapped on
all three versions, and it does the full job in C++: the tracks, vias,
pads, footprint shapes **including the text**, zones, board shapes,
`PCB_TEXT`, `PCB_FIELD`, text boxes and dimensions. A test on 10 showed
this: a text item on F.Cu came back as 3 of 9 outlines.

That one call would replace most of `_copper_polys` and all the polygon
mathematics in this code (backlog item 3), and it is more accurate than
the mathematics. On the track of 2.9 mm of the validation board:

| source | points | area |
|---|---|---|
| the exact stadium shape | — | 93.605 mm² |
| the polygonizer of KiCad | 44 | 93.579 mm² |
| `_stadium_pts`, caps of 8 segments | 18 | 93.437 mm² |

The vias are worse: `_circle_pts(n=32)` gives 0.2795 mm², and the exact
value is 0.2827 mm². Two cautions for the rewrite: the converter has no
filter for a region, thus keep the `BooleanIntersection` with the
rectangle of the region; and it gives no data about each item, thus keep
the count of the clipped copper, which uses the bounding boxes.

### True for 8, 9 and 10

- **The Python is 3.11.** Thus no version of KiCad can install the
  openEMS wheels that the solver needs, and the solver needs its own
  interpreter.
- **You cannot read the stackup.** `GetStackupDescriptor()` gives a bare
  `SwigPyObject`, and no set of bindings has a `BOARD_STACKUP` type. Thus
  there is the s-expression parser of `_stackup_from_file`, its FR4
  default values from `GetBoardThickness()` and the number of copper
  layers, and the rule "save the board first".
- **`import wx.svg` fails** with `No module named 'wx.svg._nanosvg'`.
  Refer to the wxPython traps for the replacement module.

### The IPC API

The IPC API is the only method to read the stackup, but it is not an
alternative for the geometry. It gives `get_pad_shapes_as_polygons()` and
the zone `filled_polygons`, but `get_shapes()` does not include the
tracks or the text. There is no function that makes polygons from a
track, an arc or text. Thus a full change to IPC would keep the polygon
mathematics of this code and would lose the text again. IPC also needs
KiCad to run with the API server on, which stops the test harness that
uses `LoadBoard`.

SWIG is deprecated as of KiCad 9.0, and there is a
[plan to remove it in 11.0](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/index.html).
That is the last date for a full change.

## The versions of openEMS

A lumped inductor needs an openEMS from after the merge of the lumped RLC
work (PR #121, November 2023). That work added `LEtype`: `0` = parallel
(the default) and `1` = series. The obstacle is the packaging, not the
functions:

| build | date | lumped L | wheels |
|---|---|---|---|
| v0.0.36 | Oct 2023 | ✗ (it is older than PR #121) | cp310, **cp311** |
| v0.0.36-90 / -93 | Oct 2025 | ✓ | cp313, cp314 |
| nightly -162 / -206 | May 2026 | ✓ | cp313, cp314 |
| v0.37.0-rc1 | Jun 2026 | ✓ | cp313, cp314 |

The only release with a cp311 wheel is older than the function. Each
build that has the function is for 3.13 and 3.14 only. The packaging CI
confirms this in `.github/workflows/windows-package.yml`. openEMS is not
on PyPI, thus there is no source that pip can use. You must build the
Cython extensions yourself, and the `.pxd` files are in the zip if you
must do it. KiCad contains Python 3.11, thus the solver needs its own
interpreter.

An older engine writes "Lumped Element R or C not specified! skipping"
and models an open circuit without a message. Thus `runner.main` refuses
an inductor when `_has_lumped_rlc()` tells it that the engine cannot do
inductors.

Each openEMS function that this project uses is the same in v0.0.36 and
in v0.37.

## Stability with a lumped inductor

A lumped inductor makes the FDTD unstable. The energy becomes
`-nan(ind)`, and `CalcPort` then stops with the unclear message
`could not convert string to float: '-nan(ind)'`. This is **not** a
problem of the topology: series and parallel diverge in the same way. The
problem increases with the inductance. These are the largest stable
values of `SetTimeStepFactor` on the geometry of `run_rlc.py` (coarse
mesh, 6000 steps):

| L (nH) | 1 | 3 | 10 | 30 | 100 | 300 |
|---|---|---|---|---|---|---|
| largest stable factor | 1.0 | 1.0 | 0.5 | 0.35 | 0.18 | 0.12 |
| `1/√L(nH)` | 1.00 | 0.58 | 0.32 | 0.18 | 0.10 | 0.06 |

The boundary is near `1.8/√L(nH)`. Thus `runner._time_step_factor()` uses
`min(1, 1/√L(nH))`, which keeps a margin of about 1.8. It also multiplies
`max_timesteps` by `1/f`, thus the *simulated time* does not change.
These are the results and the alternatives:

- This is an approximation from **one** geometry. The true criterion also
  includes the cell size and the box of the element. Thus
  `runner._diverged()` examines the port data for NaN after each run. If
  it finds NaN, the runner stops with a message that names
  `time_step_factor`, and `CalcPort` does not throw.
- `settings["time_step_factor"]` has priority over the automatic value.
- The cost is real: a part of 10 nH runs at a factor of 0.316, which is
  about 3 times the number of timesteps.

## The structure and the flow of the data

```
The Python 3.11 of KiCad                      the solver Python 3.13/3.14
(pcbnew + wx + skrf)                          (numpy + CSXCAD + openEMS)
─────────────────────                         ──────────────────────────
__init__.py registers RFSimPlugin
rfsim.py Run():
  test the packages (2 sides) ┐
  solverenv.solver_python()   ┘
  the selected pads
  board_reader.extract(board, pads, margin)   runner.py model.json outdir:
    -> model dict -> model.json    ──────────→  _port_geometry -> _mesh ->
  gui.RunDialog shows stdout                    build CSX -> FDTD.Run for
  gui.ResultsFrame(results.sNp)  ←──────────    each excitation ->
                                                CalcPort -> results.s2p
```

The two columns are different **processes and interpreters**. Thus a
crash of the solver cannot stop KiCad, and the solver can use a Python
that KiCad cannot use, because openEMS v0.37 and later have no cp311
wheel. Both sides import `solverenv.py` only, thus that module must not
import pcbnew, wx or numpy.

### The interpreter of the solver

The README does not give this list any more. `solverenv.solver_python()`
selects the Python of the solver in this sequence:

1. `$RFSIM_PYTHON`, if you set it. This value has priority, and it is the
   method to use a different interpreter.
2. `<openEMS>\venv` — step 6 of the installation, and the usual result.
3. None. Then the caller keeps the Python of KiCad. This is correct only
   for openEMS v0.0.36, which has a cp311 wheel but no lumped inductors.

`rfsim.py` examines the packages of the two interpreters one after the
other, and the settings dialog names the absent packages of each one.

`board_reader.py` is the only module that uses pcbnew for the geometry.
The interface is the model dict, which you can write to JSON. All the
values are in mm, the y axis points up, z=0 is at the bottom of the
board, and the copper is a set of sheets with no thickness at the
boundaries of the dielectric:

```
{ settings: {f_start, f_stop, f_field, z0, margin_mm, mesh, n_freq,
             max_timesteps, end_criteria,
             excite: [port numbers],    # absent or None = excite all
             lumped: bool,              # model the R/L/C parts?
             time_step_factor: float},  # optional; it has priority over
                                        # the automatic sub-Courant step
                                        # for the inductors
  copper_layers:     [{name, z, thickness}]          # top to bottom
  dielectric_layers: [{name, z_top, z_bottom, epsilon, loss_tangent}],
  region:     {x0,y0,x1,y1},   # the crop rect = pads bbox + 2*margin
  board_rect: {x0,y0,x1,y1},   # region ∩ board edges (dielectric extent)
  polygons:   {"F.Cu": [[[x,y],...], ...], ...},     # fractured, no holes
  vias:  [{x, y, r, z0, z1}],
  lumped_elements: [{ref, type: "R"|"L"|"C", value,  # SI (ohm/H/F)
                     ny: "x"|"y", layer, start, stop,
                     pads: [[x,y],[x,y]]}],
  ports: [{number, label, x, y, layer, ref_layer, width, length,
           direction: [±1,0]|[0,±1]|null, track_width, type}] }
```

The split also lets the solver side run in a *different Python* than
KiCad. The upgrade path of openEMS needs this.

Each run gives these outputs: `results.sNp`; one `excN/` folder for each
excited port, with the field dumps `Ef.h5` and `Hf.h5` and the NF2FF
recordings; and one `farfield_pN.json` for each excited port.

## Decisions that are not obvious, and the defects that caused them

**Traps in pcbnew** (the differences between the versions of the API are
in [Notes on the KiCad API](#notes-on-the-kicad-api)):

- `pcbnew.BOX2I()` constructs a box at the origin (0,0) by default. Then
  `Merge` incorrectly includes the origin. Always start from the bbox of
  the first pad.
- The footprint graphics are `PCB_SHAPE` items in **board** coordinates.
  Thus you do not need a transform for the footprint. The beziers operate
  through `RebuildBezierToSegmentsPointsList` and `GetBezierPoints`.
- Do not use `GetEffectiveTextShape()`. Its subshapes are opaque `SHAPE`
  wrappers, and their `Format()`, `Clone()` and `Cast()` functions
  **stop the interpreter immediately**. Use
  [the layer converter](#text-on-copper-and-the-layer-converter).

**Traps in the wxPython and the matplotlib of KiCad** (the results window
hit both, and they are still in 9 and 10):

- The wxPython of KiCad contains `wx/svg/` **without the compiled
  `_nanosvg` extension**. There are only .pyx and .c sources. Thus
  `import wx.svg` always fails. The wx backend of matplotlib does
  `import wx.svg  # noqa: F401` for a side effect only, and never uses
  it. Thus, before you import `backend_wxagg`, try the import. If it
  fails, put an empty module into `sys.modules["wx.svg"]`
  (`gui.ResultsFrame`).
- Do not write `import wx.svg` in a function that also uses `wx`. The
  statement makes the name `wx` a *local* name. Then, when the import
  fails, all the `wx.*` names after it in that function stop with an
  UnboundLocalError. Use `importlib.import_module("wx.svg")`, which binds
  no name.

**Traps in openEMS and CSXCAD**:

- CSXCAD removes a ConductingSheet polygon with no thickness without a
  message. It gives an "Unused primitive" warning only. To prevent this,
  a mesh line must be **exactly** on its z plane. `SmoothMeshLines` gives
  1.5300000000000002 for an input of 1.53. Thus the code rounds all the
  smooth lines to 9 decimals before `AddLine`. If you see "Unused
  primitive" again, examine the floats first.
- The MUR boundaries caused a slow increase of the energy at late times
  on this setup: −33 dB to −19 dB over 250k steps, with no convergence.
  PEC was stable, thus the boundary was the cause, and not the sheets.
  The correction is `PML_8` with the outer margin meshed as exactly 8
  cells (`_pml_band`).
- The code crops the copper at the *outer* edge of the domain, because
  the region is the bbox plus 2 times the margin. Thus the cut planes and
  tracks go through the PML and terminate almost matched. The MSL
  examples of openEMS use the same method. The inner margin band is clear
  air between the structure and the absorber.
- The Windows wheels need the binary folder of openEMS through
  `os.add_dll_directory`. The runner tries `../../openEMS`,
  `OPENEMS_PATH` and `C:\openEMS`. They also need `h5py` at import time.
- The HDF5 layout of the FD field dump **changed in v0.37**. There is now
  one dataset of native complex values, `FieldData/FD/f0`, with the
  attribute `d_order='NXYZ'` and the root attribute
  `openEMS_HDF5_version`. v0.0.36 wrote a pair of float32 datasets,
  `f0_real` and `f0_imag`. `gui._load_field` reads the two formats, and
  its axis code (squeeze, then move the component axis to the end, then
  swap to `[y, x, 3]`) already covered the new sequence. An incorrect
  sequence causes no error: the plot is only transposed. Thus examine the
  centroid of the energy against the known geometry; do not use your eye.
- `AddDump` uses `dump_mode=0` by default, which does no interpolation.
  It then dumps the raw values of the Yee grid at the positions of the
  mesh nodes, and it draws the in-plane H field one half of a cell away
  from the copper. The user sees the H pattern of a patch about 1 mm to
  the right. Always give `dump_mode=1` (interpolation to the nodes) for a
  dump that you will plot.
- `MSLPort` needs start ≠ stop in all three coordinates, the strip plane
  at `start[2]`, and 5 mesh lines or more along the direction of
  propagation **before** you make the port. Thus `build()` makes the mesh
  before `AddMSLPort`.
- You must give the center of `CalcNF2FF` in METERS, and it must be
  inside the recording box. The default `[0,0,0]` is outside for real
  board coordinates, and it causes "invalid center" errors.
- Lumped elements: openEMS v0.0.36 and earlier have R and C only. They
  **remove a pure L element without a message** and write "Lumped Element
  R or C not specified! skipping". The result is an open circuit, not an
  error. Thus there is the guard, which now uses a test of the function
  and not an assumption.
- The components of a lumped element that you do not give are **NaN, not
  0**, and the engine reads NaN as "absent". Thus `LEtype` (series or
  parallel) cannot change an element that has one component.
- A lumped inductor **diverges** at the full Courant timestep. openEMS
  writes `-nan(ind)` into the port files and gives no error. Thus the
  failure looks like a crash of the float parser in `CalcPort`. Refer to
  [Stability with a lumped inductor](#stability-with-a-lumped-inductor).
- The README of openEMS v0.37 and later asks for a global
  `CSXCAD_INSTALL_PATH` variable. You do not need it:
  `os.add_dll_directory` alone operates (this is a test result on
  v0.37.0-rc1), and the runner already does it.

**How to debug: S11 is smooth at −20 dB to −30 dB, with no resonance.**
A broadband match that is very *good* tells you that the plugin CUT the
structure under test at the boundary of the region. Its cut end then
terminates into the PML like a matched load. This occurred with the
meander antenna of TI SWRA117D (about 26 mm) at the default margin of
4 mm, which gave a region of 18.8 mm. There are now two defenses: (1) the
domain fits the full board automatically, because
`GetBoardEdgesBoundingBox()` is merged into the region, and (2)
`extract()` counts the copper that is not a zone and that crosses the
edge of the region, and puts the count into `model["warnings"]`. The GUI
shows the warnings after the settings dialog. Zones are not counted,
because a pour *must* be cut. Thus the warning is now most important for
a board that has no Edge.Cuts outline.

**How to debug: S11 is flat at about 0 dB, and S21 is −40 dB to −55 dB
and increases with f.** Two pads with no connection couple through their
capacitance. The ports have no ground return, or the copper between them
never came into the model. This is *correct physics for a broken setup*,
not a defect of the solver. It occurred on a board with pads that had no
net and no B.Cu plane. That event caused the guard for the ground return
in `extract()` and the support for the graphic shapes. Compare against
`validation/` first: if the microstrip board passes, the pipeline is good
and the input board is the problem.

**Traps in the wx dialog**:

- The substrate presets: apply the values with `TextCtrl.ChangeValue()`,
  which sends no EVT_TEXT, and not with `SetValue()`. The er and tanδ
  fields have an EVT_TEXT handler that changes the preset dropdown to
  "Custom" when the user edits them, and `SetValue` would also send the
  event during the application of a preset.
- You can test the GUI without a display on Windows. Use `wx.App(False)`,
  build the dialog or the frame, and exercise the logic. For a
  screenshot, do a `ScreenDC` blit after `Show()` and some rounds of
  `wx.Yield()` and `Update()`. One `Yield` is not enough: the first
  capture came back with no paint. Caution: the coordinates of a
  `ScreenDC` blit can be incorrect with display scaling or with more than
  one monitor. `figure.savefig`, after the size becomes stable, is the
  method that always shows what the canvas holds.
- `FigureCanvasWxAgg` gives the native pixel size of the figure (800×550
  for a figure of 8×5.5 in) as its **minimum size** for wx. Thus the
  sizers CLIP it at the bottom and do not make it smaller, and the label
  of the x axis stays invisible until the user changes the size. Three
  corrections are necessary: `canvas.SetMinSize((320, 240))`;
  `wx.CallAfter(self.SendSizeEvent)` at the end of `__init__`, because no
  size event occurs at the first `Show`; and `Figure(layout="constrained")`
  in the place of one `tight_layout()` call, thus the labels stay correct
  after each change of the size.

**Decisions about the model** (you can examine them again):

- The copper loss uses `AddConductingSheet` (5.8e7 S/m, the real
  thickness, with a minimum of 0.1 µm, thus the code does not clamp thin
  user values without a message). The dielectric loss is kappa at the
  center frequency.
- The substrate values of the GUI (er, tanδ, h, cu_t) always have
  priority over the stackup of the board file. This is simpler than a
  merge, and the dialog then shows exactly what the solver will use. The
  parser of the file stackup stays, for the runs that have no GUI.
- The terminals of a lumped element are the pads that have a **number**,
  and not all the pads. A real footprint often has more copper pads
  (mechanical pads or pads for paste relief) that cannot hold a net and
  are not terminals. This occurred with a KiLib
  `SMD_2terminal_chip_molded` resistor that has **4** pads, two of them
  with no number. A simple test of `len(fp.Pads()) == 2` refused it.
  `_copper_polys` still simulates the copper of the pads that have no
  number, as it simulates any other pad. A footprint whose value is
  correct, but whose number of terminals is not 2, now gives a warning
  and does not go away quietly.
- The guard for the ground return tests if the bounding box of the pad
  touches the bounding box of a polygon on the reference layer. It is a
  test of the overlap, not a test of the center, because an antenna feed
  pad is at the *edge* of the ground pour (for example the IFA of TI
  SWRA117D). Change it to a point-in-polygon test if pours with unusual
  shapes give incorrect results.
- The mesh presets are λ_min/{10, 20, 40}, with fixed lines at the
  polygon bbox edges, the port edges and centers, the layer planes, and 4
  cells or more in each dielectric. There is no 1/3-2/3 rule.
- The full S-matrix needs one run for each excited port (2 ports give 2
  runs). There is no shortcut through reciprocity.
- `runner.write_touchstone` writes the Touchstone file directly, in the
  RI format. Remember the sequence of the columns of a `.s2p` file:
  S11 S21 S12 S22.
- The reference layer of a port is the adjacent copper layer in the
  stackup. The code does not look for copper below the pad.
- `Fracture` changes the holes of a zone into slits with no width, and
  CSXCAD makes correct raster data from them. Change to a subtraction of
  the holes if you see artifacts.

## Commands

The README, section "Validation", gives the commands that run the tests.
The commands below are for development only.

```bat
set KIPY="%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe"

:: run the solver again on a model that exists, with the SOLVER python
:: and not the python of KiCad
C:\openEMS\venv\Scripts\python.exe runner.py validation\out_coarse\model.json out

:: the same tests as the README, but against the LIVE board, together
:: with the changes that you did not save. Use Tools > Scripting Console
:: of pcbnew:
::   import sys; sys.path.insert(0, r"<repo>\validation")
::   import diag_lumped; diag_lumped.report()

:: copy this checkout into the plugin directory of KiCad. KiCad runs the
:: COPY, not the repo. Then start KiCad again, or at least do
:: Tools > External Plugins > Refresh, because the old .pyc files stay
:: after an edit.
set RFDEST=%USERPROFILE%\Documents\KiCad\10.0\3rdparty\plugins\com_github_nbalciunas_kicad-rfsim
copy /Y *.py "%RFDEST%\"
rmdir /S /Q "%RFDEST%\__pycache__"
```

**KiCad runs a copy of the plugin.** An edit in this checkout changes
nothing until you copy the `.py` files, and an old `__pycache__` can
supply the old module after you copy them. This occurred live: the E/H
field views continued to fail with `object 'f0_real' doesn't exist` after
a correction of that defect in the repo, only because the deployed
`gui.py` was older. When something is correct in the repo but is still
defective in KiCad, compare the two copies first.

`validation/out_*/model.json` is the fastest entry point for debugging.
Edit it manually and run `runner.py` again under the venv of the solver.
Then you can work on the solver code with no KiCad. These keys in
`settings` are useful: `max_timesteps` (decrease it to some thousands for
a quick test of the stability), `time_step_factor`, `excite` (one port
gives one half of the time) and `mesh`.

Note the two interpreters. All the code that uses `pcbnew` needs
`%KIPY%`, and all the code that uses `openEMS` needs the venv. The
validation programs run under `%KIPY%` and start the venv themselves.

## What openEMS can do

This is what openEMS v0.37 supplies, against what this plugin uses. The
list comes from an examination of the installed engine, not from the
documentation. ✅ used · 🟨 partial · ⬜ available and not used. The ⬜
rows are the true roadmap. The backlog below takes only the near items.

### Geometry (the KiCad side)

| Function | | Extent |
|---|---|---|
| Pads, tracks, arcs, vias | ✅ | Changed to polygons; the via annulus is per layer (it obeys the padstack) |
| Filled zones | ✅ | Fractured; the holes become slits with no width |
| Graphic shapes on copper | ✅ | Poly, rect, circle, ring, segment, arc, bezier — from the board and from the footprints |
| Stackups with more layers | ✅ | Tested with 6 layers: 1388 polys, 444 vias, 1.0 s |
| Stackup εr / tanδ / thickness | 🟨 | Read from the saved file, or from the dialog override. SWIG has no access, thus you must save the board |
| Text on copper | ⬜ | It causes a warning; the model does not include it. `ConvertBrdLayerToPolygonalContours` would do it |
| Automatic fit of the domain | ✅ | The Edge.Cuts bbox plus 2 times the margin |

### Ports — 2 of 9

| Function | | Extent |
|---|---|---|
| `AddLumpedPort` | ✅ | A vertical excitation below the pad; it operates everywhere |
| `AddMSLPort` | ✅ | A de-embedded microstrip; the feed direction comes from the attached track |
| `AddCPWPort` | ⬜ | **The coplanar waveguide, which is very usual in an RF layout** |
| `AddStripLinePort` | ⬜ | **Any track on an inner layer** |
| `AddCoaxialPort` | ⬜ | Coax and SMA transitions |
| `AddRectWaveGuidePort`, `AddCircWaveGuidePort`, `AddWaveGuidePort`, `AddCurvePort` | ⬜ | Waveguide work; not in the scope of a PCB |

Today, a CPW structure and a stripline structure must use a lumped port
or a microstrip port, which puts the reference plane in the incorrect
place.

### Excitation — 1 of 5

| Function | | Extent |
|---|---|---|
| `SetGaussExcite` | ✅ | Broadband, with its center on the sweep |
| `SetStepExcite` | ⬜ | **TDR: find the impedance discontinuities along a track** |
| `SetSinusExcite` | ⬜ | One frequency in the steady state; much less costly for one frequency |
| `SetDiracExcite`, `SetCustomExcite` | ⬜ | An impulse or any other waveform |
| `AddExcitation` (a field, not a port) | ⬜ | **Plane-wave illumination: EMC immunity, RCS** |

### Lumped elements

| Function | | Extent |
|---|---|---|
| R, L, C with one component | ✅ | Tested against the theory (`run_rlc.py`) |
| 0 Ω becomes a copper short circuit | ✅ | `AddMetal` in the place of an element |
| The series topology `LEtype=1` | ✅ | Correct for a part that bridges a gap |
| The timestep stability of an inductor | ✅ | Automatic `1/√L`, more steps, and detection of NaN |
| R, L and C together in one element | ⬜ | The path to the parasitics: the ESL of a 0402 is 0.3 nH to 0.5 nH |
| The parallel topology `LEtype=0` | ⬜ | It is important only when 2 components or more are in one element |

### Materials — isotropic only

| Function | | Extent |
|---|---|---|
| `AddConductingSheet` | ✅ | Copper, 5.8e7 S/m, the real thickness |
| `AddMaterial` εr + κ | 🟨 | An isotropic scalar; tanδ becomes κ at the **center frequency only** |
| `AddMetal` | ✅ | Vias, short circuits |
| Anisotropic εr / μr / κ | ⬜ | They take *vectors*: the anisotropy of woven-glass FR4, differential skew |
| Dispersive (Lorentz, Debye) | ⬜ | A true wideband εr(f), in the place of κ at one frequency |
| `mue` / `sigma` | ⬜ | **Ferrites** and other magnetic materials |
| `density` | ⬜ | Necessary for SAR |
| `AddDiscMaterial` | ⬜ | Voxel data, for example an imported body model |
| `AddAbsorbingBC` | ⬜ | A termination with loss, as a material |

### Primitives — 3 of 14

| Function | | Extent |
|---|---|---|
| `AddBox`, `AddLinPoly`, `AddCylinder` | ✅ | Dielectrics, copper sheets, vias |
| `CSPrimPolyhedronReader` | ⬜ | **STL import: an antenna inside its real enclosure or connector** |
| `RotPoly`, `Sphere`, shells, `Wire`, `Curve`, `MultiBox`, `Point` | ⬜ | Bodies with rotational symmetry, wires, 3D shapes |

### Mesh, boundaries, engine

| Function | | Extent |
|---|---|---|
| `SmoothMeshLines` and manual hints | ✅ | λ/{10,20,40}; lines at the poly, port and element edges; 4 cells or more in each dielectric |
| `PML_8` boundaries | ✅ | All 6 faces, with a band of exactly 8 cells |
| `SetTimeStepFactor` | ✅ | Automatic when there is an inductor |
| PEC / PMC symmetry planes | ⬜ | **They would give one half or one quarter of the time on a symmetrical structure** |
| `AddEdges2Grid` | ⬜ | **Automatic refinement at the material edges: it would improve each run** |
| `SetMultiGrid` | ⬜ | Sub-grids |
| `SetCylinderCoords` | ⬜ | Cylindrical FDTD (coax, circular structures) |
| Number of threads, engine selection | ⬜ | Through `SetLibraryArguments()` of v0.37 |

### Outputs — dumps 2 of 12, probes 0 of 6

| Function | | Extent |
|---|---|---|
| S-parameters to Touchstone | ✅ | The full N×N, `.s1p` to `.sNp`; skrf reads it correctly |
| Magnitude, phase, Smith, VSWR, group delay | ✅ | It plots only the columns that the run *calculated* |
| FD dumps of E and H (10, 11) | ✅ | The mid-plane of the substrate, interpolated to the nodes, for each excited port |
| The NF2FF far field | ✅ | 3 polar cuts, a 3D balloon, Dmax, Prad, the efficiency and the lobe data |
| Dumps of the current and the current density (2, 3, 12, 13) | ⬜ | **Where the current flows: EMC hot spots and return paths** |
| Time-domain dumps of E and H (0, 1) | ⬜ | The code uses the frequency domain only |
| SAR (20, 21, 22, 29) | ⬜ | Local, and averaged over 1 g and 10 g. `sar_calc.exe` is already in the installation |
| `AddProbe` (V, I, E, H, mode matching) | ⬜ | The code uses only what the port objects make internally |

### Where FDTD is the incorrect tool

Know the limits before you promise any item above. A structure with a
high Q (a narrowband filter, a resonator) needs a very long run, because
the energy decays slowly. A small electrical feature inside a large
domain gives a very large number of cells. There is no eigenmode solver
and no frequency-domain solver, thus there is no direct modal analysis.
And there is no automatic loop to improve a design, thus you must run the
simulation again manually to tune a matching network.

## Backlog (the most important item first)

These are the near-term items for the code that exists. For the functions
of the engine that the plugin does not use — the port types, the current
dumps, SAR, TDR — refer to [Larger items](#larger-items) at the end and
to [What openEMS can do](#what-openems-can-do).

The numbers do not change, because the text in other places names them.
[Open now](#open-now) has the items that block a release, and item 10 is
one of them.

1. **A live click-through in pcbnew on KiCad 10.** This is the only thing
   that never ran through the real UI after KiCad 8. Each part that it
   uses is tested without a GUI: 18 of 18 views, both interpreters, and
   the package test.
2. Package parasitics for the lumped parts, now that the series RLC
   operates. A real 0402 capacitor has an ESL of 0.3 nH to 0.5 nH, which
   puts its self-resonance inside a usual sweep range. Thus ideal
   elements give incorrect results for a GHz matching network. Add
   default values for each package size, and an optional override. This
   needs `LEtype=1`.
3. **Write `_copper_polys` again with
   `ConvertBrdLayerToPolygonalContours`**
   ([details](#text-on-copper-and-the-layer-converter)). This removes
   `_stadium_pts`, `_circle_pts`, `_add_arc` and `_add_shape` (about 100
   lines), adds the text on copper, and improves the accuracy of the
   curves. Keep the `BooleanIntersection` with the region and the warning
   about clipped copper, which uses the bounding boxes. The validation
   numbers will change.
4. Show the timestep cost of a lumped inductor in the dialog: a warning
   that an inductor multiplies the run time, and a `time_step_factor`
   field. Today that value is in model.json only. This item and item 8 go
   together.
5. A second validation board with vias and zone holes, for example a CPWG
   segment or a stub filter. This would push those geometry paths through
   the *solver*. The test on the demo board with 6 layers now covers the
   extraction from them.
6. Antenna validation: compare a full antenna run (the frequency of the
   S11 dip, Dmax, the shape of the pattern) against a published
   reference, for example TI SWRA117D.
7. Fill the substrate fields of the dialog from the board stackup. This
   needs the file parser, which operates today, or the IPC function
   `board.get_stackup()`.
8. Put the maximum number of timesteps and the end criteria into the
   dialog, for structures with a high Q. Today they are constant at 300k
   and 1e-4 in `gui.get_settings`.
9. A version field in `model.json`. The compatibility code is now a set
   of `.get(key, default)` calls in many places: excite, lumped, pads,
   time_step_factor and more.
10. **PCM packaging (a zip layout with a `plugins/` subfolder), and a
    release.** This moved up: the README now tells the user to install
    the ZIP file with the Plugin and Content Manager, thus the ZIP file
    must exist. Note that you cannot package the venv of the solver: it
    stays a step in the installation instructions.
11. A full change to the IPC API. This is worth the work only before
    KiCad 11 removes SWIG, and only after IPC can make polygons from
    tracks and text. Refer to [The IPC API](#the-ipc-api).

### Larger items

These items have no numbers, because they are functions and not small
tasks. Each one is a ⬜ row in
[What openEMS can do](#what-openems-can-do). The least costly ones are
first.

- **CPW ports and stripline ports** (`AddCPWPort`, `AddStripLinePort`).
  These give the best value of all the items here. People draw the two
  structures in KiCad, and today they can use only a lumped port or a
  microstrip port, which puts the reference plane in the incorrect place.
- **Better mesh efficiency, with no new functions**: `AddEdges2Grid` for
  automatic refinement at the material edges, and PEC/PMC **symmetry
  planes**, which give 2 to 4 times less run time on a symmetrical board.
  These make each existing simulation better or faster.
- **Dumps of the current density** (types 12 and 13): where the current
  flows. This shows the EMC hot spots and the problems with the return
  paths, and no other free KiCad tool gives this view.
- **Better materials**: a dispersive (Lorentz or Debye) εr(f) in the
  place of κ at one frequency, and an anisotropic εr for woven-glass FR4.
  These are most important for wideband work and for differential skew.
- **TDR** through `SetStepExcite`: find an impedance discontinuity along
  a track, and do not only see it in S11.
- **STL import** through `CSPrimPolyhedronReader`: simulate an antenna
  inside its real enclosure, or with a connector body.
- **SAR** (dump types 20, 21 and 22; `sar_calc.exe` is already in the
  installation). This is the compliance path for a device that a person
  wears or holds. It needs the material `density`.
- **Plane-wave excitation** through `AddExcitation`: EMC immunity and
  radar cross-section. This is a category of work, not one function.

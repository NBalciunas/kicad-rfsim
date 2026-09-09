# RFsim Settings Guide

RFsim converts selected KiCad PCB geometry into an openEMS FDTD model. A result is usable only when the FDTD energy remains finite and decays after the excitation pulse.

## Frequency

| Setting | Meaning |
|---|---|
| Start | Lower sweep frequency in GHz. Must be positive and below Stop. |
| Stop | Upper sweep frequency in GHz. It determines the shortest wavelength used by the mesh preset. |
| Define at | Frequency in GHz for E/H field dumps and far-field output. It must lie within the sweep. |

RFsim writes 401 linearly spaced points to the Touchstone result.

## Ports

| Setting | Meaning |
|---|---|
| Port impedance | S-parameter reference impedance, normally 50 ohm. |
| Port number | Unique port index for the selected pad. |
| Excite | Runs one FDTD excitation for this port. Enable only the required columns. |
| Lumped Port | Local pad-to-reference excitation. It does not extract line impedance. |
| Microstrip | Requires an x/y feed direction and one reference plane. |
| Coplanar | Requires an x/y feed and copper on both sides of the line. |
| Stripline | Requires an x/y feed and reference planes above and below. |
| Feed / Width | Manual feed definition for a pad with no detected track. |

The selected pad needs a continuous local return path. Do not place a port across a reference-plane split or void.

## Lumped Elements

Each eligible two-terminal SMD R/L/C footprint receives a row.

| Setting | Meaning |
|---|---|
| Model | Includes the component. Clear it to leave its pad gap open. |
| Type | Resistor, Capacitor, Inductor, or user-defined Unknown. |
| Value | Ohm, pF, or nH, selected by Type. |
| Parasitics | Package default, Custom, or No parasitics. |
| ESR | Series resistance for capacitors and DCR for inductors, in ohm. |
| ESL | Series body inductance in nH. |

Package defaults are body-only fallback estimates. RFsim models pads, tracks, and nearby via loops directly, so a complete mounted ESL from a supplier can double-count board inductance. The generic trends are informed by [KEMET K-SIM](https://ksim.kemet.com/) and [Murata SimSurfing](https://ds.murata.co.jp/simsurfing/en-us/), not an exact MPN model. Use manufacturer impedance or S-parameter data for a critical part.

For stability debugging, begin with every Model checkbox clear. Add only local RF-path components one at a time.

## Substrate

| Setting | Meaning |
|---|---|
| Presets | FR-4, Rogers RO4350B, Rogers RO4003C, PTFE, or Custom. |
| er | Relative permittivity; controls wavelength and impedance. |
| Loss tangent | Dielectric loss tangent. |
| Substrate thickness | Total dielectric thickness in mm. |
| Copper thickness | Copper thickness in mm. |

Dialog substrate values override the KiCad physical stackup in the simulation model. Use your fabrication stackup for an engineering result.

## Simulation

| Setting | Meaning |
|---|---|
| CPU threads | openEMS worker threads. Auto lets RFsim choose. |
| Mesh resolution | Coarse, Medium, Fine: 10, 20, 40 cells per shortest substrate wavelength. |
| Domain margin | Margin in mm. RFsim uses one margin-width as PML and one adjacent margin-width as intended clear space. |
| Max steps | Maximum FDTD iterations. |
| End criteria | Energy ratio at which openEMS stops. Smaller values run longer. |
| Timestep | Fraction of Courant timestep. Leave empty for automatic selection; use 0.5 or 0.25 for a stiff lumped network. |
| Output directory | Parent result folder. |
| Create a separate folder for each run | Writes each run to `run_YYYYMMDD_HHMMSS` below the output directory. |

A small-timestep warning means a longer simulation. `Energy: nan` or `Energy: inf` means the solver has diverged. RFsim stops that run automatically; discard its results.

## Subregion

Enable **Export port-focused rectangular subregion** for a local interconnect study. RFsim forms a rectangle from the union of selected pad bounds and expands every side by twice Domain margin. It exports only copper, vias, and eligible R/L/C footprints intersecting that rectangle.

The Subregion tab reports port bounds, export bounds, and component candidates. **View Exported Geometry** writes `geometry_preview.xml` and opens it in AppCSXCAD without running FDTD.

### Localized Two-Port Boundary Practice

For a localized two-port PCB S-parameter simulation, use one of these approaches:

1. **Expand the subregion until all critical signal and return paths are well inside the non-PML area.**

   - Include the full local ground plane and its stitching vias.
   - Include all relevant discontinuities, launches, matching parts, and return-path transitions.
   - Avoid cutting the signal trace or a critical reference-plane region at the PML boundary.

2. **Create explicit cut-plane ports or matched terminations** where a signal path intentionally leaves the subregion.

   - This is the standard way to represent the omitted portion of a larger PCB network.
   - The current RFsim port selection supports pad-based ports, not arbitrary cut-plane ports.

Use full-board mode for antennas and radiating structures. When copper, vias, or dielectric reach the boundary, compare results with increasing margins before trusting a cutout.

## Actions

| Action | Result |
|---|---|
| Load Settings | Restores a saved RFsim JSON configuration. |
| Save Settings | Saves dialog settings and per-component choices as JSON. |
| View Exported Geometry | Exports current geometry without solving and opens AppCSXCAD. |
| Open Previous Results | Lets you choose a prior `results.sNp` file or browse elsewhere. |
| Run Simulation | Validates settings and launches openEMS. |
| Close | Cancels the dialog without solving. |

## Result Files

| File | Content |
|---|---|
| `model.json` | Geometry, ports, settings, and component data passed to openEMS. |
| `results.sNp` | Touchstone S-parameter result. |
| `excN/` | Per-excitation openEMS files. |
| `excN/Ef.h5`, `excN/Hf.h5` | Complex E/H fields at Define at frequency. |
| `farfield_pN.json` | Far-field summary. |
| `lines.json` | Extracted MSL/CPW/stripline impedance. |
| `geometry_preview.xml` | AppCSXCAD geometry-only preview. |

The results viewer can save a selected E/H field as GIF and export XDMF plus HDF5 for ParaView.

## References

- [RFsim repository](https://github.com/NBalciunas/kicad-rfsim)
- [openEMS documentation](https://docs.openems.de/)
- [KEMET K-SIM](https://ksim.kemet.com/)
- [Murata SimSurfing](https://ds.murata.co.jp/simsurfing/en-us/)
- [ParaView documentation](https://docs.paraview.org/)

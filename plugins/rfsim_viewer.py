"""Standalone RFsim result viewer and field-export utility.

Examples:
    python rfsim_viewer.py results.s1p
    python rfsim_viewer.py --save-animation rfsim_results --field E
    python rfsim_viewer.py --export-paraview rfsim_results --field E --open-paraview

The ParaView export writes an XDMF descriptor and an HDF5 dataset containing
real and imaginary vector components of the selected openEMS field dump.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import h5py
import numpy as np


def load_field(h5_path):
    """Read an openEMS FD dump as x/y mm and complex F[y, x, component]."""
    with h5py.File(h5_path, "r") as handle:
        mesh = handle["Mesh"]
        x, y = np.asarray(mesh["x"], float), np.asarray(mesh["y"], float)
        fd = handle["FieldData"]["FD"]
        frequency = float(fd.attrs["frequency"][0])
        if "f0" in fd:
            field = np.asarray(fd["f0"])
        else:
            field = np.asarray(fd["f0_real"]) + 1j * np.asarray(fd["f0_imag"])
    if float(x.max() - x.min()) < 1.0:
        x, y = x * 1e3, y * 1e3
    field = np.squeeze(field)
    if field.shape[0] == 3:
        field = np.moveaxis(field, 0, -1)
    if field.shape[:2] == (len(x), len(y)):
        field = np.swapaxes(field, 0, 1)
    return x, y, field, frequency


def _field_port(h5_path):
    """Read the excited port number from an excN field path."""
    match = re.search(r"exc(\d+)", os.path.normpath(h5_path))
    return int(match.group(1)) if match else None


def find_field(result_dir, field_kind, port=None):
    """Find the selected E or H field dump below a result folder."""
    name = field_kind.upper() + "f.h5"
    if port:
        path = os.path.join(result_dir, "exc%d" % int(port), name)
        if os.path.isfile(path):
            return path
        raise FileNotFoundError("No %s field dump found for port %d below %s"
                                % (field_kind.upper(), int(port), result_dir))
    matches = glob.glob(os.path.join(result_dir, "exc*", name))
    if not matches:
        raise FileNotFoundError("No %s field dump found below %s" % (field_kind.upper(), result_dir))
    return max(matches, key=os.path.getmtime)


def _load_model(result_dir):
    try:
        with open(os.path.join(result_dir, "model.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _dump_z(model, port):
    if not model or not port:
        return None
    try:
        z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
        p = next(q for q in model["ports"] if q["number"] == port)
        return 0.5 * (z_of[p["layer"]] + z_of[p["ref_layer"]])
    except Exception:
        return None


def _field_stem(field_kind, frequency, port=None):
    tag = "_port%d" % int(port) if port else ""
    return "%s_field%s_%.3gGHz" % (field_kind.upper(), tag, frequency / 1e9)


def save_animation(result_dir, field_kind, output_path=None, frames=24, port=None):
    """Save a phase animation of the magnitude of one complex field dump."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    h5_path = find_field(result_dir, field_kind, port)
    port = port or _field_port(h5_path)
    x, y, field, frequency = load_field(h5_path)
    model = _load_model(result_dir)
    plane_z = _dump_z(model, port)
    unit = "V/m" if field_kind.upper() == "E" else "A/m"
    output_path = output_path or os.path.join(
        result_dir, _field_stem(field_kind, frequency, port) + ".gif")

    def magnitude(index):
        return np.linalg.norm(np.real(field * np.exp(2j * np.pi * index / frames)), axis=-1)

    vmax = max(float(magnitude(index).max()) for index in range(frames)) or 1.0
    figure = plt.figure(layout="constrained")
    grid = figure.add_gridspec(1, 2, width_ratios=[1.0, 3.2])
    info_axis = figure.add_subplot(grid[0])
    info_axis.axis("off")
    axis = figure.add_subplot(grid[1])
    image = axis.pcolormesh(x, y, magnitude(0), cmap="jet", vmin=0.0, vmax=vmax,
                            shading="gouraud")
    figure.colorbar(image, ax=axis, label=unit)
    axis.set(xlabel="x (mm)", ylabel="y (mm)", aspect="equal")
    port_line = "Port: %d\n" % int(port) if port else ""
    plane_line = "Plane: z=%.2f mm\n(substrate mid-plane)" % plane_z if plane_z is not None else "Plane: unknown"
    head = "Frequency: %.2f GHz\n%s" % (frequency / 1e9, port_line)
    tail = "\nMaximum: %.4g %s\n%s" % (vmax, unit, plane_line)
    info = info_axis.text(0.0, 0.5, head + "Phase: 0 deg" + tail,
                          fontsize=9, va="center", ha="left", linespacing=1.8)

    def update(index):
        image.set_array(magnitude(index).ravel())
        phase = round(360.0 * index / frames)
        axis.set_title("%s field, %.3g GHz, phase %d deg" % (
            field_kind.upper(), frequency / 1e9, phase))
        info.set_text(head + "Phase: %d deg" % phase + tail)
        return image, info

    animation = FuncAnimation(figure, update, frames=frames, interval=75, blit=False)
    animation.save(output_path, writer=PillowWriter(fps=12))
    plt.close(figure)
    return output_path


def export_paraview(result_dir, field_kind, output_dir=None, port=None):
    """Export one openEMS field dump as HDF5 plus an XDMF descriptor for ParaView."""
    h5_path = find_field(result_dir, field_kind, port)
    port = port or _field_port(h5_path)
    x, y, field, frequency = load_field(h5_path)
    output_dir = output_dir or result_dir
    os.makedirs(output_dir, exist_ok=True)
    stem = _field_stem(field_kind, frequency, port)
    h5_out = os.path.join(output_dir, stem + ".h5")
    xdmf_out = os.path.join(output_dir, stem + ".xdmf")
    dataset = "/Fields/%s" % field_kind.upper()
    with h5py.File(h5_out, "w") as handle:
        handle.create_dataset("/Mesh/x_mm", data=x)
        handle.create_dataset("/Mesh/y_mm", data=y)
        handle.create_dataset(dataset + "_real", data=field.real)
        handle.create_dataset(dataset + "_imag", data=field.imag)
        handle[dataset + "_real"].attrs["frequency_hz"] = frequency
        handle[dataset + "_imag"].attrs["frequency_hz"] = frequency
        if port:
            handle[dataset + "_real"].attrs["port"] = int(port)
            handle[dataset + "_imag"].attrs["port"] = int(port)
    h5_name = os.path.basename(h5_out)
    ny, nx, components = field.shape
    xdmf = """<?xml version=\"1.0\" ?>
<Xdmf Version=\"3.0\">
  <Domain>
    <Grid Name=\"RFsim %s Field\" GridType=\"Uniform\">
      <Topology TopologyType=\"2DRectMesh\" Dimensions=\"%d %d\"/>
      <Geometry GeometryType=\"VXVY\">
        <DataItem Dimensions=\"%d\" NumberType=\"Float\" Precision=\"8\" Format=\"HDF\">%s:/Mesh/x_mm</DataItem>
        <DataItem Dimensions=\"%d\" NumberType=\"Float\" Precision=\"8\" Format=\"HDF\">%s:/Mesh/y_mm</DataItem>
      </Geometry>
      <Attribute Name=\"%s_real\" AttributeType=\"Vector\" Center=\"Node\">
        <DataItem Dimensions=\"%d %d %d\" NumberType=\"Float\" Precision=\"8\" Format=\"HDF\">%s:%s_real</DataItem>
      </Attribute>
      <Attribute Name=\"%s_imag\" AttributeType=\"Vector\" Center=\"Node\">
        <DataItem Dimensions=\"%d %d %d\" NumberType=\"Float\" Precision=\"8\" Format=\"HDF\">%s:%s_imag</DataItem>
      </Attribute>
    </Grid>
  </Domain>
</Xdmf>
""" % (field_kind.upper(), ny, nx, nx, h5_name, ny, h5_name,
       field_kind.upper(), ny, nx, components, h5_name, dataset,
       field_kind.upper(), ny, nx, components, h5_name, dataset)
    with open(xdmf_out, "w", encoding="utf-8") as handle:
        handle.write(xdmf)
    return xdmf_out


def main():
    parser = argparse.ArgumentParser(description="Export RFsim fields for animation or ParaView")
    parser.add_argument("--save-animation", metavar="RESULT_DIR")
    parser.add_argument("--export-paraview", metavar="RESULT_DIR")
    parser.add_argument("--field", choices=("E", "H"), default="E")
    parser.add_argument("--port", type=int, help="Excited port number to export")
    parser.add_argument("--output", help="Output GIF path or ParaView output directory")
    parser.add_argument("--open-paraview", action="store_true")
    args = parser.parse_args()
    if bool(args.save_animation) == bool(args.export_paraview):
        parser.error("choose exactly one of --save-animation or --export-paraview")
    if args.save_animation:
        path = save_animation(args.save_animation, args.field, args.output,
                              port=args.port)
        print("Saved animation: %s" % path)
        return
    path = export_paraview(args.export_paraview, args.field, args.output,
                           port=args.port)
    print("ParaView XDMF: %s" % path)
    if args.open_paraview:
        subprocess.Popen([r"C:\Program Files\ParaView 6.1.1\bin\paraview.exe", path])


if __name__ == "__main__":
    main()

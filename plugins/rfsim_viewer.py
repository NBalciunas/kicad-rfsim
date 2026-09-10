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
import os
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


def find_field(result_dir, field_kind):
    """Find the most recently written E or H field dump below a result folder."""
    name = field_kind.upper() + "f.h5"
    matches = glob.glob(os.path.join(result_dir, "exc*", name))
    if not matches:
        raise FileNotFoundError("No %s field dump found below %s" % (field_kind.upper(), result_dir))
    return max(matches, key=os.path.getmtime)


def save_animation(result_dir, field_kind, output_path=None, frames=24):
    """Save a phase animation of the magnitude of one complex field dump."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    h5_path = find_field(result_dir, field_kind)
    x, y, field, frequency = load_field(h5_path)
    output_path = output_path or os.path.join(
        result_dir, "%s_field_%.3gGHz.gif" % (field_kind.upper(), frequency / 1e9))

    def magnitude(index):
        return np.linalg.norm(np.real(field * np.exp(2j * np.pi * index / frames)), axis=-1)

    vmax = max(float(magnitude(index).max()) for index in range(frames)) or 1.0
    figure, axis = plt.subplots(layout="constrained")
    image = axis.pcolormesh(x, y, magnitude(0), cmap="jet", vmin=0.0, vmax=vmax,
                            shading="gouraud")
    figure.colorbar(image, ax=axis, label="V/m" if field_kind.upper() == "E" else "A/m")
    axis.set(xlabel="x (mm)", ylabel="y (mm)", aspect="equal")

    def update(index):
        image.set_array(magnitude(index).ravel())
        axis.set_title("%s field, %.3g GHz, phase %d deg" % (
            field_kind.upper(), frequency / 1e9, round(360.0 * index / frames)))
        return (image,)

    animation = FuncAnimation(figure, update, frames=frames, interval=75, blit=False)
    animation.save(output_path, writer=PillowWriter(fps=12))
    plt.close(figure)
    return output_path


def export_paraview(result_dir, field_kind, output_dir=None):
    """Export one openEMS field dump as HDF5 plus an XDMF descriptor for ParaView."""
    h5_path = find_field(result_dir, field_kind)
    x, y, field, frequency = load_field(h5_path)
    output_dir = output_dir or result_dir
    os.makedirs(output_dir, exist_ok=True)
    stem = "%s_field_%.3gGHz" % (field_kind.upper(), frequency / 1e9)
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
    parser.add_argument("--output", help="Output GIF path or ParaView output directory")
    parser.add_argument("--open-paraview", action="store_true")
    args = parser.parse_args()
    if bool(args.save_animation) == bool(args.export_paraview):
        parser.error("choose exactly one of --save-animation or --export-paraview")
    if args.save_animation:
        path = save_animation(args.save_animation, args.field, args.output)
        print("Saved animation: %s" % path)
        return
    path = export_paraview(args.export_paraview, args.field, args.output)
    print("ParaView XDMF: %s" % path)
    if args.open_paraview:
        subprocess.Popen([r"C:\Program Files\ParaView 6.1.1\bin\paraview.exe", path])


if __name__ == "__main__":
    main()

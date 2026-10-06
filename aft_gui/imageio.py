"""Image loading for CZI and TIFF: returns 2D planes per channel.

Z-stacks / time series are collapsed by maximum-intensity projection so every
channel ends up as a single 2D image (what AFT works on).
"""

from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
import tifffile

IMAGE_EXT = (".czi", ".tif", ".tiff")


def _czi_channel_names(czi, n):
    names = []
    try:
        meta = czi.meta
        for path in (".//Channels/Channel", ".//DisplaySetting/Channels/Channel"):
            nodes = meta.findall(path)
            if nodes:
                for nd in nodes:
                    dye = nd.find("DyeName")
                    names.append((dye.text if dye is not None and dye.text else None)
                                 or nd.attrib.get("Name") or "")
                break
    except Exception:
        pass
    names = [s or f"C{i}" for i, s in enumerate(names[:n])]
    return names + [f"C{i}" for i in range(len(names), n)]


def _read_czi(path):
    from aicspylibczi import CziFile
    czi = CziFile(path)
    dims = czi.get_dims_shape()[0]
    n_c = dims.get("C", (0, 1))[1] - dims.get("C", (0, 1))[0]
    planes = []
    for c in range(n_c):
        arr, shp = czi.read_image(C=c) if "C" in dims else czi.read_image()
        labels = [d for d, _ in shp]
        keep = [labels.index("Y"), labels.index("X")]
        other = tuple(i for i in range(arr.ndim) if i not in keep)
        planes.append(arr.max(axis=other) if other else arr)
    return planes, _czi_channel_names(czi, n_c)


def _read_tiff(path):
    with tifffile.TiffFile(path) as tf:
        s = tf.series[0]
        arr = s.asarray()
        axes = s.axes.upper()
        names = []
        if tf.imagej_metadata and tf.imagej_metadata.get("Labels"):
            names = list(tf.imagej_metadata["Labels"])
    if arr.ndim == 2:
        return [arr], ["C0"]
    # RGB / samples last
    if "S" in axes and arr.shape[axes.index("S")] in (3, 4):
        arr = np.moveaxis(arr, axes.index("S"), 0)[:3]
        axes = "C" + axes.replace("S", "")
    if "C" not in axes:
        # guess: smallest leading axis <= 6 is channel, rest projected
        lead = arr.shape[:-2]
        if lead and min(lead) <= 6 and len(lead) > 1:
            ci = int(np.argmin(lead))
            axes = "".join("C" if i == ci else "Q" for i in range(len(lead))) + "YX"
        else:
            axes = "Q" * len(lead) + "YX"
    y, x = arr.ndim - 2, arr.ndim - 1
    if "C" in axes:
        ci = axes.index("C")
        arr = np.moveaxis(arr, ci, 0)
        other = tuple(range(1, arr.ndim - 2))
        planes = [a.max(axis=tuple(i - 1 for i in other)) if other else a for a in arr]
    else:
        other = tuple(i for i in range(arr.ndim) if i not in (y, x))
        planes = [arr.max(axis=other)]
    n = len(planes)
    names = (names[:n] if len(names) >= n else []) or [f"C{i}" for i in range(n)]
    return planes, names


@lru_cache(maxsize=4)  # keep only a few images in RAM
def read_image(path: str):
    """Returns (list_of_2D_arrays, channel_names)."""
    ext = os.path.splitext(path)[1].lower()
    planes, names = _read_czi(path) if ext == ".czi" else _read_tiff(path)
    return [np.ascontiguousarray(p) for p in planes], names


def channel_names(path: str):
    return read_image(path)[1]


def get_plane(path: str, channel) -> np.ndarray:
    """channel: name (matched first) or int index."""
    planes, names = read_image(path)
    if isinstance(channel, str) and channel in names:
        return planes[names.index(channel)]
    try:
        i = int(channel) if not isinstance(channel, str) else int(channel.lstrip("C"))
    except ValueError:
        i = 0
    return planes[min(max(i, 0), len(planes) - 1)]


def find_images(folder: str):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        out += [os.path.join(root, f) for f in sorted(files)
                if f.lower().endswith(IMAGE_EXT) and not f.startswith(".")]
    return out

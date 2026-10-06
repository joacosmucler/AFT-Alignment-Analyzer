"""Regenerates the app icon (aft_gui/icon.png, packaging/icon.ico, packaging/icon.icns).

    python packaging/make_icon.py

The .icns step uses macOS `iconutil` and is skipped elsewhere.
"""

import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = 1024


def draw(size=S):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size * 0.22, fill=(44, 62, 80, 255))
    rng = np.random.default_rng(3)
    n, pad = 5, size * 0.16
    step = (size - 2 * pad) / (n - 1)
    for i in range(n):
        for j in range(n):
            # orientations converge from disordered (left) to aligned (right)
            ang = np.radians(30) + rng.normal(0, 1) * (1 - j / (n - 1)) * 0.9
            cx, cy = pad + j * step, pad + i * step
            L = step * 0.42
            dx, dy = np.cos(ang) * L, -np.sin(ang) * L
            col = (241, 196, 15, 255) if j >= 2 else (236, 240, 241, 255)
            d.line([cx - dx, cy - dy, cx + dx, cy + dy], fill=col, width=int(size * 0.035))
    return im


if __name__ == "__main__":
    big = draw()
    big.save(os.path.join(ROOT, "aft_gui", "icon.png"))
    big.save(os.path.join(HERE, "icon.ico"), sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as t:
            iconset = os.path.join(t, "icon.iconset")
            os.mkdir(iconset)
            for s in (16, 32, 128, 256, 512):
                big.resize((s, s), Image.LANCZOS).save(f"{iconset}/icon_{s}x{s}.png")
                big.resize((2 * s, 2 * s), Image.LANCZOS).save(f"{iconset}/icon_{s}x{s}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o",
                            os.path.join(HERE, "icon.icns")], check=True)
    print("icons written")

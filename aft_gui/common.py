"""Shared state, worker thread, styles and the image/map viewer."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
                             QLabel, QSlider, QCheckBox)

from .core import AFTParams, AFTResult

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

GREEN_BTN = (
    "QPushButton { background-color: #27ae60; color: white; font-weight: bold;"
    " border: 1px solid #1e8449; border-radius: 4px; padding: 6px 12px; }"
    "QPushButton:hover { background-color: #2ecc71; }"
    "QPushButton:disabled { background-color: #7f8c8d; border-color: #6c7a7a; }")
HINT = "color: #7f8c8d; font-size: 11px;"


# --------------------------------------------------------------------------
# Project state
# --------------------------------------------------------------------------
@dataclass
class ImageEntry:
    path: str
    condition: str = ""
    replicate: str = ""
    include: bool = True
    result: AFTResult | None = None
    result_key: tuple | None = None   # (channel, params) the result was made with

    @property
    def name(self):
        return os.path.basename(self.path)


class Project(QObject):
    entries_changed = pyqtSignal()
    params_changed = pyqtSignal()
    results_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.entries: list[ImageEntry] = []
        self.channel: str = "C0"
        self.params = AFTParams()

    # -- helpers --
    def key(self):
        return (self.channel, tuple(self.params.to_dict().values()))

    def is_fresh(self, e: ImageEntry):
        return e.result is not None and e.result_key == self.key()

    def included(self):
        return [e for e in self.entries if e.include]

    def conditions(self):
        seen = []
        for e in self.included():
            c = e.condition or "(none)"
            if c not in seen:
                seen.append(c)
        return seen

    def set_params(self, p: AFTParams):
        if p != self.params:
            self.params = p
            self.params_changed.emit()

    def add_paths(self, paths):
        have = {e.path for e in self.entries}
        new = [ImageEntry(p) for p in paths if p not in have]
        self.entries += new
        if new:
            self.entries_changed.emit()
        return len(new)

    # -- persistence --
    def save(self, path):
        d = dict(version=1, channel=self.channel, params=self.params.to_dict(),
                 entries=[dict(path=e.path, condition=e.condition,
                               replicate=e.replicate, include=e.include)
                          for e in self.entries])
        with open(path, "w") as f:
            json.dump(d, f, indent=1)

    def load(self, path):
        with open(path) as f:
            d = json.load(f)
        self.channel = d.get("channel", "C0")
        self.params = AFTParams(**d.get("params", {}))
        self.entries = [ImageEntry(**e) for e in d.get("entries", [])]
        self.entries_changed.emit()
        self.params_changed.emit()


# --------------------------------------------------------------------------
# Generic worker
# --------------------------------------------------------------------------
class Worker(QThread):
    """Runs fn(worker) in a thread. fn may call worker.emit_progress /
    worker.emit_partial and check worker.cancelled."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    partial = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancelled = False

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as ex:  # surfaced to the UI
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(ex).__name__}: {ex}")


# --------------------------------------------------------------------------
# Image + AFT overlay viewer
# --------------------------------------------------------------------------
_MAPS = {
    "Vectors only": None,
    "Local order parameter": ("local_order", (-1, 1), "viridis"),
    "Orientation (°)": ("theta", (-90, 90), "CET-C6"),
    "Eccentricity": ("eccentricity", (0, 1), "magma"),
}


def contrast(im):
    lo, hi = np.percentile(im[::4, ::4], (0.5, 99.7))
    return (float(lo), float(hi if hi > lo else lo + 1))


class ImagePanel(QWidget):
    """Image with orientation vectors and a switchable per-window map."""

    def __init__(self, title="", parent=None, show_controls=True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.title = QLabel(f"<b>{title}</b>")
        bar.addWidget(self.title)
        bar.addStretch()
        self.map_combo = QComboBox()
        self.map_combo.addItems(list(_MAPS))
        self.map_combo.setCurrentIndex(1)
        self.chk_vec = QCheckBox("Vectors")
        self.chk_vec.setChecked(True)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(45)
        self.opacity.setFixedWidth(80)
        self.opacity.setToolTip("Map opacity")
        for w in (QLabel("Map:"), self.map_combo, QLabel("α"), self.opacity, self.chk_vec):
            bar.addWidget(w)
        if show_controls:
            lay.addLayout(bar)

        self.glw = pg.GraphicsLayoutWidget()
        self.vb = self.glw.addViewBox(lockAspect=True, invertY=True)
        self.img = pg.ImageItem()
        self.map = pg.ImageItem()
        self.vec = pg.PlotCurveItem(connect="pairs",
                                    pen=pg.mkPen((255, 230, 0), width=1.5))
        for it in (self.img, self.map, self.vec):
            self.vb.addItem(it)
        self.cbar = pg.ColorBarItem(values=(-1, 1), colorMap="viridis",
                                    interactive=False, width=12)
        self.cbar.setImageItem(self.map)
        self.glw.addItem(self.cbar)
        lay.addWidget(self.glw, 1)

        self._res: AFTResult | None = None
        self.map_combo.currentIndexChanged.connect(self._draw_overlay)
        self.chk_vec.toggled.connect(self._draw_overlay)
        self.opacity.valueChanged.connect(lambda v: self.map.setOpacity(v / 100))
        self.map.setOpacity(0.45)

    def set_title(self, t):
        self.title.setText(f"<b>{t}</b>")

    def set_image(self, im, reset_view=True):
        if im is None:
            self.img.clear()
        else:
            self.img.setImage(im, levels=contrast(im))
            if reset_view:
                self.vb.autoRange(padding=0.01)

    def set_result(self, res: AFTResult | None):
        self._res = res
        self._draw_overlay()

    def _draw_overlay(self):
        r = self._res
        mode = _MAPS[self.map_combo.currentText()]
        if r is None or r.theta.size == 0:
            self.map.clear()
            self.vec.setData([], [])
            self.cbar.setVisible(False)
            return
        x, y = r.x, r.y
        step = (x[1] - x[0]) if len(x) > 1 else r.window_size
        if mode is None:
            self.map.clear()
            self.cbar.setVisible(False)
        else:
            attr, lv, cmap = mode
            data = getattr(r, attr)
            if attr == "theta":
                data = np.degrees(data)
            self.map.setImage(data.astype(np.float32), levels=lv)
            self.map.setRect(float(x[0] - step / 2), float(y[0] - step / 2),
                             float(step * len(x)), float(step * len(y)))
            self.cbar.setColorMap(pg.colormap.get(cmap))
            self.cbar.setLevels(lv)
            self.cbar.setVisible(True)
        if self.chk_vec.isChecked():
            X, Y = np.meshgrid(x, y)
            ok = np.isfinite(r.theta)
            L = step * 0.45
            dx, dy = np.cos(r.theta[ok]) * L, -np.sin(r.theta[ok]) * L
            xs = np.empty(2 * ok.sum())
            ys = np.empty_like(xs)
            xs[0::2], xs[1::2] = X[ok] - dx, X[ok] + dx
            ys[0::2], ys[1::2] = Y[ok] - dy, Y[ok] + dy
            self.vec.setData(xs, ys)
        else:
            self.vec.setData([], [])

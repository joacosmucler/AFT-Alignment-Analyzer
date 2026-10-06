"""Stage 2 — interactive parameter tuning on one or two images."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QSpinBox, QDoubleSpinBox, QSlider, QPushButton,
                             QComboBox, QLabel, QGroupBox, QCheckBox, QSplitter)

from . import imageio
from .core import AFTParams, DEFAULTS, analyze
from .common import Project, ImagePanel, Worker, GREEN_BTN, HINT


def _linked(spin, lo, hi, scale=1):
    """Slider bound to a spin box (scale for float spins)."""
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(int(lo * scale), int(hi * scale))
    s.setValue(int(round(spin.value() * scale)))
    s.valueChanged.connect(lambda v: spin.setValue(v / scale if scale != 1 else v))
    spin.valueChanged.connect(lambda v: (s.blockSignals(True), s.setValue(int(round(v * scale))),
                                         s.blockSignals(False)))
    spin._slider, spin._scale = s, scale
    return s


class Viewer(QWidget):
    """Image picker + panel + readout for one image."""

    def __init__(self, label, optional):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel(f"<b>{label}</b>"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(220)
        self.optional = optional
        row.addWidget(self.combo, 1)
        v.addLayout(row)
        self.panel = ImagePanel()
        v.addWidget(self.panel, 1)
        self.readout = QLabel("—")
        self.readout.setStyleSheet("font-size: 15px;")
        v.addWidget(self.readout)

    def fill(self, entries):
        cur = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        if self.optional:
            self.combo.addItem("— none —", None)
        for e in entries:
            self.combo.addItem(f"{e.name}  [{e.condition or '—'}]", e.path)
        i = self.combo.findData(cur)
        self.combo.setCurrentIndex(i if i >= 0 else 0)
        self.combo.blockSignals(False)


class TuneTab(QWidget):
    proceed = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.p = project
        self._worker = None
        self._pending = False

        # ---------- controls
        ctrl = QWidget()
        ctrl.setMaximumWidth(330)
        cv = QVBoxLayout(ctrl)
        g = QGroupBox("AFT parameters")
        f = QFormLayout(g)
        self.ws = QSpinBox(); self.ws.setRange(9, 1001); self.ws.setSingleStep(2)
        self.ws.setSuffix(" px")
        self.ws.setToolTip("Side of the square window whose FFT gives the local "
                           "orientation. Should contain a few fibres. Forced odd.")
        self.ov = QDoubleSpinBox(); self.ov.setRange(0.05, 1.0); self.ov.setSingleStep(0.05)
        self.ov.setToolTip("Window spacing as a fraction of window size (AFT 'overlap'). "
                           "0.5 → windows every ½ window; smaller = denser vector field.")
        self.nr = QSpinBox(); self.nr.setRange(1, 50)
        self.nr.setSuffix(" vectors")
        self.nr.setToolTip("Neighbourhood radius (in windows) over which local "
                           "orientations are compared to compute the order parameter.")
        self.it = QDoubleSpinBox(); self.it.setRange(0, 65535); self.it.setDecimals(1)
        self.it.setToolTip("Windows whose mean intensity is ≤ this are ignored (background).")
        self.et = QDoubleSpinBox(); self.et.setRange(0, 1); self.et.setSingleStep(0.05)
        self.et.setToolTip("Windows with FFT eccentricity below this (isotropic, no clear "
                           "fibre direction) are ignored. 0 = off.")
        f.addRow("Window size", self.ws); f.addRow("", _linked(self.ws, 9, 401))
        f.addRow("Overlap", self.ov); f.addRow("", _linked(self.ov, 0.05, 1, 100))
        f.addRow("Neighbourhood", self.nr); f.addRow("", _linked(self.nr, 1, 30))
        f.addRow("Intensity thr.", self.it)
        f.addRow("Eccentricity thr.", self.et); f.addRow("", _linked(self.et, 0, 1, 100))
        cv.addWidget(g)
        b = QPushButton("Reset to paper defaults")
        b.setToolTip("Window 65 px, overlap 0.5, neighbourhood 5 (Marcotti et al. 2021)")
        b.clicked.connect(self._reset)
        cv.addWidget(b)
        self.auto_it = QPushButton("Suggest intensity threshold (Otsu)")
        self.auto_it.clicked.connect(self._suggest_thresh)
        cv.addWidget(self.auto_it)
        self.live = QCheckBox("Live update")
        self.live.setChecked(True)
        cv.addWidget(self.live)
        self.btn_run = QPushButton("Update preview")
        self.btn_run.clicked.connect(self._compute)
        cv.addWidget(self.btn_run)
        h = QLabel("Order parameter: 1 = all neighbouring windows parallel, "
                   "0 = random orientations. Maps: each tile is one window; windows closer "
                   "than the neighbourhood radius to the edge have no local order (as in AFT).\n\n"
                   "Tip: compare a low- and a high-alignment image (B) and pick "
                   "parameters where vectors follow the fibres and the difference "
                   "is clear — or use the Parameter sweep tab.")
        h.setWordWrap(True); h.setStyleSheet(HINT)
        cv.addWidget(h)
        cv.addStretch()
        nxt = QPushButton("Next: run analysis  →")
        nxt.setStyleSheet(GREEN_BTN)
        nxt.clicked.connect(self.proceed.emit)
        cv.addWidget(nxt)

        # ---------- viewers
        self.va = Viewer("Image A", False)
        self.vb = Viewer("Image B", True)
        sp = QSplitter()
        sp.addWidget(self.va)
        sp.addWidget(self.vb)
        for v in (self.va, self.vb):
            v.combo.currentIndexChanged.connect(self._compute)

        lay = QHBoxLayout(self)
        lay.addWidget(ctrl)
        lay.addWidget(sp, 1)

        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self._compute)
        for w in (self.ws, self.ov, self.nr, self.it, self.et):
            w.valueChanged.connect(self._params_edited)
        self.p.entries_changed.connect(self._fill)
        self.p.params_changed.connect(self._load_params)
        self._load_params()

    # ---------- params <-> widgets
    def _load_params(self):
        pr = self.p.params
        for w, v in ((self.ws, pr.window_size), (self.ov, pr.overlap),
                     (self.nr, pr.neighborhood_radius), (self.it, pr.intensity_thresh),
                     (self.et, pr.eccentricity_thresh)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
            if hasattr(w, "_slider"):
                w._slider.blockSignals(True)
                w._slider.setValue(int(round(v * w._scale)))
                w._slider.blockSignals(False)

    def _params_edited(self, *_):
        ws = self.ws.value()
        self.p.set_params(AFTParams(ws if ws % 2 else ws + 1, round(self.ov.value(), 3),
                                    self.nr.value(), self.it.value(), self.et.value()))
        if self.live.isChecked():
            self._timer.start()

    def _reset(self):
        self.p.set_params(AFTParams(**DEFAULTS))
        self._compute()

    def _suggest_thresh(self):
        path = self.va.combo.currentData()
        if not path:
            return
        from skimage.filters import threshold_otsu
        im = imageio.get_plane(path, self.p.channel)
        self.it.setValue(float(threshold_otsu(im[::2, ::2])) * 0.5)
        self.status.emit("Intensity threshold set to ½ Otsu of image A (adjust as needed).")

    def _fill(self):
        inc = self.p.included()
        self.va.fill(inc)
        self.vb.fill(inc)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._fill()
        self._compute()

    # ---------- computation (threaded, coalesced)
    def _compute(self, *_):
        if self._worker and self._worker.isRunning():
            self._pending = True
            return
        jobs = [(v, v.combo.currentData()) for v in (self.va, self.vb)]
        jobs = [(v, pth) for v, pth in jobs if pth]
        for v in (self.va, self.vb):
            if not v.combo.currentData():
                v.panel.set_image(None); v.panel.set_result(None); v.readout.setText("—")
        if not jobs:
            return
        params, chan = self.p.params, self.p.channel

        def run(w):
            out = []
            for v, pth in jobs:
                im = imageio.get_plane(pth, chan)
                out.append((v, pth, im, analyze(im, params)))
            return out

        self._worker = Worker(run, self)
        self._worker.done.connect(self._show)
        self._worker.failed.connect(lambda m: self.status.emit(f"Error: {m}"))
        self._worker.finished.connect(self._after)
        self.btn_run.setText("Computing…")
        self._worker.start()

    def _after(self):
        self.btn_run.setText("Update preview")
        if self._pending:
            self._pending = False
            self._compute()

    def _show(self, out):
        for v, pth, im, r in out:
            new = getattr(v, "_path", None) != pth
            v._path = pth
            v.panel.set_image(im, reset_view=new)
            v.panel.set_result(r)
            n_ok = int(np.isfinite(r.theta).sum())
            v.readout.setText(
                f"Order parameter: <b>{r.order_parameter:.3f}</b> &nbsp; "
                f"<span style='{HINT}'>{r.theta.shape[1]}×{r.theta.shape[0]} windows, "
                f"{n_ok}/{r.theta.size} valid</span>")

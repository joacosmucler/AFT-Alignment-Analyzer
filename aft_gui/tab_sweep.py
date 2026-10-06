"""Stage 3 (optional) — parameter sweep: window size × neighbourhood radius.

Mirrors AFT_tools.parameter_search / parameter_comparison: median order
parameter per condition, difference between two conditions and the
Mann-Whitney p-value per cell. Less reliable cells are flagged, and a
suggested cell (stable plateau of the A − B difference) is marked but never
applied automatically: the user picks and confirms visually.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QSpinBox, QPushButton, QComboBox, QLabel, QGroupBox,
                             QListWidget, QListWidgetItem, QProgressBar, QFileDialog)
from scipy.ndimage import generic_filter
from scipy.stats import mannwhitneyu

from . import imageio
from .core import AFTParams, sweep_windows, sweep_image, overlapping_neighbour_fraction
from .common import Project, Worker, GREEN_BTN, HINT

MIN_WINDOWS = 30        # fewer contributing windows per image → unstable median
MAX_OVERLAP_FRAC = 0.5  # most neighbours share pixels with the centre window

GUIDE = (
    "<b>How to choose parameters</b><br>"
    "• Judge by <b>Difference A − B</b> and <b>p-value</b>, not by the brightest "
    "cell of a single condition: order always rises toward small windows and "
    "radius 1, for every sample.<br>"
    "• Prefer a <b>stable plateau</b> (several neighbouring cells alike) over an "
    "isolated peak, and avoid cells marked ×.<br>"
    "• The window should contain several fibres; window × neighbourhood sets the "
    "length scale compared (the paper finds the optimum near cell size).<br>"
    "• Confirm in Tune: vectors must follow the fibres.<br>"
    "• <b>Bias:</b> picking the cell that best separates <i>the same images</i> "
    "you will test inflates significance. Tune on a pilot subset or another "
    "replicate, fix the parameters, then analyse everything and report them.")


class SweepTab(QWidget):
    status = pyqtSignal(str)
    apply_params = pyqtSignal()

    def __init__(self, project: Project):
        super().__init__()
        self.p = project
        self.df: pd.DataFrame | None = None
        self._worker = None
        self._sel = None
        self._suggest = None
        self._overlap = project.params.overlap

        # ---------------- controls
        ctrl = QWidget()
        ctrl.setMaximumWidth(330)
        cv = QVBoxLayout(ctrl)
        g1 = QGroupBox("Conditions to include")
        v1 = QVBoxLayout(g1)
        self.cond_list = QListWidget()
        self.cond_list.setMaximumHeight(120)
        v1.addWidget(self.cond_list)
        self.max_img = QSpinBox(); self.max_img.setRange(1, 1000); self.max_img.setValue(5)
        self.max_img.setToolTip("Use a pilot subset: fewer images = faster sweep and "
                                "less circularity with the final analysis.")
        r = QHBoxLayout(); r.addWidget(QLabel("Max images / condition")); r.addWidget(self.max_img)
        v1.addLayout(r)
        cv.addWidget(g1)

        g2 = QGroupBox("Search space")
        f = QFormLayout(g2)
        self.ws_min = QSpinBox(); self.ws_min.setRange(9, 999); self.ws_min.setValue(25)
        self.ws_max = QSpinBox(); self.ws_max.setRange(0, 2000); self.ws_max.setValue(0)
        self.ws_max.setSpecialValueText("auto (⅓ image)")
        self.ws_step = QSpinBox(); self.ws_step.setRange(2, 200); self.ws_step.setValue(10)
        self.n_max = QSpinBox(); self.n_max.setRange(0, 200); self.n_max.setValue(15)
        self.n_max.setSpecialValueText("all")
        f.addRow("Min window (px)", self.ws_min)
        f.addRow("Max window (px)", self.ws_max)
        f.addRow("Window step (px)", self.ws_step)
        f.addRow("Max neighbourhood", self.n_max)
        note = QLabel("Overlap and thresholds are taken from the Tune tab.")
        note.setStyleSheet(HINT)
        f.addRow(note)
        cv.addWidget(g2)

        self.btn = QPushButton("Run sweep")
        self.btn.setStyleSheet(GREEN_BTN)
        self.btn.clicked.connect(self._run)
        cv.addWidget(self.btn)
        self.prog = QProgressBar()
        cv.addWidget(self.prog)

        g3 = QGroupBox("Display")
        f3 = QFormLayout(g3)
        self.view = QComboBox()
        self.view.addItems(["Difference A − B", "p-value (Mann-Whitney A vs B)",
                            "Median order parameter (A)"])
        self.ca, self.cb = QComboBox(), QComboBox()
        self.min_win = QSpinBox(); self.min_win.setRange(0, 10000); self.min_win.setValue(MIN_WINDOWS)
        self.min_win.setToolTip("Cells where the median image has fewer windows contributing "
                                "a local order value are marked × (unstable median).")
        f3.addRow("Show", self.view)
        f3.addRow("Condition A", self.ca)
        f3.addRow("Condition B", self.cb)
        f3.addRow("Min windows", self.min_win)
        for w in (self.view, self.ca, self.cb):
            w.currentIndexChanged.connect(self._draw)
        self.min_win.valueChanged.connect(self._draw)
        cv.addWidget(g3)
        self.btn_csv = QPushButton("Export sweep CSV…")
        self.btn_csv.clicked.connect(self._export)
        cv.addWidget(self.btn_csv)
        cv.addStretch()

        # ---------------- heatmap + guide
        right = QWidget()
        rv = QVBoxLayout(right)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Neighbourhood radius (vectors)")
        self.plot.setLabel("left", "Window size (px)")
        self.plot.getViewBox().invertY(True)
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        self.flags = pg.ScatterPlotItem(symbol="x", size=9, pen=None,
                                        brush=pg.mkBrush(150, 150, 150, 200), pxMode=True)
        self.sugg = pg.ScatterPlotItem(symbol="star", size=20, brush=pg.mkBrush(255, 255, 255, 220),
                                       pen=pg.mkPen("k", width=1), pxMode=True)
        self.marker = pg.ScatterPlotItem(symbol="s", size=18, brush=None,
                                         pen=pg.mkPen("r", width=2), pxMode=True)
        for it in (self.flags, self.sugg, self.marker):
            self.plot.addItem(it)
        self.cbar = pg.ColorBarItem(values=(0, 1), colorMap="viridis", interactive=False)
        self.cbar.setImageItem(self.img, insert_in=self.plot.getPlotItem())
        rv.addWidget(self.plot, 1)
        legend = QLabel("× less reliable (few windows, or most neighbours overlap the "
                        "centre window)  ·  ★ suggestion  ·  □ your selection")
        legend.setStyleSheet(HINT)
        rv.addWidget(legend)
        bot = QHBoxLayout()
        self.readout = QLabel("Run a sweep to see the map.")
        bot.addWidget(self.readout, 1)
        self.btn_sugg = QPushButton("Select suggestion ★")
        self.btn_sugg.setEnabled(False)
        self.btn_sugg.setToolTip("Selects (does not apply) the centre of the most stable "
                                 "region of large |A − B| among reliable cells.")
        self.btn_sugg.clicked.connect(lambda: self._select(self._suggest))
        bot.addWidget(self.btn_sugg)
        self.btn_apply = QPushButton("Use these parameters")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        bot.addWidget(self.btn_apply)
        rv.addLayout(bot)
        guide = QLabel(GUIDE)
        guide.setWordWrap(True)
        guide.setStyleSheet("font-size: 11px;")
        rv.addWidget(guide)
        self.plot.scene().sigMouseClicked.connect(self._click)
        self.plot.scene().sigMouseMoved.connect(self._hover)

        lay = QHBoxLayout(self)
        lay.addWidget(ctrl)
        lay.addWidget(right, 1)
        self.p.entries_changed.connect(self._fill_conditions)
        self.p.results_changed.connect(self._fill_conditions)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._fill_conditions()

    def _fill_conditions(self):
        conds = self.p.conditions()
        checked = {self.cond_list.item(i).text() for i in range(self.cond_list.count())
                   if self.cond_list.item(i).checkState() == Qt.CheckState.Checked}
        self.cond_list.clear()
        for c in conds:
            it = QListWidgetItem(c)
            it.setCheckState(Qt.CheckState.Checked if (c in checked or not checked)
                             else Qt.CheckState.Unchecked)
            self.cond_list.addItem(it)

    # ---------------- run
    def _run(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancelled = True
            return
        conds = [self.cond_list.item(i).text() for i in range(self.cond_list.count())
                 if self.cond_list.item(i).checkState() == Qt.CheckState.Checked]
        jobs = []
        for c in conds:
            es = [e for e in self.p.included() if (e.condition or "(none)") == c]
            jobs += [(c, e.path) for e in es[:self.max_img.value()]]
        if not jobs:
            self.status.emit("No images for the selected conditions.")
            return
        pr, chan = self.p.params, self.p.channel
        self._overlap = pr.overlap
        ws_min, ws_max, ws_step = self.ws_min.value(), self.ws_max.value() or None, self.ws_step.value()
        n_max = self.n_max.value() or None

        def run(w):
            rows = []
            for k, (c, path) in enumerate(jobs):
                im = imageio.get_plane(path, chan)
                wss = sweep_windows(im.shape, ws_min, ws_max, ws_step)
                for ws, n, op, nw in sweep_image(im, wss, pr.overlap, n_max, pr.intensity_thresh,
                                                 pr.eccentricity_thresh,
                                                 cancelled=lambda: w.cancelled):
                    rows.append((path, c, ws, n, op, nw))
                w.progress.emit(k + 1, len(jobs))
                if w.cancelled:
                    break
            return pd.DataFrame(rows, columns=["image", "condition", "window_size",
                                               "neighborhood_radius", "order_parameter",
                                               "n_windows"])

        self._worker = Worker(run, self)
        self._worker.progress.connect(lambda i, n: (self.prog.setMaximum(n), self.prog.setValue(i)))
        self._worker.done.connect(self._done)
        self._worker.failed.connect(lambda m: self.status.emit(f"Sweep error: {m}"))
        self._worker.finished.connect(lambda: self.btn.setText("Run sweep"))
        self.prog.setValue(0)
        self.btn.setText("Cancel")
        self.status.emit(f"Sweeping {len(jobs)} image(s)…")
        self._worker.start()

    def _done(self, df):
        self.df = df
        self._sel = None
        conds = list(dict.fromkeys(df.condition))
        for cb, i in ((self.ca, 0), (self.cb, 1)):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(conds)
            cb.setCurrentIndex(min(i, len(conds) - 1))
            cb.blockSignals(False)
        self.view.blockSignals(True)
        self.view.setCurrentIndex(0 if len(conds) > 1 else 2)
        self.view.blockSignals(False)
        self.btn_apply.setEnabled(False)
        self.btn_apply.setText("Use these parameters")
        self.status.emit(f"Sweep done: {len(df)} measurements.")
        self.readout.setText("Hover a cell to read its value; click to select it.")
        self._draw()

    # ---------------- matrices
    def _grid(self, values: pd.Series):
        t = values.unstack()
        return t.reindex(index=self.wss, columns=self.ns).to_numpy(float)

    def _median(self, c):
        d = self.df[self.df.condition == c]
        return self._grid(d.groupby(["window_size", "neighborhood_radius"]).order_parameter.median())

    def _pvalues(self, a, b):
        P = np.full((len(self.wss), len(self.ns)), np.nan)
        g = {k: v.order_parameter.dropna().to_numpy()
             for k, v in self.df.groupby(["condition", "window_size", "neighborhood_radius"])}
        for i, ws in enumerate(self.wss):
            for j, n in enumerate(self.ns):
                xa, xb = g.get((a, ws, n), []), g.get((b, ws, n), [])
                if len(xa) and len(xb):
                    P[i, j] = mannwhitneyu(xa, xb).pvalue
        return P

    def _unreliable(self):
        nw = self._grid(self.df.groupby(["window_size", "neighborhood_radius"]).n_windows.median())
        few = ~(nw >= self.min_win.value())
        ov = np.array([overlapping_neighbour_fraction(self._overlap, int(n)) > MAX_OVERLAP_FRAC
                       for n in self.ns])
        return (few | ov[None, :]) & np.isfinite(nw)

    def _suggestion(self, diff, bad):
        """Centre of the most stable large-|difference| region (3×3 mean)."""
        d = np.where(bad, np.nan, np.abs(diff))
        if not np.isfinite(d).any():
            return None
        sm = generic_filter(d, lambda v: np.nan if np.isnan(v[4]) else np.nanmean(v),
                            size=3, mode="constant", cval=np.nan)
        if not np.isfinite(sm).any():
            return None
        return tuple(int(x) for x in np.unravel_index(np.nanargmax(sm), sm.shape))

    # ---------------- display
    def _draw(self, *_):
        if self.df is None or self.df.empty:
            return
        self.wss = np.sort(self.df.window_size.unique())
        self.ns = np.sort(self.df.neighborhood_radius.unique())
        a, b = self.ca.currentText(), self.cb.currentText()
        two = a != b and self.cb.count() > 1
        mode = self.view.currentIndex() if two else 2
        bad = self._unreliable()
        self._suggest = self._suggestion(self._median(a) - self._median(b), bad) if two else None

        if mode == 0:
            M = self._median(a) - self._median(b)
            m = np.nanmax(np.abs(M)) if np.isfinite(M).any() else 1
            lv, cmap, title = (-m, m), "CET-D1", f"Median order parameter difference: {a} − {b}"
        elif mode == 1:
            M = self._pvalues(a, b)
            lv, cmap, title = (0, 0.1), "CET-L17", f"Mann-Whitney p ({a} vs {b}); scale capped at 0.1"
        else:
            M = self._median(a)
            lv, cmap, title = (0, 1), "viridis", f"Median order parameter — {a}"
        self.M = M
        self.img.setImage(M.astype(np.float32), levels=lv)
        self.img.setRect(0, 0, M.shape[1], M.shape[0])
        self.cbar.setColorMap(pg.colormap.get(cmap))
        self.cbar.setLevels(lv)
        self.plot.setTitle(title)
        self.plot.getAxis("bottom").setTicks([[(j + .5, str(n)) for j, n in enumerate(self.ns)]])
        self.plot.getAxis("left").setTicks([[(i + .5, str(w)) for i, w in enumerate(self.wss)]])
        bi, bj = np.nonzero(bad)
        self.flags.setData(bj + .5, bi + .5)
        if self._suggest:
            i, j = self._suggest
            self.sugg.setData([j + .5], [i + .5])
        else:
            self.sugg.setData([], [])
        self.btn_sugg.setEnabled(self._suggest is not None)
        self.plot.autoRange(padding=0)
        self._mark()

    def _cell(self, scene_pos):
        if self.df is None or not self.plot.sceneBoundingRect().contains(scene_pos):
            return None
        pt = self.plot.getViewBox().mapSceneToView(scene_pos)
        j, i = int(np.floor(pt.x())), int(np.floor(pt.y()))
        if 0 <= i < len(self.wss) and 0 <= j < len(self.ns):
            return i, j
        return None

    def _describe(self, i, j):
        nw = self.df[(self.df.window_size == self.wss[i]) &
                     (self.df.neighborhood_radius == self.ns[j])].n_windows.median()
        flag = "  (×: less reliable)" if self._unreliable()[i, j] else ""
        return (f"window {self.wss[i]} px, neighbourhood {self.ns[j]}: {self.M[i, j]:.4g} "
                f"— ~{nw:.0f} windows/image{flag}")

    def _hover(self, pos):
        c = self._cell(pos)
        if c:
            self.readout.setText(self._describe(*c))

    def _click(self, ev):
        self._select(self._cell(ev.scenePos()))

    def _select(self, c):
        if not c:
            return
        self._sel = c
        i, j = c
        self.readout.setText("<b>Selected:</b> " + self._describe(i, j))
        self.btn_apply.setEnabled(True)
        self.btn_apply.setText(f"Use window {self.wss[i]} / neighbourhood {self.ns[j]}")
        self._mark()

    def _mark(self):
        if self._sel:
            i, j = self._sel
            self.marker.setData([j + .5], [i + .5])
        else:
            self.marker.setData([], [])

    def _apply(self):
        i, j = self._sel
        pr = self.p.params
        self.p.set_params(AFTParams(int(self.wss[i]), pr.overlap, int(self.ns[j]),
                                    pr.intensity_thresh, pr.eccentricity_thresh))
        self.status.emit(f"Parameters set: window {self.wss[i]}, neighbourhood {self.ns[j]}. "
                         "Check the vectors before running the analysis.")
        self.apply_params.emit()

    def _export(self):
        if self.df is None:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Export sweep", "aft_sweep.csv", "CSV (*.csv)")
        if f:
            self.df.to_csv(f, index=False)

"""Stage 4 — batch analysis, per-condition plot + stats, per-image maps, export."""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
                             QLabel, QSplitter, QProgressBar, QFileDialog, QCheckBox,
                             QTabWidget, QAbstractItemView, QTextEdit, QLineEdit)
from scipy.stats import mannwhitneyu, kruskal, ttest_ind

from . import imageio
from .core import analyze
from .common import Project, ImagePanel, Worker, GREEN_BTN, HINT

# Validated categorical palette (fixed order) — used only for replicate identity
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, NEUTRAL = "#1f1f1e", "#5f5e58", "#c9c8c0"


def stats_text(groups: dict[str, np.ndarray]) -> str:
    g = {k: v[np.isfinite(v)] for k, v in groups.items() if np.isfinite(v).sum() > 0}
    keys = list(g)
    lines = []
    for k in keys:
        v = g[k]
        lines.append(f"{k}: n={len(v)}, mean={v.mean():.3f} ± {v.std(ddof=1) if len(v) > 1 else 0:.3f} SD, "
                     f"median={np.median(v):.3f}")
    if len(keys) >= 3 and all(len(g[k]) > 1 for k in keys):
        lines.append(f"\nKruskal-Wallis: p = {kruskal(*g.values()).pvalue:.3g}")
    if len(keys) >= 2:
        lines.append("\nPairwise Mann-Whitney U (two-sided, uncorrected) | Welch t-test:")
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = g[keys[i]], g[keys[j]]
                if len(a) and len(b):
                    pm = mannwhitneyu(a, b).pvalue
                    pt = ttest_ind(a, b, equal_var=False).pvalue if len(a) > 1 and len(b) > 1 else np.nan
                    lines.append(f"  {keys[i]} vs {keys[j]}: p_MW = {pm:.3g} | p_t = {pt:.3g}")
    return "\n".join(lines)


class ResultsTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.p = project
        self._worker = None

        top = QHBoxLayout()
        self.btn_run = QPushButton("Run analysis on all images")
        self.btn_run.setStyleSheet(GREEN_BTN)
        self.btn_run.clicked.connect(self._run)
        top.addWidget(self.btn_run)
        self.prog = QProgressBar()
        top.addWidget(self.prog, 1)
        self.params_lbl = QLabel()
        self.params_lbl.setStyleSheet(HINT)
        top.addWidget(self.params_lbl)
        b_exp = QPushButton("Export results…")
        b_exp.clicked.connect(self._export)
        top.addWidget(b_exp)

        # results table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File", "Condition", "Replicate", "Order param.", "Valid windows"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_map(r))

        # plot
        plot_w = QWidget()
        pv = QVBoxLayout(plot_w)
        opt = QHBoxLayout()
        self.kind = QComboBox(); self.kind.addItems(["Bar (mean ± SD) + points", "Box + points",
                                                     "Violin + points", "Points (mean ± SD)"])
        self.level = QComboBox(); self.level.addItems(["One point per image",
                                                       "One point per replicate (mean)"])
        self.level.setToolTip("Replicate means = mean of images within each condition × replicate "
                              "(e.g. each differentiation). Stats use the plotted points.")
        self.color_rep = QCheckBox("Colour by replicate"); self.color_rep.setChecked(True)
        self.order = QLineEdit()
        self.order.setPlaceholderText("Order: Control, Treated, …")
        self.order.setToolTip("Comma-separated order of conditions on the x axis")
        self.order.editingFinished.connect(self._plot)
        for w in (QLabel("Plot:"), self.kind, self.level, self.color_rep, self.order):
            opt.addWidget(w)
        opt.addStretch()
        b_fig = QPushButton("Save figure…")
        b_fig.clicked.connect(self._save_fig)
        opt.addWidget(b_fig)
        pv.addLayout(opt)
        self.fig = Figure(figsize=(6, 4.5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        pv.addWidget(self.canvas, 1)
        self.stats = QTextEdit(); self.stats.setReadOnly(True); self.stats.setMaximumHeight(130)
        self.stats.setStyleSheet("font-family: Menlo, Consolas, monospace; font-size: 11px;")
        pv.addWidget(self.stats)
        for w in (self.kind, self.level):
            w.currentIndexChanged.connect(self._plot)
        self.color_rep.toggled.connect(self._plot)

        self.map = ImagePanel("Select an image in the table")
        tabs = QTabWidget()
        tabs.addTab(plot_w, "Order parameter by condition")
        tabs.addTab(self.map, "Per-image alignment map")
        self.tabs = tabs
        self.table.cellClicked.connect(lambda *_: tabs.setCurrentIndex(1))

        sp = QSplitter()
        sp.addWidget(self.table)
        sp.addWidget(tabs)
        sp.setSizes([420, 800])
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(sp, 1)

        self.p.params_changed.connect(self._refresh)
        self.p.entries_changed.connect(self._refresh)
        self.p.results_changed.connect(self._refresh)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._refresh()

    # ---------- batch
    def _run(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancelled = True
            return
        todo = [e for e in self.p.included() if not self.p.is_fresh(e)]
        if not todo:
            self.status.emit("All results are up to date.")
            return
        params, chan, key = self.p.params, self.p.channel, self.p.key()

        def run(w):
            for k, e in enumerate(todo):
                if w.cancelled:
                    break
                try:
                    r = analyze(imageio.get_plane(e.path, chan), params)
                    w.partial.emit((e, r, key))
                except Exception as ex:
                    w.partial.emit((e, str(ex), key))
                w.progress.emit(k + 1, len(todo))

        self._worker = Worker(run, self)
        self._worker.partial.connect(self._store)
        self._worker.progress.connect(lambda i, n: (self.prog.setMaximum(n), self.prog.setValue(i)))
        self._worker.finished.connect(self._finished)
        self._worker.failed.connect(lambda m: self.status.emit(f"Error: {m}"))
        self.btn_run.setText("Cancel")
        self.prog.setValue(0)
        self._worker.start()

    def _store(self, item):
        e, r, key = item
        if isinstance(r, str):
            self.status.emit(f"{e.name}: {r}")
            return
        e.result, e.result_key = r, key

    def _finished(self):
        self.btn_run.setText("Run analysis on all images")
        self.status.emit("Analysis finished.")
        self._refresh()

    # ---------- data
    def dataframe(self) -> pd.DataFrame:
        rows = []
        for e in self.p.included():
            fresh = self.p.is_fresh(e)
            r = e.result if fresh else None
            rows.append(dict(file=e.name, path=e.path, condition=e.condition or "(none)",
                             replicate=e.replicate, channel=self.p.channel,
                             order_parameter=r.order_parameter if r else np.nan,
                             valid_windows=int(np.isfinite(r.theta).sum()) if r else 0,
                             total_windows=int(r.theta.size) if r else 0,
                             **self.p.params.to_dict()))
        return pd.DataFrame(rows)

    def _plot_data(self, df):
        df = df.dropna(subset=["order_parameter"])
        if self.level.currentIndex() == 1:
            df = (df.assign(replicate=df.replicate.replace("", "(none)"))
                  .groupby(["condition", "replicate"], sort=False).order_parameter.mean()
                  .reset_index())
        return df

    def _refresh(self):
        pr = self.p.params
        self.params_lbl.setText(f"ch {self.p.channel} · win {pr.window_size} · ov {pr.overlap} · "
                                f"nbr {pr.neighborhood_radius} · I>{pr.intensity_thresh:g} · "
                                f"ecc>{pr.eccentricity_thresh:g}")
        if self._worker and self._worker.isRunning():
            return
        df = self.dataframe()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(df))
        for i, row in df.iterrows():
            vals = [row.file, row.condition, row.replicate,
                    "" if np.isnan(row.order_parameter) else f"{row.order_parameter:.4f}",
                    f"{row.valid_windows}/{row.total_windows}" if row.total_windows else "—"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, row.path)
                if c == 3 and v:
                    it.setData(Qt.ItemDataRole.DisplayRole, float(v))
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        n_stale = int(df.order_parameter.isna().sum()) if len(df) else 0
        self.btn_run.setText(f"Run analysis ({n_stale} pending)" if n_stale
                             else "Run analysis on all images")
        self._plot()

    # ---------- plot
    def _plot(self, *_):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        df = self._plot_data(self.dataframe()) if self.p.entries else pd.DataFrame()
        if df.empty:
            ax.text(.5, .5, "Run the analysis to see results", ha="center", va="center",
                    color=INK2, transform=ax.transAxes)
            ax.set_axis_off()
            self.stats.clear()
            self.canvas.draw_idle()
            return
        present = [c for c in self.p.conditions() if c in set(df.condition)]
        wanted = [c.strip() for c in self.order.text().split(",") if c.strip() in present]
        conds = list(dict.fromkeys(wanted + sorted(present)))
        if not self.order.text().strip():
            self.order.setText(", ".join(conds))
        groups = {c: df[df.condition == c].order_parameter.to_numpy(float) for c in conds}
        X = np.arange(len(conds))
        kind = self.kind.currentIndex()
        data = [groups[c] for c in conds]
        if kind == 0:
            ax.bar(X, [d.mean() for d in data], width=0.6, color=NEUTRAL, edgecolor="none", zorder=1)
        elif kind == 1:
            ax.boxplot(data, positions=X, widths=0.5, showfliers=False, patch_artist=True,
                       boxprops=dict(facecolor=NEUTRAL, edgecolor=INK2, linewidth=1),
                       medianprops=dict(color=INK, linewidth=2),
                       whiskerprops=dict(color=INK2), capprops=dict(color=INK2))
        elif kind == 2:
            ok = [d for d in data if len(d) > 1]
            if len(ok) == len(data):
                parts = ax.violinplot(data, positions=X, widths=0.7, showextrema=False)
                for b in parts["bodies"]:
                    b.set_facecolor(NEUTRAL); b.set_edgecolor(INK2); b.set_alpha(1)
        if kind in (0, 3):
            for x, d in zip(X, data):
                sd = d.std(ddof=1) if len(d) > 1 else 0
                ax.errorbar(x, d.mean(), yerr=sd, fmt="_" if kind == 3 else "none",
                            color=INK, capsize=6, lw=1.5, markersize=24, zorder=3)

        # points (deterministic jitter)
        reps = [r for r in dict.fromkeys(df.replicate) if r]
        use_col = self.color_rep.isChecked() and 1 <= len(reps) <= len(SERIES)
        rng = np.random.default_rng(0)
        for x, c in zip(X, conds):
            sub = df[df.condition == c]
            jit = rng.uniform(-0.12, 0.12, len(sub))
            cols = [SERIES[reps.index(r)] if use_col and r in reps else INK for r in sub.replicate]
            ax.scatter(x + jit, sub.order_parameter, s=42, c=cols, edgecolors="white",
                       linewidths=1, zorder=4)
        if use_col:
            from matplotlib.lines import Line2D
            ax.legend(handles=[Line2D([], [], marker="o", ls="", color=SERIES[i], label=r,
                                      markeredgecolor="white") for i, r in enumerate(reps)],
                      title="Replicate", frameon=False, fontsize=9, title_fontsize=9,
                      loc="upper left", bbox_to_anchor=(1.0, 1.0))
        ax.set_xticks(X, conds)
        ax.set_ylabel("Order parameter" if self.level.currentIndex() == 0
                      else "Mean order parameter (per replicate)", color=INK)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(INK2)
        ax.tick_params(colors=INK2)
        ax.yaxis.grid(True, color="#e6e5df", lw=0.8, zorder=0)
        ax.set_axisbelow(True)
        lo = min(0, np.nanmin(df.order_parameter))
        ax.set_ylim(lo, max(1, np.nanmax(df.order_parameter)) if lo < 0 else 1)
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self.stats.setPlainText(stats_text(groups))

    def _save_fig(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save figure", "order_parameter.png",
                                           "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if f:
            self.fig.savefig(f, dpi=300, bbox_inches="tight")

    # ---------- per-image map
    def _show_map(self, row):
        it = self.table.item(row, 0) if row is not None and row >= 0 else None
        if not it:
            return
        path = it.data(Qt.ItemDataRole.UserRole)
        e = next((x for x in self.p.entries if x.path == path), None)
        if e is None:
            return
        try:
            self.map.set_image(imageio.get_plane(path, self.p.channel))
        except Exception as ex:
            self.status.emit(str(ex))
            return
        fresh = self.p.is_fresh(e)
        self.map.set_result(e.result if fresh else None)
        op = f" — order parameter {e.result.order_parameter:.3f}" if fresh else " (not analysed)"
        self.map.set_title(f"{e.name} [{e.condition}]{op}")

    # ---------- export
    def _export(self):
        d = QFileDialog.getExistingDirectory(self, "Export results to folder")
        if not d:
            return
        df = self.dataframe()
        df.to_csv(os.path.join(d, "aft_per_image.csv"), index=False)
        pdf = self._plot_data(df)
        if not pdf.empty:
            (pdf.groupby("condition", sort=False).order_parameter
             .agg(["count", "mean", "std", "median"]).to_csv(os.path.join(d, "aft_per_condition.csv")))
        # per-window long table: every window's orientation and local order
        rows = []
        for e in self.p.included():
            if self.p.is_fresh(e):
                r = e.result
                X, Y = np.meshgrid(r.x, r.y)
                rows.append(pd.DataFrame(dict(
                    file=e.name, condition=e.condition, replicate=e.replicate,
                    x=X.ravel(), y=Y.ravel(), theta_deg=np.degrees(r.theta.ravel()),
                    eccentricity=r.eccentricity.ravel(), local_order=r.local_order.ravel())))
        if rows:
            pd.concat(rows).to_csv(os.path.join(d, "aft_per_window.csv"), index=False)
        with open(os.path.join(d, "aft_parameters.json"), "w") as f:
            json.dump(dict(channel=self.p.channel, **self.p.params.to_dict()), f, indent=1)
        self.fig.savefig(os.path.join(d, "order_parameter.png"), dpi=300, bbox_inches="tight")
        self.fig.savefig(os.path.join(d, "order_parameter.svg"), bbox_inches="tight")
        with open(os.path.join(d, "aft_stats.txt"), "w") as f:
            f.write(self.stats.toPlainText())
        self.status.emit(f"Exported results to {d}")

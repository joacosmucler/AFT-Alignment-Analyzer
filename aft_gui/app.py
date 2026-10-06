"""Main window — guided 4-stage workflow."""

from __future__ import annotations

import os

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                             QLabel, QPushButton, QTabWidget, QStatusBar, QFileDialog,
                             QMessageBox)

from .common import Project
from .tab_data import DataTab
from .tab_tune import TuneTab
from .tab_sweep import SweepTab
from .tab_results import ResultsTab

HEADER_BTN = ("QPushButton { background-color: #34495e; color: #ecf0f1; font-weight: bold;"
              " border: 1px solid #4a6072; border-radius: 4px; padding: 4px 10px; }"
              "QPushButton:hover { background-color: #4a6072; }")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AFT Alignment Analyzer")
        self.resize(1400, 880)
        icon = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.project = Project()
        self.settings = QSettings()
        self._path = None

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)

        header = QFrame()
        header.setStyleSheet("QFrame { background-color: #2c3e50; border-radius: 6px; }"
                             "QLabel { color: white; }")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 6, 12, 6)
        t = QLabel("AFT Alignment Analyzer")
        t.setFont(QFont("", 15, QFont.Weight.Bold))
        h.addWidget(t)
        sub = QLabel("cytoskeletal alignment by Fourier transform")
        sub.setStyleSheet("color: #bdc3c7;")
        h.addWidget(sub)
        h.addStretch()
        for txt, fn, tip in (("Open project", self._open, "Ctrl+O"),
                             ("Save project", self._save, "Ctrl+S")):
            b = QPushButton(txt)
            b.setStyleSheet(HEADER_BTN)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
        lay.addWidget(header)

        self.tabs = QTabWidget()
        self.data = DataTab(self.project)
        self.tune = TuneTab(self.project)
        self.sweep = SweepTab(self.project)
        self.results = ResultsTab(self.project)
        for w, name in ((self.data, "1 — Images"), (self.tune, "2 — Tune parameters"),
                        (self.sweep, "3 — Parameter sweep (optional)"),
                        (self.results, "4 — Results")):
            self.tabs.addTab(w, name)
            w.status.connect(self._msg)
        self.data.proceed.connect(lambda: self.tabs.setCurrentWidget(self.tune))
        self.tune.proceed.connect(lambda: self.tabs.setCurrentWidget(self.results))
        self.sweep.apply_params.connect(lambda: self.tabs.setCurrentWidget(self.tune))
        lay.addWidget(self.tabs, 1)

        self.setStatusBar(QStatusBar())
        self._msg("Add images (CZI / TIFF) to start. Defaults = AFT paper parameters.")
        self.project.entries_changed.connect(self._update_tabs)
        self._update_tabs()
        QShortcut(QKeySequence.StandardKey.Save, self, self._save)
        QShortcut(QKeySequence.StandardKey.Open, self, self._open)

    def _msg(self, m):
        self.statusBar().showMessage(m, 8000)

    def _update_tabs(self):
        has = bool(self.project.entries)
        for i in (1, 2, 3):
            self.tabs.setTabEnabled(i, has)

    def _save(self):
        start = self._path or self.settings.value("last_dir", "")
        f, _ = QFileDialog.getSaveFileName(self, "Save project", start, "AFT project (*.aft.json)")
        if f:
            if not f.endswith(".json"):
                f += ".aft.json"
            self.project.save(f)
            self._path = f
            self.settings.setValue("last_dir", f)
            self._msg(f"Saved {f}")

    def _open(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open project", self.settings.value("last_dir", ""),
                                           "AFT project (*.json)")
        if not f:
            return
        try:
            self.project.load(f)
        except Exception as ex:
            QMessageBox.critical(self, "Open project", str(ex))
            return
        self._path = f
        self.settings.setValue("last_dir", f)
        self._msg(f"Loaded {f} — run the analysis to recompute results.")

"""Stage 1 — load images, choose channel, assign conditions."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QFileDialog, QComboBox, QLabel, QLineEdit,
                             QSplitter, QGroupBox, QAbstractItemView, QMessageBox)

from . import imageio
from .common import Project, ImagePanel, GREEN_BTN, HINT

COLS = ["Use", "File", "Condition", "Replicate", "Folder"]


class DataTab(QWidget):
    proceed = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.p = project
        self._building = False
        self.setAcceptDrops(True)

        # ---- left: file table + actions
        left = QWidget()
        lv = QVBoxLayout(left)
        row = QHBoxLayout()
        for txt, fn in (("Add images…", self._add_files), ("Add folder…", self._add_folder),
                        ("Remove selected", self._remove), ("Clear", self._clear)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lv.addLayout(row)
        hint = QLabel("Drag & drop .czi / .tif files or folders here. "
                      "Edit Condition/Replicate cells directly, or select rows and assign in bulk.")
        hint.setStyleSheet(HINT)
        hint.setWordWrap(True)
        lv.addWidget(hint)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(lambda r, *_: self._preview(r))
        lv.addWidget(self.table, 1)

        grp = QGroupBox("Assign to selected rows")
        g = QHBoxLayout(grp)
        self.cond_edit = QLineEdit()
        self.cond_edit.setPlaceholderText("Condition, e.g. Control")
        b_c = QPushButton("Set condition")
        b_c.clicked.connect(lambda: self._assign(2, self.cond_edit.text()))
        self.rep_edit = QLineEdit()
        self.rep_edit.setPlaceholderText("Replicate, e.g. IDM24")
        b_r = QPushButton("Set replicate")
        b_r.clicked.connect(lambda: self._assign(3, self.rep_edit.text()))
        b_f = QPushButton("All: condition = folder name")
        b_f.setToolTip("Sets every image's condition to its parent folder name "
                       "(you can still edit individual cells afterwards)")
        b_f.clicked.connect(self._cond_from_folder)
        for w in (self.cond_edit, b_c, self.rep_edit, b_r, b_f):
            g.addWidget(w)
        lv.addWidget(grp)

        # ---- right: channel + preview
        right = QWidget()
        rv = QVBoxLayout(right)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("<b>Channel to quantify:</b>"))
        self.chan = QComboBox()
        self.chan.setMinimumWidth(160)
        self.chan.currentTextChanged.connect(self._channel_changed)
        crow.addWidget(self.chan)
        crow.addStretch()
        rv.addLayout(crow)
        self.info = QLabel("")
        self.info.setStyleSheet(HINT)
        rv.addWidget(self.info)
        self.preview = ImagePanel("Preview", show_controls=False)
        rv.addWidget(self.preview, 1)
        self.summary = QLabel("")
        rv.addWidget(self.summary)
        self.btn_next = QPushButton("Next: tune parameters  →")
        self.btn_next.setStyleSheet(GREEN_BTN)
        self.btn_next.clicked.connect(self.proceed.emit)
        rv.addWidget(self.btn_next)

        sp = QSplitter()
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setSizes([650, 550])
        QVBoxLayout(self).addWidget(sp)

        self.p.entries_changed.connect(self.rebuild)
        self.rebuild()

    # ---------------- table
    def rebuild(self):
        self._building = True
        self.table.setRowCount(len(self.p.entries))
        for i, e in enumerate(self.p.entries):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                         | Qt.ItemFlag.ItemIsSelectable)
            use.setCheckState(Qt.CheckState.Checked if e.include else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, use)
            for c, val, edit in ((1, e.name, False), (2, e.condition, True),
                                 (3, e.replicate, True),
                                 (4, os.path.basename(os.path.dirname(e.path)), False)):
                it = QTableWidgetItem(val)
                if not edit:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 1:
                    it.setToolTip(e.path)
                self.table.setItem(i, c, it)
        self._building = False
        self._refresh_channels()
        self._update_summary()
        if self.p.entries and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def _item_changed(self, it):
        if self._building:
            return
        e = self.p.entries[it.row()]
        if it.column() == 0:
            e.include = it.checkState() == Qt.CheckState.Checked
        elif it.column() == 2:
            e.condition = it.text().strip()
        elif it.column() == 3:
            e.replicate = it.text().strip()
        self._update_summary()
        self.p.results_changed.emit()

    def _assign(self, col, text):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.status.emit("Select rows first.")
            return
        for r in rows:
            self.table.item(r, col).setText(text.strip())

    def _cond_from_folder(self):
        for r in range(len(self.p.entries)):
            self.table.item(r, 2).setText(
                os.path.basename(os.path.dirname(self.p.entries[r].path)))

    def _update_summary(self):
        inc = self.p.included()
        conds = {}
        for e in inc:
            conds[e.condition or "(none)"] = conds.get(e.condition or "(none)", 0) + 1
        txt = ", ".join(f"{k}: {v}" for k, v in conds.items())
        self.summary.setText(f"<b>{len(inc)}</b> images in use — {txt}" if inc else
                             "No images loaded.")
        self.btn_next.setEnabled(bool(inc))

    # ---------------- files
    def _add(self, paths):
        paths = [p for p in paths if p.lower().endswith(imageio.IMAGE_EXT)]
        n = self.p.add_paths(paths)
        self.status.emit(f"Added {n} image(s).")

    def _add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Add images", "",
                                             "Images (*.czi *.tif *.tiff)")
        self._add(fs)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder (recursive)")
        if d:
            self._add(imageio.find_images(d))

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.p.entries[r]
        self.p.entries_changed.emit()

    def _clear(self):
        if self.p.entries and QMessageBox.question(self, "Clear", "Remove all images?") \
                == QMessageBox.StandardButton.Yes:
            self.p.entries.clear()
            self.p.entries_changed.emit()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        paths = []
        for u in ev.mimeData().urls():
            f = u.toLocalFile()
            paths += imageio.find_images(f) if os.path.isdir(f) else [f]
        self._add(paths)

    # ---------------- channel / preview
    def _refresh_channels(self):
        if not self.p.entries:
            return
        try:
            names = imageio.channel_names(self.p.entries[0].path)
        except Exception as ex:
            self.status.emit(f"Could not read {self.p.entries[0].name}: {ex}")
            return
        self.chan.blockSignals(True)
        self.chan.clear()
        self.chan.addItems(names)
        if self.p.channel in names:
            self.chan.setCurrentText(self.p.channel)
        else:
            self.p.channel = names[0]
        self.chan.blockSignals(False)

    def _channel_changed(self, name):
        if name and name != self.p.channel:
            self.p.channel = name
            self.p.params_changed.emit()  # invalidates results
            self._preview(self.table.currentRow())

    def _preview(self, row):
        if row is None or not 0 <= row < len(self.p.entries):
            return
        e = self.p.entries[row]
        try:
            planes, names = imageio.read_image(e.path)
            im = imageio.get_plane(e.path, self.p.channel)
        except Exception as ex:
            self.info.setText(f"Error reading file: {ex}")
            return
        self.info.setText(f"{e.name} — {im.shape[1]}×{im.shape[0]} px, {im.dtype}, "
                          f"channels: {', '.join(names)}")
        self.preview.set_title(f"{e.name} [{self.p.channel}]")
        self.preview.set_image(im)

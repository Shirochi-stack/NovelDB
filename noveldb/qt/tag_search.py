"""npia Tag search dialog: find novel IDs by tag, scrape all novels or read the Top 100, with a live log."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import paths
from noveldb.core.catalog.cart import CartItem
from noveldb.core.npia import tags as T
from noveldb.core.npia.ids import novel_url
from noveldb.core.settings.model import NPIA
from noveldb.qt.context import AppContext
from noveldb.qt.widgets import Chip, flow, hint, section

# Above this many results, adding to the link cart asks first (as on mobile).
CART_CONFIRM_ABOVE = 200


class _Signals(QObject):
    log = Signal(str)
    done = Signal(object, str)  # result (list of ids or rankings dict) or None, error text


class TagSearchDialog(QDialog):
    addToDownload = Signal(list)

    def __init__(self, context: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("npia tag search")
        self.resize(900, 760)
        self.setModal(False)
        self.signals = _Signals()
        self.signals.log.connect(self._append_log, Qt.QueuedConnection)
        self.signals.done.connect(self._finished, Qt.QueuedConnection)
        self.search: Optional[T.TagSearch] = None
        self.result: list = []
        self.rankings: dict = {}
        self.kind = ""
        npia = context.settings.npia

        self.chips = {}
        for tag, _label in T.COMMON_TAGS:
            chip = Chip(T.display_name(tag), checked=tag in npia.tag_selected_tags)
            chip.setToolTip(tag if tag != T.SITE_TAG else "npia")
            self.chips[tag] = chip
        chips_box = flow(*self.chips.values(), spacing=6)
        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setWidget(chips_box)
        chips_scroll.setMinimumHeight(150)
        self.custom = QLineEdit(npia.tag_custom_tags)
        self.custom.setPlaceholderText("Custom tags, separated by commas")

        self.groups = [list(g) for g in npia.tag_query_groups]
        self.group_list = QListWidget()
        self.group_list.setMaximumHeight(110)
        add_group = QPushButton("Add selected as group")
        add_group.clicked.connect(self._add_group)
        remove_group = QPushButton("Remove group")
        remove_group.clicked.connect(self._remove_group)
        clear_groups = QPushButton("Clear all")
        clear_groups.clicked.connect(self._clear_groups)

        self.age = QComboBox()
        for key, text in T.AGE_FILTERS.items():
            self.age.addItem(text, key)
        self.age.setCurrentIndex(max(0, self.age.findData(npia.tag_age_filter)))
        self.age.currentIndexChanged.connect(self._age_changed)
        self.scrape_all = QCheckBox("Scrape all novels")
        self.scrape_all.setChecked(npia.tag_scrape_all)
        self.scrape_all.toggled.connect(self._scrape_all_changed)
        self.queries = QSpinBox()
        self.queries.setRange(1, T.DEFAULT_SEARCH_QUERY_COUNT)
        self.queries.setValue(max(1, min(npia.scrape_max_queries, T.DEFAULT_SEARCH_QUERY_COUNT)))
        self.queries_hint = hint(f"More queries find more novels but take longer; {T.DEFAULT_SEARCH_QUERY_COUNT} "
                                 "covers every current tag plus the sweep.")

        self.go = QPushButton("Retrieve novel IDs")
        self.go.setProperty("primary", True)
        self.go.clicked.connect(self._retrieve)
        self.top = QPushButton("Retrieve Top 100")
        self.top.clicked.connect(self._retrieve_top100)
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self._stop)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.save = QPushButton("Save list...")
        self.save.clicked.connect(self._save)
        self.to_download = QPushButton("Add to Download")
        self.to_download.clicked.connect(self._to_download)
        self.to_cart = QPushButton("\U0001F517 Add to link cart")
        self.to_cart.clicked.connect(self._to_cart)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        copy_log = QPushButton("Copy log")
        copy_log.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log.toPlainText()))
        clear_log = QPushButton("Clear log")
        clear_log.clicked.connect(self.log.clear)

        top = QWidget()
        layout = QVBoxLayout(top)
        layout.addWidget(section("Tags"))
        layout.addWidget(chips_scroll, 1)
        layout.addWidget(self.custom)
        layout.addWidget(section("Query builder"))
        layout.addWidget(hint("Groups are AND'd, tags within a group are OR'd. Without groups, every selected or "
                              "custom tag is its own group."))
        layout.addWidget(self.group_list)
        layout.addWidget(flow(add_group, remove_group, clear_groups))
        options = QHBoxLayout()
        options.addWidget(QLabel("Age"))
        options.addWidget(self.age)
        options.addSpacing(16)
        options.addWidget(self.scrape_all)
        options.addWidget(QLabel("Search queries"))
        options.addWidget(self.queries)
        options.addStretch(1)
        layout.addLayout(options)
        layout.addWidget(self.queries_hint)
        layout.addWidget(flow(self.go, self.top, self.stop))
        layout.addWidget(self.status)
        layout.addWidget(flow(self.save, self.to_download, self.to_cart))

        bottom = QWidget()
        log_layout = QVBoxLayout(bottom)
        log_layout.setContentsMargins(9, 0, 9, 9)
        log_head = QHBoxLayout()
        log_head.addWidget(section("Log"))
        log_head.addStretch(1)
        log_head.addWidget(copy_log)
        log_head.addWidget(clear_log)
        log_layout.addLayout(log_head)
        log_layout.addWidget(self.log, 1)
        log_layout.addWidget(hint("The search keeps running if you close this window. Stop keeps what was found so far."))

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._show_groups()
        self._age_changed()
        self._scrape_all_changed()
        self._show_results()

    # -- state -------------------------------------------------------------------------------

    def _selected(self) -> list:
        return [tag for tag, chip in self.chips.items() if chip.isChecked()]

    def _remember(self) -> None:
        selected, custom, groups = tuple(self._selected()), self.custom.text(), tuple(tuple(g) for g in self.groups)
        age, scrape_all, queries = self.age.currentData(), self.scrape_all.isChecked(), self.queries.value()
        self.context.update_settings(lambda s: replace(s, npia=replace(
            s.npia, tag_selected_tags=selected, tag_custom_tags=custom, tag_query_groups=groups,
            tag_age_filter=age, tag_scrape_all=scrape_all, scrape_max_queries=queries)))

    def _show_groups(self) -> None:
        self.group_list.clear()
        if not self.groups:
            self.group_list.addItem("No groups yet. Select tags and press Add selected as group.")
            self.group_list.item(0).setFlags(Qt.NoItemFlags)
            return
        for index, group in enumerate(self.groups, 1):
            self.group_list.addItem(f"{index}. " + " OR ".join(T.display_name(t) for t in group))

    def _add_group(self) -> None:
        group = self._selected() + [t.strip() for t in self.custom.text().split(",") if t.strip()]
        if not group:
            return
        self.groups.append(list(dict.fromkeys(group)))
        for chip in self.chips.values():
            chip.setChecked(False)
        self.custom.clear()
        self._show_groups()
        self._remember()

    def _remove_group(self) -> None:
        row = self.group_list.currentRow()
        if self.groups and 0 <= row < len(self.groups):
            del self.groups[row]
            self._show_groups()
            self._remember()

    def _clear_groups(self) -> None:
        self.groups = []
        self._show_groups()
        self._remember()

    def _age_changed(self) -> None:
        adult = self.chips.get(T.ADULT_TAG)
        if adult is not None:
            allowed = self.age.currentData() == ""
            if not allowed:
                adult.setChecked(False)
            adult.setEnabled(allowed)

    def _scrape_all_changed(self) -> None:
        on = self.scrape_all.isChecked()
        for chip in self.chips.values():
            chip.setEnabled(not on)
        self.custom.setEnabled(not on)
        self.queries.setEnabled(on)
        self.queries_hint.setEnabled(on)
        if not on:
            self._age_changed()

    # -- running -----------------------------------------------------------------------------

    def _running(self, on: bool) -> None:
        self.go.setEnabled(not on)
        self.top.setEnabled(not on)
        self.stop.setEnabled(on)
        if on:
            for button in (self.save, self.to_download, self.to_cart):
                button.setEnabled(False)

    def _start(self, kind: str, work) -> None:
        self._remember()
        self.kind = kind
        self.result, self.rankings = [], {}
        self.search = T.TagSearch(self.context.auth, self.signals.log.emit)
        self._running(True)

        def run():
            try:
                self.signals.done.emit(work(self.search), "")
            except Exception as exc:  # report it; the old window lost the message (`e` was gone)
                self.signals.done.emit(None, str(exc) or type(exc).__name__)

        threading.Thread(target=run, name="npia-tag-search", daemon=True).start()

    def _retrieve(self) -> None:
        run = self.context.settings.effective(NPIA)
        age = self.age.currentData()
        delay, threads = run.interval_range[0], run.threads
        if self.scrape_all.isChecked():
            queries = self.queries.value()
            self.status.setText(f"Scraping all novels ({queries} queries)...")
            self._start("ids", lambda s: s.scrape_all(age, delay, threads, queries))
            return
        groups = T.groups_for(self.groups, self._selected(), self.custom.text())
        if not groups:
            QMessageBox.information(self, "No tags", "Select tags, type custom tags, or add a group first.")
            return
        self._label = T.tag_label(groups)
        self.status.setText("Searching...")
        self._start("ids", lambda s: s.search(groups, age, delay, threads))

    def _retrieve_top100(self) -> None:
        age = self.age.currentData()
        self.status.setText("Fetching the Top 100 rankings...")
        self._start("top100", lambda s: s.top100(age))

    def _stop(self) -> None:
        if self.search is not None:
            self.search.stop()
            self.stop.setEnabled(False)
            self.status.setText("Stopping (keeps what was found)...")

    def _finished(self, result, error: str) -> None:
        stopped = self.search is not None and self.search.stopped
        self._running(False)
        if error:
            self.status.setText(f"Error: {error}")
            self._append_log(f"Tag search failed: {error}")
            return
        suffix = " (stopped)" if stopped else ""
        if self.kind == "top100":
            self.rankings = result or {}
            unique, summary = T.top100_summary(self.rankings)
            self.result = sorted(unique, key=lambda value: int(value) if str(value).isdigit() else 0)
            self.status.setText(summary + suffix)
        else:
            self.result = list(result or [])
            self.status.setText(f"Found {len(self.result)} novel(s).{suffix}")
        self._show_results()

    def _show_results(self) -> None:
        has = bool(self.result)
        for button in (self.save, self.to_download, self.to_cart):
            button.setEnabled(has)

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(str(text))

    # -- results -----------------------------------------------------------------------------

    def _start_dir(self) -> str:
        return self.context.settings.output.output_dir or str(paths.default_output_dir())

    def _save(self) -> None:
        if self.kind == "top100" and self.rankings:
            choice = QMessageBox.question(
                self, "Save rankings", "Save every ranking in one file (with section headers)?\n\n"
                "Yes: one file   No: one file per ranking", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if choice == QMessageBox.Cancel:
                return
            if choice == QMessageBox.Yes:
                target, _ = QFileDialog.getSaveFileName(self, "Save Top 100 rankings",
                                                        os.path.join(self._start_dir(), "top100_rankings.txt"),
                                                        "Text files (*.txt)")
                written = T.write_top100(self.rankings, path=target) if target else []
            else:
                folder = QFileDialog.getExistingDirectory(self, "Folder for the ranking files", self._start_dir())
                written = T.write_top100(self.rankings, folder=folder) if folder else []
            if written:
                self._append_log(f"Saved {len(written)} file(s): {', '.join(written)}")
            return
        label = getattr(self, "_label", "all_novels") if not self.scrape_all.isChecked() else "all_novels"
        target, _ = QFileDialog.getSaveFileName(self, "Save novel ID list",
                                                os.path.join(self._start_dir(), f"tag_{label}.txt"),
                                                "Text files (*.txt)")
        if target:
            count = T.write_ids(target, self.result)
            self._append_log(f"Saved {count} novel IDs to: {target}")

    def _to_download(self) -> None:
        self.addToDownload.emit([str(value) for value in self.result])

    def _to_cart(self) -> None:
        if len(self.result) > CART_CONFIRM_ABOVE:
            answer = QMessageBox.question(self, "Add to link cart", f"Add {len(self.result)} novels to the link cart?")
            if answer != QMessageBox.Yes:
                return
        added = self.context.cart.add_many(CartItem(novel_url(value), "", NPIA) for value in self.result)
        self.context.hub.message.emit(f"Added {added} novel(s) to the link cart.")
        self._append_log(f"Added {added} novel(s) to the link cart.")

    def closeEvent(self, event):
        self._remember()
        super().closeEvent(event)

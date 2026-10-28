"""npia Tag search dialog: build a query by dragging tags into groups, then find novel IDs, scrape
all novels or read the Top 100, with a live log.

Two columns: the tags (with a filter, custom tags and the search options) on the left; the query,
the actions, the results and the log on the right. Drop a tag on a group to OR it in, or on the
"new group" area to AND it as a group of its own; drag pills between groups, or back onto the tags
(or press their x) to remove them. Clicking a tag adds it as a group of its own.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from typing import Callable, Optional

from PySide6.QtCore import QMimeData, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import paths
from noveldb.core.catalog.cart import CartItem
from noveldb.core.npia import tags as T
from noveldb.core.npia.ids import novel_url
from noveldb.core.settings.model import NPIA
from noveldb.qt.context import AppContext
from noveldb.qt.scale import bind_size
from noveldb.qt.widgets import FlowLayout, hint, section

# Above this many results, adding to the link cart asks first (as on mobile).
CART_CONFIRM_ABOVE = 200
TAG_MIME = "application/x-noveldb-tag"


class _Signals(QObject):
    log = Signal(str)
    done = Signal(object, str)  # result (list of ids or rankings dict) or None, error text


def _tag_payload(tag: str, group: int = -1) -> QMimeData:
    data = QMimeData()
    data.setData(TAG_MIME, json.dumps({"tag": tag, "group": group}).encode("utf-8"))
    data.setText(tag)
    return data


def _read_payload(mime) -> Optional[dict]:
    if not mime.hasFormat(TAG_MIME):
        return None
    try:
        return json.loads(bytes(mime.data(TAG_MIME)).decode("utf-8"))
    except ValueError:
        return None


class TagChip(QPushButton):
    """A tag you can click (adds it as its own group) or drag into a group."""

    def __init__(self, tag: str, text: str, group: int = -1, on_click: Optional[Callable] = None) -> None:
        super().__init__(text)
        self.tag, self.group = tag, group
        self.setProperty("chip", True)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag into a group, or click to add it as a group of its own." if group < 0 else
                        "Drag to another group, or back onto the tags to remove it.")
        self._press: Optional[QPoint] = None
        if on_click is not None:
            self.clicked.connect(on_click)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        self.setDown(False)
        drag = QDrag(self)
        drag.setMimeData(_tag_payload(self.tag, self.group))
        drag.setPixmap(self.grab())
        drag.setHotSpot(QPoint(self.width() // 2, self.height() // 2))
        drag.exec(Qt.MoveAction | Qt.CopyAction)


class DropArea(QFrame):
    """Accepts dragged tags and hands them to `on_drop(tag, from_group)`."""

    def __init__(self, on_drop: Callable[[str, int], None], name: str = "dropArea") -> None:
        super().__init__()
        self.on_drop = on_drop
        self.setObjectName(name)
        self.setAcceptDrops(True)
        self.setProperty("hover", False)

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):
        if _read_payload(event.mimeData()) is not None:
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, event):
        self._set_hover(False)

    def dropEvent(self, event):
        self._set_hover(False)
        payload = _read_payload(event.mimeData())
        if payload is not None:
            event.acceptProposedAction()
            self.on_drop(str(payload.get("tag", "")), int(payload.get("group", -1)))


class TagSearchDialog(QDialog):
    addToDownload = Signal(list)

    def __init__(self, context: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("npia tag search")
        self.resize(1280, 820)
        self.setModal(False)
        self.signals = _Signals()
        self.signals.log.connect(self._append_log, Qt.QueuedConnection)
        self.signals.done.connect(self._finished, Qt.QueuedConnection)
        self.search: Optional[T.TagSearch] = None
        self.result: list = []
        self.rankings: dict = {}
        self.kind = ""
        npia = context.settings.npia
        self.groups: list[list[str]] = [list(g) for g in npia.tag_query_groups]
        self.custom_tags: list[str] = [t.strip() for t in npia.tag_custom_tags.split(",") if t.strip()]

        # -- left column: tags and options ------------------------------------------------------
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter tags")
        self.filter.textChanged.connect(self._show_palette)
        self.palette_area = DropArea(self._drop_on_palette, "palette")
        self.palette_layout = FlowLayout(self.palette_area, spacing=6)
        self.palette_layout.setContentsMargins(6, 6, 6, 6)
        palette_scroll = QScrollArea()
        palette_scroll.setWidgetResizable(True)
        palette_scroll.setFrameShape(QScrollArea.NoFrame)
        palette_scroll.setWidget(self.palette_area)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Add a custom tag and press Enter")
        self.custom.returnPressed.connect(self._add_custom)
        self.age = QComboBox()
        for key, text in T.AGE_FILTERS.items():
            self.age.addItem(text, key)
        self.age.setCurrentIndex(max(0, self.age.findData(npia.tag_age_filter)))
        self.age.currentIndexChanged.connect(self._age_changed)
        self.scrape_all = QCheckBox("Scrape all novels (ignores the query)")
        self.scrape_all.setChecked(npia.tag_scrape_all)
        self.scrape_all.toggled.connect(self._scrape_all_changed)
        self.queries = QSpinBox()
        self.queries.setRange(1, T.DEFAULT_SEARCH_QUERY_COUNT)
        self.queries.setValue(max(1, min(npia.scrape_max_queries, T.DEFAULT_SEARCH_QUERY_COUNT)))
        self.queries_hint = hint(f"More queries find more novels but take longer; {T.DEFAULT_SEARCH_QUERY_COUNT} "
                                 "covers every current tag plus the sweep.")
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 8, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(section("Tags"))
        left_layout.addWidget(self.filter)
        left_layout.addWidget(palette_scroll, 1)
        left_layout.addWidget(self.custom)
        left_layout.addWidget(section("Options"))
        options = QHBoxLayout()
        options.addWidget(QLabel("Age"))
        options.addWidget(self.age)
        options.addStretch(1)
        left_layout.addLayout(options)
        left_layout.addWidget(self.scrape_all)
        queries_row = QHBoxLayout()
        queries_row.addWidget(QLabel("Search queries"))
        queries_row.addWidget(self.queries)
        queries_row.addStretch(1)
        left_layout.addLayout(queries_row)
        left_layout.addWidget(self.queries_hint)

        # -- right column: query, actions, results, log --------------------------------------------
        self.groups_box = QWidget()
        self.groups_layout = QVBoxLayout(self.groups_box)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(8)
        groups_scroll = QScrollArea()
        groups_scroll.setWidgetResizable(True)
        groups_scroll.setFrameShape(QScrollArea.NoFrame)
        groups_scroll.setWidget(self.groups_box)
        clear_groups = QPushButton("Clear query")
        clear_groups.clicked.connect(self._clear_groups)
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

        query = QWidget()
        query_layout = QVBoxLayout(query)
        query_layout.setContentsMargins(8, 12, 16, 4)
        query_layout.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(section("Query"))
        head.addStretch(1)
        head.addWidget(clear_groups)
        query_layout.addLayout(head)
        query_layout.addWidget(hint("Tags in a group are OR'd; groups are AND'd."))
        query_layout.addWidget(groups_scroll, 1)
        actions = QHBoxLayout()
        for button in (self.go, self.top, self.stop):
            actions.addWidget(button)
        actions.addStretch(1)
        query_layout.addLayout(actions)
        query_layout.addWidget(self.status)
        results = QHBoxLayout()
        for button in (self.save, self.to_download, self.to_cart):
            results.addWidget(button)
        results.addStretch(1)
        query_layout.addLayout(results)

        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(8, 4, 16, 12)
        log_head = QHBoxLayout()
        log_head.addWidget(section("Log"))
        log_head.addStretch(1)
        log_head.addWidget(copy_log)
        log_head.addWidget(clear_log)
        log_layout.addLayout(log_head)
        log_layout.addWidget(self.log, 1)
        log_layout.addWidget(hint("The search keeps running if you close this window. Stop keeps what was found so far."))

        right = QSplitter(Qt.Vertical)
        right.setHandleWidth(12)
        right.addWidget(query)
        right.addWidget(log_box)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        right.setChildrenCollapsible(False)

        columns = QSplitter(Qt.Horizontal)
        columns.setHandleWidth(12)
        columns.addWidget(left)
        columns.addWidget(right)
        columns.setStretchFactor(0, 1)
        columns.setStretchFactor(1, 1)
        columns.setChildrenCollapsible(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(columns)

        self.chips: dict[str, TagChip] = {}
        self._show_palette()
        self._show_groups()
        self._scrape_all_changed()
        self._show_results()

    # -- tags and groups --------------------------------------------------------------------------

    def _all_tags(self) -> list[str]:
        return [tag for tag, _label in T.COMMON_TAGS] + [t for t in self.custom_tags if t not in T.TAG_LABELS]

    def _show_palette(self) -> None:
        while self.palette_layout.count():
            item = self.palette_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.chips = {}
        needle = self.filter.text().strip().lower()
        adult_blocked = self.age.currentData() != ""
        for tag in self._all_tags():
            text = T.display_name(tag)
            if needle and needle not in text.lower() and needle not in tag.lower():
                continue
            chip = TagChip(tag, text, on_click=lambda _=False, t=tag: self._add_group([t]))
            chip.setEnabled(not (adult_blocked and tag == T.ADULT_TAG) and not self.scrape_all.isChecked())
            self.palette_layout.addWidget(chip)
            self.chips[tag] = chip
        self.palette_area.updateGeometry()

    def _show_groups(self) -> None:
        while self.groups_layout.count():
            item = self.groups_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        enabled = not self.scrape_all.isChecked()
        for index, group in enumerate(self.groups):
            box = DropArea(lambda tag, src, i=index: self._drop_on_group(tag, src, i), "groupBox")
            row = QHBoxLayout(box)
            row.setContentsMargins(10, 8, 6, 8)
            label = QLabel("AND" if index else "")
            label.setProperty("hint", True)
            bind_size(label, fixed_width=34)
            row.addWidget(label)
            pills = QWidget()
            flow_layout = FlowLayout(pills, spacing=6)
            for tag in group:
                pill = QWidget()
                pill_row = QHBoxLayout(pill)
                pill_row.setContentsMargins(0, 0, 0, 0)
                pill_row.setSpacing(0)
                chip = TagChip(tag, T.display_name(tag), group=index)
                chip.setCheckable(True)
                chip.setChecked(True)
                remove = QToolButton()
                remove.setText("×")
                remove.setToolTip("Remove from this group")
                remove.clicked.connect(lambda _=False, t=tag, i=index: self._remove_from_group(t, i))
                pill_row.addWidget(chip)
                pill_row.addWidget(remove)
                flow_layout.addWidget(pill)
            row.addWidget(pills, 1)
            box.setEnabled(enabled)
            self.groups_layout.addWidget(box)
        new_group = DropArea(lambda tag, src: self._drop_on_new_group(tag, src), "newGroup")
        new_row = QHBoxLayout(new_group)
        new_row.setContentsMargins(10, 14, 10, 14)
        new_label = QLabel("Drop a tag here to start a new group" if self.groups else
                           "Drag tags here to build the query (or click a tag)")
        new_label.setAlignment(Qt.AlignCenter)
        new_label.setProperty("hint", True)
        new_row.addWidget(new_label)
        new_group.setEnabled(enabled)
        self.groups_layout.addWidget(new_group)
        self.groups_layout.addStretch(1)

    def _add_group(self, tags: list) -> None:
        tags = [t for t in dict.fromkeys(tags) if t]
        if tags:
            self.groups.append(tags)
            self._changed()

    def _drop_on_group(self, tag: str, source: int, index: int) -> None:
        if not tag or source == index or not (0 <= index < len(self.groups)):
            return
        if tag not in self.groups[index]:
            self.groups[index].append(tag)
        self._take_from(source, tag, keep_index=index)
        self._changed()

    def _drop_on_new_group(self, tag: str, source: int) -> None:
        if not tag:
            return
        if 0 <= source < len(self.groups) and self.groups[source] == [tag]:
            return  # moving a lone tag to a new group changes nothing
        self.groups.append([tag])
        self._take_from(source, tag, keep_index=len(self.groups) - 1)
        self._changed()

    def _drop_on_palette(self, tag: str, source: int) -> None:
        if 0 <= source < len(self.groups):
            self._remove_from_group(tag, source)

    def _remove_from_group(self, tag: str, index: int) -> None:
        if 0 <= index < len(self.groups) and tag in self.groups[index]:
            self.groups[index].remove(tag)
            self.groups = [g for g in self.groups if g]
            self._changed()

    def _take_from(self, source: int, tag: str, keep_index: int) -> None:
        """A drag from one group to another moves the tag (empty groups disappear)."""
        if 0 <= source < len(self.groups) and source != keep_index and tag in self.groups[source]:
            self.groups[source].remove(tag)
        self.groups = [g for g in self.groups if g]

    def _clear_groups(self) -> None:
        self.groups = []
        self._changed()

    def _add_custom(self) -> None:
        tags = [t.strip() for t in self.custom.text().split(",") if t.strip()]
        for tag in tags:
            if tag not in self.custom_tags and tag not in T.TAG_LABELS:
                self.custom_tags.append(tag)
        self.custom.clear()
        if tags:
            self._show_palette()
            self._remember()

    def _changed(self) -> None:
        self._show_groups()
        self._remember()

    def _remember(self) -> None:
        groups, custom = tuple(tuple(g) for g in self.groups), ", ".join(self.custom_tags)
        age, scrape_all, queries = self.age.currentData(), self.scrape_all.isChecked(), self.queries.value()
        self.context.update_settings(lambda s: replace(s, npia=replace(
            s.npia, tag_selected_tags=(), tag_custom_tags=custom, tag_query_groups=groups,
            tag_age_filter=age, tag_scrape_all=scrape_all, scrape_max_queries=queries)))

    def _age_changed(self) -> None:
        if self.age.currentData() != "":
            # The adult tag means nothing with an age filter on: take it out of the query too.
            self.groups = [[t for t in g if t != T.ADULT_TAG] for g in self.groups]
            self.groups = [g for g in self.groups if g]
            self._show_groups()
        self._show_palette()
        self._remember()

    def _scrape_all_changed(self) -> None:
        on = self.scrape_all.isChecked()
        self.queries.setEnabled(on)
        self.queries_hint.setEnabled(on)
        self.filter.setEnabled(not on)
        self.custom.setEnabled(not on)
        self._show_palette()
        self._show_groups()

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
        groups = [list(g) for g in self.groups if g]
        if not groups:
            QMessageBox.information(self, "No tags", "Drag tags into the query (or click a tag) first.")
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

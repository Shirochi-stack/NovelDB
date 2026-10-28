"""Download page: what to download and how, like the mobile Add tab."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import paths
from noveldb.core.settings.model import NPIA, OUTPUT_FORMATS
from noveldb.core.sources import BROWSER, detect_source, engine_for, split_inputs
from noveldb.qt.context import AppContext
from noveldb.qt.widgets import Chip, dspin, field_group, flow, hint, section, set_quietly, spin

NL = chr(10)
FORMAT_LABELS = {"epub": "EPUB", "txt": "TXT", "pdf": "PDF", "cbz": "CBZ"}
NPIA_FORMATS = ("epub", "txt", "pdf")


class InputBox(QPlainTextEdit):
    """URL/ID box that also accepts dropped links and text files."""

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setPlaceholderText(
            "Paste novel links, npia IDs or \"Title,ID\" lines - one per line.\n"
            "Lines starting with # are skipped."
        )
        self.setTabChangesFocus(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        lines = []
        for url in mime.urls() if mime.hasUrls() else []:
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".txt"):
                try:
                    with open(url.toLocalFile(), "r", encoding="utf-8-sig") as handle:
                        lines.extend(handle.read().splitlines())
                except OSError:
                    continue
            elif url.scheme() in ("http", "https"):
                lines.append(url.toString())
        if not lines and mime.hasText():
            lines = mime.text().splitlines()
        if lines:
            self.append_lines(lines)
            event.acceptProposedAction()

    def append_lines(self, lines) -> None:
        existing = self.toPlainText().rstrip("\n")
        added = "\n".join(line.strip() for line in lines if line.strip())
        self.setPlainText(f"{existing}\n{added}" if existing else added)


class DownloadPage(QWidget):
    openCart = Signal()
    queued = Signal(int)
    toolRequested = Signal(str)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._source: Optional[str] = None  # source whose run settings the controls edit
        self._lines: list[tuple[str, Optional[str]]] = []

        self.input = InputBox()
        self.input.setMinimumHeight(90)
        self.input.setMaximumHeight(150)
        paste = QPushButton("Paste")
        paste.clicked.connect(self._paste)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.input.clear)
        self.cart_button = QPushButton()
        self.cart_button.clicked.connect(self.openCart.emit)
        from noveldb.qt.tools import TOOLS, available

        tools_menu = QMenu(self)
        for tool, label in TOOLS:
            if tool == "-":
                tools_menu.addSeparator()
                continue
            action = tools_menu.addAction(label)
            ok, reason = available(tool)
            action.setEnabled(ok)
            if reason:
                action.setToolTip(reason)
            action.triggered.connect(lambda _=False, t=tool: self.toolRequested.emit(t))
        tools_menu.setToolTipsVisible(True)
        tools = QToolButton()
        tools.setText("Tools")
        tools.setProperty("menu", True)
        tools.setMinimumWidth(120)
        tools_menu.setMinimumWidth(320)
        tools.setPopupMode(QToolButton.InstantPopup)
        tools.setMenu(tools_menu)

        # Browser sign-in, as the old external window had it: Enter Browser (with the regular
        # Chrome profile switch) and Enter App for the sfc app session.
        enter_browser = QPushButton("Enter Browser")
        enter_browser.setMinimumWidth(150)
        ok, reason = available("enter_browser")
        enter_browser.setEnabled(ok)
        enter_browser.setToolTip(reason or "Open a browser window to sign in to the site of the first link, "
                                           "then close it before downloading.")
        enter_browser.clicked.connect(lambda: self.toolRequested.emit("enter_browser"))
        self.regular_browser = QCheckBox("Use regular browser")
        self.regular_browser.setToolTip("Use your installed Chrome profile (signed-in sites) instead of the "
                                        "downloader's own browser.")
        self.regular_browser.toggled.connect(lambda on: context.update_settings(
            lambda s: replace(s, sources=replace(s.sources, regular_browser=on))))
        enter_app = QPushButton("Enter App (sfc)")
        enter_app.setMinimumWidth(150)
        enter_app.setToolTip("Sign in to the sfc app, or paste its cookie, for app-only chapters.")
        enter_app.clicked.connect(lambda: self.toolRequested.emit("sfc_app"))
        browser_row = flow(enter_browser, self.regular_browser, enter_app, spacing=10)
        # The run button sits with the box it acts on, not at the bottom of the page.
        self.download = QPushButton("Download")
        self.download.setProperty("primary", True)
        self.download.setCursor(Qt.PointingHandCursor)
        self.download.setMinimumWidth(140)
        self.download.clicked.connect(self._download)
        self.summary = hint("")
        input_actions = QHBoxLayout()
        input_actions.addWidget(paste)
        input_actions.addWidget(clear)
        input_actions.addWidget(tools)
        input_actions.addSpacing(12)
        input_actions.addWidget(self.summary, 1)
        input_actions.addWidget(self.cart_button)
        input_actions.addWidget(self.download)

        self.detected = QWidget()
        self.detected_layout = None
        self.detected_holder = QVBoxLayout()
        self.detected_holder.setContentsMargins(0, 0, 0, 0)
        self.scope = hint("")

        # Run settings ---------------------------------------------------------------
        self.format_chips = {fmt: Chip(FORMAT_LABELS[fmt], on_change=lambda on, f=fmt: self._toggle_format(f, on))
                             for fmt in OUTPUT_FORMATS}
        formats_row = flow(*self.format_chips.values())

        self.threads = spin(1, 1, 64, lambda v: self._run(threads=v))
        self.workers_label = QLabel("Image Workers")
        self.workers = spin(1, 1, 32, self._set_workers)
        workers_group = QWidget()
        wl = QHBoxLayout(workers_group)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        wl.addWidget(self.workers_label)
        wl.addWidget(self.workers)
        threads_row = flow(field_group("Threads", self.threads), workers_group, spacing=18)

        self.interval_min = dspin(2.0, 0.0, 300.0, lambda v: self._run(interval_min=v))
        self.interval_max = dspin(5.0, 0.0, 300.0, lambda v: self._run(interval_max=v))
        self.retries = spin(5, 0, 50, lambda v: self._run(max_retries=v))
        rate_row = flow(field_group("Rate Limiting  Min", self.interval_min),
                        field_group("Max", self.interval_max),
                        field_group("Retries", self.retries), spacing=18)

        self.range_from = spin(0, 0, 999_999, lambda v: self._run(range_from=v or None), special="First")
        self.range_to = spin(0, 0, 999_999, lambda v: self._run(range_to=v or None), special="Last")
        range_row = flow(field_group("Chapters  From", self.range_from), field_group("To", self.range_to), spacing=18)

        self.skip_paid = Chip("Skip paid", on_change=lambda on: self._run(skip_paid=on))
        self.number_chapters = Chip("Number chapters", on_change=lambda on: self._run(number_chapters=on))
        self.generate_on_stop = Chip("Generate on stop", on_change=lambda on: self._run(generate_on_stop=on))
        self.use_cache = Chip("Use cache", on_change=lambda on: self._run(use_cache=on))
        self.cache_images = Chip("Cache images", on_change=lambda on: self._run(cache_images=on))
        chips_row = flow(self.skip_paid, self.number_chapters, self.generate_on_stop, self.use_cache, self.cache_images)

        # Output -----------------------------------------------------------------------
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(str(paths.default_output_dir()))
        self.folder.editingFinished.connect(self._folder_edited)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        self.ask_each_time = QCheckBox("Ask each time")
        self.ask_each_time.toggled.connect(
            lambda on: context.update_settings(lambda s: replace(s, output=replace(s.output, ask_each_time=on))))
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Save to"))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(self.ask_each_time)
        from noveldb.core import platform_services

        self.shutdown = QCheckBox("Shut down computer after successful download")
        self.shutdown.setToolTip("When the whole queue finishes without failures, shut down after a "
                                 "60-second countdown you can cancel.")
        self.shutdown.toggled.connect(lambda on: context.update_settings(
            lambda s: replace(s, output=replace(s.output, shutdown_after_success=on))))
        self.shutdown.setVisible(platform_services.shutdown_supported())

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 12, 12, 12)
        layout.setSpacing(6)
        title = QLabel("Download")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(self.input)
        layout.addLayout(input_actions)
        layout.addWidget(browser_row)
        layout.addLayout(self.detected_holder)
        layout.addWidget(section("Output formats"))
        layout.addWidget(formats_row)
        layout.addWidget(section("Speed"))
        layout.addWidget(self.scope)
        layout.addWidget(threads_row)
        layout.addWidget(rate_row)
        layout.addWidget(section("Chapters"))
        layout.addWidget(range_row)
        layout.addWidget(chips_row)
        layout.addWidget(section("Save"))
        layout.addLayout(folder_row)
        layout.addWidget(self.shutdown)
        layout.addStretch(1)

        # Right-hand column: the npia account and image options (the window has the room).
        from noveldb.qt.pages.settings import images_form, npia_account_panel

        self.account = npia_account_panel(context)
        self.images = images_form(context, panel=True)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 12, 8, 12)
        side_layout.setSpacing(10)
        side_layout.addWidget(self.account)
        side_layout.addWidget(self.images)
        side_layout.addStretch(1)
        side.setMinimumWidth(360)
        side.setMaximumWidth(480)
        # The options keep a compact width; whatever the window has left goes to the log.
        body.setMinimumWidth(420)
        body.setMaximumWidth(640)
        columns = QWidget()
        columns.setMaximumWidth(640 + 480 + 16)
        columns_layout = QHBoxLayout(columns)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(8)
        columns_layout.addWidget(body, 1)
        columns_layout.addWidget(side, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(columns)
        # The log sits in a column on the right of this page, as in the old window; the main
        # window moves its one log panel here while this page is shown.
        self.log_dock = QWidget()
        self.log_dock.setMinimumWidth(360)
        dock_layout = QVBoxLayout(self.log_dock)
        dock_layout.setContentsMargins(0, 8, 8, 8)
        dock_layout.setSpacing(0)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(12)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(scroll)
        self.split.addWidget(self.log_dock)
        self.split.setStretchFactor(0, 0)  # window growth goes to the log
        self.split.setStretchFactor(1, 1)
        self.split.splitterMoved.connect(lambda *_: setattr(self, "_user_split", True))
        self._user_split = False
        self._columns = columns
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.split)

        self._detect_timer = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._detect_timer.setInterval(250)
        self._detect_timer.timeout.connect(self._detect)
        self.input.textChanged.connect(self._detect_timer.start)
        # Clickable right away; `_download` detects again before queueing.
        self.input.textChanged.connect(lambda: self.download.setEnabled(bool(self.input.toPlainText().strip())))
        context.settingsChanged.connect(self.refresh)
        context.cartChanged.connect(self._refresh_cart)
        self._refresh_cart()
        self._detect()

    def _fit_split(self) -> None:
        """Give the options their compact width (at most 62% of the page) and the log the rest."""
        total = self.split.width()
        if self._user_split or total < 600:
            return
        wanted = self._columns.maximumWidth() + 24  # room for the scroll bar
        left = min(wanted, int(total * 0.62))
        self.split.setSizes([left, total - left])

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_split)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_split()

    # -- input -------------------------------------------------------------------------

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text.strip():
            self.input.append_lines(text.splitlines())

    def add_lines(self, lines) -> None:
        self.input.append_lines(lines)

    def _detect(self) -> None:
        lines = split_inputs(self.input.toPlainText())
        engine_pref = self.context.settings.npia.engine
        self._lines = [(line, detect_source(line)) for line in lines]
        chips = []
        for line, source in self._lines[:40]:
            if source is None:
                text = f"? {line[:40]}"
            else:
                engine = engine_for(line, source, engine_pref)
                label = source + (" (browser)" if source == NPIA and engine == BROWSER else "")
                text = f"{label} - {line[:48]}"
            chip = QLabel(text)
            chip.setProperty("hint", source is None)
            chip.setStyleSheet("padding: 2px 8px; border-radius: 6px; border: 1px solid palette(mid);")
            chip.setToolTip(line)
            chips.append(chip)
        if len(self._lines) > 40:
            chips.append(hint(f"+{len(self._lines) - 40} more"))
        if self.detected_layout is not None:
            self.detected.setParent(None)
            self.detected.deleteLater()
        self.detected = flow(*chips, spacing=6) if chips else QWidget()
        self.detected_layout = self.detected.layout()
        self.detected_holder.addWidget(self.detected)
        sources = [source for _line, source in self._lines if source]
        self._source = sources[0] if sources else None
        unknown = sum(1 for _line, source in self._lines if source is None)
        valid = len(sources)
        self.download.setEnabled(valid > 0)
        self.download.setText(f"Download {valid}" if valid > 1 else "Download")
        self.summary.setText(f"{unknown} line(s) not recognised" if unknown else "")
        self.refresh()

    # -- run settings --------------------------------------------------------------------

    def refresh(self) -> None:
        s = self.context.settings
        source = self._source
        run = s.effective(source)
        override = s.override(source)
        npia = source == NPIA
        mixed = len({src for _l, src in self._lines if src}) > 1
        if npia and override.is_empty:
            note = "Shared settings. Changes made here for npia are kept as npia's own override."
        elif source and not override.is_empty:
            note = f"{source} overrides: {', '.join(override.overridden())}. Other values are shared."
        elif source:
            note = "Shared settings (used by every source without its own override)."
        else:
            note = "Shared settings."
        if mixed:
            note += " Several sources detected: these controls edit the first one; each line downloads with its own source's settings."
        self.scope.setText(note)

        for fmt, chip in self.format_chips.items():
            chip.set_quietly(fmt in run.formats)
            chip.setVisible(not (npia and fmt not in NPIA_FORMATS))
        set_quietly(self.threads, run.threads)
        self.workers_label.setText("Compression Workers" if npia else "Image Workers")
        set_quietly(self.workers, s.images.compression_workers if npia else run.image_workers)
        lo, hi = run.interval_range
        set_quietly(self.interval_min, lo)
        set_quietly(self.interval_max, hi)
        set_quietly(self.retries, run.max_retries)
        set_quietly(self.range_from, run.range_from or 0)
        set_quietly(self.range_to, run.range_to or 0)
        self.skip_paid.set_quietly(run.skip_paid)
        self.number_chapters.set_quietly(run.number_chapters)
        self.generate_on_stop.set_quietly(run.generate_on_stop)
        self.use_cache.set_quietly(run.use_cache)
        self.cache_images.set_quietly(run.cache_images)
        if not self.folder.hasFocus():
            self.folder.setText(s.output.output_dir)
        set_quietly(self.ask_each_time, s.output.ask_each_time)
        set_quietly(self.regular_browser, s.sources.regular_browser)
        set_quietly(self.shutdown, s.output.shutdown_after_success)

    def _run(self, **changes) -> None:
        source = self._source
        self.context.update_settings(lambda s: s.edit_run_settings(source, lambda run: replace(run, **changes)))

    def _toggle_format(self, fmt: str, on: bool) -> None:
        current = list(self.context.settings.effective(self._source).formats)
        if on and fmt not in current:
            current.append(fmt)
        elif not on and fmt in current:
            if len(current) == 1:  # keep at least one format
                self.format_chips[fmt].set_quietly(True)
                return
            current.remove(fmt)
        ordered = tuple(f for f in OUTPUT_FORMATS if f in current)
        self._run(formats=ordered)

    def _set_workers(self, value: int) -> None:
        if self._source == NPIA:
            self.context.update_settings(lambda s: replace(s, images=replace(s.images, compression_workers=value)))
        else:
            self._run(image_workers=value)

    # -- output ----------------------------------------------------------------------------

    def _folder_edited(self) -> None:
        value = self.folder.text().strip()
        self.context.update_settings(lambda s: replace(s, output=replace(s.output, output_dir=value)))

    def _browse(self) -> None:
        start = self.folder.text().strip() or str(paths.default_output_dir())
        folder = QFileDialog.getExistingDirectory(self, "Save downloads to", start)
        if folder:
            self.folder.setText(folder)
            self._folder_edited()

    def _output_dir(self) -> Optional[str]:
        """The folder for this batch; None when the user cancelled the folder prompt."""
        s = self.context.settings.output
        folder = s.output_dir or str(paths.default_output_dir())
        if s.ask_each_time:
            chosen = QFileDialog.getExistingDirectory(self, "Save these downloads to", folder)
            return chosen or None
        return folder

    def queue_lines(self, lines) -> int:
        """Queue every recognised line (asking for a folder when set to); the cart drops them."""
        valid = [line for line in split_inputs(NL.join(lines)) if detect_source(line)]
        if not valid:
            return 0
        folder = self._output_dir()
        if folder is None:
            return 0
        jobs = self.context.enqueue_text(NL.join(valid), folder)
        self.context.cart.remove_all(valid)
        self.queued.emit(len(jobs))
        return len(jobs)

    def _download(self) -> None:
        self._detect()
        lines = [line for line, _source in self._lines]
        if not self.queue_lines(lines):
            return
        remaining = [line for line, source in self._lines if not source]
        self.input.setPlainText(NL.join(remaining))

    def _refresh_cart(self) -> None:
        count = len(self.context.cart)
        self.cart_button.setText(f"\U0001F517 Link cart ({count})")
        self.cart_button.setEnabled(count > 0)

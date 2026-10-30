"""Settings page: categories on the left (like the mobile Settings tab), one form each on the right.

Every option of the two old Tk windows lives here or on the Download page. Controls are bound to a
settings path ("images.quality") so each edit goes through `AppContext.update_settings` and every
form refreshes when settings change elsewhere.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import net_timeouts, paths, platform_services
from noveldb.core.catalog.urls import NOVELDB_NEXT_URL, valid_catalog_url
from noveldb.core.settings.model import (
    ACCENTS,
    DEFAULT_CATALOG_URL,
    ENGINE_LABELS,
    ENGINE_W2E,
    IMAGE_FORMATS,
    NAMING_MODES,
    OUTPUT_FORMATS,
    SITE_ENGINES,
    THEMES,
    UPLOAD_ZIP_MB_MAX,
    UPLOAD_ZIP_MB_MIN,
    AppSettings,
    SharedOverrides,
)
from noveldb.core.upload import ALL as UPLOAD_HOSTS
from noveldb.qt import icons
from noveldb.qt.context import AppContext
from noveldb.qt.scale import bind_rows, bind_size, bind_title, on_scale, ui_scale
from noveldb.qt.theme_palettes import ACCENT_LABELS
from noveldb.qt.widgets import ButtonFeedback, Chip, ComboBox, DoubleSpinBox, SpinBox, flow, hint, section

THEME_LABELS = {"system": "System", "light": "Light", "dark": "Dark", "amoled": "AMOLED black"}
NAMING_LABELS = {"title": "Book title", "id": "Book ID"}
# Sources a per-source override can target (codenames, as the Download page shows them).
OVERRIDE_SOURCES = (
    "npia", "global_npia", "kpage", "sfc", "nweb", "mpia", "jara", "rbooks", "nseries", "floo", "qdn",
    "kobo", "ntk", "yeduji", "fenrir", "maru", "xiyuwx", "1qxs", "69shuba", "reader",
)


def _get(settings: AppSettings, path: str):
    value = settings
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _set(settings: AppSettings, path: str, new):
    """`settings` with the field at a dotted path replaced (nested frozen dataclasses)."""
    head, _, rest = path.partition(".")
    if not rest:
        return replace(settings, **{head: new})
    return replace(settings, **{head: _set(getattr(settings, head), rest, new)})


class Form(QWidget):
    """A scrollable form whose controls are bound to settings paths."""

    def __init__(self, context: AppContext, title: str, panel: bool = False, bare: bool = False) -> None:
        """`panel`: a card for another page (no scroll area, smaller heading).
        `bare`: a plain column inside another form (no heading, no margins)."""
        super().__init__()
        self.context = context
        self._refreshers: list[Callable[[AppSettings], None]] = []
        panel = panel or bare
        body = QWidget() if not panel else self
        if not panel:
            bind_size(body, max_width=760)
        self.layout_ = QVBoxLayout(body)
        margins = (0, 0, 0, 0) if bare else (16, 12, 16, 14) if panel else (20, 16, 20, 16)
        self.layout_.setContentsMargins(*margins)
        self.layout_.setSpacing(8)
        if not bare:
            heading = bind_title(QLabel(title), 18 if panel else 22)
            self.layout_.addWidget(heading)
        if bare:
            pass
        elif panel:
            self.setObjectName("panel")
            self.setAttribute(Qt.WA_StyledBackground, True)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(body)
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(scroll)
        self._form: QFormLayout | None = None

    # -- layout helpers ------------------------------------------------------------------------

    def add(self, widget: QWidget) -> QWidget:
        self._form = None
        self.layout_.addWidget(widget)
        return widget

    def add_section(self, text: str) -> None:
        self.add(section(text))

    def add_hint(self, text: str) -> QLabel:
        return self.add(hint(text))

    def row(self, label: str, widget: QWidget) -> QWidget:
        if self._form is None:
            self._form = QFormLayout()
            self._form.setLabelAlignment(Qt.AlignLeft)
            self.layout_.addLayout(self._form)
        self._form.addRow(label, widget)
        return widget

    def finish(self) -> None:
        self.layout_.addStretch(1)
        self.context.settingsChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        for refresher in self._refreshers:
            refresher(self.context.settings)

    def change(self, path: str, value) -> None:
        self.context.update_settings(lambda s: _set(s, path, value))

    # -- bound controls -------------------------------------------------------------------------

    def check(self, text: str, path: str, label: str = "") -> QCheckBox:
        box = QCheckBox(text)
        box.toggled.connect(lambda on: self.change(path, bool(on)))
        self._bind(box, lambda s: box.setChecked(bool(_get(s, path))))
        return self.row(label, box) if label else self.add(box)

    def spin(self, label: str, path: str, low: int, high: int, suffix: str = "") -> QSpinBox:
        box = SpinBox()
        box.setRange(low, high)
        box.setSuffix(suffix)
        box.setKeyboardTracking(False)
        box.valueChanged.connect(lambda v: self.change(path, int(v)))
        self._bind(box, lambda s: box.setValue(int(_get(s, path))))
        return self.row(label, box)

    def dspin(self, label: str, path: str, low: float, high: float, step: float = 0.5, suffix: str = "",
              decimals: int = 1) -> QDoubleSpinBox:
        box = DoubleSpinBox()
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        box.setSuffix(suffix)
        box.setKeyboardTracking(False)
        box.valueChanged.connect(lambda v: self.change(path, float(v)))
        self._bind(box, lambda s: box.setValue(float(_get(s, path))))
        return self.row(label, box)

    def combo(self, label: str, path: str, options: dict) -> QComboBox:
        box = ComboBox()
        for key, text in options.items():
            box.addItem(text, key)
        box.currentIndexChanged.connect(lambda _i: self.change(path, box.currentData()))

        def show(s):
            index = box.findData(_get(s, path))
            if index >= 0:
                box.setCurrentIndex(index)

        self._bind(box, show)
        return self.row(label, box)

    def text(self, label: str, path: str, placeholder: str = "", secret: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if secret:
            edit.setEchoMode(QLineEdit.Password)
        edit.editingFinished.connect(lambda: self.change(path, edit.text().strip()))

        def show(s):
            if not edit.hasFocus():
                edit.setText(str(_get(s, path) or ""))

        self._bind(edit, show)
        return self.row(label, edit)

    def path_picker(self, label: str, path: str, title: str, file_filter: str = "", folder: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.editingFinished.connect(lambda: self.change(path, edit.text().strip()))
        browse = QPushButton("Browse...")

        def pick():
            start = edit.text().strip() or str(paths.default_output_dir())
            chosen = (QFileDialog.getExistingDirectory(self, title, start) if folder
                      else QFileDialog.getOpenFileName(self, title, start, file_filter)[0])
            if chosen:
                edit.setText(chosen)
                self.change(path, chosen)

        browse.clicked.connect(pick)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(browse)

        def show(s):
            if not edit.hasFocus():
                edit.setText(str(_get(s, path) or ""))

        self._bind(edit, show)
        self.row(label, holder)
        return edit

    def _bind(self, widget, show: Callable[[AppSettings], None]) -> None:
        def quiet(s):
            widget.blockSignals(True)
            try:
                show(s)
            finally:
                widget.blockSignals(False)

        self._refreshers.append(quiet)


# -- category forms ---------------------------------------------------------------------------------

def general_form(context: AppContext) -> Form:
    f = Form(context, "General")
    f.add_section("Appearance")
    f.combo("Theme", "ui.theme", {k: THEME_LABELS[k] for k in THEMES})
    f.combo("Accent", "ui.accent", {k: ACCENT_LABELS.get(k, k) for k in ACCENTS})
    f.add_section("Display scaling")
    f.check("Pick the scale for this screen automatically", "ui.auto_dpi_scale", " ")
    f.check("Scale the interface with the window size", "ui.scale_with_window", " ")
    scale = f.dspin("Interface scale", "ui.gui_scale_factor", 0.5, 3.0, 0.05, "x", decimals=2)
    import dpi_setup

    running = (dpi_setup.get_scale_factor(), dpi_setup.get_system_scale())
    detected = hint("")
    restart = QPushButton("Restart now")
    restart.clicked.connect(lambda: context.toolRequested.emit("restart"))
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(detected, 1)
    row.addWidget(restart)
    f.add(holder)

    def show_scale(s):
        scale.setEnabled(not s.ui.auto_dpi_scale)
        wanted = dpi_setup._get_default_scale_for_resolution() if s.ui.auto_dpi_scale else s.ui.gui_scale_factor
        text = (f"Now {running[0]:.2f}x on a {running[1]:.2f}x screen "
                f"({'automatic' if s.ui.auto_dpi_scale else 'manual'}).")
        pending = abs(float(wanted) - float(running[0])) > 0.001
        restart.setVisible(pending)
        detected.setText(text + (f" {float(wanted):.2f}x applies after a restart." if pending else ""))

    f._refreshers.append(show_scale)

    f.add_section("Downloads")
    f.path_picker("Save to", "output.output_dir", "Save downloads to", folder=True)
    f.check("Ask for a folder each time", "output.ask_each_time", " ")
    f.spin("Downloads at once", "queue.max_concurrent_jobs", 1, 3)
    f.combo("File names", "shared.naming_mode", {k: NAMING_LABELS[k] for k in NAMING_MODES})
    f.check("Add the chapter range to file names", "shared.append_range", " ")
    f.spin("Retries per chapter", "shared.max_retries", 0, 50)
    if platform_services.shutdown_supported():
        f.check("Shut down the computer when the whole queue finishes", "output.shutdown_after_success", " ")
    network_section(f)
    app_updates_section(f, context)
    f.finish()
    return f


def network_section(f: Form) -> None:
    """Settings > General > Network: the two minimum waits (noveldb.core.net_timeouts)."""
    f.add_section("Network")
    low, high = net_timeouts.MIN_SECONDS, net_timeouts.MAX_SECONDS
    f.spin("Request timeout", "shared.request_timeout", low, high, " s")
    f.spin("Page load wait", "shared.page_load_timeout", low, high, " s")
    f.add_hint("Minimum wait for a server or a page. Sources that need longer keep their own.")


def app_updates_section(f: Form, context: AppContext) -> None:
    """Settings › General › App updates: the version in use, the daily check, and Check now."""
    from noveldb.core import app_update
    from noveldb.qt.app_update import UpdateOffer

    f.add_section("App updates")
    info = app_update.build_info()
    f.add_hint(f"This copy: {info.describe()}.")
    f.check("Check for app updates automatically", "updates.check_automatically", " ")
    f.add_hint("Checks the NovelDB releases on GitHub once a day. An update is downloaded only when you choose "
               "Update, and installed only after its size and SHA-256 match the release's manifest.")
    check_now = QPushButton("Check for app updates now")
    feedback = ButtonFeedback(check_now)
    install = QPushButton("Update now")
    install.hide()
    status = f.add_hint("")
    status.hide()
    offer = UpdateOffer(f, context)
    found = {"update": None}

    def check():
        feedback.busy("Checking")
        context.check_app_update()

    def checked(result, automatic):
        if automatic and result.status != "update":
            return
        status.setText(result.message)
        status.show()
        found["update"] = result.update
        install.setVisible(result.update is not None)
        if feedback._spin.isActive():
            feedback.done(result.status not in ("failed",),
                          {"update": "Update found", "current": "Up to date", "failed": "Failed"}.get(
                              result.status, "Checked"))

    check_now.clicked.connect(check)
    check_now.setEnabled(info.can_update)
    install.clicked.connect(lambda: found["update"] and offer.install(found["update"]))
    context.appUpdateChecked.connect(checked)
    f.add(flow(check_now, install))
    if not info.can_update:
        status.setText(app_update.check(info).message)
        status.show()


def npia_account_panel(context: AppContext) -> Form:
    """The npia account (shown on the Download page)."""
    f = Form(context, "npia login", panel=True)
    email = QLineEdit()
    email.setPlaceholderText("Email")
    password = QLineEdit()
    password.setEchoMode(QLineEdit.Password)
    key = QLineEdit()
    key.setEchoMode(QLineEdit.Password)
    key.setPlaceholderText("Optional: a session key instead of signing in")
    f.row("Email", email)
    f.row("Password", password)
    f.row("Session key", key)
    status = hint("Signed in." if context.signed_in else "Not signed in: only free chapters can be read.")
    save = QPushButton("Save and sign in")
    save.setProperty("primary", True)
    save_feedback = ButtonFeedback(save)

    def fill():
        secrets = context.settings.secrets
        email.setText(secrets.email)
        password.setText(secrets.password)
        key.setText(secrets.loginkey)

    def sign_in():
        e, p, k = email.text().strip(), password.text(), key.text().strip()
        old = context.settings.secrets
        if (e, p) != (old.email, old.password) and k == old.loginkey:
            k = ""  # a new account: the old session key belongs to the previous one
            key.clear()
        context.update_settings(lambda s: replace(s, secrets=replace(s.secrets, email=e, password=p, loginkey=k)))
        context.save_now()
        status.setText("Signing in...")
        save_feedback.busy("Signing in")
        context.sign_in()

    save.clicked.connect(sign_in)
    def signed_in(ok):
        status.setText("Signed in." if ok else "Not signed in: only free chapters can be read.")
        if save_feedback._spin.isActive():
            save_feedback.done(ok, "Signed in" if ok else "Not signed in")

    context.signedInChanged.connect(signed_in)
    google = QPushButton("Sign in with Google")

    def with_google():
        from noveldb.qt.google_login import google_login

        google_login(context, f.window())
        fill()

    google.clicked.connect(with_google)
    f.add(status)
    f.add(flow(google, save))
    f.check("Sign in when the app starts", "npia.auto_login")
    f.add_hint("Credentials are kept in config.json on this computer. Settings exports leave them out.")
    fill()
    f.finish()
    return f


def npia_form(context: AppContext) -> Form:
    f = Form(context, "npia login")
    f.add_hint("Your npia account (email, password, session key, Google sign-in) is on the Download page.")
    go = QPushButton("Show the account")
    go.clicked.connect(lambda: context.toolRequested.emit("show_account"))
    f.add(flow(go))
    f.check("Sign in when the app starts", "npia.auto_login")

    f.add_section("Downloads")
    f.combo("Engine", "npia.engine", {"native": "Native (login, font mapping, notices)", "browser": "Browser"})
    f.check("Download author notices", "npia.include_notices")
    f.check("Use the browser when a chapter cannot be read directly", "npia.use_browser_fallback")
    f.path_picker("Font mapping", "npia.mapping_path", "Choose the font mapping file", "JSON (*.json);;All files (*)")
    f.add_hint("npia chapter text is decoded through this mapping when it uses a scrambled font.")
    f.add_section("Tag search")
    tag_search = QPushButton("Open tag search")
    tag_search.clicked.connect(lambda: context.toolRequested.emit("tag_search"))
    f.add(flow(tag_search))
    f.add_hint("Find novel IDs by tag, scrape all novels or read the Top 100, with a live log.")
    f.finish()
    return f


class OverridesForm(Form):
    """Per-source overrides: pick a source, then which shared values it replaces."""

    FIELDS = (
        ("threads", "Threads"), ("interval_min", "Rate limit min (s)"), ("interval_max", "Rate limit max (s)"),
        ("use_cache", "Use cache"), ("cache_images", "Cache images"), ("number_chapters", "Number chapters"),
        ("separate_txt", "Separate TXT"),
    )

    def __init__(self, context: AppContext) -> None:
        super().__init__(context, "Per-source overrides")
        self.add_hint("Every source uses the shared settings except for the values ticked here. No source "
                      "has an override until you add one.")
        self.source = ComboBox()
        self.source.addItem("Choose a source", None)
        for source in OVERRIDE_SOURCES:
            self.source.addItem(source, source)
        self.source.currentIndexChanged.connect(self.refresh)
        self.row("Source", self.source)
        self.summary = self.add_hint("")
        self.controls = {}
        for name, label in self.FIELDS:
            toggle = QCheckBox(label)
            if name in ("threads",):
                value = SpinBox()
                value.setRange(1, 64)
            elif name.startswith("interval"):
                value = DoubleSpinBox()
                value.setRange(0, 300)
                value.setDecimals(1)
                value.setSingleStep(0.5)
            else:
                value = QCheckBox("On")
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(toggle)
            row.addStretch(1)
            row.addWidget(value)
            self.add(holder)
            toggle.toggled.connect(lambda on, n=name: self._toggle(n, on))
            signal = value.toggled if isinstance(value, QCheckBox) else value.valueChanged
            signal.connect(lambda v, n=name: self._value(n, v))
            self.controls[name] = (toggle, value)
        self.format_toggle = QCheckBox("Formats")
        self.format_chips = {fmt: Chip(fmt.upper(), on_change=lambda on, x=fmt: self._format(x, on)) for fmt in OUTPUT_FORMATS}
        self.format_toggle.toggled.connect(lambda on: self._toggle("formats", on))
        self.add(self.format_toggle)
        self.add(flow(*self.format_chips.values()))
        self.range_toggle = QCheckBox("Chapter range (0 = first / last)")
        self.range_from = SpinBox()
        self.range_to = SpinBox()
        for box in (self.range_from, self.range_to):
            box.setRange(0, 999_999)
            box.valueChanged.connect(lambda _v: self._range())
        self.range_toggle.toggled.connect(lambda _on: self._range())
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.range_toggle)
        row.addStretch(1)
        row.addWidget(QLabel("From"))
        row.addWidget(self.range_from)
        row.addWidget(QLabel("To"))
        row.addWidget(self.range_to)
        self.add(holder)
        reset = QPushButton("Use the shared settings for this source")
        reset.clicked.connect(lambda: self._update(lambda _o: SharedOverrides()))
        self.add(reset)
        self.finish()

    @property
    def current(self) -> str:
        return self.source.currentData()

    def _update(self, change) -> None:
        source = self.current
        if source is None:
            return
        self.context.update_settings(lambda s: s.with_override(source, change(s.override(source))))

    def _toggle(self, name: str, on: bool) -> None:
        shared = self.context.settings.shared
        self._update(lambda o: replace(o, **{name: getattr(shared, name) if on else None}))

    def _value(self, name: str, value) -> None:
        if self.controls[name][0].isChecked():
            cast = float if name.startswith("interval") else (int if name == "threads" else bool)
            self._update(lambda o: replace(o, **{name: cast(value)}))

    def _format(self, fmt: str, on: bool) -> None:
        if not self.format_toggle.isChecked():
            return
        current = list(self.context.settings.effective(self.current).formats)
        current = [f for f in current if f != fmt] + ([fmt] if on else [])
        if current:
            self._update(lambda o: replace(o, formats=tuple(f for f in OUTPUT_FORMATS if f in current)))

    def _range(self) -> None:
        if self.range_toggle.isChecked():
            low, high = self.range_from.value() or None, self.range_to.value() or None
            self._update(lambda o: replace(o, range_from=low, range_to=high, range_override=True))
        else:
            self._update(lambda o: replace(o, range_from=None, range_to=None, range_override=False))

    def refresh(self) -> None:
        if not hasattr(self, "controls"):
            return
        s = self.context.settings
        override = s.override(self.current)
        run = s.effective(self.current)
        names = override.overridden()
        if self.current is None:
            overridden = [source for source in OVERRIDE_SOURCES if not s.override(source).is_empty]
            self.summary.setText("Sources with overrides: " + ", ".join(overridden) + "." if overridden
                                 else "No source has an override.")
        else:
            self.summary.setText(f"Overrides: {', '.join(names)}." if names else "Uses the shared settings.")
        widgets = [w for pair in self.controls.values() for w in pair]
        widgets += [self.format_toggle, self.range_toggle, self.range_from, self.range_to, *self.format_chips.values()]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            for name, (toggle, value) in self.controls.items():
                on = getattr(override, name) is not None
                toggle.setChecked(on)
                value.setEnabled(on)
                current = getattr(run, name)
                if isinstance(value, QCheckBox):
                    value.setChecked(bool(current))
                else:
                    value.setValue(current)
            self.format_toggle.setChecked(override.formats is not None)
            for fmt, chip in self.format_chips.items():
                chip.setChecked(fmt in run.formats)
                chip.setEnabled(override.formats is not None)
            self.range_toggle.setChecked(override.has_range)
            self.range_from.setValue(run.range_from or 0)
            self.range_to.setValue(run.range_to or 0)
            self.range_from.setEnabled(override.has_range)
            self.range_to.setEnabled(override.has_range)
            chosen = self.current is not None
            for toggle, _value in self.controls.values():
                toggle.setEnabled(chosen)
            self.format_toggle.setEnabled(chosen)
            self.range_toggle.setEnabled(chosen)
        finally:
            for widget in widgets:
                widget.blockSignals(False)


def sources_form(context: AppContext) -> Form:
    f = Form(context, "Source options")
    f.add_section("kpage")
    f.check("Skip the last page of each chapter", "sources.kpage.skip_last_page")
    f.check("Keep filler pages", "sources.kpage.keep_filler")
    f.check("Drop repeated images", "sources.kpage.dedupe_images")
    f.spin("Leading images to check", "sources.kpage.dedupe_leading_images", 0, 20)
    f.add_section("ntk")
    f.check("Use the npia cover when the book is also on npia", "sources.ntk_npia_cover")
    f.add_section("nweb")
    f.check("Use the store cover", "sources.nweb_store_cover")
    f.add_section("kobo")
    f.check("Horizontal page layout", "sources.kobo_horizontal_layout")
    f.add_section("Browser")
    f.check("Use the regular browser profile (signed-in sites)", "sources.regular_browser")
    f.finish()
    return f


ENGINE_HINTS = {
    "native": "The app's own scrapers for the sites it knows best.",
    "novel_downloader": "novel-downloader's rules, mostly Chinese and Japanese sites.",
    ENGINE_W2E: "WebToEpub's parsers for 650+ sites, mostly English translations.",
}


def engines_form(context: AppContext) -> Form:
    """The site engines a link tries, in order, and the rule engines' opt-in updates."""
    from noveldb.core import engine_updates

    f = Form(context, "Site engines")
    f.add_hint("Each link tries these in order until one finds chapters. Drag to reorder, "
               "and untick an engine to skip it.")
    engines = QListWidget()
    engines.setDragDropMode(QAbstractItemView.InternalMove)
    engines.setDefaultDropAction(Qt.MoveAction)
    engines.setSelectionMode(QAbstractItemView.SingleSelection)
    bind_size(engines, max_width=560)
    f.add(engines)

    def current_items():
        return [engines.item(i) for i in range(engines.count())]

    def store():
        order = tuple(item.data(Qt.UserRole) for item in current_items())
        disabled = tuple(item.data(Qt.UserRole) for item in current_items()
                         if item.checkState() != Qt.Checked)
        if len(disabled) == len(order):
            show(context.settings)  # one engine always stays on
            return
        context.update_settings(lambda s: replace(s, engines=replace(s.engines, order=order, disabled=disabled)))

    def move(step):
        row = engines.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < engines.count():
            return
        item = engines.takeItem(row)
        engines.insertItem(target, item)
        engines.setCurrentRow(target)
        store()

    up, down, default = QPushButton("Move up"), QPushButton("Move down"), QPushButton("Default order")
    up.clicked.connect(lambda: move(-1))
    down.clicked.connect(lambda: move(1))
    default.clicked.connect(lambda: context.update_settings(
        lambda s: replace(s, engines=replace(s.engines, order=SITE_ENGINES, disabled=()))))
    f.add(flow(up, down, default))

    def show(s):
        engines.blockSignals(True)
        try:
            selected = engines.currentItem().data(Qt.UserRole) if engines.currentItem() else None
            engines.clear()
            for position, name in enumerate(s.engines.full_order(), 1):
                item = QListWidgetItem(f"{position}. {ENGINE_LABELS[name]}  —  {ENGINE_HINTS[name]}")
                item.setData(Qt.UserRole, name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
                item.setCheckState(Qt.Unchecked if name in s.engines.disabled else Qt.Checked)
                item.setSizeHint(QSize(0, ui_scale.px(36)))
                engines.addItem(item)
                if name == selected:
                    engines.setCurrentItem(item)
            engines.setFixedHeight(ui_scale.px(36) * engines.count() + ui_scale.px(6))
        finally:
            engines.blockSignals(False)

    on_scale(engines, lambda: show(context.settings))
    engines.itemChanged.connect(lambda _item: store())
    engines.model().rowsMoved.connect(lambda *_args: store())
    f._refreshers.append(show)

    f.add_section("Updates")
    in_use = {engine.name: f.add_hint("") for engine in engine_updates.ENGINES}
    status = f.add_hint("")
    status.hide()
    messages = {}

    def show_bundles(_s=None):
        for engine in engine_updates.ENGINES:
            bundle = engine_updates.current(engine)
            in_use[engine.name].setText(
                f"{engine.title}: {bundle.label}." if bundle else f"{engine.title}: missing from this build.")

    f._refreshers.append(show_bundles)
    f.check("Update the novel-downloader rules automatically", "engines.nd_auto_update")
    f.check("Update the WebToEpub parsers automatically", "engines.webtoepub_auto_update")
    f.add_hint("Both are on by default and checked once a day. Updates come from NovelDB's GitHub releases, "
               "where a daily job rebuilds each engine from its official repository and checks that it loads "
               "before publishing it. A downloaded update that fails to load is set aside.")
    check_now = QPushButton("Check for updates now")
    check_feedback = ButtonFeedback(check_now)
    pending = set()

    def check():
        messages.clear()
        pending.update(engine.name for engine in engine_updates.ENGINES)
        check_feedback.busy("Checking")
        context.check_engine_updates()

    def checked(result):
        show_bundles()
        messages[result.engine.name] = result
        status.setText("\n".join(r.message for r in messages.values()))
        status.show()
        pending.discard(result.engine.name)
        if not pending and check_feedback._spin.isActive():
            results = list(messages.values())
            failed = any(r.status == "failed" for r in results)
            updated = any(r.status == "updated" for r in results)
            check_feedback.done(not failed, "Failed" if failed else "Updated" if updated else "Up to date")

    check_now.clicked.connect(check)
    context.engineUpdateChecked.connect(checked)
    f.add(flow(check_now))

    def opted_in():
        now = {e.name for e in engine_updates.ENGINES if context.settings.engines.auto_update(e.name)}
        if now - opted_in.before:
            context.check_engine_updates(automatic=True)
        opted_in.before = now

    opted_in.before = {e.name for e in engine_updates.ENGINES if context.settings.engines.auto_update(e.name)}
    context.settingsChanged.connect(opted_in)
    f.add_hint("novel-downloader is made by the 404 Novel Project (AGPL-3.0): "
               "https://github.com/404-novel-project/novel-downloader. WebToEpub is made by David Teviotdale "
               "and contributors (GPL-3.0): https://github.com/dteviot/WebToEpub")
    f.finish()
    return f


def images_form(context: AppContext, panel: bool = False) -> Form:
    """Image options in two columns: compression on the left; animations, cover and comics on the right."""
    f = Form(context, "Images", panel=panel)
    left, right = Form(context, "", bare=True), Form(context, "", bare=True)
    columns = QWidget()
    row = QHBoxLayout(columns)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(20)
    row.addWidget(left, 1, Qt.AlignTop)
    row.addWidget(right, 1, Qt.AlignTop)

    left.check("Save image addresses only", "images.save_image_urls_only")
    compress = left.check("Compress images", "images.compress")
    quality = left.spin("Quality", "images.quality", 1, 100)
    fmt = left.combo("Format", "images.format", {k: k for k in IMAGE_FORMATS})
    workers = left.spin("Workers (npia)", "images.compression_workers", 1, 32)
    target = left.check("Recompress to fit a size", "images.recompress_target_enable")
    target_mb = left.dspin("Target size", "images.recompress_target_mb", 1, 2000, 1, " MB")

    gifs = right.check("Convert animated GIFs", "images.convert_gifs")
    static = right.check("First frame only", "images.static_only")
    static.setToolTip("Keep only the first frame of animated images.")
    right.add_section("Cover")
    cover = right.check("Compress the cover", "images.compress_cover")
    cover_quality = right.spin("Quality", "images.cover_quality", 1, 100)
    cover_format = right.combo("Format", "images.cover_format", {k: k for k in IMAGE_FORMATS})
    right.add_section("Comics")
    zip_images = right.check("Compress CBZ images", "images.zip_compress_images")
    zip_images.setToolTip("Compress the images stored inside CBZ files.")
    long_layout = right.check("Long-strip layout", "images.long_image_layout")
    f.add(columns)

    def gate(s):
        on = not s.images.save_image_urls_only
        for widget in (compress, gifs, static, workers, target, cover, zip_images, long_layout):
            widget.setEnabled(on)
        for widget in (quality, fmt):
            widget.setEnabled(on and s.images.compress)
        target_mb.setEnabled(on and s.images.recompress_target_enable)
        cover_quality.setEnabled(on and s.images.compress_cover)
        cover_format.setEnabled(on and s.images.compress_cover)

    f._refreshers.append(gate)
    f.add_hint("Saving image addresses only turns the other image options off.")
    left.finish()
    right.finish()
    f.finish()
    return f


def text_form(context: AppContext) -> Form:
    f = Form(context, "Text and PDF")
    f.add_section("Text")
    f.check("Separate TXT files (one file per chapter, in a folder per book)", "shared.separate_txt")
    f.check("Strip leading spaces", "text.strip_leading_spaces")
    f.check("Remove line breaks inside paragraphs", "text.remove_newlines")
    f.check("Blank line between paragraphs", "text.blank_paragraph_lines")
    f.check("Also save the chapter HTML (npia)", "text.save_html")
    f.add_section("PDF")
    f.check("Table of contents", "text.pdf_toc")
    f.check("Page numbers", "text.pdf_page_numbers")
    f.check("Counter layout", "text.pdf_counter_layout")
    f.finish()
    return f


def upload_form(context: AppContext) -> Form:
    f = Form(context, "Upload and share")
    f.add_hint("Hosts are tried in this order until one accepts the file.")
    chips = {}

    def toggle(host_id, on):
        hosts = [h for h in context.settings.upload.hosts if h != host_id] + ([host_id] if on else [])
        if hosts:
            context.update_settings(lambda s: replace(s, upload=replace(s.upload, hosts=tuple(hosts))))
        else:
            chips[host_id].set_quietly(True)

    for host in UPLOAD_HOSTS:
        chips[host.id] = Chip(host.display_name, on_change=lambda on, h=host.id: toggle(h, on))
    f.add(flow(*chips.values()))

    def show(s):
        order = list(s.upload.hosts)
        for host in UPLOAD_HOSTS:
            chip = chips[host.id]
            chip.set_quietly(host.id in order)
            chip.setText(f"{order.index(host.id) + 1}. {host.display_name}" if host.id in order else host.display_name)

    f._refreshers.append(show)
    f.check("Copy links to the clipboard after uploading", "upload.copy_link")
    f.add_section("ZIP bundles")
    f.check("Bundle several files into ZIP uploads", "upload.zip_bundle")
    biggest = f.spin("Largest ZIP", "upload.zip_max_mb", UPLOAD_ZIP_MB_MIN, UPLOAD_ZIP_MB_MAX, " MB")
    biggest.setSingleStep(64)
    f._refreshers.append(lambda s: biggest.setEnabled(s.upload.zip_bundle))
    f.add_hint("Two or more files are packed into as few ZIPs as fit under this size, one link each (for example "
               "11 books in 3 ZIPs gives 3 links). A host with a smaller limit lowers it. A file bigger than the "
               "limit uploads on its own.")
    f.add_section("Accounts")
    f.text("pixeldrain", "secrets.pixeldrain_key", "API key", secret=True)
    f.text("gofile", "secrets.gofile_token", "Account token (optional)", secret=True)
    f.text("catbox", "secrets.catbox_userhash", "User hash (optional)", secret=True)
    agreed = f.add_hint("")
    f._refreshers.append(lambda s: agreed.setText(
        "You agreed to the sharing terms." if s.upload.agreement_accepted
        else "You will be asked to confirm you may share files the first time you upload."))
    f.finish()
    return f


def catalog_form(context: AppContext) -> Form:
    f = Form(context, "Catalog site")
    url = QLineEdit()
    url.setPlaceholderText(DEFAULT_CATALOG_URL)
    error = hint("")

    def save(text):
        value = valid_catalog_url(text)
        if value is None:
            error.setText("Enter a full address starting with http:// or https://")
            save_feedback.done(False, "Check the address")
            return
        error.setText("")
        url.setText(value)
        f.change("catalog.url", value)
        context.save_now()
        save_feedback.done(True)

    url.returnPressed.connect(lambda: save(url.text()))
    save_button = QPushButton("Save")
    save_feedback = ButtonFeedback(save_button)
    save_button.clicked.connect(lambda: save(url.text()))
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(url, 1)
    row.addWidget(save_button)
    f.add(holder)
    use_default = QPushButton("Use NovelDB")
    use_default.clicked.connect(lambda: save(DEFAULT_CATALOG_URL))
    use_next = QPushButton("Use NovelDB Next")
    use_next.clicked.connect(lambda: save(NOVELDB_NEXT_URL))
    f.add(flow(use_default, use_next))
    f.add(error)
    f.check("Fill the window (widen sites that keep their page in a narrow column)", "catalog.fill_width")
    f.check("Send outside links to the link cart", "catalog.cart_links")
    download = icons.inline(icons.DOWNLOAD, 18)
    f.add_hint("This applies only to sites other than NovelDB, NovelDB Next and the sites NovelDB downloads from. "
               f"There, links to book pages go to the link cart and their buttons read {download} Download. "
               "Turn this off to open them in your browser instead. On the sites NovelDB downloads from, links "
               f"to other sites open in your browser, and a page it can download gets a {download} Download button "
               "(bottom right) that adds the page to the link cart.")
    def show(s):
        if not url.hasFocus():
            url.setText(s.catalog.url)

    f._refreshers.append(show)
    f.finish()
    return f


def advanced_form(context: AppContext) -> Form:
    f = Form(context, "Advanced")
    f.add_section("Folders")
    buttons = []
    for label, target in (
        ("Settings folder", lambda: paths.config_path().parent),
        ("Logs", paths.logs_dir),
        ("Cache", paths.cache_dir),
        ("App data", paths.data_dir),
    ):
        button = QPushButton(label)
        button.clicked.connect(lambda _=False, t=target: platform_services.open_path(t()))
        buttons.append(button)
    f.add(flow(*buttons))
    f.add_hint(f"Settings file: {paths.config_path()}")

    f.add_section("Settings file")
    export = QPushButton("Export settings (without accounts)...")

    def do_export():
        target, _ = QFileDialog.getSaveFileName(f, "Export settings", "noveldb-settings.json", "JSON (*.json)")
        if not target:
            return
        from noveldb.core.settings.desktop_config import export_config
        from noveldb.core.settings.store import write_raw

        write_raw(export_config(context.settings, None, include_secrets=False), target)
        context.hub.message.emit(f"Settings exported to {target}")
        export_feedback.done(True, "Exported")

    export_feedback = ButtonFeedback(export)
    export.clicked.connect(do_export)
    reset = QPushButton("Reset settings...")

    def do_reset():
        answer = QMessageBox.question(f, "Reset settings",
                                      "Reset every setting to its default? Accounts and keys are kept.")
        if answer == QMessageBox.Yes:
            context.update_settings(lambda s: replace(AppSettings(), secrets=s.secrets))
            context.save_now()

    reset.clicked.connect(do_reset)
    f.add(flow(export, reset))
    f.finish()
    return f


CATEGORIES = (
    ("General", general_form),
    ("npia login", npia_form),
    ("Per-source overrides", OverridesForm),
    ("Source options", sources_form),
    ("Site engines", engines_form),
    ("Text and PDF", text_form),
    ("Upload and share", upload_form),
    ("Catalog site", catalog_form),
    ("Advanced", advanced_form),
)


class SettingsPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.categories = QListWidget()
        self.categories.setObjectName("sidebar")
        bind_size(self.categories, fixed_width=190)
        self.stack = QStackedWidget()
        self.forms = {}
        for name, build in CATEGORIES:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, ui_scale.px(40)))
            self.categories.addItem(item)
            form = build(context)
            self.forms[name] = form
            self.stack.addWidget(form)
        bind_rows(self.categories, 40)
        self.categories.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.categories.setCurrentRow(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.categories)
        layout.addWidget(self.stack, 1)

    def show_category(self, name: str) -> None:
        names = [n for n, _ in CATEGORIES]
        if name in names:
            self.categories.setCurrentRow(names.index(name))

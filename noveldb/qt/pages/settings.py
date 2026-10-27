"""Settings page: appearance, npia account, output, queue, uploads, catalog site, folders."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import paths, platform_services
from noveldb.core.catalog.urls import NOVELDB_NEXT_URL, valid_catalog_url
from noveldb.core.settings.model import ACCENTS, DEFAULT_CATALOG_URL, THEMES
from noveldb.core.upload import ALL as UPLOAD_HOSTS
from noveldb.qt.context import AppContext
from noveldb.qt.theme_palettes import ACCENT_LABELS
from noveldb.qt.widgets import Chip, flow, hint, section, set_quietly, spin

THEME_LABELS = {"system": "System", "light": "Light", "dark": "Dark", "amoled": "AMOLED black"}


class SettingsPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        s = context.settings

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        # Appearance --------------------------------------------------------------------
        layout.addWidget(section("Appearance"))
        self.theme = QComboBox()
        for key in THEMES:
            self.theme.addItem(THEME_LABELS[key], key)
        self.theme.currentIndexChanged.connect(
            lambda: self._set(lambda x: replace(x, ui=replace(x.ui, theme=self.theme.currentData()))))
        self.accent = QComboBox()
        for key in ACCENTS:
            self.accent.addItem(ACCENT_LABELS.get(key, key), key)
        self.accent.currentIndexChanged.connect(
            lambda: self._set(lambda x: replace(x, ui=replace(x.ui, accent=self.accent.currentData()))))
        form = QFormLayout()
        form.addRow("Theme", self.theme)
        form.addRow("Accent", self.accent)
        layout.addLayout(form)

        # npia account ------------------------------------------------------------------
        layout.addWidget(section("npia account"))
        self.email = QLineEdit()
        self.email.setPlaceholderText("Email")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.loginkey = QLineEdit()
        self.loginkey.setEchoMode(QLineEdit.Password)
        self.loginkey.setPlaceholderText("Optional: paste a session key instead of signing in")
        self.auto_login = QCheckBox("Sign in when the app starts")
        self.auto_login.toggled.connect(
            lambda on: self._set(lambda x: replace(x, npia=replace(x.npia, auto_login=on))))
        save_account = QPushButton("Save and sign in")
        save_account.clicked.connect(self._save_account)
        self.account_status = hint("Not signed in")
        context.signedInChanged.connect(self._signed_in)
        form = QFormLayout()
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        form.addRow("Session key", self.loginkey)
        layout.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self.auto_login)
        row.addStretch(1)
        row.addWidget(save_account)
        layout.addLayout(row)
        layout.addWidget(self.account_status)

        # Output and queue ------------------------------------------------------------------
        layout.addWidget(section("Downloads"))
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText(str(paths.default_output_dir()))
        self.output_dir.editingFinished.connect(
            lambda: self._set(lambda x: replace(x, output=replace(x.output, output_dir=self.output_dir.text().strip()))))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        folder = QHBoxLayout()
        folder.addWidget(self.output_dir, 1)
        folder.addWidget(browse)
        self.ask_each_time = QCheckBox("Ask for a folder each time")
        self.ask_each_time.toggled.connect(
            lambda on: self._set(lambda x: replace(x, output=replace(x.output, ask_each_time=on))))
        self.at_once = spin(s.queue.max_concurrent_jobs, 1, 3, lambda v: self._set(
            lambda x: replace(x, queue=replace(x.queue, max_concurrent_jobs=v))))
        self.engine = QComboBox()
        self.engine.addItem("Native (login, fonts, notices)", "native")
        self.engine.addItem("Browser", "browser")
        self.engine.currentIndexChanged.connect(
            lambda: self._set(lambda x: replace(x, npia=replace(x.npia, engine=self.engine.currentData()))))
        form = QFormLayout()
        form.addRow("Save to", folder)
        form.addRow("", self.ask_each_time)
        form.addRow("Downloads at once", self.at_once)
        form.addRow("npia engine", self.engine)
        layout.addLayout(form)

        # Uploads -------------------------------------------------------------------------
        layout.addWidget(section("Upload and share"))
        layout.addWidget(hint("Hosts are tried in this order until one accepts the file."))
        self.host_chips = {}
        for host in UPLOAD_HOSTS:
            chip = Chip(host.display_name, on_change=lambda on, h=host.id: self._toggle_host(h, on))
            self.host_chips[host.id] = chip
        layout.addWidget(flow(*self.host_chips.values()))
        self.copy_link = QCheckBox("Copy links to the clipboard after uploading")
        self.copy_link.toggled.connect(
            lambda on: self._set(lambda x: replace(x, upload=replace(x.upload, copy_link=on))))
        layout.addWidget(self.copy_link)
        self.pixeldrain_key = self._secret("pixeldrain_key", "API key")
        self.gofile_token = self._secret("gofile_token", "Account token (optional)")
        self.catbox_userhash = self._secret("catbox_userhash", "User hash (optional)")
        form = QFormLayout()
        form.addRow("pixeldrain", self.pixeldrain_key)
        form.addRow("gofile", self.gofile_token)
        form.addRow("catbox", self.catbox_userhash)
        layout.addLayout(form)

        # Catalog site ----------------------------------------------------------------------
        layout.addWidget(section("Catalog site"))
        self.catalog_url = QLineEdit()
        self.catalog_url.setPlaceholderText(DEFAULT_CATALOG_URL)
        self.catalog_error = hint("")
        save_site = QPushButton("Save")
        save_site.clicked.connect(lambda: self._save_catalog(self.catalog_url.text()))
        use_default = QPushButton("Use NovelDB")
        use_default.clicked.connect(lambda: self._save_catalog(DEFAULT_CATALOG_URL))
        use_next = QPushButton("Use NovelDB Next")
        use_next.clicked.connect(lambda: self._save_catalog(NOVELDB_NEXT_URL))
        site_row = QHBoxLayout()
        site_row.addWidget(self.catalog_url, 1)
        site_row.addWidget(save_site)
        layout.addLayout(site_row)
        layout.addWidget(flow(use_default, use_next))
        layout.addWidget(self.catalog_error)
        self.cart_links = QCheckBox("Send outside links to the link cart (other sites)")
        self.cart_links.toggled.connect(
            lambda on: self._set(lambda x: replace(x, catalog=replace(x.catalog, cart_links=on))))
        layout.addWidget(self.cart_links)

        # Folders ---------------------------------------------------------------------------
        layout.addWidget(section("Folders"))
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
        layout.addWidget(flow(*buttons))
        layout.addWidget(hint(f"Settings file: {paths.config_path()}"))
        layout.addStretch(1)

        body.setMaximumWidth(820)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        context.settingsChanged.connect(self.refresh)
        self.refresh()
        self._fill_account()

    def _set(self, change) -> None:
        self.context.update_settings(change)

    def _secret(self, name: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText(placeholder)
        edit.setText(getattr(self.context.settings.secrets, name))
        edit.editingFinished.connect(lambda: self._set(
            lambda x: replace(x, secrets=replace(x.secrets, **{name: edit.text().strip()}))))
        return edit

    def refresh(self) -> None:
        s = self.context.settings
        for combo, value in ((self.theme, s.ui.theme), (self.accent, s.ui.accent), (self.engine, s.npia.engine)):
            index = combo.findData(value)
            if index >= 0 and index != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        set_quietly(self.auto_login, s.npia.auto_login)
        if not self.output_dir.hasFocus():
            self.output_dir.setText(s.output.output_dir)
        set_quietly(self.ask_each_time, s.output.ask_each_time)
        set_quietly(self.at_once, s.queue.max_concurrent_jobs)
        for host_id, chip in self.host_chips.items():
            order = list(s.upload.hosts)
            chip.set_quietly(host_id in order)
            name = next((h.display_name for h in UPLOAD_HOSTS if h.id == host_id), host_id)
            chip.setText(f"{order.index(host_id) + 1}. {name}" if host_id in order else name)
        set_quietly(self.copy_link, s.upload.copy_link)
        if not self.catalog_url.hasFocus():
            self.catalog_url.setText(s.catalog.url)
        set_quietly(self.cart_links, s.catalog.cart_links)

    def _fill_account(self) -> None:
        secrets = self.context.settings.secrets
        self.email.setText(secrets.email)
        self.password.setText(secrets.password)
        self.loginkey.setText(secrets.loginkey)

    def _save_account(self) -> None:
        email, password, key = self.email.text().strip(), self.password.text(), self.loginkey.text().strip()
        self._set(lambda x: replace(x, secrets=replace(x.secrets, email=email, password=password, loginkey=key)))
        self.context.save_now()
        self.account_status.setText("Signing in...")
        self.context.sign_in()

    def _signed_in(self, ok: bool) -> None:
        self.account_status.setText("Signed in to npia." if ok else "Not signed in: only free chapters can be read.")

    def _browse(self) -> None:
        start = self.output_dir.text().strip() or str(paths.default_output_dir())
        folder = QFileDialog.getExistingDirectory(self, "Save downloads to", start)
        if folder:
            self.output_dir.setText(folder)
            self._set(lambda x: replace(x, output=replace(x.output, output_dir=folder)))

    def _toggle_host(self, host_id: str, on: bool) -> None:
        hosts = [h for h in self.context.settings.upload.hosts if h != host_id]
        if on:
            hosts.append(host_id)
        if not hosts:  # keep at least one host
            self.host_chips[host_id].set_quietly(True)
            return
        self._set(lambda x: replace(x, upload=replace(x.upload, hosts=tuple(hosts))))

    def _save_catalog(self, text: str) -> None:
        url = valid_catalog_url(text)
        if url is None:
            self.catalog_error.setText("Enter a full address starting with http:// or https://")
            return
        self.catalog_error.setText("")
        self.catalog_url.setText(url)
        self._set(lambda x: replace(x, catalog=replace(x.catalog, url=url)))

"""Download page tools: tag search, batch files, Enter Browser, the sfc app session, folders."""

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from noveldb import features
from noveldb.core import paths, platform_services
from noveldb.core.external.tools import login_browser_mode
from noveldb.core.sources import detect_source, split_inputs
from noveldb.qt.context import AppContext

TOOLS = (
    ("tag_search", "npia tag search..."),
    ("batch_file", "Open a list of links or IDs..."),
    ("-", ""),
    ("enter_browser", "Enter Browser (sign in to a site)"),
    ("sfc_app", "sfc app session..."),
    ("sfc_android_open", "Open the Android emulator (sfc)"),
    ("sfc_android_import", "Import the sfc session from the emulator"),
    ("-", ""),
    ("cache_folder", "Open the cache folder"),
    ("downloads_folder", "Open the downloads folder"),
)


def available(tool: str) -> tuple[bool, str]:
    """(enabled, reason when not) for a tool in this build and OS."""
    if tool == "enter_browser" and not features.has_playwright():
        return False, "Enter Browser needs the Full build (bundled browser)."
    if tool.startswith("sfc_android") and sys.platform != "win32":
        return False, "The emulator tools are Windows only."
    return True, ""


class _Done(QObject):
    finished = Signal(str, object, str)  # tool, result, error


class SfcSessionDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("sfc app session")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.cookie = QPlainTextEdit()
        self.cookie.setMaximumHeight(90)
        self.cookie.setPlaceholderText(".SFCommunity=...; session_APP=...")
        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("App cookie", self.cookie)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.error = QLabel("")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Sign in with the app account, or paste an app cookie."))
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.cookie.toPlainText().strip() and not (self.username.text().strip() and self.password.text()):
            self.error.setText("Enter a username and password, or paste an app cookie.")
            return
        self.accept()


class Tools(QObject):
    """Runs the tools for the main window; browser work goes through the shared browser worker."""

    def __init__(self, context: AppContext, window) -> None:
        super().__init__(window)
        self.context = context
        self.window = window
        self._tag_dialog = None
        self._done = _Done()
        self._done.finished.connect(self._finished, Qt.QueuedConnection)

    def run(self, tool: str) -> None:
        ok, reason = available(tool)
        if not ok:
            self.context.hub.message.emit(reason)
            return
        getattr(self, f"_{tool}")()

    # -- simple tools --------------------------------------------------------------------------

    def _tag_search(self) -> None:
        from noveldb.qt.tag_search import TagSearchDialog

        if self._tag_dialog is None:
            self._tag_dialog = TagSearchDialog(self.context, self.window)
            self._tag_dialog.addToDownload.connect(self._add_lines)
        self._tag_dialog.show()
        self._tag_dialog.raise_()
        self._tag_dialog.activateWindow()

    def _add_lines(self, lines) -> None:
        self.window.download.add_lines(lines)
        self.window.show_page(0)
        self.context.hub.message.emit(f"Added {len(lines)} line(s) to the Download box.")

    def _batch_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window, "Open a list", str(paths.default_output_dir()),
                                              "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                lines = split_inputs(handle.read())
        except OSError as exc:
            self.context.hub.message.emit(f"Could not read {path}: {exc}")
            return
        self._add_lines(lines)

    def _show_account(self) -> None:
        self.window.show_page(0)
        self.window.download.account.setFocus()

    def _restart(self) -> None:
        from noveldb.qt import app

        app.request_restart()
        self.context.save_now()
        if not self.window.close():  # the user kept running downloads: no restart later either
            app._restart["requested"] = False

    def _cache_folder(self) -> None:
        folder = paths.cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        platform_services.open_path(folder)

    def _downloads_folder(self) -> None:
        platform_services.open_path(self.context.settings.output.output_dir or str(paths.default_output_dir()))

    # -- browser worker tools --------------------------------------------------------------------

    def _submit(self, tool: str, call) -> None:
        if self.context.browser is None:
            self.context.hub.message.emit("The browser worker is not running.")
            return

        def log(text):
            self.context.hub.log("", text)

        def done(future, tool=tool):
            try:
                self._done.finished.emit(tool, future.result(), "")
            except Exception as exc:
                self._done.finished.emit(tool, None, str(exc) or type(exc).__name__)

        self.context.browser.submit(call, log=log).add_done_callback(done)

    def _enter_browser(self) -> None:
        lines = split_inputs(self.window.download.input.toPlainText())
        url = next((line for line in lines if line.startswith(("http://", "https://"))), None)
        regular, message = login_browser_mode(url, self.context.settings.sources.regular_browser,
                                              detect_source(url) if url else None)
        self.context.hub.log("", message)
        start = url or "https://www.google.com"
        self._submit("enter_browser",
                     lambda scraper: scraper.open_visible_browser(start_url=start, regular_browser=regular))

    def _sfc_app(self) -> None:
        dialog = SfcSessionDialog(self.window)
        if dialog.exec() != QDialog.Accepted:
            return
        cookie = dialog.cookie.toPlainText().strip()
        username, password = dialog.username.text().strip(), dialog.password.text()
        self.context.hub.log("", "Importing the sfc app cookie..." if cookie else "Signing in to the sfc app...")
        if cookie:
            self._submit("sfc_app", lambda scraper: scraper.save_sfc_app_cookie(cookie))
        else:
            self._submit("sfc_app", lambda scraper: scraper.login_sfc_app(username, password))

    def _sfc_android_open(self) -> None:
        self._submit("sfc_android_open", lambda scraper: scraper.open_android_emulator())

    def _sfc_android_import(self) -> None:
        self._submit("sfc_android_import", lambda scraper: scraper.import_sfc_app_cookie_from_android())

    def _finished(self, tool: str, result, error: str) -> None:
        if error:
            self.context.hub.log("", f"{tool.replace('_', ' ')} failed: {error}")
            self.context.hub.message.emit(f"Failed: {error}")
        elif tool == "enter_browser":
            self.context.hub.log("", "Browser session ended.")
        elif tool.startswith("sfc"):
            text = "sfc session saved." if result else "The sfc session was not saved."
            self.context.hub.log("", text)
            self.context.hub.message.emit(text)

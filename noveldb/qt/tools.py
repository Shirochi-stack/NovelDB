"""Download page tools: tag search, batch files, Enter Browser, the sfc/tapas/webtoons app
sessions, folders."""

from __future__ import annotations

import sys
import threading
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from noveldb import features
from noveldb.core import paths, platform_services
from noveldb.core.external.tools import login_browser_mode
from noveldb.core.sources import detect_source, split_inputs
from noveldb.qt.context import AppContext
from noveldb.qt.scale import bind_size

# The Tools menu. Enter Browser has its own button on the Download page; the Enter App entries sit
# here with the other session tools.
TOOLS = (
    ("tag_search", "npia tag search..."),
    ("batch_file", "Open a list of links or IDs..."),
    ("-", ""),
    ("sfc_app", "Enter App (sfc)..."),
    ("sfc_android_open", "Open the Android emulator (sfc)"),
    ("sfc_android_import", "Import the sfc session from the emulator"),
    ("sfc_copy", "Copy the sfc app cookie (to paste on the phone)"),
    ("-", ""),
    ("tapas_app", "Enter App (tapas)..."),
    ("tapas_android_open", "Open the Android emulator (tapas)"),
    ("tapas_android_import", "Import the tapas session from the emulator"),
    ("tapas_copy", "Copy the tapas app cookie (to paste on the phone)"),
    ("-", ""),
    ("webtoons_android_open", "Open the Android emulator (webtoons)"),
    ("webtoons_android_import", "Import the webtoons session from the emulator"),
    ("webtoons_copy", "Copy the webtoons app cookie (to paste on the phone)"),
    ("-", ""),
    ("cache_folder", "Open the cache folder"),
    ("downloads_folder", "Open the downloads folder"),
)


def available(tool: str) -> tuple[bool, str]:
    """(enabled, reason when not) for a tool in this build and OS."""
    if tool == "enter_browser" and not features.has_playwright():
        return False, "Enter Browser needs the Full build (bundled browser)."
    if "_android_" in tool and sys.platform != "win32":
        return False, "The emulator tools are Windows only."
    return True, ""


class _Done(QObject):
    finished = Signal(str, object, str)  # tool, result, error
    browserShown = Signal()  # the Enter Browser window is up (or failed to open)


def _session_status(saved: str) -> QLabel:
    """A status line showing whether a session is already saved for this app, so reopening the
    dialog makes a signed-in state visible instead of looking blank."""
    label = QLabel(
        "Signed in: a saved session is in use. Enter new details only to replace it."
        if saved else "No session is saved yet.")
    label.setWordWrap(True)
    if saved:
        label.setStyleSheet("color: #3fa34d; font-weight: bold;")
    return label


class SfcSessionDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, saved: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("sfc app session")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.cookie = QPlainTextEdit()
        bind_size(self.cookie, max_height=90)
        self.cookie.setPlaceholderText(".SFCommunity=...; session_APP=...")
        if saved:
            self.cookie.setPlainText(saved)
        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("App cookie", self.cookie)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.error = QLabel("")
        layout = QVBoxLayout(self)
        layout.addWidget(_session_status(saved))
        layout.addLayout(form)
        layout.addWidget(QLabel("Sign in with the app account, or paste an app cookie."))
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.cookie.toPlainText().strip() and not (self.username.text().strip() and self.password.text()):
            self.error.setText("Enter a username and password, or paste an app cookie.")
            return
        self.accept()


class TapasSessionDialog(QDialog):
    """Sign in to tapas with an email + password (no captcha unless tapas asks for one), or paste a
    session cookie copied from a signed-in browser."""

    def __init__(self, parent: Optional[QWidget] = None, saved: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("tapas session")
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.cookie = QPlainTextEdit()
        bind_size(self.cookie, max_height=90)
        self.cookie.setPlaceholderText("name=value; name=value  (from a signed-in browser)")
        if saved:
            self.cookie.setPlainText(saved)
        form = QFormLayout()
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        form.addRow("Cookie", self.cookie)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.error = QLabel("")
        layout = QVBoxLayout(self)
        layout.addWidget(_session_status(saved))
        layout.addLayout(form)
        layout.addWidget(QLabel("Sign in with your tapas account, or paste a session cookie."))
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.cookie.toPlainText().strip() and not (self.email.text().strip() and self.password.text()):
            self.error.setText("Enter an email and password, or paste a session cookie.")
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
        self._done.browserShown.connect(lambda: self._browser_opening(False), Qt.QueuedConnection)

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

    def _browser_opening(self, opening: bool, profile: int = 0) -> None:
        """Show on the Enter Browser button that its window is on the way."""
        show = getattr(getattr(self.window, "download", None), "show_browser_opening", None)
        if callable(show):
            show(opening, profile)

    def _submit(self, tool: str, call) -> None:
        if self.context.browser is None:
            self.context.hub.message.emit("The browser worker is not running.")
            return

        def log(text):
            self.context.hub.log("", text)
            # The window is up once the scraper says so (a new profile takes Chrome a few seconds).
            if tool == "enter_browser" and ("Browser opened" in str(text) or "Using normal installed Chrome" in str(text)):
                self._done.browserShown.emit()

        def done(future, tool=tool):
            try:
                self._done.finished.emit(tool, future.result(), "")
            except Exception as exc:
                self._done.finished.emit(tool, None, str(exc) or type(exc).__name__)

        self.context.browser.submit(call, log=log).add_done_callback(done)

    def _enter_browser(self) -> None:
        lines = split_inputs(self.window.download.input.toPlainText())
        self.enter_browser(next((line for line in lines if line.startswith(("http://", "https://"))), None))

    def enter_browser(self, url=None) -> None:
        """The visible login browser (the downloader's saved profile), opened on `url`."""
        regular, message = login_browser_mode(url, self.context.settings.sources.regular_browser,
                                              detect_source(url) if url else None)
        self.context.hub.log("", message)
        start = url or "https://www.google.com"
        profile = self.context.settings.sources.browser_profile

        def open_browser(scraper):
            scraper.use_profile(profile)  # the selected profile's sign-in sessions
            return scraper.open_visible_browser(start_url=start, regular_browser=regular)

        if not regular:
            self.context.hub.log("", f"Enter Browser: profile #{profile}.")
        self._browser_opening(True, 0 if regular else profile)
        self._submit("enter_browser", open_browser)

    @staticmethod
    def _saved_app_cookie(getter_name: str) -> str:
        """The cookie header a previous sign-in saved for an app (read on the UI thread so the
        dialog can show it), or '' when none is saved."""
        try:
            from external_scraper import ExternalScraper

            return getattr(ExternalScraper(logger=lambda *a, **k: None), getter_name)() or ""
        except Exception:
            return ""

    def _sfc_app(self) -> None:
        dialog = SfcSessionDialog(self.window, saved=self._saved_app_cookie("sfc_app_cookie_header"))
        if dialog.exec() != QDialog.Accepted:
            return
        cookie = dialog.cookie.toPlainText().strip()
        username, password = dialog.username.text().strip(), dialog.password.text()
        self.context.hub.log("", "Importing the sfc app cookie..." if cookie else "Signing in to the sfc app...")
        if cookie:
            self._submit("sfc_app", lambda scraper: scraper.save_sfc_app_cookie(cookie))
        else:
            self._submit("sfc_app", lambda scraper: scraper.login_sfc_app(username, password))

    def _tapas_app(self) -> None:
        dialog = TapasSessionDialog(self.window, saved=self._saved_app_cookie("tapas_app_cookie_header"))
        if dialog.exec() != QDialog.Accepted:
            return
        cookie = dialog.cookie.toPlainText().strip()
        email, password = dialog.email.text().strip(), dialog.password.text()
        self.context.hub.log("", "Importing the tapas session cookie..." if cookie else "Signing in to tapas...")
        if cookie:
            self._submit("tapas_app", lambda scraper: scraper.save_tapas_app_cookie(cookie))
        else:
            self._submit("tapas_app", lambda scraper: scraper.login_tapas_app(email, password))

    def _sfc_android_open(self) -> None:
        self._submit_android("sfc_android_open", lambda scraper: scraper.open_android_emulator())

    def _sfc_android_import(self) -> None:
        self._submit_android("sfc_android_import", lambda scraper: scraper.import_sfc_app_cookie_from_android())

    def _submit_android(self, tool: str, call) -> None:
        # Android emulator setup (sdkmanager downloads, a multi-GB image install, launching the
        # emulator) can run for minutes. Run it on its own thread, never the shared browser worker, so
        # neither the worker nor anything waiting on it stalls. It needs no Playwright, so a throwaway
        # scraper on this thread is enough; results reach the UI through the same queued signal.
        from external_scraper import ExternalScraper

        def log(text):
            self.context.hub.log("", text)

        def run():
            try:
                self._done.finished.emit(tool, call(ExternalScraper(logger=log)), "")
            except Exception as exc:
                self._done.finished.emit(tool, None, str(exc) or type(exc).__name__)

        threading.Thread(target=run, name=f"android-{tool}", daemon=True).start()

    def _sfc_copy(self) -> None:
        self._submit("sfc_copy", lambda scraper: scraper.sfc_app_cookie_header())

    def _tapas_android_open(self) -> None:
        self._submit_android("tapas_android_open", lambda scraper: scraper.open_android_emulator(site="tapas"))

    def _tapas_android_import(self) -> None:
        self._submit_android("tapas_android_import", lambda scraper: scraper.import_app_cookie_from_android("tapas"))

    def _tapas_copy(self) -> None:
        self._submit("tapas_copy", lambda scraper: scraper.tapas_app_cookie_header())

    def _webtoons_android_open(self) -> None:
        self._submit_android("webtoons_android_open", lambda scraper: scraper.open_android_emulator(site="webtoons"))

    def _webtoons_android_import(self) -> None:
        self._submit_android("webtoons_android_import",
                             lambda scraper: scraper.import_app_cookie_from_android("webtoons"))

    def _webtoons_copy(self) -> None:
        self._submit("webtoons_copy", lambda scraper: scraper.webtoons_cookie_header())

    # The emulator open/import and copy tools work the same for every app, so they share one handler.
    _COPY_TOOLS = {"sfc_copy": "sfc", "tapas_copy": "tapas", "webtoons_copy": "webtoons"}
    _OPEN_TOOLS = {"sfc_android_open": "sfc", "tapas_android_open": "tapas",
                   "webtoons_android_open": "webtoons"}
    _IMPORT_TOOLS = {"sfc_android_import": "sfc", "tapas_android_import": "tapas",
                     "webtoons_android_import": "webtoons"}

    def _finished(self, tool: str, result, error: str) -> None:
        if tool == "enter_browser":
            self._browser_opening(False)
        if error:
            self.context.hub.log("", f"{tool.replace('_', ' ')} failed: {error}")
            self.context.hub.message.emit(f"Failed: {error}")
            return
        if tool == "enter_browser":
            self.context.hub.log("", "Browser session ended.")
            return

        if tool in self._COPY_TOOLS:
            site = self._COPY_TOOLS[tool]
            if result:
                from PySide6.QtGui import QGuiApplication

                QGuiApplication.clipboard().setText(result)
                text = (f"{site} app cookie copied. Paste it on the phone where you add the {site} "
                        "login, then save.")
            else:
                text = (f"No {site} app session is saved yet. Sign in to the app, import it from the "
                        "emulator, or use Enter Browser first.")
        elif tool in self._OPEN_TOOLS:
            site = self._OPEN_TOOLS[tool]
            if tool == "webtoons_android_open":
                text = (f"Opened {site} on the emulator. You can sign in there." if result else
                        f"Could not open {site} on the emulator. See the Android log for details.")
            elif result:
                text = ("Opening the Android emulator. Sign in to the "
                        f"{site} app there (Google login works), then use "
                        f"\"Import the {site} session from the emulator\".")
            else:
                text = "Could not open the Android emulator. See the log."
        elif tool in self._IMPORT_TOOLS:
            site = self._IMPORT_TOOLS[tool]
            text = (f"{site} session imported from the emulator." if result else
                    f"No {site} session found in the emulator. Sign in to the {site} app there first.")
        elif tool == "tapas_app":
            text = "tapas session saved." if result else \
                   "The tapas session was not saved. Check the email/password or cookie; see the log."
        elif tool.startswith("sfc"):
            text = "sfc session saved." if result else "The sfc session was not saved."
        else:
            return
        self.context.hub.log("", text)
        self.context.hub.message.emit(text)

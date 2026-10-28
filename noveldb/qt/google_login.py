"""npia sign-in with Google.

With QtWebEngine: a dialog on a private profile watches the cookie store (it sees HttpOnly cookies)
and takes the session cookie once the page comes back from Google. Without it (TurboLite): the
pywebview helper process the old window used. Either way the key ends up in Settings > npia, and
pasting a session key by hand stays available.
"""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import threading
import time
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from noveldb import features
from noveldb.core.npia.ids import SESSION_COOKIE, SITE_DOMAIN, SITE_URL
from noveldb.qt.context import AppContext

GOOGLE_HOST = "accounts.google.com"
# Google refuses sign-in from browsers that announce themselves as embedded.
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/148.0.0.0 Safari/537.36")


def save_key(context: AppContext, key: str) -> None:
    context.update_settings(lambda s: replace(s, secrets=replace(s.secrets, loginkey=key)))
    context.save_now()
    context.auth.set_manual_key(key)
    context.signed_in = True
    context.signedInChanged.emit(True)
    context.hub.log("", "Google sign-in: session key saved.")
    context.hub.message.emit("Signed in to npia with Google.")


class GoogleLoginDialog(QDialog):
    def __init__(self, context: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.context = context
        self.setWindowTitle("Sign in to npia with Google")
        self.resize(980, 760)
        self.key = ""
        self.visited_google = False
        self.profile = QWebEngineProfile(self)  # off the record: starts signed out, leaves nothing behind
        self.profile.setHttpUserAgent(CHROME_UA)
        self.profile.cookieStore().cookieAdded.connect(self._cookie)
        self.page = QWebEnginePage(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.urlChanged.connect(self._url_changed)
        self.status = QLabel("Sign in with Google on the page below. The window closes by itself when it is done.")
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.view, 1)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(2500)  # let the redirect chain finish
        self._settle.timeout.connect(self._finish)
        self.view.load(QUrl(SITE_URL))

    def _cookie(self, cookie) -> None:
        name = bytes(cookie.name()).decode("latin-1")
        domain = cookie.domain().lstrip(".").lower()
        if name == SESSION_COOKIE and domain.endswith(SITE_DOMAIN):
            self.key = bytes(cookie.value()).decode("latin-1").strip()

    def _url_changed(self, url: QUrl) -> None:
        host = url.host().lower()
        if host == GOOGLE_HOST:
            self.visited_google = True
            self.status.setText("Signing in with Google...")
        elif self.visited_google and host.endswith(SITE_DOMAIN):
            self.status.setText("Back on npia, saving the session...")
            self._settle.start()

    def _finish(self) -> None:
        if self.key:
            save_key(self.context, self.key)
            self.accept()
        else:
            self.visited_google = False
            self.status.setText("No session yet. Finish signing in, or close this window.")

    def done(self, result):
        self.view.setPage(None)
        self.page.deleteLater()
        super().done(result)


class _HelperSignals(QObject):
    finished = Signal(str)


def _helper_login(context: AppContext) -> None:
    """The pywebview helper process (TurboLite), polling the file it writes the key to."""
    from noveldb.login.webview_login import _run_webview_login

    with tempfile.NamedTemporaryFile(delete=False, suffix=".loginkey") as handle:
        key_path = handle.name
    os.remove(key_path)
    signals = _HelperSignals(context)
    signals.finished.connect(lambda key: save_key(context, key) if key else context.hub.message.emit(
        "Google sign-in did not finish. You can paste a session key in Settings > npia login."), Qt.QueuedConnection)
    process = multiprocessing.Process(target=_run_webview_login, args=(key_path,), daemon=True)
    process.start()
    context.hub.log("", "Google sign-in window opened.")

    def poll():
        for _ in range(900):
            try:
                if os.path.exists(key_path):
                    with open(key_path, "r", encoding="utf-8") as handle:
                        key = handle.read().strip()
                    if key:
                        os.remove(key_path)
                        signals.finished.emit(key)
                        return
            except OSError:
                pass
            if not process.is_alive() and not os.path.exists(key_path):
                break
            time.sleep(1)
        signals.finished.emit("")

    threading.Thread(target=poll, name="google-login", daemon=True).start()


def google_login(context: AppContext, parent: Optional[QWidget] = None) -> None:
    if features.has_webengine():
        GoogleLoginDialog(context, parent).exec()
    elif features.has_pywebview():
        _helper_login(context)
    else:
        context.hub.message.emit("Google sign-in needs a browser component this build does not have. "
                                 "Paste a session key in Settings > npia login instead.")

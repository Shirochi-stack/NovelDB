"""Starts the Qt app: one instance per settings folder, theme, window, auto sign-in.

`--smoke` builds the window, waits for the event loop to settle and quits, writing a JSON report
to `--smoke-report` (the desktop builds run this on every OS).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from app_version import VERSION


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="NovelDB", add_help=True)
    parser.add_argument("--smoke", action="store_true", help="build the window, then quit")
    parser.add_argument("--smoke-report", default="", help="write the smoke test result here (JSON)")
    parser.add_argument("--page", default="", help="open this page first (download, catalog, queue, settings)")
    args, _unknown = parser.parse_known_args(argv)
    return args


def _instance_key() -> str:
    from noveldb.core import paths

    folder = str(paths.config_path().parent).lower() if sys.platform == "win32" else str(paths.config_path().parent)
    return "NovelDB-" + format(abs(hash(folder)) & 0xFFFFFFFF, "08x")


def _already_running(key: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(key)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def _listen(key: str, window) -> Optional[QLocalServer]:
    server = QLocalServer()
    QLocalServer.removeServer(key)  # a stale socket after a crash (Unix)
    if not server.listen(key):
        return None

    def raise_window():
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(raise_window)
    return server


def _smoke_report(window, started: float) -> dict:
    from noveldb import features

    report = {
        "ok": True,
        "version": VERSION,
        "variant": features.variant(),
        "platform": sys.platform,
        "qt_platform": QApplication.platformName(),
        "window": window.isVisible(),
        "pages": [window.sidebar.item(i).text() for i in range(window.sidebar.count())],
        "features": {
            "playwright": features.has_playwright(),
            "webengine": features.has_webengine(),
            "pywebview": features.has_pywebview(),
            "weasyprint": features.has_weasyprint(),
        },
        "forbidden_loaded": sorted(name for name in ("tkinter",) if name in sys.modules),
        "seconds": round(time.time() - started, 2),
    }
    report["ok"] = report["window"] and not report["forbidden_loaded"]
    return report


def main(argv=None) -> int:
    started = time.time()
    args = _parse(sys.argv[1:] if argv is None else argv)
    QCoreApplication.setOrganizationName("NovelDB")
    QCoreApplication.setApplicationName("NovelDB")
    QCoreApplication.setApplicationVersion(VERSION)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])

    key = _instance_key()
    if not args.smoke and _already_running(key):
        return 0

    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import PAGES, MainWindow
    from noveldb.qt.theme import apply_theme

    try:
        context = AppContext()
    except Exception as exc:  # unreadable settings folder and the like
        QMessageBox.critical(None, "NovelDB", f"NovelDB could not start:\n{exc}")
        return 1
    apply_theme(app, context.settings.ui.theme, context.settings.ui.accent)
    window = MainWindow(context)
    if args.page:
        names = [name.lower() for name in PAGES]
        if args.page.lower() in names:
            window.show_page(names.index(args.page.lower()))
    window.show()
    server = None if args.smoke else _listen(key, window)

    if not args.smoke:
        secrets = context.settings.secrets
        if context.settings.npia.auto_login and ((secrets.email and secrets.password) or secrets.loginkey):
            context.sign_in()
        if context.runner.interrupted or (context.runner.paused and context.runner.jobs):
            window.statusBar().showMessage("Downloads from the last session are in the Queue (press Start).", 10000)

    exit_code = {"value": 0}
    if args.smoke:
        def finish():
            report = _smoke_report(window, started)
            if args.smoke_report:
                with open(args.smoke_report, "w", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=2)
            print(json.dumps(report))
            exit_code["value"] = 0 if report["ok"] else 1
            window.close()
            app.quit()

        QTimer.singleShot(int(os.environ.get("NOVELDB_SMOKE_DELAY_MS", "800")), finish)

    code = app.exec()
    context.shutdown()
    if server is not None:
        server.close()
    return exit_code["value"] if args.smoke else code

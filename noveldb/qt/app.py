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


_restart = {"requested": False}
_update = {"plan": None}  # an app_update.InstallPlan to start once this copy has quit


def request_restart() -> None:
    """Start a new copy once this one has quit (display scaling only applies at startup)."""
    _restart["requested"] = True


def request_update(plan) -> None:
    """Quit, then let the update helper swap in the new build and start it."""
    _update["plan"] = plan


def _relaunch() -> None:
    from PySide6.QtCore import QProcess

    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, sys.argv[1:]
    else:
        program, arguments = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    QProcess.startDetached(program, arguments)


def _set_icon(app) -> None:
    """The mobile app's icon for the window and taskbar (its own taskbar entry on Windows)."""
    from PySide6.QtGui import QIcon

    from noveldb.core import paths

    icon = paths.bundle_dir() / "assets" / "app-icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:  # from source, without this the taskbar shows Python's icon
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NovelDB.Desktop.source")
        except Exception:
            pass


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="NovelDB", add_help=True)
    parser.add_argument("--smoke", action="store_true", help="build the window, then quit")
    parser.add_argument("--smoke-report", default="", help="write the smoke test result here (JSON)")
    parser.add_argument("--smoke-checks", default="",
                        help="extra smoke checks, comma separated: webengine, browser, pdf, catalog (or all)")
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


SMOKE_HTML = (
    "<html><head><meta charset='utf-8'><title>smoke</title></head><body>"
    "<h1>NovelDB</h1><p>한국어 문장입니다. 中文小说。 日本語。</p></body></html>"
)


def _check_webengine(app) -> dict:
    """Load a page in QtWebEngine and read its title back."""
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView()
    view.resize(320, 200)
    loop = QEventLoop()
    result = {"ok": False}

    def loaded(ok):
        result["ok"] = bool(ok)
        result["title"] = view.title()
        loop.quit()

    view.loadFinished.connect(loaded)
    QTimer.singleShot(30_000, loop.quit)
    view.setHtml(SMOKE_HTML)
    loop.exec()
    result["ok"] = result["ok"] and result.get("title") == "smoke"
    view.deleteLater()
    return result


def _check_browser() -> dict:
    """Launch the bundled Chromium headless through Playwright."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(SMOKE_HTML)
        title = page.title()
        version = browser.version
        browser.close()
    return {"ok": title == "smoke", "chromium": version}


def _check_pdf() -> dict:
    """Render Korean, Chinese and Japanese text to PDF with WeasyPrint and check fonts are embedded."""
    import tempfile

    import downloader_core

    downloader_core._prepare_weasyprint_windows_dll_path()  # same DLL lookup as real PDF output
    from weasyprint import HTML

    import re

    data = HTML(string=SMOKE_HTML).write_pdf(uncompressed_pdf=True)
    embedded = data.count(b"/FontFile")
    fonts = sorted({name.decode("latin-1").split("+")[-1] for name in re.findall(rb"/BaseFont /([^\s/]+)", data)})
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(data)
    return {"ok": len(data) > 1000 and embedded > 0, "bytes": len(data), "embedded_fonts": embedded,
            "fonts": fonts, "path": handle.name}


def _check_catalog(window) -> dict:
    """Open the Catalog page as a user would and keep the app running while its web view starts.

    Opening it crashed the Linux build once (an application-wide event filter); a crash here leaves
    no smoke report, which fails the build."""
    from PySide6.QtCore import QEventLoop

    window.show_page(1)
    page = window.catalog
    loop = QEventLoop()
    QTimer.singleShot(8_000, loop.quit)
    loop.exec()
    view = getattr(page, "view", None)
    return {"ok": view is not None, "url": view.url().toString() if view is not None else ""}


def _run_checks(app, names, window=None) -> dict:
    out = {}
    checks = (("webengine", lambda: _check_webengine(app)), ("browser", _check_browser), ("pdf", _check_pdf),
              ("catalog", lambda: _check_catalog(window)))
    for name, check in checks:
        if name not in names:
            continue
        try:
            out[name] = check()
        except Exception as exc:  # report, do not crash the smoke run
            out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


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
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)  # QtWebEngine loads later (Catalog page)
    # The Catalog zooms like a browser (the page re-lays out); Chromium's pinch zoom only magnifies it.
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-pinch" not in flags.split():
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-pinch").strip()
    app = QApplication.instance() or QApplication(sys.argv[:1])

    key = _instance_key()
    if not args.smoke and _already_running(key):
        return 0

    _set_icon(app)
    font = app.font()
    font.setPointSizeF(max(font.pointSizeF(), 11.0))  # Qt's 9 pt default reads small next to the site
    app.setFont(font)
    # Spin boxes and drop-downs ignore the mouse wheel on their own (noveldb.qt.widgets.SpinBox and
    # friends). No application-wide event filter: on Linux one crashed the app when the Catalog opened.

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
    window.show_initial()
    server = None if args.smoke else _listen(key, window)

    if not args.smoke:
        secrets = context.settings.secrets
        if context.settings.npia.auto_login and ((secrets.email and secrets.password) or secrets.loginkey):
            context.sign_in()
        context.check_engine_updates(automatic=True)  # only engines the user opted in to
        from noveldb.core import app_update
        from noveldb.qt.app_update import UpdateOffer

        app_update.clean_up()
        offer = UpdateOffer(window, context)
        context.appUpdateChecked.connect(offer.checked)
        context.check_app_update(automatic=True)
        # A copy left running for days still hears about updates (the check itself runs once a day).
        update_timer = QTimer(window)
        update_timer.setInterval(6 * 3600 * 1000)
        update_timer.timeout.connect(lambda: context.check_app_update(automatic=True))
        update_timer.start()
        if context.runner.interrupted or (context.runner.paused and context.runner.jobs):
            window.statusBar().showMessage("Downloads from the last session are in the Queue (press Start).", 10000)

    exit_code = {"value": 0}
    if args.smoke:
        def finish():
            report = _smoke_report(window, started)
            names = {n.strip() for n in args.smoke_checks.split(",") if n.strip()}
            if "all" in names:
                names = {"webengine", "browser", "pdf", "catalog"}
            report["checks"] = _run_checks(app, names, window)
            report["ok"] = report["ok"] and all(item.get("ok") for item in report["checks"].values())
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
        server.close()  # before relaunching, or the new copy would just raise this window
    if _update["plan"] is not None and not args.smoke:
        from noveldb.core import app_update

        app_update.start_helper(_update["plan"])
    elif _restart["requested"] and not args.smoke:
        _relaunch()
    return exit_code["value"] if args.smoke else code

"""Grab the real NovelDB desktop window page by page for the landing page.

python capture.py <theme: light|dark> <out_dir>

Builds the window the way noveldb.qt.app.main does, minus sign-in and update checks; the theme is
overridden in memory only and the original settings are put back before exit (nothing is saved).
"""
import os
import sys
import traceback
from dataclasses import replace

REPO = r"C:\Users\ADMIN\Projects\NpiaDownloader67"
sys.path.insert(0, REPO)
os.chdir(REPO)

theme, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
log = open(os.path.join(out, f"log-{theme}.txt"), "w", encoding="utf-8")


def say(*parts):
    print(*parts, file=log, flush=True)


from PySide6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setOrganizationName("NovelDB")
QCoreApplication.setApplicationName("NovelDB")
QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(sys.argv[:1])
font = app.font()
font.setPointSizeF(max(font.pointSizeF(), 11.0))
app.setFont(font)

from noveldb.qt.app import _set_icon  # noqa: E402
from noveldb.qt.context import AppContext  # noqa: E402
from noveldb.qt.main_window import MainWindow  # noqa: E402
from noveldb.qt.theme import apply_theme  # noqa: E402

_set_icon(app)
context = AppContext()
original = context.settings
context.settings = replace(original, ui=replace(original.ui, theme=theme))
apply_theme(app, theme, context.settings.ui.accent)
window = MainWindow(context)
window.showNormal()
window.resize(1440, 900)
window.move(40, 40)

steps = []


def grab(name):
    def run():
        path = os.path.join(out, f"{name}-{theme}.png")
        ok = window.grab().save(path)
        say("grab", name, ok, window.size().width(), window.size().height())
    return run


def page(index):
    return lambda: window.show_page(index)


from PySide6.QtWidgets import QLineEdit  # noqa: E402


def hide_logins():
    """Blank the sign-in fields on screen (signals blocked: nothing reaches the settings)."""
    for edit in window.findChildren(QLineEdit):
        if "@" in edit.text() or edit.echoMode() != QLineEdit.Normal:
            edit.blockSignals(True)
            edit.setText("")
            edit.blockSignals(False)


def pick():
    lib = window.library
    books = [b for b in lib.books if any(f.format == "EPUB" for f in b.files)]
    say("library books", len(lib.books), "epub", len(books))
    for want in ("Lord of Mysteries", "Reverend Insanity", "Regressor"):
        for b in books:
            if want.lower() in (b.title or "").lower():
                return b
    return books[0] if books else None


def filter_library():
    for edit in window.library.findChildren(QLineEdit):
        if "title or author" in edit.placeholderText():
            edit.setText(os.environ.get("SHOT_FILTER", "the"))


def open_book():
    book = pick()
    if book is not None:
        window.library._show_book(book)


def open_reader():
    book = pick()
    if book is not None:
        window.library._read_at(book, 2)


def finish():
    context.settings = original
    log.close()
    os._exit(0)


plan = [
    (2500, page(0)), (2500, hide_logins), (800, grab("download")),
    (500, page(1)), (14000, grab("catalog")),
    (500, page(2)), (9000, filter_library), (2500, grab("library")),
    (500, open_book), (3500, grab("book")),
    (500, open_reader), (5000, grab("reader")),
    (500, page(3)), (2000, grab("queue")),
    (500, page(4)), (2500, grab("settings")),
    (500, finish),
]


def run_plan(i=0):
    if i >= len(plan):
        return
    delay, action = plan[i]

    def fire():
        try:
            action()
        except Exception:
            say(traceback.format_exc())
        run_plan(i + 1)

    QTimer.singleShot(delay, fire)


run_plan()
QTimer.singleShot(120000, finish)
app.exec()

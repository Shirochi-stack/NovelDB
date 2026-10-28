"""The app-update notice and the download, verify, restart flow (noveldb.core.app_update does the work)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from noveldb.core import app_update


class _Worker(QObject):
    progress = Signal(int, int)
    stage = Signal(str)
    finished = Signal(object, str)  # an app_update.InstallPlan (or None), and an error message


class UpdateOffer(QObject):
    """Offers an update found by the automatic check, and runs the update when the user agrees."""

    def __init__(self, parent: QWidget, context) -> None:
        super().__init__(parent)
        self.parent_widget = parent
        self.context = context
        self._offered: set[int] = set()  # builds already offered while this copy runs
        self._busy = False

    def checked(self, result, automatic: bool) -> None:
        if not automatic or result.status != "update" or result.update is None:
            return
        if result.update.asset.build in self._offered:
            return
        self._offered.add(result.update.asset.build)
        self.offer(result.update)

    def offer(self, update) -> None:
        while True:
            box = QMessageBox(self.parent_widget.window())
            box.setWindowTitle("NovelDB update")
            box.setIcon(QMessageBox.Information)
            box.setText(f"NovelDB {update.title} is available.")
            box.setInformativeText(
                f"Download {update.asset.name} ({update.asset.size / 1e6:.0f} MB), check its SHA-256 against the "
                "release, and restart NovelDB with it?")
            accept = box.addButton("Update", QMessageBox.AcceptRole)
            notes = box.addButton("Release notes", QMessageBox.HelpRole)
            box.addButton("Later", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is notes:
                QDesktopServices.openUrl(QUrl(update.release_url))
                continue
            if box.clickedButton() is accept:
                self.install(update)
            return

    def install(self, update) -> None:
        if self._busy:
            return
        self._busy = True
        window = self.parent_widget.window()
        dialog = QProgressDialog(f"Downloading NovelDB {update.title}...", "Cancel", 0, 1000, window)
        dialog.setWindowTitle("NovelDB update")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        worker = _Worker(self)
        cancel = threading.Event()
        dialog.canceled.connect(cancel.set)
        worker.progress.connect(lambda done, total: dialog.setValue(int(done * 1000 / max(total, 1))))
        worker.stage.connect(dialog.setLabelText)
        worker.finished.connect(lambda plan, error: self._finished(update, plan, error, dialog))

        def work():
            try:
                path = app_update.download(update, progress=worker.progress.emit, cancelled=cancel.is_set)
                worker.stage.emit("Checksum verified. Preparing the update...")
                plan = app_update.prepare_install(update, path)
            except app_update.UpdateError as exc:
                worker.finished.emit(None, str(exc))
                return
            except Exception as exc:
                worker.finished.emit(None, f"Could not prepare the update: {exc}")
                return
            worker.finished.emit(plan, "")

        threading.Thread(target=work, name="app-update-download", daemon=True).start()
        dialog.show()

    def _finished(self, update, plan, error: str, dialog: QProgressDialog) -> None:
        dialog.canceled.disconnect()
        dialog.close()
        self._busy = False
        window = self.parent_widget.window()
        if error:
            self.context.hub.log("", error)
            if "cancelled" not in error:
                QMessageBox.warning(window, "NovelDB update", error)
            return
        self.context.hub.log("", f"Downloaded {update.asset.name}; size and SHA-256 match the release "
                                 f"({update.asset.sha256[:16]}...). {plan.summary}")
        from noveldb.qt import app as qt_app

        qt_app.request_update(plan)
        answer = QMessageBox.question(
            window, "NovelDB update",
            f"NovelDB {update.title} is downloaded and verified. Restart now to finish updating?\n\n"
            "Otherwise it is installed when you quit NovelDB.")
        if answer != QMessageBox.Yes:
            return
        self.context.save_now()
        if not window.close():  # the user kept running downloads
            self.context.hub.log("", "The update will be installed when you quit NovelDB.")

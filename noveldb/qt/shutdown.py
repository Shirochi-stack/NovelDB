"""Shut the computer down after the whole queue finished successfully, after a countdown that can be cancelled."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from noveldb.core import platform_services

COUNTDOWN_SECONDS = 60


class ShutdownCountdown(QDialog):
    """Counts down in the app on every OS, then runs the shutdown command (unless cancelled).

    `shut_down` runs the command; tests pass their own so nothing is ever shut down by a test.
    """

    def __init__(self, parent: Optional[QWidget] = None, seconds: int = COUNTDOWN_SECONDS,
                 shut_down: Optional[Callable[[], bool]] = None, before: Optional[Callable[[], None]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shutting down")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.remaining = seconds
        self.shut_down = shut_down or (lambda: platform_services.schedule_shutdown(0))
        self.before = before
        self.done_shutting_down = False
        self.label = QLabel()
        self.label.setWordWrap(True)
        cancel = QPushButton("Cancel shutdown")
        cancel.setProperty("primary", True)
        cancel.clicked.connect(self.reject)
        now = QPushButton("Shut down now")
        now.clicked.connect(self._fire)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(now)
        row.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._show_remaining()
        self.timer.start()

    def _show_remaining(self) -> None:
        self.label.setText(f"Every download finished. The computer shuts down in {self.remaining} s.\n"
                           "Cancel to keep it running.")

    def _tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self._fire()
        else:
            self._show_remaining()

    def _fire(self) -> None:
        self.timer.stop()
        if self.done_shutting_down:
            return
        self.done_shutting_down = True
        if self.before is not None:
            self.before()
        self.shut_down()
        self.accept()

    def reject(self) -> None:
        self.timer.stop()
        super().reject()

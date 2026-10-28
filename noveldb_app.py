"""NovelDB desktop app (PySide6). `gui.py` still starts the old Tk window for one more release."""

import dpi_setup

dpi_setup.configure()  # before any Qt import

import multiprocessing  # noqa: E402
import sys  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from noveldb.qt.app import main

    sys.exit(main())

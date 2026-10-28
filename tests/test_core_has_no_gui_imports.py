"""`noveldb.core` and `noveldb.features` must never import a GUI toolkit."""

import pkgutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _core_modules():
    import noveldb.core

    names = ["noveldb.features"]
    for info in pkgutil.walk_packages(noveldb.core.__path__, "noveldb.core."):
        names.append(info.name)
    return names


def test_core_modules_do_not_load_tkinter_or_pyside6():
    modules = _core_modules()
    probe = (
        "import importlib, sys\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('tkinter', '_tkinter', 'PySide6', 'shiboken6'))\n"
        "print(','.join(bad))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "", f"GUI modules imported: {proc.stdout.strip()}"
    assert len(modules) >= 5

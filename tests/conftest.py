"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def no_installed_custom_engines(tmp_path_factory, monkeypatch):
    """The custom engines added to this machine's own NovelDB stay out of the tests (the engine order lists
    them, so a test's expected order changed with what the person running it had installed)."""
    from noveldb.core.engines import custom_engines

    folder = tmp_path_factory.mktemp("custom-engines")
    monkeypatch.setattr(custom_engines, "folder", lambda: folder)


@pytest.fixture(autouse=True)
def qt_garbage_is_freed_on_the_gui_thread(request):
    """The windows and forms of a Qt test are freed here, on the GUI thread, when the test ends.

    Their Python wrappers sit in reference cycles (a widget and the functions connected to its
    signals), so only the cycle collector frees them, and it runs on whichever thread allocates
    next. When that was a queue worker thread of a later test, Qt objects were deleted off the
    GUI thread and the whole run crashed (a segmentation fault in the macOS build).
    """
    yield
    if "qtbot" in request.fixturenames:
        import gc

        gc.collect()

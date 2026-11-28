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
def private_external_chapter_cache(tmp_path_factory, monkeypatch):
    """Each test gets its own external chapter cache. It lived in this machine's real cache folder, keyed by
    the book URL, so one test's chapters (or a real download's) came back as another test's text."""
    from noveldb.core.external import pipeline

    folder = str(tmp_path_factory.mktemp("external-cache"))
    monkeypatch.setattr(pipeline, "_external_cache_dir", lambda: folder)


@pytest.fixture(autouse=True)
def shipped_engine_bundles_only(tmp_path_factory, monkeypatch):
    """The tests read the engine bundles shipped in the repository, not the updates this machine downloaded
    (an update leaves out every upstream plugin that fails to load, so a site a test reads could vanish
    with what was published that day). A test that sets its own NovelDB home keeps its own updates."""
    import os

    from noveldb.core import engine_updates, paths

    root = tmp_path_factory.mktemp("engine-updates")
    monkeypatch.setattr(engine_updates, "updates_dir", lambda engine: (
        paths.data_dir() if os.environ.get(paths.HOME_ENV) else root) / "engines" / engine.name)


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

"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def no_installed_custom_engines(tmp_path_factory, monkeypatch):
    """The custom engines added to this machine's own NovelDB stay out of the tests (the engine order lists
    them, so a test's expected order changed with what the person running it had installed)."""
    from noveldb.core.engines import custom_engines

    folder = tmp_path_factory.mktemp("custom-engines")
    monkeypatch.setattr(custom_engines, "folder", lambda: folder)

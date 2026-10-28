"""Build variant detection: forced variants switch optional parts off."""

import pytest

from noveldb import features


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("NOVELDB_FORCE_VARIANT", "NOVELDB_DISABLE_WEBENGINE", "NOVELDB_DISABLE_PLAYWRIGHT"):
        monkeypatch.delenv(name, raising=False)
    features.reset_cache()


def test_source_checkout_reports_source():
    assert features.variant() == "source"


def test_turbolite_has_no_webengine_or_playwright(monkeypatch):
    monkeypatch.setenv("NOVELDB_FORCE_VARIANT", "turbolite")
    assert features.variant() == "turbolite"
    assert features.has_webengine() is False
    assert features.has_playwright() is False


def test_lite_keeps_webengine_but_not_playwright(monkeypatch):
    monkeypatch.setenv("NOVELDB_FORCE_VARIANT", "lite")
    monkeypatch.setattr(features, "_installed", lambda module: True)
    assert features.has_webengine() is True
    assert features.has_playwright() is False


def test_disable_switches(monkeypatch):
    monkeypatch.setattr(features, "_installed", lambda module: True)
    assert features.has_webengine() and features.has_playwright()
    monkeypatch.setenv("NOVELDB_DISABLE_WEBENGINE", "1")
    monkeypatch.setenv("NOVELDB_DISABLE_PLAYWRIGHT", "true")
    assert not features.has_webengine()
    assert not features.has_playwright()


def test_unknown_forced_variant_is_ignored(monkeypatch):
    monkeypatch.setenv("NOVELDB_FORCE_VARIANT", "mega")
    assert features.variant() == "source"

"""The two minimum waits (Request timeout, Page load wait): config keys, floor helpers, call sites, UI."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from noveldb.core import net_timeouts
from noveldb.core.settings import desktop_config as dc
from noveldb.core.settings.model import AppSettings, SharedSettings
from noveldb.core.settings.store import ConfigStore, read_raw


@pytest.fixture(autouse=True)
def fresh_timeouts():
    net_timeouts.reset()
    yield
    net_timeouts.reset()


# -- config.json ------------------------------------------------------------------------------------

def test_defaults_are_sixty_seconds():
    shared = AppSettings().shared
    assert (shared.request_timeout, shared.page_load_timeout) == (60, 60)
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (60, 60)


@pytest.mark.parametrize("stored, expected", [
    (None, 60), (5, 10), (10, 10), (45, 45), (600, 600), (9999, 600), (-3, 10),
    ("90", 90), ("12.7", 12), (45.0, 45), ("soon", 60), (True, 60), ([], 60),
])
def test_config_keys_read_with_clamping(stored, expected):
    config = {} if stored is None else {"request_timeout": stored, "page_load_timeout": stored}
    shared = dc.import_config(config).shared
    assert shared.request_timeout == expected
    assert shared.page_load_timeout == expected


def test_a_missing_key_keeps_the_other_one():
    shared = dc.import_config({"request_timeout": 120}).shared
    assert (shared.request_timeout, shared.page_load_timeout) == (120, 60)


def test_export_always_writes_both_keys_and_keeps_unknown_ones():
    exported = dc.export_config(AppSettings(), {"something_else": [1, 2]})
    assert exported["request_timeout"] == 60 and exported["page_load_timeout"] == 60
    assert exported["something_else"] == [1, 2]
    odd = AppSettings(shared=replace(SharedSettings(), request_timeout=1, page_load_timeout=100000))
    exported = dc.export_config(odd)
    assert (exported["request_timeout"], exported["page_load_timeout"]) == (10, 600)


def test_round_trip_through_the_config_store(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.save(replace(AppSettings(), shared=replace(SharedSettings(), request_timeout=75, page_load_timeout=200)))
    raw = read_raw(store.path)
    assert raw["request_timeout"] == 75 and raw["page_load_timeout"] == 200
    loaded = store.load().shared
    assert (loaded.request_timeout, loaded.page_load_timeout) == (75, 200)


def test_overrides_and_run_edits_carry_the_timeouts():
    s = replace(AppSettings(), shared=replace(SharedSettings(), request_timeout=90, page_load_timeout=150))
    assert s.effective("kpage").request_timeout == 90
    edited = s.edit_run_settings("npia", lambda run: replace(run, threads=2, max_retries=7))
    assert edited.shared.request_timeout == 90 and edited.shared.page_load_timeout == 150
    assert edited.effective("npia").page_load_timeout == 150


# -- the floor ---------------------------------------------------------------------------------------

def test_request_timeout_is_the_larger_of_fixed_and_setting():
    assert net_timeouts.request_timeout() == 60
    assert net_timeouts.request_timeout(15) == 60
    assert net_timeouts.request_timeout(120) == 120
    net_timeouts.configure(request=300)
    assert net_timeouts.request_timeout(15) == 300
    assert net_timeouts.request_timeout(120) == 300
    assert net_timeouts.request_timeout(450) == 450


def test_page_helpers_use_their_own_setting():
    net_timeouts.configure(request=300, page_load=90)
    assert net_timeouts.page_timeout(45) == 90
    assert net_timeouts.page_timeout(240) == 240
    assert net_timeouts.page_timeout_ms(30000) == 90000
    assert net_timeouts.page_timeout_ms(240000) == 240000
    assert net_timeouts.page_timeout_ms() == 90000
    assert net_timeouts.request_timeout_ms(15000) == 300000


def test_configure_clamps_and_none_leaves_a_value_alone():
    net_timeouts.configure(request=5, page_load=9999)
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (10, 600)
    net_timeouts.configure(request=77)
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (77, 600)
    net_timeouts.configure_from(AppSettings())
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (60, 60)


def test_connect_part_of_a_tuple_never_drops_below_twenty_seconds():
    assert net_timeouts.request_pair(5, 15) == (20, 60)
    assert net_timeouts.request_pair(30, 300) == (30, 300)
    net_timeouts.configure(request=500)
    assert net_timeouts.request_pair(30, 300) == (30, 500)


def test_page_defaults_reach_a_playwright_like_object():
    calls = []

    class Context:
        def set_default_timeout(self, ms):
            calls.append(("timeout", ms))

        def set_default_navigation_timeout(self, ms):
            calls.append(("navigation", ms))

    net_timeouts.configure(page_load=120)
    net_timeouts.apply_page_defaults(Context())
    net_timeouts.apply_page_defaults(object())  # no such methods: nothing happens
    assert calls == [("timeout", 120000), ("navigation", 120000)]


# -- call sites --------------------------------------------------------------------------------------

def _scraper():
    from external_scraper import ExternalScraper

    return ExternalScraper()


def test_a_scraper_http_call_uses_max_of_its_fixed_timeout_and_the_setting(monkeypatch):
    import requests

    seen = []

    def fake_get(url, headers=None, timeout=None, **kwargs):
        seen.append(timeout)
        return SimpleNamespace(text="ok", raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", fake_get)
    scraper = _scraper()
    assert scraper._amazon_request_text("https://example.com/") == "ok"          # fixed 20 s
    assert scraper._amazon_request_text("https://example.com/", timeout=150) == "ok"
    net_timeouts.configure(request=200)
    scraper._amazon_request_text("https://example.com/")
    scraper._amazon_request_text("https://example.com/", timeout=150)
    assert seen == [60, 150, 200, 200]


def test_the_npia_search_call_uses_the_floor():
    from npia_search_http import get_json_with_retry

    seen = []

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.append(timeout)
            return SimpleNamespace(text='{"status": 200, "list": []}', status_code=200,
                                   json=lambda: {"status": 200, "list": []})

    assert get_json_with_retry(Session(), {}, {}, 1, timeout=15)["status"] == 200
    assert get_json_with_retry(Session(), {}, {}, 1)["status"] == 200  # its own 120 s stays
    net_timeouts.configure(request=300)
    get_json_with_retry(Session(), {}, {}, 1)
    assert seen == [60, 120, 300]


def test_upload_hosts_floor_the_read_half_and_keep_connect_at_twenty():
    from noveldb.core.upload.hosts import UploadContext

    assert UploadContext().http_timeout() == (30, 300)
    assert UploadContext(timeout=(5, 15)).http_timeout() == (20, 60)
    net_timeouts.configure(request=500)
    assert UploadContext().http_timeout() == (30, 500)


def test_update_checks_floor_their_short_timeouts(monkeypatch):
    import requests

    from noveldb.core import app_update, engine_updates

    seen = []

    def fake_get(url, timeout=None, **kwargs):
        seen.append(timeout)
        return SimpleNamespace(status_code=200, content=b"{}", raise_for_status=lambda: None,
                               iter_content=lambda chunk_size=0: iter(()))

    monkeypatch.setattr(requests, "get", fake_get)
    app_update._http_get("https://example.com/a", 20.0)
    engine_updates._download("https://example.com/b", 30.0)
    app_update._http_stream("https://example.com/c", 60.0)
    net_timeouts.configure(request=400)
    app_update._http_get("https://example.com/a", 20.0)
    assert seen == [60, 60, 60, 400]


def test_polling_waits_last_at_least_the_page_load_wait(monkeypatch):
    """`_fenrir_wait_for_token(20)` polls a page; it must keep polling for the setting, not 20 s."""
    import external_scraper

    clock = {"now": 1000.0}
    monkeypatch.setattr(external_scraper.time, "time", lambda: clock["now"])
    scraper = _scraper()
    scraper._fenrir_token_active = lambda: False
    scraper._fenrir_dismiss_popups = lambda: None
    scraper._page = SimpleNamespace(wait_for_timeout=lambda ms: clock.update(now=clock["now"] + ms / 1000.0))

    assert scraper._fenrir_wait_for_token(20) is False
    assert 60 <= clock["now"] - 1000.0 < 62
    clock["now"] = 1000.0
    net_timeouts.configure(page_load=200)
    assert scraper._fenrir_wait_for_token(20) is False
    assert 200 <= clock["now"] - 1000.0 < 202
    clock["now"] = 1000.0
    assert scraper._fenrir_wait_for_token(500) is False  # a source that waits longer keeps its value
    assert 500 <= clock["now"] - 1000.0 < 502


def test_a_new_browser_context_gets_the_page_load_wait_as_its_defaults():
    calls = []

    class Context:
        def set_default_timeout(self, ms):
            calls.append(("timeout", ms))

        def set_default_navigation_timeout(self, ms):
            calls.append(("navigation", ms))

    scraper = _scraper()
    net_timeouts.configure(page_load=90)
    scraper._context = Context()
    assert calls == [("timeout", 90000), ("navigation", 90000)]
    calls.clear()
    net_timeouts.configure(page_load=300)
    scraper.refresh_net_timeouts()  # a job starting with the shared browser still open
    assert calls == [("timeout", 300000), ("navigation", 300000)]
    scraper._context = None
    assert scraper._context is None


def test_a_job_starting_takes_the_current_settings():
    from noveldb.core.events import CancelToken
    from noveldb.core.jobs import JobDispatcher
    from noveldb.core.queue.model import Job

    class FakeNpia:
        def __init__(self, *args):
            pass

        def run(self):
            return {"ok": True, "outputs": []}

    settings = replace(AppSettings(), shared=replace(SharedSettings(), request_timeout=111, page_load_timeout=222))
    dispatcher = JobDispatcher(lambda: settings, None, None, lambda job: None, npia_job_factory=FakeNpia)
    dispatcher(Job("123", source="npia", engine="npia_native"), CancelToken())
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (111, 222)

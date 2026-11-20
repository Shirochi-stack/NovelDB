"""Profile rotation: Stop, the kept-open profile browsers, and the account shown per profile."""

import time
from types import SimpleNamespace

import pytest

from noveldb.core.external import profile_accounts
from noveldb.core.external.pipeline import ExternalPipelineMixin
from tests.test_chapter_range_and_purchase import Rotating, _chapters


def test_stop_ends_the_rotation_without_another_ticket_or_a_trip_back():
    job = Rotating([1, 1, 1], selected=1, low=4, high=6)
    purchase = job._scraper.kpage_auto_purchase
    released = []

    def stopping(chapter):
        outcome = purchase(chapter)
        if chapter["name"] == "c5":
            job._downloading = False  # Stop pressed while profile 2 worked
        return outcome

    job._scraper.kpage_auto_purchase = stopping
    job._scraper.kpage_release_profiles = lambda: released.append(True)
    fetched = ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.bought == [(1, "c4"), (2, "c5")]  # c6 is never asked for
    assert sorted(fetched) == [3]
    assert job.switches == [2]  # no switch back to profile 1, none on to profile 3
    assert released == [True]  # the kept-open browsers are closed all the same


def test_stop_while_a_profile_opens_uses_no_ticket():
    job = Rotating([1, 1], selected=1, low=4, high=5)
    switch = job._scraper.kpage_switch_profile

    def slow_switch(number):
        switch(number)
        job._downloading = False

    job._scraper.kpage_switch_profile = slow_switch
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.bought == [(1, "c4")]


def test_the_rotation_closes_the_other_browsers_when_it_ends():
    job = Rotating([1, 1, 1], low=4, high=6)
    released = []
    job._scraper.kpage_release_profiles = lambda: released.append(job.profile)
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert released == [1]  # after the selected profile is in use again


def test_accounts_are_noted_per_profile(tmp_path):
    base = str(tmp_path)
    assert profile_accounts.load(base) == {}
    assert profile_accounts.remember(3, "name@example.com", base) is True
    assert profile_accounts.remember(3, "name@example.com", base) is False  # nothing new
    profile_accounts.remember(1, "other", base)
    accounts = profile_accounts.load(base)
    assert accounts == {1: "other", 3: "name@example.com"}
    assert profile_accounts.label(3, accounts) == "Profile #3 (name@example.com)"
    assert profile_accounts.label(2, accounts) == "Profile #2"
    profile_accounts.remember(3, "", base)  # signed out
    assert profile_accounts.load(base) == {1: "other"}


def test_the_job_says_stopping_once():
    from noveldb.core.external.job import ExternalJob
    from noveldb.core.settings.model import AppSettings

    lines = []
    job = ExternalJob("https://example.com/b", AppSettings())
    job._log = lines.append
    job._scraper = SimpleNamespace(_stop_requested=False)
    job.stop()
    job.stop()
    assert len([line for line in lines if line.startswith("Stopping")]) == 1
    assert job._scraper._stop_requested and not job._downloading


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path))
    import external_scraper
    from external_scraper import ExternalScraper

    monkeypatch.setattr(ExternalScraper, "browser_profile", 1)
    lines = []
    made = ExternalScraper(logger=lines.append)
    made.lines = lines
    try:
        yield made
    finally:
        made.cleanup()


def test_a_profile_left_behind_keeps_its_browser_and_coming_back_is_immediate(scraper):
    from external_scraper import ExternalScraper

    try:
        scraper.start()
    except Exception as exc:  # no browser installed on this machine
        pytest.skip(f"headless browser unavailable: {exc}")
    first = scraper._page
    scraper._kpage_claimed = {"1": ["RT05"]}
    assert scraper.kpage_switch_profile(2)
    second = scraper._page
    assert second is not first and ExternalScraper.browser_profile == 2
    assert first.evaluate("1 + 1") == 2  # profile 1's browser is still open
    assert scraper._kpage_claimed == {}  # another account: its tickets are asked for again

    started = time.perf_counter()
    assert scraper.kpage_switch_profile(1)
    assert time.perf_counter() - started < 1.0
    assert scraper._page is first and scraper._kpage_claimed == {"1": ["RT05"]}
    assert list(scraper._profile_pool) == [2]
    assert "browser_data_2" in "".join(scraper.lines)

    scraper.kpage_release_profiles()
    assert scraper._profile_pool == {}
    with pytest.raises(Exception):
        second.evaluate("1")  # closed
    assert scraper._page.evaluate("1") == 1  # the profile in use keeps its browser


def test_cleanup_closes_every_kept_open_browser(scraper):
    try:
        scraper.start()
    except Exception as exc:
        pytest.skip(f"headless browser unavailable: {exc}")
    first = scraper._page
    scraper.kpage_switch_profile(2)
    scraper.cleanup()
    assert scraper._profile_pool == {} and scraper._context is None and scraper._playwright is None
    with pytest.raises(Exception):
        first.evaluate("1")
    # A later start works again (a new driver).
    scraper.start()
    assert scraper._page.evaluate("2") == 2


def test_the_account_is_only_asked_for_on_the_site(scraper):
    scraper._page = SimpleNamespace(url="about:blank", evaluate=lambda *_: pytest.fail("asked off the site"))
    assert scraper.kpage_account() == ""
    host = "page." + scraper._KPAGE_COOKIE_DOMAIN
    scraper._page = SimpleNamespace(url=f"https://{host}/content/1",
                                    evaluate=lambda *_: {"status": 200, "email": "name@example.com", "nickname": "n"})
    assert scraper.kpage_account() == "name@example.com"
    assert scraper.kpage_account() == "name@example.com"
    assert len([line for line in scraper.lines if "signed in as name@example.com" in line]) == 1  # said once
    import os

    assert profile_accounts.load(os.environ["NPIA_BROWSER_DATA_DIR"]) == {1: "name@example.com"}
    assert scraper._profile_name(1) == "profile #1 (name@example.com)"
    scraper._page = None
